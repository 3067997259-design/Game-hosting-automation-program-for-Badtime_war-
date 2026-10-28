"""只读公开观察的无天赋战术预测；命中分支枚举，不读取或复制真实GameState。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
import math
from types import MappingProxyType
from typing import Any

from engine.ga.config import GaConfig
from engine.ga.contracts import (
    BarrierPolicy, DamageAttribute, DamageKind, DeathPolicy, DefenseKind, DefenseSpec,
    EffectKind, EffectSpec, HitPolicy, IsolationPolicy, TargetScope,
)
from engine.ga.damage_math import absorb_barriers, armor_hit, hit_probability


@dataclass(frozen=True)
class AttackEstimate:
    """即时收益与延后灼烧分开；分数不预支王冠、押注或未来伤害。"""

    expected_hp: float = 0.0
    expected_breaks: float = 0.0
    kill_probability: float = 0.0
    score_gain: float = 0.0
    hit_probability: float = 0.0
    notes: tuple[str, ...] = ()
    expected_wear: float = 0.0
    deferred_hp: float = 0.0
    available: bool = True
    min_hp: int = 0
    max_hp: int = 0


@dataclass(frozen=True)
class ThreatEstimate:
    """各敌人下一次最危险攻击的集火情景，不声称是对手实际选择概率。"""

    expected_hp: float = 0.0
    max_hp: int = 0
    kill_probability: float = 0.0
    attackers: Mapping[str, AttackEstimate] = field(default_factory=dict)
    notes: tuple[str, ...] = ("focus_scenario_not_opponent_policy",)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attackers", MappingProxyType(dict(self.attackers)))


@dataclass(frozen=True)
class _Outcome:
    """一个命中分支的公开结果；defenses仅属于预测工作值。"""

    probability: float
    hp: int
    breaks: int
    wear: int
    deferred: int
    defenses: Mapping[str, DefenseSpec]
    hit: bool = True


def _entities(view: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """NPC也可能是目标，但其伤害不产生玩家战果。"""
    return {**view.get("units", {}), **view["players"]}


def _defenses(body: Mapping[str, Any]) -> dict[str, DefenseSpec]:
    """从公开防御合同恢复不可变值对象，不恢复角色或引擎。"""
    return {key: DefenseSpec(
        key, DefenseKind(raw["kind"]), raw["durability"], raw["max_durability"],
        frozenset(DamageKind(value) for value in raw["accepted_damage_kinds"]),
        frozenset(TargetScope(value) for value in raw["accepted_scopes"]),
        {DamageAttribute(attr): value for attr, value in raw["attribute_defense"].items()},
        raw["wear_price"],
    ) for key, raw in body.get("defense_details", {}).items()}


def _effect(raw: Mapping[str, Any]) -> EffectSpec:
    """候选声明的权限逐字段恢复，不根据技能名猜效果。"""
    return EffectSpec(
        raw["effect_id"], raw["source_id"], raw["root_id"], raw["batch_id"],
        raw["actor_id"], raw["score_owner_id"], raw["crime_owner_id"], tuple(raw["target_ids"]),
        EffectKind(raw["kind"]), DamageKind(raw["damage_kind"]), raw["amount"],
        DamageAttribute(raw["attribute"]), TargetScope(raw["scope"]), HitPolicy(raw["hit_policy"]),
        BarrierPolicy(raw["barrier_policy"]), IsolationPolicy(raw["isolation_policy"]),
        DeathPolicy(raw["death_policy"]), raw["parameters"],
    )


def _burn_gain(body: Mapping[str, Any], count: int, config: GaConfig, hp_left: int) -> int:
    """附带灼烧只算尚未兑现的边际上界；建立轮不tick，可能被治疗或终局截断。"""
    pending = [entry["timer"]["remaining"] for entry in body.get("burns", ())]
    before = sum(pending)
    for _ in range(count):
        if len(pending) == config.get("status", "burn_cap"):
            pending.remove(min(pending))
        pending.append(config.get("status", "burn_ticks"))
    extra = max(0, sum(pending) - before) * config.get("status", "burn_damage_per_stack")
    return min(hp_left, extra)


def _target_outcomes(
    view: Mapping[str, Any], effect: EffectSpec, target_id: str, config: GaConfig,
    *, body: Mapping[str, Any] | None = None, defenses: Mapping[str, DefenseSpec] | None = None,
) -> tuple[float, tuple[_Outcome, ...]]:
    """枚举实际命中/擦伤；屏障、护甲、HP上限及正常属性费用与正式解析器同源。"""
    entities = _entities(view)
    target = entities[target_id] if body is None else body
    current = _defenses(target) if defenses is None else dict(defenses)
    empty = (_Outcome(1.0, 0, 0, 0, 0, current, False),)
    if not target["alive"] or target["hp"] <= 0:
        return 0.0, empty
    if effect.damage_kind.value in target.get("immune_kinds", ()) or (
        not target.get("targetable", True) and effect.scope in
        (TargetScope.LOCAL_SINGLE, TargetScope.RANGED_SINGLE)
    ):
        if effect.damage_kind is not DamageKind.EXECUTION:
            return 0.0, empty
    if effect.damage_kind is DamageKind.EXECUTION:
        return 1.0, (_Outcome(1.0, target["hp"], 0, 0, 0, current),)
    if not effect.amount:
        return 0.0, empty
    ignored = frozenset({"police/wall"}) if effect.parameters.get("attack_side") == "back" else (
        frozenset())
    remaining, barriers = absorb_barriers(effect, current, ignored=ignored)
    for change in barriers:
        current[change.defense_id] = replace(current[change.defense_id], durability=change.after)
    if effect.damage_kind is DamageKind.PURE_WEAR:
        changes = [d for d in current.values() if d.durability and d.kind is DefenseKind.ARMOR
                   and effect.damage_kind in d.accepted_damage_kinds and
                   effect.scope in d.accepted_scopes and
                   ("armor_id" not in effect.parameters or
                    d.defense_id == effect.parameters["armor_id"])]
        worn = broken = 0
        if changes and not barriers:
            selected = min(changes, key=lambda d: (-d.durability, d.defense_id))
            worn = min(effect.amount, selected.durability)
            broken = int(worn == selected.durability)
            current[selected.defense_id] = replace(selected, durability=selected.durability - worn)
        return 1.0, (_Outcome(1.0, 0, broken, worn, 0, current),)
    if not remaining:
        return 0.0, (_Outcome(1.0, 0, 0, 0, 0, current, False),)
    actor = entities.get(effect.actor_id, {})
    cross = bool(actor and actor["location"] != target["location"])
    smoke = view.get("world", {}).get("smoke", {})
    chance = 1.0 if effect.hit_policy is HitPolicy.CERTAIN else hit_probability(
        config, effect.parameters, moved=target.get("moved", False),
        hidden=target.get("hidden", False), cross_location=cross,
        smoke=target["location"] in smoke or actor.get("location") in smoke,
        flashed="FLASHED" in actor.get("statuses", {}),
    )
    floor = min(remaining, math.ceil(effect.amount * config.get("combat", "min_damage_ratio")))
    affinities = {key: DamageAttribute(attr) for key, attr in
                  target.get("armor_affinities", {}).items()}
    result = []
    for hit, probability in ((True, chance), (False, 1.0 - chance)):
        if not probability:
            continue
        updated = dict(current)
        change = None
        if effect.damage_kind is DamageKind.DIRECT:
            computed = remaining
        elif not hit:
            computed = floor
        else:
            vulnerability = config.get("status", "vulnerability") if (
                "VULNERABLE" in target.get("statuses", {})) else 0
            gaze = (view.get("afterlife") or {}).get("gaze", ())
            vulnerability += config.get("afterlife", "gaze_next_hit_bonus") if (
                target_id in gaze) else 0
            computed, change = armor_hit(
                config, effect, current, affinities,
                body=remaining + vulnerability - target.get("flat_reduction", 0), floor=floor,
            )
        if change:
            updated[change.defense_id] = replace(updated[change.defense_id],
                                                 durability=change.after)
        hp = min(target["hp"], computed)
        burn = _burn_gain(target, effect.parameters.get("burn_stacks", 0), config,
                          target["hp"] - hp) if hit else 0
        result.append(_Outcome(probability, hp, int(bool(change and change.after == 0)),
                               change.before - change.after if change else 0, burn, updated, hit))
    return chance, tuple(result)


def _estimate_effect(
    view: Mapping[str, Any], effect: EffectSpec, config: GaConfig,
) -> AttackEstimate:
    """无天赋单段/AOE逐目标枚举，累计伤害取整与破甲PP额度只算一次。"""
    entities = _entities(view)
    if any(target not in entities for target in effect.target_ids):
        return AttackEstimate(available=False, notes=("missing_target",))
    owner = view["players"].get(effect.score_owner_id, {})
    prior = owner.get("score", {}).get("hp_damage", 0)
    allowance = view.get("reward_allowance", {}).get(effect.score_owner_id, {}).get(
        "armor_break_pp", config.get("pp", "armor_break_rewards_per_round"))
    paid = not owner.get("absolute_dead", False) and not owner.get("exited", False)
    # 概率、总HP、总破甲、总磨甲、延后HP、计分击杀、计分HP、计分破甲、所有击杀。
    branches = [(1.0, 0, 0, 0, 0, 0, 0, 0, 0)]
    chances = []
    for target_id in effect.target_ids:
        chance, outcomes = _target_outcomes(view, effect, target_id, config)
        chances.append(chance)
        scoring = (target_id in view["players"] and target_id != effect.score_owner_id and
                   bool(owner))
        branches = [(
            prior_branch[0] * outcome.probability, prior_branch[1] + outcome.hp,
            prior_branch[2] + outcome.breaks, prior_branch[3] + outcome.wear,
            prior_branch[4] + outcome.deferred,
            prior_branch[5] + int(scoring and outcome.hp >= entities[target_id]["hp"] > 0),
            prior_branch[6] + (outcome.hp if scoring else 0),
            prior_branch[7] + (outcome.breaks if scoring else 0),
            prior_branch[8] + int(outcome.hp >= entities[target_id]["hp"] > 0),
        ) for prior_branch in branches for outcome in outcomes]
    score = 0.0
    for probability, _, _, _, _, kills, damage, breaks, _ in branches:
        points = ((prior + damage) // config.get("scoring", "damage_divisor") -
                  prior // config.get("scoring", "damage_divisor"))
        points += kills * config.get("scoring", "kill_weight")
        points += breaks * config.get("scoring", "armor_break_weight")
        if paid:
            points += config.get("scoring", "pp_weight") * (
                kills * config.get("pp", "kill_gain") +
                min(allowance, breaks) * config.get("pp", "armor_break_gain"))
        score += probability * points
    return AttackEstimate(
        expected_hp=sum(row[0] * row[1] for row in branches),
        expected_breaks=sum(row[0] * row[2] for row in branches),
        kill_probability=sum(row[0] for row in branches if row[8]), score_gain=score,
        hit_probability=sum(chances) / len(chances),
        notes=("public_no_talent_forecast", "deferred_burn_not_scored", "crown_not_included"),
        expected_wear=sum(row[0] * row[3] for row in branches),
        deferred_hp=sum(row[0] * row[4] for row in branches),
        min_hp=min(row[1] for row in branches), max_hp=max(row[1] for row in branches),
    )


def _weapon_effect(
    view: Mapping[str, Any], actor_id: str, target_id: str, weapon: str, config: GaConfig,
    *, locked: bool,
) -> EffectSpec | None:
    """只从公开装备推导普通武器；不枚举角色技能，不探知未来抽样。"""
    entities = _entities(view)
    actor, target = entities.get(actor_id), entities.get(target_id)
    if actor is None or target is None or actor_id == target_id:
        return None
    if not all(p.get("alive") and p.get("awake") and p.get("home_locked") for p in (actor, target)):
        return None
    if actor.get("isolation_id") != target.get("isolation_id") or (
        weapon not in actor["weapons"] or weapon not in config.get("weapons") or
        weapon in actor.get("disabled_weapons", {}) or weapon == "gun_ammo"
    ):
        return None
    ranged = weapon in ("bow", "electromagnetic_rifle", "gauss_rifle", "hook")
    if not ranged and actor["location"] != target["location"]:
        return None
    if weapon == "hook" and actor.get("hook_cooldown", 0):
        return None
    modules = actor.get("modules", ())
    if weapon in ("bow", "electromagnetic_rifle", "gauss_rifle") and not (
        weapon == "bow" and "infinite" in modules
    ) and actor.get("ammo", {}).get(weapon, 0) < config.get("weapons", weapon, "ammo_per_attack"):
        return None
    amount = config.get("weapons", weapon, "damage")
    attribute = DamageAttribute.NORMAL
    params: dict[str, Any] = {}
    if weapon == "bow":
        attribute = DamageAttribute(actor.get("bow_attribute", "normal"))
        if "infinite" in modules:
            amount, attribute = config.get("modules", "infinite", "damage"), DamageAttribute.NORMAL
        else:
            for name in ("power", "fire", "magic", "pierce"):
                count = modules.count(name)
                if not count:
                    continue
                prefix = "single" if count == 1 else "double"
                if name == "power":
                    amount += config.get("modules", name, f"{prefix}_bonus")
                elif name == "fire":
                    params["burn_stacks"] = config.get("modules", name, f"{prefix}_burn")
                elif name == "magic":
                    params["hit_bonus"] = config.get("modules", name, f"{prefix}_hit_bonus")
                else:
                    params["defense_coefficient"] = config.get("modules", name,
                                                               f"{prefix}_coefficient")
    elif weapon in ("electromagnetic_rifle", "gauss_rifle", "hook"):
        attribute = DamageAttribute.TECH
        if weapon == "gauss_rifle" and actor.get("charged"):
            amount = config.get("weapons", weapon, "charged_damage")
    elif weapon in ("magic_barrage", "earthquake"):
        attribute = DamageAttribute.MAGIC
    if weapon == "knife" and actor.get("sharpened"):
        amount += config.get("weapons", "knife", "sharpen_bonus")
    if "WEAK" in actor.get("statuses", {}):
        amount = max(0, amount + config.get("status", "weakness"))
    area_targets = sum(p["alive"] and p["home_locked"] and key != actor_id and
                       p["location"] == actor["location"] and
                       p.get("isolation_id") == actor.get("isolation_id")
                       for key, p in view["players"].items())
    if locked and (weapon != "earthquake" or area_targets == 1):
        params["ignore_move_evasion"] = True
        params["hit_bonus"] = params.get("hit_bonus", 0) + config.get("accuracy", "lock_bonus")
        params["defense_coefficient"] = min(params.get("defense_coefficient", 1),
                                            config.get("accuracy", "lock_defense_coefficient"))
    visible = not target.get("hidden") or actor_id in target.get("revealed_to", ()) or (
        attribute is DamageAttribute.NORMAL and actor.get("inventory", {}).get("thermal", 0)
    ) or (attribute is DamageAttribute.TECH and actor.get("inventory", {}).get("radar", 0)) or (
        attribute is DamageAttribute.MAGIC and "detect_magic" in actor["weapons"])
    if weapon != "earthquake" and not visible:
        return None
    scope = TargetScope.LOCAL_AREA if weapon == "earthquake" else (
        TargetScope.RANGED_SINGLE if ranged else TargetScope.LOCAL_SINGLE)
    return EffectSpec(
        "forecast/effect", weapon, "forecast/root", "forecast/batch", actor_id, actor_id,
        actor_id, (target_id,), EffectKind.DAMAGE, DamageKind.NORMAL, amount, attribute, scope,
        HitPolicy.CERTAIN if weapon == "earthquake" else HitPolicy.ROLL,
        BarrierPolicy.RESPECT, IsolationPolicy.RESPECT, DeathPolicy.NORMAL, params,
    )


def estimate_attack(
    view: Mapping[str, Any], actor_id: str, target_id: str, weapon: str, config: GaConfig,
    *, locked: bool = False,
) -> AttackEstimate:
    """估算普通武器攻击；缺弹、不可见、异地近战等不会伪装成可用攻击。"""
    effect = _weapon_effect(view, actor_id, target_id, weapon, config, locked=locked)
    if effect is None:
        return AttackEstimate(available=False, notes=("weapon_or_target_unavailable",))
    return _estimate_effect(view, effect, config)


def estimate_option(
    view: Mapping[str, Any], descriptor: Mapping[str, Any], config: GaConfig,
) -> AttackEstimate:
    """当前候选按引擎实际声明效果估算，不把注册回调假装为已知净伤。"""
    effects = tuple(_effect(raw) for raw in descriptor.get("declared_effects", ()) if
                    raw["kind"] in ("damage", "wear"))
    if len(effects) != 1:
        return AttackEstimate(available=False, notes=("not_a_supported_single_packet",))
    return _estimate_effect(view, effects[0], config)


def _police_effect(
    view: Mapping[str, Any], target_id: str, config: GaConfig,
) -> tuple[EffectSpec | None, str]:
    """与正式编队资格一致；共享额度、受控射手和沉沦都来自公开事实。"""
    police = view.get("police")
    if not police:
        return None, "no_police"
    live = [key for key in police["officer_ids"] if view["units"][key]["alive"]]
    world = view["world"]
    if not live or "police" in world["destroyed_locations"] or police["stopped"] or (
        world["clock_paused"] or
        not any(not view["units"][key]["submerged_sources"] for key in live)
    ):
        return None, "police_unavailable"
    if police["attack_round"] == view["round"]:
        return None, "police_shared_round_allowance_spent"
    target = view["players"].get(target_id)
    if target is None or target_id not in police["wanted"] or not target["alive"] or (
        not target["home_locked"] or target["hidden"] or
        target["isolation_id"] != view["units"][live[0]]["isolation_id"]
    ):
        return None, "police_target_ineligible"
    shooters = [key for key in live if "control" not in view["units"][key]["statuses"] and
                not view["units"][key]["submerged_sources"]]
    if not shooters:
        return None, "police_no_uncontrolled_shooter"
    upgraded = bool(police["attack_upgrade"])
    amount = config.get("police", "gauss_volley_base" if upgraded else "volley_base")
    amount += len(live) * config.get("police", "volley_per_officer")
    captain = police["captain"]
    owner = captain if captain and view["players"][captain]["alive"] else None
    return EffectSpec(
        "forecast/police", "police/volley", "forecast/police-root", "forecast/police-batch",
        shooters[0], owner, None, (target_id,), EffectKind.DAMAGE, DamageKind.NORMAL, amount,
        DamageAttribute.TECH if upgraded else DamageAttribute.NORMAL, TargetScope.RANGED_SINGLE,
        HitPolicy.ROLL, BarrierPolicy.RESPECT, IsolationPolicy.RESPECT, DeathPolicy.NORMAL,
    ), "police_one_shared_volley"


def estimate_police_volley(
    view: Mapping[str, Any], target_id: str, config: GaConfig,
) -> AttackEstimate:
    """当前轮指令/移动编队协同的一次齐射；不因调用预测而消耗共享额度。"""
    effect, note = _police_effect(view, target_id, config)
    if effect is None:
        return AttackEstimate(available=False, notes=(note,))
    result = _estimate_effect(view, effect, config)
    return replace(result, notes=(*result.notes, note))


def estimate_police_response(
    view: Mapping[str, Any], config: GaConfig,
) -> tuple[str | None, str | None, AttackEstimate]:
    """R4按通缉队列选择第一合法目标；先前协同已用额度时不会再射击。"""
    police = view.get("police") or {}
    for target in police.get("wanted", ()):
        effect, note = _police_effect(view, target, config)
        if effect is not None:
            quote = _estimate_effect(view, effect, config)
            return target, effect.score_owner_id, replace(quote, notes=(*quote.notes, note))
    return None, None, AttackEstimate(available=False, notes=("no_available_R4_volley",))


def incoming_threat(view: Mapping[str, Any], actor_id: str, config: GaConfig) -> ThreatEstimate:
    """每敌一击的有界集火推演，逐支更新预测护甲；对手未行动不等于必会集火。"""
    target = view["players"][actor_id]
    if not target["alive"] or target["hp"] <= 0:
        return ThreatEstimate()
    attackers: dict[str, AttackEstimate] = {}
    effects = []
    for enemy_id, enemy in sorted(view["players"].items()):
        if enemy_id == actor_id or not enemy["alive"]:
            continue
        control = enemy.get("statuses", {}).get("control", {}).get("name")
        if control in ("PETRIFIED", "ACTION_SUPPRESSED", "SHOCKED"):
            continue
        candidates = []
        for weapon in sorted(enemy["weapons"]):
            locked = "DISORIENTED" not in enemy.get("statuses", {})
            effect = _weapon_effect(view, enemy_id, actor_id, weapon, config, locked=locked)
            if effect is not None:
                estimate = _estimate_effect(view, effect, config)
                candidates.append((estimate.expected_hp, estimate.max_hp, weapon, effect, estimate))
        if candidates:
            best = max(candidates, key=lambda row: row[:3])
            attackers[enemy_id] = best[4]
            effects.append(best[3])
    branches = [(1.0, target["hp"], _defenses(target))]
    for effect in effects:
        updated = []
        for probability, hp, defenses in branches:
            if hp <= 0:
                updated.append((probability, hp, defenses))
                continue
            _, outcomes = _target_outcomes(view, effect, actor_id, config,
                                           body={**target, "hp": hp}, defenses=defenses)
            updated.extend((probability * item.probability, hp - item.hp, item.defenses)
                           for item in outcomes)
        branches = updated
    return ThreatEstimate(
        sum(probability * (target["hp"] - hp) for probability, hp, _ in branches),
        max(target["hp"] - hp for _, hp, _ in branches),
        sum(probability for probability, hp, _ in branches if hp <= 0), attackers,
    )
