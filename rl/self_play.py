"""
rl/self_play.py
───────────────
Self-play 对手控制器 + 对手池管理。
"""

from __future__ import annotations
import os
import random
from pathlib import Path
from typing import Any, Optional, List, Dict

import numpy as np
from sb3_contrib import MaskablePPO

from controllers.base import PlayerController
from rl.action_space import ACTION_COUNT, IDX_FORFEIT, build_action_mask, idx_to_command
from rl.obs_builder import OBS_DIM, build_obs
from rl.rl_controller import RLController


class OpponentRLController(RLController):
    """
    Self-play 对手控制器。

    与 _SyncRLController 不同，这个控制器不需要与 env 线程同步。
    它在游戏引擎后台线程中被调用时，直接用自己的模型做推理。

    继承 RLController 以复用 choose()/choose_multi()/confirm()/on_event() 的启发式逻辑。
    只需要重写 get_command() 来做模型推理。
    """

    def __init__(self, model_path: str | None = None, n_stack: int = 30,
                 *, _model: MaskablePPO | None = None):
        super().__init__()
        if _model is not None:
            self.model = _model
        elif model_path is not None:
            self.model = MaskablePPO.load(model_path)
        else:
            raise ValueError("Either model_path or _model must be provided")
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

    def get_command(
        self,
        player: Any,
        game_state: Any,
        available_actions: List[str],
        context: Optional[Dict] = None,
    ) -> str:
        # 记录 player_id（首次调用时）
        if self._player_id is None:
            self._player_id = player.player_id

        # 处理重试（和 _SyncRLController 一样的逻辑）
        attempt = (context or {}).get("attempt", 1)
        if attempt > 1:
            return "forfeit"

        # 构建观测
        raw_obs = build_obs(player, game_state)
        obs = self._stack_obs(raw_obs)

        # 构建 action mask
        mask = build_action_mask(player, game_state, player.player_id)

        # 模型推理（deterministic=True，对手用确定性策略）
        action, _ = self.model.predict(obs, action_masks=mask, deterministic=True)
        action = int(action)

        # 翻译为 CLI 命令
        return idx_to_command(action, player, game_state)
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
        self._model_cache: Dict[str, MaskablePPO] = {}  # 缓存已加载的模型

    from stable_baselines3.common.base_class import BaseAlgorithm
    def save_current_model(self, model: BaseAlgorithm, step: int):
        """保存当前模型到对手池。"""
        path = self.pool_dir / f"opponent_step_{step}"
        model.save(str(path))

        # 如果超出池大小，删除最旧的
        self._cleanup_old_models()

    def _cleanup_old_models(self):
        """保留最新的 max_pool_size 个模型。"""
        models = sorted(self.pool_dir.glob("opponent_step_*.zip"), key=lambda p: p.stat().st_mtime)
        while len(models) > self.max_pool_size:
            oldest = models.pop(0)
            # 从缓存中移除（在 unlink 之前计算 key）
            cache_key = str(oldest)
            if cache_key in self._model_cache:
                del self._model_cache[cache_key]
            oldest.unlink()

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

        # 从池中随机选一个模型
        model_path = random.choice(available)
        cache_key = str(model_path)

        # 使用缓存避免重复加载
        if cache_key not in self._model_cache:
            self._model_cache[cache_key] = MaskablePPO.load(str(model_path))

        # 创建 OpponentRLController（共享模型对象，不重复加载）
        ctrl = OpponentRLController(
            n_stack=self.n_stack,
            _model=self._model_cache[cache_key],
        )

        return ctrl

    def __getstate__(self):
        state = self.__dict__.copy()
        state['_model_cache'] = {}  # 不序列化模型缓存
        return state

