"""GA人工调试、全选择记录与严格重放；正式默认入口仍待I5切换。"""

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
from engine.ga.ai.controller import BasicAI
from engine.ga.contracts import ActionRequest, ChoiceWindow, ContractError, TurnStage, thaw_json
from engine.ga.decisions import RecordingController, ReplayController, input_digest
from engine.ga.runtime import Controller, run_game


def console_select(
    view: Mapping[str, Any], actions: tuple[ActionRequest, ...], stage: TurnStage,
) -> ActionRequest | None:
    """调试台显示公开观察、真实费用和候选，输入index或可选T0的null。"""
    print(json.dumps({"view": thaw_json(view), "stage": stage.value,
                      "candidates": [{"index": i, **a.to_dict()}
                                     for i, a in enumerate(actions)]}, ensure_ascii=False))
    while True:
        try:
            selected = json.loads(input())
            if selected is None and stage is TurnStage.T0 and (
                not view["players"][view["actor_id"]]["alive"] or
                view["players"][view["actor_id"]]["awake"]
            ):
                return None
            if type(selected) is int and 0 <= selected < len(actions):
                return actions[selected]
        except (ValueError, IndexError):
            pass
        print('{"error":"select an index, or null to skip optional T0"}')


def console_answer(
    view: Mapping[str, Any], window: ChoiceWindow, context: Mapping[str, Any],
) -> str | None:
    """只显示本人的秘密选择窗口，回答来自真实选项。"""
    print(json.dumps({"view": thaw_json(view), "window_id": window.window_id,
                      "options": window.options, "context": thaw_json(context)},
                     ensure_ascii=False))
    while True:
        selected = input().strip()
        if selected in window.options:
            return selected
        print('{"error":"select one of the listed response options"}')


def console_decide(view: Mapping[str, Any], kind: str, context: Mapping[str, Any]) -> bool:
    """公演、重掷、挣脱各次窗口都要求显式布尔输入。"""
    print(json.dumps({"view": thaw_json(view), "kind": kind, "context": thaw_json(context),
                      "options": [True, False]}, ensure_ascii=False))
    while True:
        try:
            answer = json.loads(input())
            if type(answer) is bool:
                return answer
        except ValueError:
            pass
        print('{"error":"enter true or false"}')


