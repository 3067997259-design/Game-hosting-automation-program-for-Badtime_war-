"""CombatMixin —— 攻击命令、目标选择、武器选择"""
from typing import List, Optional, Any
from controllers.ai.constants import (
    EFFECTIVE_AGAINST, POLICE_AOE_WEAPONS,
    debug_ai_basic, debug_ai_attack_generation,
    make_weapon
)


class CombatMixin:
    def _cmd_attack(self, player, state, available: List[str],
                    forced_target=None) -> List[str]:
        commands = []
        target = forced_target or self._pick_target(player, state)
        if not target:
            return commands
        weapon = self._pick_weapon(player, target)
        if not weapon:
            return commands
        # Fix: 目标受警察保护时，强制切换到AOE武器绕过单体免疫
        pe = getattr(state, 'police_engine', None)
        if pe and pe.is_protected_by_police(target.player_id):
            if self._get_weapon_range(weapon) != "area":
                aoe_names = self._get_all_aoe_weapon_names(player)
                if aoe_names:
                    # 遍历所有AOE武器，按属性克制+可用性评分选最佳
                    target_armor_attrs = self._get_outer_armor_attr(target)
                    if not target_armor_attrs:
                        target_armor_attrs = self._get_inner_armor_attr(target)
                    ready_candidates = []   # [(score, weapon)]
                    charge_candidate = None
                    for aoe_name in aoe_names:
                        # 先从玩家武器列表找实体
                        aoe_weapon = next((w for w in getattr(player, 'weapons', [])
                                        if w and w.name == aoe_name), None)
                        if not aoe_weapon:
                            # 学会的法术不在weapons列表里，用make_weapon创建
                            aoe_weapon = make_weapon(aoe_name)
                        if not aoe_weapon:
                            continue  # 无法创建武器实例，跳过
                        # 检查是否需要蓄力（如电磁步枪）
                        if (getattr(aoe_weapon, 'requires_charge', False)
                                and getattr(aoe_weapon, 'charge_mandatory', True)
                                and not getattr(aoe_weapon, 'is_charged', False)):
                            # 需要蓄力，记录为备选但继续找不需要蓄力的
                            if charge_candidate is None:
                                charge_candidate = (aoe_name, aoe_weapon)
                            continue
                        # 可直接使用，按属性克制评分
                        score = self._get_weapon_damage(aoe_weapon) * 10
                        w_attr = self._get_weapon_attr(aoe_weapon)
                        if target_armor_attrs:
                            effective_set = EFFECTIVE_AGAINST.get(w_attr, set())
                            if any(a in effective_set for a in target_armor_attrs):
                                score += 50  # 能克制目标护甲，大幅加分
                            else:
                                score -= 30  # 打不动，降分
                        ready_candidates.append((score, aoe_weapon))
                    # 从可直接使用的候选中选最佳
                    ready_weapon = None
                    if ready_candidates:
                        ready_candidates.sort(key=lambda x: x[0], reverse=True)
                        ready_weapon = ready_candidates[0][1]
                        # 如果最佳AOE也打不穿护甲，不浪费回合
                        if ready_candidates[0][0] < -20:
                            debug_ai_attack_generation(player.name,
                                weapon.name, f"目标 {target.name} 受警察保护，所有AOE武器无法克制护甲")
                            return commands  # 返回空，让上层逻辑去获取有效武器
                    if ready_weapon:
                        weapon = ready_weapon
                        debug_ai_attack_generation(player.name,
                            weapon.name, f"目标 {target.name} 受警察保护，强制切换AOE武器: {weapon.name}")
                    elif charge_candidate:
                        # 没有可直接使用的，蓄力第一个需要蓄力的
                        c_name, c_weapon = charge_candidate
                        if "special" in available:
                            commands.append(f"special 蓄力{c_name}")
                            debug_ai_attack_generation(player.name,
                                c_name, f"目标 {target.name} 受警察保护，AOE武器需蓄力")
                        return commands
                    else:
                        # 所有AOE武器名都无法创建实体（理论上不应发生）
                        debug_ai_attack_generation(player.name,
                            weapon.name, f"目标 {target.name} 受警察保护，AOE武器实体化失败，跳过攻击")
                        return commands
                else:
                    # 真的没有AOE武器，跳过攻击（不浪费回合在必定失败的近战上）
                    debug_ai_attack_generation(player.name,
                        weapon.name, f"目标 {target.name} 受警察保护且无AOE武器，跳过攻击")
                    return commands
        # 检查武器是否被目标护甲克制
        if self._all_weapons_countered(player, target):
            # 所有武器都被克制，不生成攻击命令
            debug_ai_attack_generation(player.name,
                weapon.name, f"所有武器被目标 {target.name} 护甲克制，跳过攻击")
            return commands
        cmds = self._build_attack_cmd(player, target, weapon, state, available)
        commands.extend(cmds)
        debug_ai_attack_generation(player.name,
            weapon.name, f"攻击命令: {commands} (目标={target.name})")
        return commands
    def _build_attack_cmd(self, player, target, weapon, state,
                          available: List[str]) -> List[str]:
        """
        Bug6修复核心：根据武器类型检查前置条件
        - 近战：需要 ENGAGED_WITH → 没有则先find
        - 远程：需要 LOCKED_BY → 没有则先 lock
        - 区域：检查同地点有目标
        """
        commands = []
        markers = getattr(state, 'markers', None)
        weapon_range = self._get_weapon_range(weapon)
        # 救世主状态：远程武器不应该到这里（_pick_weapon 已过滤），但防御性处理
        if self._is_in_savior_state(player) and weapon_range == "ranged":
            weapon_range = "melee"  # 降级为近战路径
        if weapon_range == "melee":
            # 近战：需要先 find（建立ENGAGED_WITH）
            is_engaged = False
            if markers and hasattr(markers, 'has_relation'):
                is_engaged = markers.has_relation(
                    player.player_id, "ENGAGED_WITH", target.player_id)
            if not is_engaged:
                # 检查目标是否对自己可见
                markers_obj = getattr(state, 'markers', None)
                target_visible = True
                if markers_obj and hasattr(markers_obj, 'is_visible_to'):
                    target_visible = markers_obj.is_visible_to(
                        target.player_id, player.player_id,
                        getattr(player, 'has_detection', False))
                if not target_visible:
                    # 目标隐身且自己没有探测 → 不生成 find，改为获取探测手段
                    detection_cmds = self._cmd_get_detection(player, state, available)
                    commands.extend(detection_cmds)
                    return commands
                if "find" in available:
                    # 先确认在同一地点
                    if self._same_location(player, target):
                        commands.append(f"find {target.name}")
                        return commands
                    else:
                        # 需要先移动
                        target_loc = self._get_location_str(target)
                        if target_loc and "move" in available:
                            commands.append(f"move {target_loc}")
                        commands.append(f"find {target.name}")
                        return commands
                else:
                    return commands# find 不可用
            # 已ENGAGED_WITH，可以攻击
            if "attack" in available:
                layer, attr = self._pick_attack_layer(player, target, weapon)
                if layer and attr:
                    commands.append(f"attack {target.name} {weapon.name} {layer} {attr}")
                else:
                    commands.append(f"attack {target.name} {weapon.name}")
        elif weapon_range == "ranged":
            # 远程：需要先 lock（建立 LOCKED_BY）
            is_locked = False
            if markers and hasattr(markers, 'has_relation'):
                is_locked = markers.has_relation(
                    target.player_id, "LOCKED_BY", player.player_id)
            if not is_locked:
                # 检查目标是否对自己可见
                markers_obj = getattr(state, 'markers', None)
                target_visible = True
                if markers_obj and hasattr(markers_obj, 'is_visible_to'):
                    target_visible = markers_obj.is_visible_to(
                        target.player_id, player.player_id,
                        getattr(player, 'has_detection', False))
                if not target_visible:
                    # 目标隐身且自己没有探测 → 不生成 lock，改为获取探测手段
                    detection_cmds = self._cmd_get_detection(player, state, available)
                    commands.extend(detection_cmds)
                    return commands
                if "lock" in available:
                    commands.append(f"lock {target.name}")
                    return commands
                else:
                    return commands  # lock 不可用
            # 已 LOCKED_BY，可以攻击
            if "attack" in available:
                layer, attr = self._pick_attack_layer(player, target, weapon)
                if layer and attr:
                    commands.append(f"attack {target.name} {weapon.name} {layer} {attr}")
                else:
                    commands.append(f"attack {target.name} {weapon.name}")
        elif weapon_range == "area":
            if "attack" in available:
                same_loc_targets = self._get_same_location_targets(player, state)
                if same_loc_targets:
                    layer, attr = self._pick_attack_layer(player, target, weapon)
                    if layer and attr:
                        commands.append(f"attack {target.name} {weapon.name} {layer} {attr}")
                    else:
                        commands.append(f"attack {target.name} {weapon.name}")
                else:
                    # area 武器 move 兜底：先移动到目标位置
                    target_loc = self._get_location_str(target)
                    if target_loc and "move" in available:
                        commands.append(f"move {target_loc}")
            return commands
        else:
            # 未知类型，按近战处理
            if "attack" in available:
                layer, attr = self._pick_attack_layer(player, target, weapon)
                if layer and attr:
                    commands.append(f"attack {target.name} {weapon.name} {layer} {attr}")
                else:
                    commands.append(f"attack {target.name} {weapon.name}")
            return commands
        return commands
    def _cmd_rearm(self, player, state, available: List[str]) -> List[str]:
        """近战中所有武器被克制后的换武器逻辑
        1. 检查当前地点能否interact到非普通属性武器 → 直接拿
        2. 不能 → move到魔法所或军事基地
        - 有凭证且缺科技和魔法武器 → 选人少的
        - 选军事基地时强买通行证（通过confirm机制）
        - 没凭证 → 去魔法所
        """
        commands = []
        loc = self._get_location_str(player)
        # 1) 当前地点能拿到非普通武器吗？
        if "interact" in available:
            interact_cmd = self._get_counter_weapon_interact_cmd(player)
            if interact_cmd:
                commands.append(interact_cmd)
                return commands
        # 2) 当前地点拿不到，需要移动
        if "move" in available:
            dest = self._pick_counter_weapon_destination(player, state)
            if dest and dest != loc:
                commands.append(f"move {dest}")
        return commands
    def _all_weapons_countered(self, player, target) -> bool:
        """检查玩家所有武器是否都被目标护甲克制（检查所有层）"""
        weapons = getattr(player, 'weapons', [])
        if not weapons:
            return True  # 没武器视为被克制
        target_outer_attrs = self._get_outer_armor_attr(target)
        target_inner_attrs = self._get_inner_armor_attr(target)
        if not target_outer_attrs and not target_inner_attrs:
            return False  # 目标无甲，任何武器都有效
        # 需要检查的护甲层：如果有外层就检查外层，否则检查内层
        # （因为攻击时先打外层，外层打完才打内层）
        check_attrs = target_outer_attrs if target_outer_attrs else target_inner_attrs
        for w in weapons:
            w_attr = self._get_weapon_attr(w)
            effective_set = EFFECTIVE_AGAINST.get(w_attr, set())
            for armor_attr in check_attrs:
                if armor_attr in effective_set:
                    return False  # 至少有一把武器能打当前层
        return True
    def _has_non_ordinary_weapon(self, player) -> bool:
        """检查玩家是否拥有非普通属性的武器（魔法或科技）"""
        from utils.attribute import Attribute
        for w in getattr(player, 'weapons', []):
            if not w:
                continue
            attr = self._get_weapon_attr(w)
            if attr in (Attribute.MAGIC, Attribute.TECH):
                return True
        return False
    def _get_counter_weapon_interact_cmd(self, player) -> Optional[str]:
        """在当前地点寻找可以interact获取的非普通属性武器，返回interact命令或None"""
        loc = self._get_location_str(player)
        learned = self._get_learned_spells(player)
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
            # 高斯步枪（近战科技）、电磁步枪（范围科技）
            has_gauss = any(w.name == "高斯步枪" for w in player.weapons)
            has_emr = any(w.name == "电磁步枪" for w in player.weapons)
            if not has_gauss:
                return "interact 高斯步枪"
            if not has_emr:
                return "interact 电磁步枪"
            return None
        # home、商店、医院、警察局都没有非普通属性武器
        return None
    def _pick_counter_weapon_destination(self, player, state) -> str:
        """选择去哪里获取非普通属性武器
        规则：
        - 有凭证且缺科技和魔法武器 → 选魔法所或军事基地中人少的
        - 如果选军事基地，到达时会触发强买通行证
        - 没凭证 → 去魔法所（免费）
        """
        from utils.attribute import Attribute
        vouchers = getattr(player, 'vouchers', 0)
        has_magic_weapon = any(
            self._get_weapon_attr(w) == Attribute.MAGIC
            for w in getattr(player, 'weapons', []) if w
        )
        has_tech_weapon = any(
            self._get_weapon_attr(w) == Attribute.TECH
            for w in getattr(player, 'weapons', []) if w
        )
        if vouchers < 1:
            # 没凭证，只能去魔法所（免费学法术）
            return "魔法所"
        # 有凭证，两个地方都可以去
        candidates = []
        if not has_magic_weapon:
            candidates.append("魔法所")
        if not has_tech_weapon:
            candidates.append("军事基地")
        if not candidates:
            # 两种都有了但还是被克制？理论上不应该发生，保底去魔法所
            return "魔法所"
        if len(candidates) == 1:
            return candidates[0]
        # 两个都可以，选人少的
        enemies_magic = self._count_enemies_at("魔法所", player, state)
        enemies_military = self._count_enemies_at("军事基地", player, state)
        if enemies_military <= enemies_magic:
            return "军事基地"
        else:
            return "魔法所"
    def _pick_target(self, player, state) -> Optional[Any]:
        """选择最佳攻击目标"""
        candidates = []
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if not target or not target.is_alive():
                continue
            # 警察成员不攻击普通玩家（Bug修复：警察犯罪限制）
            if getattr(player, 'is_police', False) and not getattr(player, 'is_captain', False):
                if not getattr(target, 'is_criminal', False):
                    continue
            candidates.append(target)
        if not candidates:
            return None
                # 评分
        # 预计算全场最强候选的 power
        max_power = max(
            (self._estimate_power(c) for c in candidates),
            default=0
        )
        def score(t):
            s = 0
            s += self._threat_scores.get(t.name, 0) * 2
            if t.name in self._been_attacked_by:
                s += 50
            if self._same_location(player, t):
                s += 30
            s += max(0, 5 - self._get_effective_hp(t)) * 10
            s -= self._count_outer_armor(t) * 15
            s -= self._count_inner_armor(t) * 10
            if self.personality == "assassin":
                s += max(0, 3 - self._get_effective_hp(t)) * 20
                if self._count_outer_armor(t) == 0:
                    s += 40
                if self._count_inner_armor(t) == 0:
                    s += 20
            if self.personality == "aggressive":
                target_name = getattr(t, 'name', '')
                target_pid = getattr(t, 'player_id', '')
                is_passive = (target_name not in self._players_who_attacked
                            and target_pid not in self._players_who_attacked)
                if is_passive:
                    target_power = self._estimate_power(t)
                    s += 30 + target_power * 0.3  # 越肉的发育者越危险
            # 武器有效性
            if self._all_weapons_countered(player, t):
                s -= 200
            # 隐身且无探测 → 大幅降分（打不到）
            if getattr(t, 'is_invisible', False) and not getattr(player, 'has_detection', False):
                markers_obj = getattr(state, 'markers', None)
                if markers_obj and hasattr(markers_obj, 'is_visible_to'):
                    if not markers_obj.is_visible_to(t.player_id, player.player_id, player.has_detection):
                        s -= 300  # 看不到的目标大幅降分
            # 警察保护（属性感知）
            pe = getattr(state, 'police_engine', None)
            if pe and pe.is_protected_by_police(t.player_id):
                if not self._has_aoe_weapon(player):
                    s -= 500  # 完全没有AOE
                elif not self._has_effective_aoe_against(player, t):
                    s -= 300  # 有AOE但打不穿护甲，降分但不完全放弃
                else:
                    s -= 50   # 有有效AOE，小幅降分（AOE伤害通常低于近战）
            elif getattr(t, 'is_captain', False):
                if not self._has_aoe_weapon(player) and self._captain_has_police_escort(t, state):
                    s -= 500
                elif self._has_aoe_weapon(player) and not self._has_effective_aoe_against(player, t):
                    s -= 200  # 队长暂时不受保护但AOE打不穿，中等降分
            # 全场最强玩家额外加分
            if self._estimate_power(t) >= max_power:
                s += 40
            # 火萤 debuff 生效后的目标偏好
            if self._has_firefly_talent(player) and self._firefly_debuff_active(player):
                # 优先攻击没有伤害>=2武器的玩家
                enemy_best_dmg = self._best_weapon_damage(t)
                if enemy_best_dmg < 2.0:
                    s += 60  # 大幅加分：优先打弱者
                else:
                    # 所有人都有高伤害武器时，优先打 hp+护盾总值低的
                    total_effective_hp = self._get_effective_hp(t) + self._count_outer_armor(t) + self._count_inner_armor(t)
                    s += max(0, 10 - total_effective_hp) * 15  # 总值越低分越高
            return s
        candidates.sort(key=score, reverse=True)
        return candidates[0]
    def _can_attack_target(self, player, target, state) -> bool:
        """检查是否可能攻击目标（考虑距离和武器）"""
        weapons = getattr(player, 'weapons', [])
        if not weapons:
            return False
        for w in weapons:
            wr = self._get_weapon_range(w)
            if wr == "area":
                if self._same_location(player, target):
                    return True
            elif wr == "ranged":
                # 救世主状态禁用远程
                if self._is_in_savior_state(player):
                    continue
                return True  # 远程不需要同地点
            elif wr == "melee":
                if self._same_location(player, target):
                    return True
        return False
    def _pick_weapon(self, player, target) -> Optional[Any]:
        """选择最佳武器"""
        weapons = getattr(player, 'weapons', [])
        if not weapons:
            return None
        # 过滤掉 None，让所有武器（含拳击）参与评分，由 weapon_score 决定优劣
        pool = [w for w in weapons if w]
        # 救世主状态：过滤掉远程武器（validator 会拒绝，避免浪费重试）
        if self._is_in_savior_state(player):
            melee_and_area = [w for w in pool if self._get_weapon_range(w) != "ranged"]
            if melee_and_area:
                pool = melee_and_area
        if not pool:
            return None
        target_outer_attrs = self._get_outer_armor_attr(target)
        if not target_outer_attrs:
            target_outer_attrs = self._get_inner_armor_attr(target)
        def weapon_score(w):
            s = 0
            dmg = self._get_weapon_damage(w)
            # 救世主状态：近战武器加上临时攻击力加成
            if self._is_in_savior_state(player) and self._get_weapon_range(w) == "melee":
                talent = getattr(player, 'talent', None)
                if talent and hasattr(talent, 'temp_attack_bonus'):
                    dmg += talent.temp_attack_bonus
            s += dmg * 10
            # 蓄力必须但未蓄力 → 打不出去，大幅扣分
            if (getattr(w, 'requires_charge', False)
                    and getattr(w, 'charge_mandatory', True)
                    and not getattr(w, 'is_charged', False)):
                s -= 200
            w_attr = self._get_weapon_attr(w)
            if target_outer_attrs and w_attr in EFFECTIVE_AGAINST:
                effective_set = EFFECTIVE_AGAINST[w_attr]
                has_effective = False
                for armor_attr in target_outer_attrs:
                    if armor_attr in effective_set:
                        has_effective = True
                        s += 20
                        break
                if not has_effective:
                    s -= 50
            # 射程适配
            wr = self._get_weapon_range(w)
            if self._same_location(player, target):
                if wr == "melee":
                    s += 10
                elif wr == "area":
                    s += 5  # area 同地点也能打
            else:
                if wr == "ranged":
                    s += 15
                elif wr == "melee":
                    s -= 20
            # 控制效果加分（同地点时更有价值）
            tags = getattr(w, 'special_tags', []) or []
            has_control = any(t in tags for t in ("shock_2_targets", "stun_on_hit"))
            if has_control and self._same_location(player, target):
                s += 15
            return s
        sorted_weapons = sorted(pool, key=weapon_score, reverse=True)
        return sorted_weapons[0]
    def _pick_attack_layer(self, player, target, weapon) -> tuple:
        """选择攻击层和属性，返回 (layer_str, armor_attr_str)
        layer_str: "外层" / "内层" / None（无甲直接打HP）
        armor_attr_str: 目标护甲的属性字符串（如 "魔法"），用于指定攻击哪件护甲
        """
        from models.equipment import ArmorLayer
        from utils.attribute import Attribute
        outer_active = []
        inner_active = []
        armor = getattr(target, 'armor', None)
        if armor and hasattr(armor, 'get_active'):
            outer_active = armor.get_active(ArmorLayer.OUTER)
            inner_active = armor.get_active(ArmorLayer.INNER)
        w_attr = weapon.attribute if weapon else Attribute.ORDINARY
        if outer_active:
            # 优先攻击能被武器克制的外甲
            best_piece = self._pick_best_armor_target(outer_active, w_attr)
            armor_attr_str = best_piece.attribute.value if hasattr(best_piece.attribute, 'value') else str(best_piece.attribute)
            return ("外层", armor_attr_str)
        elif inner_active:
            best_piece = self._pick_best_armor_target(inner_active, w_attr)
            armor_attr_str = best_piece.attribute.value if hasattr(best_piece.attribute, 'value') else str(best_piece.attribute)
            return ("内层", armor_attr_str)
        else:
            # 无甲，不指定层和属性
            return (None, None)
    def _pick_best_armor_target(self, armor_pieces, weapon_attr) -> Any:
        """从护甲列表中选择最佳攻击目标：优先选能被武器克制的"""
        effective_set = EFFECTIVE_AGAINST.get(weapon_attr, set())
        # 优先选能被克制的护甲
        for piece in armor_pieces:
            if piece.attribute in effective_set:
                return piece
        # 没有可克制的，选第一个
        return armor_pieces[0]
    # ════════════════════════════════════════════════════════
    #  探测手段获取
    # ════════════════════════════════════════════════════════

    def _cmd_get_detection(self, player, state, available: List[str]) -> List[str]:
        """生成获取探测手段的命令（当目标隐身且自己没有探测时）"""
        commands = []
        loc = self._get_location_str(player)
        has_detection = getattr(player, 'has_detection', False)
        if has_detection:
            return commands  # 已有探测，不需要

        vouchers = getattr(player, 'vouchers', 0)
        has_pass = getattr(player, 'has_military_pass', False)

        # 当前位置能拿探测手段就直接拿
        if "interact" in available:
            if loc == "商店" and vouchers >= 1:
                commands.append("interact 热成像仪")
                return commands
            if loc == "魔法所":
                learned = self._get_learned_spells(player)
                if "探测魔法" not in learned:
                    commands.append("interact 探测魔法")
                    return commands
            if loc == "军事基地" and has_pass:
                commands.append("interact 雷达")
                return commands

        # 不在能拿探测的地方 → 移动过去
        if "move" in available:
            # 优先去魔法所（免费），其次商店（需凭证），最后军事基地（需通行证）
            if loc != "魔法所":
                commands.append("move 魔法所")
            elif vouchers >= 1 and loc != "商店":
                commands.append("move 商店")
            elif has_pass and loc != "军事基地":
                commands.append("move 军事基地")
            else:
                # 没凭证也没通行证 → 去魔法所学探测魔法（免费）
                if loc != "魔法所":
                    commands.append("move 魔法所")

        return commands

    # ════════════════════════════════════════════════════════
    #  属性克制工具
    # ════════════════════════════════════════════════════════

    def _pick_counter_attr(self, target_armor_attr) -> 'Attribute':
        """根据目标护甲属性，选择克制它的武器属性"""
        from utils.attribute import Attribute
        counter_map = {
            Attribute.ORDINARY: Attribute.TECH,    # 科技克普通
            Attribute.MAGIC: Attribute.ORDINARY,    # 普通克魔法
            Attribute.TECH: Attribute.MAGIC,        # 魔法克科技
        }
        return counter_map.get(target_armor_attr, Attribute.ORDINARY)
    