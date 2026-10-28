"""I4.1 警队反制的真实费用与时序；路线夹具不冒称 BasicAI 已学会这些策略。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from engine.game_state import GameState
from engine.ga.ai.forecast import estimate_option, estimate_police_response, estimate_police_volley
from engine.ga.contracts import ActionRequest, TurnStage
from engine.ga.controllers import MinimalController
from engine.ga.gameplay import build_options, execute_action, legal_actions, validate_request
from engine.ga.police import Police
from engine.ga.runtime import _r4, run_round
from engine.ga.state import observe
from tests.ga_i2_helpers import execute, ready, turn
from tools.ga_ai_arena import ArenaBaseline


class GrenadeRoute(MinimalController):
    """显式六轮采购反制剧本；对手独立决策，不送装备、钱或额外行动。"""

    def __init__(self, *, finish: bool) -> None:
        self.finish = finish

    def choose(
        self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...], stage: TurnStage,
    ) -> ActionRequest | None:
        if stage is TurnStage.T0:
            return next((a for a in actions if a.operation == "wake" and
                         a.parameters["choice"] == "shield"), None)
        steps = {
            1: ("move", {"location": "military"}),
            2: ("buy_item", {"kind": "fragment"}),
            3: ("buy_item", {"kind": "fragment"}),
            4: ("move", {"location": "police"}),
            5: ("throw", {"kind": "fragment"}),
            6: ("throw", {"kind": "fragment"}) if self.finish else ("forfeit", {}),
        }
        operation, parameters = steps[view["round"]]
        return next(a for a in actions if a.operation == operation and all(
            a.parameters.get(key) == value for key, value in parameters.items()))


class Passive(MinimalController):
    """仅用于隔离未来 R4 的计时，不向状态注入治疗或额外资源。"""

    def choose(
        self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...], stage: TurnStage,
    ) -> ActionRequest | None:
        if stage is TurnStage.T0:
            return None
        return next(a for a in actions if a.operation == "forfeit")


def bought(kind: str) -> GameState:
    """机制夹具：用初始4钱实际采购一枚道具，到只有警员驻守的警局。"""
    state = ready()
    turn(state, "a")
    execute(state, "move", location="military")
    turn(state, "a")
    execute(state, "buy_item", kind=kind)
    assert state.players["a"].credits == 2
    assert state.players["a"].ammo["bow"] == 5
    turn(state, "a")
    execute(state, "move", location="police")
    return state


def formation(kind: str, *, captain: bool = True, report: bool = True) -> GameState:
    """机制夹具显式构造授予；举报用真实受害案件，绝不手填通缉与警员血量。"""
    state = bought(kind)
    turn(state, "b")
    execute(state, "move", location="police")
    if captain:
        turn(state, "b")
        execute(state, "police_apply")
        Police().r2(state)
        assert state.ga_police.captain == "b"
    if report:
        turn(state, "a")
        execute(state, "attack", targets=("b",), weapon="fist")
        turn(state, "b")
        execute(state, "police_report", targets=("a",))
        assert state.ga_police.wanted == ["a"]
    return state


def test_two_fragments_reach_and_remove_independent_police_from_standard_start() -> None:
    """固定反例证明4钱反制路线可达；只改最后一投，比较实际避免的R4伤害。"""
    outcomes = {}
    for finish in (False, True):
        state = GameState.new_ga(["a", "b"], seed=0)
        controllers = {"a": GrenadeRoute(finish=finish),
                       "b": ArenaBaseline(state.ga_config, "police")}
        for index in range(6):
            run_round(state, controllers)
            if index == 2:
                assert state.ga_police.captain == "b"
                assert state.players["a"].credits == 0
                assert state.players["a"].inventory["fragment"] == 2
                assert state.ga_crimes and not state.ga_police.wanted
            if index == 4:
                assert [p.hp for p in state.ga_units.values()] == [2, 2, 2]
        outcomes[finish] = state
        assert state.players["a"].alive and state.players["a"].ammo["bow"] == 5
        assert state.ga_police.wanted == ["a"]
    countered, uncompleted = outcomes[True], outcomes[False]
    assert not Police().live(countered) and len(Police().live(uncompleted)) == 3
    assert countered.players["a"].hp == 11 > uncompleted.players["a"].hp == 8
    assert not any(t["label"] == "police_target" for t in countered.ga_trace)
    assert len([t for t in uncompleted.ga_trace if t["label"] == "police_target"]) == 1
    assert countered.players["a"].kills == 0  # 三个警员不算三个人头。


@pytest.mark.parametrize("kind,damage", [("fragment", 4), ("molotov", 1)])
def test_bought_area_throw_hits_only_npcs_once_without_player_score(kind: str, damage: int) -> None:
    state = bought(kind)
    turn(state, "a")
    options = build_options(state, state.current_grant, TurnStage.T1)
    option = next(o for o in options if o.request.operation == "throw" and
                  o.request.parameters["kind"] == kind)
    officers = set(state.ga_police.officer_ids)
    assert set(option.request.target_ids) == officers
    assert len(option.effects) == 1 and set(option.effects[0].target_ids) == officers
    view = observe(state, "a", options=options)
    estimate = estimate_option(view, view["candidates"][options.index(option)], state.ga_config)
    assert estimate.available and estimate.expected_hp == 3 * damage and estimate.score_gain == 0
    plan = validate_request(state, option.request)
    assert plan.accepted
    receipt = execute_action(state, plan)
    assert len(receipt.batch_ids) == 1
    assert [p.hp for p in state.ga_units.values()] == [6 - damage] * 3
    assert state.players["a"].inventory[kind] == 0
    assert state.players["a"].hp_damage == state.players["a"].kills == 0
    assert execute_action(state, plan) is receipt
    assert [p.hp for p in state.ga_units.values()] == [6 - damage] * 3


def test_molotov_burns_officers_only_on_two_future_r4s() -> None:
    state = bought("molotov")
    turn(state, "a")
    execute(state, "throw", kind="molotov")
    assert all(p.hp == 5 and len(p.burns) == 1 for p in state.ga_units.values())
    _r4(state)
    assert all(p.hp == 5 for p in state.ga_units.values())
    for hp in (4, 3):
        run_round(state, {actor: Passive() for actor in state.players})
        assert all(p.hp == hp for p in state.ga_units.values())
    assert all(not p.burns for p in state.ga_units.values())
    assert state.players["a"].hp_damage == state.players["a"].kills == 0


@pytest.mark.parametrize("kind", ["shock", "flash"])
def test_zero_damage_disruption_removes_wall_but_does_not_disable_officers(kind: str) -> None:
    state = formation(kind)
    turn(state, "b", TurnStage.T0)
    execute(state, "police_stance", stance="wall")
    hp, wall = state.players["b"].hp, state.ga_police.wall
    assert "police/wall" in state.players["b"].defenses
    turn(state, "a")
    execute(state, "throw", kind=kind)
    assert state.players["a"].credits == 2 and state.players["a"].inventory[kind] == 0
    assert state.players["b"].hp == hp and state.ga_police.wall == wall
    assert state.ga_police.stance == "mobile" and "police/wall" not in state.players["b"].defenses
    assert all(p.hp == 6 and not p.statuses for p in state.ga_units.values())
    assert estimate_police_volley(observe(state, "a"), "a", state.ga_config).available
    before = state.players["a"].hp
    Police().r4(state)
    assert state.players["a"].hp < before  # 拆墙不能被规划器当成整队停火。


@pytest.mark.parametrize("cross_location", [False, True])
def test_smoke_changes_only_cross_location_police_hit_probability(
    cross_location: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = formation("smoke")
    if cross_location:
        turn(state, "a")
        execute(state, "move", location="military")
    before = estimate_police_volley(observe(state, "a"), "a", state.ga_config)
    turn(state, "a")
    execute(state, "throw", kind="smoke")
    after = estimate_police_volley(observe(state, "a"), "a", state.ga_config)
    penalty = state.ga_config.get("consumables", "smoke_hit_penalty") if cross_location else 0
    assert after.hit_probability == pytest.approx(before.hit_probability - penalty)
    assert after.available and after.min_hp > 0  # 烟雾不是闪避至零伤，也不会令目标免疫。
    probabilities = []
    original = state.ga_rng.chance

    def chance(key: str, probability: float, *, actor_id: str | None = None) -> bool:
        probabilities.append(probability)
        return original(key, probability, actor_id=actor_id)

    monkeypatch.setattr(state.ga_rng, "chance", chance)
    Police().r4(state)
    assert probabilities == [after.hit_probability]


@pytest.mark.parametrize("stance,immediate", [("mobile", 1), ("wall", 0)])
def test_captain_attack_and_r4_consume_one_shared_volley(stance: str, immediate: int) -> None:
    state = formation("fragment")
    if stance == "wall":
        turn(state, "b", TurnStage.T0)
        execute(state, "police_stance", stance="wall")
    turn(state, "b")
    execute(state, "attack", targets=("a",), weapon="knife")
    assert len([t for t in state.ga_trace if t["label"] == "police_target"]) == immediate
    Police().r4(state)
    hp = state.players["a"].hp
    assert len([t for t in state.ga_trace if t["label"] == "police_target"]) == 1
    assert not estimate_police_response(observe(state, "a"), state.ga_config)[2].available
    Police().r4(state)
    assert state.players["a"].hp == hp


@pytest.mark.parametrize("captain", [False, True])
def test_reported_civilian_faces_police_even_without_captain(captain: bool) -> None:
    state = formation("fragment", captain=captain)
    target, owner, estimate = estimate_police_response(observe(state, "a"), state.ga_config)
    assert target == "a" and owner == ("b" if captain else None) and estimate.available
    before = state.players["a"].hp
    Police().r4(state)
    assert state.players["a"].hp < before


@pytest.mark.parametrize("reported,stopped", [(False, False), (True, True)])
def test_unreported_purchase_or_stopped_police_is_not_an_immediate_volley(
    reported: bool, stopped: bool,
) -> None:
    state = formation("smoke", report=reported)
    assert state.ga_crimes and state.current_round == 1
    if stopped:
        state.ga_police.stopped.add("explicit_test_boundary")
        Police().sync(state)
    estimate = estimate_police_response(observe(state, "a"), state.ga_config)[2]
    assert not estimate.available
    before = state.players["a"].hp
    Police().r4(state)
    assert state.players["a"].hp == before


def test_two_real_knife_hits_remove_one_officer_and_reduce_wall_and_future_volley() -> None:
    state = formation("fragment")
    turn(state, "b", TurnStage.T0)
    execute(state, "police_stance", stance="wall")
    prior = estimate_police_volley(observe(state, "a"), "a", state.ga_config)
    target = state.ga_police.officer_ids[0]
    for _ in range(2):
        turn(state, "a")
        execute(state, "npc_attack", targets=(target,), weapon="knife")
    after = estimate_police_volley(observe(state, "a"), "a", state.ga_config)
    assert len(Police().live(state)) == 2 and state.ga_police.wall == 8
    assert after.max_hp == prior.max_hp - 1
    assert state.players["a"].kills == 0 and state.players["a"].ammo["bow"] == 5


def learned_earthquake() -> GameState:
    """按正式零价学习入口获得地震，保留初始钱与箭；没有手填武器。"""
    state = ready()
    turn(state, "a")
    execute(state, "move", location="magic")
    turn(state, "a")
    execute(state, "buy_weapon", weapon="earthquake")
    assert state.players["a"].credits == 4 and state.players["a"].ammo["bow"] == 5
    return state


def test_earthquake_covers_only_local_npcs_including_hidden_without_duplicate_action() -> None:
    state = learned_earthquake()
    turn(state, "a")
    execute(state, "move", location="police")
    state.ga_units[state.ga_police.officer_ids[0]].hidden = True
    turn(state, "a")
    options = build_options(state, state.current_grant, TurnStage.T1)
    earthquakes = [o for o in options if o.request.parameters.get("weapon") == "earthquake"]
    assert len(earthquakes) == 1
    assert set(earthquakes[0].request.target_ids) == set(state.ga_police.officer_ids)
    view = observe(state, "a", options=options)
    actions = tuple(option.request for option in options)
    assert ArenaBaseline(state.ga_config, "greedy").choose(view, actions, TurnStage.T1) in actions
    execute(state, "attack", weapon="earthquake")
    assert all(p.hp == 4 for p in state.ga_units.values())
    assert all(p.hp == 20 for p in state.players.values())
    assert state.players["a"].hp_damage == 0


@pytest.mark.parametrize("boundary", ["other_location", "other_isolation", "own_officers"])
def test_earthquake_cannot_invent_aoe_targets_across_scope_or_ownership(boundary: str) -> None:
    state = learned_earthquake()
    if boundary != "other_location":
        turn(state, "a")
        execute(state, "move", location="police")
        if boundary == "other_isolation":
            for npc in state.ga_units.values():
                npc.isolation_id = "explicit_other_domain"
        else:
            turn(state, "a")
            execute(state, "police_apply")
            Police().r2(state)
            assert state.ga_police.captain == "a"
    turn(state, "a")
    assert not any(a.parameters.get("weapon") == "earthquake" for a in legal_actions(state))


def test_earthquake_mixed_captain_and_npcs_are_each_hit_once_through_wall() -> None:
    state = learned_earthquake()
    for actor in ("a", "b"):
        turn(state, actor)
        execute(state, "move", location="police")
    turn(state, "b")
    execute(state, "police_apply")
    Police().r2(state)
    turn(state, "b", TurnStage.T0)
    execute(state, "police_stance", stance="wall")
    turn(state, "a")
    actions = [a for a in legal_actions(state) if a.parameters.get("weapon") == "earthquake"]
    assert len(actions) == 1
    assert set(actions[0].target_ids) == {"b", *state.ga_police.officer_ids}
    execute(state, "attack", weapon="earthquake")
    assert state.players["b"].hp == 18 and all(p.hp == 4 for p in state.ga_units.values())
    assert state.ga_police.wall == 12 and state.players["a"].hp_damage == 2
