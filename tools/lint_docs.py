"""文档一致性静态检查器。

用途：
  检查手册源文件与 `data/balance.json` 是否一致，只报告、不修改。

用法：
  python tools/lint_docs.py
  python tools/lint_docs.py --json
  python tools/lint_docs.py --manual docs/handbook/complete.generated.src.md
  python tools/lint_docs.py --balance data/balance.json
  python tools/lint_docs.py --selftest
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from xml.etree import ElementTree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_MANUAL_PATH = os.path.join(
    ROOT, "docs", "handbook", "complete.generated.src.md"
)
DEFAULT_BALANCE_PATH = os.path.join(ROOT, "data", "balance.json")
WHITELIST_PATH = os.path.join(ROOT, "tools", "lint_whitelist.json")
FLAG_SOURCE_DIRS: Sequence[str] = (
    "engine",
    "talents",
    "actions",
    "combat",
    "controllers",
    "rl",
    "cli",
    "network",
    "tui",
    "utils",
)
FLAG_SCAN_PATHS: Sequence[str] = (
    os.path.join(ROOT, "main.py"),
    os.path.join(ROOT, "stats_runner.py"),
)

PLACEHOLDER = re.compile(r"⟦bal:([^⟧]+)⟧")
TOP_LEVEL_CHAPTER = re.compile(
    r"^##\s+(?:(?:第(?P<chapter_a>[零一二三四五六七八九十]+)章)|"
    r"(?P<chapter_b>[零一二三四五六七八九十]+)、)"
)
TABLE_SEPARATOR = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$")
VERSION_PATTERN = re.compile(r"(?:[Vv]\s*)?\d+\.\d+")
PLACEHOLDER_STRIP = re.compile(r"⟦bal:[^⟧]+⟧")
INLINE_CODE = re.compile(r"`[^`]*`")
FLAG_TOKEN_PATTERN = re.compile(r"\b(?:m\d+_\w+|k_\w+|hp20)\b")
FLAG_LITERAL_PATTERN = re.compile(r'is_enabled\("([A-Za-z0-9_]+)"\)')

HARDCODED_PATTERNS: Sequence[Tuple[str, re.Pattern[str]]] = (
    (
        "prefix",
        re.compile(
            r"(上限|下限|消耗|恢复|扣除|阈值(?:为)?|回满至|回满到|回满|复活心血|"
            r"心血设|持续|潜伏|冷却|最多储)\s*[+\-]?\d+(?:\.\d+)?"
        ),
    ),
    ("operator", re.compile(r"[+\-×≤≥]\s*\d+(?:\.\d+)?")),
    ("unit", re.compile(r"\d+(?:\.\d+)?\s*(?:点|层|轮|次|发|张|票|倍)")),
)

RETIRED_TERMS: Sequence[str] = (
    "购物凭证",
    "购买凭证",
    "行动权争夺",
    "你给路打油",
    "不良少年",
    "导弹",
    "D4争夺",
    "凭证",
)
RETIRED_INFO_HINTS = ("已退役", "退役", "废除", "取代")

CHECK_ORDER: Sequence[str] = (
    "BROKEN_REF",
    "UNREFERENCED_KEY",
    "HARDCODED_NUMBER",
    "RETIRED_CONTENT",
    "FLAG_NAME_MISMATCH",
)
CHECK_TITLES: Dict[str, str] = {
    "BROKEN_REF": "CHECK 1: 悬空占位符",
    "UNREFERENCED_KEY": "CHECK 2: 幽灵键",
    "HARDCODED_NUMBER": "CHECK 3: 疑似未迁移的硬编码数值",
    "RETIRED_CONTENT": "CHECK 4: 退役内容残留",
    "FLAG_NAME_MISMATCH": "CHECK 5: flag 名一致性检查",
}
CHECK_CODES: Dict[str, str] = {
    "BROKEN_REF": "CHECK1",
    "UNREFERENCED_KEY": "CHECK2",
    "HARDCODED_NUMBER": "CHECK3",
    "RETIRED_CONTENT": "CHECK4",
    "FLAG_NAME_MISMATCH": "CHECK5",
}
SEVERITY_ORDER = {"ERROR": 0, "WARN": 1, "INFO": 2}
CHINESE_CHAPTERS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
    "十三": 13,
}


@dataclass
class Finding:
    """单条检查结果。"""

    check: str
    severity: str
    line: int
    snippet: str
    detail: str
    file: str = ""
    fingerprint_source: str = ""

    def to_json_dict(self) -> Dict[str, Any]:
        """导出机器可读结构。"""
        return {
            "check": self.check,
            "severity": self.severity,
            "line": self.line,
            "snippet": self.snippet,
            "detail": self.detail,
        }


@dataclass
class LookupFailure:
    """点分路径解析失败信息。"""

    failed_path: str
    candidates: List[str]
    reason: str


@dataclass
class WhitelistEntry:
    """lint 白名单条目。"""

    file: str
    fingerprint: str
    check: str
    reason: str
    render: str


def read_utf8_text(path: str) -> str:
    """读取 UTF-8 文本。"""
    with open(path, encoding="utf-8") as file:
        return file.read()


def read_docx_text(path: str) -> str:
    """读取 `.docx` 内的 Word 文本，并按段落转成逐行文本。"""
    with zipfile.ZipFile(path) as archive:
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


def read_manual_text(path: str) -> str:
    """读取手册源文件，兼容 `.docx` 与纯文本源文件。"""
    extension = os.path.splitext(path)[1].lower()
    if extension == ".docx":
        return read_docx_text(path)
    return read_utf8_text(path)


def load_balance(path: str) -> Dict[str, Any]:
    """读取并解析 balance.json。"""
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def load_whitelist(path: str) -> List[WhitelistEntry]:
    """读取 lint 白名单。"""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as file:
        raw = json.load(file)

    entries: List[WhitelistEntry] = []
    if not isinstance(raw, list):
        return entries
    for item in raw:
        if not isinstance(item, dict):
            continue
        entries.append(
            WhitelistEntry(
                file=str(item.get("file", "")),
                fingerprint=str(item.get("fingerprint", "")),
                check=str(item.get("check", "")),
                reason=str(item.get("reason", "")),
                render=str(item.get("render", "")),
            )
        )
    return entries


def format_rel_path(path: str) -> str:
    """将仓库内路径转成相对路径展示。"""
    try:
        return os.path.relpath(path, ROOT)
    except ValueError:
        return path


def normalize_path_for_match(path: str) -> str:
    """规范化路径分隔符，便于白名单比对。"""
    return path.replace("/", "\\")


def collect_flag_names() -> Set[str]:
    """从代码里的 is_enabled("...") 字面量收集 flag 真名。"""
    names: Set[str] = set()
    for root_dir in FLAG_SOURCE_DIRS:
        base = Path(ROOT, root_dir)
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for match in FLAG_LITERAL_PATTERN.finditer(text):
                names.add(match.group(1))
    return names


def chinese_to_int(text: str) -> Optional[int]:
    """将有限范围的中文章号转成整数。"""
    return CHINESE_CHAPTERS.get(text)


def parse_top_level_chapter(line: str) -> Optional[int]:
    """解析 `##` 一级章节号。"""
    match = TOP_LEVEL_CHAPTER.match(line.strip())
    if not match:
        return None
    raw = match.group("chapter_a") or match.group("chapter_b")
    if raw is None:
        return None
    return chinese_to_int(raw)


def lookup_path(balance: Dict[str, Any], dotted: str) -> Tuple[bool, Any]:
    """按点分路径查值；失败时返回 LookupFailure。"""
    node: Any = balance
    traversed: List[str] = []

    for part in dotted.split("."):
        traversed.append(part)
        if not isinstance(node, dict):
            return False, LookupFailure(
                failed_path=".".join(traversed),
                candidates=[],
                reason="上一级不是对象，无法继续向下解析",
            )
        if part not in node:
            sibling_keys = [str(key) for key in node.keys()]
            candidates = [
                ".".join(traversed[:-1] + [candidate])
                for candidate in difflib.get_close_matches(part, sibling_keys, n=3, cutoff=0.5)
            ]
            return False, LookupFailure(
                failed_path=".".join(traversed),
                candidates=candidates,
                reason="该级键不存在",
            )
        node = node[part]
    return True, node


def iter_leaf_paths(node: Any, prefix: Sequence[str] = ()) -> Iterable[str]:
    """遍历 balance 中所有叶子路径，忽略 `_` 开头的键。"""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and key.startswith("_"):
                continue
            next_prefix = tuple(prefix) + (str(key),)
            yield from iter_leaf_paths(value, next_prefix)
        return
    if prefix:
        yield ".".join(prefix)


def make_placeholder_mask(line: str) -> str:
    """移除占位与行内代码，同时保留字符串宽度便于切片。"""
    masked = PLACEHOLDER_STRIP.sub(lambda match: " " * len(match.group(0)), line)
    return INLINE_CODE.sub(lambda match: " " * len(match.group(0)), masked)


def should_skip_hardcoded_line(line: str) -> bool:
    """判断该行是否不参与硬编码数字扫描。"""
    stripped = line.strip()
    if not stripped:
        return True
    if stripped.startswith("##"):
        return True
    if stripped.startswith("```"):
        return True
    if TABLE_SEPARATOR.match(stripped):
        return True
    if "例：" in line or "→" in line:
        return True
    if "6 人局" in line or "示例" in line:
        return True
    return False


def is_range_like(line: str, start: int, end: int) -> bool:
    """排除 `1-3` / `25–36` 一类范围写法。"""
    left = line[start - 1] if start > 0 else ""
    right = line[end] if end < len(line) else ""
    return (bool(left) and left in "-~～–—") or (bool(right) and right in "-~～–—")


def overlaps_version(line: str, start: int, end: int) -> bool:
    """排除版本号命中。"""
    for match in VERSION_PATTERN.finditer(line):
        if start < match.end() and end > match.start():
            return True
    return False


def snippet_around(text: str, start: int, end: int, pad: int = 15) -> str:
    """截取命中上下文。"""
    left = max(0, start - pad)
    right = min(len(text), end + pad)
    return text[left:right].strip()


def normalize_finding_order(findings: List[Finding]) -> List[Finding]:
    """统一结果排序。"""
    return sorted(
        findings,
        key=lambda item: (
            CHECK_ORDER.index(item.check),
            SEVERITY_ORDER[item.severity],
            item.line,
            item.snippet,
        ),
    )


def find_broken_refs(
    manual_text: str,
    balance: Dict[str, Any],
    manual_path: str,
) -> Tuple[List[Finding], Set[str]]:
    """CHECK 1：报告所有无法解析的占位符。"""
    findings: List[Finding] = []
    valid_refs: Set[str] = set()
    manual_label = format_rel_path(manual_path)

    for line_no, line in enumerate(manual_text.splitlines(), start=1):
        for match in PLACEHOLDER.finditer(line):
            dotted = match.group(1)
            placeholder = match.group(0)
            ok, value = lookup_path(balance, dotted)
            if ok:
                valid_refs.add(dotted)
                _ = value
                continue

            failure = value
            detail_lines = [
                "来源: {0}".format(manual_label),
                "路径失败于: {0}".format(failure.failed_path),
                "失败原因: {0}".format(failure.reason),
            ]
            if failure.candidates:
                detail_lines.append("相近候选: {0}".format(", ".join(failure.candidates)))
            findings.append(
                Finding(
                    check="BROKEN_REF",
                    severity="ERROR",
                    line=line_no,
                    snippet=placeholder,
                    detail="\n".join(detail_lines),
                    file=manual_path,
                    fingerprint_source=line,
                )
            )
    return findings, valid_refs


def find_unreferenced_keys(balance: Dict[str, Any], valid_refs: Set[str]) -> List[Finding]:
    """CHECK 2：报告未被手册引用的 balance 叶子键。"""
    findings: List[Finding] = []

    for path in sorted(iter_leaf_paths(balance)):
        if path in valid_refs:
            continue
        parent = path.rsplit(".", 1)[0] if "." in path else path
        detail_lines = [
            "分组: {0}".format(parent),
            "未被手册引用的叶子键: {0}".format(path),
            "severity: INFO（可能仅供代码使用）",
        ]
        findings.append(
            Finding(
                check="UNREFERENCED_KEY",
                severity="INFO",
                line=0,
                snippet=path,
                detail="\n".join(detail_lines),
                file=DEFAULT_BALANCE_PATH,
                fingerprint_source=path,
            )
        )
    return findings


def find_hardcoded_numbers(manual_text: str, manual_path: str) -> Tuple[List[Finding], Dict[str, int]]:
    """CHECK 3：扫描第五至十三章正文中的疑似硬编码数字。"""
    findings: List[Finding] = []
    chapter_counts: Dict[str, int] = {}
    current_chapter: Optional[int] = None
    current_label = ""
    in_fence = False
    skip_example_table = False
    manual_label = format_rel_path(manual_path)

    for line_no, raw_line in enumerate(manual_text.splitlines(), start=1):
        stripped = raw_line.strip()

        if stripped.startswith("```"):
            in_fence = not in_fence
            continue

        chapter_number = parse_top_level_chapter(raw_line)
        if chapter_number is not None:
            current_chapter = chapter_number
            current_label = stripped.lstrip("#").strip()
            skip_example_table = False
            continue

        if current_chapter is None or not 5 <= current_chapter <= 13:
            continue
        if in_fence:
            continue

        if skip_example_table:
            if stripped.startswith("|") or TABLE_SEPARATOR.match(stripped):
                continue
            skip_example_table = False

        if "6 人局" in raw_line or "示例" in raw_line:
            skip_example_table = True

        if should_skip_hardcoded_line(raw_line):
            continue

        masked_line = make_placeholder_mask(raw_line)
        for _, pattern in HARDCODED_PATTERNS:
            for match in pattern.finditer(masked_line):
                start, end = match.span()
                if is_range_like(masked_line, start, end):
                    continue
                if overlaps_version(masked_line, start, end):
                    continue
                snippet = snippet_around(raw_line, start, end)
                detail_lines = [
                    "来源: {0}".format(manual_label),
                    "章节: {0}".format(current_label),
                    "疑似未迁移的数值片段: {0}".format(raw_line[start:end].strip()),
                ]
                findings.append(
                    Finding(
                        check="HARDCODED_NUMBER",
                        severity="WARN",
                        line=line_no,
                        snippet=snippet,
                        detail="\n".join(detail_lines),
                        file=manual_path,
                        fingerprint_source=raw_line,
                    )
                )
                chapter_counts[current_label] = chapter_counts.get(current_label, 0) + 1
    return findings, chapter_counts


def find_non_overlapping_terms(line: str) -> List[Tuple[int, int, str]]:
    """按最长词优先，找出一行内不重叠的退役词命中。"""
    hits: List[Tuple[int, int, str]] = []
    occupied: List[Tuple[int, int]] = []

    for term in RETIRED_TERMS:
        for match in re.finditer(re.escape(term), line):
            span = match.span()
            if any(not (span[1] <= start or span[0] >= end) for start, end in occupied):
                continue
            occupied.append(span)
            hits.append((span[0], span[1], term))

    return sorted(hits, key=lambda item: item[0])


def retired_severity(line: str) -> str:
    """含退役说明的行降级为 INFO，否则为 WARN。"""
    if any(hint in line for hint in RETIRED_INFO_HINTS):
        return "INFO"
    return "WARN"


def find_retired_content(
    manual_text: str,
    balance_text: str,
    manual_path: str,
    balance_path: str,
) -> List[Finding]:
    """CHECK 4：扫描全文中的退役词残留。"""
    findings: List[Finding] = []
    manual_label = format_rel_path(manual_path)
    balance_label = format_rel_path(balance_path)

    for source_label, text in ((manual_label, manual_text), (balance_label, balance_text)):
        for line_no, line in enumerate(text.splitlines(), start=1):
            for start, end, term in find_non_overlapping_terms(line):
                severity = retired_severity(line)
                detail_lines = [
                    "来源: {0}".format(source_label),
                    "命中退役词: {0}".format(term),
                ]
                if severity == "INFO":
                    detail_lines.append("同行包含退役说明词，按 INFO 处理")
                findings.append(
                    Finding(
                        check="RETIRED_CONTENT",
                        severity=severity,
                        line=line_no,
                        snippet=snippet_around(line, start, end),
                        detail="\n".join(detail_lines),
                        file=os.path.join(ROOT, source_label),
                        fingerprint_source=line,
                    )
                )
    return findings


def find_flag_name_mismatches(flag_names: Set[str]) -> List[Finding]:
    """CHECK 5：扫描 docs/*.md 与 CLI 帮助文案中的旧 flag 名。"""
    findings: List[Finding] = []
    scan_paths = [str(path) for path in Path(ROOT, "docs").glob("*.md")]
    scan_paths.extend(FLAG_SCAN_PATHS)

    for path in scan_paths:
        if not os.path.exists(path):
            continue
        text = read_utf8_text(path)
        rel_path = format_rel_path(path)
        for line_no, line in enumerate(text.splitlines(), start=1):
            for match in FLAG_TOKEN_PATTERN.finditer(line):
                token = match.group(0)
                if token in flag_names:
                    continue
                detail_lines = [
                    "来源: {0}".format(rel_path),
                    "未在 is_enabled(\"...\") 信源集合中找到该 flag 名",
                ]
                findings.append(
                    Finding(
                        check="FLAG_NAME_MISMATCH",
                        severity="WARN",
                        line=line_no,
                        snippet=token,
                        detail="\n".join(detail_lines),
                        file=path,
                        fingerprint_source=line,
                    )
                )
    return findings


def extract_detail_value(detail: str, prefix: str) -> str:
    """从 detail 中提取指定前缀的值。"""
    for line in detail.splitlines():
        if line.startswith(prefix):
            return line.split(prefix, 1)[1]
    return ""


def build_chapter_counts(findings: Sequence[Finding]) -> Dict[str, int]:
    """从过滤后的 CHECK 3 结果重建章节统计。"""
    counts: Dict[str, int] = {}
    for finding in findings:
        if finding.check != "HARDCODED_NUMBER":
            continue
        chapter = extract_detail_value(finding.detail, "章节: ")
        if not chapter:
            continue
        counts[chapter] = counts.get(chapter, 0) + 1
    return counts


def is_whitelisted(finding: Finding, whitelist: Sequence[WhitelistEntry]) -> bool:
    """检查单条结果是否命中白名单。"""
    check_code = CHECK_CODES.get(finding.check, "")
    rel_path = normalize_path_for_match(format_rel_path(finding.file or ""))
    base_name = os.path.basename(rel_path)
    for entry in whitelist:
        if entry.check != check_code:
            continue
        entry_path = normalize_path_for_match(entry.file)
        if entry_path not in (rel_path, base_name) and not rel_path.endswith(entry_path):
            continue
        if entry.fingerprint and entry.fingerprint in finding.fingerprint_source:
            return True
    return False


def apply_whitelist(
    findings: Sequence[Finding],
    whitelist: Sequence[WhitelistEntry],
) -> Tuple[List[Finding], int]:
    """过滤白名单命中的结果，返回保留结果与豁免数。"""
    kept: List[Finding] = []
    exempted = 0
    for finding in findings:
        if is_whitelisted(finding, whitelist):
            exempted += 1
            continue
        kept.append(finding)
    return kept, exempted


def render_header(check: str, findings: List[Finding]) -> str:
    """生成人类可读区块标题。"""
    counts = {"ERROR": 0, "WARN": 0, "INFO": 0}
    for finding in findings:
        counts[finding.severity] += 1
    parts = [f"{counts[level]} {level}" for level in ("ERROR", "WARN", "INFO") if counts[level]]
    suffix = ", ".join(parts) if parts else "0"
    return "================ {0} ({1}) ================".format(CHECK_TITLES[check], suffix)


def render_grouped_unreferenced(findings: List[Finding]) -> List[str]:
    """按父域分组输出幽灵键。"""
    groups: Dict[str, List[Finding]] = {}
    for finding in findings:
        group = "ROOT"
        for line in finding.detail.splitlines():
            if line.startswith("分组: "):
                group = line.split(": ", 1)[1]
                break
        groups.setdefault(group, []).append(finding)

    lines: List[str] = []
    for group in sorted(groups):
        lines.append("[INFO] {0} ({1})".format(group, len(groups[group])))
        for finding in sorted(groups[group], key=lambda item: item.snippet):
            lines.append("  - {0}".format(finding.snippet))
    if not lines:
        lines.append("（无）")
    return lines


def render_findings_text(
    grouped: Dict[str, List[Finding]],
    chapter_counts: Dict[str, int],
    exempted_count: int,
) -> str:
    """渲染默认人类可读输出。"""
    output_lines: List[str] = []

    for check in CHECK_ORDER:
        findings = grouped.get(check, [])
        output_lines.append(render_header(check, findings))
        if check == "UNREFERENCED_KEY":
            output_lines.extend(render_grouped_unreferenced(findings))
        elif findings:
            for finding in findings:
                output_lines.append(
                    "[{0}] L{1}  {2}".format(
                        finding.severity,
                        finding.line,
                        finding.snippet,
                    )
                )
                output_lines.extend(finding.detail.splitlines())
        else:
            output_lines.append("（无）")

        if check == "HARDCODED_NUMBER":
            output_lines.append("---------------- 章节统计 ----------------")
            if chapter_counts:
                for chapter, count in sorted(chapter_counts.items()):
                    output_lines.append("{0}: {1}".format(chapter, count))
            else:
                output_lines.append("（无）")

    severity_counts = {"ERROR": 0, "WARN": 0, "INFO": 0}
    for findings in grouped.values():
        for finding in findings:
            severity_counts[finding.severity] += 1

    output_lines.append("================ 汇总 ================")
    output_lines.append(
        "ERROR: {0} | WARN: {1} | INFO: {2}".format(
            severity_counts["ERROR"],
            severity_counts["WARN"],
            severity_counts["INFO"],
        )
    )
    output_lines.append("已豁免: {0} 条".format(exempted_count))
    return "\n".join(output_lines)


def run_checks(
    manual_text: str,
    balance_text: str,
    balance: Dict[str, Any],
    manual_path: str,
    balance_path: str,
    include_flag_check: bool = True,
) -> List[Finding]:
    """执行四类检查并返回结果。"""
    broken_refs, valid_refs = find_broken_refs(manual_text, balance, manual_path)
    unreferenced = find_unreferenced_keys(balance, valid_refs)
    hardcoded, _chapter_counts = find_hardcoded_numbers(manual_text, manual_path)
    retired = find_retired_content(manual_text, balance_text, manual_path, balance_path)
    flag_mismatches = find_flag_name_mismatches(collect_flag_names()) if include_flag_check else []
    findings = normalize_finding_order(
        broken_refs + unreferenced + hardcoded + retired + flag_mismatches
    )
    return findings


def group_findings(findings: Sequence[Finding]) -> Dict[str, List[Finding]]:
    """按检查项分组。"""
    grouped: Dict[str, List[Finding]] = {check: [] for check in CHECK_ORDER}
    for finding in findings:
        grouped.setdefault(finding.check, []).append(finding)
    return grouped


def run_selftest() -> int:
    """运行内存级最小自测。"""
    manual_text = "\n".join(
        [
            "# 自测",
            "## 四、前置章节",
            "恢复 99。",
            "## 五、行动类型",
            "正确占位：⟦bal:hp20.player_max_hp⟧。",
            "错误占位：⟦bal:economy.sinks.钩梭⟧。",
            "上限 24。",
            "例：上限 88 → 不应命中。",
            "导弹仍可购买。",
            "导弹已退役。",
            "## 六、地点与物品",
            "下表为 6 人局示例。",
            "| 阶段 | 轮次 |",
            "| --- | --- |",
            "| 黎明 | 1-12 |",
            "## 十三、天赋",
            "消耗 12。",
            "## 新手指南",
            "上限 33。",
        ]
    )
    balance_obj = {
        "_version": "test",
        "hp20": {"player_max_hp": 20},
        "economy": {"sinks": {"钩索": 2}},
        "unused": {"leaf": 1},
        "weapons": {"导弹": {"damage": 8}},
        "stage": {"encore_reward_menu": {"军事基地": ["导弹", "高斯步枪"]}},
    }
    balance_text = json.dumps(balance_obj, ensure_ascii=False, indent=2)

    findings = run_checks(
        manual_text=manual_text,
        balance_text=balance_text,
        balance=balance_obj,
        manual_path=DEFAULT_MANUAL_PATH,
        balance_path=DEFAULT_BALANCE_PATH,
        include_flag_check=False,
    )
    chapter_counts = build_chapter_counts(findings)
    grouped = group_findings(findings)

    broken = grouped["BROKEN_REF"]
    assert len(broken) == 1, "BROKEN_REF 应命中 1 条"
    assert "钩索" in broken[0].detail, "BROKEN_REF 应给出相近候选"

    unreferenced = grouped["UNREFERENCED_KEY"]
    assert any(item.snippet == "unused.leaf" for item in unreferenced), "应报告幽灵键"
    assert all(item.snippet != "hp20.player_max_hp" for item in unreferenced), "已引用键不应误报"

    hardcoded = grouped["HARDCODED_NUMBER"]
    assert any("上限 24" in item.snippet for item in hardcoded), "应命中正文硬编码数字"
    assert any("消耗 12" in item.snippet for item in hardcoded), "应命中天赋章硬编码数字"
    assert all(item.line not in (3, 8, 15, 18) for item in hardcoded), "排除项不应误报"
    assert chapter_counts.get("五、行动类型", 0) >= 1, "章节统计应记录第五章"

    retired = grouped["RETIRED_CONTENT"]
    assert any(item.severity == "WARN" and "导弹仍可购买" in item.snippet for item in retired), "应报告手册 WARN"
    assert any(item.severity == "INFO" and "导弹已退役" in item.snippet for item in retired), "应报告手册 INFO"
    assert any("来源: data\\balance.json" in item.detail or "来源: data/balance.json" in item.detail for item in retired), (
        "应扫描 balance 全文"
    )

    print("selftest: OK")
    return 0


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="检查手册源文件与 balance.json 的一致性")
    parser.add_argument("--json", action="store_true", dest="as_json", help="输出机器可读 JSON")
    parser.add_argument("--manual", default=DEFAULT_MANUAL_PATH, help="手册源文件路径")
    parser.add_argument("--balance", default=DEFAULT_BALANCE_PATH, help="balance.json 路径")
    parser.add_argument("--selftest", action="store_true", help="运行内存级自测并退出")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """脚本入口。"""
    args = parse_args(argv)

    if args.selftest:
        return run_selftest()

    try:
        manual_text = read_manual_text(args.manual)
        balance_text = read_utf8_text(args.balance)
        balance = json.loads(balance_text)
    except FileNotFoundError as error:
        print("❌ 文件不存在: {0}".format(error.filename), file=sys.stderr)
        return 2
    except json.JSONDecodeError as error:
        print("❌ balance JSON 解析失败: {0}".format(error), file=sys.stderr)
        return 2

    whitelist = load_whitelist(WHITELIST_PATH)
    findings = run_checks(
        manual_text=manual_text,
        balance_text=balance_text,
        balance=balance,
        manual_path=args.manual,
        balance_path=args.balance,
    )
    findings, exempted_count = apply_whitelist(findings, whitelist)
    chapter_counts = build_chapter_counts(findings)

    if args.as_json:
        json.dump([finding.to_json_dict() for finding in findings], sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print(render_findings_text(group_findings(findings), chapter_counts, exempted_count))

    has_error = any(finding.severity == "ERROR" for finding in findings)
    return 1 if has_error else 0


if __name__ == "__main__":
    sys.exit(main())
