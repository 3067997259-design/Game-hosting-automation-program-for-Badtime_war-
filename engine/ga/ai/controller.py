"""I4.1无天赋BasicAI适配器；没有继承最小策略的隐式默认分支。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from engine.ga.ai.orchestrator import DecisionOrchestrator
from engine.ga.ai.strategy import best_attack
from engine.ga.config import GaConfig
from engine.ga.contracts import ActionRequest, ChoiceWindow, ContractError, TurnStage
from engine.ga.decisions import CandidateAssessment, DecisionExplanation


class BasicAI:
    """同一策略使用可调有界预算；首版强度以独立对抗结果为限。"""

    policy_version = "ga.basicai.i41.v1"

    def __init__(
        self, config: GaConfig, *, node_budget: int = 96, style: str = "balanced",
    ) -> None:
        self.config = config
        self.orchestrator = DecisionOrchestrator(config, node_budget=node_budget, style=style)
        self._explanation = DecisionExplanation("not_yet_called")

    @property
    def diagnostics(self) -> Mapping[str, Any]:
        """只读性能诊断，不影响决策与输入摘要。"""
        return self.orchestrator.diagnostics

    def choose(
        self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...], stage: TurnStage,
    ) -> ActionRequest | None:
        """所有常规行动共用orchestrator，原样返回真实菜单对象。"""
        selected = self.orchestrator.choose(view, actions, stage)
        self._explanation = self.orchestrator.last_explanation
        return selected

    def on_action_result(self, view: Mapping[str, Any], outcome: Mapping[str, Any]) -> None:
        """只接自身动作的冻结回执，不取得引擎可写引用。"""
        self.orchestrator.on_action_result(view, outcome)

    def wants_public(self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...]) -> bool:
        """无天赋没有公演；不猜测未来天赋的收益。"""
        if actions:
            raise ContractError("basicai.scope", "public performance requires talent strategy")
        self._explanation = DecisionExplanation("no_public_candidate")
        return False

    def reroll_initiative(self, view: Mapping[str, Any], value: int) -> bool:
        """只在低骰且有立即击杀/致命竞争时，用公开机会比较一枚PP。"""
        actor = view["actor_id"]
        _, _, quote = best_attack(view, actor, self.config)
        sides = self.config.get("start", "initiative_sides")
        improvement = max(0.0, (sides - value) / sides)
        cost = self.config.get("pp", "initiative_reroll_cost") * self.config.get(
            "scoring", "pp_weight")
        gain = quote.kill_probability * self.config.get("scoring", "kill_weight") * improvement
        selected = value < (sides + 1) / 2 and gain > cost
        self._explanation = DecisionExplanation("reroll_only_for_lethal_race", candidates=(
            CandidateAssessment(int(selected), "preferred", "estimated_race_return",
                                {"estimated_return": gain, "pp_score_cost": cost}),))
        return selected

    def escape_petrify(self, view: Mapping[str, Any], attempt: int) -> bool:
        """无天赋仍可能受公共控制；释放一个实际攻击机会才值得支付SP。"""
        _, _, quote = best_attack(view, view["actor_id"], self.config)
        selected = quote.available and (quote.expected_hp > 0 or attempt == 0)
        self._explanation = DecisionExplanation("escape_for_next_action_opportunity")
        return selected

    def respond(
        self, view: Mapping[str, Any], window: ChoiceWindow, context: Mapping[str, Any],
    ) -> str | None:
        """首版只处理明确代付接受权；不把未知秘密博弈自动选第一项。"""
        if "decline" in window.options:
            self._explanation = DecisionExplanation("decline_unpriced_external_commitment")
            return "decline"
        raise ContractError("basicai.response", "unsupported no-talent choice window")

    def choose_afterlife(
        self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...],
    ) -> ActionRequest | None:
        """免费押注比较近期真实战果与分红人数；付费援助须单独证明回本。"""
        afterlife = view["afterlife"]
        actor = view["actor_id"]
        scores: list[tuple[float, int]] = []
        for index, action in enumerate(actions):
            if action.operation != "afterlife_bet":
                continue
            target = action.target_ids[0]
            _, _, quote = best_attack(view, target, self.config)
            others = sum(key != actor and chosen == target for key, chosen in
                         afterlife["bets"].items())
            opportunity = quote.kill_probability * self.config.get("afterlife", "bet_kill_pool")
            opportunity += quote.expected_breaks * self.config.get("afterlife", "bet_break_pool")
            # 近期无击杀时，以距离下一次破甲/击杀的进度排出可解释次序。
            opportunity += quote.expected_hp / max(1, self.config.get("start", "max_hp"))
            value = opportunity / (others + 1)
            scores.append((value, index))
        best = max(scores, default=None, key=lambda item: (item[0], -item[1]))
        preferred = best[1] if best is not None else None
        values = {index: value for value, index in scores}
        self._explanation = DecisionExplanation("free_bet_near_term_dividend_or_keep_pp", tuple(
            CandidateAssessment(index, "preferred" if index == preferred else "considered",
                                "free_dividend_estimate" if index in values else
                                "paid_glow_return_not_established",
                                {"estimated_dividend": values[index]} if index in values else {})
            for index in range(len(actions))))
        return actions[preferred] if preferred is not None else None

    def explain_decision(
        self, kind: str, incoming: Mapping[str, Any], selected: Any,
    ) -> DecisionExplanation:
        """返回选择前生成的解释，不依据后来的输赢补故事。"""
        return self._explanation
