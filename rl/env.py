"""
rl/env.py
─────────
BadtimeWarEnv —— 主 Gym 封装（天赋局版）

单智能体环境，RL 控制一名玩家，其余由 BasicAIController 控制。
游戏引擎在后台线程运行，通过 threading.Event 与 env 同步。

支持两种同步模式：
  1. get_command 模式：RL 选择主行动（索引 0-107）
  2. choose 模式：RL 回答子决策（索引 108-123）

兼容 sb3-contrib MaskablePPO：
  - action_masks() 方法返回 bool 数组
  - info dict 中也包含 "action_masks"
"""

from __future__ import annotations
import random
import threading
from typing import Any, Optional, List, Dict

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from rl.action_space import (
    ACTION_COUNT, IDX_FORFEIT, IDX_CHOOSE_BASE,
    IDX_TALENT_T0_TARGET_BASE, IDX_TALENT_T0_SELF,
    build_action_mask, idx_to_command, idx_to_choose_option,
)
from rl.obs_builder import OBS_DIM, build_obs
from rl.reward import RewardTracker
from rl.rl_controller import RLController
from controllers.ai_basic import BasicAIController
from engine.game_state import GameState
from engine.round_manager import RoundManager
from models.player import Player
from cli import display as _display_module
from controllers.ai_basic import create_random_ai_controller

# 天赋系统导入
from engine.game_setup import (
    TALENT_TABLE, AI_DISABLED_TALENTS, AI_TALENT_PREFERENCE,
    AI_PERSONALITIES, TALENT_DECAY_FACTOR,
)

