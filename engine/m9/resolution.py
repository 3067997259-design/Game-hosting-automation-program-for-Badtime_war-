"""M9 分辨率合同机制核心（profile: m9-rfc，结算合同 RFC v0.3）。

- A/H 两阶段：A 阶段（命中/护甲/掩体/擦伤账目）先行，H 阶段（HP/死亡）后行；
  统一收尾写出 slot_resolved 与 resolution_kind（配合 action_system）。
- DIRECT_DAMAGE 身份：直达伤害仍是"伤害"，走完整死亡流程（可复活），
  只有来源标签 `absolute_death` 才跳过复活/保险/往世层。
- absolute_dead：不进入往世层、不参与投注/转仓/魂援；PP 冻结。
- 援助休整（aid_rest）：下一实际槽以 aid_rest 收尾；压制/石化/震荡保留到后续槽。
- 有效伤害统一：只损耗未破护甲/掩体或 A=0 不推进摇晃；H≥1 或耐久归零才计一次。

伤害数值只调 `combat.numeric_v2`（与引擎信源统一），本模块不重新实现伤害公式。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from combat import numeric_v2


@dataclass
class HitResolution:
    """一次攻击结算结果（A/H 两阶段账目）。"""
    raw: int = 0
    attribute: str = "普通"
    defense: int = 0
    damage: int = 0            # H 阶段实际 HP 伤害
    a_phase_absorbed: int = 0  # A 阶段护甲/掩体吸收
    broken: List[str] = field(default_factory=list)
    grazed: bool = False
    effective_hit: bool = False  # 有效伤害计数（摇晃推进条件）
    direct_damage: bool = False  # DIRECT_DAMAGE 身份（仍走死亡流程）
    absolute_death: bool = False  # 来源标签 absolute_death → 跳过复活


def resolve_attack(target: Any, raw_damage: int, attribute: str,
                   pierce_factor: float = 1.0, *,
                   direct_damage: bool = False,
                   absolute_death: bool = False) -> HitResolution:
    """A/H 两阶段单次攻击：A 阶段经 numeric_v2.resolve_hit 磨护甲/掩体，
    H 阶段按 damage 扣 HP（调用方负责死亡判定）。

    绝对免疫/替身/重定向由接入层在调用前拦截（本函数不做裁决）。
    """
    res = numeric_v2.resolve_hit(target, max(0, int(round(raw_damage))),
                                 attribute, pierce_factor=pierce_factor)
    hit = HitResolution(
        raw=max(0, int(round(raw_damage))),
        attribute=attribute,
        defense=res["defense"],
        damage=res["damage"],
        a_phase_absorbed=res["absorbed"],
        broken=list(res["broken"]),
        grazed=res["grazed"],
        direct_damage=direct_damage,
        absolute_death=absolute_death,
    )
    # 有效伤害统一（结算合同 §3 摇晃推进）：H≥1 或护甲/掩体耐久归零算一次
    hit.effective_hit = (res["damage"] >= 1) or bool(res["broken"])
    return hit


def is_absolute_dead_death(source_kind: str) -> bool:
    """来源标签是否为绝对死亡（absolute_death 专属来源）。"""
    return source_kind == "absolute_death"


def would_skip_revive(source_kind: str) -> bool:
    """绝对死亡跳过一切复活/保险（T7、G4 人形态免死等）。"""
    return is_absolute_dead_death(source_kind)


class ControlRegistry:
    """控制状态名录（冻结枚举）：压制 > 石化 > 震荡 优先级；
    aid_rest 是槽位收尾方式不是控制。即时效果（赤原猎风等）不入名录。"""

    PRIORITY = {"suppressed": 3, "petrified": 2, "shocked": 1, "stunned": 1}

    @classmethod
    def higher_or_equal(cls, new: str, existing: str) -> bool:
        return cls.PRIORITY.get(new, 0) >= cls.PRIORITY.get(existing, 0)

    @classmethod
    def is_control(cls, name: str) -> bool:
        return name in cls.PRIORITY


class AidRestTracker:
    """援助休整（aid_rest）：下一实际槽以 aid_rest 收尾，控制保留到后续槽。"""

    def __init__(self) -> None:
        self._pending: Dict[str, bool] = {}

    def mark(self, actor_id: str) -> None:
        self._pending[actor_id] = True

    def consume(self, actor_id: str) -> bool:
        was = self._pending.pop(actor_id, False)
        return was

    def pending(self, actor_id: str) -> bool:
        return self._pending.get(actor_id, False)


class SuppressRegistry:
    """压制通用裁决器（结算合同 §4：压制 > 石化 > 震荡）。

    未来压制来源统一在此登记，R3 每个 actor 实际槽裁决前查询；当前唯一来源
    G2 世末终曲仍经 `suppress_grant` 钩子消费（同槽 only-once 由 G2 状态保证），
    本注册表提供新增压制来源的统一通道，避免继续硬编码进 round_manager。
    """

    def __init__(self) -> None:
        self._suppressed: Dict[str, str] = {}

    def mark(self, actor_id: str, source_id: str) -> None:
        self._suppressed[actor_id] = source_id

    def clear(self, actor_id: str) -> None:
        self._suppressed.pop(actor_id, None)

    def is_suppressed(self, actor_id: str) -> bool:
        return actor_id in self._suppressed

    def source(self, actor_id: str) -> Optional[str]:
        return self._suppressed.get(actor_id)

    def clear_round(self) -> None:
        self._suppressed.clear()
