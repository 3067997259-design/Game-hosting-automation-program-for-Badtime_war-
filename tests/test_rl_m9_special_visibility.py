"""防失明回归：RL 动作空间必须"看见"引擎能产生的每个 special 指令。

背景（历史教训）：legacy 早期 RL 看不到 special，导致 G7 星野核心玩法无法学习。
本测试把"引擎每个 special 名/模板前缀 ∈ RL 动作空间 或 显式登记"变成硬约束：
任何引擎新增特殊操作而 RL 未声明/未登记，测试即红。

登记目录：
- KNOWN_UNMAPPED_M9_SPECIALS   ：M9 专属字面 special，尚未进入 RL 槽位 → 阶段 M9-B 动作空间扩展。
- KNOWN_UNMAPPED_TEMPLATE_PREFIXES：M9/M4 动态模板前缀（运行期拼名，无法静态占槽）→ 阶段 M9-B。

阶段 M9-B 把它们逐个迁入 rl/action_space.SPECIAL_OPS 后，从登记表移除，测试保持绿。
"""

from __future__ import annotations

import re
from pathlib import Path

from rl.action_space import (
    ACTION_COUNT,
    ACTION_COUNT_M9,
    ACTION_COUNT_M9_FIXED,
    IDX_INTERACT_M9_BASE,
    IDX_SHOOT_M9_BASE,
    IDX_SPECIAL_M9_BASE,
    SPECIAL_OPS,
    SPECIAL_OPS_M9,
    SPECIAL_OPS_M9_TEMPLATE,
)

ROOT = Path(__file__).resolve().parents[1]
SPECIAL_OP_SRC = (ROOT / "actions" / "special_op.py").read_text(encoding="utf-8")

RL_SPECIAL_SET: set[str] = (
    set(SPECIAL_OPS) | set(SPECIAL_OPS_M9) | set(SPECIAL_OPS_M9_TEMPLATE)
)

# ── 未映射登记表：M9 字面与模板均已进 RL 动作空间，表为空；新增引擎 special 必须
#    入槽或在此登记，否则 test_every_literal_* / test_every_template_* 红 ──
KNOWN_UNMAPPED_M9_SPECIALS: dict[str, str] = {}
KNOWN_UNMAPPED_TEMPLATE_PREFIXES: dict[str, str] = {}


def _literal_special_names() -> set[str]:
    """从引擎源码提取所有字面 special 名（name": "..."）。"""
    return set(re.findall(r'name":\s*"([^"]+)"', SPECIAL_OP_SRC))


def _template_prefixes() -> set[str]:
    """从引擎源码提取动态模板前缀（name": f"静态部分{...}"）。"""
    return set(re.findall(r'name":\s*f"([^"{}]+)\{', SPECIAL_OP_SRC))


def test_every_literal_engine_special_is_visible_or_registered() -> None:
    missing = {n for n in _literal_special_names()
               if n not in RL_SPECIAL_SET and n not in KNOWN_UNMAPPED_M9_SPECIALS}
    assert not missing, (
        "引擎存在 RL 未声明也未登记的 special（RL 失明）："
        f"{sorted(missing)}。要么加入 rl/action_space.SPECIAL_OPS，"
        "要么登记到 KNOWN_UNMAPPED_M9_SPECIALS（说明批次）"
    )


def test_every_template_prefix_is_covered_or_registered() -> None:
    unmatched = {p for p in _template_prefixes()
                 if not any(n.startswith(p) for n in RL_SPECIAL_SET)
                 and p not in KNOWN_UNMAPPED_TEMPLATE_PREFIXES}
    assert not unmatched, (
        f"引擎新增动态模板前缀未在 RL 覆盖或登记：{sorted(unmatched)}"
    )


def test_legacy_template_slots_declared() -> None:
    """legacy 时代已知模板槽位必须已在 RL 静止表内（蓄力/更衣）。"""
    for name in (
        "蓄力电磁步枪", "蓄力高斯步枪",
        "更衣水着-shielder", "更衣临战-Archer", "更衣临战-shielder",
    ):
        assert name in RL_SPECIAL_SET, f"RL 应声明引擎模板可产生的 special：{name}"


def test_m9_special_slots_declared_and_source_consistent() -> None:
    """M9 专属字面 special 已入槽，且名字确为引擎源码字面量（单一信源）："""
    literals = _literal_special_names()
    for name in SPECIAL_OPS_M9:
        assert name in RL_SPECIAL_SET, f"M9 special 应已入 RL 动作空间：{name}"
        assert name in literals, (f"M9 special {name!r} 不在 actions/special_op.py 字面名集合，"
                                   f"应为引擎真实产出或从 SPECIAL_OPS_M9 移除")


def test_action_count_layout_consistent() -> None:
    """布局一致性：M9 槽位于 choose 之后；
    语义段 = 137 + 字面块(8) + 模板族块(4) + shoot 段(5)，随后固定预留
    6 个 home move 槽，保证 2～6 人 checkpoint 共用一个策略 schema。
    """
    assert ACTION_COUNT == 137
    assert ACTION_COUNT_M9 == 154
    assert IDX_SHOOT_M9_BASE == (
        ACTION_COUNT + len(SPECIAL_OPS_M9) + len(SPECIAL_OPS_M9_TEMPLATE)
    )
    assert ACTION_COUNT_M9 == IDX_SHOOT_M9_BASE + 5
    assert IDX_INTERACT_M9_BASE == ACTION_COUNT_M9 + 6
    assert ACTION_COUNT_M9_FIXED == 217
    assert IDX_SPECIAL_M9_BASE == ACTION_COUNT


def test_home_move_slots_use_fixed_six_player_schema() -> None:
    """home move 固定预留 6 槽；不存在的玩家槽恒不点亮。

    固定维度允许课程从 1v1 升到多人局而不重建 action space/checkpoint。
    """
    from engine import experiments
    from rl.action_space import IDX_MOVE_HOME_M9_BASE, rl_action_count
    assert IDX_MOVE_HOME_M9_BASE == ACTION_COUNT_M9
    experiments.set_profile("m9-rfc")
    try:
        for n in (2, 3, 6):
            assert rl_action_count(n_players=n) == ACTION_COUNT_M9_FIXED
    finally:
        experiments.reset()


def test_m9_template_families_covered() -> None:
    """引擎动态模板前缀全部被 RL 覆盖：蓄力/更衣在 base 槽，热线举报*/指挥*/交易*/拆卸* 在模板族。"""
    prefixes = _template_prefixes()
    uncovered = {p for p in prefixes
                 if not any(n.startswith(p) for n in RL_SPECIAL_SET)}
    assert not uncovered, f"未覆盖的引擎模板前缀：{sorted(uncovered)}"
    for token in SPECIAL_OPS_M9_TEMPLATE:
        assert token in RL_SPECIAL_SET
        assert any(p == token or p.startswith(token) for p in prefixes), (
            f"模板族 {token!r} 应对应引擎存在"
        )


def test_disabled_virus_not_required_in_m9() -> None:
    """释放病毒：M9 禁用；RL 保留槽位无妨，但登记表不需包含它。"""
    assert "释放病毒" in RL_SPECIAL_SET  # legacy 保留槽位
    assert "释放病毒" not in KNOWN_UNMAPPED_M9_SPECIALS
