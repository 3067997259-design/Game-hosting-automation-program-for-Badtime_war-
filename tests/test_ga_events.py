"""事件接口测试：完整AOE、幂等、归因、实际额度、系统批次与终局定格。"""

from dataclasses import FrozenInstanceError, replace
import json
from typing import Any

import pytest

from engine.ga.config import load_ga_config
from engine.ga.contracts import (
    ArmorChange, BarrierChange, BarrierPolicy, ContractError, DamageAttribute, DamageKind,
    DeathOutcome, DeathPolicy, DefenseKind, EffectKind, EffectSpec, EventStamp, HitOutcome,
    HitPolicy, IsolationPolicy, PlayerResult, ResolutionRecord, RoundPhase, TargetScope,
    TerminalReason, TerminalSnapshot,
)
from engine.ga.events import (
    BeneficiaryRole, DomainEvent, EventKind, EventLedger, RewardRule, events_from_resolutions,
)


def event(event_id: str = "event-1", **changes: Any) -> DomainEvent:
    values = {"event_id": event_id, "kind": EventKind.HP_DAMAGED,
              "stamp": EventStamp(5, 5, RoundPhase.R3), "root_id": "root-1", "batch_id": "batch-1",
              "actor_id": "a", "target_id": "b", "score_owner_id": "a", "crime_owner_id": "x",
              "effective": True, "payload": {"hp_loss": 1}}
    values.update(changes)
    return DomainEvent(**values)


def effect(**changes: Any) -> EffectSpec:
    values = {"effect_id": "effect-1", "source_id": "bow", "root_id": "root-1",
              "batch_id": "batch-1", "actor_id": "a", "score_owner_id": "a",
              "crime_owner_id": "x", "target_ids": ("b",), "kind": EffectKind.DAMAGE,
              "damage_kind": DamageKind.NORMAL, "amount": 4, "attribute": DamageAttribute.NORMAL,
              "scope": TargetScope.RANGED_SINGLE, "hit_policy": HitPolicy.ROLL,
              "barrier_policy": BarrierPolicy.RESPECT, "isolation_policy": IsolationPolicy.RESPECT,
              "death_policy": DeathPolicy.NORMAL}
    values.update(changes)
    return EffectSpec(**values)


def record(**changes: Any) -> ResolutionRecord:
    values = {"resolution_id": "hit-b", "effect": effect(),
              "stamp": EventStamp(5, 5, RoundPhase.R3), "target_id": "b",
              "outcome": HitOutcome.HIT, "remaining_damage": 4, "computed_hp_loss": 4,
              "hp_before": 20, "hp_loss": 4}
    values.update(changes)
    return ResolutionRecord(**values)


def rule(rule_id: str = "attention", **changes: Any) -> RewardRule:
    values = {"rule_id": rule_id, "event_kinds": frozenset({EventKind.HP_DAMAGED}),
              "role": BeneficiaryRole.TARGET, "once_per_root": True,
              "per_round_cap": load_ga_config().get("action", "attention_per_round")}
    values.update(changes)
    return RewardRule(**values)


def terminal(**changes: Any) -> TerminalSnapshot:
    cfg = load_ga_config()
    values = {"snapshot_id": "terminal-1", "stamp": EventStamp(5, 5, RoundPhase.R4),
              "reason": TerminalReason.LAST_SURVIVOR, "survivor_ids": ("a",),
              "results": {"a": PlayerResult(1, 4, 0, (), 2, 15),
                          "b": PlayerResult(0, 0, 0, (), 0, 0)},
              "ruleset": cfg.get("_meta", "ruleset"), "schema_version": "ga-contract-v1",
              "parameter_fingerprint": cfg.fingerprint, "seed": 0}
    values.update(changes)
    return TerminalSnapshot(**values)


def test_atomic_batch_validation_prevents_partial_publication() -> None:
    ledger = EventLedger()
    first = event()
    incompatible = event("event-2", batch_id="other-batch")
    with pytest.raises(ContractError):
        ledger.commit_batch("batch-1", (first, incompatible))
    assert ledger.events == ()
    assert ledger.commit_batch("batch-1", (first,)) == (first,)


