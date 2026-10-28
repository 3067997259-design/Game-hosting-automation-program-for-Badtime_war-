"""
rl/obs_compat.py
────────────────
旧 checkpoint 的观测宽度兼容包装（P2）。

背景：`rl/obs_builder_m9` 的天赋块只在 **绝对尾部** 追加（append-only），
v5 核心宽度 828（OBS_DIM_M9_V5_CORE）之前的布局冻结不变。因此旧
checkpoint（obs=828）可以通过"尾部截断"继续在新环境上服役——截断丢弃的
只有新增通道，旧语义完好。

用法（专家栈评估，n_stack 必须为 1）：

    from rl.obs_builder_m9 import OBS_DIM_M9_V5_CORE
    from rl.obs_compat import LegacyObsDimWrapper

    env = LegacyObsDimWrapper(BadtimeWarEnv(..., n_stack=1),
                              OBS_DIM_M9_V5_CORE)
    # validate_model_against_env(old_model, env) 现在按 828 通过

不做的事：零填充升维（旧模型没有新增通道的输入权重，填充无意义）；
n_stack>1（堆叠观测中天赋块逐帧交错，前缀截断不再等价，显式拒绝）。
"""

from __future__ import annotations

from typing import Any

import gymnasium as gym
import numpy as np
from gymnasium import spaces


class LegacyObsDimWrapper(gym.Wrapper):
    """把 env 观测尾部截断到 ``target_dim``，旧 checkpoint 兼容层。

    仅支持一维 Box 观测且 n_stack=1；动作空间原样透传（M9 动作布局未变）。
    """

    def __init__(self, env: gym.Env, target_dim: int) -> None:
        super().__init__(env)
        inner_shape = tuple(getattr(env.observation_space, "shape", ()) or ())
        if len(inner_shape) != 1:
            raise ValueError(
                f"LegacyObsDimWrapper 仅支持一维 Box 观测，实际 shape={inner_shape}"
                "（n_stack>1 的堆叠观测请改用 n_stack=1 的递归策略口径）",
            )
        if inner_shape[0] < int(target_dim):
            raise ValueError(
                f"目标宽度 {target_dim} 大于环境观测宽度 {inner_shape[0]}"
                "——这不是尾部截断场景，请检查 target_dim",
            )
        self._target_dim = int(target_dim)
        self.observation_space = spaces.Box(
            low=np.asarray(env.observation_space.low[:self._target_dim]),
            high=np.asarray(env.observation_space.high[:self._target_dim]),
            dtype=env.observation_space.dtype,
        )

    def _cut(self, obs: np.ndarray) -> np.ndarray:
        return np.asarray(obs, dtype=np.float32)[: self._target_dim]

    def reset(self, **kwargs: Any):
        obs, info = self.env.reset(**kwargs)
        return self._cut(obs), info

    def step(self, action):
        obs, reward, terminated, truncated, info = self.env.step(action)
        return self._cut(obs), reward, terminated, truncated, info
