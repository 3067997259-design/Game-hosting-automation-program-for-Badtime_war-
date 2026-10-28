"""公共合同的行为边界：预检零副作用、资源守恒、隔离身份、计时与响应窗口。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import json
import random
from typing import Any

import pytest

from engine.ga.actions import ActionContext, ActionDefinition, GrantKind, precheck_action
from engine.ga.config import GaConfig, load_ga_config
from engine.ga.contracts import (
    ActionPlan, ActionRequest, ActionVerb, ArmorChange, BarrierChange, BarrierPolicy,
    ChoiceState, ChoiceWindow, ContractError, CostEntry, DamageAttribute, DamageKind,
    DeathOutcome, DeathPolicy, DefenseKind, DefenseSpec, EffectKind, EffectSpec, EventStamp,
    HitOutcome, HitPolicy, IsolationPolicy, Performance, PlayerResult, RandomRequirement,
    ResolutionRecord, RoundPhase, TargetScope, TerminalReason, TerminalSnapshot, TimedEffect,
    TimerUnit, TurnStage,
)


@pytest.fixture(scope="module")
def config() -> GaConfig:
    return load_ga_config()


def request(**changes: Any) -> ActionRequest:
    values = {"actor_id": "a", "grant_id": "grant-a", "root_id": "root-a", "stage": TurnStage.T1,
              "verb": ActionVerb.ATTACK, "operation": "weapon.attack", "target_ids": ("b",)}
    values.update(changes)
    return ActionRequest(**values)


def context(**changes: Any) -> ActionContext:
    values = {"actor_id": "a", "grant_id": "grant-a", "root_id": "root-a",
              "kind": GrantKind.STANDARD,
              "snapshot_id": "state-1", "resources": {
                  "a": {"hp": 20, "sp": 2, "credits": 4, "inventory:arrow": 5, "magic": 8},
                  "x": {"credits": 1}}, "legal_target_ids": ("a", "b", "x"),
              "allowed_verbs": frozenset(ActionVerb), "occupied_stages": frozenset(),
              "public_available": True}
    values.update(changes)
    return ActionContext(**values)


def definition(**changes: Any) -> ActionDefinition:
    values = {"operation": "weapon.attack", "stage": TurnStage.T1, "verb": ActionVerb.ATTACK,
              "allowed_performances": frozenset(Performance),
              "costs": (CostEntry("a", "inventory:arrow", 1),),
              "minimum_targets": 1, "maximum_targets": 1,
              "random_requirements": (RandomRequirement("hit", 100, 1),)}
    values.update(changes)
    return ActionDefinition(**values)


def effect(**changes: Any) -> EffectSpec:
    values = {"effect_id": "effect-a", "source_id": "weapon.bow", "root_id": "root-a",
              "batch_id": "batch-a", "actor_id": "a", "score_owner_id": "a",
              "crime_owner_id": "a", "target_ids": ("b",), "kind": EffectKind.DAMAGE,
              "damage_kind": DamageKind.NORMAL, "amount": 4, "attribute": DamageAttribute.NORMAL,
              "scope": TargetScope.RANGED_SINGLE, "hit_policy": HitPolicy.ROLL,
              "barrier_policy": BarrierPolicy.RESPECT, "isolation_policy": IsolationPolicy.RESPECT,
              "death_policy": DeathPolicy.NORMAL}
    values.update(changes)
    return EffectSpec(**values)


def resolution(**changes: Any) -> ResolutionRecord:
    values = {"resolution_id": "hit-a", "effect": effect(),
              "stamp": EventStamp(2, 2, RoundPhase.R3), "target_id": "b",
              "outcome": HitOutcome.HIT, "remaining_damage": 4, "computed_hp_loss": 4,
              "hp_before": 20, "hp_loss": 4}
    values.update(changes)
    return ResolutionRecord(**values)


def test_precheck_is_read_only_and_does_not_draw_rng(config: GaConfig) -> None:
    resources = {"a": {"inventory:arrow": 5, "sp": 2}}
    original = deepcopy(resources)
    ctx = context(resources=resources)
    rng_state = random.getstate()
    plan = precheck_action(request(), ctx, definition(), config)
    assert plan.accepted and plan.phase_claim is TurnStage.T1
    assert plan.costs == (CostEntry("a", "inventory:arrow", 1),)
    assert plan.random_requirements == (RandomRequirement("hit", 100, 1),)
    assert resources == original and ctx.resources["a"]["inventory:arrow"] == 5
    assert random.getstate() == rng_state
    resources["a"]["inventory:arrow"] = 0
    assert ctx.resources["a"]["inventory:arrow"] == 5


@pytest.mark.parametrize("change,expected", [
    ({"actor_id": "x"}, "actor_or_grant_mismatch"),
    ({"grant_id": "wrong"}, "actor_or_grant_mismatch"),
    ({"root_id": "forged-root"}, "root_mismatch"),
    ({"operation": "unknown"}, "operation_mismatch"),
    ({"target_ids": ()}, "target_count_invalid"),
    ({"target_ids": ("nobody",)}, "target_not_legal"),
    ({"parameters": {"hidden_flag": True}}, "parameter_keys_invalid"),
])
def test_rejected_precheck_cannot_authorize_costs_or_rng(
    config: GaConfig, change: dict[str, Any], expected: str
) -> None:
    plan = precheck_action(request(**change), context(), definition(), config)
    assert not plan.accepted and expected in plan.issues
    assert plan.costs == plan.random_requirements == () and plan.phase_claim is None
    with pytest.raises(ContractError):
        plan.assert_current("state-1")


def test_precheck_aggregates_costs_before_checking_payer_balance(config: GaConfig) -> None:
    plan = precheck_action(request(), context(), definition(costs=(
        CostEntry("a", "credits", 3), CostEntry("a", "credits", 3)
    )), config)
    assert not plan.accepted
    assert "insufficient_resource:a:credits" in plan.issues
    assert plan.costs == ()


def test_multiple_real_wallets_are_quoted_without_debt(config: GaConfig) -> None:
    costs = (CostEntry("x", "credits", 1), CostEntry("a", "credits", 3))
    plan = precheck_action(request(), context(), definition(costs=costs), config)
    assert plan.accepted and sum(item.amount for item in plan.costs) == 4
    failed = precheck_action(request(), context(), definition(costs=(
        CostEntry("x", "credits", 2), CostEntry("a", "credits", 2)
    )), config)
    assert not failed.accepted and failed.costs == ()


def test_hp_payment_preserves_one_hp_and_carries_floor_into_the_plan(config: GaConfig) -> None:
    plan = precheck_action(request(), context(),
                           definition(costs=(CostEntry("a", "hp", 19),)), config)
    assert plan.accepted and plan.costs[0].minimum_remaining == 1
    failed = precheck_action(request(), context(),
                             definition(costs=(CostEntry("a", "hp", 20),)), config)
    assert not failed.accepted and failed.costs == ()


@pytest.mark.parametrize("mode,expected", [
    (Performance.NONE, 0), (Performance.INSTANT, 1),
    (Performance.PUBLIC, 2), (Performance.MAJOR_INSTANT, 2),
])
def test_performance_sp_quotes_come_from_shared_configuration(
    config: GaConfig, mode: Performance, expected: int
) -> None:
    plan = precheck_action(request(performance=mode), context(), definition(), config)
    assert plan.accepted
    assert sum(item.amount for item in plan.costs if item.account == "sp") == expected


def test_public_only_uses_the_current_standard_t1_and_not_an_extra_turn(config: GaConfig) -> None:
    req = request(performance=Performance.PUBLIC)
    assert precheck_action(req, context(occupied_stages=frozenset({TurnStage.T0})),
                           definition(), config).accepted
    assert not precheck_action(req, context(kind=GrantKind.EXTRA_TURN),
                               definition(), config).accepted
    assert not precheck_action(req, context(public_available=False), definition(), config).accepted
    assert not precheck_action(req, context(occupied_stages=frozenset({TurnStage.T1})),
                               definition(), config).accepted


def test_t0_preparation_consumes_t0_without_authorizing_a_second_t1(config: GaConfig) -> None:
    req = request(stage=TurnStage.T0, verb=ActionVerb.SPECIAL, operation="prepare.barrier",
                  target_ids=(), performance=Performance.INSTANT)
    spec = definition(stage=TurnStage.T0, verb=ActionVerb.SPECIAL, operation="prepare.barrier",
                      costs=(), minimum_targets=0, maximum_targets=0, random_requirements=())
    plan = precheck_action(req, context(), spec, config)
    assert plan.accepted and plan.phase_claim is TurnStage.T0
    assert not precheck_action(req, context(occupied_stages=frozenset({TurnStage.T0})),
                               spec, config).accepted
    assert precheck_action(request(), context(occupied_stages=frozenset({TurnStage.T0})),
                           definition(), config).accepted


def test_necessary_metadata_has_no_phase_claim_or_extra_action(config: GaConfig) -> None:
    req = request(stage=TurnStage.T0, verb=ActionVerb.SPECIAL,
                  operation="setup.selector", target_ids=())
    spec = definition(stage=TurnStage.T0, verb=ActionVerb.SPECIAL, operation="setup.selector",
                      costs=(), minimum_targets=0, maximum_targets=0, consumes_stage=False)
    plan = precheck_action(req, context(occupied_stages=frozenset({TurnStage.T0})), spec, config)
    assert plan.accepted and plan.phase_claim is None
    assert not precheck_action(request(), context(occupied_stages=frozenset({TurnStage.T1})),
                               definition(), config).accepted


@pytest.mark.parametrize("kind", [GrantKind.STEP_FOLLOWUP, GrantKind.REACTION])
def test_restricted_grants_never_gain_t0_or_performance(config: GaConfig, kind: GrantKind) -> None:
    ctx = context(kind=kind, allowed_verbs=frozenset({ActionVerb.ATTACK, ActionVerb.MOVE}))
    assert precheck_action(request(), ctx, definition(), config).accepted
    assert not precheck_action(request(performance=Performance.INSTANT), ctx,
                               definition(), config).accepted
    with pytest.raises(ContractError, match="restricted"):
        context(kind=kind)
    req = request(stage=TurnStage.T0, operation="prepare", verb=ActionVerb.MOVE)
    spec = definition(stage=TurnStage.T0, operation="prepare", verb=ActionVerb.MOVE)
    assert not precheck_action(req, ctx, spec, config).accepted


def test_zero_effect_and_stale_plans_fail_without_consumption(config: GaConfig) -> None:
    assert not precheck_action(request(), context(), definition(meaningful=False), config).accepted
    plan = precheck_action(request(), context(), definition(), config)
    plan.assert_current("state-1")
    with pytest.raises(ContractError, match="stale"):
        plan.assert_current("state-2")


def test_later_segment_failure_does_not_rewrite_an_earlier_plan(config: GaConfig) -> None:
    earlier = precheck_action(request(), context(), definition(costs=(CostEntry("a", "magic", 7),)),
                              config)
    later = precheck_action(request(root_id="root-a"), context(snapshot_id="state-2", resources={
        "a": {"magic": 1, "sp": 2}}), definition(costs=(CostEntry("a", "magic", 3),)), config)
    assert earlier.accepted and earlier.costs[0].amount == 7
    assert not later.accepted and later.costs == ()
    assert earlier.snapshot_id == "state-1"


def test_controller_request_json_roundtrip_is_strict_and_deeply_immutable() -> None:
    parameters = {"choices": [{"weapon": "bow"}]}
    original = request(parameters=parameters)
    decoded = ActionRequest.from_dict(json.loads(json.dumps(original.to_dict())))
    assert original == decoded
    parameters["choices"][0]["weapon"] = "knife"
    assert original.parameters["choices"][0]["weapon"] == "bow"
    exported = decoded.to_dict()
    exported["parameters"]["choices"][0]["weapon"] = "other"
    assert decoded.parameters["choices"][0]["weapon"] == "bow"
    with pytest.raises(TypeError):
        decoded.parameters["choices"][0]["weapon"] = "other"
    with pytest.raises(FrozenInstanceError):
        decoded.actor_id = "x"


@pytest.mark.parametrize("bad", [
    {"stage": "future"}, {"performance": "legacy"}, {"target_ids": "b"},
    {"target_ids": ["b", "b"]}, {"parameters": {"x": float("nan")}},
    {"parameters": {1: "not-a-JSON-key"}},
])
def test_malformed_request_transport_is_rejected(bad: dict[str, Any]) -> None:
    payload = request().to_dict()
    payload.update(bad)
    with pytest.raises(ContractError):
        ActionRequest.from_dict(payload)


def test_request_transport_cannot_fill_in_missing_or_unknown_fields() -> None:
    payload = request().to_dict()
    del payload["grant_id"]
    with pytest.raises(ContractError):
        ActionRequest.from_dict(payload)
    payload = request().to_dict()
    payload["legacy_profile"] = "m9-rfc"
    with pytest.raises(ContractError):
        ActionRequest.from_dict(payload)


def test_damage_attribute_and_direct_identity_are_independent() -> None:
    normal = effect(attribute=DamageAttribute.UNATTRIBUTED)
    direct = effect(damage_kind=DamageKind.DIRECT)
    assert normal.damage_kind is DamageKind.NORMAL
    assert direct.death_policy is DeathPolicy.NORMAL
    assert direct.barrier_policy is BarrierPolicy.RESPECT
    named = effect(source_id="g7_terror_but_no_permissions")
    assert named.death_policy is DeathPolicy.NORMAL
    assert named.isolation_policy is IsolationPolicy.RESPECT


def test_effect_keeps_actual_actor_score_owner_and_crime_owner_separate() -> None:
    framed = effect(actor_id="caster", score_owner_id="caster", crime_owner_id="framed-player")
    assert framed.actor_id == framed.score_owner_id == "caster"
    assert framed.crime_owner_id == "framed-player"


def test_defense_snapshot_cannot_promote_ordinary_armor_to_an_absorption_pool() -> None:
    armor = DefenseSpec("armor", DefenseKind.ARMOR, 1, 6,
                        frozenset({DamageKind.NORMAL, DamageKind.PURE_WEAR}),
                        frozenset(TargetScope), {DamageAttribute.NORMAL: 2,
                        DamageAttribute.TECH: 0, DamageAttribute.MAGIC: 0}, 2)
    assert not armor.protects_breaking_hit
    active = DefenseSpec("active", DefenseKind.BREAKING_HIT_BARRIER, 1, 6,
                         frozenset({DamageKind.NORMAL}), frozenset({TargetScope.RANGED_SINGLE}))
    assert active.protects_breaking_hit
    with pytest.raises(ContractError):
        replace(armor, accepted_damage_kinds=frozenset({DamageKind.DIRECT}))
    with pytest.raises(ContractError):
        replace(active, wear_price=1)


def test_barrier_receipts_distinguish_full_breaking_hit_and_finite_overflow() -> None:
    full = BarrierChange("active", DefenseKind.BREAKING_HIT_BARRIER, 1, 0, 8)
    record = resolution(effect=effect(amount=8), remaining_damage=0, computed_hp_loss=0,
                        hp_loss=0, outcome=HitOutcome.BLOCKED, barrier_changes=(full,))
    assert record.effective_hit and record.hp_loss == 0
    partial = BarrierChange("finite", DefenseKind.FINITE_BARRIER, 7, 0, 7)
    assert resolution(effect=effect(amount=8), remaining_damage=1, computed_hp_loss=1,
                      hp_loss=1, barrier_changes=(partial,)).hp_loss == 1
    with pytest.raises(ContractError):
        BarrierChange("halo", DefenseKind.HALO, 1, 0, 8)
    with pytest.raises(ContractError):
        resolution(remaining_damage=0, computed_hp_loss=0, hp_loss=0, outcome=HitOutcome.BLOCKED)


def test_resolution_excludes_overkill_and_does_not_emit_death_for_insurance() -> None:
    record = resolution(hp_before=2, hp_loss=2, death_outcome=DeathOutcome.RESURRECTED)
    assert record.computed_hp_loss == 4 and record.hp_loss == 2
    with pytest.raises(ContractError):
        replace(record, hp_loss=4)
    with pytest.raises(ContractError):
        replace(record, death_outcome=DeathOutcome.NONE)
    absolute = replace(record, effect=effect(death_policy=DeathPolicy.ABSOLUTE_IF_LETHAL),
                       death_outcome=DeathOutcome.ABSOLUTE_DEAD)
    assert absolute.hp_loss == 2
    with pytest.raises(ContractError):
        replace(absolute, death_outcome=DeathOutcome.MORPHED)


def test_graze_is_effective_hp_loss_without_hit_status_or_armor_wear() -> None:
    graze = resolution(outcome=HitOutcome.GRAZE, computed_hp_loss=1, hp_loss=1)
    assert graze.effective_hit
    with pytest.raises(ContractError):
        replace(graze, applied_statuses=("burn",))
    with pytest.raises(ContractError):
        replace(graze, armor_changes=(ArmorChange("armor", 6, 5, ordinary_wear=1),))


def test_pure_wear_does_not_generate_hp_floor_or_relay_after_interception() -> None:
    wear = effect(kind=EffectKind.WEAR, damage_kind=DamageKind.PURE_WEAR, amount=2,
                  hit_policy=HitPolicy.CERTAIN)
    record = resolution(effect=wear, remaining_damage=0, computed_hp_loss=0, hp_loss=0,
                        armor_changes=(ArmorChange("armor", 3, 1, pure_wear=2),))
    assert not record.effective_hit
    with pytest.raises(ContractError):
        replace(record, hp_loss=1, computed_hp_loss=1)
    with pytest.raises(ContractError):
        replace(record, barrier_changes=(
            BarrierChange("shield", DefenseKind.FINITE_BARRIER, 1, 0, 1),))
    blocker = BarrierChange("shield", DefenseKind.FINITE_BARRIER, 1, 0, 1)
    blocked = replace(record, armor_changes=(), outcome=HitOutcome.BLOCKED,
                      barrier_changes=(blocker,))
    assert blocked.hp_loss == 0


def test_wear_accounts_conserve_durability_and_never_relay_to_a_second_armor() -> None:
    change = ArmorChange("armor-a", 3, 0, ordinary_wear=2, attached_wear=1)
    assert change.broken
    with pytest.raises(ContractError):
        ArmorChange("armor-a", 3, 0, ordinary_wear=2)
    with pytest.raises(ContractError):
        resolution(armor_changes=(change, ArmorChange("armor-b", 2, 0, ordinary_wear=2)))


def test_direct_damage_cannot_claim_ordinary_armor_billing() -> None:
    with pytest.raises(ContractError, match="skips armor billing"):
        resolution(effect=effect(damage_kind=DamageKind.DIRECT),
                   armor_changes=(ArmorChange("armor", 3, 2, ordinary_wear=1),))


def test_zero_output_attack_cannot_disguise_a_pure_wear_effect() -> None:
    with pytest.raises(ContractError, match="zero-output"):
        resolution(effect=effect(amount=0), remaining_damage=0, computed_hp_loss=0, hp_loss=0,
                   armor_changes=(ArmorChange("armor", 3, 2, attached_wear=1),))


def test_direct_death_is_explicit_and_not_a_large_damage_number() -> None:
    execution = effect(amount=0, damage_kind=DamageKind.EXECUTION,
                       death_policy=DeathPolicy.ABSOLUTE_TRIGGER, hit_policy=HitPolicy.CERTAIN)
    dead = resolution(effect=execution, remaining_damage=0, computed_hp_loss=20, hp_loss=20,
                      death_outcome=DeathOutcome.ABSOLUTE_DEAD)
    assert dead.death_outcome is DeathOutcome.ABSOLUTE_DEAD
    with pytest.raises(ContractError, match="not a numeric"):
        replace(execution, amount=999)


@pytest.mark.parametrize("scope", [TargetScope.LOCAL_AREA, TargetScope.GLOBAL_AREA])
def test_area_damage_cannot_claim_a_random_evasion_result(scope: TargetScope) -> None:
    with pytest.raises(ContractError, match="do not roll evasion"):
        effect(scope=scope)
    certain = effect(scope=scope, hit_policy=HitPolicy.CERTAIN)
    with pytest.raises(ContractError, match="cannot create evasion"):
        resolution(effect=certain, outcome=HitOutcome.GRAZE, computed_hp_loss=1, hp_loss=1)


def test_round_timer_skips_creation_round_and_ignores_clock_speed() -> None:
    timer = TimedEffect("burn", "b", 5, 2, TimerUnit.FUTURE_R4)
    assert timer.on_r4(5) is timer
    once = timer.on_r4(6)
    assert once.remaining == 1 and once.on_r4(6) is once
    assert once.on_r4(7).remaining == 0
    immediate = replace(timer, tick_on_creation=True).on_r4(5)
    assert immediate.remaining == 1


def test_standard_slot_timer_cannot_be_paid_by_extra_turn_or_duplicate_callback() -> None:
    timer = TimedEffect("self-doubt", "a", 5, 1, TimerUnit.STANDARD_SLOT)
    assert timer.on_slot("extra-1", standard=False) is timer
    expired = timer.on_slot("standard-6", standard=True)
    assert expired.remaining == 0
    assert expired.on_slot("standard-6", standard=True) is expired
    regular = replace(timer, unit=TimerUnit.SLOT)
    assert regular.on_slot("extra-1", standard=False).remaining == 0


def test_gift_has_no_remote_open_or_timeout_before_selectable_t0(config: GaConfig) -> None:
    gift = ChoiceWindow("gift", "sender", "recipient", ("open", "bad-4", "bad-5", "bad-6"),
                        config.get("talents", "G6", "gift_fallback_ticks"),
                        allow_remote_resolution=True, requires_t0_opportunity=True)
    suppressed = gift.offer_t0(8, selectable=False)
    assert suppressed.on_r4(9) is suppressed
    with pytest.raises(ContractError):
        suppressed.resolve("bad-4", actor_id="sender")
    with pytest.raises(ContractError):
        suppressed.resolve("open", actor_id="recipient")
    offered = suppressed.offer_t0(10, selectable=True)
    assert offered.on_r4(10) is offered
    after_one = offered.on_r4(11)
    assert after_one.remaining_ticks == 1 and after_one.on_r4(11) is after_one
    assert after_one.offer_t0(12, selectable=True).remaining_ticks == 1
    expired = after_one.on_r4(12)
    assert expired.state is ChoiceState.EXPIRED and expired.selected_option is None
    assert offered.resolve("bad-4", actor_id="sender").resolved_by == "sender"


def test_poem_requires_explicit_recipient_answer_and_cannot_be_overwritten() -> None:
    poem = ChoiceWindow("poem", "sender", "recipient", ("accept", "reject"), None)
    assert poem.on_r4(99).state is ChoiceState.PENDING
    with pytest.raises(ContractError):
        poem.resolve("accept", actor_id="sender")
    refused = poem.resolve("reject", actor_id="recipient")
    assert refused.selected_option == "reject"
    assert refused.resolve("reject", actor_id="recipient") is refused
    with pytest.raises(ContractError):
        refused.resolve("accept", actor_id="recipient")


def test_terminal_snapshot_preserves_results_and_actual_pp(config: GaConfig) -> None:
    results = {"a": PlayerResult(1, 20, 1, ("chapter-1",), 5, 21),
               "b": PlayerResult(0, 4, 0, (), 2, 3)}
    snapshot = TerminalSnapshot("terminal", EventStamp(12, 11, RoundPhase.R4),
                                TerminalReason.REVERSAL_CLOSURE, ("a", "b"), results,
                                config.get("_meta", "ruleset"), "ga-contract-v1",
                                config.fingerprint, 0)
    results["a"] = replace(results["a"], pp_balance=8)
    exported = snapshot.to_dict()
    exported["results"]["a"]["pp_balance"] = 0
    assert snapshot.results["a"].pp_balance == 5
    with pytest.raises(TypeError):
        snapshot.results["a"] = results["a"]
    with pytest.raises(ContractError):
        replace(snapshot, reason=TerminalReason.LAST_SURVIVOR)


@pytest.mark.parametrize("bad", [True, -1, 1.0, "1"])
def test_noninteger_or_negative_accounts_are_rejected(bad: Any) -> None:
    with pytest.raises(ContractError):
        context(resources={"a": {"sp": bad}})
    with pytest.raises(ContractError):
        CostEntry("a", "sp", bad)


def test_rejected_plan_cannot_be_forged_into_a_payment_authorization() -> None:
    with pytest.raises(ContractError):
        ActionPlan(request(), "state-1", False, (CostEntry("a", "sp", 1),), None, (), ("error",))


@pytest.mark.parametrize("bad", [None, "T0", [["unhashable"]]])
def test_bad_context_enum_containers_produce_contract_errors(bad: Any) -> None:
    with pytest.raises(ContractError):
        context(occupied_stages=bad)


@pytest.mark.parametrize("bad", [None, "sp", ["not-a-cost"]])
def test_bad_cost_containers_produce_contract_errors(bad: Any) -> None:
    with pytest.raises(ContractError):
        definition(costs=bad)


def test_defense_cannot_offer_to_intercept_direct_death() -> None:
    with pytest.raises(ContractError, match="cannot stop direct death"):
        DefenseSpec("shield", DefenseKind.FINITE_BARRIER, 4, 4,
                    frozenset({DamageKind.EXECUTION}), frozenset({TargetScope.RANGED_SINGLE}))
