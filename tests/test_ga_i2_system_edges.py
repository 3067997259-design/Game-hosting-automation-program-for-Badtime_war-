"""I2跨系统资源/时限/重复维护边界，包含真实NPC持续伤害。"""

from engine.ga.combat import resolve_batch
from engine.ga.contracts import TurnStage
from engine.ga.gameplay import legal_actions
from engine.ga.police import Police
from engine.ga.runtime import run_round
from engine.ga.state import run_hooks
from engine.ga.status import put_status
from engine.ga.talents.base import ability, packet
from tests.ga_i2_helpers import Decisions, execute, ready, turn
from tests.test_ga_afterlife import kill, window
from tests.test_ga_police import case


def test_borrowed_conceal_expires_after_two_real_future_rounds_without_role() -> None:
    state = ready({"c": "T4"}, count=4)
    kill(state, "c")
    window(state, "c")
    execute(state, "afterlife_encore", targets=("a",), kind="T4")
    state.ga_controllers["a"] = Decisions(encore_hexagram=["paper"])
    state.ga_controllers["b"] = Decisions(encore_hexagram=["rock"])
    turn(state, "a")
    execute(state, "encore_T4", targets=("b",))
    assert state.players["a"].hidden and "a" not in state.ga_roles
    assert state.players["a"].hidden_timer.remaining == 2
    run_round(state, state.ga_controllers)
    assert state.players["a"].hidden and state.players["a"].hidden_timer.remaining == 1
    run_round(state, state.ga_controllers)
    assert not state.players["a"].hidden and state.players["a"].hidden_timer is None
    assert not state.players["a"].chapters


def test_borrowed_supply_can_reinforce_an_entirely_dead_fixed_roster() -> None:
    state = ready({"c": "T6"}, count=4)
    kill(state, "c")
    window(state, "c")
    execute(state, "afterlife_encore", targets=("a",), kind="T6")
    for officer in state.ga_units.values():
        officer.hp = 0
    turn(state, "a")
    execute(state, "encore_T6", kind="reinforce")
    assert len(Police().live(state)) == 1 and len(state.ga_units) == 3
    assert not state.players["a"].chapters


def test_duplicate_glasses_recovery_hook_cannot_repeat_paid_hp_refund() -> None:
    state = ready({"a": "G0"})
    turn(state, "a", TurnStage.T0)
    execute(state, "g0_mount", relics=("glasses",))
    turn(state, "a", TurnStage.T0)
    execute(state, "g0_drone")
    paid_hp = state.players["a"].hp
    run_hooks(state, "R4.recovery")
    assert state.players["a"].hp == paid_hp + 1
    run_hooks(state, "R4.recovery")
    assert state.players["a"].hp == paid_hp + 1


def test_duplicate_entropy_maintenance_does_not_charge_an_extra_round() -> None:
    state = ready({"a": "G1"})
    turn(state, "a", TurnStage.T0)
    execute(state, "g1_sam")
    role = state.ga_roles["a"]
    role.attacked_round = state.current_round
    before = role.entropy
    run_hooks(state, "R4.dot_maintenance")
    first = role.entropy
    assert first != before
    run_hooks(state, "R4.dot_maintenance")
    assert role.entropy == first


def test_fully_shocked_roster_consumes_shared_shot_and_recovers_no_extra_slot() -> None:
    state = ready({"a": "T6"})
    case(state)
    Police().mark_wanted(state, "b")
    for npc in state.ga_units.values():
        put_status(npc, state.ga_config, state.current_round, "SHOCKED", source_id="fixture")
    hp = state.players["b"].hp
    assert not Police().fire(state, "b")
    assert state.ga_police.attack_round == state.current_round
    assert all("control" not in npc.statuses for npc in state.ga_units.values())
    assert not Police().fire(state, "b") and state.players["b"].hp == hp
    state.current_round += 1
    assert Police().fire(state, "b") and state.players["b"].hp < hp


def test_explicit_back_attack_bypasses_only_front_police_wall() -> None:
    state = ready()
    state.ga_police.captain = "b"
    state.ga_police.stance = "wall"
    Police().sync(state)
    turn(state, "a")
    request = ability(state.current_grant, TurnStage.T1, "fixture", targets=("b",)).request
    front = packet(state, request, 4, certain=True, attack_side="front")
    resolve_batch(state, (front,))
    assert state.players["b"].hp == 20 and state.ga_police.wall == 8
    back = packet(state, request, 4, suffix="back", certain=True, attack_side="back")
    resolve_batch(state, (back,))
    assert state.players["b"].hp == 16 and state.ga_police.wall == 8


def test_npc_burn_ticks_in_real_rounds_without_player_hp_score_or_player_heads() -> None:
    state = ready({"a": "G1"})
    turn(state, "a", TurnStage.T0)
    execute(state, "g1_sam")
    turn(state, "a")
    execute(state, "g1_supernova", location="police")
    before = [npc.hp for npc in state.ga_units.values()]
    assert all(npc.burns for npc in state.ga_units.values())
    run_round(state, state.ga_controllers)
    assert [npc.hp for npc in state.ga_units.values()] == [hp - 1 for hp in before]
    run_round(state, state.ga_controllers)
    assert not Police().live(state)
    assert state.players["a"].hp_damage == 0 and state.players["a"].kills == 0
    starts = [e["actor"] for e in state.ga_trace if e["label"] == "slot_start"]
    assert set(starts) <= set(state.players)


def test_npc_weapon_candidates_keep_hook_cooldown_and_do_not_clone_area_attack() -> None:
    state = ready()
    state.players["a"].weapons.update({"hook", "earthquake"})
    state.players["a"].location = "police"
    turn(state, "a")
    npc_options = [a for a in legal_actions(state) if a.operation == "npc_attack"]
    assert any(a.parameters["weapon"] == "hook" for a in npc_options)
    assert not any(a.parameters["weapon"] == "earthquake" for a in npc_options)
    state.players["a"].hook_cooldown = 1
    assert not any(a.operation == "npc_attack" and a.parameters["weapon"] == "hook"
                   for a in legal_actions(state))


def test_real_r0_ghost_loop_bet_glow_encore_does_not_receive_a_living_standard_slot() -> None:
    state = ready({"c": "T1"}, count=4)
    kill(state, "c")
    state.players["c"].pp = 4
    state.players["a"].hp = 10
    state.ga_controllers["c"].ghost_actions.extend([
        {"operation": "afterlife_bet", "target_ids": ["a"]},
        {"operation": "afterlife_glow", "target_ids": ["a"], "kind": "heal", "payer": "c"},
        {"operation": "afterlife_encore", "target_ids": ["a"], "kind": "T1"},
        None,
    ])
    run_round(state, state.ga_controllers)
    assert state.ga_afterlife.bet_round["c"] == state.current_round == 2
    assert state.ga_afterlife.bets["c"] == "a" and state.players["c"].pp == 2
    assert state.players["a"].hp == 12 and "c" in state.ga_afterlife.encore_used
    assert state.ga_afterlife.borrowed["a"][0]["talent"] == "T1"
    starts = [e["actor"] for e in state.ga_trace if e["label"] == "slot_start"]
    assert "c" not in starts
