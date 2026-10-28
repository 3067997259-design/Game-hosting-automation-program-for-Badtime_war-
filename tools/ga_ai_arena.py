"""GA 无天赋 BasicAI 有界对抗；默认仅显示协议，显式选择分区才运行。"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.ga.config import GaConfig
from engine.ga.contracts import ActionRequest, TurnStage
from engine.ga.controllers import MinimalController
from engine.ga.runtime import Controller, run_game


PARTITIONS = {"dev": tuple(range(8)), "holdout": tuple(range(1000, 1008))}
GROUPS = ("greedy", "builder", "mixed")
BASELINES = ("greedy", "builder", "police")
RESULT_MARKER = "GA_ARENA_RESULT="
BASELINE_VERSION = "ga.arena.baselines.v1"


@dataclass(frozen=True)
class MatchSpec:
    """单局输入完整指定种子、座次、对手群与被测策略预算。"""

    partition: str
    seed: int
    players: int
    seat: int
    group: str
    node_budget: int = 96
    style: str = "balanced"

    def __post_init__(self) -> None:
        if self.partition not in PARTITIONS or self.seed not in PARTITIONS[self.partition]:
            raise ValueError("seed must belong to the declared partition")
        if self.players not in (2, 4, 6) or not 0 <= self.seat < self.players:
            raise ValueError("invalid player count or seat")
        if self.group not in GROUPS or self.node_budget < 1:
            raise ValueError("invalid opponent group or node budget")

    @property
    def actor(self) -> str:
        """被测策略本局对应的公开玩家编号。"""
        return f"p{self.seat}"


def build_schedule(
    partition: str, *, seeds: Sequence[int] | None = None,
    players: Sequence[int] = (2, 4, 6), groups: Sequence[str] = GROUPS,
    node_budget: int = 96, style: str = "balanced",
) -> tuple[MatchSpec, ...]:
    """先锁种子与阵容，再轮换全部座位；不根据胜负追加或删掉对局。"""
    if partition not in PARTITIONS:
        raise ValueError("unknown partition")
    chosen = PARTITIONS[partition] if seeds is None else tuple(seeds)
    if not chosen or not players or not groups:
        raise ValueError("schedule selections cannot be empty")
    if any(len(set(values)) != len(values) for values in (chosen, players, groups)):
        raise ValueError("duplicate schedule entries are not allowed")
    return tuple(MatchSpec(partition, seed, count, seat, group, node_budget, style)
                 for seed in chosen for count in players for group in groups
                 for seat in range(count))


def opponents(spec: MatchSpec) -> dict[str, str]:
    """相同种子下整体旋转阵容，混合群的类型不因被测座位改变。"""
    return {f"p{(spec.seat + offset) % spec.players}": (
        BASELINES[(spec.seed + offset + 1) % len(BASELINES)] if spec.group == "mixed"
        else spec.group) for offset in range(1, spec.players)}


class ArenaBaseline(MinimalController):
    """独立固定策略：直接抢分、生命/火弓发育、警队运营；不调用 BasicAI。"""

    def __init__(self, config: GaConfig, style: str) -> None:
        if style not in BASELINES:
            raise ValueError("unknown baseline")
        self.config = config
        self.style = style

    @staticmethod
    def _find(
        actions: tuple[ActionRequest, ...], operation: str, **parameters: Any,
    ) -> ActionRequest | None:
        """只从当前真实候选查找，不跨轮保存已经过期的请求。"""
        return next((a for a in actions if a.operation == operation and
                     all(a.parameters.get(k) == v for k, v in parameters.items())), None)

    @staticmethod
    def _attacks(
        view: Mapping[str, Any], actions: tuple[ActionRequest, ...],
    ) -> list[tuple[tuple[float, ...], ActionRequest]]:
        """按注册原始伤害排序；这是故意简单的基线估计，不称为真实净伤或胜率。"""
        result = []
        quotes = view.get("candidates", ())
        for index, action in enumerate(actions):
            if action.operation != "attack" or not action.target_ids:
                continue
            quote = quotes[index] if index < len(quotes) else {}
            amount = sum(e.get("amount", 0) for e in quote.get("declared_effects", ()))
            targets = [view["players"][key] for key in action.target_ids if key in view["players"]]
            if not targets:
                continue
            kills = sum(p["hp"] <= amount for p in targets)
            damage = sum(min(p["hp"], amount) for p in targets)
            urgency = -min(p["hp"] for p in targets)
            result.append(((kills, damage, urgency, -index), action))
        return sorted(result, key=lambda item: item[0], reverse=True)

    def choose(
        self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...], stage: TurnStage,
    ) -> ActionRequest | None:
        """固定基线各自有不同资源路线与机会成本，所有选择仍由引擎预检。"""
        find = self._find
        me = view["players"][view["actor_id"]]
        if stage is TurnStage.T0:
            wake = find(actions, "wake", choice="knife" if self.style == "greedy" else "shield")
            if wake:
                return wake
            if me["max_hp"] - me["hp"] >= self.config.get("hospital", "syringe_heal"):
                if candidate := find(actions, "syringe"):
                    return candidate
            mounts = [a for a in actions if a.operation == "modules" and
                      "fire" in a.parameters.get("modules", ())]
            if mounts and "fire" not in me["modules"]:
                return max(mounts, key=lambda a: len(a.parameters["modules"]))
            if candidate := find(actions, "sharpen"):
                return candidate
            if self.style == "police":
                if candidate := find(actions, "police_stance", stance="mobile"):
                    return candidate
            locks = [a for a in actions if a.operation == "lock"]
            return min(locks, key=lambda a: view["players"][a.target_ids[0]]["hp"], default=None)
        attacks = self._attacks(view, actions)
        if attacks and attacks[0][0][0]:
            return attacks[0][1]
        if me["hp"] <= me["max_hp"] // 2:
            if candidate := find(actions, "heal"):
                return candidate
        if self.style == "builder":
            if candidate := self._build(view, actions):
                return candidate
        if self.style == "police":
            if candidate := self._police(view, actions):
                return candidate
        if attacks:
            return attacks[0][1]
        for operation in ("loot", "refill", "search", "buy_weapon"):
            parameters = {"weapon": "knife"} if operation == "buy_weapon" else {}
            if candidate := find(actions, operation, **parameters):
                return candidate
        if me["location"] == "shop":
            if candidate := find(actions, "work"):
                return candidate
        if candidate := find(actions, "move", location="shop"):
            return candidate
        return super().choose(view, actions, stage)

    def _build(
        self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...],
    ) -> ActionRequest | None:
        """有限投资路线：早局生命手术，随后至多一枚火模块，不无限堆库存。"""
        me = view["players"][view["actor_id"]]
        find = self._find
        if me["surgery"] is None and view["world_tick"] < self.config.get("clock", "day_start"):
            if candidate := find(actions, "surgery", kind="life"):
                return candidate
            if me["credits"] >= self.config.get("hospital", "surgery_min_price"):
                return find(actions, "move", location="hospital")
        owns_fire = "fire" in me["modules"] or me["inventory"].get("module:fire", 0)
        if not owns_fire and view["module_stock"]["fire"] and (
            view["world_tick"] < self.config.get("clock", "dusk_start")
        ):
            if candidate := find(actions, "buy_module", kind="fire"):
                return candidate
            if me["credits"] >= self.config.get("modules", "fire", "price"):
                return find(actions, "move", location="magic")
            return find(actions, "work") or find(actions, "move", location="shop")
        return None

    def _police(
        self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...],
    ) -> ActionRequest | None:
        """争取队长、合法举报、攻击通缉者；不对无辜目标发出减编命令。"""
        police = view["police"]
        actor = view["actor_id"]
        find = self._find
        if candidate := find(actions, "police_apply"):
            return candidate
        if candidate := find(actions, "police_report"):
            return candidate
        if not police["captain"] and actor not in police["wanted"] and (
            actor not in police["applicants"] and not police["stopped"]
        ):
            return find(actions, "move", location="police")
        if police["captain"] == actor:
            coordinated = [action for _, action in self._attacks(view, actions)
                           if action.target_ids[0] in police["wanted"]]
            if coordinated and police["stance"] == "mobile":
                return coordinated[0]
            commands = [a for a in actions if a.operation == "police_command" and
                        a.target_ids[0] in police["wanted"] and
                        view["players"][a.target_ids[0]]["isolation_id"] ==
                        view["players"][actor]["isolation_id"] and
                        not view["players"][a.target_ids[0]]["hidden"]]
            if commands and police["attack_round"] != view["round"]:
                return min(commands, key=lambda a: view["players"][a.target_ids[0]]["hp"])
        return None

    def choose_afterlife(
        self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...],
    ) -> ActionRequest | None:
        """免费押注公开战果领先者，避免所有基线死亡后都无条件放弃得分。"""
        bets = [a for a in actions if a.operation == "afterlife_bet"]
        return max(bets, key=lambda a: (
            view["players"][a.target_ids[0]]["score"]["components"]["total"],
            view["players"][a.target_ids[0]]["hp"]), default=None)


def run_match(
    spec: MatchSpec, *, factory: Callable[[GaConfig], Controller] | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """子进程内跑一局自然资源对局；工厂仅供隔离测试，不读取真实随机流。"""
    from engine.game_state import GameState

    started = time.perf_counter()
    state = GameState.new_ga([f"p{i}" for i in range(spec.players)], seed=spec.seed)
    if factory is None:
        from engine.ga.ai.controller import BasicAI
        focal = BasicAI(state.ga_config, node_budget=spec.node_budget, style=spec.style)
    else:
        focal = factory(state.ga_config)
    controllers = {actor: ArenaBaseline(state.ga_config, style)
                   for actor, style in opponents(spec).items()}
    controllers[spec.actor] = focal
    terminal = run_game(state, controllers, timeout_seconds=timeout)
    diagnostics = dict(getattr(focal, "diagnostics", {}))
    return {
        "spec": asdict(spec), "status": "completed", "opponents": opponents(spec),
        "parameter_fingerprint": state.ga_config.fingerprint,
        "policy_version": getattr(focal, "policy_version", None),
        "scores": {key: value.total_score for key, value in terminal.results.items()},
        "score_winners": list(state.score_winners), "survivors": list(terminal.survivor_ids),
        "rounds": state.current_round, "round_limit": terminal.reason.value == "round_limit",
        "wall_seconds": time.perf_counter() - started,
        "decision_seconds": diagnostics.get("total_seconds", 0.0),
        "decisions": diagnostics.get("decisions", 0), "nodes": diagnostics.get("nodes", 0),
        "max_nodes": diagnostics.get("max_nodes", 0),
    }


def run_isolated(spec: MatchSpec, *, timeout: float) -> dict[str, Any]:
    """单局硬超时覆盖控制器死循环；失败仅记一次，不自动换种子或重试。"""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("worker timeout must be finite and positive")
    started = time.perf_counter()
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--worker",
               json.dumps(asdict(spec)), "--game-timeout", str(timeout)]
    try:
        process = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=timeout)
        lines = [line[len(RESULT_MARKER):] for line in process.stdout.splitlines()
                 if line.startswith(RESULT_MARKER)]
        if process.returncode or len(lines) != 1:
            raise RuntimeError(f"worker exit {process.returncode}: {process.stderr[-2000:]}")
        result = json.loads(lines[0])
        result["wall_seconds"] = time.perf_counter() - started
        return result
    except (subprocess.TimeoutExpired, ValueError, RuntimeError, OSError) as exc:
        return {"spec": asdict(spec), "status": "failed", "error": str(exc),
                "wall_seconds": time.perf_counter() - started}


def summarize(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """失败留在胜率分母；只对成功局报告分差，避免伪造失败局得分。"""
    completed = [r for r in records if r["status"] == "completed"]
    count = len(records)
    shared = 0.0
    sole = tied = survivor = limits = 0
    margins = []
    for record in completed:
        actor = f"p{record['spec']['seat']}"
        winners = record["score_winners"]
        if actor in winners:
            shared += 1 / len(winners)
            sole += len(winners) == 1
            tied += len(winners) > 1
        survivor += record["survivors"] == [actor]
        limits += record["round_limit"]
        margins.append(record["scores"][actor] - max(
            score for key, score in record["scores"].items() if key != actor))
    decisions = sum(r.get("decisions", 0) for r in completed)
    decision_seconds = sum(r.get("decision_seconds", 0.0) for r in completed)
    return {
        "scheduled": count, "completed": len(completed),
        "failures": sum(r["status"] == "failed" for r in records),
        "not_run": sum(r["status"] == "not_run" for r in records),
        "shared_first_credit": shared, "shared_first_rate": shared / count if count else 0.0,
        "sole_first_rate": sole / count if count else 0.0,
        "tied_first_rate": tied / count if count else 0.0,
        "last_survivor_rate": survivor / count if count else 0.0,
        "round_limit_rate": limits / count if count else 0.0,
        "mean_margin_completed": statistics.mean(margins) if margins else None,
        "wall_seconds": sum(r.get("wall_seconds", 0.0) for r in records),
        "decision_seconds": decision_seconds, "decisions": decisions,
        "mean_decision_seconds": decision_seconds / decisions if decisions else None,
        "nodes": sum(r.get("nodes", 0) for r in completed),
        "max_nodes": max((r.get("max_nodes", 0) for r in completed), default=0),
    }


def evaluate(
    schedule: Sequence[MatchSpec], *, game_timeout: float = 20.0, budget_seconds: float = 600.0,
    runner: Callable[..., dict[str, Any]] = run_isolated,
) -> dict[str, Any]:
    """锁定赛程后逐局运行；总预算耗尽明确列出未运行项，不能冒充通过。"""
    if any(not math.isfinite(value) or value <= 0 for value in (game_timeout, budget_seconds)):
        raise ValueError("time budgets must be finite and positive")
    started = time.monotonic()
    records = []
    for spec in schedule:
        remaining = budget_seconds - (time.monotonic() - started)
        if remaining <= 0:
            records.append({"spec": asdict(spec), "status": "not_run",
                            "error": "declared arena budget exhausted", "wall_seconds": 0.0})
        else:
            records.append(runner(spec, timeout=min(game_timeout, remaining)))
    by_group = {group: summarize([r for r in records if r["spec"]["group"] == group])
                for group in sorted({spec.group for spec in schedule})}
    clusters = {}
    for spec in schedule:
        key = f"{spec.group}/{spec.players}/{spec.seed}"
        if key not in clusters:
            clusters[key] = summarize([r for r in records if all(
                r["spec"][field] == getattr(spec, field)
                for field in ("group", "players", "seed"))])
    by_players = {str(count): summarize([r for r in records if r["spec"]["players"] == count])
                  for count in sorted({spec.players for spec in schedule})}
    return {"schema": "ga.ai.arena.v1", "baseline_version": BASELINE_VERSION,
            "game_timeout": game_timeout, "budget_seconds": budget_seconds,
            "summary": summarize(records), "by_group": by_group, "by_players": by_players,
            "by_seed_lineup": clusters,
            "matches": records,
            "interpretation": "Seat rotations share a seed; these are clustered pilot estimates, "
                              "not calibrated win probabilities or proof of balance."}


def main() -> int:
    """默认不启动对局；显式分区运行的完整赛程为每区288局。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--partition", choices=PARTITIONS)
    parser.add_argument("--seeds", help="Comma-separated subset of the declared partition")
    parser.add_argument("--players", default="2,4,6")
    parser.add_argument("--groups", default=",".join(GROUPS))
    parser.add_argument("--node-budget", type=int, default=96)
    parser.add_argument("--style", default="balanced")
    parser.add_argument("--game-timeout", type=float, default=20.0)
    parser.add_argument("--budget-seconds", type=float, default=600.0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        result = run_match(MatchSpec(**json.loads(args.worker)), timeout=args.game_timeout)
        print(RESULT_MARKER + json.dumps(result, ensure_ascii=True))
        return 0
    if args.partition is None:
        print(json.dumps({"partitions": PARTITIONS, "players": [2, 4, 6], "groups": GROUPS,
                          "games_per_partition": 288, "baseline_version": BASELINE_VERSION,
                          "run_requires": "--partition dev|holdout; no games were run",
                          "pilot_example": "--partition dev --seeds 0 --players 2 "
                                           "--groups greedy,builder,mixed"}, indent=2))
        return 0
    try:
        schedule = build_schedule(
            args.partition, seeds=tuple(map(int, args.seeds.split(","))) if args.seeds else None,
            players=tuple(map(int, args.players.split(","))), groups=args.groups.split(","),
            node_budget=args.node_budget, style=args.style)
        report = evaluate(schedule, game_timeout=args.game_timeout,
                          budget_seconds=args.budget_seconds)
    except ValueError as exc:
        parser.error(str(exc))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary": report["summary"], "by_group": report["by_group"]}, indent=2))
    return int(report["summary"]["failures"] + report["summary"]["not_run"] > 0)


if __name__ == "__main__":
    sys.exit(main())
