"""模块化手册迁移、装配、检查与按主题输出工具。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = ROOT / "docs" / "handbook" / "manifest.json"
GENERATED_BANNER = (
    "<!-- GENERATED FILE: 由 tools/handbook.py 从模块化手册装配；禁止手工编辑。 -->\n"
)
PLACEHOLDER = re.compile(r"⟦bal:([^⟧]+)⟧")
LOCAL_LINK = re.compile(
    r"(?P<prefix>!?\[[^\]]*\]\()(?P<target>[^)\s]+)(?P<suffix>\))"
)


class HandbookError(RuntimeError):
    """手册迁移或一致性检查失败。"""


def sha256_text(text: str) -> str:
    """计算 UTF-8 文本的 SHA-256。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_utf8(path: Path, text: str) -> None:
    """以 Python 3.8 兼容方式写入 UTF-8 无 BOM、LF 文本。"""
    with path.open("w", encoding="utf-8", newline="\n") as file:
        file.write(text)


def read_docx_text(path: Path) -> str:
    """从 DOCX 的 document.xml 按非空段落提取逻辑文本。"""
    with zipfile.ZipFile(str(path)) as archive:
        document_xml = archive.read("word/document.xml")

    root = ElementTree.fromstring(document_xml)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    lines: List[str] = []
    for paragraph in root.findall(".//w:p", namespace):
        texts = [node.text or "" for node in paragraph.findall(".//w:t", namespace)]
        line = "".join(texts)
        if line:
            lines.append(line)
    return "\n".join(lines)


def read_source_text(path: Path) -> str:
    """读取 DOCX 逻辑文本或 UTF-8 Markdown 迁移源。"""
    if path.suffix.lower() == ".docx":
        return read_docx_text(path)
    return path.read_text(encoding="utf-8").lstrip("\ufeff")


def load_manifest(path: Path) -> Dict[str, Any]:
    """加载并执行清单的基础结构校验。"""
    with path.open(encoding="utf-8") as file:
        manifest = json.load(file)
    modules = manifest.get("modules")
    if not isinstance(modules, list) or not modules:
        raise HandbookError("manifest.modules 必须是非空数组")
    return manifest


def module_path(manifest_path: Path, module: Dict[str, Any]) -> Path:
    """解析模块路径。"""
    return manifest_path.parent / str(module["path"])


def find_unique_line(lines: Sequence[str], marker: str) -> int:
    """查找唯一的整行迁移标记。"""
    matches = [index for index, line in enumerate(lines) if line == marker]
    if len(matches) != 1:
        raise HandbookError(
            "迁移标记必须恰好出现一次：{0!r}，实际 {1} 次".format(marker, len(matches))
        )
    return matches[0]


def source_slices(
    manifest: Dict[str, Any], source_text: str
) -> Dict[str, str]:
    """按清单标记把迁移源切成互不重叠的模块正文。"""
    lines = source_text.splitlines()
    modules = manifest["modules"]
    starts: List[int] = []
    for index, module in enumerate(modules):
        marker = module.get("source_start")
        if marker is None:
            if index != 0:
                raise HandbookError("只有第一个模块可以省略 source_start")
            starts.append(0)
        else:
            starts.append(find_unique_line(lines, str(marker)))

    if starts != sorted(starts) or len(starts) != len(set(starts)):
        raise HandbookError("模块 source_start 顺序错误或重复")

    bodies: Dict[str, str] = {}
    for index, module in enumerate(modules):
        start = starts[index]
        end = starts[index + 1] if index + 1 < len(starts) else len(lines)
        body = "\n".join(lines[start:end]) + "\n"
        bodies[str(module["doc_id"])] = body
    return bodies


def render_frontmatter(module: Dict[str, Any], body: str) -> str:
    """从清单生成确定性的模块元数据头。"""
    lines = [
        "---",
        "doc_id: {0}".format(module["doc_id"]),
        "status: {0}".format(module["status"]),
        "profile: {0}".format(module["profile"]),
        "canonical_for: {0}".format(
            json.dumps(module.get("canonical_for", []), ensure_ascii=False)
        ),
        "requires: {0}".format(json.dumps(module.get("requires", []), ensure_ascii=False)),
        "topics: {0}".format(json.dumps(module.get("topics", []), ensure_ascii=False)),
        "source_body_sha256: {0}".format(sha256_text(body)),
        "---",
        "",
    ]
    return "\n".join(lines)


