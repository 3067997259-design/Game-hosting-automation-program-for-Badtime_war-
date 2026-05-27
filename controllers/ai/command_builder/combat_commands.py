"""CombatCommandBuilder —— 攻击指令、武器选择、换武器、蓄力、探测获取

从 combat_mixin.py 复制，所有 self._xxx 属性访问改为通过 GameQuery / ctx 参数。
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

from controllers.ai.constants import (
    EFFECTIVE_AGAINST, POLICE_AOE_WEAPONS,
    debug_ai_basic, debug_ai_attack_generation,
    make_weapon,
)

if TYPE_CHECKING:
    from controllers.ai.game_query import GameQuery
    from controllers.ai.context import OrchestratorContext


class CombatCommandBuilder:
    """战斗指令构建器。"""

    def __init__(self, query: "GameQuery"):
        self._query = query

    # ════════════════════════════════════════════════════════
    #  攻击命令入口
    # ════════════════════════════════════════════════════════

    def build_attack(
        self,
        player: Any,
        state: Any,
        strategy: Any,
        available: List[str],
        ctx: "OrchestratorContext",
        forced_target: Any = None,
        police_mind: Any = None,
    ) -> List[str]:
        """生成攻击指令序列（复制自 CombatMixin._cmd_attack）。"""
        Q = self._query
        commands: List[str] = []
        target = forced_target or self._pick_target_simple(player, state, ctx)
        if not target:
            return commands
        weapon = self.pick_weapon(player, target)
        if not weapon:
            return commands

        # 武器蓄力检查
        if (getattr(weapon, 'requires_charge', False)
                and getattr(weapon, 'charge_mandatory', True)
                and not getattr(weapon, 'is_charged', False)):
            if "special" in available:
                debug_ai_attack_generation(player.name,
                    weapon.name, f"武器 {weapon.name} 未蓄力，先生成蓄力命令")
                commands.append(f"special 蓄力{weapon.name}")
            return commands

        # 警察保护穿透检查
        pe = getattr(state, 'police_engine', None)
        if pe and pe.is_protected_by_police(target.player_id):
            target_outer = Q.get_outer_armor_attr(target)
            target_inner = Q.get_inner_armor_attr(target)
            aoe_names = Q.get_all_aoe_weapon_names(player)
            best_dmg = Q.estimate_talent_adjusted_damage(player, weapon)

            can_dmg, reason = (False, "无PoliceMind")
            protection_eval_source = "旧架构"
            if police_mind is not None:
                protection_eval_source = "PoliceMind"
                can_dmg, reason = police_mind.can_damage_through_protection(
                    player, target, state,
                    talent_adjusted_damage=best_dmg,
                    outer_armor_attrs=target_outer,
                    inner_armor_attrs=target_inner,
                    aoe_weapon_names=aoe_names,
                    player_weapons=getattr(player, 'weapons', []),
                    learned_spells=Q.get_learned_spells(player),
                )
            else:
                threshold = pe.get_protection_threshold(target.player_id)
                if best_dmg > threshold:
                    can_dmg = True
                    reason = f"伤害({best_dmg:.1f})超过警察保护阈值({threshold})，可硬穿"
                else:
                    reason = f"伤害({best_dmg:.1f})不足穿透阈值({threshold})"

            debug_ai_attack_generation(player.name, weapon.name,
                f"目标 {target.name} 受警察保护 → {protection_eval_source}: {reason}")

            # 判断 can_dmg=True 是否依赖 AOE 武器（当前武器伤害不够穿透阈值）
            threshold = pe.get_protection_threshold(target.player_id)
            need_aoe_swap = (
                (not can_dmg or best_dmg <= threshold)
                and Q.get_weapon_range(weapon) != "area"
            )
            if can_dmg and not need_aoe_swap:
                pass  # 当前武器伤害足够穿透，无需换武器
            elif need_aoe_swap:
                if aoe_names:
                    target_armor_attrs = target_outer if target_outer else target_inner
                    ready_candidates = []
                    charge_candidate = None
                    for aoe_name in aoe_names:
                        aoe_weapon = next((w for w in getattr(player, 'weapons', [])
                                           if w and w.name == aoe_name), None)
                        if not aoe_weapon:
                            aoe_weapon = make_weapon(aoe_name)
                        if not aoe_weapon:
                            continue
                        if (getattr(aoe_weapon, 'requires_charge', False)
                                and getattr(aoe_weapon, 'charge_mandatory', True)
                                and not getattr(aoe_weapon, 'is_charged', False)):
                            if charge_candidate is None:
                                charge_candidate = (aoe_name, aoe_weapon)
                            continue
                        score = Q.get_weapon_damage(aoe_weapon) * 10
                        w_attr = Q.get_weapon_attr(aoe_weapon)
                        if target_armor_attrs:
                            effective_set = EFFECTIVE_AGAINST.get(w_attr, set())
                            if any(a in effective_set for a in target_armor_attrs):
                                score += 50
                            else:
                                score -= 30
                        ready_candidates.append((score, aoe_weapon))
                    ready_weapon = None
                    if ready_candidates:
                        ready_candidates.sort(key=lambda x: x[0], reverse=True)
                        ready_weapon = ready_candidates[0][1]
                        if ready_candidates[0][0] < -20:
                            return commands
                    if ready_weapon:
                        weapon = ready_weapon
                    elif charge_candidate:
                        c_name, _ = charge_candidate
                        if "special" in available:
                            commands.append(f"special 蓄力{c_name}")
                        return commands
                    else:
                        return commands
                else:
                    return commands

        # 武器克制检查
        if Q.all_weapons_countered(player, target):
            debug_ai_attack_generation(player.name,
                weapon.name, f"所有武器被目标 {target.name} 护甲克制，跳过攻击")
            return commands

        # 被选中武器的克制检查
        if weapon:
            target_armor_attrs = Q.get_outer_armor_attr(target)
            if not target_armor_attrs:
                target_armor_attrs = Q.get_inner_armor_attr(target)
            if target_armor_attrs:
                w_attr = Q.get_weapon_attr(weapon)
                effective_set = EFFECTIVE_AGAINST.get(w_attr, set())
                if not any(a in effective_set for a in target_armor_attrs):
                    alt_weapons = [w for w in getattr(player, 'weapons', [])
                                   if w and w.name != weapon.name]
                    if alt_weapons:
                        for alt_w in alt_weapons:
                            alt_attr = Q.get_weapon_attr(alt_w)
                            alt_effective = EFFECTIVE_AGAINST.get(alt_attr, set())
                            if any(a in alt_effective for a in target_armor_attrs):
                                weapon = alt_w
                                break

        # 武器切换后也检查蓄力（替代武器可能未蓄力）
        if (getattr(weapon, 'requires_charge', False)
                and getattr(weapon, 'charge_mandatory', True)
                and not getattr(weapon, 'is_charged', False)):
            if "special" in available:
                commands.append(f"special 蓄力{weapon.name}")
            return commands

        cmds = self.build_attack_cmd(player, target, weapon, state, available)
        commands.extend(cmds)
        debug_ai_attack_generation(player.name,
            weapon.name, f"攻击命令: {commands} (目标={target.name})")
        return commands

    # ════════════════════════════════════════════════════════
    #  攻击命令构建（近战/远程/范围）
    # ════════════════════════════════════════════════════════

    def build_attack_cmd(
        self, player: Any, target: Any, weapon: Any,
        state: Any, available: List[str],
    ) -> List[str]:
        """根据武器类型生成具体攻击指令（复制自 _build_attack_cmd）。"""
        Q = self._query
        commands: List[str] = []
        markers = getattr(state, 'markers', None)
        weapon_range = Q.get_weapon_range(weapon)

        if Q.is_in_savior_state(player) and weapon_range == "ranged":
            weapon_range = "melee"

        me_blocked, target_in_barrier = self._check_hologram_block(player, target, state)

        if weapon_range == "melee":
            # 全息影像区域内 cannot find → 目标同在就攻击，目标在外就离开
            if me_blocked:
                if target_in_barrier and "attack" in available:
                    layer, attr = self.pick_attack_layer(player, target, weapon)
                    if layer and attr:
                        commands.append(f"attack {target.name} {weapon.name} {layer} {attr}")
                    else:
                        commands.append(f"attack {target.name} {weapon.name}")
                    return commands
                if not target_in_barrier and "move" in available:
                    target_loc = Q.get_location_str(target)
                    if target_loc:
                        commands.append(f"move {target_loc}")
                    return commands
            is_engaged = False
            if markers and hasattr(markers, 'has_relation'):
                is_engaged = markers.has_relation(
                    player.player_id, "ENGAGED_WITH", target.player_id)
            if not is_engaged:
                markers_obj = getattr(state, 'markers', None)
                target_visible = True
                if markers_obj and hasattr(markers_obj, 'is_visible_to'):
                    target_visible = markers_obj.is_visible_to(
                        target.player_id, player.player_id,
                        getattr(player, 'has_detection', False))
                if not target_visible:
                    detection_cmds = self.build_detection(player, state, available)
                    commands.extend(detection_cmds)
                    return commands
                if "find" in available:
                    if Q.same_location(player, target):
                        commands.append(f"find {target.name}")
                        return commands
                    else:
                        target_loc = Q.get_location_str(target)
                        if target_loc and "move" in available:
                            commands.append(f"move {target_loc}")
                        commands.append(f"find {target.name}")
                        return commands
                else:
                    return commands
            if "attack" in available:
                layer, attr = self.pick_attack_layer(player, target, weapon)
                if layer and attr:
                    commands.append(f"attack {target.name} {weapon.name} {layer} {attr}")
                else:
                    commands.append(f"attack {target.name} {weapon.name}")

        elif weapon_range == "ranged":
            # 全息影像区域内 cannot lock → 目标同在就攻击，目标在外就离开
            if me_blocked:
                if target_in_barrier and "attack" in available:
                    layer, attr = self.pick_attack_layer(player, target, weapon)
                    if layer and attr:
                        commands.append(f"attack {target.name} {weapon.name} {layer} {attr}")
                    else:
                        commands.append(f"attack {target.name} {weapon.name}")
                    return commands
                if not target_in_barrier and "move" in available:
                    target_loc = Q.get_location_str(target)
                    if target_loc:
                        commands.append(f"move {target_loc}")
                    return commands
            is_locked = False
            if markers and hasattr(markers, 'has_relation'):
                is_locked = markers.has_relation(
                    target.player_id, "LOCKED_BY", player.player_id)
            if not is_locked:
                markers_obj = getattr(state, 'markers', None)
                target_visible = True
                if markers_obj and hasattr(markers_obj, 'is_visible_to'):
                    target_visible = markers_obj.is_visible_to(
                        target.player_id, player.player_id,
                        getattr(player, 'has_detection', False))
                if not target_visible:
                    detection_cmds = self.build_detection(player, state, available)
                    commands.extend(detection_cmds)
                    return commands
                if "lock" in available:
                    commands.append(f"lock {target.name}")
                    return commands
                else:
                    return commands
            if "attack" in available:
                layer, attr = self.pick_attack_layer(player, target, weapon)
                if layer and attr:
                    commands.append(f"attack {target.name} {weapon.name} {layer} {attr}")
                else:
                    commands.append(f"attack {target.name} {weapon.name}")

        elif weapon_range == "area":
            if "attack" in available:
                same_loc_targets = Q.get_same_location_targets(player, state)
                if same_loc_targets:
                    target_armor_attrs = Q.get_outer_armor_attr(target)
                    if not target_armor_attrs:
                        target_armor_attrs = Q.get_inner_armor_attr(target)
                    if target_armor_attrs:
                        w_attr = Q.get_weapon_attr(weapon)
                        effective_set = EFFECTIVE_AGAINST.get(w_attr, set())
                        if not any(a in effective_set for a in target_armor_attrs):
                            aoe_names = Q.get_all_aoe_weapon_names(player)
                            better_aoe = None
                            for aoe_name in aoe_names:
                                if aoe_name == weapon.name:
                                    continue
                                aoe_w = next((w for w in getattr(player, 'weapons', [])
                                              if w and w.name == aoe_name), None)
                                if not aoe_w:
                                    aoe_w = make_weapon(aoe_name)
                                if not aoe_w:
                                    continue
                                if (getattr(aoe_w, 'requires_charge', False)
                                        and getattr(aoe_w, 'charge_mandatory', True)
                                        and not getattr(aoe_w, 'is_charged', False)):
                                    continue
                                aoe_attr = Q.get_weapon_attr(aoe_w)
                                aoe_effective = EFFECTIVE_AGAINST.get(aoe_attr, set())
                                if any(a in aoe_effective for a in target_armor_attrs):
                                    better_aoe = aoe_w
                                    break
                            if better_aoe:
                                weapon = better_aoe
                    layer, attr = self.pick_attack_layer(player, target, weapon)
                    if layer and attr:
                        commands.append(f"attack {target.name} {weapon.name} {layer} {attr}")
                    else:
                        commands.append(f"attack {target.name} {weapon.name}")
                else:
                    target_loc = Q.get_location_str(target)
                    if target_loc and "move" in available:
                        commands.append(f"move {target_loc}")
                return commands
        else:
            if "attack" in available:
                layer, attr = self.pick_attack_layer(player, target, weapon)
                if layer and attr:
                    commands.append(f"attack {target.name} {weapon.name} {layer} {attr}")
                else:
                    commands.append(f"attack {target.name} {weapon.name}")
            return commands
        return commands

    # ════════════════════════════════════════════════════════
    #  换武器
    # ════════════════════════════════════════════════════════

    def build_rearm(
        self, player: Any, state: Any,
        strategy: Any, available: List[str],
        ctx: "OrchestratorContext",
    ) -> List[str]:
        """换武器逻辑（复制自 CombatMixin._cmd_rearm）。"""
        Q = self._query
        commands: List[str] = []
        loc = Q.get_location_str(player)
        if "interact" in available:
            interact_cmd = self._get_counter_weapon_interact_cmd(player, loc)
            if interact_cmd:
                commands.append(interact_cmd)
                return commands
        if "move" in available:
            dest = self._pick_counter_weapon_destination(player, state, loc)
            if dest and dest != loc:
                commands.append(f"move {dest}")
        return commands

    # ════════════════════════════════════════════════════════
    #  探测获取
    # ════════════════════════════════════════════════════════

    def build_detection(
        self, player: Any, state: Any, available: List[str],
    ) -> List[str]:
        """获取探测手段（复制自 CombatMixin._cmd_get_detection）。"""
        Q = self._query
        commands: List[str] = []
        loc = Q.get_location_str(player)
        has_detection = getattr(player, 'has_detection', False)
        if has_detection:
            return commands
        vouchers = getattr(player, 'vouchers', 0)
        has_pass = getattr(player, 'has_military_pass', False)
        if "interact" in available:
            if loc == "商店" and vouchers >= 1:
                commands.append("interact 热成像仪")
                return commands
            if loc == "魔法所":
                learned = Q.get_learned_spells(player)
                if "探测魔法" not in learned:
                    commands.append("interact 探测魔法")
                    return commands
            if loc == "军事基地" and has_pass:
                commands.append("interact 雷达")
                return commands
        if "move" in available:
            if loc != "魔法所":
                commands.append("move 魔法所")
            elif vouchers >= 1 and loc != "商店":
                commands.append("move 商店")
            elif has_pass and loc != "军事基地":
                commands.append("move 军事基地")
            else:
                if loc != "魔法所":
                    commands.append("move 魔法所")
        return commands

    # ════════════════════════════════════════════════════════
    #  武器选择
    # ════════════════════════════════════════════════════════

    def pick_weapon(self, player: Any, target: Any) -> Optional[Any]:
        """选择最佳武器（复制自 CombatMixin._pick_weapon）。"""
        Q = self._query
        weapons = getattr(player, 'weapons', [])
        if not weapons:
            return None
        pool = [w for w in weapons if w]
        pool = [w for w in pool if not getattr(w, '_hexagram_disabled', False)]
        if Q.is_in_savior_state(player):
            melee_and_area = [w for w in pool if Q.get_weapon_range(w) != "ranged"]
            if melee_and_area:
                pool = melee_and_area
        if not pool:
            return None
        target_outer_attrs = Q.get_outer_armor_attr(target)
        if not target_outer_attrs:
            target_outer_attrs = Q.get_inner_armor_attr(target)

        def weapon_score(w):
            s = 0
            dmg = Q.get_weapon_damage(w)
            if Q.is_in_savior_state(player):
                talent = getattr(player, 'talent', None)
                if Q.get_weapon_range(w) == "melee":
                    dmg += getattr(talent, 'temp_attack_bonus', 0)
                elif Q.get_weapon_range(w) == "area":
                    dmg += getattr(talent, 'aoe_bonus', 0)
            s += dmg * 10
            if Q.has_unused_mythland(target):
                wr_check = Q.get_weapon_range(w)
                if wr_check == "ranged":
                    s += 100
                elif wr_check == "melee":
                    s -= 60
                elif wr_check == "area":
                    s -= 40
            if w.name == "电磁步枪":
                if self._target_has_emr_immunity(target):
                    s -= 30
            if (getattr(w, 'requires_charge', False)
                    and getattr(w, 'charge_mandatory', True)
                    and not getattr(w, 'is_charged', False)):
                s -= 500
            w_attr = Q.get_weapon_attr(w)
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
            wr = Q.get_weapon_range(w)
            if Q.same_location(player, target):
                if wr == "melee":
                    s += 10
                elif wr == "area":
                    s += 5
            else:
                if wr == "ranged":
                    s += 15
                elif wr == "melee":
                    s -= 20
            if (hasattr(player, 'talent') and player.talent
                    and hasattr(player.talent, 'active') and player.talent.active
                    and hasattr(player.talent, 'name')
                    and player.talent.name == "请一直，注视着我"):
                if wr == "area":
                    s += 50
                elif wr == "melee":
                    s += 5
            tags = getattr(w, 'special_tags', []) or []
            has_control = any(t in tags for t in ("shock_2_targets", "stun_on_hit"))
            if has_control and Q.same_location(player, target):
                s += 15
            if Q.is_in_savior_state(target):
                wr_check = Q.get_weapon_range(w)
                if wr_check == "ranged":
                    s += 80
                elif wr_check == "melee":
                    savior_bonus = getattr(getattr(target, 'talent', None), 'temp_attack_bonus', 0)
                    if savior_bonus >= 2.0:
                        s -= 30
            return s

        pool.sort(key=weapon_score, reverse=True)
        return pool[0]

    # ════════════════════════════════════════════════════════
    #  攻击层选择
    # ════════════════════════════════════════════════════════

    def pick_attack_layer(
        self, player: Any, target: Any, weapon: Any,
    ) -> tuple:
        """选择攻击层和属性（复制自 CombatMixin._pick_attack_layer）。"""
        Q = self._query
        outer = Q.count_outer_armor(target)
        inner = Q.count_inner_armor(target)
        if outer == 0 and inner == 0:
            return (None, None)
        w_attr = Q.get_weapon_attr(weapon)
        if outer > 0:
            target_attrs = Q.get_outer_armor_attr(target)
            effective_set = EFFECTIVE_AGAINST.get(w_attr, set())
            if target_attrs:
                for a in target_attrs:
                    if a in effective_set:
                        return ("outer", str(a.value) if hasattr(a, 'value') else str(a))
                return ("outer", str(target_attrs[0].value) if hasattr(target_attrs[0], 'value') else str(target_attrs[0]))
            return ("outer", None)
        if inner > 0:
            target_attrs = Q.get_inner_armor_attr(target)
            if target_attrs:
                return ("inner", str(target_attrs[0].value) if hasattr(target_attrs[0], 'value') else str(target_attrs[0]))
            return ("inner", None)
        return (None, None)

    # ════════════════════════════════════════════════════════
    #  蓄力
    # ════════════════════════════════════════════════════════

    def build_charge(self, player: Any, available: List[str]) -> List[str]:
        """获取武器蓄力指令（复制自 orchestrator._get_charge_commands）。"""
        cmds: List[str] = []
        if "special" not in available:
            return cmds
        for w in getattr(player, 'weapons', []):
            if not w:
                continue
            if (getattr(w, 'requires_charge', False)
                    and getattr(w, 'charge_mandatory', True)
                    and not getattr(w, 'is_charged', False)):
                cmds.append(f"special 蓄力{w.name}")
        return cmds

    # ════════════════════════════════════════════════════════
    #  内部辅助
    # ════════════════════════════════════════════════════════

    def _pick_target_simple(self, player, state, ctx):
        """简化版目标选择（使用 ctx 中的 combat_target 作为优先目标）。"""
        if ctx.combat_target:
            ct = ctx.combat_target
            if hasattr(ct, 'is_alive') and ct.is_alive():
                return ct
        # fallback: 找最近的有效攻击目标
        Q = self._query
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if not target or not target.is_alive():
                continue
            if Q.is_valid_attack_target(player, target, state):
                return target
        return None

    def _get_counter_weapon_interact_cmd(self, player, loc) -> Optional[str]:
        """当前地点可 interact 获取的非普通属性武器。"""
        Q = self._query
        learned = Q.get_learned_spells(player)
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
            has_gauss = any(w.name == "高斯步枪" for w in player.weapons if w)
            has_emr = any(w.name == "电磁步枪" for w in player.weapons if w)
            if not has_gauss:
                return "interact 高斯步枪"
            if not has_emr:
                return "interact 电磁步枪"
            return None
        return None

    def _pick_counter_weapon_destination(self, player, state, loc) -> str:
        """选择去哪里获取非普通属性武器。"""
        from utils.attribute import Attribute
        Q = self._query
        vouchers = getattr(player, 'vouchers', 0)
        has_pass = getattr(player, 'has_military_pass', False)
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
        candidates = []
        if not has_magic:
            candidates.append("魔法所")
        if not has_tech and has_pass:
            candidates.append("军事基地")
        if not candidates:
            return "魔法所"
        if len(candidates) == 1:
            return candidates[0]
        enemies_magic = Q.count_enemies_at("魔法所", player, state)
        enemies_military = Q.count_enemies_at("军事基地", player, state)
        return "军事基地" if enemies_military <= enemies_magic else "魔法所"

    @staticmethod
    def _target_has_emr_immunity(target) -> bool:
        """检查目标是否有电磁步枪免疫。"""
        armor_obj = getattr(target, 'armor', None)
        if armor_obj and hasattr(armor_obj, 'get_all_active'):
            for a in armor_obj.get_all_active():
                if "immune_electric" in getattr(a, 'special_tags', []) and not a.is_broken:
                    return True
        return False

    @staticmethod
    def _check_hologram_block(player, target, state):
        """返回 (blocked: 被禁find/lock, target_in_hologram: 目标同在影像内)。
        全息影像内 find/lock 被禁，但同在影像内的目标自动面对面可直接攻击。"""
        for pid in getattr(state, 'player_order', []):
            p = state.get_player(pid)
            talent = getattr(p, 'talent', None) if p else None
            if not talent or not hasattr(talent, 'can_lock_or_find'):
                continue
            allowed, _ = talent.can_lock_or_find(player.player_id)
            if allowed:
                continue
            target_in = (
                talent.is_in_hologram(target.player_id)
                if target and hasattr(talent, 'is_in_hologram')
                else False
            )
            return True, target_in
        return False, False
