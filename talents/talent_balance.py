"""天赋数值换算入口（M7，experiment: m7_talents，v2.0 §7）。

天赋钩子至今返回 v1 量纲（0.5 单位），hp20 流水线直接透传——这是 --no-talents
的根源。M7 让天赋在 m7_talents 开关下读 hp20 量纲（balance.json talents 分区），
否则返回 v1 默认值（向后兼容，七条 golden 锚点不受影响）。

换算依据：严格按设计文档 §7 换算表（不是机械 ×N——§0.3 律6"换算不是 ×10"）。
"""
from __future__ import annotations
from typing import Any

from engine import experiments
from engine.balance import get as bget


def m7_enabled() -> bool:
    """天赋 hp20 量纲开关。"""
    return experiments.is_enabled("m7_talents")


def talent_num(talent_key: str, *value_keys: str, v1: Any) -> Any:
    """读天赋数值：m7 下读 balance.json talents.{talent_key}.{value_keys}，否则 v1。

    用法：talent_num("g1", "attack_bonus", v1=2.0)  # m7=3, v1=2.0
    缺键静默回退 v1（每个天赋只声明自己有的键）。
    """
    if not m7_enabled():
        return v1
    node = bget("talents", talent_key, default={}) or {}
    for k in value_keys:
        if not isinstance(node, dict) or k not in node:
            return v1
        node = node[k]
    return node
