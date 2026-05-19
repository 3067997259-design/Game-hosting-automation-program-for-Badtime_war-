"""
BaseTalentAIHook —— 天赋AI行为钩子基类

每个天赋可选实现此钩子，提供AI决策所需的方法。
所有方法都有默认空实现（不改变行为）。
"""

from __future__ import annotations
from typing import List, Optional, Any


class BaseTalentAIHook:
    """天赋AI钩子基类。子类覆盖需要的方法。"""

    talent_name: str = "base"

    # ════════════════════════════════════════════════════════
    #  威胁评估
    # ════════════════════════════════════════════════════════

    def modify_threat_power(self, target: Any, base_power: float) -> float:
        """调整目标的威胁评估值。"""
        return base_power

    def modify_target_score(self, target: Any, base_score: float, player: Any) -> float:
        """调整目标评分（在 _pick_target 中调用）。"""
        return base_score

    # ════════════════════════════════════════════════════════
    #  优先级覆盖
    # ════════════════════════════════════════════════════════

    def should_override_candidates(
        self, player: Any, state: Any, available: List[str]
    ) -> Optional[List[str]]:
        """天赋是否需要完全接管候选命令生成。
        返回命令列表表示接管，返回 None 表示不接管。
        """
        return None

    # ════════════════════════════════════════════════════════
    #  发育
    # ════════════════════════════════════════════════════════

    def is_development_complete(self, player: Any, state: Any) -> Optional[bool]:
        """天赋感知的发育完成判定。返回 None 表示用默认逻辑。"""
        return None

    def get_development_needs_override(self, player: Any) -> Optional[List[str]]:
        """天赋覆盖的发育需求。返回 None 表示用默认逻辑。"""
        return None

    # ════════════════════════════════════════════════════════
    #  Choose 决策
    # ════════════════════════════════════════════════════════

    def handle_choose(
        self, player: Any, situation: str, options: List[str]
    ) -> Optional[str]:
        """天赋覆盖的 choose 决策。返回 None 表示用默认逻辑。"""
        return None

    # ════════════════════════════════════════════════════════
    #  特殊目标
    # ════════════════════════════════════════════════════════

    def get_special_goals(
        self, player: Any, state: Any
    ) -> List[Any]:
        """返回天赋建议的持久化目标列表。"""
        return []
