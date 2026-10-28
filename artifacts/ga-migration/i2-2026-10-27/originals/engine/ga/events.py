"""GA公开事件与订阅额度账本。

本模块不修改HP、SP或钱包，也不执行回调。正式状态提交后，调用者发布完整批次，
再把新AwardGrant的实际amount应用到同一状态事务。重复领取返回0，不能重复支付。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from engine.ga.contracts import (
    ContractError, DeathOutcome, EventStamp, HitOutcome, ResolutionRecord,
    RoundPhase, TerminalSnapshot, checked_enums, checked_object, freeze_json, require_bool,
    require_enum, require_id, require_int, thaw_json,
)


class EventKind(str, Enum):
    """已经发生的领域事实；预检失败不属于成功发动。"""

    ACTION_ACTIVATED = "action_activated"
    ACTION_RESOLVED = "action_resolved"
    HP_DAMAGED = "hp_damaged"
    ARMOR_WORN = "armor_worn"
    ARMOR_BROKEN = "armor_broken"
    BARRIER_BROKEN = "barrier_broken"
    EVADED = "evaded"
    STATUS_APPLIED = "status_applied"
    LETHAL_REPLACED = "lethal_replaced"
    ACTOR_DIED = "actor_died"
    ACTOR_EXITED = "actor_exited"
    PUBLIC_PERFORMED = "public_performed"
    CHAPTER_COMPLETED = "chapter_completed"
    CHOICE_RESOLVED = "choice_resolved"
    TURN_FORFEITED = "turn_forfeited"
    STATE_CHANGED = "state_changed"


@dataclass(frozen=True)
class DomainEvent:
    """公开且不可变的已落地事实，不根据source名称补全任何权限。"""

    event_id: str
    kind: EventKind
    stamp: EventStamp
    root_id: str | None
    batch_id: str
    actor_id: str | None
    target_id: str | None
    score_owner_id: str | None
    crime_owner_id: str | None
    effective: bool
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_id(self.event_id, "event.event_id")
        require_id(self.batch_id, "event.batch_id")
        require_enum(self.kind, EventKind, "event.kind")
        if type(self.stamp) is not EventStamp:
            raise ContractError("event.stamp", "requires EventStamp")
        for key in ("root_id", "actor_id", "target_id", "score_owner_id", "crime_owner_id"):
            if getattr(self, key) is not None:
                require_id(getattr(self, key), f"event.{key}")
        require_bool(self.effective, "event.effective")
        if not isinstance(self.payload, Mapping):
            raise ContractError("event.payload", "requires an object")
        object.__setattr__(self, "payload", freeze_json(self.payload, "event.payload"))

    @property
    def root_scope(self) -> tuple[str, str]:
        """无根系统事件按同批去重，避免把所有root=None合成整局一次。"""
        return ("root", self.root_id) if self.root_id is not None else ("batch", self.batch_id)

    def to_dict(self) -> dict[str, Any]:
        """稳定的JSON事件边界，返回独立副本。"""
        return {"event_id": self.event_id, "kind": self.kind.value, "stamp": self.stamp.to_dict(),
                "root_id": self.root_id, "batch_id": self.batch_id, "actor_id": self.actor_id,
                "target_id": self.target_id, "score_owner_id": self.score_owner_id,
                "crime_owner_id": self.crime_owner_id, "effective": self.effective,
                "payload": thaw_json(self.payload)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> DomainEvent:
        """拒绝残缺旧事件或未知字段，显式恢复时间与事件枚举。"""
        data = checked_object(value, {"event_id", "kind", "stamp", "root_id", "batch_id",
                                      "actor_id", "target_id", "score_owner_id", "crime_owner_id",
                                      "effective", "payload"}, "event")
        stamp = checked_object(
            data["stamp"], {"global_round", "world_tick", "phase"}, "event.stamp"
        )
        try:
            return cls(data["event_id"], EventKind(data["kind"]),
                       EventStamp(stamp["global_round"], stamp["world_tick"],
                                  RoundPhase(stamp["phase"])),
                       data["root_id"], data["batch_id"], data["actor_id"], data["target_id"],
                       data["score_owner_id"], data["crime_owner_id"],
                       data["effective"], data["payload"])
        except (TypeError, ValueError) as exc:
            if isinstance(exc, ContractError):
                raise
            raise ContractError("event", "invalid enum value") from exc


def events_from_resolutions(records: Iterable[ResolutionRecord]) -> tuple[DomainEvent, ...]:
    """完整目标组转事件，拒绝半个AOE；不在这里执行伤害或判断赢家。"""
    items = tuple(records)
    if not items or any(type(item) is not ResolutionRecord for item in items):
        raise ContractError("resolutions", "requires a non-empty group of ResolutionRecord values")
    if len({item.resolution_id for item in items}) != len(items):
        raise ContractError("resolutions", "duplicate resolution IDs")
    groups: dict[str, list[ResolutionRecord]] = {}
    first = items[0]
    for item in items:
        if (item.effect.batch_id, item.effect.root_id, item.stamp) != (
            first.effect.batch_id, first.effect.root_id, first.stamp
        ):
            raise ContractError("resolutions", "atomic group must share batch, root and stamp")
        groups.setdefault(item.effect.effect_id, []).append(item)
    ordered: list[ResolutionRecord] = []
    for effect_id in sorted(groups):
        group = groups[effect_id]
        effect = group[0].effect
        if any(item.effect != effect for item in group):
            raise ContractError("resolutions.effect", "one effect ID has conflicting declarations")
        by_target = {item.target_id: item for item in group}
        if len(by_target) != len(group) or set(by_target) != set(effect.target_ids):
            raise ContractError(
                "resolutions.targets", "every declared target needs exactly one record"
            )
        ordered.extend(by_target[target] for target in effect.target_ids)

    result: list[DomainEvent] = []
    for item in ordered:
        effect = item.effect

        def emit(kind: EventKind, suffix: str, payload: dict[str, Any], effective: bool) -> None:
            result.append(DomainEvent(
                f"{item.resolution_id}/{suffix}", kind, item.stamp, effect.root_id, effect.batch_id,
                effect.actor_id, item.target_id, effect.score_owner_id, effect.crime_owner_id,
                effective, {"source_id": effect.source_id, "effect_id": effect.effect_id,
                            "resolution_id": item.resolution_id, **payload},
            ))

        if item.outcome is HitOutcome.GRAZE and effect.amount > 0:
            emit(EventKind.EVADED, "evaded", {"hp_loss": item.hp_loss}, True)
        if item.hp_loss:
            emit(EventKind.HP_DAMAGED, "hp", {"hp_loss": item.hp_loss,
                 "computed_hp_loss": item.computed_hp_loss, "damage_kind": effect.damage_kind.value,
                 "attribute": effect.attribute.value,
                 "grazed": item.outcome is HitOutcome.GRAZE}, True)
        for change in item.armor_changes:
            if change.before != change.after:
                emit(EventKind.ARMOR_WORN, f"armor_worn/{change.defense_id}", {
                    "defense_id": change.defense_id, "before": change.before, "after": change.after,
                    "ordinary_wear": change.ordinary_wear, "attached_wear": change.attached_wear,
                    "pure_wear": change.pure_wear,
                }, item.effective_hit)
            if change.broken:
                emit(EventKind.ARMOR_BROKEN, f"armor_broken/{change.defense_id}", {
                    "defense_id": change.defense_id,
                }, True)
        for change in item.barrier_changes:
            if change.after == 0:
                emit(EventKind.BARRIER_BROKEN, f"barrier_broken/{change.defense_id}", {
                    "defense_id": change.defense_id, "kind": change.kind.value,
                }, True)
        if item.applied_statuses:
            emit(EventKind.STATUS_APPLIED, "statuses", {"statuses": item.applied_statuses}, True)
        if item.death_outcome in (DeathOutcome.DEAD, DeathOutcome.ABSOLUTE_DEAD):
            emit(EventKind.ACTOR_DIED, "death", {
                "absolute": item.death_outcome is DeathOutcome.ABSOLUTE_DEAD,
            }, True)
        elif item.death_outcome in (DeathOutcome.MORPHED, DeathOutcome.RESURRECTED):
            emit(EventKind.LETHAL_REPLACED, "replacement",
                 {"outcome": item.death_outcome.value}, True)
    return tuple(result)


class BeneficiaryRole(str, Enum):
    """领取者身份显式区分施术者、焦点、战果归属与案底归属。"""

    ACTOR = "actor"
    TARGET = "target"
    SCORE_OWNER = "score_owner"
    CRIME_OWNER = "crime_owner"
    OBSERVER = "observer"


@dataclass(frozen=True)
class RewardRule:
    """由订阅适配器注册的额度规则；不记录玩家钱包或专属资源余额。"""

    rule_id: str
    event_kinds: frozenset[EventKind]
    role: BeneficiaryRole
    once_per_root: bool
    per_round_cap: int | None
    requires_effective: bool = True

    def __post_init__(self) -> None:
        require_id(self.rule_id, "reward.rule_id")
        require_enum(self.role, BeneficiaryRole, "reward.role")
        require_bool(self.once_per_root, "reward.once_per_root")
        require_bool(self.requires_effective, "reward.requires_effective")
        kinds = checked_enums(self.event_kinds, EventKind, "reward.event_kinds")
        if not kinds:
            raise ContractError("reward.event_kinds", "must be explicit")
        object.__setattr__(self, "event_kinds", kinds)
        if self.per_round_cap is not None:
            require_int(self.per_round_cap, "reward.per_round_cap")


@dataclass(frozen=True)
class AwardGrant:
    """本次允许入账的实际量；replay永远返回0，避免调用者重复支付。"""

    rule_id: str
    event_id: str
    beneficiary_id: str
    amount: int
    replayed: bool

    def __post_init__(self) -> None:
        for key in ("rule_id", "event_id", "beneficiary_id"):
            require_id(getattr(self, key), f"award.{key}")
        require_int(self.amount, "award.amount")
        require_bool(self.replayed, "award.replayed")
        if self.replayed and self.amount:
            raise ContractError("award", "replayed grants cannot authorize new payment")


def _stamp_order(stamp: EventStamp) -> tuple[int, int]:
    """只按实际轮和阶段排序；世界刻度不决定事件先后。"""
    return stamp.global_round, int(stamp.phase.value[1:])


class EventLedger:
    """单局事实/去重台账，由唯一GameState拥有；不运行订阅者回调。"""

    def __init__(self) -> None:
        self._events: dict[str, DomainEvent] = {}
        self._batches: dict[str, tuple[DomainEvent, ...]] = {}
        self._records: dict[str, tuple[ResolutionRecord, ...] | None] = {}
        self._rules: dict[str, RewardRule] = {}
        self._claims: dict[tuple[str, str, str], AwardGrant] = {}
        self._root_awards: set[tuple[str, str, tuple[str, str]]] = set()
        self._round_awards: dict[tuple[str, str, int], int] = {}
        self._last_stamp: EventStamp | None = None
        self._terminal: TerminalSnapshot | None = None

    @property
    def events(self) -> tuple[DomainEvent, ...]:
        """提交顺序的只读公开历史。"""
        return tuple(self._events.values())

    @property
    def claims(self) -> tuple[AwardGrant, ...]:
        """原始实际领取回执；零容量也有回执但不占正数额度。"""
        return tuple(self._claims.values())

    @property
    def terminal(self) -> TerminalSnapshot | None:
        """已保存的终局快照，不从后续展示状态重算。"""
        return self._terminal

    def commit_batch(self, batch_id: str, events: Iterable[DomainEvent]) -> tuple[DomainEvent, ...]:
        """全部检查通过才发布；同批同内容重放返回空，不重复通知。"""
        values = tuple(events)
        if not values:
            raise ContractError("batch.events", "empty generic batch is not a successful action")
        return self._commit(batch_id, values, None)

    def commit_resolutions(self, records: Iterable[ResolutionRecord]) -> tuple[DomainEvent, ...]:
        """伤害入口验证完整目标集合，免疫目标也必须带零结果记录。"""
        items = tuple(records)
        events = events_from_resolutions(items)
        ordered = tuple(sorted(items, key=lambda item: item.resolution_id))
        return self._commit(items[0].effect.batch_id, events, ordered)

    def _commit(
        self, batch_id: str, events: tuple[DomainEvent, ...],
        records: tuple[ResolutionRecord, ...] | None,
    ) -> tuple[DomainEvent, ...]:
        require_id(batch_id, "batch.batch_id")
        if any(type(event) is not DomainEvent for event in events):
            raise ContractError("batch.events", "requires DomainEvent values")
        if batch_id in self._batches:
            if self._batches[batch_id] == events and self._records[batch_id] == records:
                return ()
            raise ContractError("batch.batch_id", "replayed batch has conflicting content")
        if self._terminal is not None:
            raise ContractError("batch", "game is already terminal")
        if len({event.event_id for event in events}) != len(events):
            raise ContractError("batch.events", "duplicate event IDs")
        if any(event.event_id in self._events for event in events):
            raise ContractError("batch.events", "event ID already belongs to a committed batch")
        stamp = events[0].stamp if events else records[0].stamp
        root_id = events[0].root_id if events else records[0].effect.root_id
        for event in events:
            if (event.batch_id, event.root_id, event.stamp) != (batch_id, root_id, stamp):
                raise ContractError("batch.events", "batch/root/stamp must match")
        if self._last_stamp is not None and _stamp_order(stamp) < _stamp_order(self._last_stamp):
            raise ContractError("batch.stamp", "cannot append earlier facts after later ones")
        self._batches[batch_id] = events
        self._records[batch_id] = records
        self._events.update((event.event_id, event) for event in events)
        self._last_stamp = stamp
        return events

    def claim(
        self, rule: RewardRule, event_id: str, beneficiary_id: str, *,
        requested: int, available_room: int | None = None,
    ) -> AwardGrant:
        """按实际可入账量消费根/轮额度；余额上限由正式状态导出的room提供。"""
        if type(rule) is not RewardRule:
            raise ContractError("claim.rule", "requires RewardRule")
        require_id(event_id, "claim.event_id")
        require_id(beneficiary_id, "claim.beneficiary_id")
        require_int(requested, "claim.requested")
        if available_room is not None:
            require_int(available_room, "claim.available_room")
        if event_id not in self._events:
            raise ContractError("claim.event_id", "event has not been committed")
        existing_rule = self._rules.get(rule.rule_id)
        if existing_rule is not None and existing_rule != rule:
            raise ContractError("claim.rule_id", "cannot change a rule to reset its quota")
        key = (rule.rule_id, beneficiary_id, event_id)
        if key in self._claims:
            return AwardGrant(rule.rule_id, event_id, beneficiary_id, 0, True)
        if self._terminal is not None:
            raise ContractError("claim", "new awards cannot change a terminal snapshot")
        event = self._events[event_id]
        if self._last_stamp is not None and (
            event.stamp.global_round != self._last_stamp.global_round
        ):
            raise ContractError(
                "claim.event_id", "cannot collect a new award from an earlier round"
            )
        role_id = {
            BeneficiaryRole.ACTOR: event.actor_id,
            BeneficiaryRole.TARGET: event.target_id,
            BeneficiaryRole.SCORE_OWNER: event.score_owner_id,
            BeneficiaryRole.CRIME_OWNER: event.crime_owner_id,
        }.get(rule.role)
        eligible = event.kind in rule.event_kinds and (
            not rule.requires_effective or event.effective
        ) and (rule.role is BeneficiaryRole.OBSERVER or beneficiary_id == role_id)
        root_key = (rule.rule_id, beneficiary_id, event.root_scope)
        round_key = (rule.rule_id, beneficiary_id, event.stamp.global_round)
        amount = requested if eligible else 0
        if rule.once_per_root and root_key in self._root_awards:
            amount = 0
        if available_room is not None:
            amount = min(amount, available_room)
        if rule.per_round_cap is not None:
            used = self._round_awards.get(round_key, 0)
            amount = min(amount, max(0, rule.per_round_cap - used))
        grant = AwardGrant(rule.rule_id, event_id, beneficiary_id, amount, False)
        self._rules[rule.rule_id] = rule
        self._claims[key] = grant
        if amount:
            self._root_awards.add(root_key)
            self._round_awards[round_key] = self._round_awards.get(round_key, 0) + amount
        return grant

    def finalize(self, snapshot: TerminalSnapshot) -> bool:
        """一次定格；完全相同的重放幂等，其他终局不能覆盖已存成绩。"""
        if type(snapshot) is not TerminalSnapshot:
            raise ContractError("terminal", "requires TerminalSnapshot")
        if self._terminal is not None:
            if self._terminal == snapshot:
                return False
            raise ContractError("terminal", "terminal snapshot is already sealed")
        if self._last_stamp is not None and (
            _stamp_order(snapshot.stamp) < _stamp_order(self._last_stamp)
        ):
            raise ContractError("terminal.stamp", "snapshot predates committed facts")
        self._terminal = snapshot
        return True
