"""GA公开预测与真实引擎分支对照，禁止读取随机流或改动实际状态。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from engine.ga.ai.forecast import (
    estimate_attack, estimate_option, estimate_police_response, estimate_police_volley,
    incoming_threat,
)
from engine.ga.combat import resolve_batch
from engine.ga.contracts import (
    DamageAttribute, DamageKind, DefenseKind, DefenseSpec, TargetScope, TurnStage, thaw_json,
)
from engine.ga.gameplay import build_options, describe_options, execute_action, validate_request
from engine.ga.observation import public_value
from engine.ga.police import Police
from engine.ga.scoring import score_components
from engine.ga.state import add_armor, observe
from engine.ga.status import add_burns, put_status
from tests.ga_i2_helpers import execute, ready, turn
from tests.test_ga_combat import packet


CASES = (
    ("shield", 6, "bow", (), "normal", False, None, 0, 20),
    ("shield", 6, "bow", (), "normal", True, None, 0, 20),
    ("ceramic", 1, "bow", (), "normal", True, None, 0, 20),
    ("ceramic", 3, "gauss_rifle", (), "normal", False, None, 0, 20),
    ("at_field", 3, "magic_barrage", (), "normal", False, None, 0, 20),
    ("magic_shield", 3, "bow", ("fire",), "magic", True, None, 0, 20),
    ("shield", 6, "bow", ("fire",), "magic", True, None, 0, 20),
    ("ceramic", 10, "bow", ("power", "pierce"), "tech", False, None, 0, 20),
    ("shield", 6, "bow", (), "normal", False, "finite_barrier", 2, 20),
    ("shield", 6, "bow", (), "normal", False, "breaking_hit_barrier", 1, 20),
    ("shield", 6, "bow", (), "normal", True, "finite_barrier", 8, 20),
    ("shield", 1, "bow", (), "normal", False, None, 0, 2),
)


def setup_case(case: tuple[Any, ...]) -> Any:
    """仅构造前置状态；实际伤害、费用及战果都走正式候选和执行器。"""
    armor, durability, weapon, modules, attribute, locked, pool, pool_hp, hp = case
    state = ready()
    actor, target = state.players["a"], state.players["b"]
    actor.weapons.add(weapon)
    actor.ammo[weapon] = 5
    actor.modules = modules
    actor.bow_attribute = DamageAttribute(attribute)
    target.hp = hp
    actor.hp_damage = 3
    key = add_armor(state, "b", armor)
    target.defenses[key] = replace(target.defenses[key], durability=durability)
    if weapon != "magic_barrage":
        target.location = "magic"
    if pool:
        target.defenses["pool"] = DefenseSpec(
            "pool", DefenseKind(pool), pool_hp, pool_hp,
            frozenset({DamageKind.NORMAL, DamageKind.DIRECT, DamageKind.PURE_WEAR}),
            frozenset(TargetScope),
        )
    turn(state, "a", TurnStage.T0 if locked else TurnStage.T1)
    if locked:
        execute(state, "lock", targets=("b",))
        state.current_stage = TurnStage.T1
    return state


@pytest.mark.parametrize("case", CASES)
def test_public_forecast_matches_real_hit_and_graze_branches(case: tuple[Any, ...]) -> None:
    records = []
    for hit in (True, False):
        state = setup_case(case)
        options = build_options(state, state.current_grant, TurnStage.T1)
        option = next(o for o in options if o.request.operation == "attack" and
                      o.request.parameters["weapon"] == case[2] and
                      o.request.target_ids == ("b",))
        descriptor, = describe_options(state, state.current_grant, (option,))
        view = observe(state, "a")
        before = (state.ga_revision, tuple(state.ga_rng.draws), thaw_json(view))
        prediction = estimate_option(view, descriptor, state.ga_config)
        independent = estimate_attack(view, "a", "b", case[2], state.ga_config, locked=case[5])
        assert prediction == independent
        assert before == (state.ga_revision, tuple(state.ga_rng.draws),
                          thaw_json(observe(state, "a")))
        state.ga_rng.filters.append(lambda value, kind, key, bound, actor: 0.0 if hit else 1.0)
        score_before = score_components(state.players["a"], state.ga_config)["total"]
        execute_action(state, validate_request(state, option.request))
        record, = state.ga_resolution_batches[option.effects[0].batch_id][1]
        score_after = score_components(state.players["a"], state.ga_config)["total"]
        records.append((record.hp_loss, sum(c.after == 0 for c in record.armor_changes),
                        sum(c.before - c.after for c in record.armor_changes),
                        score_after - score_before, float(not state.players["b"].alive)))
    probability = prediction.hit_probability
    expected = [probability * a + (1 - probability) * b for a, b in zip(*records)]
    assert prediction.expected_hp == pytest.approx(expected[0])
    assert prediction.expected_breaks == pytest.approx(expected[1])
    assert prediction.expected_wear == pytest.approx(expected[2])
    assert prediction.score_gain == pytest.approx(expected[3])
    assert prediction.kill_probability == pytest.approx(expected[4])


def test_graze_does_not_claim_future_burn_and_no_overkill_score() -> None:
    state = setup_case(CASES[6])
    view = observe(state, "a")
    result = estimate_attack(view, "a", "b", "bow", state.ga_config, locked=True)
    assert result.expected_hp == 4 and result.deferred_hp == 2
    assert result.score_gain == 1  # 已有3累计伤害，本次4伤只新增1分，DOT尚未兑现。
    state.players["b"].hp = 1
    result = estimate_attack(observe(state, "a"), "a", "b", "bow", state.ga_config, locked=True)
    assert result.expected_hp == 1 and result.deferred_hp == 0
    assert result.score_gain == 9 and result.kill_probability == 1


def test_burn_refresh_only_counts_marginal_future_damage() -> None:
    state = setup_case(CASES[6])
    add_burns(state.players["b"], state.ga_config, state.current_round, 3,
              source_id="existing", actor_id="a", score_owner_id="a")
    result = estimate_attack(observe(state, "a"), "a", "b", "bow", state.ga_config, locked=True)
    assert result.deferred_hp == 0


@pytest.mark.parametrize("condition", ("empty", "hidden", "different_location", "disabled"))
def test_unavailable_weapon_never_becomes_a_free_attack(condition: str) -> None:
    state = ready()
    weapon = "bow"
    if condition == "empty":
        state.players["a"].ammo["bow"] = 0
    elif condition == "hidden":
        state.players["b"].hidden = True
    elif condition == "different_location":
        weapon = "knife"
        state.players["b"].location = "magic"
    else:
        state.players["a"].disabled_weapons["bow"] = None
    result = estimate_attack(observe(state, "a"), "a", "b", weapon, state.ga_config, locked=True)
    assert not result.available and result.expected_hp == result.score_gain == 0


def test_infinite_module_replaces_ammo_cost_but_does_not_keep_other_module_effects() -> None:
    state = ready()
    state.players["a"].ammo["bow"] = 0
    state.players["a"].modules = ("infinite",)
    prediction = estimate_attack(observe(state, "a"), "a", "b", "bow", state.ga_config,
                                 locked=True)
    assert prediction.available and prediction.expected_hp == 4 and prediction.deferred_hp == 0


def test_armor_break_reward_allowance_is_shared_by_area_targets() -> None:
    state = ready()
    for actor in ("b", "c"):
        key = add_armor(state, actor, "magic_shield")
        state.players[actor].defenses[key] = replace(state.players[actor].defenses[key],
                                                    durability=1)
    effect = packet(targets=("b", "c"), actor="a", amount=4, attribute=DamageAttribute.MAGIC)
    view = thaw_json(observe(state, "a"))
    descriptor = {"declared_effects": [public_value(effect)]}
    full = estimate_option(view, descriptor, state.ga_config)
    view["reward_allowance"] = {"a": {"armor_break_pp": 0}}
    spent = estimate_option(view, descriptor, state.ga_config)
    assert full.expected_breaks == spent.expected_breaks == 2
    assert full.score_gain - spent.score_gain == 1
    prior = score_components(state.players["a"], state.ga_config)["total"]
    resolve_batch(state, (effect,))
    actual = score_components(state.players["a"], state.ga_config)["total"] - prior
    assert full.score_gain == actual


def test_npc_damage_does_not_mint_player_combat_score() -> None:
    state = ready()
    target = state.ga_police.officer_ids[0]
    state.ga_units[target].hp = 1
    prediction = estimate_attack(observe(state, "a"), "a", target, "bow", state.ga_config,
                                 locked=True)
    assert prediction.expected_hp == 1 and prediction.kill_probability == 1
    assert prediction.score_gain == 0


def test_focus_threat_consumes_armor_between_opponents_and_is_pure() -> None:
    state = ready()
    state.players["a"].hp = 6
    key = add_armor(state, "a", "shield")
    state.players["a"].defenses[key] = replace(state.players["a"].defenses[key], durability=1)
    view = observe(state, "a")
    before = thaw_json(view)
    threat = incoming_threat(view, "a", state.ga_config)
    assert threat.expected_hp == 6 and threat.kill_probability == 1
    assert len(threat.attackers) == 2
    assert before == thaw_json(observe(state, "a"))
    assert state.ga_rng.draws == []


def test_control_and_missing_ammunition_reduce_the_focus_scenario() -> None:
    state = ready()
    state.players["a"].location = "hospital"
    state.players["b"].ammo["bow"] = 0
    put_status(state.players["c"], state.ga_config, state.current_round, "ACTION_SUPPRESSED",
               source_id="test")
    threat = incoming_threat(observe(state, "a"), "a", state.ga_config)
    assert threat.expected_hp == 0 and threat.attackers == {}


def test_smoke_flash_hidden_and_public_gaze_are_not_ignored() -> None:
    state = ready()
    state.players["b"].location = "magic"
    state.players["b"].hidden = True
    state.players["a"].inventory["thermal"] = 1
    put_status(state.players["a"], state.ga_config, state.current_round, "FLASHED",
               source_id="test")
    view = thaw_json(observe(state, "a"))
    view["world"]["smoke"] = {"magic": {}}
    view["afterlife"]["gaze"] = ["b"]
    estimate = estimate_attack(view, "a", "b", "bow", state.ga_config, locked=True)
    assert estimate.hit_probability == pytest.approx(0.6)
    assert estimate.expected_hp == pytest.approx(0.6 * 6 + 0.4 * 1)


@pytest.mark.parametrize("kind", (DamageKind.NORMAL, DamageKind.DIRECT, DamageKind.PURE_WEAR))
def test_explicit_damage_permissions_match_engine(kind: DamageKind) -> None:
    state = ready()
    add_armor(state, "b", "ceramic")
    effect = packet(targets=("b",), actor="a", kind=kind)
    predicted = estimate_option(observe(state, "a"),
                                 {"declared_effects": [public_value(effect)]}, state.ga_config)
    record, = resolve_batch(state, (effect,))
    assert predicted.expected_hp == record.hp_loss
    assert predicted.expected_wear == sum(c.before - c.after for c in record.armor_changes)


def test_dead_player_has_no_future_incoming_damage() -> None:
    state = ready()
    state.players["a"].hp = 0
    threat = incoming_threat(observe(state, "a"), "a", state.ga_config)
    assert threat.expected_hp == threat.kill_probability == 0


@pytest.mark.parametrize("captain", (None, "a"))
@pytest.mark.parametrize("upgrade", (0, 1))
def test_police_volley_forecast_matches_real_branches_and_score_owner(
    captain: str | None, upgrade: int,
) -> None:
    actual = []
    for hit in (True, False):
        state = ready()
        state.players["b"].location = "magic"
        state.ga_police.captain = captain
        state.ga_police.attack_upgrade = upgrade
        state.ga_police.wanted = ["b"]
        Police().sync(state)
        add_armor(state, "b", "shield")
        view = observe(state, "a")
        target, owner, prediction = estimate_police_response(view, state.ga_config)
        assert target == "b" and owner == captain and prediction.available
        assert prediction == estimate_police_volley(view, "b", state.ga_config)
        before = score_components(state.players["a"], state.ga_config)["total"]
        hp_before = state.players["b"].hp
        state.ga_rng.filters.append(lambda value, kind, key, bound, actor: 0.0 if hit else 1.0)
        assert Police().fire(state)
        points = score_components(state.players["a"], state.ga_config)["total"] - before
        actual.append((hp_before - state.players["b"].hp, points))
    probability = prediction.hit_probability
    assert prediction.expected_hp == pytest.approx(
        probability * actual[0][0] + (1 - probability) * actual[1][0])
    assert prediction.score_gain == pytest.approx(
        probability * actual[0][1] + (1 - probability) * actual[1][1])
    if captain is None:
        assert prediction.score_gain == 0


@pytest.mark.parametrize("reason", (
    "used", "stopped", "destroyed", "paused", "submerged", "controlled",
    "hidden", "isolated", "innocent", "dead", "home", "no_roster",
))
def test_police_unavailable_or_ineligible_is_not_a_free_volley(reason: str) -> None:
    state = ready()
    state.ga_police.captain = "a"
    state.ga_police.wanted = ["b"]
    police = state.ga_police
    if reason == "used":
        police.attack_round = state.current_round
    elif reason == "stopped":
        police.stopped.add("test")
    elif reason == "destroyed":
        state.destroyed_locations.add("police")
    elif reason == "paused":
        state.ga_clock_paused = True
    elif reason in ("submerged", "controlled", "no_roster"):
        for key in police.officer_ids:
            npc = state.ga_units[key]
            if reason == "submerged":
                npc.submerged_sources.add("test")
            elif reason == "no_roster":
                npc.hp = 0
            else:
                put_status(npc, state.ga_config, state.current_round, "SHOCKED", source_id="test")
    elif reason == "hidden":
        state.players["b"].hidden = True
    elif reason == "isolated":
        state.players["b"].isolation_id = "separate"
    elif reason == "innocent":
        police.wanted = []
    elif reason == "dead":
        state.players["b"].hp = 0
    else:
        state.players["b"].home_locked = False
    view = observe(state, "a")
    prediction = estimate_police_volley(view, "b", state.ga_config)
    assert not prediction.available and prediction.expected_hp == 0
    assert estimate_police_response(view, state.ga_config)[0] is None
    if reason != "innocent":
        assert not Police().fire(state, "b")


def test_partly_controlled_roster_still_counts_live_officers_in_volley_damage() -> None:
    state = ready()
    state.ga_police.wanted = ["b"]
    first, second, third = state.ga_police.officer_ids
    put_status(state.ga_units[first], state.ga_config, state.current_round, "SHOCKED",
               source_id="test")
    state.ga_units[second].submerged_sources.add("test")
    state.ga_units[third].location = "shop"
    state.players["b"].moved = False
    prediction = estimate_police_volley(observe(state, "a"), "b", state.ga_config)
    assert prediction.available and prediction.max_hp == 5
    state.ga_rng.filters.append(lambda value, kind, key, bound, actor: 0.0)
    before = state.players["b"].hp
    assert Police().fire(state)
    assert before - state.players["b"].hp == 5


def test_mobile_coordination_and_r4_share_exactly_one_volley() -> None:
    state = ready()
    state.ga_police.captain = "a"
    state.ga_police.stance = "mobile"
    state.ga_police.wanted = ["b"]
    Police().sync(state)
    prediction = estimate_police_volley(observe(state, "a"), "b", state.ga_config)
    assert prediction.available
    turn(state, "a")
    execute(state, "attack", targets=("b",), weapon="bow")
    assert state.ga_police.attack_round == state.current_round
    after = state.players["b"].hp
    assert not estimate_police_volley(observe(state, "a"), "b", state.ga_config).available
    assert estimate_police_response(observe(state, "a"), state.ga_config)[0] is None
    Police().r4(state)
    assert state.players["b"].hp == after


def test_r4_skips_hidden_first_wanted_target_instead_of_selecting_maximum_damage() -> None:
    state = ready()
    state.ga_police.wanted = ["b", "c"]
    state.players["b"].hidden = True
    target, _, prediction = estimate_police_response(observe(state, "a"), state.ga_config)
    assert target == "c" and prediction.available
    before = state.players["b"].hp
    assert Police().fire(state)
    assert state.players["b"].hp == before and state.players["c"].hp < before
