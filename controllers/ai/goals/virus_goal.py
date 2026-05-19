"""
VirusCureGoal —— 病毒免疫目标

覆盖旧架构 L3（病毒应急）+ L11（病毒预防）

持久化获取病毒免疫的意图：
1. 去商店/医院买防毒面具，或去魔法所学封闭
2. 获取后自动完成
"""

from __future__ import annotations
from typing import List, Optional, Any

from controllers.ai.goals.base_goal import BaseGoal
from controllers.ai.constants import debug_ai_basic


class VirusCureGoal(BaseGoal):
    """持久化病毒免疫目标。优先级高于一般发育。"""

    # 各地点可获取的病毒免疫手段
    CURE_LOCATIONS = {
        "商店": "interact 防毒面具",       # 病毒期间免费，平常需凭证
        "医院": "interact 防毒面具",       # 需凭证
        "魔法所": "interact 封闭",          # 免费，2回合
    }

    def __init__(
        self,
        preferred_location: str = "商店",
        priority: int = 9,  # 高于一般发育(4)，低于战斗(6)和逃跑(10)
        debug_name: str = "AI",
    ):
        super().__init__()
        self.preferred_location = preferred_location
        self.priority = priority
        self.description = f"获取病毒免疫（去{preferred_location}）"
        self._debug_name = debug_name
        self._state: str = "MOVING"  # MOVING → CURING → DONE

    def is_expired(self, player: Any, state: Any) -> bool:
        """病毒已消失或自己已死亡"""
        if not player.is_alive():
            return True
        virus = getattr(state, 'virus', None)
        if virus is None or not getattr(virus, 'is_active', False):
            # 病毒没激活且自己没感染 → 不再需要
            if not self._is_infected(player, state):
                debug_ai_basic(self._debug_name,
                    "VirusCureGoal: 病毒已消失，目标过期")
                return True
        return False

    def is_achieved(self, player: Any, state: Any) -> bool:
        """获得病毒免疫后完成"""
        return self._has_virus_immunity(player)

    def _get_next_command_internal(
        self, player: Any, state: Any, available: List[str]
    ) -> Optional[str]:
        my_loc = self._get_location_str(player)
        vouchers = getattr(player, 'vouchers', 0)
        virus = getattr(state, 'virus', None)
        virus_active = getattr(virus, 'is_active', False) if virus else False

        if self._state == "MOVING":
            if my_loc == self.preferred_location:
                self._state = "CURING"
            elif "move" in available:
                return f"move {self.preferred_location}"

        if self._state == "CURING" and "interact" in available:
            if self.preferred_location in ("商店", "医院"):
                if vouchers < 1 and not virus_active:
                    # 病毒没激活时需要打工拿凭证
                    return "interact 打工"
                return "interact 防毒面具"
            elif self.preferred_location == "魔法所":
                learned = getattr(player, 'learned_spells', set())
                if "封闭" not in learned:
                    return "interact 封闭"

        return None

    @staticmethod
    def _get_location_str(player: Any) -> str:
        loc = getattr(player, 'location', None)
        return str(loc) if loc else ""

    @staticmethod
    def _has_virus_immunity(player: Any) -> bool:
        """检查是否有任何病毒免疫手段"""
        # 防毒面具
        for item in getattr(player, 'items', []):
            if getattr(item, 'name', '') == "防毒面具":
                return True
        # 封闭法术
        if "封闭" in getattr(player, 'learned_spells', set()):
            return True
        if getattr(player, 'has_seal', False):
            return True
        return False

    @staticmethod
    def _is_infected(player: Any, state: Any) -> bool:
        """检查玩家是否已感染病毒"""
        virus = getattr(state, 'virus', None)
        if not virus or not getattr(virus, 'is_active', False):
            return False
        return player.player_id in getattr(virus, 'infected_players', set())
