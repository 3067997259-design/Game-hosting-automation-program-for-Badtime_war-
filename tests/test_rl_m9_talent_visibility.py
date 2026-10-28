"""tests/test_rl_m9_talent_visibility.py
─────────────────────────────────────
P2 防失明天赋扩展（docs/m9/rl/m9_rl_next_steps.md §5）：

对全部 14 个 M9 槽位逐一跑真实 env 短局（指定该天赋），验证：
1. obs 维度 = OBS_DIM_M9_TOTAL（918）且全程有限（无 NaN）；
2. 天赋块端到端正确：env._rl_player 的天赋槽位与 one-hot 位置一致；
3. 掩码始终存在非 forfeit 合法动作（该天赋下 RL "有手可出"）；
4. 逐槽运行无异常——任何天赋独有分支炸 obs/mask 在此现形。

special/choose 的静态防失明由 tests/test_rl_m9_special_visibility.py 与
tests/test_rl_m9_choose_visibility.py 承担；本测试补"逐天赋运行时"维度。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import numpy as np

from engine.experiments import set_profile

set_profile("m9-rfc")

from engine.m9.talent_registry import active_registrations  # noqa: E402
from rl.action_space import IDX_FORFEIT  # noqa: E402
from rl.obs_builder_m9 import (  # noqa: E402
    M9_TALENT_BLOCK_BASE,
    M9_TALENT_OWN_DIM,
    M9_TALENT_SLOT_IDS,
    OBS_DIM_M9_TOTAL,
    _talent_slot_id,
)

_MAX_STEPS = 10


def _run_slot(reg, max_attempts: int = 6) -> dict:
    """跑一个槽位的探针局。

    注意：env 的 _assign_rl_talent 在编号被对手先选走时会静默跳过
    （天赋池互斥），因此用多个种子重试直至 RL 真正拿到指定槽位；
    连续失败即说明分配语义漂移，应当红。
    """
    from rl.env import BadtimeWarEnv

    env = BadtimeWarEnv(
        num_opponents=1,
        max_rounds=6,
        n_stack=1,
        enable_talents=True,
        rl_talent=reg.legacy_number,
    )
    try:
        obs = None
        for attempt in range(max_attempts):
            obs, _info = env.reset(
                seed=90210 + int(reg.legacy_number) * 101 + attempt * 7919,
            )
            own_slot = _talent_slot_id(
                getattr(env._rl_player, "talent", None),
            )
            if own_slot == reg.slot_id:
                break
        else:
            raise AssertionError(
                f"{reg.slot_id} 经 {max_attempts} 个种子仍未分配到 RL"
                f"（最后一次解析={own_slot!r}）——分配语义疑似漂移",
            )
        assert obs is not None and obs.shape == (OBS_DIM_M9_TOTAL,), (
            obs.shape if obs is not None else None,
        )
        block = obs[M9_TALENT_BLOCK_BASE:]
        onehot = block[1:M9_TALENT_OWN_DIM]
        assert onehot[M9_TALENT_SLOT_IDS.index(own_slot)] == 1.0
        assert int(onehot.sum()) == 1

        non_forfeit_seen = False
        for step in range(_MAX_STEPS):
            mask = env.action_masks()
            legal = np.flatnonzero(mask)
            assert legal.size > 0
            non_forfeit = legal[legal != IDX_FORFEIT]
            if non_forfeit.size:
                non_forfeit_seen = True
            action = int(non_forfeit[step % non_forfeit.size]) \
                if non_forfeit.size else int(legal[0])
            obs, _reward, term, trunc, _info = env.step(action)
            assert obs.shape == (OBS_DIM_M9_TOTAL,), obs.shape
            assert bool(np.all(np.isfinite(obs))), \
                f"{reg.slot_id} 第 {step} 步观测出现非有限值"
            if term or trunc:
                break
        return {"slot": reg.slot_id, "non_forfeit_seen": non_forfeit_seen}
    finally:
        env.close()


class TestPerTalentVisibility(unittest.TestCase):
    """14 槽逐一运行时探针。"""

    def test_every_active_slot_runs_with_visible_actions(self):
        results = {}
        for reg in active_registrations():
            if reg.legacy_number is None:
                self.fail(f"{reg.slot_id} 缺 legacy_number，无法逐槽探针")
            results[reg.slot_id] = _run_slot(reg)
        self.assertEqual(len(results), 14)
        blind = [slot for slot, r in results.items() if not r["non_forfeit_seen"]]
        self.assertEqual(
            blind, [],
            f"以下天赋在探针局内从未出现非 forfeit 合法动作：{blind}",
        )


if __name__ == "__main__":
    unittest.main()
