"""tests/test_rl_obs_talent_block.py
──────────────────────────────
P2 天赋观测块单测（docs/m9/rl/m9_rl_next_steps.md §5）：

- 布局常量：918 = 828(v5 冻结核心) + 90(天赋块)，append-only 不动前缀；
- _fill_talent_block：自身/对手 one-hot 正确、None 槽全零、无天赋全零；
- LegacyObsDimWrapper：尾部截断、observation_space 收窄、非法参数拒绝。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("OMP_NUM_THREADS", "1")

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from rl.obs_builder_m9 import (
    M9_TALENT_BLOCK_BASE,
    M9_TALENT_BLOCK_DIM,
    M9_TALENT_OWN_DIM,
    M9_TALENT_PER_OPPONENT_DIM,
    M9_TALENT_SLOT_IDS,
    OBS_DIM_M9_TOTAL,
    OBS_DIM_M9_V5_CORE,
    _fill_talent_block,
)
from rl.obs_compat import LegacyObsDimWrapper


def _player(pid: str, slot: str | None):
    talent = SimpleNamespace(slot_id=slot) if slot else None
    return SimpleNamespace(player_id=pid, talent=talent)


def _state(players: dict):
    return SimpleNamespace(
        m9_enabled=True,
        player_order=list(players),
        get_player=lambda pid: players.get(pid),
    )


class TestTalentBlockLayout(unittest.TestCase):
    """布局常量与 append-only 纪律。"""

    def test_dimensions(self):
        self.assertEqual(OBS_DIM_M9_V5_CORE, 828)
        self.assertEqual(len(M9_TALENT_SLOT_IDS), 14)
        self.assertEqual(
            M9_TALENT_BLOCK_DIM,
            M9_TALENT_OWN_DIM + 5 * M9_TALENT_PER_OPPONENT_DIM,
        )
        self.assertEqual(M9_TALENT_BLOCK_DIM, 90)
        self.assertEqual(OBS_DIM_M9_TOTAL, 828 + 90)
        self.assertEqual(M9_TALENT_BLOCK_BASE, OBS_DIM_M9_V5_CORE)

    def test_slot_ids_canonical(self):
        self.assertEqual(
            M9_TALENT_SLOT_IDS,
            ("T1", "T2", "T3", "T4", "T6", "T7",
             "G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7"),
        )


class TestFillTalentBlock(unittest.TestCase):
    """_fill_talent_block 的填充正确性。"""

    def _fill(self, own_slot, opp_slots):
        players = {"rl_0": _player("rl_0", own_slot)}
        for i, slot in enumerate(opp_slots):
            players[f"ai_{i}"] = _player(f"ai_{i}", slot)
        obs = np.zeros(OBS_DIM_M9_TOTAL, dtype=np.float32)
        _fill_talent_block(obs, players["rl_0"], _state(players))
        return obs

    def test_own_onehot(self):
        obs = self._fill("G3", ["T1", None, "G0", "T7", "G7"])
        block = obs[M9_TALENT_BLOCK_BASE:]
        self.assertEqual(block[0], 1.0)  # 有天赋标志
        onehot = block[1:1 + 14]
        self.assertEqual(int(onehot.sum()), 1)
        self.assertEqual(onehot[M9_TALENT_SLOT_IDS.index("G3")], 1.0)

    def test_opponent_slots(self):
        # 3 名真实对手（其中 ai_1 无天赋）+ 2 个 padding 槽
        obs = self._fill("T1", ["G5", None, "G0"])
        block = obs[M9_TALENT_BLOCK_BASE:]
        base = M9_TALENT_OWN_DIM
        expected = ["G5", "no_talent", "G0", "padding", "padding"]
        for i, kind in enumerate(expected):
            seg = block[base + i * M9_TALENT_PER_OPPONENT_DIM:
                        base + (i + 1) * M9_TALENT_PER_OPPONENT_DIM]
            if kind == "padding":
                self.assertEqual(seg[0], 0.0)
                self.assertEqual(int(seg[1:].sum()), 0)
            elif kind == "no_talent":
                # 真实对手但无天赋：在席、one-hot 全零
                self.assertEqual(seg[0], 1.0)
                self.assertEqual(int(seg[1:].sum()), 0)
            else:
                self.assertEqual(seg[0], 1.0)
                self.assertEqual(int(seg[1:].sum()), 1)
                self.assertEqual(
                    seg[1 + M9_TALENT_SLOT_IDS.index(kind)], 1.0,
                )

    def test_no_talent_own_section_zero(self):
        obs = self._fill(None, ["T6"])
        block = obs[M9_TALENT_BLOCK_BASE:]
        self.assertEqual(block[0], 0.0)
        self.assertEqual(int(block[1:1 + 14].sum()), 0)
        # 对手区不受自身无天赋影响
        self.assertEqual(block[M9_TALENT_OWN_DIM], 1.0)

    def test_append_only_prefix_untouched(self):
        obs = self._fill("T4", ["G2", "G4", None, "T6", "T1"])
        self.assertEqual(float(np.abs(obs[:OBS_DIM_M9_V5_CORE]).sum()), 0.0)
        # 写入只发生在 [828, 918)
        self.assertGreater(float(np.abs(obs[OBS_DIM_M9_V5_CORE:]).sum()), 0.0)


class _StubEnv(gym.Env):
    """最小 env 桩：返回可预测的 obs 序列。"""

    metadata = {}

    def __init__(self, dim: int = 918) -> None:
        self.observation_space = spaces.Box(
            low=-1.0, high=1.0, shape=(dim,), dtype=np.float32,
        )
        self.action_space = spaces.Discrete(217)

    def reset(self, *, seed=None, options=None):
        self._t = 0
        return np.arange(self.observation_space.shape[0],
                         dtype=np.float32) / 1000.0, {}

    def step(self, action):
        self._t += 1
        obs = np.arange(self.observation_space.shape[0],
                        dtype=np.float32) / 1000.0
        return obs, 0.0, self._t >= 3, False, {}


class TestLegacyObsDimWrapper(unittest.TestCase):
    """截断 wrapper 行为。"""

    def test_truncates_and_narrows_space(self):
        wrapper = LegacyObsDimWrapper(_StubEnv(918), 828)
        self.assertEqual(wrapper.observation_space.shape, (828,))
        obs, _info = wrapper.reset(seed=1)
        self.assertEqual(obs.shape, (828,))
        reference = np.arange(918, dtype=np.float32) / 1000.0
        np.testing.assert_array_equal(obs, reference[:828])
        obs2, _r, term, _trunc, _info = wrapper.step(0)
        self.assertEqual(obs2.shape, (828,))
        np.testing.assert_array_equal(obs2, reference[:828])
        self.assertFalse(term)

    def test_rejects_oversized_target(self):
        with self.assertRaises(ValueError):
            LegacyObsDimWrapper(_StubEnv(828), 918)

    def test_rejects_multidim_obs(self):
        stub = _StubEnv()
        stub.observation_space = spaces.Box(
            low=-1.0, high=1.0, shape=(4, 918), dtype=np.float32,
        )
        with self.assertRaises(ValueError):
            LegacyObsDimWrapper(stub, 828)


if __name__ == "__main__":
    unittest.main()
