"""公开警务事实与反制投影；不按具名对手设优先级，不调用真实状态或随机流。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from engine.ga.ai.forecast import (
    AttackEstimate, _effect, _police_effect, _target_outcomes, estimate_option,
    estimate_police_response, estimate_police_volley,
)
from engine.ga.config import GaConfig
from engine.ga.contracts import (
    ActionRequest, ActionVerb, BarrierPolicy, DamageAttribute, DamageKind, DeathPolicy,
    DefenseKind, DefenseSpec, EffectKind, EffectSpec, HitPolicy, IsolationPolicy, TargetScope,
    TimedEffect, TimerUnit, TurnStage, thaw_json,
)
from engine.ga.observation import public_value
from engine.ga.state import LOCATIONS


SUPPORTED = frozenset({"npc_attack", "throw", "police_stance", "police_command"})


def _supported(request: Mapping[str, Any]) -> bool:
    """普通玩家单体攻击留给战斗Mind，地震的范围减编需要警务投影。"""
    return request["operation"] in SUPPORTED or (
        request["operation"] == "attack" and request["parameters"].get("weapon") == "earthquake")


@dataclass(frozen=True)
class OfficerAssessment:
    """存活编制计伤害，实际可射击编制决定能否射击，两者不混用。"""

    officer_id: str
    hp: int
    alive: bool
    can_shoot: bool
    blocked_by: tuple[str, ...]


@dataclass(frozen=True)
class PoliceAssessment:
    """当前真实警务机会，counter_indices仅指本次正式菜单。"""

    available: bool
    remaining_volleys: int
    captain: str | None
    wanted: bool
    r4_target: str | None
    r4_owner: str | None
    r4_attack: AttackEstimate
    officers: tuple[OfficerAssessment, ...]
    counter_indices: tuple[int, ...]


@dataclass(frozen=True)
class CounterBranch:
    """一个实际命中分支的独立公开工作值，不含真实引擎对象。"""

    probability: float
    projected: dict[str, Any]


@dataclass(frozen=True)
class PoliceCounter:
    """局部反制结果不自行决定策略；renewed指标不假装已推进下个世界tick。"""

    available: bool
    projected: dict[str, Any]
    branches: tuple[CounterBranch, ...]
    direct_damage: AttackEstimate
    avoided_volley_hp: float
    renewed_volley_hp_reduction: float
    wall_removed: float
    own_wall_removed: float
    interrupted_volleys: float
    notes: tuple[str, ...]
    timing: Mapping[str, Any]


def _system_available(view: Mapping[str, Any]) -> bool:
    """编队存在且未停机；暂时没人能射击不等同编队永久消失。"""
    police = view.get("police")
    return bool(police and "police" not in view["world"]["destroyed_locations"] and
                not police["stopped"] and not view["world"]["clock_paused"] and any(
                    view["units"][key]["alive"] and
                    not view["units"][key]["submerged_sources"] for key in police["officer_ids"]))


def assess_police(
    view: Mapping[str, Any], actor_id: str, config: GaConfig,
) -> PoliceAssessment:
    """把编制、共享额度、自动目标和当前可用反制分开给仲裁器。"""
    police = view.get("police") or {}
    officers = []
    for key in police.get("officer_ids", ()):
        npc = view["units"][key]
        reasons = tuple(reason for reason, present in (
            ("dead", not npc["alive"]), ("controlled", "control" in npc["statuses"]),
            ("submerged", bool(npc["submerged_sources"]))) if present)
        officers.append(OfficerAssessment(key, npc["hp"], npc["alive"], not reasons, reasons))
    target, owner, quote = estimate_police_response(view, config)
    available = _system_available(view)
    remaining = int(available and police["attack_round"] != view["round"])
    counters = tuple(index for index, descriptor in enumerate(view.get("candidates", ())) if
                     descriptor["request"]["actor_id"] == actor_id and
                     _supported(descriptor["request"]))
    return PoliceAssessment(available, remaining, police.get("captain"),
                             actor_id in police.get("wanted", ()), target, owner, quote,
                             tuple(officers), counters)


def _sync_police(view: dict[str, Any], config: GaConfig) -> None:
    """投影只同步已公开的编制/盾墙/位置，与正式sync相同，不修复耗损。"""
    police = view["police"]
    if police["captain"] and not view["players"][police["captain"]]["alive"]:
        police["captain"], police["stance"], police["location"] = None, "mobile", "police"
    if police["guard_id"] and not view["players"][police["guard_id"]]["alive"]:
        police["guard_id"] = None
    protected = police["captain"] or police["guard_id"]
    live = sum(view["units"][key]["alive"] for key in police["officer_ids"])
    cap = live * (config.get("police", "wall_per_officer") + police["wall_upgrade"] *
                  config.get("police", "wall_upgrade_per_officer"))
    if protected:
        old = view["players"][protected]["defense_details"].get("police/wall")
        if old:
            police["wall"] = min(police["wall"], old["durability"])
    police["wall"] = min(police["wall"], cap)
    for body in view["players"].values():
        body["defense_details"].pop("police/wall", None)
        body["defenses"].pop("police/wall", None)
    if police["location"] in view["world"]["destroyed_locations"]:
        police["location"] = next(location for location in LOCATIONS if location not in
                                  view["world"]["destroyed_locations"])
    location = view["players"][police["captain"]]["location"] if police["captain"] else (
        police["location"])
    for key in police["officer_ids"]:
        view["units"][key]["owner_id"] = police["captain"]
        view["units"][key]["location"] = location
    if protected and police["stance"] == "wall" and police["wall"] and _system_available(view):
        defense = DefenseSpec("police/wall", DefenseKind.FINITE_BARRIER, police["wall"], max(1, cap),
                              frozenset({DamageKind.NORMAL, DamageKind.DIRECT,
                                         DamageKind.PURE_WEAR}),
                              frozenset({TargetScope.LOCAL_SINGLE, TargetScope.RANGED_SINGLE}))
        view["players"][protected]["defense_details"]["police/wall"] = public_value(defense)
        view["players"][protected]["defenses"]["police/wall"] = defense.durability


def _status(
    body: dict[str, Any], target: str, name: str, action: ActionRequest,
    round_number: int, config: GaConfig,
) -> bool:
    """仅投掷会产生的普通状态；震荡不能覆盖更强控制，建立轮不扣tick。"""
    if name in body["status_immunities"]:
        return False
    key = "control" if name == "SHOCKED" else name
    if name == "SHOCKED" and body["statuses"].get("control", {}).get("name") in (
        "PETRIFIED", "ACTION_SUPPRESSED"
    ):
        return False
    ticks = config.get("status", "unconsumed_control_expiry_ticks" if name == "SHOCKED" else
                       "modifier_ticks")
    timer = TimedEffect(f"{action.root_id}/{target}/{name}", target, round_number, ticks,
                        TimerUnit.FUTURE_R4)
    body["statuses"][key] = {"name": name, "timer": public_value(timer),
        "actor_id": action.actor_id, "score_owner_id": action.actor_id,
        "crime_owner_id": action.actor_id, "hits": 0, "anchored": False, "uncleansable": False}
    return True


def _burns(
    body: dict[str, Any], target: str, effect: EffectSpec, round_number: int, config: GaConfig,
) -> None:
    """追加的DOT保持未来R4语义与来源；这里只建立，不预付延后伤害。"""
    for index in range(effect.parameters.get("burn_stacks", 0)):
        timer = TimedEffect(f"{effect.effect_id}/{target}/burn/{index}", target, round_number,
                            config.get("status", "burn_ticks"), TimerUnit.FUTURE_R4)
        entry = {"name": "BURN", "timer": public_value(timer), "actor_id": effect.actor_id,
                 "score_owner_id": effect.score_owner_id, "crime_owner_id": effect.crime_owner_id,
                 "hits": 0, "anchored": False, "uncleansable": False}
        burns = body["burns"]
        if len(burns) < config.get("status", "burn_cap"):
            burns.append(entry)
        else:
            oldest = min(range(len(burns)), key=lambda i: (
                burns[i]["timer"]["remaining"], burns[i]["timer"]["created_round"], i))
            burns[oldest] = entry


def _effect_branches(
    view: dict[str, Any], effect: EffectSpec, config: GaConfig,
) -> tuple[CounterBranch, ...]:
    """命中/擦伤各自保留HP与护甲，避免把期望血量伪装成确定减编。"""
    branches = (CounterBranch(1.0, view),)
    for target in effect.target_ids:
        next_branches = []
        for branch in branches:
            _, outcomes = _target_outcomes(branch.projected, effect, target, config)
            for outcome in outcomes:
                projected = thaw_json(branch.projected)
                body = (projected["players"].get(target) or projected["units"][target])
                body["hp"] -= outcome.hp
                body["alive"] = body["hp"] > 0
                body["defense_details"] = public_value(outcome.defenses)
                body["defenses"] = {key: item.durability for key, item in outcome.defenses.items()}
                body["engaged"] = True
                if effect.actor_id:
                    source = projected["players"].get(effect.actor_id) or (
                        projected["units"][effect.actor_id])
                    source["engaged"] = True
                if outcome.hit and body["alive"]:
                    _burns(body, target, effect, projected["round"], config)
                _credit(projected, target, effect, outcome.hp, outcome.breaks, config)
                if not body["alive"]:
                    body["burns"], body["statuses"] = [], {}
                next_branches.append(CounterBranch(branch.probability * outcome.probability,
                                                    projected))
        branches = tuple(next_branches)
    for branch in branches:
        _sync_police(branch.projected, config)
    return branches


def _credit(
    view: dict[str, Any], target: str, effect: EffectSpec, hp: int, breaks: int, config: GaConfig,
) -> None:
    """累计即时战果让下一枚投掷正确取整；不模拟往世分红、王冠和死亡掉落。"""
    owner = view["players"].get(effect.score_owner_id)
    if owner is None or target not in view["players"] or target == effect.score_owner_id:
        return
    score = owner["score"]
    kill = int(hp > 0 and not view["players"][target]["alive"])
    score["hp_damage"] += hp
    score["kills"] += kill
    score["armor_breaks"] += breaks
    allowance = view["reward_allowance"][effect.score_owner_id]
    if not owner["absolute_dead"] and not owner["exited"]:
        awarded = min(breaks, allowance["armor_break_pp"])
        owner["pp"] += (kill * config.get("pp", "kill_gain") +
                        awarded * config.get("pp", "armor_break_gain"))
        allowance["armor_break_pp"] -= awarded
    parts = score["components"]
    parts.update({"damage": score["hp_damage"] // config.get("scoring", "damage_divisor"),
                  "kills": score["kills"] * config.get("scoring", "kill_weight"),
                  "armor_breaks": score["armor_breaks"] * config.get("scoring", "armor_break_weight"),
                  "pp": owner["pp"] * config.get("scoring", "pp_weight")})
    parts["total"] = sum(value for key, value in parts.items() if key != "total")
    score["crown_eligible"] = (score["kills"] >= config.get("scoring", "crown_min_kills") or
                               len(score["chapters"]) >= config.get("scoring", "crown_min_chapters"))


def _pay(view: dict[str, Any], descriptor: Mapping[str, Any]) -> None:
    """预测工作钱包也支付正式菜单列出的费用，不能复制有限弹药或道具。"""
    for entry in descriptor["costs"]:
        body = view["players"][entry["payer_id"]]
        account, amount = entry["account"], entry["amount"]
        if ":" in account:
            group, name = account.split(":", 1)
            field = {"ammo": "ammo", "item": "inventory", "resource": "resources"}[group]
            body[field][name] -= amount
        else:
            body[account] -= amount


def _wall(view: Mapping[str, Any], actor: str | None = None) -> int:
    """只统计当前挂载的盾墙；架盾收起后的潜在库存不能当现有防护。"""
    return sum(body["defense_details"].get("police/wall", {}).get("durability", 0)
               for key, body in view["players"].items() if actor is None or key == actor)


def _incoming(view: Mapping[str, Any], actor: str, config: GaConfig) -> tuple[float, bool]:
    """R4情景不改变通缉顺序，也不把攻击别人的齐射算到行动者身上。"""
    target, _, quote = estimate_police_response(view, config)
    return (quote.expected_hp if target == actor else 0.0), quote.available


def estimate_counter(
    view: Mapping[str, Any], action: ActionRequest, config: GaConfig,
) -> PoliceCounter:
    """只预测本次正式菜单中的操作；结果是局部战术工作值，不是整局模拟器。"""
    original = thaw_json(view)
    descriptor = next((item for item in view.get("candidates", ()) if
                       thaw_json(item["request"]) == action.to_dict()), None)
    empty = AttackEstimate(available=False, notes=("no_direct_damage",))
    if descriptor is None or not _supported(action.to_dict()):
        return PoliceCounter(False, original, (), empty, 0, 0, 0, 0, 0,
                              ("not_a_supported_current_candidate",), {})
    projected = thaw_json(view)
    projected["candidates"] = []  # 状态变化后旧菜单失效，也避免每个分支复制整份候选目录。
    _pay(projected, descriptor)
    body = projected["players"][action.actor_id]
    if descriptor["hostile"] and not body["retain_stealth_attacks"]:
        body["hidden"] = False
    police = projected["police"]
    effects = tuple(_effect(item) for item in descriptor["declared_effects"] if
                    item["kind"] in (EffectKind.DAMAGE.value, EffectKind.WEAR.value))
    direct = estimate_option(view, descriptor, config) if effects else empty
    notes = ["public_counter_projection", "not_full_game_or_terminal_simulation",
             "projected_is_highest_probability_branch", "no_death_loot_or_afterlife_dividends"]
    timing: dict[str, Any] = {"created_round": view["round"], "current_R4": "before_expiry",
                             "renewed_allowance": "same_state_no_clock_or_status_advance"}
    if action.operation == "police_stance":
        police["stance"] = action.parameters["stance"]
        if police["stance"] == "wall":
            police["immobile_round"] = projected["round"]
        _sync_police(projected, config)
        notes.append("stance_does_not_restore_wall_or_spend_volley")
    elif action.operation == "police_command":
        target = action.target_ids[0]
        if target not in police["wanted"] and _system_available(projected):
            live = [key for key in police["officer_ids"] if projected["units"][key]["alive"]]
            for key in live[:config.get("police", "innocent_command_roster_loss")]:
                projected["units"][key]["hp"] = 0
                projected["units"][key]["alive"] = False
            police["stance"] = "mobile"
            _sync_police(projected, config)
            notes.append("innocent_command_loses_roster_without_firing")
        else:
            effect, reason = _police_effect(view, target, config)
            direct = estimate_police_volley(view, target, config)
            if effect is not None or reason == "police_no_uncontrolled_shooter":
                police["attack_round"] = projected["round"]
                for key in police["officer_ids"]:
                    npc = projected["units"][key]
                    if npc["alive"] and npc["statuses"].get("control", {}).get("name") in (
                        "SHOCKED", "ACTION_SUPPRESSED"
                    ):
                        del npc["statuses"]["control"]
            if effect is not None:
                effects = (effect,)
            notes.append(reason)
    elif action.operation == "throw":
        kind = action.parameters["kind"]
        if kind == "smoke":
            timer = TimedEffect(action.root_id, action.actor_id, view["round"],
                                config.get("consumables", "smoke_ticks"), TimerUnit.FUTURE_R4)
            projected["world"]["smoke"][body["location"]] = public_value(timer)
            timing["smoke_expiry_after_R4"] = view["round"] + timer.remaining
            notes.append("smoke_only_cross_location_accuracy_not_concealment")
        elif kind in ("shock", "flash"):
            name = "SHOCKED" if kind == "shock" else "FLASHED"
            for target in action.target_ids:
                recipient = projected["players"][target]
                _status(recipient, target, name, action, view["round"], config)
                if kind == "flash":
                    recipient["hidden"] = False
                recipient["engaged"] = body["engaged"] = True
                # 正式事件即使免疫仍发布；警队监听同一事件而拆墙。
                if target in (police["captain"], police["guard_id"]):
                    police["stance"] = "mobile"
            _sync_police(projected, config)
            timing["status_first_age_R4"] = view["round"] + 1
            timing["status"] = "next_slot_or_future_R4" if kind == "shock" else "future_R4"
            notes.append("captain_control_does_not_control_officers_or_cancel_R4")
        elif kind == "molotov":
            timing["burn_first_R4"] = view["round"] + 1
            timing["burn_last_R4"] = view["round"] + config.get("status", "burn_ticks")
    if action.operation == "npc_attack":
        weapon = action.parameters["weapon"]
        if weapon == "knife":
            body["sharpened"] = False
        elif weapon == "gauss_rifle":
            body["charged"] = False
        elif weapon == "hook":
            body["hook_cooldown"] = config.get("weapons", "hook", "cooldown_standard_slots")
    branches = (CounterBranch(1.0, projected),)
    for effect in effects:
        branches = tuple(CounterBranch(branch.probability * item.probability, item.projected)
                         for branch in branches for item in
                         _effect_branches(branch.projected, effect, config))
    if action.operation == "attack":
        coordinated = []
        extra_quotes = []
        for branch in branches:
            public = branch.projected
            eligible = (public["police"]["captain"] == action.actor_id and
                        public["police"]["stance"] == "mobile" and action.target_ids and
                        sum(player["alive"] for player in public["players"].values()) > 1)
            effect, reason = _police_effect(public, action.target_ids[0], config) if eligible else (
                None, "no_mobile_coordination")
            if effect is not None or reason == "police_no_uncontrolled_shooter":
                quote = estimate_police_volley(public, action.target_ids[0], config)
                extra_quotes.append((branch.probability, quote))
                public["police"]["attack_round"] = public["round"]
                for key in public["police"]["officer_ids"]:
                    npc = public["units"][key]
                    if npc["alive"] and npc["statuses"].get("control", {}).get("name") in (
                        "SHOCKED", "ACTION_SUPPRESSED"
                    ):
                        del npc["statuses"]["control"]
            if effect is None:
                coordinated.append(branch)
            else:
                coordinated.extend(CounterBranch(branch.probability * item.probability,
                                                   item.projected) for item in
                                   _effect_branches(public, effect, config))
        branches = tuple(coordinated)
        if extra_quotes:
            damage = [(branch.probability, sum(
                view[group][key]["hp"] - body["hp"] for group in ("players", "units") for
                key, body in branch.projected[group].items())) for branch in branches]
            direct = replace(direct,
                expected_hp=sum(probability * hp for probability, hp in damage),
                expected_breaks=direct.expected_breaks + sum(p * q.expected_breaks for p, q in
                                                             extra_quotes),
                expected_wear=direct.expected_wear + sum(p * q.expected_wear for p, q in extra_quotes),
                score_gain=direct.score_gain + sum(p * q.score_gain for p, q in extra_quotes),
                kill_probability=sum(branch.probability for branch in branches if any(
                    view[group][key]["alive"] and not body["alive"] for group in ("players", "units")
                    for key, body in branch.projected[group].items())),
                min_hp=min(hp for _, hp in damage), max_hp=max(hp for _, hp in damage),
                notes=(*direct.notes, "includes_mobile_police_coordination"))
            notes.append("mobile_coordination_consumes_shared_volley_before_R4")
    for branch in branches:
        branch.projected["projection"] = {"scope": "police_counter_after_action",
                                           "notes": list(notes), "timing": timing}
    selected = max(branches, key=lambda item: item.probability).projected
    before_hp, before_available = _incoming(view, action.actor_id, config)
    after_hp = sum(branch.probability * _incoming(branch.projected, action.actor_id, config)[0]
                   for branch in branches)
    interrupted = sum(branch.probability * int(before_available and not _incoming(
        branch.projected, action.actor_id, config)[1]) for branch in branches)
    original["police"]["attack_round"] = view["round"] - 1
    renewed_before, _ = _incoming(original, action.actor_id, config)
    renewed_after = 0.0
    for branch in branches:
        renewed = thaw_json(branch.projected)
        renewed["police"]["attack_round"] = view["round"] - 1
        renewed_after += branch.probability * _incoming(renewed, action.actor_id, config)[0]
    return PoliceCounter(
        True, selected, branches, direct, before_hp - after_hp, renewed_before - renewed_after,
        _wall(view) - sum(branch.probability * _wall(branch.projected) for branch in branches),
        _wall(view, action.actor_id) - sum(branch.probability * _wall(branch.projected, action.actor_id)
                                          for branch in branches),
        interrupted, tuple(notes), timing,
    )


def estimate_area_counter(
    view: Mapping[str, Any], actor_id: str, kind: str, config: GaConfig,
) -> PoliceCounter:
    """假设完成路线后投掷一枚伤害道具；重建活目标和库存，不把预测请求当合法菜单。"""
    actor = view["players"][actor_id]
    targets = tuple(sorted(key for key, body in {**view["players"], **view["units"]}.items() if
                           key != actor_id and body["alive"] and
                           (key not in view["players"] or body["home_locked"]) and
                           body["location"] == actor["location"] and
                           body["isolation_id"] == actor["isolation_id"] and
                           (key in view["players"] or body["owner_id"] != actor_id)))
    root = f"projection/throw/{kind}/{actor['inventory'].get(kind, 0)}"
    action = ActionRequest(actor_id, "projection/conditional-counter", root, TurnStage.T1,
                           ActionVerb.SPECIAL, "throw", targets, {"kind": kind})
    if kind not in ("fragment", "molotov") or not targets or not all(
        actor[key] for key in ("alive", "awake", "home_locked")
    ) or actor["inventory"].get(kind, 0) < config.get("consumables", "per_throw") or (
        "control" in actor["statuses"]
    ):
        return replace(estimate_counter(view, action, config), available=False,
                       notes=("conditional_future_counter_unavailable",))
    effect = EffectSpec(
        f"{root}/throw", kind, root, f"{root}/throw-batch", actor_id, actor_id, actor_id,
        targets, EffectKind.DAMAGE, DamageKind.NORMAL,
        config.get("consumables", f"{kind}_damage"),
        DamageAttribute.MAGIC if kind == "molotov" else DamageAttribute.NORMAL,
        TargetScope.LOCAL_AREA, HitPolicy.CERTAIN, BarrierPolicy.RESPECT,
        IsolationPolicy.RESPECT, DeathPolicy.NORMAL,
        {"burn_stacks": config.get("consumables", "molotov_burn")} if kind == "molotov" else {},
    )
    hypothetical = thaw_json(view)
    hypothetical["candidates"] = [{"request": action.to_dict(), "hostile": True,
        "costs": [{"payer_id": actor_id, "account": f"item:{kind}",
                   "amount": config.get("consumables", "per_throw")}],
        "declared_effects": [public_value(effect)]}]
    result = estimate_counter(hypothetical, action, config)
    notes = (*result.notes, "conditional_future_counter", "requires_fresh_real_candidate_to_execute")
    for branch in result.branches:
        branch.projected["candidates"] = []
        branch.projected["projection"]["notes"] = list(notes)
    return replace(result, notes=notes)
