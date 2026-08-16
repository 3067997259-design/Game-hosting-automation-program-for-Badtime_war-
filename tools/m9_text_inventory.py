"""M9 硬编码中文盘点工具（text migration inventory）。

扫描 M9 相关代码中的非 docstring 中文字符串字面量，输出结构化盘点：
- `zone: strict`：M9 专有文件（engine/m9/**、cli/m9_ui.py、AI M9 适配器），
  目标为全部迁入 prompts.json 的 `m9` 命名空间，最终只留白名单身份键；
- `zone: mixed`：与 legacy/v2exp 共享的文件（引擎/actions/cli/main/AI 决策），
  只迁 M9 分支上的字面量，需人工按 function/class 上下文复核。

用法：
    python tools/m9_text_inventory.py                  # 只打印汇总
    python tools/m9_text_inventory.py --out docs/m9/text_inventory.json
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import pathlib
import re
import sys
from typing import Any, Dict, List, Optional

ROOT = pathlib.Path(__file__).resolve().parent.parent
CJK = re.compile(r"[\u4e00-\u9fff]")

STRICT_DIRS = ["engine/m9"]
STRICT_FILES = [
    "cli/m9_ui.py",
    "controllers/ai/m9_adapters.py",
]
MIXED_FILES = [
    "engine/action_turn.py",
    "engine/round_manager.py",
    "engine/action_enumerator.py",
    "engine/game_setup.py",
    "actions/special_op.py",
    "cli/display.py",
    "cli/parser.py",
    "cli/validator.py",
    "controllers/human.py",
    "main.py",
    "controllers/ai/orchestrator.py",
    "controllers/ai/controller.py",
    "controllers/ai/game_query.py",
    "controllers/ai/constants.py",
    "controllers/ai/counter.py",
    "controllers/ai/decision/t0_policy.py",
    "controllers/ai/decision/c_policy.py",
    "controllers/ai/decision/value.py",
    "controllers/ai/decision/snapshot.py",
    "controllers/ai/minds/police_mind.py",
    "controllers/ai/minds/threat_mind.py",
    "controllers/ai/minds/develop_mind.py",
    "controllers/ai/minds/combat_mind.py",
    "controllers/ai/talents/t0_policy.py",
    "controllers/ai/talents/g1_g2_g4_hooks.py",
    "controllers/ai/talents/g3_mythland_hook.py",
    "controllers/ai/talents/g5_ripple_hook.py",
    "controllers/ai/talents/hoshino_impl.py",
    "controllers/ai/talents/hoshino_hook.py",
    "controllers/ai/talents/t1_oneslash_hook.py",
    "controllers/ai/talents/t3_star_hook.py",
    "controllers/ai/talents/t4_hexagram_hook.py",
]
EXCLUDE_PARTS = ("__pycache__", "\\tests\\", "\\network\\", "\\rl\\", "\\ai_chat\\")


def literal_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def collect_literals(path: pathlib.Path, zone: str) -> List[Dict[str, Any]]:
    """收集一个文件中的 CJK 字面量（跳过 docstring）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    doc_ids = set()

    def mark_doc(body: list) -> None:
        if body and isinstance(body[0], ast.Expr) and isinstance(
                getattr(body[0], "value", None), ast.Constant) and isinstance(
                    body[0].value.value, str):
            doc_ids.add(id(body[0].value))

    if isinstance(tree, ast.Module) and tree.body:
        mark_doc(tree.body)

    parents: Dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent

    def context_of(node: ast.AST) -> str:
        cur: Optional[ast.AST] = parents.get(id(node))
        while cur is not None and not isinstance(
                cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            cur = parents.get(id(cur))
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur.name
        if isinstance(cur, ast.ClassDef):
            return cur.name
        return "<module>"

    entries: List[Dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            mark_doc(node.body)
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in doc_ids or not CJK.search(node.value):
            continue
        entries.append({
            "file": str(path.relative_to(ROOT)).replace("\\", "/"),
            "line": node.lineno,
            "hash": literal_hash(node.value),
            "text": node.value.replace("\n", "\\n"),
            "context": context_of(node),
            "zone": zone,
        })
    return entries


def scope_files() -> List[tuple]:
    files: List[tuple] = []
    for rel in STRICT_DIRS:
        for path in sorted((ROOT / rel).rglob("*.py")):
            if any(part in str(path) for part in EXCLUDE_PARTS):
                continue
            files.append((path, "strict"))
    for rel in STRICT_FILES:
        path = ROOT / rel
        if path.exists():
            files.append((path, "strict"))
    for rel in MIXED_FILES:
        path = ROOT / rel
        if path.exists():
            files.append((path, "mixed"))
    return files


def build_inventory() -> Dict[str, Any]:
    files = scope_files()
    entries: List[Dict[str, Any]] = []
    for path, zone in files:
        try:
            entries.extend(collect_literals(path, zone))
        except SyntaxError:
            print(f"  skip (syntax): {path.relative_to(ROOT)}", file=sys.stderr)
    by_file: Dict[str, List[Dict[str, Any]]] = {}
    for entry in entries:
        by_file.setdefault(entry["file"], []).append(entry)
    return {
        "scope": {
            "strict_dirs": STRICT_DIRS,
            "strict_files": STRICT_FILES,
            "mixed_files": MIXED_FILES,
        },
        "counts": {
            "total": len(entries),
            "strict": sum(1 for e in entries if e["zone"] == "strict"),
            "mixed": sum(1 for e in entries if e["zone"] == "mixed"),
            "files": len(by_file),
        },
        "files": {
            name: {"zone": value[0]["zone"], "count": len(value), "entries": value}
            for name, value in sorted(by_file.items())
        },
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=pathlib.Path, default=None)
    args = parser.parse_args(argv)
    inventory = build_inventory()
    counts = inventory["counts"]
    print(f"M9 文本盘点：{counts['files']} 个文件，"
          f"{counts['total']} 个中文字面量"
          f"（strict {counts['strict']} / mixed {counts['mixed']}）")
    for name, info in inventory["files"].items():
        print(f"  [{info['zone']:6s}] {name}: {info['count']}")
    if args.out is not None:
        target = args.out.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(inventory, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8", newline="\n")
        print(f"  已写入 {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
