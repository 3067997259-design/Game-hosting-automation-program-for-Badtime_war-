"""负面效果抗性两层制（experiment: hp20，v2.0 §2.5.1）。

有效抗性 = max(通用抗性, 来源免疫 tag→100)
- 通用抗性：player.general_resistance（0-100），吃天赋/状态修正 + 韧性脉冲
- 来源免疫 tag：装备叙事身份（陶瓷护甲 immune_electric 等），编译为对该来源
  抗性 100（真免疫）
- 抗性生效 = 降级不归零（零产出禁令的控制版）：M2 临时降级 = 下轮先攻 −2，
  M3 命中体系落地后改 −20 命中
- 韧性脉冲：行动剥夺类控制生效后通用抗性临时 +40 持续 2 轮（递减在
  round_manager R0），系统性消灭眩晕链/震荡锁
- DoT（灼烧/病毒）不走此系统——它们是压力钟，各有专属对策

管辖范围：行动剥夺类（眩晕/震荡/石化/致盲）。
"""
from __future__ import annotations
import random
from typing import Any, Dict, List, Optional

from engine.balance import get as bget

# 行动剥夺类效果（受本系统管辖）
CONTROL_EFFECTS = {"stun", "shock", "petrify", "blind"}


def effective_resistance(target: Any, source_tags: Optional[List[str]] = None) -> int:
    """计算目标对某次控制的有效抗性（0-100）。

    source_tags: 控制来源的标签（如 ["electric"]）——目标持有对应免疫 tag
    （陶瓷护甲 immune_electric）则抗性 = 100。
    """
    general = int(getattr(target, "general_resistance", 0))
    # 韧性脉冲叠加
    if getattr(target, "resist_pulse_rounds", 0) > 0:
        general += bget("status_resistance", "resilience_pulse", default=40)

    # 来源免疫 tag 层
    if source_tags and "electric" in source_tags:
        armor = getattr(target, "armor", None)
        outer = list(getattr(armor, "outer", []) or []) if armor else []
        for piece in outer:
            if "immune_electric" in getattr(piece, "special_tags", []):
                return 100

    return max(0, min(100, general))


def apply_control(target: Any, effect: str, game_state: Any = None,
                  source_tags: Optional[List[str]] = None) -> Dict[str, Any]:
    """对目标施加一次行动剥夺类控制，经抗性判定后全额生效或降级。

    返回 {"applied": bool, "degraded": bool, "resistance": int, "message": str}。
    调用方负责根据 applied 实际写入状态（is_stunned 等）——本函数只做判定
    与韧性脉冲/降级标记，不直接改控制状态（保持施加点的现有写入逻辑）。
    """
    resist = effective_resistance(target, source_tags)
    result = {"applied": True, "degraded": False, "resistance": resist, "message": ""}

    if resist >= 100:
        result["applied"] = False
        result["degraded"] = False
        result["message"] = f"{getattr(target, 'name', '?')} 完全免疫了该控制效果"
        return result

    if resist > 0 and random.randint(1, 100) <= resist:
        # 抗性生效 → 降级不归零（零产出禁令的控制版）
        result["applied"] = False
        result["degraded"] = True
        from engine import experiments as _exp
        if _exp.is_enabled("m3_accuracy"):
            # M3 起：降级 = 命中惩罚（自消耗 flag，accuracy.compute_hit_chance 消费）
            penalty = -abs(int(bget("accuracy", "degraded_shock_hit_penalty",
                                    default=20)))
            target._resist_degrade_hit_penalty = penalty
            result["message"] = (f"{getattr(target, 'name', '?')} 抵抗了控制"
                                 f"（降级：下次攻击命中 {penalty}）")
        else:
            # M2 临时映射：先攻惩罚（自消耗 flag，get_d6_bonus 消费）
            penalty = bget("status_resistance", "degraded_initiative_penalty",
                           default=-2)
            target._resist_degrade_penalty = penalty
            result["message"] = (f"{getattr(target, 'name', '?')} 抵抗了控制"
                                 f"（降级：下轮先攻 {penalty}）")
        return result

    # 全额生效 → 触发韧性脉冲（防连锁）
    pulse_rounds = bget("status_resistance", "resilience_pulse_rounds", default=2)
    target.resist_pulse_rounds = max(
        getattr(target, "resist_pulse_rounds", 0), pulse_rounds)
    result["message"] = f"{getattr(target, 'name', '?')} 被控制（韧性脉冲启动）"
    return result
