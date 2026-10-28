"""I1只读控制器边界：人工选择、显式脚本与最小合法候选策略。"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from engine.ga.contracts import (
    ActionRequest, ChoiceWindow, ContractError, Performance, TurnStage, thaw_json,
)
from engine.ga.decisions import CandidateAssessment, DecisionExplanation


class MinimalController:
    """只用于完整链路验收；不是I4的BasicAI策略或平衡证据。"""

    def _pick(self, action: ActionRequest | None, reason: str) -> ActionRequest | None:
        """记录实际命中的固定优先级规则，不把它解释成效用最优。"""
        self._decision_reason = reason
        return action

    def choose(
        self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...], stage: TurnStage,
    ) -> ActionRequest | None:
        """从传入候选选择，不拼接命令，不直接修改状态。"""
        if stage is TurnStage.T0:
            for operation in ("wake", "syringe", "g4_ascend", "g1_sam", "g0_drone", "t3_jade"):
                candidate = next((a for a in actions if a.operation == operation), None)
                if candidate:
                    return self._pick(candidate, "minimal.t0_priority")
            mounts = [a for a in actions if a.operation == "g0_mount" and
                      len(a.parameters["relics"]) == 2]
            if mounts:
                return self._pick(mounts[0], "minimal.two_relic_mount")
            return self._pick(next((a for a in actions if a.operation in ("wake", "lock")), None),
                              "minimal.prepare_or_skip")
        public = next((a for a in actions if a.performance is Performance.PUBLIC), None)
        if public:
            return self._pick(public, "minimal.public_first")
        performance = next((a for a in actions if a.performance is Performance.INSTANT), None)
        if performance:
            return self._pick(performance, "minimal.instant_first")
        attacks = [a for a in actions if a.verb.value == "attack"]
        if attacks:
            return self._pick(next((a for a in attacks if a.parameters.get("weapon") != "fist"),
                                   attacks[0]), "minimal.first_attack_prefer_weapon")
        if not view["players"][view["actor_id"]]["awake"]:
            raise ContractError("controller", "primary action requested before wake")
        player = view["players"][view["actor_id"]]
        refills = [a for a in actions if a.operation == "refill"]
        if refills:
            return self._pick(refills[0], "minimal.refill")
        others = sorted(key for key, p in view["players"].items() if p["alive"] and
                        key != view["actor_id"])
        for actor in others:
            move = next((a for a in actions if a.operation == "move" and
                         a.parameters["location"] == view["players"][actor]["location"]), None)
            if move:
                return self._pick(move, "minimal.move_to_first_opponent")
        if not player["location"].startswith("home:"):
            return self._pick(next(a for a in actions if a.operation == "forfeit"),
                              "minimal.no_route_forfeit")
        return self._pick(next(a for a in actions if a.operation == "move"), "minimal.leave_home")

    def explain_decision(
        self, kind: str, incoming: Mapping[str, Any], selected: Any,
    ) -> DecisionExplanation | None:
        """只解释本类真正执行的分支；子类自定义策略不能冒领最小策略的理由。"""
        if getattr(type(self), kind) is not getattr(MinimalController, kind):
            return None
        reasons = {"wants_public": "minimal.any_public_candidate",
                   "reroll_initiative": "minimal.keep_pp", "escape_petrify": "minimal.try_escape",
                   "respond": "minimal.decline_or_first", "choose_afterlife": "minimal.skip_ghost"}
        reason = self._decision_reason if kind == "choose" else reasons[kind]
        return DecisionExplanation(reason, tuple(
            CandidateAssessment(i, "preferred" if thaw_json(candidate) == selected else
                                "not_evaluated", reason if thaw_json(candidate) == selected else
                                "minimal.no_value_estimate")
            for i, candidate in enumerate(incoming["actions"])))

    def wants_public(self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...]) -> bool:
        """合法演出候选存在才申请；无天赋局不会虚构公演技能。"""
        return bool(actions)

    def reroll_initiative(self, view: Mapping[str, Any], value: int) -> bool:
        """最小策略不消费可保留到终分的PP。"""
        return False

    def escape_petrify(self, view: Mapping[str, Any], attempt: int) -> bool:
        """仅从引擎给出的有限窗口尝试挣脱。"""
        return True

    def respond(
        self, view: Mapping[str, Any], window: ChoiceWindow, context: Mapping[str, Any],
    ) -> str | None:
        """最小策略明确作答；不把没有回答当成接受。"""
        if "decline" in window.options:
            return "decline"
        return window.options[0]

    def choose_afterlife(
        self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...],
    ) -> ActionRequest | None:
        """默认最小策略明确跳过可选往世操作，不伪造投资策略。"""
        return None


class HumanController(MinimalController):
    """由现有UI或命令行提供输入回调，仍只能返回同一合法候选。"""

    def __init__(
        self, select: Callable[[Mapping[str, Any], tuple[ActionRequest, ...], TurnStage],
                               ActionRequest | None],
        answer: Callable[
            [Mapping[str, Any], ChoiceWindow, Mapping[str, Any]], str | None
        ] | None = None,
        decide: Callable[[Mapping[str, Any], str, Mapping[str, Any]], bool] | None = None,
    ) -> None:
        self.select = select
        self.answer = answer
        self.decide = decide

    def wants_public(self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...]) -> bool:
        """人工入口可明确接受或拒绝当前公演申请。"""
        return self.decide(view, "wants_public", {"actions": [a.to_dict() for a in actions]}) if (
            self.decide is not None) else super().wants_public(view, actions)

    def reroll_initiative(self, view: Mapping[str, Any], value: int) -> bool:
        """人工入口可明确支付PP重掷。"""
        return self.decide(view, "reroll_initiative", {"value": value}) if (
            self.decide is not None) else super().reroll_initiative(view, value)

    def escape_petrify(self, view: Mapping[str, Any], attempt: int) -> bool:
        """人工入口可明确决定每次付费挣脱。"""
        return self.decide(view, "escape_petrify", {"attempt": attempt}) if (
            self.decide is not None) else super().escape_petrify(view, attempt)

    def choose(
        self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...], stage: TurnStage,
    ) -> ActionRequest | None:
        """输入选择交回唯一验证器，不授予UI业务状态写权限。"""
        return self.select(view, actions, stage)

    def respond(
        self, view: Mapping[str, Any], window: ChoiceWindow, context: Mapping[str, Any],
    ) -> str | None:
        """人工没有提供答案就保持待回答，不继承最小策略的默认出拳。"""
        return self.answer(view, window, context) if self.answer else None

    def choose_afterlife(
        self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...],
    ) -> ActionRequest | None:
        """同一个人工选择边界显示真实往世按钮。"""
        return self.select(view, actions, TurnStage.T0)


class ScriptController(MinimalController):
    """显式输入序列；脚本耗尽立即报错，不自动换成AI或放弃。"""

    def __init__(
        self, choices: Iterable[Mapping[str, Any] | None], *, responses: Iterable[str | None] = (),
        afterlife: Iterable[Mapping[str, Any] | None] = (),
    ) -> None:
        self.choices = iter(choices)
        self.responses = iter(responses)
        self.afterlife = iter(afterlife)

    def choose(
        self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...], stage: TurnStage,
    ) -> ActionRequest | None:
        """脚本选择器匹配当前候选，参数不匹配视为验收失败。"""
        try:
            wanted = next(self.choices)
        except StopIteration as exc:
            raise ContractError("script", "explicit script exhausted") from exc
        if wanted is None:
            return None
        matches = [a for a in actions if all(
            (a.to_dict().get(key) == value if key in a.to_dict() else
             a.parameters.get(key) == value) for key, value in wanted.items()
        )]
        if len(matches) != 1:
            raise ContractError("script", "selector must identify exactly one legal action")
        return matches[0]

    def respond(
        self, view: Mapping[str, Any], window: ChoiceWindow, context: Mapping[str, Any],
    ) -> str | None:
        """必须提供实际窗口响应；脚本缺答案明确失败。"""
        try:
            return next(self.responses)
        except StopIteration as exc:
            raise ContractError("script", "explicit response script exhausted") from exc

    def choose_afterlife(
        self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...],
    ) -> ActionRequest | None:
        """省略可选往世序列表示明确不参与，不消费正常行动脚本。"""
        wanted = next(self.afterlife, None)
        if wanted is None:
            return None
        matches = [a for a in actions if all(a.to_dict().get(k, a.parameters.get(k)) == value
                                            for k, value in wanted.items())]
        if len(matches) != 1:
            raise ContractError("script", "afterlife selector must match exactly one candidate")
        return matches[0]
