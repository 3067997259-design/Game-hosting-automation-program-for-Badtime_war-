"""I2神代三人：正式动作、有限资源、优先死亡门和固定总池。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from engine.game_state import GameState
from engine.ga.contracts import Performance, RoundPhase, TurnStage
from engine.ga.gameplay import legal_actions
from engine.ga.runtime import run_game, run_round
from engine.ga.state import add_armor
from engine.ga.talents.base import barrier
from tests.ga_i2_helpers import Decisions, execute, ready, turn


def test_g0_wake_replaces_bow_and_automatic_fire_pays_ammo_attached_wear() -> None:
    state = ready({"a": "G0"})
    player = state.players["a"]
    assert "ar" in player.weapons and "bow" not in player.weapons
    assert player.ammo == {"ar": 15} and len(state.ga_roles["a"].relics) == 4
    key = add_armor(state, "b", "ceramic")
    player.sp = 0
    turn(state, "a")
    execute(state, "g0_ar", targets=("b",), fire_mode="auto")
    assert player.ammo["ar"] == 12
    assert state.players["b"].hp == 17
    assert state.players["b"].defenses[key].durability == 5


def test_g0_drone_is_real_npc_no_player_score_or_crime_focus_owner() -> None:
    state = ready({"a": "G0"})
    turn(state, "a", TurnStage.T0)
    execute(state, "g0_drone")
    assert state.players["a"].hp == 16 and state.ga_units["npc/drone/a"].hp == 4
    run_round(state, state.ga_controllers)
    state.players["a"].sp = 0
    turn(state, "b")
    execute(state, "npc_attack", targets=("npc/drone/a",), weapon="bow")
    assert not state.ga_units["npc/drone/a"].alive and state.players["a"].sp == 1
    assert state.players["b"].kills == state.players["b"].hp_damage == 0
    assert not state.ga_crimes


def test_g0_relic_shield_only_refreshes_at_r0_not_remount() -> None:
    state = ready({"a": "G0"})
    turn(state, "a", TurnStage.T0)
    execute(state, "g0_mount", relics=("shield",))
    assert "g0/relic-shield/a" not in state.players["a"].defenses
    run_round(state, state.ga_controllers)
    assert state.players["a"].defenses["g0/relic-shield/a"].durability == 2
    turn(state, "b")
    execute(state, "attack", targets=("a",), weapon="knife")
    turn(state, "a", TurnStage.T0)
    execute(state, "g0_mount", relics=())
    turn(state, "a", TurnStage.T0)
    execute(state, "g0_mount", relics=("shield",))
    assert "g0/relic-shield/a" not in state.players["a"].defenses


def test_g0_drone_mystery_shot_and_glasses_actual_paid_hp_recovery() -> None:
    state = ready({"a": "G0"})
    turn(state, "a", TurnStage.T0)
    execute(state, "g0_mount", relics=("glasses", "sight"))
    state.players["a"].sp = 1
    turn(state, "a", TurnStage.T0)
    execute(state, "g0_drone")
    key = add_armor(state, "b", "ceramic")
    turn(state, "a")
    execute(state, "g0_ar", targets=("b",), fire_mode="point")
    assert state.players["b"].hp == 15
    assert state.players["b"].defenses[key].durability == 10
    state.current_phase = RoundPhase.R4
    state.ga_roles["a"].boundary(state, boundary="R4.recovery")
    assert state.players["a"].hp == 17
    assert {"chapter/1", "chapter/2"} <= state.players["a"].chapters


def test_g0_crossfire_terminal_still_destroys_drone_relic_and_applies_safe_hp() -> None:
    state = ready({"a": "G0"}, count=2)
    turn(state, "a", TurnStage.T0)
    execute(state, "g0_mount", relics=("belt", "shield"))
    turn(state, "a", TurnStage.T0)
    execute(state, "g0_mount", relics=("glasses", "sight"))
    state.players["a"].hp = 10
    turn(state, "a", TurnStage.T0)
    execute(state, "g0_drone")
    state.players["a"].sp = 2
    state.players["b"].hp = 1
    turn(state, "a")
    execute(state, "g0_crossfire", mode=Performance.PUBLIC, destroy_relic="glasses")
    assert state.game_over and state.winner == "a"
    assert state.players["a"].hp == 8 and not state.ga_units["npc/drone/a"].alive
    assert "glasses" not in state.ga_roles["a"].relics
    assert "chapter/3" in state.terminal_snapshot.results["a"].chapter_ids


def test_g0_breath_before_insurance_real_hp_lifesteal_no_barrier_or_overkill() -> None:
    state = ready({"a": "G0", "c": "T7"}, count=4)
    turn(state, "c")
    execute(state, "t7_insure", targets=("a",), mode=Performance.INSTANT)
    state.players["a"].hp = 1
    turn(state, "b")
    execute(state, "attack", targets=("a",), weapon="knife")
    assert state.players["a"].hp == 1 and state.ga_roles["a"].breath_used == 1
    assert state.ga_insurance is not None and state.ga_roles["c"].used == 0
    turn(state, "b")
    execute(state, "attack", targets=("a",), weapon="bow")
    assert state.players["a"].hp == 1 and state.players["b"].ammo["bow"] == 4
    state.players["b"].hp = 2
    turn(state, "a")
    execute(state, "g0_ar", targets=("b",), fire_mode="point")
    assert state.players["a"].hp == 2
    barrier(state, "c", "fixture/full-block", 20)
    turn(state, "a")
    execute(state, "g0_ar", targets=("c",), fire_mode="point")
    assert state.players["a"].hp == 2


def test_g0_failed_breath_is_exit_not_kill_or_afterlife() -> None:
    state = ready({"a": "G0"})
    state.players["a"].hp = 1
    turn(state, "b")
    execute(state, "attack", targets=("a",), weapon="knife")
    role = state.ga_roles["a"]
    role.boundary(state, boundary="R4.expiry")
    assert role.breath_timer.remaining == 2
    for _ in range(2):
        run_round(state, state.ga_controllers)
    assert state.players["a"].exited and not state.players["a"].alive
    assert state.players["b"].kills == 0
    assert "a" not in state.ga_afterlife.bets


def test_g0_breath_refill_free_but_ammo_still_needed_and_capacity_bounded() -> None:
    state = ready({"a": "G0"})
    state.players["a"].hp = 1
    turn(state, "b")
    execute(state, "attack", targets=("a",), weapon="knife")
    state.players["a"].ammo["ar"] = 0
    state.players["a"].credits = 0
    turn(state, "a")
    assert not any(a.operation == "g0_ar" for a in legal_actions(state))
    execute(state, "g0_refill")
    assert state.players["a"].ammo["ar"] == 15
    turn(state, "a", TurnStage.T0)
    assert not any(a.operation == "g0_drone" for a in legal_actions(state))


def test_g1_sam_t0_and_unarmor_discount_are_single_rule_paths() -> None:
    state = ready({"a": "G1"})
    turn(state, "a")
    receipt = execute(state, "buy_armor", kind="ceramic")
    assert sum(c.amount for c in receipt.costs if c.account == "credits") == 2
    turn(state, "a", TurnStage.T0)
    execute(state, "g1_sam")
    state.current_stage = TurnStage.T1
    assert not any(a.verb.value == "interact" or a.operation == "search"
                   for a in legal_actions(state))
    execute(state, "attack", targets=("b",), weapon="knife")
    assert state.players["b"].hp == 14
    turn(state, "a", TurnStage.T0)
    execute(state, "g1_unarmor")
    assert state.ga_roles["a"].round_form == "sam"


def test_g1_supernova_consumes_overload_and_ardent_absorption_is_finite() -> None:
    state = ready({"a": "G1"})
    turn(state, "a", TurnStage.T0)
    execute(state, "g1_sam")
    turn(state, "a")
    execute(state, "g1_supernova", location="shop")
    role = state.ga_roles["a"]
    assert role.overload == 0 and len(role.ardent(state)) == 2
    assert state.players["b"].hp == 16 and len(state.players["b"].burns) == 1
    turn(state, "b")
    execute(state, "attack", targets=("a",), weapon="knife")
    assert state.players["a"].hp == 20 and not role.ardent(state)
    turn(state, "b")
    execute(state, "attack", targets=("a",), weapon="knife")
    assert state.players["a"].hp == 17


def test_g1_full_window_can_complete_three_chapters_through_real_r4() -> None:
    state = ready({"a": "G1"})
    turn(state, "a", TurnStage.T0)
    execute(state, "g1_sam")
    state.players["a"].sp = 2
    turn(state, "a")
    execute(state, "g1_full", mode=Performance.PUBLIC)
    turn(state, "a")
    execute(state, "attack", targets=("b",), weapon="fist")
    for _ in range(3):
        run_round(state, state.ga_controllers)
    assert state.players["a"].alive and state.ga_roles["a"].form == "unarmored"
    assert state.players["a"].chapters == {"chapter/1", "chapter/2", "chapter/3"}
    assert sum(t["label"] == "slot_start" and t["kind"] == "step_followup"
               for t in state.ga_trace) == 3


def test_g1_entropy_hp_cost_is_not_hostile_damage_attention_or_crime() -> None:
    state = ready({"a": "G1"})

    def reached_threshold(s: object) -> None:
        s.ga_roles["a"].entropy = 6

    state.ga_hooks["R4.dot_maintenance"].insert(0, reached_threshold)
    run_round(state, state.ga_controllers)
    assert state.players["a"].hp == 16 and state.players["a"].sp == 1
    assert not state.ga_crimes and state.ga_roles["a"].entropy == 2


@pytest.mark.parametrize("armored,expected", [(False, 18), (True, 20)])
def test_g1_apocalypse_reactor_half_or_immunity(armored: bool, expected: int) -> None:
    state = ready({"a": "G1"})
    if armored:
        turn(state, "a", TurnStage.T0)
        execute(state, "g1_sam")
    state.current_round = state.world_tick = 15
    state.ga_clock_accelerated = True
    run_round(state, state.ga_controllers)
    assert state.world_tick == 17 and state.players["a"].hp == expected


def test_g1_without_full_history_cannot_propagate() -> None:
    state = ready({"a": "G1"})
    turn(state, "a", TurnStage.T0)
    execute(state, "g1_sam")
    state.players["a"].hp = 1
    turn(state, "b")
    execute(state, "attack", targets=("a",), weapon="knife")
    assert not state.players["a"].alive and state.ga_roles["a"].propagation_used == 0


def propagation(state: GameState) -> None:
    """从正常着装、公演和实际普通致死进入繁育。"""
    turn(state, "a", TurnStage.T0)
    execute(state, "g1_sam")
    state.players["a"].sp = 2
    turn(state, "a")
    execute(state, "g1_full", mode=Performance.PUBLIC)
    state.players["a"].hp = 1
    turn(state, "b")
    execute(state, "attack", targets=("a",), weapon="knife")


def test_g1_propagation_precedes_insurance_expiry_is_absolute_even_when_clock_paused() -> None:
    state = ready({"a": "G1", "c": "T7"}, count=4)
    turn(state, "c")
    execute(state, "t7_insure", targets=("a",), mode=Performance.INSTANT)
    propagation(state)
    assert state.players["a"].hp == 8 and state.ga_roles["a"].form == "propagation"
    assert state.ga_insurance and state.ga_roles["c"].used == 0
    state.ga_clock_paused = True
    for _ in range(3):
        run_round(state, state.ga_controllers)
    assert state.players["a"].absolute_dead and state.ga_roles["c"].used == 0
    assert state.ga_insurance is None


def test_g1_structural_supernova_destroys_building_and_win_beats_expiry() -> None:
    state = ready({"a": "G1"}, count=2)
    propagation(state)
    state.players["b"].hp = 1
    turn(state, "a")
    execute(state, "g1_supernova", location="shop")
    assert state.game_over and state.winner == "a"
    assert "shop" in state.destroyed_locations and state.players["a"].location != "shop"
    assert "chapter/3" in state.terminal_snapshot.results["a"].chapter_ids


@pytest.mark.parametrize("embers,survives", [(5, False), (6, True)])
def test_g4_partial_requires_registered_threshold(embers: int, survives: bool) -> None:
    state = ready({"a": "G4"})
    state.ga_roles["a"].ember = embers
    state.players["a"].hp = 1
    turn(state, "b")
    execute(state, "attack", targets=("a",), weapon="knife")
    assert state.players["a"].alive is survives
    if survives:
        assert state.players["a"].hp == 12 and state.ga_roles["a"].ruin == 1


def test_g4_personal_three_chapters_ascension_no_extra_turn_and_exit_keeps_body() -> None:
    state = ready({"a": "G4"})
    turn(state, "a")
    execute(state, "g4_slash", targets=("b",), mode=Performance.INSTANT, weapon="knife")
    state.ga_roles["a"].ember = 12
    turn(state, "a", TurnStage.T0)
    execute(state, "g4_ascend")
    assert state.players["a"].hp == 24 and state.players["a"].sp == 2
    assert not state.ga_pending_extras
    state.current_stage = TurnStage.T1
    execute(state, "g4_judgment", mode=Performance.PUBLIC)
    assert state.ga_roles["a"].form == "human" and state.players["a"].hp == 20
    assert state.players["a"].chapters == {"chapter/1", "chapter/2", "chapter/3"}


def test_g4_pool_d7_two_challengers_three_refusers_is_14_and_10_total() -> None:
    state = ready({"a": "G4"}, count=6)
    for player in state.players.values():
        player.hp = player.max_hp = 100
    state.ga_roles["a"].ember = 12
    turn(state, "a", TurnStage.T0)
    execute(state, "g4_ascend")
    state.ga_roles["a"].ruin = 7
    state.ga_controllers["b"] = Decisions(judgment_commit=["challenge"], challenge_weapon=["0"])
    state.ga_controllers["c"] = Decisions(judgment_commit=["challenge"], challenge_weapon=["0"])
    state.current_stage = TurnStage.T1
    execute(state, "g4_judgment", mode=Performance.PUBLIC)
    assert [state.players[p].hp for p in "bcdef"] == [93, 93, 96, 97, 97]
    assert state.players["a"].hp_damage == 24
    assert {case["actor_id"] for case in state.ga_crimes} == {"a"}
    events = {e.event_id: e for e in state.ga_ledger.events}
    assert not any("/response/" in (events[c.event_id].root_id or "")
                   for c in state.ga_ledger.claims)


def test_g4_challenge_forcing_exit_cancels_all_remaining_responses_and_pools() -> None:
    state = ready({"a": "G4"})
    state.ga_roles["a"].ember = 12
    turn(state, "a", TurnStage.T0)
    execute(state, "g4_ascend")
    state.players["a"].hp = 1
    state.ga_controllers["b"] = Decisions(judgment_commit=["challenge"], challenge_weapon=["0"])
    state.current_stage = TurnStage.T1
    execute(state, "g4_judgment", mode=Performance.PUBLIC)
    assert state.ga_roles["a"].form == "human" and state.players["a"].hp == 1
    assert state.players["b"].hp == state.players["c"].hp == 20
    assert state.players["a"].hp_damage == 0
    assert any(t["label"] == "judgment_cancelled" for t in state.ga_trace)


def test_g4_cannot_heal_ember_or_gain_fire_from_same_hit_that_exits_form() -> None:
    state = ready({"a": "G4", "c": "T7"})
    state.ga_roles["a"].ember = 12
    turn(state, "a", TurnStage.T0)
    execute(state, "g4_ascend")
    state.players["a"].hp = 1
    state.players["a"].inventory["syringe"] = 1
    turn(state, "a", TurnStage.T0)
    assert not any(a.operation == "syringe" for a in legal_actions(state))
    turn(state, "b")
    execute(state, "attack", targets=("a",), weapon="knife")
    assert state.ga_roles["a"].form == "human" and state.ga_roles["a"].ember == 0