def split_module_text(text: str) -> Tuple[str, str]:
    """把模块文件拆为 frontmatter 与正文。"""
    if not text.startswith("---\n"):
        raise HandbookError("模块缺少 frontmatter")
    boundary = text.find("\n---\n", 4)
    if boundary < 0:
        raise HandbookError("模块 frontmatter 未闭合")
    return text[: boundary + 5], text[boundary + 5 :]


def read_validated_module(path: Path, module: Dict[str, Any]) -> str:
    """读取模块并验证元数据与正文哈希。"""
    text = path.read_text(encoding="utf-8")
    _, body = split_module_text(text)
    expected = render_frontmatter(module, body) + body
    if text != expected:
        raise HandbookError("模块元数据或正文哈希不匹配：{0}".format(path))
    return body


def rebase_local_links(text: str, source_dir: Path, target_dir: Path) -> str:
    """把模块内相对链接重定位到合订文件所在目录。"""

    def replace(match: re.Match[str]) -> str:
        target = match.group("target")
        wrapped = target.startswith("<") and target.endswith(">")
        bare_target = target[1:-1] if wrapped else target
        if bare_target.startswith(("#", "/", "http://", "https://", "mailto:")):
            return match.group(0)
        path_text, separator, fragment = bare_target.partition("#")
        resolved = (source_dir / path_text).resolve()
        rebased = Path(os.path.relpath(str(resolved), str(target_dir))).as_posix()
        if separator:
            rebased = "{0}#{1}".format(rebased, fragment)
        if wrapped:
            rebased = "<{0}>".format(rebased)
        return "{0}{1}{2}".format(
            match.group("prefix"), rebased, match.group("suffix")
        )

    return LOCAL_LINK.sub(replace, text)


def validate_manifest(manifest: Dict[str, Any]) -> None:
    """校验 doc_id、路径、依赖与权威范围唯一性。"""
    modules = manifest["modules"]
    doc_ids = [str(module["doc_id"]) for module in modules]
    paths = [str(module["path"]) for module in modules]
    if len(doc_ids) != len(set(doc_ids)):
        raise HandbookError("manifest 存在重复 doc_id")
    if len(paths) != len(set(paths)):
        raise HandbookError("manifest 存在重复 path")

    known = set(doc_ids)
    for module in modules:
        unknown = set(module.get("requires", [])) - known
        if unknown:
            raise HandbookError(
                "{0} 引用了未知依赖：{1}".format(module["doc_id"], sorted(unknown))
            )

    authorities: Dict[Tuple[str, str], str] = {}
    for module in modules:
        profile = str(module["profile"])
        for topic in module.get("canonical_for", []):
            key = (profile, str(topic))
            if key in authorities:
                raise HandbookError(
                    "权威范围重复：{0} 同时属于 {1} 与 {2}".format(
                        key, authorities[key], module["doc_id"]
                    )
                )
            authorities[key] = str(module["doc_id"])


def migrate_batch(
    manifest_path: Path, manifest: Dict[str, Any], source: Path, batch: str
) -> None:
    """从 DOCX 或 Markdown 迁移源生成一个不超过五个文件的批次。"""
    validate_manifest(manifest)
    selected = [module for module in manifest["modules"] if module.get("batch") == batch]
    if not selected:
        raise HandbookError("未知或空批次：{0}".format(batch))
    if len(selected) > 5:
        raise HandbookError("批次 {0} 超过五个文件".format(batch))

    bodies = source_slices(manifest, read_source_text(source))
    written: List[Path] = []
    for module in selected:
        body = bodies[str(module["doc_id"])]
        target = module_path(manifest_path, module)
        content = render_frontmatter(module, body) + body
        if target.exists():
            if target.read_text(encoding="utf-8") != content:
                raise HandbookError("目标已存在且内容不同：{0}".format(target))
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        write_utf8(target, content)
        written.append(target)
    for path in written:
        print("  迁移 {0}".format(path.relative_to(ROOT)))
    print("  批次 {0}：{1} 个模块就绪".format(batch, len(selected)))


def refresh_module_metadata(manifest_path: Path, manifest: Dict[str, Any]) -> None:
    """在正文已人工编辑后刷新全部模块的确定性 frontmatter。"""
    validate_manifest(manifest)
    refreshed = 0
    for module in manifest["modules"]:
        path = module_path(manifest_path, module)
        text = path.read_text(encoding="utf-8")
        _, body = split_module_text(text)
        expected = render_frontmatter(module, body) + body
        if text == expected:
            continue
        write_utf8(path, expected)
        refreshed += 1
    print("  刷新模块元数据：{0} 个".format(refreshed))


