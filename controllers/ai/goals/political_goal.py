"""
PoliticalGoal —— 政治型玩家持久化目标

迁移旧架构 L14：PoliceMixin._cmd_police_political()

状态机：JOINING → BECOMING_CAPTAIN → ACTIVE
处理警察加入、竞选队长、举报犯罪者等政治行动
"""

from __future__ import annotations
from typing import List, Optional, Any, Callable

from controllers.ai.goals.base_goal import BaseGoal


class PoliticalGoal(BaseGoal):
    """政治型AI持久化目标。封装 _cmd_police_political 的调用。"""

    def __init__(
        self,
        cmd_political_fn: Callable,
        priority: int = 9,  # 高于发育(4-8)和战斗(6-8)，低于FleeGoal(10)
        debug_name: str = "AI",
    ):
        super().__init__()
        self._cmd_political_fn = cmd_political_fn
        self.priority = priority
        self.description = "政治行动（加入警察/竞选队长）"
        self._debug_name = debug_name

    def is_expired(self, player: Any, state: Any) -> bool:
        if not player.is_alive():
            return True
        # 当上队长后过期（由CaptainGoal接管）
        if getattr(player, 'is_captain', False):
            return True
        # 警察系统不可用
        police = getattr(state, 'police', None)
        if not police or getattr(police, 'permanently_disabled', False):
            return True
        # 已有非自己的队长 → 不能再当队长，过期
        if police and police.has_captain():
            return True
        # 已有其他玩家是警察 → 不能再加入，过期
        pe = getattr(state, 'police_engine', None)
        if pe:
            existing = pe.get_current_police_member_id()
            if existing is not None and existing != player.player_id:
                return True
        # 自己有犯罪记录 → 无法加入警察
        if getattr(player, 'is_criminal', False):
            return True
        if police and hasattr(police, 'is_criminal'):
            if police.is_criminal(player.player_id):
                return True
        return False

    def is_achieved(self, player: Any, state: Any) -> bool:
        # 当上队长时完成
        return getattr(player, 'is_captain', False)

    def _get_next_command_internal(
        self, player: Any, state: Any, available: List[str]
    ) -> Optional[str]:
        # 优先集结/追踪
        police = getattr(state, 'police', None)
        if police and police.report_phase == "reported" and police.reporter_id == player.player_id:
            if "assemble" in available:
                return "assemble"
        if "track_guide" in available:
            pe = getattr(state, 'police_engine', None)
            if pe:
                can_track, _ = pe.can_track_guide(player.player_id)
                if can_track:
                    return "track"

        loc = str(getattr(player, 'location', ''))
        is_police = getattr(player, 'is_police', False)

        # 还没加入警察 → 去警察局
        if not is_police:
            if loc == "警察局" and "recruit" in available:
                return "recruit"
            if "move" in available and loc != "警察局":
                return "move 警察局"

        # 已加入但没当队长 → 去竞选
        if is_police and "election" in available and loc == "警察局":
            return "election"
        if is_police and "move" in available and loc != "警察局":
            return "move 警察局"

        # 举报犯罪者
        if "report" in available and is_police:
            police_data = getattr(state, 'police', None)
            report_phase = getattr(police_data, 'report_phase', 'idle') if police_data else 'idle'
            has_captain = police_data.has_captain() if police_data and hasattr(police_data, 'has_captain') else False
            if report_phase == "idle" and not has_captain and loc == "警察局":
                for pid in state.player_order:
                    if pid == player.player_id:
                        continue
                    t = state.get_player(pid)
                    if t and t.is_alive():
                        is_crim = getattr(t, 'is_criminal', False)
                        if not is_crim and police_data and hasattr(police_data, 'is_criminal'):
                            is_crim = police_data.is_criminal(t.player_id)
                        if is_crim:
                            return f"report {t.name}"

        return None
