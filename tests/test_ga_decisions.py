"""I4.0全决策记录、分歧定位、秘密边界与完整对局重放。"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

import pytest

from engine.game_state import GameState
from engine.ga.controllers import MinimalController
from engine.ga.contracts import ActionRequest, ChoiceWindow, ContractError, TurnStage, thaw_json
from engine.ga.decisions import (
    CandidateAssessment, DecisionExplanation, GoalIntent, RecordingController, ReplayController,
)
from engine.ga.extensions import ask
from engine.ga.gameplay import build_options
from engine.ga.runtime import run_game, run_round
from engine.ga.state import observe
from engine.ga.status import put_status
from tests.ga_i2_helpers import execute, ready, turn


class Deliberate(MinimalController):
    """用明确的非默认选择检验重放，不能由继承的最小策略碰巧答对。"""

    def wants_public(self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...]) -> bool:
        return False

    def reroll_initiative(self, view: Mapping[str, Any], value: int) -> bool:
        return True

    def escape_petrify(self, view: Mapping[str, Any], attempt: int) -> bool:
        return False

    def respond(
        self, view: Mapping[str, Any], window: ChoiceWindow, context: Mapping[str, Any],
    ) -> str | None:
        return window.options[-1]


@pytest.mark.parametrize("slot", (None, "T2", "T4", "T7", "G2", "G3", "G5", "G6", "G7"))
def test_full_run_replay_preserves_events_rng_scores_and_explanations(slot: str | None) -> None:
    talents = {"a": slot, "b": "T6"} if slot else {}
    state = GameState.new_ga(["a", "b"], seed=3, talents=talents)
    recorders = {actor: RecordingController(MinimalController()) for actor in state.players}
    first = run_game(state, recorders)
    replay = GameState.new_ga(["a", "b"], seed=3, talents=talents)
    controllers = {actor: ReplayController(r.replay_inputs()) for actor, r in recorders.items()}
    second = run_game(replay, controllers)
    assert first == second
    assert state.ga_ledger.events == replay.ga_ledger.events
    assert state.ga_rng.draws == replay.ga_rng.draws
    assert state.ga_trace == replay.ga_trace
    for controller in controllers.values():
        controller.assert_consumed()


def test_real_round_replays_nondefault_public_reroll_and_escape() -> None:
    def world() -> GameState:
        state = ready({"a": "T7", "b": "T4"})
        for p in state.players.values():
            p.pp, p.sp = 5, 2
        put_status(state.players["c"], state.ga_config, state.current_round,
                   "PETRIFIED", source_id="fixture")
        return state

    first = world()
    recorded = {actor: RecordingController(Deliberate()) for actor in first.players}
    run_round(first, recorded)
    kinds = {r["kind"] for rec in recorded.values() for r in rec.records}
    assert {"reroll_initiative", "wants_public", "escape_petrify"} <= kinds
    second = world()
    replay = {actor: ReplayController(rec.replay_inputs()) for actor, rec in recorded.items()}
    run_round(second, replay)
    assert thaw_json(observe(first, "a")) == thaw_json(observe(second, "a"))
    assert first.ga_rng.draws == second.ga_rng.draws
    for controller in replay.values():
        controller.assert_consumed()


def test_secret_commitment_never_enters_another_observation() -> None:
    state = ready()
    first = RecordingController(Deliberate(), detailed=True)
    second = RecordingController(Deliberate(), detailed=True)
    state.ga_controllers.update(a=first, b=second)
    answer = "secret-answer-not-in-public-state"
    ask(state, "c", "a", ("no", answer), kind="secret", secret=True)
    ask(state, "c", "b", ("left", "right"), kind="secret", secret=True)
    assert answer in repr(first.records)
    assert answer not in repr(second.records)
    assert answer not in repr(observe(state, "b"))


@pytest.mark.parametrize("change", ("view", "order", "kind", "selection", "schema"))
def test_replay_rejects_drift_and_illegal_selection(change: str) -> None:
    state = ready()
    turn(state, "a", TurnStage.T1)
    options = build_options(state, state.current_grant, TurnStage.T1)
    actions = tuple(o.request for o in options)
    view = observe(state, "a", options=options)
    recorder = RecordingController(MinimalController())
    recorder.choose(view, actions, TurnStage.T1)
    script = deepcopy(recorder.replay_inputs())
    if change == "view":
        view = thaw_json(view)
        view["players"]["a"]["hp"] -= 1
    elif change == "order":
        actions = tuple(reversed(actions))
    elif change == "kind":
        script["decisions"][0]["kind"] = "respond"
    elif change == "selection":
        script["decisions"][0]["selected"]["target_ids"] = ["unknown"]
    else:
        script["schema"] = "old"
    before = state.players["a"].credits, list(state.ga_rng.draws)
    with pytest.raises(ContractError):
        ReplayController(script).choose(view, actions, TurnStage.T1)
    assert before == (state.players["a"].credits, state.ga_rng.draws)


def test_exhaustion_leftovers_and_missing_response_are_explicit() -> None:
    state = ready()
    recorder = RecordingController(Deliberate())
    view = observe(state, "a")
    recorder.reroll_initiative(view, 1)
    replay = ReplayController(recorder.replay_inputs())
    with pytest.raises(ContractError, match="unused"):
        replay.assert_consumed()
    assert replay.reroll_initiative(view, 1)
    replay.assert_consumed()
    with pytest.raises(ContractError, match="exhausted"):
        replay.reroll_initiative(view, 1)


def test_invalid_controller_keeps_failed_input_and_never_defaults() -> None:
    class Invalid(Deliberate):
        def reroll_initiative(self, view: Mapping[str, Any], value: int) -> Any:
            return "yes"

    state = ready()
    recorder = RecordingController(Invalid())
    with pytest.raises(ContractError, match="bool"):
        recorder.reroll_initiative(observe(state, "a"), 1)
    assert recorder.records[0]["error"]["type"] == "ContractError"
    assert recorder.records[0]["input"]["context"]["value"] == 1


def test_minimal_reason_is_reported_without_fabricated_candidate_scores() -> None:
    state = ready()
    turn(state, "a")
    options = build_options(state, state.current_grant, TurnStage.T1)
    recorder = RecordingController(MinimalController(), detailed=True)
    recorder.choose(observe(state, "a", options=options), tuple(o.request for o in options),
                    TurnStage.T1)
    explanation = recorder.records[0]["explanation"]
    assert explanation["reason"] == "minimal.first_attack_prefer_weapon"
    assert sum(c["disposition"] == "preferred" for c in explanation["candidates"]) == 1
    assert all(c["estimates"] == {} for c in explanation["candidates"])


def test_goal_and_alternative_estimates_round_trip_without_becoming_engine_facts() -> None:
    report = DecisionExplanation("reserve_sp", (
        CandidateAssessment(0, "deferred", "keep_public_budget", {"future_score": 4.0}),
    ), (GoalIntent("perform", "interrupted", "target_left", {"reserve_sp": 2}),))
    assert report.to_dict(1)["goals"][0]["state"] == "interrupted"
    with pytest.raises(ContractError, match="out-of-menu"):
        report.to_dict(0)
    with pytest.raises(ContractError):
        CandidateAssessment(0, "considered", "unknown", {"score": float("nan")})


def test_afterlife_real_action_and_skip_are_both_replayed() -> None:
    class Ghost(Deliberate):
        def choose_afterlife(
            self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...],
        ) -> ActionRequest | None:
            return next((a for a in actions if a.operation == "afterlife_bet"), None)

    first, second = ready(), ready()
    for state in (first, second):
        state.players["a"].hp = 0
        state.players["a"].pp = 5
    recorder = RecordingController(Ghost())
    run_round(first, {**first.ga_controllers, "a": recorder})
    actions = [r for r in recorder.records if r["kind"] == "choose_afterlife"]
    assert len(actions) == 2 and actions[0]["selected"] and actions[1]["selected"] is None
    replay = ReplayController(recorder.replay_inputs())
    run_round(second, {**second.ga_controllers, "a": replay})
    replay.assert_consumed()
    assert first.ga_afterlife.bets == second.ga_afterlife.bets
    assert first.ga_ledger.events == second.ga_ledger.events


def test_actual_macro_substeps_replay_costs_ammunition_and_root_events() -> None:
    from tests.test_ga_g7 import Steps, fused

    first = fused()
    recorder = RecordingController(Steps(["g7_macro_shoot", "g7_macro_shoot"]), detailed=True)
    first.ga_controllers["a"] = recorder
    turn(first, "a")
    execute(first, "g7_macro")
    assert len(recorder.records) >= 3
    assert all(r["kind"] == "respond" for r in recorder.records)
    second = fused()
    replay = ReplayController(recorder.replay_inputs())
    second.ga_controllers["a"] = replay
    turn(second, "a")
    execute(second, "g7_macro")
    replay.assert_consumed()
    assert first.players["a"].resources == second.players["a"].resources
    assert first.players["a"].ammo == second.players["a"].ammo
    assert first.ga_ledger.events == second.ga_ledger.events
