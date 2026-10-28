"""
rl/maskable_recurrent/policies.py
─────────────────────────────────
MaskableRecurrentActorCriticPolicy —— 掩码 + 递归（LSTM）策略。

实现方式：以 sb3_contrib 的 RecurrentActorCriticPolicy 为基类做 delta 移植，
只替换与分布相关的构造/前向方法（掩码接触点），LSTM 架构、episode 重置
（_process_sequence）、值函数等逻辑全部继承不动。对应上游两个独立实现：
    - sb3_contrib.common.recurrent.policies.RecurrentActorCriticPolicy
    - sb3_contrib.common.maskable.policies.MaskableActorCriticPolicy

核心不变式：掩码只经 distribution.apply_masking() 注入，出现在
forward / evaluate_actions / get_distribution / _predict / predict 五处，
绝不进入 LSTM 状态。
"""

from __future__ import annotations

import logging
from functools import partial
from typing import Any, Optional, Union

import numpy as np
import torch as th
from gymnasium import spaces
from stable_baselines3.common.distributions import Distribution
from stable_baselines3.common.policies import ActorCriticPolicy
from stable_baselines3.common.torch_layers import (
    BaseFeaturesExtractor,
    CombinedExtractor,
    FlattenExtractor,
    NatureCNN,
)
from stable_baselines3.common.type_aliases import Schedule
from torch import nn

from sb3_contrib.common.maskable.distributions import make_masked_proba_distribution
from sb3_contrib.common.recurrent.policies import RecurrentActorCriticPolicy
from sb3_contrib.common.recurrent.type_aliases import RNNStates

logger = logging.getLogger(__name__)


def _apply_masking_safely(distribution: Distribution, action_masks: np.ndarray) -> None:
    """应用动作掩码，并清除 sb3-contrib 遗留的概率缓存。

    ``MaskableCategorical.apply_masking()`` 会用新 logits 重新初始化 PyTorch
    ``Categorical``，但旧 ``probs`` lazy cache 仍留在实例字典中。长训后 logits
    较尖锐时，PyTorch 会连旧 cache 一起做 Simplex 校验并可能因 float32 舍入
    误报；真实 logits 仍是有限值。清掉 cache 后，新初始化仍会校验 logits，
    随后由 sb3-contrib 按新掩码重建 probs，不会绕过 NaN/Inf 检查。
    """
    inner = getattr(distribution, "distribution", None)
    categoricals = inner if isinstance(inner, list) else [inner]
    for categorical in categoricals:
        if categorical is not None:
            categorical.__dict__.pop("probs", None)
    distribution.apply_masking(action_masks)


