"""手册改稿审阅：助手交稿时存快照，作者改完后按小节对比，列出需要核对的地方。

用法：
  python tools/manual_review.py snapshot <标签> [--note 说明]   助手交稿时记录基线
  python tools/manual_review.py list                            列出已有快照
  python tools/manual_review.py diff [标签] [--sections 11,12.1] [--manual 路径]

diff 会在 artifacts/ga-manual/reviews/ 写一份 Markdown 报告，内容包括：
规则数字和上限的增删、改掉的名字还在哪里出现、配置里找不到的数字、
必须保留却没找到的规则（见 docs/v2.0-ga/manual_review_rules.json）、断开的 § 引用、逐句改动。
这些都是提示，不是结论：最后由助手逐条核对，并向作者确认新增的机制。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
MANUAL = ROOT / "docs" / "v2.0-ga" / "manual_draft_v0.1.md"
DOCS = ROOT / "docs" / "v2.0-ga"
SNAPSHOTS = ROOT / "artifacts" / "ga-manual" / "snapshots"
REVIEWS = ROOT / "artifacts" / "ga-manual" / "reviews"
RULES = DOCS / "manual_review_rules.json"
BALANCE = ROOT / "data" / "balance.json"

HEADING = re.compile(r"^(#{2,4})\s+(.+?)\s*$")
CHAPTER = re.compile(r"^([一二三四五六七八九十]+)、")
NUMBERED = re.compile(r"^(\d+(?:\.\d+)+)(?=\s|$)")
TALENT_CODE = re.compile(r"(?<![A-Za-z])([TG]\d)(?!\d)")
# 天赋小节的标题是“编号「名字」”；第三章“T1：主行动”这类阶段名不算天赋。
TALENT_HEADING = re.compile(r"(?<![A-Za-z])([TG]\d)(?!\d)\s*「")
NAME = re.compile(r"[「【]([^」】\n]{1,24})[」】]|\*\*([^*\n]{1,24})\*\*")
SUFFIX = re.compile(r"（[^）]{1,8}）$")
REF = re.compile(r"§(\d+(?:\.\d+)*)")
UNIT = r"个百分点|%|点|HP|SP|PP|层|次|轮|格|发|枚|件|名|耐久|信用点|回合|段|刻度|倍"
FACT = re.compile(rf"(?P<num>[+\-−]?\d+(?:\.\d+)?)\s*(?P<unit>{UNIT})?")
NOT_FACT = re.compile(r"§\d+(?:\.\d+)*|(?<![A-Za-z])[TGRD]\d(?!\d)|V\d+(?:\.\d+)*|DEC-\d+")
DESIGN_WORDS = re.compile(r"DEC-\d|已定|风洞|校准|铁律|钉 ?\d|RFC")
CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def cn_to_int(text: str) -> int:
    """把“十七”这类章号转成整数，只支持 1～99。"""
    if text == "十":
        return 10
    if text.startswith("十"):
        return 10 + CN_DIGITS[text[1:]]
    if "十" in text:
        tens, _, ones = text.partition("十")
        return CN_DIGITS[tens] * 10 + (CN_DIGITS[ones] if ones else 0)
    return CN_DIGITS[text]


@dataclass
class Section:
    """手册中的一个标题小节，正文不含下级标题。"""

    key: str
    level: int
    title: str
    line_no: int
    lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        """小节正文。"""
        return "\n".join(self.lines)

    @property
    def code(self) -> str | None:
        """标题里的天赋编号，如 T1、G7；数值表小节也算，用来认数字。"""
        match = TALENT_CODE.search(self.title)
        return match.group(1) if match else None

    @property
    def talent_code(self) -> str | None:
        """只认天赋本节的编号（编号后跟「名字」），阶段名 T0～T2 不算。"""
        match = TALENT_HEADING.search(self.title)
        return match.group(1) if match else None


def parse_sections(text: str) -> list[Section]:
    """按 ##～#### 标题切分；代码块里的井号不算标题。"""
    sections = [Section("__preamble__", 1, "（开头）", 1)]
    parents: dict[int, str] = {}
    seen: Counter[str] = Counter()
    fenced = False
    for number, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith("```"):
            fenced = not fenced
        match = None if fenced else HEADING.match(line)
        if not match:
            sections[-1].lines.append(line)
            continue
        level, title = len(match.group(1)), match.group(2)
        chapter, numbered = CHAPTER.match(title), NUMBERED.match(title)
        if level == 2:
            key = str(cn_to_int(chapter.group(1))) if chapter else title
        elif numbered:
            key = numbered.group(1)
        else:
            key = f"{parents.get(level - 1, '')}/{title}"
        seen[key] += 1
        if seen[key] > 1:
            key = f"{key}#{seen[key]}"
        parents[level] = key
        for deeper in [lvl for lvl in parents if lvl > level]:
            del parents[deeper]
        sections.append(Section(key, level, title, number))
    return sections


