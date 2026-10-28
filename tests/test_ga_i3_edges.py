"""I3跨系统有限资源、隔离与诗篇代价，保护真实终局收尾。"""

from dataclasses import replace

import pytest

from engine.ga.actions import GrantKind
from engine.ga.contracts import Performance, TurnStage
from engine.ga.gameplay import legal_actions
from engine.ga.runtime import run_round
from engine.ga.state import run_hooks
from engine.ga.status import put_status
from tests.ga_i2_helpers import Decisions, execute, ready, turn
from tests.test_ga_g7 import Steps, fused
from tests.test_ga_poems import offer


def test_tomorrow_crossfire_costs_zero_hp_once_and_keeps_relics() -> None:
    state = ready({"a": "G5", "b": "G0"})
    offer(state)
    turn(state, "b", TurnStage.T0)
    execute(state, "g0_mount", relics=("belt", "sight"))
    turn(state, "b", TurnStage.T0)
    execute(state, "g0_drone")
    hp = state.players["b"].hp
    relics = set(state.ga_roles["b"].relics)
    state.players["b"].sp = 2
    turn(state, "b")
    receipt = execute(state, "g0_crossfire", mode=Performance.PUBLIC)
    assert state.players["b"].hp == hp and state.ga_roles["b"].relics == relics
    assert not any(c.amount for c in receipt.costs if c.account == "hp")
    assert not state.ga_roles["b"].promise and state.ga_roles["b"].tomorrow
    assert not state.ga_roles["b"].drone_alive(state)


def test_burden_burn_cannot_be_prayed_away_and_savior_timer_stays_frozen() -> None:
    state = ready({"a": "G5", "b": "G4", "c": "T7"})
    for _ in range(3):
        offer(state)
    state.players["c"].sp = 1
    turn(state, "c")
    execute(state, "t7_insure", targets=("a",), mode=Performance.INSTANT)
    turn(state, "b", TurnStage.T0)
    execute(state, "g4_ascend")
    run_round(state, state.ga_controllers)
    assert state.players["b"].burns and all(b.uncleansable for b in state.players["b"].burns)
    turn(state, "c")
    assert not any(a.operation == "t7_prayer" and a.target_ids == ("b",)
                   for a in legal_actions(state))
    put_status(state.players["b"], state.ga_config, state.current_round, "WEAK",
               source_id="fixture")
    execute(state, "t7_prayer", targets=("b",), mode=Performance.INSTANT)
    assert "WEAK" not in state.players["b"].statuses and state.players["b"].burns
    assert state.ga_roles["b"].form_timer.remaining == 3


def test_law_refills_actual_old_guard_wall_without_refreshing_attack_round_quota() -> None:
    state = ready({"a": "G5", "b": "T6"})
    police = state.ga_police
    police.guard_id, police.stance = "b", "wall"
    from engine.ga.police import Police
    Police().sync(state)
    wall = state.players["b"].defenses["police/wall"]
    state.players["b"].defenses["police/wall"] = replace(wall, durability=1)
    police.attack_round = state.current_round
    state.ga_controllers["b"] = Decisions(poem_law=["wall"])
    offer(state)
    assert police.wall == 18 and police.attack_round == state.current_round


@pytest.mark.parametrize("form", ["archer", "shielder"])
def test_other_two_ex_forms_use_actual_magazine_and_complete_declared_effects(form: str) -> None:
    state = fused()
    role = state.ga_roles["a"]
    turn(state, "a", TurnStage.T0)
    execute(state, "g7_change", form=form)
    state.players["a"].sp = 2
    destination = "magic" if form == "shielder" else "shop"
    state.players["b"].location = destination
    turn(state, "a")
    execute(state, "g7_ex", targets=("b",), mode=Performance.PUBLIC, location=destination)
    assert not role.magazine and state.players["a"].ammo["horus_loaded"] == 0
    assert state.players["b"].hp < 20
    if form == "shielder":
        assert state.players["a"].location == "magic" and role.pose == "active"
        assert state.players["a"].defenses["g7/ex-barrier"].durability == 2
    else:
        assert state.players["b"].statuses["VULNERABLE"].timer.remaining == 1


def test_terror_two_nonwinning_attacks_exhaust_extra_hp_as_normal_death() -> None:
    state = ready({"a": "G7"})
    role = state.ga_roles["a"]
    role.enter_terror(state)
    role.terror_ready = True
    for _ in range(2):
        turn(state, "a")
        execute(state, "g7_terror")
    assert role.extra_hp == 0 and not state.players["a"].alive
    assert not state.players["a"].absolute_dead and not state.game_over
    assert "terror/a" not in state.ga_police.stopped


def test_extra_turn_cannot_pay_doubt_or_make_first_terror_attack() -> None:
    state = ready({"a": "G7"})
    role = state.ga_roles["a"]
    role.form = "doubt"
    turn(state, "a", kind=GrantKind.EXTRA_TURN)
    assert not role.slot_gate(state, state.current_grant) and role.form == "doubt"
    role.enter_terror(state)
    assert not role.slot_gate(state, state.current_grant) and not role.terror_ready


def test_front_departure_spends_one_more_actual_slot_but_broken_shield_no_longer_delays() -> None:
    state = fused()
    role = state.ga_roles["a"]
    state.ga_controllers["a"] = Steps(["g7_macro_shield"])
    turn(state, "a")
    execute(state, "g7_macro")
    turn(state, "b")
    execute(state, "move", location="magic")
    assert state.players["b"].location == "shop"
    turn(state, "b")
    execute(state, "move", location="magic")
    assert state.players["b"].location == "magic"


