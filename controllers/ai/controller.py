"""
BasicAIController —— 基础AI控制器（v2.0 Mixin 重构版）
═══════════════════════════════════════════════════════
原 controllers/ai_basic.py 拆分为 Mixin 模块后的主入口。
"""
from typing import List, Dict, Optional, Any
import random

from controllers.base import PlayerController
from controllers.ai.constants import (
    NEED_PROVIDERS,
    debug_ai_basic, debug_ai_detailed, debug_ai_candidate_commands,
    debug_ai_combat_state, debug_ai_development_plan,
)
from controllers.ai.helpers_mixin import HelpersMixin
from controllers.ai.hoshino_mixin import HoshinoMixin
from controllers.ai.evaluation_mixin import EvaluationMixin
from controllers.ai.choose_mixin import ChooseMixin
from controllers.ai.combat_mixin import CombatMixin
from controllers.ai.develop_mixin import DevelopMixin
from controllers.ai.police_mixin import PoliceMixin
from controllers.ai.events_mixin import EventsMixin

# 新架构模块（组合优于继承）
from controllers.ai.evaluation import infer_aoe_weapon
from controllers.ai.game_query import GameQuery
from controllers.ai.ai_state import AIState
from controllers.ai.minds.police_mind import PoliceMind, PoliceSituation, PoliceStance
from controllers.ai.minds.threat_mind import ThreatMind
from controllers.ai.minds.develop_mind import DevelopMind
from controllers.ai.minds.combat_mind import CombatMind
from controllers.ai.talents.terror_defense import TerrorDefenseAI
from controllers.ai.goals.base_goal import GoalStack
from controllers.ai.goals.develop_goal import DevelopGoal
from controllers.ai.goals.combat_goal import CombatGoal
from controllers.ai.goals.flee_goal import FleeGoal
from controllers.ai.goals.virus_goal import VirusCureGoal
from controllers.ai.goals.captain_goal import CaptainGoal
from controllers.ai.goals.political_goal import PoliticalGoal
from controllers.ai.strategies.registry import create_strategy
from controllers.ai.talents.base_hook import BaseTalentAIHook
from controllers.ai.talents.hoshino_hook import HoshinoAIHook
from controllers.ai.talents.g1_g2_g4_hooks import HologramAIHook, SaviorAIHook, FireflyAIHook
from controllers.ai.talents.g3_mythland_hook import MythlandAIHook
from controllers.ai.talents.t1_oneslash_hook import OneSlashAIHook
from controllers.ai.talents.t3_star_hook import StarAIHook
from controllers.ai.talents.t4_hexagram_hook import HexagramAIHook
from controllers.ai.talents.g5_ripple_hook import RippleAIHook

# 新架构决策编排器
from controllers.ai.orchestrator import DecisionOrchestrator

# 诊断收集器
from controllers.ai.diagnostics import DiagCollector



