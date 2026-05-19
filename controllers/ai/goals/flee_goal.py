"""
FleeGoal —— 危险模式持久化目标（匹配旧 _cmd_danger_develop 行为）

旧行为：先尝试在当前地点拿护甲 → 拿不到再移动到安全地点
新行为：彻底匹配旧行为，不再"先走后拿"

状态机：CHECK_LOCAL → MOVE_AWAY → HEAL → DONE
优先就地解决，拿不到甲才移动。
"""

from __future__ import annotations
from typing import List, Optional, Any

from controllers.ai.goals.base_goal import BaseGoal
from controllers.ai.constants import debug_ai_basic


class FleeGoal(BaseGoal):
    """危险模式持久化目标。匹配旧 _cmd_danger_develop 的完整逻辑。"""

    def __init__(
        self,
        destination: str,
        priority: int = 10,
        debug_name: str = "AI",
    ):
        super().__init__()
        self.destination = destination
        self.priority = priority
        self.description = f"逃往 {destination}（危险模式）"
        self._debug_name = debug_name
        self._state: str = "CHECK_LOCAL"  # CHECK_LOCAL → MOVE_AWAY → HEAL → DONE
        self._danger_resolved = False

    def is_expired(self, player: Any, state: Any) -> bool:
        return not player.is_alive()

    def is_achieved(self, player: Any, state: Any) -> bool:
        """危险解除或获取足够护甲时完成"""
        if self._danger_resolved:
            return True
        # 获取足够护甲也视为完成（匹配旧 _is_danger_resolved: total_armor >= 2）
        outer = self._count_outer_armor(player)
        inner = self._count_inner_armor(player)
        return outer + inner >= 2

    def _get_next_command_internal(
        self, player: Any, state: Any, available: List[str]
    ) -> Optional[str]:
        # 护甲已够 → 不再需要逃跑
        outer = self._count_outer_armor(player)
        inner = self._count_inner_armor(player)
        if outer + inner >= 2:
            return None  # 让 pop_expired 在下轮清理

        my_loc = self._get_location_str(player)
        vouchers = getattr(player, 'vouchers', 0)

        # ── 阶段1：CHECK_LOCAL — 当前地点能拿护甲就直接拿（匹配旧逻辑）──
        if self._state == "CHECK_LOCAL" and "interact" in available:
            cmd = self._get_local_armor_cmd(player, state, my_loc, outer, inner, vouchers)
            if cmd:
                return cmd
            # 当前地点拿不到任何护甲 → 需要移动
            self._state = "MOVE_AWAY"

        # ── 阶段2：MOVE_AWAY — 移动到安全地点 ──
        if self._state == "MOVE_AWAY":
            if my_loc == self.destination or self._at_home(player, self.destination):
                self._state = "HEAL"
                debug_ai_basic(self._debug_name,
                    f"FleeGoal: 到达 {self.destination}，开始补给")
            elif "move" in available:
                return f"move {self.destination}"

        # ── 阶段3：HEAL — 在目标地点获取护甲 ──
        if self._state == "HEAL" and "interact" in available:
            cmd = self._get_local_armor_cmd(player, state, my_loc, outer, inner, vouchers)
            if cmd:
                return cmd

        return None

    def _get_local_armor_cmd(
        self, player: Any, state: Any, loc: str, outer: int, inner: int, vouchers: int
    ) -> Optional[str]:
        """在当前地点获取护甲（直接复制旧 _cmd_danger_develop 的逻辑）"""
        if loc == "home" or self._at_home(player, "home"):
            if outer == 0 and not self._has_armor_by_name(player, "盾牌"):
                return "interact 盾牌"
        elif loc == "商店":
            if vouchers >= 1 and outer < 2 and not self._has_armor_by_name(player, "陶瓷护甲"):
                return "interact 陶瓷护甲"
            if vouchers < 1:
                return "interact 打工"
            # 病毒期间优先拿面具
            virus = getattr(state, 'virus', None)
            if virus and getattr(virus, 'is_active', False):
                if not self._has_virus_immunity(player):
                    return "interact 防毒面具"
        elif loc == "魔法所":
            learned = getattr(player, 'learned_spells', set())
            if "魔法护盾" not in learned and outer < 2:
                return "interact 魔法护盾"
        elif loc == "医院":
            if inner == 0:
                return "interact 晶化皮肤手术"
            virus = getattr(state, 'virus', None)
            if virus and getattr(virus, 'is_active', False) and not self._has_virus_immunity(player):
                if vouchers >= 1:
                    return "interact 防毒面具"
                return "interact 打工"
            if vouchers < 1:
                return "interact 打工"
        elif loc == "军事基地":
            has_pass = getattr(player, 'has_military_pass', False)
            if has_pass and outer < 2 and not self._has_armor_by_name(player, "AT力场"):
                return "interact AT力场"
        return None

    @staticmethod
    def _get_location_str(player: Any) -> str:
        loc = getattr(player, 'location', None)
        return str(loc) if loc else ""

    @staticmethod
    def _at_home(player: Any, dest: str) -> bool:
        if dest != "home":
            return False
        loc = str(getattr(player, 'location', ''))
        return loc == "home" or loc.startswith("home")

    @staticmethod
    def _count_outer_armor(player: Any) -> int:
        armor = getattr(player, 'armor', None)
        if not armor or not hasattr(armor, 'get_active'):
            return 0
        from models.equipment import ArmorLayer
        return len(armor.get_active(ArmorLayer.OUTER))

    @staticmethod
    def _count_inner_armor(player: Any) -> int:
        armor = getattr(player, 'armor', None)
        if not armor or not hasattr(armor, 'get_active'):
            return 0
        from models.equipment import ArmorLayer
        return len(armor.get_active(ArmorLayer.INNER))

    @staticmethod
    def _has_armor_by_name(player: Any, name: str) -> bool:
        armor = getattr(player, 'armor', None)
        if not armor or not hasattr(armor, 'get_all_active'):
            return False
        for piece in armor.get_all_active():
            if getattr(piece, 'name', '') == name and not getattr(piece, 'is_broken', False):
                return True
        return False

    @staticmethod
    def _has_virus_immunity(player: Any) -> bool:
        for item in getattr(player, 'items', []):
            if getattr(item, 'name', '') == "防毒面具":
                return True
        if "封闭" in getattr(player, 'learned_spells', set()):
            return True
        if getattr(player, 'has_seal', False):
            return True
        return False
