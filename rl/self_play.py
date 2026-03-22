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

    def __init__(self, model_path: str, n_stack: int = 30):
        super().__init__()
        self.model = MaskablePPO.load(model_path)
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