def assemble(manifest_path: Path, manifest: Dict[str, Any]) -> str:
    """按清单顺序装配所有模块正文。"""
    validate_manifest(manifest)
    bodies: List[str] = []
    for module in manifest["modules"]:
        path = module_path(manifest_path, module)
        if not path.exists():
            raise HandbookError("模块不存在：{0}".format(path))
        body = read_validated_module(path, module)
        bodies.append(rebase_local_links(body, path.parent, manifest_path.parent))
    return "".join(bodies)


def lookup_balance(balance: Dict[str, Any], dotted: str) -> Any:
    """按点分路径读取 balance 值。"""
    node: Any = balance
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise HandbookError("balance 缺少键：{0}".format(dotted))
        node = node[part]
    return node


def format_balance(value: Any) -> str:
    """按旧渲染器规则格式化 balance 值。"""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return "{0:g}".format(value)
    if isinstance(value, list):
        return "/".join(format_balance(item) for item in value)
    return str(value)


def render_balance(text: str, balance_path: Path) -> str:
    """把装配文本中的 balance 占位渲染为玩家值。"""
    with balance_path.open(encoding="utf-8") as file:
        balance = json.load(file)

    def replace(match: re.Match[str]) -> str:
        return format_balance(lookup_balance(balance, match.group(1)))

    rendered = PLACEHOLDER.sub(replace, text)
    if "⟦bal:" in rendered:
        raise HandbookError("渲染后仍存在畸形 balance 占位")
    return rendered