def test_failing_input_iterator_does_not_publish_its_prefix() -> None:
    ledger = EventLedger()

    def incomplete() -> Any:
        yield event()
        raise RuntimeError("interrupted publisher")

    with pytest.raises(RuntimeError, match="interrupted"):
        ledger.commit_batch("batch-1", incomplete())
    assert ledger.events == ()


def test_exact_replay_is_silent_and_conflicting_event_ids_fail_closed() -> None:
    ledger = EventLedger()
    first = event()
    ledger.commit_batch("batch-1", (first,))
    assert ledger.commit_batch("batch-1", (DomainEvent.from_dict(first.to_dict()),)) == ()
    with pytest.raises(ContractError, match="conflicting"):
        ledger.commit_batch("batch-1", (replace(first, payload={"hp_loss": 2}),))
    with pytest.raises(ContractError, match="already belongs"):
        ledger.commit_batch("batch-2", (replace(first, batch_id="batch-2"),))
    assert ledger.events == (first,)


def test_duplicate_ids_inside_an_atomic_batch_never_publish_twice() -> None:
    ledger = EventLedger()
    with pytest.raises(ContractError, match="duplicate"):
        ledger.commit_batch("batch-1", (event(), event()))
    assert ledger.events == ()


def test_complete_aoe_is_required_before_any_target_events_become_visible() -> None:
    ledger = EventLedger()
    area = effect(target_ids=("b", "c"), scope=TargetScope.LOCAL_AREA, hit_policy=HitPolicy.CERTAIN)
    b = record(effect=area, hp_before=2, hp_loss=2, death_outcome=DeathOutcome.DEAD)
    c = replace(b, resolution_id="hit-c", target_id="c")
    with pytest.raises(ContractError, match="every declared target"):
        ledger.commit_resolutions((b,))
    assert ledger.events == ()
    result = ledger.commit_resolutions((c, b))
    assert {item.target_id for item in result if item.kind is EventKind.ACTOR_DIED} == {"b", "c"}
    assert [item.target_id for item in result if item.kind is EventKind.HP_DAMAGED] == ["b", "c"]
    assert ledger.commit_resolutions((b, c)) == ()
    assert events_from_resolutions((b, c)) == result


def test_immune_target_still_needs_a_zero_result_and_can_produce_no_events() -> None:
    ledger = EventLedger()
    immune = record(outcome=HitOutcome.IMMUNE, remaining_damage=0, computed_hp_loss=0, hp_loss=0)
    assert ledger.commit_resolutions((immune,)) == ()
    assert ledger.commit_resolutions((immune,)) == ()
    changed = replace(immune, outcome=HitOutcome.SUBSTITUTED)
    with pytest.raises(ContractError, match="conflicting"):
        ledger.commit_resolutions((changed,))


def test_graze_publishes_evasion_and_real_hp_damage_without_status_or_wear() -> None:
    result = events_from_resolutions((record(outcome=HitOutcome.GRAZE,
                                            computed_hp_loss=1, hp_loss=1),))
    assert [item.kind for item in result] == [EventKind.EVADED, EventKind.HP_DAMAGED]
    assert result[1].payload["hp_loss"] == 1 and result[1].payload["grazed"]


def test_overkill_and_insurance_cannot_be_counted_as_a_kill() -> None:
    events = events_from_resolutions((record(hp_before=2, hp_loss=2,
                                            death_outcome=DeathOutcome.RESURRECTED),))
    assert [item.kind for item in events] == [EventKind.HP_DAMAGED, EventKind.LETHAL_REPLACED]
    assert events[0].payload["hp_loss"] == 2
    assert events[0].payload["computed_hp_loss"] == 4
    assert all(item.score_owner_id == "a" and item.crime_owner_id == "x" for item in events)


