"""M0a 确定性测试：同种子重跑结果逐局全等。

golden 回放（M0c）的前提条件。游戏逻辑使用全局 random 且 stats_runner 串行跑局，
固定种子后单进程内结果必须完全可复现。若本测试失败，说明引擎/AI 引入了
random 之外的非确定性来源（时间、id()、未固定的外部状态等）——必须先修复。
"""
import random
import unittest
from typing import Any, Dict, List

import stats_runner


def _fingerprint(result: Dict[str, Any]) -> Dict[str, Any]:
    """从单局结果提取可比对指纹（裁剪掉 traceback 等自由文本）。"""
    return {
        "winner_pid": result.get("winner_pid"),
        "rounds": result.get("rounds"),
        "draw": result.get("draw"),
        "draw_reason": result.get("draw_reason"),
        "talents": sorted(result.get("talent_nums_picked", [])),
        "players": [
            {
                "pid": p["pid"],
                "talent_num": p["talent_num"],
                "alive": p["alive"],
                "kill_count": p["kill_count"],
                "is_winner": p["is_winner"],
            }
            for p in result.get("players", [])
        ],
    }


def _run_seeded(seed: int, num_players: int = 4) -> Dict[str, Any]:
    """以固定种子跑一局并返回指纹。"""
    random.seed(seed)
    result = stats_runner.run_single_game(num_players)
    return _fingerprint(result)


class DeterminismTest(unittest.TestCase):
    """同种子两遍逐局全等。"""

    @classmethod
    def setUpClass(cls) -> None:
        # run_single_game 不静音输出（静音由 run_batch 负责），测试里手动静音
        stats_runner._silence_display()
        stats_runner._silence_prompt_manager()

    @classmethod
    def tearDownClass(cls) -> None:
        stats_runner._restore_display()
        stats_runner._restore_prompt_manager()

    def test_same_seed_same_result(self) -> None:
        seeds = [42, 1337]
        first_pass: List[Dict[str, Any]] = [_run_seeded(s) for s in seeds]
        second_pass: List[Dict[str, Any]] = [_run_seeded(s) for s in seeds]
        for seed, a, b in zip(seeds, first_pass, second_pass):
            with self.subTest(seed=seed):
                self.assertEqual(a, b, f"种子 {seed} 两遍结果不一致")

    def test_different_seed_usually_differs(self) -> None:
        """反向冒烟：不同种子应当（几乎必然）产生不同对局，防 seed 形同虚设。"""
        a = _run_seeded(1)
        b = _run_seeded(2)
        self.assertNotEqual(a, b, "种子 1 与 2 结果完全相同——seed 可能没有生效")


if __name__ == "__main__":
    unittest.main()
