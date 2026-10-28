"""I4.0独立进程重放及失败输出；产物仅写入pytest临时目录。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from engine.ga.controllers import HumanController
from engine.ga.decisions import RecordingController
from engine.ga.extensions import ChoicePending, ask
from engine.ga.state import observe
from tests.ga_i2_helpers import ready


ROOT = Path(__file__).resolve().parents[1]


def cli(*args: str, hash_seed: str = "0") -> subprocess.CompletedProcess[str]:
    """真实命令行入口，显式有界运行并改变进程哈希种子验证稳定性。"""
    env = {**os.environ, "PYTHONHASHSEED": hash_seed, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, "-B", "tools/ga_play.py", *args], cwd=ROOT,
                          env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)


def test_six_player_cli_replay_and_single_decision_inspection(tmp_path: Path) -> None:
    original, replayed = tmp_path / "original.json", tmp_path / "replayed.json"
    run = cli("--players", "6", "--seed", "2", "--talents", "G2,G3,G5,G6,G7,G1",
              "--output", str(original))
    assert run.returncode == 0, run.stderr
    repeat = cli("--replay", str(original), "--inspect-decision", "p0:0", "--output",
                 str(replayed), hash_seed="37")
    assert repeat.returncode == 0, repeat.stderr
    before, after = (json.loads(p.read_text(encoding="utf-8")) for p in (original, replayed))
    assert after["replay_verified"]
    for key in ("terminal", "events", "random_draws", "trace", "score_winners", "closure_display"):
        assert before[key] == after[key]
    for actor, stream in before["replay_inputs"].items():
        new = after["replay_inputs"][actor]["decisions"]
        for prior, current in zip(stream["decisions"], new, strict=True):
            assert {k: v for k, v in current.items() if k != "input"} == prior
    assert '"input"' in repeat.stdout and '"candidates"' in repeat.stdout


def test_cli_mismatch_retains_failure_prefix_and_nonzero_exit(tmp_path: Path) -> None:
    original, broken, failure = (tmp_path / name for name in ("ok.json", "bad.json", "fail.json"))
    assert cli("--output", str(original)).returncode == 0
    content = json.loads(original.read_text(encoding="utf-8"))
    for stream in content["replay_inputs"].values():
        stream["decisions"][0]["input_digest"] = "wrong"
    broken.write_text(json.dumps(content), encoding="utf-8")
    result = cli("--replay", str(broken), "--output", str(failure))
    assert result.returncode != 0
    report = json.loads(failure.read_text(encoding="utf-8"))
    assert report["terminal"] is None
    assert "input/order mismatch" in report["failure"]["message"]
    failures = [r for s in report["replay_inputs"].values() for r in s["decisions"] if "error" in r]
    assert len(failures) == 1 and "input" in failures[0]


@pytest.mark.parametrize("wrapped", [False, True])
def test_full_replay_rejects_legacy_selector_streams(tmp_path: Path, wrapped: bool) -> None:
    original, broken, output = (tmp_path / name for name in ("ok.json", "old.json", "out.json"))
    assert cli("--output", str(original)).returncode == 0
    content = json.loads(original.read_text(encoding="utf-8"))
    for actor, stream in content["replay_inputs"].items():
        actions = [r["selected"] for r in stream["decisions"] if r["kind"] == "choose"]
        content["replay_inputs"][actor] = {"actions": actions} if wrapped else actions
    broken.write_text(json.dumps(content), encoding="utf-8")
    result = cli("--replay", str(broken), "--output", str(output))
    assert result.returncode != 0 and "replay" in result.stderr
    assert not output.exists()  # 格式预检失败，不能隐式采用旧选择器并开始运行。


def test_human_boolean_windows_go_to_input_callback() -> None:
    seen = []

    def decide(view: Any, kind: str, context: Any) -> bool:
        seen.append((kind, context))
        return kind == "reroll_initiative"

    human = HumanController(lambda view, actions, stage: None, decide=decide)
    view = observe(ready(), "a")
    assert not human.wants_public(view, ())
    assert human.reroll_initiative(view, 1)
    assert not human.escape_petrify(view, 0)
    assert [item[0] for item in seen] == ["wants_public", "reroll_initiative", "escape_petrify"]


def test_unanswered_required_window_is_recorded_as_pending_not_acceptance() -> None:
    state = ready()
    recorder = RecordingController(HumanController(lambda view, actions, stage: None))
    state.ga_controllers["a"] = recorder
    with pytest.raises(ChoicePending):
        ask(state, "b", "a", ("accept", "decline"), kind="permission")
    assert recorder.records[0]["selected"] is None
    assert recorder.records[0]["point"]["window_id"]


def test_basicai_cli_replays_across_process_hash_seeds(tmp_path: Path) -> None:
    original, repeated = tmp_path / "basic.json", tmp_path / "repeated.json"
    first = cli("--controller", "basic", "--seed", "4", "--output", str(original))
    assert first.returncode == 0, first.stderr
    replay = cli("--replay", str(original), "--output", str(repeated), hash_seed="37")
    assert replay.returncode == 0, replay.stderr
    before, after = (json.loads(path.read_text(encoding="utf-8")) for path in (original, repeated))
    assert before["initial"]["policy"]["version"] == "ga.basicai.i41.v1"
    assert after["replay_verified"]
    assert before["replay_inputs"] == after["replay_inputs"]
    assert all(d["max_nodes"] <= 96 for d in before["policy_diagnostics"].values())


def test_new_t2_cli_replays_across_process_hash_seeds(tmp_path: Path) -> None:
    original, replayed = tmp_path / "t2.json", tmp_path / "t2-replayed.json"
    run = cli("--players", "6", "--seed", "2", "--talents", "T2,T3,T4,G6,G1,T6",
              "--output", str(original))
    assert run.returncode == 0, run.stderr
    repeat = cli("--replay", str(original), "--output", str(replayed), hash_seed="37")
    assert repeat.returncode == 0, repeat.stderr
    before, after = (json.loads(p.read_text(encoding="utf-8")) for p in (original, replayed))
    assert after["replay_verified"]
    for key in ("terminal", "events", "random_draws", "trace", "replay_inputs"):
        assert before[key] == after[key]
    operations = {entry.get("operation") for entry in before["trace"]}
    assert operations & {"t2_take", "t2_fear"}
    assert "t2_strike" not in operations
