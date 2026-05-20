"""
ThreatMind —— 威胁评估、危险判定、紧急事件检测

职责：
- 威胁排行（替代 _update_threat_assessment）
- 危险判定（替代 _is_critical）
- 紧急事件检测（病毒/Terror/超新星/救世主）
- 击杀机会判定（替代 _find_kill_target）

纯函数分析器：不保持任何跨轮次状态。
EMA衰减威胁分由 Orchestrator 管理，通过 previous_threat_scores 参数传入。
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional, Set

from controllers.ai.strategies.base_strategy import DecisionPhase
from controllers.ai.minds.base import BaseMind, MindAssessment
from controllers.ai.constants import (
    EFFECTIVE_AGAINST,
    debug_ai_basic,
    debug_ai_kill_opportunity,
)


class ThreatMind(BaseMind):
    """威胁评估分析器。

    使用方式：
        mind = ThreatMind(debug_name="AI_张三")
        assessment = mind.assess(player, state, strategy)
        if assessment.data.get("danger"):
            # 进入危险模式
    """

    # ════════════════════════════════════════════════════════
    #  主入口：assess
    # ════════════════════════════════════════════════════════

    def assess(
        self,
        player: Any,
        state: Any,
        strategy: Any,
        previous_threat_scores: Optional[Dict[str, float]] = None,
        low_threat_streak: Optional[Dict[str, int]] = None,
        been_attacked_by: Optional[Set[str]] = None,
        llm_aggression_mod: float = 0.0,
        polices_cache: Optional[Dict[str, Any]] = None,
        ctx=None,
    ) -> MindAssessment:
        """分析威胁态势。

        Args:
            player: 当前 AI 玩家
            state: 游戏状态
            strategy: 人格策略
            previous_threat_scores: 上一轮的 EMA 威胁分（跨轮次状态由 Orchestrator 维护）
            low_threat_streak: 安静发育者连击计数
            been_attacked_by: 本局攻击过我的玩家名集合
            llm_aggression_mod: LLM 攻击倾向调整（目标选择阶段按相对威胁使用）
            polices_cache: 警察态势缓存（来自 PoliceMind）
        Returns:
            MindAssessment with data containing:
                - threat_scores: {name: score} 更新后的威胁分
                - low_threat_streak: {name: count} 安静发育者连击
                - danger: bool 是否处于危险状态
                - virus_emergency: bool 是否需要紧急治疗病毒
                - supernova_threat: bool 是否存在超新星威胁
                - terror_info: Optional[dict] Terror目标信息 {target, reason}
                - savior_present: bool 场上有救世主
                - kill_targets: List[dict] 可击杀目标 [{target, damage}]
                - best_weapon_damage: float 最强武器伤害
                - threat_ranking: List[(name, score)] 威胁排行（降序）
        """
        # 优先从 OrchestratorContext 读取，兼容旧调用方式
        if ctx is not None:
            previous_threat_scores = previous_threat_scores if previous_threat_scores is not None else ctx.threat_scores
            low_threat_streak = low_threat_streak if low_threat_streak is not None else ctx.low_threat_streak
            been_attacked_by = been_attacked_by if been_attacked_by is not None else ctx.been_attacked_by
            llm_aggression_mod = llm_aggression_mod or ctx.llm_aggression_mod
            polices_cache = polices_cache if polices_cache is not None else ctx.police_cache
        threat_scores = dict(previous_threat_scores or {})
        streak = dict(low_threat_streak or {})

        # ── 计算威胁分 ──
        alive_names = set()
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if not target or not target.is_alive():
                if target and target.name in threat_scores:
                    del threat_scores[target.name]
                continue
            alive_names.add(target.name)

            power = self._query.estimate_power(target)

            # ★ 队长威胁度：纳入指挥的警察单位力量
            if getattr(target, 'is_captain', False):
                police_data = getattr(state, 'police', None)
                if police_data:
                    units = getattr(police_data, 'units', [])
                    alive = sum(1 for u in units if getattr(u, 'is_alive', lambda: False)())
                    active = sum(1 for u in units if getattr(u, 'is_alive', lambda: False)() and getattr(u, 'is_active', True))
                    power += alive * 15 + active * 10

            # 星野特殊威胁调整
            t_talent = getattr(target, 'talent', None)
            if t_talent and getattr(t_talent, 'name', '') == "大叔我啊，剪短发了":
                if getattr(t_talent, 'tactical_unlocked', False) and len(getattr(t_talent, 'ammo', [])) > 0:
                    power += 50
                elif not getattr(t_talent, 'tactical_unlocked', False):
                    power -= 20

            # EMA衰减
            existing = threat_scores.get(target.name, 0)
            updated = existing * 0.8 + power * 0.2
            threat_scores[target.name] = updated

        # ── 安静发育者检测 ──
        if len(alive_names) >= 2:
            alive_threats = {n: threat_scores.get(n, 0) for n in alive_names}
            min_threat = min(alive_threats.values()) if alive_threats else 0
            for name in alive_names:
                score = threat_scores.get(name, 0)
                if score <= min_threat + 1.0:
                    streak[name] = streak.get(name, 0) + 1
                else:
                    streak[name] = 0
                if streak.get(name, 0) >= 5:
                    threat_scores[name] = threat_scores.get(name, 0) + 15.0
            for name in list(streak.keys()):
                if name not in alive_names:
                    del streak[name]

        # ── 威胁排行 ──
        ranking = sorted(threat_scores.items(), key=lambda x: x[1], reverse=True)

        # ── 危险判定 ──
        danger = self._is_critical(
            player, state, strategy,
            polices_cache=polices_cache,
        )

        # ── 病毒应急 ──
        virus_emergency = self._needs_virus_cure(player, state)

        # ── 紧急事件检测 ──
        supernova_threat = self._detect_supernova_threat(player, state)
        terror_info = self._detect_terror_threat(player, state)
        savior_present = self._detect_savior_present(player, state)

        # ── 击杀机会 ──
        kill_targets = self._find_kill_targets(player, state)

        # ── 最强武器伤害 ──
        best_dmg = self._query.best_weapon_damage(player)

        return MindAssessment(
            mind_name="threat",
            urgency=10 if virus_emergency or danger else (8 if terror_info else 0),
            phase=(DecisionPhase.EMERGENCY_VIRUS if virus_emergency
                   else DecisionPhase.SURVIVAL if danger
                   else DecisionPhase.EMERGENCY_TERROR if terror_info
                   else DecisionPhase.COMBAT),
            summary=self._build_summary(virus_emergency, danger, terror_info, supernova_threat, savior_present),
            data={
                "threat_scores": threat_scores,
                "low_threat_streak": streak,
                "threat_ranking": ranking,
                "danger": danger,
                "virus_emergency": virus_emergency,
                "supernova_threat": supernova_threat,
                "terror_info": terror_info,
                "savior_present": savior_present,
                "kill_targets": kill_targets,
                "best_weapon_damage": best_dmg,
            },
        )

    def _build_summary(self, virus, danger, terror, supernova, savior) -> str:
        parts = []
        if virus:
            parts.append("病毒应急")
        if danger:
            parts.append("危险模式")
        if terror:
            parts.append(f"Terror威胁({terror['target'].name})")
        if supernova:
            parts.append("超新星威胁")
        if savior:
            parts.append("救世主在场")
        return ", ".join(parts) if parts else "正常"

    # ════════════════════════════════════════════════════════
    #  危险判定
    # ════════════════════════════════════════════════════════

    def _is_critical(
        self, player, state, strategy,
        polices_cache=None,
    ) -> bool:
        Q = self._query
        # 火萤自定义判定
        if Q.has_talent(player, "火萤IV型-完全燃烧"):
            return self._is_critical_firefly(player, state, polices_cache)

        # 星野自定义判定
        if Q.has_talent(player, "大叔我啊，剪短发了"):
            return self._is_critical_hoshino(player, state, polices_cache)

        # 队长危险判定：Strategy可额外标记危险，但不跳过基础判定
        if getattr(player, 'is_captain', False) and hasattr(strategy, 'assess_captain_danger'):
            if strategy.assess_captain_danger(
                player, state,
                police_cache=polices_cache or {},
                count_outer_armor_fn=Q.count_outer_armor,
            ):
                return True

        outer = Q.count_outer_armor(player)
        inner = Q.count_inner_armor(player)

        if player.hp <= 0.5:
            return True
        if player.hp <= 1.0 and outer == 0:
            return True

        pc = polices_cache or {}
        if pc.get("report_target") == player.player_id:
            if pc.get("report_phase", "idle") == "dispatched":
                return True

        locked = Q.count_locked_by(player, state)
        if locked >= 2 and (outer + inner) <= 1:
            return True

        if Q.is_anchored(player, state):
            return True

        # 被灼烧且无护甲
        for pid in state.player_order:
            p = state.get_player(pid)
            if (p and p.is_alive() and p.talent
                    and hasattr(p.talent, 'burn_targets')
                    and player.player_id in p.talent.burn_targets):
                if outer + inner == 0:
                    return True
                break

        return False

    def _is_critical_firefly(self, player, state, polices_cache) -> bool:
        Q = self._query
        pc = polices_cache or {}
        if pc.get("report_target") == player.player_id:
            if pc.get("report_phase", "idle") == "dispatched":
                return True
        if Q.is_anchored(player, state):
            return True
        if Q.firefly_debuff_active(player):
            return False
        outer = Q.count_outer_armor(player)
        if outer > 0:
            return False
        markers = getattr(state, 'markers', None)
        if markers:
            engaged = markers.get_related(player.player_id, "ENGAGED_WITH")
            for eid in engaged:
                enemy = state.get_player(eid)
                if enemy and enemy.is_alive():
                    enemy_best = Q.best_weapon_damage(enemy)
                    if enemy_best > 1.0:
                        return True
        locked = Q.count_locked_by(player, state)
        if locked > 1:
            return True
        return False

    def _is_critical_hoshino(self, player, state, polices_cache) -> bool:
        Q = self._query
        if player.hp <= 0.5:
            return True
        talent = getattr(player, 'talent', None)
        iron_horus_hp = getattr(talent, 'iron_horus_hp', 0) if talent else 0
        has_horus = iron_horus_hp > 0
        regular = Q.count_outer_armor(player) + Q.count_inner_armor(player)
        effective = regular + (2 if has_horus else 0)

        if player.hp <= 1.0 and effective == 0:
            return True
        pc = polices_cache or {}
        if pc.get("report_target") == player.player_id:
            if pc.get("report_phase", "idle") == "dispatched":
                return True
        locked = Q.count_locked_by(player, state)
        if locked >= 1 and effective <= 1:
            return True
        if Q.is_anchored(player, state):
            return True
        for pid in state.player_order:
            p = state.get_player(pid)
            if (p and p.is_alive() and p.talent
                    and hasattr(p.talent, 'burn_targets')
                    and player.player_id in p.talent.burn_targets):
                if effective <= 1:
                    return True
                break
        return False

    # ════════════════════════════════════════════════════════
    #  病毒检测
    # ════════════════════════════════════════════════════════

    def _needs_virus_cure(self, player, state) -> bool:
        virus = getattr(state, 'virus', None)
        if virus is None:
            return False
        if not getattr(virus, 'is_active', False):
            return False
        if self._query.has_virus_immunity(player):
            return False
        return True

    # ════════════════════════════════════════════════════════
    #  紧急事件检测
    # ════════════════════════════════════════════════════════

    def _detect_terror_threat(self, player, state) -> Optional[Dict]:
        """检测场上是否存在 Terror 或自我怀疑目标"""
        my_talent = getattr(getattr(player, 'talent', None), 'name', '')
        if my_talent == "大叔我啊，剪短发了":
            return None  # 星野自己不集火 Terror
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            t = state.get_player(pid)
            if not t or not t.is_alive():
                continue
            t_talent = getattr(t, 'talent', None)
            if not t_talent:
                continue
            if getattr(t_talent, 'is_terror', False):
                return {"target": t, "reason": "Terror"}
            if getattr(t_talent, 'self_doubt_pending', False):
                return {"target": t, "reason": "SelfDoubt"}
        return None

    def _detect_supernova_threat(self, player, state) -> bool:
        """检测场上是否存在超新星威胁"""
        if self._query.has_talent(player, "火萤IV型-完全燃烧"):
            return False  # 自己有火萤不怕超新星
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            t = state.get_player(pid)
            if not t or not t.is_alive():
                continue
            t_talent = getattr(t, 'talent', None)
            if t_talent and getattr(t_talent, 'name', '') == "火萤IV型-完全燃烧":
                # 检测是否持超新星
                for w in getattr(t, 'weapons', []):
                    if w and getattr(w, 'name', '') == "超新星过载":
                        return True
        return False

    def _detect_savior_present(self, player, state) -> bool:
        """检测场上是否有救世主"""
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            t = state.get_player(pid)
            if not t or not t.is_alive():
                continue
            t_talent = getattr(t, 'talent', None)
            if t_talent and getattr(t_talent, 'is_savior', False):
                return True
        return False

    # ════════════════════════════════════════════════════════
    #  击杀机会
    # ════════════════════════════════════════════════════════

    def _find_kill_targets(self, player, state) -> List[Dict]:
        """找到所有可击杀目标"""
        results = []
        best_dmg = self._query.best_weapon_damage(player)
        if best_dmg <= 0:
            return results
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if not target or not target.is_alive():
                continue
            if not self._query.is_valid_attack_target(player, target, state):
                continue
            outer = self._query.count_outer_armor(target)
            inner = self._query.count_inner_armor(target)
            eff_hp = self._query.get_effective_hp(target)
            if outer == 0 and inner == 0:
                if eff_hp <= best_dmg:
                    if self._query.can_attack_target(player, target, state):
                        debug_ai_kill_opportunity(player.name, target.name, eff_hp)
                        results.append({"target": target, "damage": best_dmg})
            elif outer == 0 and inner > 0:
                if eff_hp <= 0.5 and best_dmg >= 1.0:
                    if self._query.can_attack_target(player, target, state):
                        results.append({"target": target, "damage": best_dmg})
        return results
