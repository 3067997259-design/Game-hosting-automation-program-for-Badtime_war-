"""公开假设投影与真实无天赋执行核对；不把路途预测当作完整世界模拟。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from engine.ga.ai.planning import propose_routes
from engine.ga.ai.projection import project_preparation, project_route
from engine.ga.contracts import TurnStage, thaw_json
from engine.ga.gameplay import execute_action, legal_actions, validate_request
from engine.ga.state import add_armor, observe
from tests.ga_i2_helpers import ready, turn


def execute_route(state: Any, objective: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """先假设整条路线成功，再通过正式预检逐步执行作对照。"""
    view = observe(state, "a")
    route = next(r for r in propose_routes(view, state.ga_config) if r.objective == objective)
    prediction = project_route(view, route, state.ga_config)
    for step in route.steps:
        turn(state, "a", step.stage)
        request = next(action for action in legal_actions(state) if step.matches(action))
        execute_action(state, validate_request(state, request))
    return prediction, thaw_json(observe(state, "a"))


def armor_values(player: dict[str, Any]) -> list[dict[str, Any]]:
    """投影ID与正式ID不同；比较属性、耐久、计费及承接范围。"""
    return [{key: value for key, value in armor.items() if key != "defense_id"}
            for armor in player["defense_details"].values()]


@pytest.mark.parametrize("objective", [
    "ammo:bow", "surgery:life", "armor:ceramic", "armor:at_field",
    "module:fire:2", "module:infinite:1", "module:pierce:1", "weapon:magic_barrage",
    "weapon:gauss_rifle", "heal",
])
def test_completed_route_projection_matches_actual_inventory_money_and_health(
    objective: str,
) -> None:
    state = ready()
    state.players["a"].hp -= 7
    state.players["a"].credits = 1
    prediction, actual = execute_route(state, objective)
    before, after = prediction["players"]["a"], actual["players"]["a"]
    for key in ("credits", "hp", "max_hp", "location", "ammo", "inventory", "weapons", "surgery",
                "hospital_heals", "modules", "bow_attribute"):
        assert before[key] == after[key], (objective, key)
    assert armor_values(before) == armor_values(after)
    assert prediction["module_stock"] == actual["module_stock"]
    assert "does_not_simulate_opponents_world_ticks_or_runtime_hooks" in (
        prediction["projection"]["assumptions"])


def test_repair_projection_obeys_existing_defense_capacity() -> None:
    state = ready()
    key = add_armor(state, "a", "shield")
    state.players["a"].defenses[key] = replace(state.players["a"].defenses[key], durability=5)
    prediction, actual = execute_route(state, f"repair:{key}")
    assert prediction["players"]["a"]["defense_details"] == (
        actual["players"]["a"]["defense_details"])


@pytest.mark.parametrize("operation", ["lock", "modules", "syringe", "sharpen", "police_stance"])
def test_t0_projection_matches_actual_preparation_and_does_not_mutate_input(operation: str) -> None:
    state = ready()
    player = state.players["a"]
    player.inventory.update({"module:fire": 1, "syringe": 1, "whetstone": 1})
    player.hp -= 3
    state.ga_police.captain = "a"
    turn(state, "a", TurnStage.T0)
    request = next(action for action in legal_actions(state) if action.operation == operation and (
        operation != "modules" or action.parameters["modules"] == ("fire",)))
    view = observe(state, "a")
    original = thaw_json(view)
    prediction = project_preparation(view, request, state.ga_config)
    assert thaw_json(view) == original
    assert not state.ga_rng.draws
    execute_action(state, validate_request(state, request))
    actual = thaw_json(observe(state, "a"))
    for key in ("hp", "inventory", "sharpened", "modules", "bow_attribute", "locked_targets"):
        assert prediction["players"]["a"][key] == actual["players"]["a"][key], key
    assert armor_values(prediction["players"]["a"]) == armor_values(actual["players"]["a"])
    assert prediction["schedule"]["grant"]["locked_target"] == (
        actual["schedule"]["grant"]["locked_target"])


@pytest.mark.parametrize("choice", ["knife", "shield"])
def test_wake_projection_includes_starting_bow_and_real_defense(choice: str) -> None:
    from engine.game_state import GameState

    state = GameState.new_ga(["a", "b"], seed=0)
    state.current_round = state.world_tick = 1
    turn(state, "a", TurnStage.T0)
    request = next(action for action in legal_actions(state)
                   if action.parameters["choice"] == choice)
    prediction = project_preparation(observe(state, "a"), request, state.ga_config)
    execute_action(state, validate_request(state, request))
    actual = thaw_json(observe(state, "a"))
    assert prediction["players"]["a"]["weapons"] == actual["players"]["a"]["weapons"]
    assert prediction["players"]["a"]["ammo"] == actual["players"]["a"]["ammo"]
    assert armor_values(prediction["players"]["a"]) == armor_values(actual["players"]["a"])


def test_loot_projection_obeys_ammo_and_syringe_caps_and_retains_remainder() -> None:
    state = ready()
    state.players["a"].ammo["bow"] = 9
    state.ground_loot["magic"] = [{"credits": 3, "weapons": {"gauss_rifle"},
        "defenses": {}, "affinities": {}, "armor_kinds": {}, "modules": ("fire",),
        "ammo": {"bow": 5}, "inventory": {"syringe": 3}}]
    prediction, actual = execute_route(state, "loot:magic")
    for key in ("credits", "inventory", "ammo", "weapons"):
        assert prediction["players"]["a"][key] == actual["players"]["a"][key]
    assert prediction["ground_loot"] == actual["ground_loot"]


def test_unknown_projection_is_explicit_and_route_does_not_keep_old_slot_lock() -> None:
    state = ready()
    turn(state, "a", TurnStage.T0)
    request = next(action for action in legal_actions(state) if action.operation == "lock")
    view = project_preparation(observe(state, "a"), request, state.ga_config)
    route = next(r for r in propose_routes(view, state.ga_config) if r.objective == "surgery:life")
    projected = project_route(view, route, state.ga_config)
    assert projected["schedule"]["grant"]["locked_target"] is None
    assert projected["players"]["a"]["locked_targets"] == []
    with pytest.raises(NotImplementedError):
        project_preparation(view, replace(request, operation="not_a_real_action"), state.ga_config)
    with pytest.raises(NotImplementedError):
        project_route(view, replace(route, objective="unsupported"), state.ga_config)


@pytest.mark.parametrize("kind", ["fragment", "molotov"])
def test_counter_projection_stops_after_procurement_and_arrival_before_any_damage(
    kind: str,
) -> None:
    state = ready()
    state.players["a"].credits = 0
    state.ga_police.wanted.append("a")
    view = observe(state, "a")
    route = next(route for route in propose_routes(view, state.ga_config)
                 if route.objective == f"counter:{kind}:police")
    original = thaw_json(view)
    projected = project_route(view, route, state.ga_config)
    assert projected["players"]["a"]["location"] == "police"
    assert projected["players"]["a"]["inventory"][kind] == route.facts["buy_count"]
    assert projected["players"]["a"]["credits"] == 0
    assert projected["units"] == original["units"]
    assert projected["police"]["wall"] == original["police"]["wall"]
    assert projected["projection"]["pre_throw"]
    assert projected["projection"]["remaining_throws"] == 2
    assert projected["projection"]["t1_turns"] == route.t1_turns
    assert "assumes_procurement_and_arrival_before_counter_throws" in (
        projected["projection"]["assumptions"])
    assert thaw_json(view) == original
    for step in route.steps:
        if step.operation == "throw":
            break
        turn(state, "a", step.stage)
        request = next(action for action in legal_actions(state) if step.matches(action))
        execute_action(state, validate_request(state, request))
    actual = observe(state, "a")
    for key in ("location", "credits", "inventory"):
        assert projected["players"]["a"][key] == actual["players"]["a"][key]
