"""BaseCard —— 物料牌抽象基类。"""
from __future__ import annotations
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from engine.ish_bosheth import IshBosheth


class BaseCard:
    name: str = ""
    count: int = 2
    voice: Optional[str] = None  # Acc/Ind/Str 限定，None=通用
    desc: str = ""

    def play(self, player, ish: IshBosheth, turn_mgr: Any) -> None:
        """执行牌效果。turn_mgr 是 ActionTurnManager 实例。"""
        raise NotImplementedError

    def is_playable(self, player) -> bool:
        """检查声部限制。"""
        if self.voice is None:
            return True
        return getattr(player, 'emotion', None) == self.voice
