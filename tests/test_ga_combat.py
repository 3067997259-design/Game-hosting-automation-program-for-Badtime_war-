"""GA-008～015/020/037/040：使用正式GameState的伤害与资源行为验收。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from engine.game_state import GameState
from engine.ga.combat import attack_output, resolve_batch
from engine.ga.contracts import (
    BarrierPolicy, ContractError, DamageAttribute, DamageKind, DeathPolicy, DefenseKind,
    DefenseSpec, EffectKind, EffectSpec, HitOutcome, HitPolicy, IsolationPolicy, RoundPhase,
    TargetScope, TerminalReason,
)
from engine.ga.events import EventKind
from engine.ga.state import add_armor, finish_if_needed, record_death


def arena(players: int = 2, *, seed: int = 0) -> GameState:
    """公共建筑中的正式状态夹具；不初始化旧profile。"""
    state = GameState.new_ga([f"p{i}" for i in range(players)], seed=seed)
    state.current_round = state.world_tick = 1
    state.current_phase = RoundPhase.R3
    for player in state.players.values():
        player.awake = player.home_locked = True
        player.location = "shop"
    return state


def packet(
    key: str = "a", *, amount: int = 4, targets: tuple[str, ...] = ("p1",),
    kind: DamageKind = DamageKind.NORMAL, attribute: DamageAttribute = DamageAttribute.NORMAL,
    actor: str | None = "p0", **parameters: Any,
) -> EffectSpec:
    """显式声明效果权限与归因，无显示名隐式规则。"""
    return EffectSpec(key, "test", f"root/{key}", f"batch/{key}", actor, actor, actor,
                      targets, EffectKind.WEAR if kind is DamageKind.PURE_WEAR else (
                          EffectKind.DAMAGE
                      ), kind, amount, attribute,
                      TargetScope.GLOBAL_AREA if len(targets) > 1 else TargetScope.LOCAL_SINGLE,
                      HitPolicy.CERTAIN, BarrierPolicy.RESPECT, IsolationPolicy.RESPECT,
                      DeathPolicy.NORMAL, parameters)


def armor(state: GameState, *, defense: int, durability: int) -> str:
    """仅改变夹具前置耐久/防御，实际伤害仍经生产解析器。"""
    key = add_armor(state, "p1", "ceramic")
    current = state.players["p1"].defenses[key]
    state.players["p1"].defenses[key] = replace(
        current, durability=durability, max_durability=max(durability, current.max_durability),
        attribute_defense={attr: defense for attr in current.attribute_defense},
    )
    return key


def barrier(state: GameState, durability: int, kind: DefenseKind) -> None:
    """有限池和破碎整击防护使用同一公开防御合同。"""
    state.players["p1"].defenses["barrier"] = DefenseSpec(
        "barrier", kind, durability, durability,
        frozenset({DamageKind.NORMAL, DamageKind.DIRECT, DamageKind.PURE_WEAR}),
        frozenset(TargetScope),
    )


def test_ga008_ceramic_four_hits_use_durability_once() -> None:
    state = arena()
    key = armor(state, defense=3, durability=10)
    losses, wear = [], []
    for index in range(4):
        record, = resolve_batch(state, (packet(str(index)),))
        losses.append(record.hp_loss)
        wear.append(record.armor_changes[0].ordinary_wear)
    assert losses == [1, 1, 1, 3]
    assert wear == [3, 3, 3, 1]
    assert state.players["p1"].defenses[key].durability == 0
    record, = resolve_batch(state, (packet("after"),))
    assert record.hp_loss == 4 and record.armor_changes == ()


@pytest.mark.parametrize("damage,defense,durability,price,hp,worn", [
    (4, 3, 1, 1, 3, 1), (10, 6, 1, 1, 9, 1),
    (4, 3, 4, 2, 2, 4), (10, 6, 4, 2, 8, 4),
    (4, 3, 3, 2, 2, 3), (4, 1, 3, 2, 3, 2),
])
def test_ga009_tail_billing(
    damage: int, defense: int, durability: int, price: int, hp: int, worn: int,
) -> None:
    state = arena()
    armor(state, defense=defense, durability=durability)
    record, = resolve_batch(state, (packet(amount=damage, wear_price=price),))
    assert (record.hp_loss, record.armor_changes[0].ordinary_wear) == (hp, worn)


def test_counter_and_explicit_double_wear_take_maximum() -> None:
    state = arena()
    armor(state, defense=3, durability=3)
    record, = resolve_batch(state, (packet(attribute=DamageAttribute.TECH, wear_price=2),))
    assert (record.hp_loss, record.armor_changes[0].ordinary_wear) == (2, 3)


def test_ga010_choose_actual_reduction_no_relay() -> None:
    state = arena()
    first = armor(state, defense=6, durability=2)
    second = armor(state, defense=1, durability=10)
    record, = resolve_batch(state, (packet(amount=8),))
    assert record.hp_loss == 6
    assert record.armor_changes[0].defense_id == first
    assert state.players["p1"].defenses[second].durability == 10


def test_equal_armor_uses_cost_then_remaining_then_id() -> None:
    state = arena()
    first = armor(state, defense=2, durability=5)
    second = armor(state, defense=2, durability=8)
    state.players["p1"].defenses[second] = replace(
        state.players["p1"].defenses[second], wear_price=2
    )
    record, = resolve_batch(state, (packet(amount=8),))
    assert record.armor_changes[0].defense_id == first


def test_ga011_flat_reduction_is_not_billed_to_armor() -> None:
    state = arena()
    armor(state, defense=4, durability=10)
    state.players["p1"].flat_reduction = 4
    record, = resolve_batch(state, (packet(amount=8),))
    assert (record.hp_loss, record.armor_changes[0].ordinary_wear) == (2, 2)


@pytest.mark.parametrize("pool,expected", [(7, 1), (8, 0)])
def test_ga012_barrier_does_not_recreate_floor(pool: int, expected: int) -> None:
    state = arena()
    barrier(state, pool, DefenseKind.FINITE_BARRIER)
    record, = resolve_batch(state, (packet(amount=8),))
    assert record.hp_loss == expected


def test_ga013_graze_has_no_armor_wear_or_burn() -> None:
    state = arena(seed=2)
    key = armor(state, defense=6, durability=10)
    state.players["p1"].moved = True
    effect = replace(packet(amount=8, burn_stacks=2), hit_policy=HitPolicy.ROLL)
    record, = resolve_batch(state, (effect,))
    assert record.outcome is HitOutcome.GRAZE and record.hp_loss == 2
    assert state.players["p1"].defenses[key].durability == 10
    assert not state.players["p1"].burns
    assert {e.kind for e in state.ga_ledger.events} == {EventKind.EVADED, EventKind.HP_DAMAGED}


def test_attached_wear_does_not_recalculate_hp_or_relay() -> None:
    state = arena()
    armor(state, defense=3, durability=4)
    other = armor(state, defense=2, durability=10)
    record, = resolve_batch(state, (packet(amount=6, attached_wear=2),))
    assert record.hp_loss == 3
    assert (record.armor_changes[0].ordinary_wear, record.armor_changes[0].attached_wear) == (3, 1)
    assert state.players["p1"].defenses[other].durability == 10


@pytest.mark.parametrize("with_barrier", [False, True])
def test_ga014_pure_wear_discards_excess(with_barrier: bool) -> None:
    state = arena()
    key = armor(state, defense=3, durability=1)
    if with_barrier:
        barrier(state, 1, DefenseKind.FINITE_BARRIER)
    record, = resolve_batch(state, (packet(amount=9, kind=DamageKind.PURE_WEAR),))
    assert record.hp_loss == 0 and state.players["p1"].hp == 20
    assert state.players["p1"].defenses[key].durability == (1 if with_barrier else 0)
    assert state.players["p0"].pp == (0 if with_barrier else 1)


@pytest.mark.parametrize("kind,expected", [
    (DefenseKind.BREAKING_HIT_BARRIER, 0), (DefenseKind.FINITE_BARRIER, 7),
    (DefenseKind.HALO, 7),
])
def test_ga015_breaking_hit_privilege_is_explicit(kind: DefenseKind, expected: int) -> None:
    state = arena()
    barrier(state, 1, kind)
    record, = resolve_batch(state, (packet(amount=8),))
    assert record.hp_loss == expected and state.players["p0"].armor_breaks == 0


def test_direct_skips_armor_and_reduction_but_respects_barrier() -> None:
    state = arena()
    armor(state, defense=6, durability=10)
    barrier(state, 3, DefenseKind.FINITE_BARRIER)
    state.players["p1"].flat_reduction = 10
    record, = resolve_batch(state, (packet(amount=8, kind=DamageKind.DIRECT),))
    assert record.hp_loss == 5 and not record.armor_changes


def test_ga020_simultaneous_all_dead_not_iteration_winner() -> None:
    for targets in (("p0", "p1"), ("p1", "p0")):
        state = arena()
        resolve_batch(state, (packet(amount=30, targets=targets, actor=None,
                                     kind=DamageKind.DIRECT),))
        assert state.terminal_snapshot.reason is TerminalReason.ALL_DEAD
        assert state.winner is None and state.score_winners == ("p0", "p1")


def test_ga037_actual_hp_score_and_replay_terminal_once() -> None:
    state = arena()
    effect = packet(amount=99)
    first = resolve_batch(state, (effect,))
    events = state.ga_ledger.events
    draws = tuple(state.ga_rng.draws)
    saved = state.terminal_snapshot
    assert state.players["p0"].hp_damage == 20
    assert saved.results["p0"].total_score == 19
    assert resolve_batch(state, (effect,)) == first
    assert state.ga_ledger.events == events and state.terminal_snapshot is saved
    assert tuple(state.ga_rng.draws) == draws
    with pytest.raises(ContractError, match="conflicting"):
        resolve_batch(state, (replace(effect, amount=1),))


def test_invalid_atomic_group_leaves_hp_resources_and_rng_unchanged() -> None:
    state = arena()
    before = (state.players["p1"].hp, len(state.ga_rng.draws), state.ga_revision)
    bad = packet(targets=("p1", "missing"))
    with pytest.raises(ContractError, match="target"):
        resolve_batch(state, (bad,))
    assert (state.players["p1"].hp, len(state.ga_rng.draws), state.ga_revision) == before
    assert not state.ga_ledger.events


def test_attack_output_rounds_only_final_total() -> None:
    assert attack_output(3, 0.5, 1, 0) == 3


def test_armor_break_rewards_share_round_budget_but_count_each_real_break() -> None:
    state = arena(3)
    for target in ("p1", "p2"):
        key = add_armor(state, target, "shield")
        state.players[target].defenses[key] = replace(
            state.players[target].defenses[key], durability=1
        )
    effect = packet(amount=9, targets=("p1", "p2"), kind=DamageKind.PURE_WEAR)
    resolve_batch(state, (effect,))
    player = state.players["p0"]
    assert (player.armor_breaks, player.pp, player.sp, player.hp_damage) == (2, 1, 2, 0)
    finish_if_needed(state, round_limit=True)
    assert state.terminal_snapshot.results["p0"].total_score == 3


def test_zero_output_and_wear_without_armor_never_create_hp_floor() -> None:
    state = arena()
    assert resolve_batch(state, (packet(amount=0),))[0].hp_loss == 0
    record, = resolve_batch(state, (packet("wear", amount=20, kind=DamageKind.PURE_WEAR),))
    assert record.hp_loss == 0 and not state.ga_ledger.events


def test_immune_packet_does_not_consume_defense_or_roll_evasion() -> None:
    state = arena()
    barrier(state, 7, DefenseKind.FINITE_BARRIER)
    state.players["p1"].immune_kinds = frozenset({DamageKind.NORMAL})
    effect = replace(packet(amount=8), hit_policy=HitPolicy.ROLL)
    record, = resolve_batch(state, (effect,))
    assert record.outcome is HitOutcome.IMMUNE and not record.hp_loss
    assert state.players["p1"].defenses["barrier"].durability == 7
    assert not state.ga_rng.draws


def test_true_absolute_lethal_and_nonlethal_are_different() -> None:
    state = arena()
    effect = replace(packet(amount=4), death_policy=DeathPolicy.ABSOLUTE_IF_LETHAL)
    resolve_batch(state, (effect,))
    assert state.players["p1"].alive and not state.players["p1"].absolute_dead
    effect = replace(packet("lethal", amount=20), death_policy=DeathPolicy.ABSOLUTE_IF_LETHAL)
    resolve_batch(state, (effect,))
    assert state.players["p1"].absolute_dead and state.players["p0"].kills == 1


def test_daytime_loot_and_death_cleanup_are_idempotent() -> None:
    state = arena(3)
    state.world_tick = 5
    victim = state.players["p1"]
    victim.weapons.add("bow")
    victim.ammo["bow"] = 5
    key = add_armor(state, "p1", "ceramic")
    resolve_batch(state, (packet(amount=99),))
    pile = state.ground_loot["shop"][0]
    assert (victim.credits, victim.ammo, victim.defenses) == (0, {}, {})
    assert pile["credits"] == 4 and key in pile["defenses"]
    record_death(state, "p1", absolute=False)
    assert len(state.ground_loot["shop"]) == 1


def test_absolute_dead_wallet_stays_frozen_when_old_dot_scores_later() -> None:
    state = arena(4)
    owner = state.players["p0"]
    owner.pp = 3
    record_death(state, "p0", absolute=True)
    resolve_batch(state, (packet(amount=99, kind=DamageKind.DIRECT),))
    assert owner.kills == 1 and owner.hp_damage == 20
    assert owner.pp == 3 and owner.sp == 0
