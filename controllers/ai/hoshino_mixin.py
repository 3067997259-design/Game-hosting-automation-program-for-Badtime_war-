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

    def _hoshino_pick_reload_item_at_location(self, player, state, loc) -> Optional[str]:
        """根据当前地点选择最佳的可消耗物品用于装填子弹。
        优先选择能产出与当前弹药属性不同的物品（属性多样化）。"""
        talent = getattr(player, 'talent', None)
        has_pass = getattr(player, 'has_military_pass', False)
        vouchers = getattr(player, 'vouchers', 0)
        iron_horus_hp = getattr(talent, 'iron_horus_hp', 0) if talent else 0
        iron_horus_max = getattr(talent, 'iron_horus_max_hp', 3) if talent else 3

        # 已有的子弹属性统计（用于属性多样化）
        ammo = getattr(talent, 'ammo', []) if talent else []
        existing_attrs = set(b.get("attribute", "普通") for b in ammo)

        # 各地点可拿的消耗品及其属性
        # 注意：盾牌和AT力场是修复材料，只有荷鲁斯满血时才用于装填
        horus_full = iron_horus_hp >= iron_horus_max

        candidates = []  # (item_name, attribute, priority)

        if loc == "home" or self._is_at_home(player):
            if not player.has_weapon("小刀"):
                candidates.append(("小刀", "普通", 1))
            if horus_full and not self._has_armor_by_name(player, "盾牌"):
                candidates.append(("盾牌", "普通", 0))  # 低优先级，修复材料

        elif loc == "商店":
            if vouchers >= 1:
                if not player.has_weapon("小刀"):
                    candidates.append(("小刀", "普通", 1))
                # 陶瓷护甲也是普通属性，但比小刀贵（需凭证）
                if not self._has_armor_by_name(player, "陶瓷护甲"):
                    candidates.append(("陶瓷护甲", "普通", 0))

        elif loc == "魔法所":
            learned = getattr(player, 'learned_spells', set())
            # 魔法护盾：学习后变成魔法属性外甲，可消耗装填魔法子弹
            if "魔法护盾" not in learned:
                candidates.append(("魔法护盾", "魔法", 2))
            # 魔法弹幕：魔法属性武器
            if "魔法弹幕" not in learned:
                candidates.append(("魔法弹幕", "魔法", 2))

        elif loc == "医院":
            if vouchers >= 1:
                # 手术可以拿到不同属性的内甲
                candidates.append(("晶化皮肤手术", "科技", 2))
                candidates.append(("不老泉手术", "魔法", 2))
                candidates.append(("额外心脏手术", "普通", 1))

        elif loc == "军事基地":
            if has_pass:
                # 雷达是最佳选择：科技属性物品，不影响战斗力
                if not getattr(player, 'has_detection', False):
                    candidates.append(("雷达", "科技", 3))
                # AT力场：科技属性，但也是修复材料
                if horus_full and not self._has_armor_by_name(player, "AT力场"):
                    candidates.append(("AT力场", "科技", 1))
                # 高斯步枪/电磁步枪：融合后可以重新拿（如果没有的话）
                if not player.has_weapon("高斯步枪"):
                    candidates.append(("高斯步枪", "科技", 2))
                if not player.has_weapon("电磁步枪"):
                    candidates.append(("电磁步枪", "科技", 2))

        if not candidates:
            return None

        # 优先选择当前弹药中没有的属性（属性多样化）
        for name, attr, prio in sorted(candidates, key=lambda x: -x[2]):
            if attr not in existing_attrs:
                return name
        # 都有了就选优先级最高的
        candidates.sort(key=lambda x: -x[2])
        return candidates[0][0]


    def _hoshino_best_reload_destination(self, player, state) -> Optional[str]:
        """选择最佳的移动目的地来获取装填消耗品。
        考虑：当前弹药属性缺什么、各地点能提供什么。"""
        talent = getattr(player, 'talent', None)
        has_pass = getattr(player, 'has_military_pass', False)
        vouchers = getattr(player, 'vouchers', 0)
        ammo = getattr(talent, 'ammo', []) if talent else []
        existing_attrs = set(b.get("attribute", "普通") for b in ammo)

        # 各地点能提供的属性
        location_attrs = {
            "home": {"普通"},           # 小刀
            "商店": {"普通"} if vouchers >= 1 else set(),
            "魔法所": {"魔法"},         # 魔法护盾/魔法弹幕
            "军事基地": {"科技"} if has_pass else set(),
            "医院": {"科技", "魔法", "普通"} if vouchers >= 1 else set(),
        }

        # 优先去能提供缺失属性的地点
        for loc, attrs in location_attrs.items():
            missing = attrs - existing_attrs
            if missing:
                return loc

        # 都不缺就去最方便的（家最简单，免费无条件）
        return "home"
    def _hoshino_prefer_deploy_shield(self, player) -> bool:
        """铁之荷鲁斯HP低于上限一半时偏好架盾"""
        talent = getattr(player, 'talent', None)
        if not talent:
            return False
        hp = getattr(talent, 'iron_horus_hp', 0)
        max_hp = getattr(talent, 'iron_horus_max_hp', 3)
        return 0 < hp < max_hp / 2
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
        # 安全网：无弹药且无消耗品时不应进入宏（正常情况下 controller.py 已拦截）
        if not talent.ammo and not self._hoshino_find_consumable_for_reload(player):
            return ["terminal"]
        # 铁之荷鲁斯破损时不应进入宏
        if talent.iron_horus_hp <= 0:
            return ["terminal"]

        queue = []
        cost = talent.cost
        shield_mode = talent.shield_mode  # "架盾"/"持盾"/None
        form = talent.form
        same_loc = self._hoshino_target_same_location(player, target)
        target_loc = self._get_location_str(target)
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

        # 在 COST 字典定义之后、阶段1之前添加
        prefer_deploy = (talent.iron_horus_hp < talent.iron_horus_max_hp / 2
                        and talent.iron_horus_hp > 0)  # HP低于上限一半且未破损 → 偏好架盾

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
                    queue.append(f"冲刺 {target_loc}")
                    used_cost += COST["冲刺"]
                if can_afford("find"):
                    queue.append(f"find {target.name}")
                    used_cost += COST["find"]

        elif shield_mode == "持盾":
            if same_loc and prefer_deploy:
                # 持盾中 + 同地点 + HP低 → 取消持盾 → 架盾
                queue.append("取消")  # cost 0
                if can_afford("架盾"):
                    queue.append("架盾")
                    used_cost += COST["架盾"]
            elif same_loc and has_find:
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
                    queue.append(f"冲刺 {self._get_location_str(target)}")
                    used_cost += COST["冲刺"]
                if can_afford("find"):
                    queue.append(f"find {target.name}")
                    used_cost += COST["find"]

        else:  # shield_mode is None
            if same_loc and prefer_deploy:
                # 同地点 + HP低 → 架盾（不需要 find，直接射击正面）
                if can_afford("架盾"):
                    queue.append("架盾")
                    used_cost += COST["架盾"]
            elif same_loc and has_find:
                # 同地点 + 已 find + HP健康 → 持盾然后射击
                if can_afford("持盾"):
                    queue.append("持盾")
                    used_cost += COST["持盾"]
            elif same_loc and not has_find:
                # 同地点 + 没 find + HP健康 → 持盾 → find
                if can_afford("持盾"):
                    queue.append("持盾")
                    used_cost += COST["持盾"]
                if can_afford("find"):
                    queue.append(f"find {target.name}")
                    used_cost += COST["find"]
            elif not same_loc:
                # 不同地点 → 必须持盾冲刺（架盾不能移动）
                if can_afford("持盾"):
                    queue.append("持盾")
                    used_cost += COST["持盾"]
                if can_afford("冲刺"):
                    queue.append(f"冲刺 {self._get_location_str(target)}")
                    used_cost += COST["冲刺"]
                if can_afford("find"):
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