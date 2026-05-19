"""
CombatMind —— 目标选择、武器选择、战斗可行性

职责：
- 从存活敌人中选出最优攻击目标
- 为选中的目标选择最佳武器
- 构建攻击指令序列（find/lock/attack）

纯函数分析器，不保持跨轮次状态。
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional, Set

from controllers.ai.strategies.base_strategy import DecisionPhase
from controllers.ai.minds.base import BaseMind, MindAssessment
from controllers.ai.constants import (
    EFFECTIVE_AGAINST, POLICE_AOE_WEAPONS,
    debug_ai_basic, debug_ai_attack_generation,
)


class CombatMind(BaseMind):
    """战斗分析器。"""

    def assess(
        self,
        player: Any,
        state: Any,
        strategy: Any,
        threat_scores: Optional[Dict[str, float]] = None,
        combat_target: Any = None,
        in_combat: bool = False,
        police_protected_ids: Optional[Set[str]] = None,
        police_stance: Optional[str] = None,
        police_mind: Any = None,
        llm_alliance: Optional[Set[str]] = None,
        terror_defense=None,
        star_follow_up_rounds: int = 0,
    ) -> MindAssessment:
        """分析战斗态势。

        Returns MindAssessment with data:
            - viable_targets: List[(target, score)] 可攻击目标（按评分降序）
            - best_target: Optional[Any] 推荐目标
            - best_weapon: Optional[Any] 为推荐目标选的最佳武器
            - combat_commands: List[str] 攻击指令序列
            - combat_ready: bool 是否可以发起攻击
            - all_countered: bool 所有武器是否被克制
        """
        threat_scores = threat_scores or {}
        police_protected = police_protected_ids or set()
        llm_alliance = llm_alliance or set()

        # 找到所有可攻击目标并评分
        scored = self._score_targets(
            player, state, strategy, threat_scores,
            combat_target, in_combat, police_protected,
            police_stance, police_mind,
            llm_alliance, terror_defense,
            star_follow_up_rounds=star_follow_up_rounds,
        )

        best_target = scored[0][0] if scored else None  # 元组 (target, score) → 取 target
        best_weapon = None
        combat_commands = []
        combat_ready = False
        all_countered = False

        if best_target:
            best_weapon = self._pick_weapon(player, best_target, police_protected_ids)
            if best_weapon:
                combat_ready = True
                combat_commands = self._build_attack_commands(
                    player, best_target, best_weapon, state
                )
            else:
                all_countered = self._all_weapons_countered(player, best_target)

        return MindAssessment(
            mind_name="combat",
            urgency=8 if combat_ready and in_combat else (6 if combat_ready else 0),
            phase=DecisionPhase.COMBAT,
            summary=f"战斗目标: {best_target.name if best_target else '无'} (武器: {best_weapon.name if best_weapon else '无'})",
            data={
                "viable_targets": [(t, s) for t, s in scored],
                "best_target": best_target,
                "best_weapon": best_weapon,
                "combat_commands": combat_commands,
                "combat_ready": combat_ready,
                "all_countered": all_countered,
            },
        )

    # ════════════════════════════════════════════════════════
    #  目标评分
    # ════════════════════════════════════════════════════════

    def _score_targets(
        self, player, state, strategy, threat_scores,
        combat_target, in_combat, police_protected,
        police_stance, police_mind,
        llm_alliance, terror_defense,
        star_follow_up_rounds: int = 0,
    ) -> List:
        """对所有可攻击目标评分，返回 [(target, score)] 降序列表"""
        scored = []

        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if not target or not target.is_alive():
                continue
            if not self._is_valid_target(player, target, state):
                continue
            # ★ 不在此处过滤可达性——近战武器可通过 move+find 攻击远处目标
            # _build_attack_commands 负责生成必要的 move 指令

            score = self._score_target(
                player, target, state, strategy, threat_scores,
                combat_target, in_combat, police_protected,
                police_stance, police_mind,
                llm_alliance, terror_defense,
                star_follow_up_rounds=star_follow_up_rounds,
            )
            if score > -999:
                scored.append((target, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    def _score_target(
        self, player, target, state, strategy, threat_scores,
        combat_target, in_combat, police_protected,
        police_stance, police_mind,
        llm_alliance, terror_defense,
        star_follow_up_rounds: int = 0,
    ) -> float:
        """对单个目标评分，包含警察保护穿透判定 + 天星补刀加成。"""
        base = threat_scores.get(target.name, 0)

        # 当前战斗目标加分
        if in_combat and combat_target and target.player_id == combat_target.player_id:
            base += 50

        # ★ 天星补刀标记：同地点的石化/残血目标加分
        if star_follow_up_rounds > 0 and self._same_location(player, target):
            is_petrified = getattr(target, 'is_petrified', False)
            is_low_hp = target.hp <= 1.0  # 天星伤害最低1.5，1HP以下是残血
            if is_petrified or is_low_hp:
                base += 50

        # ── 警察保护判定 ──
        is_protected = target.player_id in police_protected
        is_captain = getattr(target, 'is_captain', False)
        stance_is_resist = (police_stance == "resist")

        if is_protected:
            can_dmg = self._check_can_damage_police_target(player, target, state, police_mind)
            if is_captain and stance_is_resist:
                if can_dmg:
                    base += 80   # RESIST + 能打穿 → 队长是高价值目标
                else:
                    base -= 500  # RESIST + 打不了 → 别送死
            elif is_captain:
                # 非 RESIST 态度，队长无特殊待遇
                base -= 500 if not can_dmg else 30
            else:
                # 非队长受保护目标
                base -= 500 if not can_dmg else 30

        # 同地点加分
        if self._same_location(player, target):
            base += 20

        # HP 低加分
        eff_hp = self._get_effective_hp(target)
        if eff_hp <= 1.0:
            base += 30
        elif eff_hp <= 2.0:
            base += 15

        # 无护甲加分
        outer = self._count_armor(target, "outer")
        inner = self._count_armor(target, "inner")
        if outer == 0 and inner == 0:
            base += 40
        elif outer == 0:
            base += 20

        # Strategy 调整
        target_power = self._estimate_power(target)
        players_who_attacked = getattr(player, '_players_who_attacked', set()) if hasattr(player, '_players_who_attacked') else set()
        is_passive = self._is_passive_player(target, state)
        base = strategy.modify_target_score(
            target, base, player,
            players_who_attacked=players_who_attacked,
            is_passive=is_passive,
            target_power=target_power,
        )

        # LLM 同盟降权
        if target.name in llm_alliance:
            base -= 200

        # TerrorDefense 调整
        if terror_defense:
            try:
                base = terror_defense.modify_target_score(target, base)
            except Exception:
                pass

        return base

    # ════════════════════════════════════════════════════════
    #  武器选择
    # ════════════════════════════════════════════════════════

    def _pick_weapon(self, player, target, police_protected_ids: Optional[Set[str]] = None) -> Optional[Any]:
        """选择最佳武器。警察保护目标时优先AOE。"""
        weapons = [w for w in getattr(player, 'weapons', [])
                   if w and not getattr(w, '_hexagram_disabled', False)]
        if not weapons:
            return None

        # 救世主状态过滤远程
        if self._is_in_savior_state(player):
            melee_and_area = [w for w in weapons
                              if self._get_weapon_range(w) != "ranged"]
            if melee_and_area:
                weapons = melee_and_area

        if not weapons:
            return None

        target_attrs = self._get_outer_armor_attr(target) or self._get_inner_armor_attr(target)
        is_protected = police_protected_ids and target.player_id in police_protected_ids

        def score(w):
            s = self._get_weapon_damage(w) * 10

            # 救世主加成
            if self._is_in_savior_state(player):
                talent = getattr(player, 'talent', None)
                if self._get_weapon_range(w) == "melee":
                    s += getattr(talent, 'temp_attack_bonus', 0) * 10
                elif self._get_weapon_range(w) == "area":
                    s += getattr(talent, 'aoe_bonus', 0) * 10

            # 未蓄力降权
            if (getattr(w, 'requires_charge', False)
                    and getattr(w, 'charge_mandatory', True)
                    and not getattr(w, 'is_charged', False)):
                s -= 500

            # 属性克制
            w_attr = self._get_weapon_attr(w)
            if target_attrs and w_attr in EFFECTIVE_AGAINST:
                effective_set = EFFECTIVE_AGAINST[w_attr]
                if any(a in effective_set for a in target_attrs):
                    s += 20
                else:
                    s -= 50

            # ★ 警察保护目标：AOE武器大幅度加分（AOE无视警察保护阈值）
            wr = self._get_weapon_range(w)
            if is_protected and wr == "area":
                s += 80

            # 射程适配
            if self._same_location(player, target):
                if wr == "melee":
                    s += 10
                elif wr == "area":
                    s += 5
            else:
                if wr == "ranged":
                    s += 15
                elif wr == "melee":
                    s -= 20

            # 全息影像 AOE 偏好
            talent = getattr(player, 'talent', None)
            if talent and getattr(talent, 'active', False) and getattr(talent, 'name', '') == "请一直，注视着我":
                if wr == "area":
                    s += 50

            return s

        weapons.sort(key=score, reverse=True)
        return weapons[0] if weapons else None

    # ════════════════════════════════════════════════════════
    #  攻击指令构建
    # ════════════════════════════════════════════════════════

    def _build_attack_commands(
        self, player, target, weapon, state
    ) -> List[str]:
        """构建攻击指令序列"""
        commands = []
        markers = getattr(state, 'markers', None)
        weapon_range = self._get_weapon_range(weapon)

        if self._is_in_savior_state(player) and weapon_range == "ranged":
            weapon_range = "melee"

        if weapon_range == "melee":
            is_engaged = (markers and hasattr(markers, 'has_relation')
                          and markers.has_relation(player.player_id, "ENGAGED_WITH", target.player_id))
            if not is_engaged:
                if not self._same_location(player, target):
                    target_loc = self._get_location_str(target)
                    if target_loc:
                        commands.append(f"move {target_loc}")
                commands.append(f"find {target.name}")
            else:
                commands.append(f"attack {target.name} {weapon.name}")

        elif weapon_range == "ranged":
            is_locked = (markers and hasattr(markers, 'has_relation')
                         and markers.has_relation(target.player_id, "LOCKED_BY", player.player_id))
            if not is_locked:
                commands.append(f"lock {target.name}")
            else:
                commands.append(f"attack {target.name} {weapon.name}")

        elif weapon_range == "area":
            if self._same_location(player, target):
                commands.append(f"attack {target.name} {weapon.name}")
            else:
                target_loc = self._get_location_str(target)
                if target_loc:
                    commands.append(f"move {target_loc}")

        return commands

    # ════════════════════════════════════════════════════════
    #  工具方法
    # ════════════════════════════════════════════════════════

    @staticmethod
    def _is_valid_target(player, target, state) -> bool:
        if getattr(player, 'is_police', False) and not getattr(player, 'is_captain', False):
            if not getattr(target, 'is_criminal', False):
                return False
        if (target.talent and hasattr(target.talent, 'has_love_wish')
                and target.talent.has_love_wish(player.player_id)):
            return False
        if getattr(target, 'is_invisible', False) and not getattr(player, 'has_detection', False):
            markers_obj = getattr(state, 'markers', None)
            if markers_obj and hasattr(markers_obj, 'is_visible_to'):
                if not markers_obj.is_visible_to(target.player_id, player.player_id, getattr(player, 'has_detection', False)):
                    return False
        return True

    @staticmethod
    def _can_reach(player, target) -> bool:
        if CombatMind._same_location(player, target):
            return True
        for w in getattr(player, 'weapons', []):
            if w and CombatMind._get_weapon_range(w) in ("ranged", "area"):
                return True
        return False

    @staticmethod
    def _same_location(player, target) -> bool:
        return str(getattr(player, 'location', '')) == str(getattr(target, 'location', ''))

    @staticmethod
    def _get_location_str(p) -> str:
        return str(getattr(p, 'location', ''))

    @staticmethod
    def _get_weapon_range(weapon) -> str:
        return getattr(weapon, 'range', 'melee')

    @staticmethod
    def _get_weapon_damage(weapon) -> float:
        if not weapon:
            return 0.0
        if hasattr(weapon, 'get_effective_damage'):
            return weapon.get_effective_damage()
        return getattr(weapon, 'base_damage', 1.0)

    @staticmethod
    def _get_weapon_attr(weapon) -> Any:
        return getattr(weapon, 'attribute', None)

    @staticmethod
    def _get_outer_armor_attr(target) -> List:
        """获取目标所有活跃外层护甲的属性列表"""
        armor = getattr(target, 'armor', None)
        if armor and hasattr(armor, 'get_active'):
            from models.equipment import ArmorLayer
            return [a.attribute for a in armor.get_active(ArmorLayer.OUTER)]
        return []

    @staticmethod
    def _get_inner_armor_attr(target) -> List:
        """获取目标所有活跃内层护甲的属性列表"""
        armor = getattr(target, 'armor', None)
        if armor and hasattr(armor, 'get_active'):
            from models.equipment import ArmorLayer
            return [a.attribute for a in armor.get_active(ArmorLayer.INNER)]
        return []

    @staticmethod
    def _count_armor(player, layer: str) -> int:
        armor = getattr(player, 'armor', None)
        if armor and hasattr(armor, 'get_active'):
            from models.equipment import ArmorLayer
            layer_enum = {"outer": ArmorLayer.OUTER, "inner": ArmorLayer.INNER}.get(layer)
            if layer_enum is None:
                return 0
            return len(armor.get_active(layer_enum))
        return 0

    @staticmethod
    def _get_effective_hp(player) -> float:
        hp = player.hp
        talent = getattr(player, 'talent', None)
        if talent:
            temp_hp = getattr(talent, 'temp_hp', 0.0)
            if temp_hp > 0:
                hp += temp_hp
            charges = getattr(talent, 'ardent_wish_charges', 0)
            if charges > 0:
                hp += charges * 0.5
        return hp

    @staticmethod
    def _estimate_power(player) -> float:
        power = CombatMind._get_effective_hp(player) * 10
        for w in getattr(player, 'weapons', []):
            if w:
                power += CombatMind._get_weapon_damage(w) * 15
        outer = CombatMind._count_armor(player, "outer")
        inner = CombatMind._count_armor(player, "inner")
        power += outer * 20 + inner * 15
        return power

    @staticmethod
    def _is_in_savior_state(player) -> bool:
        talent = getattr(player, 'talent', None)
        return talent is not None and getattr(talent, 'is_savior', False)

    @staticmethod
    def _is_passive_player(target, state) -> bool:
        """判断目标是否是安静发育型玩家（当前无战斗关系即视为被动）"""
        markers = getattr(state, 'markers', None)
        if not markers:
            return True
        tid = getattr(target, 'player_id', None)
        if not tid:
            return True
        # 无 ENGAGED_WITH 关系 → 未与人面对面战斗
        engaged = markers.get_related(tid, "ENGAGED_WITH")
        if engaged:
            return False
        # 检查是否锁定他人（LOCKED_BY 的逆向：tid lock → other）
        for pid in getattr(state, 'player_order', []):
            if pid == tid:
                continue
            if markers.has_relation(pid, "LOCKED_BY", tid):
                return False
        return True

    @staticmethod
    def _all_weapons_countered(player, target) -> bool:
        weapons = [w for w in getattr(player, 'weapons', [])
                   if w and not getattr(w, '_hexagram_disabled', False)]
        if not weapons:
            return True
        target_attrs = CombatMind._get_outer_armor_attr(target) or CombatMind._get_inner_armor_attr(target)
        if not target_attrs:
            return False
        for w in weapons:
            w_attr = CombatMind._get_weapon_attr(w)
            effective_set = EFFECTIVE_AGAINST.get(w_attr, set())
            if any(a in effective_set for a in target_attrs):
                return False
        return True

    # ════════════════════════════════════════════════════════
    #  警察保护穿透：天赋枚举
    # ════════════════════════════════════════════════════════

    @staticmethod
    def _check_can_damage_police_target(player, target, state, police_mind) -> bool:
        """综合判定能否对受警察保护的目标造成伤害。
        路径1：AOE武器（PoliceMind 判定）
        路径2：天赋穿透（T1/T3/G1/G4 枚举）"""
        # 路径1：通用AOE
        if police_mind:
            try:
                from controllers.ai.minds.police_mind import PoliceMind as PM
                outer = CombatMind._get_outer_armor_attr(target)
                inner = CombatMind._get_inner_armor_attr(target)
                aoe_names = PM.get_aoe_weapon_names(player)
                best_dmg = CombatMind._best_weapon_damage_raw(player)
                can, _ = police_mind.can_damage_through_protection(
                    player, target, state,
                    talent_adjusted_damage=best_dmg,
                    outer_armor_attrs=outer,
                    inner_armor_attrs=inner,
                    aoe_weapon_names=aoe_names,
                    player_weapons=getattr(player, 'weapons', []),
                    learned_spells=getattr(player, 'learned_spells', set()),
                )
                if can:
                    return True
            except Exception:
                pass
        # 路径2：天赋穿透
        return CombatMind._can_bypass_police_via_talent(player, target, state)

    @staticmethod
    def _can_bypass_police_via_talent(player, target, state) -> bool:
        """检查玩家能否通过天赋绕过警察保护。"""
        talent = getattr(player, 'talent', None)
        if not talent:
            return False
        name = getattr(talent, 'name', '')

        pe = getattr(state, 'police_engine', None)
        if not pe:
            return True
        threshold = pe.get_protection_threshold(target.player_id)
        if threshold <= 0:
            return True

        # B1: 一刀缭断 — 有发动次数时伤害×2 > 阈值
        if "一刀缭断" in name and getattr(talent, 'uses_remaining', 0) > 0:
            best_dmg = CombatMind._best_weapon_damage_raw(player)
            if best_dmg * 2.0 > threshold:
                return True

        # B2: 天星 — 有发动次数，自身即AOE
        if name == "天星" and getattr(talent, 'uses_remaining', 0) > 0:
            return True

        # B3: 火萤 — 无debuff时伤害×2，或有超新星
        if "火萤" in name:
            if not getattr(talent, 'debuff_started', False):
                best_dmg = CombatMind._best_weapon_damage_raw(player)
                if best_dmg * 2.0 > threshold:
                    return True
            if getattr(talent, 'has_supernova', False) or getattr(talent, 'supernova_charges', 0) > 0:
                return True

        # B4: 全息影像（G2）— 激活后所有警察被沉沦，保护无效
        if "注视" in name and getattr(talent, 'active', False):
            return True

        # B5: 神话之外（G3）— 未使用时可将同地点目标拉入结界
        if "神话之外" in name and not getattr(talent, 'used', False):
            if CombatMind._same_location(player, target):
                return True

        # B6: 救世主 — is_savior + divinity >= 4
        if "愿负世" in name and getattr(talent, 'is_savior', False):
            if getattr(talent, 'divinity', 0) >= 4:
                return True

        return False

    @staticmethod
    def _best_weapon_damage_raw(player) -> float:
        """获取玩家最强武器的原始伤害（不考虑天赋加成）"""
        weapons = getattr(player, 'weapons', [])
        if not weapons:
            return 0.0
        best = 0.0
        for w in weapons:
            if not w:
                continue
            dmg = getattr(w, 'base_damage', 0)
            if isinstance(dmg, (int, float)) and dmg > best:
                best = float(dmg)
        for spell in getattr(player, 'learned_spells', set()):
            from models.equipment import make_weapon
            temp_w = make_weapon(spell)
            if temp_w:
                dmg = getattr(temp_w, 'base_damage', 0)
                if isinstance(dmg, (int, float)) and dmg > best:
                    best = float(dmg)
        return best