class BasicAIController(
    HoshinoMixin, # type: ignore
    HelpersMixin, # type: ignore
    EvaluationMixin, # type: ignore
    ChooseMixin, # type: ignore
    CombatMixin, # type: ignore
    DevelopMixin, # type: ignore
    PoliceMixin, # type: ignore
    EventsMixin, # type: ignore
    PlayerController,
):
    """
    基础AI：按阶段判定 + 候选命令优先级 + validate 过滤。
    6种人格: balanced, aggressive, defensive, political, assassin, builder
    """

    # ════════════════════════════════════════════════════════
    #  __init__ (原 lines 199-246)
    # ════════════════════════════════════════════════════════

    def __init__(
        self,
        personality: str = "balanced",
        diag_enabled: bool = False,
    ):
        super().__init__()
        self.personality = personality
        self._new_arch_enabled = True  # C7: 始终新架构，保留属性以兼容旧引用
        self.event_log: List[Dict] = []
        self._round_number = 0

        # 内部记忆
        self._threat_scores: Dict[str, float] = {}
        self._been_attacked_by: set = set()
        self._my_kills: int = 0
        self._consecutive_forfeits: int = 0
        self._last_action: Optional[str] = None
        self._develop_plan: List[str] = []
        self._attempt_index: int = 0
        self.player_name: Optional[str] = None
        self._my_id: Optional[str] = None
        self._combat_target = None
        self._in_combat = False
        self._danger_mode = False

        # 导弹相关
        self._missile_cooldown = 0

        # 引用缓存
        self._player = None
        self._game_state = None

        # 警察状态缓存
        self._police_cache: Optional[Dict] = None
        self._current_phase: str = "development"
        self._last_commands: List[str] = []
        self._should_become_captain_flag: bool = False

        self._virus_active: bool = False
        self._virus_location: Optional[str] = None

        # 警察发育状态追踪（political 队长用）
        self._police_dev_assignments: Dict[str, Dict] = {}
        self._police_dev_initialized = False

        self._low_threat_streak: Dict[str, int] = {}
        self._players_who_attacked: set = set()

        self._last_combat_location = None
        self._combat_just_ended_at = None

        # 病毒预防标记（每局一次）
        self._virus_prevention_done: bool = False
        # 行动标记（轮次内）
        self._action_used: bool = False
        # EMR蓄力标记（全息影像发动前）
        self._emr_needs_charge_before_hologram: bool = False

        # 星野战术宏队列
        self._hoshino_macro_queue: Optional[list] = None
        # 星野反队长两阶段标记
        self._hoshino_anti_captain_approached: bool = False
        self._hoshino_anti_captain_target_id: Optional[str] = None

        # 初始化 DevelopMixin 所需的 fallback level 属性
        self._political_fallback_level = "none"

        # ════════════════════════════════════════════════════════
        #  新架构开关：通过构造函数参数 new_arch_enabled 控制
        #  False时：不创建任何新模块，hasattr检查自然回退到旧行为
        # ════════════════════════════════════════════════════════

        # 诊断收集器（显式启用诊断时创建，与架构无关）
        self._diag: Optional[DiagCollector] = (
            DiagCollector() if diag_enabled else None
        )

        # ════════════════════════════════════════════════════════
        #  新架构模块：仅在 _new_arch_enabled=True 时创建
        #  禁用时 hasattr 自然返回 False，所有代码回退到旧行为
        # ════════════════════════════════════════════════════════
        self._talent_hook_instances = {}
        self._decision_log: List[Dict] = []
        if self._new_arch_enabled:
            self._ai_state = AIState()
            self._query = GameQuery()
            self._police_mind = PoliceMind(debug_name="AI", query=self._query)
            self._goal_stack = GoalStack(max_goals=5)
            self._strategy = create_strategy(personality)
            self._talent_hook_instances = {
                "大叔我啊，剪短发了": HoshinoAIHook(self),
                "请一直，注视着我": HologramAIHook(self),
                "愿负世，照拂黎明": SaviorAIHook(self),
                "火萤IV型-完全燃烧": FireflyAIHook(self),
                "神话之外": MythlandAIHook(self),
                "一刀缭断": OneSlashAIHook(self),
                "天星": StarAIHook(self),
                "六爻": HexagramAIHook(self),
                "往世的涟漪": RippleAIHook(self),
            }
            self._terror_defense = TerrorDefenseAI(debug_name="AI")
            self._ai_state.terror_defense = self._terror_defense

            # ════════════════════════════════════════════════════════
            #  ★ 新架构 DecisionOrchestrator：替代旧瀑布流的独立管道
            #  包含所有 Mind 实例 + Orchestrator 编排器
            # ════════════════════════════════════════════════════════
            self._minds = [
                PoliceMind(debug_name="AI", query=self._query),
                ThreatMind(debug_name="AI", query=self._query),
                DevelopMind(debug_name="AI", query=self._query),
                CombatMind(debug_name="AI", query=self._query),
            ]
            self._orchestrator = DecisionOrchestrator(
                strategy=self._strategy,
                goal_stack=self._goal_stack,
                talent_hooks=self._talent_hook_instances,
                minds=self._minds,
                controller=self,
                ai_state=self._ai_state,
                query=self._query,
                personality=personality,
            )

        # ════════════════════════════════════════════════════
        #  LLM 行为调整接口（由 AIChatModule 通过 [ADJUST] 写入）
        #  - _llm_alliance: LLM认为的盟友集合 → _pick_target 降低对其的攻击优先级
        #  - _llm_aggression_mod: LLM调整的攻击倾向 [-20, +20] → 影响目标选择评分
        # ════════════════════════════════════════════════════
        self._llm_alliance_local: set = set()
        self._llm_aggression_mod_local: float = 0.0
        if hasattr(self, '_ai_state'):
            self._ai_state.llm_alliance = self._llm_alliance_local
            self._ai_state.llm_aggression_mod = self._llm_aggression_mod_local


    @property
    def _llm_alliance(self) -> set:
        if self._uses_new_arch_events():
            return self._ai_state.llm_alliance
        return getattr(self, '_llm_alliance_local', set())

    @_llm_alliance.setter
    def _llm_alliance(self, value) -> None:
        normalized = set(value or set())
        self._llm_alliance_local = normalized
        if hasattr(self, '_ai_state'):
            self._ai_state.llm_alliance = normalized

    @property
    def _llm_aggression_mod(self) -> float:
        if self._uses_new_arch_events():
            return self._ai_state.llm_aggression_mod
        return getattr(self, '_llm_aggression_mod_local', 0.0)

    @_llm_aggression_mod.setter
    def _llm_aggression_mod(self, value) -> None:
        try:
            normalized = float(value)
        except (TypeError, ValueError):
            normalized = 0.0
        self._llm_aggression_mod_local = normalized
        if hasattr(self, '_ai_state'):
            self._ai_state.llm_aggression_mod = normalized


    # ════════════════════════════════════════════════════════
    #  对外只读决策上下文（供 LLM 战略社交模块读取）
    # ════════════════════════════════════════════════════════

    def get_decision_context(self) -> Dict[str, Any]:
        """供 LLM 读取的决策上下文（只读，不影响 AI 行为）。

        所有可变集合都会做浅拷贝/转列表，确保外部修改不会影响内部状态。
        """
        combat_target_name = None
        if self._combat_target is not None:
            combat_target_name = getattr(self._combat_target, "name", None)

        candidates: List[str] = []
        if hasattr(self, "_candidates") and self._candidates:
            try:
                candidates = list(self._candidates)
            except Exception:
                candidates = []

        return {
            "personality": self.personality,
            "current_phase": self._current_phase,
            "in_combat": self._in_combat,
            "combat_target": combat_target_name,
            "danger_mode": self._danger_mode,
            "threat_scores": dict(self._threat_scores),
            "last_action": self._last_action,
            "candidates": candidates,
            "develop_plan": list(self._develop_plan),
            "consecutive_forfeits": self._consecutive_forfeits,
            "my_kills": self._my_kills,
            "been_attacked_by": list(self._been_attacked_by),
            # 新增：LLM 可读写的状态
            "llm_alliance": list(getattr(self, '_llm_alliance', set())),
            "llm_aggression_mod": getattr(self, '_llm_aggression_mod', 0.0),
        }


    # ════════════════════════════════════════════════════════
    #  新旧架构事件入口分发
    # ════════════════════════════════════════════════════════

    # C7: 事件路径统一为新架构（_on_*_new），旧 EventsMixin 路径已移除

    def _uses_new_arch_events(self) -> bool:
        """C7 后恒为 True；保留方法因 _llm_* property 与 cutaway 路径仍引用。"""
        return hasattr(self, '_ai_state')

    def on_event(self, event: Dict) -> None:
        self._on_event_new(event)

    def on_round_start(self, player, state, round_number: int):
        self._on_round_start_new(player, state, round_number)

    def on_round_end(self, player, state, round_number: int):
        self._on_round_end_new(player, state, round_number)

    def on_damaged(self, player, attacker_name: str, damage: float):
        self._on_damaged_new(player, attacker_name, damage)

    def on_player_killed(self, player, killed_name: str, killer_name: str):
        self._on_player_killed_new(player, killed_name, killer_name)

    def respond_to_event(self, player, state, event_type: str,
                         event_data: dict) -> Optional[str]:
        return self._respond_to_event_new(player, state, event_type, event_data)

    def get_debug_info(self, player) -> dict:
        return self._get_debug_info_new(player)


    # ════════════════════════════════════════════════════════
    #  接口实现：get_command (原 lines 282-308)
    # ════════════════════════════════════════════════════════

    def get_command(
        self, player: Any, game_state: Any,
        available_actions: List[str], context: Optional[Dict] = None
    ) -> str:
        self.player_name = player.name
        self._my_id = player.player_id
        # 同步新架构模块的调试名称
        if hasattr(self, '_police_mind'):
            self._police_mind._debug_name = player.name
        if hasattr(self, '_terror_defense'):
            self._terror_defense._debug_name = player.name
        # 同步所有 Mind 的调试名称
        for mind in getattr(self, '_minds', []):
            mind._debug_name = player.name
        attempt = context.get("attempt", 1) if context else 1
        situation = (context or {}).get("situation", "")

        # v2.0 StageAI: 舞台内决策接管（正常模式 + duet 模式）
        ish = getattr(game_state, 'ish_bosheth', None)
        if ish and ish.phase in ("active", "duet") and attempt == 1:
            from controllers.ai.stage import StageAI
            ctx = context or {}
            ctx.setdefault("threat_scores", getattr(self, '_threat_scores', {}))
            cmd = StageAI.get_command(player, game_state, available_actions, ctx)
            if cmd:
                self._candidates = [cmd]
                self._attempt_index = 0
                return cmd

        # 星野战术宏输入：从预生成队列逐条弹出
        if situation == "hoshino_tactical_input":
            available_action_names = [
                action.get("usage", "") if isinstance(action, dict) else action
                for action in available_actions
            ]
            return self._hoshino_get_tactical_command(player, game_state, available_action_names)

        # 星野排弹：计算最优弹药顺序并返回（在战术宏执行期间被调用）
        if situation == "hoshino_reorder_ammo":
            target = self._combat_target
            # 反队长宏中 _combat_target 可能未指向队长，优先用专用标记
            if not target:
                cap_id = getattr(self, '_hoshino_anti_captain_target_id', None)
                if cap_id:
                    target = game_state.get_player(cap_id)
            if target:
                optimal = self._hoshino_compute_optimal_ammo_order(player, target)
                if optimal and len(optimal) == len(available_actions):
                    return " ".join(str(i) for i in optimal)
            # 兜底：返回当前顺序
            return " ".join(str(i+1) for i in range(len(available_actions)))

        # 插入式笑话：使用专用候选生成器
        if (context or {}).get("cutaway_joke"):
            if attempt == 1:
                self._candidates = self._generate_cutaway_candidates(
                    player, game_state, available_actions, context
                )
                self._attempt_index = 0
                debug_ai_candidate_commands(self._pname(),
                    [f"插入式笑话候选命令（共{len(self._candidates)}条）"])
                for i, cmd in enumerate(self._candidates, 1):
                    debug_ai_detailed(player.name, f"   {i}. {cmd}")
            else:
                self._attempt_index += 1

            if self._attempt_index < len(self._candidates):
                cmd = self._candidates[self._attempt_index]
                debug_ai_basic(player.name, f"插入式笑话尝试第{attempt}条：{cmd}")
                if str(cmd).strip().lower() == "forfeit" and self._diag:
                    self._diag.record_forfeit(
                        round_num=self._round_number,
                        player_name=player.name,
                        talent=getattr(getattr(player, 'talent', None), 'name', ''),
                        personality=self.personality,
                        reason="direct_forfeit",
                        candidates=list(self._candidates) if hasattr(self, '_candidates') else [],
                        available_actions=list(available_actions) if available_actions else [],
                        reject_reasons=[],
                    )
            else:
                cmd = "forfeit"
                debug_ai_basic(player.name, "插入式笑话候选耗尽，兜底forfeit")
                if self._diag:
                    self._diag.record_forfeit(
                        round_num=self._round_number,
                        player_name=player.name,
                        talent=getattr(getattr(player, 'talent', None), 'name', ''),
                        personality=self.personality,
                        reason="direct_forfeit",
                        candidates=list(self._candidates) if hasattr(self, '_candidates') else [],
                        available_actions=list(available_actions) if available_actions else [],
                        reject_reasons=[],
                    )
            return cmd

        if attempt == 1:
            # C7: 始终走新架构 Orchestrator
            self._candidates = self._orchestrator.generate(
                player, game_state, available_actions,
                getattr(game_state, 'current_round', 0)
            )
            if hasattr(self, '_ai_state'):
                self._threat_scores = self._ai_state.threat_scores
                self._been_attacked_by = self._ai_state.been_attacked_by
                self._players_who_attacked = self._ai_state.players_who_attacked
                self._in_combat = self._ai_state.in_combat
                self._combat_target = self._ai_state.combat_target
                self._danger_mode = self._ai_state.danger_mode
            self._attempt_index = 0
            debug_ai_candidate_commands(self._pname(),
                [f"候选命令列表（共{len(self._candidates)}条）"])
            for i, cmd in enumerate(self._candidates, 1):
                debug_ai_detailed(player.name, f"   {i}. {cmd}")
        else:
            self._attempt_index += 1

        if self._attempt_index < len(self._candidates):
            cmd = self._candidates[self._attempt_index]
            debug_ai_basic(player.name, f"尝试第{attempt}条：{cmd}")
            if str(cmd).strip().lower() == "forfeit" and self._diag:
                self._diag.record_forfeit(
                    round_num=self._round_number,
                    player_name=player.name,
                    talent=getattr(getattr(player, 'talent', None), 'name', ''),
                    personality=self.personality,
                    reason="direct_forfeit",
                    candidates=list(self._candidates) if hasattr(self, '_candidates') else [],
                    available_actions=list(available_actions) if available_actions else [],
                    reject_reasons=[],
                )
        else:
            cmd = "forfeit"
            debug_ai_basic(player.name, "候选耗尽，兜底forfeit")
            if self._diag:
                self._diag.record_forfeit(
                    round_num=self._round_number,
                    player_name=player.name,
                    talent=getattr(getattr(player, 'talent', None), 'name', ''),
                    personality=self.personality,
                    reason="candidate_exhausted",
                    candidates=list(self._candidates) if hasattr(self, '_candidates') else [],
                    available_actions=list(available_actions) if available_actions else [],
                    reject_reasons=[],
                )
        return cmd

    def export_diagnostics(self) -> Dict:
        """导出诊断数据供 stats_runner 调用。"""
        if self._diag:
            return self._diag.export_summary()
        return {}

    # ════════════════════════════════════════════════════════
    #  choose：新架构天赋钩子分发（从 ChooseMixin 提升）
    # ════════════════════════════════════════════════════════

    def choose(
        self, prompt: str, options: List[str],
        context: Optional[Dict] = None
    ) -> str:
        situation = (context or {}).get("situation", "")

        # ★ 天赋AI钩子分发：天赋覆盖 choose() 决策
        # 返回非 None 时直接采用，返回 None 时走旧逻辑（super().choose() → ChooseMixin）
        talent_name = getattr(getattr(self._player, 'talent', None), 'name', '')
        if talent_name and self._new_arch_enabled:
            hook_instances = getattr(self, '_talent_hook_instances', {})
            hook = hook_instances.get(talent_name)
            if hook and hasattr(hook, 'handle_choose'):
                hook_ctx = {
                    "situation": situation,
                    "personality": getattr(self, 'personality', 'balanced'),
                    "threat_scores": getattr(self, '_threat_scores', {}),
                    "been_attacked_by": getattr(self, '_been_attacked_by', set()),
                    "combat_target": getattr(self, '_combat_target', None),
                    "danger_mode": getattr(self, '_danger_mode', False),
                    "police_cache": getattr(self, '_police_cache', None),
                    **(context or {}),
                }
                result = hook.handle_choose(
                    self._player, self._game_state, situation, options, hook_ctx)
                if result is not None:
                    return result

        return super().choose(prompt, options, context)

    # ════════════════════════════════════════════════════════
    #  插入式笑话专用：候选命令生成
    # ════════════════════════════════════════════════════════

    def _generate_cutaway_candidates(self, player, state, available_actions, context):
        """G6 插入式笑话专用候选生成器。

        优先级：
        1. 借用队长的警察指令（移动警察/指定执法目标）
        2. 借用来源玩家的攻击打高威胁目标
        3. 借用来源玩家的 interact 发育（拿自己需要但当前位置没有的东西）
        """
        self._my_id = player.player_id
        self.player_name = player.name
        self._player = player
        self._game_state = state

        if self._uses_new_arch_events():
            self._expose_ai_state_for_legacy_views()
        self._update_threat_scores(player, state)
        self._read_police_state(state)
        if self._uses_new_arch_events():
            self._sync_legacy_views_to_ai_state()
            self._cleanup_dead_players_new(state)
            self._expose_ai_state_for_legacy_views()
        else:
            self._cleanup_dead_players(state)

        source_lookup = context.get("source_lookup", {})
        collected_actions = context.get("collected_actions", [])
        candidates = []

        # ===== 优先级 1：借用队长的警察指令 =====
        captain_cmds = self._cutaway_captain_commands(player, state, source_lookup)
        if captain_cmds:
            candidates.extend(captain_cmds)

        # ===== 优先级 2：借用攻击（不处于危险状态时）=====
        if not self._is_critical(player, state):
            attack_cmds = self._cutaway_attack_commands(
                player, state, source_lookup, collected_actions)
            if attack_cmds:
                candidates.extend(attack_cmds)

        # ===== 优先级 3：借用 interact 发育 =====
        develop_cmds = self._cutaway_develop_commands(
            player, state, source_lookup, collected_actions)
        if develop_cmds:
            for cmd in develop_cmds:
                if cmd not in candidates:
                    candidates.append(cmd)

        candidates.append("forfeit")

        # 去重
        seen = set()
        deduped = []
        for cmd in candidates:
            if cmd not in seen:
                seen.add(cmd)
                deduped.append(cmd)
        return deduped

    # ---- 插入式笑话：队长指令 ----
    def _cutaway_captain_commands(self, player, state, source_lookup):
        """如果有队长在来源玩家中，生成警察指令"""
        commands = []
        pc = self._police_cache or {}
        captain_id = pc.get("captain_id")
        if not captain_id:
            return commands

        # 检查队长是否在来源玩家中（如果自己就是队长，跳过此检查）
        if captain_id != player.player_id:
            captain_in_sources = (
                captain_id in source_lookup.get("police_command", [])
                or captain_id in source_lookup.get("designate", [])
                or captain_id in source_lookup.get("study", [])
            )
            if not captain_in_sources:
                return commands

        captain = state.get_player(captain_id)
        if not captain or not captain.is_alive():
            return commands

        units = pc.get("units", [])
        alive_units = [u for u in units if u.get("is_alive")]
        active_units = [u for u in units if u.get("is_alive") and u.get("is_active", True)]
        if not alive_units:
            return commands

        # 指定执法目标：选威胁最高的非队长玩家
        designate_sources = source_lookup.get("designate", [])
        if captain_id in designate_sources:
            best_target = None
            best_score = -1
            for pid in state.player_order:
                if pid == player.player_id or pid == captain_id:
                    continue
                t = state.get_player(pid)
                if not t or not t.is_alive():
                    continue
                # 不指定队长（会被拦截）
                if getattr(t, 'is_captain', False):
                    continue
                score = self._threat_scores.get(t.name, 0)
                if getattr(t, 'is_criminal', False):
                    score += 100
                if score > best_score:
                    best_score = score
                    best_target = t
            if best_target:
                commands.append(f"designate {best_target.name}")

        # 移动警察到高威胁目标位置 / 攻击犯罪者
        if active_units:
            criminal_target = self._find_criminal_target(player, state)
            if criminal_target:
                target_loc = self._get_location_str(criminal_target)
                # 找一个不在目标位置的警察移过去
                for unit in active_units:
                    uid = unit["id"]
                    if unit.get("location") != target_loc:
                        commands.append(f"police move {uid} {target_loc}")
                        break
                # 找一个在目标位置的警察攻击
                for unit in active_units:
                    uid = unit["id"]
                    if unit.get("location") == target_loc:
                        commands.append(
                            f"police attack {uid} {criminal_target.player_id}")
                        break

        return commands

    # ---- 插入式笑话：借用攻击 ----
    def _cutaway_attack_commands(self, player, state, source_lookup, collected_actions):
        """遍历来源玩家，找最佳 (来源, 目标, 武器) 组合"""
        commands = []
        attack_sources = source_lookup.get("attack", [])
        if not attack_sources:
            return commands

        # 收集所有可能的 (来源, 目标, 武器, 评分) 组合
        options = []
        for sp_id in attack_sources:
            sp = state.get_player(sp_id)
            if not sp or not sp.is_alive():
                continue
            sp_weapons = [w for w in getattr(sp, 'weapons', [])
                          if w and not getattr(w, '_hexagram_disabled', False)]
            if not sp_weapons:
                continue

            for pid in state.player_order:
                if pid == player.player_id or pid == sp_id:
                    continue
                target = state.get_player(pid)
                if not target or not target.is_alive():
                    continue

                # 基础威胁评分
                score = self._threat_scores.get(target.name, 0) * 2
                if target.name in self._been_attacked_by:
                    score += 50
                # 低 HP 加分
                score += max(0, 5 - self._get_effective_hp(target)) * 10
                # 救世主集火
                if self._is_in_savior_state(target):
                    score += 200
                # Terror 集火
                t_talent = getattr(target, 'talent', None)
                if t_talent and getattr(t_talent, 'is_terror', False):
                    score += 500

                # 选最佳武器
                best_weapon = None
                best_w_score = -999
                for w in sp_weapons:
                    if (getattr(w, 'requires_charge', False)
                            and getattr(w, 'charge_mandatory', True)
                            and not getattr(w, 'is_charged', False)):
                        continue  # 未蓄力，跳过
                    w_range = self._get_weapon_range(w)
                    w_score = self._get_weapon_damage(w) * 10
                    # 射程适配
                    if w_range == "melee":
                        # 近战需要来源和目标同地点 + ENGAGED_WITH
                        if sp.location != target.location:
                            continue
                        markers = getattr(state, 'markers', None)
                        if markers and not markers.has_relation(
                                sp.player_id, "ENGAGED_WITH", target.player_id):
                            continue  # 没有面对面关系，近战打不了
                    elif w_range == "ranged":
                        # 远程需要 LOCKED_BY
                        markers = getattr(state, 'markers', None)
                        if markers and not markers.has_relation(
                                target.player_id, "LOCKED_BY", sp.player_id):
                            continue  # 没有锁定关系
                    elif w_range == "area":
                        if sp.location != target.location:
                            continue  # AOE 需要同地点
                    if w_score > best_w_score:
                        best_w_score = w_score
                        best_weapon = w

                if best_weapon:
                    options.append((score + best_w_score, target, best_weapon))

        if not options:
            return commands

        # 按评分排序，取最高的
        options.sort(key=lambda x: x[0], reverse=True)
        for _, target, weapon in options[:3]:  # 最多生成 3 个候选
            cmd = f"attack {target.name} {weapon.name}"
            if cmd not in commands:
                commands.append(cmd)

        return commands

    # ---- 插入式笑话：借用 interact 发育 ----
    def _cutaway_develop_commands(self, player, state, source_lookup, collected_actions):
        """检查 G6 需要什么，从来源玩家的位置获取"""
        commands = []
        interact_sources = source_lookup.get("interact", [])
        if not interact_sources:
            return commands

        my_loc = self._get_location_str(player)
        outer = self._count_outer_armor(player)
        inner = self._count_inner_armor(player)
        real_weapons = [w for w in player.weapons
                        if w and getattr(w, 'name', '') != "拳击"]
        vouchers = getattr(player, 'vouchers', 0)
        learned = self._get_learned_spells(player)

        # 构建需求列表：(物品名, 所在地点, 优先级)
        needs = []

        # 武器需求
        if len(real_weapons) < 1:
            needs.append(("小刀", "home", 60))
            needs.append(("小刀", "商店", 55))
            if "魔法弹幕" not in learned:
                needs.append(("魔法弹幕", "魔法所", 50))
            needs.append(("高斯步枪", "军事基地", 45))

        # 外甲需求
        if outer < 2:
            if not self._has_armor_by_name(player, "盾牌"):
                needs.append(("盾牌", "home", 70))
            if not self._has_armor_by_name(player, "陶瓷护甲"):
                needs.append(("陶瓷护甲", "商店", 65))
            if not self._has_armor_by_name(player, "魔法护盾") and "魔法护盾" not in learned:
                needs.append(("魔法护盾", "魔法所", 60))
            if not self._has_armor_by_name(player, "AT力场"):
                needs.append(("AT力场", "军事基地", 55))

        # 内甲需求
        if inner < 1:
            needs.append(("晶化皮肤手术", "医院", 50))
            needs.append(("额外心脏手术", "医院", 45))

        # 凭证需求
        if vouchers < 1:
            needs.append(("打工", "商店", 40))
            needs.append(("打工", "医院", 40))

        # 过滤：只保留当前位置没有的（插入式笑话的价值在于拿到自己位置拿不到的东西）
        from controllers.ai.constants import LOCATION_ITEMS
        normalized_loc = "home" if my_loc.startswith("home") else my_loc
        my_items = LOCATION_ITEMS.get(normalized_loc, [])
        needs_unfiltered = list(needs)
        needs = [(item, loc, pri) for item, loc, pri in needs if item not in my_items]

        # 按优先级排序
        needs.sort(key=lambda x: -x[2])

        # 为每个需求找到对应的来源玩家（含来源玩家的前置条件检查）
        for item_name, item_loc, _ in needs:
            for sp_id in interact_sources:
                sp = state.get_player(sp_id)
                if not sp:
                    continue
                sp_loc = self._get_location_str(sp)
                at_source = (
                    (item_loc == "home" and sp_loc.startswith("home"))
                    or sp_loc == item_loc
                )
                if not at_source:
                    continue
                # ★ 来源玩家能否负担此物品
                if not self._source_can_afford(sp, item_name, item_loc):
                    continue
                # 武器蓄力自查（蓄力是 G6 自己的 self-action，查 G6 玩家不是来源玩家）
                if item_name in ("电磁步枪", "高斯步枪"):
                    weapon_found = False
                    for w in getattr(player, 'weapons', []):
                        if w and w.name == item_name:
                            weapon_found = True
                            if (getattr(w, 'requires_charge', False)
                                    and not getattr(w, 'is_charged', False)):
                                cmd = f"special 蓄力{item_name}"
                                if cmd not in commands:
                                    commands.append(cmd)
                            break
                    if not weapon_found:
                        cmd = f"interact {item_name}"
                        if cmd not in commands:
                            commands.append(cmd)
                else:
                    cmd = f"interact {item_name}"
                    if cmd not in commands:
                        commands.append(cmd)
                break

        # fallback：过滤后无匹配 → 用不过滤的 needs 重试
        if not commands and needs_unfiltered:
            needs_unfiltered.sort(key=lambda x: -x[2])
            for item_name, item_loc, _ in needs_unfiltered:
                for sp_id in interact_sources:
                    sp = state.get_player(sp_id)
                    if not sp:
                        continue
                    sp_loc = self._get_location_str(sp)
                    at_source = (
                        (item_loc == "home" and sp_loc.startswith("home"))
                        or sp_loc == item_loc
                    )
                    if not at_source:
                        continue
                    if not self._source_can_afford(sp, item_name, item_loc):
                        continue
                    cmd = f"interact {item_name}"
                    if cmd not in commands:
                        commands.append(cmd)
                    break

        # 绝望模式：两轮过滤后仍无命令 → 不顾 own-location 和物品名，
        # 只要来源玩家当前位置有可负担的物品就收下
        if not commands:
            for sp_id in interact_sources:
                sp = state.get_player(sp_id)
                if not sp:
                    continue
                sp_loc = self._get_location_str(sp)
                item_loc = "home" if sp_loc.startswith("home") else sp_loc
                for item_name in LOCATION_ITEMS.get(item_loc, []):
                    if not self._source_can_afford(sp, item_name, item_loc):
                        continue
                    cmd = f"interact {item_name}"
                    if cmd not in commands:
                        commands.append(cmd)
                        break
                if len(commands) >= 3:
                    break

        return commands[:3]  # 最多 3 个发育候选

    @staticmethod
    def _source_can_afford(source, item_name: str, item_loc: str) -> bool:
        """检查来源玩家是否有足够凭证/通行证在指定地点交互物品。"""
        vouchers = getattr(source, 'vouchers', 0)
        has_pass = getattr(source, 'has_military_pass', False)
        if item_loc == "home" and item_name == "凭证" and vouchers >= 1:
            return False
        if item_loc in ("商店", "医院") and item_name == "打工" and vouchers >= 1:
            return False
        if item_loc == "军事基地" and item_name in ("通行证", "办理通行证"):
            return not has_pass
        if item_loc == "军事基地" and item_name != "通行证":
            if not has_pass:
                return False
        shop_paid = {"小刀", "陶瓷护甲", "磨刀石", "热成像仪", "隐身衣", "防毒面具"}
        if item_loc == "商店" and item_name in shop_paid:
            if vouchers < 1:
                return False
        hospital_paid = {"防毒面具"}
        surgery = {"晶化皮肤手术", "额外心脏手术", "不老泉手术"}
        if item_loc == "医院" and (item_name in hospital_paid or item_name in surgery):
            if vouchers < 1:
                return False
        return True

    @staticmethod
    def _infer_aoe_weapon(destination: str) -> Optional[str]:
        """根据目的地推断AI要去拿什么AOE武器（→ evaluation.infer_aoe_weapon）"""
        return infer_aoe_weapon(destination)

    @staticmethod
    def _pick_virus_cure_location(player: Any, state: Any) -> str:
        """选择获取病毒免疫的最佳地点（人最少 + 能获取）"""
        loc = str(getattr(player, 'location', ''))
        vouchers = getattr(player, 'vouchers', 0)
        virus = getattr(state, 'virus', None)
        virus_active = getattr(virus, 'is_active', False) if virus else False

        candidates = []
        # 商店：病毒期间免费，否则需凭证
        if virus_active or vouchers >= 1:
            candidates.append("商店")
        # 医院：需凭证
        if vouchers >= 1:
            candidates.append("医院")
        # 魔法所：免费学封闭（2回合）
        candidates.append("魔法所")

        if not candidates:
            return "商店"

        # 选人最少的
        best = candidates[0]
        best_count = 999
        for dest in candidates:
            count = 0
            for pid in state.player_order:
                p = state.get_player(pid)
                if p and p.is_alive():
                    p_loc = str(getattr(p, 'location', ''))
                    if p_loc == dest:
                        count += 1
            if count < best_count:
                best_count = count
                best = dest

        return best

    def _push_combat_goal(self, target: Any, player: Any, priority: int = 6) -> None:
        """推入持久化战斗目标（避免被其他优先级劫持）"""
        if not hasattr(self, '_goal_stack'):
            return
        goal = CombatGoal(
            target_id=target.player_id,
            target_name=target.name,
            priority=priority,
            debug_name=player.name,
        )
        goal.set_round(self._round_number)
        self._goal_stack.push(goal)

    def _is_hoshino_handled_by_hook(self, player: Any) -> bool:
        """检查Hoshino逻辑是否已被天赋钩子处理"""
        talent_name = getattr(getattr(player, 'talent', None), 'name', '')
        if talent_name and hasattr(self, '_talent_hook_instances'):
            hook = self._talent_hook_instances.get(talent_name)
            if hook and getattr(hook, 'talent_name', '') == "大叔我啊，剪短发了":
                return True
        return False

    def _is_talent_handled_by_hook(self, player: Any) -> bool:
        """检查当前天赋是否有活跃钩子（通用版本）"""
        talent_name = getattr(getattr(player, 'talent', None), 'name', '')
        if talent_name and hasattr(self, '_talent_hook_instances'):
            return talent_name in self._talent_hook_instances
        return False


    def _pick_fallback_destination(self, player, state) -> Optional[str]:
        """发育受阻时的兜底：在能满足需求的地点中选敌人最少的"""
        unmet_needs = self._get_unmet_needs(player, state)
        if not unmet_needs:
            return self._find_nearest_enemy_location(player, state)

        loc = self._get_location_str(player)
        # 收集所有能满足至少一个需求的地点
        useful_locs = set()
        for need_key, _ in unmet_needs:
            for (ploc, item_name, _) in NEED_PROVIDERS.get(need_key, []):
                if not self._already_has_item(player, item_name):
                    useful_locs.add(ploc)

        # 排除当前位置和已在的 home
        useful_locs.discard(loc)
        if self._is_at_home(player):
            useful_locs.discard("home")

        if not useful_locs:
            return self._find_nearest_enemy_location(player, state)

        # 按敌人数排序，选最少的
        scored = []
        for dest in useful_locs:
            enemies = self._count_enemies_at(dest, player, state)
            scored.append((dest, enemies))
        scored.sort(key=lambda x: x[1])
        return scored[0][0]

    def _has_supernova(self, player) -> bool:
        """检查火萤IV型是否有超新星可用"""
        talent = getattr(player, 'talent', None)
        if not talent:
            return False
        return getattr(talent, 'has_supernova', False)

    def _pick_supernova_target(self, player, state) -> Optional[str]:
        """选择敌人最多的地点作为超新星目标（包含当前位置）"""
        my_loc = self._get_location_str(player)
        best_loc = None
        best_count = 0

        # 包含当前位置（超新星允许同地点移动）
        all_locations = ["home", "商店", "医院", "魔法所", "军事基地", "警察局"]
        if my_loc not in all_locations:
            all_locations.append(my_loc)

        for loc in all_locations:
            count = self._count_enemies_at(loc, player, state)
            if count > best_count:
                best_count = count
                best_loc = loc

        # 必须有敌人才使用超新星
        if best_loc and best_count > 0:
            return best_loc
        return None  # 没有敌人，不浪费超新星

    # ════════════════════════════════════════════════════════
    #  新架构：事件回调（Phase 5）
    # ════════════════════════════════════════════════════════

    def _expose_ai_state_for_legacy_views(self) -> None:
        """把 AIState 暴露到旧字段，仅供遗留调试/choose 只读路径读取。"""
        s = getattr(self, '_ai_state', None)
        if not s:
            return
        self._threat_scores = dict(s.threat_scores)
        self._low_threat_streak = dict(s.low_threat_streak)
        self._been_attacked_by = set(s.been_attacked_by)
        self._players_who_attacked = set(s.players_who_attacked)
        self._in_combat = s.in_combat
        self._combat_target = s.combat_target
        self._danger_mode = s.danger_mode
        self._round_number = s.round_number
        if s.police_cache is not None:
            self._police_cache = s.police_cache
        self._virus_active = s.virus_active
        self._virus_location = s.virus_location

    def _sync_legacy_views_to_ai_state(self) -> None:
        """把遗留路径更新过的字段写回 AIState。"""
        s = getattr(self, '_ai_state', None)
        if not s:
            return
        s.threat_scores = dict(self._threat_scores)
        s.low_threat_streak = dict(self._low_threat_streak)
        s.been_attacked_by = set(self._been_attacked_by)
        s.players_who_attacked = set(self._players_who_attacked)
        s.in_combat = self._in_combat
        s.combat_target = self._combat_target
        s.danger_mode = self._danger_mode
        s.round_number = self._round_number
        s.police_cache = self._police_cache
        s.virus_active = self._virus_active
        s.virus_location = self._virus_location

    def _cleanup_dead_players_new(self, state) -> None:
        s = getattr(self, '_ai_state', None)
        if not s:
            self._cleanup_dead_players(state)
            return
        dead_names = []
        for pid in state.player_order:
            target = state.get_player(pid)
            if target and not target.is_alive():
                dead_names.append(target.name)
        for name in dead_names:
            s.threat_scores.pop(name, None)
            s.been_attacked_by.discard(name)
            s.players_who_attacked.discard(name)

    def _on_event_new(self, event: Dict) -> None:
        """新架构路径的事件处理"""
        self.event_log.append(event)
        event_type = event.get("type", "")
        target = event.get("target")
        attacker = event.get("attacker", "")
        s = getattr(self, '_ai_state', None)
        threat_scores = s.threat_scores if s else self._threat_scores
        been_attacked_by = s.been_attacked_by if s else self._been_attacked_by
        players_who_attacked = s.players_who_attacked if s else self._players_who_attacked

        if event_type == "attack" and self.player_name is not None:
            if target == self.player_name:
                been_attacked_by.add(attacker)
                threat_scores[attacker] = threat_scores.get(attacker, 0) + 20
            players_who_attacked.add(attacker)

        if event_type == "find" and self._my_id is not None:
            finder = event.get("player", "")
            if target == self._my_id:
                finder_name = self._pid_to_name(finder)
                if finder_name:
                    threat_scores[finder_name] = threat_scores.get(finder_name, 0) + 10

        if event_type == "lock" and self._my_id is not None:
            locker = event.get("player", "")
            if target == self._my_id:
                locker_name = self._pid_to_name(locker)
                if locker_name:
                    threat_scores[locker_name] = threat_scores.get(locker_name, 0) + 15

        if event_type == "release_virus":
            releaser_pid = event.get("player", "")
            releaser_name = self._pid_to_name(releaser_pid)
            if releaser_name and releaser_name != self.player_name:
                threat_scores[releaser_name] = threat_scores.get(releaser_name, 0) + 20

        if event_type == "death":
            killer = event.get("killer", "")
            if killer:
                threat_scores[killer] = threat_scores.get(killer, 0) + 30
            dead_name = event.get("dead", "") or target
            if dead_name:
                threat_scores.pop(dead_name, None)
                been_attacked_by.discard(dead_name)
                players_who_attacked.discard(dead_name)

        if event_type == "election":
            candidate_pid = event.get("player", "")
            candidate_name = self._pid_to_name(candidate_pid)
            if candidate_name and candidate_name != self.player_name:
                threat_scores[candidate_name] = threat_scores.get(candidate_name, 0) + 10

        if event_type == "captain_elected":
            captain_pid = event.get("captain", "")
            captain_name = self._pid_to_name(captain_pid)
            if captain_name and captain_name != self.player_name:
                threat_scores[captain_name] = threat_scores.get(captain_name, 0) + 30

    def _on_round_start_new(self, player, state, round_number: int):
        """新架构路径的轮次开始"""
        self._round_number = round_number
        self._action_used = False
        self._missile_cooldown = max(0, self._missile_cooldown - 1)
        self._update_caches_new(player, state)
        self._cleanup_dead_players_new(state)
        s = getattr(self, '_ai_state', None)
        if s:
            s.round_number = round_number
            s.action_used = False
            s.missile_cooldown = self._missile_cooldown
        debug_ai_basic(player.name,
            f"轮次{round_number}开始，人格={self.personality}，"
            f"阶段={self._current_phase}")

    def _on_round_end_new(self, player, state, round_number: int):
        """新架构路径的轮次结束"""
        s = getattr(self, '_ai_state', None)
        if s:
            s.been_attacked_by.clear()
        else:
            self._been_attacked_by.clear()

    def _on_damaged_new(self, player, attacker_name: str, damage: float):
        """新架构路径的被攻击处理"""
        s = getattr(self, '_ai_state', None)
        threat_scores = s.threat_scores if s else self._threat_scores
        been_attacked_by = s.been_attacked_by if s else self._been_attacked_by
        been_attacked_by.add(attacker_name)
        threat_scores[attacker_name] = threat_scores.get(attacker_name, 0) + damage * 10
        debug_ai_basic(player.name,
            f"被 {attacker_name} 攻击，伤害={damage}")

    def _on_player_killed_new(self, player, killed_name: str, killer_name: str):
        """新架构路径的玩家死亡处理"""
        s = getattr(self, '_ai_state', None)
        threat_scores = s.threat_scores if s else self._threat_scores
        if killed_name in threat_scores:
            del threat_scores[killed_name]
        if s:
            s.been_attacked_by.discard(killed_name)
            s.players_who_attacked.discard(killed_name)
        else:
            self._been_attacked_by.discard(killed_name)
            self._players_who_attacked.discard(killed_name)
        if killer_name and killer_name != player.name:
            threat_scores[killer_name] = threat_scores.get(killer_name, 0) + 30
        debug_ai_basic(player.name,
            f"玩家 {killed_name} 被 {killer_name} 杀死")

    def _update_caches_new(self, player, state):
        """新架构路径的缓存更新"""
        self._police_cache = GameQuery.read_police_state(state)
        virus = getattr(state, 'virus', None)
        if virus:
            self._virus_active = getattr(virus, 'is_active', False)
            self._virus_location = GameQuery.get_location_str(virus) if hasattr(virus, 'location') else None
        else:
            self._virus_active = False
            self._virus_location = None
        s = getattr(self, '_ai_state', None)
        if s:
            s.police_cache = self._police_cache
            s.virus_active = self._virus_active
            s.virus_location = self._virus_location

    def _respond_to_event_new(self, player, state, event_type: str,
                               event_data: dict) -> Optional[str]:
        """新架构路径的事件响应"""
        debug_ai_basic(player.name,
            f"响应事件: type={event_type}, data={event_data}")

        talent_name = getattr(getattr(player, 'talent', None), 'name', '')
        if talent_name and event_type == "天赋触发":
            hook = self._talent_hook_instances.get(talent_name)
            if hook and hasattr(hook, 'handle_event_response'):
                result = hook.handle_event_response(player, state, event_data)
                if result is not None:
                    return result

        if event_type == "被攻击":
            return self._respond_attacked_new(player, state, event_data)
        elif event_type == "举报":
            return self._respond_report_new(player, state, event_data)
        elif event_type == "天赋触发":
            return self._respond_talent_new(player, state, event_data)
        elif event_type == "投票":
            return self._respond_vote_new(player, state, event_data)
        elif event_type == "警察行动":
            return self._respond_police_action_new(player, state, event_data)
        elif event_type == "病毒":
            return self._respond_virus_new(player, state, event_data)
        else:
            debug_ai_basic(player.name, f"未知事件类型: {event_type}")
            return None

    def _respond_attacked_new(self, player, state, data) -> Optional[str]:
        attacker = data.get("attacker")
        if not attacker:
            return None
        s = getattr(self, '_ai_state', None)
        if s:
            s.been_attacked_by.add(attacker)
        else:
            self._been_attacked_by.add(attacker)
        options = data.get("options", [])
        strategy = getattr(self, '_strategy', None)
        if strategy:
            pref = strategy.get_combat_response_preference(options)
            if pref in options:
                return pref
        if "block" in options:
            return "block"
        return options[0] if options else None

    def _respond_report_new(self, player, state, data) -> Optional[str]:
        reporter = data.get("reporter")
        target = data.get("target")
        options = data.get("options", [])
        if target == player.name:
            if "deny" in options:
                return "deny"
            return options[0] if options else None
        if "support" in options and "oppose" in options:
            threat = self._threat_scores.get(target, 0)
            strategy = getattr(self, '_strategy', None)
            if strategy and strategy.should_support_report(target, threat):
                return "support"
            elif self.personality == "political":
                return "support"
            elif threat > 30:
                return "support"
            else:
                return "oppose"
        return options[0] if options else None

    def _respond_talent_new(self, player, state, data) -> Optional[str]:
        options = data.get("options", [])
        if "accept" in options:
            return "accept"
        if "activate" in options:
            return "activate"
        return options[0] if options else None

    def _respond_vote_new(self, player, state, data) -> Optional[str]:
        options = data.get("options", [])
        candidate = data.get("candidate")
        if candidate:
            threat = self._threat_scores.get(candidate, 0)
            if threat < 20 and "support" in options:
                return "support"
            elif "oppose" in options:
                return "oppose"
        return options[0] if options else None

    def _respond_police_action_new(self, player, state, data) -> Optional[str]:
        options = data.get("options", [])
        action = data.get("action", "")
        if action == "arrest" and player.name == data.get("target"):
            if "resist" in options and self.personality == "aggressive":
                return "resist"
            if "surrender" in options:
                return "surrender"
        return options[0] if options else None

    def _respond_virus_new(self, player, state, data) -> Optional[str]:
        options = data.get("options", [])
        if "use_mask" in options:
            return "use_mask"
        if "flee" in options:
            return "flee"
        return options[0] if options else None

    def _get_debug_info_new(self, player) -> dict:
        """新架构路径的调试输出"""
        dev_complete = False
        if player and self._game_state:
            dev_complete = self._is_development_complete(player, self._game_state)
        return {
            "personality": self.personality,
            "phase": self._current_phase,
            "round": self._round_number,
            "threat_scores": dict(self._threat_scores),
            "in_combat": self._in_combat,
            "combat_target": self._combat_target,
            "been_attacked_by": list(self._been_attacked_by),
            "virus_active": getattr(self, '_virus_active', False),
            "last_commands": self._last_commands[:],
            "police_cache": self._police_cache,
            "development_complete": dev_complete,
        }

