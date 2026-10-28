"""tests/test_rl_m9_distill_train.py
──────────────────────────────
P4 门禁（docs/m9/rl/m9_rl_next_steps.md §7）：

- 数据加载：episode_starts 切分正确（每局 k 转换 + 1 尾拍）、排除清单生效；
- 逆频率权重：sqrt 温度数学、均值归一；
- 小型合成蒸馏端到端：损失下降、aux 准确率学到 1.0；
- forward_actor_aux 与 get_distribution 的掩码分布一致（forward 无漂移）。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import numpy as np
import torch as th
from gymnasium import spaces

from engine.experiments import set_profile

set_profile("m9-rfc")

from rl.distill_student import (  # noqa: E402
    N_EXPERTS,
    DistillEpisode,
    DistillStudentPolicy,
    DISTILL_EXCLUDED_CELLS,
    evaluate_distillation,
    expert_balance_weights,
    load_distill_dataset,
    train_distillation,
)

OBS_DIM, ACT_DIM = 24, 217  # 合成任务用低维（917 维纯噪声会逼网络靠记忆过拟合）


def _make_shard(path: Path, *, episodes: int, length: int,
                expert: int, action: int, seed: int) -> int:
    """写一个最小合法分片；返回总步数。"""
    rng = np.random.default_rng(seed)
    steps = episodes * length
    masks = np.zeros((steps, ACT_DIM), dtype=bool)
    masks[:, action] = True
    actions = np.full(steps, action, dtype=np.int64)
    experts = np.full(steps, expert, dtype=np.int64)
    probs = np.full((steps, ACT_DIM), 1e-6, dtype=np.float32)
    probs[:, action] = 1.0
    starts = np.zeros(steps, dtype=bool)
    for i in range(episodes):
        starts[i * length] = True
    obs = np.zeros((steps + episodes, OBS_DIM), dtype=np.float32)
    np.savez_compressed(
        path, observations=obs, action_masks=masks, actions=actions,
        expert_ids=experts, reasons=np.asarray(["r"] * steps),
        teacher_probs=probs, rewards=np.zeros(steps, dtype=np.float32),
        episode_starts=starts,
    )
    return steps


def _tiny_policy() -> DistillStudentPolicy:
    return DistillStudentPolicy(
        spaces.Box(-1.0, 1.0, (OBS_DIM,), dtype=np.float32),
        spaces.Discrete(ACT_DIM),
        lr_schedule=lambda _: 1e-3,
        lstm_hidden_size=16,
        net_arch=dict(pi=[32], vf=[32]),
        enable_critic_lstm=True,
        distill_n_experts=N_EXPERTS,
    )


class TestLoadDataset(unittest.TestCase):
    """分片加载与切分。"""

    def test_split_and_tail_obs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_shard(root / "natural_3p.npz", episodes=3, length=4,
                        expert=0, action=5, seed=1)
            episodes = load_distill_dataset(root, exclude=())
            self.assertEqual(len(episodes), 3)
            for episode in episodes:
                k = episode.actions.shape[0]
                self.assertEqual(episode.observations.shape, (k + 1, OBS_DIM))
                self.assertEqual(episode.action_masks.shape, (k, ACT_DIM))
                self.assertTrue(bool(episode.action_masks[0, 5]))

    def test_exclusion_list_honored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_shard(root / "course_survival_heal_counterattack.npz",
                        episodes=2, length=3, expert=3, action=7, seed=2)
            _make_shard(root / "natural_4p.npz", episodes=2, length=3,
                        expert=0, action=5, seed=3)
            episodes = load_distill_dataset(root)  # 默认排除清单
            self.assertEqual(
                sorted({ep.cell for ep in episodes}), ["natural_4p"],
            )

    def test_default_exclusion_matches_doc(self):
        # P4.1 勘误：pp_damage_choice 修好后移出排除清单
        self.assertEqual(
            set(DISTILL_EXCLUDED_CELLS),
            {"course_survival_heal_counterattack",
             "course_survival_armor_counterattack"},
        )


class TestBalanceWeights(unittest.TestCase):
    """逆频率权重数学。"""

    def test_sqrt_inverse_frequency(self):
        # expert0: 8 步, expert1: 2 步 → 权重比 = (10/(10*8))^.5 : (10/(10*2))^.5
        episodes = []
        for _ in range(4):
            episodes.append(DistillEpisode(
                cell="c", observations=np.zeros((3, OBS_DIM), np.float32),
                action_masks=np.ones((2, ACT_DIM), bool),
                actions=np.zeros(2, np.int64),
                expert_ids=np.array([0, 0], np.int64),
                teacher_probs=np.full((2, ACT_DIM), 1 / ACT_DIM, np.float32),
            ))
        episodes.append(DistillEpisode(
            cell="c", observations=np.zeros((3, OBS_DIM), np.float32),
            action_masks=np.ones((2, ACT_DIM), bool),
            actions=np.zeros(2, np.int64),
            expert_ids=np.array([1, 1], np.int64),
            teacher_probs=np.full((2, ACT_DIM), 1 / ACT_DIM, np.float32),
        ))
        weights = expert_balance_weights(episodes)
        self.assertAlmostEqual(float(weights.mean()), 1.0, places=5)
        self.assertGreater(float(weights[1]), float(weights[0]))
        self.assertAlmostEqual(
            float(weights[1] / weights[0]), np.sqrt(8 / 2), places=4,
        )
        self.assertEqual(float(weights[2]), 0.0)  # 无样本专家权重为 0


class TestSyntheticDistillation(unittest.TestCase):
    """小型可学习任务的收敛性。"""

    def test_distill_converges_and_aux_learns(self):
        rng = np.random.default_rng(0)
        episodes = []
        # 两个可分族：obs[0]=0 → teacher 集中 action 3 / expert 1；
        #            obs[0]=1 → teacher 集中 action 9 / expert 5。
        # 每拍另有 4 个合法干扰动作——掩码本身不泄露答案，策略必须学。
        legal = {0: [3, 20, 40, 60, 80], 1: [9, 21, 41, 61, 81]}
        for idx in range(24):
            mark = idx % 2
            length = 4
            obs = rng.normal(size=(length + 1, OBS_DIM)).astype(np.float32)
            obs[:, 0] = float(mark)
            masks = np.zeros((length, ACT_DIM), dtype=bool)
            probs = np.full((length, ACT_DIM), 1e-6, dtype=np.float32)
            target = legal[mark][0]
            for a in legal[mark]:
                masks[:, a] = True
                if a == target:
                    probs[:, a] = 1.0
                else:
                    probs[:, a] = 1e-6
            episodes.append(DistillEpisode(
                cell="syn", observations=obs, action_masks=masks,
                actions=np.full(length, target, np.int64),
                expert_ids=np.full(length, 1 if mark == 0 else 5, np.int64),
                teacher_probs=probs,
            ))
        policy = _tiny_policy()
        baseline = evaluate_distillation(policy, episodes[20:])
        history = train_distillation(
            policy, episodes[:20], episodes[20:],
            epochs=120, learning_rate=1e-2, batch_episodes=4,
            patience=30, seed=0,
        )
        self.assertGreater(len(history), 0)
        final = evaluate_distillation(policy, episodes[20:])
        # 任务数十 epoch 收敛到近 0——用训练前基线做收敛断言
        self.assertLess(final["val_nll"], baseline["val_nll"])
        self.assertLess(final["val_nll"], 0.5)
        self.assertGreater(final["val_aux_accuracy"], 0.85)
        self.assertLess(final["val_kl"], 0.5)


class TestAuxForwardConsistency(unittest.TestCase):
    """aux 前向与官方 get_distribution 同口径。"""

    def test_probs_match_get_distribution(self):
        policy = _tiny_policy()
        policy.set_training_mode(False)
        obs = th.randn(6, OBS_DIM)
        starts = th.zeros(6)
        starts[0] = 1.0
        hidden = th.zeros(
            1, 1, int(policy.lstm_actor.hidden_size),
        )
        state = (hidden, hidden.clone())
        masks = np.ones((6, ACT_DIM), dtype=bool)
        masks[:, 100:] = False
        dist, aux, _next = policy.forward_actor_aux(obs, state, starts)
        from rl.maskable_recurrent.policies import _apply_masking_safely

        _apply_masking_safely(dist, masks)
        ref, _ = policy.get_distribution(obs, state, starts, action_masks=masks)
        np.testing.assert_allclose(
            dist.distribution.probs.detach().numpy(),
            ref.distribution.probs.detach().numpy(),
            atol=1e-5,
        )
        self.assertEqual(aux.shape, (6, N_EXPERTS))
        # expert_head 不在 policy.optimizer 里（PPO 阶段自动冻结）
        opt_params = {id(p) for p in policy.optimizer.param_groups[0]["params"]}
        self.assertNotIn(id(policy.expert_head.weight), opt_params)


if __name__ == "__main__":
    unittest.main()
