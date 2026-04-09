"""HoshinoMixin —— 神代天赋7 AI 专属逻辑"""
from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional, Any
from controllers.ai.constants import debug_ai_basic

if TYPE_CHECKING:
    from controllers.ai.controller import BasicAIController

_Base = BasicAIController if TYPE_CHECKING else object


class HoshinoMixin(_Base):

    # ════════════════════════════════════════════════════════
    #  辅助判定
    # ════════════════════════════════════════════════════════

    def _has_hoshino_talent(self, player) -> bool:
        """判断玩家是否持有神代天赋7"""
        talent = getattr(player, 'talent', None)
        if not talent:
            return False
        return getattr(talent, 'name', '') == "大叔我啊，剪短发了"

    def _hoshino_is_terror(self, player) -> bool:
        """判断星野是否处于 Terror 状态"""
        talent = getattr(player, 'talent', None)
        return bool(talent and getattr(talent, 'is_terror', False))

    def _hoshino_tactical_unlocked(self, player) -> bool:
        """判断战术指令是否已解锁"""
        talent = getattr(player, 'talent', None)
        return bool(talent and getattr(talent, 'tactical_unlocked', False))

    def _hoshino_get_shield_mode(self, player) -> Optional[str]:
        """获取当前盾牌模式：'架盾'/'持盾'/None"""
        talent = getattr(player, 'talent', None)
        return getattr(talent, 'shield_mode', None) if talent else None

    def _hoshino_has_ammo(self, player) -> bool:
        """是否有子弹"""
        talent = getattr(player, 'talent', None)
        return bool(talent and getattr(talent, 'ammo', []))

    def _hoshino_get_cost(self, player) -> int:
        """获取当前 cost"""
        talent = getattr(player, 'talent', None)
        return getattr(talent, 'cost', 0) if talent else 0

    def _hoshino_get_form(self, player) -> str:
        """获取当前形态"""
        talent = getattr(player, 'talent', None)
        return getattr(talent, 'form', '水着-shielder') if talent else '水着-shielder'

    def _hoshino_has_fusion_shield(self, player) -> bool:
        talent = getattr(player, 'talent', None)
        return bool(talent and getattr(talent, 'fusion_shield_done', False))

    def _hoshino_has_fusion_weapon(self, player) -> bool:
        talent = getattr(player, 'talent', None)
        return bool(talent and getattr(talent, 'fusion_weapon_done', False))

    def _hoshino_iron_horus_hp(self, player) -> float:
        talent = getattr(player, 'talent', None)
        return getattr(talent, 'iron_horus_hp', 0) if talent else 0

    def _hoshino_find_consumable_for_reload(self, player) -> Optional[str]:
        """找到可消耗的物品用于装填子弹（小刀等非融合武器/物品）"""
        for w in getattr(player, 'weapons', []):
            if w and w.name not in ("拳击", "荷鲁斯之眼"):
                return w.name
        for item in getattr(player, 'items', []):
            if item:
                return getattr(item, 'name', None)
        return None

    def _hoshino_find_target(self, player, state) -> Optional[Any]:
        """找到最佳攻击目标（复用 _pick_target 或按威胁分排序）"""
        target = self._pick_target(player, state)
        return target

    def _hoshino_target_same_location(self, player, target) -> bool:
        """目标是否在同一地点"""
        return self._same_location(player, target)

    def _hoshino_is_engaged_with(self, player, target, state) -> bool:
        """是否已与目标面对面"""
        markers = getattr(state, 'markers', None)
        if not markers:
            return False
        return markers.has_relation(player.player_id, "ENGAGED_WITH", target.player_id)

    def _hoshino_is_in_front(self, player, target) -> bool:
        """目标是否在星野的正面（架盾时）"""
        talent = getattr(player, 'talent', None)
        if not talent or not hasattr(talent, 'is_front'):
            return False
        return talent.is_front(target.player_id)
    # ════════════════════════════════════════════════════════
    #  战术宏模板生成
    # ════════════════════════════════════════════════════════

    def _hoshino_build_macro(self, player, state, target) -> List[str]:
        """
        根据当前状态生成战术指令宏队列。
        两阶段生成：
          阶段1：根据状态生成"接近+控制"前缀
          阶段2：填充射击直到 cost 耗尽
        """
        talent = getattr(player, 'talent', None)
        if not talent:
            return ["terminal"]

        queue = []
        cost = talent.cost
        shield_mode = talent.shield_mode  # "架盾"/"持盾"/None
        form = talent.form
        same_loc = self._hoshino_target_same_location(player, target)
        has_find = self._hoshino_is_engaged_with(player, target, state)
        has_ammo = bool(talent.ammo)

        COST = {
            "架盾": 2, "射击": 2, "重新装填": 0, "持盾": 1,
            "投掷": 1, "服药": 0, "冲刺": 1, "取消": 0,
            "find": 1, "lock": 1, "转向": 0, "排弹": 0,
        }

        used_cost = 0

        def can_afford(action):
            return used_cost + COST.get(action, 0) <= cost

        # ===== 阶段1：接近 + 控制前缀 =====

        if shield_mode == "架盾":
            if has_find and self._hoshino_is_in_front(player, target):
                # 架盾 + 目标在正面 + 已 find → 直接射击
                pass
            elif has_find and not self._hoshino_is_in_front(player, target):
                # 架盾 + 目标在背面 → 转向
                queue.append("转向")
                # 转向 cost=0
            elif same_loc and not has_find:
                # 架盾 + 同地点但没 find → find
                if can_afford("find"):
                    queue.append(f"find {target.name}")
                    used_cost += COST["find"]
            elif not same_loc:
                # 架盾 + 不同地点 → 取消架盾 → 持盾 → 冲刺 → find
                queue.append("取消")  # cost 0
                if can_afford("持盾"):
                    queue.append("持盾")
                    used_cost += COST["持盾"]
                if can_afford("冲刺"):
                    queue.append(f"冲刺 {target.name}")
                    used_cost += COST["冲刺"]
                if can_afford("find"):
                    queue.append(f"find {target.name}")
                    used_cost += COST["find"]

        elif shield_mode == "持盾":
            if same_loc and has_find:
                # 持盾 + 同地点 + 已 find → 直接射击
                pass
            elif same_loc and not has_find:
                # 持盾 + 同地点 + 没 find → find
                if can_afford("find"):
                    queue.append(f"find {target.name}")
                    used_cost += COST["find"]
            elif not same_loc:
                # 持盾 + 不同地点 → 冲刺 → find
                if can_afford("冲刺"):
                    queue.append(f"冲刺 {target.name}")
                    used_cost += COST["冲刺"]
                if can_afford("find"):
                    queue.append(f"find {target.name}")
                    used_cost += COST["find"]

        else:  # shield_mode is None
            if same_loc and has_find:
                # 无盾 + 同地点 + 已 find → 持盾然后射击
                if can_afford("持盾"):
                    queue.append("持盾")
                    used_cost += COST["持盾"]
            elif same_loc and not has_find:
                # 无盾 + 同地点 + 没 find → 持盾 → find
                if can_afford("持盾"):
                    queue.append("持盾")
                    used_cost += COST["持盾"]
                if can_afford("find"):
                    queue.append(f"find {target.name}")
                    used_cost += COST["find"]
            elif not same_loc:
                # 无盾 + 不同地点 → 持盾 → 冲刺 → find
                if can_afford("持盾"):
                    queue.append("持盾")
                    used_cost += COST["持盾"]
                if can_afford("冲刺"):
                    queue.append(f"冲刺 {target.name}")
                    used_cost += COST["冲刺"]
                # 临战-shielder 冲刺后自动冲击+架盾，不需要额外 find
                if form == "临战-shielder":
                    pass  # 冲刺自动处理
                elif can_afford("find"):
                    queue.append(f"find {target.name}")
                    used_cost += COST["find"]

        # ===== 装填检查 =====
        if not has_ammo:
            consumable = self._hoshino_find_consumable_for_reload(player)
            if consumable:
                queue.append(f"重新装填 {consumable}")  # cost 0
            else:
                # 没有子弹也没有可消耗物品 → 无法射击，结束宏
                queue.append("terminal")
                return queue

        # ===== 阶段2：射击填充 =====
        remaining_cost = cost - used_cost
        while remaining_cost >= COST["射击"]:
            queue.append(f"射击 {target.name}")
            remaining_cost -= COST["射击"]

        queue.append("terminal")
        return queue
    # ════════════════════════════════════════════════════════
    #  战术宏指令输入（逐条弹出）
    # ════════════════════════════════════════════════════════

    def _hoshino_get_tactical_command(self, player, state, available_actions) -> str:
        """从预生成的战术宏队列中逐条弹出指令"""
        if not self._hoshino_macro_queue:
            # 队列为空，需要生成新的
            target = self._hoshino_find_target(player, state)
            if target:
                self._hoshino_macro_queue = self._hoshino_build_macro(player, state, target)
            else:
                self._hoshino_macro_queue = ["terminal"]

        if self._hoshino_macro_queue:
            cmd = self._hoshino_macro_queue.pop(0)
            return cmd
        return "terminal"

    def _hoshino_terror_command(self, player, state, available_actions) -> List[str]:
        """Terror 状态：直接 attack"""
        if "attack" in available_actions:
            return ["attack"]
        return ["forfeit"]