def test_g2_residual_coordinate_cannot_move_the_shared_body_out_of_g3_isolation() -> None:
    state = ready({"a": "G3", "b": "G2"})
    state.ga_roles["b"].positions["shadow"] = "magic"
    state.players["a"].sp = 2
    turn(state, "a")
    execute(state, "g3_domain", targets=("b",), mode=Performance.PUBLIC)
    turn(state, "b", TurnStage.T0)
    assert not any(a.operation == "g2_control" for a in legal_actions(state))
    from engine.ga.extensions import relocate
    assert not relocate(state, "b", "magic")
    assert state.players["b"].location == "shop"


def test_savior_sun_adds_one_real_fire_packet_without_recursive_free_damage() -> None:
    state = ready({"a": "G5", "b": "G4"})
    for _ in range(3):
        offer(state)
    turn(state, "b", TurnStage.T0)
    execute(state, "g4_ascend")
    state.players["c"].moved = False
    state.current_stage = TurnStage.T1
    execute(state, "attack", targets=("c",), weapon="knife")
    fire = [r for _, batch in state.ga_resolution_batches.values() for r in batch if
            r.effect.source_id == "burden_fire"]
    assert len(fire) == 1 and fire[0].effect.amount == fire[0].hp_loss == 1


def test_joy_mass_open_same_negative_face_has_unique_segments_and_one_real_root() -> None:
    state = ready({"a": "G6"})
    state.ga_roles["a"].receive_poem(state, "b")
    state.players["a"].sp = 2
    turn(state, "a")
    execute(state, "g6_gifts", mode=Performance.PUBLIC)
    run_round(state, state.ga_controllers)
    state.ga_controllers["a"] = Decisions(gift_face=["4", "4"])
    turn(state, "a", TurnStage.T0)
    receipt = execute(state, "g6_open_all")
    assert state.players["b"].hp == state.players["c"].hp == 16
    records = [r for _, rs in state.ga_resolution_batches.values() for r in rs if
               r.effect.source_id == "g6_gift_effect"]
    assert len(records) == 2 and {r.effect.root_id for r in records} == {receipt.request.root_id}
    assert state.ga_roles["a"].boxes_opened == 2 and not state.ga_gifts


def test_gift_cannot_count_a_terror_holders_nonexistent_t0_as_refusal() -> None:
    state = ready({"a": "G6", "b": "G7"})
    state.players["a"].sp = 2
    turn(state, "a")
    execute(state, "g6_gifts", mode=Performance.PUBLIC)
    state.ga_roles["b"].enter_terror(state)
    state.ga_roles["b"].terror_ready = True
    run_round(state, state.ga_controllers)
    assert state.ga_gifts["b"][1].first_opportunity_round is None
    assert state.ga_gifts["b"][1].remaining_ticks == 2


def test_normal_gun_does_not_target_hidden_unit_without_search_or_detection() -> None:
    state = fused()
    state.players["b"].hidden = True
    turn(state, "a")
    assert not any(a.operation == "g7_shoot" and a.target_ids == ("b",)
                   for a in legal_actions(state))


def test_actual_memory_cost_is_part_of_shared_precheck_and_action_receipt() -> None:
    state = ready({"a": "G5", "b": "T1"})
    role = state.ga_roles["a"]
    role.stage, role.memory = 2, 4
    state.players["a"].sp = 0
    state.ga_controllers["b"] = Decisions(poem_offer=["decline"])
    turn(state, "a")
    receipt = execute(state, "g5_poem", targets=("b",), mode=Performance.MAJOR_INSTANT, paid_sp=0)
    assert any(c.account == "resource:memory" and c.amount == 4 for c in receipt.costs)
    assert state.players["a"].resources["memory"] == role.memory == 0


@pytest.mark.parametrize("rescued", [False, True])
def test_gift_shock_breaks_active_horus_even_when_isolation_blocks_teleport(rescued: bool) -> None:
    state = ready({"a": "G6", "b": "G7", "c": "G3"})
    from engine.ga.state import add_armor
    add_armor(state, "b", "at_field")
    role = state.ga_roles["b"]
    role.fuse(state)
    if rescued:
        role.receive_poem(state, "a")
    role.set_pose(state, "active", ("a",))
    state.players["a"].sp = 2
    turn(state, "a")
    execute(state, "g6_gifts", mode=Performance.PUBLIC)
    run_round(state, state.ga_controllers)
    state.players["c"].sp = 2
    turn(state, "c")
    execute(state, "g3_domain", targets=("a",), mode=Performance.PUBLIC)
    before = state.players["b"].location
    turn(state, "a", TurnStage.T0)
    execute(state, "g6_remote_box", targets=("b",), face=6)
    assert state.players["b"].location == before and role.pose == "passive"
    assert state.players["b"].statuses["control"].name == "SHOCKED"


def test_burden_automatic_t0_cost_is_due_even_when_shock_leaves_no_micro_action() -> None:
    state = ready({"a": "G5", "b": "G4"})
    for _ in range(3):
        offer(state)
    turn(state, "b", TurnStage.T0)
    execute(state, "g4_ascend")
    put_status(state.players["b"], state.ga_config, state.current_round, "SHOCKED",
               source_id="fixture")
    run_round(state, state.ga_controllers)
    assert any(b.uncleansable and b.timer.created_round == state.current_round for b in
               state.players["b"].burns)
