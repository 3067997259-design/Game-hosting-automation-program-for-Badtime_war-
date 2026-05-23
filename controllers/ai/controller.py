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
        new_arch_enabled: bool = True,
        diag_enabled: bool = False,
    ):
        super().__init__()
        self.personality = personality
        self._new_arch_enabled = new_arch_enabled
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
        self._shadow_mode = False
        self._shadow_log: List[Dict] = []

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

    def _uses_new_arch_events(self) -> bool:
        return bool(getattr(self, '_new_arch_enabled', False) and hasattr(self, '_ai_state'))

    def on_event(self, event: Dict) -> None:
        if self._uses_new_arch_events():
            self._on_event_new(event)
        else:
            EventsMixin.on_event(self, event)

    def on_round_start(self, player, state, round_number: int):
        if self._uses_new_arch_events():
            self._on_round_start_new(player, state, round_number)
        else:
            EventsMixin.on_round_start(self, player, state, round_number)

    def on_round_end(self, player, state, round_number: int):
        if self._uses_new_arch_events():
            self._on_round_end_new(player, state, round_number)
        else:
            EventsMixin.on_round_end(self, player, state, round_number)

    def on_damaged(self, player, attacker_name: str, damage: float):
        if self._uses_new_arch_events():
            self._on_damaged_new(player, attacker_name, damage)
        else:
            EventsMixin.on_damaged(self, player, attacker_name, damage)

    def on_player_killed(self, player, killed_name: str, killer_name: str):
        if self._uses_new_arch_events():
            self._on_player_killed_new(player, killed_name, killer_name)
        else:
            EventsMixin.on_player_killed(self, player, killed_name, killer_name)

    def respond_to_event(self, player, state, event_type: str,
                         event_data: dict) -> Optional[str]:
        if self._uses_new_arch_events():
            return self._respond_to_event_new(player, state, event_type, event_data)
        return EventsMixin.respond_to_event(self, player, state, event_type, event_data)

    def get_debug_info(self, player) -> dict:
        if self._uses_new_arch_events():
            return self._get_debug_info_new(player)
        return EventsMixin.get_debug_info(self, player)


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
            # ════════════════════════════════════════════════════════
            #  ★ 管道选择：新架构 Orchestrator vs 旧架构 waterfall
            #  new_arch_enabled=True 时走新管道，False 时走旧管道
            # ════════════════════════════════════════════════════════
            if hasattr(self, '_orchestrator') and self._new_arch_enabled:
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
            else:
                self._candidates = self._generate_candidates(
                    player, game_state, available_actions
                )
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
                # 已有该武器但未蓄力 → 改为蓄力指令
                if item_name in ("电磁步枪", "高斯步枪"):
                    for w in getattr(sp, 'weapons', []):
                        if w and w.name == item_name:
                            if (getattr(w, 'requires_charge', False)
                                    and not getattr(w, 'is_charged', False)):
                                cmd = f"special 蓄力{item_name}"
                                if cmd not in commands:
                                    commands.append(cmd)
                                break
                    else:
                        cmd = f"interact {item_name}"
                        if cmd not in commands:
                            commands.append(cmd)
                        break
                    break
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
        if item_loc == "军事基地" and item_name != "通行证":
            if not has_pass:
                return False
        paid_items = {"陶瓷护甲", "磨刀石", "热成像仪", "隐身衣", "防毒面具"}
        if item_loc == "商店" and item_name in paid_items:
            if vouchers < 1:
                return False
        surgery = {"晶化皮肤手术", "额外心脏手术", "不老泉手术"}
        if item_loc == "医院" and (item_name in paid_items or item_name in surgery):
            if vouchers < 1:
                return False
        return True

    # ════════════════════════════════════════════════════════
    #  核心：候选命令生成 (原 lines 858-1154)
    # ════════════════════════════════════════════════════════

    def _generate_candidates(self, player, state, available_actions: List[str]) -> List[str]:
        self._my_id = player.player_id
        self.player_name = player.name
        self._player = player
        self._game_state = state

        current_round = getattr(state, 'current_round', 0)
        if current_round > self._round_number:
            self._round_number = current_round

        self._update_threat_scores(player, state)
        self._read_police_state(state)
        self._update_combat_status(player, state)
        self._cleanup_dead_players(state)

        # ════════════════════════════════════════════════════════
        #  PoliceMind：统一警察态势评估（调试可见性，不改变行为）
        #  每轮评估一次，输出结构化日志到 debug_ai_basic
        # ════════════════════════════════════════════════════════
        if hasattr(self, '_police_mind'):
            self._police_mind.assess(
                player, state,
                police_cache=self._police_cache or {},
                threat_scores=self._threat_scores,
                my_location=self._get_location_str(player),
                strategy=getattr(self, '_strategy', None),
            )

        # ════════════════════════════════════════════════════════
        #  目标系统：清理过期目标，获取活跃目标的下一步命令
        # ════════════════════════════════════════════════════════
        if hasattr(self, '_goal_stack'):
            removed = self._goal_stack.pop_expired(player, state)
            for g in removed:
                debug_ai_basic(player.name, f"目标完成/过期: {g.description}")
            # 记录当前活跃目标（调试可见）
            if not self._goal_stack.is_empty:
                top = self._goal_stack.top()
                if top:
                    debug_ai_basic(player.name,
                        f"活跃目标: {top.description} (优先级={top.priority})")

        if self.personality == "political":
            self._political_fallback_level = self._political_should_fallback(player, state)
            self._political_in_balanced_fallback = (self._political_fallback_level == "full_balanced")
            self._political_develop_only = (self._political_fallback_level == "develop_only")
        else:
            self._political_fallback_level = "none"
            self._political_in_balanced_fallback = False
            self._political_develop_only = False

        candidates = []

        # 未起床
        if not player.is_awake:
            return ["wake"]

        # ════════════════════════════════════════════════════════
        #  天赋AI钩子分发：天赋可完全接管候选命令生成
        #  替代散落在各处的 _has_hoshino_talent / _has_firefly 等硬编码
        # ════════════════════════════════════════════════════════
        talent_name = getattr(getattr(player, 'talent', None), 'name', '')
        self._talent_hook_handled = False
        if talent_name and hasattr(self, '_talent_hook_instances'):
            hook = self._talent_hook_instances.get(talent_name)
            if hook:
                override = hook.should_override_candidates(player, state, available_actions)
                if override is not None:
                    self._talent_hook_handled = True
                    return override

        # 旧架构下没有天赋钩子实例；Terror 必须仍然优先走全图攻击。
        if (self._has_hoshino_talent(player)
                and not self._talent_hook_handled
                and self._hoshino_is_terror(player)):
            debug_ai_basic(player.name, "星野Terror：旧架构fallback全图攻击")
            return self._hoshino_terror_command(player, state, available_actions)

        # ===== 病毒应急（最高优先级，优先于terror以外所有战术决策）=====
        if self._needs_virus_cure(player, state):
            debug_ai_basic(player.name, "进入病毒应急模式（优先于战术宏）")
            # 推入持久化目标：不会因为其他优先级劫持而忘记治病毒
            if hasattr(self, '_goal_stack') and not self._has_virus_immunity(player):
                goal = VirusCureGoal(
                    preferred_location=self._pick_virus_cure_location(player, state),
                    debug_name=player.name,
                )
                goal.set_round(self._round_number)
                self._goal_stack.push(goal)
            virus_cmds = self._cmd_virus(player, state, available_actions)
            if virus_cmds:
                candidates.extend(virus_cmds)
                candidates.append("forfeit")
                return candidates

        # ===== 星野肾上腺素（宏外使用，不消耗行动回合）=====
        # @deprecated 已迁移到 HoshinoAIHook.should_override_candidates()
        # 仅当钩子未处理时作为fallback
        if (self._has_hoshino_talent(player)
                and not self._talent_hook_handled
                and self._hoshino_tactical_unlocked(player)
                and not self._hoshino_is_terror(player)
                and "special" in available_actions):
            adr_target = self._hoshino_find_target(player, state)
            if adr_target and self._hoshino_should_use_adrenaline(player, adr_target):
                debug_ai_basic(player.name, "星野：注射肾上腺素（宏外免费行动）")
                candidates.insert(0, "special 肾上腺素")

        # ===== 星野反警察：搏命模式（被追击时）=====
        # @deprecated 已迁移到 HoshinoAIHook
        if (self._has_hoshino_talent(player)
                and not self._talent_hook_handled
                and self._hoshino_tactical_unlocked(player)
                and not self._hoshino_is_terror(player)
                and self._is_pursued_by_police_extended(player, state)):
            can_shoot = self._hoshino_has_ammo(player) or bool(self._hoshino_find_consumable_for_reload(player))
            if can_shoot:
                # 搏命：放弃修盾，直接冲队长或警察
                target = self._hoshino_find_target(player, state)
                if target and "special" in available_actions:
                    horus_ok = self._hoshino_iron_horus_hp(player) > 0
                    if horus_ok:
                        self._hoshino_macro_queue = self._hoshino_build_anti_captain_approach_macro(
                            player, state, target)
                        self._hoshino_anti_captain_approached = True
                        self._hoshino_anti_captain_target_id = target.player_id
                        debug_ai_basic(player.name, f"星野搏命反警察：冲 {target.name}")
                        return ["special Hoshino", "forfeit"]
                    else:
                        # 无盾版：必须先确认同地点才能进入宏
                        target_loc = self._get_location_str(target)
                        my_loc = self._get_location_str(player)
                        if target_loc == my_loc:
                            self._hoshino_macro_queue = self._hoshino_build_anti_captain_unshielded_macro(
                                player, state, target)
                            debug_ai_basic(player.name, f"星野搏命反警察（无盾）：冲 {target.name}")
                            return ["special Hoshino", "forfeit"]
                        elif "move" in available_actions:
                            debug_ai_basic(player.name, f"星野搏命反警察（无盾）：移动到 {target_loc}")
                            return [f"move {target_loc}", "forfeit"]
            # 没有弹药 → 直接 move 到队长位置
            pc = self._police_cache or {}
            captain_id = pc.get("captain_id")
            if captain_id:
                captain = state.get_player(captain_id)
                if captain and captain.is_alive():
                    captain_loc = self._get_location_str(captain)
                    if captain_loc != self._get_location_str(player) and "move" in available_actions:
                        return [f"move {captain_loc}", "forfeit"]

        # ===== 星野战术指令已解锁：优先使用 special Hoshino =====
        # @deprecated 已迁移到 HoshinoAIHook
        if (self._has_hoshino_talent(player)
                and not self._talent_hook_handled
                and self._hoshino_tactical_unlocked(player)
                and not self._hoshino_is_terror(player)):
            # 前置检查：有弹药或可装填物品，且铁之荷鲁斯未破损
            shield_mode = self._hoshino_shield_mode(player)
            can_shoot = self._hoshino_has_ammo(player) or bool(self._hoshino_find_consumable_for_reload(player))
            horus_ok = self._hoshino_iron_horus_hp(player) > 0

            # 新增：持盾/架盾死锁检测 — 需要 interact 但被盾牌模式阻止
            # 此时允许进入战术宏仅用于取消盾牌
            if shield_mode and (not can_shoot or not horus_ok) and "special" in available_actions:
                reasons = []
                if not can_shoot:
                    reasons.append("无弹药")
                if not horus_ok:
                    reasons.append("铁之荷鲁斯破损")
                reason = "+".join(reasons)
                debug_ai_basic(player.name, f"星野：{shield_mode}中{reason}，进入宏取消盾牌")
                self._hoshino_macro_queue = ["取消", "terminal"]
                return ["special Hoshino", "forfeit"]

            if can_shoot and horus_ok:
                # 反队长射击轮：上一轮已接近，本轮全力射击
                # 守卫：如果宏队列已有内容（接近宏尚未执行完），跳过全力射击
                if (getattr(self, '_hoshino_anti_captain_approached', False)
                        and not self._hoshino_macro_queue):
                    self._hoshino_anti_captain_approached = False
                    captain_id = getattr(self, '_hoshino_anti_captain_target_id', None)
                    self._hoshino_anti_captain_target_id = None  # 清除，防止残留
                    if captain_id:
                        captain = state.get_player(captain_id)
                        if captain and captain.is_alive():
                            self._hoshino_macro_queue = self._hoshino_build_fullfire_macro(
                                player, state, captain)
                            debug_ai_basic(player.name, f"星野反队长射击轮：全力射击 {captain.name}")
                            return ["special Hoshino", "forfeit"]
                    # 队长已死或不存在 → 清除标记，走正常逻辑
                target = self._hoshino_find_target(player, state)
                if target and "special" in available_actions:
                    # 新增：反队长战术宏
                    pc = self._police_cache or {}
                    captain_id = pc.get("captain_id")
                    is_anti_captain = (
                        getattr(target, 'is_captain', False)
                        and self._hoshino_captain_has_police_protection(state)
                        and self._hoshino_has_enough_tactical_items(player)
                    )

                    if is_anti_captain:
                        talent = getattr(player, 'talent', None)
                        # 检查肾上腺素
                        if (talent and "肾上腺素" in getattr(talent, 'medicines', [])
                                and not getattr(talent, 'adrenaline_used', False)
                                and talent.cost <= 5):
                            # 肾上腺素在宏外执行（不消耗回合），同一回合紧接着进入接近宏
                            # 注意：不在此处设置 _hoshino_anti_captain_approached，
                            # 因为肾上腺素是免费行动，引擎会再次调用 _generate_candidates，
                            # 如果此时标记已设置，会被误判为射击轮而覆盖接近宏。
                            # 标记在接近宏执行完毕后由 _hoshino_get_tactical_command 设置。
                            self._hoshino_macro_queue = self._hoshino_build_anti_captain_approach_macro(
                                player, state, target)
                            self._hoshino_anti_captain_target_id = target.player_id
                            debug_ai_basic(player.name, "星野：注射肾上腺素 + 反队长接近宏")
                            return ["special 肾上腺素", "special Hoshino", "forfeit"]

                        # 无肾上腺素：直接进入反队长接近宏
                        self._hoshino_macro_queue = self._hoshino_build_anti_captain_approach_macro(
                            player, state, target)
                        self._hoshino_anti_captain_approached = True
                        self._hoshino_anti_captain_target_id = target.player_id
                        debug_ai_basic(player.name, f"星野反队长接近宏：目标 {target.name}")
                        return ["special Hoshino", "forfeit"]
                    # 检查是否有同地点残血目标可以补刀
                    finish_target = self._hoshino_find_finishable_target(player, state)
                    if finish_target and finish_target.player_id != target.player_id:
                        # 有残血目标且不是主目标 → 使用补刀+转火模板
                        self._hoshino_macro_queue = self._hoshino_build_finish_and_switch_macro(
                            player, state, finish_target, target)
                        debug_ai_basic(player.name,
                            f"星野补刀+转火宏：补刀 {finish_target.name} → 转火 {target.name}")
                        return ["special Hoshino", "forfeit"]
                    else:
                        # 正常单目标宏
                        self._hoshino_macro_queue = []
                        debug_ai_basic(player.name, f"星野战术宏：目标 {target.name}")
                        return ["special Hoshino", "forfeit"]
                elif target is None:
                    # 没有攻击目标：先尝试顺手拿，再移动到敌人位置
                    grab = self._hoshino_grab_while_here(player, state, available_actions)
                    if grab:
                        debug_ai_basic(player.name, "星野：无目标，顺手拿物品")
                        grab.append("forfeit")
                        return grab
                    enemy_loc = self._find_nearest_enemy_location(player, state)
                    if enemy_loc and "move" in available_actions:
                        loc = self._get_location_str(player)
                        if enemy_loc == "home" and self._is_at_home(player):
                            pass  # 已在家，不移动
                        elif enemy_loc != loc:
                            debug_ai_basic(player.name, f"星野：无目标，移动到 {enemy_loc}")
                            return [f"move {enemy_loc}", "forfeit"]
                    # 都不行 → fall through
            else:
                # 条件不满足时清除反队长标记，防止过期标记触发错误的全力射击
                self._hoshino_anti_captain_approached = False
                self._hoshino_anti_captain_target_id = None
                # 新增：铁之荷鲁斯破损但被警察追击 → 放弃修盾，直接冲队长
                if (not horus_ok and can_shoot
                        and self._is_pursued_by_police_extended(player, state)):
                    pc = self._police_cache or {}
                    captain_id = pc.get("captain_id")
                    if captain_id:
                        captain = state.get_player(captain_id)
                        if captain and captain.is_alive():
                            captain_loc = self._get_location_str(captain)
                            loc = self._get_location_str(player)
                            if captain_loc == loc:
                                # 同地点：直接进入无盾反队长宏
                                self._hoshino_macro_queue = self._hoshino_build_anti_captain_unshielded_macro(
                                    player, state, captain)
                                debug_ai_basic(player.name, f"星野反队长宏（无盾）：目标 {captain.name}")
                                return ["special Hoshino", "forfeit"]
                            elif "move" in available_actions:
                                # 不同地点：先 move 过去
                                debug_ai_basic(player.name, f"星野：无盾反队长，移动到 {captain_loc}")
                                return [f"move {captain_loc}", "forfeit"]
                if not can_shoot:
                    debug_ai_basic(player.name, "星野：无弹药且无可装填物品，跳过战术宏")
                if not horus_ok:
                    debug_ai_basic(player.name, "星野：铁之荷鲁斯已破损，跳过战术宏")
                # fall through 到发育路径（去拿刀/修盾/拿装备）

        # ===== 队长指挥 =====
        if getattr(player, 'is_captain', False) and "police_command" in available_actions:
            debug_ai_basic(player.name, "作为队长，生成警察指挥命令")
            # 推入持久化队长目标
            if hasattr(self, '_goal_stack'):
                cap_goal = CaptainGoal(
                    cmd_captain_fn=self._cmd_captain,
                    debug_name=player.name,
                )
                cap_goal.set_round(self._round_number)
                self._goal_stack.push(cap_goal)
            captain_cmds = self._cmd_captain(player, state, available_actions)
            if captain_cmds:
                candidates.append(captain_cmds[0])

        # （病毒应急已在上方最高优先级处理，此处不再重复）

        # ===== G2 EMR蓄力准备：即将发动全息影像但EMR未蓄力 =====
        if (getattr(self, '_emr_needs_charge_before_hologram', False)
                and player.talent and hasattr(player.talent, 'name')
                and player.talent.name == "请一直，注视着我"
                and not getattr(player.talent, 'active', False)):  # 影像还没激活
            emr = next((w for w in player.weapons if w and w.name == "电磁步枪"), None)
            if emr and not getattr(emr, 'is_charged', False) and "special" in available_actions:
                debug_ai_basic(player.name, "G2准备发动：先蓄力电磁步枪")
                candidates.insert(0, "special 蓄力电磁步枪")
                candidates.append("forfeit")
                self._emr_needs_charge_before_hologram = False  # 清除标记
                return candidates
            else:
                self._emr_needs_charge_before_hologram = False  # EMR已蓄力或无法蓄力，清除标记

        # ===== 全息影像激活中：留在影像区域用AOE扫场 =====  # ★ 改动：从 line 326 提前到此处
        if (player.talent and hasattr(player.talent, 'name')
                and player.talent.name == "请一直，注视着我"
                and getattr(player.talent, 'active', False)):
            hologram = player.talent
            my_loc = self._get_location_str(player)
            raw_hologram_loc = getattr(hologram, 'location', None)
            hologram_loc = str(raw_hologram_loc) if raw_hologram_loc is not None else None

            if my_loc == hologram_loc:
                # 在影像区域内：优先用AOE攻击被拉入的目标
                debug_ai_basic(player.name, "全息影像激活中：AOE扫场模式")
                same_loc = self._get_same_location_targets(player, state)
                if same_loc:
                    # 检查是否应该先蓄力EMR再攻击
                    emr = next((w for w in player.weapons if w and w.name == "电磁步枪"), None)
                    if emr and not getattr(emr, 'is_charged', False) and "special" in available_actions:
                        # 陶瓷护甲只免疫电流眩晕，不免疫伤害，EMR的0.5 AOE伤害始终有效
                        debug_ai_basic(player.name, "全息影像中：蓄力电磁步枪")
                        candidates.insert(0, "special 蓄力电磁步枪")
                        candidates.append("forfeit")
                        return candidates
                    attack_cmds = self._cmd_attack(player, state, available_actions)
                    if attack_cmds:
                        candidates.extend(attack_cmds)
                        candidates.append("forfeit")
                        return candidates
                # 同地点没有目标（都跑了）→ 拿当前地点的装备
                if "interact" in available_actions:
                    dev_cmds = self._cmd_develop_hologram(player, state, available_actions)
                    if dev_cmds:
                        candidates.extend(dev_cmds)
                candidates.append("forfeit")
                return candidates
            else:
                # 不在影像区域：移动回去
                if "move" in available_actions and hologram_loc:
                    candidates.insert(0, f"move {hologram_loc}")
                    candidates.append("forfeit")
                    return candidates
                # hologram_loc 为 None（异常情况）：兜底 forfeit，避免 fallthrough
                candidates.append("forfeit")
                return candidates

        # ===== 救世主状态 =====
        if self._is_in_savior_state(player) and self._get_effective_hp(player) > 0.5:
            debug_ai_basic(player.name, "救世主状态激活，优先攻击")
            last_attacker = self._get_last_attacker(player, state)
            if last_attacker:
                attack_cmds = self._cmd_attack(player, state, available_actions, last_attacker)
                if attack_cmds:
                    candidates.extend(attack_cmds)
                    candidates.append("forfeit")
                    return candidates
            attack_cmds = self._cmd_attack(player, state, available_actions)
            if attack_cmds:
                candidates.extend(attack_cmds)
                candidates.append("forfeit")
                return candidates

        # ===== 病毒预防 =====
        if not self._virus_prevention_done and not self._has_virus_immunity(player):
            if self._someone_has_virus_immunity(state):
                self._virus_prevention_done = True
                debug_ai_basic(player.name, "检测到有人持有病毒免疫，主动预防")
                prevention_cmds = self._cmd_virus(player, state, available_actions)
                if prevention_cmds:
                    candidates.extend(prevention_cmds)
                    candidates.append("forfeit")
                    return candidates

        # ===== Assassin 主动放毒 =====
        if self._should_release_virus(player, state) and "special" in available_actions:
            debug_ai_basic(player.name, "Assassin 在医院放毒！")
            candidates.append("special 释放病毒")

        # ===== 危险情况 =====
        if self._is_critical(player, state):
            self._danger_mode = True
            # 进入危险模式时，打断所有持久化目标（保命优先）
            if hasattr(self, '_goal_stack'):
                self._goal_stack.interrupt_all()
                # 推入逃跑目标：队长优先去警察所在地，普通玩家去安全地点
                if getattr(player, 'is_captain', False):
                    safe_loc = self._pick_captain_safe_destination(player, state)
                else:
                    safe_loc = self._pick_safe_armor_destination(player, state)
                if safe_loc:
                    flee = FleeGoal(
                        destination=safe_loc,
                        debug_name=player.name,
                    )
                    flee.set_round(self._round_number)
                    self._goal_stack.push(flee)

        if self._danger_mode:
            if self._is_danger_resolved(player):
                debug_ai_basic(player.name, "危险解除，退出危险模式")
                self._danger_mode = False
                # 危险解除，恢复被打断的目标，清理FleeGoal
                if hasattr(self, '_goal_stack'):
                    # 标记所有FleeGoal为完成
                    count_flee = 0
                    for g in self._goal_stack.all_goals:
                        if hasattr(g, '_danger_resolved'):
                            g._danger_resolved = True
                            count_flee += 1
                    debug_ai_basic(player.name, f"危险解除: 标记{count_flee}个FleeGoal完成")
                    self._goal_stack.resume_all()
                    # 立即清理已完成的FleeGoal
                    removed = self._goal_stack.pop_expired(player, state)
                    for g in removed:
                        debug_ai_basic(player.name, f"危险解除: 清理 {g.description}")
                    if count_flee > 0 and not removed:
                        debug_ai_basic(player.name, "警告: FleeGoal标记了但pop_expired未移除!")
            else:
                debug_ai_basic(player.name, "处于危险模式")
                if self._is_pursued_by_police(player, state):
                    if self._can_fight_police(player, state):
                        fight_cmds = self._cmd_fight_police(player, state, available_actions)
                        if fight_cmds:
                            candidates.extend(fight_cmds)
                            danger_fallback = self._cmd_danger_develop(player, state, available_actions)
                            for cmd in danger_fallback:
                                if cmd not in candidates:
                                    candidates.append(cmd)
                            candidates.append("forfeit")
                            return candidates
                    # ════════════════════════════════════════════════════
                    #  PoliceMind 补充：打不过警察时，主动获取AOE武器
                    #  替代仅逃跑的默认行为
                    # ════════════════════════════════════════════════════
                    elif hasattr(self, '_police_mind'):
                        pe = getattr(state, 'police_engine', None)
                        if pe:
                            # 收集受保护目标的护甲属性
                            target_armor_attrs: set = set()
                            for pid in state.player_order:
                                if pid == player.player_id:
                                    continue
                                t = state.get_player(pid)
                                if t and t.is_alive() and pe.is_protected_by_police(t.player_id):
                                    attrs = self._get_outer_armor_attr(t)
                                    if not attrs:
                                        attrs = self._get_inner_armor_attr(t)
                                    target_armor_attrs.update(attrs)
                            aoe_cmds = self._police_mind.get_aoe_acquisition_commands(
                                player, state, available_actions,
                                target_armor_attrs=target_armor_attrs,
                                my_location=self._get_location_str(player),
                                has_pass=getattr(player, 'has_military_pass', False),
                                learned_spells=self._get_learned_spells(player),
                            )
                            if aoe_cmds:
                                debug_ai_basic(player.name,
                                    "PoliceMind: 被警察追击，主动获取AOE武器反制")
                                # ════════════════════════════════════════════
                                #  推入持久化目标：AI会记住要去拿AOE武器
                                # ════════════════════════════════════════════
                                if hasattr(self, '_goal_stack') and aoe_cmds:
                                    first_cmd = aoe_cmds[0]
                                    if first_cmd.startswith("move "):
                                        dest = first_cmd[5:]
                                        weapon = self._infer_aoe_weapon(dest)
                                        if weapon:
                                            goal = DevelopGoal(
                                                target_item=weapon,
                                                target_location=dest,
                                                priority=8,  # 高于一般发育
                                                debug_name=player.name,
                                            )
                                            goal.set_round(self._round_number)
                                            self._goal_stack.push(goal)
                                candidates.extend(aoe_cmds)
                                candidates.append("forfeit")
                                return candidates

                danger_cmds = self._cmd_danger_develop(player, state, available_actions)
                candidates.extend(danger_cmds)
                if candidates:
                    candidates.append("forfeit")
                    return candidates

        # ===== Political 非队长 =====
        if (not getattr(player, 'is_captain', False)
            and self.personality == "political"):
            if not self._political_in_balanced_fallback:
                # 推入持久化政治目标
                if hasattr(self, '_goal_stack'):
                    pol_goal = PoliticalGoal(
                        cmd_political_fn=self._cmd_police_political,
                        debug_name=player.name,
                    )
                    pol_goal.set_round(self._round_number)
                    self._goal_stack.push(pol_goal)
                political = self._cmd_police_political(player, state, available_actions)
                candidates.extend(political)
                develop = self._cmd_develop(player, state, available_actions)
                for cmd in develop:
                    if cmd not in candidates:
                        candidates.append(cmd)
                candidates.append("forfeit")
                seen = set()
                deduped = []
                for cmd in candidates:
                    if cmd not in seen:
                        seen.add(cmd)
                        deduped.append(cmd)
                return deduped
            debug_ai_basic(player.name, "political fallback 激活：采用 balanced 行动策略")

        if self._political_develop_only and self._in_combat:
            self._in_combat = False
            self._combat_target = None

        # ===== 救世主紧急集火 =====
        if not self._in_combat:
            for pid in state.player_order:
                if pid == player.player_id:
                    continue
                t = state.get_player(pid)
                if t and t.is_alive() and self._is_in_savior_state(t):
                    # 有远程武器 → 立刻进入战斗
                    # 自己也是救世主就算了
                    has_ranged = not self._is_in_savior_state(player) and any(
                        self._get_weapon_range(w) == "ranged"
                        for w in getattr(player, 'weapons', []) if w
                    )
                    if has_ranged:
                        debug_ai_basic(player.name, f"紧急：发现救世主 {t.name}，用远程武器集火")
                        self._in_combat = True
                        self._combat_target = t
                        self._push_combat_goal(t, player, priority=8)  # 救世主集火高优先级
                        break
        # ===== 超新星紧急分散 =====
        if (not self._has_firefly_talent(player)
                and self._firefly_supernova_threat(player, state)):
            my_loc = self._get_location_str(player)
            same_loc_count = len(self._get_same_location_targets(player, state))
            if same_loc_count >= 2 and "move" in available_actions:
                # 同地点有2+个其他玩家，紧急分散
                # 选择没有其他玩家的地点
                empty_locs = []
                for loc in ["home", "商店", "医院", "魔法所", "军事基地", "警察局"]:
                    if loc == my_loc:
                        continue
                    enemies = self._count_enemies_at(loc, player, state)
                    if enemies == 0:
                        empty_locs.append(loc)
                if empty_locs:
                    import random
                    dest = random.choice(empty_locs)
                    candidates.insert(0, f"move {dest}")
                    # 不直接return，让后续逻辑也生成备选命令
        # ===== 星野 Terror / 自我怀疑集火 =====
        if not self._has_hoshino_talent(player):
            for pid in state.player_order:
                if pid == player.player_id:
                    continue
                t = state.get_player(pid)
                if not t or not t.is_alive():
                    continue
                t_talent = getattr(t, 'talent', None)
                if not t_talent:
                    continue
                # Terror 存在 → 最高优先级集火
                if getattr(t_talent, 'is_terror', False):
                    debug_ai_basic(player.name, f"Terror 存在！集火 {t.name}")
                    self._push_combat_goal(t, player, priority=9)
                    attack_cmds = self._cmd_attack(player, state, available_actions, t)
                    if attack_cmds:
                        candidates.extend(attack_cmds)
                        candidates.append("forfeit")
                        return candidates
                # 自我怀疑 → 紧急集火（下回合变 Terror）
                if getattr(t_talent, 'self_doubt_pending', False):
                    debug_ai_basic(player.name, f"星野自我怀疑！紧急集火 {t.name}")
                    self._push_combat_goal(t, player, priority=9)
                    attack_cmds = self._cmd_attack(player, state, available_actions, t)
                    if attack_cmds:
                        candidates.extend(attack_cmds)
                        candidates.append("forfeit")
                        return candidates

        # ===== 星野发育路径（战术宏入口已在上方处理）=====
        if self._has_hoshino_talent(player):
            if not self._is_development_complete(player, state):
                dev = self._cmd_develop_hoshino(player, state, available_actions)
                if dev:
                    candidates.extend(dev)
                    candidates.append("forfeit")
                    return candidates

        # ===== 战斗状态 =====                           # ★ 改动：删除 hologram pass-through
        if self._in_combat and self._combat_target:
            if self._should_continue_combat(player, self._combat_target):
                debug_ai_combat_state(player.name, f"战斗目标: {self._combat_target.name}")
                combat_cmds = self._cmd_attack(player, state, available_actions, self._combat_target)
                if combat_cmds:
                    candidates.extend(combat_cmds)
                    candidates.append("forfeit")
                    return candidates
            else:
                debug_ai_basic(player.name, "退出战斗状态")
                self._in_combat = False
                target_ref = self._combat_target
                self._combat_target = None
                if target_ref and self._all_weapons_countered(player, target_ref):
                    debug_ai_basic(player.name, "所有武器被克制，寻找新武器")
                    rearm_cmds = self._cmd_rearm(player, state, available_actions)
                    if rearm_cmds:
                        candidates.extend(rearm_cmds)
                        candidates.append("forfeit")
                        return candidates

        # ===== 火萤专用逻辑 =====
        # @deprecated 已迁移到 FireflyAIHook.should_override_candidates()
        if (self._has_firefly_talent(player)
                and not self._talent_hook_handled):
            # 超新星优先：有超新星就用
            if self._has_supernova(player) and "move" in available_actions:
                best_loc = self._pick_supernova_target(player, state)
                if best_loc:
                    debug_ai_basic(player.name, f"火萤：超新星过载，目标地点={best_loc}")
                    candidates.insert(0, f"move {best_loc}")
                    candidates.append("forfeit")
                    return candidates

            # Phase 1（debuff 前）：拿到刀就冲
            if not self._firefly_debuff_active(player):
                has_knife = any(w.name == "小刀" for w in player.weapons if w)
                if has_knife:
                    # 有刀就攻击，不等发育完成
                    debug_ai_basic(player.name, "火萤Phase1：有刀就冲")
                    attack_cmds = self._cmd_attack(player, state, available_actions)
                    if attack_cmds:
                        candidates.extend(attack_cmds)
                        # 备用发育（只拿护甲，不拿更多武器）
                        dev = self._cmd_develop_firefly_minimal(player, state, available_actions)
                        candidates.extend(dev)
                        candidates.append("forfeit")
                        return candidates

            # Phase 2/3（debuff 后）：攻击优先于发育
            if self._firefly_debuff_active(player):
                debug_ai_basic(player.name, "火萤Phase2/3：debuff已生效，攻击优先")
                attack_cmds = self._cmd_attack(player, state, available_actions)
                if attack_cmds:
                    candidates.extend(attack_cmds)
                # 发育作为备选
                dev = self._cmd_develop(player, state, available_actions)
                for cmd in dev:
                    if cmd not in candidates:
                        candidates.append(cmd)
                candidates.append("forfeit")
                return candidates

        # ===== 火萤击杀机会 =====
        if (self._has_firefly_talent(player)
            and not self._political_develop_only):
            firefly_kill_target = self._find_firefly_kill_target(player, state)
            if firefly_kill_target:
                debug_ai_basic(player.name, "火萤发现击杀机会，打断发育！")
                kill_cmds = self._cmd_attack(player, state, available_actions,
                                            forced_target=firefly_kill_target)
                if kill_cmds:
                    candidates.extend(kill_cmds)
                    dev = self._cmd_develop(player, state, available_actions)
                    if dev:
                        candidates.append(dev[0])
                    candidates.append("forfeit")
                    return candidates

        # ===== 击杀机会 =====
        kill_target = self._find_kill_target(player, state)
        if kill_target and not self._political_develop_only:
            debug_ai_basic(player.name, "发现击杀机会！")
            self._push_combat_goal(kill_target, player, priority=7)
            kill_cmds = self._cmd_attack(player, state, available_actions,
                                        forced_target=kill_target)
            if kill_cmds:
                candidates.extend(kill_cmds)
                # 备用发育
                dev = self._cmd_develop(player, state, available_actions)
                if dev:
                    candidates.append(dev[0])
                candidates.append("forfeit")
                return candidates

        # ===== 发育 =====
        debug_ai_development_plan(player.name, "进入发育模式")
        develop = self._cmd_develop(player, state, available_actions)
        candidates.extend(develop)

        # 发育受阻：develop 为空但发育未完成
        if not develop and not self._is_development_complete(player, state):
            if self.personality in ("aggressive", "assassin", "balanced") or self._political_in_balanced_fallback:
                debug_ai_basic(player.name, "发育受阻，转为进攻冲散人群")
                attack_cmds = self._cmd_attack(player, state, available_actions)
                for cmd in attack_cmds:
                    if cmd not in candidates:
                        candidates.append(cmd)
            if not candidates:
                fallback_loc = self._pick_fallback_destination(player, state)
                if fallback_loc:
                    candidates.append(f"move {fallback_loc}")

        # ===== 发育完成后主动进攻 =====
        if self._is_development_complete(player, state) and not self._political_develop_only:
            debug_ai_basic(player.name, "发育完成，尝试进攻")
            attack_cmds = self._cmd_attack(player, state, available_actions)
            for cmd in attack_cmds:
                if cmd not in candidates:
                    candidates.insert(0, cmd)

        # ===== 所有目标受警察保护且无AOE =====
        if self._is_stuck_by_police(player, state):
            debug_ai_basic(player.name, "所有目标受警察保护且无法穿透，去获取有效武器")
            aoe_cmds = self._cmd_fight_police(player, state, available_actions)
            # ════════════════════════════════════════════════════════
            #  PoliceMind 补充：现有逻辑可能只返回空列表，
            #  PoliceMind 提供明确的AOE获取路径（去军事基地/魔法所）
            # ════════════════════════════════════════════════════════
            if not aoe_cmds and hasattr(self, '_police_mind'):
                pe = getattr(state, 'police_engine', None)
                if pe:
                    target_armor_attrs: set = set()
                    for pid in state.player_order:
                        if pid == player.player_id:
                            continue
                        t = state.get_player(pid)
                        if t and t.is_alive() and pe.is_protected_by_police(t.player_id):
                            attrs = self._get_outer_armor_attr(t)
                            if not attrs:
                                attrs = self._get_inner_armor_attr(t)
                            target_armor_attrs.update(attrs)
                    supplement = self._police_mind.get_aoe_acquisition_commands(
                        player, state, available_actions,
                        target_armor_attrs=target_armor_attrs,
                        my_location=self._get_location_str(player),
                        has_pass=getattr(player, 'has_military_pass', False),
                        learned_spells=self._get_learned_spells(player),
                    )
                    if supplement:
                        debug_ai_basic(player.name,
                            "PoliceMind: 补充AOE获取路径")
                        aoe_cmds = supplement
                        # 推入持久化目标：记住要去拿AOE
                        if hasattr(self, '_goal_stack') and supplement:
                            first_cmd = supplement[0]
                            if first_cmd.startswith("move "):
                                dest = first_cmd[5:]
                                weapon = self._infer_aoe_weapon(dest)
                                if weapon:
                                    goal = DevelopGoal(
                                        target_item=weapon,
                                        target_location=dest,
                                        priority=7,
                                        debug_name=player.name,
                                    )
                                    goal.set_round(self._round_number)
                                    self._goal_stack.push(goal)
            for cmd in aoe_cmds:
                if cmd not in candidates:
                    candidates.insert(0, cmd)

        # ===== 政治型补充 =====
        if self.personality == "political":
            political = self._cmd_police_political(player, state, available_actions)
            candidates.extend(political)

        # ===== 常规攻击补充 =====
        is_political_no_attack = self._political_develop_only or (
            self.personality == "political" and self._political_fallback_level == "none"
        )
        if not is_political_no_attack and (
            "attack" in available_actions or "find" in available_actions
            or "lock" in available_actions
        ):
            attack = self._cmd_attack(player, state, available_actions)
            for cmd in attack:
                if cmd not in candidates:
                    candidates.append(cmd)

        # ════════════════════════════════════════════════════════
        #  目标系统：遍历所有活跃目标，每个都贡献命令（容纳制）
        #  匹配旧架构的瀑布流：所有优先级层都有发言权
        # ════════════════════════════════════════════════════════
        if (hasattr(self, '_goal_stack')
                and not self._goal_stack.is_empty
                and not self._danger_mode):
            has_combat = any(
                c.startswith(("attack", "special", "lock", "find"))
                for c in candidates
            )
            for goal in self._goal_stack.all_goals:
                goal_cmd = goal.get_next_command(player, state, available_actions)
                if goal_cmd and goal_cmd not in candidates:
                    if has_combat:
                        candidates.append(goal_cmd)  # 有战斗命令时追加
                    else:
                        candidates.insert(0, goal_cmd)  # 无战斗命令时优先

        candidates.append("forfeit")

        # 去重
        seen = set()
        deduped = []
        for cmd in candidates:
            if cmd not in seen:
                seen.add(cmd)
                deduped.append(cmd)

        # ════════════════════════════════════════════════════════
        #  Shadow模式：记录决策上下文用于新旧对比
        #  每次get_command调用都会记录一行，包含该决策点的关键状态
        # ════════════════════════════════════════════════════════
        if self._shadow_mode:
            goals = []
            if hasattr(self, '_goal_stack') and self._goal_stack:
                goals = [g.description for g in self._goal_stack.all_goals]
            self._shadow_log.append({
                "round": self._round_number,
                "personality": self.personality,
                "talent": getattr(getattr(player, 'talent', None), 'name', ''),
                "candidates": deduped[:5],
                "in_combat": self._in_combat,
                "danger_mode": self._danger_mode,
                "active_goals": goals,
                "police_cache": (
                    {k: v for k, v in (self._police_cache or {}).items()
                     if k in ("has_police", "captain_id", "alive_count", "active_count")}
                    if self._police_cache else {}
                ),
                "location": self._get_location_str(player),
            })
        return deduped

    def dump_shadow_log(self) -> List[Dict]:
        """导出shadow日志（用于新旧对比分析），并清空内部缓存"""
        log = self._shadow_log
        self._shadow_log = []
        return log


    @staticmethod
    def _infer_aoe_weapon(destination: str) -> Optional[str]:
        """根据目的地推断AI要去拿什么AOE武器"""
        if destination == "军事基地":
            return "电磁步枪"
        if destination == "魔法所":
            return "地动山摇"  # 优先升级版
        return None

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
        for key in ("new_arch_enabled", "diag_enabled")
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
