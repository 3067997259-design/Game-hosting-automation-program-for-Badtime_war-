"""tests/test_rl_m9_talent_drills.py
───────────────────────────────
P5 首批天赋课可行性探针（docs/m9/rl/m9_rl_next_steps.md §8.1）：

B 组零激活天赋（T6/G1/G6/G7）的每门新课，用"choose 通道贪心 oracle"
（T0 时选发动项；T6 直接点亮热线槽）证明：核心动作链在 RL 编码下走得通。

成功指标按机制定制（§7.3 度量伪影教训——G1 换装/G6/G7 即演在引擎层
没有专属事件，公演路径才有）：T6 = hotline 事件；G1 = form 离开卸甲
形态；G6/G7 = SP 1→0（dispatch_improvise 即演扣减）。
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

from rl.action_space import (  # noqa: E402
    IDX_SPECIAL_TARGET_M9_BASE,
    M9_TARGET_SLOTS,
    idx_to_choose_option,
    idx_to_command,
)
from rl.env import BadtimeWarEnv  # noqa: E402
from rl.obs_builder_m9 import _talent_slot_id  # noqa: E402

_MAX_STEPS = 24
_SLOT_OF_NUMBER = {6: "T6", 8: "G1", 13: "G6", 14: "G7"}


def _oracle_pick(env, mask, legal) -> int:
    """贪心：choose 模式选天赋发动/演出类选项；命令模式优先热线槽。"""
    if env._choose_mode:
        situation = str(
            (env._choose_context or {}).get("situation", ""),
        )
        best = None
        for idx in legal:
            option = str(idx_to_choose_option(
                int(idx), env._choose_options, situation,
                env._current_player, env._current_game_state,
            ))
            if any(key in option for key in (
                "发动", "即演", "换装", "宣言", "公演", "插入", "笑话",
                "表演", "演出",
            )):
                best = int(idx)
                break
        return best if best is not None else int(legal[0])
    for idx in legal:
        if IDX_SPECIAL_TARGET_M9_BASE <= idx < (
            IDX_SPECIAL_TARGET_M9_BASE + M9_TARGET_SLOTS
        ):
            try:
                cmd = idx_to_command(int(idx), env._rl_player, env._state)
            except Exception:
                continue
            if cmd.startswith("special 热线举报"):
                return int(idx)
    return int(legal[0])


def _run_drill(scenario: str, talent_number: int) -> dict:
    """跑一局天赋课（贪心 oracle）；返回事件/终态 SP/终态形态。"""
    env = BadtimeWarEnv(
        num_opponents=2, max_rounds=4, n_stack=1,
        enable_talents=True,
        rl_talent=talent_number, rl_talent_reserve=True,
        action_contract="talent_course", course_scenario=scenario,
    )
    try:
        _obs, _info = env.reset(seed=90_000 + talent_number)
        assert _talent_slot_id(
            getattr(env._rl_player, "talent", None),
        ) == _SLOT_OF_NUMBER[talent_number], "预留池未生效"
        fired: list[str] = []
        done = False
        for _ in range(_MAX_STEPS):
            mask = env.action_masks()
            legal = np.flatnonzero(mask)
            assert legal.size > 0
            action = _oracle_pick(env, mask, legal)
            _obs, _r, term, trunc, info = env.step(int(action))
            fired.extend(
                (info.get("reward_ledger") or {}).get("event_types") or (),
            )
            if term or trunc:
                break
        m9 = getattr(env._state, "m9_system", None)
        talent = getattr(env._rl_player, "talent", None)
        return {
            "fired": fired,
            "final_sp": int(m9.get_sp("rl_0")) if m9 else -1,
            "final_form": str(getattr(talent, "form", "")),
        }
    finally:
        env.close()


class TestP5FirstBatchDrills(unittest.TestCase):
    """四门 B 组天赋课：oracle 必须走通核心链（行为指标）。"""

    def test_g1_burn_chain_fires(self):
        result = _run_drill("talent_g1_burn", 8)
        # 换装无专属事件，但授予超新星是换装成功的专属副作用
        # （终态 form 不可靠：贪心后续轮可能又选免费"卸甲宣言"切回）。
        self.assertIn(
            "firefly_supernova_granted", result["fired"],
            f"G1 换装未生效（无超新星授予事件）；事件: "
            f"{sorted(set(result['fired']))}",
        )

    def test_g6_borrow_chain_fires(self):
        result = _run_drill("talent_g6_borrow", 13)
        self.assertEqual(
            result["final_sp"], 0,
            f"G6 即演未扣 SP（终态 SP={result['final_sp']}）；事件: "
            f"{sorted(set(result['fired']))}",
        )

    def test_g7_suppress_chain_fires(self):
        result = _run_drill("talent_g7_suppress", 14)
        self.assertEqual(
            result["final_sp"], 0,
            f"G7 即演未扣 SP（终态 SP={result['final_sp']}）；事件: "
            f"{sorted(set(result['fired']))}",
        )

    def test_t6_hotline_chain_fires(self):
        result = _run_drill("talent_t6_hotline", 6)
        self.assertIn(
            "hotline", result["fired"],
            f"T6 热线未触发；事件: {sorted(set(result['fired']))}",
        )


if __name__ == "__main__":
    unittest.main()
