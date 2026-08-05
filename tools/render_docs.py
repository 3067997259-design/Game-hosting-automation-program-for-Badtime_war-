"""文档数值渲染器 —— 模块化手册 + data/balance.json → 玩家合订版。

约定：
  当前作者源为 `docs/handbook/manifest.json` 登记的模块化 Markdown；
  本脚本委托 `tools/handbook.py` 装配作者版和玩家版。旧 `docs/*.src.md` 链只保留历史文件，
  不再作为默认输入。

  示例：作者版写「监控期 ⟦bal:anchor.window⟧ 轮」→ 渲染版「监控期 8 轮」。

用法：
  python tools/render_docs.py          # 装配模块并渲染 balance 数值
  python tools/render_docs.py --check  # 只校验模块、manifest 与生成产物（不写文件）

纪律：文档里所有机制数值都走 ⟦bal:...⟧，不留裸数字；占位指向的键必须在 balance 中存在
（缺键直接报错），保证"唯一数字源"。
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BALANCE_PATH = os.path.join(ROOT, "data", "balance.json")
DOCS_DIR = os.path.join(ROOT, "docs")
SRC_SUFFIX = ".src.md"
HANDBOOK_MANIFEST = os.path.join(DOCS_DIR, "handbook", "manifest.json")

# 占位：⟦bal:dotted.key⟧（白方括号 U+27E6/27E7，中文/markdown 内不撞）
# key 允许中文（balance 有中文键，如 bow_modules.穿甲）；取到闭括号前的任意非闭括号字符。
PLACEHOLDER = re.compile(r"⟦bal:([^⟧]+)⟧")
_LEFTOVER = re.compile(r"⟦bal:")


def load_balance() -> dict:
    with open(BALANCE_PATH, encoding="utf-8") as f:
        return json.load(f)


def lookup(balance: dict, dotted: str):
    """按点路径取值；缺键抛 KeyError(dotted)。"""
    node = balance
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(dotted)
        node = node[part]
    return node


def fmt_value(v) -> str:
    """渲染值：int 原样、float 去尾零（8.0→8, 0.5→0.5）、list 用「/」连接。"""
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, float):
        return f"{v:g}"
    if isinstance(v, list):
        return "/".join(fmt_value(x) for x in v)
    return str(v)


def render_text(text: str, balance: dict, src_name: str) -> str:
    """替换文本中的所有 ⟦bal:key⟧。缺键收集后一次性报错（不静默）。"""
    missing: list[str] = []

    def _sub(m: re.Match) -> str:
        key = m.group(1)
        try:
            return fmt_value(lookup(balance, key))
        except KeyError:
            missing.append(key)
            return m.group(0)

    out = PLACEHOLDER.sub(_sub, text)
    if missing:
        raise KeyError(f"{src_name}: balance 缺以下键: {sorted(set(missing))}")
    # 残留 ⟦bal: = 畸形占位（未闭合等），不许静默放过
    if _LEFTOVER.search(out):
        raise KeyError(f"{src_name}: 存在畸形 ⟦bal:...⟧ 占位（未闭合？）")
    return out


def src_to_out(src_path: str) -> str:
    """foo.src.md → foo.md。"""
    return src_path[: -len(SRC_SUFFIX)] + ".md"


def iter_sources() -> list[str]:
    return sorted(glob.glob(os.path.join(DOCS_DIR, "*" + SRC_SUFFIX)))


def main(check: bool = False) -> int:
    if os.path.exists(HANDBOOK_MANIFEST):
        from handbook import (
            HandbookError,
            build_outputs,
            check_outputs,
            load_manifest,
        )

        manifest_path = Path(HANDBOOK_MANIFEST)
        source_output = manifest_path.parent / "complete.generated.src.md"
        rendered_output = manifest_path.parent / "complete.generated.md"
        try:
            manifest = load_manifest(manifest_path)
            if check:
                check_outputs(
                    manifest_path,
                    manifest,
                    None,
                    source_output,
                    rendered_output,
                    Path(BALANCE_PATH),
                )
            else:
                build_outputs(
                    manifest_path,
                    manifest,
                    source_output,
                    rendered_output,
                    Path(BALANCE_PATH),
                )
        except (HandbookError, OSError, ValueError) as error:
            print(f"❌ {error}")
            return 2
        return 0

    balance = load_balance()
    sources = iter_sources()
    if not sources:
        print("  （无 docs/*.src.md，无需渲染）")
        return 0

    stale: list[str] = []
    for src in sources:
        with open(src, encoding="utf-8") as f:
            text = f.read()
        try:
            rendered = render_text(text, balance, os.path.basename(src))
        except KeyError as e:
            print(f"❌ {e}")
            return 2
        out = src_to_out(src)
        if check:
            existing = ""
            if os.path.exists(out):
                with open(out, encoding="utf-8") as f:
                    existing = f.read()
            if existing != rendered:
                stale.append(os.path.basename(out))
        else:
            with open(out, "w", encoding="utf-8", newline="\n") as f:
                f.write(rendered)
            print(f"  渲染 {os.path.basename(src)} → {os.path.basename(out)}")

    if check:
        if stale:
            print(f"❌ 渲染版过期（balance 改了没重渲）: {stale}\n"
                  f"   跑：python tools/render_docs.py")
            return 1
        print("  ✅ 文档渲染版与 balance 一致")
    return 0


if __name__ == "__main__":
    sys.exit(main(check="--check" in sys.argv))
