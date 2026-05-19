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

与旧架构完全独立：通过 new_arch_enabled 开关在 controller.py 中选择管道。
"""

from __future__ import annotations
from typing import Any, Callable, Dict, List, Optional, Set

from controllers.ai.strategies.base_strategy import DecisionPhase
from controllers.ai.minds.police_mind import PoliceStance
from controllers.ai.constants import (
    debug_ai_basic, debug_ai_development_plan,
    debug_ai_combat_state, debug_ai_candidate_commands,
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
        controller: Any,  # BasicAIController 实例，用于访问 helper 方法
    ):
        self._strategy = strategy
        self._goal_stack = goal_stack
        self._talent_hooks = talent_hooks
        self._minds = minds
        self._ctrl = controller

        # Orchestrator 维护的跨轮次状态
        self._threat_scores: Dict[str, float] = {}
        self._low_threat_streak: Dict[str, int] = {}
        self._in_combat: bool = False
        self._combat_target: Any = None
        self._danger_mode: bool = False
        self._been_attacked_by: Set[str] = set()

        # T3 天星补刀追踪
        self._star_prev_uses: Optional[int] = None   # 上轮剩余次数（检测是否刚发动）
        self._star_follow_up_rounds: int = 0          # 补刀标记剩余轮数

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
        name = getattr(self._ctrl, 'player_name', '?')
        prefix = {1: "  [Orch]", 2: "  [Orch·]", 3: "  [Orch··]"}.get(level, "  [Orch]")
        print(f"{prefix} {name}: {msg}")

    # ════════════════════════════════════════════════════════
    #  主入口
    # ════════════════════════════════════════════════════════

    def generate(
        self, player: Any, state: Any,
        available_actions: List[str], round_num: int,
    ) -> List[str]:
        """产出候选指令列表（与旧 _generate_candidates 同签名）。"""

        # Step 0.5: 同步 controller 状态（必须在任何 _dbg 之前，因为 _dbg 读取 _ctrl.player_name）
        self._ctrl._my_id = player.player_id
        self._ctrl.player_name = player.name
        self._ctrl._player = player
        self._ctrl._game_state = state
        self._ctrl._read_police_state(state)

        # 同步 political 降级状态（旧 mixin 方法依赖）
        if self._ctrl.personality == "political":
            self._ctrl._political_fallback_level = self._ctrl._political_should_fallback(player, state)
            self._ctrl._political_in_balanced_fallback = (self._ctrl._political_fallback_level == "full_balanced")
            self._ctrl._political_develop_only = (self._ctrl._political_fallback_level == "develop_only")
        else:
            self._ctrl._political_fallback_level = "none"
            self._ctrl._political_in_balanced_fallback = False
            self._ctrl._political_develop_only = False

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
        barrier = getattr(state, 'active_barrier', None)
        if barrier:
            in_barrier = False
            if hasattr(barrier, 'is_in_barrier'):
                in_barrier = barrier.is_in_barrier(player.player_id)
            else:
                barrier_players = getattr(barrier, 'barrier_players', [])
                in_barrier = player.player_id in barrier_players
            if in_barrier:
                available_actions = [
                    a for a in available_actions
                    if a not in ("move", "interact", "find")
                    and not a.startswith("move ")
                    and not a.startswith("interact ")
                    and not a.startswith("find ")
                    and not a.startswith("interact ")
                ]

        my_loc = self._get_location_str(player)
        self._dbg(1, f"R{round_num} 开始决策 | 位置={my_loc} | 可用: {available_actions}")

        # Step 0: 未起床
        if not player.is_awake:
            self._dbg(1, "未起床 → wake")
            return ["wake"]

        # Step 1: 天赋钩子接管
        override = self._check_talent_overrides(player, state, available_actions)
        if override is not None:
            self._dbg(1, f"天赋钩子接管 → {override}")
            return override

        # Step 2: 运行所有 Mind，收集态势快照
        snapshots = self._run_all_minds(player, state)
        self._dbg_mind_snapshots(snapshots)

        # Step 3: 清理过期目标
        if self._goal_stack:
            removed = self._goal_stack.pop_expired(player, state)
            if removed:
                self._dbg(1, f"清理过期目标: {[g.description for g in removed]}")

        # Step 4: 按 Strategy 的阶段顺序逐层决策
        candidates: List[str] = []
        phase_order = self._strategy.get_phase_order()
        handled_phases: List[str] = []

        for phase in sorted(phase_order, key=lambda p: p.value, reverse=True):
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
                        candidates.append("forfeit")
                        return self._dedup(candidates)
                else:
                    self._dbg(2, f"阶段 {phase.name} 产出全被过滤(原:{phase_cmds})，继续")
            else:
                # 调试：标注被跳过的阶段及其原因（level 2+）
                skip_reason = self._get_skip_reason(phase, snapshots, player)
                if skip_reason:
                    self._dbg(2, f"跳过 {phase.name}: {skip_reason}")

        self._dbg(1, f"阶段产出: {' → '.join(handled_phases) if handled_phases else '无'}")

        # Step 5: GoalStack 补充收尾指令
        goal_cmds = self._collect_goal_commands(player, state, available_actions, candidates)
        if goal_cmds:
            self._dbg(2, f"GoalStack补充: {goal_cmds}")
        candidates.extend(goal_cmds)

        candidates.append("forfeit")
        result = self._finalize(candidates, player, my_loc)
        # ★ 同步 Orchestrator 状态回 controller（供 LLM 社交 / get_decision_context 读取）
        self._ctrl._threat_scores = dict(self._threat_scores)
        self._ctrl._low_threat_streak = dict(self._low_threat_streak)
        self._ctrl._in_combat = self._in_combat
        self._ctrl._combat_target = self._combat_target
        self._ctrl._danger_mode = self._danger_mode
        return result

    def _finalize(self, candidates: List[str], player, my_loc: str) -> List[str]:
        """去重 + 过滤无效指令（如 move 到当前位置）"""
        deduped = self._dedup(candidates)
        filtered = self._filter_invalid_moves(deduped, my_loc)
        self._dbg(1, f"最终候选({len(filtered)}条): {filtered}")
        return filtered

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
                return "无超新星威胁"
        elif phase == DecisionPhase.EMERGENCY_TERROR:
            if not threat or not threat.data.get("terror_info"):
                return "无Terror"
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
        if talent_name and self._talent_hooks:
            hook = self._talent_hooks.get(talent_name)
            if hook:
                override = hook.should_override_candidates(player, state, available)
                if override is not None:
                    return override
        return None

    # ════════════════════════════════════════════════════════
    #  Step 2: 运行所有 Mind
    # ════════════════════════════════════════════════════════

    def _run_all_minds(self, player, state) -> Dict[str, Any]:
        """运行所有 Mind，返回 {mind_name: MindAssessment}"""
        snapshots = {}

        # 1. PoliceMind (先跑，为其他 Mind 提供 police 上下文)
        for mind in self._minds:
            mind_name = mind.__class__.__name__

            if mind_name == "PoliceMind":
                police_ctx = self._build_police_context(player, state)
                raw_result = mind.assess(
                    player, state,
                    police_cache=police_ctx.get("cache", {}),
                    threat_scores=self._threat_scores,
                    my_location=self._get_location_str(player),
                    strategy=self._strategy,
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

        police_sit_obj = None
        police_snapshot = snapshots.get("police")
        if police_snapshot:
            police_sit_obj = police_snapshot.data.get("police_situation")
        polices_cache = self._extract_police_cache_from_situation(police_sit_obj) if police_sit_obj else {}

        # 2. ThreatMind
        for mind in self._minds:
            mind_name = mind.__class__.__name__

            if mind_name == "ThreatMind":
                snapshots["threat"] = mind.assess(
                    player, state, self._strategy,
                    previous_threat_scores=self._threat_scores,
                    low_threat_streak=self._low_threat_streak,
                    been_attacked_by=self._been_attacked_by,
                    llm_aggression_mod=self._get_llm_aggression(),
                    polices_cache=polices_cache,
                    count_outer_armor_fn=self._count_outer_armor,
                    count_inner_armor_fn=self._count_inner_armor,
                    count_locked_by_fn=self._count_locked_by,
                    is_anchored_fn=self._is_anchored,
                )
                # 更新 EMA 威胁分
                self._threat_scores = snapshots["threat"].data.get("threat_scores", {})
                self._low_threat_streak = snapshots["threat"].data.get("low_threat_streak", {})
                break

        # 3. DevelopMind
        for mind in self._minds:
            if mind.__class__.__name__ == "DevelopMind":
                snapshots["develop"] = mind.assess(
                    player, state, self._strategy,
                    talent_hooks=self._talent_hooks,
                )
                break

        # 4. CombatMind
        for mind in self._minds:
            if mind.__class__.__name__ == "CombatMind":
                police_protected = self._get_police_protected_ids(state)
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
                    threat_scores=self._threat_scores,
                    combat_target=self._combat_target,
                    in_combat=self._in_combat,
                    police_protected_ids=police_protected,
                    police_stance=police_stance,
                    police_mind=police_mind_ref,
                    llm_alliance=self._get_llm_alliance(),
                    terror_defense=self._get_terror_defense(),
                    star_follow_up_rounds=self._star_follow_up_rounds,
                    llm_aggression_mod=self._get_llm_aggression(),
                )
                break

        return snapshots

    def _build_police_context(self, player, state) -> Dict:
        """构建 PoliceMind 需要的上下文"""
        return {
            "cache": self._get_police_cache(state),
        }

    @staticmethod
    def _extract_police_cache_from_situation(police_sit) -> Dict:
        """从 PoliceSituation 提取 ThreatMind 需要的 police 上下文"""
        if police_sit is None:
            return {}
        return {
            "has_police": getattr(police_sit, 'police_exists', False),
            "captain_id": getattr(police_sit, 'captain_id', None),
            "is_captain": getattr(police_sit, 'i_am_captain', False),
            "alive_count": getattr(police_sit, 'alive_units', 0),
            "active_count": getattr(police_sit, 'active_units', 0),
            "report_target": getattr(police_sit, 'i_am_report_target', False),
            "report_phase": getattr(police_sit, 'report_phase', 'idle'),
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
        cmds = self._cmd_virus(player, state, available)
        if cmds:
            # 推入持久化目标
            if self._goal_stack:
                from controllers.ai.goals.virus_goal import VirusCureGoal
                goal = VirusCureGoal(
                    preferred_location=self._pick_virus_cure_location(player, state),
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

        my_loc = self._get_location_str(player)
        same_loc = self._get_same_location_targets(player, state)
        if len(same_loc) < 2:
            return []

        cmds = []
        empty_locs = [
            loc for loc in ["home", "商店", "医院", "魔法所", "军事基地", "警察局"]
            if loc != my_loc and self._count_enemies_at(loc, player, state) == 0
        ]
        if empty_locs and "move" in available:
            import random
            dest = random.choice(empty_locs)
            cmds.append(f"move {dest}")
        return cmds

    # ── Terror 紧急集火 ──
    def _handle_emergency_terror(self, player, state, available, snapshots, round_num) -> List[str]:
        threat = snapshots.get("threat")
        if not threat:
            return []
        terror_info = threat.data.get("terror_info")
        if not terror_info:
            return []

        # ★ 星野玩家已在 ThreatMind._detect_terror_threat() 中排除，此处无需再检查

        target = terror_info["target"]
        debug_ai_basic(player.name, f"Terror威胁！紧急集火 {target.name}")

        combat = snapshots.get("combat")
        if combat and combat.data.get("combat_ready"):
            cmds = combat.data["combat_commands"]
            if cmds:
                self._push_combat_goal(target, player, round_num, priority=9)
                return cmds

        return []

    # ── 危险模式 ──
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
                safe_loc = self._pick_safe_destination(player, state)
                if safe_loc:
                    from controllers.ai.goals.flee_goal import FleeGoal
                    flee = FleeGoal(destination=safe_loc, debug_name=player.name)
                    flee.set_round(round_num)
                    self._goal_stack.push(flee)

        if self._danger_mode:
            if self._is_danger_resolved(player):
                debug_ai_basic(player.name, "危险解除")
                self._danger_mode = False
                if self._goal_stack:
                    self._goal_stack.resume_all()
                return []

            # 仍在危险中
            return self._cmd_danger_develop(player, state, available)

        # ════════════════════════════════════════════════════════
        #  RESIST 消费：警察态度=resist，但无AOE武器 → 获取AOE
        #  有AOE时 fallthrough 给 COMBAT 阶段（评分系统会处理队长加分）
        # ════════════════════════════════════════════════════════
        police_snap = snapshots.get("police")
        if police_snap:
            police_sit = police_snap.data.get("police_situation")
            if police_sit:
                try:
                    from controllers.ai.minds.police_mind import PoliceMind as PM
                    stance = getattr(police_sit, 'recommended_stance', None)
                    if stance == PoliceStance.RESIST:
                        has_any_aoe = PM.has_any_aoe(player)
                        if has_any_aoe:
                            self._dbg(2, "RESIST: 已有AOE，交COMBAT处理")
                        else:
                            self._dbg(2, "RESIST: 无AOE，获取AOE武器")
                            target_armor_attrs = self._get_all_protected_armor_attrs(state, player)
                            # 找到 PoliceMind 实例
                            for pm in self._minds:
                                if pm.__class__.__name__ == "PoliceMind":
                                    aoe_cmds = pm.get_aoe_acquisition_commands(
                                        player, state, available,
                                        target_armor_attrs=target_armor_attrs,
                                        my_location=self._get_location_str(player),
                                        has_pass=getattr(player, 'has_military_pass', False),
                                        learned_spells=getattr(player, 'learned_spells', set()),
                                    )
                                    if aoe_cmds:
                                        return aoe_cmds
                                    break
                except Exception:
                    pass

        return []

    # ── 队长指挥 / Political 警察建设 ──
    def _handle_captain(self, player, state, available, snapshots, round_num) -> List[str]:
        # 队长：警察指挥
        if getattr(player, 'is_captain', False) and "police_command" in available:
            self._dbg(2, "队长: 生成警察指挥")
            cmds = self._cmd_captain(player, state, available)
            if cmds:
                if self._goal_stack:
                    from controllers.ai.goals.captain_goal import CaptainGoal
                    goal = CaptainGoal(
                        cmd_captain_fn=self._cmd_captain,
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
                        cmds = self._cmd_police_political(player, state, available)
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

        # ★ RESIST + 有队长目标：切换战斗目标为队长（优先级超越当前战斗）
        police_snap = snapshots.get("police")
        resist_active = False
        if police_snap:
            police_sit = police_snap.data.get("police_situation")
            if police_sit:
                try:
                    stance = getattr(police_sit, 'recommended_stance', None)
                    resist_active = (stance == PoliceStance.RESIST)
                except Exception:
                    pass

        if resist_active and self._in_combat and self._combat_target:
            # 队长存在且可触及 → 切换目标
            if not getattr(self._combat_target, 'is_captain', False):
                captain = self._find_captain_in_viable_targets(combat, state)
                if captain and self._can_reach_target(player, state, captain):
                    self._dbg(1, f"RESIST: 切换目标 {self._combat_target.name} → 队长 {captain.name}")
                    self._combat_target = captain

        # 如果正在战斗中
        if self._in_combat and self._combat_target:
            if self._should_continue_combat(player, self._combat_target):
                combat_cmds = combat.data.get("combat_commands", [])
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
                    return self._cmd_rearm(player, state, available)

        # ★ RESIST stance: 强制进入战斗，不管发育是否完成
        if not self._in_combat and resist_active and combat.data.get("combat_ready"):
            combat_cmds = combat.data.get("combat_commands", [])
            if combat_cmds:
                best_target = combat.data.get("best_target")
                # RESIST 下：队长优先（即使评分不是最高，只要可打穿就选队长）
                # 但结界内只能攻击同地点目标——队长在结界外则跳过
                captain = self._find_captain_in_viable_targets(combat, state)
                if captain and self._can_damage_via_combat(combat, captain):
                    if self._can_reach_target(player, state, captain):
                        best_target = captain
                        self._dbg(1, f"RESIST: 队长优先 → {captain.name}")
                    else:
                        self._dbg(2, f"RESIST: 队长 {captain.name} 不可达（结界/位置不同），跳过")
                if best_target:
                    self._dbg(1, f"RESIST: 强制进入战斗 → {best_target.name}")
                    self._in_combat = True
                    self._combat_target = best_target
                    self._push_combat_goal(best_target, player, round_num)
                    # 如果目标切换了，需要重新生成攻击指令
                    if best_target != combat.data.get("best_target"):
                        from controllers.ai.minds.combat_mind import CombatMind as CM
                        cm = CM(debug_name=player.name)
                        pe = getattr(state, 'police_engine', None)
                        protected = {pid for pid in state.player_order
                                     if pe and pe.is_protected_by_police(pid)} if pe else set()
                        weapon = cm._pick_weapon(player, best_target, police_protected_ids=protected)
                        if weapon:
                            combat_cmds = cm._build_attack_commands(player, best_target, weapon, state)
                    return combat_cmds

        # 发育完成后尝试攻击
        develop = snapshots.get("develop")
        dev_complete = develop.data.get("development_complete") if develop else False
        if dev_complete and combat.data.get("combat_ready"):
            combat_cmds = combat.data["combat_commands"]
            if combat_cmds:
                best_target = combat.data.get("best_target")
                if best_target:
                    self._in_combat = True
                    self._combat_target = best_target
                    self._push_combat_goal(best_target, player, round_num)
                return combat_cmds

        return []

    # ── 击杀机会 ──
    def _handle_kill_opportunity(self, player, state, available, snapshots, round_num) -> List[str]:
        threat = snapshots.get("threat")
        combat = snapshots.get("combat")
        if not threat or not combat:
            return []

        kill_targets = threat.data.get("kill_targets", [])
        if not kill_targets:
            return []

        # 拿第一个可击杀目标
        for kt in kill_targets:
            target = kt.get("target")
            if target and combat.data.get("combat_ready"):
                self._push_combat_goal(target, player, round_num, priority=7)
                cmds = combat.data["combat_commands"]
                if cmds:
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

        cmds = []
        my_loc = self._get_location_str(player)

        # 当前地点可交互
        current_interact = develop.data.get("current_location_actions", [])
        if current_interact and "interact" in available:
            cmds.extend(current_interact)
            self._dbg(2, f"发育: 本地交互 {current_interact}")
        elif current_interact and "interact" not in available:
            self._dbg(2, f"发育: 有本地交互{current_interact} 但 interact 不可用")

        # 蓄力武器
        charge_cmds = self._get_charge_commands(player, available)
        for c in charge_cmds:
            if c not in cmds:
                cmds.append(c)
                self._dbg(2, f"发育: 蓄力 {c}")

        # 磨刀（通用：有任何AI持小刀+磨刀石就磨）
        if "special" in available:
            for w in getattr(player, 'weapons', []):
                if w and w.name == "小刀" and getattr(w, 'base_damage', 0) < 2:
                    if any(getattr(it, 'name', '') == "磨刀石" for it in getattr(player, 'items', [])):
                        cmd = "special 磨刀"
                        if cmd not in cmds:
                            cmds.append(cmd)
                            self._dbg(2, "发育: 磨刀")
                        break

        # 移动到最优地点（仅当没有其他指令时）
        if not cmds:
            best_move = develop.data.get("best_move")
            if best_move and "move" in available:
                dest = best_move.replace("move ", "")
                if not self._is_same_location(dest, my_loc):
                    cmds.append(best_move)
                    self._dbg(2, f"发育: 移动到最优地点 {dest}")
                else:
                    self._dbg(2, f"发育: 最优地点={dest} 等于当前位置，跳过移动")

        # 发育受阻 → 转为进攻 或 兜底目的地
        if not cmds and not develop.data.get("development_complete"):
            personality = self._strategy.personality_name
            if personality in ("aggressive", "assassin", "balanced"):
                combat = snapshots.get("combat")
                if combat and combat.data.get("combat_ready"):
                    self._dbg(2, "发育: 受阻 → 转为进攻")
                    return combat.data["combat_commands"]

            fallback_loc = self._pick_fallback_destination(player, state)
            if fallback_loc and "move" in available:
                if not self._is_same_location(fallback_loc, my_loc):
                    cmds.append(f"move {fallback_loc}")
                    self._dbg(2, f"发育: 受阻 → 兜底移动到 {fallback_loc}")
                else:
                    self._dbg(2, f"发育: 兜底={fallback_loc} 等于当前位置，跳过移动")

        if not cmds:
            self._dbg(2, "发育: 无可行指令")
        return cmds

    # ── 兜底 ──
    def _handle_fallback(self, player, state, available, snapshots, round_num) -> List[str]:
        """所有阶段均无产出时的兜底行为"""
        cmds: List[str] = []
        my_loc = self._get_location_str(player)

        # 1. 当前地点可交互
        develop = snapshots.get("develop")
        if develop:
            current_actions = develop.data.get("current_location_actions", [])
            if current_actions and "interact" in available:
                cmds.extend(current_actions)
                self._dbg(2, f"兜底: 本地交互 {current_actions}")

        # 2. 移动到安全地点
        if not cmds and "move" in available:
            safe_loc = self._pick_fallback_destination(player, state)
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
    #  Delegation to controller helpers
    #  (这些方法委托给旧的 controller 实例，避免重复实现)
    # ════════════════════════════════════════════════════════

    def _get_location_str(self, player) -> str:
        return self._ctrl._get_location_str(player)

    def _count_outer_armor(self, player) -> int:
        return self._ctrl._count_outer_armor(player)

    def _count_inner_armor(self, player) -> int:
        return self._ctrl._count_inner_armor(player)

    def _count_locked_by(self, player, state) -> int:
        return self._ctrl._count_locked_by(player, state)

    def _is_anchored(self, player, state) -> bool:
        return self._ctrl._is_anchored(player, state)

    def _get_same_location_targets(self, player, state) -> List:
        return self._ctrl._get_same_location_targets(player, state)

    def _count_enemies_at(self, loc, player, state) -> int:
        return self._ctrl._count_enemies_at(loc, player, state)

    def _get_police_cache(self, state):
        return self._ctrl._police_cache or {}

    def _get_llm_aggression(self) -> float:
        return getattr(self._ctrl, '_llm_aggression_mod', 0.0)

    def _get_llm_alliance(self) -> Set[str]:
        return getattr(self._ctrl, '_llm_alliance', set())

    def _get_terror_defense(self):
        return getattr(self._ctrl, '_terror_defense', None)

    def _get_police_protected_ids(self, state) -> Set[str]:
        protected = set()
        pe = getattr(state, 'police_engine', None)
        if pe:
            for pid in state.player_order:
                if pe.is_protected_by_police(pid):
                    protected.add(pid)
        return protected

    def _get_all_protected_armor_attrs(self, state, player) -> set:
        """收集所有警察保护目标的护甲属性（用于AOE获取决策）"""
        attrs: set = set()
        from utils.attribute import Attribute
        pe = getattr(state, 'police_engine', None)
        if not pe:
            return attrs
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            t = state.get_player(pid)
            if not t or not t.is_alive() or not pe.is_protected_by_police(pid):
                continue
            outer = self._ctrl._get_outer_armor_attr(t)
            if outer:
                attrs.update(outer)
            else:
                inner = self._ctrl._get_inner_armor_attr(t)
                attrs.update(inner)
        return attrs

    def _has_talent(self, player, name: str) -> bool:
        t = getattr(player, 'talent', None)
        return t is not None and getattr(t, 'name', '') == name

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
                return score > -400  # -500 = 打不了，> -400 即视为可打击
        return False

    @staticmethod
    def _can_reach_target(player, state, target) -> bool:
        """检查玩家能否接触到目标（结界内无法攻击结界外目标）"""
        barrier = getattr(state, 'active_barrier', None)
        if barrier:
            in_barrier = False
            if hasattr(barrier, 'is_in_barrier'):
                in_barrier = barrier.is_in_barrier(player.player_id)
            else:
                barrier_players = getattr(barrier, 'barrier_players', [])
                in_barrier = player.player_id in barrier_players
            if in_barrier:
                # 结界内只能打同在结界内的目标
                if hasattr(barrier, 'barrier_players'):
                    return target.player_id in barrier.barrier_players
                return False
        return True  # 不在结界内，无限制

    def _cmd_virus(self, player, state, available) -> List[str]:
        return self._ctrl._cmd_virus(player, state, available)

    def _cmd_captain(self, player, state, available) -> List[str]:
        return self._ctrl._cmd_captain(player, state, available)

    def _cmd_police_political(self, player, state, available) -> List[str]:
        return self._ctrl._cmd_police_political(player, state, available)

    def _cmd_danger_develop(self, player, state, available) -> List[str]:
        return self._ctrl._cmd_danger_develop(player, state, available)

    def _cmd_rearm(self, player, state, available) -> List[str]:
        return self._ctrl._cmd_rearm(player, state, available)

    def _is_danger_resolved(self, player) -> bool:
        return self._ctrl._is_danger_resolved(player)

    def _should_continue_combat(self, player, target) -> bool:
        return self._ctrl._should_continue_combat(player, target)

    def _pick_virus_cure_location(self, player, state) -> str:
        return self._ctrl._pick_virus_cure_location(player, state)

    def _pick_safe_destination(self, player, state) -> Optional[str]:
        return self._ctrl._pick_safe_armor_destination(player, state)

    def _pick_fallback_destination(self, player, state) -> Optional[str]:
        return self._ctrl._pick_fallback_destination(player, state)

    def _get_charge_commands(self, player, available) -> List[str]:
        """获取武器蓄力指令"""
        cmds = []
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
