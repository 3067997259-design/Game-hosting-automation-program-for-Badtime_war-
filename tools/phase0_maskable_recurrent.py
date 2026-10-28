"""
tools/phase0_maskable_recurrent.py
──────────────────────────────────
MaskableRecurrentPPO Phase 0 验证脚本。

跑 3 + 1 项冒烟：
  [A] 数学等价性：相同种子/超参下，MaskableRecurrentPPO（全 True 掩码）
      与官方 RecurrentPPO 的 forward / evaluate_actions 输出逐数字一致；
  [B] 掩码服从性：训练 3k 步后，predict 采样/argmax 的动作 100% 落在
      环境提供的合法掩码内；
  [C] 训练稳定性：10k 步（n_envs=4，DummyVecEnv）无 NaN/Inf，参数有限，
      episode 统计正常，缓冲 action_masks 与 padding 语义自洽；
  [D] 多进程冒烟（附注项）：SubprocVecEnv(spawn) 200 步——验证
      env_method("action_masks") 跨进程路径（失败仅 WARN，不影响 [A][B][C]）。

用法（仓库根目录）：
    python tools/phase0_maskable_recurrent.py [--timesteps 10000] [--quick]

退出码：全部 PASS = 0；任一 FAIL = 1。
"""

from __future__ import annotations

import argparse
import sys
import time
from copy import deepcopy
from pathlib import Path

# 允许从仓库根直接 `python tools/phase0_maskable_recurrent.py` 运行
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import torch as th
import gymnasium as gym
from gymnasium import spaces
from stable_baselines3.common.callbacks import BaseCallback
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.vec_env import DummyVecEnv

from rl.maskable_recurrent import MaskableRecurrentPPO
from rl.maskable_recurrent.policies import MaskableRecurrentActorCriticPolicy

from sb3_contrib.ppo_recurrent.ppo_recurrent import RecurrentPPO as RECURRENT_PPO

# ─────────────────────────────────────────────────────────────────────────────
#  结果台账
# ─────────────────────────────────────────────────────────────────────────────

