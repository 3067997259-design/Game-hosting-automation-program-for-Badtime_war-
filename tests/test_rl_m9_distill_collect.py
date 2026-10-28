"""tests/test_rl_m9_distill_collect.py
──────────────────────────────
P3 门禁（docs/m9/rl/m9_rl_next_steps.md §6）：

- rl_talent_reserve 预留池语义：指定编号不被 AI 对手抢占、非法组合拒绝、
  默认关闭时行为不变（可能被抢）；
- ExpertStackModel 捕获通道：capture_enabled 时 predict 附带
  expert/reason/probs/boundary，logits_fn 拿到切换前的专家状态；
- 采集分片不变量（npz 结构与 verify 规则的纯数组级校验）。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import numpy as np

from engine.experiments import set_profile

set_profile("m9-rfc")

from rl.expert_stack import (  # noqa: E402
    EXPERT_CHECKPOINTS,
    GameFacts,
    ExpertStackModel,
    OpponentFacts,
)


def _facts(**overrides) -> GameFacts:
    defaults = dict(
        kill_count=0, hp=20, max_hp=20, pp=0, location="",
        choose_mode=False, situation="", has_bow=False,
        best_weapon_damage=4, has_unsharpened_knife=False,
        has_detection=False, detection_attrs=frozenset(),
        own_armor_min_ratio=1.0,
        opponents=(OpponentFacts(True, 20, False, False),),
    )
    defaults.update(overrides)
    return GameFacts(**defaults)


class _FakeExpert:
    def __init__(self, label: int) -> None:
        self.label = label

        class _Space:
            shape = (828,)
            n = 217

        self.observation_space = _Space()
        self.action_space = _Space()

    def predict(self, obs, state=None, episode_start=None,
                action_masks=None, deterministic=True):
        return np.array([self.label], dtype=np.int64), f"s{self.label}"


def _fake_experts() -> dict:
    keys = ["opening", "postkill", "pp_finish", "survival_heal",
            "armor_repair", "sharpen", "detection", "attribute",
            "fire_antiarmor", "police"]
    return {key: _FakeExpert(i) for i, key in enumerate(keys)}


class TestTalentReserve(unittest.TestCase):
    """预留池语义（P3 裁决 2026-08-22）。"""

    def test_reserve_rejects_invalid_combination(self):
        from rl.env import BadtimeWarEnv

        for bad in (None, 0):
            with self.assertRaises(ValueError):
                BadtimeWarEnv(rl_talent=bad, rl_talent_reserve=True)

    def test_reserve_guarantees_assignment_on_colliding_seeds(self):
        from rl.env import BadtimeWarEnv
        from rl.obs_builder_m9 import _talent_slot_id

        # 90213 是无预留时 T3 必被 AI 抢走的种子（探针实证）
        for seed in (90213, 90214, 90215):
            env = BadtimeWarEnv(
                num_opponents=1, max_rounds=4, n_stack=1,
                enable_talents=True, rl_talent=3, rl_talent_reserve=True,
            )
            try:
                env.reset(seed=seed)
                self.assertEqual(
                    _talent_slot_id(getattr(env._rl_player, "talent", None)),
                    "T3",
                )
            finally:
                env.close()


class TestCaptureChannel(unittest.TestCase):
    """ExpertStackModel 的 teacher-logits 捕获。"""

    def test_capture_off_by_default(self):
        stack = ExpertStackModel(_fake_experts(), lambda: _facts())
        stack.predict(
            np.zeros(828, dtype=np.float32), episode_start=np.array([True]),
            action_masks=np.ones(217, dtype=bool),
        )
        self.assertIsNone(stack.capture_last)

    def test_capture_records_expert_reason_boundary_and_probs(self):
        seen_states = []

        def stub_logits_fn(expert, obs, state, boundary, mask):
            seen_states.append((state, boundary))
            probs = np.full(217, 1.0 / 217, dtype=np.float32)
            probs[expert.label] = 0.5
            return probs / probs.sum()

        stack = ExpertStackModel(
            _fake_experts(), lambda: _facts(), logits_fn=stub_logits_fn,
        )
        stack.capture_enabled = True
        mask = np.ones(217, dtype=bool)
        obs = np.zeros(828, dtype=np.float32)
        _, state = stack.predict(
            obs, state=None, episode_start=np.array([False]),
            action_masks=mask,
        )
        capture = stack.capture_last
        self.assertEqual(capture["expert"], "opening")
        self.assertTrue(capture["boundary"])  # state=None 哨兵边界
        self.assertAlmostEqual(float(capture["probs"].sum()), 1.0, places=5)
        # logits_fn 拿到的是该专家切换前的状态（首拍 = None）
        self.assertEqual(seen_states[-1][0], None)
        # 第二拍：非边界、状态为前值
        stack.predict(
            obs, state=state, episode_start=np.array([False]),
            action_masks=mask,
        )
        self.assertFalse(stack.capture_last["boundary"])
        self.assertEqual(seen_states[-1][0], "s0")

    def test_capture_does_not_change_actions(self):
        def stub_logits_fn(expert, obs, state, boundary, mask):
            return np.full(217, 1.0 / 217, dtype=np.float32)

        plain = ExpertStackModel(_fake_experts(), lambda: _facts())
        capturing = ExpertStackModel(
            _fake_experts(), lambda: _facts(), logits_fn=stub_logits_fn,
        )
        capturing.capture_enabled = True
        mask = np.ones(217, dtype=bool)
        obs = np.zeros(828, dtype=np.float32)
        for stack in (plain, capturing):
            stack.predict(
                obs, state=None, episode_start=np.array([True]),
                action_masks=mask,
            )
        self.assertEqual(plain.routing_log, capturing.routing_log)


class TestShardInvariants(unittest.TestCase):
    """verify 规则的数组级自检（构造一个合法/非法分片各验一次）。"""

    def _write_shard(self, path, *, legal=True):
        steps = 6
        episodes = 2
        masks = np.zeros((steps, 217), dtype=bool)
        masks[:, 3] = True
        actions = np.full(steps, 3, dtype=np.int64)
        if not legal:
            actions[2] = 99  # 违反自身掩码
        probs = np.full((steps, 217), 1.0 / 217, dtype=np.float32)
        starts = np.zeros(steps, dtype=bool)
        starts[0] = True
        starts[3] = True
        obs = np.zeros((steps + episodes, 918), dtype=np.float32)
        np.savez_compressed(
            path,
            observations=obs, action_masks=masks, actions=actions,
            expert_ids=np.zeros(steps, dtype=np.int64),
            reasons=np.asarray(["r"] * steps),
            teacher_probs=probs, rewards=np.zeros(steps, dtype=np.float32),
            episode_starts=starts,
        )

    def test_verify_accepts_legal_shard(self):
        import tempfile

        from tools.m9_distill_collect import verify_dir

        with tempfile.TemporaryDirectory() as tmp:
            self._write_shard(Path(tmp) / "natural_3p.npz", legal=True)
            self.assertEqual(verify_dir(Path(tmp)), 6)

    def test_verify_rejects_mask_violation(self):
        import tempfile

        from tools.m9_distill_collect import verify_dir

        with tempfile.TemporaryDirectory() as tmp:
            self._write_shard(Path(tmp) / "natural_3p.npz", legal=False)
            with self.assertRaises(AssertionError):
                verify_dir(Path(tmp))


class TestRegistrySynchronization(unittest.TestCase):
    """采集侧专家 ID 序与注册表同步。"""

    def test_expert_id_order_matches_registry(self):
        from tools.m9_distill_collect import EXPERT_ID_ORDER

        self.assertEqual(
            set(EXPERT_ID_ORDER), set(EXPERT_CHECKPOINTS),
        )
        self.assertEqual(len(EXPERT_ID_ORDER), 10)


if __name__ == "__main__":
    unittest.main()
