"""GA全选择记录、严格重放与策略解释合同；记录不会反馈给其他玩家。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, TYPE_CHECKING

from engine.ga.contracts import (
    ActionRequest, ChoiceWindow, ContractError, TurnStage, checked_object, freeze_json,
    require_id, require_int, thaw_json,
)
from engine.ga.observation import public_value

if TYPE_CHECKING:
    from engine.ga.runtime import Controller


DECISION_SCHEMA = "ga.decisions.v1"


def input_digest(value: Mapping[str, Any]) -> str:
    """稳定摘要包含观察、候选顺序及窗口上下文，不依赖进程地址或哈希随机种子。"""
    encoded = json.dumps(thaw_json(freeze_json(value)), sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class CandidateAssessment:
    """候选评估明确区分未评估、保留与放弃；缺分数不是零分。"""

    index: int
    disposition: str
    reason: str
    estimates: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_int(self.index, "assessment.index")
        if self.disposition not in ("considered", "preferred", "deferred", "not_evaluated"):
            raise ContractError("assessment.disposition", "unknown disposition")
        require_id(self.reason, "assessment.reason")
        object.__setattr__(self, "estimates", freeze_json(self.estimates))


@dataclass(frozen=True)
class GoalIntent:
    """策略目标的诊断快照；执行回执驱动生命周期，不能代替引擎状态。"""

    goal_id: str
    state: str
    reason: str
    facts: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_id(self.goal_id, "goal.id")
        require_id(self.reason, "goal.reason")
        if self.state not in ("proposed", "active", "interrupted", "completed", "abandoned"):
            raise ContractError("goal.state", "unknown lifecycle state")
        object.__setattr__(self, "facts", freeze_json(self.facts))


@dataclass(frozen=True)
class DecisionExplanation:
    """控制器主动提供的理由，诊断器不根据最终战果事后编造。"""

    reason: str
    candidates: tuple[CandidateAssessment, ...] = ()
    goals: tuple[GoalIntent, ...] = ()

    def to_dict(self, candidate_count: int) -> dict[str, Any]:
        """检查候选索引，再导出估计与目标；没有报告的候选保持未解释。"""
        require_id(self.reason, "explanation.reason")
        if any(type(item) is not CandidateAssessment for item in self.candidates):
            raise ContractError("explanation.candidates", "requires CandidateAssessment")
        if any(type(item) is not GoalIntent for item in self.goals):
            raise ContractError("explanation.goals", "requires GoalIntent")
        indices = [item.index for item in self.candidates]
        if len(set(indices)) != len(indices) or any(i >= candidate_count for i in indices):
            raise ContractError("explanation.candidates", "duplicate or out-of-menu index")
        return public_value(self)


def _result(
    kind: str, selected: Any, actions: tuple[ActionRequest, ...], context: Mapping[str, Any],
) -> Any:
    """控制器结果在记录边界检查，非法返回不能进入支付或被当成布尔真值。"""
    if kind in ("choose", "choose_afterlife"):
        if selected is None:
            if kind == "choose" and context["stage"] != TurnStage.T0.value:
                raise ContractError("decision.selection", "primary choice cannot be omitted")
            return None
        if type(selected) is not ActionRequest or selected not in actions:
            raise ContractError("decision.selection", "not one of the supplied actions")
        return selected.to_dict()
    if kind == "respond":
        if selected is not None and (type(selected) is not str or
                                     selected not in context["window"]["options"]):
            raise ContractError("decision.response", "not one of the window options")
        return selected
    if type(selected) is not bool:
        raise ContractError("decision.selection", "requires an explicit bool")
    return selected


class DecisionPort:
    """六种正式控制器回调使用同一种输入身份和记录顺序。"""

    def choose(
        self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...], stage: TurnStage,
    ) -> ActionRequest | None:
        """普通行动也包括免费设置，保留每次真实回调。"""
        return self._call("choose", view, actions, {"stage": stage.value}, (actions, stage))

    def wants_public(self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...]) -> bool:
        """区分多次夺台窗口，不把上一边界的回答移用到下一边界。"""
        return self._call("wants_public", view, actions, {}, (actions,))

    def reroll_initiative(self, view: Mapping[str, Any], value: int) -> bool:
        """包含本次初骰；后续骰子仍由引擎生成。"""
        return self._call("reroll_initiative", view, (), {"value": value}, (value,))

    def escape_petrify(self, view: Mapping[str, Any], attempt: int) -> bool:
        """每次挣脱尝试分别记录。"""
        return self._call("escape_petrify", view, (), {"attempt": attempt}, (attempt,))

    def respond(
        self, view: Mapping[str, Any], window: ChoiceWindow, context: Mapping[str, Any],
    ) -> str | None:
        """秘密答案只进本地回放数据，不进入公开观察或另一控制器。"""
        return self._call("respond", view, (),
                          {"window": public_value(window), "context": context}, (window, context))

    def choose_afterlife(
        self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...],
    ) -> ActionRequest | None:
        """死亡后仍记录全部选择和显式跳过。"""
        return self._call("choose_afterlife", view, actions, {}, (actions,))

    def _call(
        self, kind: str, view: Mapping[str, Any], actions: tuple[ActionRequest, ...],
        context: Mapping[str, Any], args: tuple[Any, ...],
    ) -> Any:
        """由记录或重放实现调用，不提供默认策略。"""
        raise NotImplementedError


def _input(
    view: Mapping[str, Any], actions: tuple[ActionRequest, ...], context: Mapping[str, Any],
) -> Mapping[str, Any]:
    """输入是被调用者实际看见的内容，不混入全局私有状态。"""
    return freeze_json({"view": view, "actions": [a.to_dict() for a in actions],
                        "context": context})


class RecordingController(DecisionPort):
    """默认只存摘要与选择；显式详细模式才保存完整观察和候选。"""

    def __init__(self, controller: Controller, *, detailed: bool = False) -> None:
        self.controller = controller
        self.detailed = detailed
        self.records: list[dict[str, Any]] = []

    def _call(
        self, kind: str, view: Mapping[str, Any], actions: tuple[ActionRequest, ...],
        context: Mapping[str, Any], args: tuple[Any, ...],
    ) -> Any:
        """先保存决策点；异常留下输入及错误，绝不重试或静默降级。"""
        incoming = _input(view, actions, context)
        count = len(context["window"]["options"]) if kind == "respond" else (
            len(actions) if kind in ("choose", "choose_afterlife", "wants_public") else 2)
        schedule = view.get("schedule", {})
        grant = schedule.get("grant") or {}
        stage = context.get("stage", schedule.get("stage"))
        root = actions[0].root_id if actions else grant.get("root_id")
        if root is None and grant and stage is not None:
            root = f"{grant['grant_id']}/{stage}"
        record = {
            "sequence": len(self.records), "kind": kind, "input_digest": input_digest(incoming),
            "point": {key: view[key] for key in ("actor_id", "round", "world_tick", "phase")},
            "candidate_count": count, "selected": None,
            "explanation": None,
        }
        record["point"].update(grant_id=grant.get("grant_id"), root_id=root, stage=stage,
                               window_id=context.get("window", {}).get("window_id"))
        if self.detailed:
            record["input"] = thaw_json(incoming)
        self.records.append(record)
        try:
            selected = getattr(self.controller, kind)(view, *args)
            record["selected"] = _result(kind, selected, actions, context)
            explain = getattr(self.controller, "explain_decision", None)
            if explain is not None:
                explanation = explain(kind, incoming, record["selected"])
                if explanation is not None:
                    if type(explanation) is not DecisionExplanation:
                        raise ContractError("explanation", "requires DecisionExplanation or None")
                    record["explanation"] = explanation.to_dict(count)
            return selected
        except Exception as exc:
            record["error"] = {"type": type(exc).__name__, "message": str(exc)}
            record.setdefault("input", thaw_json(incoming))
            raise

    def replay_inputs(self) -> dict[str, Any]:
        """完整顺序供严格重放，不输出不完整的旧三个输入数组。"""
        return {"schema": DECISION_SCHEMA, "decisions": thaw_json(freeze_json(self.records))}

    def on_action_result(self, view: Mapping[str, Any], outcome: Mapping[str, Any]) -> None:
        """实际执行后的只读通知不是选择；包装器透明转发目标进度证据。"""
        callback = getattr(self.controller, "on_action_result", None)
        if callback is not None:
            callback(view, outcome)


def notify_action_result(controller: Any, view: Mapping[str, Any], receipt: Any) -> None:
    """控制器仅收到自身动作的实际回执；重复通知由目标按请求身份去重。"""
    callback = getattr(controller, "on_action_result", None)
    if callback is not None:
        callback(view, freeze_json(public_value(receipt)))


class ReplayController(DecisionPort):
    """输入摘要、调用种类和顺序全部匹配才消费记录；永不回落MinimalController。"""

    def __init__(self, script: Mapping[str, Any]) -> None:
        checked_object(script, {"schema", "decisions"}, "replay")
        if script["schema"] != DECISION_SCHEMA:
            raise ContractError("replay.schema", "incompatible decision semantics")
        if not isinstance(script["decisions"], (list, tuple)):
            raise ContractError("replay.decisions", "expected ordered decisions")
        self.records = freeze_json(script["decisions"])
        self.position = 0

    def _call(
        self, kind: str, view: Mapping[str, Any], actions: tuple[ActionRequest, ...],
        context: Mapping[str, Any], args: tuple[Any, ...],
    ) -> Any:
        """发现分歧就定位到玩家与序号，尚未执行动作或消费资源。"""
        path = f"replay.{view['actor_id']}.{self.position}.{kind}"
        if self.position == len(self.records):
            raise ContractError(path, "explicit decision stream exhausted")
        record = self.records[self.position]
        if not isinstance(record, Mapping) or record.get("sequence") != self.position or (
            record.get("kind") != kind or record.get("input_digest") !=
            input_digest(_input(view, actions, context))
        ):
            raise ContractError(path, "decision input/order mismatch")
        if "error" in record or "selected" not in record:
            raise ContractError(path, "recorded failure has no executable response")
        selected = record["selected"]
        if kind in ("choose", "choose_afterlife") and selected is not None:
            selected = ActionRequest.from_dict(selected)
        _result(kind, selected, actions, context)
        self.position += 1
        return selected

    def assert_consumed(self) -> None:
        """提前结束且剩余输入不算成功重放。"""
        if self.position != len(self.records):
            raise ContractError("replay", f"{len(self.records) - self.position} unused decisions")

    def explain_decision(
        self, kind: str, incoming: Mapping[str, Any], selected: Any,
    ) -> DecisionExplanation | None:
        """保留原策略报告的理由，重放器不重新评分。"""
        raw = self.records[self.position - 1].get("explanation")
        if raw is None:
            return None
        return DecisionExplanation(raw["reason"], tuple(CandidateAssessment(**item)
                                   for item in raw["candidates"]),
                                   tuple(GoalIntent(**item) for item in raw["goals"]))
