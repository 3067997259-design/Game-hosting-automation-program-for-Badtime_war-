"""M9 玩家手册装配器（profile: m9-rfc）。

与 v2exp 的 `tools/handbook.py` 同机制、不同 manifest 与输出目录：
- 作者源：`docs/m9/manual/manifest.json` 登记的模块化 Markdown
  （`docs/m9/manual/core/...`，可继续分主题扩充）；
- 装配：按 manifest 顺序拼接模块 → `complete.generated.src.md`；
- 渲染：`⟦bal:...⟧` 占位从 `data/balance.json` 取值 →
  `complete.generated.md`（玩家版）；
- 校验：模块 frontmatter 哈希、依赖、权威范围唯一性与生成产物一致性。

用法：
    python tools/m9_handbook.py build       # 装配合订作者版 + 玩家版
    python tools/m9_handbook.py check       # 只校验，不写文件
    python tools/m9_handbook.py context 行动 --paths-only
    python tools/m9_handbook.py batches
    python tools/m9_handbook.py refresh     # 人工改模块正文后刷新 frontmatter
    python tools/m9_handbook.py assemble --output <path>

模块是「玩家手册候选」的作者源；规则语义冲突时以 `docs/m9/current/` RFC 为准。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from handbook import (  # noqa: E402
    HandbookError,
    assemble,
    available_batches,
    build_outputs,
    check_outputs,
    load_manifest,
    print_context,
    refresh_module_metadata,
    write_generated,
)

DEFAULT_MANIFEST = ROOT / "docs" / "m9" / "manual" / "manifest.json"
DEFAULT_SOURCE_OUTPUT = ROOT / "docs" / "m9" / "manual" / "complete.generated.src.md"
DEFAULT_RENDERED_OUTPUT = ROOT / "docs" / "m9" / "manual" / "complete.generated.md"
DEFAULT_BALANCE = ROOT / "data" / "balance.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    subparsers = parser.add_subparsers(dest="command", required=True)

    assemble_parser = subparsers.add_parser("assemble", help="生成一份只读合订稿")
    assemble_parser.add_argument("--output", type=Path, required=True)

    build = subparsers.add_parser("build", help="装配合订作者版和玩家版")
    build.add_argument("--source-output", type=Path, default=DEFAULT_SOURCE_OUTPUT)
    build.add_argument("--rendered-output", type=Path, default=DEFAULT_RENDERED_OUTPUT)
    build.add_argument("--balance", type=Path, default=DEFAULT_BALANCE)

    check = subparsers.add_parser("check", help="检查模块与生成产物一致性")
    check.add_argument("--source-output", type=Path, default=DEFAULT_SOURCE_OUTPUT)
    check.add_argument("--rendered-output", type=Path, default=DEFAULT_RENDERED_OUTPUT)
    check.add_argument("--balance", type=Path, default=DEFAULT_BALANCE)

    subparsers.add_parser("refresh", help="按模块正文刷新 frontmatter 哈希")
    subparsers.add_parser("batches", help="列出迁移批次")

    context = subparsers.add_parser("context", help="输出主题相关模块和依赖")
    context.add_argument("topic")
    context.add_argument("--paths-only", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    manifest_path = args.manifest.resolve()
    manifest = load_manifest(manifest_path)
    try:
        if args.command == "build":
            build_outputs(
                manifest_path,
                manifest,
                args.source_output.resolve(),
                args.rendered_output.resolve(),
                args.balance.resolve(),
            )
        elif args.command == "check":
            check_outputs(
                manifest_path,
                manifest,
                None,
                args.source_output.resolve(),
                args.rendered_output.resolve(),
                args.balance.resolve(),
            )
        elif args.command == "refresh":
            refresh_module_metadata(manifest_path, manifest)
        elif args.command == "batches":
            for batch in available_batches(manifest):
                print(batch)
        elif args.command == "assemble":
            output = args.output.resolve()
            write_generated(output, assemble(manifest_path, manifest))
            print(f"  生成 {output.relative_to(ROOT)}")
        elif args.command == "context":
            print_context(manifest_path, manifest, args.topic, args.paths_only)
        else:
            parser.error("未知命令")
    except (HandbookError, OSError, ValueError) as error:
        print(f"❌ {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
