"""I2六原初的正常能力入口、真实事件和章节/死亡路径。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from engine.ga.contracts import DamageKind, Performance, TurnStage
from engine.ga.extensions import ChoicePending
from engine.ga.gameplay import legal_actions
from engine.ga.police import Police
from engine.ga.runtime import run_round
from engine.ga.state import add_armor
from engine.ga.talents.base import barrier
from tests.ga_i2_helpers import Decisions, execute, ready, turn


def test_t1_slash_double_billing_and_public_barrier_bypass_unlocks_three_chapters() -> None:
    state = ready({"a": "T1"})
    key = add_armor(state, "b", "ceramic")
    state.players["b"].defenses[key] = replace(state.players["b"].defenses[key], durability=3)
    barrier(state, "b", "fixture/wall", 20)
    state.players["a"].sp = 2
    turn(state, "a")
    execute(state, "t1_slash", targets=("b",), mode=Performance.PUBLIC, weapon="knife")
    assert state.players["b"].hp == 14
    assert state.players["b"].defenses["fixture/wall"].durability == 20
    assert state.players["a"].chapters == {"chapter/1", "chapter/2", "chapter/3"}
    assert state.players["b"].defenses[key].durability == 0


def test_t1_instant_cannot_bypass_barrier_and_still_pays_sp() -> None:
    state = ready({"a": "T1"})
    barrier(state, "b", "wall", 10)
    turn(state, "a")
    receipt = execute(state, "t1_slash", targets=("b",), mode=Performance.INSTANT, weapon="knife")
    assert state.players["b"].hp == 20 and state.players["b"].defenses["wall"].durability == 2
    assert any(c.account == "sp" and c.amount == 1 for c in receipt.costs)
    assert not state.players["a"].chapters


def test_t2_nonlethal_exempt_but_true_kill_is_crime_and_hides_until_first_kill() -> None:
    state = ready({"a": "T2"})
    state.players["a"].hidden = True
    turn(state, "a")
    execute(state, "attack", targets=("b",), weapon="knife")
    assert state.players["a"].hidden and not state.ga_crimes
    state.players["b"].hp = 1
    turn(state, "a")
    execute(state, "attack", targets=("b",), weapon="knife")
    assert state.ga_crimes and not state.players["a"].hidden


def test_t2_repairs_same_template_only_not_similar_affinity_or_broken_armor() -> None:
    state = ready({"a": "T2"})
    shield = add_armor(state, "a", "shield")
    ceramic = add_armor(state, "a", "ceramic")
    enemy = add_armor(state, "b", "ceramic")
    for key in (shield, ceramic):
        state.players["a"].defenses[key] = replace(state.players["a"].defenses[key], durability=2)
    for _ in range(2):
        turn(state, "a")
        execute(state, "attack", targets=("b",), weapon="knife")
    assert state.players["a"].defenses[ceramic].durability == 3
    assert state.players["a"].defenses[shield].durability == 2


def test_t2_shadow_step_two_uses_no_extra_slot_and_no_retired_chase() -> None:
    state = ready({"a": "T2"})
    state.players["a"].sp = 2
    state.ga_controllers["a"] = Decisions(shadow_step=["lock", "lock", "lock"])
    for location in ("magic", "hospital", "shop"):
        turn(state, "b")
        execute(state, "move", location=location)
        state.players["a"].sp = 2
    assert state.ga_roles["a"].shadow_uses == 2
    assert state.players["a"].location == "hospital"
    assert not state.ga_pending_extras and not state.ga_pending_followups
    turn(state, "a")
    assert not any(a.operation == "t2_strike" for a in legal_actions(state))
    assert "chapter/2" in state.players["a"].chapters


def test_t2_effective_performances_in_two_real_rounds_unlock_final() -> None:
    state = ready({"a": "T2"})
    state.players["a"].hidden = True
    for _ in range(2):
        state.players["a"].sp = 2
        turn(state, "a")
        execute(state, "t2_take", mode=Performance.INSTANT, purchase="buy_armor", kind="shield")
        run_round(state, state.ga_controllers)
    assert "chapter/3" in state.players["a"].chapters
    assert "chapter/1" not in state.players["a"].chapters


def test_t3_jade_without_enemy_armor_still_creates_barrier_no_hp_loss() -> None:
    state = ready({"a": "T3"})
    turn(state, "a", TurnStage.T0)
    execute(state, "t3_jade")
    assert [state.players[k].hp for k in state.players] == [20, 20, 20]
    assert state.players["a"].defenses["t3/jade/a"].durability == 3
    turn(state, "b")
    execute(state, "attack", targets=("a",), weapon="knife")
    assert state.players["a"].hp == 19 and "chapter/1" in state.players["a"].chapters


def test_t3_starfall_is_area_petrify_and_own_petrified_target_kill_unlocks() -> None:
    state = ready({"a": "T3"})
    state.players["a"].sp = 2
    turn(state, "a")
    execute(state, "t3_starfall")
    assert state.players["b"].hp == state.players["c"].hp == 16
    assert state.players["b"].statuses["control"].name == "PETRIFIED"
    assert "chapter/2" in state.players["a"].chapters
    state.players["b"].hp = 1
    turn(state, "a")
    execute(state, "attack", targets=("b",), weapon="knife")
    assert "chapter/3" in state.players["a"].chapters


@pytest.mark.parametrize("first,second,result", [
    ("scissors", "scissors", "pierce"), ("rock", "rock", "copy"),
    ("paper", "paper", "immune"), ("scissors", "rock", "disable"),
    ("scissors", "paper", "extra"), ("rock", "paper", "conceal"),
])
def test_t4_all_six_real_secret_choices(first: str, second: str, result: str) -> None:
    state = ready({"a": "T4"})
    add_armor(state, "b", "ceramic")
    state.ga_controllers["a"] = Decisions(hexagram=[first])
    state.ga_controllers["b"] = Decisions(hexagram=[second])
    turn(state, "a")
    execute(state, "t4_hexagram", targets=("b",), mode=Performance.INSTANT)
    assert result in state.ga_roles["a"].results
    assert state.players["a"].chapters == {"chapter/1"}
    if result == "extra":
        assert state.ga_pending_extras == {"a": "t4_or_yue"}


def test_t4_no_answer_is_pending_not_default_paper_or_random() -> None:
    state = ready({"a": "T4"})
    state.ga_controllers["b"] = Decisions(hexagram=[None])
    turn(state, "a")
    with pytest.raises(ChoicePending):
        execute(state, "t4_hexagram", targets=("b",), mode=Performance.INSTANT)
    assert not state.ga_roles["a"].results
    assert any(w.state.value == "pending" for w in state.ga_choice_windows.values())


def test_t4_copy_empty_does_not_farm_chapter_or_sp() -> None:
    state = ready({"a": "T4"})
    turn(state, "a")
    execute(state, "t4_hexagram", targets=("b",), mode=Performance.INSTANT)
    assert state.players["a"].sp == 0 and not state.players["a"].chapters


def test_t7_insurance_death_owner_independent_no_armor_repair_and_prayer_after() -> None:
    state = ready({"a": "T7"}, count=4)
    key = add_armor(state, "b", "ceramic")
    state.players["b"].defenses[key] = replace(state.players["b"].defenses[key], durability=1)
    turn(state, "a")
    execute(state, "t7_insure", targets=("b",), mode=Performance.INSTANT)
    state.players["a"].hp = 1
    turn(state, "c")
    execute(state, "attack", targets=("a",), weapon="knife")
    assert not state.players["a"].alive and state.ga_insurance is not None
    state.players["b"].hp = 1
    turn(state, "c")
    execute(state, "attack", targets=("b",), weapon="knife")
    assert state.players["b"].hp == 10 and state.players["b"].sp == 0
    assert state.players["b"].defenses[key].durability == 0
    assert state.ga_roles["a"].used == 1 and "chapter/3" in state.players["a"].chapters


def test_t7_prayer_after_lament_remains_a_legal_paid_t1() -> None:
    state = ready({"a": "T7"})
    turn(state, "a")
    execute(state, "t7_insure", targets=("b",), mode=Performance.INSTANT)
    state.players["b"].hp = 1
    turn(state, "c")
    execute(state, "attack", targets=("b",), weapon="knife")
    state.players["a"].sp = 1
    turn(state, "a")
    execute(state, "t7_prayer", targets=("b",))
    assert state.players["b"].hp == 13
    assert state.players["a"].chapters == {"chapter/1", "chapter/2", "chapter/3"}


def test_t6_without_police_has_all_personal_chapters_no_new_attack_skill() -> None:
    state = ready({"a": "T6"})
    state.destroyed_locations.add("police")
    key = add_armor(state, "b", "shield")
    state.players["b"].defenses[key] = replace(state.players["b"].defenses[key], durability=1)
    state.players["b"].hp = 2
    turn(state, "a")
    assert not any(a.operation == "t6_supply" for a in legal_actions(state))
    execute(state, "attack", targets=("b",), weapon="knife")
    assert state.players["a"].chapters == {"chapter/1", "chapter/2", "chapter/3"}
