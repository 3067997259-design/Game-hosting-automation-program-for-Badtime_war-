"""I2往世按钮、固定池守恒、支付同意与七枚有限Encore标记。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from engine.game_state import GameState
from engine.ga.actions import GrantKind
from engine.ga.afterlife import Afterlife
from engine.ga.contracts import DamageKind, Performance, RoundPhase, TurnStage
from engine.ga.combat import resolve_batch
from engine.ga.gameplay import legal_actions, validate_request
from engine.ga.runtime import run_round
from engine.ga.state import add_armor
from engine.ga.status import add_burns
from engine.ga.talents.base import ability, packet
from tests.ga_i2_helpers import Decisions, execute, ready, turn


def kill(state: GameState, target: str) -> None:
    """实际玩家普通攻击制造普通死者，未直接修改往世资格。"""
    if target in state.ga_roles and state.ga_roles[target].talent_id == "G0":
        state.ga_roles[target].breath_used = state.ga_config.get("talents", "G0", "breath_uses")
    state.players[target].hp = 1
    turn(state, "b")
    execute(state, "attack", targets=(target,), weapon="knife")


def window(state: GameState, actor: str, *, advance: bool = True) -> None:
    """只在下一R0开放金融/支援窗口，不能在看到结果后改押。"""
    if advance:
        state.current_round += 1
        state.world_tick += 1
    state.current_phase = RoundPhase.R0
    turn(state, actor, TurnStage.T0, kind=GrantKind.AFTERLIFE)


def gifted(mark: str, *, receiver: str | None = None) -> GameState:
    """通过真实死者Encore按钮借出，非直接塞标记。"""
    slots = {"c": "G1"}
    if receiver:
        slots["a"] = receiver
    state = ready(slots, count=4)
    kill(state, "c")
    window(state, "c")
    execute(state, "afterlife_encore", targets=("a",), kind=mark)
    return state


def test_bet_only_at_r0_and_future_kill_crown_pools_conserve_total() -> None:
    state = ready(count=4)
    kill(state, "c")
    kill(state, "d")
    before = {p: state.players[p].pp for p in ("c", "d")}
    window(state, "c")
    execute(state, "afterlife_bet", targets=("a",))
    window(state, "d", advance=False)
    execute(state, "afterlife_bet", targets=("a",))
    state.players["b"].hp = 1
    turn(state, "a")
    execute(state, "attack", targets=("b",), weapon="knife")
    assert state.winner == "a"
    gained = {p: state.players[p].pp - before[p] for p in before}
    assert gained == {"c": 2, "d": 2}
    assert sum(gained.values()) == 4
    assert state.terminal_snapshot.results["c"].pp_balance == state.players["c"].pp
    turn(state, "c", TurnStage.T0, kind=GrantKind.AFTERLIFE)
    assert not legal_actions(state)


def test_armor_bet_pool_once_per_actor_round_remainder_rotates() -> None:
    state = ready(count=4)
    kill(state, "c")
    kill(state, "d")
    window(state, "c")
    execute(state, "afterlife_bet", targets=("a",))
    window(state, "d", advance=False)
    execute(state, "afterlife_bet", targets=("a",))
    before = sum(state.players[p].pp for p in ("c", "d"))
    for round_index in range(2):
        if round_index:
            state.current_round += 1
            state.world_tick += 1
        state.current_phase = RoundPhase.R3
        for hit in range(2):
            key = add_armor(state, "b", "shield")
            defense = state.players["b"].defenses[key]
            state.players["b"].defenses[key] = replace(defense, durability=1)
            turn(state, "a")
            request = ability(state.current_grant, TurnStage.T1, "fixture_wear",
                              targets=("b",)).request
            resolve_batch(state, (packet(state, request, 1, kind=DamageKind.PURE_WEAR),))
    assert sum(state.players[p].pp for p in ("c", "d")) - before == 2
    assert state.players["c"].pp == state.players["d"].pp


def test_glow_actual_payment_and_beneficiary_once_per_round() -> None:
    state = ready(count=4)
    kill(state, "c")
    kill(state, "d")
    state.players["c"].pp = state.players["d"].pp = 5
    window(state, "c")
    execute(state, "afterlife_glow", targets=("a",), kind="barrier", payer="c")
    assert state.players["c"].pp == 3 and state.players["a"].defenses[
        "afterlife/glow/a"].durability == 3
    window(state, "d", advance=False)
    assert not any(a.operation == "afterlife_glow" and a.target_ids == ("a",)
                   for a in legal_actions(state))


def test_aid_decline_no_transfer_accept_exact_two_pp_no_mint() -> None:
    state = ready(count=4)
    kill(state, "c")
    state.players["c"].pp = 0
    state.players["a"].pp = 3
    state.players["a"].hp = 10
    state.ga_controllers["a"] = Decisions(microglow_aid=["decline", "accept"])
    window(state, "c")
    declined = execute(state, "afterlife_glow", targets=("a",), kind="barrier", payer="a")
    assert declined.cancelled and state.players["a"].pp == 3 and state.players["c"].pp == 0
    assert "c" not in state.ga_afterlife.glow_round
    execute(state, "afterlife_glow", targets=("a",), kind="heal", payer="a")
    assert state.players["a"].pp == 1 and state.players["c"].pp == 0
    assert state.players["a"].hp == 12


def test_revelation_expires_next_r0_and_gaze_is_single_incoming_bonus() -> None:
    state = ready(count=4)
    kill(state, "c")
    state.players["c"].pp = 6
    window(state, "c")
    execute(state, "afterlife_glow", targets=("a",), kind="revelation", payer="c")
    assert state.players["a"].initiative_bonus == 2 and state.players["a"].sp == 2
    run_round(state, state.ga_controllers)
    assert state.players["a"].initiative_bonus == 0
    state.players["b"].hidden = True
    state.current_phase = RoundPhase.R0
    state.current_round += 1
    state.world_tick += 1
    turn(state, "c", TurnStage.T0, kind=GrantKind.AFTERLIFE)
    execute(state, "afterlife_glow", targets=("b",), kind="gaze", payer="c")
    assert not state.players["b"].hidden
    turn(state, "a")
    execute(state, "attack", targets=("b",), weapon="knife")
    assert state.players["b"].hp == 14 and "b" not in state.ga_afterlife.gaze


def test_absolute_and_retired_actors_have_no_afterlife_candidates() -> None:
    state = ready(count=4)
    kill(state, "c")
    window(state, "c")
    state.players["c"].absolute_dead = True
    assert not legal_actions(state)
    state.players["c"].absolute_dead = False
    state.players["c"].exited = True
    assert not legal_actions(state)


def test_ranger_single_chase_is_not_extra_turn() -> None:
    state = gifted("ranger")
    state.players["b"].location = "magic"
    turn(state, "a")
    execute(state, "encore_chase", targets=("b",), weapon="knife")
    assert state.players["a"].location == "magic" and state.players["b"].hp == 16
    assert "ranger" not in state.ga_afterlife.marks["a"]
    assert not state.ga_pending_extras


def test_stars_single_bounce_shares_root_and_does_not_recurse() -> None:
    state = gifted("stars")
    turn(state, "a")
    receipt = execute(state, "attack", targets=("b",), weapon="knife")
    assert state.players["b"].hp == 16 and state.players["d"].hp == 18
    assert state.players["a"].hp_damage == 6
    roots = {record.effect.root_id for _, records in state.ga_resolution_batches.values()
             for record in records if record.effect.actor_id == "a"}
    assert roots == {receipt.request.root_id}
    assert "stars" not in state.ga_afterlife.marks["a"]


def test_yin_yang_one_explicit_reroll_no_recursive_sampling() -> None:
    state = gifted("yin_yang")
    state.ga_controllers["a"] = Decisions(encore_reroll=["second"])
    before = len(state.ga_rng.draws)
    value = state.ga_rng.die("a/probe", 6, actor_id="a")
    assert len(state.ga_rng.draws) == before + 2
    assert value == state.ga_rng.draws[-1][1]
    assert "yin_yang" not in state.ga_afterlife.marks["a"]


def test_eternity_quote_is_pure_and_consumes_once_on_legal_ammo_payment() -> None:
    state = gifted("eternity")
    turn(state, "a")
    first = next(a for a in legal_actions(state) if a.operation == "attack" and
                 a.parameters["weapon"] == "bow" and a.target_ids == ("b",))
    plan = validate_request(state, first)
    assert plan.accepted and all(c.amount == 0 for c in plan.costs)
    assert "eternity" in state.ga_afterlife.marks["a"]
    execute(state, "attack", targets=("b",), weapon="bow")
    assert state.players["a"].ammo["bow"] == 5
    assert "eternity" not in state.ga_afterlife.marks["a"]


def test_firefly_reduces_one_real_dot_then_is_consumed() -> None:
    state = gifted("firefly")
    add_burns(state.players["a"], state.ga_config, state.current_round, 1, source_id="test/burn",
              actor_id="a", score_owner_id="a")
    run_round(state, state.ga_controllers)
    assert state.players["a"].hp == 20
    assert "firefly" not in state.ga_afterlife.marks["a"]
    run_round(state, state.ga_controllers)
    assert state.players["a"].hp == 19


def test_spotlight_adds_once_and_cannot_copy_to_future_attack() -> None:
    state = gifted("spotlight")
    turn(state, "a")
    execute(state, "attack", targets=("b",), weapon="knife")
    assert state.players["b"].hp == 15
    turn(state, "a")
    execute(state, "attack", targets=("b",), weapon="knife")
    assert state.players["b"].hp == 11


def test_tomorrow_can_waive_g0_positive_hp_fee_at_hp_one_only_once() -> None:
    state = gifted("tomorrow", receiver="G0")
    state.players["a"].hp = state.players["a"].sp = 1
    turn(state, "a", TurnStage.T0)
    receipt = execute(state, "g0_drone")
    assert all(c.amount == 0 for c in receipt.costs if c.account == "hp")
    assert state.players["a"].hp == 1 and state.ga_units["npc/drone/a"].hp == 4
    assert "tomorrow" not in state.ga_afterlife.marks["a"]
    state.ga_units["npc/drone/a"].hp = 0
    turn(state, "a", TurnStage.T0)
    assert not any(a.operation == "g0_drone" for a in legal_actions(state))


@pytest.mark.parametrize("slot,operation", [("T1", "encore_T1"), ("T2", "encore_T2"),
                                             ("T3", "encore_T3"), ("T7", "encore_T7")])
def test_original_encore_borrows_core_not_passive_or_insurance(slot: str, operation: str) -> None:
    state = ready({"c": slot}, count=4)
    kill(state, "c")
    window(state, "c")
    execute(state, "afterlife_encore", targets=("a",), kind=slot)
    stage = TurnStage.T0 if slot == "T3" else TurnStage.T1
    state.players["a"].sp = 1
    state.players["b"].hp = 10
    turn(state, "a", stage)
    if slot == "T1":
        execute(state, operation, targets=("b",), weapon="knife")
    elif slot == "T2":
        before = state.players["a"].credits
        execute(state, operation, purchase="buy_item", kind="thermal")
        assert state.players["a"].inventory["thermal"] == 1
        assert state.players["a"].credits == before
        assert not state.players["a"].retain_stealth_attacks
        assert not state.players["a"].inventory.get("cloak")
        assert not state.players["a"].chapters
    elif slot == "T7":
        execute(state, operation, targets=("b",))
        assert state.ga_insurance is None and state.players["b"].hp == 13
    else:
        execute(state, operation)
        assert state.players["a"].defenses["t3/jade/a"].durability == 3
    assert not state.ga_afterlife.borrowed["a"] and "c" in state.ga_afterlife.encore_used


def test_borrowed_hexagram_can_issue_extra_turn() -> None:
    state = ready({"c": "T4"}, count=4)
    kill(state, "c")
    window(state, "c")
    execute(state, "afterlife_encore", targets=("a",), kind="T4")
    state.ga_controllers["a"] = Decisions(encore_hexagram=["scissors"])
    state.ga_controllers["b"] = Decisions(encore_hexagram=["paper"])
    turn(state, "a")
    execute(state, "encore_T4", targets=("b",))
    assert state.ga_pending_extras == {"a": "t4_or_yue"}
