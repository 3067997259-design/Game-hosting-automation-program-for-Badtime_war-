"""RearmGoal —— 武器换装（获取克制目标护甲的武器）

当 CombatMind 判定 all_countered=True 时，此目标持久化直到
拿到能击穿目标护甲的武器为止。

状态机：MOVING → INTERACTING → ACHIEVED
"""

from __future__ import annotations
from typing import List, Optional, Any

from controllers.ai.goals.base_goal import BaseGoal
from controllers.ai.game_query import GameQuery
from controllers.ai.constants import debug_ai_basic


class RearmGoal(BaseGoal):
    """获取克制目标护甲的武器。

    is_achieved 直接用 all_weapons_countered 判定——不拘泥于特定武器，
    只要有任何武器能击穿目标护甲即完成。
    """

    priority: int = 5

    def __init__(self, target_id: str, debug_name: str = ""):
        super().__init__()
        self._target_id = target_id
        self._state: str = "MOVING"  # MOVING → INTERACTING → ACHIEVED
        self._dest: Optional[str] = None
        self._query = GameQuery()
        self.description = f"武器换装→{debug_name}" if debug_name else "武器换装"
        self._debug_name = debug_name

    # ── 生命周期 ──

    def is_expired(self, player: Any, state: Any) -> bool:
        target = state.get_player(self._target_id) if state else None
        return not target or not target.is_alive()

    def is_achieved(self, player: Any, state: Any) -> bool:
        target = state.get_player(self._target_id) if state else None
        if not target:
            return True
        return not GameQuery.all_weapons_countered(player, target)

    # ── 命令生成 ──

    def _get_next_command_internal(
        self, player: Any, state: Any, available: List[str]
    ) -> Optional[str]:
        loc = self._location(player)

        if self._state == "MOVING":
            dest = self._pick_destination(player, state, loc)
            if dest is None:
                return None
            self._dest = dest
            if dest != loc and "move" in available:
                debug_ai_basic(self._debug_name,
                    f"RearmGoal: 移动到 {dest} 获取克制武器")
                return f"move {dest}"
            self._state = "INTERACTING"

        if self._state == "INTERACTING":
            cmd = self._interact_cmd(player, loc)
            if cmd and "interact" in available:
                debug_ai_basic(self._debug_name,
                    f"RearmGoal: 交互获取克制武器 → {cmd}")
                return cmd
            # 不在目标地点或无法 interact → 回退到 MOVING
            self._state = "MOVING"
            return None

        return None

    # ── 辅助 ──

    @staticmethod
    def _location(player: Any) -> str:
        loc = getattr(player, 'location', None)
        return str(loc) if loc else ""

    def _pick_destination(self, player: Any, state: Any, loc: str) -> Optional[str]:
        """选择获取非普通属性武器的目的地。"""
        from utils.attribute import Attribute
        Q = self._query
        vouchers = getattr(player, 'vouchers', 0)
        has_magic = any(
            Q.get_weapon_attr(w) == Attribute.MAGIC
            for w in getattr(player, 'weapons', []) if w
        )
        has_tech = any(
            Q.get_weapon_attr(w) == Attribute.TECH
            for w in getattr(player, 'weapons', []) if w
        )
        if vouchers < 1:
            return "魔法所"
        candidates: List[str] = []
        if not has_magic:
            candidates.append("魔法所")
        if not has_tech:
            candidates.append("军事基地")
        if not candidates:
            return "魔法所"
        if len(candidates) == 1:
            return candidates[0]
        enemies_magic = Q.count_enemies_at("魔法所", player, state)
        enemies_military = Q.count_enemies_at("军事基地", player, state)
        return "军事基地" if enemies_military <= enemies_magic else "魔法所"

    def _interact_cmd(self, player: Any, loc: str) -> Optional[str]:
        """当前地点可交互获取的非普通属性武器。"""
        learned = getattr(player, 'learned_spells', set())
        has_pass = getattr(player, 'has_military_pass', False)
        if loc == "魔法所":
            if "魔法弹幕" not in learned:
                return "interact 魔法弹幕"
            if "远程魔法弹幕" not in learned:
                return "interact 远程魔法弹幕"
            if "地震" not in learned:
                return "interact 地震"
            if "地动山摇" not in learned:
                return "interact 地动山摇"
            return None
        elif loc == "军事基地" and has_pass:
            has_gauss = any(w.name == "高斯步枪" for w in getattr(player, 'weapons', []) if w)
            has_emr = any(w.name == "电磁步枪" for w in getattr(player, 'weapons', []) if w)
            if not has_gauss:
                return "interact 高斯步枪"
            if not has_emr:
                return "interact 电磁步枪"
            return None
        return None
