"""
CaptainGoal —— 队长指挥持久化目标

迁移旧架构 L7：PoliceMixin._cmd_captain() 

状态机：MANAGING（持续管理警察单位）
每轮生成一条警察指挥命令（move/equip/attack/wake/study）
"""

from __future__ import annotations
from typing import List, Optional, Any, Callable

from controllers.ai.goals.base_goal import BaseGoal
from controllers.ai.constants import debug_ai_basic


class CaptainGoal(BaseGoal):
    """队长持久化目标。封装 _cmd_captain 的调用。"""

    def __init__(
        self,
        cmd_captain_fn: Callable,  # controller._cmd_captain(player, state, available) -> List[str]
        priority: int = 8,
        debug_name: str = "AI",
    ):
        super().__init__()
        self._cmd_captain_fn = cmd_captain_fn
        self.priority = priority
        self.description = "队长指挥警察"
        self._debug_name = debug_name

    def is_expired(self, player: Any, state: Any) -> bool:
        if not player.is_alive():
            return True
        if not getattr(player, 'is_captain', False):
            return True
        police = getattr(state, 'police', None)
        if police and getattr(police, 'permanently_disabled', False):
            return True
        return False

    def is_achieved(self, player: Any, state: Any) -> bool:
        return False

    def _get_next_command_internal(
        self, player: Any, state: Any, available: List[str]
    ) -> Optional[str]:
        if "police_command" not in available:
            return None
        cmds = self._cmd_captain_fn(player, state, available)
        if cmds:
            return cmds[0]
        return None
