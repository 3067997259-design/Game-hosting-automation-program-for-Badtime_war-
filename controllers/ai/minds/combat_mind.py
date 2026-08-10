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
from engine.experiments import is_enabled as _is_exp_enabled

_LLM_AGGRESSION_TARGET_BIAS_SCALE = 0.2
_LLM_AGGRESSION_TARGET_BIAS_CLAMP = 60.0


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
        llm_aggression_mod: float = 0.0,
        players_who_attacked: Optional[Set[str]] = None,
        ctx=None,
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
        # 优先从 OrchestratorContext 读取，兼容旧调用方式
        if ctx is not None:
            threat_scores = threat_scores if threat_scores is not None else ctx.threat_scores
            combat_target = combat_target if combat_target is not None else ctx.combat_target
            in_combat = in_combat or ctx.in_combat
            police_protected_ids = police_protected_ids if police_protected_ids is not None else ctx.police_protected_ids
            police_stance = police_stance if police_stance is not None else ctx.police_stance
            llm_alliance = llm_alliance if llm_alliance is not None else ctx.llm_alliance
            terror_defense = terror_defense if terror_defense is not None else ctx.terror_defense
            star_follow_up_rounds = star_follow_up_rounds or ctx.star_follow_up_rounds
            llm_aggression_mod = llm_aggression_mod or ctx.llm_aggression_mod
            players_who_attacked = (
                players_who_attacked
                if players_who_attacked is not None
                else ctx.players_who_attacked
            )
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
            llm_aggression_mod=llm_aggression_mod,
            players_who_attacked=players_who_attacked or set(),
        )

        best_target = scored[0][0] if scored else None  # 元组 (target, score) → 取 target
        best_weapon = None
        combat_commands = []
        combat_ready = False
        all_countered = False

        if best_target:
            best_weapon = self._pick_weapon(player, best_target, police_protected_ids)
            all_countered = self._query.all_weapons_countered(player, best_target)
            if best_weapon and not all_countered:
                combat_ready = True
                combat_commands = self._build_attack_commands(
                    player, best_target, best_weapon, state
                )

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
        llm_aggression_mod: float = 0.0,
        players_who_attacked: Optional[Set[str]] = None,
    ) -> List:
        """对所有可攻击目标评分，返回 [(target, score)] 降序列表"""
        scored = []
        targets = []

        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if not target or not target.is_alive():
                continue
            if not self._query.is_valid_attack_target(player, target, state):
                continue
            # ★ 不在此处过滤可达性——近战武器可通过 move+find 攻击远处目标
            # _build_attack_commands 负责生成必要的 move 指令
            targets.append(target)

        threat_by_name = {
            target.name: threat_scores.get(target.name, 0)
            for target in targets
        }
        avg_threat = (
            sum(threat_by_name.values()) / len(targets)
            if targets else 0
        )

        for target in targets:
            score = self._score_target(
                player, target, state, strategy, threat_scores,
                combat_target, in_combat, police_protected,
                police_stance, police_mind,
                llm_alliance, terror_defense,
                star_follow_up_rounds=star_follow_up_rounds,
                avg_threat=avg_threat,
                llm_aggression_mod=llm_aggression_mod,
                players_who_attacked=players_who_attacked or set(),
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
        avg_threat: float = 0.0,
        llm_aggression_mod: float = 0.0,
        players_who_attacked: Optional[Set[str]] = None,
    ) -> float:
        """对单个目标评分，包含警察保护穿透判定 + 天星补刀加成。"""
        threat_score = threat_scores.get(target.name, 0)
        base = threat_score
        if llm_aggression_mod != 0.0:
            # 正值更愿意挑战高于平均威胁的目标；负值更倾向避开强敌。
            aggression_bias = (
                (threat_score - avg_threat)
                * llm_aggression_mod
                * _LLM_AGGRESSION_TARGET_BIAS_SCALE
            )
            aggression_bias = max(
                -_LLM_AGGRESSION_TARGET_BIAS_CLAMP,
                min(_LLM_AGGRESSION_TARGET_BIAS_CLAMP, aggression_bias),
            )
            base += aggression_bias

        # 当前战斗目标加分
        if in_combat and combat_target and target.player_id == combat_target.player_id:
            base += 50

        # ★ 天星补刀标记：同地点的石化/残血目标加分
        if star_follow_up_rounds > 0 and self._query.same_location(player, target):
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
        if self._query.same_location(player, target):
            base += 20

        # HP 低加分
        eff_hp = self._query.get_effective_hp(target)
        if eff_hp <= 1.0:
            base += 30
        elif eff_hp <= 2.0:
            base += 15

        # 无护甲加分
        outer = self._query.count_outer_armor(target)
        inner = self._query.count_inner_armor(target)
        if outer == 0 and inner == 0:
            base += 40
        elif outer == 0:
            base += 20

        # Strategy 调整
        target_power = self._query.estimate_power(target)
        is_passive = self._query.is_passive_player(target, state)
        base = strategy.modify_target_score(
            target, base, player,
            players_who_attacked=players_who_attacked or set(),
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
        if self._query.is_in_savior_state(player):
            melee_and_area = [w for w in weapons
                              if self._query.get_weapon_range(w) != "ranged"]
            if melee_and_area:
                weapons = melee_and_area

        if not weapons:
            return None

        target_attrs = self._query.get_outer_armor_attr(target) or self._query.get_inner_armor_attr(target)
        is_protected = police_protected_ids and target.player_id in police_protected_ids

        def score(w):
            if _is_exp_enabled("m8_ai"):
                # D1：净伤为基（属性差异已折进防御表），克制不再二元
                s = self._query.net_damage(player, w, target) * 10
            else:
                s = self._query.get_weapon_damage(w) * 10

            # 救世主加成
            if self._query.is_in_savior_state(player):
                talent = getattr(player, 'talent', None)
                if self._query.get_weapon_range(w) == "melee":
                    s += getattr(talent, 'temp_attack_bonus', 0) * 10
                elif self._query.get_weapon_range(w) == "area":
                    s += getattr(talent, 'aoe_bonus', 0) * 10

            # 未蓄力降权
            if (getattr(w, 'requires_charge', False)
                    and getattr(w, 'charge_mandatory', True)
                    and not getattr(w, 'is_charged', False)):
                s -= 500

            # 属性克制
            w_attr = self._query.get_weapon_attr(w)
            if not _is_exp_enabled("m8_ai") and target_attrs and w_attr in EFFECTIVE_AGAINST:
                effective_set = EFFECTIVE_AGAINST[w_attr]
                if any(a in effective_set for a in target_attrs):
                    s += 20
                else:
                    s -= 50

            # ★ 警察保护目标：AOE武器大幅度加分（AOE无视警察保护阈值）
            wr = self._query.get_weapon_range(w)
            if is_protected and wr == "area":
                s += 80

            # 射程适配
            if self._query.same_location(player, target):
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
        weapon_range = self._query.get_weapon_range(weapon)

        if self._query.is_in_savior_state(player) and weapon_range == "ranged":
            weapon_range = "melee"

        if weapon_range == "melee":
            is_engaged = (markers and hasattr(markers, 'has_relation')
                          and markers.has_relation(player.player_id, "ENGAGED_WITH", target.player_id))
            if not is_engaged:
                if not self._query.same_location(player, target):
                    target_loc = self._query.get_location_str(target)
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
            if self._query.same_location(player, target):
                commands.append(f"attack {target.name} {weapon.name}")
            else:
                target_loc = self._query.get_location_str(target)
                if target_loc:
                    commands.append(f"move {target_loc}")

        return commands

    # ════════════════════════════════════════════════════════
    #  警察保护穿透：天赋枚举
    # ════════════════════════════════════════════════════════

    def _check_can_damage_police_target(self, player, target, state, police_mind) -> bool:
        """综合判定能否对受警察保护的目标造成伤害。
        路径1：AOE武器（PoliceMind 判定）
        路径2：天赋穿透（T1/T3/G1/G4 枚举）"""
        # 路径1：通用AOE
        if police_mind:
            try:
                outer = self._query.get_outer_armor_attr(target)
                inner = self._query.get_inner_armor_attr(target)
                aoe_names = self._query.get_all_aoe_weapon_names(player)
                best_dmg = self._query.best_weapon_damage_raw(player)
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
        return self._can_bypass_police_via_talent(player, target, state)

    def _can_bypass_police_via_talent(self, player, target, state) -> bool:
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
            best_dmg = self._query.best_weapon_damage_raw(player)
            if best_dmg * 2.0 > threshold:
                return True

        # B2: 天星 — 有发动次数，自身即AOE
        if name == "天星" and getattr(talent, 'uses_remaining', 0) > 0:
            return True

        # B3: 火萤 — 无debuff时伤害×2，或有超新星
        if "火萤" in name:
            if not getattr(talent, 'debuff_started', False):
                best_dmg = self._query.best_weapon_damage_raw(player)
                if best_dmg * 2.0 > threshold:
                    return True
            if getattr(talent, 'has_supernova', False) or getattr(talent, 'supernova_charges', 0) > 0:
                return True

        # B4: 全息影像（G2）— 激活后所有警察被沉沦，保护无效
        if "注视" in name and getattr(talent, 'active', False):
            return True

        # B5: 神话之外（G3）— 未使用时可将同地点目标拉入结界
        if "神话之外" in name and getattr(talent, 'uses_remaining', 0) > 0:
            if self._query.same_location(player, target):
                return True

        # B6: 救世主 — is_savior + divinity >= 4
        if "愿负世" in name and getattr(talent, 'is_savior', False):
            if getattr(talent, 'divinity', 0) >= 4:
                return True

        return False
