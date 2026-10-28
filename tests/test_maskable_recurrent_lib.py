"""maskable_recurrent 库的发布级回归门禁（pytest）。

覆盖：
- 等价性：全 True 掩码 + 同 seed/超参下与官方 RecurrentPPO 前向逐数字一致；
- 掩码服从：训练后采样的动作 100% 落在合法集；
- 稳定性：短训练无 NaN，缓冲内掩码自洽；
- 支持面：MultiDiscrete 可跑；MultiBinary 显式拒绝；sb3-contrib 版本 pin。

玩具环境（Box obs + Discrete / MultiDiscrete + action_masks 同时经 env 方法与
info dict 暴露——验证 collect_rollouts 从 infos 读掩码的路径）。
"""

from __future__ import annotations

import numpy as np
import gymnasium as gym
from gymnasium import spaces
import torch as th
import pytest

from stable_baselines3.common.vec_env import DummyVecEnv
from sb3_contrib import RecurrentPPO
from sb3_contrib.ppo_recurrent.policies import MlpLstmPolicy as UpstreamLstm

from rl.maskable_recurrent import MaskableRecurrentPPO
from rl.maskable_recurrent.buffers import _mask_dims_for
from rl.maskable_recurrent.policies import MlpLstmPolicy
from rl.maskable_recurrent.policies import _apply_masking_safely


def small_policy_kwargs() -> dict:
    return dict(lstm_hidden_size=32, enable_critic_lstm=True,
                net_arch=dict(pi=[32], vf=[32]))


class CorridorEnv(gym.Env):
    """5 位置走廊：动作 0=左移/1=右移/2=原地/3=探测(仅中心)/4=爆发(偶数位禁)。"""

    L = 5
    MAX = 30

    def __init__(self, seed: int = 0):
        super().__init__()
        self.observation_space = spaces.Box(-1, 1, (7,), np.float32)
        self.action_space = spaces.Discrete(5)
        self._seed = seed
        self._pos = 0
        self._steps = 0

    def action_masks(self) -> np.ndarray:
        m = np.ones(5, dtype=bool)
        if self._pos == 0:
            m[0] = False
        if self._pos == self.L - 1:
            m[1] = False
        m[3] = self._pos == 2
        if self._pos % 2 == 0:
            m[4] = False
        return m

    def _obs(self) -> np.ndarray:
        oh = np.zeros(5, np.float32); oh[self._pos] = 1.0
        return np.concatenate([oh, [self._pos / 4.0, self._steps / 30.0]]).astype(np.float32)

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed if seed is not None else self._seed)
        self._pos = int(self.np_random.integers(0, self.L)); self._steps = 0
        return self._obs(), {"action_masks": self.action_masks()}

    def step(self, action: int):
        assert bool(self.action_masks()[action]), f"非法动作 {action}"
        r = -0.1
        if action == 0:
            self._pos = max(self._pos - 1, 0)
        elif action == 1:
            self._pos = min(self._pos + 1, self.L - 1)
        elif action == 3:
            r += 1.0 if self._pos == 2 else -0.5
        elif action == 4:
            r += 0.5
        self._steps += 1
        return self._obs(), float(r), self._pos == self.L - 1, self._steps >= 30, {"action_masks": self.action_masks()}


class MultiDiscreteEnv(gym.Env):
    """MultiDiscrete([2,3]) 玩具：每个子空间至少 1 个合法动作。"""

    def __init__(self, seed: int = 0):
        super().__init__()
        self.observation_space = spaces.Box(-1, 1, (4,), np.float32)
        self.action_space = spaces.MultiDiscrete([2, 3])
        self._seed = seed
        self._t = 0

    def action_masks(self) -> np.ndarray:
        # 每维禁一个入口，保证合法；Multicategorical 用扁平 sum(nvec)=5 维掩码
        return np.concatenate([np.array([True, False]), np.array([True, True, False])])

    def _obs(self):
        return np.zeros(4, np.float32)

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed if seed is not None else self._seed)
        self._t = 0
        return self._obs(), {"action_masks": self.action_masks().flatten()}

    def step(self, action):
        self._t += 1
        return self._obs(), 0.1, self._t >= 40, False, {"action_masks": self.action_masks().flatten()}


