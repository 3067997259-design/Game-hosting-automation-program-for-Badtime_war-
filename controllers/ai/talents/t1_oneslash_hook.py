"""
OneSlashAIHook —— T1「一刀缭断」天赋AI钩子

核心职责：
  - 通过 get_development_needs_override 将「磨刀石」注入发育需求列表
  - 让 DevelopMind 自然地规划：拿小刀→拿凭证→去商店买磨刀石→磨刀
  - 磨刀的 special 指令由 Orchestrator._handle_develop 通用检测生成

设计原则：
  - 不接管命令生成（should_override_candidates 返回 None）
  - 只在发育需求列表中插入 "whetstone"，让正常发育系统处理一切
  - 武器就绪后返回空列表，不干预发育
"""

from __future__ import annotations
from typing import List, Optional, Any
from controllers.ai.talents.base_hook import BaseTalentAIHook


class OneSlashAIHook(BaseTalentAIHook):
    talent_name = "一刀缭断"

    def __init__(self, controller: Any):
        self._ctrl = controller

    # ════════════════════════════════════════════════════════
    #  get_development_needs_override：注入发育需求
    # ════════════════════════════════════════════════════════

    def get_development_needs_override(self, player: Any) -> Optional[List[str]]:
        """T1 发育引导：注入额外需求到发育优先级列表。

        逻辑：
          - 已有磨过的刀 (base_damage >= 2) → 不需要，返回 []
          - 已有蓄力高斯 (is_charged) → 不需要，返回 []
          - 已有小刀但没磨 → 需要磨刀石 → 返回 ["whetstone"]
          - 还没小刀 → 返回 []（正常的 "weapon" 需求已经覆盖了）
        """
        weapons = getattr(player, 'weapons', [])

        # 武器就绪 → 不干预
        has_sharpened = any(
            w.name == "小刀" and getattr(w, 'base_damage', 0) >= 2
            for w in weapons if w
        )
        has_charged_gauss = any(
            w.name == "高斯步枪" and getattr(w, 'is_charged', False)
            for w in weapons if w
        )
        if has_sharpened or has_charged_gauss:
            return []

        # 有刀但没磨 → 需要磨刀石
        has_knife = any(w.name == "小刀" for w in weapons if w)
        if has_knife:
            return ["whetstone"]

        # 还没刀 → 让正常 "weapon" 需求处理
        return []
