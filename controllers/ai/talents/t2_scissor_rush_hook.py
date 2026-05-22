"""ScissorRushAIHook —— T2「剪刀手一突」天赋AI钩子

核心机制：Vigilance — 首次 find 或首次被 find 各获得 1 额外行动回合。
AI 策略：在候选列表中优先插入 find 指令以触发 vigilance。
"""

from __future__ import annotations
from typing import Dict, List, Optional, Any
from controllers.ai.talents.base_hook import BaseTalentAIHook
from controllers.ai.game_query import GameQuery


class ScissorRushAIHook(BaseTalentAIHook):
    talent_name = "剪刀手一突"

    def __init__(self, controller: Any):
        self._ctrl = controller

    def should_override_candidates(
        self, player: Any, state: Any, available: List[str]
    ) -> Optional[List[str]]:
        if not self._is_my_talent(player):
            return None

        talent = getattr(player, 'talent', None)
        if not talent:
            return None

        # Vigilance 已全部触发 → 不覆盖
        find_available = not getattr(talent, 'find_triggered', True)
        if not find_available:
            return None

        # 找不同地点的存活敌人
        my_loc = GameQuery.get_location_str(player)
        candidates = []
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            t = state.get_player(pid)
            if not t or not t.is_alive():
                continue
            t_loc = GameQuery.get_location_str(t)
            if t_loc != my_loc and "find" in available:
                threat = getattr(self._ctrl, '_threat_scores', {}).get(t.name, 0)
                candidates.append((t, threat))

        if not candidates:
            return None

        target = max(candidates, key=lambda x: x[1])[0]
        return [f"find {target.name}", "forfeit"]

    def _is_my_talent(self, player: Any) -> bool:
        t = getattr(player, 'talent', None)
        return bool(t and getattr(t, 'name', '') == self.talent_name)
