"""I3 G2一体双标、实际节拍、两终局和救赎的资源断言。"""

from dataclasses import replace

from engine.ga.combat import resolve_batch
from engine.ga.contracts import DamageKind, Performance, TurnStage
from engine.ga.gameplay import legal_actions
from engine.ga.police import Police
from engine.ga.runtime import run_round
from engine.ga.state import add_armor
from engine.ga.talents.base import ability, packet
from tests.ga_i2_helpers import execute, ready, turn


def test_two_positions_share_body_and_free_control_does_not_consume_t0() -> None:
    state = ready({"a": "G2"})
    role = state.ga_roles["a"]
    turn(state, "a")
    execute(state, "move", location="magic")
    assert role.positions == {"light": "magic", "shadow": "shop"}
    turn(state, "a", TurnStage.T0)
    execute(state, "g2_control", control="shadow")
    assert state.players["a"].location == "shop" and not state.current_grant.occupied
    assert len(state.players) == 3 and len(state.ga_units) == 3
    assert not any(a.operation == "g2_a" for a in legal_actions(state))


def test_light_a_uses_one_t0_forces_only_move_and_major_never_claims_public() -> None:
    state = ready({"a": "G2"})
    state.players["a"].sp = 2
    state.players["a"].hp = 10
    turn(state, "a", TurnStage.T0)
    execute(state, "g2_a", mode=Performance.MAJOR_INSTANT)
    assert state.players["a"].hp == 14 and state.players["a"].defenses["g2/a/a"].durability == 4
    state.current_stage = TurnStage.T1
    assert all(a.verb.value == "move" for a in legal_actions(state))
    assert not state.ga_public_used and state.ga_roles["a"].white == 2


def test_shadow_b_real_short_r4_window_debuffs_players_and_stops_local_npcs() -> None:
    state = ready({"a": "G2"})
    for player in state.players.values():
        player.location = "police"
    turn(state, "a", TurnStage.T0)
    execute(state, "g2_control", control="shadow")
    state.players["a"].location = "police"
    state.current_stage = TurnStage.T1
    execute(state, "g2_b", mode=Performance.INSTANT)
    assert "DISORIENTED" in state.players["b"].statuses
    assert all(npc.submerged_sources for npc in state.ga_units.values())
    state.ga_world.expire(state)
    assert not state.ga_world.vulnerability
    assert all(not p.submerged_sources for p in state.ga_units.values())


def test_double_clock_records_two_keys_but_only_one_actual_round_of_status_age() -> None:
    state = ready({"a": "G2"})
    role = state.ga_roles["a"]
    role.loneliness = 4
    state.ga_clock_accelerated = True
    run_round(state, state.ga_controllers)
    assert state.world_tick == 3 and state.current_round == 2 and role.black == 2
    state.ga_clock_paused = True
    run_round(state, state.ga_controllers)
    assert state.world_tick == 3 and role.black == 2


def test_rain_fuses_at_dusk_first_slot_and_stops_fixed_roster_until_owner_death() -> None:
    state = ready({"a": "G2"})
    state.world_tick = state.ga_config.get("clock", "dusk_start") - 1
    run_round(state, state.ga_controllers)
    assert state.ga_roles["a"].form == "rain" and not Police().available(state)
    assert "chapter/3" in state.players["a"].chapters
    state.players["a"].hp = 1
    turn(state, "b")
    execute(state, "attack", targets=("a",), weapon="knife")
    assert not state.ga_world.rain and Police().available(state)
    assert len(state.ga_units) == 3


def test_black_lethal_fusion_is_positive_hp_then_halves_once_before_insurance() -> None:
    state = ready({"a": "G2", "c": "T7"})
    state.players["c"].sp = 1
    turn(state, "c")
    execute(state, "t7_insure", targets=("a",), mode=Performance.INSTANT)
    state.world_tick = state.ga_config.get("clock", "dusk_start")
    state.ga_roles["a"].black = 9
    state.players["a"].hp = 1
    armor = add_armor(state, "b", "ceramic")
    before = state.players["b"].defenses[armor].durability
    turn(state, "b")
    receipt = execute(state, "attack", targets=("a",), weapon="knife")
    assert state.players["a"].hp == 4 and state.players["a"].max_hp == 10
    assert state.players["b"].hp == 10
    assert state.players["b"].defenses[armor].durability == -(-before // 2)
    assert state.ga_roles["c"].used == 0 and state.ga_insurance["target"] == "a"
    assert state.ga_clock_accelerated and state.ga_roles["a"].form == "reactor"
    assert not state.players["a"].targetable and not state.players["a"].healable
    assert receipt.costs == ()


def test_pre_dusk_lethal_uses_insurance_and_absolute_death_never_fuses() -> None:
    state = ready({"a": "G2", "c": "T7"})
    turn(state, "c")
    execute(state, "t7_insure", targets=("a",), mode=Performance.INSTANT)
    state.players["a"].hp = 1
    turn(state, "b")
    execute(state, "attack", targets=("a",), weapon="knife")
    assert state.ga_roles["a"].form == "dual" and state.ga_roles["c"].used == 1
    state.world_tick = state.ga_config.get("clock", "dusk_start")
    turn(state, "b")
    request = ability(state.current_grant, TurnStage.T1, "fixture/absolute", targets=("a",)).request
    resolve_batch(state, (packet(state, request, 0, kind=DamageKind.EXECUTION, certain=True),))
    assert state.players["a"].absolute_dead and not state.ga_roles["a"].fused