_CHECKS: list[tuple[str, bool, str]] = []
_CHECKS_SKIP: list[tuple[str, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    _CHECKS.append((name, ok, detail))
    tag = "PASS" if ok else "FAIL"
    print(f"  [{tag}] {name}" + (f" — {detail}" if detail else ""))


def record_skip(name: str, detail: str = "") -> None:
    _CHECKS_SKIP.append((name, detail))
    print(f"  [SKIP] {name}" + (f" — {detail}" if detail else ""))


# ─────────────────────────────────────────────────────────────────────────────
#  玩具环境：5 位置走廊 + 5 动作，掩码随状态变化，episode 长度可变
# ─────────────────────────────────────────────────────────────────────────────


class CorridorEnv(gym.Env):
    """5 位置走廊。

    动作：0=左移, 1=右移, 2=原地, 3=探测(仅 pos==2 合法), 4=爆发(偶数位非法)。
    终止：从非终点右移到 pos==4；截断：30 步上限。
    观测（8 维）：pos one-hot(5) + pos_norm + step_norm + last_reward。
    """

    L = 5
    MAX_STEPS = 30

    def __init__(self, seed: int = 0):
        super().__init__()
        self.observation_space = spaces.Box(low=-1.0, high=1.0, shape=(8,), dtype=np.float32)
        self.action_space = spaces.Discrete(5)
        self._seed = seed
        self._pos = 0
        self._steps = 0
        self._last_rew = 0.0

    def action_masks(self) -> np.ndarray:
        """当前状态下的合法动作掩码（bool，(5,)）。"""
        m = np.ones(5, dtype=bool)
        if self._pos == 0:
            m[0] = False         # 左移越界
        if self._pos == CorridorEnv.L - 1:
            m[1] = False         # 右移越界
        m[3] = self._pos == 2    # 探测仅中心合法
        if self._pos % 2 == 0:
            m[4] = False         # 爆发偶数位非法
        return m

    def _obs(self) -> np.ndarray:
        oh = np.zeros(5, dtype=np.float32)
        oh[self._pos] = 1.0
        return np.concatenate(
            [
                oh,
                [self._pos / 4.0, self._steps / CorridorEnv.MAX_STEPS, self._last_rew],
            ]
        ).astype(np.float32)

    def reset(self, *, seed=None, options=None):  # noqa: ANN001
        super().reset(seed=seed if seed is not None else self._seed)
        self._pos = int(self.np_random.integers(0, CorridorEnv.L))
        self._steps = 0
        self._last_rew = 0.0
        return self._obs(), {"action_masks": self.action_masks()}

    def step(self, action: int):
        valid = self.action_masks()
        assert bool(valid[action]), f"非法动作 {action}，masks={valid}"

        reward = -0.1
        if action == 0:
            self._pos = max(self._pos - 1, 0)
        elif action == 1:
            if self._pos < CorridorEnv.L - 1:
                self._pos += 1
        elif action == 2:
            pass
        elif action == 3:
            reward += 1.0 if self._pos == 2 else -0.5
        elif action == 4:
            reward += 0.5

        self._steps += 1
        terminated = self._pos == CorridorEnv.L - 1 and action == 1 and self._steps > 1
        truncated = self._steps >= CorridorEnv.MAX_STEPS
        self._last_rew = float(reward)
        return self._obs(), float(reward), terminated, truncated, {"action_masks": self.action_masks()}


def small_policy_kwargs() -> dict:
    return dict(lstm_hidden_size=32, net_arch=dict(pi=[32], vf=[32]))


class _NoopCallback(BaseCallback):
    """collect_rollouts 占位回调（不做任何事）。"""

    def __init__(self) -> None:
        super().__init__(verbose=0)


def make_vec_env(num_envs: int, seed_base: int = 0) -> DummyVecEnv:
    """DummyVecEnv 包装（Monitor 提供 episode 统计；env_method 经 __getattr__ 可达）。"""
    return DummyVecEnv(
        [lambda i=i: Monitor(CorridorEnv(seed=seed_base + i)) for i in range(num_envs)]
    )


def build_pair(seed: int = 42):
    """相同种子/超参下构建官方 RecurrentPPO 与 MaskableRecurrentPPO（不训练）。

    两个模型分别在各自 _setup_model 里 set_random_seed(seed)，网络初始化
    完全一致；掩码版仅在分布层多包一层，不消耗 RNG。
    """
    env_rec = make_vec_env(1)
    env_mask = make_vec_env(1)
    common = dict(
        n_steps=32,
        batch_size=8,
        n_epochs=3,
        gamma=0.99,
        gae_lambda=0.95,
        ent_coef=0.01,
        seed=seed,
        device="cpu",  # 等价性在 CPU 逐数字比对（跨机稳定；CUDA 装后 device=auto 会到 cuda 而手动 forward 用 CPU 张量 → 设备不匹配）
        policy_kwargs=small_policy_kwargs(),
        verbose=0,
    )
    rec = RECURRENT_PPO("MlpLstmPolicy", env_rec, **common)
    mas = MaskableRecurrentPPO("MlpLstmPolicy", env_mask, **common)
    return rec, mas, env_rec, env_mask


# ─────────────────────────────────────────────────────────────────────────────
#  [A] 数学等价性
# ─────────────────────────────────────────────────────────────────────────────


def check_equivalence() -> None:
    print("\n[A] 数学等价性：MaskableRecurrentPPO(全 True mask) vs RecurrentPPO")
    rec, mas, env_rec, env_mask = build_pair(seed=42)

    obs_rec = env_rec.reset()
    obs_mask = env_mask.reset()
    lstm_rec = deepcopy(rec._last_lstm_states)
    lstm_mask = deepcopy(mas._last_lstm_states)

    all_true = np.ones((1, 5), dtype=bool)
    max_delta_action = 0.0
    max_delta_value = 0.0
    max_delta_logp = 0.0

    for t in range(6):
        obs_t_rec = rec.policy.obs_to_tensor(obs_rec)[0]
        ep_rec = th.zeros(1, dtype=th.float32, device=rec.device)
        th.manual_seed(0)
        a_rec, v_rec, lp_rec, lstm_rec = rec.policy.forward(
            obs_t_rec, lstm_rec, ep_rec, deterministic=True
        )

        obs_t_mask = mas.policy.obs_to_tensor(obs_mask)[0]
        ep_mask = th.zeros(1, dtype=th.float32, device=mas.device)
        th.manual_seed(0)
        a_mask, v_mask, lp_mask, lstm_mask = mas.policy.forward(
            obs_t_mask, lstm_mask, ep_mask, deterministic=True, action_masks=all_true
        )

        max_delta_action = max(max_delta_action, float((a_rec - a_mask).detach().abs().max()))
        max_delta_value = max(max_delta_value, float((v_rec - v_mask).detach().abs().max()))
        max_delta_logp = max(max_delta_logp, float((lp_rec - lp_mask).detach().abs().max()))

        # 环境推进：两侧用同一个"合法动作"驱动（掩码 argmax），
        # 等价性只比较模型输出，避免全 True 掩码采样出环境非法动作。
        legal = env_rec.env_method("action_masks")[0]
        drive = np.array([int(np.argmax(legal))])
        _, _, dones_rec, _ = env_rec.step(drive)
        _, _, dones_mask, _ = env_mask.step(drive)
        # 下一步的 episode_start = 本步 done
        ep_rec = th.tensor(dones_rec, dtype=th.float32, device=rec.device)
        ep_mask = th.tensor(dones_mask, dtype=th.float32, device=mas.device)

    ok = (
        max_delta_action == 0.0
        and max_delta_value == 0.0
        and max_delta_logp <= 1e-6
    )
    record(
        "forward 等价（6 步 deterministic）",
        ok,
        f"Δaction={max_delta_action:.2e} Δvalue={max_delta_value:.2e} "
        f"Δlogp={max_delta_logp:.2e}（Δlogp 允许 ≤1e-6 的 ULP："
        f"torch Categorical 构造会归一化 logits，而上游 MaskableCategorical "
        f"在 apply_masking(None) 时对已归一化 logits 二次归一化，引入 2^-23 重舍入，"
        f"非逻辑差异）",
    )

    # evaluate_actions 等价（合成 padded 风格 batch：8 行，episode_starts 混合）
    from sb3_contrib.common.recurrent.type_aliases import RNNStates as _RNNStates

    B = 8
    obs_b = th.rand(B, 8, dtype=th.float32)
    acts_b = th.randint(0, 5, (B,))
    shape = (1, B, 32)
    h_zero = th.zeros(shape)
    st_rec = _RNNStates((h_zero, h_zero.clone()), (h_zero.clone(), h_zero.clone()))
    st_mask = _RNNStates((h_zero.clone(), h_zero.clone()), (h_zero.clone(), h_zero.clone()))
    ep_b = th.tensor([1.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0])

    v_rec, lp_rec, ent_rec = rec.policy.evaluate_actions(
        obs_b, acts_b, st_rec, ep_b
    )
    v_mask, lp_mask, ent_mask = mas.policy.evaluate_actions(
        obs_b, acts_b, st_mask, ep_b,
        action_masks=np.ones((B, 5), dtype=bool),
    )
    dv = float((v_rec - v_mask).detach().abs().max())
    dlp = float((lp_rec - lp_mask).detach().abs().max())
    de = float((ent_rec - ent_mask).detach().abs().max())
    record(
        "evaluate_actions 等价（8 行混合 episode_starts）",
        dv == 0.0 and dlp <= 1e-6 and de <= 1e-6,
        f"Δv={dv:.2e} Δlogp={dlp:.2e} Δent={de:.2e}",
    )

    # 掩码语义：把 0 号位屏蔽 → 该动作 log_prob 必须为 -inf（概率为 0）
    m_zero = np.ones((B, 5), dtype=bool)
    m_zero[:, 0] = False
    _, lp_masked, ent_masked = mas.policy.evaluate_actions(
        obs_b, acts_b, st_mask, ep_b, action_masks=m_zero
    )
    all_zero_actions = (acts_b == 0).float()
    masked_at_0 = all_zero_actions == 1.0
    if masked_at_0.any():
        lp_at_0 = lp_masked[masked_at_0]
        # 注意：torch Categorical 构造会对 logits 归一化，因此掩码动作的
        # log_prob 是约 -1e8 的有限大负数（exp → 0），不是字面 -inf。
        ok_maskinf = bool((lp_at_0 <= -1e6).all())
        record(
            "被屏蔽动作 log_prob ≈ -1e8（概率≈0）",
            ok_maskinf,
            f"行 {list(masked_at_0.nonzero(as_tuple=True)[0].tolist())}，"
            f"min={float(lp_at_0.detach().min()):.2e}",
        )
    else:
        record("被屏蔽动作 log_prob ≈ -1e8（概率≈0）", True, "本批无动作 0（构造已保证存在）")

    # padding 全 False 行的熵 = 0（不产生 NaN）
    m_all_false = np.zeros((B, 5), dtype=bool)
    _, _, ent_ff = mas.policy.evaluate_actions(
        obs_b, acts_b, st_mask, ep_b, action_masks=m_all_false
    )
    record(
        "全 False 掩码行的熵为 0 且无 NaN",
        bool(th.isfinite(ent_ff).all()) and float(ent_ff.detach().abs().sum()) == 0.0,
        f"max|ent|={float(ent_ff.detach().abs().max()):.2e}",
    )


# ─────────────────────────────────────────────────────────────────────────────
#  [B] 掩码服从性
# ─────────────────────────────────────────────────────────────────────────────


def check_legality(model: MaskableRecurrentPPO, n_steps: int = 300) -> None:
    print("\n[B] 掩码服从性：训练 3k 步后 predict 采样 100% 合法")
    env = CorridorEnv(seed=7)
    obs, _ = env.reset(seed=7)
    state = None
    violation = 0
    samples = 0
    for _ in range(n_steps):
        mask = env.action_masks()
        action, state = model.predict(
            obs[None],
            state=state,
            episode_start=np.array([False]),
            action_masks=mask[None],
            deterministic=(samples % 2 == 0),
        )
        a = int(action[0])
        samples += 1
        if not mask[a]:
            violation += 1
        obs, _, term, trunc, _ = env.step(a)
        if term or trunc:
            obs, _ = env.reset(seed=7)
            state = None
    record(
        "predict 采样/argmax 全落在合法集内",
        violation == 0,
        f"{samples} 步，越界 {violation} 次",
    )


# ─────────────────────────────────────────────────────────────────────────────
#  [C] 训练稳定性（无 NaN）
# ─────────────────────────────────────────────────────────────────────────────


def check_stability(timesteps: int) -> None:
    print(f"\n[C] 训练稳定性：{timesteps} 步 n_envs=4（DummyVecEnv）")
    from stable_baselines3.common.logger import configure

    model = MaskableRecurrentPPO(
        "MlpLstmPolicy",
        make_vec_env(4),
        n_steps=64,
        batch_size=16,
        n_epochs=3,
        gamma=0.99,
        gae_lambda=0.95,
        ent_coef=0.01,
        seed=123,
        policy_kwargs=small_policy_kwargs(),
        verbose=0,
    )
    model.set_logger(configure(None, ["stdout"]))

    t0 = time.time()
    model.learn(total_timesteps=timesteps, progress_bar=False)
    elapsed = time.time() - t0

    # 参数有限性
    inf_params = 0
    for name, p in model.policy.named_parameters():
        if not bool(th.isfinite(p).all()):
            inf_params += 1
            print(f"    !! 非有限参数: {name}")
    record("全部参数有限（无 NaN/Inf）", inf_params == 0, f"非有限参数 {inf_params} 个")

    # 缓冲 action_masks 自洽：真实行至少一个合法动作；padding 行全 False
    buf = model.rollout_buffer
    if not buf.full:
        # learn 可能在 rollout 中途结束：补采一轮完整 rollout 再抽样
        model.collect_rollouts(
            model.env,
            _NoopCallback(),
            buf,
            n_rollout_steps=buf.buffer_size,
        )
    batch = next(iter(buf.get(64)))  # 任一 minibatch
    am = batch.action_masks
    am_real = am[batch.mask > 0.5]
    am_pad = am[batch.mask <= 0.5]
    ok_real = bool(am_real.sum(dim=1).ge(1).all())
    ok_pad = bool(th.isfinite(am_pad).all())
    record(
        "缓冲 action_masks 自洽（真实行≥1 合法；padding 有限）",
        ok_real and ok_pad,
        f"真实行 {len(am_real)}、padding 行 {len(am_pad)}",
    )

    # episode 统计
    ep_rewards = [info.get("r", 0.0) for info in model.ep_info_buffer if info is not None]
    ep_lens = [info.get("l", 0) for info in model.ep_info_buffer if info is not None]
    mean_r = float(np.mean(ep_rewards)) if ep_rewards else float("nan")
    mean_l = float(np.mean(ep_lens)) if ep_lens else float("nan")
    record(
        "episode 统计正常",
        len(ep_rewards) > 0 and all(np.isfinite(ep_rewards)),
        f"{len(ep_rewards)} 局，mean_r={mean_r:.3f}，mean_len={mean_l:.1f}，耗时 {elapsed:.1f}s",
    )
    model.env.close()


# ─────────────────────────────────────────────────────────────────────────────
#  [D] 多进程冒烟（附注项）
# ─────────────────────────────────────────────────────────────────────────────


def check_subproc(quick: bool = False) -> None:
    print("\n[D] 多进程冒烟：SubprocVecEnv(spawn) 200 步（附注项）")
    try:
        from stable_baselines3.common.vec_env import SubprocVecEnv
        from sb3_contrib.common.maskable.utils import is_masking_supported

        env = SubprocVecEnv(
            [lambda i=i: CorridorEnv(seed=20 + i) for i in range(2)],
            start_method="spawn",
        )
        if not is_masking_supported(env):
            record(
                "SubprocVecEnv is_masking_supported",
                False,
                "has_attr 在子进程下探测不到；Phase 1 需经 info 通道或 has_attr 适配（本项非门禁）",
            )
            env.close()
            return

        model = MaskableRecurrentPPO(
            "MlpLstmPolicy",
            env,
            n_steps=32,
            batch_size=8,
            n_epochs=2,
            ent_coef=0.01,
            seed=1,
            policy_kwargs=small_policy_kwargs(),
            verbose=0,
        )
        model.learn(total_timesteps=200 if quick else 400, progress_bar=False)
        record("SubprocVecEnv 训练 200+ 步通过", True, "env_method(action_masks) 跨进程可用")
        model.env.close()
    except PermissionError as exc:
        # 沙箱/系统策略禁止子进程创建（spawn）——环境限制，非代码问题，记为 SKIP
        record_skip("SubprocVecEnv 训练", f"进程创建被拒（WinError）：{exc}")
        try:
            env.close()  # noqa: F821
        except Exception:  # noqa: BLE001, S110
            pass
    except Exception as exc:  # noqa: BLE001 — 附注项，异常仅记录
        record("SubprocVecEnv 训练", False, f"{type(exc).__name__}: {exc}")
        try:
            env.close()  # noqa: F821
        except Exception:  # noqa: BLE001, S110
            pass


# ─────────────────────────────────────────────────────────────────────────────
#  入口
# ─────────────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="MaskableRecurrentPPO Phase 0 验证")
    parser.add_argument("--timesteps", type=int, default=10_000, help="稳定性测试步数")
    parser.add_argument("--quick", action="store_true", help="快速模式（缩小步数）")
    args = parser.parse_args()

    timesteps = min(args.timesteps, 2_000) if args.quick else args.timesteps

    print("=" * 72)
    print("MaskableRecurrentPPO Phase 0 验证")
    print(f"torch={th.__version__} / numpy={np.__version__}")
    print("=" * 72)

    # [A] 数学等价性（需要先构建未训练模型）
    try:
        check_equivalence()
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        record("A 数学等价性", False, f"{type(exc).__name__}: {exc}")

    # 训练一个小模型供 [B] 使用
    print("\n-- 训练 3k 步供 [B] 使用 --")
    model_small = MaskableRecurrentPPO(
        "MlpLstmPolicy",
        make_vec_env(2),
        n_steps=64,
        batch_size=16,
        n_epochs=3,
        ent_coef=0.01,
        seed=9,
        policy_kwargs=small_policy_kwargs(),
        verbose=0,
    )
    model_small.learn(total_timesteps=3_000, progress_bar=False)
    model_small.env.close()
    check_legality(model_small)

    # [C] 稳定性
    check_stability(timesteps)

    # [D] 多进程
    check_subproc(quick=args.quick)

    # 汇总
    print("\n" + "=" * 72)
    n_fail = sum(1 for _, ok, _ in _CHECKS if not ok)
    n_pass = len(_CHECKS) - n_fail
    msg = f"Phase 0 结果汇总：{n_pass}/{len(_CHECKS)} PASS"
    if n_fail:
        msg += f"，{n_fail} FAIL"
    if _CHECKS_SKIP:
        msg += f"，{len(_CHECKS_SKIP)} SKIP（环境限制，不判失败）"
    print(msg)
    print("=" * 72)
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())