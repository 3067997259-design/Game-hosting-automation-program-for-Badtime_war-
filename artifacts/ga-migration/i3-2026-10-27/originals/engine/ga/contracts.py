"""GA 公共值对象：只表达已验证的选择、效果和结果，不持有可写GameState。

业务数值由调用者从GaConfig提供；本模块中的0/1仅用于结构、整数性和计数边界。
所有容器均复制为只读快照，JSON边界拒绝未知字段及非有限数值。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
import math
from types import MappingProxyType
from typing import Any


class ContractError(ValueError):
    """公共接口不符合约定；path定位到具体字段。"""

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        super().__init__(f"{path}: {reason}")


def require_id(value: str, path: str) -> None:
    """稳定ID不可为空，也不通过去空格偷偷改变其身份。"""
    if type(value) is not str or not value or value != value.strip():
        raise ContractError(path, "expected a non-empty, trimmed string ID")


def require_int(value: int, path: str, minimum: int = 0) -> None:
    """拒绝bool和浮点整数，避免资源/时钟被隐式转换。"""
    if type(value) is not int or value < minimum:
        raise ContractError(path, f"expected an integer >= {minimum}")


def require_bool(value: bool, path: str) -> None:
    """布尔语义只接受真正的bool。"""
    if type(value) is not bool:
        raise ContractError(path, "expected bool")


def require_enum(value: Enum, enum_type: type[Enum], path: str) -> None:
    """进程内接口使用枚举；文本转换仅在显式JSON入口发生。"""
    if type(value) is not enum_type:
        raise ContractError(path, f"expected {enum_type.__name__}")


def freeze_json(value: Any, path: str = "payload") -> Any:
    """复制JSON值为递归只读对象；不接受任意Python对象或NaN。"""
    if isinstance(value, Mapping):
        frozen = {}
        for key, item in value.items():
            if type(key) is not str:
                raise ContractError(path, "JSON object keys must be strings")
            frozen[key] = freeze_json(item, f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, (tuple, list)):
        return tuple(freeze_json(item, f"{path}[{i}]") for i, item in enumerate(value))
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ContractError(path, "expected a finite JSON value")


def thaw_json(value: Any) -> Any:
    """导出独立可序列化副本，不暴露内部容器。"""
    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return value


def checked_ids(values: Any, path: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    """保持声明顺序并拒绝重复目标；不把字符串当字符序列。"""
    if not isinstance(values, (tuple, list)):
        raise ContractError(path, "expected a list or tuple of IDs")
    result = tuple(values)
    if not result and not allow_empty:
        raise ContractError(path, "must not be empty")
    for index, value in enumerate(result):
        require_id(value, f"{path}[{index}]")
    if len(set(result)) != len(result):
        raise ContractError(path, "duplicate IDs")
    return result


def checked_object(value: Any, keys: set[str], path: str) -> Mapping[str, Any]:
    """JSON输入必填键和允许键完全一致，不静默兼容旧协议。"""
    if not isinstance(value, Mapping):
        raise ContractError(path, "expected an object")
    if any(type(key) is not str for key in value):
        raise ContractError(path, "object keys must be strings")
    missing = keys - value.keys()
    unknown = value.keys() - keys
    if missing or unknown:
        raise ContractError(path, f"missing={sorted(missing)}, unknown={sorted(unknown)}")
    return value


def checked_values(values: Any, item_type: type, path: str) -> tuple[Any, ...]:
    """顺序有意义的值对象集合只接受明确序列，不消费任意迭代器。"""
    if not isinstance(values, (tuple, list)):
        raise ContractError(path, "expected a list or tuple")
    result = tuple(values)
    if any(type(item) is not item_type for item in result):
        raise ContractError(path, f"requires {item_type.__name__} values")
    return result


def checked_enums(values: Any, enum_type: type[Enum], path: str) -> frozenset[Any]:
    """枚举集合先验类型再冻结，坏容器不会泄漏Python内部异常。"""
    if not isinstance(values, (tuple, list, set, frozenset)):
        raise ContractError(path, "expected an enum collection")
    for value in values:
        require_enum(value, enum_type, path)
    return frozenset(values)


class RoundPhase(str, Enum):
    """实际全局轮的五阶段，不代表世界刻度。"""

    R0 = "R0"
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"
    R4 = "R4"


class TurnStage(str, Enum):
    """玩家回合的阶段。"""

    T0 = "T0"
    T1 = "T1"
    T2 = "T2"


class ActionVerb(str, Enum):
    """历史倒流只读取标准T1的这四类动词。"""

    MOVE = "move"
    ATTACK = "attack"
    INTERACT = "interact"
    SPECIAL = "special"


class Performance(str, Enum):
    """演出入口身份与是否占主行动是两件事。"""

    NONE = "none"
    INSTANT = "instant"
    PUBLIC = "public"
    MAJOR_INSTANT = "major_instant"


class EffectKind(str, Enum):
    """效果类别；声明类别不等于已经修改状态。"""

    DAMAGE = "damage"
    WEAR = "wear"
    HEAL = "heal"
    STATUS = "status"
    RESOURCE = "resource"
    MOVE = "move"
    BARRIER = "barrier"


class DamageKind(str, Enum):
    """直达、纯磨甲和直接死亡不能互相推导。"""

    NONE = "none"
    NORMAL = "normal"
    DIRECT = "direct"
    PURE_WEAR = "pure_wear"
    EXECUTION = "execution"


class DamageAttribute(str, Enum):
    """伤害属性；无属性是E=0而不是直达伤害。"""

    NORMAL = "normal"
    TECH = "tech"
    MAGIC = "magic"
    MYSTERIOUS = "mysterious"
    UNATTRIBUTED = "unattributed"


class TargetScope(str, Enum):
    """目标范围仍需实际位置、视野和隔离检查。"""

    LOCAL_SINGLE = "local_single"
    RANGED_SINGLE = "ranged_single"
    LOCAL_AREA = "local_area"
    GLOBAL_AREA = "global_area"


class HitPolicy(str, Enum):
    """命中权限不改变目标合法性。"""

    ROLL = "roll"
    CERTAIN = "certain"


class BarrierPolicy(str, Enum):
    """绕过数值屏障不包含替身或绝对免疫。"""

    RESPECT = "respect"
    BYPASS_NUMERIC = "bypass_numeric"


class IsolationPolicy(str, Enum):
    """当前GA来源均尊重隔离，类型不提供一个便捷的全图越界开关。"""

    RESPECT = "respect"


class DeathPolicy(str, Enum):
    """只有致死或明文直接死亡入口才执行绝对死亡。"""

    NORMAL = "normal"
    ABSOLUTE_IF_LETHAL = "absolute_if_lethal"
    ABSOLUTE_TRIGGER = "absolute_trigger"


class DefenseKind(str, Enum):
    """普通甲、有限池和破碎整击承接各有独立身份。"""

    ARMOR = "armor"
    FINITE_BARRIER = "finite_barrier"
    BREAKING_HIT_BARRIER = "breaking_hit_barrier"
    HALO = "halo"


class HitOutcome(str, Enum):
    """擦伤与实际受伤可以同时成立。"""

    HIT = "hit"
    GRAZE = "graze"
    BLOCKED = "blocked"
    IMMUNE = "immune"
    SUBSTITUTED = "substituted"


class DeathOutcome(str, Enum):
    """一次濒死最多有一个最终替代或死亡结果。"""

    NONE = "none"
    MORPHED = "morphed"
    RESURRECTED = "resurrected"
    DEAD = "dead"
    ABSOLUTE_DEAD = "absolute_dead"


@dataclass(frozen=True)
class EventStamp:
    """同时携带实际轮与世界刻度，状态计时只消费实际轮/槽。"""

    global_round: int
    world_tick: int
    phase: RoundPhase

    def __post_init__(self) -> None:
        require_int(self.global_round, "stamp.global_round", 1)
        require_int(self.world_tick, "stamp.world_tick", 1)
        require_enum(self.phase, RoundPhase, "stamp.phase")

    def to_dict(self) -> dict[str, Any]:
        """公开事件的稳定JSON时间字段。"""
        return {"global_round": self.global_round, "world_tick": self.world_tick,
                "phase": self.phase.value}


@dataclass(frozen=True)
class ActionRequest:
    """控制器提交的选择；没有费用支付或可写状态。"""

    actor_id: str
    grant_id: str
    root_id: str
    stage: TurnStage
    verb: ActionVerb
    operation: str
    target_ids: tuple[str, ...] = ()
    parameters: Mapping[str, Any] = field(default_factory=dict)
    performance: Performance = Performance.NONE

    def __post_init__(self) -> None:
        for key in ("actor_id", "grant_id", "root_id", "operation"):
            require_id(getattr(self, key), f"request.{key}")
        require_enum(self.stage, TurnStage, "request.stage")
        require_enum(self.verb, ActionVerb, "request.verb")
        require_enum(self.performance, Performance, "request.performance")
        object.__setattr__(self, "target_ids", checked_ids(self.target_ids, "request.target_ids"))
        if not isinstance(self.parameters, Mapping):
            raise ContractError("request.parameters", "expected an object")
        object.__setattr__(self, "parameters", freeze_json(self.parameters, "request.parameters"))

    def to_dict(self) -> dict[str, Any]:
        """跨人类/脚本/AI/RL边界的同一请求语义。"""
        return {"actor_id": self.actor_id, "grant_id": self.grant_id, "root_id": self.root_id,
                "stage": self.stage.value, "verb": self.verb.value, "operation": self.operation,
                "target_ids": list(self.target_ids), "parameters": thaw_json(self.parameters),
                "performance": self.performance.value}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ActionRequest:
        """显式解码，不用旧字段或缺省grant补齐请求。"""
        data = checked_object(value, {"actor_id", "grant_id", "root_id", "stage", "verb",
                                      "operation", "target_ids", "parameters", "performance"},
                              "request")
        try:
            return cls(data["actor_id"], data["grant_id"], data["root_id"],
                       TurnStage(data["stage"]),
                       ActionVerb(data["verb"]), data["operation"], data["target_ids"],
                       data["parameters"], Performance(data["performance"]))
        except (TypeError, ValueError) as exc:
            if isinstance(exc, ContractError):
                raise
            raise ContractError("request", "invalid enum value") from exc


@dataclass(frozen=True)
class CostEntry:
    """预检费用项；payer可与行动者不同，支持真实钱包扣款而非负债。"""

    payer_id: str
    account: str
    amount: int
    minimum_remaining: int = 0

    def __post_init__(self) -> None:
        require_id(self.payer_id, "cost.payer_id")
        require_id(self.account, "cost.account")
        require_int(self.amount, "cost.amount")
        require_int(self.minimum_remaining, "cost.minimum_remaining")


@dataclass(frozen=True)
class RandomRequirement:
    """只登记所需随机抽样；预检本身不得消耗随机流。"""

    key: str
    upper_bound: int
    draws: int

    def __post_init__(self) -> None:
        require_id(self.key, "random.key")
        require_int(self.upper_bound, "random.upper_bound", 1)
        require_int(self.draws, "random.draws", 1)


@dataclass(frozen=True)
class ActionPlan:
    """单步预检结果；执行适配器需复核snapshot_id后才能消费费用与阶段。"""

    request: ActionRequest
    snapshot_id: str
    accepted: bool
    costs: tuple[CostEntry, ...]
    phase_claim: TurnStage | None
    random_requirements: tuple[RandomRequirement, ...]
    issues: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.request) is not ActionRequest:
            raise ContractError("plan.request", "expected ActionRequest")
        require_id(self.snapshot_id, "plan.snapshot_id")
        require_bool(self.accepted, "plan.accepted")
        object.__setattr__(self, "costs", checked_values(self.costs, CostEntry, "plan.costs"))
        object.__setattr__(self, "random_requirements", checked_values(
            self.random_requirements, RandomRequirement, "plan.random_requirements"
        ))
        object.__setattr__(self, "issues", checked_ids(self.issues, "plan.issues"))
        if self.phase_claim is not None:
            require_enum(self.phase_claim, TurnStage, "plan.phase_claim")
            if self.phase_claim is not self.request.stage:
                raise ContractError("plan.phase_claim", "must match the request stage")
        if self.accepted == bool(self.issues):
            raise ContractError("plan.accepted", "accepted plans have no issues; rejected plans do")
        if not self.accepted and (self.costs or self.phase_claim or self.random_requirements):
            raise ContractError("plan", "rejected prechecks cannot authorize costs, phase or RNG")

    def assert_current(self, snapshot_id: str) -> None:
        """资源/目标状态变化后旧计划失效，必须重新预检。"""
        if snapshot_id != self.snapshot_id:
            raise ContractError("plan.snapshot_id", "stale precheck; revalidate before execution")
        if not self.accepted:
            raise ContractError("plan.accepted", "cannot execute a rejected plan")


@dataclass(frozen=True)
class EffectSpec:
    """效果身份和权限；source显示名不能隐式授予穿盾、越界或绝对死亡。"""

    effect_id: str
    source_id: str
    root_id: str | None
    batch_id: str
    actor_id: str | None
    score_owner_id: str | None
    crime_owner_id: str | None
    target_ids: tuple[str, ...]
    kind: EffectKind
    damage_kind: DamageKind
    amount: int
    attribute: DamageAttribute
    scope: TargetScope
    hit_policy: HitPolicy
    barrier_policy: BarrierPolicy
    isolation_policy: IsolationPolicy
    death_policy: DeathPolicy
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for key in ("effect_id", "source_id", "batch_id"):
            require_id(getattr(self, key), f"effect.{key}")
        for key in ("root_id", "actor_id", "score_owner_id", "crime_owner_id"):
            if getattr(self, key) is not None:
                require_id(getattr(self, key), f"effect.{key}")
        for key, enum_type in (("kind", EffectKind), ("damage_kind", DamageKind),
                               ("attribute", DamageAttribute), ("scope", TargetScope),
                               ("hit_policy", HitPolicy), ("barrier_policy", BarrierPolicy),
                               ("isolation_policy", IsolationPolicy),
                               ("death_policy", DeathPolicy)):
            require_enum(getattr(self, key), enum_type, f"effect.{key}")
        require_int(self.amount, "effect.amount")
        object.__setattr__(self, "target_ids", checked_ids(
            self.target_ids, "effect.target_ids", allow_empty=False
        ))
        if self.scope in (TargetScope.LOCAL_SINGLE, TargetScope.RANGED_SINGLE):
            if len(self.target_ids) != 1:
                raise ContractError("effect.target_ids", "single-target effect requires one target")
        if self.kind is EffectKind.DAMAGE:
            if self.damage_kind not in (DamageKind.NORMAL, DamageKind.DIRECT, DamageKind.EXECUTION):
                raise ContractError("effect.damage_kind", "damage requires an explicit damage kind")
        elif self.kind is EffectKind.WEAR:
            if self.damage_kind is not DamageKind.PURE_WEAR:
                raise ContractError("effect.damage_kind", "wear is not HP damage")
        elif self.damage_kind is not DamageKind.NONE:
            raise ContractError("effect.damage_kind", "non-damage effects use NONE")
        if self.scope in (TargetScope.LOCAL_AREA, TargetScope.GLOBAL_AREA) or self.damage_kind in (
            DamageKind.PURE_WEAR, DamageKind.EXECUTION
        ):
            if self.hit_policy is not HitPolicy.CERTAIN:
                raise ContractError(
                    "effect.hit_policy", "area/wear/execution effects do not roll evasion"
                )
        if self.damage_kind is DamageKind.EXECUTION:
            if self.amount:
                raise ContractError("effect.amount", "execution is not a numeric damage packet")
            if self.death_policy is not DeathPolicy.ABSOLUTE_TRIGGER:
                raise ContractError(
                    "effect.death_policy", "execution requires explicit death policy"
                )
        elif self.death_policy is DeathPolicy.ABSOLUTE_TRIGGER:
            raise ContractError("effect.death_policy", "direct death is not a damage multiplier")
        if self.kind is not EffectKind.DAMAGE and self.death_policy is not DeathPolicy.NORMAL:
            raise ContractError("effect.death_policy", "non-damage cannot imply absolute death")
        if not isinstance(self.parameters, Mapping):
            raise ContractError("effect.parameters", "expected an object")
        object.__setattr__(self, "parameters", freeze_json(self.parameters, "effect.parameters"))


@dataclass(frozen=True)
class DefenseSpec:
    """一次承伤时的防御快照；状态变更仍由正式状态源处理。"""

    defense_id: str
    kind: DefenseKind
    durability: int
    max_durability: int
    accepted_damage_kinds: frozenset[DamageKind]
    accepted_scopes: frozenset[TargetScope]
    attribute_defense: Mapping[DamageAttribute, int] = field(default_factory=dict)
    wear_price: int | None = None

    def __post_init__(self) -> None:
        require_id(self.defense_id, "defense.defense_id")
        require_enum(self.kind, DefenseKind, "defense.kind")
        require_int(self.durability, "defense.durability")
        require_int(self.max_durability, "defense.max_durability", 1)
        if self.durability > self.max_durability:
            raise ContractError("defense.durability", "cannot exceed maximum")
        for key, enum_type in (("accepted_damage_kinds", DamageKind),
                               ("accepted_scopes", TargetScope)):
            values = checked_enums(getattr(self, key), enum_type, f"defense.{key}")
            if not values:
                raise ContractError(f"defense.{key}", "must be explicit and non-empty")
            object.__setattr__(self, key, values)
        ordinary_kinds = {DamageKind.NORMAL, DamageKind.DIRECT, DamageKind.PURE_WEAR}
        if self.accepted_damage_kinds - ordinary_kinds:
            raise ContractError("defense.accepted_damage_kinds", "defense cannot stop direct death")
        if not isinstance(self.attribute_defense, Mapping):
            raise ContractError("defense.attribute_defense", "requires an object")
        defenses = dict(self.attribute_defense)
        if self.kind is DefenseKind.ARMOR:
            expected = {DamageAttribute.NORMAL, DamageAttribute.TECH, DamageAttribute.MAGIC}
            if set(defenses) != expected or any(
                type(key) is not DamageAttribute for key in defenses
            ):
                raise ContractError(
                    "defense.attribute_defense", "requires all three base attributes"
                )
            if self.wear_price is None:
                raise ContractError("defense.wear_price", "armor requires a billing price")
            require_int(self.wear_price, "defense.wear_price", 1)
            if self.accepted_damage_kinds - {DamageKind.NORMAL, DamageKind.PURE_WEAR}:
                raise ContractError(
                    "defense.accepted_damage_kinds", "armor cannot reduce direct damage"
                )
        elif defenses or self.wear_price is not None:
            raise ContractError("defense", "finite pools do not use ordinary armor defense billing")
        for key, value in defenses.items():
            require_int(value, f"defense.attribute_defense.{key.value}")
        object.__setattr__(self, "attribute_defense", MappingProxyType(defenses))

    @property
    def protects_breaking_hit(self) -> bool:
        """只有明文主动防护类型具有破碎当击完整承接。"""
        return self.kind is DefenseKind.BREAKING_HIT_BARRIER


@dataclass(frozen=True)
class ArmorChange:
    """普通减免计费、附加磨甲、纯磨甲分账，禁止一击多甲接力。"""

    defense_id: str
    before: int
    after: int
    ordinary_wear: int = 0
    attached_wear: int = 0
    pure_wear: int = 0

    def __post_init__(self) -> None:
        require_id(self.defense_id, "armor_change.defense_id")
        require_int(self.before, "armor_change.before", 1)
        for key in ("after", "ordinary_wear", "attached_wear", "pure_wear"):
            require_int(getattr(self, key), f"armor_change.{key}")
        if self.before - self.after != self.ordinary_wear + self.attached_wear + self.pure_wear:
            raise ContractError("armor_change", "durability must conserve the three wear accounts")
        if self.pure_wear and (self.ordinary_wear or self.attached_wear):
            raise ContractError("armor_change", "independent wear is not an attached attack")

    @property
    def broken(self) -> bool:
        """只有耐久实际归零才产生普通破甲。"""
        return self.after == 0


@dataclass(frozen=True)
class BarrierChange:
    """实际吸收量与耐久支出分开，支持残盾整击拦截。"""

    defense_id: str
    kind: DefenseKind
    before: int
    after: int
    absorbed: int

    def __post_init__(self) -> None:
        require_id(self.defense_id, "barrier_change.defense_id")
        require_enum(self.kind, DefenseKind, "barrier_change.kind")
        require_int(self.before, "barrier_change.before", 1)
        require_int(self.after, "barrier_change.after")
        require_int(self.absorbed, "barrier_change.absorbed", 1)
        if self.kind is DefenseKind.ARMOR:
            raise ContractError("barrier_change.kind", "ordinary armor is not a second HP pool")
        expected = min(self.before, self.absorbed)
        if self.before - self.after != expected:
            raise ContractError("barrier_change", "durability payment does not match absorption")
        if self.absorbed > self.before and self.kind is not DefenseKind.BREAKING_HIT_BARRIER:
            raise ContractError(
                "barrier_change.absorbed", "finite pool cannot swallow excess damage"
            )


@dataclass(frozen=True)
class ResolutionRecord:
    """一个目标的一段已结算攻击；不保存复活后的可写生命状态。"""

    resolution_id: str
    effect: EffectSpec
    stamp: EventStamp
    target_id: str
    outcome: HitOutcome
    remaining_damage: int
    computed_hp_loss: int
    hp_before: int
    hp_loss: int
    armor_changes: tuple[ArmorChange, ...] = ()
    barrier_changes: tuple[BarrierChange, ...] = ()
    applied_statuses: tuple[str, ...] = ()
    death_outcome: DeathOutcome = DeathOutcome.NONE

    def __post_init__(self) -> None:
        require_id(self.resolution_id, "resolution.resolution_id")
        if type(self.effect) is not EffectSpec or type(self.stamp) is not EventStamp:
            raise ContractError("resolution", "requires EffectSpec and EventStamp")
        require_id(self.target_id, "resolution.target_id")
        if self.target_id not in self.effect.target_ids:
            raise ContractError("resolution.target_id", "target was not declared by the effect")
        require_enum(self.outcome, HitOutcome, "resolution.outcome")
        require_enum(self.death_outcome, DeathOutcome, "resolution.death_outcome")
        for key in ("remaining_damage", "computed_hp_loss", "hp_loss"):
            require_int(getattr(self, key), f"resolution.{key}")
        require_int(self.hp_before, "resolution.hp_before", 1)
        if self.effect.kind not in (EffectKind.DAMAGE, EffectKind.WEAR):
            raise ContractError("resolution.effect", "combat records require damage or wear")
        if self.remaining_damage > self.effect.amount:
            raise ContractError("resolution.remaining_damage", "cannot recreate intercepted damage")
        if self.hp_loss != min(self.hp_before, self.computed_hp_loss):
            raise ContractError("resolution.hp_loss", "actual HP loss must exclude overkill")
        for key, item_type in (("armor_changes", ArmorChange), ("barrier_changes", BarrierChange)):
            values = checked_values(getattr(self, key), item_type, f"resolution.{key}")
            if len({item.defense_id for item in values}) != len(values):
                raise ContractError(f"resolution.{key}", "duplicate defense IDs")
            object.__setattr__(self, key, values)
        if len(self.armor_changes) > 1:
            raise ContractError("resolution.armor_changes", "one hit cannot relay between armor")
        absorbed = sum(item.absorbed for item in self.barrier_changes)
        if absorbed > self.effect.amount - self.remaining_damage:
            raise ContractError(
                "resolution.barrier_changes", "absorption exceeds intercepted amount"
            )
        if self.effect.kind is EffectKind.DAMAGE:
            if self.outcome not in (HitOutcome.IMMUNE, HitOutcome.SUBSTITUTED):
                if absorbed != self.effect.amount - self.remaining_damage:
                    raise ContractError(
                        "resolution.barrier_changes", "interception needs a receipt"
                    )
            if self.effect.damage_kind is not DamageKind.EXECUTION:
                if not self.remaining_damage and self.computed_hp_loss:
                    raise ContractError("resolution.computed_hp_loss", "full block has no HP floor")
        object.__setattr__(self, "applied_statuses", checked_ids(
            self.applied_statuses, "resolution.applied_statuses"
        ))
        if self.outcome in (HitOutcome.BLOCKED, HitOutcome.IMMUNE, HitOutcome.SUBSTITUTED):
            if self.remaining_damage or self.computed_hp_loss or self.armor_changes:
                raise ContractError(
                    "resolution.outcome", "full interception cannot damage the body"
                )
            if self.applied_statuses:
                raise ContractError(
                    "resolution.applied_statuses", "intercepted statuses cannot land"
                )
        if self.outcome is HitOutcome.GRAZE and (self.armor_changes or self.applied_statuses):
            raise ContractError(
                "resolution.outcome", "graze cannot wear armor or apply hit statuses"
            )
        if self.outcome is HitOutcome.GRAZE and not self.effect.amount:
            raise ContractError("resolution.outcome", "zero-output effects are not evasion events")
        if self.outcome is HitOutcome.GRAZE and self.effect.hit_policy is not HitPolicy.ROLL:
            raise ContractError(
                "resolution.outcome", "certain effects cannot create evasion events"
            )
        if self.effect.damage_kind is DamageKind.PURE_WEAR:
            if self.hp_loss or self.computed_hp_loss or self.remaining_damage:
                raise ContractError("resolution", "pure wear never falls back to HP damage")
            if any(item.ordinary_wear or item.attached_wear for item in self.armor_changes):
                raise ContractError("resolution.armor_changes", "pure wear uses its own account")
            if self.barrier_changes and self.armor_changes:
                raise ContractError(
                    "resolution.armor_changes", "intercepted wear cannot reach armor"
                )
            if sum(item.pure_wear for item in self.armor_changes) > self.effect.amount:
                raise ContractError("resolution.armor_changes", "wear exceeds its declared amount")
        elif any(item.pure_wear for item in self.armor_changes):
            raise ContractError(
                "resolution.armor_changes", "normal attacks cannot masquerade as wear"
            )
        if self.effect.damage_kind is DamageKind.DIRECT:
            if any(item.ordinary_wear for item in self.armor_changes):
                raise ContractError("resolution.armor_changes", "direct damage skips armor billing")
        if self.effect.kind is EffectKind.DAMAGE and not self.effect.amount:
            if self.effect.damage_kind is not DamageKind.EXECUTION and self.armor_changes:
                raise ContractError(
                    "resolution.armor_changes", "zero-output damage is not pure wear"
                )
        lethal = self.hp_loss == self.hp_before
        if lethal == (self.death_outcome is DeathOutcome.NONE):
            raise ContractError("resolution.death_outcome", "lethal damage needs one gate outcome")
        if self.effect.death_policy is not DeathPolicy.NORMAL and lethal:
            if self.death_outcome is not DeathOutcome.ABSOLUTE_DEAD:
                raise ContractError("resolution.death_outcome", "absolute lethal skips replacement")
        if self.death_outcome is DeathOutcome.ABSOLUTE_DEAD:
            if self.effect.death_policy is DeathPolicy.NORMAL:
                raise ContractError(
                    "resolution.death_outcome", "absolute death needs explicit policy"
                )

    @property
    def effective_hit(self) -> bool:
        """实际受伤或普通甲/屏障破碎才算有效伤害；擦伤仍可能受伤。"""
        return self.hp_loss > 0 or any(item.broken for item in self.armor_changes) or any(
            item.after == 0 for item in self.barrier_changes
        )


class TimerUnit(str, Enum):
    """轮制、任意槽制和必须标准槽三种计时不可混用。"""

    FUTURE_R4 = "future_r4"
    SLOT = "slot"
    STANDARD_SLOT = "standard_slot"


@dataclass(frozen=True)
class TimedEffect:
    """不可变计时状态，由GameState持有返回的新值；不读取世界时钟。"""

    effect_id: str
    target_id: str
    created_round: int
    remaining: int
    unit: TimerUnit
    tick_on_creation: bool = False
    last_r4: int | None = None
    consumed_slots: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_id(self.effect_id, "timer.effect_id")
        require_id(self.target_id, "timer.target_id")
        require_int(self.created_round, "timer.created_round", 1)
        require_int(self.remaining, "timer.remaining")
        require_enum(self.unit, TimerUnit, "timer.unit")
        require_bool(self.tick_on_creation, "timer.tick_on_creation")
        if self.last_r4 is not None:
            require_int(self.last_r4, "timer.last_r4", self.created_round)
        object.__setattr__(self, "consumed_slots", checked_ids(
            self.consumed_slots, "timer.consumed_slots"
        ))
        if self.unit is TimerUnit.FUTURE_R4 and self.consumed_slots:
            raise ContractError("timer.consumed_slots", "round timer cannot also consume slots")
        if self.unit is not TimerUnit.FUTURE_R4 and self.last_r4 is not None:
            raise ContractError("timer.last_r4", "slot timer cannot also consume rounds")

    def on_r4(self, global_round: int) -> TimedEffect:
        """同轮重放不重复扣tick；建立轮默认跳过第一段。"""
        require_int(global_round, "timer.global_round", self.created_round)
        first = self.created_round + (0 if self.tick_on_creation else 1)
        if self.unit is not TimerUnit.FUTURE_R4 or not self.remaining or global_round < first:
            return self
        if self.last_r4 is not None and global_round <= self.last_r4:
            return self
        return replace(self, remaining=self.remaining - 1, last_r4=global_round)

    def on_slot(self, slot_id: str, *, standard: bool) -> TimedEffect:
        """额外槽不能代缴自我怀疑等标准槽债。"""
        require_id(slot_id, "timer.slot_id")
        require_bool(standard, "timer.standard")
        if self.unit is TimerUnit.FUTURE_R4 or not self.remaining or slot_id in self.consumed_slots:
            return self
        if self.unit is TimerUnit.STANDARD_SLOT and not standard:
            return self
        return replace(self, remaining=self.remaining - 1,
                       consumed_slots=(*self.consumed_slots, slot_id))


class ChoiceState(str, Enum):
    """未回答与拒绝是不同状态。"""

    PENDING = "pending"
    RESOLVED = "resolved"
    EXPIRED = "expired"


@dataclass(frozen=True)
class ChoiceWindow:
    """受赠响应窗口；远程处理和超时从首次真实可选T0之后才开放。"""

    window_id: str
    creator_id: str
    recipient_id: str
    options: tuple[str, ...]
    remaining_ticks: int | None
    allow_remote_resolution: bool = False
    requires_t0_opportunity: bool = False
    first_opportunity_round: int | None = None
    last_tick_round: int | None = None
    state: ChoiceState = ChoiceState.PENDING
    selected_option: str | None = None
    resolved_by: str | None = None

    def __post_init__(self) -> None:
        for key in ("window_id", "creator_id", "recipient_id"):
            require_id(getattr(self, key), f"choice.{key}")
        object.__setattr__(self, "options", checked_ids(
            self.options, "choice.options", allow_empty=False
        ))
        require_bool(self.allow_remote_resolution, "choice.allow_remote_resolution")
        require_bool(self.requires_t0_opportunity, "choice.requires_t0_opportunity")
        require_enum(self.state, ChoiceState, "choice.state")
        for key in ("remaining_ticks", "first_opportunity_round", "last_tick_round"):
            if getattr(self, key) is not None:
                minimum = 0 if key == "remaining_ticks" else 1
                require_int(getattr(self, key), f"choice.{key}", minimum)
        if self.state is ChoiceState.RESOLVED:
            if self.selected_option not in self.options or self.resolved_by is None:
                raise ContractError("choice", "resolved choice needs option and responder")
            require_id(self.resolved_by, "choice.resolved_by")
            if self.resolved_by != self.recipient_id:
                if self.resolved_by != self.creator_id or not self.allow_remote_resolution:
                    raise ContractError("choice.resolved_by", "responder is not authorized")
                if self.first_opportunity_round is None:
                    raise ContractError("choice", "remote resolution requires prior opportunity")
            if self.requires_t0_opportunity and self.first_opportunity_round is None:
                raise ContractError("choice", "this choice requires an actual T0 opportunity")
        elif self.selected_option is not None or self.resolved_by is not None:
            raise ContractError("choice", "pending/expired choice cannot imply acceptance")
        if self.last_tick_round is not None:
            if self.first_opportunity_round is None or (
                self.last_tick_round <= self.first_opportunity_round
            ):
                raise ContractError("choice.last_tick_round", "must follow the first opportunity")
        if self.state is ChoiceState.PENDING and self.remaining_ticks == 0:
            raise ContractError("choice.remaining_ticks", "zero remaining ticks must be expired")
        if self.state is ChoiceState.EXPIRED and (
            self.remaining_ticks != 0 or self.last_tick_round is None
        ):
            raise ContractError("choice.state", "expiry requires actual elapsed opportunity ticks")

    def offer_t0(self, global_round: int, *, selectable: bool) -> ChoiceWindow:
        """控制剥夺的T0不算拒拆；重复通知不重置期限。"""
        require_int(global_round, "choice.global_round", 1)
        require_bool(selectable, "choice.selectable")
        if not selectable or self.first_opportunity_round is not None:
            return self
        if self.state is not ChoiceState.PENDING:
            return self
        return replace(self, first_opportunity_round=global_round)

    def resolve(self, option: str, *, actor_id: str) -> ChoiceWindow:
        """接受/拒绝均须主动提交；他人代拆需要显式授权和先前机会。"""
        require_id(actor_id, "choice.actor_id")
        require_id(option, "choice.option")
        if option not in self.options:
            raise ContractError("choice.option", "not an offered option")
        if self.state is ChoiceState.RESOLVED:
            if self.selected_option == option and self.resolved_by == actor_id:
                return self
            raise ContractError("choice.state", "choice already resolved differently")
        if self.state is not ChoiceState.PENDING:
            raise ContractError("choice.state", "expired choice cannot be resolved")
        if self.requires_t0_opportunity and self.first_opportunity_round is None:
            raise ContractError("choice.first_opportunity_round", "no selectable T0 yet")
        if actor_id != self.recipient_id:
            if actor_id != self.creator_id or not self.allow_remote_resolution:
                raise ContractError("choice.actor_id", "not authorized to resolve")
            if self.first_opportunity_round is None:
                raise ContractError("choice.first_opportunity_round", "recipient must act first")
        return replace(self, state=ChoiceState.RESOLVED,
                       selected_option=option, resolved_by=actor_id)

    def on_r4(self, global_round: int) -> ChoiceWindow:
        """机会当轮不tick；超时仅标记待兜底，不自动接受或抽奖。"""
        require_int(global_round, "choice.global_round", 1)
        if self.state is not ChoiceState.PENDING or self.remaining_ticks is None:
            return self
        if self.first_opportunity_round is None or global_round <= self.first_opportunity_round:
            return self
        if self.last_tick_round is not None and global_round <= self.last_tick_round:
            return self
        left = max(0, self.remaining_ticks - 1)
        return replace(self, remaining_ticks=left, last_tick_round=global_round,
                       state=ChoiceState.EXPIRED if left == 0 else ChoiceState.PENDING)


class TerminalReason(str, Enum):
    """终局快照的来源，不把回合兜底当正常节奏通过。"""

    LAST_SURVIVOR = "last_survivor"
    ALL_DEAD = "all_dead"
    REVERSAL_CLOSURE = "reversal_closure"
    ROUND_LIMIT = "round_limit"


@dataclass(frozen=True)
class PlayerResult:
    """终局逐人账目；章节锁定，PP只保存实际余额。"""

    kills: int
    hp_damage: int
    armor_breaks: int
    chapter_ids: tuple[str, ...]
    pp_balance: int
    total_score: int

    def __post_init__(self) -> None:
        for key in ("kills", "hp_damage", "armor_breaks", "pp_balance", "total_score"):
            require_int(getattr(self, key), f"player_result.{key}")
        object.__setattr__(self, "chapter_ids", checked_ids(
            self.chapter_ids, "player_result.chapter_ids"
        ))

    def to_dict(self) -> dict[str, Any]:
        """独立的终局账目JSON。"""
        return {"kills": self.kills, "hp_damage": self.hp_damage, "armor_breaks": self.armor_breaks,
                "chapter_ids": list(self.chapter_ids), "pp_balance": self.pp_balance,
                "total_score": self.total_score}


@dataclass(frozen=True)
class TerminalSnapshot:
    """展示重置前保存的成绩；事件账本只允许同一局定格一次。"""

    snapshot_id: str
    stamp: EventStamp
    reason: TerminalReason
    survivor_ids: tuple[str, ...]
    results: Mapping[str, PlayerResult]
    ruleset: str
    schema_version: str
    parameter_fingerprint: str
    seed: int

    def __post_init__(self) -> None:
        for key in ("snapshot_id", "ruleset", "schema_version", "parameter_fingerprint"):
            require_id(getattr(self, key), f"terminal.{key}")
        if len(self.parameter_fingerprint) != 64 or any(
            char not in "0123456789abcdef" for char in self.parameter_fingerprint
        ):
            raise ContractError("terminal.parameter_fingerprint", "requires a SHA-256 digest")
        if type(self.stamp) is not EventStamp:
            raise ContractError("terminal.stamp", "requires EventStamp")
        require_enum(self.reason, TerminalReason, "terminal.reason")
        require_int(self.seed, "terminal.seed")
        object.__setattr__(self, "survivor_ids", checked_ids(
            self.survivor_ids, "terminal.survivor_ids"
        ))
        if not isinstance(self.results, Mapping):
            raise ContractError("terminal.results", "expected per-player results")
        results = dict(self.results)
        if not results:
            raise ContractError("terminal.results", "must retain participant results")
        for actor_id, result in results.items():
            require_id(actor_id, "terminal.results.actor_id")
            if type(result) is not PlayerResult:
                raise ContractError(f"terminal.results.{actor_id}", "requires PlayerResult")
        object.__setattr__(self, "results", MappingProxyType(results))
        if not set(self.survivor_ids) <= self.results.keys():
            raise ContractError("terminal.survivor_ids", "survivors must have saved results")
        if self.reason is TerminalReason.LAST_SURVIVOR and len(self.survivor_ids) != 1:
            raise ContractError("terminal.reason", "requires exactly one survivor")
        if self.reason is TerminalReason.ALL_DEAD and self.survivor_ids:
            raise ContractError("terminal.reason", "all-dead ending has no survivors")

    def to_dict(self) -> dict[str, Any]:
        """保存与展示使用独立对象，避免闭环面板重置污染成绩。"""
        return {"snapshot_id": self.snapshot_id, "stamp": self.stamp.to_dict(),
                "reason": self.reason.value, "survivor_ids": list(self.survivor_ids),
                "results": {key: result.to_dict() for key, result in self.results.items()},
                "ruleset": self.ruleset, "schema_version": self.schema_version,
                "parameter_fingerprint": self.parameter_fingerprint, "seed": self.seed}