def test_absolute_policy_only_becomes_death_when_hp_really_reaches_zero() -> None:
    tagged = effect(damage_kind=DamageKind.DIRECT, death_policy=DeathPolicy.ABSOLUTE_IF_LETHAL)
    survived = events_from_resolutions((record(effect=tagged),))
    assert [item.kind for item in survived] == [EventKind.HP_DAMAGED]
    dead = events_from_resolutions((record(effect=tagged, hp_before=2, hp_loss=2,
                                           death_outcome=DeathOutcome.ABSOLUTE_DEAD),))
    assert dead[-1].kind is EventKind.ACTOR_DIED and dead[-1].payload["absolute"]


def test_barrier_break_is_not_ordinary_armor_reward() -> None:
    ledger = EventLedger()
    blocker = BarrierChange("shield", DefenseKind.FINITE_BARRIER, 4, 0, 4)
    blocked = record(outcome=HitOutcome.BLOCKED, remaining_damage=0, computed_hp_loss=0, hp_loss=0,
                     barrier_changes=(blocker,))
    events = ledger.commit_resolutions((blocked,))
    assert [item.kind for item in events] == [EventKind.BARRIER_BROKEN]
    pp = rule("break-pp", event_kinds=frozenset({EventKind.ARMOR_BROKEN}),
              role=BeneficiaryRole.SCORE_OWNER)
    assert ledger.claim(pp, events[0].event_id, "a", requested=1).amount == 0
    broken = record(resolution_id="hit-2", effect=effect(effect_id="effect-2", batch_id="batch-2"),
                    armor_changes=(ArmorChange("armor", 1, 0, ordinary_wear=1),))
    broken_events = ledger.commit_resolutions((broken,))
    armor_event = next(item for item in broken_events if item.kind is EventKind.ARMOR_BROKEN)
    assert ledger.claim(pp, armor_event.event_id, "a", requested=1).amount == 1


def test_unbroken_pure_wear_is_permanent_equipment_progress_but_not_effective_hp_damage() -> None:
    wear = effect(kind=EffectKind.WEAR, damage_kind=DamageKind.PURE_WEAR, amount=1,
                  hit_policy=HitPolicy.CERTAIN)
    events = events_from_resolutions((record(effect=wear, remaining_damage=0,
        computed_hp_loss=0, hp_loss=0, armor_changes=(ArmorChange("armor", 4, 3, pure_wear=1),)),))
    assert [item.kind for item in events] == [EventKind.ARMOR_WORN]
    assert not events[0].effective and events[0].payload["pure_wear"] == 1


def test_root_award_is_once_across_multiple_segments_targets_and_event_types() -> None:
    ledger = EventLedger()
    memory = rule("memory", event_kinds=frozenset({EventKind.HP_DAMAGED, EventKind.ARMOR_BROKEN,
                  EventKind.PUBLIC_PERFORMED}), role=BeneficiaryRole.OBSERVER, per_round_cap=None)
    candidates = (event("one"), event("two", kind=EventKind.ARMOR_BROKEN),
                  event("three", target_id="c", kind=EventKind.PUBLIC_PERFORMED))
    ledger.commit_batch("batch-1", candidates)
    gains = [ledger.claim(memory, item.event_id, "observer", requested=1).amount
             for item in candidates]
    assert gains == [1, 0, 0]
    next_segment = event("four", batch_id="batch-2")
    ledger.commit_batch("batch-2", (next_segment,))
    assert ledger.claim(memory, "four", "observer", requested=1).amount == 0


def test_claim_replay_returns_zero_and_rule_changes_cannot_reset_quota() -> None:
    ledger = EventLedger()
    ledger.commit_batch("batch-1", (event(),))
    attention = rule()
    first = ledger.claim(attention, "event-1", "b", requested=1)
    retry = ledger.claim(attention, "event-1", "b", requested=99)
    assert first.amount == 1 and retry.amount == 0 and retry.replayed
    assert ledger.claims == (first,)
    with pytest.raises(ContractError, match="cannot change"):
        ledger.claim(replace(attention, per_round_cap=99), "event-1", "b", requested=1)


