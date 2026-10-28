"""GA-001/002/007及N0地点经济：从合法候选到真实状态变化。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from engine.game_state import GameState
from engine.ga.actions import GrantKind
from engine.ga.contracts import (
    ContractError, DamageKind, DefenseKind, DefenseSpec, Performance, RoundPhase,
    TargetScope, TurnStage,
)
from engine.ga.events import EventKind
from engine.ga.gameplay import execute_action, legal_actions, validate_request
from engine.ga.state import ActionGrant, add_armor, next_id, observe, touch


def setup(*, prepared: bool = True, seed: int = 0) -> GameState:
    """正式GA状态；prepared只用于指定场景的前置状态。"""
    state = GameState.new_ga(["a", "b", "c"], seed=seed)
    state.current_round = state.world_tick = 1
    state.current_phase = RoundPhase.R3
    if prepared:
        for player in state.players.values():
            player.awake = player.home_locked = True
            player.location = "shop"
            player.weapons.update({"knife", "bow"})
            player.ammo["bow"] = 5
    turn(state)
    return state


def turn(state: GameState, actor: str = "a", stage: TurnStage = TurnStage.T1) -> None:
    """为定向夹具建立一个新槽，阶段变化使旧计划失效。"""
    state.current_grant = ActionGrant(next_id(state, "test-grant"), actor, GrantKind.STANDARD)
    state.current_stage = stage
    touch(state)


def select(state: GameState, operation: str, **parameters: Any) -> Any:
    """只选择生产枚举器提供的候选。"""
    matches = [a for a in legal_actions(state) if a.operation == operation and
               all(a.parameters.get(key) == value for key, value in parameters.items())]
    assert matches, (operation, parameters, legal_actions(state))
    return matches[0]


def perform(state: GameState, operation: str, **parameters: Any) -> Any:
    """生产预检与执行的唯一入口。"""
    return execute_action(state, validate_request(state, select(state, operation, **parameters)))


def test_ga001_start_pack_first_exit_and_no_return_home() -> None:
    state = setup(prepared=False)
    player = state.players["a"]
    assert (player.hp, player.credits, player.sp, player.pp) == (20, 4, 1, 0)
    assert player.location == "home:a" and not player.defenses
    state.current_stage = TurnStage.T0
    perform(state, "wake", choice="shield")
    assert player.weapons == {"fist", "bow"} and player.ammo["bow"] == 5
    assert next(iter(player.defenses.values())).durability == 6
    state.current_stage = TurnStage.T1
    actions = legal_actions(state)
    assert len(actions) == 5 and all(a.operation == "move" for a in actions)
    perform(state, "move", location="hospital")
    turn(state)
    assert all(a.parameters.get("location") != "home:a" for a in legal_actions(state))
    assert player.home_locked


def test_ga002_exposed_same_location_attacks_without_search() -> None:
    state = setup()
    actions = legal_actions(state)
    assert any(a.operation == "attack" and a.parameters["weapon"] == "knife" for a in actions)
    assert not any(a.operation in ("search", "find", "lock") for a in actions)
    perform(state, "attack", weapon="knife")
    assert state.players["b"].hp == 16


def test_t0_lock_syringe_and_sharpen_share_one_quota() -> None:
    state = setup()
    player = state.players["a"]
    player.inventory.update(syringe=1, whetstone=1)
    player.hp = 10
    turn(state, stage=TurnStage.T0)
    assert {a.operation for a in legal_actions(state)} >= {"lock", "syringe", "sharpen"}
    perform(state, "syringe")
    assert player.hp == 14 and not legal_actions(state)
    state.current_stage = TurnStage.T1
    assert any(a.operation == "attack" for a in legal_actions(state))


def test_cross_location_requires_ranged_and_lock_applies_coefficient() -> None:
    state = setup(seed=2)
    state.players["b"].location = "magic"
    state.players["b"].moved = True
    key = add_armor(state, "b", "ceramic")
    turn(state, stage=TurnStage.T0)
    locked = next(a for a in legal_actions(state) if a.operation == "lock" and
                  a.target_ids == ("b",))
    execute_action(state, validate_request(state, locked))
    state.current_stage = TurnStage.T1
    attacks = [a for a in legal_actions(state) if a.operation == "attack" and
               a.target_ids == ("b",)]
    assert all(a.parameters["weapon"] == "bow" for a in attacks)
    execute_action(state, validate_request(state, attacks[0]))
    assert state.players["b"].hp == 18
    assert state.players["b"].defenses[key].durability == 8


def test_hidden_target_qualification_requires_search_or_matching_detection() -> None:
    state = setup()
    state.players["b"].hidden = True
    assert not any(a.operation == "attack" and a.target_ids == ("b",) for a in legal_actions(state))
    search = next(a for a in legal_actions(state) if a.operation == "search")
    execute_action(state, validate_request(state, search))
    turn(state)
    assert any(a.operation == "attack" and a.target_ids == ("b",) for a in legal_actions(state))
    state.players["b"].revealed_to.clear()
    state.players["a"].inventory["thermal"] = 1
    assert any(a.operation == "attack" and a.target_ids == ("b",) for a in legal_actions(state))


def test_ga007_invalid_or_unaffordable_plan_does_not_pay_or_draw() -> None:
    state = setup()
    request = select(state, "buy_armor", kind="ceramic")
    state.players["a"].credits = 0
    before = (state.ga_revision, tuple(state.ga_rng.draws), len(state.ga_ledger.events))
    plan = validate_request(state, request)
    assert not plan.accepted and not plan.costs
    with pytest.raises(ContractError):
        execute_action(state, plan)
    assert (state.ga_revision, tuple(state.ga_rng.draws), len(state.ga_ledger.events)) == before
    assert not state.current_grant.occupied


def test_stale_plan_cannot_pay_and_forged_performance_is_not_a_skill() -> None:
    state = setup()
    request = select(state, "attack", weapon="bow")
    plan = validate_request(state, request)
    touch(state)
    with pytest.raises(ContractError, match="snapshot"):
        execute_action(state, plan)
    assert state.players["a"].ammo["bow"] == 5
    assert not validate_request(state, replace(request, performance=Performance.PUBLIC)).accepted


def test_blocked_attack_still_pays_ammo_once_replay_is_idempotent() -> None:
    state = setup()
    state.players["b"].defenses["wall"] = DefenseSpec(
        "wall", DefenseKind.FINITE_BARRIER, 10, 10, frozenset({DamageKind.NORMAL}),
        frozenset(TargetScope),
    )
    request = select(state, "attack", weapon="bow")
    plan = validate_request(state, request)
    receipt = execute_action(state, plan)
    events = state.ga_ledger.events
    assert state.players["a"].ammo["bow"] == 4 and state.players["b"].hp == 20
    assert execute_action(state, plan) is receipt
    assert state.ga_ledger.events == events and state.players["a"].ammo["bow"] == 4


@pytest.mark.parametrize("location", ["magic", "hospital", "military", "police"])
def test_shop_is_only_work_entry(location: str) -> None:
    state = setup()
    state.players["a"].location = location
    assert not any(a.operation == "work" for a in legal_actions(state))


def test_work_changes_only_money_no_sp_or_pp() -> None:
    state = setup()
    perform(state, "work")
    player = state.players["a"]
    assert (player.credits, player.sp, player.pp) == (6, 1, 0)


def test_broken_armor_cannot_be_repaired() -> None:
    state = setup()
    key = add_armor(state, "a", "ceramic")
    player = state.players["a"]
    player.defenses[key] = replace(player.defenses[key], durability=3)
    perform(state, "repair", defense_id=key)
    assert player.defenses[key].durability == 7 and player.credits == 2
    turn(state)
    player.defenses[key] = replace(player.defenses[key], durability=0)
    assert not any(a.operation == "repair" for a in legal_actions(state))


def test_heal_is_limited_and_clears_burn_without_inner_armor() -> None:
    state = setup()
    player = state.players["a"]
    player.location = "hospital"
    player.credits = 20
    for index in range(3):
        player.hp = 10
        turn(state)
        perform(state, "heal")
        assert player.hp == 14
    turn(state)
    player.hp = 1
    assert not any(a.operation == "heal" for a in legal_actions(state))
    assert not player.defenses


def test_syringe_hold_and_lifetime_purchase_caps() -> None:
    state = setup()
    player = state.players["a"]
    player.location = "hospital"
    player.credits = 20
    for index in range(2):
        turn(state)
        perform(state, "buy_item", kind="syringe")
        turn(state)
        assert not any(a.operation == "buy_item" and a.parameters.get("kind") == "syringe"
                       for a in legal_actions(state))
        turn(state, stage=TurnStage.T0)
        player.hp = 10
        perform(state, "syringe")
        assert player.hp == 14
    turn(state)
    assert not any(a.operation == "buy_item" and a.parameters.get("kind") == "syringe"
                   for a in legal_actions(state))


@pytest.mark.parametrize("kind", ["life", "overclock", "furnace"])
def test_surgery_clears_whole_wallet_and_is_once(kind: str) -> None:
    state = setup()
    player = state.players["a"]
    player.location = "hospital"
    player.credits = 9
    perform(state, "surgery", kind=kind)
    assert player.credits == 0 and player.surgery == kind
    if kind == "life":
        assert player.hp == player.max_hp == 28
    turn(state)
    player.credits = 9
    assert not any(a.operation == "surgery" for a in legal_actions(state))


def test_furnace_cost_preserves_life_and_two_game_uses() -> None:
    state = setup()
    player = state.players["a"]
    player.surgery = "furnace"
    player.hp = 7
    for index in range(2):
        player.sp = 0
        turn(state, stage=TurnStage.T0)
        perform(state, "furnace", mode="sp")
    assert player.hp == 1 and player.furnace_uses == 2
    turn(state, stage=TurnStage.T0)
    assert not any(a.operation == "furnace" for a in legal_actions(state))


def test_module_stock_physical_items_and_installation_conserve() -> None:
    state = setup()
    state.players["a"].credits = 20
    initial = state.module_stock["power"]
    perform(state, "buy_module", kind="power")
    turn(state)
    perform(state, "buy_module", kind="power")
    turn(state)
    assert state.module_stock["power"] == initial - 2
    assert not any(a.operation == "buy_module" and a.parameters["kind"] == "power"
                   for a in legal_actions(state))
    turn(state, stage=TurnStage.T0)
    perform(state, "modules", modules=("power", "power"))
    assert state.players["a"].inventory["module:power"] == 0
    state.current_stage = TurnStage.T1
    perform(state, "attack", weapon="bow")
    assert state.players["b"].hp == 14
    turn(state, stage=TurnStage.T0)
    perform(state, "modules", modules=())
    assert state.players["a"].inventory["module:power"] == 2
    assert state.module_stock["power"] == initial - 2


def test_infinite_module_uses_both_slots_and_no_ammo() -> None:
    state = setup()
    player = state.players["a"]
    player.inventory.update({"module:infinite": 1, "module:power": 1})
    player.ammo["bow"] = 0
    turn(state, stage=TurnStage.T0)
    assert not any(a.operation == "modules" and "infinite" in a.parameters["modules"] and
                   len(a.parameters["modules"]) > 1 for a in legal_actions(state))
    perform(state, "modules", modules=("infinite",))
    state.current_stage = TurnStage.T1
    perform(state, "attack", weapon="bow")
    assert player.ammo["bow"] == 0 and state.players["b"].hp == 16


def test_military_direct_purchase_records_case_and_pass_is_one_use() -> None:
    state = setup()
    player = state.players["a"]
    player.location = "military"
    perform(state, "buy_weapon", weapon="electromagnetic_rifle")
    assert player.ammo["electromagnetic_rifle"] == 3 and len(state.ga_crimes) == 1
    player.credits = 10
    turn(state)
    perform(state, "buy_item", kind="black_market_pass")
    turn(state)
    perform(state, "buy_armor", kind="at_field")
    assert player.inventory["black_market_pass"] == 0 and len(state.ga_crimes) == 1
    turn(state)
    perform(state, "buy_item", kind="radar")
    assert len(state.ga_crimes) == 2


def test_gauss_charge_not_an_extra_shot_and_consumed_on_fire() -> None:
    state = setup()
    player = state.players["a"]
    player.weapons.add("gauss_rifle")
    player.ammo["gauss_rifle"] = 2
    perform(state, "charge")
    assert player.charged and player.ammo["gauss_rifle"] == 2
    turn(state)
    perform(state, "attack", weapon="gauss_rifle")
    assert state.players["b"].hp == 12 and not player.charged
    assert player.ammo["gauss_rifle"] == 1


def test_area_earthquake_certainly_hits_all_others_once() -> None:
    state = setup(seed=2)
    state.players["a"].weapons.add("earthquake")
    state.players["b"].moved = state.players["c"].hidden = True
    before = len(state.ga_rng.draws)
    perform(state, "attack", weapon="earthquake")
    assert [state.players[k].hp for k in ("a", "b", "c")] == [20, 18, 18]
    assert len(state.ga_rng.draws) == before


@pytest.mark.parametrize("kind", ["shock", "flash", "smoke", "fragment", "molotov"])
def test_tactical_consumables_have_real_inventory_and_effect(kind: str) -> None:
    state = setup()
    state.players["a"].inventory[kind] = 1
    perform(state, "throw", kind=kind)
    assert state.players["a"].inventory[kind] == 0
    target = state.players["b"]
    if kind == "fragment":
        assert target.hp == 16
    elif kind == "molotov":
        assert target.hp == 19 and len(target.burns) == 1
    elif kind == "shock":
        assert target.hp == 20 and target.statuses["control"].name == "SHOCKED"
    elif kind == "flash":
        assert target.hp == 20 and "FLASHED" in target.statuses
    else:
        assert "shop" in state.ga_smoke and target.hp == 20


def test_observation_is_recursive_read_only() -> None:
    state = setup()
    view = observe(state, "a")
    with pytest.raises(TypeError):
        view["players"]["a"]["hp"] = 0
    assert state.players["a"].hp == 20


def test_candidate_and_precheck_are_pure_and_declare_conditional_rng_budget() -> None:
    state = setup()
    before = (state.ga_revision, state.ga_id_counter, tuple(state.ga_rng.draws),
              state.players["a"].ammo["bow"])
    first = legal_actions(state)
    second = legal_actions(state)
    assert first == second
    request = next(a for a in first if a.operation == "attack")
    plan = validate_request(state, request)
    assert plan.random_requirements[0].draws == 1
    assert (state.ga_revision, state.ga_id_counter, tuple(state.ga_rng.draws),
            state.players["a"].ammo["bow"]) == before


def test_ammo_refill_clamps_capacity_and_empty_weapon_is_not_legal() -> None:
    state = setup()
    player = state.players["a"]
    player.ammo["bow"] = 8
    perform(state, "refill", weapon="bow")
    assert player.ammo["bow"] == 10 and player.credits == 3
    turn(state)
    assert not any(a.operation == "refill" for a in legal_actions(state))
    player.ammo["bow"] = 0
    assert not any(a.operation == "attack" and a.parameters["weapon"] == "bow"
                   for a in legal_actions(state))


def test_second_game_has_independent_random_stream_and_module_stock() -> None:
    first, second = setup(), setup()
    perform(first, "buy_module", kind="power")
    assert first.module_stock["power"] + 1 == second.module_stock["power"]
    first.ga_rng.die("probe", 6)
    assert not second.ga_rng.draws


def test_search_cannot_repeat_noop_and_rehide_clears_old_discovery() -> None:
    state = setup()
    state.players["b"].hidden = True
    state.players["b"].inventory["cloak"] = 1
    perform(state, "search")
    turn(state)
    assert not any(a.operation == "search" for a in legal_actions(state))
    assert any(a.operation == "attack" and a.target_ids == ("b",) for a in legal_actions(state))
    turn(state, actor="b")
    perform(state, "hide")
    turn(state)
    assert not any(a.operation == "attack" and a.target_ids == ("b",) for a in legal_actions(state))
