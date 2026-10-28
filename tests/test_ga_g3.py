"""I3 G3真实魔力、结构隔离、有限连发、猎风与崩坏。"""

from engine.ga.contracts import Performance, TurnStage
from engine.ga.gameplay import legal_actions
from engine.ga.runtime import run_round
from engine.ga.state import run_hooks
from tests.ga_i2_helpers import Decisions, execute, ready, turn


def test_rho_and_guard_cost_actual_magic_and_share_one_outside_channel_at_slot_end() -> None:
    state = ready({"a": "G3"})
    role = state.ga_roles["a"]
    turn(state, "a", TurnStage.T0)
    execute(state, "g3_trace", kind="rho")
    assert state.players["a"].resources["magic"] == 3
    turn(state, "a", TurnStage.T0)
    execute(state, "g3_trace", kind="guard")
    assert role.rho_id in state.players["a"].defenses and role.guard
    run_hooks(state, "slot_end")
    assert role.rho_id not in state.players["a"].defenses and role.guard


def test_domain_captures_real_entities_excludes_police_and_external_attacks() -> None:
    state = ready({"a": "G3"}, count=4)
    state.players["d"].location = "magic"
    state.players["a"].sp = 2
    turn(state, "a")
    execute(state, "g3_domain", targets=("b",), mode=Performance.PUBLIC)
    role = state.ga_roles["a"]
    assert all(state.players[p].isolation_id == role.realm for p in ("a", "b", "c"))
    assert state.players["d"].isolation_id is None
    assert all(npc.isolation_id is None for npc in state.ga_units.values() if
               npc.unit_kind == "police")
    turn(state, "d")
    assert not any("a" in a.target_ids for a in legal_actions(state))
    turn(state, "b")
    assert not any(a.verb.value in ("move", "interact") for a in legal_actions(state))
    execute(state, "g3_break", targets=(role.anchor,))
    assert state.ga_units[role.anchor].hp == 6
    turn(state, "b")
    execute(state, "g3_break", targets=(role.anchor,))
    turn(state, "c")
    execute(state, "g3_break", targets=(role.anchor,))
    assert role.realm is None and all(p.isolation_id is None for p in state.players.values())


def test_full_magic_chain_2_3_4_then_collapse_spends_twelve_and_cleans_before_terminal() -> None:
    state = ready({"a": "G3"})
    state.players["a"].resources["magic"] = 8
    state.players["a"].sp = 2
    turn(state, "a")
    execute(state, "g3_domain", targets=("b",), mode=Performance.PUBLIC)
    state.ga_controllers["a"] = Decisions(projection_chain=["b", "b"],
                                          projection_finish=["collapse"])
    turn(state, "a")
    execute(state, "g3_spiral", targets=("b",), mode=Performance.NONE)
    role = state.ga_roles["a"]
    assert role.realm is None
    assert state.players["a"].resources == {"magic": 0, "temporary_magic": 0}
    assert state.ga_public_blocked["b"] == state.current_round
    assert state.players["a"].chapters >= {"chapter/2", "chapter/3"}
    assert not state.ga_extra_used and not state.ga_pending_extras


def test_poem_free_first_segment_does_not_fabricate_gale_threshold_spending() -> None:
    state = ready({"a": "G3"})
    role = state.ga_roles["a"]
    role.receive_poem(state, "b")
    state.players["a"].sp = 2
    turn(state, "a")
    execute(state, "g3_domain", targets=("b",), mode=Performance.PUBLIC)
    state.ga_controllers["a"] = Decisions(projection_chain=["b", "stop"])
    before = role.available(state)
    turn(state, "a")
    execute(state, "g3_spiral", targets=("b",), mode=Performance.NONE)
    assert role.available(state) == before - 3
    assert "b" not in state.ga_public_blocked and not role.ideal


def test_realm_future_r4_age_and_upkeep_continue_while_world_clock_is_paused() -> None:
    state = ready({"a": "G3"})
    state.players["a"].sp = 2
    turn(state, "a")
    execute(state, "g3_domain", targets=("b",), mode=Performance.PUBLIC)
    role = state.ga_roles["a"]
    state.ga_clock_paused = True
    before = role.available(state)
    for _ in range(3):
        run_round(state, state.ga_controllers)
    assert role.realm is None and state.world_tick == 1
    assert state.players["a"].resources["magic"] == 3
    assert before == 10
