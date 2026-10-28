"""I4.1规划跨真实菜单执行，验证筹款、剩余预算与中断生命周期。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from engine.ga.ai.planning import Budget, GoalStack, Route, Step, propose_routes
from engine.ga.contracts import TurnStage, thaw_json
from engine.ga.gameplay import execute_action, legal_actions, validate_request
from engine.ga.police import Police
from engine.ga.state import observe
from tests.ga_i2_helpers import ready, turn


def find_route(state: Any, objective: str) -> Route:
    """重新用当前公开事实报价，不复用旧资源快照。"""
    return next(route for route in propose_routes(observe(state, "a"), state.ga_config)
                if route.objective == objective)


def perform_step(state: Any, stack: GoalStack) -> None:
    """真实预检和执行后向目标栈反馈只读回执。"""
    assert stack.active is not None
    turn(state, "a", stack.active.steps[0].stage)
    action = stack.active.first_action(legal_actions(state))
    assert action is not None
    receipt = execute_action(state, validate_request(state, action))
    view = observe(state, "a")
    stack.reconcile(view, propose_routes(view, state.ga_config), receipt)


def test_natural_funding_purchase_and_module_assembly_complete() -> None:
    state = ready()
    state.players["a"].credits = 0
    stack = GoalStack()
    route = find_route(state, "module:fire:2")
    assert [step.operation for step in route.steps] == [
        "work", "work", "move", "buy_module", "buy_module", "modules"]
    assert route.costs.credits == 2 * state.ga_config.get("modules", "fire", "price")
    assert route.facts["credit_income"] == route.costs.credits
    stack.commit(route)
    for _ in range(6):
        perform_step(state, stack)
    assert state.players["a"].modules == ("fire", "fire")
    assert state.players["a"].credits == 0
    assert stack.active is None and stack.completed_steps == 6
    assert stack.intents()[-1].state == "completed"


def test_hospital_route_gets_funds_only_at_shop_and_surgery_spends_whole_wallet() -> None:
    state = ready()
    state.players["a"].location = "hospital"
    state.players["a"].credits = 1
    route = find_route(state, "surgery:life")
    assert route.steps[0] == Step("move", {"location": "shop"})
    assert route.steps[-1] == Step("surgery", {"kind": "life"})
    assert route.costs.credits == 5 and route.facts["wallet_after"] == 0
    stack = GoalStack()
    stack.commit(route)
    while stack.active:
        perform_step(state, stack)
    assert state.players["a"].surgery == "life" and state.players["a"].credits == 0
    assert state.players["a"].max_hp == state.ga_config.get("start", "max_hp") + (
        state.ga_config.get("hospital", "life_max_hp_bonus"))


def test_repeated_observation_does_not_advance_or_complete_goal() -> None:
    state = ready()
    stack = GoalStack()
    route = find_route(state, "module:fire:1")
    stack.commit(route)
    view = observe(state, "a")
    before = stack.intents()
    for _ in range(5):
        stack.reconcile(view, propose_routes(view, state.ga_config))
        stack.commit(route)
    assert stack.active == route and stack.completed_steps == 0
    assert stack.intents() == before


def test_receipt_is_idempotent_and_cancelled_step_does_not_spend_or_progress() -> None:
    state = ready()
    stack = GoalStack()
    stack.commit(find_route(state, "surgery:life"))
    turn(state, "a")
    action = stack.active.first_action(legal_actions(state))
    receipt = execute_action(state, validate_request(state, action))
    view = observe(state, "a")
    routes = propose_routes(view, state.ga_config)
    stack.reconcile(view, routes, receipt)
    stack.reconcile(view, routes, receipt)
    assert stack.completed_steps == 1
    turn(state, "a")
    surgery = stack.active.first_action(legal_actions(state))
    cancelled = {"request": surgery.to_dict(), "cancelled": True}
    stack.reconcile(view, routes, cancelled)
    assert stack.active is None and stack.paused is not None
    assert stack.completed_steps == 1 and state.players["a"].credits == 4
    assert stack.reserved.credits == 0


def test_interruption_releases_budget_then_reprices_resume() -> None:
    state = ready()
    stack = GoalStack()
    stack.commit(find_route(state, "module:fire:2"))
    assert stack.reserved.credits == 4
    stack.interrupt("incoming_lethal_threat")
    assert stack.reserved.credits == 0
    state.players["a"].hp -= 8
    stack.commit(find_route(state, "heal"))
    assert stack.reserved.credits == state.ga_config.get("hospital", "heal_price")
    assert stack.paused.objective == "module:fire:2"
    while stack.active:
        perform_step(state, stack)
    new = find_route(state, "module:fire:2")
    assert new.steps[0] == Step("move", {"location": "shop"})
    assert any(step.operation == "work" for step in new.steps)
    stack.commit(new)
    assert stack.paused is None
    assert any(intent.reason == "replanned_and_resumed" for intent in stack.intents())


def test_resume_can_interrupt_another_active_route_without_abandoning_resumed_goal() -> None:
    state = ready()
    stack = GoalStack()
    fire = find_route(state, "module:fire:2")
    stack.commit(fire)
    state.players["a"].hp -= 4
    heal = find_route(state, "heal")
    stack.commit(heal)
    stack.commit(fire)
    assert stack.active.goal_id == fire.goal_id and stack.paused.goal_id == heal.goal_id
    assert not any(intent.goal_id == fire.goal_id and intent.state == "abandoned"
                   for intent in stack.intents())
    assert any(intent.reason == "replanned_and_resumed" for intent in stack.intents())


@pytest.mark.parametrize("change", ["stock", "building", "death"])
def test_unavailable_route_is_abandoned_without_sunk_cost_lock(change: str) -> None:
    state = ready()
    stack = GoalStack()
    stack.commit(find_route(state, "module:fire:1"))
    if change == "stock":
        state.module_stock["fire"] = 0
    elif change == "building":
        state.destroyed_locations.add("magic")
    else:
        state.players["a"].hp = 0
    view = observe(state, "a")
    stack.reconcile(view, propose_routes(view, state.ga_config))
    assert stack.active is None and stack.reserved.credits == 0
    assert stack.intents()[-1].state == "abandoned"


def test_no_funding_route_without_shop_and_no_purposeless_work_goal() -> None:
    state = ready()
    state.players["a"].credits = 0
    state.destroyed_locations.add("shop")
    routes = propose_routes(observe(state, "a"), state.ga_config)
    assert not any(route.objective.startswith(("module:", "armor:", "surgery:"))
                   for route in routes)
    assert not any(step.operation == "work" for route in routes for step in route.steps)
    assert any(route.objective == "weapon:magic_barrage" for route in routes)


def test_pending_captain_application_does_not_force_standing_at_station() -> None:
    state = ready()
    state.ga_police.applicants.append("a")
    route = find_route(state, "captain")
    assert route.steps == () and route.facts["queued"]
    stack = GoalStack()
    stack.commit(route)
    state.ga_police.captain = "a"
    view = observe(state, "a")
    stack.reconcile(view, propose_routes(view, state.ga_config))
    assert stack.active is None and stack.intents()[-1].state == "completed"


def test_resource_budget_is_immutable_and_does_not_mix_accounts() -> None:
    state = ready()
    player = observe(state, "a")["players"]["a"]
    reserve = Budget(credits=4, ammo={"bow": 5})
    assert reserve.affordable(player)
    assert not replace(reserve, pp=1).affordable(player)
    assert not replace(reserve, hp=player["hp"]).affordable(player)
    with pytest.raises(TypeError):
        reserve.ammo["bow"] = 0
    with pytest.raises(ValueError):
        Budget(credits=-1)


def test_step_binds_semantics_not_old_grant_and_respects_t0() -> None:
    state = ready()
    state.players["a"].inventory["module:fire"] = 1
    route = find_route(state, "module:fire:1")
    assert route.costs.credits == 0 and route.t1_turns == 0
    assert route.first_action(legal_actions(state)) is None
    turn(state, "a", TurnStage.T0)
    first = route.first_action(legal_actions(state))
    turn(state, "a", TurnStage.T0)
    second = route.first_action(legal_actions(state))
    assert first.grant_id != second.grant_id and route.steps[0].matches(second)


def test_full_ammo_ground_pile_does_not_create_unexecutable_loot_plan() -> None:
    state = ready()
    state.players["a"].ammo["bow"] = state.ga_config.get("start", "arrow_cap")
    view = thaw_json(observe(state, "a"))
    view["ground_loot"] = {"magic": [{"credits": 0, "ammo": {"bow": 2}}]}
    assert not any(route.family == "loot" for route in propose_routes(view, state.ga_config))
    view["ground_loot"]["magic"][0]["credits"] = 1
    assert any(route.objective == "loot:magic" for route in propose_routes(view, state.ga_config))


def test_police_counter_funds_full_volley_before_approach_and_completes_on_throw() -> None:
    state = ready()
    state.ga_police.wanted.append("a")
    state.players["a"].credits = 0
    stack = GoalStack()
    route = find_route(state, "counter:fragment:police")
    assert [step.operation for step in route.steps] == [
        "work", "work", "move", "buy_item", "buy_item", "move", "throw", "throw"]
    assert route.costs.credits == 2 * state.ga_config.get("consumables", "price")
    assert route.facts["remaining_throws"] == route.facts["buy_count"] == 2
    assert route.steps[-1].target_ids == state.ga_police.officer_ids
    assert route.t1_turns == len(route.steps)
    stack.commit(route)
    for _ in range(6):
        perform_step(state, stack)
        assert stack.active is not None
    assert state.players["a"].inventory["fragment"] == 2
    assert stack.reserved.items == {"fragment": 2}
    perform_step(state, stack)
    assert stack.active is not None and stack.active.facts["remaining_throws"] == 1
    assert stack.active.facts["buy_count"] == 0
    assert [state.ga_units[key].hp for key in state.ga_police.officer_ids] == [2, 2, 2]
    assert stack.active.steps[0].operation == "throw"
    perform_step(state, stack)
    assert all(not state.ga_units[key].alive for key in state.ga_police.officer_ids)
    assert stack.active is None and stack.completed_steps == 8
    assert stack.intents()[-1].state == "completed"
    assert state.players["a"].credits == 0


def test_existing_counter_stock_needs_no_destroyed_supplier_and_is_not_completion() -> None:
    state = ready()
    state.ga_police.wanted.append("a")
    state.players["a"].inventory["fragment"] = 2
    state.destroyed_locations.add("military")
    route = find_route(state, "counter:fragment:police")
    assert route.costs.credits == 0 and route.facts["buy_count"] == 0
    assert [step.operation for step in route.steps] == ["move", "throw", "throw"]
    stack = GoalStack()
    stack.commit(route)
    view = observe(state, "a")
    stack.reconcile(view, propose_routes(view, state.ga_config))
    assert stack.active is not None and stack.completed_steps == 0
    assert stack.active.first_action(()) is None


def test_partial_counter_stock_only_buys_shortfall_and_dead_officers_leave_next_menu() -> None:
    state = ready()
    state.ga_police.wanted.append("a")
    state.players["a"].inventory["fragment"] = 1
    state.ga_units[state.ga_police.officer_ids[0]].hp = 1
    stack = GoalStack()
    route = find_route(state, "counter:fragment:police")
    assert route.facts["buy_count"] == 1
    assert route.costs.credits == state.ga_config.get("consumables", "price")
    assert route.reserves.items == {"fragment": 1}
    stack.commit(route)
    while stack.active.steps[0].operation != "throw":
        perform_step(state, stack)
    perform_step(state, stack)
    assert stack.active is not None and stack.active.facts["remaining_throws"] == 1
    assert stack.active.steps[0].target_ids == state.ga_police.officer_ids[1:]
    perform_step(state, stack)
    assert stack.active is None
    assert all(not state.ga_units[key].alive for key in state.ga_police.officer_ids)


def test_counter_retargets_moving_captain_and_drops_obsolete_location_intent() -> None:
    state = ready()
    state.ga_police.captain = "b"
    state.ga_police.stance = "wall"
    Police().sync(state)
    route = find_route(state, "counter:fragment:shop")
    assert route.steps[-1].target_ids == ("b", "c", *state.ga_police.officer_ids)
    stack = GoalStack()
    stack.commit(route)
    state.players["b"].location = "magic"
    Police().sync(state)
    view = observe(state, "a")
    routes = propose_routes(view, state.ga_config)
    stack.reconcile(view, routes)
    assert stack.active is None and stack.intents()[-1].state == "abandoned"
    revised = next(route for route in routes if route.objective == "counter:fragment:magic")
    assert revised.facts["destination"] == "magic"
    assert revised.steps[-1].target_ids == ("b", *state.ga_police.officer_ids)


def test_mobile_foreign_captain_permits_prospective_counter_but_own_roster_does_not() -> None:
    state = ready()
    state.ga_police.captain = "b"
    Police().sync(state)
    route = find_route(state, "counter:fragment:shop")
    assert route.facts["prospective_police_risk"]
    assert "a" not in observe(state, "a")["police"]["wanted"]
    state.ga_police.captain = "a"
    Police().sync(state)
    assert not any(route.family == "counter" for route in propose_routes(
        observe(state, "a"), state.ga_config))


@pytest.mark.parametrize("condition", ["neutral", "stopped", "dead"])
def test_counter_requires_active_relevant_police_threat(condition: str) -> None:
    state = ready()
    stack = GoalStack()
    if condition != "neutral":
        state.ga_police.wanted.append("a")
        stack.commit(find_route(state, "counter:fragment:police"))
    if condition == "stopped":
        state.ga_police.stopped.add("fixture")
    elif condition == "dead":
        for key in state.ga_police.officer_ids:
            state.ga_units[key].hp = 0
    view = observe(state, "a")
    routes = propose_routes(view, state.ga_config)
    assert not any(route.family == "counter" for route in routes)
    stack.reconcile(view, routes)
    assert stack.active is None and stack.reserved == Budget()
    if condition != "neutral":
        assert stack.intents()[-1].reason == "counter_threat_gone"


def test_capacity_limited_molotov_volley_finishes_without_pretending_dot_kills() -> None:
    state = ready()
    state.ga_police.wanted.append("a")
    stack = GoalStack()
    route = find_route(state, "counter:molotov:police")
    assert route.facts["capacity_limited"]
    assert route.facts["remaining_throws"] == state.ga_config.get("consumables", "hold_cap")
    stack.commit(route)
    while stack.active and stack.active.steps[0].operation != "throw":
        perform_step(state, stack)
    perform_step(state, stack)
    assert stack.active is not None
    assert stack.active.facts["remaining_throws"] == 1
    assert stack.active.facts["buy_count"] == 0
    assert stack.active.steps[0].operation == "throw"
    perform_step(state, stack)
    assert stack.active is None
    assert all(state.ga_units[key].alive for key in state.ga_police.officer_ids)
    assert stack.intents()[-1].reason == "execution_confirmed"
