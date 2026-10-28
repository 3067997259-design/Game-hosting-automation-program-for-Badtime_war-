"""慈爱的怪盗重写：正式候选、库存守恒、演出生效、反应和借用隔离。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from engine.game_state import GameState
from engine.ga.contracts import ContractError, DamageAttribute, Performance, TurnStage
from engine.ga.gameplay import (
    _visible, build_options, execute_action, legal_actions, validate_request,
)
from engine.ga.decisions import RecordingController, ReplayController
from engine.ga.possessions import transferable_items
from engine.ga.state import add_armor, touch
from engine.ga.status import STATUS_NAMES
from tests.ga_i2_helpers import Decisions, execute, ready, turn


@pytest.mark.parametrize("choice", ["knife", "shield"])
def test_first_wake_grants_extra_cloak_once_without_hiding(choice: str) -> None:
    state = GameState.new_ga(["a", "b"], talents={"a": "T2"}, seed=0)
    state.current_round = state.world_tick = 1
    turn(state, "a", TurnStage.T0)
    action = next(a for a in legal_actions(state) if a.parameters.get("choice") == choice)
    plan = validate_request(state, action)
    receipt = execute_action(state, plan)
    assert execute_action(state, plan) == receipt
    player = state.players["a"]
    assert player.inventory["cloak"] == 1 and not player.hidden
    assert "bow" in player.weapons and player.ammo["bow"] == 5
    assert ("knife" in player.weapons) == (choice == "knife")
    assert bool(player.defenses) == (choice == "shield")
    state.current_stage = TurnStage.T1
    execute(state, "move", location="shop")
    turn(state, "a")
    execute(state, "hide")
    assert player.hidden


@pytest.mark.parametrize("source", ["cloak", "coating", "stealth_spell"])
def test_stealth_source_does_not_have_an_affinity(source: str) -> None:
    state = ready()
    player = state.players["a"]
    if source == "stealth_spell":
        player.weapons.add(source)
    else:
        player.inventory[source] = 1
    turn(state, "a")
    execute(state, "hide")
    observer = state.players["b"]
    assert all(not _visible(observer, player, attr) for attr in (
        DamageAttribute.NORMAL, DamageAttribute.TECH, DamageAttribute.MAGIC))
    observer.inventory["thermal"] = 1
    assert _visible(observer, player, DamageAttribute.NORMAL)
    assert not _visible(observer, player, DamageAttribute.TECH)
    observer.inventory["radar"] = 1
    assert _visible(observer, player, DamageAttribute.TECH)
    assert not _visible(observer, player, DamageAttribute.MAGIC)
    observer.weapons.add("detect_magic")
    assert _visible(observer, player, DamageAttribute.MAGIC)


@pytest.mark.parametrize("location,purchase,parameter,item", [
    ("shop", "buy_item", "kind", "thermal"),
    ("shop", "buy_armor", "kind", "ceramic"),
    ("shop", "buy_module", "kind", "power"),
    ("magic", "learn", "spell", "stealth_spell"),
    ("magic", "buy_weapon", "weapon", "earthquake"),
    ("military", "buy_weapon", "weapon", "gauss_rifle"),
    ("military", "buy_item", "kind", "fragment"),
    ("military", "buy_armor", "kind", "at_field"),
    ("hospital", "buy_item", "kind", "syringe"),
])
def test_take_goods_pays_only_sp_and_does_not_reveal_create_crime_or_draw_attention(
    location: str, purchase: str, parameter: str, item: str,
) -> None:
    state = ready({"a": "T2"})
    player = state.players["a"]
    player.location = location
    player.credits = 0
    player.kills = 1
    player.retain_stealth_attacks = False
    player.hidden = True
    player.inventory["black_market_pass"] = 1
    turn(state, "a")
    receipt = execute(state, "t2_take", purchase=purchase, **{parameter: item})
    assert [(c.account, c.amount) for c in receipt.costs] == [("sp", 1)]
    # 拿东西不给关注，即演花掉的 1 SP 不会被补回来。
    assert player.sp == 0
    assert player.credits == 0 and player.hidden and not state.ga_crimes
    assert player.inventory["black_market_pass"] == 1
    if purchase == "buy_armor":
        assert item in player.armor_kinds.values()
    elif purchase == "buy_module":
        assert player.inventory[f"module:{item}"] == 1
    elif purchase in ("learn", "buy_weapon"):
        assert item in player.weapons
    else:
        assert player.inventory[item] == 1
    assert state.ga_roles["a"].performance_rounds == {1}


def test_take_excludes_services_ground_loot_and_foreign_goods() -> None:
    state = ready({"a": "T2"})
    player = state.players["a"]
    player.hp = 5
    player.ammo["bow"] = 0
    for location in ("shop", "magic", "military", "hospital", "police"):
        player.location = location
        turn(state, "a")
        actions = [a for a in legal_actions(state) if a.operation == "t2_take"]
        assert all(a.parameters["purchase"] in {
            "buy_armor", "buy_module", "buy_weapon", "buy_item", "learn"} for a in actions)
        assert not any(a.parameters.get("kind") == "fragment" for a in actions) == (
            location != "military")
        if location == "police":
            assert not actions


def test_take_respects_stock_ownership_capacity_and_game_purchase_limit() -> None:
    state = ready({"a": "T2"})
    player = state.players["a"]
    player.location = "military"
    player.weapons.add("gauss_rifle")
    player.inventory["fragment"] = state.ga_config.get("consumables", "hold_cap")
    state.module_stock["pierce"] = 0
    turn(state, "a")
    actions = [a for a in legal_actions(state) if a.operation == "t2_take"]
    assert not any(a.parameters.get("weapon") == "gauss_rifle" for a in actions)
    assert not any(a.parameters.get("kind") in ("fragment", "pierce") for a in actions)
    player.location = "hospital"
    player.syringe_purchases = state.ga_config.get("hospital", "syringe_purchase_cap")
    assert not any(a.operation == "t2_take" and a.parameters.get("kind") == "syringe"
                   for a in legal_actions(state))


def test_take_rechecks_stock_before_payment_and_replay_cannot_duplicate_goods() -> None:
    state = ready({"a": "T2"})
    turn(state, "a")
    request = next(a for a in legal_actions(state) if a.operation == "t2_take" and
                   a.parameters.get("kind") == "power")
    plan = validate_request(state, request)
    before = state.players["a"].sp, tuple(state.ga_rng.draws)
    stock = state.module_stock["power"]
    state.module_stock["power"] = 0
    touch(state)
    with pytest.raises(ContractError):
        execute_action(state, plan)
    assert (state.players["a"].sp, tuple(state.ga_rng.draws)) == before
    state.module_stock["power"] = stock
    touch(state)
    plan = validate_request(state, request)
    receipt = execute_action(state, plan)
    assert execute_action(state, plan) == receipt
    assert state.module_stock["power"] == stock - 1
    assert state.players["a"].inventory["module:power"] == 1


def test_fear_targets_hidden_local_players_but_never_police_or_isolated_players() -> None:
    state = ready({"a": "T2"}, count=4)
    for player in state.players.values():
        player.location = "police"
    state.players["a"].sp = 2
    state.players["b"].hidden = True
    state.players["c"].isolation_id = "other"
    turn(state, "a")
    receipt = execute(state, "t2_fear", mode=Performance.PUBLIC)
    assert receipt.request.target_ids == ("b", "d")
    assert "FEAR" in state.players["b"].statuses
    assert "FEAR" in state.players["d"].statuses
    assert "FEAR" not in state.players["c"].statuses
    assert all("FEAR" not in npc.statuses for npc in state.ga_units.values())
    assert all(player.hp == 20 for player in state.players.values())
    assert [(c.account, c.amount) for c in receipt.costs] == [("sp", 2)]
    assert not state.ga_crimes and state.ga_public_used


def test_fear_immune_cast_pays_but_does_not_count_toward_final_chapter() -> None:
    state = ready({"a": "T2"})
    for key in ("b", "c"):
        state.players[key].status_immunities = STATUS_NAMES
    state.players["a"].sp = 2
    turn(state, "a")
    receipt = execute(state, "t2_fear")
    assert receipt.costs[0].amount == 2
    assert not state.ga_roles["a"].performance_rounds
    assert not state.players["a"].chapters


def test_final_chapter_counts_mixed_effective_performances_in_different_rounds() -> None:
    state = ready({"a": "T2"})
    turn(state, "a")
    execute(state, "t2_take", purchase="buy_item", kind="thermal")
    state.players["a"].sp = 2
    turn(state, "a")
    execute(state, "t2_fear")
    assert state.ga_roles["a"].performance_rounds == {1}
    assert "chapter/3" not in state.players["a"].chapters
    state.current_round += 1
    state.players["a"].sp = 1
    turn(state, "a")
    execute(state, "t2_take", purchase="buy_armor", kind="shield")
    assert "chapter/3" in state.players["a"].chapters
    assert "chapter/1" not in state.players["a"].chapters


def test_shadow_lock_waits_for_selected_target_and_does_not_stack_with_normal_lock() -> None:
    state = ready({"a": "T2"})
    state.ga_controllers["a"] = Decisions(shadow_step=["lock"])
    turn(state, "b")
    execute(state, "move", location="magic")
    role = state.ga_roles["a"]
    assert role.shadow_lock == "b"
    turn(state, "a")
    execute(state, "attack", targets=("c",), weapon="bow")
    assert role.shadow_lock == "b"
    for already_locked in (False, True):
        turn(state, "a")
        if already_locked:
            state.current_grant.locked_target = "b"
        option = next(o for o in build_options(state, state.current_grant, TurnStage.T1) if
                      o.request.operation == "attack" and o.request.target_ids == ("b",) and
                      o.request.parameters["weapon"] == "bow")
        assert option.effects[0].parameters["hit_bonus"] == state.ga_config.get(
            "accuracy", "lock_bonus")
        assert option.effects[0].parameters["ignore_move_evasion"]
        assert option.effects[0].parameters["defense_coefficient"] == .75
    execute(state, "attack", targets=("b",), weapon="bow")
    assert role.shadow_lock is None


@pytest.mark.parametrize("category", ["armor", "weapon", "item", "module"])
def test_shadow_theft_transfers_one_real_item_and_does_not_add_turns(category: str) -> None:
    state = ready({"a": "T2"})
    donor, receiver = state.players["b"], state.players["a"]
    if category == "armor":
        key = add_armor(state, "b", "ceramic")
        donor.defenses[key] = replace(donor.defenses[key], durability=2)
        choice = f"armor:{key}"
    elif category == "weapon":
        donor.weapons.add("gauss_rifle")
        donor.ammo["gauss_rifle"] = 4
        choice = "weapon:gauss_rifle"
    elif category == "module":
        donor.modules = ("fire", "pierce")
        donor.bow_attribute = DamageAttribute.MAGIC
        choice = "module:fire"
    else:
        donor.inventory["fragment"] = 2
        choice = "item:fragment"
    receiver.hidden = True
    state.ga_controllers["a"] = Decisions(shadow_step=[f"steal:{choice}"])
    turn(state, "b")
    request = next(a for a in legal_actions(state) if a.operation == "move" and
                   a.parameters["location"] == "magic")
    plan = validate_request(state, request)
    receipt = execute_action(state, plan)
    assert execute_action(state, plan) == receipt
    assert receiver.location == donor.location == "magic"
    assert receiver.hidden and not state.ga_crimes
    assert state.ga_roles["a"].shadow_uses == 1 and state.ga_roles["a"].shadow_lock is None
    assert "chapter/2" in receiver.chapters
    assert not state.ga_pending_extras and not state.ga_pending_followups
    if category == "armor":
        assert key not in donor.defenses and receiver.defenses[key].durability == 2
        assert receiver.armor_kinds[key] == "ceramic"
    elif category == "weapon":
        assert "gauss_rifle" in receiver.weapons and "gauss_rifle" not in donor.weapons
        assert receiver.ammo["gauss_rifle"] == 4 and donor.ammo["gauss_rifle"] == 0
    elif category == "module":
        assert donor.modules == ("pierce",) and donor.bow_attribute is DamageAttribute.TECH
        assert receiver.inventory["module:fire"] == 1
    else:
        assert donor.inventory["fragment"] == receiver.inventory["fragment"] == 1


def test_stolen_gun_carries_ammo_up_to_capacity_and_ammo_alone_is_not_offered() -> None:
    state = ready({"a": "T2"})
    donor, receiver = state.players["b"], state.players["a"]
    cap = state.ga_config.get("weapons", "gauss_rifle", "ammo_cap")
    donor.weapons.add("gauss_rifle")
    donor.ammo["gauss_rifle"] = 4
    receiver.ammo["gauss_rifle"] = cap - 1
    items = transferable_items(state, "b", "a")
    assert "weapon:gauss_rifle" in items
    assert not any(item.startswith("ammo:") for item in items)
    state.ga_controllers["a"] = Decisions(shadow_step=["steal:weapon:gauss_rifle"])
    turn(state, "b")
    execute(state, "move", location="magic")
    assert "gauss_rifle" in receiver.weapons and receiver.ammo["gauss_rifle"] == cap
    assert donor.ammo["gauss_rifle"] == 3


def test_shadow_step_only_follows_players_it_can_see_and_lock_does_not_reveal() -> None:
    state = ready({"a": "T2"})
    role, follower, mover = state.ga_roles["a"], state.players["a"], state.players["b"]
    state.ga_controllers["a"] = Decisions(shadow_step=["lock"])
    mover.hidden = True
    turn(state, "b")
    execute(state, "move", location="magic")
    assert follower.location == "shop" and role.shadow_uses == 0 and role.shadow_lock is None
    mover.location = "shop"
    follower.inventory["thermal"] = 1
    touch(state)
    turn(state, "b")
    execute(state, "move", location="magic")
    assert follower.location == "magic" and role.shadow_uses == 1 and role.shadow_lock == "b"
    assert mover.hidden and "a" not in mover.revealed_to


def test_theft_excludes_talent_resources_spells_and_over_capacity_goods() -> None:
    state = ready({"a": "T2", "b": "G0"})
    donor, receiver = state.players["b"], state.players["a"]
    donor.weapons.update({"stealth_spell", "earthquake", "magic_barrage", "horus"})
    donor.inventory.update({"relic": 1, "fragment": 1})
    receiver.inventory["fragment"] = state.ga_config.get("consumables", "hold_cap")
    items = transferable_items(state, "b", "a")
    assert not any(item in items for item in (
        "weapon:fist", "weapon:bow", "weapon:stealth_spell", "weapon:earthquake",
        "weapon:magic_barrage", "weapon:horus", "item:relic", "item:fragment", "ammo:ar"))


def test_free_shadow_poem_can_pay_for_theft_and_decline_preserves_it() -> None:
    state = ready({"a": "T2"})
    role = state.ga_roles["a"]
    role.shadow_uses = 2
    role.receive_poem(state, "a")
    state.players["a"].sp = 0
    state.players["b"].inventory["thermal"] = 1
    state.ga_controllers["a"] = Decisions(shadow_step=["decline", "steal:item:thermal"])
    turn(state, "b")
    execute(state, "move", location="magic")
    assert role.free_shadow and role.shadow_uses == 0
    state.players["a"].location = "magic"
    turn(state, "b")
    execute(state, "move", location="hospital")
    assert not role.free_shadow and role.shadow_uses == 1
    assert state.players["a"].sp == 0 and state.players["a"].inventory["thermal"] == 1


@pytest.mark.parametrize("choice", ["lock", "steal:item:thermal"])
def test_nondefault_shadow_response_replays_exactly(choice: str) -> None:
    def world() -> GameState:
        """两份一致的局面，各自走正式跟随与选择窗口。"""
        state = ready({"a": "T2"})
        state.players["b"].inventory["thermal"] = 1
        return state

    first = world()
    recorder = RecordingController(Decisions(shadow_step=[choice]))
    first.ga_controllers["a"] = recorder
    turn(first, "b")
    execute(first, "move", location="magic")
    second = world()
    replay = ReplayController(recorder.replay_inputs())
    second.ga_controllers["a"] = replay
    turn(second, "b")
    execute(second, "move", location="magic")
    replay.assert_consumed()
    assert first.ga_ledger.events == second.ga_ledger.events
    assert first.ga_roles["a"].public() == second.ga_roles["a"].public()
    assert first.players == second.players


def test_retired_t2_damage_parameter_and_action_are_rejected() -> None:
    from engine.ga.config import GaConfigError, validate_ga_config

    state = ready({"a": "T2"})
    raw = state.ga_config.to_dict()
    raw["talents"]["T2"]["performance_bonus"] = 1
    with pytest.raises(GaConfigError):
        validate_ga_config(raw)
    turn(state, "a")
    request = next(a for a in legal_actions(state) if a.operation == "attack")
    stale = replace(request, operation="t2_strike", performance=Performance.INSTANT)
    assert not validate_request(state, stale).accepted
