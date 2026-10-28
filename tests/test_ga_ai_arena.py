"""I4.1 对抗协议、公平统计与真实基线对局；不把脚手架通过当成策略强度。"""

from __future__ import annotations

from dataclasses import asdict
import json
import subprocess
import sys
from typing import Any

import pytest

from tools import ga_ai_arena as arena


def record(
    spec: arena.MatchSpec, *, tie: bool = False, failed: bool = False,
) -> dict[str, Any]:
    """固定统计夹具；失败局不编造分数。"""
    if failed:
        return {"spec": asdict(spec), "status": "failed", "wall_seconds": 3.0}
    return {"spec": asdict(spec), "status": "completed", "scores": {"p0": 10, "p1": 10
            if tie else 8}, "score_winners": ["p0", "p1"] if tie else ["p0"],
            "survivors": ["p1"] if tie else ["p0"], "round_limit": tie,
            "wall_seconds": 2.0, "decision_seconds": 0.3, "decisions": 3, "nodes": 12,
            "max_nodes": 5}


def test_schedule_rotates_every_seat_and_keeps_partitions_disjoint() -> None:
    development = arena.build_schedule("dev")
    held_out = arena.build_schedule("holdout")
    assert len(development) == len(held_out) == 288
    assert {s.seed for s in development}.isdisjoint(s.seed for s in held_out)
    for seed in arena.PARTITIONS["dev"]:
        for players in (2, 4, 6):
            for group in arena.GROUPS:
                block = [s for s in development if (s.seed, s.players, s.group) ==
                         (seed, players, group)]
                assert {s.seat for s in block} == set(range(players))
                rotations = [tuple(arena.opponents(s)[f"p{(s.seat + offset) % players}"]
                                   for offset in range(1, players)) for s in block]
                assert len(set(rotations)) == 1


@pytest.mark.parametrize("arguments", [
    {"seeds": (1000,)}, {"seeds": (0, 0)}, {"players": (3,)},
    {"groups": ("unknown",)}, {"node_budget": 0}, {"seeds": ()},
])
def test_schedule_rejects_partition_leaks_and_invalid_scope(arguments: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        arena.build_schedule("dev", **arguments)


def test_shared_first_statistics_include_failure_in_denominator() -> None:
    spec = arena.MatchSpec("dev", 0, 2, 0, "greedy")
    result = arena.summarize([record(spec), record(spec, tie=True), record(spec, failed=True)])
    assert result["scheduled"] == 3 and result["completed"] == 2 and result["failures"] == 1
    assert result["shared_first_credit"] == 1.5 and result["shared_first_rate"] == 0.5
    assert result["sole_first_rate"] == result["tied_first_rate"] == 1 / 3
    assert result["last_survivor_rate"] == result["round_limit_rate"] == 1 / 3
    assert result["mean_margin_completed"] == 1
    assert result["wall_seconds"] == 7 and result["decision_seconds"] == 0.6
    assert result["nodes"] == 24 and result["max_nodes"] == 5
    assert arena.summarize([record(spec, failed=True)])["mean_margin_completed"] is None


def test_failed_matches_are_not_retried_or_dropped() -> None:
    schedule = arena.build_schedule("dev", seeds=(0,), players=(2,), groups=("greedy",))
    calls = []

    def runner(spec: arena.MatchSpec, *, timeout: float) -> dict[str, Any]:
        calls.append((spec, timeout))
        return record(spec, failed=True)

    report = arena.evaluate(schedule, runner=runner, game_timeout=2, budget_seconds=10)
    assert [spec for spec, _ in calls] == list(schedule)
    assert all(timeout == 2 for _, timeout in calls)
    assert report["summary"]["failures"] == report["summary"]["scheduled"] == 2
    assert report["summary"]["shared_first_rate"] == 0


def test_budget_exhaustion_records_unrun_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    schedule = arena.build_schedule("dev", seeds=(0,), players=(2,), groups=("greedy",))
    clock = iter((0.0, 0.0, 11.0))
    monkeypatch.setattr(arena.time, "monotonic", lambda: next(clock))

    def runner(spec: arena.MatchSpec, *, timeout: float) -> dict[str, Any]:
        return record(spec)

    report = arena.evaluate(schedule, runner=runner, budget_seconds=10)
    assert report["summary"]["completed"] == report["summary"]["not_run"] == 1
    assert report["summary"]["shared_first_rate"] == 0.5


def test_worker_hard_timeout_becomes_one_failed_match(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def blocked(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(arena.subprocess, "run", blocked)
    result = arena.run_isolated(arena.MatchSpec("dev", 0, 2, 0, "greedy"), timeout=0.1)
    assert result["status"] == "failed" and len(calls) == 1
    assert "timed out" in result["error"]


@pytest.mark.parametrize("limit", [0.0, -1.0, float("nan"), float("inf")])
def test_nonfinite_or_nonpositive_budgets_are_rejected(limit: float) -> None:
    schedule = arena.build_schedule("dev", seeds=(0,), players=(2,), groups=("greedy",))
    with pytest.raises(ValueError):
        arena.evaluate(schedule, budget_seconds=limit)
    with pytest.raises(ValueError):
        arena.run_isolated(schedule[0], timeout=limit)


def test_default_command_only_displays_protocol(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["ga_ai_arena.py"])
    assert arena.main() == 0
    protocol = json.loads(capsys.readouterr().out)
    assert protocol["games_per_partition"] == 288
    assert "no games were run" in protocol["run_requires"]


@pytest.mark.parametrize("group", arena.GROUPS)
def test_independent_baselines_complete_real_natural_resource_games(group: str) -> None:
    spec = arena.MatchSpec("dev", 0, 2, 0, group)
    result = arena.run_match(spec, factory=lambda cfg: arena.ArenaBaseline(cfg, "greedy"))
    assert result["status"] == "completed" and result["rounds"] <= 40
    assert set(result["scores"]) == {"p0", "p1"} and result["score_winners"]


def test_basicai_worker_completes_real_game_with_recorded_budget() -> None:
    result = arena.run_isolated(arena.MatchSpec("dev", 0, 2, 0, "greedy", 16), timeout=20)
    assert result["status"] == "completed", result
    assert result["decisions"] > 0 and 0 < result["max_nodes"] <= 16
    assert result["wall_seconds"] >= result["decision_seconds"] >= 0
