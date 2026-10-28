"""I3最终证据核对：命令、源码、回放、可恢复原件和文档链接。"""

from __future__ import annotations

import ast
from collections import Counter
from datetime import datetime, timezone
import difflib
import hashlib
import json
from pathlib import Path
import re
import statistics

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def read(path: Path) -> dict:
    """仅读取小型JSON凭据，不读取checkpoint或训练数据。"""
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path: Path) -> str:
    """计算明确列出的代码/文档散列，不扫描模型目录。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    """先核对全部证据，再保存总回执，不改任何业务文件。"""
    baseline = read(HERE / "baseline.json")
    protocol = read(HERE / "final-runtime-3/protocol.json")
    matches = {name: sha(ROOT / name) == value
               for name, value in protocol["source_sha256"].items()}
    assert all(matches.values()), matches
    tests = ["test_ga_g2", "test_ga_g3", "test_ga_g5", "test_ga_g6", "test_ga_g7",
             "test_ga_poems", "test_ga_i3_edges", "test_ga_i3_round_traces"]
    paths = [*ROOT.glob("engine/ga/**/*.py"), ROOT / "tools/ga_play.py",
             *(ROOT / "tests" / f"{name}.py" for name in tests),
             ROOT / "tests/test_ga_i2_round_traces.py"]
    audit = {}
    for path in paths:
        data = path.read_bytes()
        source = data.decode("utf-8")
        tree = ast.parse(source)
        assert not data.startswith(b"\xef\xbb\xbf"), path
        assert all(len(line) <= 100 for line in source.splitlines()), path
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
                args += [a for a in (node.args.vararg, node.args.kwarg) if a]
                assert node.returns is not None, (path, node.name)
                assert all(a.annotation for a in args if a.arg not in ("self", "cls")), (
                    path, node.name)
        audit[path.relative_to(ROOT).as_posix()] = {
            "ast": True, "annotations": True, "utf8_without_bom": True, "line_width_100": True}
    log = (HERE / "pytest-final-3.log").read_text(encoding="utf-8")
    result = re.search(r"(\d+) passed,\s*(\d+) subtests passed in ([\d.]+)s", log)
    assert result and (int(result[1]), int(result[2])) == (1152, 2)
    matrices = {}
    for count in (2, 4, 6):
        rows = [json.loads(line) for line in (HERE / "final-runtime-3" /
                f"matrix-{count}.jsonl").read_text(encoding="utf-8").splitlines()]
        assert len(rows) == 20 and all(r["status"] == "passed" for r in rows)
        assert [r["seed"] for r in rows] == list(range(20))
        matrices[str(count)] = {
            "games": 20, "reasons": dict(Counter(r["terminal"]["reason"] for r in rows)),
            "rounds_min": min(r["rounds"] for r in rows),
            "rounds_max": max(r["rounds"] for r in rows),
            "rounds_mean": statistics.mean(r["rounds"] for r in rows),
            "seconds_max": max(r["elapsed_seconds"] for r in rows),
        }
    original = read(HERE / "cli-final3-original.json")
    replay = read(HERE / "cli-final3-replay.json")
    replay_matches = {key: original[key] == replay[key] for key in original
                      if key != "elapsed_seconds"}
    assert all(replay_matches.values()), replay_matches
    manual = "docs/v2.0-ga/manual_draft_v0.1.md"
    protected = {name: sha(ROOT / name) == value for name, value in baseline["protected"].items()
                 if name != manual}
    assert all(protected.values()), protected
    assert sha(ROOT / "engine/game_state.py") == baseline["existing"][
        "engine/game_state.py"]["sha256"]
    docs = [ROOT / "docs/v2.0-ga" / name for name in (
        "README.md", "implementation_contract_v0.1.md")]
    for path in docs:
        for link in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            if "://" not in link and not link.startswith("#"):
                assert (path.parent / link.split("#", 1)[0]).exists(), (path, link)
    patch = []
    for old in (HERE / "originals").rglob("*"):
        if old.is_file():
            relative = old.relative_to(HERE / "originals")
            assert sha(old) == baseline["existing"][relative.as_posix()]["sha256"]
            patch.extend(difflib.unified_diff(
                old.read_text(encoding="utf-8").splitlines(keepends=True),
                (ROOT / relative).read_text(encoding="utf-8").splitlines(keepends=True),
                fromfile="before-i3/" + relative.as_posix(),
                tofile="after-i3/" + relative.as_posix()))
    (HERE / "existing-files.patch").write_text("".join(patch), encoding="utf-8")
    assert "8 场景全过" in (HERE / "m9-smoke.log").read_text(encoding="utf-8")
    receipt = {
        "batch": "I3-five-complex-talents-and-fourteen-poems",
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "head_at_start": baseline["head"], "new_i3_tests": 85,
        "combined_tests": {"passed": int(result[1]), "subtests_passed": int(result[2]),
                           "seconds": float(result[3]), "log": "pytest-final-3.log",
                           "command": read(HERE / "test-command-final-3.json")},
        "old_m9_smoke": {"passed": 8, "scope": "regression, not GA balance"},
        "matrix": {"passed": 60, "failures": 0, "round_limit": 0,
                   "protocol": "final-runtime-3/protocol.json", "by_players": matrices},
        "cli_replay": replay_matches, "cli_lineup": ["G2", "G3", "G5", "G6", "G7", "G1"],
        "cli_seed": 2, "recorded_actions": sum(len(x["actions"]) for x in
                                                original["replay_inputs"].values()),
        "source_matches_final_matrix": matches, "python_audit": audit,
        "protected_inputs_unchanged": protected, "legacy_game_state_unchanged": True,
        "manual": {"written_by_this_batch": False,
                   "unchanged_at_final_check": sha(ROOT / manual) == baseline["protected"][manual],
                   "concurrent_changes_allowed_and_preserved": True},
        "checkpoint_models": {"loaded": False, "trained": False, "copied": False,
                              "modified": False},
        "worktree": {"reset": False, "cleaned": False, "auto_committed": False,
                     "recoverable_originals_verified": True},
        "default_profile": read(ROOT / "config/game_config.json").get("profile"),
        "parameter_fingerprint": original["terminal"]["parameter_fingerprint"],
        "source_sha256": {p.relative_to(ROOT).as_posix(): sha(p) for p in [*paths, *docs]},
        "next": "I4: controllers, observation/action/reward semantics and model contracts",
        "limits": ["No balance claim", "No default main/network/RL migration",
                   "Minimum candidate policy is not I4 BasicAI",
                   "Asynchronous UI response recovery deferred to I4"],
    }
    (HERE / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    print(json.dumps({"audit": "passed", "tests": 1152, "i3_new": 85, "matrix": 60,
                      "replay": all(replay_matches.values()),
                      "artifact_MiB": sum(p.stat().st_size for p in HERE.rglob("*") if
                                          p.is_file()) / 1048576}))


if __name__ == "__main__":
    main()