def match_sections(
    old: list[Section], new: list[Section],
) -> list[tuple[Section | None, Section | None]]:
    """先按编号配对，再按天赋编号，最后按标题相似度；剩下的算增删。"""
    pairs: list[tuple[Section | None, Section | None]] = []
    new_by_key = {s.key: s for s in new}
    used: set[str] = set()
    pending: list[Section] = []
    for section in old:
        twin = new_by_key.get(section.key)
        if twin is not None and twin.key not in used:
            pairs.append((section, twin))
            used.add(twin.key)
        else:
            pending.append(section)
    for section in pending:
        candidates = [s for s in new if s.key not in used and s.level == section.level]
        twin = next((s for s in candidates if section.talent_code and
                     s.talent_code == section.talent_code), None)
        if twin is None and candidates:
            best = max(candidates, key=lambda s: SequenceMatcher(None, section.title,
                                                                 s.title).ratio())
            if SequenceMatcher(None, section.title, best.title).ratio() >= 0.6:
                twin = best
        if twin is not None:
            used.add(twin.key)
        pairs.append((section, twin))
    pairs.extend((None, s) for s in new if s.key not in used)
    return pairs


def units(text: str) -> list[str]:
    """把正文拆成句子；表格行和代码行整行算一句，表格分隔行忽略。"""
    result = []
    fenced = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            fenced = not fenced
            continue
        if not line or re.fullmatch(r"\|[\s\-:|]+\|", line):
            continue
        if fenced or line.startswith("|"):
            result.append(line)
            continue
        result.extend(part.strip() for part in re.split(r"(?<=[。！？；])", line) if part.strip())
    return result


