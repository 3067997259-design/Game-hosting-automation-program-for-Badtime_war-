"""
DecisionOrchestrator —— 新架构的核心决策编排器

职责：
- 替代旧 _generate_candidates() 850行瀑布流
- 协调 Strategy、Mind、GoalStack、TalentHook 的协作
- 按 Strategy.get_phase_order() 逐阶段决策
- 每个阶段检查 Mind 评估 → 检查 GoalStack → 产出指令

设计原则：
1. 所有状态（threat_scores, danger_mode, in_combat）由 Orchestrator 维护
2. Mind 是纯函数，只分析不记忆
3. GoalStack 提供跨轮次持久意图
4. Strategy 提供性格差异化的阈值和优先级

调试级别（读取 engine.debug_config.DebugConfig.level）：
  0 = 关闭
  1 = 阶段追踪（哪些阶段产出/跳过了命令）
  2 = 阶段详情（包含 Mind 关键数据）
  3 = 完整 MindAssessment 导出

与旧架构完全独立：C7 后作为唯一决策管道（`new_arch_enabled` 开关已退役）。
"""

from __future__ import annotations
from typing import Any, Callable, Dict, List, Optional, Set

from controllers.ai.strategies.base_strategy import DecisionPhase
from controllers.ai.minds.police_mind import PoliceStance
from controllers.ai.game_query import GameQuery
from controllers.ai.context import OrchestratorContext
from engine.m9.text import m9_text


def _read_m9_police_summary(state: Any) -> Dict[str, Any]:
    """M9 警务摘要（m9_police → police_cache 兼容 dict）。

    minds 仍按 v2exp dict 形状读取（cases/leader/captain/disabled 等键），
    但值来自 m9_police 状态机；无 m9_police 时返回空 dict（minds 降级处理）。
    """
    police = getattr(state, "m9_police", None)
    if police is None:
        return {}
    summary: Dict[str, Any] = {"m9_police": True}
    try:
        summary["cases"] = len(getattr(police, "cases", []) or [])
        wanted = police.open_wanted()
        summary["wanted"] = getattr(wanted, "suspect_id", None) \
            if wanted is not None else None
        summary["lead"] = police.lead_id
        summary["captain"] = police.captain_id
        summary["disabled"] = police.is_disabled()
        roster = getattr(police, "_roster", []) or []
        summary["has_police"] = bool(roster)
        summary["roster"] = [
            {"unit_id": u.unit_id, "location": getattr(u, "location", None),
             "id": u.unit_id, "alive": u.is_alive(),
             "is_alive": u.is_alive(), "is_active": u.is_active(),
             "active": u.is_active(), "weapon": u.weapon_name}
            for u in roster
        ]
    except Exception:
        pass
    return summary
from controllers.ai.command_builder import (
    CombatCommandBuilder, DevelopCommandBuilder, PoliceCommandBuilder,
)
from controllers.ai.constants import (
    debug_ai_basic, debug_ai_development_plan,
    debug_ai_combat_state, debug_ai_candidate_commands,
    POLICE_AOE_WEAPONS,
)


