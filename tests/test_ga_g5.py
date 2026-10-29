"""I3 G5成长、真实代付、历史约束和先存成绩的有限闭环。"""

from engine.ga.contracts import Performance, TurnStage
from engine.ga.events import EventKind
from engine.ga.gameplay import legal_actions
from engine.ga.runtime import run_round
from engine.ga.state import emit
from tests.ga_i2_helpers import Decisions, execute, ready, turn


def test_memory_highlights_once_per_root_and_stage3_stops_growth() -> None:
    state = ready({"a": "G5"})
    role = state.ga_roles["a"]
    emit(state, EventKind.PUBLIC_PERFORMED, root_id="one", actor_id="b", effective=True)
    emit(state, EventKind.ARMOR_BROKEN, root_id="one", actor_id="b", target_id="c", effective=True)
    assert role.memory == 1
    role.stage = 2
    emit(state, EventKind.PUBLIC_PERFORMED, root_id="two", actor_id="c", effective=True)
    assert role.memory == 2
    role.stage = 3
    emit(state, EventKind.PUBLIC_PERFORMED, root_id="three", actor_id="c", effective=True)
    assert role.memory == 2


def test_story_sets_target_sp_zero_heals_and_uses_major_t1_without_public() -> None:
    state = ready({"a": "G5"})
    state.players["a"].sp = state.players["b"].sp = 2
    state.players["b"].hp = 10
    turn(state, "a")
    execute(state, "g5_story", targets=("b",), mode=Performance.MAJOR_INSTANT)
    assert state.players["b"].sp == 0 and state.players["b"].hp == 11
    assert state.players["a"].sp == 0, "讲故事不给 G5 自己关注"
    assert state.ga_roles["a"].memory == 2 and not state.ga_public_used
    assert not legal_actions(state)


def test_refused_poem_still_pays_exact_sp_memory_and_current_weight_drops() -> None:
    state = ready({"a": "G5", "b": "T1"})
    role = state.ga_roles["a"]
    role.stage, role.memory = 2, 6
    state.players["a"].sp = 1
    state.ga_controllers["b"] = Decisions(poem_offer=["decline"])
    turn(state, "a")
    assert role.stat(state) == 1
    receipt = execute(state, "g5_poem", targets=("b",), mode=Performance.MAJOR_INSTANT, paid_sp=1)
    assert role.memory == 4 and state.players["a"].sp == 0 and role.stat(state) == 0
    assert not state.ga_roles["b"].ranger and not state.ga_poems.accepted
    assert not legal_actions(state) and not state.ga_public_used
    assert sum(c.amount for c in receipt.costs if c.account == "sp") == 1


def test_self_arrows_use_total_budget_and_per_target_two_with_growth() -> None:
    state = ready({"a": "G5"}, count=6)
    role = state.ga_roles["a"]
    role.stage, role.memory = 2, 12
    state.players["a"].sp = 2
    turn(state, "a")
    execute(state, "g5_poem", targets=("a",), mode=Performance.MAJOR_INSTANT, paid_sp=2)
    records = [r for _, batch in state.ga_resolution_batches.values() for r in batch if
               r.effect.source_id == "g5_arrows"]
    assert len(records) == 4 and sum(r.effect.amount for r in records) == 10
    assert role.arrow_growth == 1 and not state.ga_poems.love
    assert records[-1].effect.attribute.value == "unattributed"


def test_ascension_keeps_other_slots_and_starts_historical_reversal_next_r0() -> None:
    state = ready({"a": "G5"})
    role = state.ga_roles["a"]
    role.stage, role.memory = 2, 12
    state.current_round = 6
    state.world_tick = state.ga_config.get("clock", "dusk_start")
    state.ga_history.extend([(5, "b", "move"), (5, "c", "attack")])
    state.players["a"].sp = 2
    turn(state, "a")
    execute(state, "g5_ascend", mode=Performance.PUBLIC)
    assert not state.ga_clock_paused and role.stage == 3
    turn(state, "b")
    execute(state, "forfeit")
    before = state.world_tick
    run_round(state, state.ga_controllers)
    assert state.world_tick == before and state.ga_clock_paused
    assert role.required == {"b": "move", "c": "attack"}
    assert role.disobedience == {"b": 1, "c": 1}
    assert state.players["b"].hp == 18 and state.players["c"].hp == 18


def test_no_legal_historical_verb_is_exempt_not_penalized_for_missing_ammo() -> None:
    state = ready({"a": "G5"})
    role = state.ga_roles["a"]
    role.stage = 3
    state.ga_world.begin_reversal(state, "a")
    role.required = {"b": "attack"}
    state.players["a"].hidden = state.players["c"].hidden = True
    for npc in state.ga_units.values():
        npc.isolation_id = "elsewhere"
    turn(state, "b")
    assert not any(a.verb.value == "attack" for a in legal_actions(state))
    execute(state, "forfeit")
    assert not role.disobedience


def test_closure_saves_real_wallet_and_chapters_before_display_reset() -> None:
    state = ready({"a": "G5"})
    role = state.ga_roles["a"]
    role.stage, role.pointer = 3, 1
    state.players["b"].pp = 7
    state.players["b"].chapters = {"chapter/1", "chapter/2", "chapter/3"}
    state.ga_world.begin_reversal(state, "a")
    run_round(state, state.ga_controllers)
    assert state.terminal_snapshot.reason.value == "reversal_closure"
    assert state.terminal_snapshot.results["b"].pp_balance == 7
    assert len(state.terminal_snapshot.results["b"].chapter_ids) == 3
    assert all(not p.awake and p.location.startswith("home:") for p in state.players.values())
    assert state.terminal_snapshot.survivor_ids == ("a", "b", "c")
