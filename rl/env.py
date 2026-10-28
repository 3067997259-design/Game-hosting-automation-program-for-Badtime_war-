"""
rl/env.py
─────────
BadtimeWarEnv —— 主 Gym 封装（天赋局版）

单智能体环境，RL 控制一名玩家，其余由 BasicAIController 控制。
游戏引擎在后台线程运行，通过 threading.Event 与 env 同步。

支持两种同步模式：
  1. get_command 模式：RL 选择主行动（索引 0-107）
  2. choose 模式：RL 回答子决策（索引 108-129）

兼容 sb3-contrib MaskablePPO：
  - action_masks() 方法返回 bool 数组
  - info dict 中也包含 "action_masks"
"""

from __future__ import annotations

import multiprocessing
import random
import threading
from typing import Any, Optional, List, Dict

import gymnasium as gym
import numpy as np
import torch
from gymnasium import spaces

from rl.action_space import (
    IDX_FORFEIT, IDX_CHOOSE_BASE,
    IDX_TALENT_T0_TARGET_BASE, IDX_TALENT_T0_SELF,
    action_spec_from_idx, apply_action_contract, build_action_mask,
    idx_to_command, idx_to_choose_option,
    rl_action_count,
)
from rl.obs_builder import OBS_DIM, build_obs  # noqa: F401  (legacy 常量/构建器仍被引用)
from rl.obs_builder_m9 import (
    build_obs_profile,
    fill_choose_obs_m9,
    obs_dim_for_profile,
)
from rl.reward import RewardTracker
from rl.rl_controller import RLController
from controllers.ai_basic import BasicAIController
from engine.game_state import GameState
from engine.round_manager import RoundManager
from models.player import Player
from cli import display as _display_module
from controllers.ai_basic import create_ai_controller
from engine.prompt_manager import prompt_manager as _prompt_manager

# 天赋系统导入
from engine.game_setup import (
    AI_DISABLED_TALENTS, AI_TALENT_PREFERENCE,
    AI_PERSONALITIES, TALENT_DECAY_FACTOR, assign_talent_entry,
    find_talent_entry, talent_table_for_current_profile,
)

_DISPLAY_FUNCS = [
    "show_banner", "show_round_header", "show_phase", "show_d4_results",
    "show_initiative_results",
    "show_action_turn_header", "show_player_status", "show_available_actions",
    "show_result", "show_error", "show_info", "show_victory", "show_death",
    "show_police_status", "show_virus_status", "show_police_enforcement",
    "show_virus_deaths", "show_all_players_status", "show_help",
    "show_critical", "show_warning", "show_prompt", "clear_screen",
]

_original_display = {}


def _silence_display():
    """将 cli.display 的所有输出函数替换为 no-op"""
    for name in _DISPLAY_FUNCS:
        if hasattr(_display_module, name):
            _original_display[name] = getattr(_display_module, name)
            setattr(_display_module, name, lambda *a, **kw: None)
    if hasattr(_display_module, "prompt_input"):
        _original_display["prompt_input"] = _display_module.prompt_input
        _display_module.prompt_input = lambda *a, **kw: ""
    if hasattr(_display_module, "prompt_choice"):
        _original_display["prompt_choice"] = _display_module.prompt_choice
        _display_module.prompt_choice = lambda prompt, options, **kw: options[0] if options else ""
    if hasattr(_display_module, "prompt_secret"):
        _original_display["prompt_secret"] = _display_module.prompt_secret
        _display_module.prompt_secret = lambda *a, **kw: ""
    # monkey-patch builtins.input 防止任何未守卫的 input() 阻塞子进程
    import builtins
    if not hasattr(builtins, '_original_input'):
        builtins._original_input = builtins.input
    builtins.input = lambda *a, **kw: ""


def _restore_display():
    """恢复 cli.display 的原始函数"""
    for name, func in _original_display.items():
        setattr(_display_module, name, func)
    _original_display.clear()
    # 恢复 builtins.input
    import builtins
    if hasattr(builtins, '_original_input'):
        builtins.input = builtins._original_input

_original_pm_output = None

def _silence_prompt_manager():
    """将 prompt_manager._output 替换为 no-op"""
    global _original_pm_output
    _original_pm_output = getattr(_prompt_manager, '_output', None)
    setattr(_prompt_manager, '_output', lambda text, level: None)

def _restore_prompt_manager():
    """恢复 prompt_manager._output"""
    global _original_pm_output
    if _original_pm_output is not None:
        setattr(_prompt_manager, '_output', _original_pm_output)
        _original_pm_output = None



# ─────────────────────────────────────────────────────────────────────────────
#  内部异常：用于中止后台游戏线程
# ─────────────────────────────────────────────────────────────────────────────

class _GameAborted(Exception):
    pass


# ─────────────────────────────────────────────────────────────────────────────
#  同步版 RLController：在 get_command / choose / confirm 中阻塞等待 env
# ─────────────────────────────────────────────────────────────────────────────

def _combat_core_default_choice(options) -> str:
    """基础作战课对旁路 choose 回调取确定性中性选项。"""
    choices = list(options or [])
    if not choices:
        return ""
    for decline in ("不参与", "不押注", "保留", "跳过", "否", "放弃", "取消"):
        if decline in choices:
            return decline
    for choice in choices:
        normalized = str(choice).strip()
        if normalized.startswith(("不", "拒绝")) or any(
            token in normalized for token in ("不参与", "不押注", "跳过")
        ):
            return choice
    return choices[0]


class _SyncRLController(RLController):
    """
    扩展 RLController，在 get_command() 和战略 choose()/confirm() 中
    与 env 线程同步。

    流程（get_command）：
      1. 引擎调用 get_command()
      2. 控制器信号 env（_obs_event）并阻塞（_action_event）
      3. env.step() 设置 pending_action_idx 并信号控制器
      4. 控制器将 idx 翻译为 CLI 命令返回给引擎

    流程（战略 choose）：
      1. 引擎调用 choose()，基类路由到 _rl_choose()
      2. _rl_choose() 设置 env._choose_mode=True，信号 _obs_event
      3. env.step() 返回 choose 模式的 obs/mask
      4. 下一次 env.step(action) 将 action 翻译为选项索引
      5. _rl_choose() 被唤醒，返回对应选项
    """

    def __init__(self, env: "BadtimeWarEnv"):
        super().__init__()
        self._env = env
        self._turn_call_count = 0

    def get_command(self, player, game_state, available_actions, context=None):
        if self._env._game_over_flag:
            raise _GameAborted()

        # 判断是否为重试调用（命令校验失败后引擎会重新调用）
        attempt = (context or {}).get("attempt", 1)
        if attempt == 1:
            self._turn_call_count = 0
        self._turn_call_count += 1

        if self._turn_call_count > 1:
            # 重试：mask 未能完全过滤，安全回退
            return "forfeit"

        # 缓存引用供 _rl_choose / _rl_confirm 使用
        self._cache_player_ref(player, game_state)

        # 保存引用供 env 读取
        self._env._current_player = player
        self._env._current_game_state = game_state
        self._env._choose_mode = False  # 确保是 get_command 模式

        # 通知 env：准备好接收动作
        self._env._obs_event.set()

        # 等待 env 提供动作
        self._env._action_event.wait()
        self._env._action_event.clear()

        if self._env._game_over_flag:
            raise _GameAborted()

        return idx_to_command(self.pending_action_idx, player, game_state)

    def _rl_choose(self, prompt, options, context=None):
        """线程同步版 _rl_choose：让 RL 智能体通过 env.step() 做 choose 决策。"""
        if self._env._game_over_flag:
            return options[0] if options else ""

        # combat_core 只教移动/接敌/通用攻击。警察投票、PP 押注、
        # 公演等全局 choose 回调由环境以中性默认值结算，不向 RL 额外
        # 暴露决策步。否则虽然动作 mask 屏蔽了警察/special，LSTM 仍会
        # 在基础课中被迫学习这些旁路菜单。
        effective_contract = (
            getattr(self._env, "action_contract", None)
            or getattr(self._env, "_rl_base_action_contract", None)
        )
        if effective_contract in {
            "combat_core", "economy_course", "economy_integration_course",
            "combat_economy", "pp_course", "survival_course",
        }:
            return _combat_core_default_choice(options)

        # RL 内部使用稳定的功能顺序，不改人类/BasicAI 看到的菜单。M9 报名窗口
        # 的显示顺序是“保留/报名”，而 talent_t0 是“发动/不发动”；若照抄显示
        # 序号，连续两个接受型决策会要求 LSTM 先选第 2 槽、再选第 1 槽，实测
        # 学成“报名后先拒绝发动一轮”。这里规范为接受在前、拒绝在后。
        rl_options = list(options)
        if (
            (context or {}).get("phase") == "M9_PUBLIC_REGISTRATION"
            and set(rl_options) == {"保留", "报名公演"}
        ):
            rl_options = ["报名公演", "保留"]

        # 预游戏阶段（on_register 中的 choose 调用）：游戏线程未启动，
        # 事件同步不可用，直接返回随机选择
        if self._env._pre_game_phase:
            idx = self._env.np_random.integers(len(rl_options))
            return rl_options[idx]

        # 设置 choose 模式
        self._env._choose_mode = True
        self._env._choose_options = rl_options
        self._env._choose_context = context or {}
        self._env._current_player = self._player_ref
        self._env._current_game_state = self._state_ref

        # 通知 env：准备好接收 choose 决策
        self._env._obs_event.set()

        # 等待 env 提供动作（RL 智能体在 env.step() 中选择）
        self._env._action_event.wait()
        self._env._action_event.clear()

        if self._env._game_over_flag:
            return options[0] if options else ""

        # 读取 RL 选择的索引（step() 已计算 action - IDX_CHOOSE_BASE）
        chosen_idx = self._env._pending_choose_idx

        chosen = (
            rl_options[chosen_idx]
            if 0 <= chosen_idx < len(rl_options)
            else rl_options[0]
        )

        # 立即清除 choose 模式，避免其他线程看到过时状态。
        # 线程安全说明：此时 env 线程阻塞在 _obs_event.wait()，
        # action_masks() 只从 env 线程调用，因此不会并发访问。
        self._env._choose_mode = False
        self._env._choose_options = []
        self._env._choose_context = {}

        return chosen

    def _rl_confirm(self, prompt, context=None):
        """线程同步版 _rl_confirm：映射为 2 选项的 _rl_choose。"""
        result = self._rl_choose(prompt, ["是", "否"], context)
        return result == "是"