class MaskableRecurrentActorCriticPolicy(RecurrentActorCriticPolicy):
    """
    掩码递归 Actor-Critic 策略（PPO 等 on-policy 算法用）。

    参数与 RecurrentActorCriticPolicy 完全一致，额外支持动作掩码。
    动作空间支持 Discrete / MultiDiscrete；MultiBinary 刻意不支持——掩码
    action_net 维度与上游 masked Bernoulli（2n）不匹配、且无测试覆盖，已在
    buffer 层显式拒绝（诚实报错而非虚假声称）。
    """

    def __init__(
        self,
        observation_space: spaces.Space,
        action_space: spaces.Space,
        lr_schedule: Schedule,
        net_arch: Optional[Union[list[int], dict[str, list[int]]]] = None,
        activation_fn: type[nn.Module] = nn.Tanh,
        ortho_init: bool = True,
        use_sde: bool = False,
        log_std_init: float = 0.0,
        full_std: bool = True,
        use_expln: bool = False,
        squash_output: bool = False,
        features_extractor_class: type[BaseFeaturesExtractor] = FlattenExtractor,
        features_extractor_kwargs: Optional[dict[str, Any]] = None,
        share_features_extractor: bool = True,
        normalize_images: bool = True,
        optimizer_class: type[th.optim.Optimizer] = th.optim.Adam,
        optimizer_kwargs: Optional[dict[str, Any]] = None,
        lstm_hidden_size: int = 256,
        n_lstm_layers: int = 1,
        shared_lstm: bool = False,
        enable_critic_lstm: bool = True,
        lstm_kwargs: Optional[dict[str, Any]] = None,
    ):
        # 与 MaskableActorCriticPolicy 一致：Adam 小 eps 防 NaN。
        if optimizer_kwargs is None:
            optimizer_kwargs = {}
            if optimizer_class == th.optim.Adam:
                optimizer_kwargs["eps"] = 1e-5

        super().__init__(
            observation_space,
            action_space,
            lr_schedule,
            net_arch,
            activation_fn,
            ortho_init,
            use_sde,
            log_std_init,
            full_std,
            use_expln,
            squash_output,
            features_extractor_class,
            features_extractor_kwargs,
            share_features_extractor,
            normalize_images,
            optimizer_class,
            optimizer_kwargs,
            lstm_hidden_size,
            n_lstm_layers,
            shared_lstm,
            enable_critic_lstm,
            lstm_kwargs,
        )

        # 关键：ActorCriticPolicy.__init__ 末尾会把 self.action_dist 重置为
        # 普通分布（make_proba_distribution），因此掩码分布必须在
        # super().__init__() 返回之后重新覆盖。action_net 两侧都是
        # nn.Linear(latent_dim, action_dim)，构造完全一致，无兼容问题。
        self.action_dist = make_masked_proba_distribution(action_space)

    # ─────────────────────────────────────────────────────────────────────────
    #  网络构建：移植 MaskableActorCriticPolicy._build，
    #  只改 action_net 来源（掩码分布工厂），其余正交初始化/优化器逻辑一致。
    # ─────────────────────────────────────────────────────────────────────────

    def _build(self, lr_schedule: Schedule) -> None:
        """创建 mlp_extractor（输入为 lstm_output_dim，由父类覆盖）、
        action_net（掩码分布层）、value_net，并做正交初始化与优化器。"""
        self._build_mlp_extractor()

        self.action_net = self.action_dist.proba_distribution_net(
            latent_dim=self.mlp_extractor.latent_dim_pi
        )
        self.value_net = nn.Linear(self.mlp_extractor.latent_dim_vf, 1)

        if self.ortho_init:
            module_gains: dict[nn.Module, float] = {
                self.features_extractor: np.sqrt(2),
                self.mlp_extractor: np.sqrt(2),
                self.action_net: 0.01,
                self.value_net: 1,
            }
            if not self.share_features_extractor:
                del module_gains[self.features_extractor]
                module_gains[self.pi_features_extractor] = np.sqrt(2)
                module_gains[self.vf_features_extractor] = np.sqrt(2)

            for module, gain in module_gains.items():
                module.apply(partial(self.init_weights, gain=gain))

        # 注意：RecurrentActorCriticPolicy.__init__ 在 super().__init__() 返回后
        # 会重建一次 optimizer（把 LSTM 参数纳入），这里的 optimizer 会被替换，
        # 因此不会出现"遗漏 LSTM 参数"的问题。
        self.optimizer = self.optimizer_class(
            self.parameters(),
            lr=lr_schedule(1),
            **self.optimizer_kwargs,
        )

    # ─────────────────────────────────────────────────────────────────────────
    #  掩码接触点
    # ─────────────────────────────────────────────────────────────────────────

    def _get_action_dist_from_latent(self, latent_pi: th.Tensor) -> Distribution:
        """从 actor latent 构建掩码分布（返回 MaskableDistribution）。"""
        action_logits = self.action_net(latent_pi)
        return self.action_dist.proba_distribution(action_logits=action_logits)

    def forward(
        self,
        obs: th.Tensor,
        lstm_states: RNNStates,
        episode_starts: th.Tensor,
        deterministic: bool = False,
        action_masks: Optional[np.ndarray] = None,
    ) -> tuple[th.Tensor, th.Tensor, th.Tensor, RNNStates]:
        """前向：观测 → features → LSTM(actor/critic) → 掩码分布 → 动作。

        与 RecurrentActorCriticPolicy.forward 唯一差异：采样前 apply_masking。
        """
        features = self.extract_features(obs)
        if self.share_features_extractor:
            pi_features = vf_features = features
        else:
            pi_features, vf_features = features

        latent_pi, lstm_states_pi = self._process_sequence(
            pi_features, lstm_states.pi, episode_starts, self.lstm_actor
        )
        if self.lstm_critic is not None:
            latent_vf, lstm_states_vf = self._process_sequence(
                vf_features, lstm_states.vf, episode_starts, self.lstm_critic
            )
        elif self.shared_lstm:
            latent_vf = latent_pi.detach()
            lstm_states_vf = (lstm_states_pi[0].detach(), lstm_states_pi[1].detach())
        else:
            latent_vf = self.critic(vf_features)
            lstm_states_vf = lstm_states_pi

        latent_pi = self.mlp_extractor.forward_actor(latent_pi)
        latent_vf = self.mlp_extractor.forward_critic(latent_vf)

        values = self.value_net(latent_vf)
        distribution = self._get_action_dist_from_latent(latent_pi)
        if action_masks is not None:
            _apply_masking_safely(distribution, action_masks)
        actions = distribution.get_actions(deterministic=deterministic)
        log_prob = distribution.log_prob(actions)
        return actions, values, log_prob, RNNStates(lstm_states_pi, lstm_states_vf)

    def get_distribution(
        self,
        obs: th.Tensor,
        lstm_states: tuple[th.Tensor, th.Tensor],
        episode_starts: th.Tensor,
        action_masks: Optional[np.ndarray] = None,
    ) -> tuple[Distribution, tuple[th.Tensor, ...]]:
        """获取当前策略分布（掩码注入点）。"""
        features = super(ActorCriticPolicy, self).extract_features(obs, self.pi_features_extractor)
        latent_pi, lstm_states = self._process_sequence(
            features, lstm_states, episode_starts, self.lstm_actor
        )
        latent_pi = self.mlp_extractor.forward_actor(latent_pi)
        distribution = self._get_action_dist_from_latent(latent_pi)
        if action_masks is not None:
            _apply_masking_safely(distribution, action_masks)
        return distribution, lstm_states

    def evaluate_actions(
        self,
        obs: th.Tensor,
        actions: th.Tensor,
        lstm_states: RNNStates,
        episode_starts: th.Tensor,
        action_masks: Optional[np.ndarray] = None,
    ) -> tuple[th.Tensor, th.Tensor, th.Tensor]:
        """评估动作：返回 (value, log_prob, entropy)，掩码与收集时一致。"""
        features = self.extract_features(obs)
        if self.share_features_extractor:
            pi_features = vf_features = features
        else:
            pi_features, vf_features = features

        latent_pi, _ = self._process_sequence(
            pi_features, lstm_states.pi, episode_starts, self.lstm_actor
        )
        if self.lstm_critic is not None:
            latent_vf, _ = self._process_sequence(
                vf_features, lstm_states.vf, episode_starts, self.lstm_critic
            )
        elif self.shared_lstm:
            latent_vf = latent_pi.detach()
        else:
            latent_vf = self.critic(vf_features)

        latent_pi = self.mlp_extractor.forward_actor(latent_pi)
        latent_vf = self.mlp_extractor.forward_critic(latent_vf)

        distribution = self._get_action_dist_from_latent(latent_pi)
        if action_masks is not None:
            _apply_masking_safely(distribution, action_masks)
        log_prob = distribution.log_prob(actions)
        values = self.value_net(latent_vf)
        return values, log_prob, distribution.entropy()

    def _predict(
        self,
        observation: th.Tensor,
        lstm_states: tuple[th.Tensor, th.Tensor],
        episode_starts: th.Tensor,
        deterministic: bool = False,
        action_masks: Optional[np.ndarray] = None,
    ) -> tuple[th.Tensor, tuple[th.Tensor, ...]]:
        """单观测推理（掩码注入点）。"""
        distribution, lstm_states = self.get_distribution(
            observation, lstm_states, episode_starts, action_masks
        )
        return distribution.get_actions(deterministic=deterministic), lstm_states

    def predict(
        self,
        observation: Union[np.ndarray, dict[str, np.ndarray]],
        state: Optional[tuple[np.ndarray, ...]] = None,
        episode_start: Optional[np.ndarray] = None,
        deterministic: bool = False,
        action_masks: Optional[np.ndarray] = None,
    ) -> tuple[np.ndarray, Optional[tuple[np.ndarray, ...]]]:
        """numpy 推理 API：观测 + 可选 (h,c) 状态 + 掩码 → (动作, 新状态)。"""
        self.set_training_mode(False)

        if isinstance(observation, tuple) and len(observation) == 2 and isinstance(observation[1], dict):
            raise ValueError(
                "You have passed a tuple to the predict() function instead of a Numpy array or a Dict. "
                "You are probably mixing Gym API with SB3 VecEnv API: `obs, info = env.reset()` (Gym) "
                "vs `obs = vec_env.reset()` (SB3 VecEnv)."
            )

        observation, vectorized_env = self.obs_to_tensor(observation)

        if isinstance(observation, dict):
            n_envs = observation[next(iter(observation.keys()))].shape[0]
        else:
            n_envs = observation.shape[0]
        if state is None:
            # (n_layers, n_envs, hidden_size) 的 (h, c) 零初始化
            state = np.concatenate([np.zeros(self.lstm_hidden_state_shape) for _ in range(n_envs)], axis=1)
            state = (state, state)

        if episode_start is None:
            episode_start = np.array([False for _ in range(n_envs)])

        with th.no_grad():
            states = th.tensor(state[0], dtype=th.float32, device=self.device), th.tensor(
                state[1], dtype=th.float32, device=self.device
            )
            episode_starts = th.tensor(episode_start, dtype=th.float32, device=self.device)
            actions, states = self._predict(
                observation,
                lstm_states=states,
                episode_starts=episode_starts,
                deterministic=deterministic,
                action_masks=action_masks,
            )
            states = (states[0].cpu().numpy(), states[1].cpu().numpy())

        actions = actions.cpu().numpy()

        if isinstance(self.action_space, spaces.Box):
            if self.squash_output:
                actions = self.unscale_action(actions)
            else:
                actions = np.clip(actions, self.action_space.low, self.action_space.high)

        if not vectorized_env:
            actions = actions.squeeze(axis=0)

        return actions, states

    # —— 与基类 RecurrentActorCriticPolicy 一致，无需改动 ——
    predict_values = RecurrentActorCriticPolicy.predict_values

    def _process_sequence(
        self,
        features: th.Tensor,
        lstm_states: tuple[th.Tensor, th.Tensor],
        episode_starts: th.Tensor,
        lstm: nn.LSTM,
    ) -> tuple[th.Tensor, tuple[th.Tensor, ...]]:
        """快路径化：训练/采集中 LSTM 序列的 episode 重置只在序列首部。

        get() 的 create_sequencers 按 episode_starts/env_change 切序，保证重置位
        只会出现在每个序列的第 0 位。因此可先把序列首部待重置位置的初始 state
        置零（等价于慢路径的第 0 步重置），再把 episode_starts 清 0 → 单次 LSTM
        前向（torch/cuDNN 融合 kernel），免去 Python 逐时间步循环的碎 kernel 开销。

        若未来上游出现"序列中途重置"（episode_starts[1:] 非 0），回退到上游原逻辑，
        保证正确性永不劣化。数值等价性由 tests/test_maskable_recurrent_lib.py
        的等价性测试守门（与官方 RecurrentPPO 逐数字一致）。
        """
        n_seq = lstm_states[0].shape[1]
        features_sequence = features.reshape((n_seq, -1, lstm.input_size)).swapaxes(0, 1)
        episode_starts = episode_starts.reshape((n_seq, -1)).swapaxes(0, 1)

        if th.all(episode_starts == 0.0):
            lstm_output, new_states = lstm(features_sequence, lstm_states)
            return th.flatten(lstm_output.transpose(0, 1), start_dim=0, end_dim=1), new_states

        # 中途存在重置 → 只能用慢路径（正确地回退到上游原逻辑）
        if not th.all(episode_starts[1:] == 0.0):
            return super()._process_sequence(features, lstm_states, episode_starts.swapaxes(0, 1).reshape(-1), lstm)

        # 快路径化：序列首部按 episode_starts[0] 重置初始状态，再当全 0 前向
        try:
            mask = (1.0 - episode_starts[0]).view(1, n_seq, 1)
            init_states = (mask * lstm_states[0], mask * lstm_states[1])
            lstm_output, new_states = lstm(features_sequence, init_states)
            return (th.flatten(lstm_output.transpose(0, 1), start_dim=0, end_dim=1),
                    new_states)
        except Exception as exc:  # noqa: BLE001
            # 弹回守卫：fast-path 任何异常（含未预料到的 batch 形状边缘）→ 打日志 + 回退
            # 上游慢路径，正确性永不劣化（日志即根因线索）。
            logger.warning("[fast-path] 异常回退上游慢路径：%r", exc, exc_info=True)
            return super()._process_sequence(
                features, lstm_states, episode_starts.swapaxes(0, 1).reshape(-1), lstm
            )


class MlpLstmPolicy(MaskableRecurrentActorCriticPolicy):
    """MLP 输入 + LSTM：与 sb3_contrib.ppo_recurrent.policies.MlpLstmPolicy 对齐。"""

    def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        super().__init__(*args, **kwargs, features_extractor_class=FlattenExtractor)


class CnnLstmPolicy(MaskableRecurrentActorCriticPolicy):
    """CNN 输入 + LSTM：图像类观测。"""

    def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        super().__init__(*args, **kwargs, features_extractor_class=NatureCNN)


class MultiInputLstmPolicy(MaskableRecurrentActorCriticPolicy):
    """多模态 Dict 输入 + LSTM。

    ⚠️ 未导出、不进 policy_aliases：Dict 观测需要对应的递归 buffer/采样扩展
    （当前 buffer 对 Dict 显式 raise NotImplementedError）。保留类定义仅为
    对照上游结构；在支持 Dict 之前请勿公开宣称可用。
    """

    def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        super().__init__(*args, **kwargs, features_extractor_class=CombinedExtractor)
