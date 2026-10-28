"""GA决策编排：公开分析、跨轮目标和当前战术共同仲裁。"""

from __future__ import annotations

from collections.abc import Mapping
import time
from typing import Any

from engine.ga.ai.minds import ScoreMind
from engine.ga.ai.planning import GoalStack, propose_routes
from engine.ga.ai.strategy import Proposal, SearchBudget, Strategy
from engine.ga.config import GaConfig
from engine.ga.contracts import ActionRequest, ContractError, TurnStage, thaw_json
from engine.ga.decisions import CandidateAssessment, DecisionExplanation


class DecisionOrchestrator:
    """一个主目标与一个暂停意图，实际执行回执驱动推进，候选统一竞争。"""

    def __init__(
        self, config: GaConfig, *, node_budget: int = 96, style: str = "balanced",
    ) -> None:
        if type(node_budget) is not int or not 16 <= node_budget <= 4096:
            raise ValueError("node_budget must be an integer in [16, 4096]")
        self.config = config
        self.node_budget = node_budget
        self.strategy = Strategy(config, style)
        self.goals = GoalStack()
        self.last_explanation = DecisionExplanation("not_yet_called")
        self._decisions = 0
        self._nodes = 0
        self._max_nodes = 0
        self._seconds = 0.0

    @property
    def diagnostics(self) -> Mapping[str, Any]:
        """性能统计独立于策略输入，墙钟不影响候选排序或严格重放。"""
        return {"decisions": self._decisions, "nodes": self._nodes,
                "max_nodes": self._max_nodes, "total_seconds": self._seconds}

    def choose(
        self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...], stage: TurnStage,
    ) -> ActionRequest | None:
        """先同批比较全部可预算候选，再对领先提案展开有界响应情景。"""
        if any(player["talent"] is not None for player in view["players"].values()):
            raise ContractError("basicai.scope", "I4.1 supports no-talent lineups only")
        if view["parameter_fingerprint"] != self.config.fingerprint:
            raise ContractError("basicai.config", "observation/rules mismatch")
        started = time.perf_counter()
        budget = SearchBudget(self.node_budget)
        self.strategy.start_decision()
        situation = ScoreMind.assess(view, self.config)
        routes = tuple(route for route in propose_routes(view, self.config)
                       if route.t1_turns < situation.remaining_slots)
        self.goals.reconcile(view, routes)
        descriptions = view["candidates"]
        if len(descriptions) != len(actions):
            raise ContractError("basicai.menu", "requires same-menu candidate descriptions")
        if any(action.to_dict() != thaw_json(description["request"]) for action, description in
               zip(actions, descriptions, strict=True)):
            raise ContractError("basicai.menu", "candidate descriptions do not match menu order")
        proposals: dict[int, Proposal] = {}
        for index, action in enumerate(actions):
            if not budget.take():
                break
            proposals[index] = self.strategy.immediate(
                view, action, descriptions[index], situation, index)
        for route in routes:
            first = route.first_action(actions)
            if first is None:
                continue
            if not budget.take():
                break
            index = actions.index(first)
            candidate = self.strategy.route(view, route, situation, index)
            if self.goals.active and self.goals.active.goal_id == route.goal_id:
                # 只给极小的近值续行偏好，不返还沉没成本，不覆盖明显坏计划。
                candidate.value += 1 / max(2, situation.remaining_slots)
                candidate.estimates["continuity_allowance"] = 1 / max(
                    2, situation.remaining_slots)
            if index not in proposals or candidate.value > proposals[index].value:
                proposals[index] = candidate
        selectable = list(proposals.values())
        if stage is TurnStage.T1:
            width = max(1, min(len(proposals), (budget.limit - budget.used) // 2))
            leaders = sorted(proposals.values(), key=lambda item: (-item.value, item.index))[:width]
            expanded = []
            for proposal in leaders:
                responses = []
                for mode in ("greedy", "recover"):
                    if not budget.take():
                        break
                    responses.append(self.strategy.response_scenario(
                        view, actions[proposal.index], descriptions[proposal.index], proposal,
                        mode))
                if responses:
                    penalty = sum(responses) / len(responses)
                    proposal.value += penalty
                    proposal.estimates["response_adjustment"] = penalty
                    proposal.estimates["response_modes"] = ["greedy", "recover"][:len(responses)]
                    expanded.append(proposal)
            if expanded:
                selectable = expanded
        if not proposals:
            raise ContractError("basicai.menu", "no evaluated legal candidate")
        best = max(selectable, key=lambda item: (item.value, -item.index))
        can_skip = stage is TurnStage.T0 and view["players"][situation.actor_id]["awake"]
        selected = None if can_skip and best.value <= 0 else actions[best.index]
        if selected is not None and best.route is not None:
            self.goals.commit(best.route)
        elif selected is not None and stage is TurnStage.T1 and self.goals.active:
            self.goals.interrupt("tactical_opportunity_or_emergency")
        assessments = []
        for index in range(len(actions)):
            proposal = proposals.get(index)
            if proposal is None:
                assessments.append(CandidateAssessment(index, "not_evaluated", "node_budget"))
            else:
                assessments.append(CandidateAssessment(
                    index, "preferred" if selected is not None and index == best.index else
                    "considered", proposal.reason, {**proposal.estimates,
                        "estimated_value": proposal.value, "search_nodes": budget.used,
                        "node_budget": budget.limit}))
        self.last_explanation = DecisionExplanation(
            best.reason if selected is not None else "skip_t0_no_positive_marginal_return",
            tuple(assessments), self.goals.intents())
        self._decisions += 1
        self._nodes += budget.used
        self._max_nodes = max(self._max_nodes, budget.used)
        self._seconds += time.perf_counter() - started
        return selected

    def on_action_result(self, view: Mapping[str, Any], outcome: Mapping[str, Any]) -> None:
        """提案不推进目标；引擎执行后才核对回执及下一公开状态。"""
        remaining = ScoreMind.assess(view, self.config).remaining_slots
        routes = tuple(route for route in propose_routes(view, self.config)
                       if route.t1_turns < remaining)
        self.goals.reconcile(view, routes, outcome)
