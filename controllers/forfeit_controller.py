"""
ForfeitController —— 断线占位控制器
═══════════════════════════════════════
在玩家断线等待重连期间，自动 forfeit 所有行动。
"""

from typing import List, Optional, Dict, Any
from controllers.base import PlayerController


class ForfeitController(PlayerController):
    """断线占位：所有操作返回安全默认值。"""

    def get_command(
        self,
        player: Any,
        game_state: Any,
        available_actions: List[str],
        context: Optional[Dict] = None,
    ) -> str:
        return "forfeit"

    def choose(
        self,
        prompt: str,
        options: List[str],
        context: Optional[Dict] = None,
    ) -> str:
        return options[0] if options else ""

    def choose_multi(
        self,
        prompt: str,
        options: List[str],
        max_count: int,
        min_count: int = 0,
        context: Optional[Dict] = None,
    ) -> List[str]:
        return options[:min_count] if min_count > 0 else []

    def confirm(
        self,
        prompt: str,
        context: Optional[Dict] = None,
    ) -> bool:
        return False
