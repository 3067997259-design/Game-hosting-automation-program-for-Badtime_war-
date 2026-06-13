"""白昼世界时钟（M5，experiment: m5_clock，v2.0 §3）。

三幕剧压力曲线：黎明 → 白昼 → 黄昏 → 终焉，四阶段挂 R0。
段长 = segment_base + 初始人数（6 人局每段 12 轮）。时钟是破僵局的压力阀——
短局停在黎明/白昼，只有拖到后期的僵持局才坠入黄昏/终焉。

纯函数模块（无状态）：阶段由 current_round + 初始人数推导，各子系统读取阶段
做全局修正（伤害/经济/警察/控制）。
"""
from __future__ import annotations
from typing import Any

from engine import experiments
from engine.balance import get as bget

DAWN = "dawn"
DAY = "day"
DUSK = "dusk"
APOCALYPSE = "apocalypse"

_PHASE_ORDER = [DAWN, DAY, DUSK, APOCALYPSE]
_PHASE_LABEL = {DAWN: "黎明", DAY: "白昼", DUSK: "黄昏", APOCALYPSE: "终焉"}


def _segment_length(game_state: Any) -> int:
    """段长 = segment_base + 初始人数（v2.0 §3）。"""
    base = bget("world_clock", "segment_base", default=6)
    n = len(getattr(game_state, "player_order", []) or [])
    return max(1, base + n)


def current_phase(game_state: Any) -> str:
    """当前世界阶段（m5_clock 关闭恒为黎明=基础规则）。"""
    if not experiments.is_enabled("m5_clock"):
        return DAWN
    seg = _segment_length(game_state)
    rnd = getattr(game_state, "current_round", 1)
    idx = (max(1, rnd) - 1) // seg
    return _PHASE_ORDER[min(idx, len(_PHASE_ORDER) - 1)]


def is_phase(game_state: Any, phase: str) -> bool:
    return current_phase(game_state) == phase


def phase_value(phase: str, *keys: str, default: Any = None) -> Any:
    """读 balance world_clock 的阶段子键（如 phase_value("dusk","global_damage_bonus")）。"""
    return bget("world_clock", phase, *keys, default=default)


def active_value(game_state: Any, *keys: str, default: Any = None) -> Any:
    """读当前阶段的某子键（便捷封装）。"""
    return phase_value(current_phase(game_state), *keys, default=default)


def label(phase: str) -> str:
    return _PHASE_LABEL.get(phase, phase)