def test_full_sp_does_not_consume_round_quota_or_allow_retroactive_award() -> None:
    ledger = EventLedger()
    first = event()
    later = event("event-2", batch_id="batch-2", root_id="root-2")
    ledger.commit_batch("batch-1", (first,))
    ledger.commit_batch("batch-2", (later,))
    attention = rule()
    assert ledger.claim(attention, first.event_id, "b", requested=1, available_room=0).amount == 0
    assert ledger.claim(attention, later.event_id, "b", requested=1, available_room=1).amount == 1
    replay = ledger.claim(attention, first.event_id, "b", requested=1, available_room=1)
    assert replay.amount == 0 and replay.replayed


def test_round_quota_counts_actual_amount_and_uses_actual_round_not_clock() -> None:
    ledger = EventLedger()
    laughs = rule("laugh", role=BeneficiaryRole.OBSERVER, once_per_root=False,
                  per_round_cap=load_ga_config().get("talents", "G6", "laugh_per_round"))
    actual = []
    for number, stamp in enumerate((EventStamp(5, 10, RoundPhase.R3),
                                    EventStamp(5, 10, RoundPhase.R4),
                                    EventStamp(5, 10, RoundPhase.R4),
                                    EventStamp(6, 10, RoundPhase.R0))):
        item = event(f"event-{number}", batch_id=f"batch-{number}",
                     root_id=f"root-{number}", stamp=stamp)
        ledger.commit_batch(item.batch_id, (item,))
        requested, room = ((2, 1), (2, 9), (1, 9), (1, 9))[number]
        actual.append(ledger.claim(laughs, item.event_id, "viewer", requested=requested,
                                   available_room=room).amount)
    assert actual == [1, 2, 0, 1]


def test_unprocessed_old_round_events_cannot_mint_new_round_resources() -> None:
    ledger = EventLedger()
    ledger.commit_batch("batch-1", (event(),))
    newer = event("new", batch_id="batch-2", root_id="root-2",
                  stamp=EventStamp(6, 11, RoundPhase.R0))
    ledger.commit_batch("batch-2", (newer,))
    with pytest.raises(ContractError, match="earlier round"):
        ledger.claim(rule(), "event-1", "b", requested=1)
    assert ledger.claims == ()
    assert ledger.claim(rule(), "new", "b", requested=1).amount == 1


def test_observer_and_initiator_policies_cannot_confuse_crime_framing() -> None:
    ledger = EventLedger()
    ledger.commit_batch("batch-1", (event(),))
    damage = rule("score", role=BeneficiaryRole.SCORE_OWNER)
    assert ledger.claim(damage, "event-1", "x", requested=1).amount == 0
    assert ledger.claim(damage, "event-1", "a", requested=1).amount == 1
    crime = rule("crime", role=BeneficiaryRole.CRIME_OWNER)
    assert ledger.claim(crime, "event-1", "x", requested=1).amount == 1
    assert ledger.claim(crime, "event-1", "a", requested=1).amount == 0


def test_different_award_policies_do_not_share_attention_budget() -> None:
    ledger = EventLedger()
    ledger.commit_batch("batch-1", (event(),))
    assert ledger.claim(rule(), "event-1", "b", requested=1).amount == 1
    assert ledger.claim(rule("poem-refund"), "event-1", "b", requested=1).amount == 1


def test_rootless_system_groups_coalesce_per_batch_without_namespace_collision() -> None:
    ledger = EventLedger()
    one = event("one", root_id=None, actor_id=None, batch_id="system-1")
    same = event("same", root_id=None, actor_id=None, batch_id="system-1", target_id="c")
    two = event("two", root_id=None, actor_id=None, batch_id="system-2")
    named = event("named", root_id="system-1", batch_id="batch-action")
    ledger.commit_batch("system-1", (one, same))
    ledger.commit_batch("system-2", (two,))
    ledger.commit_batch("batch-action", (named,))
    memory = rule("memory", role=BeneficiaryRole.OBSERVER, per_round_cap=None)
    assert [ledger.claim(memory, item.event_id, "viewer", requested=1).amount
            for item in (one, same, two, named)] == [1, 0, 1, 1]


