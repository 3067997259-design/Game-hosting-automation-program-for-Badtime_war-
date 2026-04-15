"""
rl/feature_extractor.py
───────────────────────
GRU 时序特征提取器。

将帧堆叠的扁平观测 (n_stack * OBS_DIM,) reshape 为 (n_stack, OBS_DIM)，
经过线性投影 + GRU 处理后输出最后一帧的 hidden state。

兼容 sb3-contrib MaskablePPO 的 policy_kwargs["features_extractor_class"]。
"""

import torch
import torch.nn as nn
import gymnasium as gym
from stable_baselines3.common.torch_layers import BaseFeaturesExtractor
from rl.obs_builder import OBS_DIM


class GRUFeatureExtractor(BaseFeaturesExtractor):
    """
    GRU-based feature extractor for frame-stacked observations.

    Parameters
    ----------
    observation_space : gym.spaces.Box
        Shape = (n_stack * OBS_DIM,)
    gru_hidden_size : int
        GRU hidden dimension (default 128)
    proj_size : int
        Linear projection size before GRU (default 128)
    num_layers : int
        Number of GRU layers (default 1)
    """

    def __init__(
        self,
        observation_space: gym.spaces.Box,
        gru_hidden_size: int = 192,
        proj_size: int = 256,
        num_layers: int = 1,
    ):
        # n_stack 从 observation_space 推断
        total_dim = observation_space.shape[0]
        assert total_dim % OBS_DIM == 0, (
            f"Observation dim {total_dim} is not a multiple of OBS_DIM={OBS_DIM}"
        )
        n_stack = total_dim // OBS_DIM

        # features_dim = GRU 输出维度，传给 BaseFeaturesExtractor
        super().__init__(observation_space, features_dim=gru_hidden_size)

        self.n_stack = n_stack
        self.obs_dim = OBS_DIM

        # 线性投影：OBS_DIM → proj_size
        self.projection = nn.Sequential(
            nn.Linear(OBS_DIM, proj_size),
            nn.ReLU(),
        )

        # GRU：按时间步处理投影后的帧
        self.gru = nn.GRU(
            input_size=proj_size,
            hidden_size=gru_hidden_size,
            num_layers=num_layers,
            batch_first=True,
        )

        # 可学习的初始隐状态（弥补没有跨 episode hidden state 的缺陷）
        self.h0 = nn.Parameter(
            torch.zeros(num_layers, 1, gru_hidden_size)
        )

    def forward(self, observations: torch.Tensor) -> torch.Tensor:
        batch_size = observations.shape[0]

        # (batch, n_stack * OBS_DIM) → (batch, n_stack, OBS_DIM)
        x = observations.view(batch_size, self.n_stack, self.obs_dim)

        # 线性投影每一帧：(batch, n_stack, OBS_DIM) → (batch, n_stack, proj_size)
        x = self.projection(x)

        # 扩展可学习初始隐状态到 batch 维度
        h0 = self.h0.expand(-1, batch_size, -1).contiguous()

        # GRU 前向：(batch, n_stack, proj_size) → (batch, n_stack, hidden_size)
        output, _ = self.gru(x, h0)

        # 取最后一帧的输出作为特征
        return output[:, -1, :]  # (batch, hidden_size)