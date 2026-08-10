"""M9 天赋层机制文件（profile: m9-rfc，聚光索引 v0.5 + 各天赋合同）。

按 19 份 current 合同落地天赋的结构事实（身份/槽位/机制挂点），数值一律读
`m9_talents_extended.*` / `m9_system.*`（[待风洞]）。本层是结构注册表：
具体演出结算由各合同对应机制（action_system / resolution / g3_chain /
g0_world_poem / pp）承接，不在本文件重实现数值。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from engine.balance import get as bget


def _ext(talent_key: str, value_key: str, default):
    return bget("m9_talents_extended", talent_key, value_key, default=default)


# ── 聚光索引（talent_spotlight v0.5）：身份枚举 ──
SPOTLIGHT_IMPROVISE = "improvise"      # 即演
SPOTLIGHT_PUBLIC = "public"            # 公演
SPOTLIGHT_FORESIGHT = "foresight"      # 伏笔
SPOTLIGHT_TURNING = "turning"          # 转折


@dataclass
class SpotlightIdentity:
    slot_id: str                # 天赋槽位名（M7 名 → M9 名映射见 slot_map）
    identity: str = SPOTLIGHT_IMPROVISE
    uses_sp: int = 1
    retired: bool = False       # T5 退役 → 槽位转 G0


# 槽位迁移表（talent_action v0.3 §八）：T5 退役 → G0；其余保留槽位名
SLOT_MIGRATION: Dict[str, str] = {
    "T5": "G0",
}

# 聚光索引：14 槽位 → 身份（局部正文一律跳转独立 RFC，不从索引反推未写效果）
SPOTLIGHT_INDEX: Dict[str, SpotlightIdentity] = {
    "T1": SpotlightIdentity("T1", SPOTLIGHT_IMPROVISE, 1),
    "T2": SpotlightIdentity("T2", SPOTLIGHT_FORESIGHT, 0),
    "T3": SpotlightIdentity("T3", SPOTLIGHT_PUBLIC, 2),   # 仅 2 SP 公演（DOC-045）
    "T4": SpotlightIdentity("T4", SPOTLIGHT_IMPROVISE, 1),
    "T5": SpotlightIdentity("T5", SPOTLIGHT_IMPROVISE, 1, retired=True),
    "T6": SpotlightIdentity("T6", SPOTLIGHT_FORESIGHT, 0),
    "T7": SpotlightIdentity("T7", SPOTLIGHT_FORESIGHT, 0),
    "G0": SpotlightIdentity("G0", SPOTLIGHT_PUBLIC, 2),
    "G1": SpotlightIdentity("G1", SPOTLIGHT_TURNING, 1),
    "G2": SpotlightIdentity("G2", SPOTLIGHT_PUBLIC, 2),
    "G3": SpotlightIdentity("G3", SPOTLIGHT_PUBLIC, 2),
    "G4": SpotlightIdentity("G4", SPOTLIGHT_TURNING, 1),
    "G5": SpotlightIdentity("G5", SPOTLIGHT_FORESIGHT, 0),
    "G6": SpotlightIdentity("G6", SPOTLIGHT_IMPROVISE, 1),
    "G7": SpotlightIdentity("G7", SPOTLIGHT_PUBLIC, 2),
}


def resolve_slot(slot_id: str) -> str:
    """槽位迁移（T5 退役 → G0；其余原样）。"""
    return SLOT_MIGRATION.get(slot_id, slot_id)


def spotlight(slot_id: str) -> SpotlightIdentity:
    return SPOTLIGHT_INDEX[resolve_slot(slot_id)]


# ── 天赋机制数值挂点（全部 [待风洞]，只读 balance）──
def g0_drone_stats() -> Dict:
    return {
        "max_hp": int(_ext("g0", "drone_hp", 4)),
        "damage": int(_ext("g0", "drone_damage", 2)),
    }


def g1_form_entropy() -> Dict:
    """G1 三段形态：失熵量表 0→cap、形态驱动累积率（结构冻结，数值待风洞）。"""
    return {
        "entropy_cap": int(_ext("g1", "entropy_cap", 10)),
        "form_rate_armorless": float(_ext("g1", "entropy_rate_normal", 1.0)),
        "form_rate_secondary": float(_ext("g1", "entropy_rate_secondary", 1.5)),
        "form_rate_full": float(_ext("g1", "entropy_rate_full", 2.0)),
    }


def g4_ember_pool() -> Dict:
    """G4 十二火种：余烬池与 ember_floor（结构冻结）。"""
    return {
        "max_embers": int(_ext("g4", "max_embers", 12)),
        "ember_floor": int(_ext("g4", "ember_floor", 3)),
    }


def g6_template_pool_categories() -> Tuple[str, ...]:
    """G6 即演重演模板池大类白名单（v0.2 §三/§四）。"""
    return ("move", "interact", "find", "lock", "attack")


def g7_tactical_macro_cost_table() -> Dict[str, int]:
    """G7 战术宏 Cost 表（v0.3 §2.4；Cost 每 R0 回满）。"""
    return dict(_ext("g7", "macro_costs", {"shoot": 1, "throw": 1, "find": 1,
                                           "lock": 1, "move": 1}))