def test_only_committed_eligible_events_can_reserve_awards() -> None:
    ledger = EventLedger()
    with pytest.raises(ContractError):
        ledger.claim(rule(), "unknown", "b", requested=1)
    ledger.commit_batch("batch-1", (event(effective=False),))
    assert ledger.claim(rule(), "event-1", "b", requested=1).amount == 0
    assert ledger.claims[0].amount == 0


@pytest.mark.parametrize("invalid", [True, -1, 1.0])
def test_invalid_available_room_cannot_consume_ledger_quota(invalid: Any) -> None:
    ledger = EventLedger()
    ledger.commit_batch("batch-1", (event(),))
    with pytest.raises(ContractError):
        ledger.claim(rule(), "event-1", "b", requested=1, available_room=invalid)
    assert ledger.claims == ()
    assert ledger.claim(rule(), "event-1", "b", requested=1).amount == 1


def test_events_roundtrip_to_json_without_sharing_payloads() -> None:
    payload = {"details": [{"hp_loss": 1}]}
    original = event(payload=payload)
    decoded = DomainEvent.from_dict(json.loads(json.dumps(original.to_dict())))
    assert decoded == original
    payload["details"][0]["hp_loss"] = 9
    assert decoded.payload["details"][0]["hp_loss"] == 1
    with pytest.raises(TypeError):
        decoded.payload["details"][0]["hp_loss"] = 9
    with pytest.raises(FrozenInstanceError):
        decoded.effective = False


@pytest.mark.parametrize("change", [
    {"kind": "legacy-replay"}, {"stamp": {"global_round": 5, "world_tick": 10, "phase": "R9"}},
    {"effective": 1}, {"payload": {"x": float("inf")}}, {"unexpected": True},
])
def test_malformed_event_transport_does_not_silently_coerce(change: dict[str, Any]) -> None:
    payload = event().to_dict()
    payload.update(change)
    with pytest.raises(ContractError):
        DomainEvent.from_dict(payload)


def test_world_tick_can_pause_while_append_order_remains_actual_time() -> None:
    ledger = EventLedger()
    first = event(stamp=EventStamp(5, 11, RoundPhase.R4))
    ledger.commit_batch("batch-1", (first,))
    later = event("later", batch_id="batch-2", stamp=EventStamp(6, 11, RoundPhase.R0))
    ledger.commit_batch("batch-2", (later,))
    assert ledger.commit_batch("batch-1", (first,)) == ()
    with pytest.raises(ContractError, match="earlier facts"):
        ledger.commit_batch("batch-old", (event("old", batch_id="batch-old"),))


def test_terminal_is_sealed_once_and_new_facts_or_awards_cannot_change_it() -> None:
    ledger = EventLedger()
    first = event()
    ledger.commit_batch("batch-1", (first,))
    award = ledger.claim(rule(), first.event_id, "b", requested=1)
    snapshot = terminal()
    assert ledger.finalize(snapshot)
    assert not ledger.finalize(snapshot)
    assert ledger.commit_batch("batch-1", (first,)) == ()
    assert ledger.claim(rule(), first.event_id, "b", requested=1).amount == 0
    assert ledger.claims == (award,)
    with pytest.raises(ContractError, match="terminal"):
        ledger.commit_batch("batch-2", (event("new", batch_id="batch-2"),))
    with pytest.raises(ContractError, match="terminal"):
        ledger.claim(rule("new-award"), first.event_id, "b", requested=1)
    with pytest.raises(ContractError, match="already sealed"):
        ledger.finalize(replace(snapshot, snapshot_id="other-ending"))
    assert ledger.terminal is snapshot and ledger.terminal.results["a"].pp_balance == 2


def test_terminal_cannot_predate_the_completed_batch() -> None:
    ledger = EventLedger()
    ledger.commit_batch("batch-1", (event(stamp=EventStamp(6, 11, RoundPhase.R3)),))
    with pytest.raises(ContractError, match="predates"):
        ledger.finalize(terminal())
    assert ledger.terminal is None


@pytest.mark.parametrize("bad", [None, "hp_damaged", [["unhashable"]]])
def test_bad_reward_event_kind_collections_fail_with_a_contract_error(bad: Any) -> None:
    with pytest.raises(ContractError):
        rule(event_kinds=bad)
