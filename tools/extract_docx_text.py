"""将 DOCX 的逻辑段落提取为可搜索的 UTF-8 Markdown 历史阅读稿。"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional, Sequence

from handbook import read_docx_text, write_utf8


def build_parser() -> argparse.ArgumentParser:
    """构造命令行参数。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="输入 DOCX")
    parser.add_argument("output", type=Path, help="输出 Markdown")
    parser.add_argument("--title", required=True, help="提取稿标题")
    parser.add_argument(
        "--note",
        default="机器提取的历史阅读稿，不定义现行规则。",
        help="标题下方的身份说明",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """提取逻辑文本并写入无 BOM、LF Markdown。"""
    args = build_parser().parse_args(argv)
    source = args.source.resolve()
    output = args.output.resolve()
    body = read_docx_text(source).rstrip("\n") + "\n"
    header = (
        "# {0}\n\n"
        "> {1}  \n"
        "> 来源：`{2}`。本文件不保留 Word 排版。\n\n"
        "---\n\n"
    ).format(args.title, args.note, source.name)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_utf8(output, header + body)
    print("提取完成：{0}".format(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
