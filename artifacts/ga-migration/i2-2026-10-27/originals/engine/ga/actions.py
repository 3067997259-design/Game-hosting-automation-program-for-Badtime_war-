"""GA公共动作预检：只生成计划，不扣资源、不调用随机数、不写GameState。

未来CLI/控制器适配器从正式状态导出ActionContext，执行前再核对snapshot_id。
复合根每段重新导出上下文和预检，已执行步骤的状态不由这里回滚。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from engine.ga.config import GaConfig
from engine.ga.contracts import (
    ActionPlan, ActionRequest, ActionVerb, ContractError, CostEntry, Performance,
    RandomRequirement, TurnStage, checked_enums, checked_ids, checked_values, freeze_json,
    require_bool, require_enum, require_id, require_int,
)


class GrantKind(str, Enum):
    """行动授予身份；反应和单步追击不是完整回合。"""

    STANDARD = "standard"
    EXTRA_TURN = "extra_turn"
    STEP_FOLLOWUP = "step_followup"
    REACTION = "reaction"


@dataclass(frozen=True)
class ActionContext:
    """一次预检的只读状态投影，不是可写钱包或新的游戏状态源。"""

    actor_id: str
    grant_id: str
    root_id: str
    kind: GrantKind
    snapshot_id: str
    resources: Mapping[str, Mapping[str, int]]
    legal_target_ids: tuple[str, ...]
    allowed_verbs: frozenset[ActionVerb]
    occupied_stages: frozenset[TurnStage]
    public_available: bool

    def __post_init__(self) -> None:
        for key in ("actor_id", "grant_id", "root_id", "snapshot_id"):
            require_id(getattr(self, key), f"context.{key}")
        require_enum(self.kind, GrantKind, "context.kind")
        require_bool(self.public_available, "context.public_available")
        if not isinstance(self.resources, Mapping):
            raise ContractError("context.resources", "expected payer/account balances")
        for payer, accounts in self.resources.items():
            require_id(payer, "context.resources.payer")
            if not isinstance(accounts, Mapping):
                raise ContractError(f"context.resources.{payer}", "expected account balances")
            for account, amount in accounts.items():
                require_id(account, f"context.resources.{payer}.account")
                require_int(amount, f"context.resources.{payer}.{account}")
        object.__setattr__(self, "resources", freeze_json(self.resources, "context.resources"))
        object.__setattr__(self, "legal_target_ids", checked_ids(
            self.legal_target_ids, "context.legal_target_ids"
        ))
        for key, enum_type in (("allowed_verbs", ActionVerb), ("occupied_stages", TurnStage)):
            values = checked_enums(getattr(self, key), enum_type, f"context.{key}")
            object.__setattr__(self, key, values)
        if self.kind in (GrantKind.STEP_FOLLOWUP, GrantKind.REACTION):
            if self.allowed_verbs - {ActionVerb.MOVE, ActionVerb.ATTACK}:
                raise ContractError(
                    "context.allowed_verbs", "restricted grants only move or attack"
                )


@dataclass(frozen=True)
class ActionDefinition:
    """注册操作的单步合同，费用和目标数由实际技能/地点适配器传入。"""

    operation: str
    stage: TurnStage
    verb: ActionVerb
    allowed_performances: frozenset[Performance]
    costs: tuple[CostEntry, ...]
    minimum_targets: int
    maximum_targets: int
    required_parameters: tuple[str, ...] = ()
    optional_parameters: tuple[str, ...] = ()
    random_requirements: tuple[RandomRequirement, ...] = ()
    consumes_stage: bool = True
    meaningful: bool = True

    def __post_init__(self) -> None:
        require_id(self.operation, "definition.operation")
        require_enum(self.stage, TurnStage, "definition.stage")
        require_enum(self.verb, ActionVerb, "definition.verb")
        if self.stage is TurnStage.T2:
            raise ContractError(
                "definition.stage", "T2 dispatches followups, not a second root choice"
            )
        require_int(self.minimum_targets, "definition.minimum_targets")
        require_int(self.maximum_targets, "definition.maximum_targets")
        if self.minimum_targets > self.maximum_targets:
            raise ContractError("definition.maximum_targets", "below minimum_targets")
        require_bool(self.consumes_stage, "definition.consumes_stage")
        require_bool(self.meaningful, "definition.meaningful")
        modes = checked_enums(
            self.allowed_performances, Performance, "definition.allowed_performances"
        )
        if not modes:
            raise ContractError("definition.allowed_performances", "must be explicit")
        object.__setattr__(self, "allowed_performances", modes)
        for key, item_type in (("costs", CostEntry), ("random_requirements", RandomRequirement)):
            values = checked_values(getattr(self, key), item_type, f"definition.{key}")
            object.__setattr__(self, key, values)
        for key in ("required_parameters", "optional_parameters"):
            object.__setattr__(self, key, checked_ids(getattr(self, key), f"definition.{key}"))
        if set(self.required_parameters) & set(self.optional_parameters):
            raise ContractError("definition.parameters", "required/optional keys overlap")
        random_keys = [item.key for item in self.random_requirements]
        if len(set(random_keys)) != len(random_keys):
            raise ContractError("definition.random_requirements", "duplicate draw keys")


def _aggregate_costs(costs: tuple[CostEntry, ...]) -> tuple[CostEntry, ...]:
    """同一付款者/账户先汇总，防止拆成两项绕过余额检查。"""
    totals: dict[tuple[str, str], tuple[int, int]] = {}
    for cost in costs:
        key = (cost.payer_id, cost.account)
        amount, floor = totals.get(key, (0, 0))
        totals[key] = (amount + cost.amount, max(floor, cost.minimum_remaining))
    return tuple(CostEntry(payer, account, amount, floor)
                 for (payer, account), (amount, floor) in sorted(totals.items()))


def precheck_action(
    request: ActionRequest, context: ActionContext, definition: ActionDefinition, config: GaConfig,
) -> ActionPlan:
    """生成可执行计划或零授权拒绝；合法后被拦截不产生自动退款计划。"""
    if type(request) is not ActionRequest or type(context) is not ActionContext:
        raise ContractError("precheck", "requires typed request and context")
    if type(definition) is not ActionDefinition or type(config) is not GaConfig:
        raise ContractError("precheck", "requires operation definition and validated GaConfig")
    issues: list[str] = []
    if (request.actor_id, request.grant_id) != (context.actor_id, context.grant_id):
        issues.append("actor_or_grant_mismatch")
    if request.root_id != context.root_id:
        issues.append("root_mismatch")
    if (request.operation, request.stage, request.verb) != (
        definition.operation, definition.stage, definition.verb
    ):
        issues.append("operation_mismatch")
    if request.verb not in context.allowed_verbs:
        issues.append("verb_not_granted")
    if request.performance not in definition.allowed_performances:
        issues.append("performance_not_registered")
    if definition.consumes_stage and request.stage in context.occupied_stages:
        issues.append("stage_already_consumed")
    restricted = context.kind in (GrantKind.STEP_FOLLOWUP, GrantKind.REACTION)
    if request.stage is TurnStage.T0 and restricted:
        issues.append("restricted_grant_has_no_t0")
    if context.kind in (GrantKind.STEP_FOLLOWUP, GrantKind.REACTION):
        if request.performance is not Performance.NONE:
            issues.append("restricted_grant_cannot_perform")
    if request.performance is Performance.PUBLIC:
        if context.kind is not GrantKind.STANDARD or request.stage is not TurnStage.T1:
            issues.append("public_requires_standard_t1")
        if not context.public_available:
            issues.append("public_slot_unavailable")
    if not definition.minimum_targets <= len(request.target_ids) <= definition.maximum_targets:
        issues.append("target_count_invalid")
    if not set(request.target_ids) <= set(context.legal_target_ids):
        issues.append("target_not_legal")
    required = set(definition.required_parameters)
    allowed = required | set(definition.optional_parameters)
    if not required <= request.parameters.keys() or not request.parameters.keys() <= allowed:
        issues.append("parameter_keys_invalid")
    if not definition.meaningful:
        issues.append("operation_has_no_effect")

    costs = definition.costs
    if request.performance is not Performance.NONE:
        if any(cost.account == "sp" and cost.payer_id == request.actor_id for cost in costs):
            raise ContractError(
                "definition.costs", "performance SP is quoted from the shared config"
            )
        key = "instant_sp_cost" if request.performance is Performance.INSTANT else "public_sp_cost"
        costs = (*costs, CostEntry(request.actor_id, "sp", config.get("action", key)))
    quoted_list: list[CostEntry] = []
    for cost in _aggregate_costs(costs):
        hp_floor = config.get("combat", "paid_hp_survival_floor") if (
            cost.account == "hp" and cost.amount
        ) else 0
        quoted_list.append(CostEntry(cost.payer_id, cost.account, cost.amount,
                                     max(cost.minimum_remaining, hp_floor)))
    quoted = tuple(quoted_list)
    for cost in quoted:
        accounts = context.resources.get(cost.payer_id)
        if accounts is None or cost.account not in accounts:
            issues.append(f"missing_account:{cost.payer_id}:{cost.account}")
            continue
        if accounts[cost.account] - cost.amount < cost.minimum_remaining:
            issues.append(f"insufficient_resource:{cost.payer_id}:{cost.account}")
    if issues:
        return ActionPlan(request, context.snapshot_id, False, (), None, (), tuple(issues))
    return ActionPlan(request, context.snapshot_id, True, quoted,
                      request.stage if definition.consumes_stage else None,
                      definition.random_requirements, ())
