"""M9 天赋文档同步治理测试（docs/m9/ai ↔ engine/m9/talents 信源对照）。

守护目标：`docs/m9/ai/talents.md` 父文档及其分片卡（`slots_t1t4t6t7.md`、
`slots_g0g1g2g3.md`、`slots_g4g5g6g7.md`）必须覆盖 adapter 源码暴露给 AI 的
接口事实（父文档 §6 治理条款的机械校验）：

1. 每个 adapter 的 `get_t0_option` 返回字典里的 `m9_kind`（engine/m9/talents/ 下
   t1/t2/t3/t4/t6/t7/g0..g7，类名 *9）——对应分片卡必须包含该字符串；
2. 每个 adapter 内 `controller.choose(..., context={"situation": ...})` 传入的
   `situation` 标签（静态文本提取即可，另捕 `situation=` 关键字用法）——对应
   分片卡必须包含该字符串；
3. `actions/special_op.py` 在 m9_enabled 分支挂出的 M9 特殊操作名：
   破界 / 武器破界 / 热线举报 / 竞选队长 / 指挥——父文档与卡片须提及
   （最低要求：G3 卡含破界/武器破界；T6 卡含热线/竞选/指挥）。

提取方式：纯文件读取 + 正则，不 import engine.m9.talents.*（避免跨 profile 导入
副作用，也不需要 experiments/profile；分片卡可能由并行任务稍后创建——文件缺失时
对应断言整体跳过（pytest.mark.skip），不视为失败）。

缺口报告：跨分片、跨槽位的所有缺口汇总为单条失败信息列出（附源码 file:line
锚点），不提前短路。
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TALENTS_DIR = ROOT / "engine" / "m9" / "talents"
FRAGMENT_DIR = ROOT / "docs" / "m9" / "ai"
PARENT_DOC = FRAGMENT_DIR / "talents.md"
SPECIAL_OP_SOURCE = ROOT / "actions" / "special_op.py"

# slot_id → adapter 文件名（engine/m9/talents/ 下，类名 *9）
ADAPTER_FILE = {
    "T1": "t1.py",
    "T2": "t2.py",
    "T3": "t3.py",
    "T4": "t4.py",
    "T6": "t6.py",
    "T7": "t7.py",
    "G0": "g0.py",
    "G1": "g1.py",
    "G2": "g2.py",
    "G3": "g3.py",
    "G4": "g4.py",
    "G5": "g5.py",
    "G6": "g6.py",
    "G7": "g7.py",
}

# 分片卡 → 覆盖槽位（与 talents.md §1 分片索引一致）
FRAGMENT_SLOTS = {
    "slots_t1t4t6t7.md": ("T1", "T2", "T3", "T4", "T6", "T7"),
    "slots_g0g1g2g3.md": ("G0", "G1", "G2", "G3"),
    "slots_g4g5g6g7.md": ("G4", "G5", "G6", "G7"),
}

# M9 特殊操作名（actions/special_op.py 的 m9_enabled 分支，_append_m9_specials）
SPECIAL_OPS = ("破界", "武器破界", "热线举报", "竞选队长", "指挥")

# 分片卡最低要求：G3 卡须含破界/武器破界；T6 卡须含热线/竞选/指挥
# （T6 相关名称允许长/短两种写法，任一提及即算覆盖）
G3_CARD_SPECIALS = ("破界", "武器破界")
T6_CARD_SPECIALS = (("热线举报", "热线"), ("竞选队长", "竞选"), ("指挥",))

# 提取正则：m9_kind 与 situation 均为双引号字面量（源码现状如此）；
# 另捕 `situation=` 关键字用法（当前源码无，保留以防未来引入）。
M9_KIND_RE = re.compile(r'm9_kind"\s*:\s*"([a-z0-9_]+)"')
SITUATION_RE = re.compile(r'"situation"\s*:\s*"([a-zA-Z0-9_]+)"')
SITUATION_KW_RE = re.compile(r'situation\s*=\s*["\']([a-zA-Z0-9_]+)["\']')


def _line_of(text: str, match: re.Match[str]) -> int:
    """match 所在 1-based 行号。"""
    return text[: match.start()].count("\n") + 1


@lru_cache(maxsize=1)
def _extract_adapter_facts() -> dict[str, dict[str, dict[str, list[int]]]]:
    """每个槽位的 {fact_kind: {value: [行号, ...]}}（m9_kind / situation）。"""
    out: dict[str, dict[str, dict[str, list[int]]]] = {}
    for slot_id, filename in ADAPTER_FILE.items():
        text = (TALENTS_DIR / filename).read_text(encoding="utf-8")
        facts: dict[str, dict[str, list[int]]] = {"m9_kind": {}, "situation": {}}
        for pattern, kind in (
            (M9_KIND_RE, "m9_kind"),
            (SITUATION_RE, "situation"),
            (SITUATION_KW_RE, "situation"),
        ):
            for m in pattern.finditer(text):
                facts[kind].setdefault(m.group(1), []).append(_line_of(text, m))
        out[slot_id] = facts
    return out


def _special_op_anchors() -> dict[str, list[int]]:
    """actions/special_op.py 中每个特殊操作名出现的行号（来源锚点）。"""
    lines = SPECIAL_OP_SOURCE.read_text(encoding="utf-8").splitlines()
    return {name: [i for i, line in enumerate(lines, 1) if name in line]
            for name in SPECIAL_OPS}


def _anchor_text(rel_path: str, lines: list[int]) -> str:
    return f"{rel_path}:行{','.join(map(str, sorted(set(lines))))}"


def _fragment_missing_items(fragment_name: str) -> list[str]:
    """返回该分片卡缺失的 m9_kind / situation / 卡级特殊操作清单（含来源锚点）。

    分片卡不存在时 `pytest.skip`（并行任务产出中，跳过而非失败）。
    """
    path = FRAGMENT_DIR / fragment_name
    if not path.exists():
        pytest.skip(
            f"分片卡 {fragment_name} 尚未创建（并行任务产出中），跳过该分片断言")
    card_text = path.read_text(encoding="utf-8")
    facts = _extract_adapter_facts()
    missing: list[str] = []

    # 1+2. 每个槽位的 m9_kind / situation 覆盖
    for slot_id in FRAGMENT_SLOTS[fragment_name]:
        adapter = ADAPTER_FILE[slot_id]
        for kind in ("m9_kind", "situation"):
            for value, lines in sorted(facts[slot_id][kind].items()):
                if value not in card_text:
                    missing.append(
                        f"{slot_id} {kind}「{value}」未出现在 {fragment_name}"
                        f"（来源 {_anchor_text(f'engine/m9/talents/{adapter}', lines)}）")

    # 3. 卡级特殊操作最低要求
    if fragment_name == "slots_g0g1g2g3.md":
        anchors = _special_op_anchors()
        for name in G3_CARD_SPECIALS:
            if name not in card_text:
                missing.append(
                    f"G3 卡未提及特殊操作「{name}」"
                    f"（来源 {_anchor_text('actions/special_op.py', anchors[name])}）")
    if fragment_name == "slots_t1t4t6t7.md":
        anchors = _special_op_anchors()
        for variants in T6_CARD_SPECIALS:
            if not any(variant in card_text for variant in variants):
                missing.append(
                    f"T6 卡未提及特殊操作「{'/'.join(variants)}」"
                    f"（来源 {_anchor_text('actions/special_op.py', anchors[variants[0]])}）")
    return missing


def test_parent_doc_mentions_m9_special_ops() -> None:
    """父文档 talents.md 必须提及全部 M9 特殊操作名（special_op.py m9_enabled）。"""
    if not PARENT_DOC.exists():
        pytest.skip(f"父文档不存在：{PARENT_DOC.relative_to(ROOT)}")
    doc_text = PARENT_DOC.read_text(encoding="utf-8")
    anchors = _special_op_anchors()
    missing = [
        f"父文档 talents.md 未提及特殊操作「{name}」"
        f"（来源 {_anchor_text('actions/special_op.py', anchors[name])}）"
        for name in SPECIAL_OPS
        if name not in doc_text
    ]
    assert not missing, "父文档 talents.md 缺少以下 M9 特殊操作覆盖：\n" + "\n".join(missing)


def test_slots_t1t4t6t7_cards_cover_source_facts() -> None:
    """核心槽分片卡覆盖 T1/T2/T3/T4/T6/T7 的 m9_kind、situation 与 T6 特殊操作。"""
    missing = _fragment_missing_items("slots_t1t4t6t7.md")
    assert not missing, "slots_t1t4t6t7.md 覆盖缺口：\n" + "\n".join(missing)


def test_slots_g0g1g2g3_cards_cover_source_facts() -> None:
    """神代 G 上半分片卡覆盖 G0/G1/G2/G3 的 m9_kind、situation 与 G3 特殊操作。"""
    missing = _fragment_missing_items("slots_g0g1g2g3.md")
    assert not missing, "slots_g0g1g2g3.md 覆盖缺口：\n" + "\n".join(missing)


def test_slots_g4g5g6g7_cards_cover_source_facts() -> None:
    """神代 G 下半分片卡覆盖 G4/G5/G6/G7 的 m9_kind、situation。"""
    missing = _fragment_missing_items("slots_g4g5g6g7.md")
    assert not missing, "slots_g4g5g6g7.md 覆盖缺口：\n" + "\n".join(missing)


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