def write_generated(path: Path, body: str) -> None:
    """写入带禁止编辑标记的生成文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    write_utf8(path, GENERATED_BANNER + body)


def build_outputs(
    manifest_path: Path,
    manifest: Dict[str, Any],
    source_output: Path,
    rendered_output: Path,
    balance_path: Path,
) -> None:
    """生成合订作者版和玩家渲染版。"""
    assembled = assemble(manifest_path, manifest)
    write_generated(source_output, assembled)
    write_generated(rendered_output, render_balance(assembled, balance_path))
    print("  生成 {0}".format(source_output.relative_to(ROOT)))
    print("  生成 {0}".format(rendered_output.relative_to(ROOT)))


def check_outputs(
    manifest_path: Path,
    manifest: Dict[str, Any],
    source: Optional[Path],
    source_output: Path,
    rendered_output: Path,
    balance_path: Path,
) -> None:
    """校验模块、可选迁移源和生成产物的一致性。"""
    assembled = assemble(manifest_path, manifest)
    if source is not None:
        expected_source = read_source_text(source)
        if not expected_source.endswith("\n"):
            expected_source += "\n"
        if assembled != expected_source:
            raise HandbookError("模块回组装结果与迁移基线不同")

    expected_generated = GENERATED_BANNER + assembled
    if source_output.exists() and source_output.read_text(encoding="utf-8") != expected_generated:
        raise HandbookError("合订作者版过期：{0}".format(source_output))

    expected_rendered = GENERATED_BANNER + render_balance(assembled, balance_path)
    if rendered_output.exists() and rendered_output.read_text(encoding="utf-8") != expected_rendered:
        raise HandbookError("玩家渲染版过期：{0}".format(rendered_output))

    print("  模块：{0} 个".format(len(manifest["modules"])))
    print("  回组装 SHA-256：{0}".format(sha256_text(assembled)))
    if source is not None:
        print("  ✅ 回组装逻辑文本与迁移基线一致")
    print("  ✅ manifest、元数据、依赖、权威范围与生成产物一致")


def select_context_modules(
    manifest: Dict[str, Any], topic: str
) -> List[Dict[str, Any]]:
    """选择命中主题的模块及其递归依赖。"""
    modules = manifest["modules"]
    by_id = {str(module["doc_id"]): module for module in modules}
    selected: Set[str] = set()

    for module in modules:
        topics = [str(item) for item in module.get("topics", [])]
        canonical = [str(item) for item in module.get("canonical_for", [])]
        if topic in topics or any(item == topic or item.startswith(topic + ".") for item in canonical):
            selected.add(str(module["doc_id"]))

    if not selected:
        raise HandbookError("没有模块匹配主题：{0}".format(topic))

    pending = list(selected)
    while pending:
        current = pending.pop()
        for dependency in by_id[current].get("requires", []):
            dependency = str(dependency)
            if dependency not in selected:
                selected.add(dependency)
                pending.append(dependency)
    return [module for module in modules if str(module["doc_id"]) in selected]


def print_context(
    manifest_path: Path,
    manifest: Dict[str, Any],
    topic: str,
    paths_only: bool,
) -> None:
    """向标准输出打印主题相关模块。"""
    selected = select_context_modules(manifest, topic)
    for module in selected:
        path = module_path(manifest_path, module)
        if paths_only:
            print(path.relative_to(ROOT))
            continue
        body = read_validated_module(path, module)
        print("<!-- module: {0} -->".format(path.relative_to(ROOT)))
        print(body, end="")


def available_batches(manifest: Dict[str, Any]) -> List[str]:
    """返回迁移批次，保持首次出现顺序。"""
    result: List[str] = []
    for module in manifest["modules"]:
        batch = str(module["batch"])
        if batch not in result:
            result.append(batch)
    return result


def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    subparsers = parser.add_subparsers(dest="command", required=True)

    migrate = subparsers.add_parser("migrate", help="从 DOCX/Markdown 迁移最多五文件的批次")
    migrate_source = migrate.add_mutually_exclusive_group(required=True)
    migrate_source.add_argument("--source", type=Path)
    migrate_source.add_argument("--source-docx", type=Path, help="兼容旧调用")
    migrate.add_argument("--batch", required=True)

    subparsers.add_parser("batches", help="列出迁移批次")
    subparsers.add_parser("refresh", help="按模块正文刷新 frontmatter 哈希")

    assemble_output = subparsers.add_parser("assemble", help="生成一份只读合订稿")
    assemble_output.add_argument("--output", type=Path, required=True)

    build = subparsers.add_parser("build", help="装配合订作者版和玩家版")
    build.add_argument(
        "--source-output",
        type=Path,
        default=ROOT / "docs" / "handbook" / "complete.generated.src.md",
    )
    build.add_argument(
        "--rendered-output",
        type=Path,
        default=ROOT / "docs" / "handbook" / "complete.generated.md",
    )
    build.add_argument("--balance", type=Path, default=ROOT / "data" / "balance.json")

    check = subparsers.add_parser("check", help="检查迁移和生成一致性")
    check_source = check.add_mutually_exclusive_group()
    check_source.add_argument("--source", type=Path)
    check_source.add_argument("--source-docx", type=Path, help="兼容旧调用")
    check.add_argument(
        "--source-output",
        type=Path,
        default=ROOT / "docs" / "handbook" / "complete.generated.src.md",
    )
    check.add_argument(
        "--rendered-output",
        type=Path,
        default=ROOT / "docs" / "handbook" / "complete.generated.md",
    )
    check.add_argument("--balance", type=Path, default=ROOT / "data" / "balance.json")

    context = subparsers.add_parser("context", help="输出主题相关模块和依赖")
    context.add_argument("topic")
    context.add_argument("--paths-only", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """命令行入口。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    manifest_path = args.manifest.resolve()
    manifest = load_manifest(manifest_path)
    try:
        if args.command == "migrate":
            source = args.source or args.source_docx
            migrate_batch(manifest_path, manifest, source.resolve(), args.batch)
        elif args.command == "batches":
            for batch in available_batches(manifest):
                print(batch)
        elif args.command == "refresh":
            refresh_module_metadata(manifest_path, manifest)
        elif args.command == "assemble":
            output = args.output.resolve()
            write_generated(output, assemble(manifest_path, manifest))
            print("  生成 {0}".format(output.relative_to(ROOT)))
        elif args.command == "build":
            build_outputs(
                manifest_path,
                manifest,
                args.source_output.resolve(),
                args.rendered_output.resolve(),
                args.balance.resolve(),
            )
        elif args.command == "check":
            source = args.source or args.source_docx
            source_path = source.resolve() if source else None
            check_outputs(
                manifest_path,
                manifest,
                source_path,
                args.source_output.resolve(),
                args.rendered_output.resolve(),
                args.balance.resolve(),
            )
        elif args.command == "context":
            print_context(manifest_path, manifest, args.topic, args.paths_only)
        else:
            parser.error("未知命令")
    except (HandbookError, OSError, ValueError, zipfile.BadZipFile) as error:
        print("❌ {0}".format(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
