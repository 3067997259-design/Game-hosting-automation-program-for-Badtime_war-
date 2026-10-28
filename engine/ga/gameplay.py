"""GA合法候选、五地点服务与唯一动作执行入口。"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from itertools import combinations_with_replacement, groupby
from typing import Any, TYPE_CHECKING

from engine.ga.actions import ActionContext, ActionDefinition, GrantKind, precheck_action
from engine.ga.combat import attack_output, resolve_batch
from engine.ga.contracts import (
    ActionPlan, ActionRequest, ActionVerb, BarrierPolicy, ContractError, CostEntry,
    DamageAttribute, DamageKind, DeathPolicy, EffectKind, EffectSpec, HitPolicy,
    IsolationPolicy, Performance, RandomRequirement, TargetScope, TimedEffect, TimerUnit, TurnStage,
)
from engine.ga.events import EventKind
from engine.ga.state import (
    ActionGrant, LOCATIONS, add_armor, balance, emit, set_balance, snapshot_id, touch, trace,
)
from engine.ga.status import cleanse_burns, heal, put_status
from engine.ga.extensions import units

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.ga.state import GaPlayer


@dataclass(frozen=True)
class ActionOption:
    """引擎注册的可执行候选；控制器只能提交其中的request。"""

    request: ActionRequest
    definition: ActionDefinition
    effects: tuple[EffectSpec, ...] = ()
    hostile: bool = False
    after: Callable[[GameState, ActionPlan], None] | None = None
    confirm: Callable[[GameState, ActionPlan], bool] | None = None
    weapon_use: bool = False
    allow_untargetable: bool = False
    ordinary_attack: bool = False


@dataclass(frozen=True)
class ActionReceipt:
    """已支付单步回执，重放返回原回执而不重新支付。"""

    request: ActionRequest
    costs: tuple[CostEntry, ...]
    batch_ids: tuple[str, ...]
    cancelled: bool = False


ARMOR_SHOPS = {"shield": "shop", "ceramic": "shop", "magic_shield": "magic",
               "at_field": "military"}
MODULE_SHOPS = {"power": "shop", "fire": "magic", "magic": "magic", "pierce": "military",
                "impact": "military", "infinite": "hospital"}
ITEM_SHOPS = {"whetstone": "shop", "cloak": "shop", "thermal": "shop", "radar": "military",
              "coating": "military", "black_market_pass": "military", "syringe": "hospital"}
TACTICAL_ITEMS = ("fragment", "shock", "flash", "smoke", "molotov")
BUILTIN_OPERATIONS = frozenset({
    "wake", "move", "lock", "syringe", "sharpen", "modules", "furnace", "work", "buy_armor",
    "buy_module", "buy_weapon", "buy_item", "refill", "repair", "learn", "heal", "surgery",
    "search", "hide", "charge", "attack", "throw", "loot", "forfeit",
})


def _visible(actor: GaPlayer, target: GaPlayer, attribute: DamageAttribute) -> bool:
    """对应属性探测或搜索/锁定可获得隐匿目标资格。"""
    if not target.hidden or actor.player_id in target.revealed_to:
        return True
    return bool((attribute is DamageAttribute.NORMAL and actor.inventory.get("thermal", 0)) or
                (attribute is DamageAttribute.TECH and actor.inventory.get("radar", 0)) or
                (attribute is DamageAttribute.MAGIC and "detect_magic" in actor.weapons))


def _bow_parameters(
    state: GameState, player: GaPlayer,
) -> tuple[int, DamageAttribute, dict[str, Any]]:
    """两模块只形成一箭；混装的属性由装配时选择。"""
    cfg = state.ga_config
    counts = {name: player.modules.count(name) for name in MODULE_SHOPS}
    if counts["infinite"]:
        return cfg.get("modules", "infinite", "damage"), DamageAttribute.NORMAL, {}
    damage = cfg.get("weapons", "bow", "damage")
    params: dict[str, Any] = {}
    for name in ("power", "fire", "magic", "pierce"):
        count = counts[name]
        if not count:
            continue
        prefix = "single" if count == 1 else "double"
        if name == "power":
            damage += cfg.get("modules", name, f"{prefix}_bonus")
        elif name == "fire":
            params["burn_stacks"] = cfg.get("modules", name, f"{prefix}_burn")
        elif name == "magic":
            params["hit_bonus"] = cfg.get("modules", name, f"{prefix}_hit_bonus")
        else:
            params["defense_coefficient"] = cfg.get("modules", name, f"{prefix}_coefficient")
    return damage, player.bow_attribute, params


def _attack_effect(
    state: GameState, grant: ActionGrant, request: ActionRequest, weapon: str,
) -> EffectSpec:
    """从当前正式状态形成伤害描述；不支付、不掷骰。"""
    player = state.players[request.actor_id]
    cfg = state.ga_config
    attribute = DamageAttribute.NORMAL
    parameters: dict[str, Any] = {}
    damage = cfg.get("weapons", weapon, "damage")
    if weapon == "bow":
        damage, attribute, parameters = _bow_parameters(state, player)
    elif weapon in ("electromagnetic_rifle", "gauss_rifle", "hook"):
        attribute = DamageAttribute.TECH
        if weapon == "gauss_rifle" and player.charged:
            damage = cfg.get("weapons", weapon, "charged_damage")
    elif weapon in ("magic_barrage", "earthquake"):
        attribute = DamageAttribute.MAGIC
    if weapon == "knife" and player.sharpened:
        damage += cfg.get("weapons", "knife", "sharpen_bonus")
    if len(request.target_ids) == 1 and grant.locked_target == request.target_ids[0]:
        parameters["ignore_move_evasion"] = True
        parameters["hit_bonus"] = parameters.get("hit_bonus", 0) + cfg.get("accuracy", "lock_bonus")
        parameters["defense_coefficient"] = min(parameters.get("defense_coefficient", 1),
                                                cfg.get("accuracy", "lock_defense_coefficient"))
    weakness = -cfg.get("status", "weakness") if "WEAK" in player.statuses else 0
    damage = attack_output(damage, 1, 0, weakness)
    ranged = weapon in ("bow", "electromagnetic_rifle", "gauss_rifle", "hook")
    scope = TargetScope.LOCAL_AREA if weapon == "earthquake" else (
        TargetScope.RANGED_SINGLE if ranged else TargetScope.LOCAL_SINGLE
    )
    return EffectSpec(
        f"{request.root_id}/damage", weapon, request.root_id, f"{request.root_id}/combat",
        player.player_id, player.player_id, player.player_id, request.target_ids,
        EffectKind.DAMAGE, DamageKind.NORMAL, damage, attribute, scope,
        HitPolicy.CERTAIN if weapon == "earthquake" else HitPolicy.ROLL,
        BarrierPolicy.RESPECT, IsolationPolicy.RESPECT, DeathPolicy.NORMAL, parameters,
    )


def _raw_options(
    state: GameState, grant: ActionGrant, stage: TurnStage,
) -> tuple[ActionOption, ...]:
    """枚举有真实效果的操作，地点与目标资格只在此定义一次。"""
    player = state.players[grant.actor_id]
    cfg = state.ga_config
    result: list[ActionOption] = []

    def add(
        operation: str, verb: ActionVerb, *, targets: tuple[str, ...] = (),
        costs: tuple[CostEntry, ...] = (), hostile: bool = False, **parameters: Any,
    ) -> ActionOption:
        request = ActionRequest(player.player_id, grant.grant_id,
                                f"{grant.grant_id}/{stage.value}", stage, verb, operation,
                                targets, parameters, Performance.NONE)
        definition = ActionDefinition(operation, stage, verb, frozenset({Performance.NONE}),
                                      costs, len(targets), len(targets), tuple(parameters))
        option = ActionOption(request, definition, hostile=hostile)
        result.append(option)
        return option

    def cost(account: str, amount: int) -> tuple[CostEntry, ...]:
        return (CostEntry(player.player_id, account, amount),)

    if state.game_over or stage in grant.occupied:
        return ()
    if not player.alive:
        if grant.kind is GrantKind.AFTERLIFE and not player.absolute_dead and not player.exited:
            return tuple(o for provider in state.ga_action_providers for o in provider(
                state, grant, stage))
        return ()
    if grant.grant_id in state.ga_response_options:
        return state.ga_response_options[grant.grant_id]
    if stage is TurnStage.T0 and (grant.t0_disabled or grant.kind in (
        GrantKind.STEP_FOLLOWUP, GrantKind.REACTION
    )):
        return ()
    if stage is TurnStage.T0 and not player.awake:
        for choice in ("knife", "shield"):
            add("wake", ActionVerb.INTERACT, choice=choice)
        return tuple(result)
    if stage is TurnStage.T1 and not player.awake:
        return ()
    if stage is TurnStage.T1:
        for location in LOCATIONS:
            if location != player.location and location not in state.destroyed_locations:
                add("move", ActionVerb.MOVE, location=location)
        if not player.home_locked:
            return tuple(result)
    if stage is TurnStage.T0:
        if player.inventory.get("syringe", 0) and player.hp < player.max_hp:
            add("syringe", ActionVerb.INTERACT, costs=cost("item:syringe", 1))
        if "knife" in player.weapons and not player.sharpened and (
            player.inventory.get("whetstone", 0)
        ):
            add("sharpen", ActionVerb.SPECIAL, costs=cost("item:whetstone", 1))
        for target in state.players.values():
            if target.player_id != player.player_id and target.alive and target.home_locked and (
                player.isolation_id == target.isolation_id
            ) and any(_visible(player, target, attr) for attr in
                      (DamageAttribute.NORMAL, DamageAttribute.TECH, DamageAttribute.MAGIC)):
                add("lock", ActionVerb.SPECIAL, targets=(target.player_id,))
        _module_options(state, player, add)
        if player.surgery == "furnace" and player.furnace_uses < cfg.get(
            "hospital", "furnace_uses"
        ):
            if player.sp < cfg.get("action", "sp_cap"):
                add("furnace", ActionVerb.INTERACT,
                    costs=cost("hp", cfg.get("hospital", "furnace_hp_cost")), mode="sp")
            if "control" in player.statuses or grant.shocked:
                add("furnace", ActionVerb.INTERACT,
                    costs=cost("hp", cfg.get("hospital", "furnace_hp_cost")), mode="cleanse")
    elif stage is TurnStage.T1:
        for weapon in sorted(player.weapons & set(cfg.get("weapons"))):
            if weapon in player.disabled_weapons:
                continue
            if weapon in ("gun_ammo",):
                continue
            if weapon == "hook" and (player.hook_cooldown or grant.hook_on_cooldown):
                continue
            ranged = weapon in ("bow", "electromagnetic_rifle", "gauss_rifle", "hook")
            attribute = _bow_parameters(state, player)[1] if weapon == "bow" else (
                DamageAttribute.TECH if weapon in ("electromagnetic_rifle", "gauss_rifle", "hook")
                else DamageAttribute.MAGIC if weapon in ("magic_barrage", "earthquake")
                else DamageAttribute.NORMAL
            )
            targets = tuple(sorted(key for key, target in state.players.items() if
                                   key != player.player_id and target.alive and
                                   target.home_locked and
                                   target.isolation_id == player.isolation_id and
                                   (ranged or target.location == player.location) and
                                   (weapon == "earthquake" or _visible(player, target, attribute))))
            costs = ()
            if weapon == "bow" and "infinite" not in player.modules:
                costs = cost("ammo:bow", cfg.get("weapons", "bow", "ammo_per_attack"))
            elif weapon in ("electromagnetic_rifle", "gauss_rifle"):
                costs = cost(f"ammo:{weapon}", cfg.get("weapons", weapon, "ammo_per_attack"))
            groups = (targets,) if weapon == "earthquake" and targets else (
                tuple((target,) for target in targets)
            )
            for group in groups:
                option = add("attack", ActionVerb.ATTACK, targets=group, costs=costs,
                             hostile=True, weapon=weapon)
                effect = _attack_effect(state, grant, option.request, weapon)
                # 随机需求是条件上限：完整拦截/免疫不进入命中抽样。
                random = (RandomRequirement("hit", 1, 1),) if effect.hit_policy is (
                    HitPolicy.ROLL
                ) and effect.amount else ()
                result[-1] = replace(
                    option, definition=replace(option.definition, random_requirements=random),
                    effects=(effect,),
                )
        for target in state.players.values():
            if target.alive and target.hidden and target.location == player.location and (
                target.player_id != player.player_id and target.isolation_id == player.isolation_id
            ) and player.player_id not in target.revealed_to:
                add("search", ActionVerb.SPECIAL, targets=(target.player_id,))
        if (not player.hidden or player.revealed_to) and not grant.t0_disabled and (
            "DISORIENTED" not in player.statuses
        ) and (
            player.inventory.get("cloak", 0) or player.inventory.get("coating", 0) or
            "stealth_spell" in player.weapons
        ):
            add("hide", ActionVerb.SPECIAL)
        if "gauss_rifle" in player.weapons and not player.charged:
            add("charge", ActionVerb.SPECIAL)
        _location_options(state, player, add, cost)
        _throw_options(state, player, add, cost)
        if _lootable(state, player):
            add("loot", ActionVerb.INTERACT)
        add("forfeit", ActionVerb.SPECIAL)
    for provider in tuple(state.ga_action_providers):
        supplied = tuple(provider(state, grant, stage))
        if any(type(option) is not ActionOption for option in supplied):
            raise ContractError("provider", "must return ActionOption values")
        if any(o.request.operation in BUILTIN_OPERATIONS for o in supplied):
            raise ContractError("provider", "cannot replace built-in operations")
        if any(not o.effects and o.after is None for o in supplied):
            raise ContractError("provider", "registered operation has no executable hook")
        result.extend(supplied)
    if grant.kind in (GrantKind.STEP_FOLLOWUP, GrantKind.REACTION):
        result = [o for o in result if o.request.verb in (ActionVerb.MOVE, ActionVerb.ATTACK)]
    if grant.shocked:
        result = [o for o in result if not o.hostile and o.request.verb in (
            ActionVerb.MOVE, ActionVerb.INTERACT
        ) or o.request.operation == "forfeit"]
    return tuple(result)


def _module_options(state: GameState, player: GaPlayer, add: Callable[..., ActionOption]) -> None:
    """装配枚举来自持有的实物，卸装不产生新库存。"""
    if "bow" not in player.weapons:
        return
    available = {name: player.inventory.get(f"module:{name}", 0) + player.modules.count(name)
                 for name in MODULE_SHOPS}
    choices: set[tuple[str, ...]] = {()}
    names = sorted(name for name, count in available.items() if count and name != "infinite")
    for count in range(1, state.ga_config.get("modules", "slots") + 1):
        choices.update(group for group in combinations_with_replacement(names, count)
                       if all(group.count(name) <= available[name] for name in group))
    if available["infinite"]:
        choices.add(("infinite",))
    for modules in sorted(choices):
        attrs = (DamageAttribute.MAGIC, DamageAttribute.TECH) if (
            "fire" in modules and "pierce" in modules
        ) else (DamageAttribute.MAGIC,) if "fire" in modules else (
            (DamageAttribute.TECH,) if "pierce" in modules else (DamageAttribute.NORMAL,)
        )
        for attr in attrs:
            if modules != player.modules or attr is not player.bow_attribute:
                add("modules", ActionVerb.SPECIAL, modules=modules, attribute=attr.value)


def _location_options(
    state: GameState, player: GaPlayer, add: Callable[..., ActionOption],
    cost: Callable[[str, int], tuple[CostEntry, ...]],
) -> None:
    """地点菜单只列N0商品，旧通行证税/病毒/内甲不回填。"""
    cfg = state.ga_config
    location = player.location
    if location == "shop":
        add("work", ActionVerb.INTERACT)
        if player.ammo.get("bow", 0) < cfg.get("start", "arrow_cap") and "bow" in player.weapons:
            add("refill", ActionVerb.INTERACT, costs=cost("credits", cfg.get(
                "weapons", "bow", "refill_price")), weapon="bow")
        for key, defense in player.defenses.items():
            if defense.kind.value == "armor" and 0 < defense.durability < defense.max_durability:
                add("repair", ActionVerb.INTERACT, costs=cost("credits", cfg.get(
                    "economy", "repair_price")), defense_id=key)
    for kind, shop in ARMOR_SHOPS.items():
        if location == shop:
            add("buy_armor", ActionVerb.INTERACT,
                costs=cost("credits", cfg.get("armor", kind, "price")), kind=kind)
    for kind, shop in MODULE_SHOPS.items():
        if location == shop and state.module_stock[kind] > 0:
            add("buy_module", ActionVerb.INTERACT,
                costs=cost("credits", cfg.get("modules", kind, "price")), kind=kind)
    for kind, shop in ITEM_SHOPS.items():
        if location != shop:
            continue
        if kind in ("cloak", "thermal", "radar", "coating"):
            price = cfg.get("economy", "visibility_item_price")
        elif kind == "syringe":
            price = cfg.get("hospital", "syringe_price")
        else:
            price = cfg.get("economy", f"{kind}_price")
        cap = cfg.get("hospital", "syringe_hold_cap") if kind == "syringe" else 1
        if player.inventory.get(kind, 0) >= cap:
            continue
        if kind == "syringe" and player.syringe_purchases >= cfg.get(
            "hospital", "syringe_purchase_cap"
        ):
            continue
        add("buy_item", ActionVerb.INTERACT, costs=cost("credits", price), kind=kind)
    weapon_shops = {"knife": "shop", "magic_barrage": "magic", "earthquake": "magic",
                    "electromagnetic_rifle": "military", "gauss_rifle": "military",
                    "hook": "military"}
    for weapon, shop in weapon_shops.items():
        if location == shop and weapon not in player.weapons:
            add("buy_weapon", ActionVerb.INTERACT,
                costs=cost("credits", cfg.get("weapons", weapon, "price")), weapon=weapon)
    if location == "magic":
        for spell in ("stealth_spell", "detect_magic"):
            if spell not in player.weapons:
                add("learn", ActionVerb.INTERACT, costs=cost("credits", cfg.get(
                    "economy", "visibility_spell_price")), spell=spell)
    if location == "military":
        for weapon in ("electromagnetic_rifle", "gauss_rifle"):
            if weapon in player.weapons and player.ammo.get(weapon, 0) < cfg.get(
                "weapons", weapon, "ammo_cap"
            ):
                add("refill", ActionVerb.INTERACT, costs=cost("credits", cfg.get(
                    "weapons", "gun_ammo", "refill_price")), weapon=weapon)
        for kind in TACTICAL_ITEMS:
            if player.inventory.get(kind, 0) < cfg.get("consumables", "hold_cap"):
                add("buy_item", ActionVerb.INTERACT,
                    costs=cost("credits", cfg.get("consumables", "price")), kind=kind)
    if location == "hospital":
        if player.hospital_heals < cfg.get("hospital", "heal_uses_per_game") and (
            player.hp < player.max_hp or player.burns
        ):
            add("heal", ActionVerb.INTERACT,
                costs=cost("credits", cfg.get("hospital", "heal_price")))
        if player.surgery is None and player.credits >= cfg.get("hospital", "surgery_min_price"):
            for kind in ("life", "overclock", "furnace"):
                add("surgery", ActionVerb.INTERACT,
                    costs=cost("credits", player.credits), kind=kind)


def _throw_options(
    state: GameState, player: GaPlayer, add: Callable[..., ActionOption],
    cost: Callable[[str, int], tuple[CostEntry, ...]],
) -> None:
    """普通投掷也占T1；不需要G7宏才能使用已有战术道具。"""
    targets = tuple(sorted(key for key, target in state.players.items() if key != player.player_id
                           and target.alive and target.home_locked and
                           target.location == player.location and
                           target.isolation_id == player.isolation_id))
    for kind in TACTICAL_ITEMS:
        if not player.inventory.get(kind, 0) or (kind != "smoke" and not targets):
            continue
        if kind == "smoke" and player.location in state.ga_smoke:
            continue
        add("throw", ActionVerb.SPECIAL, targets=targets,
            costs=cost(f"item:{kind}", state.ga_config.get("consumables", "per_throw")),
            hostile=kind != "smoke", kind=kind)


def _context(state: GameState, grant: ActionGrant, stage: TurnStage) -> ActionContext:
    """同一账本导出所有付款者账户，预检可汇总多付款者费用。"""
    resources = {key: {
        "hp": p.hp, "sp": p.sp, "pp": p.pp, "credits": p.credits,
        **{f"ammo:{name}": amount for name, amount in p.ammo.items()},
        **{f"item:{name}": amount for name, amount in p.inventory.items()},
        **{f"resource:{name}": amount for name, amount in p.resources.items()},
    } for key, p in state.players.items()}
    verbs = frozenset({ActionVerb.MOVE, ActionVerb.ATTACK}) if grant.kind in (
        GrantKind.STEP_FOLLOWUP, GrantKind.REACTION
    ) else frozenset(ActionVerb)
    root = grant.root_id or f"{grant.grant_id}/{stage.value}"
    return ActionContext(grant.actor_id, grant.grant_id, root,
                         grant.kind, snapshot_id(state), resources,
                         tuple(units(state)), verbs, frozenset(grant.occupied),
                         not state.ga_public_used)


def build_options(
    state: GameState, grant: ActionGrant, stage: TurnStage,
) -> tuple[ActionOption, ...]:
    """控制器候选与最终验证共享同一纯枚举/预检路径。"""
    context = _context(state, grant, stage)
    raw = _raw_options(state, grant, stage)
    for callback in tuple(state.ga_option_filters):
        raw = callback(state, grant, stage, raw)
    raw = tuple(o for o in raw if all(units(state)[target].isolation_id == state.players[
        grant.actor_id].isolation_id for target in o.request.target_ids))
    raw = tuple(o for o in raw if o.allow_untargetable or all(
        units(state)[target].targetable or bool(o.effects) and all(e.scope in (
            TargetScope.LOCAL_AREA, TargetScope.GLOBAL_AREA
        ) for e in o.effects) for target in o.request.target_ids))
    result = tuple(option for option in raw if precheck_action(
        option.request, context, option.definition, state.ga_config
    ).accepted and (option.definition.consumes_stage or _request_key(
        option.request, consumes_stage=False
    ) not in state.ga_executed_requests))
    if any(not o.definition.consumes_stage and o.request.stage is not TurnStage.T0 for o in result):
        raise ContractError("options", "free setup belongs to T0, not another primary action")
    if len({repr(option.request.to_dict()) for option in result}) != len(result):
        raise ContractError("options", "duplicate registered action")
    return result


def _request_key(request: ActionRequest, *, consumes_stage: bool) -> tuple[str, ...]:
    """普通小动作共用阶段位；必要设置分别去重且不占用小动作。"""
    identity = "stage" if consumes_stage else json.dumps(request.to_dict(), sort_keys=True)
    return request.grant_id, request.stage.value, identity


def legal_actions(state: GameState) -> tuple[ActionRequest, ...]:
    """当前阶段唯一合法动作集；无正在执行的槽时返回空集。"""
    if state.current_grant is None or state.current_stage not in (TurnStage.T0, TurnStage.T1):
        return ()
    return tuple(o.request for o in build_options(state, state.current_grant, state.current_stage))


def validate_request(state: GameState, request: ActionRequest) -> ActionPlan:
    """非法指令零费用零随机；不会自动换成旧命令或静默放弃。"""
    grant = state.current_grant
    if grant and request.grant_id == grant.grant_id and request.actor_id == grant.actor_id and (
        request.stage is state.current_stage and not grant.finished
    ):
        for option in build_options(state, grant, request.stage):
            if option.request == request:
                return precheck_action(request, _context(state, grant, request.stage),
                                       option.definition, state.ga_config)
    return ActionPlan(request, snapshot_id(state), False, (), None, (), ("not_legal_candidate",))


def execute_action(state: GameState, plan: ActionPlan) -> ActionReceipt:
    """执行前再核对版本与候选，先整笔支付，再结算各独立批次。"""
    key = _request_key(plan.request, consumes_stage=plan.phase_claim is not None)
    previous = state.ga_executed_requests.get(key)
    if previous:
        if previous.request != plan.request:
            raise ContractError("action", "conflicting action replay")
        return previous
    plan.assert_current(snapshot_id(state))
    actual = validate_request(state, plan.request)
    if not actual.accepted or actual != plan:
        raise ContractError("action", "plan no longer matches authoritative validation")
    grant = state.current_grant
    candidates = build_options(state, grant, plan.request.stage)
    option = next(o for o in candidates
                  if o.request == plan.request)
    if option.confirm is not None and not option.confirm(state, plan):
        receipt = ActionReceipt(plan.request, (), (), True)
        state.ga_executed_requests[key] = receipt
        return receipt
    for entry in plan.costs:
        payer = state.players[entry.payer_id]
        set_balance(payer, entry.account, balance(payer, entry.account) - entry.amount)
    if plan.phase_claim is not None:
        grant.occupied.add(plan.phase_claim)
    if plan.request.performance is Performance.PUBLIC:
        state.ga_public_used = True
        emit(state, EventKind.PUBLIC_PERFORMED, root_id=plan.request.root_id,
             actor_id=grant.actor_id, effective=True)
    touch(state)
    state.ga_action_meta[plan.request.root_id] = {
        "actor_id": grant.actor_id, "operation": plan.request.operation,
        "performance": plan.request.performance.value,
        "hidden": state.players[grant.actor_id].hidden,
        "parameters": dict(plan.request.parameters), "verb": plan.request.verb.value,
        "grant_kind": grant.kind.value,
        "legal_verbs": tuple(sorted({o.request.verb.value for o in candidates})),
        "normal_attack": option.ordinary_attack or plan.request.operation == "attack" or (
            option.weapon_use and plan.request.performance is Performance.NONE),
    }
    for callback in tuple(state.ga_before_action):
        callback(state, option, plan)
    if option.hostile and not state.players[grant.actor_id].retain_stealth_attacks:
        state.players[grant.actor_id].hidden = False
    for target in plan.request.target_ids or (None,):
        emit(state, EventKind.ACTION_ACTIVATED, root_id=plan.request.root_id,
             actor_id=grant.actor_id, target_id=target, effective=option.hostile,
             payload={"operation": plan.request.operation})
    if option.weapon_use and plan.request.operation != "attack":
        _apply_builtin(state, replace(plan.request, operation="attack"))
    _apply_builtin(state, plan.request)
    batches = []
    for batch_id, group in groupby(option.effects, key=lambda e: e.batch_id):
        if state.game_over:
            break
        resolve_batch(state, tuple(group))
        batches.append(batch_id)
    if not state.game_over:
        if option.after:
            option.after(state, plan)
        if not state.game_over:
            _after_attack(state, replace(plan.request, operation="attack") if option.weapon_use else
                          plan.request)
        for callback in tuple(state.ga_after_action):
            if state.game_over:
                break
            callback(state, option, plan)
        if not state.game_over and grant.kind is not GrantKind.ROOT_STEP:
            emit(state, EventKind.ACTION_RESOLVED, root_id=plan.request.root_id,
                 actor_id=grant.actor_id, payload={"operation": plan.request.operation,
                                                  "parameters": plan.request.parameters})
    receipt = ActionReceipt(plan.request, plan.costs, tuple(batches))
    state.ga_executed_requests[key] = receipt
    if plan.request.stage is TurnStage.T1 and grant.kind is GrantKind.STANDARD:
        state.ga_history.append((state.current_round, grant.actor_id, plan.request.verb.value))
    trace(state, "action", actor=grant.actor_id, operation=plan.request.operation,
          stage=plan.request.stage.value, performance=plan.request.performance.value)
    touch(state)
    return receipt


def execute_registered_step(state: GameState, option: ActionOption) -> ActionReceipt:
    """复合根内部仍走共同预检/支付，不再领取玩家槽、T0/T2或根收尾。"""
    request = option.request
    previous = state.current_grant, state.current_stage
    previous_meta = state.ga_action_meta.get(request.root_id)
    grant = ActionGrant(request.grant_id, request.actor_id, GrantKind.ROOT_STEP,
                        root_id=request.root_id)
    state.ga_response_options[grant.grant_id] = (option,)
    state.current_grant, state.current_stage = grant, request.stage
    try:
        return execute_action(state, validate_request(state, request))
    finally:
        if previous_meta is not None:
            state.ga_action_meta[request.root_id] = previous_meta
        state.ga_response_options.pop(grant.grant_id, None)
        state.current_grant, state.current_stage = previous


def _apply_builtin(state: GameState, request: ActionRequest) -> None:
    """已通过唯一验证器的地点/准备操作；天赋效果由注册候选提供。"""
    player = state.players[request.actor_id]
    cfg = state.ga_config
    params = request.parameters
    op = request.operation
    kind = params.get("kind")
    if op == "wake":
        player.awake = True
        player.weapons.add("bow")
        player.ammo["bow"] = cfg.get("start", "arrows")
        if params["choice"] == "knife":
            player.weapons.add("knife")
        else:
            add_armor(state, player.player_id, "shield")
    elif op == "move":
        from engine.ga.extensions import relocate
        relocate(state, player.player_id, params["location"], active=True)
    elif op == "lock":
        state.current_grant.locked_target = request.target_ids[0]
        player.locked_targets.add(request.target_ids[0])
    elif op == "syringe":
        heal(player, cfg.get("hospital", "syringe_heal"))
    elif op == "sharpen":
        player.sharpened = True
    elif op == "modules":
        for name in player.modules:
            key = f"module:{name}"
            player.inventory[key] = player.inventory.get(key, 0) + 1
        player.modules = tuple(params["modules"])
        player.bow_attribute = DamageAttribute(params["attribute"])
        for name in player.modules:
            player.inventory[f"module:{name}"] -= 1
    elif op == "furnace":
        player.furnace_uses += 1
        if params["mode"] == "sp":
            player.sp = min(cfg.get("action", "sp_cap"),
                            player.sp + cfg.get("hospital", "furnace_sp_gain"))
        else:
            player.statuses.pop("control", None)
            state.current_grant.shocked = False
    elif op == "work":
        player.credits += cfg.get("economy", "work_income")
    elif op == "buy_armor":
        add_armor(state, player.player_id, kind)
    elif op == "buy_module":
        state.module_stock[kind] -= 1
        key = f"module:{kind}"
        player.inventory[key] = player.inventory.get(key, 0) + 1
    elif op == "buy_weapon":
        weapon = params["weapon"]
        player.weapons.add(weapon)
        if weapon in ("electromagnetic_rifle", "gauss_rifle"):
            player.ammo[weapon] = cfg.get("weapons", weapon, "initial_ammo")
    elif op == "buy_item":
        player.inventory[kind] = player.inventory.get(kind, 0) + 1
        if kind == "syringe":
            player.syringe_purchases += 1
    elif op == "refill":
        weapon = params["weapon"]
        cap = cfg.get("start", "arrow_cap") if weapon == "bow" else cfg.get(
            "weapons", weapon, "ammo_cap"
        )
        amount = cfg.get("weapons", "bow" if weapon == "bow" else "gun_ammo", "refill_amount")
        player.ammo[weapon] = min(cap, player.ammo.get(weapon, 0) + amount)
    elif op == "repair":
        defense = player.defenses[params["defense_id"]]
        player.defenses[defense.defense_id] = replace(
            defense, durability=min(defense.max_durability,
                                    defense.durability + cfg.get("economy", "repair_amount"))
        )
    elif op == "learn":
        player.weapons.add(params["spell"])
    elif op == "heal":
        heal(player, cfg.get("hospital", "heal_amount"))
        cleanse_burns(player)
        player.hospital_heals += 1
    elif op == "surgery":
        player.surgery = kind
        if kind == "life":
            player.max_hp += cfg.get("hospital", "life_max_hp_bonus")
            player.hp += cfg.get("hospital", "life_current_hp_bonus")
    elif op == "search":
        state.players[request.target_ids[0]].revealed_to.add(player.player_id)
    elif op == "hide":
        player.hidden = True
        player.revealed_to.clear()
    elif op == "charge":
        player.charged = True
    elif op == "attack":
        if params["weapon"] == "knife":
            player.sharpened = False
        elif params["weapon"] == "gauss_rifle":
            player.charged = False
        elif params["weapon"] == "hook":
            player.hook_cooldown = cfg.get("weapons", "hook", "cooldown_standard_slots")
    elif op == "throw":
        _throw(state, request)
    elif op == "loot":
        _loot(state, player)
    elif op == "forfeit":
        emit(state, EventKind.TURN_FORFEITED, actor_id=player.player_id, root_id=request.root_id)
    if player.location == "military" and op in (
        "buy_armor", "buy_module", "buy_weapon", "buy_item"
    ) and kind != "black_market_pass":
        if player.inventory.get("black_market_pass", 0):
            player.inventory["black_market_pass"] -= cfg.get("economy", "pass_uses")
        else:
            state.ga_crimes.append({"actor_id": player.player_id, "root_id": request.root_id,
                                    "round": state.current_round, "kind": "military_purchase"})
            emit(state, EventKind.ACTION_ACTIVATED, actor_id=player.player_id,
                 root_id=request.root_id, effective=True, payload={"crime": "military_purchase"})


def _after_attack(state: GameState, request: ActionRequest) -> None:
    """命中后的位移与冲击只看已落地记录；擦伤不触发。"""
    if request.operation != "attack":
        return
    player = state.players[request.actor_id]
    records = state.ga_resolution_batches.get(f"{request.root_id}/combat", ((), ()))[1]
    for record in records:
        target = units(state)[record.target_id]
        if record.outcome.value != "hit" or not record.remaining_damage or not target.alive:
            continue
        if request.parameters["weapon"] == "hook":
            from engine.ga.extensions import relocate
            relocate(state, target.player_id, player.location)
        elif request.parameters["weapon"] == "bow" and "impact" in player.modules:
            locations = [loc for loc in LOCATIONS if loc != target.location and
                         loc not in state.destroyed_locations]
            if locations:
                from engine.ga.extensions import relocate
                relocate(state, target.player_id, locations[0])
            if player.modules.count("impact") == state.ga_config.get("modules", "slots"):
                if target.player_id not in state.ga_impact_penalties:
                    penalty = state.ga_config.get("modules", "impact", "double_initiative_penalty")
                    state.ga_impact_penalties[target.player_id] = penalty
                    if target.player_id in state.ga_initiative:
                        state.ga_initiative[target.player_id] += penalty
                        state.ga_round_order = tuple(sorted(
                            state.ga_initiative, key=lambda a: (-state.ga_initiative[a], a)
                        ))


def _throw(state: GameState, request: ActionRequest) -> None:
    """零伤控制道具是明文状态事件，不制造保底伤害。"""
    kind = request.parameters["kind"]
    cfg = state.ga_config
    if kind in ("fragment", "molotov"):
        effect = EffectSpec(
            f"{request.root_id}/throw", kind, request.root_id, f"{request.root_id}/throw-batch",
            request.actor_id, request.actor_id, request.actor_id, request.target_ids,
            EffectKind.DAMAGE, DamageKind.NORMAL, cfg.get("consumables", f"{kind}_damage"),
            DamageAttribute.MAGIC if kind == "molotov" else DamageAttribute.NORMAL,
            TargetScope.LOCAL_AREA, HitPolicy.CERTAIN, BarrierPolicy.RESPECT,
            IsolationPolicy.RESPECT, DeathPolicy.NORMAL,
            {"burn_stacks": cfg.get("consumables", "molotov_burn")} if kind == "molotov" else {},
        )
        resolve_batch(state, (effect,))
    elif kind == "smoke":
        location = state.players[request.actor_id].location
        state.ga_smoke[location] = TimedEffect(request.root_id, request.actor_id,
                                              state.current_round,
                                              cfg.get("consumables", "smoke_ticks"),
                                              TimerUnit.FUTURE_R4)
    else:
        for target in request.target_ids:
            player = state.players[target]
            name = "SHOCKED" if kind == "shock" else "FLASHED"
            put_status(player, cfg, state.current_round, name, source_id=request.root_id,
                       actor_id=request.actor_id, score_owner_id=request.actor_id,
                       crime_owner_id=request.actor_id)
            if kind == "flash":
                player.hidden = False
            player.engaged = True
            emit(state, EventKind.STATUS_APPLIED, actor_id=request.actor_id, target_id=target,
                 root_id=request.root_id, effective=True, payload={"statuses": [name]})
        state.players[request.actor_id].engaged = True


def _loot(state: GameState, player: GaPlayer) -> None:
    """拾取不复制掉落物；超容量的库存留在地面。"""
    piles = state.ground_loot[player.location]
    for pile in piles:
        player.credits += pile["credits"]
        pile["credits"] = 0
        player.weapons.update(pile["weapons"])
        pile["weapons"].clear()
        player.defenses.update(pile["defenses"])
        pile["defenses"].clear()
        player.armor_affinities.update(pile["affinities"])
        pile["affinities"].clear()
        player.armor_kinds.update(pile.get("armor_kinds", {}))
        pile.get("armor_kinds", {}).clear()
        for name in pile["modules"]:
            key = f"module:{name}"
            pile["inventory"][key] = pile["inventory"].get(key, 0) + 1
        pile["modules"] = ()
        for name, amount in tuple(pile["ammo"].items()):
            cap = state.ga_config.get("start", "arrow_cap") if name == "bow" else (
                state.ga_config.get("weapons", name, "ammo_cap")
            )
            take = min(amount, max(0, cap - player.ammo.get(name, 0)))
            player.ammo[name] = player.ammo.get(name, 0) + take
            pile["ammo"][name] -= take
        for name, amount in tuple(pile["inventory"].items()):
            cap = state.ga_config.get("hospital", "syringe_hold_cap") if name == "syringe" else (
                state.ga_config.get("consumables", "hold_cap") if name in TACTICAL_ITEMS else
                amount + player.inventory.get(name, 0)
            )
            take = min(amount, max(0, cap - player.inventory.get(name, 0)))
            player.inventory[name] = player.inventory.get(name, 0) + take
            pile["inventory"][name] -= take
    state.ground_loot[player.location] = [pile for pile in piles if pile["credits"] or
                                        any(pile["ammo"].values()) or
                                        any(pile["inventory"].values()) or pile["weapons"] or
                                        pile["defenses"]]


def _lootable(state: GameState, player: GaPlayer) -> bool:
    """容量已满且无其他可收获实物时，不列出空拾取。"""
    for pile in state.ground_loot.get(player.location, ()):
        if pile["credits"] or pile["defenses"] or pile["modules"] or (
            pile["weapons"] - player.weapons
        ):
            return True
        for name, amount in pile["ammo"].items():
            cap = state.ga_config.get("start", "arrow_cap") if name == "bow" else (
                state.ga_config.get("weapons", name, "ammo_cap")
            )
            if amount and player.ammo.get(name, 0) < cap:
                return True
        for name, amount in pile["inventory"].items():
            cap = state.ga_config.get("hospital", "syringe_hold_cap") if name == "syringe" else (
                state.ga_config.get("consumables", "hold_cap") if name in TACTICAL_ITEMS else None
            )
            if amount and (cap is None or player.inventory.get(name, 0) < cap):
                return True
    return False
