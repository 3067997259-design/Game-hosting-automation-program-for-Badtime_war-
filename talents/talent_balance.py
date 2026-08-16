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

    M9-rfc 下 G7 的战术数值路由到 m9_talents_extended.g7（独立分区），
    使风洞调参真正作用到运行代码；v2exp/legacy 仍读 talents.g7。
    """
    if not m7_enabled():
        return v1
    if talent_key == "g7" and value_keys \
            and experiments.is_enabled("m9_rfc"):
        key = value_keys[0]
        mapped = {
            "armor_pierce_durability_cost": "archer_break_armor_loss",
        }.get(key, key)
        node = bget("m9_talents_extended", "g7", default={}) or {}
        if isinstance(node, dict) and mapped in node:
            return node[mapped]
    node = bget("talents", talent_key, default={}) or {}
    for k in value_keys:
        if not isinstance(node, dict) or k not in node:
            return v1
        node = node[k]
    return node
