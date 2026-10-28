"""I2警务完整入口：案件→举报/热线→就任→共享攻击与真实NPC资源。"""

from __future__ import annotations

from dataclasses import replace

from engine.game_state import GameState
from engine.ga.contracts import Performance, TurnStage
from engine.ga.gameplay import legal_actions
from engine.ga.police import Police
from engine.ga.runtime import run_round
from engine.ga.state import add_armor
from tests.ga_i2_helpers import Decisions, execute, ready, turn


def case(state: GameState, actor: str = "b") -> None:
    """军基实际购买产生公开案底，不能凭手填通缉跳过入口。"""
    state.players[actor].location = "military"
    turn(state, actor)
    execute(state, "buy_weapon", weapon="electromagnetic_rifle")


def test_one_fixed_roster_without_independent_officer_slots() -> None:
    state = ready()
    assert len(state.ga_police.officer_ids) == 3
    assert [state.ga_units[key].hp for key in state.ga_police.officer_ids] == [6, 6, 6]
    assert state.ga_police.wall == 12
    run_round(state, state.ga_controllers)
    starts = [t["actor"] for t in state.ga_trace if t["label"] == "slot_start"]
    assert sorted(starts) == sorted(state.players)


def test_hotline_then_shared_r4_instant_quota_no_r2_shot() -> None:
    state = ready({"a": "T6"})
    case(state)
    turn(state, "a", TurnStage.T0)
    execute(state, "t6_hotline", targets=("b",))
    assert state.ga_police.wanted == ["b"]
    before = state.players["b"].hp
    Police().r2(state)
    assert state.players["b"].hp == before
    assert Police().fire(state, "b", owner="a")
    hp = state.players["b"].hp
    assert hp < before
    assert not Police().fire(state, "b", owner="a")
    Police().r4(state)
    assert state.players["b"].hp == hp


def test_apply_then_next_r2_joins_captain_and_moves_all_officers() -> None:
    state = ready()
    state.players["a"].location = "police"
    turn(state, "a")
    execute(state, "police_apply")
    assert state.ga_police.captain is None
    run_round(state, state.ga_controllers)
    assert state.ga_police.captain == "a"
    turn(state, "a")
    execute(state, "move", location="magic")
    assert all(state.ga_units[key].location == "magic" for key in state.ga_police.officer_ids)


def test_wall_does_not_refresh_by_stance_toggle_and_blocks_this_round_move() -> None:
    state = ready()
    state.ga_police.captain = "a"
    Police().sync(state)
    turn(state, "a", TurnStage.T0)
    execute(state, "police_stance", stance="wall")
    assert state.players["a"].defenses["police/wall"].durability == 12
    turn(state, "b")
    execute(state, "attack", targets=("a",), weapon="knife")
    assert state.ga_police.wall == 8 and state.players["a"].hp == 20
    turn(state, "a", TurnStage.T0)
    execute(state, "police_stance", stance="mobile")
    turn(state, "a", TurnStage.T0)
    execute(state, "police_stance", stance="wall")
    assert state.ga_police.wall == 8
    state.current_stage = TurnStage.T1
    assert not any(a.verb.value == "move" for a in legal_actions(state))


def test_area_attack_hits_captain_and_officers_wall_does_not_block_area() -> None:
    state = ready()
    state.ga_police.captain = "b"
    state.ga_police.stance = "wall"
    Police().sync(state)
    state.players["a"].weapons.add("earthquake")
    turn(state, "a")
    execute(state, "attack", weapon="earthquake")
    assert state.players["b"].hp == 18
    assert all(state.ga_units[key].hp == 4 for key in state.ga_police.officer_ids)
    assert state.ga_police.wall == 12
    assert state.players["a"].hp_damage == 4


def test_hidden_or_isolated_or_stopped_wanted_target_is_not_shot() -> None:
    state = ready({"a": "T6"})
    case(state)
    Police().mark_wanted(state, "b")
    state.players["b"].hidden = True
    assert not Police().fire(state, "b")
    state.players["b"].hidden = False
    state.players["b"].isolation_id = "arena"
    assert not Police().fire(state, "b")
    state.players["b"].isolation_id = None
    state.destroyed_locations.add("police")
    assert not Police().fire(state, "b")
    assert state.ga_crimes and state.ga_police.wanted == ["b"]


def test_explicit_innocent_command_loses_officer_but_personal_attack_does_not() -> None:
    state = ready()
    state.ga_police.captain = "a"
    Police().sync(state)
    turn(state, "a")
    execute(state, "attack", targets=("b",), weapon="knife")
    assert len(Police().live(state)) == 3
    hp = state.players["c"].hp
    turn(state, "a")
    execute(state, "police_command", targets=("c",))
    assert state.players["c"].hp == hp and len(Police().live(state)) == 2


def test_t6_upgrade_is_finite_and_reinforces_real_roster_only() -> None:
    state = ready({"a": "T6"})
    state.players["a"].sp = 2
    turn(state, "a")
    execute(state, "t6_supply", mode=Performance.INSTANT, kind="wall", deployment="none")
    assert state.ga_police.wall == 18 and state.ga_police.wall_upgrade == 1
    turn(state, "a")
    assert not any(a.operation == "t6_supply" and a.parameters["kind"] == "wall"
                   for a in legal_actions(state))
    dead = state.ga_police.officer_ids[0]
    state.ga_units[dead].hp = 0
    state.players["a"].sp = 2
    execute(state, "t6_supply", mode=Performance.INSTANT, kind="reinforce", deployment="none")
    assert state.ga_units[dead].hp == 6 and len(state.ga_units) == 3


def test_t6_public_guard_is_not_automatic_captain_appointment() -> None:
    state = ready({"a": "T6"})
    state.players["a"].sp = 2
    turn(state, "a")
    execute(state, "t6_supply", mode=Performance.PUBLIC, kind="wall", deployment="wall")
    assert state.ga_police.captain is None and state.ga_police.guard_id == "a"
    assert state.players["a"].defenses["police/wall"].durability == 18


def test_military_pass_does_not_create_new_crime() -> None:
    state = ready({"a": "T6"})
    state.players["b"].location = "military"
    state.players["b"].inventory["black_market_pass"] = 1
    turn(state, "b")
    execute(state, "buy_armor", kind="at_field")
    turn(state, "a", TurnStage.T0)
    assert not any(a.operation == "t6_hotline" for a in legal_actions(state))


def test_repeat_offender_has_one_report_after_any_eligible_case() -> None:
    """前案无举报资格不遮蔽后案；同一嫌疑人多案只产生一个真实举报按钮。"""
    state = ready()
    turn(state, "a")
    execute(state, "move", location="hospital")
    turn(state, "b")
    execute(state, "attack", targets=("c",), weapon="knife")
    turn(state, "a")
    execute(state, "move", location="shop")
    for _ in range(2):
        turn(state, "b")
        execute(state, "attack", targets=("a",), weapon="knife")
    assert len(state.ga_crimes) == 3
    turn(state, "a")
    execute(state, "move", location="police")
    turn(state, "a")
    reports = [a for a in legal_actions(state) if a.operation == "police_report"]
    assert len(reports) == 1 and reports[0].target_ids == ("b",)
    execute(state, "police_report", targets=("b",))
    assert state.ga_police.wanted == ["b"] and len(state.ga_crimes) == 3
    turn(state, "a")
    assert not any(a.operation == "police_report" for a in legal_actions(state))