def _pair(seed: int = 42):
    env_u = DummyVecEnv([lambda: CorridorEnv(seed=1)])
    env_m = DummyVecEnv([lambda: CorridorEnv(seed=1)])
    common = dict(n_steps=32, batch_size=8, n_epochs=3, gamma=0.99, gae_lambda=0.95,
                  ent_coef=0.01, seed=seed, device="cpu",
                  policy_kwargs=small_policy_kwargs(), verbose=0)
    up = RecurrentPPO("MlpLstmPolicy", env_u, **common)
    my = MaskableRecurrentPPO("MlpLstmPolicy", env_m, **common)
    return up, my, env_u, env_m


def test_equivalence_with_official_recurrent_ppo() -> None:
    from sb3_contrib.common.recurrent.type_aliases import RNNStates
    # 全 True 掩码下：forward/评估与官方 RecurrentPPO 逐数字一致
    up, my, env_u, env_m = _pair(seed=42)
    obs_u = env_u.reset(); obs_m = env_m.reset()
    h0 = th.zeros(1, 1, 32); c0 = th.zeros(1, 1, 32)
    st_u = RNNStates((h0, c0), (h0.clone(), c0.clone()))
    st_m = RNNStates((h0.clone(), c0.clone()), (h0.clone(), c0.clone()))
    all_true = np.ones((1, 5), dtype=bool)
    for _ in range(4):
        a_u, v_u, lp_u, _ = up.policy.forward(th.as_tensor(obs_u, dtype=th.float32),
                                              st_u, th.zeros(1), deterministic=True)
        a_m, v_m, lp_m, _ = my.policy.forward(th.as_tensor(obs_m, dtype=th.float32),
                                              st_m, th.zeros(1), deterministic=True,
                                              action_masks=all_true)
        assert float((a_u - a_m).detach().abs().max()) == 0.0
        assert float((v_u - v_m).detach().abs().max()) == 0.0
        assert float((lp_u - lp_m).detach().abs().max()) <= 1e-6  # 上游 masked 二次归一化的 1-ULP
        legal = env_u.env_method("action_masks")[0]
        drive = np.array([int(np.argmax(legal))])
        # env 同步前进并取新 obs（DummyVecEnv 对终止局自动 reset，返回的即下一决策 obs）
        obs_u = env_u.step(drive)[0]
        obs_m = env_m.step(drive)[0]
    up.env.close(); my.env.close()


def test_predict_mask_compliance_after_training() -> None:
    model = MaskableRecurrentPPO("MlpLstmPolicy", DummyVecEnv([lambda: CorridorEnv(seed=7)]),
                                 n_steps=64, batch_size=16, n_epochs=3, ent_coef=0.01, seed=9,
                                 policy_kwargs=small_policy_kwargs(), verbose=0)
    model.learn(total_timesteps=600, progress_bar=False)
    env = CorridorEnv(seed=7)
    obs, _ = env.reset(seed=7)
    state, ep = None, np.array([False])
    viol = 0
    for _ in range(200):
        mask = env.action_masks()
        action, state = model.predict(obs[None], state=state, episode_start=ep,
                                      action_masks=mask[None], deterministic=False)
        a = int(action[0])
        if not mask[a]:
            viol += 1
        obs, _, term, trunc, _ = env.step(a)
        ep = np.array([term or trunc])
        if term or trunc:
            obs, _ = env.reset(seed=7); state, ep = None, np.array([False])
    model.env.close()
    assert viol == 0, f"predict 采样越界 {viol}/200"


def test_stability_no_nan_and_buffer_coherent() -> None:
    model = MaskableRecurrentPPO("MlpLstmPolicy", DummyVecEnv([lambda: CorridorEnv(seed=3)]),
                                 n_steps=64, batch_size=16, n_epochs=3, ent_coef=0.01, seed=0,
                                 policy_kwargs=small_policy_kwargs(), verbose=0)
    model.learn(total_timesteps=2000, progress_bar=False)
    bad = sum(1 for _, p in model.policy.named_parameters() if not bool(th.isfinite(p).all()))
    assert bad == 0, f"非有限参数 {bad}"
    # 缓冲掩码自洽：真实行至少 1 个合法
    buf = model.rollout_buffer
    if not buf.full:
        from stable_baselines3.common.callbacks import BaseCallback
        class _N(BaseCallback):
            def _on_step(self): return True
        model.collect_rollouts(model.env, _N(), buf, n_rollout_steps=buf.buffer_size)
    for rd in buf.get(64):
        am = rd.action_masks
        assert bool(am[rd.mask > 0.5].sum(dim=1).ge(1).all()), "有真实 transition 全掩码非法"
        break
    model.env.close()


