"""
DevelopGoal —— 发育目标（去X地点拿Y物品）

这是解决"AI忘记自己在做什么"问题的核心目标类型。
当AI决定"去军事基地拿电磁步枪"时，这个目标会持久化，直到：
- 物品到手（is_achieved）
- 物品无法再获取（is_expired）
- 被更高优先级目标打断（interrupt）

状态机：MOVING → INTERACTING → ACHIEVED
"""

from __future__ import annotations
from typing import List, Optional, Any

from controllers.ai.goals.base_goal import BaseGoal
from controllers.ai.constants import debug_ai_basic


class DevelopGoal(BaseGoal):
    """前往指定地点获取物品的持久化目标。

    使用方式：
        goal = DevelopGoal(
            target_item="电磁步枪",
            target_location="军事基地",
            priority=6,
            debug_name="AI_张三",
        )
        goal_stack.push(goal)
        # 每回合：
        cmd = goal.get_next_command(player, state, available)
    """

    def __init__(
        self,
        target_item: str,
        target_location: str,
        priority: int = 4,
        debug_name: str = "AI",
    ):
        super().__init__()
        self.target_item = target_item
        self.target_location = target_location
        self.priority = priority
        self.description = f"获取 {target_item}（去{target_location}）"
        self._debug_name = debug_name
        self._state: str = "MOVING"  # MOVING → INTERACTING → ACHIEVED

    def is_expired(self, player: Any, state: Any) -> bool:
        """目标过期条件：玩家死亡或物品不再存在于目标地点（极少数情况）"""
        if not player.is_alive():
            return True
        # 对于大部分物品，只要玩家活着就不会过期
        return False

    def is_achieved(self, player: Any, state: Any) -> bool:
        """目标完成条件：玩家已拥有目标物品"""
        return self._already_has_item(player)

    def _get_next_command_internal(
        self, player: Any, state: Any, available: List[str]
    ) -> Optional[str]:
        my_loc = self._get_location_str(player)

        # 状态：MOVING → 移动到目标地点
        if self._state == "MOVING":
            if self._at_target_location(player):
                self._state = "INTERACTING"
                debug_ai_basic(self._debug_name,
                    f"目标「{self.description}」: 已到达{self.target_location}，开始交互")
            elif "move" in available and not self._at_target_location(player):
                debug_ai_basic(self._debug_name,
                    f"目标「{self.description}」: 移动到{self.target_location}")
                return f"move {self.target_location}"

        # 状态：INTERACTING → 交互获取物品
        if self._state == "INTERACTING":
            if self._at_target_location(player) and "interact" in available:
                # 某些物品需要前置条件（如凭证、通行证）
                prereq_cmd = self._check_prerequisite(player)
                if prereq_cmd:
                    return prereq_cmd
                debug_ai_basic(self._debug_name,
                    f"目标「{self.description}」: 交互获取 {self.target_item}")
                return f"interact {self.target_item}"
            elif not self._at_target_location(player) and "move" in available:
                self._state = "MOVING"  # 被传送走了，重新移动
                return f"move {self.target_location}"

        return None  # 本回合无法推进

    def _at_target_location(self, player: Any) -> bool:
        """检查玩家是否在目标地点"""
        loc = self._get_location_str(player)
        if self.target_location == "home":
            return loc == "home" or (hasattr(player, 'location') and str(getattr(player, 'location', '')).startswith("home"))
        return loc == self.target_location

    def _check_prerequisite(self, player: Any) -> Optional[str]:
        """检查是否需要先获取前置条件"""
        # 军事基地物品需要通行证
        military_items = {"电磁步枪", "高斯步枪", "AT力场", "雷达", "隐形涂层"}
        if self.target_location == "军事基地" and self.target_item in military_items:
            if not getattr(player, 'has_military_pass', False) and self.target_item != "通行证":
                return "interact 通行证"
        # m4 信用点经济：按价格表检查 credits（防 AI 凭证检查死循环的最小适配，
        # 完整经济决策归 M8）
        from engine.economy import m4_enabled, price
        if m4_enabled():
            from engine.balance import get as _bget
            if self.target_location in ("商店", "医院") and self.target_item != "打工":
                cost = price(self.target_item)
                if self.target_item in {"晶化皮肤手术", "额外心脏手术", "不老泉手术"}:
                    cost = _bget("economy", "surgery_min_cost", default=4)
                if cost > 0 and getattr(player, 'credits', 0) < cost:
                    return "interact 打工"
            return None
        # 商店物品需要凭证（小刀除外）
        shop_paid_items = {"陶瓷护甲", "磨刀石", "热成像仪", "隐身衣", "防毒面具"}
        if self.target_location == "商店" and self.target_item in shop_paid_items:
            if getattr(player, 'vouchers', 0) < 1:
                return "interact 打工"
        # 医院手术需要凭证
        hospital_items = {"晶化皮肤手术", "额外心脏手术", "不老泉手术", "防毒面具"}
        if self.target_location == "医院" and self.target_item in hospital_items:
            if getattr(player, 'vouchers', 0) < 1:
                return "interact 打工"
        return None

    def _already_has_item(self, player: Any) -> bool:
        """检查玩家是否已拥有目标物品"""
        item = self.target_item
        # 武器
        if item in ("小刀", "高斯步枪", "电磁步枪"):
            return any(getattr(w, 'name', '') == item for w in getattr(player, 'weapons', []))
        # 护甲
        if item in ("盾牌", "陶瓷护甲", "AT力场", "魔法护盾"):
            return self._has_armor_by_name(player, item)
        # 内甲手术
        surgery_map = {"晶化皮肤手术": "晶化皮肤", "额外心脏手术": "额外心脏", "不老泉手术": "不老泉"}
        if item in surgery_map:
            return self._has_armor_by_name(player, surgery_map[item])
        # 法术
        learned = getattr(player, 'learned_spells', set())
        if item in ("魔法弹幕", "远程魔法弹幕", "封闭", "地震", "地动山摇", "隐身术", "探测魔法"):
            return item in learned
        # 物品
        if item == "通行证":
            return getattr(player, 'has_military_pass', False)
        if item == "凭证":
            return getattr(player, 'vouchers', 0) >= 1
        if item == "打工":
            return getattr(player, 'vouchers', 0) >= 1
        if item in ("热成像仪", "雷达"):
            return getattr(player, 'has_detection', False)
        if item in ("隐身衣", "隐形涂层", "隐身术"):
            return self._has_stealth(player)
        if item == "防毒面具":
            return self._has_virus_immunity(player)
        return False

    @staticmethod
    def _get_location_str(player: Any) -> str:
        loc = getattr(player, 'location', None)
        return str(loc) if loc else ""

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
    def _has_stealth(player: Any) -> bool:
        if getattr(player, 'is_invisible', False):
            return True
        for item in getattr(player, 'items', []):
            if getattr(item, 'name', '') in ("隐身衣", "隐形涂层"):
                return True
        return "隐身术" in getattr(player, 'learned_spells', set())

    @staticmethod
    def _has_virus_immunity(player: Any) -> bool:
        for item in getattr(player, 'items', []):
            if getattr(item, 'name', '') == "防毒面具":
                return True
        talent = getattr(player, 'talent', None)
        if talent and getattr(talent, 'is_terror', False):
            return True
        learned = getattr(player, 'learned_spells', set())
        if "封闭" in learned:
            return True
        return getattr(player, 'has_seal', False)
