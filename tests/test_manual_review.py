"""tools/manual_review.py：小节切分与配对、数字和名字对比、断开引用、必留规则。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("manual_review", ROOT / "tools" / "manual_review.py")
assert _SPEC and _SPEC.loader
manual_review = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = manual_review
_SPEC.loader.exec_module(manual_review)

BASE = """## 十一、原初天赋

前言。

### 11.1 T1「一刀缭断」

公演的这一刀无视屏障。

#### 精确结算

```text
# 这一行不是标题
A = 8
```

### 11.2 T2「剪刀手」

暗影步整局最多用 2 次。

### 11.3 T3「天星」

给自己一层 3 点的【玉璋】屏障。
"""


def test_sections_get_stable_keys_and_ignore_code_fences() -> None:
    sections = manual_review.parse_sections(BASE)
    keys = [section.key for section in sections]
    assert keys == ["__preamble__", "11", "11.1", "11.1/精确结算", "11.2", "11.3"]
    assert sections[2].code == "T1"


def test_renumbered_talent_section_is_matched_by_code() -> None:
    new = BASE.replace("### 11.2 T2「剪刀手」", "### 11.5 T2「暗影刺客」")
    pairs = manual_review.match_sections(manual_review.parse_sections(BASE),
                                         manual_review.parse_sections(new))
    keys = {(old.key if old else None, fresh.key if fresh else None) for old, fresh in pairs}
    assert ("11.2", "11.5") in keys


def test_number_and_name_changes_are_reported() -> None:
    new = BASE.replace("一层 3 点的【玉璋】", "一层 4 点的【岩璋】")
    report = manual_review.review(BASE, new, [], rules={})
    item = next(entry for entry in report["sections"] if entry["key"] == "11.3")
    assert (item["removed_names"], item["added_names"]) == (["玉璋"], ["岩璋"])
    assert any("3点" in fact for fact in item["removed_facts"])
    assert any("4点" in fact for fact in item["added_facts"])
    assert any(kind == "改" and "[-3-]{+4+}" in manual_review.inline(old, new)
               for kind, old, new in item["changes"])


def test_table_cell_rename_and_leftover_old_name_are_reported() -> None:
    base = BASE.replace("给自己一层 3 点的【玉璋】屏障。",
                        "| 怎么用 | 花费 |\n|---|---|\n| 玉璋（即演） | 1 SP |\n\n玉璋可以刷新。")
    new = base.replace("| 玉璋（即演） |", "| 岩璋（即演） |")
    report = manual_review.review(base, new, [], rules={})
    item = next(entry for entry in report["sections"] if entry["key"] == "11.3")
    assert item["renamed_cells"] == ["玉璋 → 岩璋"]
    assert any(name == "玉璋" and "本节" in places for _, name, places in report["stale_names"])


def test_broken_section_reference_is_reported() -> None:
    new = BASE.replace("暗影步整局最多用 2 次。", "暗影步整局最多用 2 次，见 §9.9。")
    report = manual_review.review(BASE, new, [], rules={})
    assert ("11.2", "9.9") in report["refs"]


def test_missing_must_keep_rule_is_reported() -> None:
    rules = {"must_keep": [{"code": "T1", "note": "公演斩击无视屏障", "any": ["无视.{0,8}屏障"]}]}
    kept = manual_review.review(BASE, BASE, [], rules=rules)
    assert kept["missing"] == []
    new = BASE.replace("公演的这一刀无视屏障。", "公演的这一刀更痛。")
    report = manual_review.review(BASE, new, [], rules=rules)
    assert report["missing"] == [("11.1", "T1", "公演斩击无视屏障")]
