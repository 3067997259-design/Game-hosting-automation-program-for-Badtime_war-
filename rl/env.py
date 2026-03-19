"""  
rl/env.py  
─────────  
BadtimeWarEnv —— 主 Gym 封装  
  
单智能体环境，RL 控制一名玩家，其余由 BasicAIController 控制。  
游戏引擎在后台线程运行，通过 threading.Event 与 env 同步。  
  
兼容 sb3-contrib MaskablePPO：  
  - action_masks() 方法返回 bool 数组  
  - info dict 中也包含 "action_masks"  
"""  
  
from __future__ import annotations  
import threading  
from typing import Any, Optional
  
import gymnasium as gym  
import numpy as np  
from gymnasium import spaces  

from rl.action_space import (  
    ACTION_COUNT, IDX_FORFEIT, build_action_mask, idx_to_command,  
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
    # 有返回值的函数需要特殊处理  
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
#  同步版 RLController：在 get_command 中阻塞等待 env 提供动作  
# ─────────────────────────────────────────────────────────────────────────────  
  
class _SyncRLController(RLController):  
    """  
    扩展 RLController，在 get_command() 中与 env 线程同步。  
  
    流程：  
      1. 引擎调用 get_command()  
      2. 控制器信号 env（_obs_event）并阻塞（_action_event）  
      3. env.step() 设置 pending_action_idx 并信号控制器  
      4. 控制器将 idx 翻译为 CLI 命令返回给引擎  
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
  
        # 保存引用供 env 读取  
        self._env._current_player = player  
        self._env._current_game_state = game_state  
  
        # 通知 env：准备好接收动作  
        self._env._obs_event.set()  
  
        # 等待 env 提供动作  
        self._env._action_event.wait()  
        self._env._action_event.clear()  
  
        if self._env._game_over_flag:  
            raise _GameAborted()  
  
        return idx_to_command(self.pending_action_idx, player, game_state)  
  
  
# ─────────────────────────────────────────────────────────────────────────────  
#  主环境  
# ─────────────────────────────────────────────────────────────────────────────  
  
class BadtimeWarEnv(gym.Env):  
    """  
    Badtime War 单智能体 Gym 环境。  
  
    参数  
    ----  
    num_opponents : 对手数量（1-5），默认 3  
    max_rounds    : 最大轮数，超过则 truncated=True，默认 50  
    render_mode   : "human" 时保留游戏输出，否则静默  
    """  
  
    metadata = {"render_modes": ["human"]}  
  
    def __init__(  
        self,  
        num_opponents: int = 3,  
        max_rounds: int = 50,  
        render_mode: Optional[str] = None,
        n_stack: int = 1, 
    ):  
        super().__init__()  
  
        self.num_opponents = num_opponents  
        self.max_rounds = max_rounds  
        self.render_mode = render_mode
        self.n_stack = n_stack 
  
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

    def _stack_obs(self, raw_obs: np.ndarray) -> np.ndarray:  
        """将新观测推入帧堆叠缓冲，返回拼接后的完整观测。"""  
        if self.n_stack <= 1:  
            return raw_obs  
        # 左移旧帧，新帧放最右  
        self._obs_stack[:-OBS_DIM] = self._obs_stack[OBS_DIM:]  
        self._obs_stack[-OBS_DIM:] = raw_obs  
        return self._obs_stack.copy()
  
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
        self._state.add_player(self._rl_player)  
  
        # 创建 AI 对手  
        for i in range(self.num_opponents):  
            ai_ctrl = create_random_ai_controller(player_name=f"AI_{i}")  
            p = Player(f"ai_{i}", f"AI_{i}", ai_ctrl)  
            self._state.add_player(p)  
  
        # 创建轮次管理器  
        self._round_manager = RoundManager(self._state)  
  
        # 创建奖励追踪器  
        self._reward_tracker = RewardTracker("rl_0")  
  
        # 重置同步原语  
        self._obs_event.clear()  
        self._action_event.clear()  
        self._game_over_flag = False
        self._max_rounds_reached = False
  
        # 后台线程启动游戏  
        self._game_thread = threading.Thread(target=self._run_game, daemon=True)  
        self._game_thread.start()  
  
        # 等待第一个 RL 回合（或游戏提前结束）  
        self._obs_event.wait()  
        self._obs_event.clear()  
  
        # 初始化奖励追踪器基线
        assert self._rl_player is not None  
        assert self._state is not None  
        assert self._reward_tracker is not None
        self._reward_tracker.reset(self._rl_player, self._state)  
  
        self._obs_stack = np.zeros(OBS_DIM * self.n_stack, dtype=np.float32)  # 重置缓冲  
        raw_obs = build_obs(self._rl_player, self._state)  
        obs = self._stack_obs(raw_obs) 
        info = {"action_masks": self.action_masks()}  
  
        return obs, info  
  
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
            raw_obs = build_obs(self._rl_player, self._state)  
            obs = self._stack_obs(raw_obs)
            reward = self._reward_tracker.compute(  
                self._rl_player, self._state, "forfeit", False  
            )  
            info: dict[str, Any] = {"action_masks": self.action_masks()}  
            if self._state:  
                info["winner"] = getattr(self._state, "winner", None)  
            return obs, reward, True, False, info
    
        # 将动作传递给控制器  
        self._rl_controller.pending_action_idx = action  
    
        # 唤醒游戏线程  
        self._action_event.set()  
    
        # 等待下一个 RL 回合或游戏结束  
        self._obs_event.wait()  
        self._obs_event.clear()  
    
        # 读取上一次动作结果  
        action_type = getattr(self._rl_player, "last_action_type", None) or "forfeit"  
        action_success = not (action != IDX_FORFEIT and action_type == "forfeit")  
    
        # ── 判定终止 / 截断（修复竞态条件）──  
        # 后台线程可能已经因 max_rounds 设置了 game_over=True，  
        # 用 _max_rounds_reached 标志区分"胜利终止"和"轮数截断"  
        truncated = bool(self._max_rounds_reached)  
        if not truncated and not self._state.game_over:  
            # env 侧也检查一次（防御性）  
            if self._state.current_round >= self.max_rounds:  
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
            self._rl_player, self._state, action_type, action_success  
        )  
    
        raw_obs = build_obs(self._rl_player, self._state)  
        obs = self._stack_obs(raw_obs)
        info = {"action_masks": self.action_masks()}  
    
        # ★ 传递实际胜者给 WinRateCallback  
        if terminated or truncated:  
            info["winner"] = self._state.winner  
    
        return obs, reward, terminated, truncated, info
  
    # ══════════════════════════════════════════════════════════════════════════  
    #  action_masks（MaskablePPO 接口）  
    # ══════════════════════════════════════════════════════════════════════════  
  
    def action_masks(self) -> np.ndarray:  
        """返回当前合法动作的 bool 掩码，shape=(108,)"""  
        if (  
            self._state is None  
            or self._state.game_over  
            or self._rl_player is None  
            or not self._rl_player.is_alive()  
        ):  
            mask = np.zeros(ACTION_COUNT, dtype=bool)  
            mask[IDX_FORFEIT] = True  # 至少保留一个合法动作  
            return mask  
        return build_action_mask(self._rl_player, self._state, "rl_0")  
  
    # ══════════════════════════════════════════════════════════════════════════  
    #  后台游戏线程  
    # ══════════════════════════════════════════════════════════════════════════  

    def _run_game(self):  
        """在后台线程中运行游戏主循环。"""  
        assert self._state is not None  
        assert self._round_manager is not None
        try:  
            while not self._state.game_over and not self._game_over_flag:  
                self._round_manager.run_one_round()  
  
                # 检查胜利  
                winner_id = self._state.check_victory()  
                if winner_id:  
                    self._state.game_over = True  
                    self._state.winner = winner_id  
                    break  
  
                # 安全网：超过最大轮数  
                if self._state.current_round >= self.max_rounds:  
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
  
    def close(self):  
        self._cleanup()  
        _restore_display()  
        super().close()
