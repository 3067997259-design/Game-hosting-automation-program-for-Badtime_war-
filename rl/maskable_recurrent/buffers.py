"""
rl/maskable_recurrent/buffers.py
─────────────────────────────────
MaskableRecurrentRolloutBuffer —— 递归回滚缓冲 + 每步动作掩码存储。

在 sb3_contrib RecurrentRolloutBuffer 的 episode 切序/pad 机制之上，
并行保存每个 transition 的动作掩码（与动作同刻采集），并在 get() 时
按同一 seq_start/seq_end 切序 pad，保证训练期 evaluate_actions 使用的
掩码与采集时完全一致（"用当时能取的 action 的分布下的 log_prob"）。

padding 行：掩码 pad 值为 0（全 False）；该行 logits 全被置 -1e8，
但 padding 行的损失/熵被父类自带的 padding mask 字段排除，不会污染。
"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from typing import Optional, Union

import numpy as np
import torch as th
from gymnasium import spaces
from stable_baselines3.common.vec_env import VecNormalize

from sb3_contrib.common.recurrent.buffers import (
    RecurrentRolloutBuffer,
    create_sequencers,
    pad,
    pad_and_flatten,
)
from sb3_contrib.common.recurrent.type_aliases import RNNStates


def _mask_dims_for(action_space: spaces.Space) -> int:
    """动作掩码的最后一维：Discrete → n；MultiDiscrete → Σnvec。

    注意：MultiBinary 刻意**不支持**——上游 masked 分布把 MultiBinary 映射为
    Bernoulli(MultiCategorical [2,…,2])，掩码/ logits 维度是 2n 而非 n，而
    本实现为 Discrete/MultiDiscrete 构建 action_net。要为 MultiBinary 提供支持，
    需要单独验证 action_net 维度与掩码宽度，暂无测试覆盖，故显式拒绝而非虚假声称。
    """
    if isinstance(action_space, spaces.Discrete):
        return int(action_space.n)
    if isinstance(action_space, spaces.MultiDiscrete):
        return int(sum(action_space.nvec))
    raise NotImplementedError(
        f"MaskableRecurrentRolloutBuffer 支持 Discrete/MultiDiscrete（MultiBinary 未实现，"
        f"见函数 docstring）：{type(action_space).__name__}"
    )


@dataclass
class MaskableRecurrentRolloutBufferSamples:
    """训练 minibatch 样本：在 RecurrentRolloutBufferSamples 基础上增加 action_masks。

    不复用上游 dataclass 做继承，避免字段默认值排序的隐性问题。
    """
    observations: th.Tensor
    actions: th.Tensor
    old_values: th.Tensor
    old_log_prob: th.Tensor
    advantages: th.Tensor
    returns: th.Tensor
    lstm_states: RNNStates
    episode_starts: th.Tensor
    mask: th.Tensor
    action_masks: th.Tensor


class MaskableRecurrentRolloutBuffer(RecurrentRolloutBuffer):
    """递归回滚缓冲 + 每步动作掩码（float32，(n_steps, n_envs, mask_dims)）。"""

    def __init__(
        self,
        buffer_size: int,
        observation_space: spaces.Space,
        action_space: spaces.Space,
        hidden_state_shape: tuple[int, int, int, int],
        device: Union[th.device, str] = "auto",
        gae_lambda: float = 1,
        gamma: float = 0.99,
        n_envs: int = 1,
    ):
        self.mask_dims = _mask_dims_for(action_space)
        super().__init__(
            buffer_size,
            observation_space,
            action_space,
            hidden_state_shape,
            device,
            gae_lambda,
            gamma,
            n_envs,
        )

    def reset(self) -> None:
        super().reset()
        self.action_masks = np.ones(
            (self.buffer_size, self.n_envs, self.mask_dims), dtype=np.float32
        )

    def add(self, *args, lstm_states: RNNStates, action_masks=None, **kwargs) -> None:  # noqa: ANN001
        """存储本步动作掩码，再交给父类写入 LSTM 状态与常规字段（状态由父类负责，避免双写）。"""
        if action_masks is not None:
            self.action_masks[self.pos] = action_masks.reshape((self.n_envs, self.mask_dims))

        super().add(*args, lstm_states=lstm_states, **kwargs)

    def get(self, batch_size: Optional[int] = None) -> Generator[MaskableRecurrentRolloutBufferSamples, None, None]:
        """与父类 get() 相同，追加 action_masks 的等序展平与 pad 化切分。"""
        assert self.full, "Rollout buffer must be full before sampling from it"

        if not self.generator_ready:
            for tensor in ["hidden_states_pi", "cell_states_pi", "hidden_states_vf", "cell_states_vf"]:
                self.__dict__[tensor] = self.__dict__[tensor].swapaxes(1, 2)

            for tensor in [
                "observations",
                "actions",
                "values",
                "log_probs",
                "advantages",
                "returns",
                "hidden_states_pi",
                "cell_states_pi",
                "hidden_states_vf",
                "cell_states_vf",
                "episode_starts",
                "action_masks",
            ]:
                self.__dict__[tensor] = self.swap_and_flatten(self.__dict__[tensor])
            self.generator_ready = True

        if batch_size is None:
            batch_size = self.buffer_size * self.n_envs

        # 保持序列顺序的轮转切分（与父类一致）
        split_index = np.random.randint(self.buffer_size * self.n_envs)
        indices = np.arange(self.buffer_size * self.n_envs)
        indices = np.concatenate((indices[split_index:], indices[:split_index]))

        env_change = np.zeros(self.buffer_size * self.n_envs).reshape(self.buffer_size, self.n_envs)
        env_change[0, :] = 1.0
        env_change = self.swap_and_flatten(env_change)

        start_idx = 0
        while start_idx < self.buffer_size * self.n_envs:
            batch_inds = indices[start_idx : start_idx + batch_size]
            yield self._get_samples(batch_inds, env_change)
            start_idx += batch_size

    def _get_samples(
        self,
        batch_inds: np.ndarray,
        env_change: np.ndarray,
        env: Optional[VecNormalize] = None,
    ) -> MaskableRecurrentRolloutBufferSamples:
        """与父类 _get_samples 相同，追加 padded action_masks。"""
        self.seq_start_indices, self.pad, self.pad_and_flatten = create_sequencers(
            self.episode_starts[batch_inds], env_change[batch_inds], self.device
        )

        n_seq = len(self.seq_start_indices)
        max_length = self.pad(self.actions[batch_inds]).shape[1]
        padded_batch_size = n_seq * max_length

        lstm_states_pi = (
            self.hidden_states_pi[batch_inds][self.seq_start_indices].swapaxes(0, 1),
            self.cell_states_pi[batch_inds][self.seq_start_indices].swapaxes(0, 1),
        )
        lstm_states_vf = (
            self.hidden_states_vf[batch_inds][self.seq_start_indices].swapaxes(0, 1),
            self.cell_states_vf[batch_inds][self.seq_start_indices].swapaxes(0, 1),
        )
        lstm_states_pi = (self.to_torch(lstm_states_pi[0]).contiguous(), self.to_torch(lstm_states_pi[1]).contiguous())
        lstm_states_vf = (self.to_torch(lstm_states_vf[0]).contiguous(), self.to_torch(lstm_states_vf[1]).contiguous())

        return MaskableRecurrentRolloutBufferSamples(
            observations=self.pad(self.observations[batch_inds]).reshape((padded_batch_size, *self.obs_shape)),
            actions=self.pad(self.actions[batch_inds]).reshape((padded_batch_size, *self.actions.shape[1:])),
            old_values=self.pad_and_flatten(self.values[batch_inds]),
            old_log_prob=self.pad_and_flatten(self.log_probs[batch_inds]),
            advantages=self.pad_and_flatten(self.advantages[batch_inds]),
            returns=self.pad_and_flatten(self.returns[batch_inds]),
            lstm_states=RNNStates(lstm_states_pi, lstm_states_vf),
            episode_starts=self.pad_and_flatten(self.episode_starts[batch_inds]),
            mask=self.pad_and_flatten(np.ones_like(self.returns[batch_inds])),
            action_masks=self.pad(self.action_masks[batch_inds]).reshape(
                (padded_batch_size, self.mask_dims)
            ),
        )

    # 供算法类引用（与 sb3_contrib 的命名保持一致）
    buffer_samples_cls = MaskableRecurrentRolloutBufferSamples