def test_multidiscrete_runs() -> None:
    model = MaskableRecurrentPPO("MlpLstmPolicy", DummyVecEnv([lambda: MultiDiscreteEnv(seed=1)]),
                                 n_steps=32, batch_size=8, n_epochs=2, ent_coef=0.01, seed=0,
                                 policy_kwargs=small_policy_kwargs(), verbose=0)
    model.learn(total_timesteps=200, progress_bar=False)
    assert model.action_space.nvec.sum() == 5
    model.env.close()


def test_multibinary_explicitly_rejected() -> None:
    from gymnasium import spaces as _sp
    with pytest.raises(NotImplementedError):
        _mask_dims_for(_sp.MultiBinary(4))


def test_pinned_sb3_contrib_major() -> None:
    import sb3_contrib
    ver = tuple(int(x) for x in sb3_contrib.__version__.split(".")[:2])
    assert (2, 7) <= ver < (3, 0), f"依赖 sb3-contrib 2.x 内部实现，当前 {sb3_contrib.__version__}"


def test_mask_reinitialization_discards_stale_probability_cache() -> None:
    """长训重掩码不得让 PyTorch 校验 sb3-contrib 的旧 probs cache。"""
    from sb3_contrib.common.maskable.distributions import (
        MaskableCategoricalDistribution,
    )

    wrapper = MaskableCategoricalDistribution(217).proba_distribution(
        action_logits=th.zeros((2, 217), dtype=th.float32)
    )
    categorical = wrapper.distribution
    # 模拟长训中 float32 cache 的单纯形漂移；logits 本身仍完全有限。
    categorical.probs = th.full((2, 217), 2.0 / 217, dtype=th.float32)
    masks = np.zeros((2, 217), dtype=bool)
    masks[:, :7] = True

    _apply_masking_safely(wrapper, masks)

    assert bool(th.isfinite(wrapper.distribution.logits).all())
    assert bool(th.allclose(
        wrapper.distribution.probs.sum(dim=-1), th.ones(2), atol=1e-6,
    ))
    assert bool((wrapper.distribution.probs[:, 7:] == 0).all())


def test_buffer_masks_match_decision_time() -> None:
    """缓冲存储的掩码必须是决策时刻（s_t）的掩码，而非 step 后（s_{t+1}）。

    等价断言：采集后、训练前，用缓冲掩码复算 log_prob 必须与采集时
    old_log_prob 逐数字一致（PPO 第 0 epoch ratio==1 不变式）。掩码错刻
    会改变分布归一化，log_prob 立刻偏离——这是 collect 侧掩码流水线的
    端到端门禁（曾抓到"掩码刷新早于 buffer.add 的 off-by-one"）。
    """
    from stable_baselines3.common.callbacks import BaseCallback

    model = MaskableRecurrentPPO("MlpLstmPolicy", DummyVecEnv([lambda: CorridorEnv(seed=5)]),
                                 n_steps=64, batch_size=64, n_epochs=1, seed=11,
                                 policy_kwargs=small_policy_kwargs(), verbose=0)
    # 只采集不训练（learn() 会先 train 一次导致策略漂移，ratio 不再为 1）；
    # 手工补齐 _setup_learn 初始化的最小字段
    from collections import deque
    model._last_obs = model.env.reset()
    model._last_episode_starts = np.ones((model.env.num_envs,), dtype=bool)
    model.ep_info_buffer = deque(maxlen=100)
    model.ep_success_buffer = deque(maxlen=100)

    class _N(BaseCallback):
        def _on_step(self) -> bool:
            return True

    cb = _N()
    cb.init_callback(model)
    model.collect_rollouts(model.env, cb, model.rollout_buffer, n_rollout_steps=64)

    for rd in model.rollout_buffer.get(64):
        real = rd.mask > 1e-8
        actions = rd.actions.long().flatten()
        with th.no_grad():
            _, log_prob, _ = model.policy.evaluate_actions(
                rd.observations, actions, rd.lstm_states, rd.episode_starts,
                action_masks=rd.action_masks > 1e-8)
        diff = float((log_prob - rd.old_log_prob)[real].abs().max())
        assert diff <= 1e-5, f"缓冲掩码与决策时刻不一致（ratio≠1）：max|Δlogp|={diff:.6f}"
        break
    model.env.close()
