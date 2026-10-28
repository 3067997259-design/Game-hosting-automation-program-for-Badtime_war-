"""正式伤害与公开预测共享的纯数学；不持有状态、不支付、不抽随机数。"""

from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

from engine.ga.config import GaConfig
from engine.ga.contracts import (
    ArmorChange, BarrierChange, BarrierPolicy, DamageAttribute, DamageKind, DefenseKind,
    DefenseSpec, EffectSpec,
)


def hit_probability(
    config: GaConfig, parameters: Mapping[str, Any], *, moved: bool, hidden: bool,
    cross_location: bool, smoke: bool, flashed: bool,
) -> float:
    """只计算命中概率；同一锁定标志同时免除移动和跨地点惩罚。"""
    ignore_move = parameters.get("ignore_move_evasion", False)
    chance = config.get("accuracy", "base") + parameters.get("hit_bonus", 0)
    if moved and not ignore_move:
        chance -= config.get("accuracy", "move_evasion")
    if hidden:
        chance -= config.get("accuracy", "stealth_evasion")
    if cross_location and not ignore_move and not parameters.get("ignore_range_penalty", False):
        chance -= config.get("accuracy", "unlocked_cross_location_penalty")
    if cross_location and smoke:
        chance -= config.get("consumables", "smoke_hit_penalty")
    if flashed:
        chance -= config.get("consumables", "flash_hit_penalty")
    return min(config.get("accuracy", "maximum"), max(config.get("accuracy", "minimum"), chance))


def absorb_barriers(
    effect: EffectSpec, defenses: Mapping[str, DefenseSpec], *,
    ignored: frozenset[str] = frozenset(),
) -> tuple[int, tuple[BarrierChange, ...]]:
    """有限池先于命中；返回变更，由调用者决定是否应用到真实状态。"""
    remaining = effect.amount
    changes = []
    pools = sorted((d for d in defenses.values() if d.durability and
                    d.defense_id not in ignored and d.kind is not DefenseKind.ARMOR and
                    effect.damage_kind in d.accepted_damage_kinds and
                    effect.scope in d.accepted_scopes),
                   key=lambda d: (d.kind is DefenseKind.HALO, d.defense_id))
    if effect.barrier_policy is BarrierPolicy.RESPECT:
        for defense in pools:
            absorbed = remaining if defense.protects_breaking_hit else min(
                remaining, defense.durability)
            after = defense.durability - min(defense.durability, absorbed)
            changes.append(BarrierChange(defense.defense_id, defense.kind,
                                         defense.durability, after, absorbed))
            remaining -= absorbed
            if effect.damage_kind is DamageKind.PURE_WEAR or not remaining:
                break
    return remaining, tuple(changes)


def armor_hit(
    config: GaConfig, effect: EffectSpec, defenses: Mapping[str, DefenseSpec],
    affinities: Mapping[str, DamageAttribute], *, body: int, floor: int,
) -> tuple[int, ArmorChange | None]:
    """普通命中择一甲、尾数计费与附加磨甲；不把磨甲溢出另加到生命伤害。"""
    params = effect.parameters
    candidates = []
    for defense in defenses.values():
        if not defense.durability or defense.kind is not DefenseKind.ARMOR or (
            effect.damage_kind not in defense.accepted_damage_kinds or
            effect.scope not in defense.accepted_scopes
        ):
            continue
        nominal = min(defense.attribute_defense.values()) if effect.attribute is (
            DamageAttribute.MYSTERIOUS) else defense.attribute_defense.get(effect.attribute, 0)
        nominal = math.floor(nominal * params.get("defense_coefficient", 1))
        price = max(defense.wear_price, params.get("wear_price", 1))
        affinity = affinities.get(defense.defense_id)
        counters = {DamageAttribute.NORMAL: DamageAttribute.MAGIC,
                    DamageAttribute.MAGIC: DamageAttribute.TECH,
                    DamageAttribute.TECH: DamageAttribute.NORMAL}
        if counters.get(effect.attribute) is affinity and affinity is not None:
            price = max(price, config.get("combat", "counter_wear_price"))
        if effect.attribute is DamageAttribute.MYSTERIOUS:
            price = max(price, config.get("combat", "mysterious_wear_price"))
        reduction = min(nominal, max(0, body - floor), -(-defense.durability // price))
        wear = min(defense.durability, price * reduction)
        candidates.append((-reduction, wear, -defense.durability, defense.defense_id, defense))
    chosen = min(candidates, key=lambda item: item[:4]) if candidates else None
    computed = max(body + chosen[0] if chosen else body, floor)
    if chosen is None:
        return computed, None
    defense, ordinary = chosen[4], chosen[1]
    attached = min(params.get("attached_wear", 0), defense.durability - ordinary)
    if not ordinary and not attached:
        return computed, None
    return computed, ArmorChange(defense.defense_id, defense.durability,
                                 defense.durability - ordinary - attached, ordinary, attached)
