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
        """找到可消耗的物品用于装填子弹（小刀等非融合武器/物品）。
        当铁之荷鲁斯受损时，盾牌和AT力场保留用于修复，不算作可消耗品。"""
        talent = getattr(player, 'talent', None)
        iron_horus_hp = getattr(talent, 'iron_horus_hp', 0) if talent else 0
        iron_horus_max = getattr(talent, 'iron_horus_max_hp', 2) if talent else 2
        horus_damaged = iron_horus_hp < iron_horus_max

        # 受损时需要保留的修复材料
        repair_names = {"盾牌", "AT力场"} if horus_damaged else set()

        for w in getattr(player, 'weapons', []):
            if w and w.name not in ("拳击", "荷鲁斯之眼"):
                return w.name
        for item in getattr(player, 'items', []):
            if item and getattr(item, 'name', None) not in repair_names:
                return getattr(item, 'name', None)
        # 检查护甲（盾牌/AT力场等）—— 受损时跳过修复材料
        for a in getattr(getattr(player, 'armor', None), 'get_all_active', lambda: [])():
            if a and a.name not in ("拳击", "荷鲁斯之眼") and a.name not in repair_names:
                return a.name
        return None

    def _hoshino_captain_has_police_protection(self, state) -> bool:
        """队长所在地点是否有 active 状态的警察单位"""
        pc = self._police_cache or {}
        captain_id = pc.get("captain_id")
        if not captain_id:
            return False
        captain = state.get_player(captain_id)
        if not captain or not captain.is_alive():
            return False
        captain_loc = self._get_location_str(captain)
        for unit in pc.get("units", []):
            if (unit.get("is_active") and unit.get("is_alive")
                    and unit.get("location") == captain_loc):
                return True
        return False

    def _hoshino_has_enough_tactical_items(self, player) -> bool:
        """是否有至少2个战术道具"""
        talent = getattr(player, 'talent', None)
        if not talent:
            return False
        return len(getattr(talent, 'tactical_items', [])) >= 2

    def _hoshino_count_throwables(self, player) -> int:
        """统计玩家持有的投掷类战术道具数量"""
        talent = getattr(player, 'talent', None)
        if not talent:
            return 0
        items = getattr(talent, 'tactical_items', [])
        throwable_names = {"闪光弹", "烟雾弹", "震撼弹", "破片手雷", "燃烧瓶"}
        return sum(1 for item in items if item in throwable_names)

    def _hoshino_find_safe_repair_location(self, player, state) -> Optional[str]:
        """找到没有警察的修复材料地点。
        优先军事基地（有通行证时），其次家。
        都有警察时返回家（因为队长AI写死了去军事基地发育的逻辑）。
        """
        pc = self._police_cache or {}
        has_pass = getattr(player, 'has_military_pass', False)

        def has_police_at(location):
            for unit in pc.get("units", []):
                if unit.get("is_alive") and unit.get("location") == location:
                    return True
            return False

        if has_pass and not has_police_at("军事基地"):
            return "军事基地"

        # 获取玩家的家的实际位置名
        home_loc = f"home_{player.player_id}" if hasattr(player, 'player_id') else "home"
        if not has_police_at(home_loc):
            return "home"

        # 都有警察 → 返回家（队长AI倾向去军事基地，家相对安全）
        return "home"

    def _hoshino_find_target(self, player, state) -> Optional[Any]:
        """找到最佳攻击目标（复用 _pick_target 或按威胁分排序）"""
        # 被警察追击时优先攻击队长
        if self._is_pursued_by_police(player, state):
            pc = self._police_cache or {}
            captain_id = pc.get("captain_id")
            if captain_id:
                captain = state.get_player(captain_id)
                if captain and captain.is_alive():
                    return captain
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

    def _hoshino_pick_best_item(self, player, state, loc) -> Optional[dict]:
        """按三层优先级选择当前地点最佳物品。
        返回 {"name": str, "priority": int} 或 None。

        层级1（priority 30）：护盾类
        层级2（priority 20）：探测手段（如果还没有探测能力）
        层级3（priority 10-15）：其他消耗品
        同优先级内，能提供当前弹药中缺失属性的物品 +5。
        """
        talent = getattr(player, 'talent', None)
        has_pass = getattr(player, 'has_military_pass', False)
        vouchers = getattr(player, 'vouchers', 0)
        has_detection = getattr(player, 'has_detection', False)
        tactical_unlocked = getattr(talent, 'tactical_unlocked', False) if talent else False

        ammo = getattr(talent, 'ammo', []) if talent else []
        existing_attrs = set(b.get("attribute", "普通") for b in ammo)

        candidates = []  # (item_name, attribute, base_priority)

        is_home = (loc == "home" or loc.startswith("home_") or "家" in loc)

        if is_home:
            # 层级1：盾牌（护盾）
            if not self._has_armor_by_name(player, "盾牌"):
                candidates.append(("盾牌", "普通", 30))
            # 层级3：小刀
            if not any(w.name == "小刀" for w in getattr(player, 'weapons', []) if w):
                candidates.append(("小刀", "普通", 10))

        elif loc == "商店":
            # 层级1：陶瓷护甲
            if vouchers >= 1 and not self._has_armor_by_name(player, "陶瓷护甲"):
                candidates.append(("陶瓷护甲", "普通", 30))
            # 层级2：热成像仪
            if vouchers >= 1 and not has_detection:
                candidates.append(("热成像仪", "科技", 20))

        elif loc == "魔法所":
            learned = getattr(player, 'learned_spells', set())
            # 层级1：魔法护盾
            if "魔法护盾" not in learned:
                candidates.append(("魔法护盾", "魔法", 30))
            # 层级2：探测魔法
            if not has_detection and "探测魔法" not in learned:
                candidates.append(("探测魔法", "魔法", 20))
            # 层级3：魔法弹幕
            if "魔法弹幕" not in learned:
                candidates.append(("魔法弹幕", "魔法", 10))

        elif loc == "军事基地":
            if has_pass:
                # 层级1：AT力场
                if not self._has_armor_by_name(player, "AT力场"):
                    candidates.append(("AT力场", "科技", 30))
                # 层级2：雷达
                if not has_detection:
                    candidates.append(("雷达", "科技", 20))
                # 层级3：枪
                if not any(w.name == "高斯步枪" for w in getattr(player, 'weapons', []) if w):
                    candidates.append(("高斯步枪", "科技", 10))
                if not any(w.name == "电磁步枪" for w in getattr(player, 'weapons', []) if w):
                    candidates.append(("电磁步枪", "科技", 10))

        elif loc == "医院":
            if tactical_unlocked:
                # 层级3：药物（按优先级区分）— 需检查持有上限和使用限制
                medicines = getattr(talent, 'medicines', []) if talent else []
                held_names = set(medicines)
                if len(medicines) < 2:
                    if "肾上腺素" not in held_names and not getattr(talent, 'adrenaline_used', False):
                        candidates.append(("肾上腺素", "科技", 15))
                    if "EPO" not in held_names:
                        candidates.append(("EPO", "科技", 12))
                    if "海豚巧克力" not in held_names:
                        candidates.append(("海豚巧克力", "普通", 10))
            if vouchers >= 1:
                # 层级3：手术
                candidates.append(("晶化皮肤手术", "科技", 8))
                candidates.append(("不老泉手术", "魔法", 8))
                candidates.append(("额外心脏手术", "普通", 8))

        if not candidates:
            return None

        # 属性多样化加分：弹药中缺失的属性 +5
        scored = []
        for name, attr, base_prio in candidates:
            bonus = 5 if attr not in existing_attrs else 0
            scored.append((name, attr, base_prio + bonus))

        scored.sort(key=lambda x: -x[2])
        best = scored[0]
        return {"name": best[0], "priority": best[2]}

    def _hoshino_best_item_destination(self, player, state) -> Optional[str]:
        """选择最佳移动目的地：对每个可达地点模拟 _hoshino_pick_best_item，选 priority 最高的。
        荷鲁斯受损时优先去能拿修复材料的地方。"""
        talent = getattr(player, 'talent', None)
        has_pass = getattr(player, 'has_military_pass', False)
        iron_horus_hp = getattr(talent, 'iron_horus_hp', 0) if talent else 0
        iron_horus_max = getattr(talent, 'iron_horus_max_hp', 2) if talent else 2
        loc = self._get_location_str(player)

        # 荷鲁斯受损时优先去能拿修复材料的地方
        if iron_horus_hp < iron_horus_max:
            # 军事基地拿AT力场（需要通行证）
            if has_pass and not self._has_armor_by_name(player, "AT力场"):
                if loc != "军事基地":
                    return "军事基地"
            # 家拿盾牌
            if not self._has_armor_by_name(player, "盾牌"):
                if not self._is_at_home(player):
                    return "home"

        # 正常情况：对每个可达地点模拟，选 priority 最高的
        all_locations = ["home", "商店", "魔法所", "军事基地", "医院"]
        best_loc = None
        best_prio = -1

        for dest in all_locations:
            # 跳过当前地点（用 _is_at_home 避免 home vs home_pN 不匹配）
            if dest == loc:
                continue
            if dest == "home" and self._is_at_home(player):
                continue

            result = self._hoshino_pick_best_item(player, state, dest)
            if result and result["priority"] > best_prio:
                best_prio = result["priority"]
                best_loc = dest

        return best_loc
    def _hoshino_prefer_deploy_shield(self, player) -> bool:
        """铁之荷鲁斯HP低于上限一半时偏好架盾"""
        talent = getattr(player, 'talent', None)
        if not talent:
            return False
        hp = getattr(talent, 'iron_horus_hp', 0)
        max_hp = getattr(talent, 'iron_horus_max_hp', 2)
        return 0 < hp <= max_hp / 2

    def _hoshino_has_missing_halo(self, player) -> bool:
        """是否有光环缺失"""
        talent = getattr(player, 'talent', None)
        if not talent:
            return False
        halos = getattr(talent, 'halos', [])
        return any(not h.get('active', True) for h in halos)

    def _hoshino_target_is_hard_to_kill(self, target) -> bool:
        """目标是否难杀（护甲多/临时HP/火萤/救世主）"""
        armor_count = self._count_outer_armor(target) + self._count_inner_armor(target)
        if armor_count >= 2:
            return True
        t_talent = getattr(target, 'talent', None)
        if t_talent:
            # 救世主状态或高火种
            if getattr(t_talent, 'is_savior', False):
                return True
            if hasattr(t_talent, 'divinity') and getattr(t_talent, 'divinity', 0) >= 6:
                return True
            # 火萤
            if hasattr(t_talent, 'has_supernova'):
                return True
            # 临时HP
            if hasattr(t_talent, 'temp_hp') and getattr(t_talent, 'temp_hp', 0) > 0:
                return True
        return False

    def _hoshino_target_is_police_protected(self, target) -> bool:
        """目标是否受警察单体保护"""
        pe = getattr(self._game_state, 'police_engine', None) if self._game_state else None
        if not pe:
            return False
        threshold = pe.get_protection_threshold(target.player_id)
        return threshold > 0

    def _hoshino_pick_throw_item(self, player, target) -> Optional[str]:
        """根据目标状态选择最佳投掷道具。返回道具名或 None。"""
        talent = getattr(player, 'talent', None)
        if not talent:
            return None
        items = getattr(talent, 'tactical_items', [])
        if not items:
            return None

        is_protected = self._hoshino_target_is_police_protected(target)
        is_hard = self._hoshino_target_is_hard_to_kill(target)

        # 目标受警察保护 → 闪光弹（禁用保护+攻击+命令）
        if is_protected:
            if "闪光弹" in items:
                return "闪光弹"
            if "烟雾弹" in items:
                return "烟雾弹"

        # 目标难杀 → 烟雾弹（控制+隐身+禁保护）
        if is_hard:
            if "烟雾弹" in items:
                return "烟雾弹"
            if "闪光弹" in items:
                return "闪光弹"

        # 其他 → 破片手雷（伤害+脆弱增加破甲率）
        if "破片手雷" in items:
            return "破片手雷"
        if "震撼弹" in items:
            return "震撼弹"

        # 兜底：有什么用什么
        return items[0] if items else None

    def _hoshino_should_use_epo(self, player, cost, used_cost) -> bool:
        """判断是否应该在宏内使用 EPO。
        条件：持有 EPO + 当前剩余 cost 为奇数（+1 后能多打一发 cost=2 的射击）"""
        talent = getattr(player, 'talent', None)
        if not talent:
            return False
        medicines = getattr(talent, 'medicines', [])
        if "EPO" not in medicines:
            return False
        remaining = cost - used_cost
        # 剩余 cost 为奇数时，+1 能多打一发射击（射击 cost=2）
        # 或者剩余 cost < 2 但 +1 后 >= 2（即剩余=1时）
        return remaining % 2 == 1 and remaining >= 1

    def _hoshino_should_use_chocolate(self, player) -> bool:
        """判断是否应该在宏内使用海豚巧克力"""
        talent = getattr(player, 'talent', None)
        if not talent:
            return False
        medicines = getattr(talent, 'medicines', [])
        if "海豚巧克力" not in medicines:
            return False
        return self._hoshino_has_missing_halo(player)

    def _hoshino_has_enough_ammo_for_burst(self, player) -> bool:
        """检查是否有足够弹药/消耗品支撑一轮爆发（至少能打2发）"""
        talent = getattr(player, 'talent', None)
        if not talent:
            return False
        ammo_count = len(getattr(talent, 'ammo', []))
        # 已有子弹 >= 4 → 足够
        if ammo_count >= 4:
            return True
        # 已有子弹 + 可装填的消耗品数量 >= 4
        consumable_count = 0
        for w in getattr(player, 'weapons', []):
            if w and w.name not in ("拳击", "荷鲁斯之眼"):
                consumable_count += 1
        for item in getattr(player, 'items', []):
            if item:
                consumable_count += 1
        # 每个消耗品装填4发
        return ammo_count + consumable_count * 4 >= 4

    def _hoshino_can_effectively_shoot(self, player, target) -> bool:
        """检查当前弹药属性是否能有效打击目标护甲"""
        talent = getattr(player, 'talent', None)
        if not talent:
            return False
        ammo = getattr(talent, 'ammo', [])
        ammo_attrs = set(b.get("attribute", "普通") for b in ammo)

        # 检查目标外层护甲属性
        armor_obj = getattr(target, 'armor', None)
        if not armor_obj or not hasattr(armor_obj, 'get_all_active'):
            return True  # 无护甲，任何子弹都有效

        outer_armors = [a for a in armor_obj.get_all_active() if not a.is_broken and getattr(a, 'layer', 'outer') == 'outer']
        if not outer_armors:
            return True  # 无外层护甲

        # 克制关系（护甲属性 → 克制它的武器属性）
        # 规则：普通→魔法有效，魔法→科技有效，科技→普通有效
        # 所以：魔法护甲需要普通武器，科技护甲需要魔法武器，普通护甲需要科技武器
        counter_map = {"普通": "科技", "魔法": "普通", "科技": "魔法"}

        for armor in outer_armors:
            armor_attr = getattr(armor, 'attribute', '普通')
            if isinstance(armor_attr, str):
                attr_name = armor_attr
            else:
                attr_name = getattr(armor_attr, 'value', '普通')

            # 检查是否有能克制这个护甲的子弹
            needed_attr = counter_map.get(attr_name, attr_name)
            if needed_attr in ammo_attrs or attr_name in ammo_attrs:
                return True  # 有克制或同属性子弹

        # 没有任何有效子弹
        return False

    def _hoshino_find_finishable_target(self, player, state) -> Optional[Any]:
        """找到同地点可一发击杀的残血目标（非主目标）"""
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            t = state.get_player(pid)
            if not t or not t.is_alive():
                continue
            if not self._same_location(player, t):
                continue
            # 无外甲 + 无内甲 + effective HP <= 1.0（一次射击 = 2发×0.5 = 1.0 伤害）
            # 或 无外甲 + 有内甲 + effective HP <= 0.5（最后内甲吸收所有溢出）
            if self._count_outer_armor(t) > 0:
                continue
            inner_count = self._count_inner_armor(t)
            eff_hp = self._get_effective_hp(t)
            if inner_count == 0 and eff_hp > 1.0:
                continue
            if inner_count > 0 and eff_hp > 0.5:
                continue
            # 爱愿检查：如果目标是 G5 持有者且自己有爱愿，跳过
            t_talent = getattr(t, 'talent', None)
            if t_talent and hasattr(t_talent, 'has_love_wish') and t_talent.has_love_wish(player.player_id):
                continue
            return t
        return None

    def _hoshino_should_use_adrenaline(self, player, target) -> bool:
        """判断是否应该在宏外使用肾上腺素。
        条件：
        1. 持有肾上腺素且未使用过
        2. 目标难杀（受警察保护 / 护甲多 / 临时HP）
        3. 有足够弹药/消耗品支撑爆发
        4. 弹药属性能有效打击目标
        """
        talent = getattr(player, 'talent', None)
        if not talent:
            return False
        if getattr(talent, 'adrenaline_used', True):
            return False
        if "肾上腺素" not in getattr(talent, 'medicines', []):
            return False

        # 条件1：目标值得用肾上腺素
        is_protected = self._hoshino_target_is_police_protected(target)
        is_hard = self._hoshino_target_is_hard_to_kill(target)
        armor_count = self._count_outer_armor(target) + self._count_inner_armor(target)
        target_worth_it = is_protected or is_hard or armor_count >= 2
        if not target_worth_it:
            return False

        # 条件2：弹药充足
        if not self._hoshino_has_enough_ammo_for_burst(player):
            return False

        # 条件3：属性克制
        if not self._hoshino_can_effectively_shoot(player, target):
            return False

        return True

    def _hoshino_build_finish_and_switch_macro(self, player, state, finish_target, switch_target) -> List[str]:
        """补刀残血目标 + 转火到第二目标的战术宏模板。

        结构（同地点残血 + 不同地点第二目标）：
        find A(1) → 射击 A(2) → 冲刺 B位置(1) → find B(1) = 5 cost

        结构（同地点残血 + 同地点第二目标）：
        find A(1) → 射击 A(2) → find B(1) → 射击 B(2) = 6 cost（需EPO或肾上腺素）
        或 find A(1) → 射击 A(2) → find B(1) → terminal = 4 cost（保守）
        """
        talent = getattr(player, 'talent', None)
        if not talent:
            return ["terminal"]

        cost = getattr(talent, 'cost', 5)
        used_cost = 0
        queue = []

        COST = {"find": 1, "射击": 2, "冲刺": 1, "持盾": 1, "架盾": 2, "取消": 0,
                "投掷": 1, "服药": 0, "重新装填": 0, "转向": 0}

        def can_afford(action):
            return (cost - used_cost) >= COST.get(action, 0)

        # 如果当前在架盾/持盾状态，先取消（补刀不需要盾）
        shield_mode = getattr(talent, 'shield_mode', None)
        if shield_mode:
            queue.append("取消")

        # 阶段1：补刀残血目标
        # 检查是否需要装填
        ammo = getattr(talent, 'ammo', [])
        if len(ammo) < 2 and can_afford("重新装填"):
            consumable = self._hoshino_find_consumable_for_reload(player)
            if consumable:
                queue.append(f"重新装填 {consumable}")

        # find 残血目标
        if can_afford("find"):
            queue.append(f"find {finish_target.name}")
            used_cost += COST["find"]

        # 射击残血目标（一发就够）
        if can_afford("射击"):
            queue.append(f"射击 {finish_target.name}")
            used_cost += COST["射击"]

        # 阶段2：转火到第二目标
        switch_same_loc = self._same_location(player, switch_target)

        if not switch_same_loc:
            # 不同地点：冲刺过去 + find
            switch_loc = self._get_location_str(switch_target)
            if can_afford("冲刺"):
                queue.append(f"冲刺 {switch_loc}")
                used_cost += COST["冲刺"]
            if can_afford("find"):
                queue.append(f"find {switch_target.name}")
                used_cost += COST["find"]
        else:
            # 同地点：直接 find + 如果还有 cost 就射击
            if can_afford("find"):
                queue.append(f"find {switch_target.name}")
                used_cost += COST["find"]
            if can_afford("射击"):
                queue.append(f"射击 {switch_target.name}")
                used_cost += COST["射击"]

        queue.append("terminal")
        return queue
    # ════════════════════════════════════════════════════════
    #  顺手拿
    # ════════════════════════════════════════════════════════

    def _hoshino_grab_while_here(self, player, state, available_actions) -> List[str]:
        """当前地点有可拿物品时顺手拿一个。
        条件：interact 可用、战术已解锁、当前地点有可拿物品。"""
        if "interact" not in available_actions:
            return []
        if not self._hoshino_tactical_unlocked(player):
            return []
        loc = self._get_location_str(player)
        result = self._hoshino_pick_best_item(player, state, loc)
        if result:
            return [f"interact {result['name']}"]
        return []

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
        prefer_deploy = (talent.iron_horus_hp <= talent.iron_horus_max_hp / 2
                        and talent.iron_horus_hp > 0)  # HP低于上限一半且未破损 → 偏好架盾

        # ===== 阶段1：接近 + 控制前缀 =====
        # ===== 预投掷：冲刺前先投掷到目标位置（禁用警察保护等）=====
        throw_item = self._hoshino_pick_throw_item(player, target)
        pre_throw = False
        if throw_item and not same_loc and can_afford("投掷"):
            # 不同地点：先投掷到目标位置，再冲刺过去
            queue.append(f"投掷 {throw_item} {target_loc}")
            used_cost += COST["投掷"]
            pre_throw = True

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
            if same_loc and has_find and prefer_deploy:
                # 持盾中 + 同地点 + 已 find + HP低 → 取消持盾 → 架盾 → find（确保目标在正面）
                queue.append("取消")  # cost 0
                if can_afford("架盾"):
                    queue.append("架盾")
                    used_cost += COST["架盾"]
                if can_afford("find"):
                    queue.append(f"find {target.name}")
                    used_cost += COST["find"]
            elif same_loc and not has_find and prefer_deploy:
                # 持盾中 + 同地点 + 没 find + HP低 → 取消持盾 → 架盾 → find
                queue.append("取消")  # cost 0
                if can_afford("架盾"):
                    queue.append("架盾")
                    used_cost += COST["架盾"]
                if can_afford("find"):
                    queue.append(f"find {target.name}")
                    used_cost += COST["find"]
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
                # 同地点 + HP低 → 架盾 → find（确保目标在正面可射击）
                if can_afford("架盾"):
                    queue.append("架盾")
                    used_cost += COST["架盾"]
                if can_afford("find"):
                    queue.append(f"find {target.name}")
                    used_cost += COST["find"]
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

        # ===== 同地点投掷（如果还没投掷过）=====
        if not pre_throw and throw_item and same_loc and can_afford("投掷"):
            queue.append(f"投掷 {throw_item} {target_loc}")
            used_cost += COST["投掷"]

        # ===== 服药：海豚巧克力（cost 0）=====
        if self._hoshino_should_use_chocolate(player):
            queue.append("服药 海豚巧克力")

        # ===== 服药：EPO（cost 0，剩余 cost 为奇数时 +1 能多打一发）=====
        if self._hoshino_should_use_epo(player, cost, used_cost):
            queue.append("服药 EPO")
            cost += 1  # EPO 立即生效

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

    def _hoshino_build_anti_captain_shielded_macro(self, player, state, captain) -> List[str]:
        """反队长宏（有盾版）：投掷 → 持盾 → dash → 架盾

        设计意图：
        - 投掷道具到队长位置（闪光弹/烟雾弹禁用警察）
        - 持盾冲刺过去
        - 到达后立刻架盾（警察强制正面，造伤≤1，架盾大概率挡住）
        - 下一个宏再全力射击
        """
        talent = getattr(player, 'talent', None)
        if not talent:
            return ["terminal"]

        queue = []
        cost = talent.cost
        used_cost = 0
        COST = {
            "架盾": 2, "射击": 2, "重新装填": 0, "持盾": 1,
            "投掷": 1, "服药": 0, "冲刺": 1, "取消": 0,
            "find": 1, "lock": 1, "转向": 0, "排弹": 0,
        }

        def can_afford(action):
            return used_cost + COST.get(action, 0) <= cost

        captain_loc = self._get_location_str(captain)
        same_loc = self._hoshino_target_same_location(player, captain)
        shield_mode = talent.shield_mode
        items = getattr(talent, 'tactical_items', [])

        # 选择投掷道具（优先闪光弹 > 烟雾弹 > 震撼弹 > 破片手雷 > 燃烧瓶）
        anti_police_priority = ["闪光弹", "烟雾弹", "震撼弹", "破片手雷", "燃烧瓶"]
        throw_item = None
        for item in anti_police_priority:
            if item in items:
                throw_item = item
                break

        if not same_loc:
            # 不同地点：投掷 → 持盾（如果没持盾）→ dash → 架盾
            if throw_item and can_afford("投掷"):
                queue.append(f"投掷 {throw_item} {captain_loc}")
                used_cost += COST["投掷"]

            if shield_mode == "架盾":
                queue.append("取消")  # cost 0
                shield_mode = None

            if shield_mode != "持盾" and can_afford("持盾"):
                queue.append("持盾")
                used_cost += COST["持盾"]

            if can_afford("冲刺"):
                queue.append(f"冲刺 {captain_loc}")
                used_cost += COST["冲刺"]

            # 到达后架盾（警察强制正面，架盾挡住）
            if can_afford("架盾"):
                queue.append("架盾")
                used_cost += COST["架盾"]
        else:
            # 同地点：投掷（原地）→ 架盾（如果没架盾）→ find → 射击
            if shield_mode == "持盾":
                # 已持盾 → 取消 → 投掷 → 架盾
                queue.append("取消")
                shield_mode = None

            if throw_item and can_afford("投掷"):
                queue.append(f"投掷 {throw_item} {captain_loc}")
                used_cost += COST["投掷"]

            if shield_mode != "架盾" and can_afford("架盾"):
                queue.append("架盾")
                used_cost += COST["架盾"]

            if can_afford("find"):
                queue.append(f"find {captain.name}")
                used_cost += COST["find"]

            # 装填检查（射击前确保有弹药）
            has_ammo = bool(getattr(talent, 'ammo', []))
            if not has_ammo:
                consumable = self._hoshino_find_consumable_for_reload(player)
                if consumable:
                    queue.append(f"重新装填 {consumable}")  # cost 0
                else:
                    queue.append("terminal")
                    return queue

            # 填充射击
            remaining_cost = cost - used_cost
            while remaining_cost >= COST["射击"]:
                queue.append(f"射击 {captain.name}")
                remaining_cost -= COST["射击"]

        queue.append("terminal")
        return queue

    def _hoshino_build_anti_captain_unshielded_macro(self, player, state, captain) -> List[str]:
        """反队长宏（无盾版）：move到队长位置 → 投掷 → find → 射击

        铁之荷鲁斯破损，没有盾可用。直接冲过去最大化输出。
        """
        talent = getattr(player, 'talent', None)
        if not talent:
            return ["terminal"]

        queue = []
        cost = talent.cost
        used_cost = 0
        COST = {
            "架盾": 2, "射击": 2, "重新装填": 0, "持盾": 1,
            "投掷": 1, "服药": 0, "冲刺": 1, "取消": 0,
            "find": 1, "lock": 1, "转向": 0, "排弹": 0,
        }

        def can_afford(action):
            return used_cost + COST.get(action, 0) <= cost

        captain_loc = self._get_location_str(captain)
        same_loc = self._hoshino_target_same_location(player, captain)
        items = getattr(talent, 'tactical_items', [])

        anti_police_priority = ["闪光弹", "烟雾弹", "震撼弹", "破片手雷", "燃烧瓶"]
        throw_item = None
        for item in anti_police_priority:
            if item in items:
                throw_item = item
                break

        # 注意：无盾版不能用冲刺（冲刺需要持盾），只能用 move（在宏外执行）
        # 所以这个方法返回的不是宏队列，而是普通命令
        # 如果不在同地点，controller 层应该先 move 过去

        if same_loc:
            # 同地点：投掷 → find → 射击（尽可能多）
            if throw_item and can_afford("投掷"):
                queue.append(f"投掷 {throw_item} {captain_loc}")
                used_cost += COST["投掷"]

            if can_afford("find"):
                queue.append(f"find {captain.name}")
                used_cost += COST["find"]

            # 装填检查（射击前确保有弹药）
            has_ammo = bool(getattr(talent, 'ammo', []))
            if not has_ammo:
                consumable = self._hoshino_find_consumable_for_reload(player)
                if consumable:
                    queue.append(f"重新装填 {consumable}")  # cost 0
                else:
                    queue.append("terminal")
                    return queue

            # 填充射击
            remaining_cost = cost - used_cost
            while remaining_cost >= COST["射击"]:
                queue.append(f"射击 {captain.name}")
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
