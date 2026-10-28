"""警务Mind逐分支对照正式执行器；反制不预支未发生的R4、库存或奖励。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import pytest

from engine.game_state import GameState
from engine.ga.ai.forecast import estimate_police_response
from engine.ga.ai.police_mind import assess_police, estimate_area_counter, estimate_counter
from engine.ga.contracts import ActionRequest, TurnStage, thaw_json
from engine.ga.gameplay import build_options, execute_action, validate_request
from engine.ga.police import Police
from engine.ga.state import observe
from engine.ga.status import put_status
from tests.ga_i2_helpers import ready, turn


def formation(*, captain: str | None = "b", actor: str = "a") -> GameState:
    """仅布置公开场景；各测试动作都从实际菜单选择并执行。"""
    state = ready()
    state.ga_police.captain = captain
    state.ga_police.stance = "wall" if captain else "mobile"
    state.ga_police.wanted = ["a"]
    if not captain:
        state.ga_police.location = "shop"
    Police().sync(state)
    turn(state, actor)
    return state


def choose(
    state: GameState, operation: str, *, targets: tuple[str, ...] | None = None,
    **parameters: Any,
) -> tuple[Mapping[str, Any], ActionRequest]:
    """候选和观察来自同一正式槽，不能把当前不可用反制伪造为现成动作。"""
    options = build_options(state, state.current_grant, state.current_stage)
    request = next(option.request for option in options if
                   option.request.operation == operation and
                   (targets is None or option.request.target_ids == targets) and
                   all(option.request.parameters.get(key) == value for key, value in
                       parameters.items()))
    return observe(state, request.actor_id, options=options), request


def compare_bodies(projected: Mapping[str, Any], actual: Mapping[str, Any]) -> None:
    """逐项比较此Mind承诺的局部结果，SP注意力、终局、掉落不在它的模拟范围。"""
    for group in ("players", "units"):
        for key in actual[group]:
            for field in ("hp", "alive", "defenses", "defense_details", "burns", "statuses",
                          "inventory", "ammo", "hidden"):
                assert projected[group][key][field] == thaw_json(actual[group][key][field]), (
                    group, key, field, projected[group][key][field], actual[group][key][field])
    for field in ("captain", "guard_id", "stance", "wall", "attack_round", "immobile_round"):
        assert projected["police"][field] == actual["police"][field], field


def test_assessment_separates_live_count_shooters_allowance_and_menu() -> None:
    state = formation()
    officer = state.ga_units[state.ga_police.officer_ids[0]]
    put_status(officer, state.ga_config, 1, "SHOCKED", source_id="fixture")
    options = build_options(state, state.current_grant, state.current_stage)
    view = observe(state, "a", options=options)
    assessment = assess_police(view, "a", state.ga_config)
    assert assessment.available and assessment.remaining_volleys == 1
    assert assessment.captain == "b" and assessment.wanted and assessment.r4_target == "a"
    assert not assessment.officers[0].can_shoot and assessment.officers[0].alive
    assert sum(item.can_shoot for item in assessment.officers) == 2
    assert assessment.r4_attack.max_hp == 5  # 伤害按活编制3，不按实际射手2。
    assert all(view["candidates"][index]["request"]["operation"] == "npc_attack" for index in
               assessment.counter_indices)
    state.ga_police.attack_round = state.current_round
    assert assess_police(observe(state, "a"), "a", state.ga_config).remaining_volleys == 0


@pytest.mark.parametrize("hit", (True, False))
def test_npc_attack_preserves_discrete_roster_branches_and_is_pure(hit: bool) -> None:
    state = formation()
    officer = state.ga_police.officer_ids[0]
    state.ga_units[officer].hp = 3
    state.players["a"].location = "magic"
    view, action = choose(state, "npc_attack", targets=(officer,), weapon="bow")
    before = state.ga_revision, tuple(state.ga_rng.draws), thaw_json(view)
    result = estimate_counter(view, action, state.ga_config)
    assert before == (state.ga_revision, tuple(state.ga_rng.draws), thaw_json(view))
    assert result.available and len(result.branches) == 2
    assert result.direct_damage.score_gain == 0
    assert sorted(branch.projected["units"][officer]["hp"] for branch in result.branches) == [0, 2]
    state.ga_rng.filters.append(lambda value, kind, key, bound, actor: 0.0 if hit else 1.0)
    execute_action(state, validate_request(state, action))
    actual = observe(state, "a")
    branch = next(branch for branch in result.branches if
                  branch.projected["units"][officer]["hp"] == actual["units"][officer]["hp"])
    compare_bodies(branch.projected, actual)
    assert result.wall_removed == pytest.approx(result.direct_damage.kill_probability * 4)
    result.projected["players"]["a"]["ammo"]["bow"] = 0
    assert view["players"]["a"]["ammo"]["bow"] > 0


def test_last_officer_death_interrupts_current_and_renewed_volley() -> None:
    state = formation()
    for key in state.ga_police.officer_ids:
        state.ga_units[key].hp = 0
    officer = state.ga_police.officer_ids[0]
    state.ga_units[officer].hp = 1
    Police().sync(state)
    view, action = choose(state, "npc_attack", targets=(officer,), weapon="knife")
    result = estimate_counter(view, action, state.ga_config)
    execute_action(state, validate_request(state, action))
    compare_bodies(result.projected, observe(state, "a"))
    assert result.interrupted_volleys == 1
    assert result.avoided_volley_hp > 0
    assert result.renewed_volley_hp_reduction == result.avoided_volley_hp
    assert result.direct_damage.score_gain == 0


@pytest.mark.parametrize("kind", ("shock", "flash"))
@pytest.mark.parametrize("immune", (False, True))
def test_control_throw_drops_wall_without_stopping_officers(kind: str, immune: bool) -> None:
    state = formation()
    state.players["a"].inventory[kind] = 1
    if immune:
        state.players["b"].status_immunities = frozenset({
            "SHOCKED" if kind == "shock" else "FLASHED"})
    state.players["b"].hidden = True
    view, action = choose(state, "throw", kind=kind)
    result = estimate_counter(view, action, state.ga_config)
    execute_action(state, validate_request(state, action))
    compare_bodies(result.projected, observe(state, "a"))
    assert result.wall_removed == 12 and result.avoided_volley_hp == 0
    assert result.interrupted_volleys == result.renewed_volley_hp_reduction == 0
    assert all(not body["statuses"] and body["hp"] == 6 for body in
               result.projected["units"].values())
    assert result.timing["status_first_age_R4"] == state.current_round + 1


@pytest.mark.parametrize("cross", (False, True))
def test_smoke_only_changes_cross_location_hit_and_keeps_current_target(cross: bool) -> None:
    state = formation()
    if cross:
        state.players["a"].location = "magic"
    state.players["a"].inventory["smoke"] = 1
    view, action = choose(state, "throw", kind="smoke")
    result = estimate_counter(view, action, state.ga_config)
    execute_action(state, validate_request(state, action))
    actual = observe(state, "a")
    compare_bodies(result.projected, actual)
    assert result.projected["world"]["smoke"] == thaw_json(actual["world"]["smoke"])
    old_target, _, before = estimate_police_response(view, state.ga_config)
    new_target, _, after = estimate_police_response(actual, state.ga_config)
    assert old_target == new_target == "a" and after.available
    assert result.avoided_volley_hp == pytest.approx(before.expected_hp - after.expected_hp)
    assert (result.avoided_volley_hp > 0) == cross
    assert result.interrupted_volleys == 0
    assert result.timing["smoke_expiry_after_R4"] == state.current_round + 1


@pytest.mark.parametrize("kind", ("fragment", "molotov"))
def test_area_throw_matches_npc_and_player_damage_without_npc_score(kind: str) -> None:
    state = formation()
    state.players["a"].inventory[kind] = 2
    view, action = choose(state, "throw", kind=kind)
    result = estimate_counter(view, action, state.ga_config)
    conditional = estimate_area_counter(view, "a", kind, state.ga_config)
    assert conditional.available and "conditional_future_counter" in conditional.notes
    assert conditional.direct_damage == result.direct_damage
    execute_action(state, validate_request(state, action))
    actual = observe(state, "a")
    compare_bodies(result.projected, actual)
    assert result.projected["players"]["a"]["score"] == thaw_json(actual["players"]["a"]["score"])
    assert result.direct_damage.score_gain == (2 if kind == "fragment" else 0)
    assert result.projected["players"]["a"]["inventory"][kind] == 1
    if kind == "molotov":
        assert result.timing["burn_first_R4"] == state.current_round + 1
        assert all(len(body["burns"]) == 1 for body in result.projected["units"].values())


def test_two_conditional_grenades_consume_stock_rebuild_alive_targets_and_clear_roster() -> None:
    state = formation(captain=None)
    state.players["b"].location = state.players["c"].location = "magic"
    state.players["a"].inventory["fragment"] = 2
    initial = observe(state, "a")
    first = estimate_area_counter(initial, "a", "fragment", state.ga_config)
    assert first.direct_damage.expected_hp == 12 and first.direct_damage.score_gain == 0
    assert all(npc["hp"] == 2 for npc in first.projected["units"].values())
    second = estimate_area_counter(first.projected, "a", "fragment", state.ga_config)
    assert second.direct_damage.expected_hp == 6 and second.direct_damage.score_gain == 0
    assert second.projected["players"]["a"]["inventory"]["fragment"] == 0
    assert all(not npc["alive"] for npc in second.projected["units"].values())
    assert second.interrupted_volleys == 1 and second.projected["police"]["wall"] == 0
    assert not estimate_area_counter(second.projected, "a", "fragment", state.ga_config).available
    for _ in range(2):
        turn(state, "a")
        _, action = choose(state, "throw", kind="fragment")
        execute_action(state, validate_request(state, action))
    compare_bodies(second.projected, observe(state, "a"))
    assert initial["players"]["a"]["inventory"]["fragment"] == 2


@pytest.mark.parametrize("stance", ("wall", "mobile"))
def test_stance_preserves_damaged_wall_and_immobility_without_spending_volley(stance: str) -> None:
    state = formation(captain="a")
    state.ga_police.stance = "mobile" if stance == "wall" else "wall"
    state.ga_police.wall = 5
    state.ga_police.immobile_round = state.current_round
    Police().sync(state)
    turn(state, "a", TurnStage.T0)
    view, action = choose(state, "police_stance", stance=stance)
    result = estimate_counter(view, action, state.ga_config)
    execute_action(state, validate_request(state, action))
    compare_bodies(result.projected, observe(state, "a"))
    assert result.projected["police"]["wall"] == 5
    assert result.own_wall_removed == (5 if stance == "mobile" else -5)
    assert result.projected["police"]["attack_round"] != state.current_round


@pytest.mark.parametrize("spent", (False, True))
def test_innocent_command_backlash_applies_even_after_volley_spent(spent: bool) -> None:
    state = formation(captain="a")
    state.ga_police.attack_round = state.current_round if spent else 0
    view, action = choose(state, "police_command", targets=("b",))
    result = estimate_counter(view, action, state.ga_config)
    execute_action(state, validate_request(state, action))
    compare_bodies(result.projected, observe(state, "a"))
    assert sum(npc["alive"] for npc in result.projected["units"].values()) == 2
    assert result.projected["police"]["attack_round"] == view["police"]["attack_round"]
    assert not result.direct_damage.available


@pytest.mark.parametrize("controlled", (False, True))
def test_command_uses_shared_quota_and_consumes_officer_control(controlled: bool) -> None:
    state = formation(captain="b", actor="b")
    if controlled:
        for npc in state.ga_units.values():
            put_status(npc, state.ga_config, 1, "SHOCKED", source_id="fixture")
    view, action = choose(state, "police_command", targets=("a",))
    result = estimate_counter(view, action, state.ga_config)
    state.ga_rng.filters.append(lambda value, kind, key, bound, actor: 0.0)
    execute_action(state, validate_request(state, action))
    actual = observe(state, "b")
    branch = min(result.branches, key=lambda branch: branch.projected["players"]["a"]["hp"])
    compare_bodies(branch.projected, actual)
    assert result.projected["police"]["attack_round"] == state.current_round
    assert not any(npc["statuses"] for npc in result.projected["units"].values())
    assert result.direct_damage.available != controlled
    assert not estimate_police_response(result.projected, state.ga_config)[2].available


def test_noncurrent_request_is_rejected_and_future_scope_checks_real_resources() -> None:
    state = formation()
    state.players["a"].inventory["fragment"] = 1
    view, action = choose(state, "throw", kind="fragment")
    assert not estimate_counter(view, replace(action, root_id="invented"), state.ga_config).available
    state.players["a"].inventory["fragment"] = 0
    assert not estimate_area_counter(observe(state, "a"), "a", "fragment", state.ga_config).available
    assert not estimate_area_counter(view, "a", "flash", state.ga_config).available


@pytest.mark.parametrize("only_npc", (False, True))
def test_earthquake_projects_actual_local_area_npcs_and_wall(only_npc: bool) -> None:
    state = formation(captain=None if only_npc else "b")
    state.players["a"].weapons.add("earthquake")
    if only_npc:
        state.players["b"].location = state.players["c"].location = "magic"
    for npc in state.ga_units.values():
        npc.hp = 2
    view, action = choose(state, "attack", weapon="earthquake")
    assert set(state.ga_police.officer_ids) <= set(action.target_ids)
    result = estimate_counter(view, action, state.ga_config)
    assert result.available
    state.ga_rng.filters.append(lambda value, kind, key, bound, actor: 0.0)
    execute_action(state, validate_request(state, action))
    branch = min(result.branches, key=lambda branch: sum(
        body["hp"] for group in ("players", "units") for body in branch.projected[group].values()))
    compare_bodies(branch.projected, observe(state, "a"))
    assert all(not npc["alive"] for npc in branch.projected["units"].values())


def test_captain_earthquake_includes_single_mobile_coordination_without_extra_R4() -> None:
    state = formation(captain="a")
    state.ga_police.wanted = ["b"]
    state.ga_police.stance = "mobile"
    state.players["a"].weapons.add("earthquake")
    Police().sync(state)
    view, action = choose(state, "attack", weapon="earthquake")
    result = estimate_counter(view, action, state.ga_config)
    state.ga_rng.filters.append(lambda value, kind, key, bound, actor: 0.0)
    execute_action(state, validate_request(state, action))
    branch = min(result.branches, key=lambda branch: branch.projected["players"]["b"]["hp"])
    compare_bodies(branch.projected, observe(state, "a"))
    assert "includes_mobile_police_coordination" in result.direct_damage.notes
    assert not estimate_police_response(branch.projected, state.ga_config)[2].available
