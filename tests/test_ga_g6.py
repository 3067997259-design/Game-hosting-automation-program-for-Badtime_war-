"""I3 G6：有限笑点/假币、责任归属、真实响应机会和礼盒时钟。"""

from dataclasses import replace

from engine.ga.combat import resolve_batch
from engine.ga.contracts import Performance, TurnStage
from engine.ga.gameplay import legal_actions, validate_request
from engine.ga.runtime import run_round
from engine.ga.state import run_hooks
from engine.ga.status import expire_r4, put_status
from engine.ga.talents.base import ability, packet
from tests.ga_i2_helpers import Decisions, execute, ready, turn


def test_self_forfeit_and_controlled_skip_do_not_refund_counter_cost() -> None:
    state = ready({"a": "G6"})
    turn(state, "a", TurnStage.T0)
    execute(state, "g6_counter")
    assert state.players["a"].resources["laugh"] == 0
    state.current_stage = TurnStage.T1
    execute(state, "forfeit")
    assert state.players["a"].resources["laugh"] == 0
    turn(state, "b")
    execute(state, "forfeit")
    assert state.players["a"].resources["laugh"] == 1


def test_counter_intercepts_only_first_segment_and_no_laugh_refund() -> None:
    state = ready({"a": "G6"})
    turn(state, "a", TurnStage.T0)
    execute(state, "g6_counter")
    turn(state, "b")
    execute(state, "attack", targets=("a",), weapon="knife")
    assert state.players["a"].hp == 20 and state.players["b"].hp == 17
    assert not state.ga_roles["a"].armed and state.players["a"].resources["laugh"] == 0
    turn(state, "c")
    execute(state, "attack", targets=("a",), weapon="knife")
    assert state.players["a"].hp == 16


def test_fake_credit_quote_has_two_payers_no_debt_and_precheck_is_pure() -> None:
    state = ready({"a": "G6"})
    state.players["a"].credits = 1
    state.players["b"].credits = 2
    state.players["b"].location = "shop"
    turn(state, "a")
    candidate = next(a for a in legal_actions(state) if a.operation == "g6_card" and
                     a.parameters.get("kind") == "ceramic" and a.parameters["victim"] == "b")
    plan = validate_request(state, candidate)
    assert plan.accepted and state.players["b"].credits == 2
    execute(state, "g6_card", victim="b", menu_operation="buy_armor", kind="ceramic")
    assert state.players["a"].credits == 1 and state.players["b"].credits == 0
    assert state.players["a"].resources["laugh"] == 0
    assert "ceramic" in state.players["a"].armor_kinds.values()
    assert all(p.credits >= 0 for p in state.players.values())


def test_borrowed_base_weapon_does_not_spend_victim_ammo_and_crime_is_victim() -> None:
    state = ready({"a": "G6"})
    state.players["b"].location = state.players["c"].location = "magic"
    before = dict(state.players["b"].ammo)
    turn(state, "a")
    execute(state, "g6_borrow", targets=("c",), victim="b", source="borrow", weapon="bow")
    assert state.players["b"].ammo == before and state.players["a"].hp_damage > 0
    assert state.ga_crimes[-1]["actor_id"] == "b" and state.ga_crimes[-1]["victim_id"] == "c"
    state.current_round += 1
    turn(state, "a")
    assert not any(a.operation in ("g6_borrow", "g6_card") and a.parameters["victim"] == "b"
                   for a in legal_actions(state))


def test_disoriented_target_gets_no_gift_opportunity_or_early_expiry() -> None:
    state = ready({"a": "G6"})
    state.players["a"].sp = 2
    turn(state, "a")
    execute(state, "g6_gifts", mode=Performance.PUBLIC)
    window_id = state.ga_gifts["b"][1].window_id
    put_status(state.players["b"], state.ga_config, state.current_round, "DISORIENTED",
               source_id="fixture")
    run_round(state, state.ga_controllers)
    assert state.ga_gifts["b"][1].first_opportunity_round is None
    assert state.ga_gifts["b"][1].remaining_ticks == 2
    state.players["a"].sp = 2
    turn(state, "a")
    execute(state, "g6_gifts", mode=Performance.PUBLIC)
    assert state.ga_gifts["b"][1].window_id == window_id
    run_round(state, state.ga_controllers)
    assert state.ga_gifts["b"][1].first_opportunity_round == state.current_round


def test_gift_face_five_disables_g0_ar_for_two_rounds_instead_of_scrapping_it() -> None:
    state = ready({"a": "G6", "b": "G0"})
    state.players["a"].sp = 2
    turn(state, "a")
    execute(state, "g6_gifts", mode=Performance.PUBLIC)
    run_round(state, state.ga_controllers)
    turn(state, "a", TurnStage.T0)
    execute(state, "g6_remote_box", targets=("b",), face=5)
    player = state.players["b"]
    assert "ar" in player.weapons and "ar" in player.disabled_weapons
    turn(state, "b")
    assert not any(a.operation == "g0_ar" for a in legal_actions(state))
    opened = state.current_round
    for round_number in (opened + 1, opened + 2):
        state.current_round = round_number
        expire_r4(state)
    assert "ar" not in player.disabled_weapons
    turn(state, "b")
    assert any(a.operation == "g0_ar" for a in legal_actions(state))


def test_gift_remote_requires_opportunity_and_two_future_r4s_after_it() -> None:
    state = ready({"a": "G6"})
    state.players["a"].sp = 2
    turn(state, "a")
    execute(state, "g6_gifts", mode=Performance.PUBLIC)
    turn(state, "a", TurnStage.T0)
    assert not any(a.operation == "g6_remote_box" for a in legal_actions(state))
    run_round(state, state.ga_controllers)
    opened = state.current_round
    assert state.ga_gifts["b"][1].remaining_ticks == 2
    run_round(state, state.ga_controllers)
    assert state.ga_gifts["b"][1].remaining_ticks == 1
    run_round(state, state.ga_controllers)
    assert "b" not in state.ga_gifts and state.current_round == opened + 2
    assert state.ga_roles["a"].boxes_opened == 2


def test_joy_missed_counter_finds_legal_enemy_but_never_crosses_isolation() -> None:
    state = ready({"a": "G6"})
    state.ga_roles["a"].receive_poem(state, "b")
    turn(state, "a", TurnStage.T0)
    execute(state, "g6_counter")
    state.players["b"].isolation_id = state.players["c"].isolation_id = "realm"
    for npc in state.ga_units.values():
        npc.isolation_id = "realm"
    turn(state, "a")
    run_hooks(state, "slot_start")
    assert state.players["b"].hp == state.players["c"].hp == 20
    assert not state.ga_roles["a"].armed
