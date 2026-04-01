"""EvaluationMixin —— 威胁评估、战力估算、阶段判定"""
from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional, Any, Dict
from controllers.ai.constants import EFFECTIVE_AGAINST, debug_ai_basic, debug_ai_kill_opportunity

if TYPE_CHECKING:
    from controllers.ai.controller import BasicAIController

_Base = BasicAIController if TYPE_CHECKING else object


class EvaluationMixin(_Base):

    # ════════════════════════════════════════════════════════
    #  危险判定
    # ════════════════════════════════════════════════════════

    def _is_critical_firefly(self, player, state) -> bool:
        """火萤IV型的危险判定：更激进，不轻易进入危险模式"""
        # 被警察围攻仍然算危险
        pc = self._police_cache or {}
        if pc.get("report_target") == player.player_id:
            phase = pc.get("report_phase", "idle")
            if phase == "dispatched":
                return True
        # 被锚定仍然算危险
        if self._is_anchored(player, state):
            return True
        if self._firefly_debuff_active(player):
            # debuff 已生效：不再因为没有护甲而陷入危险
            # 只有 hp <= 0.5 时才算危险（但火萤 0.5 不眩晕，T0 自愈到 1）
            # 所以实际上几乎不会进入危险模式
            return False
        else:
            # debuff 未生效：
            # 条件1：无护甲 + 对方（engaged_with 的人）有伤害>1的武器
            # 条件2：无护甲 + 被>1人锁定
            outer = self._count_outer_armor(player)
            if outer > 0:
                return False  # 有护甲就不危险
            # 无护甲时检查条件1：engaged_with 的对手有伤害>1武器
            markers = getattr(state, 'markers', None)
            if markers:
                engaged = markers.get_related(player.player_id, "ENGAGED_WITH")
                for eid in engaged:
                    enemy = state.get_player(eid)
                    if enemy and enemy.is_alive():
                        enemy_best_dmg = self._best_weapon_damage(enemy)
                        if enemy_best_dmg > 1.0:
                            return True
            # 无护甲时检查条件2：被>1人锁定
            locked_count = self._count_locked_by(player, state)
            if locked_count > 1:
                return True
            return False
    def _is_critical(self, player, state) -> bool:
        # 火萤IV型：自定义危险判定
        if self._has_firefly_talent(player):
            return self._is_critical_firefly(player, state)
        if player.hp <= 0.5:
                return True
        if player.hp <= 1.0 and self._count_outer_armor(player) == 0:
            return True
        # 被警察围攻
        pc = self._police_cache or {}
        if pc.get("report_target") == player.player_id:
            phase = pc.get("report_phase", "idle")
            if phase == "dispatched":
                return True
        # 被锁定且完全没有护甲
        locked_count = self._count_locked_by(player, state)
        if locked_count >= 1:
            total_armor = self._count_outer_armor(player) + self._count_inner_armor(player)
            if total_armor <= 1:
                return True
        # 被锚定
        if self._is_anchored(player, state):
            return True
        return False
    def _is_anchored(self, player, state) -> bool:
        markers = getattr(state, 'markers', None)
        if not markers or not hasattr(markers, 'has_relation'):
            return False
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            if markers.has_relation(player.player_id, "ANCHORED_BY", pid):
                return True
        # 备用检查
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if target and target.talent and hasattr(target.talent, 'is_anchoring'):
                if target.talent.is_anchoring(player):
                    return True
        return False
    # ════════════════════════════════════════════════════════
    #  击杀机会判定
    # ════════════════════════════════════════════════════════

    def _has_kill_opportunity(self, player, state) -> bool:
        """Bug15修复：击杀机会需考虑护甲"""
        best_dmg = self._best_weapon_damage(player)
        if best_dmg <= 0:
            return False
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if not target or not target.is_alive():
                continue
            # Bug15修复：必须考虑护甲
            outer_count = self._count_outer_armor(target)
            inner_count = self._count_inner_armor(target)
            # 只有在无护甲时，才比较 hp vs damage
            if outer_count == 0 and inner_count == 0:
                eff_hp = self._get_effective_hp(target)
                if eff_hp <= best_dmg:
                    if self._can_attack_target(player, target, state):
                        debug_ai_basic(player.name,
                            f"击杀机会: {target.name} HP={target.hp}(有效{eff_hp}) 无护甲 dmg={best_dmg}")
                        return True
            # 有护甲时，需要更高伤害穿透
            elif outer_count == 0 and inner_count > 0:
                # 外层清了只剩内层，如果伤害足够打破最后内层+hp
                if self._get_effective_hp(target) <= 0.5 and best_dmg >= 1.0:
                    if self._can_attack_target(player, target, state):
                        return True
        return False
    def _best_effective_weapon_damage(self, player, target) -> float:
        """返回能有效打击目标当前最外层护甲的最高武器伤害（考虑属性克制）"""
        weapons = getattr(player, 'weapons', [])
        if not weapons:
            return 0.0
        # 获取目标当前最外层护甲属性
        target_armor_attrs = self._get_outer_armor_attr(target)
        if not target_armor_attrs:
            target_armor_attrs = self._get_inner_armor_attr(target)
        best = 0.0
        for w in weapons:
            if not w:
                continue
            dmg = self._get_weapon_damage(w)
            # 火萤+100%伤害加成
            if self._has_firefly_talent(player):
                talent = getattr(player, 'talent', None)
                if talent and hasattr(talent, 'modify_outgoing_damage'):
                    mod = talent.modify_outgoing_damage(player, target, w, dmg)
                    if mod and "damage_multiplier_override" in mod:
                        dmg = dmg * mod["damage_multiplier_override"]
                    if mod and "bonus_damage" in mod:
                        dmg += mod["bonus_damage"]
            if target_armor_attrs:
                w_attr = self._get_weapon_attr(w)
                effective_set = EFFECTIVE_AGAINST.get(w_attr, set())
                if not any(a in effective_set for a in target_armor_attrs):
                    continue  # 这把武器被克制，跳过
            if dmg > best:
                best = dmg
        return best
    def _has_firefly_kill_opportunity(self, player, state) -> bool:
        """火萤专用击杀机会判定（更激进，但考虑护甲克制）"""
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if not target or not target.is_alive():
                continue
            if not self._can_attack_target(player, target, state):
                continue
            # 先检查是否所有武器都被克制
            if self._all_weapons_countered(player, target):
                continue
            # 用能有效打击的最高伤害来判断
            eff_dmg = self._best_effective_weapon_damage(player, target)
            if eff_dmg <= 0:
                continue
            outer = self._count_outer_armor(target)
            inner = self._count_inner_armor(target)
            eff_hp = self._get_effective_hp(target)
            # 无甲：伤害 >= HP
            if outer == 0 and inner == 0:
                if eff_hp <= eff_dmg:
                    return True
            # 1层外甲+无内甲：穿甲后溢出 >= HP（外甲通常1HP）
            if outer <= 1 and inner == 0 and eff_hp <= (eff_dmg - 1.0):
                return True
            # 无外甲+有内甲+低HP
            if outer == 0 and inner > 0 and eff_hp <= 0.5 and eff_dmg >= 1.0:
                return True
            # 总耐久度低于有效伤害的75%
            total_durability = outer + inner + eff_hp
            if total_durability <= eff_dmg * 0.75:
                return True
        return False
    # ════════════════════════════════════════════════════════
    #  战斗状态更新
    # ════════════════════════════════════════════════════════

    def _update_combat_status(self, player, state):
        markers = getattr(state, 'markers', None)
        current_target = None
        if markers and hasattr(markers, 'has_relation'):
            for pid in state.player_order:
                if pid == player.player_id:
                    continue
                target = state.get_player(pid)
                if target and target.is_alive():
                    if markers.has_relation(player.player_id, "ENGAGED_WITH", pid):
                        current_target = target
                        break
        if current_target:
            self._in_combat = True
            self._combat_target = current_target
            self._last_combat_location = self._get_location_str(player)
            self._combat_just_ended_at = None  # 正在战斗，清除结束标记
        else:
            if self._in_combat:
                # 战斗刚结束，记录结束地点
                self._combat_just_ended_at = self._last_combat_location
                debug_ai_basic(player.name, f"战斗结束于 {self._last_combat_location}")
            else:
                self._combat_just_ended_at = None  # 非战斗状态，不设标记
            self._in_combat = False
            self._combat_target = None

    # ════════════════════════════════════════════════════════
    #  战斗持续判定
    # ════════════════════════════════════════════════════════

    def _should_continue_combat(self, player, target) -> bool:
        if not target or not target.is_alive():
            return False

        # 火萤特判：只有武器被克制才退出
        if self._has_firefly_talent(player):
            if self._all_weapons_countered(player, target):
                return False
            # 火萤不因 HP 低而退出（有减伤 + 0.5 自愈）
            return True
        # aggressive：只有被打到无甲才撤退
        if self.personality == "aggressive":
            total_armor = self._count_outer_armor(player) + self._count_inner_armor(player)
            if total_armor == 0:
                return False
        else:
            # 其他人格：HP <= 0.5 时退出
            if player.hp <= 0.5:
                return False
        if self._is_at_disadvantage(player, target) and self.personality == "defensive":
            return False
        # 所有武器被目标护甲克制 → 退出近战
        if self._all_weapons_countered(player, target):
            return False
        # political 非 full_balanced 时不继续战斗（避免犯法），队长除外
        if (self.personality == "political"
                and not self._political_in_balanced_fallback
                and not getattr(player, 'is_captain', False)):
            return False
        return True

    def _is_at_disadvantage(self, player, target) -> bool:
        """是否处于劣势"""
        my_power = self._estimate_power(player)
        enemy_power = self._estimate_power(target)
        return my_power < enemy_power * 0.7

    # ════════════════════════════════════════════════════════
    #  伤害估算
    # ════════════════════════════════════════════════════════

    def _estimate_talent_adjusted_damage(self, player, weapon=None) -> float:
        """估算考虑天赋修正后的实际伤害（用于评估其他玩家的威胁）
        如果 weapon 为 None，则返回该玩家最强武器的天赋修正后伤害。
        """
        if weapon is not None:
            base_dmg = self._get_weapon_damage(weapon)
        else:
            # 找最强武器
            weapons = getattr(player, 'weapons', [])
            if not weapons:
                return 0.0
            base_dmg = max((self._get_weapon_damage(w) for w in weapons if w), default=0.0)
        talent = getattr(player, 'talent', None)
        if not talent:
            return base_dmg
        # 火萤IV型：所有伤害×2
        if hasattr(talent, 'name') and talent.name == "火萤IV型-完全燃烧":
            return base_dmg * 2.0
        # 救世主状态：近战+temp_attack_bonus
        if hasattr(talent, 'is_savior') and talent.is_savior:
            bonus = getattr(talent, 'temp_attack_bonus', 0.0)
            aoe_bonus = getattr(talent, 'aoe_bonus', 0.0)
            if weapon and self._get_weapon_range(weapon) == "area":
                return base_dmg + aoe_bonus
            return base_dmg + bonus
        # 一刀缭断：有使用次数时近战×2（共2次）
        # 不在这里加成，因为是有限次数的爆发
        return base_dmg

    def _best_weapon_damage(self, player) -> float:
        """获取玩家最强武器的伤害值"""
        weapons = getattr(player, 'weapons', [])
        if not weapons:
            return 0.0
        best = 0.0
        for w in weapons:
            if not w:
                continue
            dmg = self._estimate_talent_adjusted_damage(player, w)
            if dmg > best:
                best = dmg
        return best
    # ════════════════════════════════════════════════════════
    #  生命值与战力估算
    # ════════════════════════════════════════════════════════

    def _get_effective_hp(self, player) -> float:
        """获取玩家的有效生命值（含天赋额外HP）
        - 愿负世：救世主状态的临时HP
        - 火萤IV型：炽愿层数 × 0.5
        """
        hp = player.hp
        talent = getattr(player, 'talent', None)
        if talent:
            # 愿负世：救世主临时HP
            temp_hp = getattr(talent, 'temp_hp', 0.0)
            if temp_hp > 0:
                hp += temp_hp
            # 火萤IV型：炽愿额外HP
            charges = getattr(talent, 'ardent_wish_charges', 0)
            if charges > 0:
                hp += charges * 0.5
        return hp
    def _estimate_power(self, player) -> float:
        """估算玩家战力"""
        power = 0.0
        power += self._get_effective_hp(player) * 10
        weapons = getattr(player, 'weapons', [])
        for w in weapons:
            power += self._estimate_talent_adjusted_damage(player, w) * 15 if w else 0
        outer = self._count_outer_armor(player)
        inner = self._count_inner_armor(player)
        power += outer * 20
        power += inner * 15
        if self._has_stealth(player):
            power += 10
        if getattr(player, 'has_detection', False):
            power += 5
        return power
    # ════════════════════════════════════════════════════════
    #  威胁评估
    # ════════════════════════════════════════════════════════

    def _update_threat_scores(self, player, state):
        """更新威胁分数（_update_threat_assessment的别名）"""
        self._update_threat_assessment(player, state)
    def _cleanup_dead_players(self, state):
        """清理已死亡玩家的相关数据"""
        dead_names = []
        for pid in state.player_order:
            target = state.get_player(pid)
            if target and not target.is_alive():
                dead_names.append(target.name)
        for name in dead_names:
            if name in self._threat_scores:
                del self._threat_scores[name]
            self._been_attacked_by.discard(name)
    def _count_locked_by(self, player, state) -> int:
        """计算有多少人锁定了自己"""
        count = 0
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if target and target.is_alive():
                locked = getattr(target, 'locked_target', None)
                if locked and (locked == player.name or locked == player.player_id):
                    count += 1
        return count
    def _update_threat_assessment(self, player, state):
        """更新威胁评估"""
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if not target or not target.is_alive():
                if target and target.name in self._threat_scores:
                    del self._threat_scores[target.name]
                continue
            power = self._estimate_power(target)
            existing = self._threat_scores.get(target.name, 0)
            # 衰减历史威胁 + 新威胁
            self._threat_scores[target.name] = existing * 0.8 + power * 0.2
        # 检测安静发育者：连续多轮处于最低威胁的玩家
        alive_threats = {
            name: score for name, score in self._threat_scores.items()
            if any(
                state.get_player(p) and state.get_player(p).is_alive()
                and state.get_player(p).name == name
                for p in state.player_order
            )
        }
        if len(alive_threats) >= 2:
            min_threat = min(alive_threats.values())
            for name, score in alive_threats.items():
                if score <= min_threat + 1.0:
                    self._low_threat_streak[name] = self._low_threat_streak.get(name, 0) + 1
                else:
                    self._low_threat_streak[name] = 0
                if self._low_threat_streak.get(name, 0) >= 5:
                    self._threat_scores[name] = self._threat_scores.get(name, 0) + 15.0
        # 清理死亡玩家
        for name in list(self._low_threat_streak.keys()):
            if name not in alive_threats:
                del self._low_threat_streak[name]

    # ════════════════════════════════════════════════════════
    #  危险解除与应急发育
    # ════════════════════════════════════════════════════════

    def _is_danger_resolved(self, player) -> bool:
        """判断危险状态是否已解除"""
        if self._is_critical(player, self._game_state):
            return False
        # 火萤 debuff 生效后不要求护甲来解除危险
        if self._has_firefly_talent(player) and self._firefly_debuff_active(player):
            return True
        total_armor = self._count_outer_armor(player) + self._count_inner_armor(player)
        return total_armor >= 2
    def _cmd_danger_develop(self, player, state, available: List[str]) -> List[str]:
        """危险模式下的发育：在当前地点拿护甲，然后移动到远离当前位置的安全地点"""
        commands = []
        loc = self._get_location_str(player)
        outer = self._count_outer_armor(player)
        inner = self._count_inner_armor(player)
        vouchers = getattr(player, 'vouchers', 0)
        # 1) 当前地点能拿到护甲就拿
        if "interact" in available:
            if loc == "home" or self._is_at_home(player):
                if outer == 0 and not self._has_armor_by_name(player, "盾牌"):
                    commands.append("interact 盾牌")
            elif loc == "商店":
                if vouchers >= 1 and outer < 2 and not self._has_armor_by_name(player, "陶瓷护甲"):
                    commands.append("interact 陶瓷护甲")
                if vouchers < 1:
                    commands.append("interact 打工")
                if not self._has_virus_immunity(player) and getattr(state, 'virus', None) and getattr(state.virus, 'is_active', False):
                    commands.insert(0, "interact 防毒面具")  # 插到最前面
            elif loc == "魔法所":
                learned = self._get_learned_spells(player)
                if "魔法护盾" not in learned and outer < 2:
                    commands.append("interact 魔法护盾")
            elif loc == "医院":
                if inner == 0:
                    commands.append("interact 晶化皮肤手术")
                if not self._has_virus_immunity(player) and getattr(state, 'virus', None) and getattr(state.virus, 'is_active', False):
                    if vouchers >= 1:
                        commands.insert(0, "interact 防毒面具")
                    else:
                        commands.insert(0, "interact 打工")  # 先打工拿凭证
                if vouchers < 1:
                    commands.append("interact 打工")
            elif loc == "军事基地":
                has_pass = getattr(player, 'has_military_pass', False)
                if has_pass and outer < 2 and not self._has_armor_by_name(player, "AT力场"):
                    commands.append("interact AT力场")
        # 2) 移动到安全且能拿护甲的地方（优先远离当前位置）
        if "move" in available:
            dest = self._pick_safe_armor_destination(player, state)
            if dest and dest != loc:
                commands.append(f"move {dest}")
        return commands
    def _needs_virus_cure(self, player, state) -> bool:
        """Bug2修复：防御 state.virus 为 None"""
        virus = getattr(state, 'virus', None)
        if virus is None:
            return False
        if not getattr(virus, 'is_active', False):
            return False
        if self._has_virus_immunity(player):
            return False
        return True

    def _pick_safe_armor_destination(self, player, state) -> Optional[str]:
        """危险模式下选择目的地：安全 + 能拿护甲"""
        loc = self._get_location_str(player)
        outer = self._count_outer_armor(player)
        inner = self._count_inner_armor(player)
        vouchers = getattr(player, 'vouchers', 0)
        has_pass = getattr(player, 'has_military_pass', False)
        # 候选地点：能拿到护甲的地方
        armor_locations = []
        if outer < 1 and loc != "home":
            armor_locations.append("home")  # 盾牌
        if outer < 2 and loc != "商店":
            armor_locations.append("商店")  # 陶瓷护甲
        if outer < 2 and loc != "魔法所":
            armor_locations.append("魔法所")  # 魔法护盾
        if inner < 1 and loc != "医院":
            armor_locations.append("医院")  # 手术
        if has_pass and outer < 2 and loc != "军事基地":
            armor_locations.append("军事基地")  # AT力场
        if not armor_locations:
            # 没有需要护甲的地方，找最安全的地方
            return self._find_safe_location(player, state)
        # 按敌人数排序，选最安全的
        scored = []
        for dest in armor_locations:
            enemies = self._count_enemies_at(dest, player, state)
            scored.append((dest, enemies))
        scored.sort(key=lambda x: x[1])
        return scored[0][0]
