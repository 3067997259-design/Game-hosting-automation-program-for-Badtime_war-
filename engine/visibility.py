"""统一可见性判定（M3 属性分轨，experiment: m3_accuracy，v2.0 §1.2/§2.7）。

m3 规则（拍板 §1.2）：可见性是二元判定——观察者持有与目标**任一**隐身件
对应属性的探测 → 目标对其可见（闪避加成按属性逐条算，可见性一刀切）。

m3 关闭时回退 markers.is_visible_to 的 v1 布尔语义（全局探测 vs 全局隐身）。
INVISIBLE_SUPPRESSED（面对面压制）与 DETECTED_BY（已被看穿）语义两套通用。
"""
from __future__ import annotations
from typing import Any

from engine import experiments


def can_see_m(observer: Any, target: Any, markers: Any) -> bool:
    """核心判定（只依赖 markers——供 action_enumerator 等无 state 处使用）。"""
    target_id = getattr(target, "player_id", None)
    observer_id = getattr(observer, "player_id", None)
    if target_id is None or observer_id is None or target_id == observer_id:
        return True

    # M9 G5 微澜（W4）：目标被 G5 微澜揭示 → G5 对其无视隐身/闪避，直至
    # G5 下一个实际结算的 ActionGrant 结束（round_manager 槽收尾清除）。
    if getattr(target, "_m9_ripple_ignore_stealth_from", None) == observer_id:
        return True

    if not experiments.is_enabled("m3_accuracy"):
        return markers.is_visible_to(
            target_id, observer_id, getattr(observer, "has_detection", False))

    # ── m3 属性对位规则 ──
    if not markers.has(target_id, "INVISIBLE"):
        return True  # 未隐身（含 SUPPRESSED 压制中）恒可见
    if markers.has_relation(target_id, "DETECTED_BY", observer_id):
        return True  # 已被看穿一次 → 恒可见（v1 语义沿用）

    target_stealth = getattr(target, "stealth_attrs", None) or set()
    observer_detect = getattr(observer, "detection_attrs", None) or set()
    if target_stealth & observer_detect:
        return True  # 任一属性对位即破隐

    if not target_stealth:
        # 无属性记录的隐身（天赋隐身等）→ 回退 v1 全局探测布尔
        return bool(getattr(observer, "has_detection", False))
    return False


def can_see(observer: Any, target: Any, game_state: Any) -> bool:
    """observer 能否看见 target（目标选择与行动隐匿共用入口）。"""
    return can_see_m(observer, target, game_state.markers)


def mark_detected_if_seen(observer: Any, target: Any, game_state: Any) -> None:
    """观察者看穿了隐身目标 → 建立 DETECTED_BY（之后恒可见）。

    lock/find 成功路径调用；与 v1 的「探测发现」语义一致。
    """
    target_id = getattr(target, "player_id", None)
    observer_id = getattr(observer, "player_id", None)
    if (target_id and observer_id
            and game_state.markers.has(target_id, "INVISIBLE")
            and can_see(observer, target, game_state)):
        game_state.markers.on_player_detected(observer_id, target_id)