def inline(old: str, new: str) -> str:
    """字符级改动标记：[-删掉的-]{+新加的+}。"""
    parts = []
    matcher = SequenceMatcher(None, old, new, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            parts.append(old[i1:i2])
            continue
        if op in ("delete", "replace"):
            parts.append(f"[-{old[i1:i2]}-]")
        if op in ("insert", "replace"):
            parts.append(f"{{+{new[j1:j2]}+}}")
    return "".join(parts)


def sentence_changes(old: list[str], new: list[str]) -> list[tuple[str, str, str]]:
    """逐句对比，相近的句子配成“改”，其余记“删”或“增”。"""
    changes: list[tuple[str, str, str]] = []
    matcher = SequenceMatcher(None, old, new, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            continue
        olds, news, taken = old[i1:i2], new[j1:j2], set()
        for sentence in olds:
            scores = [(SequenceMatcher(None, sentence, other, autojunk=False).ratio(), index)
                      for index, other in enumerate(news) if index not in taken]
            score, index = max(scores, default=(0.0, -1))
            if score >= 0.45:
                taken.add(index)
                changes.append(("改", sentence, news[index]))
            else:
                changes.append(("删", sentence, ""))
        changes.extend(("增", "", other) for index, other in enumerate(news) if index not in taken)
    return changes


def names(text: str) -> Counter[str]:
    """书名号、方头括号和加粗里的名字。"""
    return Counter(a or b for a, b in NAME.findall(text))


def cell_renames(old: str, new: str) -> list[tuple[str, str]]:
    """配对的表格行里整格换掉的短文字，多半是改名；数字变化交给数字检查。"""
    if not (old.startswith("|") and new.startswith("|")):
        return []
    old_cells = [cell.strip() for cell in old.strip("|").split("|")]
    new_cells = [cell.strip() for cell in new.strip("|").split("|")]
    if len(old_cells) != len(new_cells):
        return []
    result = []
    for before, after in zip(old_cells, new_cells):
        if before == after or not before or not after or len(before) > 16 or len(after) > 16:
            continue
        if re.search(r"\d", before + after):
            continue
        result.append((SUFFIX.sub("", before), SUFFIX.sub("", after)))
    return result


def facts(text: str) -> list[tuple[str, str, str]]:
    """抽出“数字 + 单位”和前面几个字的上下文；章节号、天赋编号不算。"""
    cleaned = NOT_FACT.sub(lambda m: " " * len(m.group(0)), text)
    found = []
    for match in FACT.finditer(cleaned):
        number = match.group("num").replace("−", "-")
        before = cleaned[max(0, match.start() - 8):match.start()].strip()
        before = re.split(r"[。！？；，、|\n]", before)[-1].strip()
        found.append((number, match.group("unit") or "", before))
    return found


def fact_diff(old: str, new: str) -> tuple[list[str], list[str]]:
    """比较两段文字里的数字事实，返回（旧有新无，新有旧无），附上下文。"""
    def describe(items: Iterable[tuple[str, str, str]], keys: Counter) -> list[str]:
        result, budget = [], Counter(keys)
        for number, unit, context in items:
            if budget[(number, unit)] > 0:
                budget[(number, unit)] -= 1
                result.append(f"{context}「{number}{unit}」" if context else f"「{number}{unit}」")
        return result

    old_facts, new_facts = facts(old), facts(new)
    old_counter = Counter((n, u) for n, u, _ in old_facts)
    new_counter = Counter((n, u) for n, u, _ in new_facts)
    return (describe(old_facts, old_counter - new_counter),
            describe(new_facts, new_counter - old_counter))


def config_numbers(code: str | None) -> set[str]:
    """数值配置里出现过的数（含比例换算成的百分数），用于提示陌生数字。"""
    data = json.loads(BALANCE.read_text(encoding="utf-8"))["ga"]
    values: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, bool) or not isinstance(node, (int, float)):
            return
        else:
            values.add(str(int(node)) if float(node).is_integer() else str(node))
            if isinstance(node, float) and abs(node) <= 1:
                values.add(str(round(node * 100)))
            values.add(str(abs(int(node))) if float(node).is_integer() else str(abs(node)))

    for key, block in data.items():
        if key not in ("talents", "_meta"):
            walk(block)
    if code and code in data.get("talents", {}):
        walk(data["talents"][code])
    return values


def section_text(sections: list[Section], key: str) -> str:
    """某小节连同它下级小节的正文。"""
    return "\n".join(s.text for s in sections if s.key == key or s.key.startswith(key + "/"))


def in_scope(key: str, scope: list[str]) -> bool:
    """小节是否落在 --sections 指定的范围内。"""
    return not scope or any(key == item or key.startswith(item + ".") or
                            key.startswith(item + "/") for item in scope)


def snapshot_index() -> list[dict[str, Any]]:
    """读取快照登记表。"""
    path = SNAPSHOTS / "index.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def cmd_snapshot(label: str, note: str) -> int:
    """把当前手册存成带标签的快照。"""
    index = snapshot_index()
    if any(item["label"] == label for item in index):
        print(f"快照标签已存在：{label}")
        return 1
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    target = SNAPSHOTS / f"{label}.md"
    shutil.copyfile(MANUAL, target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    index.append({"label": label, "file": target.name, "sha256": digest,
                  "created": datetime.now().isoformat(timespec="seconds"), "note": note})
    (SNAPSHOTS / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n",
                                          encoding="utf-8", newline="\n")
    print(f"已存快照 {label}（sha256 {digest[:16]}）")
    return 0


def cmd_list() -> int:
    """列出全部快照。"""
    for item in snapshot_index():
        print(f"{item['label']}\t{item['created']}\t{item['sha256'][:16]}\t{item['note']}")
    return 0


def review(
    base_text: str, new_text: str, scope: list[str], rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """生成审阅数据：逐节改动与全局检查。rules 省略时读取规则文件。"""
    old_sections, new_sections = parse_sections(base_text), parse_sections(new_text)
    if rules is None:
        rules = json.loads(RULES.read_text(encoding="utf-8")) if RULES.exists() else {}
    report: dict[str, Any] = {"sections": [], "refs": [], "missing": [], "stale_names": []}
    numeric_keys = {s.key for s in new_sections if re.fullmatch(r"\d+(?:\.\d+)*", s.key)}
    for section in new_sections:
        if not in_scope(section.key, scope):
            continue
        for ref in REF.findall(section.text + " " + section.title):
            if ref not in numeric_keys:
                report["refs"].append((section.key, ref))
    for old, new in match_sections(old_sections, new_sections):
        key = (new or old).key
        if not in_scope(key, scope) or (old and new and old.text == new.text and
                                        old.title == new.title):
            continue
        old_text, new_text_part = (old.text if old else ""), (new.text if new else "")
        changes = sentence_changes(units(old_text), units(new_text_part))
        removed_facts, added_facts = fact_diff(old_text, new_text_part)
        old_names = names((old.title + "\n" + old_text) if old else "")
        new_names = names((new.title + "\n" + new_text_part) if new else "")
        renamed = sorted({pair for kind, before, after in changes if kind == "改"
                          for pair in cell_renames(before, after)})
        known = config_numbers((new or old).code)
        changed_new = "\n".join(n for kind, _, n in changes if kind in ("改", "增"))
        strange = sorted({f"{ctx}「{num}{unit}」" for num, unit, ctx in facts(changed_new)
                          if num.lstrip("+-") not in known})
        design = sorted(set(DESIGN_WORDS.findall(changed_new)))
        report["sections"].append({
            "key": key, "old_title": old.title if old else None,
            "new_title": new.title if new else None, "changes": changes,
            "removed_facts": removed_facts, "added_facts": added_facts,
            "removed_names": sorted((old_names - new_names).elements()),
            "added_names": sorted((new_names - old_names).elements()),
            "renamed_cells": [f"{before} → {after}" for before, after in renamed],
            "stale_candidates": sorted(set((old_names - new_names).elements()) |
                                       {before for before, _ in renamed}),
            "strange_numbers": strange, "design_words": design,
        })
    all_new = {s.key: s for s in new_sections}
    for item in report["sections"]:
        for name in item["stale_candidates"]:
            if len(name) < 2:
                continue
            places = [("本节" if key == item["key"] else key) for key, section in all_new.items()
                      if name in section.title + section.text]
            for doc in sorted(DOCS.glob("*.md")):
                if doc.resolve() == MANUAL.resolve():
                    continue
                for number, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
                    if name in line:
                        places.append(f"{doc.name}:{number}")
            if places:
                report["stale_names"].append((item["key"], name, places))
    for rule in rules.get("must_keep", []):
        targets = [s for s in new_sections if s.level == 3 and s.talent_code == rule["code"]]
        if not targets or not in_scope(targets[0].key, scope):
            continue
        body = section_text(new_sections, targets[0].key)
        if not any(re.search(pattern, body) for pattern in rule["any"]):
            report["missing"].append((targets[0].key, rule["code"], rule["note"]))
    return report


def render(report: dict[str, Any], header: list[str]) -> str:
    """把审阅数据写成 Markdown。"""
    lines = ["# 手册改稿审阅", "", *header, "", "## 总览", "",
             "| 小节 | 标题 | 增/删/改句 | 数字变化 | 名字变化 | 陌生数字 |", "|---|---|---|---|---|---|"]
    for item in report["sections"]:
        counts = Counter(kind for kind, _, _ in item["changes"])
        title = item["new_title"] or f"（已删除）{item['old_title']}"
        if item["old_title"] and item["new_title"] and item["old_title"] != item["new_title"]:
            title = f"{item['old_title']} → {item['new_title']}"
        lines.append(f"| {item['key']} | {title} | {counts['增']}/{counts['删']}/{counts['改']} | "
                     f"{len(item['removed_facts']) + len(item['added_facts'])} | "
                     f"{len(item['removed_names']) + len(item['added_names']) +
                        len(item['renamed_cells'])} | "
                     f"{len(item['strange_numbers'])} |")
    lines += ["", "## 全局检查", "", "### 断开的 § 引用", ""]
    lines += [f"- {key} 引用了不存在的 §{ref}" for key, ref in report["refs"]] or ["- 无"]
    lines += ["", "### 必须保留、但没找到的规则", ""]
    lines += [f"- {key}（{code}）：{note}" for key, code, note in report["missing"]] or ["- 无"]
    lines += ["", "### 删掉的名字还出现在别处（需要同步）", ""]
    lines += [f"- {key}「{name}」：{'、'.join(places)}"
              for key, name, places in report["stale_names"]] or ["- 无"]
    lines += ["", "## 逐节详情", ""]
    for item in report["sections"]:
        title = item["new_title"] or item["old_title"]
        lines += [f"### {title if title.startswith(item['key']) else item['key'] + ' ' + title}", ""]
        for label, values in (("删掉的名字", item["removed_names"]), ("新的名字", item["added_names"]),
                              ("表格里的改名", item["renamed_cells"]),
                              ("旧有新无的数字", item["removed_facts"]),
                              ("新有旧无的数字", item["added_facts"]),
                              ("配置里找不到的数字", item["strange_numbers"]),
                              ("设计用语", item["design_words"])):
            if values:
                lines.append(f"- {label}：{'；'.join(values)}")
        lines += ["", "逐句改动：", ""]
        for kind, old, new in item["changes"]:
            body = inline(old, new) if kind == "改" else (old or new)
            lines.append(f"- {kind}：{body}")
        lines.append("")
    return "\n".join(lines) + "\n"


def cmd_diff(label: str | None, manual: Path, scope: list[str]) -> int:
    """对比快照和当前手册，写报告并打印摘要。"""
    index = snapshot_index()
    if not index:
        print("还没有快照")
        return 1
    entry = next((item for item in index if item["label"] == label), None) if label else index[-1]
    if entry is None:
        print(f"找不到快照：{label}")
        return 1
    base_text = (SNAPSHOTS / entry["file"]).read_text(encoding="utf-8")
    new_bytes = manual.read_bytes()
    report = review(base_text, new_bytes.decode("utf-8"), scope)
    digest = hashlib.sha256(new_bytes).hexdigest()
    header = [f"- 基线：{entry['label']}（{entry['sha256'][:16]}，{entry['created']}）{entry['note']}",
              f"- 对比：{manual.name}（{digest[:16]}）",
              f"- 范围：{'、'.join(scope) if scope else '全部'}"]
    REVIEWS.mkdir(parents=True, exist_ok=True)
    target = REVIEWS / f"{entry['label']}--{digest[:8]}.md"
    target.write_text(render(report, header), encoding="utf-8", newline="\n")
    changed = len(report["sections"])
    print(f"改动小节 {changed}，断开引用 {len(report['refs'])}，缺失规则 {len(report['missing'])}，"
          f"待同步旧名 {len(report['stale_names'])}")
    print(f"报告：{target.relative_to(ROOT)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    snap = sub.add_parser("snapshot")
    snap.add_argument("label")
    snap.add_argument("--note", default="")
    sub.add_parser("list")
    diff = sub.add_parser("diff")
    diff.add_argument("label", nargs="?")
    diff.add_argument("--manual", type=Path, default=MANUAL)
    diff.add_argument("--sections", default="")
    args = parser.parse_args(argv)
    if args.command == "snapshot":
        return cmd_snapshot(args.label, args.note)
    if args.command == "list":
        return cmd_list()
    scope = [item.strip() for item in args.sections.split(",") if item.strip()]
    return cmd_diff(args.label, args.manual, scope)


if __name__ == "__main__":
    sys.exit(main())
