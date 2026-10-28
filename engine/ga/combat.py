"""GA原生伤害解析及原子目标批次；不分派到M9或旧22步流水线。"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any, TYPE_CHECKING

from engine.ga.contracts import (
    ArmorChange, BarrierChange, BarrierPolicy, ContractError, DamageAttribute, DamageKind,
    DeathOutcome, DeathPolicy, DefenseKind, EffectKind, EffectSpec, HitOutcome, HitPolicy,
    ResolutionRecord, TargetScope, checked_ids, require_bool, require_id, require_int,
)
from engine.ga.state import (
    GaPlayer, award_events, finish_if_needed, record_death, stamp, touch, trace,
)
from engine.ga.status import STATUS_NAMES, add_burns, effective_hit, put_status
from engine.ga.extensions import Replacement, unit, units

if TYPE_CHECKING:
    from engine.game_state import GameState


def attack_output(base: int, multiplier: float, flat_bonus: int, weakness: int) -> int:
    """攻击方只在最终输出端向上取整一次。"""
    return max(0, math.ceil(base * multiplier + flat_bonus - weakness))


def _parameters(effect: EffectSpec) -> dict[str, Any]:
    """执行前完整校验效果载荷，拒绝未接入的权限和错类型。"""
    params = dict(effect.parameters)
    integers = {"attached_wear", "wear_price", "burn_stacks"}
    ratios = {"defense_coefficient", "hit_bonus"}
    flags = {"ignore_move_evasion", "ignore_range_penalty", "hostile", "fixed_pool"}
    if params.keys() - (integers | ratios | flags | {"armor_id", "statuses", "attack_side"}):
        raise ContractError("effect.parameters", "unsupported combat parameter")
    for key in integers & params.keys():
        require_int(params[key], f"effect.parameters.{key}", 1 if key == "wear_price" else 0)
    for key in ratios & params.keys():
        value = params[key]
        minimum = -1 if key == "hit_bonus" else 0
        if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= 1:
            raise ContractError(f"effect.parameters.{key}", "invalid bounded ratio")
    for key in flags & params.keys():
        require_bool(params[key], f"effect.parameters.{key}")
    if "armor_id" in params:
        require_id(params["armor_id"], "effect.parameters.armor_id")
    if "attack_side" in params and params["attack_side"] not in ("front", "back"):
        raise ContractError("effect.parameters.attack_side", "requires explicit front or back")
    if "statuses" in params:
        names = checked_ids(params["statuses"], "effect.parameters.statuses")
        if set(names) - STATUS_NAMES:
            raise ContractError("effect.parameters.statuses", "unsupported status")
    return params


def _clone(player: GaPlayer) -> GaPlayer:
    """复制本批工作数据，验证失败不留下半个AOE。"""
    return replace(player, defenses=dict(player.defenses), statuses=dict(player.statuses),
                   burns=list(player.burns))


def _resolve_target(
    state: GameState, effect: EffectSpec, player: GaPlayer,
) -> tuple[ResolutionRecord, tuple[Any, Replacement] | None]:
    """对工作副本结算一个目标，普通甲/屏障/生命各付一次。"""
    cfg = state.ga_config
    params = _parameters(effect)
    amount = effect.amount
    remaining = amount
    armor_changes: list[ArmorChange] = []
    barriers: list[BarrierChange] = []
    statuses: list[str] = []
    outcome = HitOutcome.HIT
    hp_before = player.hp
    computed = 0
    death = DeathOutcome.NONE
    replacement = None
    modifiers = tuple(callback(state, effect, player) for callback in state.ga_incoming_modifiers)
    ignored = frozenset(key for mod in modifiers for key in mod.ignored_defenses)
    immune = any(mod.immune for mod in modifiers)
    if any(mod.substituted for mod in modifiers) and effect.damage_kind is not DamageKind.EXECUTION:
        outcome = HitOutcome.SUBSTITUTED
        remaining = 0
    elif (immune or effect.damage_kind in player.immune_kinds or not player.targetable and (
        effect.scope in (TargetScope.LOCAL_SINGLE, TargetScope.RANGED_SINGLE)
    )) and (
        effect.damage_kind is not DamageKind.EXECUTION
    ):
        outcome = HitOutcome.IMMUNE
        remaining = 0
    elif effect.damage_kind is DamageKind.EXECUTION:
        computed = hp_before
    elif amount:
        pools = sorted((d for d in player.defenses.values() if d.durability and
                        d.defense_id not in ignored and
                        d.kind is not DefenseKind.ARMOR and
                        effect.damage_kind in d.accepted_damage_kinds and
                        effect.scope in d.accepted_scopes),
                       key=lambda d: (d.kind is DefenseKind.HALO, d.defense_id))
        if effect.barrier_policy is BarrierPolicy.RESPECT:
            for defense in pools:
                absorbed = remaining if defense.protects_breaking_hit else min(
                    remaining, defense.durability
                )
                after = defense.durability - min(defense.durability, absorbed)
                barriers.append(BarrierChange(defense.defense_id, defense.kind,
                                              defense.durability, after, absorbed))
                player.defenses[defense.defense_id] = replace(defense, durability=after)
                remaining -= absorbed
                if effect.damage_kind is DamageKind.PURE_WEAR or not remaining:
                    break
        if effect.damage_kind is DamageKind.PURE_WEAR:
            if not barriers:
                candidates = [d for d in player.defenses.values() if d.durability and
                              d.kind is DefenseKind.ARMOR and
                              effect.damage_kind in d.accepted_damage_kinds and
                              effect.scope in d.accepted_scopes]
                if "armor_id" in params:
                    candidates = [d for d in candidates if d.defense_id == params["armor_id"]]
                if candidates:
                    defense = min(candidates, key=lambda d: (-d.durability, d.defense_id))
                    worn = min(amount, defense.durability)
                    after = defense.durability - worn
                    armor_changes.append(ArmorChange(defense.defense_id, defense.durability,
                                                     after, pure_wear=worn))
                    player.defenses[defense.defense_id] = replace(defense, durability=after)
            remaining = 0
            if barriers:
                outcome = HitOutcome.BLOCKED
        elif not remaining:
            outcome = HitOutcome.BLOCKED
        else:
            grazed = False
            if effect.hit_policy is HitPolicy.ROLL:
                actor = units(state).get(effect.actor_id)
                cross = bool(actor and actor.location != player.location)
                ignore_move = params.get("ignore_move_evasion", False)
                chance = cfg.get("accuracy", "base") + params.get("hit_bonus", 0)
                chance -= cfg.get("accuracy", "move_evasion") if player.moved and (
                    not ignore_move
                ) else 0
                chance -= cfg.get("accuracy", "stealth_evasion") if player.hidden else 0
                chance -= cfg.get("accuracy", "unlocked_cross_location_penalty") if (
                    cross and not ignore_move and not params.get("ignore_range_penalty", False)
                ) else 0
                chance -= cfg.get("consumables", "smoke_hit_penalty") if cross and (
                    player.location in state.ga_smoke or actor.location in state.ga_smoke
                ) else 0
                chance -= cfg.get("consumables", "flash_hit_penalty") if actor and (
                    "FLASHED" in actor.statuses
                ) else 0
                chance = min(cfg.get("accuracy", "maximum"),
                             max(cfg.get("accuracy", "minimum"), chance))
                grazed = not state.ga_rng.chance(f"{effect.effect_id}/{player.player_id}", chance,
                                                actor_id=effect.actor_id)
            floor = min(remaining, math.ceil(amount * cfg.get("combat", "min_damage_ratio")))
            if grazed:
                outcome = HitOutcome.GRAZE
                computed = remaining if effect.damage_kind is DamageKind.DIRECT else floor
            elif effect.damage_kind is DamageKind.DIRECT:
                computed = remaining
            else:
                vulnerability = cfg.get("status", "vulnerability") if (
                    "VULNERABLE" in player.statuses
                ) else 0
                body = (remaining + vulnerability + sum(m.vulnerability for m in modifiers) -
                        player.flat_reduction - sum(m.reduction for m in modifiers))
                candidates = []
                for defense in player.defenses.values():
                    if not defense.durability or defense.kind is not DefenseKind.ARMOR or (
                        effect.damage_kind not in defense.accepted_damage_kinds or
                        effect.scope not in defense.accepted_scopes
                    ):
                        continue
                    if effect.attribute is DamageAttribute.MYSTERIOUS:
                        nominal = min(defense.attribute_defense.values())
                    else:
                        nominal = defense.attribute_defense.get(effect.attribute, 0)
                    nominal = math.floor(nominal * params.get("defense_coefficient", 1))
                    price = max(defense.wear_price, params.get("wear_price", 1))
                    affinity = player.armor_affinities.get(defense.defense_id)
                    counters = {DamageAttribute.NORMAL: DamageAttribute.MAGIC,
                                DamageAttribute.MAGIC: DamageAttribute.TECH,
                                DamageAttribute.TECH: DamageAttribute.NORMAL}
                    if counters.get(effect.attribute) is affinity and affinity is not None:
                        price = max(price, cfg.get("combat", "counter_wear_price"))
                    if effect.attribute is DamageAttribute.MYSTERIOUS:
                        price = max(price, cfg.get("combat", "mysterious_wear_price"))
                    reduction = min(nominal, max(0, body - floor),
                                    -(-defense.durability // price))
                    wear = min(defense.durability, price * reduction)
                    candidates.append((-reduction, wear, -defense.durability,
                                       defense.defense_id, defense))
                chosen = min(candidates, key=lambda item: item[:4]) if candidates else None
                computed = max(body + chosen[0] if chosen else body, floor)
                if chosen:
                    defense = chosen[4]
                    ordinary = chosen[1]
                    attached = min(params.get("attached_wear", 0), defense.durability - ordinary)
                    after = defense.durability - ordinary - attached
                    if ordinary or attached:
                        armor_changes.append(ArmorChange(defense.defense_id, defense.durability,
                                                         after, ordinary, attached))
                        player.defenses[defense.defense_id] = replace(defense, durability=after)
            if outcome is HitOutcome.HIT:
                for name in params.get("statuses", ()):
                    if put_status(player, cfg, state.current_round, name,
                                  source_id=effect.effect_id, actor_id=effect.actor_id,
                                  score_owner_id=effect.score_owner_id,
                                  crime_owner_id=effect.crime_owner_id):
                        statuses.append(name)
                if params.get("burn_stacks", 0):
                    add_burns(player, cfg, state.current_round, params["burn_stacks"],
                              source_id=effect.effect_id, actor_id=effect.actor_id,
                              score_owner_id=effect.score_owner_id,
                              crime_owner_id=effect.crime_owner_id)
                    statuses.append("BURN")
    if computed and effect.damage_kind is not DamageKind.EXECUTION:
        computed = max(0, math.ceil(computed * min((m.hp_ratio for m in modifiers), default=1)) -
                       sum(m.hp_reduction for m in modifiers))
    loss = min(hp_before, computed)
    player.hp -= loss
    if not player.hp:
        death = DeathOutcome.DEAD if effect.death_policy is DeathPolicy.NORMAL else (
            DeathOutcome.ABSOLUTE_DEAD
        )
        if death is DeathOutcome.DEAD:
            for gate in sorted(state.ga_death_gates, key=lambda g: g.priority):
                preview = gate.preview(state, effect, player)
                if preview is not None:
                    if preview.hp <= 0 or preview.outcome not in (
                        DeathOutcome.MORPHED, DeathOutcome.RESURRECTED
                    ):
                        raise ContractError("replacement", "invalid positive-HP death gate")
                    player.hp = preview.hp
                    death = preview.outcome
                    replacement = gate, preview
                    break
    record = ResolutionRecord(
        f"{effect.effect_id}/{player.player_id}", effect, stamp(state), player.player_id,
        outcome, remaining, computed, hp_before, loss, tuple(armor_changes), tuple(barriers),
        tuple(statuses), death,
    )
    if record.effective_hit and "PETRIFIED" not in statuses:
        effective_hit(player, cfg)
    return record, replacement


def resolve_batch(
    state: GameState, effects: tuple[EffectSpec, ...],
) -> tuple[ResolutionRecord, ...]:
    """完整批次先验证再落地；同批重放不抽骰、不扣血、不再发奖。"""
    if not effects or any(type(effect) is not EffectSpec for effect in effects):
        raise ContractError("effects", "requires a non-empty EffectSpec batch")
    first = effects[0]
    original_effects = effects
    previous = state.ga_resolution_batches.get(first.batch_id)
    if previous:
        if previous[0] != effects:
            raise ContractError("batch_id", "conflicting combat replay")
        return previous[1]
    if state.game_over:
        raise ContractError("combat", "cannot change terminal state")
    for callback in tuple(state.ga_effect_filters):
        effects = callback(state, effects)
    if tuple((e.effect_id, e.batch_id, e.root_id) for e in effects) != tuple(
        (e.effect_id, e.batch_id, e.root_id) for e in original_effects
    ):
        raise ContractError("effects", "effect filters cannot change batch identities")
    if len({e.effect_id for e in effects}) != len(effects) or any(
        (e.batch_id, e.root_id) != (first.batch_id, first.root_id) for e in effects
    ):
        raise ContractError("effects", "atomic batch requires unique IDs, one root and batch")
    working = {}
    for effect in effects:
        _parameters(effect)
        if effect.kind not in (EffectKind.DAMAGE, EffectKind.WEAR):
            raise ContractError("effect.kind", "combat accepts only damage and wear")
        for owner in (effect.actor_id, effect.score_owner_id, effect.crime_owner_id):
            if owner is not None and owner not in units(state):
                raise ContractError("effect.owner", "unregistered owner")
        actor = units(state).get(effect.actor_id)
        for target in effect.target_ids:
            player = units(state).get(target)
            if player is None or not player.alive or not player.home_locked:
                raise ContractError("effect.target_ids", "target is dead, missing or in safe room")
            if actor and effect.scope in (TargetScope.LOCAL_SINGLE, TargetScope.LOCAL_AREA) and (
                actor.location != player.location
            ):
                raise ContractError("effect.scope", "local target is at another location")
            if actor and actor.isolation_id != player.isolation_id:
                raise ContractError("effect.isolation", "effect cannot cross isolation")
            working.setdefault(target, _clone(player))
    records = []
    replacements = []
    contexts = {}
    for effect in effects:
        for target in effect.target_ids:
            if not working[target].alive:
                raise ContractError("effects", "independent lethal segments need separate batches")
            control = working[target].statuses.get("control")
            contexts[f"{effect.effect_id}/{target}"] = {
                "control": control.name if control else None,
                "control_actor": control.actor_id if control else None,
                "talent_form": state.ga_roles[target].public().get("form") if target in
                state.ga_roles else None,
                "armor_kinds": dict(working[target].armor_kinds),
            }
            record, replacement = _resolve_target(state, effect, working[target])
            records.append(record)
            if replacement:
                replacements.append((effect, target, replacement))
    result = tuple(records)
    state.ga_record_context.update(contexts)
    for target, player in working.items():
        actual = unit(state, target)
        actual.hp, actual.defenses = player.hp, player.defenses
        actual.statuses, actual.burns = player.statuses, player.burns
    for effect in effects:
        hostile = effect.parameters.get("hostile", True)
        if hostile and effect.actor_id is not None and any(t != effect.actor_id for t in
                                                         effect.target_ids):
            unit(state, effect.actor_id).engaged = True
            for target in effect.target_ids:
                if target != effect.actor_id:
                    unit(state, target).engaged = True
        elif effect.score_owner_id is not None:
            for target in effect.target_ids:
                if target != effect.score_owner_id:
                    unit(state, target).engaged = True
    for effect, target, (gate, replacement) in replacements:
        gate.commit(state, effect, unit(state, target), replacement)
    for record in records:
        if record.death_outcome in (DeathOutcome.DEAD, DeathOutcome.ABSOLUTE_DEAD):
            record_death(state, record.target_id,
                         absolute=record.death_outcome is DeathOutcome.ABSOLUTE_DEAD)
    state.ga_resolution_batches[first.batch_id] = (original_effects, result)
    events = state.ga_ledger.commit_resolutions(result)
    state.ga_attention_blocked_events.update(f"{r.resolution_id}/hp" for r in result if
                                             r.death_outcome is DeathOutcome.RESURRECTED)
    award_events(state, events)
    for callback in tuple(state.ga_batch_listeners):
        callback(state, result)
    for callback in tuple(state.ga_batch_finalizers.pop(first.batch_id, ())):
        callback(state)
    touch(state)
    trace(state, "combat_batch", batch=first.batch_id, targets=[r.target_id for r in records])
    if not finish_if_needed(state):
        while state.ga_after_batch_queue and not state.game_over:
            callback = state.ga_after_batch_queue.pop(0)
            callback(state)
    return result