class DecisionOrchestrator:
    """新架构决策编排器。

    使用方式：
        orch = DecisionOrchestrator(
            strategy=strategy,
            goal_stack=goal_stack,
            talent_hooks=talent_hooks,
            minds=minds,
            controller=controller,  # 用于访问旧 helper 方法
        )
        candidates = orch.generate(player, state, available_actions, round_num)
    """

    def __init__(
        self,
        strategy: Any,
        goal_stack: Any,
        talent_hooks: Dict[str, Any],
        minds: List[Any],
        controller: Any,  # BasicAIController 实例，仅保留旧 choose/on_event 所需最小上下文
        ai_state: Any = None,
        query: GameQuery = None,
        personality: str = "balanced",
    ):
        self._strategy = strategy
        self._goal_stack = goal_stack
        self._talent_hooks = talent_hooks
        self.talent_hook_resolver = None  # (profile, slot_id) 解析器（controller 注入）
        self._minds = minds
        if ai_state is None:
            from controllers.ai.ai_state import AIState
            ai_state = AIState()
        self._ctrl = controller
        self._shared_state = ai_state  # AIState 共享引用（Phase 6）
        self._personality = personality
        self._player_name = "?"

        # 新架构基础设施
        self._query = query or GameQuery()
        self._combat_cmd = CombatCommandBuilder(self._query)
        self._develop_cmd = DevelopCommandBuilder(self._query)
        self._police_cmd = PoliceCommandBuilder(self._query)

        # T3 天星补刀追踪
        self._star_prev_uses: Optional[int] = None   # 上轮剩余次数（检测是否刚发动）

    @property
    def _threat_scores(self):
        return self._shared_state.threat_scores

    @_threat_scores.setter
    def _threat_scores(self, value):
        self._shared_state.threat_scores = value

    @property
    def _low_threat_streak(self):
        return self._shared_state.low_threat_streak

    @_low_threat_streak.setter
    def _low_threat_streak(self, value):
        self._shared_state.low_threat_streak = value

    @property
    def _been_attacked_by(self):
        return self._shared_state.been_attacked_by

    @_been_attacked_by.setter
    def _been_attacked_by(self, value):
        self._shared_state.been_attacked_by = value

    @property
    def _in_combat(self):
        return self._shared_state.in_combat

    @_in_combat.setter
    def _in_combat(self, value):
        self._shared_state.in_combat = value

    @property
    def _combat_target(self):
        return self._shared_state.combat_target

    @_combat_target.setter
    def _combat_target(self, value):
        self._shared_state.combat_target = value

    @property
    def _danger_mode(self):
        return self._shared_state.danger_mode

    @_danger_mode.setter
    def _danger_mode(self, value):
        self._shared_state.danger_mode = value

    @property
    def _last_combat_location(self):
        return self._shared_state.last_combat_location

    @_last_combat_location.setter
    def _last_combat_location(self, value):
        self._shared_state.last_combat_location = value

    @property
    def _combat_just_ended_at(self):
        return self._shared_state.combat_just_ended_at

    @_combat_just_ended_at.setter
    def _combat_just_ended_at(self, value):
        self._shared_state.combat_just_ended_at = value

    @property
    def _star_follow_up_rounds(self):
        return self._shared_state.star_follow_up_rounds

    @_star_follow_up_rounds.setter
    def _star_follow_up_rounds(self, value):
        self._shared_state.star_follow_up_rounds = value

    def _cleanup_dead_players(self, state):
        dead_names = []
        for pid in state.player_order:
            target = state.get_player(pid)
            if target and not target.is_alive():
                dead_names.append(target.name)
        for name in dead_names:
            self._shared_state.threat_scores.pop(name, None)
            self._shared_state.low_threat_streak.pop(name, None)
            self._shared_state.been_attacked_by.discard(name)
            self._shared_state.players_who_attacked.discard(name)

    def _sync_combat_status_from_markers(self, player, state):
        """复刻旧 _update_combat_status：用 ENGAGED_WITH 标记维护战斗状态。"""
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
            self._last_combat_location = GameQuery.get_location_str(player)
            self._combat_just_ended_at = None
        else:
            if self._in_combat:
                location = self._last_combat_location or GameQuery.get_location_str(player)
                self._combat_just_ended_at = location
                debug_ai_basic(player.name, f"战斗结束于 {location}")
            else:
                self._combat_just_ended_at = None
            self._in_combat = False
            self._combat_target = None

    def _build_ctx(self, state=None) -> OrchestratorContext:
        """从 AIState 构建上下文快照供 Mind / CommandBuilder 使用。"""
        s = self._shared_state
        police_protected = GameQuery.get_police_protected_ids(state) if state else set()
        return OrchestratorContext(
            threat_scores=dict(s.threat_scores),
            low_threat_streak=dict(s.low_threat_streak),
            been_attacked_by=set(s.been_attacked_by),
            players_who_attacked=set(s.players_who_attacked),
            in_combat=s.in_combat,
            combat_target=s.combat_target,
            danger_mode=s.danger_mode,
            llm_aggression_mod=s.llm_aggression_mod,
            llm_alliance=set(s.llm_alliance),
            star_follow_up_rounds=s.star_follow_up_rounds,
            terror_defense=s.terror_defense,
            police_protected_ids=police_protected,
            police_stance=None,
            police_cache=s.police_cache or {},
            political_fallback_level=s.political_fallback_level,
            personality=self._personality,
            police_dev_assignments=s.police_dev_assignments,
            police_dev_initialized=s.police_dev_initialized,
            last_criminal_target_id=s.last_criminal_target_id,
            aoe_acquisition_window=s.aoe_acquisition_window,
            ai_state=s,
        )

    # ════════════════════════════════════════════════════════
    #  调试辅助
    # ════════════════════════════════════════════════════════

    @staticmethod
    def _get_debug_level() -> int:
        try:
            from engine.debug_config import get_debug_level as _get_dbg_lvl
            return _get_dbg_lvl()
        except Exception:
            return 0

    def _dbg(self, level: int, msg: str):
        """编排器分级调试输出，与旧系统共享调试开关"""
        try:
            from engine.debug_config import DebugConfig
            if not DebugConfig.should_show(level):
                return
        except Exception:
            return
        name = self._player_name
        prefix = {1: "  [Orch]", 2: "  [Orch·]", 3: "  [Orch··]"}.get(level, "  [Orch]")
        print(f"{prefix} {name}: {msg}")

    def _finish_generate(self, candidates: List[str]) -> List[str]:
        """结束决策时把 AIState 暴露给遗留只读路径。"""
        expose = getattr(self._ctrl, '_expose_ai_state_for_legacy_views', None)
        if expose:
            expose()
        return candidates

    # ════════════════════════════════════════════════════════
    #  主入口
    # ════════════════════════════════════════════════════════

    def generate(
        self, player: Any, state: Any,
        available_actions: List[str], round_num: int,
    ) -> List[str]:
        """产出候选指令列表（与旧 _generate_candidates 同签名）。"""

        # Step 0: 更新本轮上下文；Controller 旧字段仅保留 choose/on_event 所需最小集合。
        self._player_name = player.name
        self._ctrl._my_id = player.player_id
        self._ctrl.player_name = player.name
        self._ctrl._player = player
        self._ctrl._game_state = state
        self._ctrl._round_number = round_num

        s = self._shared_state
        s.round_number = round_num
        # 决策快照：供策略/minds 只读消费 M9 世界事实（含被毁地点过滤）
        from controllers.ai.decision.snapshot import DecisionSnapshot
        self._snapshot = DecisionSnapshot.build(player, state)
        # 警务上下文：M9 读 m9_police 摘要；v2exp 走 GameQuery.read_police_state
        from engine.m9.gate import m9_enabled
        if m9_enabled(state):
            s.police_cache = _read_m9_police_summary(state)
            # police_cache 是全局摘要；按当前玩家补个人队长视图
            s.police_cache["is_captain"] = (
                s.police_cache.get("captain") == player.player_id)
        else:
            s.police_cache = self._query.read_police_state(state, player.player_id)
        s.political_fallback_level = (
            self._query.political_should_fallback(player, state)
            if self._strategy.supports_political_fallback() else "none"
        )
        self._cleanup_dead_players(state)
        self._sync_combat_status_from_markers(player, state)

        # ★ T3 天星补刀追踪：每轮递减，检测发动时重置为2
        if self._star_follow_up_rounds > 0:
            self._star_follow_up_rounds -= 1
        talent = getattr(player, 'talent', None)
        if talent and getattr(talent, 'name', '') == "天星":
            current_uses = getattr(talent, 'uses_remaining', 2)
            if self._star_prev_uses is not None and current_uses < self._star_prev_uses:
                self._star_follow_up_rounds = 2  # 天星刚发动 → 接下来2轮补刀
            self._star_prev_uses = current_uses

        # ★ 结界内过滤：移除 move、interact、find（结界内只能攻击同地点目标）
        available_actions = self._filter_barrier_actions(player, state, available_actions)

        my_loc = GameQuery.get_location_str(player)
        self._dbg(1, f"R{round_num} 开始决策 | 位置={my_loc} | 可用: {available_actions}")

        # Step 0: 未起床
        if not player.is_awake:
            self._dbg(1, "未起床 → wake")
            return self._finish_generate(["wake"])

        # Step 0.5: 结构性反制必须先于自身天赋 hook。真实 M9 对局中人人有
        # 天赋；若先让 hook 接管，被 G3 捕捉者会直接提前返回，永远走不到末尾
        # 的 counter 层（此前 500 局破界候选恒为 0）。
        try:
            from controllers.ai.counter import counter_candidates
            early_counters = counter_candidates(
                player, self._snapshot, list(available_actions), state=state)
            barrier_counters = [
                cmd for cmd in early_counters if "破界" in str(cmd)
            ]
            if barrier_counters:
                self._dbg(1, m9_text(
                    "ai.orchestrator.counter_priority",
                    counters=barrier_counters))
                return self._finish_generate(
                    self._dedup(barrier_counters + ["forfeit"]))
        except Exception:
            pass

        # Step 1: 天赋钩子接管
        override = self._check_talent_overrides(player, state, available_actions)
        if override is not None:
            self._dbg(1, f"天赋钩子接管 → {override}")
            return self._finish_generate(override)

        # Step 2: 运行所有 Mind，收集态势快照（复用 generate 已建的决策快照）
        snapshots = self._run_all_minds(player, state, projected=self._snapshot)
        self._dbg_mind_snapshots(snapshots)

        # ★ 战斗结束窗口管理：使用 combat_just_ended_at 信号
        self._manage_aoe_window(player, state, snapshots)

        # Step 3: 清理过期目标
        if self._goal_stack:
            removed = self._goal_stack.pop_expired(player, state)
            if removed:
                self._dbg(1, f"清理过期目标: {[g.description for g in removed]}")

        # Step 4: 按 Strategy 的阶段顺序逐层决策
        candidates: List[str] = []
        if "special" in available_actions and self._should_release_virus(player, state):
            candidates.append("special 释放病毒")
            self._dbg(1, "放毒: 释放病毒")

        # Step 3.5: 通用治疗策略（所有天赋）——受伤（<40%）且无即时危险时，
        # 回医院治疗（没钱先打工）。医院治疗是全局机制，任何 AI 都该会用；
        # G0 等带专属 adapter 的天赋已在 Step 1 接管，不经过这里。
        threat_snap = snapshots.get("threat")
        in_danger = bool(threat_snap and threat_snap.data.get("danger", False))
        if not self._danger_mode and not in_danger:
            heal_cmds = self._wound_heal_candidates(
                player, state, available_actions)
            if heal_cmds:
                self._dbg(1, f"受伤治疗 → {heal_cmds}")
                return self._finish_generate(self._dedup(heal_cmds + ["forfeit"]))

        # Step 3.6: 槽位 special 时机候选提前到阶段循环之前——
        # T6 热线/竞选/指挥是政治线的第一候选，否则 DEVELOP/CAPTAIN 恒先于
        # special，警察线永远排不上（diag_political_trace 证实 special=0/局）。
        try:
            resolver = getattr(self, 'talent_hook_resolver', None)
            hook = resolver(player) if resolver is not None else None
            if hook is not None and hasattr(
                    hook, 'get_talent_special_candidates'):
                talent_cmds = hook.get_talent_special_candidates(
                    player, state, list(available_actions),
                    snapshot=getattr(self, "_snapshot", None))
                for cmd in talent_cmds:
                    if cmd not in candidates:
                        candidates.append(cmd)
                if talent_cmds:
                    self._dbg(2, m9_text(
                        "ai.orchestrator.slot_special_pre",
                        commands=talent_cmds))
        except Exception:
            pass

        phase_order = self._strategy.get_phase_order()
        handled_phases: List[str] = []

        for phase in phase_order:
            # ★ 战斗结束窗口：在 COMBAT 之前优先消费 GoalStack 中的 AOE 目标
            if phase == DecisionPhase.COMBAT and s.aoe_acquisition_window:
                aoe_cmd = self._consume_aoe_goal(player, state, available_actions, my_loc)
                if aoe_cmd:
                    self._dbg(1, f"战斗结束窗口: GoalStack优先 → {aoe_cmd}")
                    result = [aoe_cmd, "forfeit"]
                    return self._finish_generate(self._dedup(result))

            phase_cmds = self._execute_phase(
                phase, player, state, available_actions,
                snapshots, round_num
            )
            if phase_cmds:
                # ★ 先过滤该阶段产出的无效 move（如 move home 但已在家）
                valid_cmds = [c for c in phase_cmds
                              if not (c.startswith("move ") and self._is_same_location(c[5:].strip(), my_loc))]
                if valid_cmds:
                    candidates.extend(valid_cmds)
                    handled_phases.append(f"{phase.name}({len(valid_cmds)}:{valid_cmds[0]})")
                    if self._strategy.is_terminal_phase(phase):
                        self._dbg(2, f"阶段 {phase.name} 终止后续 | 指令: {valid_cmds}")
                        # 终端阶段提前返回前，先补充 GoalStack 中已压入的待执行指令
                        goal_cmds = self._collect_goal_commands(
                            player, state, available_actions, candidates
                        )
                        if goal_cmds:
                            self._dbg(2, f"GoalStack补充(terminal): {goal_cmds}")
                            candidates.extend(goal_cmds)
                        candidates.append("forfeit")
                        result = self._dedup(candidates)
                        return self._finish_generate(result)
                else:
                    self._dbg(2, f"阶段 {phase.name} 产出全被过滤(原:{phase_cmds})，继续")
            else:
                # 调试：标注被跳过的阶段及其原因（level 2+）
                skip_reason = self._get_skip_reason(phase, snapshots, player)
                if skip_reason:
                    self._dbg(2, f"跳过 {phase.name}: {skip_reason}")

        self._dbg(1, f"阶段产出: {' → '.join(handled_phases) if handled_phases else '无'}")

        # ★ 战斗中：把 combat 类指令重排到 develop 类指令前面
        if self._in_combat:
            combat_cmds = [c for c in candidates
                           if c.split()[0] in ("attack", "find", "lock", "special")]
            other_cmds = [c for c in candidates
                          if c.split()[0] not in ("attack", "find", "lock", "special")]
            candidates = combat_cmds + other_cmds
            if combat_cmds:
                self._dbg(2, f"战斗中重排: combat={len(combat_cmds)}条 → 优先")

        # Step 4.5: 通用攻击补充（有武器有敌人就试一下）
        if not any(c.startswith(("attack", "find", "lock", "special"))
                   for c in candidates):
            weapons = getattr(player, 'weapons', [])
            if weapons:
                enemies = [state.get_player(pid) for pid in state.player_order
                           if pid != player.player_id and state.get_player(pid)
                           and state.get_player(pid).is_alive()]
                if enemies:
                    target = max(enemies,
                                 key=lambda t: self._threat_scores.get(t.name, 0))
                    attack_cmds = self._build_forced_attack_commands(
                        player, state, available_actions, target)
                    for cmd in attack_cmds:
                        if cmd not in candidates:
                            candidates.append(cmd)
                    if attack_cmds:
                        self._dbg(2, f"通用攻击补充: {attack_cmds}")

        # Step 5: GoalStack 补充收尾指令
        goal_cmds = self._collect_goal_commands(player, state, available_actions, candidates)
        if goal_cmds:
            self._dbg(2, f"GoalStack补充: {goal_cmds}")
        candidates.extend(goal_cmds)

        # Step 5.5: counter 层反制候选（capabilities 类目模板，如结界内破界）
        try:
            from controllers.ai.counter import counter_candidates
            counter_cmds = counter_candidates(
                player, getattr(self, "_snapshot", None), list(available_actions),
                state=state)
            for cmd in counter_cmds:
                if cmd not in candidates:
                    candidates.append(cmd)
            if counter_cmds:
                self._dbg(2, m9_text(
                    "ai.orchestrator.counter_supplement",
                    counters=counter_cmds))
        except Exception:
            pass

        # Step 5.6: 槽位 special 时机候选（C 层简单版：T6 热线/竞选/指挥等）
        try:
            resolver = getattr(self, 'talent_hook_resolver', None)
            hook = resolver(player) if resolver is not None else None
            if hook is not None and hasattr(
                    hook, 'get_talent_special_candidates'):
                talent_cmds = hook.get_talent_special_candidates(
                    player, state, list(available_actions),
                    snapshot=getattr(self, "_snapshot", None))
                for cmd in talent_cmds:
                    if cmd not in candidates:
                        candidates.append(cmd)
                if talent_cmds:
                    self._dbg(2, m9_text(
                        "ai.orchestrator.slot_special_supplement",
                        commands=talent_cmds))
        except Exception:
            pass

        candidates.append("forfeit")
        result = self._finalize(candidates, player, my_loc)
        return self._finish_generate(result)

    def _filter_barrier_actions(self, player: Any, state: Any,
                                available_actions: List[str]) -> List[str]:
        """M9 事实接入：结界内（被困）只保留攻击/特殊动作，移除移动类。

        兼容 M9：engine.m9 active_barrier(state) 是天赋实例（Mythland9，
        用 _is_trapped 判定）；v2exp 读 state.active_barrier 属性。
        """
        from engine.m9.gate import m9_enabled
        barrier = None
        if m9_enabled(state):
            try:
                from engine.m9.talents.g3 import active_barrier as _m9_barrier
                barrier = _m9_barrier(state)
            except Exception:
                barrier = None
        if barrier is None:
            barrier = getattr(state, 'active_barrier', None)
        if barrier:
            in_barrier = False
            if hasattr(barrier, '_is_trapped'):
                in_barrier = barrier._is_trapped(player)
            elif hasattr(barrier, 'is_in_barrier'):
                in_barrier = barrier.is_in_barrier(player.player_id)
            else:
                barrier_players = getattr(barrier, 'barrier_players', [])
                in_barrier = player.player_id in barrier_players
            if in_barrier:
                return [
                    a for a in available_actions
                    if a not in ("move", "interact", "find")
                    and not a.startswith("move ")
                    and not a.startswith("interact ")
                    and not a.startswith("find ")
                    and not a.startswith("interact ")
                ]
        return list(available_actions)

    def _finalize(self, candidates: List[str], player, my_loc: str) -> List[str]:
        """去重 + 过滤无效指令（move 到当前位置 / M9 被毁地点 / 高威胁地点）"""
        deduped = self._dedup(candidates)
        filtered = self._filter_invalid_moves(deduped, my_loc)
        filtered = self._filter_destroyed_moves(filtered)
        filtered = self._filter_high_threat_moves(filtered, my_loc)
        self._dbg(1, f"最终候选({len(filtered)}条): {filtered}")
        return filtered

    def _filter_high_threat_moves(self, candidates: List[str],
                                  my_loc: str) -> List[str]:
        """派生层消费：非战斗状态下，避免 move 到威胁显著高于当前位置的地点。

        AssessmentLayer.location_threat = 每地点对手威胁分之和；威胁分量级
        ≈ estimate_power（hp20 下单个对手 200-400）。阈值 400 ≈ 2 名对手压点
        （低于此视为常规抢点/追人，不剔除）；且仅在目的地威胁 > 当前位置 1.5 倍
        时剔除（已身处热点则移动不再受限）。
        """
        asm = getattr(self, "_assessment", None)
        if asm is None or not asm.location_threat:
            return candidates
        if self._in_combat:
            return candidates
        my_threat = asm.location_threat.get(my_loc, 0.0)
        out = []
        for cmd in candidates:
            if cmd.startswith("move "):
                dest = cmd[5:].strip()
                dest_threat = asm.location_threat.get(dest, 0.0)
                if dest_threat > 400 and dest_threat > my_threat * 1.5:
                    continue  # 2+ 对手压点且远高于当前位置，剔除白给移动
            out.append(cmd)
        return out

    def _filter_destroyed_moves(self, candidates: List[str]) -> List[str]:
        """M9：过滤 move 到被毁地点的指令（DecisionSnapshot.m9.destroyed_locations）。"""
        snap = getattr(self, "_snapshot", None)
        destroyed = ()
        if snap is not None and snap.m9 is not None:
            destroyed = snap.m9.destroyed_locations
        if not destroyed:
            return candidates
        result = []
        for cmd in candidates:
            if cmd.startswith("move "):
                dest = cmd[5:].strip()
                if dest in destroyed:
                    continue
            result.append(cmd)
        return result

    def _get_skip_reason(self, phase: DecisionPhase, snapshots: Dict, player) -> str:
        """返回跳过某个阶段的原因（调试用）"""
        threat = snapshots.get("threat")
        develop = snapshots.get("develop")
        combat = snapshots.get("combat")

        if phase == DecisionPhase.EMERGENCY_VIRUS:
            if not threat or not threat.data.get("virus_emergency"):
                return "无病毒"
        elif phase == DecisionPhase.EMERGENCY_SUPERNOVA:
            if not threat or not threat.data.get("supernova_threat"):
                return m9_text("ai.orchestrator.skip_no_supernova")
        elif phase == DecisionPhase.EMERGENCY_TERROR:
            if not threat or not threat.data.get("terror_info"):
                return m9_text("ai.orchestrator.skip_no_terror")
        elif phase == DecisionPhase.SURVIVAL:
            if not threat or not threat.data.get("danger"):
                return "非危险状态"
        elif phase == DecisionPhase.CAPTAIN:
            if not getattr(player, 'is_captain', False):
                return "非队长"
        elif phase == DecisionPhase.COMBAT:
            if not self._in_combat:
                if develop and not develop.data.get("development_complete"):
                    return "未发育完成"
                if combat and not combat.data.get("combat_ready"):
                    return "无可用目标或武器"
        elif phase == DecisionPhase.KILL_OPPORTUNITY:
            if not threat or not threat.data.get("kill_targets"):
                return "无可击杀目标"
        elif phase == DecisionPhase.DEVELOP:
            if develop and develop.data.get("development_complete"):
                return "发育已完成"
        return ""

    # ════════════════════════════════════════════════════════
    #  Step 1: 天赋钩子
    # ════════════════════════════════════════════════════════

    def _check_talent_overrides(self, player, state, available) -> Optional[List[str]]:
        talent_name = getattr(getattr(player, 'talent', None), 'name', '')
        # 优先按 (profile, slot_id) 解析（M9 adapter 分派层）；回退显示名
        resolver = getattr(self, 'talent_hook_resolver', None)
        hook = resolver(player) if resolver is not None else None
        if hook is None and talent_name and self._talent_hooks:
            hook = self._talent_hooks.get(talent_name)
        if hook:
            override = hook.should_override_candidates(player, state, available)
            if override is not None:
                return override
        return None

    # ════════════════════════════════════════════════════════
    #  Step 2: 运行所有 Mind
    # ════════════════════════════════════════════════════════

    def _run_all_minds(self, player, state, projected=None) -> Dict[str, Any]:
        """运行所有 Mind，返回 {mind_name: MindAssessment}。

        minds 快照化：构建 ProjectedSnapshot（全量不可变投影）+ AssessmentLayer
        （派生容器），作为 snapshot/assessment 传入每个 mind；mind 内部优先消费
        快照字段，缺失回退旧读法（渐进迁移，行为不变）。

        `projected` 可由 generate() 传入（同一决策点已构建过），避免一次决策
        重复投影 2~3 次。"""
        from controllers.ai.decision.snapshot import (
            AssessmentLayer, ProjectedSnapshot)
        projected = projected or ProjectedSnapshot.build(player, state)
        assessment = AssessmentLayer()
        self._snapshot = projected
        self._assessment = assessment
        snapshots = {}

        # 1. PoliceMind (先跑，为其他 Mind 提供 police 上下文)
        for mind in self._minds:
            mind_name = mind.__class__.__name__

            if mind_name == "PoliceMind":
                police_ctx = self._build_police_context(player, state)
                raw_result = mind.assess(
                    player, state,
                    police_cache=police_ctx.get("cache", {}),
                    threat_scores=self._shared_state.threat_scores,
                    my_location=self._query.get_location_str(player),
                    strategy=self._strategy,
                    snapshot=projected,
                    assessment=assessment,
                )
                # PoliceMind.assess() 返回 PoliceSituation，包装为 MindAssessment
                from controllers.ai.minds.base import MindAssessment
                snapshots["police"] = MindAssessment(
                    mind_name="police",
                    urgency=5 if raw_result.i_am_report_target else 0,
                    phase=DecisionPhase.SURVIVAL,
                    summary=f"警察态势: {raw_result.recommended_stance.value}",
                    data={"police_situation": raw_result},
                )
                break

        ctx = self._build_ctx(state)
        polices_cache = ctx.police_cache or {}

        # 2. ThreatMind
        for mind in self._minds:
            mind_name = mind.__class__.__name__

            if mind_name == "ThreatMind":
                snapshots["threat"] = mind.assess(
                    player, state, self._strategy,
                    previous_threat_scores=self._shared_state.threat_scores,
                    low_threat_streak=self._low_threat_streak,
                    been_attacked_by=self._been_attacked_by,
                    llm_aggression_mod=ctx.llm_aggression_mod,
                    polices_cache=polices_cache,
                    ctx=self._build_ctx(state),
                    snapshot=projected,
                    assessment=assessment,
                )
                # 更新 EMA 威胁分
                self._threat_scores = snapshots["threat"].data.get("threat_scores", {})
                self._low_threat_streak = snapshots["threat"].data.get("low_threat_streak", {})
                break

        # 3. DevelopMind
        for mind in self._minds:
            if mind.__class__.__name__ == "DevelopMind":
                merged_hooks = dict(self._talent_hooks or {})
                resolver = getattr(self, "talent_hook_resolver", None)
                if resolver is not None:
                    try:
                        slot_hook = resolver(player)
                        if slot_hook is not None:
                            merged_hooks["__slot__"] = slot_hook
                    except Exception:
                        pass
                snapshots["develop"] = mind.assess(
                    player, state, self._strategy,
                    talent_hooks=merged_hooks,
                    snapshot=projected,
                    assessment=assessment,
                )
                break

        # 4. CombatMind
        for mind in self._minds:
            if mind.__class__.__name__ == "CombatMind":
                police_protected = ctx.police_protected_ids
                # 提取 police stance（来自 PoliceMind 评估）
                police_stance = None
                police_mind_ref = None
                police_snap = snapshots.get("police")
                if police_snap:
                    police_sit = police_snap.data.get("police_situation")
                    if police_sit:
                        police_stance = getattr(police_sit, 'recommended_stance', None)
                        police_stance = police_stance.value if (police_stance and hasattr(police_stance, 'value')) else str(police_stance) if police_stance else None
                for pm in self._minds:
                    if pm.__class__.__name__ == "PoliceMind":
                        police_mind_ref = pm
                        break
                snapshots["combat"] = mind.assess(
                    player, state, self._strategy,
                    threat_scores=self._shared_state.threat_scores,
                    combat_target=self._combat_target,
                    in_combat=self._in_combat,
                    police_protected_ids=police_protected,
                    police_stance=police_stance,
                    police_mind=police_mind_ref,
                    llm_alliance=ctx.llm_alliance,
                    terror_defense=ctx.terror_defense,
                    star_follow_up_rounds=ctx.star_follow_up_rounds,
                    llm_aggression_mod=ctx.llm_aggression_mod,
                    players_who_attacked=ctx.players_who_attacked,
                    snapshot=projected,
                    assessment=assessment,
                )
                break

        # 派生层回写：minds 写入的 AssessmentLayer 字段由 orchestrator 共享
        self._assessment = assessment
        return snapshots

    def _build_police_context(self, player, state) -> Dict:
        """构建 PoliceMind 需要的上下文"""
        return {
            "cache": self._shared_state.police_cache or {},
        }

    # ════════════════════════════════════════════════════════
    #  Step 3+4: 按 Phase 执行
    # ════════════════════════════════════════════════════════

    def _execute_phase(
        self, phase: DecisionPhase, player, state,
        available: List[str], snapshots: Dict[str, Any], round_num: int,
    ) -> List[str]:
        """执行单个决策阶段，返回指令列表或空列表"""
        handler = {
            DecisionPhase.EMERGENCY_VIRUS: self._handle_emergency_virus,
            DecisionPhase.EMERGENCY_SUPERNOVA: self._handle_emergency_supernova,
            DecisionPhase.EMERGENCY_TERROR: self._handle_emergency_terror,
            DecisionPhase.SURVIVAL: self._handle_survival,
            DecisionPhase.CAPTAIN: self._handle_captain,
            DecisionPhase.SPECIAL_TALENT: self._handle_special_talent,
            DecisionPhase.COMBAT: self._handle_combat,
            DecisionPhase.KILL_OPPORTUNITY: self._handle_kill_opportunity,
            DecisionPhase.DEVELOP: self._handle_develop,
            DecisionPhase.FALLBACK: self._handle_fallback,
        }.get(phase)
        if handler:
            return handler(player, state, available, snapshots, round_num)
        return []

    # ── 病毒应急 ──
    def _handle_emergency_virus(self, player, state, available, snapshots, round_num) -> List[str]:
        threat = snapshots.get("threat")
        if not threat or not threat.data.get("virus_emergency"):
            return []

        debug_ai_basic(player.name, "进入病毒应急模式")
        ctx = self._build_ctx(state)
        cmds = self._develop_cmd.build_virus(player, state, self._strategy, available, ctx)
        if cmds:
            if self._goal_stack:
                from controllers.ai.goals.virus_goal import VirusCureGoal
                preferred_loc = self._develop_cmd.pick_virus_cure_location(player, state)
                goal = VirusCureGoal(
                    preferred_location=preferred_loc,
                    debug_name=player.name,
                )
                goal.set_round(round_num)
                self._goal_stack.push(goal)
            cmds.append("forfeit")
        return cmds

    # ── 超新星紧急分散 ──
    def _handle_emergency_supernova(self, player, state, available, snapshots, round_num) -> List[str]:
        threat = snapshots.get("threat")
        if not threat or not threat.data.get("supernova_threat"):
            return []

        # ★ 火萤玩家已在 ThreatMind._detect_supernova_threat() 中排除，此处无需再检查

        my_loc = GameQuery.get_location_str(player)
        same_loc = GameQuery.get_same_location_targets(player, state)
        if len(same_loc) < 2:
            return []

        cmds = []
        empty_locs = [
            loc for loc in ["home", "商店", "医院", "魔法所", "军事基地", "警察局"]
            if loc != my_loc and GameQuery.count_enemies_at(loc, player, state) == 0
        ]
        if empty_locs and "move" in available:
            import random
            dest = random.choice(empty_locs)
            cmds.append(f"move {dest}")
        return cmds

    # ── Terror 紧急集火 ──
    def _build_forced_attack_commands(self, player, state, available, target) -> List[str]:
        """为指定目标重新构建攻击命令，避免复用 CombatMind 的 best_target 命令。"""
        if not target:
            return []
        ctx = self._build_ctx(state)
        police_mind = None
        for mind in self._minds:
            if mind.__class__.__name__ == "PoliceMind":
                police_mind = mind
                break
        return self._combat_cmd.build_attack(
            player, state, self._strategy, available, ctx,
            forced_target=target, police_mind=police_mind,
        )

    def _handle_emergency_terror(self, player, state, available, snapshots, round_num) -> List[str]:
        threat = snapshots.get("threat")
        if not threat:
            return []
        terror_info = threat.data.get("terror_info")
        if not terror_info:
            return []

        # ★ 星野玩家已在 ThreatMind._detect_terror_threat() 中排除，此处无需再检查

        target = terror_info["target"]
        debug_ai_basic(player.name, m9_text(
            "ai.orchestrator.terror_threat", target=target.name))

        cmds = self._build_forced_attack_commands(player, state, available, target)
        if cmds:
            self._push_combat_goal(target, player, round_num, priority=9)
            return cmds

        return []

    # ── 危险模式 ──
    _WOUND_HEAL_THRESHOLD_PCT = 0.4  # 通用治疗阈值（与 G0 adapter 一致）

    def _wound_heal_candidates(self, player, state, available) -> List[str]:
        """通用治疗策略：受伤（<40% HP）→ 医院治疗链（没钱先打工攒费）。

        医院治疗是全局机制（+6 HP / 2 信用点），任何 AI 都应会用；
        返回空列表 = 无需治疗或无法治疗（交回正常阶段决策）。
        available 只含类别名（"move"/"interact"），完整命令须经
        build_action_options 枚举（与 _G0Adapter 同源）。
        """
        if "interact" not in available and "move" not in available:
            return []
        hp = float(getattr(player, "hp", 0) or 0)
        max_hp = float(getattr(player, "max_hp", 20) or 20)
        if hp <= 0 or hp >= max_hp * self._WOUND_HEAL_THRESHOLD_PCT:
            return []
        from engine.action_enumerator import build_action_options
        loc = getattr(player, "location", None)
        if loc == "医院":
            if "interact" not in available:
                return []
            interacts = set(build_action_options(
                player, state, ["interact"]).get("interact", []) or [])
            if "interact 治疗" in interacts:
                from controllers.ai.constants import ai_wallet
                if float(ai_wallet(player) or 0) >= 2:
                    return ["interact 治疗"]
                return ["interact 打工"] if "interact 打工" in interacts else []
            return []
        if "move" in available:
            moves = set(build_action_options(
                player, state, ["move"]).get("move", []) or [])
            if "move 医院" in moves:
                return ["move 医院"]
        return []

    def _handle_survival(self, player, state, available, snapshots, round_num) -> List[str]:
        threat = snapshots.get("threat")
        if not threat:
            return []

        is_danger = threat.data.get("danger", False)

        if is_danger and not self._danger_mode:
            # 进入危险模式
            self._danger_mode = True
            if self._goal_stack:
                self._goal_stack.interrupt_all()
                if getattr(player, 'is_captain', False):
                    safe_loc = self._develop_cmd.pick_captain_safe_destination(player, state, self._shared_state.police_cache or {})
                else:
                    safe_loc = self._develop_cmd.pick_safe_armor_destination(player, state)
                if safe_loc:
                    from controllers.ai.goals.flee_goal import FleeGoal
                    flee = FleeGoal(destination=safe_loc, debug_name=player.name)
                    flee.set_round(round_num)
                    self._goal_stack.push(flee)

        if self._danger_mode:
            if self._query.is_danger_resolved(
                player, state, self._shared_state.police_cache, self._strategy,
            ):
                debug_ai_basic(player.name, "危险解除")
                self._danger_mode = False
                if self._goal_stack:
                    for goal in self._goal_stack.all_goals:
                        if hasattr(goal, '_danger_resolved'):
                            goal._danger_resolved = True
                    self._goal_stack.resume_all()
                    self._goal_stack.pop_expired(player, state)
                return []

            # 仍在危险中
            ctx = self._build_ctx(state)
            return self._develop_cmd.build_danger_develop(
                player, state, self._strategy, available, ctx,
            )

        return []

    # ── 队长指挥 / Political 警察建设 ──
    def _handle_captain(self, player, state, available, snapshots, round_num) -> List[str]:
        # 队长：警察指挥
        if getattr(player, 'is_captain', False) and "police_command" in available:
            self._dbg(2, "队长: 生成警察指挥")
            ctx = self._build_ctx(state)
            cmds = self._police_cmd.build_captain(
                player, state, self._strategy, available, ctx)
            if cmds:
                if self._goal_stack:
                    from controllers.ai.goals.captain_goal import CaptainGoal
                    goal = CaptainGoal(
                        cmd_captain_fn=lambda p, s, a: self._police_cmd.build_captain(
                            p, s, self._strategy, a, self._build_ctx(s)),
                        debug_name=player.name,
                    )
                    goal.set_round(round_num)
                    self._goal_stack.push(goal)
                return cmds[:1]
            return []

        # 非队长但 PoliceMind 建议 build（political 人格去警察局）
        police = snapshots.get("police")
        if police:
            police_sit = police.data.get("police_situation")
            if police_sit:
                try:
                    if getattr(police_sit, 'recommended_stance', None) == PoliceStance.BUILD:
                        self._dbg(2, "political: 尝试警察建设")
                        ctx = self._build_ctx(state)
                        cmds = self._police_cmd.build_police_political(
                            player, state, self._strategy, available, ctx)
                        if cmds:
                            self._dbg(2, f"political: 警察建设 → {cmds}")
                            return cmds
                        else:
                            self._dbg(2, "political: 警察建设无可用指令")
                except Exception:
                    pass

        return []

    # ── 特殊天赋技能 ──
    def _handle_special_talent(self, player, state, available, snapshots, round_num) -> List[str]:
        # ★ 设计说明：全息影像扫场/救世主集火/火萤超新星等天赋主动技能
        # 由 TalentHook.should_override_candidates() 在 Step 1 处理。
        # T0 激活类天赋（一刀缭断/天星/全息发动/神话之外）由 choose() 在
        # T0 阶段处理。SPECIAL_TALENT phase 仅作为 fallback 占位存在。
        return []

    # ── 战斗 ──
    def _handle_combat(self, player, state, available, snapshots, round_num) -> List[str]:
        combat = snapshots.get("combat")
        if not combat:
            return []
        combat_cmds: List[str] = []

        # ★ RESIST 预处理：无 AOE → 获取 AOE（与战斗直接相关的武器准备）
        police_snap = snapshots.get("police")
        resist_active = False
        if police_snap:
            police_sit = police_snap.data.get("police_situation")
            if police_sit:
                try:
                    from controllers.ai.minds.police_mind import PoliceMind as PM
                    stance = getattr(police_sit, 'recommended_stance', None)
                    if stance == PoliceStance.RESIST:
                        resist_active = True
                        polices_cache = self._shared_state.police_cache or {}
                        alive_police = sum(1 for u in polices_cache.get("units", [])
                                          if u.get("is_alive"))
                        if alive_police > 0 and not PM.has_any_aoe(player):
                            # T1 一刀缭断：有剩余次数+就绪武器 → 天赋伤害翻倍可绕过警察保护
                            t1_talent = getattr(player, 'talent', None)
                            t1_can_bypass = (
                                t1_talent
                                and "一刀缭断" in getattr(t1_talent, 'name', '')
                                and getattr(t1_talent, 'uses_remaining', 0) > 0
                                and (
                                    any(w.name == "小刀"
                                        and getattr(w, 'base_damage', 0) >= 2
                                        for w in getattr(player, 'weapons', []) if w)
                                    or any(w.name == "高斯步枪"
                                           and getattr(w, 'is_charged', False)
                                           for w in getattr(player, 'weapons', []) if w)
                                )
                            )
                            if t1_can_bypass:
                                self._dbg(2, "RESIST: T1天赋可绕过警察保护，跳过AOE获取")
                            else:
                                # ★ 无AOE：始终尝试获取AOE指令
                                target_armor_attrs = self._query.get_all_protected_armor_attrs(
                                    state, player.player_id)
                                aoe_cmds = None
                                for pm in self._minds:
                                    if pm.__class__.__name__ == "PoliceMind":
                                        aoe_cmds = pm.get_aoe_acquisition_commands(
                                            player, state, available,
                                            target_armor_attrs=target_armor_attrs,
                                            my_location=self._query.get_location_str(player),
                                            has_pass=getattr(player, 'has_military_pass', False),
                                            learned_spells=getattr(player, 'learned_spells', set()),
                                        )
                                        break

                                combat_data = combat.data if combat else {}
                                viable = combat_data.get("viable_targets", [])
                                police_protected = self._query.get_police_protected_ids(state)
                                protected_viable = sum(
                                    1 for t, _ in viable
                                    if getattr(t, 'player_id', None) in police_protected
                                )
                                unprotected_count = max(0, len(viable) - protected_viable)

                                if unprotected_count > 0:
                                    # 有不受保护目标 → 不中断当前战斗，但把AOE获取压入GoalStack
                                    self._dbg(2, f"RESIST: {unprotected_count}个不受保护目标存在，AOE获取压入GoalStack")
                                    if aoe_cmds and self._goal_stack:
                                        self._try_push_aoe_goal(aoe_cmds, player, round_num, 7)
                                    # 不 return —— 让控制流继续到 COMBAT 阶段
                                else:
                                    # 全受保护 → 立即获取AOE
                                    self._dbg(2, "RESIST: 无AOE，获取AOE武器")
                                    if aoe_cmds:
                                        self._try_push_aoe_goal(aoe_cmds, player, round_num, 7)
                                        return aoe_cmds
                except Exception:
                    pass

        # ★ RESIST + 有队长目标：切换战斗目标为队长（优先级超越当前战斗）
        if resist_active and self._in_combat and self._combat_target:
            # 队长存在且可触及 → 切换目标
            if not getattr(self._combat_target, 'is_captain', False):
                captain = self._find_captain_in_viable_targets(combat, state)
                if captain and self._query.same_location(player, captain) \
                        and self._query.can_reach_target_in_barrier(player, state, captain):
                    self._dbg(1, f"RESIST: 切换目标 {self._combat_target.name} → 队长 {captain.name}")
                    self._combat_target = captain

        # 如果正在战斗中
        if self._in_combat and self._combat_target:
            if self._query.should_continue_combat(player, self._combat_target, state, self._strategy, self._personality, self._shared_state.political_fallback_level):
                combat_cmds = self._build_forced_attack_commands(
                    player, state, available, self._combat_target)
                if combat_cmds:
                    debug_ai_combat_state(player.name, f"战斗目标: {self._combat_target.name}")
                    return combat_cmds
            else:
                debug_ai_basic(player.name, "退出战斗状态")
                self._in_combat = False
                old_target = self._combat_target
                self._combat_target = None
                # 武器被克制时换武器
                if old_target and combat.data.get("all_countered"):
                    ctx = self._build_ctx(state)
                    return self._combat_cmd.build_rearm(player, state, self._strategy, available, ctx)

        # ★ RESIST stance: 强制进入战斗，不管发育是否完成
        if not self._in_combat and resist_active and combat.data.get("combat_ready"):
            best_target = combat.data.get("best_target")
            # RESIST 下：队长优先（即使评分不是最高，只要可打穿就选队长）
            # 但结界内只能攻击同地点目标——队长在结界外则跳过
            captain = self._find_captain_in_viable_targets(combat, state)
            if captain and self._can_damage_via_combat(combat, captain):
                if not self._query.same_location(player, captain):
                    self._dbg(2, f"RESIST: 队长 {captain.name} 不在同地点，跳过")
                elif self._query.can_reach_target_in_barrier(player, state, captain):
                    best_target = captain
                    self._dbg(1, f"RESIST: 队长优先 → {captain.name}")
                else:
                    self._dbg(2, f"RESIST: 队长 {captain.name} 不可达（结界/位置不同），跳过")
            if best_target:
                combat_cmds = self._build_forced_attack_commands(player, state, available, best_target)
                if combat_cmds:
                    self._dbg(1, f"RESIST: 强制进入战斗 → {best_target.name}")
                    self._in_combat = True
                    self._combat_target = best_target
                    self._push_combat_goal(best_target, player, round_num)
                    return combat_cmds

        # 发育完成后尝试攻击
        develop = snapshots.get("develop")
        dev_complete = develop.data.get("development_complete") if develop else False
        # 发育空转：策略说未完成，但既无缺项也无目标地点 → DEVELOP 必定空返
        develop_empty = (
            not dev_complete and develop
            and not develop.data.get("unmet_needs")
            and develop.data.get("best_location") is None
        )
        if dev_complete and combat.data.get("combat_ready"):
            best_target = combat.data.get("best_target")
            if best_target is None:
                # 派生层消费：CombatMind 未选出目标时，用快照威胁目标降级
                # （AssessmentLayer.combat_target = 威胁最高的存活对手）
                asm = getattr(self, '_assessment', None)
                asm_target = asm.combat_target if asm is not None else None
                if asm_target:
                    t = state.get_player(asm_target)
                    if t and t.is_alive():
                        best_target = t
            if best_target:
                # 对齐 political 禁战门：非队长 political 不主动开新战斗
                # （should_continue_combat 在持续战斗路径生效，但本开战路径
                # 此前直接绕过了它）。
                if not self._query.should_continue_combat(
                        player, best_target, state, self._strategy,
                        self._personality,
                        self._shared_state.political_fallback_level):
                    self._dbg(1, f"禁战门拦截开战目标 {best_target.name}，"
                                 f"转交其他阶段处理")
                    best_target = None
            if best_target:
                combat_cmds = self._build_forced_attack_commands(player, state, available, best_target)
                if combat_cmds:
                    self._in_combat = True
                    self._combat_target = best_target
                    self._push_combat_goal(best_target, player, round_num)
                    return combat_cmds

        # 武器全被克制 + 发育已完成 → 推送 RearmGoal 换武器
        if (not combat_cmds and (dev_complete or develop_empty)
                and combat.data.get("all_countered")
                and combat.data.get("best_target")):
            target = combat.data.get("best_target")
            if self._goal_stack:
                from controllers.ai.goals.rearm_goal import RearmGoal
                has_rearm_goal = any(
                    isinstance(goal, RearmGoal)
                    and getattr(goal, '_target_id', None) == target.player_id
                    for goal in self._goal_stack.all_goals
                )
                if not has_rearm_goal:
                    rearm_goal = RearmGoal(target.player_id, debug_name=player.name)
                    rearm_goal.set_round(round_num)
                    self._goal_stack.push(rearm_goal)
                    self._dbg(1, f"武器被克制，推送RearmGoal → {target.name}")
            ctx = self._build_ctx(state)
            rearm_cmds = self._combat_cmd.build_rearm(
                player, state, self._strategy, available, ctx)
            if rearm_cmds:
                return rearm_cmds

        # ★ RESIST 兜底：有 AOE + 存活警察 + 没有产出任何战斗指令 → 反击警察单位
        if resist_active and not combat_cmds:
            polices_cache = self._shared_state.police_cache or {}
            alive_police = sum(1 for u in polices_cache.get("units", [])
                              if u.get("is_alive"))
            if alive_police > 0:
                ctx = self._build_ctx(state)
                fight_cmds = self._police_cmd.build_fight_police(
                    player, state, self._strategy, available, ctx)
                if fight_cmds:
                    return fight_cmds

        # ════════════════════════════════════════════════════════════
        #  警察保护降级：best_target 被保护拦住时，按 stance 分流处理
        #  - IGNORE/BUILD: 降级到 viable_targets 中第一个不受保护的目标
        #  - RESIST:       推 DevelopGoal 拿 AOE，后续自动打队长
        # ════════════════════════════════════════════════════════════
        if not combat_cmds and (dev_complete or develop_empty):
            best_target = combat.data.get("best_target")
            if best_target and self._is_blocked_by_police_protection(
                    player, state, best_target):
                stance = self._get_police_stance(snapshots)
                if stance == "resist":
                    self._dbg(1,
                        f"RESIST: best_target {best_target.name} 被警察保护拦住 → 获取AOE")
                    aoe_cmds = self._push_aoe_develop_goal(
                        player, state, available, best_target, round_num)
                    if aoe_cmds:
                        return aoe_cmds
                else:
                    viable = combat.data.get("viable_targets", [])
                    self._dbg(1,
                        f"IGNORE: best_target {best_target.name} 被保护 → "
                        f"降级遍历 {len(viable)-1} 个次优目标")
                    for entry in viable[1:]:
                        t = entry[0] if isinstance(entry, (list, tuple)) else entry
                        if not t or not t.is_alive():
                            continue
                        if self._is_blocked_by_police_protection(player, state, t):
                            continue
                        fb_cmds = self._build_forced_attack_commands(
                            player, state, available, t)
                        if fb_cmds:
                            self._dbg(1, f"降级目标 → {t.name}")
                            self._in_combat = True
                            self._combat_target = t
                            self._push_combat_goal(t, player, round_num)
                            return fb_cmds

        return []

    # ── 击杀机会 ──
    def _handle_kill_opportunity(self, player, state, available, snapshots, round_num) -> List[str]:
        threat = snapshots.get("threat")
        if not threat:
            return []

        kill_targets = threat.data.get("kill_targets", [])
        if not kill_targets:
            return []

        # 拿第一个可击杀目标
        for kt in kill_targets:
            target = kt.get("target")
            if target:
                cmds = self._build_forced_attack_commands(player, state, available, target)
                if cmds:
                    self._push_combat_goal(target, player, round_num, priority=7)
                    return cmds

        return []

    # ── 发育 ──
    def _handle_develop(self, player, state, available, snapshots, round_num) -> List[str]:
        develop = snapshots.get("develop")
        if not develop:
            self._dbg(2, "发育: DevelopMind 未产出评估")
            return []

        if develop.data.get("development_complete"):
            self._dbg(2, "发育: 已完成")
            return []

        if self._goal_stack:
            from controllers.ai.goals.develop_goal import DevelopGoal
            if any(isinstance(g, DevelopGoal) for g in self._goal_stack.all_goals):
                self._dbg(2, "发育: 沿用已有发育目标")
                return []

        ctx = self._build_ctx(state)
        cmds = self._develop_cmd.build_develop(
            player, state, self._strategy, available, ctx,
            develop_assessment=develop,
            combat_assessment=snapshots.get("combat"),
            talent_hooks=self._talent_hooks,
            combat_builder=self._combat_cmd,
        )

        if cmds:
            self._dbg(2, f"发育: Builder 产出 {cmds}")
            # ★ 产出 move 指令时持久化发育意图到 GoalStack
            if any(c.startswith("move ") for c in cmds):
                develop_data = develop.data if develop else {}
                unmet = develop_data.get("unmet_needs", [])
                best_location = develop_data.get("best_location")
                if best_location and unmet:
                    target_item = None
                    for _need_type, providers in unmet:
                        for loc, item, _cost in providers:
                            if loc == best_location:
                                target_item = item
                                break
                        if target_item:
                            break
                    if target_item and self._goal_stack:
                        goal = DevelopGoal(
                            target_item=target_item,
                            target_location=best_location,
                            priority=4,
                            debug_name=player.name,
                        )
                        goal.set_round(round_num)
                        self._goal_stack.push(goal)
                        self._dbg(2, f"推送发育目标: {goal.description}")
        else:
            self._dbg(2, "发育: 无可行指令")
        return cmds

    # ── 兜底 ──
    def _handle_fallback(self, player, state, available, snapshots, round_num) -> List[str]:
        """所有阶段均无产出时的兜底行为"""
        cmds: List[str] = []
        my_loc = GameQuery.get_location_str(player)

        # 1. 当前地点可交互
        develop = snapshots.get("develop")
        if develop:
            current_actions = develop.data.get("current_location_actions", [])
            if current_actions and "interact" in available:
                cmds.extend(current_actions)
                self._dbg(2, f"兜底: 本地交互 {current_actions}")

        # 2. 移动到安全地点
        if not cmds and "move" in available:
            safe_loc = self._develop_cmd.pick_fallback_destination(player, state, self._strategy, self._personality, self._build_ctx(state))
            if safe_loc and not self._is_same_location(safe_loc, my_loc):
                cmds.append(f"move {safe_loc}")
                self._dbg(2, f"兜底: 移动到 {safe_loc}")

        return cmds

    # ════════════════════════════════════════════════════════
    #  Step 5: GoalStack 收尾
    # ════════════════════════════════════════════════════════

    def _collect_goal_commands(
        self, player, state, available, candidates
    ) -> List[str]:
        if not self._goal_stack or self._goal_stack.is_empty:
            return []
        if self._danger_mode:
            return []

        cmds = []
        has_combat = any(
            c.startswith(("attack", "special", "lock", "find"))
            for c in candidates
        )
        for goal in self._goal_stack.all_goals:
            goal_cmd = goal.get_next_command(player, state, available)
            if goal_cmd and goal_cmd not in candidates and goal_cmd not in cmds:
                cmds.append(goal_cmd)

        return cmds

    # ════════════════════════════════════════════════════════
    #  战斗结束窗口：AOE 获取
    # ════════════════════════════════════════════════════════

    def _manage_aoe_window(self, player, state, snapshots):
        """管理 AOE 获取窗口的置位与复位。

        置位条件：combat_just_ended_at 信号 + RESIST + 无AOE + 存活警察
        复位条件：AOE 已到手 / 警察消失 / 队长死亡
        """
        s = self._shared_state
        police_snap = snapshots.get("police")

        # ── 置位 ──
        if s.combat_just_ended_at is not None:
            resist = False
            alive_police = 0
            if police_snap:
                sit = police_snap.data.get("police_situation")
                if sit:
                    stance = getattr(sit, 'recommended_stance', None)
                    if stance is not None:
                        raw = stance.value if hasattr(stance, 'value') else str(stance)
                        resist = (raw == "resist")
                    pc = self._shared_state.police_cache or {}
                    alive_police = sum(1 for u in pc.get("units", [])
                                      if u.get("is_alive"))

            from controllers.ai.minds.police_mind import PoliceMind as PM
            has_aoe = PM.has_any_aoe(player)
            t1_can_bypass = False
            if alive_police > 0 and not has_aoe:
                t1_talent = getattr(player, 'talent', None)
                t1_can_bypass = (
                    t1_talent
                    and "一刀缭断" in getattr(t1_talent, 'name', '')
                    and getattr(t1_talent, 'uses_remaining', 0) > 0
                    and (
                        any(w.name == "小刀"
                            and getattr(w, 'base_damage', 0) >= 2
                            for w in getattr(player, 'weapons', []) if w)
                        or any(w.name == "高斯步枪"
                               and getattr(w, 'is_charged', False)
                               for w in getattr(player, 'weapons', []) if w)
                    )
                )

            if resist and alive_police > 0 and not has_aoe and not t1_can_bypass:
                pc = self._shared_state.police_cache or {}
                captain_id = pc.get("captain_id")
                captain_alive = (
                    captain_id is not None
                    and captain_id != player.player_id
                    and state.get_player(captain_id)
                    and state.get_player(captain_id).is_alive()
                )
                if captain_alive:
                    s.aoe_acquisition_window = True
                    self._dbg(1,
                        f"战斗结束窗口: 开启（战斗结束于{s.combat_just_ended_at}，无AOE，RESIST）")

        # ── 复位 ──
        if s.aoe_acquisition_window:
            from controllers.ai.minds.police_mind import PoliceMind as PM
            if PM.has_any_aoe(player):
                s.aoe_acquisition_window = False
                self._dbg(1, "战斗结束窗口: 关闭（AOE已获取）")
            else:
                pc = self._shared_state.police_cache or {}
                alive_police = sum(1 for u in pc.get("units", [])
                                  if u.get("is_alive"))
                captain_id = pc.get("captain_id")
                captain_alive = (
                    captain_id is not None
                    and captain_id != player.player_id
                    and state.get_player(captain_id)
                    and state.get_player(captain_id).is_alive()
                )
                if alive_police == 0 or not captain_alive:
                    s.aoe_acquisition_window = False
                    self._dbg(1, "战斗结束窗口: 关闭（警察/队长不再构成威胁）")

    def _consume_aoe_goal(
        self, player, state, available, my_loc: str
    ) -> Optional[str]:
        """从 GoalStack 中消费 AOE 获取目标的下一步指令。"""
        if not self._goal_stack or self._goal_stack.is_empty:
            return None
        from controllers.ai.goals.develop_goal import DevelopGoal
        for goal in self._goal_stack.all_goals:
            if isinstance(goal, DevelopGoal) and goal.target_item in POLICE_AOE_WEAPONS:
                cmd = goal.get_next_command(player, state, available)
                if cmd and not (cmd.startswith("move ")
                                and self._is_same_location(cmd[5:].strip(), my_loc)):
                    return cmd
        return None

    # ════════════════════════════════════════════════════════
    #  GameQuery 辅助（保留少量编排层需要的战术判断）
    # ════════════════════════════════════════════════════════

    @staticmethod
    def _find_captain_in_viable_targets(combat_snapshot, state) -> Optional[Any]:
        """在 CombatMind 的 viable_targets 中找队长"""
        viable = combat_snapshot.data.get("viable_targets", [])
        for entry in viable:
            target = entry[0] if isinstance(entry, (list, tuple)) else entry
            if getattr(target, 'is_captain', False):
                return target
        return None

    @staticmethod
    def _can_damage_via_combat(combat_snapshot, captain) -> bool:
        """检查 CombatMind 评分中是否判定队长可被伤害（分数 > -400 即可打击）"""
        viable = combat_snapshot.data.get("viable_targets", [])
        for entry in viable:
            target = entry[0] if isinstance(entry, (list, tuple)) else entry
            if target.player_id == captain.player_id:
                score = entry[1] if isinstance(entry, (list, tuple)) and len(entry) > 1 else 0
                return score > -400
        return False

    def _push_combat_goal(self, target, player, round_num, priority: int = 6):
        if not self._goal_stack:
            return
        from controllers.ai.goals.combat_goal import CombatGoal
        goal = CombatGoal(
            target_id=target.player_id,
            target_name=target.name,
            priority=priority,
            debug_name=player.name,
        )
        goal.set_round(round_num)
        self._goal_stack.push(goal)

    # ════════════════════════════════════════════════════════════
    #  警察保护降级 helper
    # ════════════════════════════════════════════════════════════

    def _get_police_stance(self, snapshots: Dict[str, Any]) -> str:
        """从 police 快照中提取推荐 stance 字符串。"""
        ps = snapshots.get("police")
        if ps:
            sit = ps.data.get("police_situation")
            if sit:
                raw = getattr(sit, 'recommended_stance', None)
                if raw is not None:
                    return raw.value if hasattr(raw, 'value') else str(raw)
        return "ignore"

    def _get_police_mind(self):
        """获取 PoliceMind 实例。"""
        for mind in self._minds:
            if mind.__class__.__name__ == "PoliceMind":
                return mind
        return None

    def _is_blocked_by_police_protection(
        self, player: Any, state: Any, target: Any
    ) -> bool:
        """检查目标是否因警察保护而无法被当前武器库击穿。"""
        pe = getattr(state, 'police_engine', None)
        if not pe or not pe.is_protected_by_police(target.player_id):
            return False
        pm = self._get_police_mind()
        if pm is None:
            threshold = pe.get_protection_threshold(target.player_id)
            return threshold >= 1.5
        can_dmg, _ = pm.can_damage_through_protection(
            player, target, state,
            talent_adjusted_damage=self._query.estimate_talent_adjusted_damage(player),
            outer_armor_attrs=set(self._query.get_outer_armor_attr(target)),
            inner_armor_attrs=set(self._query.get_inner_armor_attr(target)),
            aoe_weapon_names=self._query.get_all_aoe_weapon_names(player),
            player_weapons=getattr(player, 'weapons', []),
            learned_spells=self._query.get_learned_spells(player),
        )
        return not can_dmg

    def _try_push_aoe_goal(
        self, aoe_cmds: List[str], player: Any, round_num: int, priority: int,
    ) -> None:
        """从 aoe_cmds 提取目标并推入 GoalStack（含命令格式处理+去重）。"""
        if not aoe_cmds or not self._goal_stack:
            return
        from controllers.ai.goals.develop_goal import DevelopGoal
        first_cmd = aoe_cmds[0]
        if first_cmd.startswith("move "):
            dest = first_cmd[5:]
            weapon = self._ctrl._infer_aoe_weapon(dest)
            # ★ 地动山摇需先学会地震
            if weapon == "地动山摇":
                learned = getattr(player, 'learned_spells', set())
                if "地震" not in learned:
                    weapon = "地震"
        else:
            dest = self._extract_dest_from_cmd(first_cmd)
            weapon = self._extract_item_from_cmd(first_cmd)
            if weapon and not dest:
                dest = self._infer_aoe_destination(weapon)
        if not weapon or not dest:
            return
        has_aoe_goal = any(
            isinstance(g, DevelopGoal)
            and getattr(g, 'target_item', None) == weapon
            for g in self._goal_stack.all_goals
        )
        if has_aoe_goal:
            return
        goal = DevelopGoal(
            target_item=weapon,
            target_location=dest,
            priority=priority,
            debug_name=player.name,
        )
        goal.set_round(round_num)
        self._goal_stack.push(goal)
        self._dbg(2, f"推送AOE获取目标: {weapon}（去{dest}）")

    def _push_aoe_develop_goal(
        self, player: Any, state: Any, available: List[str],
        target: Any, round_num: int,
    ) -> List[str]:
        """RESIST 下 best_target 被保护拦住时：推 DevelopGoal 拿 AOE。"""
        pm = self._get_police_mind()
        if pm is None:
            return []
        target_armor_attrs = (
            self._query.get_outer_armor_attr(target)
            or self._query.get_inner_armor_attr(target)
        )
        aoe_cmds = pm.get_aoe_acquisition_commands(
            player, state, available,
            target_armor_attrs=set(target_armor_attrs),
            my_location=self._query.get_location_str(player),
            has_pass=getattr(player, 'has_military_pass', False),
            learned_spells=getattr(player, 'learned_spells', set()),
        )
        self._try_push_aoe_goal(aoe_cmds, player, round_num, 7)
        return aoe_cmds

    @staticmethod
    def _extract_item_from_cmd(cmd: str) -> str:
        """从 'interact <item>' 中提取物品名。"""
        if cmd.startswith("interact "):
            return cmd[len("interact "):]
        return cmd

    @staticmethod
    def _extract_dest_from_cmd(cmd: str) -> str:
        """从 'move <dest>' 中提取目的地，否则返回空字符串。"""
        if cmd.startswith("move "):
            return cmd[len("move "):]
        return ""

    @staticmethod
    def _infer_aoe_destination(weapon: str) -> str:
        """根据武器/物品名推断所在目的地（_infer_aoe_weapon 的逆向映射）。"""
        if weapon in ("电磁步枪", "通行证"):
            return "军事基地"
        if weapon in ("地震", "地动山摇"):
            return "魔法所"
        return ""

    def _should_release_virus(self, player, state) -> bool:
        """判断是否应释放病毒（移植自 develop_mixin._should_release_virus）。"""
        if self._strategy.personality_name != "assassin":
            return False
        if GameQuery.get_location_str(player) != "医院":
            return False
        virus = getattr(state, 'virus', None)
        if virus and getattr(virus, 'is_active', False):
            return False
        if not GameQuery.has_virus_immunity(player):
            return False
        if getattr(player, 'is_police', False) and not getattr(player, 'is_captain', False):
            return False
        alive = [p for p in state.players.values() if p.is_alive()]
        vulnerable = sum(1 for p in alive
                        if p.player_id != player.player_id
                        and not GameQuery.has_virus_immunity(p))
        if len(alive) >= 4:
            return vulnerable >= 2
        return vulnerable >= 1

    # ════════════════════════════════════════════════════════
    #  工具
    # ════════════════════════════════════════════════════════

    @staticmethod
    def _dedup(candidates: List[str]) -> List[str]:
        seen = set()
        result = []
        for cmd in candidates:
            if cmd not in seen:
                seen.add(cmd)
                result.append(cmd)
        return result

    @staticmethod
    def _filter_invalid_moves(candidates: List[str], my_loc: str) -> List[str]:
        """过滤掉 move 到当前位置的无效指令（含 home 变体：home/home_p1/某某的家）"""
        result = []
        for cmd in candidates:
            if cmd.startswith("move "):
                dest = cmd[5:].strip()
                # 精确匹配
                if dest == my_loc:
                    continue
                # home 变体：move home 但已在家
                if dest == "home" and DecisionOrchestrator._is_home_location(my_loc):
                    continue
            result.append(cmd)
        return result

    @staticmethod
    def _is_home_location(loc: str) -> bool:
        """检查是否在任何形式的家位置"""
        return loc == "home" or loc.startswith("home_") or "家" in loc

    @staticmethod
    def _is_same_location(a: str, b: str) -> bool:
        """比较两地点是否相同（仅匹配通用 'home' 到具体 home_* 变体，
        不匹配变体到变体——home_p2 ≠ home_p6"""
        if a == b:
            return True
        # 只有通用 "home" 匹配所有 home 变体；home_p2 和 home_p6 是不同的家
        if a == "home" and DecisionOrchestrator._is_home_location(b):
            return True
        if b == "home" and DecisionOrchestrator._is_home_location(a):
            return True
        return False

    def _dbg_mind_snapshots(self, snapshots: Dict[str, Any]):
        """调试：输出所有 Mind 评估摘要 (level 2) / 完整数据 (level 3)"""
        lvl = self._get_debug_level()
        if lvl < 2:
            return
        for name, snap in snapshots.items():
            self._dbg(2, f"Mind[{name}]: {snap.summary}")
            if lvl >= 3 and snap.data:
                for k, v in snap.data.items():
                    if k in ("threat_scores", "threat_ranking"):
                        continue  # 太长，跳过
                    formatted = self._fmt_val(v, max_items=3)
                    self._dbg(3, f"  {k}: {formatted}")

    @staticmethod
    def _fmt_val(v: Any, max_items: int = 3) -> str:
        """格式化调试值，Player 对象显示 name 而非内存地址"""
        if v is None:
            return "None"
        if hasattr(v, 'name') and hasattr(v, 'player_id'):
            # Player-like 对象
            return f"Player({v.name})"
        if isinstance(v, list):
            if not v:
                return "[]"
            if len(v) > max_items:
                items = [DecisionOrchestrator._fmt_val(x, 1) for x in v[:max_items]]
                return f"[{len(v)} items] [{', '.join(items)}...]"
            items = [DecisionOrchestrator._fmt_val(x, 1) for x in v]
            return f"[{', '.join(items)}]"
        if isinstance(v, tuple):
            items = [DecisionOrchestrator._fmt_val(x, 1) for x in v]
            return f"({', '.join(items)})"
        if isinstance(v, dict):
            if not v:
                return "{}"
            items = [f"{k}={DecisionOrchestrator._fmt_val(vv, 1)}" for k, vv in list(v.items())[:max_items]]
            return f"{{{', '.join(items)}}}"
        return str(v)
