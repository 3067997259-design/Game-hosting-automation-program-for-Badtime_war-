"""
rl/self_play.py
───────────────
Self-play 对手控制器 + 对手池管理。
"""

from __future__ import annotations
import logging
import os
import random
from pathlib import Path
from typing import Any, Optional, List, Dict, TYPE_CHECKING

import multiprocessing
import torch
import numpy as np
from sb3_contrib import MaskablePPO

from controllers.base import PlayerController
from rl.action_space import (
    ACTION_COUNT,
    IDX_FORFEIT,
    IDX_CHOOSE_BASE,
    build_action_mask,
    idx_to_choose_option,
    idx_to_command,
)
from rl.obs_builder import OBS_DIM, build_obs
from rl.rl_controller import RLController

if TYPE_CHECKING:
    from stable_baselines3.common.base_class import BaseAlgorithm

logger = logging.getLogger(__name__)


class OpponentRLController(RLController):
    """
    Self-play 对手控制器。

    与 _SyncRLController 不同，这个控制器不需要与 env 线程同步。
    它在游戏引擎后台线程中被调用时，直接用自己的模型做推理。

    继承 RLController 以复用 choose()/choose_multi()/confirm()/on_event() 的启发式逻辑。
    只需要重写 get_command() 来做模型推理。
    """

    def __init__(self, model_path: str | None = None, n_stack: int = 30,
                 *, _model: MaskablePPO | None = None,
                 _jit_model: "torch.jit.ScriptModule | None" = None):
        # 子进程中单样本推理（batch_size=1），多线程并行没有收益，
        # 反而会导致多个子进程之间的线程争抢，必须限制为单线程。
        # 仅在 SubprocVecEnv 子进程中生效，避免 DummyVecEnv（n_envs=1）
        # 时覆盖主进程的线程数设置。
        if multiprocessing.current_process().name != "MainProcess":
            torch.set_num_threads(1)
        super().__init__()
        self._jit_model = _jit_model
        if _jit_model is not None:
            # 走 TorchScript 快路径时不需要完整的 MaskablePPO 实例。
            self.model = None
        elif _model is not None:
            self.model = _model
        elif model_path is not None:
            self.model = MaskablePPO.load(model_path)
        else:
            raise ValueError("Either model_path, _model, or _jit_model must be provided")
        self.n_stack = n_stack
        self._obs_stack = np.zeros(OBS_DIM * n_stack, dtype=np.float32)
        self._player_id: Optional[str] = None

    def _stack_obs(self, raw_obs: np.ndarray) -> np.ndarray:
        if self.n_stack <= 1:
            return raw_obs
        self._obs_stack[:-OBS_DIM] = self._obs_stack[OBS_DIM:]
        self._obs_stack[-OBS_DIM:] = raw_obs
        return self._obs_stack.copy()

    def reset_stack(self):
        """每局开始时重置帧堆叠缓冲。"""
        self._obs_stack = np.zeros(OBS_DIM * self.n_stack, dtype=np.float32)

    def reset_game_state(self):
        """重置所有跨局会泄漏的状态（用于 stats_runner 等复用 controller 实例的场景）。

        除了 reset_stack()，还清理继承自 RLController 的 per-game 状态：
          - _event_log：否则跨局无限增长，造成内存泄漏
          - _threat_scores：否则上一局的威胁分会干扰启发式 target 选择
          - _been_attacked_by / _round_number
          - pending_action_idx / last_action_type / last_action_success
          - _player_ref / _state_ref / _player_id
        """
        self.reset_stack()
        self._event_log.clear()
        self._threat_scores.clear()
        self._been_attacked_by.clear()
        self._round_number = 0
        self.pending_action_idx = 0
        self.last_action_type = ""
        self.last_action_success = True
        self._player_ref = None
        self._state_ref = None
        self._player_id = None

    def get_command(
        self,
        player: Any,
        game_state: Any,
        available_actions: List[str],
        context: Optional[Dict] = None,
    ) -> str:
        # 缓存 player/state 引用，供 _rl_choose 使用
        self._cache_player_ref(player, game_state)

        # 记录 player_id（首次调用时）
        if self._player_id is None:
            self._player_id = player.player_id

        # 处理重试（和 _SyncRLController 一样的逻辑）
        attempt = (context or {}).get("attempt", 1)
        if attempt > 1:
            return "forfeit"

        # 构建观测
        raw_obs = build_obs(player, game_state, player.player_id)
        obs = self._stack_obs(raw_obs)

        # 构建 action mask
        mask = build_action_mask(player, game_state, player.player_id)

        # 模型推理（deterministic=True，对手用确定性策略）
        if self._jit_model is not None:
            action = self._fast_predict(obs, mask)
        else:
            action, _ = self.model.predict(obs, action_masks=mask, deterministic=True)
            action = int(action)

        # 翻译为 CLI 命令
        return idx_to_command(action, player, game_state)

    def _fast_predict(self, obs: np.ndarray, mask: np.ndarray) -> int:
        """使用 TorchScript 模型做快速推理（跳过 SB3 的 Python/numpy 包装开销）。

        等价于 ``MaskablePPO.predict(..., action_masks=mask, deterministic=True)``
        的输出：先算 logits，再把 mask 为 False 的位置置为 -inf，最后取 argmax。
        """
        with torch.no_grad():
            obs_tensor = torch.as_tensor(obs, dtype=torch.float32).unsqueeze(0)
            logits = self._jit_model(obs_tensor)  # (1, ACTION_COUNT)
            mask_tensor = torch.as_tensor(mask, dtype=torch.bool)
            logits[0, ~mask_tensor] = float("-inf")
            return int(logits.argmax(dim=1).item())

    def _rl_choose(
        self,
        prompt: str,
        options: List[str],
        context: Optional[Dict] = None,
    ) -> str:
        """用模型推理做 choose 决策（覆写基类的默认实现）。"""
        if not options:
            return ""
        if len(options) == 1:
            return options[0]

        player = self._player_ref
        state = self._state_ref
        if player is None or state is None:
            return options[0]

        situation = (context or {}).get("situation", "")

        # 构建观测
        raw_obs = build_obs(player, state, player.player_id)

        # 填充 choose 模式指示维（与训练 env._fill_choose_obs 保持一致）
        # 尾部 3 维：[current_mode=1.0, situation_id/30, n_options/16]
        from rl.obs_builder import _CHOOSE_SITUATION_MAP, _MAX_CHOOSE_SITUATIONS
        base = OBS_DIM - 3
        raw_obs[base] = 1.0
        raw_obs[base + 1] = _CHOOSE_SITUATION_MAP.get(situation, 0) / max(_MAX_CHOOSE_SITUATIONS, 1)
        raw_obs[base + 2] = min(len(options), 16) / 16.0

        obs = self._stack_obs(raw_obs)

        # 复用训练时的 mask 构造器：target-selection situation 会启用 108-113，
        # 其余通用 situation 启用 114-129，与训练端完全一致
        mask = build_action_mask(
            player, state, player.player_id,
            choose_mode=True,
            choose_situation=situation,
            choose_options=options,
        )

        # 模型推理
        if self._jit_model is not None:
            action = self._fast_predict(obs, mask)
        else:
            action, _ = self.model.predict(obs, action_masks=mask, deterministic=True)
            action = int(action)

        # 用 env 侧同款翻译逻辑把索引还原为选项字符串
        return idx_to_choose_option(action, options, situation, player, state)

    def _rl_confirm(
        self,
        prompt: str,
        context: Optional[Dict] = None,
    ) -> bool:
        """用模型推理做 confirm 决策（与训练 env._SyncRLController 一致）。

        复用 _rl_choose 处理 2 选项 ["是", "否"]，避免响应窗口 / 强买通行证
        等决策永远返回 False 而浪费模型学到的策略。
        """
        result = self._rl_choose(prompt, ["是", "否"], context)
        return result == "是"

    def set_player_ref(self, player, state):
        """手动设置 player 和 state 引用（用于 stats_runner 等非 env 场景）。"""
        self._player_ref = player
        self._state_ref = state
        if self._player_id is None:
            self._player_id = player.player_id


