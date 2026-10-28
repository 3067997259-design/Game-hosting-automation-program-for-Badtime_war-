"""I4.1策略集成：真实对局、行动回执、有限预算与投资时机。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from engine.game_state import GameState
from engine.ga.ai.controller import BasicAI
from engine.ga.ai.forecast import estimate_attack
from engine.ga.ai.planning import propose_routes
from engine.ga.ai.projection import project_preparation
from engine.ga.ai.strategy import Strategy
from engine.ga.contracts import ActionRequest, ContractError, TurnStage, freeze_json, thaw_json
from engine.ga.decisions import RecordingController, ReplayController, notify_action_result
from engine.ga.gameplay import build_options, execute_action, validate_request
from engine.ga.runtime import run_game
from engine.ga.state import observe
from tests.ga_i2_helpers import ready, turn


def menu(state: GameState, stage: TurnStage) -> tuple[Mapping[str, Any], tuple[ActionRequest, ...]]:
    """公开观察和候选必须来自同一次正式枚举。"""
    state.current_stage = stage
    options = build_options(state, state.current_grant, stage)
    return observe(state, "a", options=options), tuple(option.request for option in options)


@pytest.mark.parametrize("budget", [32, 96])
def test_natural_games_fulfil_plans_within_budget_and_replay_exactly(budget: int) -> None:
    state = GameState.new_ga(["a", "b"], seed=7)
    controllers = {actor: RecordingController(BasicAI(state.ga_config, node_budget=budget))
                   for actor in state.players}
    terminal = run_game(state, controllers, timeout_seconds=30)
    assert terminal is not None and state.game_over
    completed = []
    for actor, recorder in controllers.items():
        decisions = [record for record in recorder.records if record["kind"] == "choose"]
        assert decisions[0]["selected"]["operation"] == "wake"
        assert not any("error" in record for record in recorder.records)
        diagnostic = recorder.controller.diagnostics
        assert 0 < diagnostic["max_nodes"] <= budget
        assert diagnostic["decisions"] == len(decisions)
        completed.extend(goal for record in decisions for goal in
                         record["explanation"]["goals"] if goal["state"] == "completed")
    assert completed, "有目标提案还不够，至少一条路线必须有实际完成证据"
    replay = GameState.new_ga(["a", "b"], seed=7)
    replay_controllers = {actor: ReplayController(recorder.replay_inputs())
                          for actor, recorder in controllers.items()}
    assert run_game(replay, replay_controllers, timeout_seconds=30) == terminal
    assert replay.ga_rng.draws == state.ga_rng.draws
    assert replay.ga_ledger.events == state.ga_ledger.events
    assert replay.ga_trace == state.ga_trace
    for controller in replay_controllers.values():
        controller.assert_consumed()


def test_same_seed_and_budget_make_same_decisions_without_timing_dependence() -> None:
    records = []
    for _ in range(2):
        state = GameState.new_ga(["a", "b"], seed=3)
        controllers = {actor: RecordingController(BasicAI(state.ga_config, node_budget=32))
                       for actor in state.players}
        run_game(state, controllers, timeout_seconds=30)
        records.append({actor: controller.replay_inputs() for actor, controller in
                        controllers.items()})
    assert records[0] == records[1]


@pytest.mark.parametrize("budget", [True, 0, 15, 4097])
def test_invalid_search_budget_is_rejected(budget: int) -> None:
    with pytest.raises(ValueError, match="node_budget"):
        BasicAI(ready(count=2).ga_config, node_budget=budget)


@pytest.mark.parametrize("change", ["config", "talent", "menu_length", "menu_order"])
def test_scope_and_current_menu_mismatches_are_rejected_before_execution(change: str) -> None:
    state = ready(count=2)
    turn(state, "a")
    view, actions = menu(state, TurnStage.T1)
    altered = thaw_json(view)
    if change == "config":
        altered["parameter_fingerprint"] = "different_rules"
    elif change == "talent":
        altered["players"]["b"]["talent"] = "T6"
    elif change == "menu_length":
        actions = actions[:-1]
    else:
        actions = tuple(reversed(actions))
    before = thaw_json(observe(state, "a"))
    with pytest.raises(ContractError):
        BasicAI(state.ga_config).choose(freeze_json(altered), actions, TurnStage.T1)
    assert thaw_json(observe(state, "a")) == before
    assert not state.ga_rng.draws


def test_lock_then_ranged_lethal_attack_does_not_chase_target_to_other_building() -> None:
    state = ready(count=2)
    state.players["a"].location = "shop"
    state.players["b"].location = "magic"
    state.players["b"].hp = state.ga_config.get("weapons", "bow", "damage")
    controller = BasicAI(state.ga_config)
    turn(state, "a", TurnStage.T0)
    view, actions = menu(state, TurnStage.T0)
    preparation = controller.choose(view, actions, TurnStage.T0)
    assert preparation in actions and preparation.operation == "lock"
    execute_action(state, validate_request(state, preparation))
    view, actions = menu(state, TurnStage.T1)
    attack = controller.choose(view, actions, TurnStage.T1)
    assert attack in actions and attack.operation == "attack"
    assert attack.target_ids == ("b",) and attack.parameters["weapon"] == "bow"
    assert state.players["a"].location != state.players["b"].location


def test_historical_lock_does_not_replace_lock_bonus_for_current_grant() -> None:
    state = ready(count=2)
    state.players["a"].location = "shop"
    state.players["b"].location = "magic"
    state.players["b"].hp = state.ga_config.get("weapons", "bow", "damage")
    state.players["a"].locked_targets.add("b")
    turn(state, "a", TurnStage.T0)
    assert state.current_grant.locked_target is None
    view, actions = menu(state, TurnStage.T0)
    selected = BasicAI(state.ga_config).choose(view, actions, TurnStage.T0)
    assert selected is not None and selected.operation == "lock"
    assert selected.target_ids == ("b",)


def test_lock_increases_lethal_opportunity_when_no_armor_break_reward_is_lost() -> None:
    state = ready(count=2)
    state.players["b"].location = "magic"
    state.players["b"].hp = state.ga_config.get("weapons", "bow", "damage")
    turn(state, "a", TurnStage.T0)
    view, actions = menu(state, TurnStage.T0)
    lock = next(action for action in actions if action.operation == "lock")
    projected = project_preparation(view, lock, state.ga_config)
    before = estimate_attack(view, "a", "b", "bow", state.ga_config, locked=False)
    after = estimate_attack(projected, "a", "b", "bow", state.ga_config, locked=True)
    assert 0 < before.kill_probability < after.kill_probability == 1
    assert after.expected_hp > before.expected_hp
    assert before.expected_breaks == after.expected_breaks == 0
    strategy = Strategy(state.ga_config)
    old_value = strategy.opportunity(view, "a", may_prepare=False)
    new_value = strategy.opportunity(projected, "a", may_prepare=False)
    assert new_value > old_value
    assert new_value == pytest.approx(after.score_gain)
    assert BasicAI(state.ga_config).choose(view, actions, TurnStage.T0) == lock


def test_same_affordable_investment_is_rejected_when_time_to_repay_disappears() -> None:
    results = []
    for tick in (1, 22):
        state = ready(count=2)
        state.players["a"].location = "hospital"
        state.players["b"].location = "magic"
        state.current_round = state.world_tick = tick
        turn(state, "a")
        controller = BasicAI(state.ga_config)
        view, actions = menu(state, TurnStage.T1)
        selected = controller.choose(view, actions, TurnStage.T1)
        results.append((selected, controller.orchestrator.goals.active))
    assert results[0][1] is not None and results[0][1].family == "develop"
    assert results[1][0].operation == "attack" and results[1][1] is None


def test_lethal_opportunity_interrupts_old_equipment_plan_under_immediate_threat() -> None:
    state = ready(count=2)
    controller = BasicAI(state.ga_config)
    view = observe(state, "a")
    old = next(route for route in propose_routes(view, state.ga_config)
               if route.objective == "module:fire:2")
    controller.orchestrator.goals.commit(old)
    state.players["a"].hp = state.players["b"].hp = 1
    turn(state, "a")
    view, actions = menu(state, TurnStage.T1)
    selected = controller.choose(view, actions, TurnStage.T1)
    assert selected.operation == "attack" and selected.target_ids == ("b",)
    goals = controller.orchestrator.goals
    assert goals.active is None and goals.paused.goal_id == old.goal_id
    assert any(intent.state == "interrupted" for intent in goals.intents())


def test_exhausted_stock_abandons_old_plan_before_choosing_another_legal_action() -> None:
    state = ready(count=2)
    controller = BasicAI(state.ga_config)
    view = observe(state, "a")
    old = next(route for route in propose_routes(view, state.ga_config)
               if route.objective == "module:fire:1")
    controller.orchestrator.goals.commit(old)
    state.module_stock["fire"] = 0
    turn(state, "a")
    view, actions = menu(state, TurnStage.T1)
    assert controller.choose(view, actions, TurnStage.T1) in actions
    assert any(intent.goal_id == old.goal_id and intent.state == "abandoned"
               for intent in controller.orchestrator.goals.intents())


def test_recording_wrapper_forwards_real_result_once_without_advancing_on_proposal() -> None:
    state = ready(count=2)
    state.players["a"].location = "hospital"
    state.players["b"].location = "magic"
    turn(state, "a")
    controller = BasicAI(state.ga_config)
    recorder = RecordingController(controller)
    view, actions = menu(state, TurnStage.T1)
    selected = recorder.choose(view, actions, TurnStage.T1)
    assert controller.orchestrator.goals.active is not None
    assert controller.orchestrator.goals.completed_steps == 0
    receipt = execute_action(state, validate_request(state, selected))
    after = observe(state, "a")
    notify_action_result(recorder, after, receipt)
    notify_action_result(recorder, after, receipt)
    assert controller.orchestrator.goals.completed_steps == 1
    assert controller.orchestrator.goals.active is None
    assert len(recorder.records) == 1  # 结果通知不伪装成新的策略选择。
