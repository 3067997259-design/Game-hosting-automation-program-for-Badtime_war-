"""
HoshinoAIHook —— 星野(G7)天赋AI钩子（完整生命周期）

覆盖Hoshino从注册到终局的全部决策：
- Terror状态全图攻击
- 战术宏模式（肾上腺素、反警察搏命、正常宏）
- 未解锁时：融合材料获取（旧 _cmd_develop_hoshino 阶段0-1）
- 已解锁但无法战斗时：装填弹药、修复荷鲁斯（阶段2-3）
- 发育完成后：寻找目标出击（阶段4）

关键原则：should_override_candidates 永远不返回 None。
"""

from __future__ import annotations
from typing import Dict, List, Optional, Any
from controllers.ai.talents.base_hook import BaseTalentAIHook
from controllers.ai.game_query import GameQuery
from controllers.ai.constants import debug_ai_basic, PROTECTED_ITEMS


class HoshinoAIHook(BaseTalentAIHook):
    talent_name = "大叔我啊，剪短发了"

    def __init__(self, controller: Any):
        self._ctrl = controller

    def _get_threat_scores(self) -> Dict[str, float]:
        state = getattr(self._ctrl, '_ai_state', None)
        if state is not None:
            return getattr(state, 'threat_scores', {})
        return getattr(self._ctrl, '_threat_scores', {})

    def _get_players_who_attacked(self) -> set:
        state = getattr(self._ctrl, '_ai_state', None)
        if state is not None:
            return getattr(state, 'players_who_attacked', set())
        return getattr(self._ctrl, '_players_who_attacked', set())

    # ── 威胁修改 ──
    def modify_threat_power(self, target: Any, base_power: float) -> float:
        t_talent = getattr(target, 'talent', None)
        if not t_talent or getattr(t_talent, 'name', '') != self.talent_name:
            return base_power
        if getattr(t_talent, 'is_terror', False):
            return base_power + 200
        if getattr(t_talent, 'self_doubt_pending', False):
            return base_power + 150
        if getattr(t_talent, 'tactical_unlocked', False) and len(getattr(t_talent, 'ammo', [])) > 0:
            return base_power + 50
        if not getattr(t_talent, 'tactical_unlocked', False):
            return base_power - 20
        return base_power

    # ── T0 / choose 决策 ──
    def handle_choose(
        self, player: Any, state: Any, situation: str,
        options: List[str], context: Dict,
    ) -> Optional[str]:
        """覆盖星野的 choose 决策"""
        personality = context.get("personality", "balanced")
        threat_scores = context.get("threat_scores", {})

        if situation == "hoshino_form_choice":
            if personality == "aggressive":
                priority = ["临战-Archer", "临战-shielder", "水着-shielder"]
            elif personality == "defensive":
                priority = ["水着-shielder", "临战-shielder", "临战-Archer"]
            else:
                priority = ["水着-shielder", "临战-Archer", "临战-shielder"]
            for form in priority:
                if form in options:
                    return form
            return options[0]

        if situation == "hoshino_form":
            if personality in ("balanced", "defensive"):
                for opt in options:
                    if "水着" in opt:
                        return opt
            elif personality == "aggressive":
                for opt in options:
                    if "Archer" in opt:
                        return opt
            else:
                for opt in options:
                    if "shielder" in opt:
                        return opt
            return options[0]

        if situation == "hoshino_self_doubt":
            if state:
                alive_count = sum(1 for pid in state.player_order
                                  if state.get_player(pid) and state.get_player(pid).is_alive())
                if alive_count <= 2:
                    for opt in options:
                        if "接受" in opt or "terror" in opt.lower():
                            return opt
            for opt in options:
                if "拒绝" in opt or "抵抗" in opt:
                    return opt
            return options[-1]

        if situation == "hoshino_self_doubt_choice":
            if state:
                alive_count = sum(1 for pid in state.player_order
                                  if state.get_player(pid) and state.get_player(pid).is_alive())
                if alive_count <= 2:
                    return options[0]
            return options[1] if len(options) > 1 else options[0]

        if situation == "hoshino_tactical_equip":
            talent = getattr(player, 'talent', None)
            owned_items = getattr(talent, 'tactical_items', []) if talent else []
            owned_meds = getattr(talent, 'medicines', []) if talent else []
            priority_items = ["闪光弹", "烟雾弹", "破片手雷", "震撼弹"]
            for item_name in priority_items:
                for opt in options:
                    if item_name in opt and item_name not in owned_items:
                        return opt
            for opt in options:
                if "肾上腺素" in opt and "肾上腺素" not in owned_meds:
                    return opt
            for opt in options:
                if "子弹" in opt:
                    return opt
            return options[0]

        if situation == "hoshino_repair_material":
            for opt in options:
                if "盾牌" in opt:
                    return opt
            return options[0]

        if situation == "hoshino_throw_item":
            priority = ["闪光弹", "烟雾弹", "破片手雷", "震撼弹", "燃烧瓶"]
            for item in priority:
                if item in options:
                    return item
            return options[0]

        if situation == "hoshino_medicine":
            for opt in options:
                if "EPO" in opt:
                    return opt
            for opt in options:
                if "巧克力" in opt:
                    return opt
            return options[0] if options else ""

        if situation == "hoshino_dash_target":
            if not options:
                return ""
            return max(options, key=lambda name: threat_scores.get(name, 0))

        if situation == "hoshino_shoot_target":
            if not options:
                return ""
            return max(options, key=lambda name: threat_scores.get(name, 0))

        if situation == "hoshino_find_target":
            if not options:
                return ""
            return max(options, key=lambda name: threat_scores.get(name, 0))

        if situation == "hoshino_throw_location":
            combat_target = context.get("combat_target")
            if combat_target:
                target_loc = GameQuery.get_location_str(combat_target)
                if target_loc in options:
                    return target_loc
            return options[0]

        if situation == "poem_nightwatch_choice":
            talent = getattr(player, 'talent', None)
            if talent and getattr(talent, 'is_terror', False):
                for opt in options:
                    if "接受" in opt:
                        return opt
            for opt in options:
                if "拒绝" in opt:
                    return opt
            return options[-1]

        return None

    def _max_enemy_total_power(self, player, state=None) -> float:
        if state is None:
            state = getattr(self._ctrl, '_game_state', None)
        if not state:
            return 999.0
        max_power = 0.0
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            p = state.get_player(pid)
            if not p or not p.is_alive():
                continue
            outer = GameQuery.count_outer_armor(p)
            inner = GameQuery.count_inner_armor(p)
            power = p.hp + outer + inner
            if power > max_power:
                max_power = power
        return max_power

    # ── 候选命令覆盖：永远不返回 None ──
    def should_override_candidates(
        self, player: Any, state: Any, available: List[str]
    ) -> Optional[List[str]]:
        if not GameQuery.has_hoshino_talent(player):
            return None

        # 1. Terror: 全图攻击
        if self._ctrl._hoshino_is_terror(player):
            return self._ctrl._hoshino_terror_command(player, state, available)

        tactical_unlocked = self._ctrl._hoshino_tactical_unlocked(player)

        # 2. 未解锁战术 → Hoshino专用发育路径（融合材料）
        if not tactical_unlocked:
            return self._get_development_commands(player, state, available)

        # 3. 已解锁 → 先检查是否能正常战斗
        can_shoot = (self._ctrl._hoshino_has_ammo(player)
                     or bool(self._ctrl._hoshino_find_consumable_for_reload(player)))
        horus_ok = self._ctrl._hoshino_iron_horus_hp(player) > 0

        # ════════════════════════════════════════════════════════
        #  盾牌死锁检测：持盾/架盾时无法interact
        #  必须在所有分支之前——弹药耗尽或荷鲁斯破损时先解除盾牌
        # ════════════════════════════════════════════════════════
        shield_mode = self._ctrl._hoshino_shield_mode(player)
        if shield_mode and (not can_shoot or not horus_ok) and "special" in available:
            self._ctrl._hoshino_macro_queue = ["取消", "terminal"]
            return ["special Hoshino", "forfeit"]

        # 3a. 肾上腺素（宏外免费行动）
        if "special" in available:
            adr_target = self._ctrl._hoshino_find_target(player, state)
            if adr_target and self._ctrl._hoshino_should_use_adrenaline(player, adr_target):
                macro_cmds = self._build_macro_commands(player, state, available)
                if macro_cmds:
                    return ["special 肾上腺素"] + macro_cmds
                return ["special 肾上腺素", "forfeit"]

        # 3b. 可以战斗 → 战术宏
        if can_shoot and horus_ok:
            macro_result = self._build_macro_commands(player, state, available)
            if macro_result is not None:
                return macro_result
            # 无目标 → 去敌人位置
            enemy_loc = GameQuery.find_nearest_enemy_location(
                player, state, self._get_threat_scores(),
                personality=getattr(self._ctrl, 'personality', 'balanced'),
                players_who_attacked=self._get_players_who_attacked(),
            )
            if enemy_loc and "move" in available:
                loc = GameQuery.get_location_str(player)
                if enemy_loc != loc and not (enemy_loc == "home" and GameQuery.is_at_home(player)):
                    return [f"move {enemy_loc}", "forfeit"]

        # 3c. 无法战斗 → 发育/修复（装填子弹、修复荷鲁斯）
        return self._get_maintenance_commands(player, state, available)

    def get_develop_commands(
        self, player: Any, state: Any, available: List[str]
    ) -> Optional[List[str]]:
        if not GameQuery.has_hoshino_talent(player):
            return None
        talent = player.talent
        if not getattr(talent, 'tactical_unlocked', False):
            return self._get_development_commands(player, state, available)
        return self._get_maintenance_commands(player, state, available)

    # ════════════════════════════════════════════════════════════
    #  发育路径（未解锁战术时）：融合材料获取
    #  对应旧 _cmd_develop_hoshino 阶段0-1
    # ════════════════════════════════════════════════════════════

    def _get_development_commands(
        self, player: Any, state: Any, available: List[str]
    ) -> List[str]:
        """未解锁战术时的发育：获取小刀→通行证→AT力场+盾牌→EMR+Gauss"""
        loc = GameQuery.get_location_str(player)
        talent = player.talent
        has_pass = getattr(player, 'has_military_pass', False)
        is_home = GameQuery.is_at_home(player)

        fusion_shield_done = getattr(talent, 'fusion_shield_done', False)
        fusion_weapon_done = getattr(talent, 'fusion_weapon_done', False)

        # 阶段0：小刀（顺手拿，不专门回家）
        has_knife = any(w.name == "小刀" for w in player.weapons if w)
        if not has_knife and "interact" in available and is_home:
            return ["interact 小刀", "forfeit"]

        # 阶段1：融合材料（需求驱动）
        has_at = GameQuery.has_armor_by_name(player, "AT力场")
        has_shield = GameQuery.has_armor_by_name(player, "盾牌")
        has_emr = any(w.name == "电磁步枪" for w in player.weapons if w)
        has_gauss = any(w.name == "高斯步枪" for w in player.weapons if w)

        needs = []  # (物品名, 地点, 优先级)
        if not has_pass:
            needs.append(("通行证", "军事基地", 100))
        if not fusion_shield_done:
            if not has_at:
                needs.append(("AT力场", "军事基地", 80))
            if not has_shield:
                needs.append(("盾牌", "home", 80))
        if not fusion_weapon_done:
            if not has_emr:
                needs.append(("电磁步枪", "军事基地", 60))
            if not has_gauss:
                needs.append(("高斯步枪", "军事基地", 60))
        needs.sort(key=lambda x: -x[2])

        # 当前地点能拿就先拿
        if "interact" in available:
            for item_name, item_loc, _ in needs:
                if item_loc == "军事基地" and loc == "军事基地" and has_pass:
                    return ["interact " + item_name, "forfeit"]
                if item_loc == "军事基地" and loc == "军事基地" and item_name == "通行证":
                    return ["interact 通行证", "forfeit"]
                if item_loc == "home" and is_home:
                    return ["interact " + item_name, "forfeit"]

        # 移动去拿
        if "move" in available and needs:
            _, target_loc, _ = needs[0]
            if target_loc == "军事基地" and loc != "军事基地":
                return ["move 军事基地", "forfeit"]
            if target_loc == "home" and not is_home:
                return ["move home", "forfeit"]

        return ["forfeit"]

    # ════════════════════════════════════════════════════════════
    #  维护路径（已解锁但无法战斗）：装填弹药、修复荷鲁斯
    #  对应旧 _cmd_develop_hoshino 阶段2-3
    # ════════════════════════════════════════════════════════════

    def _get_maintenance_commands(
        self, player: Any, state: Any, available: List[str]
    ) -> List[str]:
        """已解锁但无法战斗时的维护：装填→修复→囤道具→找敌人"""
        ctrl = self._ctrl
        loc = GameQuery.get_location_str(player)
        talent = player.talent
        has_pass = getattr(player, 'has_military_pass', False)
        ammo_count = len(getattr(talent, 'ammo', []))
        iron_horus_hp = getattr(talent, 'iron_horus_hp', 0)

        # 阶段2：装填子弹
        if ammo_count == 0:
            consumable = ctrl._hoshino_find_consumable_for_reload(player)
            if not consumable:
                if "interact" in available:
                    result = ctrl._hoshino_pick_best_item(player, state, loc)
                    if result:
                        return ["interact " + result['name'], "forfeit"]
                if "move" in available:
                    best_loc = ctrl._hoshino_best_item_destination(player, state)
                    if best_loc and best_loc != loc:
                        if not (best_loc == "home" and GameQuery.is_at_home(player)):
                            return [f"move {best_loc}", "forfeit"]
            # 有消耗品 → 需要在战术宏里装填（由 build_macro_commands 处理）

        # 反警察准备：囤战术道具
        if (ctrl._has_active_captain(player, state)
                and not ctrl._is_pursued_by_police_extended(player, state)):
            throwables = ctrl._hoshino_count_throwables(player)
            if throwables < 2:
                if loc == "军事基地" and has_pass and "interact" in available:
                    tactical_items = getattr(talent, 'tactical_items', [])
                    for item in ["闪光弹", "烟雾弹", "震撼弹", "破片手雷", "燃烧瓶"]:
                        if item not in tactical_items:
                            return ["interact " + item, "forfeit"]
                elif has_pass and loc != "军事基地" and "move" in available:
                    return ["move 军事基地", "forfeit"]

        # 阶段3：修复铁之荷鲁斯
        max_hp = getattr(talent, 'iron_horus_max_hp', 2)
        if iron_horus_hp < max_hp:
            has_material = (GameQuery.has_armor_by_name(player, "盾牌")
                            or GameQuery.has_armor_by_name(player, "AT力场"))
            if has_material and "special" in available:
                return ["special 修复", "forfeit"]
            if not has_material and "interact" in available:
                if GameQuery.is_at_home(player):
                    return ["interact 盾牌", "forfeit"]
                if loc == "军事基地" and has_pass:
                    return ["interact AT力场", "forfeit"]
            if not has_material and "move" in available:
                dest = "军事基地" if has_pass else "home"
                if loc != dest and not (dest == "home" and GameQuery.is_at_home(player)):
                    return [f"move {dest}", "forfeit"]

        # 阶段4：发育完成 → 找敌人
        if "move" in available:
            enemy_loc = GameQuery.find_nearest_enemy_location(
                player, state, self._get_threat_scores(),
                personality=getattr(ctrl, 'personality', 'balanced'),
                players_who_attacked=self._get_players_who_attacked(),
            )
            if enemy_loc and enemy_loc != loc:
                if not (enemy_loc == "home" and GameQuery.is_at_home(player)):
                    return [f"move {enemy_loc}", "forfeit"]

        if "attack" in available:
            same = GameQuery.get_same_location_targets(player, state)
            if same:
                # 选最佳近战武器攻击同地点目标
                weapons = [w for w in getattr(player, 'weapons', [])
                           if w and getattr(w, 'name', '') != "拳击"]
                if weapons:
                    best_w = max(weapons, key=lambda w: getattr(w, 'base_damage', 0))
                    return [f"attack {same[0].name} {best_w.name}", "forfeit"]
                # fallback: 拳击
                return [f"attack {same[0].name} 拳击", "forfeit"]

        return ["forfeit"]

    # ════════════════════════════════════════════════════════════
    #  战术宏命令生成
    # ════════════════════════════════════════════════════════════

    def _build_macro_commands(
        self, player: Any, state: Any, available: List[str]
    ) -> Optional[List[str]]:
        """构建战术宏命令"""
        ctrl = self._ctrl

        # 被警察追击 → 搏命模式
        if ctrl._is_pursued_by_police_extended(player, state):
            can_shoot = ctrl._hoshino_has_ammo(player) or bool(ctrl._hoshino_find_consumable_for_reload(player))
            if can_shoot:
                target = ctrl._hoshino_find_target(player, state)
                if target and "special" in available:
                    horus_ok = ctrl._hoshino_iron_horus_hp(player) > 0
                    if horus_ok:
                        ctrl._hoshino_macro_queue = ctrl._hoshino_build_anti_captain_approach_macro(player, state, target)
                        ctrl._hoshino_anti_captain_approached = True
                        ctrl._hoshino_anti_captain_target_id = target.player_id
                        return ["special Hoshino", "forfeit"]
                    else:
                        target_loc = GameQuery.get_location_str(target)
                        my_loc = GameQuery.get_location_str(player)
                        if target_loc == my_loc:
                            ctrl._hoshino_macro_queue = ctrl._hoshino_build_anti_captain_unshielded_macro(player, state, target)
                            return ["special Hoshino", "forfeit"]
                        elif "move" in available:
                            return [f"move {target_loc}", "forfeit"]

        # 盾牌死锁检测
        shield_mode = ctrl._hoshino_shield_mode(player)
        can_shoot = ctrl._hoshino_has_ammo(player) or bool(ctrl._hoshino_find_consumable_for_reload(player))
        horus_ok = ctrl._hoshino_iron_horus_hp(player) > 0
        if shield_mode and (not can_shoot or not horus_ok) and "special" in available:
            ctrl._hoshino_macro_queue = ["取消", "terminal"]
            return ["special Hoshino", "forfeit"]

        if can_shoot and horus_ok:
            if (getattr(ctrl, '_hoshino_anti_captain_approached', False)
                    and not ctrl._hoshino_macro_queue):
                ctrl._hoshino_anti_captain_approached = False
                captain_id = getattr(ctrl, '_hoshino_anti_captain_target_id', None)
                ctrl._hoshino_anti_captain_target_id = None
                if captain_id:
                    captain = state.get_player(captain_id)
                    if captain and captain.is_alive():
                        ctrl._hoshino_macro_queue = ctrl._hoshino_build_fullfire_macro(player, state, captain)
                        return ["special Hoshino", "forfeit"]

            target = ctrl._hoshino_find_target(player, state)
            if target and "special" in available:
                # ★ 弹药有效性检查：弹匣里的子弹打不穿目标护甲 → 先去装填克制属性弹药
                if (ctrl._hoshino_has_ammo(player)
                        and not ctrl._hoshino_can_effectively_shoot(player, target)
                        and self._target_has_outer_armor(target)):
                    # 有可用的克制装填来源 → 去装填
                    counter_item = self._find_counter_consumable(player, target)
                    if counter_item:
                        ctrl._hoshino_macro_queue = [
                            f"重新装填 {counter_item}", "terminal"]
                        return ["special Hoshino", "forfeit"]
                    # 无克制装填来源 → 继续通用战术宏，避免维护路径空转

                pc = ctrl._police_cache or {}
                captain_id = pc.get("captain_id")
                is_anti_captain = (
                    getattr(target, 'is_captain', False)
                    and ctrl._hoshino_captain_has_police_protection(state)
                    and ctrl._hoshino_has_enough_tactical_items(player)
                )
                if is_anti_captain:
                    talent = getattr(player, 'talent', None)
                    if (talent and "肾上腺素" in getattr(talent, 'medicines', [])
                            and not getattr(talent, 'adrenaline_used', False)
                            and talent.cost <= 5):
                        ctrl._hoshino_macro_queue = ctrl._hoshino_build_anti_captain_approach_macro(player, state, target)
                        ctrl._hoshino_anti_captain_target_id = target.player_id
                        return ["special 肾上腺素", "special Hoshino", "forfeit"]
                    ctrl._hoshino_macro_queue = ctrl._hoshino_build_anti_captain_approach_macro(player, state, target)
                    ctrl._hoshino_anti_captain_approached = True
                    ctrl._hoshino_anti_captain_target_id = target.player_id
                    return ["special Hoshino", "forfeit"]

                finish_target = ctrl._hoshino_find_finishable_target(player, state)
                if finish_target and finish_target.player_id != target.player_id:
                    ctrl._hoshino_macro_queue = ctrl._hoshino_build_finish_and_switch_macro(player, state, finish_target, target)
                    return ["special Hoshino", "forfeit"]

                # ★ 目标在不同地点（如不同玩家的家）→ 先移动再进宏
                my_loc = GameQuery.get_location_str(player)
                target_loc = GameQuery.get_location_str(target)
                if (my_loc != target_loc
                        and "move" in available
                        and not (target_loc == "home" and GameQuery.is_at_home(player))):
                    return [f"move {target_loc}", "forfeit"]

                ctrl._hoshino_macro_queue = []
                return ["special Hoshino", "forfeit"]

        return None

    def _find_counter_consumable(self, player, target) -> Optional[str]:
        """找能克制目标外甲的装填消耗品。
        克制链：目标科技甲→找魔法属性物品，目标普通甲→找科技属性，目标魔法甲→找普通。
        没有克制物品时返回 None（调用方应回退到通用路径）。"""
        outer_attrs = self._ctrl._hoshino_get_target_outer_armor_attrs(target)
        if not outer_attrs:
            return None
        counter_map = {"科技": "魔法", "普通": "科技", "魔法": "普通"}
        needed_attr = counter_map.get(outer_attrs[0])
        if not needed_attr:
            return None
        # 物品名 → 其属性 的映射（硬编码：只有这些常用装填品有明确属性）
        _ITEM_ATTRS = {
            "小刀": "普通", "盾牌": "普通", "陶瓷护甲": "普通",
            "电磁步枪": "科技", "高斯步枪": "科技", "AT力场": "科技",
            "雷达": "科技", "热成像仪": "科技",
            "魔法护盾": "魔法", "魔法弹幕": "魔法", "探测魔法": "魔法",
            "肾上腺素": "科技", "EPO": "科技",
        }
        talent = getattr(player, 'talent', None)
        iron_horus_hp = getattr(talent, 'iron_horus_hp', 0) if talent else 0
        iron_horus_max = getattr(talent, 'iron_horus_max_hp', 2) if talent else 2
        repair_names = {"盾牌", "AT力场"} if iron_horus_hp < iron_horus_max else set()
        # 武器
        for w in getattr(player, 'weapons', []):
            if w and w.name not in ("拳击", "荷鲁斯之眼"):
                if _ITEM_ATTRS.get(w.name) == needed_attr:
                    return w.name
        # 物品
        for item in getattr(player, 'items', []):
            item_name = getattr(item, 'name', None)
            if item_name and item_name not in repair_names and item_name not in PROTECTED_ITEMS:
                if _ITEM_ATTRS.get(item_name) == needed_attr:
                    return item_name
        # 护甲
        for a in getattr(getattr(player, 'armor', None), 'get_all_active', lambda: [])():
            if a and a.name not in ("拳击", "荷鲁斯之眼") and a.name not in repair_names:
                if _ITEM_ATTRS.get(a.name) == needed_attr:
                    return a.name
        return None

    @staticmethod
    def _target_has_outer_armor(target) -> bool:
        """检查目标是否有外层护甲（用于判断是否需要检查弹药克制）。"""
        armor_obj = getattr(target, 'armor', None)
        if not armor_obj or not hasattr(armor_obj, 'get_active'):
            return False
        from models.equipment import ArmorLayer
        outer = armor_obj.get_active(ArmorLayer.OUTER)
        return len(outer) > 0

    # ── 发育判定 ──
    def is_development_complete(self, player: Any, state: Any) -> Optional[bool]:
        if not GameQuery.has_hoshino_talent(player):
            return None
        talent = player.talent
        tactical_unlocked = getattr(talent, 'tactical_unlocked', False)
        has_ammo = len(getattr(talent, 'ammo', [])) > 0
        has_consumable = bool(self._ctrl._hoshino_find_consumable_for_reload(player))
        iron_horus_hp = getattr(talent, 'iron_horus_hp', 0)
        return tactical_unlocked and (has_ammo or has_consumable) and iron_horus_hp > 0