_DISPLAY_FUNCS = [
    "show_banner", "show_round_header", "show_phase", "show_d4_results",
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


def _restore_display():
    """恢复 cli.display 的原始函数"""
    for name, func in _original_display.items():
        setattr(_display_module, name, func)
    _original_display.clear()



# ─────────────────────────────────────────────────────────────────────────────
#  内部异常：用于中止后台游戏线程
# ─────────────────────────────────────────────────────────────────────────────

class _GameAborted(Exception):
    pass


# ─────────────────────────────────────────────────────────────────────────────
#  同步版 RLController：在 get_command / choose / confirm 中阻塞等待 env
# ─────────────────────────────────────────────────────────────────────────────

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

        # 设置 choose 模式
        self._env._choose_mode = True
        self._env._choose_options = list(options)
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

        # 立即清除 choose 模式，避免其他线程看到过时状态。
        # 线程安全说明：此时 env 线程阻塞在 _obs_event.wait()，
        # action_masks() 只从 env 线程调用，因此不会并发访问。
        self._env._choose_mode = False
        self._env._choose_options = []
        self._env._choose_context = {}

        if 0 <= chosen_idx < len(options):
            return options[chosen_idx]
        return options[0]  # 安全回退

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
    enable_talents : 是否启用天赋系统
    rl_talent      : RL 玩家的天赋编号（None=随机, 0=无天赋）
    """

    metadata = {"render_modes": ["human"]}

    def __init__(
        self,
        num_opponents: int = 3,
        max_rounds: Optional[int] = None,
        render_mode: Optional[str] = None,
        n_stack: int = 1,
        opponent_pool=None,
        enable_talents: bool = True,
        rl_talent: Optional[int] = None,
    ):
        super().__init__()

        self.num_opponents = num_opponents
        self.max_rounds = max_rounds
        self.render_mode = render_mode
        self.n_stack = n_stack
        self.opponent_pool = opponent_pool
        self.enable_talents = enable_talents
        self.rl_talent = rl_talent  # None=随机, 0=无天赋, 1-14=指定天赋

        # ── Gym 空间 ──
        self.observation_space = spaces.Box(
            low=-1.0, high=1.0, shape=(OBS_DIM * n_stack,), dtype=np.float32
        )
        self.action_space = spaces.Discrete(ACTION_COUNT)

        # ── 帧堆叠缓冲 ──
        self._obs_stack = np.zeros(OBS_DIM * n_stack, dtype=np.float32)

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
        self._max_rounds_reached = False

        # ── 临时引用（由 _SyncRLController 写入）──
        self._current_player = None
        self._current_game_state = None

        # ── choose 同步状态 ──
        self._choose_mode: bool = False
        self._choose_options: List[str] = []
        self._choose_context: Dict = {}
        self._pending_choose_idx: int = 0

        self._taken_talents: set = set()

    def _stack_obs(self, raw_obs: np.ndarray) -> np.ndarray:
        """将新观测推入帧堆叠缓冲，返回拼接后的完整观测。"""
        if self.n_stack <= 1:
            return raw_obs
        self._obs_stack[:-OBS_DIM] = self._obs_stack[OBS_DIM:]
        self._obs_stack[-OBS_DIM:] = raw_obs
        return self._obs_stack.copy()

    def set_num_opponents(self, n: int):
        """供 CurriculumCallback 调用，修改对手数量（下次 reset 生效）。"""
        self.num_opponents = n

    def set_opponent_pool(self, pool):
        """供 SelfPlayCallback 调用，设置对手池（下次 reset 生效）。"""
        self.opponent_pool = pool

    def update_basic_ai_prob(self, new_prob: float):
        """供 SelfPlayCallback 调用，更新对手池的 BasicAI 概率。"""
        if self.opponent_pool is not None:
            self.opponent_pool.basic_ai_prob = new_prob

    # ══════════════════════════════════════════════════════════════════════════
    #  reset
    # ══════════════════════════════════════════════════════════════════════════

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)

        # 清理上一局
        self._cleanup()
        if self.render_mode != "human":
            _silence_display()

        # 创建新游戏
        self._state = GameState()

        # 创建 RL 玩家
        self._rl_controller = _SyncRLController(self)
        self._rl_player = Player("rl_0", "RL_Agent", self._rl_controller)

        # 创建对手（self-play 或 BasicAI）
        ai_players = []
        ai_personalities = {}  # pid → personality
        for i in range(self.num_opponents):
            if self.opponent_pool is not None:
                ctrl = self.opponent_pool.sample_opponent_controller()
                if hasattr(ctrl, 'reset_stack'):
                    ctrl.reset_stack()
                personality = "balanced"  # self-play 对手无人格概念
            else:
                personality = random.choice(AI_PERSONALITIES)
                ctrl = create_random_ai_controller(player_name=f"AI_{i}")
            p = Player(f"ai_{i}", f"AI_{i}", ctrl)
            ai_players.append(p)
            ai_personalities[p.player_id] = personality

        # 随机化玩家顺序
        all_players = [self._rl_player] + ai_players
        self.np_random.shuffle(all_players)
        for p in all_players:
            self._state.add_player(p)

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

        # 创建奖励追踪器
        self._reward_tracker = RewardTracker("rl_0")

        # 重置同步原语
        self._obs_event.clear()
        self._action_event.clear()
        self._game_over_flag = False
        self._max_rounds_reached = False
        self._choose_mode = False
        self._choose_options = []
        self._choose_context = {}

        # 后台线程启动游戏
        self._game_thread = threading.Thread(target=self._run_game, daemon=True)
        self._game_thread.start()

        # 等待第一个 RL 决策点（get_command 或 choose）
        self._obs_event.wait()
        self._obs_event.clear()

        # 初始化奖励追踪器基线
        assert self._rl_player is not None
        assert self._state is not None
        assert self._reward_tracker is not None
        self._reward_tracker.reset(self._rl_player, self._state)

        self._obs_stack = np.zeros(OBS_DIM * self.n_stack, dtype=np.float32)
        raw_obs = build_obs(self._rl_player, self._state, "rl_0")
        self._fill_choose_obs(raw_obs)
        obs = self._stack_obs(raw_obs)
        info = {"action_masks": self.action_masks()}

        return obs, info

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

        for pid in self._state.player_order:
            player = self._state.get_player(pid)
            if player is None:
                continue

            # 可用天赋列表（排除已选走的）
            available = [
                (n, name, cls, desc) for n, name, cls, desc in TALENT_TABLE
                if n not in taken
            ]
            if not available:
                continue

            # ── RL 玩家：延迟到游戏线程中通过 choose 同步分配 ──
            if pid == "rl_0":
                # 记录当前已被选走的天赋，供后续 _assign_rl_talent 使用
                continue

            # ── AI 对手 ──
            # 排除 AI 禁用天赋
            ai_available = [
                (n, name, cls, desc) for n, name, cls, desc in available
                if n not in AI_DISABLED_TALENTS
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
            talent_inst = cls(pid, self._state)
            player.talent = talent_inst
            player.talent_name = name
            talent_inst.on_register()
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

        taken = getattr(self, '_taken_talents', set())

        # 明确不要天赋
        if self.rl_talent == 0:
            return

        # 指定天赋编号：直接分配
        if self.rl_talent is not None and self.rl_talent != 0:
            for n, name, cls, desc in TALENT_TABLE:
                if n == self.rl_talent and n not in taken:
                    talent_inst = cls("rl_0", self._state)
                    self._rl_player.talent = talent_inst
                    self._rl_player.talent_name = name
                    talent_inst.on_register()
                    return
            # 指定的天赋已被选走，回退到 choose
            # （继续往下走 choose 路径）

        # RL 自主选择：通过 choose 同步
        available = [
            (n, name, cls, desc) for n, name, cls, desc in TALENT_TABLE
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
                talent_inst = cls("rl_0", self._state)
                self._rl_player.talent = talent_inst
                self._rl_player.talent_name = name
                talent_inst.on_register()
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

        # 游戏已在上一步结束（防御性检查）
        if self._game_over_flag or (self._state and self._state.game_over):
            raw_obs = build_obs(self._rl_player, self._state, "rl_0")
            self._fill_choose_obs(raw_obs)
            obs = self._stack_obs(raw_obs)
            reward = self._reward_tracker.compute(
                self._rl_player, self._state, "forfeit", False,
                action_idx=IDX_FORFEIT
            )
            info: dict[str, Any] = {"action_masks": self.action_masks()}
            if self._state:
                info["winner"] = getattr(self._state, "winner", None)
            return obs, reward, True, False, info

        # ── 根据当前模式处理动作 ──
        if self._choose_mode:
            # choose 模式：将动作翻译为选项索引
            if IDX_CHOOSE_BASE <= action < IDX_CHOOSE_BASE + 16:
                # 通用 choose 索引 (114-123) → 直接映射
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
        self._obs_event.wait()
        self._obs_event.clear()

        # 读取上一次动作结果（仅 get_command 模式有意义）
        if not self._choose_mode:
            action_type = getattr(self._rl_player, "last_action_type", None) or "forfeit"
            action_success = not (action != IDX_FORFEIT and action_type == "forfeit")
        else:
            # choose 模式下，action_type 沿用上一次 get_command 的结果
            action_type = getattr(self._rl_player, "last_action_type", None) or "choose"
            action_success = True

        # ── 判定终止 / 截断 ──
        truncated = bool(self._max_rounds_reached)
        if not truncated and not self._state.game_over:
            if self._state.is_max_rounds_reached():
                truncated = True
                self._max_rounds_reached = True
                self._state.game_over = True
                self._state.winner = "nobody"
                self._game_over_flag = True
                self._action_event.set()
        terminated = bool(self._state.game_over) and not truncated

        # 截断时确保后台线程退出
        if truncated and not self._game_over_flag:
            self._game_over_flag = True
            self._action_event.set()

        # 计算奖励
        reward = self._reward_tracker.compute(
            self._rl_player, self._state, action_type, action_success,
            action_idx=action
        )

        raw_obs = build_obs(self._rl_player, self._state, "rl_0")
        self._fill_choose_obs(raw_obs)
        obs = self._stack_obs(raw_obs)
        info = {"action_masks": self.action_masks()}

        if terminated or truncated:
            info["winner"] = self._state.winner

        return obs, reward, terminated, truncated, info

    # ══════════════════════════════════════════════════════════════════════════
    #  action_masks（MaskablePPO 接口）
    # ══════════════════════════════════════════════════════════════════════════

    def action_masks(self) -> np.ndarray:
        """返回当前合法动作的 bool 掩码，shape=(ACTION_COUNT,)"""
        if (
            self._state is None
            or self._state.game_over
            or self._rl_player is None
            or not self._rl_player.is_alive()
        ):
            mask = np.zeros(ACTION_COUNT, dtype=bool)
            mask[IDX_FORFEIT] = True
            return mask

        return build_action_mask(
            self._rl_player,
            self._state,
            "rl_0",
            choose_mode=self._choose_mode,
            choose_situation=self._choose_context.get("situation", ""),
            choose_options=self._choose_options if self._choose_mode else None,
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  choose 模式观测填充
    # ══════════════════════════════════════════════════════════════════════════

    def _fill_choose_obs(self, raw_obs: np.ndarray) -> None:
        """
        将 choose 模式指示写入 raw_obs 的末尾 3 维。
        在 build_obs() 返回后、_stack_obs() 之前调用。

        维度布局（相对于 OBS_DIM 末尾 3 维）：
          [-3] current_mode     : 0.0 = get_command, 1.0 = choose
          [-2] situation_id     : 归一化的 situation 编号 (/max)
          [-1] n_options        : 归一化的选项数量 (/10)
        """
        from rl.obs_builder import _CHOOSE_SITUATION_MAP, _MAX_CHOOSE_SITUATIONS

        base = OBS_DIM - 3  # choose 观测的起始索引

        if self._choose_mode:
            raw_obs[base] = 1.0
            situation = self._choose_context.get("situation", "")
            raw_obs[base + 1] = _CHOOSE_SITUATION_MAP.get(situation, 0) / max(_MAX_CHOOSE_SITUATIONS, 1)
            raw_obs[base + 2] = len(self._choose_options) / 16.0
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
        try:
            # ── 游戏开始前：为 RL 玩家分配天赋 ──
            if self.enable_talents:
                self._assign_rl_talent()

            while not self._state.game_over and not self._game_over_flag:
                self._round_manager.run_one_round()

                # 检查胜利
                winner_id = self._state.check_victory()
                if winner_id:
                    self._state.game_over = True
                    self._state.winner = winner_id
                    break

                # 安全网：超过最大轮数
                if self._state.is_max_rounds_reached():
                    self._max_rounds_reached = True
                    self._state.game_over = True
                    self._state.winner = "nobody"
                    break
        except _GameAborted:
            pass
        except Exception as e:
            import sys, traceback
            sys.stderr.write(f"[ENV ERROR] _run_game crashed: {e}\n")
            traceback.print_exc(file=sys.stderr)
        finally:
            self._game_over_flag = True
            # 如果 env 线程正在等待 choose 同步，也需要唤醒
            self._choose_mode = False
            self._obs_event.set()  # 唤醒 env 线程

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
        self._current_player = None
        self._current_game_state = None
        # 重置 choose 模式状态
        self._choose_mode = False
        self._choose_options = []
        self._choose_context = {}
        self._pending_choose_idx = 0

    def close(self):
        self._cleanup()
        _restore_display()
        super().close()