def main() -> int:
    """默认紧凑记录；详细观察按需重放生成，异常输出保留失败前缀。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--players", type=int, choices=range(2, 7))
    parser.add_argument("--seed", type=int)
    parser.add_argument("--talents", help="Comma-separated GA slots; none is allowed")
    parser.add_argument("--human", action="append", default=[], help="Actor ID, e.g. p0")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--script", type=Path, help="Actor -> selectors or decision streams")
    source.add_argument("--replay", type=Path, help="Complete output of a previous ga_play run")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--controller", choices=("minimal", "basic"))
    parser.add_argument("--ai-budget", type=int, help="Fixed candidate/response node budget")
    parser.add_argument("--ai-style", choices=("balanced", "aggressive", "patient"))
    parser.add_argument("--trace-decisions", action="store_true", help="Include full inputs")
    parser.add_argument("--inspect-decision", help="With --replay, print a full actor:index record")
    args = parser.parse_args()
    if args.inspect_decision and not args.replay:
        parser.error("--inspect-decision requires --replay")
    expected = json.loads(args.replay.read_text(encoding="utf-8")) if args.replay else None
    if expected is not None:
        if args.human or args.talents or args.players is not None or args.seed is not None or (
            args.controller is not None or args.ai_budget is not None or args.ai_style is not None
        ):
            parser.error("--replay takes lineup/seed from the recording; omit game overrides")
        initial = expected["initial"]
        state = GameState.new_ga(initial["players"], seed=initial["seed"],
                                 talents=initial["talents"])
        if initial["parameter_fingerprint"] != state.ga_config.fingerprint:
            raise ContractError("replay.config", "parameter fingerprint changed")
        scripts = expected["replay_inputs"]
        if set(scripts) != set(state.players):
            raise ContractError("replay.actors", "requires all original participants")
    else:
        count = args.players if args.players is not None else 2
        selected = args.talents.split(",") if args.talents else ["none"] * count
        if len(selected) != count:
            parser.error("--talents requires one slot per participant")
        talents = {f"p{i}": slot for i, slot in enumerate(selected) if slot != "none"}
        state = GameState.new_ga([f"p{i}" for i in range(count)],
                                 seed=args.seed if args.seed is not None else 0, talents=talents)
        scripts = json.loads(args.script.read_text(encoding="utf-8")) if args.script else {}
        initial = {"players": list(state.players), "seed": state.ga_seed, "talents": talents,
                   "parameter_fingerprint": state.ga_config.fingerprint,
                   "policy": {"controller": args.controller or "minimal",
                              "node_budget": args.ai_budget if args.ai_budget is not None else 96,
                              "style": args.ai_style or "balanced",
                              "version": BasicAI.policy_version if args.controller == "basic"
                              else None}}
    if not isinstance(scripts, dict) or (set(scripts) | set(args.human)) - set(state.players):
        parser.error("script/human actor IDs must belong to this game")
    controllers: dict[str, RecordingController] = {}
    for actor in state.players:
        controller: Controller
        if expected is not None:
            controller = ReplayController(scripts[actor])
        elif actor in args.human:
            controller = HumanController(console_select, answer=console_answer,
                                         decide=console_decide)
        elif actor in scripts and isinstance(scripts[actor], dict):
            script = scripts[actor]
            if "schema" in script:
                controller = ReplayController(script)
            else:
                controller = ScriptController(script["actions"],
                                              responses=script.get("responses", ()),
                                              afterlife=script.get("afterlife", ()))
        elif actor in scripts:
            controller = ScriptController(scripts[actor])
        elif args.controller == "basic":
            controller = BasicAI(state.ga_config, node_budget=(
                args.ai_budget if args.ai_budget is not None else 96),
                style=args.ai_style or "balanced")
        else:
            controller = MinimalController()
        controllers[actor] = RecordingController(
            controller, detailed=args.trace_decisions or bool(args.inspect_decision))
    started = time.perf_counter()
    failure: Exception | None = None
    try:
        run_game(state, controllers, timeout_seconds=args.timeout)
        for recorder in controllers.values():
            if isinstance(recorder.controller, ReplayController):
                recorder.controller.assert_consumed()
    except Exception as exc:
        failure = exc
    receipt = {
        "initial": initial,
        "terminal": state.terminal_snapshot.to_dict() if state.terminal_snapshot else None,
        "score_winners": state.score_winners, "rounds": state.current_round,
        "world_tick": state.world_tick, "elapsed_seconds": time.perf_counter() - started,
        "events": [event.to_dict() for event in state.ga_ledger.events],
        "closure_display": (thaw_json(state.ga_closure_display) if state.ga_closure_display
                            else None),
        "random_draws": state.ga_rng.draws, "trace": state.ga_trace,
        "replay_inputs": {actor: c.replay_inputs() for actor, c in controllers.items()},
        "scope": "GA development game; strategy strength requires independent arena evaluation",
        "policy_diagnostics": {actor: getattr(c.controller, "diagnostics", {})
                               for actor, c in controllers.items()},
        "failure": {"type": type(failure).__name__, "message": str(failure)} if failure else None,
    }
    if expected is not None and failure is None:
        keys = ("terminal", "score_winners", "rounds", "world_tick", "events",
                "closure_display", "random_draws", "trace")
        if input_digest({key: receipt[key] for key in keys}) != input_digest(
            {key: expected[key] for key in keys}
        ):
            failure = ContractError("replay.result", "terminal/events/RNG/trace differ")
            receipt["failure"] = {"type": type(failure).__name__, "message": str(failure)}
        receipt["replay_verified"] = failure is None
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.inspect_decision:
        actor, index = args.inspect_decision.rsplit(":", 1)
        print(json.dumps(controllers[actor].records[int(index)], ensure_ascii=False, indent=2))
    print(json.dumps({key: receipt[key] for key in
                      ("terminal", "score_winners", "rounds", "elapsed_seconds", "failure")},
                     ensure_ascii=False))
    if failure is not None:
        raise failure
    return 0


if __name__ == "__main__":
    sys.exit(main())