# ════════════════════════════════════════════════════════════════
#  工厂函数 (原 lines 4460-4502)
# ════════════════════════════════════════════════════════════════

def create_ai_controller(personality: str = "balanced",
                         player_name: str = "",
                         **kwargs) -> BasicAIController:
    """
    创建AI控制器的工厂函数

    参数:
        personality: AI人格类型
            - "aggressive": 激进型，优先攻击
            - "defensive": 防御型，优先发育和防守
            - "assassin": 刺客型，隐身突袭
            - "balanced": 均衡型，攻守兼备
            - "builder": 建设型，追求全面发育
            - "political": 政治型，利用警察系统
        player_name: 玩家名称（用于调试）
    """
    valid_personalities = [
        "aggressive", "defensive", "assassin",
        "balanced", "builder", "political"
    ]
    if personality not in valid_personalities:
        debug_ai_basic(player_name,
            f"未知人格类型 '{personality}'，使用 'balanced'")
        personality = "balanced"

    controller_kwargs = {
        key: kwargs[key]
        for key in ("diag_enabled",)
        if key in kwargs
    }
    controller = BasicAIController(personality=personality, **controller_kwargs) # type: ignore
    debug_ai_basic(player_name,
        f"创建AI控制器: personality={personality}")
    return controller


def create_random_ai_controller(player_name: str = "", **kwargs) -> BasicAIController:
    import random as _rand
    personalities = [
        "aggressive", "defensive", "assassin",
        "balanced", "builder", "political"
    ]
    personality = _rand.choice(personalities)
    return create_ai_controller(personality=personality, player_name=player_name, **kwargs)
