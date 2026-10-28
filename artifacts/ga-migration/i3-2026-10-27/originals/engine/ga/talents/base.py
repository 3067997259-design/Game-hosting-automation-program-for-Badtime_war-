"""天赋公共构建工具；所有能力仍经候选、预检、支付和效果解析。"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import replace
from functools import partial
from typing import Any, TYPE_CHECKING

from engine.ga.actions import ActionDefinition, GrantKind
from engine.ga.contracts import (
    ActionPlan, ActionRequest, ActionVerb, BarrierPolicy, CostEntry, DamageAttribute, DamageKind,
    DeathPolicy, DefenseKind, DefenseSpec, EffectKind, EffectSpec, HitPolicy, IsolationPolicy,
    Performance, TargetScope, TimedEffect, TimerUnit, TurnStage,
)
from engine.ga.extensions import DeathGate, IncomingModifier, Replacement, chapter, units
from engine.ga.gameplay import ActionOption, _attack_effect, _visible
from engine.ga.state import next_id, touch

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.ga.events import DomainEvent
    from engine.ga.state import ActionGrant, GaPlayer
    from engine.ga.contracts import ResolutionRecord


def ability(
    grant: ActionGrant, stage: TurnStage, operation: str, *, mode: Performance = Performance.NONE,
    verb: ActionVerb = ActionVerb.SPECIAL, targets: tuple[str, ...] = (),
    parameters: Mapping[str, Any] | None = None, costs: tuple[CostEntry, ...] = (),
    effects: tuple[EffectSpec, ...] = (), hostile: bool = False,
    after: Callable[[GameState, ActionPlan], None] | None = None,
) -> ActionOption:
    """费用由注册者声明，演出SP始终由公共预检统一报价。"""
    params = dict(parameters or {})
    request = ActionRequest(grant.actor_id, grant.grant_id, f"{grant.grant_id}/{stage.value}",
                            stage, verb, operation, targets, params, mode)
    definition = ActionDefinition(operation, stage, verb, frozenset({mode}), costs,
                                  len(targets), len(targets), tuple(params))
    return ActionOption(request, definition, effects, hostile, after)


def packet(
    state: GameState, request: ActionRequest, amount: int, *,
    targets: tuple[str, ...] | None = None,
    source: str | None = None, suffix: str = "damage", kind: DamageKind = DamageKind.NORMAL,
    attribute: DamageAttribute = DamageAttribute.NORMAL, scope: TargetScope | None = None,
    certain: bool = False, bypass: bool = False, absolute: bool = False,
    **parameters: Any,
) -> EffectSpec:
    """显式数值/范围/屏障/死亡权限，不根据显示名授予特权。"""
    target_ids = request.target_ids if targets is None else targets
    selected_scope = scope or (TargetScope.LOCAL_SINGLE if len(target_ids) == 1 else
                               TargetScope.LOCAL_AREA)
    return EffectSpec(
        f"{request.root_id}/{suffix}", source or request.operation, request.root_id,
        f"{request.root_id}/batch/{suffix}", request.actor_id, request.actor_id,
        request.actor_id, target_ids, EffectKind.WEAR if kind is DamageKind.PURE_WEAR else
        EffectKind.DAMAGE, kind, amount, attribute, selected_scope,
        HitPolicy.CERTAIN if certain or selected_scope in (
            TargetScope.LOCAL_AREA, TargetScope.GLOBAL_AREA
        ) or kind in (DamageKind.PURE_WEAR, DamageKind.EXECUTION) else HitPolicy.ROLL,
        BarrierPolicy.BYPASS_NUMERIC if bypass else BarrierPolicy.RESPECT,
        IsolationPolicy.RESPECT, DeathPolicy.ABSOLUTE_TRIGGER if kind is DamageKind.EXECUTION else
        DeathPolicy.ABSOLUTE_IF_LETHAL if absolute else DeathPolicy.NORMAL, parameters,
    )


def enemies(state: GameState, actor_id: str, *, local: bool = True) -> tuple[str, ...]:
    """公开范围资格；隐身不能躲明文范围伤害，隔离仍阻止外部作用。"""
    actor = state.players[actor_id]
    return tuple(key for key, target in units(state).items() if target.alive and
                 target.home_locked and key != actor_id and target.owner_id != actor_id and
                 target.isolation_id == actor.isolation_id and
                 (not local or target.location == actor.location))


def timer(state: GameState, actor: str, name: str, ticks: int) -> TimedEffect:
    """建立轮不扣tick，世界刻度不参与状态年龄。"""
    return TimedEffect(next_id(state, name), actor, state.current_round, ticks, TimerUnit.FUTURE_R4)


def barrier(
    state: GameState, actor: str, key: str, amount: int, *, ticks: int | None = None,
    kind: DefenseKind = DefenseKind.FINITE_BARRIER,
) -> None:
    """同源替换到指定值；维护/刷新时点由来源负责，不能重复挂载补值。"""
    player = state.players[actor]
    player.defenses[key] = DefenseSpec(key, kind, amount, max(1, amount),
                                      frozenset({DamageKind.NORMAL, DamageKind.DIRECT,
                                                 DamageKind.PURE_WEAR}), frozenset(TargetScope))
    if ticks is not None:
        player.barrier_timers[key] = timer(state, actor, key, ticks)
    touch(state)


def hp_cost(state: GameState, actor: str, ratio: float, *, discount: int = 0) -> int | None:
    """百分比费用先ceil/减免，再限制HP−1；最低费用付不起就不列候选。"""
    player = state.players[actor]
    minimum = state.ga_config.get("combat", "minimum_paid_hp_cost")
    if player.hp <= state.ga_config.get("combat", "paid_hp_survival_floor"):
        ledger = getattr(state, "ga_afterlife", None)
        if ledger and any(mark in ledger.marks.get(actor, ()) for mark in (
            "tomorrow", "firefly", "eternity"
        )):
            return minimum
        return None
    return min(player.hp - 1, max(minimum, math.ceil(player.hp * ratio) - discount))


def weapon_option(
    state: GameState, grant: ActionGrant, original: ActionOption, operation: str,
    mode: Performance, *, multiplier: float = 1, bonus: int = 0, wear_price: int = 1,
    bypass: bool = False,
) -> ActionOption:
    """复用实际普通武器的弹药、命中与锁定合同，再应用明确演出修正。"""
    candidate = ability(grant, original.request.stage, operation, mode=mode,
                        verb=ActionVerb.ATTACK, targets=original.request.target_ids,
                        parameters=original.request.parameters, costs=original.definition.costs,
                        hostile=True)
    effect = _attack_effect(state, grant, candidate.request, candidate.request.parameters["weapon"])
    weapon = candidate.request.parameters["weapon"]
    base = state.ga_config.get("weapons", weapon, "damage")
    if weapon == "gauss_rifle" and state.players[grant.actor_id].charged:
        base = state.ga_config.get("weapons", weapon, "charged_damage")
    amount = max(0, math.ceil(base * multiplier + effect.amount - base + bonus))
    params = {**dict(effect.parameters), "wear_price": wear_price}
    return replace(candidate, weapon_use=True, effects=(replace(
        effect, source_id=operation, amount=amount, parameters=params,
        barrier_policy=BarrierPolicy.BYPASS_NUMERIC if bypass else effect.barrier_policy,
    ),))


def attack_choices(
    state: GameState, grant: ActionGrant, *, melee: bool = False, local: bool = False,
    chase: bool = False,
) -> tuple[ActionOption, ...]:
    """能力复用合法单体武器与真实弹药，不能靠演出绕过库存。"""
    player = state.players[grant.actor_id]
    cfg = state.ga_config
    result = []
    for weapon in sorted(player.weapons & set(cfg.get("weapons"))):
        if weapon in ("earthquake", "gun_ammo") or weapon in player.disabled_weapons:
            continue
        if melee and weapon not in ("fist", "knife"):
            continue
        ranged = weapon in ("bow", "electromagnetic_rifle", "gauss_rifle", "hook")
        if weapon == "hook" and (player.hook_cooldown or grant.hook_on_cooldown):
            continue
        costs = ()
        if weapon in ("bow", "electromagnetic_rifle", "gauss_rifle") and not (
            weapon == "bow" and "infinite" in player.modules
        ):
            costs = (CostEntry(player.player_id, f"ammo:{weapon}", cfg.get(
                "weapons", weapon, "ammo_per_attack")),)
        for target in enemies(state, grant.actor_id, local=local or not ranged and not chase):
            target_unit = units(state)[target]
            candidate = ability(grant, TurnStage.T1, "attack", verb=ActionVerb.ATTACK,
                                targets=(target,), parameters={"weapon": weapon},
                                costs=costs, hostile=True)
            effect = _attack_effect(state, grant, candidate.request, weapon)
            if _visible(player, target_unit, effect.attribute):
                result.append(replace(candidate, effects=(effect,), weapon_use=True))
    return tuple(result)


def own_slot(state: GameState, owner: str, grant: ActionGrant) -> bool:
    """只允许已出门生者从自己的正常能力入口发动。"""
    player = state.players[owner]
    return grant.actor_id == owner and player.alive and player.awake and player.home_locked and (
        grant.kind in (GrantKind.STANDARD, GrantKind.EXTRA_TURN)
    )


class Talent:
    """角色状态存于GameState.ga_roles；核心只调用通用生命周期。"""

    talent_id = ""

    def __init__(self, owner_id: str) -> None:
        self.owner_id = owner_id
        self.completed_boundaries: set[tuple[int, str, str | None]] = set()

    def public(self) -> Mapping[str, Any]:
        """子类只公开基本类型，不向控制器暴露可写对象。"""
        return {}

    def register(self, state: GameState) -> None:
        """注册公共钩子；没有向调度器添加角色分支。"""
        state.ga_action_providers.append(self.options)
        state.ga_option_filters.append(self.filter_options)
        state.ga_before_action.append(self.before_action)
        state.ga_after_action.append(self.after_action)
        state.ga_incoming_modifiers.append(self.incoming)
        state.ga_batch_listeners.append(self.on_batch)
        state.ga_event_listeners.append(self.on_events)
        state.ga_death_gates.append(DeathGate(10, self.lethal, self.commit_lethal))
        state.ga_before_terminal.append(self.terminal)
        for name in ("R0", "slot_start", "T2", "R4.dot_maintenance", "R4.recovery", "R4.expiry"):
            callback = partial(self.dispatch_boundary, boundary=name)
            state.ga_hooks.setdefault(name, []).append(callback)

    def dispatch_boundary(self, state: GameState, *, boundary: str) -> None:
        """同轮同边界幂等；每个真实槽仍有独立T2和开始维护。"""
        grant = state.current_grant
        key = (state.current_round, boundary,
               grant.grant_id if grant and boundary in ("slot_start", "T2") else None)
        if key in self.completed_boundaries:
            return
        self.completed_boundaries.add(key)
        self.boundary(state, boundary=boundary)

    def options(
        self, state: GameState, grant: ActionGrant, stage: TurnStage,
    ) -> tuple[ActionOption, ...]:
        """角色专属候选；默认没有能力，绝不借旧实现。"""
        return ()

    def filter_options(
        self, state: GameState, grant: ActionGrant, stage: TurnStage,
        options: tuple[ActionOption, ...],
    ) -> tuple[ActionOption, ...]:
        """修正普通攻击或费用的唯一扩展入口。"""
        return options

    def reaction_attacks(self, state: GameState, grant: ActionGrant) -> tuple[ActionOption, ...]:
        """受限挑战仅借当前合法普通武器，临时距离不借能力。"""
        return attack_choices(state, grant, chase=True)

    def before_action(self, state: GameState, option: ActionOption, plan: ActionPlan) -> None:
        """合法支付之后、结算之前的准备。"""

    def after_action(self, state: GameState, option: ActionOption, plan: ActionPlan) -> None:
        """同根已落地后的维护。"""

    def incoming(self, state: GameState, effect: EffectSpec, target: GaPlayer) -> IncomingModifier:
        """目标侧普通防护，不承担外甲耐久费用。"""
        return IncomingModifier()

    def lethal(self, state: GameState, effect: EffectSpec, target: GaPlayer) -> Replacement | None:
        """纯濒死预览；只有明确替代才取消死亡。"""
        return None

    def commit_lethal(
        self, state: GameState, effect: EffectSpec, target: GaPlayer, result: Replacement,
    ) -> None:
        """整批确认后兑现一次替代。"""

    def on_batch(self, state: GameState, records: tuple[ResolutionRecord, ...]) -> None:
        """完整结算记录触发角色状态与章节。"""

    def on_events(self, state: GameState, events: tuple[DomainEvent, ...]) -> None:
        """永久事实订阅，重复事件不会再调用。"""

    def boundary(self, state: GameState, *, boundary: str) -> None:
        """实际轮/槽维护。"""

    def terminal(self, state: GameState, survivors: tuple[str, ...]) -> None:
        """成绩定格前的明确终章条件。"""
