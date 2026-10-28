"""I2定向场景夹具：真实起床/出门、共同验证器和明确窗口响应。"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from typing import Any

from engine.game_state import GameState
from engine.ga.actions import GrantKind
from engine.ga.controllers import MinimalController
from engine.ga.contracts import ActionRequest, ChoiceWindow, Performance, RoundPhase, TurnStage
from engine.ga.gameplay import ActionReceipt, execute_action, legal_actions, validate_request
from engine.ga.state import ActionGrant, next_id, touch


class Decisions(MinimalController):
    """空闲时明确放弃，秘密窗口用指定序列，不依赖暗中默认答案。"""

    def __init__(self, **answers: list[str | None]) -> None:
        self.answers = {key: deque(values) for key, values in answers.items()}
        self.ghost_actions: deque[dict[str, Any] | None] = deque()
        self.seen: list[tuple[dict[str, Any], str]] = []

    def choose(
        self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...], stage: TurnStage,
    ) -> ActionRequest | None:
        if stage is TurnStage.T0:
            return next((a for a in actions if a.operation == "wake"), None)
        return next((a for a in actions if a.operation == "forfeit"), actions[0])

    def wants_public(self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...]) -> bool:
        return False

    def respond(
        self, view: Mapping[str, Any], window: ChoiceWindow, context: Mapping[str, Any],
    ) -> str | None:
        self.seen.append((dict(context), window.recipient_id))
        choices = self.answers.get(context["kind"])
        if choices:
            return choices.popleft()
        return "decline" if "decline" in window.options else window.options[0]

    def choose_afterlife(
        self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...],
    ) -> ActionRequest | None:
        if not self.ghost_actions:
            return None
        wanted = self.ghost_actions.popleft()
        if wanted is None:
            return None
        return next(a for a in actions if all(a.to_dict().get(k, a.parameters.get(k)) == v
                                               for k, v in wanted.items()))


def ready(slots: dict[str, str] | None = None, *, count: int = 3, seed: int = 0) -> GameState:
    """所有人经实际动作领取初始包出门，余下为明示定向前置状态。"""
    ids = [chr(97 + i) for i in range(count)]
    state = GameState.new_ga(ids, seed=seed, talents=slots or {})
    state.current_round = state.world_tick = 1
    state.current_phase = RoundPhase.R3
    state.ga_controllers = {actor: Decisions() for actor in ids}
    state.ga_initiative = {actor: count - index for index, actor in enumerate(ids)}
    state.ga_round_order = tuple(ids)
    for actor in ids:
        turn(state, actor, TurnStage.T0)
        execute(state, "wake", choice="knife")
        state.current_stage = TurnStage.T1
        execute(state, "move", location="shop")
    return state


def turn(
    state: GameState, actor: str, stage: TurnStage = TurnStage.T1, *,
    kind: GrantKind = GrantKind.STANDARD,
) -> None:
    """为单步行为夹具注入授予；完整对局另走run_round/run_game。"""
    state.current_grant = ActionGrant(next_id(state, f"fixture/{actor}"), actor, kind)
    state.current_stage = stage
    if kind is not GrantKind.AFTERLIFE:
        state.current_phase = RoundPhase.R3
    touch(state)


def execute(
    state: GameState, operation: str, *, targets: tuple[str, ...] | None = None,
    mode: Performance | None = None, **parameters: Any,
) -> ActionReceipt:
    """每个动作必须来自生产候选且通过生产预检。"""
    options = [a for a in legal_actions(state) if a.operation == operation and
               (targets is None or a.target_ids == targets) and
               (mode is None or a.performance is mode) and
               all(a.parameters.get(key) == value for key, value in parameters.items())]
    assert options, (operation, parameters, [a.to_dict() for a in legal_actions(state)])
    plan = validate_request(state, options[0])
    assert plan.accepted
    return execute_action(state, plan)