class OpponentPool:
    """
    对手模型池。

    管理历史 checkpoint 的存储、加载和采样。
    支持混入 BasicAI 以保证多样性。
    """

    def __init__(
        self,
        pool_dir: str,
        n_stack: int = 30,
        max_pool_size: int = 20,
        basic_ai_prob: float = 0.3,  # 30% 概率使用 BasicAI 而非历史模型
    ):
        self.pool_dir = Path(pool_dir)
        self.pool_dir.mkdir(parents=True, exist_ok=True)
        self.n_stack = n_stack
        self.max_pool_size = max_pool_size
        self.basic_ai_prob = basic_ai_prob
        # 模型缓存：可能同时包含 MaskablePPO 实例（由 .zip 加载）和
        # torch.jit.ScriptModule 实例（由 .pts 加载），用不同的
        # cache_key（完整文件路径，含 .zip / .pts 后缀）区分。
        self._model_cache: Dict[str, Any] = {}

    def save_current_model(self, model: BaseAlgorithm, step: int):
        """保存当前模型到对手池，同时导出 TorchScript 快推理版本。"""
        path = self.pool_dir / f"opponent_step_{step}"
        model.save(str(path))

        # 同时导出 TorchScript 版本用于快速推理（导出失败时回退到完整模型）
        try:
            from rl.export_torchscript import export_torchscript
            pts_path = self.pool_dir / f"opponent_step_{step}.pts"
            export_torchscript(model, str(pts_path), n_stack=self.n_stack)
        except Exception as e:
            logger.warning("TorchScript 导出失败，将回退到完整模型: %s", e)

        # 如果超出池大小，删除最旧的
        self._cleanup_old_models()

    def _cleanup_old_models(self):
        """保留最新的 max_pool_size 个模型（同时清理对应的 .pts）。"""
        models = sorted(self.pool_dir.glob("opponent_step_*.zip"), key=lambda p: p.stat().st_mtime)
        while len(models) > self.max_pool_size:
            oldest = models.pop(0)
            # 从缓存中移除（在 unlink 之前计算 key）
            cache_key = str(oldest)
            if cache_key in self._model_cache:
                del self._model_cache[cache_key]
            pts_file = oldest.with_suffix(".pts")
            pts_key = str(pts_file)
            if pts_key in self._model_cache:
                del self._model_cache[pts_key]
            oldest.unlink()
            if pts_file.exists():
                pts_file.unlink()

    def get_available_models(self) -> List[Path]:
        """返回池中所有可用的模型路径。"""
        return sorted(self.pool_dir.glob("opponent_step_*.zip"))

    def sample_opponent_controller(self) -> PlayerController:
        """
        从对手池中采样一个控制器。

        有 basic_ai_prob 的概率返回 BasicAI，
        否则从历史模型中随机选一个。
        如果池为空，总是返回 BasicAI。
        """
        from controllers.ai_basic import create_random_ai_controller

        available = self.get_available_models()

        # 池为空或随机选择 BasicAI
        if not available or random.random() < self.basic_ai_prob:
            return create_random_ai_controller(player_name="AI")

        # 清理缓存中已不存在于磁盘的模型（主进程可能已删除旧文件）
        # 同一个 checkpoint 可能有 .zip 和 .pts 两个 cache entry，都要照顧到
        available_set = {str(p) for p in available}
        available_set |= {str(p.with_suffix(".pts")) for p in available}
        stale_keys = [k for k in self._model_cache if k not in available_set]
        for k in stale_keys:
            del self._model_cache[k]

        # 从池中随机选一个模型（带重试，防止主进程并发删除导致 FileNotFoundError）
        random.shuffle(available)
        for model_path in available:
            cache_key = str(model_path)
            pts_path = model_path.with_suffix(".pts")
            pts_key = str(pts_path)
            try:
                # 优先尝试加载 TorchScript 版本（快路径）
                if pts_path.exists():
                    if pts_key not in self._model_cache:
                        jit_model = torch.jit.load(str(pts_path))
                        jit_model.eval()
                        self._model_cache[pts_key] = jit_model
                    ctrl = OpponentRLController(
                        n_stack=self.n_stack,
                        _jit_model=self._model_cache[pts_key],
                    )
                    return ctrl

                # 回退：加载完整 MaskablePPO
                if cache_key not in self._model_cache:
                    self._model_cache[cache_key] = MaskablePPO.load(str(model_path))
                ctrl = OpponentRLController(
                    n_stack=self.n_stack,
                    _model=self._model_cache[cache_key],
                )
                return ctrl
            except (FileNotFoundError, OSError) as e:
                # 主进程的 _cleanup_old_models 可能在 glob 和 load 之间删除了文件
                logger.warning("对手模型加载失败（可能已被清理）: %s — %s", model_path, e)
                self._model_cache.pop(cache_key, None)
                self._model_cache.pop(pts_key, None)
                continue

        # 所有模型都加载失败，回退到 BasicAI
        return create_random_ai_controller(player_name="AI")

    def __getstate__(self):
        state = self.__dict__.copy()
        state['_model_cache'] = {}  # 不序列化模型缓存
        return state

