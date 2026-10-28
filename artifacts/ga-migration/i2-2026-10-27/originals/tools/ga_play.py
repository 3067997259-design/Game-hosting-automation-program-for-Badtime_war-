"""I1候选GA运行/重放工具；尚不切换正式main/RL/联机默认入口。"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
import sys
import time
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.game_state import GameState
from engine.ga.controllers import HumanController, MinimalController, ScriptController
from engine.ga.contracts import ActionRequest, TurnStage, thaw_json
from engine.ga.runtime import Controller, run_game


class Recorder:
    """记录控制器原始选择，包含跳过T0，便于精确脚本重放。"""

    def __init__(self, controller: Controller) -> None:
        self.controller = controller
        self.choices: list[dict[str, Any] | None] = []

    def choose(
        self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...], stage: TurnStage,
    ) -> ActionRequest | None:
        """记录选择，不改写合法候选。"""
        result = self.controller.choose(view, actions, stage)
        self.choices.append(None if result is None else result.to_dict())
        return result

    def wants_public(self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...]) -> bool:
        """转交申请；无天赋没有演出候选。"""
        return self.controller.wants_public(view, actions)

    def reroll_initiative(self, view: Mapping[str, Any], value: int) -> bool:
        """转交先攻决定。"""
        return self.controller.reroll_initiative(view, value)

    def escape_petrify(self, view: Mapping[str, Any], attempt: int) -> bool:
        """转交有限挣脱决定。"""
        return self.controller.escape_petrify(view, attempt)


def console_select(
    view: Mapping[str, Any], actions: tuple[ActionRequest, ...], stage: TurnStage,
) -> ActionRequest | None:
    """开发调试台用JSON显示真实候选，输入其index或T0的null。"""
    print(json.dumps({"view": thaw_json(view), "stage": stage.value,
                      "candidates": [{"index": i, **a.to_dict()}
                                     for i, a in enumerate(actions)]}, ensure_ascii=False))
    while True:
        try:
            selected = json.loads(input())
            if selected is None and stage is TurnStage.T0 and view["players"][
                view["actor_id"]
            ]["awake"]:
                return None
            if type(selected) is int and 0 <= selected < len(actions):
                return actions[selected]
        except (ValueError, IndexError):
            pass
        print('{"error":"select a candidate index, or null to skip optional T0"}')


def main() -> None:
    """有限对局保存终局、规则身份、随机和完整阶段轨迹。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--players", type=int, choices=range(2, 7), default=2)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--human", action="append", default=[], help="Actor ID, e.g. p0")
    parser.add_argument("--script", type=Path, help="JSON actor -> explicit selectors/requests")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, help="Declared cooperative wall-clock budget")
    args = parser.parse_args()
    state = GameState.new_ga([f"p{i}" for i in range(args.players)], seed=args.seed)
    scripts = json.loads(args.script.read_text(encoding="utf-8")) if args.script else {}
    if not isinstance(scripts, dict) or (set(scripts) | set(args.human)) - set(state.players):
        parser.error("script/human actor IDs must belong to this game")
    controllers = {}
    for actor in state.players:
        controller = HumanController(console_select) if actor in args.human else (
            ScriptController(scripts[actor]) if actor in scripts else MinimalController()
        )
        controllers[actor] = Recorder(controller)
    started = time.perf_counter()
    terminal = run_game(state, controllers, timeout_seconds=args.timeout)
    receipt = {
        "terminal": terminal.to_dict(), "score_winners": state.score_winners,
        "rounds": state.current_round, "world_tick": state.world_tick,
        "elapsed_seconds": time.perf_counter() - started,
        "events": [event.to_dict() for event in state.ga_ledger.events],
        "random_draws": state.ga_rng.draws, "trace": state.ga_trace,
        "choices": {actor: c.choices for actor, c in controllers.items()},
        "scope": "I1 no talents; police/afterlife/role adapters not implemented",
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: receipt[key] for key in
                      ("terminal", "score_winners", "rounds", "world_tick", "elapsed_seconds")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
