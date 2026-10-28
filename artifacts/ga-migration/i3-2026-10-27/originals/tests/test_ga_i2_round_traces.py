"""I2真实R0～R4剧本：资源自然反馈、九角色完整局与四条三章路线。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from engine.game_state import GameState
from engine.ga.contracts import ActionRequest, ContractError, Performance, TurnStage
from engine.ga.controllers import MinimalController
from engine.ga.runtime import run_game, run_round
from engine.ga.setup import TALENTS
from engine.ga.state import add_armor
from tests.ga_i2_helpers import Decisions, ready


def choose(
    actions: tuple[ActionRequest, ...], operation: str, *, mode: Performance | None = None,
    target: str | None = None, **parameters: Any,
) -> ActionRequest:
    """剧本只拿真实候选，不向执行器注入伪计划。"""
    return next(a for a in actions if a.operation == operation and
                (mode is None or a.performance is mode) and
                (target is None or a.target_ids == (target,)) and
                all(a.parameters.get(k) == v for k, v in parameters.items()))


def hit_once_round(state: GameState) -> None:
    """一个真实标准槽的攻击把准备中的玩家推到SP2。"""
    class Attacker(Decisions):
        def choose(
            self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...], stage: TurnStage,
        ) -> ActionRequest | None:
            if stage is TurnStage.T1:
                return choose(actions, "attack", target="a", weapon="knife")
            return None

    state.ga_controllers["b"] = Attacker()
    run_round(state, state.ga_controllers)
    state.ga_controllers["b"] = Decisions()
    assert state.players["a"].sp == 2


def test_g0_three_chapters_with_real_standard_turns_and_current_t1_public() -> None:
    state = ready({"a": "G0"})
    hit_once_round(state)

    class G0Script(Decisions):
        def choose(
            self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...], stage: TurnStage,
        ) -> ActionRequest | None:
            rnd = view["round"]
            if stage is TurnStage.T0:
                if rnd == 3:
                    return choose(actions, "g0_mount", relics=("belt", "shield"))
                if rnd == 4:
                    return choose(actions, "g0_mount", relics=("glasses", "sight"))
                if rnd == 5:
                    return choose(actions, "g0_drone")
                return None
            if rnd == 5:
                return choose(actions, "g0_ar", target="b", fire_mode="point")
            if rnd == 6:
                return choose(actions, "g0_crossfire", mode=Performance.PUBLIC,
                              destroy_relic="none")
            return choose(actions, "forfeit")

    state.ga_controllers["a"] = G0Script()
    for _ in range(4):
        run_round(state, state.ga_controllers)
    assert state.players["a"].chapters == {"chapter/1", "chapter/2", "chapter/3"}
    assert state.current_round == 6 and not state.ga_roles["a"].drone_alive(state)
    starts = [e for e in state.ga_trace if e["label"] == "slot_start" and e["actor"] == "a"]
    assert len(starts) == 5 and all(e["kind"] == "standard" for e in starts)


def test_t4_three_chapters_across_four_standard_turns_real_secret_responses() -> None:
    state = ready({"a": "T4"})
    add_armor(state, "b", "ceramic")
    hit_once_round(state)

    class CastScript(Decisions):
        def choose(
            self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...], stage: TurnStage,
        ) -> ActionRequest | None:
            if stage is TurnStage.T0:
                return None
            mode = Performance.PUBLIC if view["round"] == 6 else Performance.INSTANT
            return choose(actions, "t4_hexagram", target="b", mode=mode)

    state.ga_controllers["a"] = CastScript(hexagram=["scissors", "rock", "paper", "scissors"])
    state.ga_controllers["b"] = Decisions(hexagram=["scissors", "rock", "paper", "rock"])
    for _ in range(4):
        run_round(state, state.ga_controllers)
    assert state.players["a"].chapters == {"chapter/1", "chapter/2", "chapter/3"}
    assert state.ga_roles["a"].results == {"pierce", "copy", "immune", "disable"}
    assert not state.ga_roles["a"].immunity_round == state.current_round


def test_g1_three_chapters_current_t0_sam_then_full_then_bounded_followups() -> None:
    state = ready({"a": "G1"})

    class BurnScript(Decisions):
        def choose(
            self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...], stage: TurnStage,
        ) -> ActionRequest | None:
            rnd = view["round"]
            if stage is TurnStage.T0:
                if rnd in (2, 4):
                    return choose(actions, "g1_sam")
                if rnd == 3:
                    return choose(actions, "g1_unarmor")
                return None
            if state.current_grant.kind.value == "step_followup":
                if rnd == 4:
                    return choose(actions, "attack", target="b", weapon="knife")
                return next(a for a in actions if a.verb.value == "move")
            if rnd == 4:
                return choose(actions, "g1_full", mode=Performance.PUBLIC)
            return choose(actions, "forfeit")

    state.ga_controllers["a"] = BurnScript()
    for _ in range(6):
        run_round(state, state.ga_controllers)
    assert state.players["a"].alive and state.ga_roles["a"].form == "unarmored"
    assert state.players["a"].chapters == {"chapter/1", "chapter/2", "chapter/3"}
    assert all(e["kind"] != "extra_turn" for e in state.ga_trace if e["label"] == "slot_start")


def test_g4_three_chapters_all_embers_from_actual_attacks_death_no_seeded_resource() -> None:
    state = ready({"a": "G4"})

    class FireScript(Decisions):
        def choose(
            self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...], stage: TurnStage,
        ) -> ActionRequest | None:
            if stage is TurnStage.T0:
                return choose(actions, "g4_ascend") if view["round"] == 6 else None
            if view["round"] < 6:
                return choose(actions, "g4_slash", target="b", mode=Performance.INSTANT,
                              weapon="knife")
            return choose(actions, "g4_judgment", mode=Performance.PUBLIC)

    class Harass(Decisions):
        def choose(
            self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...], stage: TurnStage,
        ) -> ActionRequest | None:
            if stage is TurnStage.T0:
                return None
            return choose(actions, "attack", target="a", weapon="knife") if view["round"] < 6 else (
                choose(actions, "forfeit"))

    state.ga_controllers["a"] = FireScript()
    state.ga_controllers["c"] = Harass()
    for _ in range(5):
        run_round(state, state.ga_controllers)
    assert state.players["a"].chapters == {"chapter/1", "chapter/2", "chapter/3"}
    assert state.ga_roles["a"].form == "human" and state.players["a"].hp == 4
    assert not state.ga_extra_used


@pytest.mark.parametrize("slot", tuple(TALENTS))
def test_each_i2_talent_runs_from_own_wake_to_complete_terminal(slot: str) -> None:
    other = "T6" if slot != "T6" else "T1"
    state = GameState.new_ga(["a", "b"], seed=3, talents={"a": slot, "b": other})
    terminal = run_game(state, {p: MinimalController() for p in state.players}, timeout_seconds=10)
    assert state.game_over and terminal.ruleset == "v2.0-ga"
    assert set(terminal.results) == {"a", "b"}
    assert all(p.awake and p.home_locked for p in state.players.values())


@pytest.mark.parametrize("slot", ["T5", "G2", "G3", "G5", "G6", "G7", "old_g6_template"])
def test_unimplemented_or_retired_slots_fail_without_legacy_fallback(slot: str) -> None:
    with pytest.raises(ContractError, match="unimplemented GA talent"):
        GameState.new_ga(["a", "b"], talents={"a": slot})
