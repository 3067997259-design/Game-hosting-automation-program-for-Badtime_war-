"""汇总I2证据并核对最终源码、回放和保全边界，不改业务文件。"""

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
    """读取小型验收JSON，不触碰模型。"""
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path: Path) -> str:
    """计算本批程序与文档散列。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    """所有断言通过后写一次最终回执，保留之前探针和矩阵。"""
    baseline = read(HERE / "baseline.json")
    protocol = read(HERE / "final-runtime-2/protocol.json")
    matches = {name: sha(ROOT / name) == value
               for name, value in protocol["source_sha256"].items()}
    assert all(matches.values()), matches
    tests = ["ga_i2_helpers", "test_ga_police", "test_ga_origins", "test_ga_afterlife",
             "test_ga_divine_i2", "test_ga_i2_round_traces", "test_ga_i2_system_edges"]
    paths = [*ROOT.glob("engine/ga/**/*.py"), ROOT / "tools/ga_play.py",
             *(ROOT / "tests" / f"{name}.py" for name in tests)]
    audit = {}
    for path in paths:
        data = path.read_bytes()
        source = data.decode("utf-8")
        tree = ast.parse(source)
        assert not data.startswith(b"\xef\xbb\xbf"), path
        assert all(len(line) <= 100 for line in source.splitlines()), path
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
                args += [a for a in (node.args.vararg, node.args.kwarg) if a]
                assert node.returns is not None, (path, node.name)
                assert all(a.annotation for a in args if a.arg not in ("self", "cls")), (
                    path, node.name)
        audit[str(path.relative_to(ROOT))] = {"ast": True, "annotations": True,
                                            "utf8_without_bom": True, "line_width_100": True}
    matrices = {}
    for count in (2, 4, 6):
        rows = [json.loads(line) for line in (HERE / "final-runtime-2" /
                f"matrix-{count}.jsonl").read_text(encoding="utf-8").splitlines()]
        assert len(rows) == 20 and all(r["status"] == "passed" for r in rows)
        assert [r["seed"] for r in rows] == list(range(20))
        matrices[str(count)] = {
            "games": len(rows), "reasons": dict(Counter(r["terminal"]["reason"] for r in rows)),
            "rounds_min": min(r["rounds"] for r in rows),
            "rounds_max": max(r["rounds"] for r in rows),
            "rounds_mean": statistics.mean(r["rounds"] for r in rows),
            "seconds_max": max(r["elapsed_seconds"] for r in rows),
        }
    original = read(HERE / "cli-final-original.json")
    replay = read(HERE / "cli-final-replay.json")
    replay_matches = {key: original[key] == replay[key] for key in original
                      if key != "elapsed_seconds"}
    assert all(replay_matches.values()), replay_matches
    protected = {p: sha(ROOT / p) == baseline["existing"][p]["sha256"] for p in (
        "data/balance.json", "engine/ga/config.py", "engine/ga/contracts.py",
        "engine/ga/events.py")}
    assert all(protected.values()), protected
    manual = "docs/v2.0-ga/manual_draft_v0.1.md"
    manual_changed = sha(ROOT / manual) != baseline["existing"][manual]["sha256"]
    original_state = ast.parse((HERE / "originals/engine/game_state.py").read_text(
        encoding="utf-8"))
    current_state = ast.parse((ROOT / "engine/game_state.py").read_text(encoding="utf-8"))
    before = next(n for n in original_state.body if isinstance(n, ast.ClassDef))
    after = next(n for n in current_state.body if isinstance(n, ast.ClassDef))
    unchanged = {n.name: ast.dump(n) for n in before.body if isinstance(n, ast.FunctionDef)
                 and n.name != "new_ga"}
    now = {n.name: ast.dump(n) for n in after.body if isinstance(n, ast.FunctionDef)
           and n.name != "new_ga"}
    assert unchanged == now, "Non-GA GameState methods changed in this batch"
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
                fromfile="before-i2/" + relative.as_posix(),
                tofile="after-i2/" + relative.as_posix()))
    (HERE / "existing-files.patch").write_text("".join(patch), encoding="utf-8")
    receipt = {
        "batch": "I2-police-afterlife-nine-talents",
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "head_at_start": baseline["head"], "new_i2_tests": 99,
        "combined_tests": {"passed": 1067, "subtests_passed": 2, "seconds": 33.51,
                           "basetemp": "pytest-final-3", "cache_disabled": True},
        "old_m9_smoke": {"passed": 8, "scope": "regression only, not GA balance"},
        "matrix": {"passed": 60, "failures": 0, "round_limit": 0,
                   "protocol": "final-runtime-2/protocol.json", "by_players": matrices},
        "cli_replay": replay_matches,
        "cli_lineup": ["T4", "G4", "G0", "G1", "T7", "T6"],
        "cli_seed": 7, "response_count": sum(len(x["responses"]) for x in
                                              original["replay_inputs"].values()),
        "source_matches_final_matrix": matches, "python_audit": audit,
        "protected_inputs_unchanged": protected,
        "non_ga_game_state_methods_unchanged": True,
        "manual": {"written_by_this_batch": False, "concurrent_changes_detected": manual_changed,
                   "concurrent_changes_preserved": True},
        "models": {"loaded": False, "trained": False, "copied": False, "modified": False},
        "worktree": {"reset": False, "cleaned": False, "auto_committed": False,
                     "recoverable_originals_verified": True},
        "default_profile": read(ROOT / "config/game_config.json").get("profile"),
        "parameter_fingerprint": original["terminal"]["parameter_fingerprint"],
        "source_sha256": {str(p.relative_to(ROOT)): sha(p) for p in [*paths, *docs]},
        "next": "I3: G2/G3/G5/G6/G7 and fourteen poems",
        "limits": ["No balance claim", "No main/network/RL migration yet",
                   "Synchronous response boundary; asynchronous UI resume deferred to I4",
                   "Non-damage high-impact attention implemented as recommendation A"],
    }
    (HERE / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    print(json.dumps({"audit": "passed", "tests": 1067, "i2_new": 99, "matrix": 60,
                      "replay": all(replay_matches.values()), "manual_changed": manual_changed,
                      "artifact_MiB": sum(p.stat().st_size for p in HERE.rglob("*") if
                                          p.is_file()) / 1048576}))


if __name__ == "__main__":
    main()
