"""命令系统文档同步治理测试（docs/ai/commands*.md ↔ 命令系统源码信源对照）。

守护目标：`docs/ai/commands.md`（命令总表）、`docs/ai/commands_mapping.md`
（RL 索引布局）、`docs/ai/commands_choose.md`（T0/choose 同步）必须覆盖
命令系统源码暴露给 AI 的接口事实（机械校验）：

1. `actions/special_op.py` 注册的每个特殊操作名（静态 `"name": "…"` 与
   f-string 前缀如 蓄力/更衣/热线举报/指挥/拆卸）——`commands.md` **现役区**
   （`## 附录` 标题之前）必须包含该字符串；
2. `actions/*.py`（除 `__init__.py` / `action_registry.py`）每个行动模块的
   类型关键字（英文模块名或稳定别名，如 find→find_target、wake→wake_up）——
   `commands.md` 必须至少提及其中一种写法（starlight/police_command 为
   legacy-frozen，允许只在附录出现；其余允许出现在文档任意位置）；
3. `rl/action_space.py` 静态 `SPECIAL_OPS` 列表的 13 个特殊操作名——
   `commands_mapping.md` 必须包含；
4. `engine/m9/talents/*.py` 每个 adapter 的 `m9_kind` 值与
   `"situation": "…"` 上下文标签——`commands_choose.md` 必须包含；
5. 通用 T0 事实：`engine/action_turn.py` 的提示文案
   「是否在本回合开始时发动天赋？」与 action_type「talent_t0」——
   `commands_choose.md` 必须提及；
6. **现役/附录分区纪律（防回归）**：`commands.md` 只梳理**现役**指令；
   退役/冻结指令（legacy 警察引擎指令、G2/G5 演唱）只能出现在 `## 附录`
   标题之后的附录区，特殊类退役指令（report/election/designate/study/
   assemble/track/recruit/wake_police）不得出现在现役区（词边界匹配，
   避免 `tracking`、中文正文等误命中）。

提取方式：纯文件读取 + 正则，不 import 引擎模块（避免跨 profile 导入副作用，
也不需要 experiments/profile）。`docs/ai/` 三个文档可能由并行任务稍后创建——
对应文档缺失时该测试整体跳过（pytest.skip），不视为失败。

缺口报告：每个文档的全部缺口汇总为单条失败信息列出（附源码 file:line 锚点），
不提前短路。
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOC_DIR = ROOT / "docs" / "ai"
COMMANDS_DOC = DOC_DIR / "commands.md"
COMMANDS_MAPPING_DOC = DOC_DIR / "commands_mapping.md"
COMMANDS_CHOOSE_DOC = DOC_DIR / "commands_choose.md"

ACTIONS_DIR = ROOT / "actions"
SPECIAL_OP_SOURCE = ACTIONS_DIR / "special_op.py"
ACTION_SPACE_SOURCE = ROOT / "rl" / "action_space.py"
TALENTS_DIR = ROOT / "engine" / "m9" / "talents"
ACTION_TURN_SOURCE = ROOT / "engine" / "action_turn.py"

# 行动模块 → 可接受提及（英文关键字或中文命令形；缺省回退到英文模块名）。
# 排除 action_registry.py（注册表，非行动模块）与 __init__.py。
MODULE_MENTIONS: dict[str, tuple[str, ...]] = {
    "move": ("move", "移动"),
    "find_target": ("find_target", "find", "找到"),
    "lock_target": ("lock_target", "lock", "锁定"),
    "attack": ("attack", "攻击"),
    "shoot": ("shoot", "射箭"),
    "hook": ("hook", "钩索"),
    "interact": ("interact", "交互"),
    "forfeit": ("forfeit", "放弃"),
    "wake_up": ("wake_up", "wake", "起床"),
    "police_command": ("police_command", "police", "队长操控警察"),
    "special_op": ("special_op", "special", "特殊操作"),
    "starlight": ("starlight", "星光"),
    "applause_spend": ("applause_spend", "applause", "喝彩"),
}

# 通用 T0 事实（engine/action_turn.py 的 controller.choose 调用）
T0_PROMPT_TEXT = "是否在本回合开始时发动天赋？"
T0_KIND_TEXT = "talent_t0"

# 退役/冻结指令（硬编码，防回归；来源见 commands.md 附录「退役/冻结对照」）。
# 这些指令 M9 后已退役/冻结，只允许出现在 commands.md 的 `## 附录` 之后的
# 附录区；其中特殊类（ASCII 词）同时不得出现在现役区。
RETIRED = [
    "report",
    "assemble",
    "track",
    "recruit",
    "election",
    "designate",
    "study",
    "wake_police",
    "追寻那道光",
    "拼接遗憾",
    "Before light",
    "追光",
    "遗憾",
    "光色",
]
# 特殊类退役指令：必须同时满足「附录区出现」+「现役区不出现」。
RETIRED_SPECIAL_LIKE = frozenset(
    ("report", "election", "designate", "study", "assemble", "track", "recruit", "wake_police")
)

# 附录标题（commands.md 的 `## 附录：退役/冻结对照`）
APPENDIX_HEADING_RE = re.compile(r"^##\s*附录", re.MULTILINE)

# 提取正则（双引号字面量，源码现状如此）：
# 特殊操作注册：静态 "name": "…" 与 f-string 前缀（f"蓄力{w.name}" 等）。
SPECIAL_STATIC_RE = re.compile(r'specials\.append\(\s*\{\s*"name":\s*"([^"]+)"')
SPECIAL_FSTRING_RE = re.compile(r'specials\.append\(\s*\{\s*"name":\s*f"([^"{]+)')
# RL 静态列表内的字符串字面量（SPECIAL_OPS 块）。
SPECIAL_OPS_STRING_RE = re.compile(r'"([^"]+)"')
# T0 adapter 事实：m9_kind 与 situation 双引号字面量；
# 另捕 `situation=` 关键字用法（当前源码无，保留以防未来引入）。
M9_KIND_RE = re.compile(r'm9_kind"\s*:\s*"([a-z0-9_]+)"')
SITUATION_RE = re.compile(r'"situation"\s*:\s*"([a-zA-Z0-9_]+)"')
SITUATION_KW_RE = re.compile(r'situation\s*=\s*["\']([a-zA-Z0-9_]+)["\']')


def _line_of(text: str, match: re.Match[str]) -> int:
    """match 所在 1-based 行号。"""
    return text[: match.start()].count("\n") + 1


def _anchor_text(rel_path: str, lines: list[int]) -> str:
    return f"{rel_path}:行{','.join(map(str, sorted(set(lines))))}"


def _present_in(text: str, token: str) -> bool:
    r"""词边界判断 token 是否出现在 text 中。

    用 `(^|[^\w])…([^\w]|$)` 而非裸 `in`，避免把 `tracking` 里的 `track`、
    或中文正文连续字命中（Unicode 下中文属于 `\w`）。退订/退格等标点与
    反引号均视为边界，因此 `report`（反引号包裹）会被正确检出。
    """
    return re.search(
        rf"(^|[^\w]){re.escape(token)}([^\w]|$)", text, re.MULTILINE
    ) is not None


def _present_lines(text: str, token: str) -> list[int]:
    """text 中 token 出现的全部 1-based 行号（词边界匹配）。"""
    pattern = re.compile(rf"(^|[^\w]){re.escape(token)}([^\w]|$)", re.MULTILINE)
    return [_line_of(text, m) for m in pattern.finditer(text)]


def _doc_slices() -> tuple[str, str, int]:
    """把 commands.md 切分为 (现役区, 附录区, 附录标题行号)。

    现役区 = `## 附录` 标题之前；附录区 = 标题起（含）至文末。
    找不到标题时现役区 = 全文、附录区 = 空、标题行号 = -1（治理失败态）。
    """
    text = COMMANDS_DOC.read_text(encoding="utf-8")
    m = APPENDIX_HEADING_RE.search(text)
    if m is None:
        return text, "", -1
    return text[: m.start()], text[m.start():], _line_of(text, m)


@lru_cache(maxsize=1)
def _extract_special_op_names() -> dict[str, list[int]]:
    """actions/special_op.py 注册的特殊操作名 → 来源行号。

    静态 `"name": "…"` 与 f-string 前缀各取其一（f"蓄力{w.name}" → 蓄力），
    动态目标（拆卸/热线举报/指挥…移动）以稳定前缀覆盖。
    """
    text = SPECIAL_OP_SOURCE.read_text(encoding="utf-8")
    out: dict[str, list[int]] = {}
    for pattern in (SPECIAL_STATIC_RE, SPECIAL_FSTRING_RE):
        for m in pattern.finditer(text):
            out.setdefault(m.group(1), []).append(_line_of(text, m))
    return out


@lru_cache(maxsize=1)
def _extract_rl_specials() -> dict[str, list[int]]:
    """rl/action_space.py 静态 SPECIAL_OPS 列表的名称 → 来源行号。"""
    text = ACTION_SPACE_SOURCE.read_text(encoding="utf-8")
    start = text.index("SPECIAL_OPS: List[str] = [")
    end = text.index("assert len(SPECIAL_OPS)")
    block = text[start:end]
    out: dict[str, list[int]] = {}
    for m in SPECIAL_OPS_STRING_RE.finditer(block):
        line = text[: start + m.start()].count("\n") + 1
        out.setdefault(m.group(1), []).append(line)
    return out


@lru_cache(maxsize=1)
def _action_modules() -> dict[str, tuple[str, ...]]:
    """actions/*.py 行动模块名 → 可接受提及（英文关键字或中文命令形）。"""
    modules: dict[str, tuple[str, ...]] = {}
    for path in sorted(ACTIONS_DIR.glob("*.py")):
        if path.name in ("__init__.py", "action_registry.py"):
            continue
        modules[path.stem] = MODULE_MENTIONS.get(path.stem, (path.stem,))
    return modules


def _action_module_anchor(module: str) -> list[int]:
    """行动模块的类型声明行（docstring 首行「行动类型：…」，缺省回退第 1 行）。"""
    lines = (ACTIONS_DIR / f"{module}.py").read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines, 1):
        if "行动类型" in line:
            return [i]
    return [1]


@lru_cache(maxsize=1)
def _extract_t0_facts() -> dict[str, dict[str, dict[str, list[int]]]]:
    """engine/m9/talents/*.py → {file: {fact_kind: {value: [行号, ...]}}}。

    fact_kind ∈ {"m9_kind", "situation"}；无相关事实的文件产出空字典。
    """
    out: dict[str, dict[str, dict[str, list[int]]]] = {}
    for path in sorted(TALENTS_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        facts: dict[str, dict[str, list[int]]] = {"m9_kind": {}, "situation": {}}
        for pattern, kind in (
            (M9_KIND_RE, "m9_kind"),
            (SITUATION_RE, "situation"),
            (SITUATION_KW_RE, "situation"),
        ):
            for m in pattern.finditer(text):
                facts[kind].setdefault(m.group(1), []).append(_line_of(text, m))
        out[path.name] = facts
    return out


@lru_cache(maxsize=1)
def _t0_generic_anchors() -> dict[str, list[int]]:
    """engine/action_turn.py 中通用 T0 事实（提示文案 / talent_t0）所在行。"""
    lines = ACTION_TURN_SOURCE.read_text(encoding="utf-8").splitlines()
    return {
        T0_PROMPT_TEXT: [i for i, line in enumerate(lines, 1) if T0_PROMPT_TEXT in line],
        T0_KIND_TEXT: [i for i, line in enumerate(lines, 1) if T0_KIND_TEXT in line],
    }


def test_commands_doc_covers_special_ops() -> None:
    """commands.md 现役区必须提及 special_op.py 注册的全部特殊操作名。"""
    if not COMMANDS_DOC.exists():
        pytest.skip(f"命令总表不存在：{COMMANDS_DOC.relative_to(ROOT)}（并行任务产出中，跳过）")
    active_slice, _, _ = _doc_slices()
    anchors = _extract_special_op_names()
    missing = [
        f"commands.md 现役区未提及特殊操作「{name}」"
        f"（来源 {_anchor_text('actions/special_op.py', lines)}）"
        for name, lines in sorted(anchors.items())
        if name not in active_slice
    ]
    assert not missing, "commands.md 现役区缺少以下特殊操作覆盖：\n" + "\n".join(missing)


def test_commands_doc_covers_action_modules() -> None:
    """commands.md 必须提及每个行动模块的类型关键字（英文或中文形）。"""
    if not COMMANDS_DOC.exists():
        pytest.skip(f"命令总表不存在：{COMMANDS_DOC.relative_to(ROOT)}（并行任务产出中，跳过）")
    doc_text = COMMANDS_DOC.read_text(encoding="utf-8")
    missing: list[str] = []
    for module, mentions in _action_modules().items():
        if any(mention in doc_text for mention in mentions):
            continue
        anchor = _anchor_text(f"actions/{module}.py", _action_module_anchor(module))
        missing.append(
            f"commands.md 未提及行动模块「{module}」"
            f"（可接受提及：{' / '.join(mentions)}；来源 {anchor}）")
    assert not missing, "commands.md 缺少以下行动模块覆盖：\n" + "\n".join(missing)


def test_commands_doc_appendix_locks_retired() -> None:
    """退役/冻结指令分区纪律：只能出现在附录区；特殊类不得泄漏进现役区。

    防回归保证：legacy 警察引擎指令与 G2/G5 演唱指令永远不能重新进入
    commands.md 的现役契约区（`## 附录` 标题之前）。
    """
    if not COMMANDS_DOC.exists():
        pytest.skip(f"命令总表不存在：{COMMANDS_DOC.relative_to(ROOT)}（并行任务产出中，跳过）")
    active_slice, appendix_slice, heading_line = _doc_slices()
    problems: list[str] = []

    # 1) 每个退役/冻结指令必须出现在附录区（子串搜索）。
    for token in RETIRED:
        if token not in appendix_slice:
            anchor = (
                f"commands.md:附录起始行{heading_line}"
                if heading_line > 0
                else "commands.md:未找到「## 附录」标题（附录区为空）"
            )
            problems.append(
                f"退役指令「{token}」未出现在附录区（{anchor}）"
                f"——附录区必须覆盖全部退役/冻结指令")

    # 2) 特殊类退役指令不得出现在现役区（词边界匹配，避免 tracking/中文误命中）。
    for token in sorted(RETIRED_SPECIAL_LIKE):
        leak_lines = _present_lines(active_slice, token)
        if leak_lines:
            problems.append(
                f"退役指令「{token}」泄漏进现役区"
                f"（{_anchor_text('commands.md', leak_lines)}）"
                f"——只允许出现在附录区，不得进入现役契约")

    assert not problems, "commands.md 退役/冻结指令分区违规：\n" + "\n".join(problems)


def test_commands_mapping_doc_covers_rl_specials() -> None:
    """commands_mapping.md 必须提及 action_space.py 静态 SPECIAL_OPS 全部名称。"""
    if not COMMANDS_MAPPING_DOC.exists():
        pytest.skip(
            f"索引布局文档不存在：{COMMANDS_MAPPING_DOC.relative_to(ROOT)}"
            f"（并行任务产出中，跳过）")
    doc_text = COMMANDS_MAPPING_DOC.read_text(encoding="utf-8")
    anchors = _extract_rl_specials()
    missing = [
        f"commands_mapping.md 未提及 RL 特殊操作「{name}」"
        f"（来源 {_anchor_text('rl/action_space.py', lines)}）"
        for name, lines in sorted(anchors.items())
        if name not in doc_text
    ]
    assert not missing, "commands_mapping.md 缺少以下 RL 特殊操作覆盖：\n" + "\n".join(missing)


def test_commands_choose_doc_covers_t0_facts() -> None:
    """commands_choose.md 必须提及全部 adapter 的 m9_kind 值与 situation 标签。"""
    if not COMMANDS_CHOOSE_DOC.exists():
        pytest.skip(
            f"T0/choose 文档不存在：{COMMANDS_CHOOSE_DOC.relative_to(ROOT)}"
            f"（并行任务产出中，跳过）")
    doc_text = COMMANDS_CHOOSE_DOC.read_text(encoding="utf-8")
    missing: list[str] = []
    facts = _extract_t0_facts()
    for filename, kinds in facts.items():
        for kind in ("m9_kind", "situation"):
            for value, lines in sorted(kinds[kind].items()):
                if value not in doc_text:
                    missing.append(
                        f"commands_choose.md 未提及 {kind}「{value}」"
                        f"（来源 {_anchor_text(f'engine/m9/talents/{filename}', lines)}）")
    assert not missing, "commands_choose.md 缺少以下 T0 事实覆盖：\n" + "\n".join(missing)


def test_commands_choose_doc_generic_t0_facts() -> None:
    """commands_choose.md 必须提及通用 T0 提示文案与 action_type「talent_t0」。"""
    if not COMMANDS_CHOOSE_DOC.exists():
        pytest.skip(
            f"T0/choose 文档不存在：{COMMANDS_CHOOSE_DOC.relative_to(ROOT)}"
            f"（并行任务产出中，跳过）")
    doc_text = COMMANDS_CHOOSE_DOC.read_text(encoding="utf-8")
    anchors = _t0_generic_anchors()
    missing = [
        f"commands_choose.md 未提及通用 T0 事实「{fact}」"
        f"（来源 {_anchor_text('engine/action_turn.py', lines)}）"
        for fact, lines in anchors.items()
        if fact not in doc_text
    ]
    assert not missing, "commands_choose.md 缺少以下通用 T0 覆盖：\n" + "\n".join(missing)


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