# ─────────────────────────────────────────────────────────────────────────────
#  天赋分配辅助
# ─────────────────────────────────────────────────────────────────────────────

def _ai_pick_talent_for_env(personality: str, available, taken: set, rng):
    """
    AI 根据人格偏好从可用天赋中加权随机选择（env 专用版，使用 env 的 rng）。
    返回 (编号, 名称, 类) 或 None。
    """
    preference = AI_TALENT_PREFERENCE.get(personality,
                                           AI_TALENT_PREFERENCE["balanced"])
    candidates = []
    weights = []
    for i, talent_num in enumerate(preference):
        for n, name, cls, desc in available:
            if n == talent_num:
                candidates.append((n, name, cls))
                weights.append(TALENT_DECAY_FACTOR ** i)
                break

    if not candidates:
        # 偏好列表里的天赋全被选走 → 随机兜底
        chosen = available[rng.integers(len(available))]
        return (chosen[0], chosen[1], chosen[2])

    # 加权随机
    total_w = sum(weights)
    probs = [w / total_w for w in weights]
    idx = rng.choice(len(candidates), p=probs)
    return candidates[idx]


# ─────────────────────────────────────────────────────────────────────────────
#  主环境
# ─────────────────────────────────────────────────────────────────────────────

class BadtimeWarEnv(gym.Env):
    """
    Badtime War 单智能体 Gym 环境（天赋局版）。

    参数
    ----
    num_opponents  : 对手数量（1-5），默认 3
    max_rounds     : 最大轮数，超过则 truncated=True，默认动态计算
    render_mode    : "human" 时保留游戏输出，否则静默
    n_stack        : 帧堆叠数量
    opponent_pool  : Self-play 对手池
    enable_talents          : 是否启用天赋系统
    enable_opponent_talents : 是否给对手分配天赋（专项课可只保留 RL 天赋）
    rl_talent               : RL 玩家的天赋编号（None=随机, 0=无天赋）
    """

    metadata = {"render_modes": ["human"]}

    def __init__(
        self,
        num_opponents: int = 3,
        max_rounds: Optional[int] = None,
        render_mode: Optional[str] = None,
        n_stack: int = 1,
        opponent_pool=None,
        opponent_bots: Optional[list] = None,
        opponent_personalities: Optional[list[str]] = None,
        enable_talents: bool = True,
        enable_opponent_talents: bool = True,
        rl_talent: Optional[int] = None,
        rl_talent_reserve: bool = False,
        force_random_talent: bool = False,
        max_per_model: int = 1,
        trace_decisions: bool = False,
        max_trace_entries: int = 2000,
        action_contract: Optional[str] = None,
        course_scenario: Optional[str] = None,
        enable_bow_context: bool = False,
    ):
        super().__init__()

        # SubprocVecEnv 使用 start_method="spawn"，子进程不继承主进程的
        # torch.set_num_threads 设置，默认会使用所有 CPU 核心。多个子进程同时
        # 在 self-play 中做单样本推理时会导致严重的线程争抢，因此在每个 env
        # 构造时显式限制为 1 线程。
        # 注意：torch.set_num_threads 是进程级设置；仅在真正的子进程中生效，
        # 否则会覆盖 train.py 在主进程设置的 torch.set_num_threads(min(8, ...))，
        # 影响 DummyVecEnv(n_envs=1) / watch.py / watch_all.py 等主进程场景。
        if multiprocessing.parent_process() is not None:
            torch.set_num_threads(1)

        self.num_opponents = num_opponents
        self.max_rounds = max_rounds
        self.render_mode = render_mode
        self.n_stack = n_stack
        self.opponent_pool = opponent_pool
        self.opponent_bots = opponent_bots  # 指定的简单 bot 名（BOT_REGISTRY），作弱对手课程梯级
        self.opponent_personalities = tuple(opponent_personalities or ())
        unknown_personalities = (
            set(self.opponent_personalities) - set(AI_PERSONALITIES)
        )
        if unknown_personalities:
            raise ValueError(
                f"未知 BasicAI 人格: {sorted(unknown_personalities)}"
            )
        self.enable_talents = enable_talents
        self.enable_opponent_talents = enable_opponent_talents
        self.rl_talent = rl_talent  # None=随机, 0=无天赋, 1-14=指定天赋
        # 预留池语义（P3 裁决 2026-08-22）：指定编号从 AI 对手可选池排除，
        # RL 必然拿到该天赋；与 rl_talent∈{None,0} 组合无意义，直接拒绝。
        self.rl_talent_reserve: bool = rl_talent_reserve
        if self.rl_talent_reserve and not (
            isinstance(rl_talent, int) and rl_talent > 0
        ):
            raise ValueError(
                "rl_talent_reserve=True 需要 rl_talent 为正整数编号"
                f"（当前 rl_talent={rl_talent!r}）"
            )
        self.force_random_talent: bool = force_random_talent
        self.max_per_model = max_per_model
        self.trace_decisions = bool(trace_decisions)
        self.max_trace_entries = max(1, int(max_trace_entries))
        self.action_contract = action_contract
        self.course_scenario = course_scenario
        self.enable_bow_context = bool(enable_bow_context)

        # ── Gym 空间 ──
        # 观测维度 profile 感知：M9 含摘要块与逐选项 choose 语义，legacy → 539
        self._obs_dim = obs_dim_for_profile()
        self.observation_space = spaces.Box(
            low=-1.0, high=1.0, shape=(self._obs_dim * n_stack,), dtype=np.float32
        )
        # 动作规模 profile 感知：M9 为 2～6 人固定 schema（预留 6 个 home 槽），
        # legacy/v2exp 仍为 137。固定维度保证课程切换和 checkpoint 可兼容。
        self.action_space = spaces.Discrete(rl_action_count())

        # ── 帧堆叠缓冲 ──
        self._obs_stack = np.zeros(self._obs_dim * n_stack, dtype=np.float32)

        # ── 局结果记录（用于坍塌检测的 eval 胜率计算） ──
        self._episode_outcomes: list[str | None] = []
        self._episode_summaries: list[dict[str, Any]] = []

        # ── 内部状态（reset 时初始化）──
        self._state: Optional[GameState] = None
        self._round_manager: Optional[RoundManager] = None
        self._rl_controller: Optional[_SyncRLController] = None
        self._rl_player: Optional[Player] = None
        self._reward_tracker: Optional[RewardTracker] = None

        # ── 线程同步 ──
        self._obs_event = threading.Event()
        self._action_event = threading.Event()
        self._game_over_flag = False
        self._game_thread: Optional[threading.Thread] = None
        self._thread_error: BaseException | None = None
        self._max_rounds_reached = False
        self._termination_reason: str | None = None

        # ── 临时引用（由 _SyncRLController 写入）──
        self._current_player = None
        self._current_game_state = None

        # ── 预游戏阶段标志（on_register 中的 choose 调用跳过事件同步）──
        self._pre_game_phase: bool = False

        # ── choose 同步状态 ──
        self._choose_mode: bool = False
        self._choose_options: List[str] = []
        self._choose_context: Dict = {}
        self._pending_choose_idx: int = 0
        self._decision_counter: int = 0
        self._decision_trace: list[dict[str, Any]] = []

        self._taken_talents: set = set()

    def _stack_obs(self, raw_obs: np.ndarray) -> np.ndarray:
        """将新观测推入帧堆叠缓冲，返回拼接后的完整观测。"""
        if self.n_stack <= 1:
            return raw_obs
        dim = self._obs_dim
        self._obs_stack[:-dim] = self._obs_stack[dim:]
        self._obs_stack[-dim:] = raw_obs
        return self._obs_stack.copy()

    def set_num_opponents(self, n: int):
        """供 CurriculumCallback 调用，修改对手数量（下次 reset 生效）。

        注意：当 self.max_rounds 为 None 时，reset() 会根据新的
        player_order 长度动态计算 max_rounds，无需在此处更新。
        """
        self.num_opponents = n

    def set_opponent_pool(self, pool):
        """供 SelfPlayCallback 调用，设置对手池（下次 reset 生效）。"""
        self.opponent_pool = pool

    def update_basic_ai_prob(self, new_prob: float):
        """供 SelfPlayCallback 调用，更新对手池的 BasicAI 概率。"""
        if self.opponent_pool is not None:
            self.opponent_pool.basic_ai_prob = new_prob

    def set_force_random_talent(self, value: bool):
        """供 ForceRandomTalentCallback 调用。"""
        self.force_random_talent = value

    # ══════════════════════════════════════════════════════════════════════════
    #  reset
    # ══════════════════════════════════════════════════════════════════════════

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)

        # 清理上一局
        self._cleanup()
        if self.render_mode != "human":
            _silence_display()
            _silence_prompt_manager()

        # 创建新游戏
        self._state = GameState()

        # 创建 RL 玩家
        self._rl_controller = _SyncRLController(self)
        self._rl_player = Player("rl_0", "RL_Agent", self._rl_controller)

        # 创建对手（self-play 或 BasicAI）
        ai_players = []
        ai_personalities = {}  # pid → personality
        self._opponent_model_stems: Dict[str, str] = {}  # pid → model_stem（用于 ELO）
        model_usage_count: Dict[str, int] = {}
        for i in range(self.num_opponents):
            fixed_personality = (
                self.opponent_personalities[i]
                if i < len(self.opponent_personalities) else None
            )
            if self.opponent_pool is not None:
                # 构建排除集合：已达到 max_per_model 上限的模型
                over_limit_stems = {
                    stem for stem, count in model_usage_count.items()
                    if count >= self.max_per_model and stem != "basic_ai"
                }
                ctrl, model_stem = self.opponent_pool.sample_opponent_controller(
                    exclude_stems=over_limit_stems if over_limit_stems else None
                )
                model_usage_count[model_stem] = model_usage_count.get(model_stem, 0) + 1
                if hasattr(ctrl, 'reset_stack'):
                    ctrl.reset_stack()
                personality = "balanced"  # self-play 对手无人格概念
            elif self.opponent_bots and i < len(self.opponent_bots):
                bot_name = self.opponent_bots[i]
                if bot_name == "basic_ai":
                    # 显式逐座位列表也允许完整 BasicAI；训练 CLI 的多人模式
                    # 会原样传入该哨兵，不能落进简单 bot 注册表。
                    personality = fixed_personality or random.choice(
                        AI_PERSONALITIES
                    )
                    ctrl = create_ai_controller(
                        personality=personality, player_name=f"AI_{i}",
                    )
                    model_stem = "basic_ai"
                else:
                    # 弱对手课程：用 BOT_REGISTRY 里的简单 bot。
                    from controllers.bots import BOT_REGISTRY
                    ctrl = BOT_REGISTRY[bot_name]()
                    model_stem = bot_name
                    personality = "balanced"  # 简单 bot 无人格
            else:
                personality = fixed_personality or random.choice(
                    AI_PERSONALITIES
                )
                ctrl = create_ai_controller(personality=personality, player_name=f"AI_{i}")
                model_stem = "basic_ai"
            p = Player(f"ai_{i}", f"AI_{i}", ctrl)
            ai_players.append(p)
            ai_personalities[p.player_id] = personality
            self._opponent_model_stems[p.player_id] = model_stem

        # 随机化玩家顺序
        all_players = [self._rl_player] + ai_players
        self.np_random.shuffle(all_players)
        for p in all_players:
            self._state.add_player(p)

        self._rl_talent_assigned = False

        # ── 天赋分配 ──
        if self.enable_talents:
            self._assign_talents(ai_personalities)

        # 设置最大轮数
        if self.max_rounds is not None:
            self._state.max_rounds = self.max_rounds
        else:
            self._state.max_rounds = GameState.compute_default_max_rounds(
                len(self._state.player_order))

        # 创建轮次管理器
        self._round_manager = RoundManager(self._state)

        # 课程场景只改变显式训练 env；正式对局（None）完全不受影响。
        self._apply_course_scenario()

        # 创建奖励追踪器（profile 感知：m9-rfc → M9RewardTracker）
        from rl.reward_m9 import make_reward_tracker
        _rw_cls = make_reward_tracker(self._state)
        self._reward_tracker = _rw_cls("rl_0")

        # ── 长线统计 ──
        self._lt_initial_hp = self._rl_player.hp
        self._lt_total_actions = 0
        self._lt_attack_actions = 0
        self._lt_damage_dealt = 0.0
        self._lt_last_kill_round = 0
        self._lt_last_kill_count = 0

        # 重置同步原语
        self._obs_event.clear()
        self._action_event.clear()
        self._game_over_flag = False
        self._thread_error = None
        self._max_rounds_reached = False
        self._termination_reason = None
        self._choose_mode = False
        self._choose_options = []
        self._choose_context = {}
        self._decision_counter = 0
        self._decision_trace = []

        # 后台线程启动游戏
        self._game_thread = threading.Thread(target=self._run_game, daemon=True)
        self._game_thread.start()

        # 等待第一个 RL 决策点（get_command 或 choose）
        if not self._obs_event.wait(timeout=30.0):
            raise RuntimeError(
                "Game thread did not reach first RL decision point within 30s. "
                "Possible cause: unguarded input() call in game engine code."
            )
        self._obs_event.clear()
        self._raise_thread_error()

        # 初始化奖励追踪器基线
        assert self._rl_player is not None
        assert self._state is not None
        assert self._reward_tracker is not None
        self._reward_tracker.reset(self._rl_player, self._state)

        self._obs_stack = np.zeros(self._obs_dim * self.n_stack, dtype=np.float32)
        raw_obs = build_obs_profile(self._rl_player, self._state, "rl_0",
                                    long_term_stats=self._build_long_term_stats(),
                                    **self._course_obs_kwargs())
        self._fill_choose_obs(raw_obs)
        obs = self._stack_obs(raw_obs)
        info = {"action_masks": self.action_masks()}

        return obs, info

    def _apply_course_scenario(self) -> None:
        """在游戏线程启动前布置可复现的专项教学局。"""
        scenario = self.course_scenario
        if scenario is None:
            return
        if scenario not in {
            "combat_multi_quarter_hp", "combat_multi_half_hp",
            "combat_multi_full_hp",
            "combat_assassin_pressure",
            "combat_retarget", "combat_retarget_static",
            "economy_bow_supply", "economy_pursuit_supply",
            "economy_armored_pursuit_supply",
            "economy_armored_pursuit_supply_slot0",
            "equipment_fire_antiarmor",
            "equipment_bow_loadout",
            "pp_damage_finish", "pp_damage_choice",
            "survival_heal_counterattack",
            "survival_armor_counterattack",
            "survival_armor_repair",
            "equipment_sharpen_chain",
            "visibility_thermal_chain",
            "visibility_magic_chain", "visibility_tech_chain",
            "attribute_magic_armor_choice", "attribute_tech_armor_choice",
            "police_command", "police_election",
            "talent_t1_improvise", "talent_t3_public",
            "talent_g1_burn", "talent_g6_borrow",
            "talent_g7_suppress", "talent_t6_hotline",
        }:
            raise ValueError(f"未知 RL course_scenario={scenario!r}")
        assert self._state is not None
        assert self._rl_player is not None

        target = self._state.get_player("ai_0")
        if scenario == "combat_assassin_pressure":
            if target is None:
                raise RuntimeError("双刺客压力课需要至少 2 名 AI 对手")
            opponents = [
                self._state.get_player(f"ai_{idx}")
                for idx in range(self.num_opponents)
            ]
            if len(opponents) < 2 or any(opp is None for opp in opponents):
                raise RuntimeError("双刺客压力课需要至少 2 名 AI 对手")
            from models.equipment import make_armor, make_weapon

            # Match the real visibility-option handoff: the opening policy has
            # spent its three arrows, bought a knife, and the detector expert
            # has just moved to the magic facility and acquired detection.
            self._rl_player.is_awake = True
            self._state.markers.on_player_wake_up("rl_0")
            self._rl_player.location = "魔法所"
            self._rl_player.credits = 0
            self._rl_player.arrows = 0
            if not self._rl_player.has_weapon("小刀"):
                self._rl_player.weapons.append(make_weapon("小刀"))
            self._rl_player.grant_visibility_item("探测魔法")
            for idx, opponent in enumerate(opponents):
                opponent.is_awake = True
                self._state.markers.on_player_wake_up(opponent.player_id)
                opponent.location = f"home_{opponent.player_id}"
                opponent.hp = 17 if idx == 0 else 20
                opponent.is_invisible = True
                opponent.grant_visibility_item("隐身术")
                self._state.markers.on_player_go_invisible(
                    opponent.player_id, list(self._state.players.values()),
                )
                armor = make_armor("陶瓷护甲")
                if armor is None:
                    raise RuntimeError("双刺客压力课无法创建陶瓷护甲")
                added, reason = opponent.add_armor(armor)
                if not added:
                    raise RuntimeError(
                        f"双刺客压力课无法装备陶瓷护甲: {reason}"
                    )
            self._state.rl_course_scenario = scenario
            return

        if scenario in {
            "visibility_thermal_chain",
            "visibility_magic_chain",
            "visibility_tech_chain",
        }:
            remaining = self._state.get_player("ai_1")
            if target is None or remaining is None:
                raise RuntimeError("属性探测一对多课程需要至少 2 名 AI 对手")
            from controllers.bots.dummy_bot import DummyBotController
            from models.equipment import make_weapon

            detector_specs = {
                "visibility_thermal_chain": (
                    "普通", "隐身衣", "商店", 3,
                ),
                "visibility_magic_chain": (
                    "魔法", "隐身术", "魔法所", 0,
                ),
                "visibility_tech_chain": (
                    "科技", "隐形涂层", "军事基地", 3,
                ),
            }
            stealth_attr, stealth_item, facility, credits = detector_specs[scenario]
            # 隐身目标与RL同处对应设施，但在没有同属性探测时连find都不合法；
            # 取得探测后可按 find→attack→换位→find→attack 在时限内完成二杀。
            self._rl_player.is_awake = True
            self._state.markers.on_player_wake_up("rl_0")
            self._rl_player.location = facility
            self._rl_player.credits = credits
            if scenario == "visibility_tech_chain":
                self._rl_player.has_military_pass = True
            self._rl_player.weapons = [make_weapon("小刀")]
            for idx, opponent in enumerate((target, remaining)):
                opponent.is_awake = True
                self._state.markers.on_player_wake_up(opponent.player_id)
                opponent.hp = 4
                opponent.controller = DummyBotController()
                self._opponent_model_stems[opponent.player_id] = "dummy"
                opponent.location = (
                    facility if idx == 0 else f"home_{opponent.player_id}"
                )
            target.is_invisible = True
            target.grant_visibility_item(stealth_item)
            if stealth_attr not in target.stealth_attrs:
                raise RuntimeError(f"课程无法建立{stealth_attr}隐身")
            self._state.markers.on_player_go_invisible(
                target.player_id, list(self._state.players.values()),
            )
            # 超时只杀可见目标时，让仍隐身的目标稳定赢得M9评分；二杀自然胜。
            target.damage_dealt = 60
            self._state.m9_scoring.add_arc(target.player_id, 1)
            self._state.rl_course_scenario = scenario
            return

        if scenario in {
            "attribute_magic_armor_choice", "attribute_tech_armor_choice",
        }:
            if target is None:
                raise RuntimeError("属性护甲辨别课需要至少 1 名 AI 对手")
            from controllers.bots.rush_bot import RushBotController
            from models.equipment import make_armor, make_weapon

            # 两个状态只改变目标外甲。魔盾对魔法主防、科技零防；AT力场
            # 对科技主防、魔法副防。RL 同时持两种武器且必定先手：选对属性
            # 一击终结，选错则留下目标并被4伤小刀反杀。
            armor_name, target_hp = {
                "attribute_magic_armor_choice": ("魔法护盾", 6),
                "attribute_tech_armor_choice": ("AT力场", 4),
            }[scenario]
            self._rl_player.is_awake = True
            self._state.markers.on_player_wake_up("rl_0")
            self._rl_player.location = "home_rl_0"
            self._rl_player.hp = 4
            self._rl_player.weapons = [
                make_weapon("魔法弹幕"), make_weapon("高斯步枪"),
            ]
            self._rl_player.get_initiative_bonus = lambda: 100

            target.is_awake = True
            self._state.markers.on_player_wake_up(target.player_id)
            target.location = self._rl_player.location
            target.hp = target_hp
            target.weapons = [make_weapon("小刀")]
            target.controller = RushBotController()
            self._opponent_model_stems[target.player_id] = "rush"
            armor = make_armor(armor_name)
            if armor is None:
                raise RuntimeError(f"属性护甲课程无法创建{armor_name}")
            added, reason = target.add_armor(armor)
            if not added:
                raise RuntimeError(f"属性护甲课程无法装备{armor_name}: {reason}")
            self._state.markers.add_relation(
                "rl_0", "ENGAGED_WITH", target.player_id,
            )
            self._state.markers.add_relation(
                target.player_id, "ENGAGED_WITH", "rl_0",
            )
            self._state.rl_course_scenario = scenario
            return

        if scenario == "equipment_sharpen_chain":
            remaining = self._state.get_player("ai_1")
            if target is None or remaining is None:
                raise RuntimeError("磨刀一对多课程需要至少 2 名 AI 对手")
            from controllers.bots.dummy_bot import DummyBotController
            from models.equipment import make_weapon

            # 两个13HP目标：4伤小刀需要4+4刀；磨刀虽花2拍准备，但7伤
            # 只需2+2刀。加入换位/find后，9轮时限内只有磨刀路线能二杀。
            self._rl_player.is_awake = True
            self._state.markers.on_player_wake_up("rl_0")
            self._rl_player.location = "商店"
            self._rl_player.credits = 2
            self._rl_player.weapons = [make_weapon("小刀")]
            for idx, opponent in enumerate((target, remaining)):
                opponent.is_awake = True
                self._state.markers.on_player_wake_up(opponent.player_id)
                opponent.hp = 13
                opponent.controller = DummyBotController()
                self._opponent_model_stems[opponent.player_id] = "dummy"
                opponent.location = (
                    "商店" if idx == 0 else f"home_{opponent.player_id}"
                )
            # 第二目标带封顶伤害分5+一章剧情分2，形成稳定排名门槛：只杀
            # 第一个目标最高6.75分仍不足，完成两杀约12.3分才超过它。
            remaining.damage_dealt = 60
            self._state.m9_scoring.add_arc(remaining.player_id, 1)
            self._state.markers.add_relation("rl_0", "ENGAGED_WITH", "ai_0")
            self._state.markers.add_relation("ai_0", "ENGAGED_WITH", "rl_0")
            self._state.rl_course_scenario = scenario
            return

        if scenario in {
            "survival_heal_counterattack", "survival_armor_counterattack",
            "survival_armor_repair",
        }:
            remaining = self._state.get_player("ai_1")
            if target is None or remaining is None:
                raise RuntimeError("治疗反击课程需要至少 2 名 AI 对手")
            from controllers.bots.dummy_bot import DummyBotController
            from controllers.bots.rush_bot import RushBotController
            from models.equipment import make_weapon

            # 一对二生存闭环：RL 固定先手只消除骰运噪声。治疗/购甲课的
            # 近敌为5HP；修甲课从1耐久陶瓷甲与9HP近敌开始，直接进攻时
            # 护甲会在第一击后破碎，撑不到第三刀，先修理才有足够耐久。
            self._rl_player.is_awake = True
            self._state.markers.on_player_wake_up("rl_0")
            self._rl_player.location = (
                "医院" if scenario == "survival_heal_counterattack" else "商店"
            )
            self._rl_player.hp = 4
            self._rl_player.credits = {
                "survival_heal_counterattack": 2,
                "survival_armor_counterattack": 3,
                "survival_armor_repair": 1,
            }[scenario]
            self._rl_player.weapons = [make_weapon("小刀")]
            if scenario == "survival_armor_repair":
                from models.equipment import make_armor
                ceramic = make_armor("陶瓷护甲")
                assert ceramic is not None
                ceramic.durability = 1
                added, reason = self._rl_player.add_armor(ceramic)
                if not added:
                    raise RuntimeError(f"修甲课程无法装备陶瓷护甲: {reason}")
            self._rl_player.get_initiative_bonus = lambda: 100

            target.is_awake = True
            self._state.markers.on_player_wake_up(target.player_id)
            target.location = self._rl_player.location
            target.hp = 9 if scenario == "survival_armor_repair" else 5
            target.weapons = [make_weapon("小刀")]
            target.controller = RushBotController()
            self._opponent_model_stems[target.player_id] = "rush"
            self._state.markers.add_relation(
                "rl_0", "ENGAGED_WITH", target.player_id,
            )
            self._state.markers.add_relation(
                target.player_id, "ENGAGED_WITH", "rl_0",
            )

            remaining.is_awake = True
            self._state.markers.on_player_wake_up(remaining.player_id)
            remaining.location = f"home_{remaining.player_id}"
            remaining.hp = 5
            remaining.controller = DummyBotController()
            self._opponent_model_stems[remaining.player_id] = "dummy"
            self._state.rl_course_scenario = scenario
            return

        if scenario in {"pp_damage_finish", "pp_damage_choice"}:
            if target is None:
                raise RuntimeError("PP 加伤课程需要至少 1 名 ai_0 对手")
            from controllers.bots.dummy_bot import DummyBotController
            from controllers.bots.rush_bot import RushBotController
            from models.equipment import make_weapon

            # 原子 PP 闭环：5 HP 靶正好比小刀基础 4 伤多 1；先花 1 PP
            # 取得下一击 +1，随后一刀终结。专项 mask 在增伤尚未挂载时隐藏
            # 普通攻击，防止课程被“两刀照样杀”绕过；真实局规则完全不变。
            self._rl_player.is_awake = True
            self._state.markers.on_player_wake_up("rl_0")
            self._rl_player.location = "home_rl_0"
            self._rl_player.weapons = [make_weapon("小刀")]
            self._state.m9_pp.earn("rl_0", 1)
            target.is_awake = True
            self._state.markers.on_player_wake_up(target.player_id)
            target.location = self._rl_player.location
            target.hp = 5
            if scenario == "pp_damage_choice":
                # 应用题：RL 必定先手且只有 4 HP；直接砍 4 伤会留下 1 HP，
                # 随后被持刀 rush 反杀。PP加伤不耗标准行动，先加伤再砍则
                # 在对手行动前以 5 伤终结。由真实战斗结果区分两条路线。
                self._rl_player.hp = 4
                self._rl_player.get_initiative_bonus = lambda: 100
                target.weapons = [make_weapon("小刀")]
                target.controller = RushBotController()
                self._opponent_model_stems[target.player_id] = "rush"
            else:
                target.controller = DummyBotController()
                self._opponent_model_stems[target.player_id] = "dummy"
            self._state.markers.add_relation(
                "rl_0", "ENGAGED_WITH", target.player_id,
            )
            self._state.markers.add_relation(
                target.player_id, "ENGAGED_WITH", "rl_0",
            )
            self._state.rl_course_scenario = scenario
            return

        if scenario in {
            "economy_pursuit_supply", "economy_armored_pursuit_supply",
            "economy_armored_pursuit_supply_slot0",
        }:
            other = self._state.get_player("ai_1")
            if target is None or other is None:
                raise RuntimeError("移动补给续战课程需要至少 2 名 AI 对手")
            from controllers.bots.dummy_bot import DummyBotController
            from engine.bow_modules import module_specs, price_of
            from models.equipment import make_armor, make_bow, make_weapon

            armored_bridge = scenario.startswith("economy_armored_pursuit_supply")
            mirror_survivor = scenario.endswith("_slot0")
            dead, remaining = ((other, target) if mirror_survivor
                               else (target, other))

            # 应用课从真实的后杀资源状态开始：第一席已死亡、RL 已有一杀，
            # 另一席仍由完整 BasicAI 决策并可每轮移动。镜像课交换存活槽位，
            # 防止策略把“追击 ai_1”记成答案。RL 没有近战绕行出口，
            # 必须移动到商店，完成打工→补箭→锁定/追射，并在箭耗尽后再次补给。
            self._rl_player.is_awake = True
            self._state.markers.on_player_wake_up("rl_0")
            self._rl_player.location = f"home_{dead.player_id}"
            self._rl_player.weapons = (
                [make_weapon("小刀"), make_bow()]
                if armored_bridge else [make_bow()]
            )
            self._rl_player.arrows = 3 if armored_bridge else 0
            self._rl_player.credits = 0
            self._rl_player.kill_count = 1
            if armored_bridge:
                self._rl_player.hp = 12
            # 这节只考资源调度/追射；可见性和先手作为已掌握前置固定住，
            # 防止完整 BasicAI 买到异属性隐身后把课程偷换成探测器课。
            self._rl_player.has_detection = True
            self._rl_player.detection_attrs = {"普通", "魔法", "科技"}
            self._rl_player.get_initiative_bonus = lambda: 100

            dead.hp = 0
            dead.death_round = 1
            dead.location = None
            dead.controller = DummyBotController()
            self._opponent_model_stems[dead.player_id] = "dummy"
            self._state.markers.on_player_death(dead.player_id)
            self._state.m9_pp.freeze(dead.player_id)

            remaining.is_awake = True
            self._state.markers.on_player_wake_up(remaining.player_id)
            remaining.location = f"home_{remaining.player_id}"
            # BasicAI 仍会完整移动、打工、购买、治疗与隐身；只清空初始杀伤，
            # 并把治疗上限锁到 6，使补箭追射在 30 轮风洞里稳定可达。
            remaining.hp = 20 if armored_bridge else 6
            remaining.max_hp = remaining.hp
            remaining.weapons = []
            remaining.arrows = 0
            remaining.credits = 0
            if armored_bridge:
                shield = make_armor("盾牌")
                if shield is None:
                    raise RuntimeError("装甲追击课程无法创建盾牌")
                added, reason = remaining.add_armor(shield)
                if not added:
                    raise RuntimeError(f"装甲追击课程无法装备盾牌: {reason}")
            # 只活到时限不能靠已有一杀拿到正式胜利；完成第二杀自然清场。
            remaining.damage_dealt = 60
            self._state.m9_scoring.add_arc(remaining.player_id, 1)
            self._state.rl_course_scenario = scenario
            return

        if scenario == "equipment_fire_antiarmor":
            remaining = self._state.get_player("ai_1")
            if target is None or remaining is None:
                raise RuntimeError("火矢反装甲课程需要至少 2 名 AI 对手")
            from controllers.bots.dummy_bot import DummyBotController
            from models.equipment import make_armor, make_bow, make_weapon

            # 真实首杀后负载：普通弓打陶瓷甲每箭只剩最低伤害；魔法所免费
            # 火矢把同一把跨地点弓改成魔法属性，再接回补箭/追射循环。
            self._rl_player.is_awake = True
            self._state.markers.on_player_wake_up("rl_0")
            self._rl_player.location = "魔法所"
            self._rl_player.weapons = [make_weapon("小刀"), make_bow()]
            self._rl_player.arrows = 3
            self._rl_player.credits = 0
            self._rl_player.kill_count = 1
            self._rl_player.hp = 16
            self._rl_player.has_detection = True
            self._rl_player.detection_attrs = {"普通", "魔法", "科技"}
            self._rl_player.get_initiative_bonus = lambda: 100

            target.hp = 0
            target.death_round = 1
            target.location = None
            target.controller = DummyBotController()
            self._opponent_model_stems[target.player_id] = "dummy"
            self._state.markers.on_player_death(target.player_id)
            self._state.m9_pp.freeze(target.player_id)

            remaining.is_awake = True
            self._state.markers.on_player_wake_up(remaining.player_id)
            remaining.location = f"home_{remaining.player_id}"
            remaining.hp = 20
            remaining.max_hp = 20
            remaining.weapons = [make_bow()]
            remaining.arrows = 3
            remaining.credits = 0
            ceramic = make_armor("陶瓷护甲")
            if ceramic is None:
                raise RuntimeError("火矢反装甲课程无法创建陶瓷护甲")
            added, reason = remaining.add_armor(ceramic)
            if not added:
                raise RuntimeError(f"火矢反装甲课程无法装备陶瓷护甲: {reason}")
            remaining.damage_dealt = 60
            self._state.m9_scoring.add_arc(remaining.player_id, 1)
            self._state.rl_course_scenario = scenario
            return

        if scenario == "equipment_bow_loadout":
            remaining = self._state.get_player("ai_1")
            if target is None or remaining is None:
                raise RuntimeError("统一弓配置课程需要至少 2 名 AI 对手")
            from controllers.bots.dummy_bot import DummyBotController
            from engine.bow_modules import module_specs, price_of
            from models.equipment import make_armor, make_bow, make_weapon

            # 同一课程 ID 随机出六种可观察需求。教师标签只保存在 state 作
            # 诊断/监督，不进入 observation；策略必须依据箭量、锁定、目标地点
            # 与护甲三属性选择模块，并在移动一拍后用 LSTM 记住选择。
            module_name = (
                "力量", "火矢", "魔导", "穿甲", "冲击", "无限",
            )[int(self.np_random.integers(6))]
            module_cases = {
                "力量": {"hp": 16, "arrows": 6, "armor": None,
                       "locked": True, "target_location": "home_ai_1"},
                "火矢": {"hp": 20, "arrows": 6, "armor": "陶瓷护甲",
                       "locked": True, "target_location": "home_ai_1"},
                "魔导": {"hp": 16, "arrows": 6, "armor": None,
                       "locked": False, "target_location": "home_ai_1"},
                "穿甲": {"hp": 20, "arrows": 6, "armor": "魔法护盾",
                       "locked": True, "target_location": "home_ai_1"},
                "冲击": {"hp": 20, "arrows": 6, "armor": None,
                       "locked": True, "target_location": "军事基地"},
                "无限": {"hp": 20, "arrows": 0, "armor": None,
                       "locked": True, "target_location": "home_ai_1"},
            }
            case = module_cases[module_name]

            # 同一课程内做自然首杀状态的 nuisance randomization；模块身份仍
            # 只由需求特征决定。尤其火矢必须同时见过普通高防的盾牌与陶瓷甲，
            # 否则策略会把装备名/固定 16HP/固定 home 起点当成课程标签。
            module_location = str(module_specs()[module_name]["location"])
            start_locations = [
                "home_ai_0", "商店", "魔法所", "医院", "军事基地", "警察局",
            ]
            start_locations = [
                location for location in start_locations
                if location != module_location
            ]
            player_start = start_locations[
                int(self.np_random.integers(len(start_locations)))
            ]
            player_hp = (11, 13, 16, 20)[int(self.np_random.integers(4))]
            player_arrows = (
                0 if module_name == "无限"
                else int(self.np_random.integers(1, 7))
            )
            minimum_credits = int(price_of(module_name))
            player_credits = max(
                minimum_credits,
                int(self.np_random.integers(0, 7)),
            )
            armor_name = case["armor"]
            if module_name == "火矢" and bool(self.np_random.integers(2)):
                armor_name = "盾牌"

            self._rl_player.is_awake = True
            self._state.markers.on_player_wake_up("rl_0")
            self._rl_player.location = player_start
            self._rl_player.weapons = [make_weapon("小刀"), make_bow()]
            self._rl_player.arrows = player_arrows
            self._rl_player.credits = player_credits
            self._rl_player.kill_count = 1
            self._rl_player.hp = player_hp
            self._rl_player.has_military_pass = True
            self._rl_player.has_detection = True
            self._rl_player.detection_attrs = {"普通", "魔法", "科技"}
            self._rl_player.get_initiative_bonus = lambda: 100

            target.hp = 0
            target.death_round = 1
            target.location = None
            target.controller = DummyBotController()
            self._opponent_model_stems[target.player_id] = "dummy"
            self._state.markers.on_player_death(target.player_id)
            self._state.m9_pp.freeze(target.player_id)

            # ai_1 保留 reset 时创建的完整 BasicAIController：这不是静态靶，
            # 安装后仍需接回真实的移动、发育、追击、补给和战斗闭环。
            remaining.is_awake = True
            self._state.markers.on_player_wake_up(remaining.player_id)
            remaining.location = str(case["target_location"])
            remaining.hp = int(case["hp"])
            remaining.max_hp = remaining.hp
            remaining.weapons = [make_weapon("小刀")]
            remaining.arrows = 0
            remaining.credits = 0
            if armor_name:
                armor = make_armor(str(armor_name))
                if armor is None:
                    raise RuntimeError(f"统一弓配置课程无法创建{armor_name}")
                added, reason = remaining.add_armor(armor)
                if not added:
                    raise RuntimeError(
                        f"统一弓配置课程无法装备{armor_name}: {reason}"
                    )
            if case["locked"]:
                self._state.markers.add_relation(
                    remaining.player_id, "LOCKED_BY", self._rl_player.player_id,
                )
            remaining.damage_dealt = 60
            self._state.m9_scoring.add_arc(remaining.player_id, 1)
            self._state.rl_bow_loadout_target_module = module_name
            self._state.rl_course_scenario = scenario
            return

        if scenario == "economy_bow_supply":
            if target is None:
                raise RuntimeError("弓补给课程需要至少 1 名 ai_0 对手")
            from controllers.bots.dummy_bot import DummyBotController
            from models.equipment import make_bow

            # 原子经济闭环：0 信用点/0 箭起步，打工一次获得 2 点，
            # 花 1 点补 2 支箭，恰好用两次 3 伤射击击杀 6 HP 远程靶。
            # 不奖励“持有钱”本身，唯一成功出口仍是亲手战果。
            self._rl_player.is_awake = True
            self._state.markers.on_player_wake_up("rl_0")
            self._rl_player.location = "商店"
            self._rl_player.weapons = [make_bow()]
            self._rl_player.arrows = 0
            self._rl_player.credits = 0
            target.is_awake = True
            self._state.markers.on_player_wake_up(target.player_id)
            target.location = f"home_{target.player_id}"
            target.hp = 6
            target.controller = DummyBotController()
            self._opponent_model_stems[target.player_id] = "dummy"
            self._state.markers.add_relation(
                target.player_id, "LOCKED_BY", self._rl_player.player_id,
            )
            self._state.rl_course_scenario = scenario
            return

        if scenario in {
            "combat_multi_quarter_hp", "combat_multi_half_hp",
            "combat_multi_full_hp",
        }:
            opponents = [
                self._state.get_player(f"ai_{idx}")
                for idx in range(self.num_opponents)
            ]
            if len(opponents) < 2 or any(opp is None for opp in opponents):
                raise RuntimeError("半血多人课程需要至少 2 名 AI 对手")
            # 保留正常开局（起床、拿武器、移动、BasicAI 全量决策），
            # 只缩短首杀/二杀的延迟，避免 40 HP 稀疏终局把多个技能捆在一起。
            hp_cap = {
                "combat_multi_quarter_hp": 5,
                "combat_multi_half_hp": 10,
                "combat_multi_full_hp": 20,
            }[scenario]
            for opponent in opponents:
                opponent.hp = min(int(opponent.hp), hp_cap)
            self._state.rl_course_scenario = scenario
            return

        if scenario in {"combat_retarget", "combat_retarget_static"}:
            remaining = self._state.get_player("ai_1")
            if target is None or remaining is None:
                raise RuntimeError("续战课程需要至少 2 名 AI 对手")
            from models.equipment import make_weapon

            # 真实首杀前一拍：RL 先在同一 episode 内击杀 1 HP 的 ai_0，
            # 随后必须保留 LSTM 状态、重新选择仍由完整 BasicAI 控制的 ai_1。
            # 这训练的是“首杀不是终局”，而不是 reset 后静态看见一个死者。
            self._rl_player.is_awake = True
            self._state.markers.on_player_wake_up("rl_0")
            self._rl_player.location = "home_ai_0"
            self._rl_player.weapons = [make_weapon("小刀")]
            target.is_awake = True
            self._state.markers.on_player_wake_up(target.player_id)
            target.location = self._rl_player.location
            target.hp = 1
            remaining.is_awake = True
            self._state.markers.on_player_wake_up(remaining.player_id)
            remaining.location = f"home_{remaining.player_id}"
            if scenario == "combat_retarget_static":
                # 先把 slot1 的追击/调查/攻击语义教稳；仍然保留
                # “本 episode 首杀后切换目标”的 LSTM 链，只拿掉第二
                # 对手的随机移动/发育。通过后再回 combat_retarget full BasicAI。
                from controllers.bots.dummy_bot import DummyBotController
                remaining.controller = DummyBotController()
                self._opponent_model_stems[remaining.player_id] = "dummy"
            self._state.markers.add_relation(
                "rl_0", "ENGAGED_WITH", target.player_id,
            )
            self._state.markers.add_relation(
                target.player_id, "ENGAGED_WITH", "rl_0",
            )
            self._state.rl_course_scenario = scenario
            return

        if scenario in {
            "talent_t1_improvise", "talent_t3_public",
            "talent_g1_burn", "talent_g6_borrow", "talent_g7_suppress",
        }:
            if target is None:
                raise RuntimeError("天赋课程需要至少 1 名 ai_0 对手")
            from models.equipment import make_weapon

            # 可归因靶场：T1 擦伤/T3 天星均至少造成 1 点，因此 1 HP 靶
            # 必定终局；普通 attack 由 talent_drill 合同屏蔽。
            self._rl_player.is_awake = True
            self._state.markers.on_player_wake_up("rl_0")
            self._rl_player.location = "home_rl_0"
            target.is_awake = True
            self._state.markers.on_player_wake_up(target.player_id)
            target.location = self._rl_player.location
            target.hp = 1
            # 只保留一把武器，避免发动 T1 后再出现“拳击/小刀”的次级 choose；
            # 首课只训练一个清晰决策：发动或不发动。
            self._rl_player.weapons = [make_weapon("小刀")]
            self._state.markers.add_relation(
                "rl_0", "ENGAGED_WITH", target.player_id,
            )
            self._state.markers.add_relation(
                target.player_id, "ENGAGED_WITH", "rl_0",
            )
            # P5 首批（B 组零激活）：G1 换装/G6 即演/G7 即演均为 SP1 的
            # T0 choose 通道（talent_drill 不裁剪 choose）；G1 初始即卸甲
            # 形态，sp=1 时 get_t0_option 必出 g1_dress。G6 类别合法性依赖
            # 模板池，靶场布局下至少一个类别可重演（探针实证）。
            self._state.m9_system.set_sp("rl_0", 1)
            if scenario == "talent_t3_public":
                # T3 无即演入口；先让策略在 R0 学会选择“报名公演”，随后
                # 才会在同轮 T0 获得天星发动选择。这是首个真正的两阶段天赋课。
                self._state.m9_system.set_sp("rl_0", 2)
            self._state.rl_course_scenario = scenario
            return

        if scenario == "talent_t6_hotline":
            if target is None:
                raise RuntimeError("T6 热线课程需要至少 1 名 ai_0 对手")
            # 热线不读 SP、不走 T0：资格 = 受害者/目击者/特别线索。课程
            # 直接登记一条特别线索（special_clues_for 只按 type+suspect 过滤
            # event_log——天赋对象此时尚未分配，绕开 talent.record_special_clue）。
            if not (
                isinstance(self.rl_talent, int) and self.rl_talent == 6
            ):
                raise RuntimeError(
                    "T6 热线课程需要 rl_talent=6（朝阳好市民）"
                )
            station = getattr(self._state, "m9_police", None)
            if station is None:
                raise RuntimeError("T6 热线课程需要 M9 警察机制")
            station.set_state_ref(self._state)
            station.automatic_enforcement_enabled = False
            for unit in station.ensure_roster(initial_location="警察局"):
                unit.location = "警察局"
                unit.acted_this_round = False
            target.is_awake = True
            self._state.markers.on_player_wake_up(target.player_id)
            target.location = f"home_{target.player_id}"
            self._rl_player.is_awake = True
            self._state.markers.on_player_wake_up("rl_0")
            self._rl_player.location = "home_rl_0"
            self._state.log_event(
                "special_clue", player="rl_0",
                suspect=target.player_id, clue="course_drill",
                round=1,
            )
            self._state.rl_course_scenario = scenario
            return

        station = getattr(self._state, "m9_police", None)
        if station is None or target is None:
            raise RuntimeError("警察课程需要 M9 警察机制与至少 1 名 ai_0 对手")
        station.set_state_ref(self._state)
        station.automatic_enforcement_enabled = False
        for unit in station.ensure_roster(initial_location="警察局"):
            unit.location = "警察局"
            unit.acted_this_round = False

        case = station.file_case(
            "rl_0", target.player_id, evidence=1,
        )
        if case is None or not station.verify_case(case.case_id):
            raise RuntimeError("警察课程无法建立唯一已核验通缉")

        # 第一课直接给权限，只学“有效指挥”；第二课保留空缺席位，要求策略
        # 先竞选、跨过下一轮 R2 任命，再使用同一套指挥动作。
        if scenario == "police_command":
            if not station.apply_captain("rl_0"):
                raise RuntimeError("警察课程无法登记 RL 队长候选")
            station.r2_tick(self._state, 0)
            if station.captain_id != "rl_0":
                raise RuntimeError("警察课程无法预任命 RL 队长")
        self._state.rl_course_scenario = scenario

    # ══════════════════════════════════════════════════════════════════════════
    #  天赋分配
    # ══════════════════════════════════════════════════════════════════════════

    def _assign_talents(self, ai_personalities: Dict[str, str]):
        """
        为所有玩家分配天赋。
        RL 玩家：延迟到游戏进程中自己决定。
        AI 对手：按人格偏好加权随机选择。
        """
        assert self._state is not None

        taken: set = set()
        talent_table = talent_table_for_current_profile(self._state)

        # 强制随机模式下，RL 先选（确保均匀分布，不受 AI 偏好影响）
        if self.force_random_talent:
            rl_player = self._state.get_player("rl_0")
            if rl_player is not None:
                all_available = [
                    (n, name, cls, desc) for n, name, cls, desc in talent_table
                ]
                if all_available:
                    chosen = all_available[self.np_random.integers(len(all_available))]
                    n, name, cls, desc = chosen
                    self._pre_game_phase = True
                    try:
                        assign_talent_entry(self._state, rl_player, chosen)
                    finally:
                        self._pre_game_phase = False
                    taken.add(n)
                # 标记 RL 已分配
                self._rl_talent_assigned = True

        for pid in self._state.player_order:
            player = self._state.get_player(pid)
            if player is None:
                continue

            # 可用天赋列表（排除已选走的）
            available = [
                (n, name, cls, desc) for n, name, cls, desc in talent_table
                if n not in taken
            ]
            if not available:
                continue

            # ── RL 玩家 ──
            if pid == "rl_0":
                # 如果已在上面分配过（force_random_talent），跳过
                # 否则延迟到游戏线程中通过 choose 同步分配
                continue

            # ── AI 对手 ──
            if not self.enable_opponent_talents:
                continue
            # 排除 AI 禁用天赋；预留池开启时排除 RL 指定编号（P3 裁决）
            reserved_number = (
                self.rl_talent if self.rl_talent_reserve else None
            )
            ai_available = [
                (n, name, cls, desc) for n, name, cls, desc in available
                if n not in AI_DISABLED_TALENTS and n != reserved_number
            ]
            if not ai_available:
                continue

            personality = ai_personalities.get(pid, "balanced")
            result = _ai_pick_talent_for_env(
                personality, ai_available, taken, self.np_random
            )
            if result is None:
                continue

            n, name, cls = result
            entry = next(item for item in available if item[0] == n)
            assign_talent_entry(self._state, player, entry)
            taken.add(n)

        # 保存已选天赋集合，供 _assign_rl_talent 使用
        self._taken_talents = taken

    def _assign_rl_talent(self):
        """
        在游戏线程中为 RL 玩家分配天赋。

        - rl_talent=0  : 无天赋，直接跳过
        - rl_talent=N  : 指定天赋编号，直接分配（不走 choose）
        - rl_talent=None: 通过 choose 同步让 RL 自主选择
        """
        assert self._state is not None
        assert self._rl_player is not None
        assert self._rl_controller is not None

        # 如果 _assign_talents 中已经分配过（force_random_talent 模式），跳过
        if getattr(self, '_rl_talent_assigned', False):
            self._rl_talent_assigned = False  # 重置标记
            return

        taken = getattr(self, '_taken_talents', set())
        talent_table = talent_table_for_current_profile(self._state)

        # 强制随机分配模式（训练前期探索用）
        if self.force_random_talent:
            available = [
                (n, name, cls, desc) for n, name, cls, desc in talent_table
                if n not in taken
            ]
            if available:
                chosen = available[self.np_random.integers(len(available))]
                n, name, cls, desc = chosen
                assign_talent_entry(self._state, self._rl_player, chosen)
            return

        # 明确不要天赋
        if self.rl_talent == 0:
            return

        # 指定天赋编号：直接分配
        if self.rl_talent is not None and self.rl_talent != 0:
            entry = find_talent_entry(
                self.rl_talent, talent_table, game_state=self._state,
            )
            if entry is not None and entry[0] not in taken:
                assign_talent_entry(self._state, self._rl_player, entry)
                return
            # 指定的天赋已被选走，回退到 choose
            # （继续往下走 choose 路径）

        # RL 自主选择：通过 choose 同步
        available = [
            (n, name, cls, desc) for n, name, cls, desc in talent_table
            if n not in taken
        ]
        if not available:
            return

        # 构建选项列表：["一刀缭断", "剪刀手一突", ...]
        option_names = [name for n, name, cls, desc in available]
        # 添加"不选天赋"选项
        option_names.append("不选择天赋")

        # 通过 controller.choose() 走 RL 同步路径
        chosen_name = self._rl_controller.choose(
            "选择你的天赋：",
            option_names,
            context={
                "phase": "pregame",
                "situation": "talent_pick",
                "taken": list(taken),
            }
        )

        if chosen_name == "不选择天赋":
            return

        # 查找选中的天赋并分配
        for n, name, cls, desc in available:
            if name == chosen_name:
                assign_talent_entry(
                    self._state, self._rl_player, (n, name, cls, desc),
                )
                return

    # ══════════════════════════════════════════════════════════════════════════
    #  step
    # ══════════════════════════════════════════════════════════════════════════

    def step(self, action: int):
        assert self._game_thread is not None, "必须先调用 reset()"
        assert self._state is not None
        assert self._rl_player is not None
        assert self._reward_tracker is not None
        assert self._rl_controller is not None
        self._raise_thread_error()

        # 游戏已在上一步结束（防御性检查）
        if self._game_over_flag or (self._state and self._state.game_over):
            raw_obs = build_obs_profile(self._rl_player, self._state, "rl_0",
                                        long_term_stats=self._build_long_term_stats(),
                                        **self._course_obs_kwargs())
            self._fill_choose_obs(raw_obs)
            obs = self._stack_obs(raw_obs)
            reward = self._reward_tracker.compute(
                self._rl_player, self._state, "forfeit", False,
                action_idx=IDX_FORFEIT, forced_forfeit=True,
            )
            info: dict[str, Any] = {"action_masks": self.action_masks()}
            ledger = getattr(self._reward_tracker, "last_ledger", None)
            if ledger is not None:
                info["reward_ledger"] = ledger.to_dict()
            if self._state:
                info.update(self._build_episode_summary())
            return obs, reward, True, False, info

        # 在唤醒游戏线程前冻结决策语义；choose 返回后会立即清空 options/context，
        # 下一决策点也可能切换模式。只有显式开启 trace 时才复制这些诊断数据。
        trace_context: dict[str, Any] | None = None
        decision_id = self._decision_counter
        self._decision_counter += 1
        decision_was_choose = bool(self._choose_mode)
        legal_before = self.action_masks()
        forced_forfeit = bool(
            not decision_was_choose
            and int(action) == IDX_FORFEIT
            and np.count_nonzero(legal_before) == 1
            and bool(legal_before[IDX_FORFEIT])
        )
        if self.trace_decisions:
            mode_before = "choose" if decision_was_choose else "command"
            if decision_was_choose:
                selected_semantic = idx_to_choose_option(
                    action,
                    self._choose_options,
                    self._choose_context.get("situation", ""),
                    self._current_player,
                    self._current_game_state,
                )
            else:
                selected_semantic = idx_to_command(
                    action, self._rl_player, self._state,
                )
            trace_context = {
                "decision_id": decision_id,
                "round_before": int(self._state.current_round),
                "mode_before": mode_before,
                "situation_before": self._choose_context.get("situation", ""),
                "options_before": tuple(self._choose_options)
                if decision_was_choose else (),
                "legal_actions_before": tuple(
                    int(i) for i in np.flatnonzero(self.action_masks())
                ),
                "action_idx": int(action),
                "selected_semantic": selected_semantic,
                "action_spec": action_spec_from_idx(
                    action,
                    self._current_player,
                    self._current_game_state,
                    choose_mode=decision_was_choose,
                    choose_options=self._choose_options if decision_was_choose else None,
                    choose_situation=self._choose_context.get("situation", ""),
                ).to_dict(),
                "event_cursor_before": len(self._state.event_log),
            }

        # ── 根据当前模式处理动作 ──
        if decision_was_choose:
            # choose 模式：将动作翻译为选项索引
            if IDX_CHOOSE_BASE <= action < IDX_CHOOSE_BASE + 16:
                # 通用 choose 索引 (114-129) → 直接映射
                self._pending_choose_idx = action - IDX_CHOOSE_BASE
            elif IDX_TALENT_T0_TARGET_BASE <= action <= IDX_TALENT_T0_SELF:
                # 目标槽位索引 (108-113) → 通过 idx_to_choose_option 翻译
                # _build_choose_mask 对目标选择 situation 启用的正是这些索引
                situation = self._choose_context.get("situation", "")
                chosen_str = idx_to_choose_option(
                    action, self._choose_options, situation,
                    self._current_player, self._current_game_state,
                )
                # 在选项列表中查找匹配的索引
                try:
                    self._pending_choose_idx = self._choose_options.index(chosen_str)
                except ValueError:
                    self._pending_choose_idx = 0  # 安全回退
            else:
                # 安全回退：选第一个选项
                self._pending_choose_idx = 0
        else:
            # get_command 模式：将动作传递给控制器
            self._rl_controller.pending_action_idx = action

        # 唤醒游戏线程
        self._action_event.set()

        # 等待下一个 RL 决策点（get_command 或 choose）或游戏结束
        if not self._obs_event.wait(timeout=30.0):
            raise RuntimeError(
                "Game thread did not reach RL decision point within 30s. "
                "Possible cause: unguarded input() call in game engine code."
            )
        self._obs_event.clear()
        self._raise_thread_error()

        # 读取上一次动作结果（仅 get_command 模式有意义）
        if not decision_was_choose:
            action_type = getattr(self._rl_player, "last_action_type", None) or "forfeit"
            action_success = not (action != IDX_FORFEIT and action_type == "forfeit")
        else:
            action_type = "choose"
            action_success = True

        # ── 判定终止 / 截断 ──
        truncated = bool(self._max_rounds_reached)
        terminated = bool(self._state.game_over) and not truncated

        # 截断时确保后台线程退出
        if truncated and not self._game_over_flag:
            self._game_over_flag = True
            self._action_event.set()

        # ── 更新长线统计 ──
        if not decision_was_choose:
            self._lt_total_actions += 1
            if action_type == "attack":
                self._lt_attack_actions += 1
            current_kills = self._rl_player.kill_count
            if current_kills > self._lt_last_kill_count:
                self._lt_last_kill_round = self._state.current_round
                self._lt_last_kill_count = current_kills

        # 计算奖励
        reward = self._reward_tracker.compute(
            self._rl_player, self._state, action_type, action_success,
            action_idx=action, forced_forfeit=forced_forfeit,
        )

        raw_obs = build_obs_profile(self._rl_player, self._state, "rl_0",
                                    long_term_stats=self._build_long_term_stats(),
                                    **self._course_obs_kwargs())
        self._fill_choose_obs(raw_obs)
        obs = self._stack_obs(raw_obs)
        info = {"action_masks": self.action_masks()}
        reward_ledger = getattr(self._reward_tracker, "last_ledger", None)
        if reward_ledger is not None:
            info["reward_ledger"] = reward_ledger.to_dict()

        if trace_context is not None:
            trace_entry = {
                **trace_context,
                "round_after": int(self._state.current_round),
                "mode_after": "choose" if self._choose_mode else "command",
                "situation_after": self._choose_context.get("situation", ""),
                "action_type_attributed": action_type,
                "action_success_attributed": bool(action_success),
                "event_count": len(self._state.event_log)
                - int(trace_context["event_cursor_before"]),
                "reward": float(reward),
                "reward_ledger": reward_ledger.to_dict()
                if reward_ledger is not None else None,
                "terminated": bool(terminated),
                "truncated": bool(truncated),
            }
            self._decision_trace.append(trace_entry)
            if len(self._decision_trace) > self.max_trace_entries:
                self._decision_trace = self._decision_trace[-self.max_trace_entries:]
            info["decision_trace_entry"] = dict(trace_entry)

        if terminated or truncated:
            summary = self._build_episode_summary()
            episode_ledger = getattr(self._reward_tracker, "episode_ledger", None)
            if callable(episode_ledger):
                summary["episode_reward_ledger"] = episode_ledger()
            info.update(summary)
            self._episode_outcomes.append(self._state.winner)
            self._episode_summaries.append(summary)
            # 将对手模型标识传入 info，由主进程的 SelfPlayCallback 更新 ELO
            if self.opponent_pool is not None:
                opp_stems = [s for s in self._opponent_model_stems.values()
                             if s != "basic_ai"]
                if opp_stems:
                    info["opponent_stems"] = opp_stems

        return obs, reward, terminated, truncated, info

    # ══════════════════════════════════════════════════════════════════════════
    #  局结果追踪（用于坍塌检测）
    # ══════════════════════════════════════════════════════════════════════════

    def get_episode_outcomes(self) -> list[str | None]:
        """返回并清空累积的局结果（winner player_id 列表）。"""
        outcomes = self._episode_outcomes
        self._episode_outcomes = []
        return outcomes

    def get_episode_summaries(self) -> list[dict[str, Any]]:
        """返回并清空结构化终局摘要，供评估和坍塌诊断统一读取。"""
        summaries = self._episode_summaries
        self._episode_summaries = []
        return summaries

    def get_decision_trace(self, *, clear: bool = False) -> list[dict[str, Any]]:
        """返回当前局决策 trace；仅 ``trace_decisions=True`` 时有内容。"""
        trace = [dict(entry) for entry in self._decision_trace]
        if clear:
            self._decision_trace = []
        return trace

    def _build_episode_summary(self) -> dict[str, Any]:
        """构建不改变游戏状态的生存/正式结算双轨摘要。

        ``winner`` 是环境当前实际使用的胜者；``official_winners`` 只读取正式
        M9 settle 后冻结的快照。诊断层绝不主动调用 ``settle()``，否则阶段 1
        会在测量时改变被测生命周期。
        """
        state = self._state
        player = self._rl_player
        if state is None:
            return {
                "winner": None,
                "official_settled": False,
                "official_winners": [],
                "termination_reason": self._termination_reason or "unknown",
            }

        snapshot = getattr(state, "game_winner_snapshot", None)
        official_settled = snapshot is not None
        official_winners = sorted(str(pid) for pid in (snapshot or ()))
        base_scores_raw = getattr(state, "base_final_scores", None)
        display_scores_raw = getattr(state, "final_scores", None)
        base_scores = (
            {str(pid): float(score) for pid, score in base_scores_raw.items()}
            if isinstance(base_scores_raw, dict) else {}
        )
        display_scores = (
            {str(pid): float(score) for pid, score in display_scores_raw.items()}
            if isinstance(display_scores_raw, dict) else {}
        )

        rl_rank = None
        rl_score_margin = None
        if "rl_0" in base_scores:
            rl_score = base_scores["rl_0"]
            rl_rank = 1 + sum(score > rl_score for score in base_scores.values())
            opponent_scores = [
                score for pid, score in base_scores.items() if pid != "rl_0"
            ]
            if opponent_scores:
                rl_score_margin = rl_score - max(opponent_scores)

        return {
            "winner": getattr(state, "winner", None),
            "survival_winner": getattr(state, "survival_winner", None),
            "official_settled": official_settled,
            "official_winners": official_winners,
            "base_final_scores": base_scores,
            "display_final_scores": display_scores,
            "rl_rank": rl_rank,
            "rl_score_margin": rl_score_margin,
            "rounds": int(getattr(state, "current_round", 0) or 0),
            "rl_alive": bool(player is not None and player.is_alive()),
            "rl_kill_count": int(getattr(player, "kill_count", 0) or 0),
            "decisions": int(self._decision_counter),
            "termination_reason": self._termination_reason or "engine_game_over",
        }

    # ══════════════════════════════════════════════════════════════════════════
    #  action_masks（MaskablePPO 接口）
    # ══════════════════════════════════════════════════════════════════════════

    def action_masks(self) -> np.ndarray:
        """返回当前合法动作的 bool 掩码，shape=(action_space.n,)。

        M9 使用固定 6 人 schema。死亡玩家在往世层仍可能收到 choose 决策，
        因此只有“死亡且当前不是 choose”才回退为 forfeit。
        """
        if (
            self._state is None
            or self._state.game_over
            or self._rl_player is None
            or (not self._rl_player.is_alive() and not self._choose_mode)
        ):
            mask = np.zeros(int(self.action_space.n), dtype=bool)
            mask[IDX_FORFEIT] = True
            return mask

        mask = build_action_mask(
            self._rl_player,
            self._state,
            "rl_0",
            choose_mode=self._choose_mode,
            choose_situation=self._choose_context.get("situation", ""),
            choose_options=self._choose_options if self._choose_mode else None,
        )
        contract = getattr(self, "action_contract", None)
        course_scenario = getattr(self, "course_scenario", None)
        if contract == "police_course":
            # 同一个并行课程批次里：专项场景只给指挥靶场，none worker 回放
            # 原基础作战课。两边 action/obs schema 不变，只改变合法动作集合。
            contract = "police_drill" if course_scenario else "combat_core"
        elif contract == "economy_course":
            contract = {
                "economy_bow_supply": "economy_drill",
                "economy_pursuit_supply": "economy_pursuit_drill",
                "economy_armored_pursuit_supply": "economy_pursuit_drill",
                "economy_armored_pursuit_supply_slot0": "economy_pursuit_drill",
            }.get(course_scenario, "combat_core")
        elif contract == "economy_integration_course":
            contract = {
                "economy_bow_supply": "economy_drill",
                "economy_pursuit_supply": "economy_pursuit_drill",
                "economy_armored_pursuit_supply": "economy_pursuit_drill",
                "economy_armored_pursuit_supply_slot0": "economy_pursuit_drill",
            }.get(course_scenario, "combat_economy")
        elif contract == "pp_course":
            contract = "pp_damage_drill" if course_scenario else "combat_core"
        elif contract == "survival_course":
            contract = {
                "survival_heal_counterattack": "survival_heal_drill",
                "survival_armor_counterattack": "survival_armor_drill",
                "survival_armor_repair": "survival_repair_drill",
            }.get(course_scenario, "combat_core")
        elif contract == "equipment_course":
            contract = {
                "equipment_sharpen_chain": "sharpen_drill",
                "equipment_fire_antiarmor": "antiarmor_fire_drill",
                "equipment_bow_loadout": "bow_loadout_drill",
                "visibility_thermal_chain": "visibility_drill",
                "visibility_magic_chain": "visibility_drill",
                "visibility_tech_chain": "visibility_drill",
                "attribute_magic_armor_choice": "attribute_armor_drill",
                "attribute_tech_armor_choice": "attribute_armor_drill",
            }.get(course_scenario, "combat_core")
        elif contract == "talent_course":
            # 天赋专项 worker 只暴露 T0 choose；none worker 继续回放基础作战。
            # T6 热线是根行动 special——走 hotline_drill 放行热线模板槽。
            contract = (
                "hotline_drill"
                if course_scenario == "talent_t6_hotline"
                else "talent_drill"
                if course_scenario
                else "combat_core"
            )
        mask = apply_action_contract(
            mask, contract,
            choose_mode=self._choose_mode,
        )
        if (
            course_scenario == "pp_damage_finish"
            and not self._choose_mode
            and not getattr(self._rl_player, "_m9_pp_damage_bonus", 0)
        ):
            # 第一拍必须建立增伤承诺；PP加伤不消耗标准行动，下一决策点
            # 立即恢复攻击槽。这只是教学 mask，不改变正式对局合法性。
            from rl.action_space import IDX_ATTACK_BASE, IDX_SPECIAL_BASE
            mask[IDX_ATTACK_BASE:IDX_SPECIAL_BASE] = False
        return mask

    def _course_obs_kwargs(self) -> dict[str, bool]:
        """Opt in new course-only features without perturbing old checkpoints."""
        if (
            (
                self.course_scenario == "equipment_bow_loadout"
                or self.enable_bow_context
            )
            and bool(getattr(self._state, "m9_enabled", False))
        ):
            return {"bow_context": True}
        return {}

    # ══════════════════════════════════════════════════════════════════════════
    #  长线统计
    # ══════════════════════════════════════════════════════════════════════════

    def _build_long_term_stats(self) -> dict:
        """构建长线记忆特征字典（8 维），传入 build_obs()。"""
        player = self._rl_player
        state = self._state
        if player is None or state is None:
            return {}

        alive_opps = [
            state.get_player(pid) for pid in state.player_order
            if pid != "rl_0"
            and state.get_player(pid) is not None
            and state.get_player(pid).is_alive()
        ]
        n_alive = len(alive_opps)

        stats: dict = {}
        stats["rl_hp_delta"] = (self._lt_initial_hp - player.hp) / 5.0
        stats["avg_opp_hp_ratio"] = (
            sum(p.hp for p in alive_opps) / (n_alive * 5.0)
        ) if n_alive > 0 else 0.0
        stats["max_opp_kill_count"] = (
            max((p.kill_count for p in alive_opps), default=0) / 5.0
        )

        markers = state.markers
        n_engaged = sum(
            1 for p in alive_opps
            if markers.has_relation(player.player_id, "ENGAGED_WITH", p.player_id)
        )
        stats["n_engaged"] = n_engaged / 5.0

        rounds_since = (
            (state.current_round - self._lt_last_kill_round)
            if self._lt_last_kill_count > 0
            else state.current_round
        )
        stats["rounds_since_last_kill"] = min(
            rounds_since / max(state.max_rounds, 1), 1.0
        )

        stats["rl_attack_ratio"] = (
            self._lt_attack_actions / max(self._lt_total_actions, 1)
        )
        stats["rl_damage_dealt"] = min(
            self._lt_last_kill_count * 5.0
            / max(self.num_opponents * 5.0, 1),
            1.0,
        )

        use_credits = bool(getattr(state, "m9_enabled", False))
        resource_name = "credits" if use_credits else "vouchers"
        resource_norm = 20.0 if use_credits else 3.0
        avg_opp_resource = (
            sum(float(getattr(p, resource_name, 0) or 0) for p in alive_opps)
            / n_alive
        ) if n_alive > 0 else 0.0
        stats["economy_advantage"] = max(
            min(
                (float(getattr(player, resource_name, 0) or 0) - avg_opp_resource)
                / resource_norm,
                1.0,
            ),
            -1.0,
        )

        return stats

    # ══════════════════════════════════════════════════════════════════════════
    #  choose 模式观测填充
    # ══════════════════════════════════════════════════════════════════════════

    def _fill_choose_obs(self, raw_obs: np.ndarray) -> None:
        """
        将 choose 模式指示写入（profile 感知）：
          legacy/v2exp → OBS 末尾 3 维 [536-538]（legacy 位置）；
          m9-rfc       → 全局末尾 [569:OBS_DIM)（头 + 逐选项语义，见 obs_builder_m9）。
        在 build_obs_profile() 返回后、_stack_obs() 之前调用。
        """
        situation = self._choose_context.get("situation", "")
        n_options = len(self._choose_options)
        if self._obs_dim != OBS_DIM:  # m9-rfc（obs_dim_for_profile）
            # 统一弓课程复用非 choose 的 option-tail 空间；真正进入 choose 时
            # 仍由下方完整覆盖。普通决策不能再次清零刚注入的弓配置上下文。
            if not self._choose_mode and self._course_obs_kwargs():
                return
            fill_choose_obs_m9(
                raw_obs,
                choose_mode=self._choose_mode,
                situation=situation,
                n_options=n_options,
                options=self._choose_options,
                player=self._current_player,
                game_state=self._current_game_state,
            )
            return

        from rl.obs_builder import _CHOOSE_SITUATION_MAP, _MAX_CHOOSE_SITUATIONS

        base = OBS_DIM - 3  # choose 观测的起始索引（legacy）

        if self._choose_mode:
            raw_obs[base] = 1.0
            raw_obs[base + 1] = _CHOOSE_SITUATION_MAP.get(situation, 0) / max(_MAX_CHOOSE_SITUATIONS, 1)
            raw_obs[base + 2] = min(n_options, 16) / 16.0
        else:
            raw_obs[base] = 0.0
            raw_obs[base + 1] = 0.0
            raw_obs[base + 2] = 0.0

    # ══════════════════════════════════════════════════════════════════════════
    #  后台游戏线程
    # ══════════════════════════════════════════════════════════════════════════

    def _run_game(self):
        """在后台线程中运行游戏主循环。"""
        assert self._state is not None
        assert self._round_manager is not None
        assert self._rl_controller is not None
        assert self._rl_player is not None
        try:
            # 预缓存 player/state 引用，供 _assign_rl_talent 中的
            # choose 同步使用（正常情况下由 get_command 调用 _cache_player_ref，
            # 但 talent_pick 发生在第一次 get_command 之前）
            self._rl_controller._cache_player_ref(self._rl_player, self._state)

            # ── 游戏开始前：为 RL 玩家分配天赋 ──
            if self.enable_talents:
                self._assign_rl_talent()

            while not self._state.game_over and not self._game_over_flag:
                self._round_manager.run_one_round()

                # 与 RoundManager.run_game_loop 保持同一终局合同。RL 死亡不是终局：
                # 游戏继续推进，死者仍可在 R0 投注窗口通过 choose() 参与往世层。
                winner_id = self._state.check_victory()
                if winner_id:
                    self._state.game_over = True
                    self._state.survival_winner = winner_id
                    self._state.winner = self._round_manager._finalize_winner(
                        winner_id
                    )
                    self._termination_reason = "official_survival_end"
                    break

                # 最大轮数必须在完整轮结束后正式结算，不能由 env.step() 在轮中
                # 抢先截断并写 nobody。
                if self._state.is_max_rounds_reached():
                    self._max_rounds_reached = True
                    self._state.game_over = True
                    self._state.survival_winner = "nobody"
                    self._state.winner = self._round_manager._finalize_winner(
                        "nobody"
                    )
                    self._termination_reason = "official_max_rounds"
                    break
        except _GameAborted:
            if self._termination_reason is None:
                self._termination_reason = "environment_abort"
            pass
        except Exception as e:
            import sys, traceback
            self._thread_error = e
            self._termination_reason = "thread_error"
            sys.stderr.write(f"[ENV ERROR] _run_game crashed: {e}\n")
            traceback.print_exc(file=sys.stderr)
        finally:
            self._game_over_flag = True
            # 如果 env 线程正在等待 choose 同步，也需要唤醒
            self._choose_mode = False
            self._obs_event.set()  # 唤醒 env 线程

    def _raise_thread_error(self) -> None:
        """把后台游戏线程异常传播给 Gym 调用方，禁止生成坏 episode。"""
        error = self._thread_error
        if error is None:
            return
        self._thread_error = None
        raise error

    # ══════════════════════════════════════════════════════════════════════════
    #  清理
    # ══════════════════════════════════════════════════════════════════════════

    def _cleanup(self):
        """终止旧的游戏线程并清理状态。"""
        if self._game_thread is not None and self._game_thread.is_alive():
            self._game_over_flag = True
            self._action_event.set()  # 唤醒阻塞的游戏线程
            self._game_thread.join(timeout=5)
        self._game_thread = None
        self._thread_error = None
        self._current_player = None
        self._current_game_state = None
        # 重置 choose 模式状态
        self._choose_mode = False
        self._choose_options = []
        self._choose_context = {}
        self._pending_choose_idx = 0
        self._taken_talents = set()

    def close(self):
        self._cleanup()
        _restore_display()
        _restore_prompt_manager()
        super().close()
