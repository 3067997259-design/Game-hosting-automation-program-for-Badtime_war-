"""tests/test_rl_m9_active_victory.py
─────────────────────────────
主动胜利条款门禁（DOC-054 / docs/m9/rl/m9_rl_next_steps.md §9.3）：

league 训练实证避战拖分流的名次-only 捷径（80% 得分来自名次加成）。
训练终局奖励要求：常规胜利 且（击杀数最高 或 剧情分最高）才得满分；
空胜利打折（30 < 主动 100，仍高于败方名次梯度起点的 -20）。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("OMP_NUM_THREADS", "1")

from engine.experiments import set_profile

set_profile("m9-rfc")

from rl.reward_m9 import (  # noqa: E402
    ACTIVE_VICTORY_REWARD,
    PASSIVE_VICTORY_REWARD,
    M9RewardTracker,
    active_victory,
)


def _player(pid: str, kills: int = 0):
    return SimpleNamespace(player_id=pid, kill_count=kills, hp=1)


class _FakeScoring:
    def __init__(self, stories: dict[str, float]):
        self._stories = stories

    def _story(self, pid: str) -> float:
        return self._stories.get(pid, 0.0)


def _state(players, stories=None):
    return SimpleNamespace(
        player_order=[p.player_id for p in players],
        get_player=lambda pid: next(
            (p for p in players if p.player_id == pid), None,
        ),
        m9_scoring=_FakeScoring(stories or {}),
        game_over=True,
        game_winner_snapshot=None,
        base_final_scores=None,
    )


class TestActiveVictory(unittest.TestCase):
    """主动胜利判定。"""

    def test_kill_leader_counts(self):
        state = _state([
            _player("rl_0", kills=2), _player("ai_0", kills=0),
            _player("ai_1", kills=1),
        ])
        self.assertTrue(active_victory(state, "rl_0"))

    def test_tied_kill_leader_counts(self):
        state = _state([
            _player("rl_0", kills=1), _player("ai_0", kills=1),
        ])
        self.assertTrue(active_victory(state, "rl_0"))

    def test_zero_kill_tie_is_not_active(self):
        # 全员 0 杀的并列最高不算主动（必须 > 0）
        state = _state([
            _player("rl_0", kills=0), _player("ai_0", kills=0),
        ])
        self.assertFalse(active_victory(state, "rl_0"))

    def test_story_leader_counts(self):
        # 0 杀但剧情分最高（演出路线）
        state = _state(
            [_player("rl_0"), _player("ai_0")],
            stories={"rl_0": 4.0, "ai_0": 2.0},
        )
        self.assertTrue(active_victory(state, "rl_0"))

    def test_pure_turtle_is_not_active(self):
        # v22 病理：0 杀 0 剧情，纯名次分赢
        state = _state(
            [_player("rl_0"), _player("ai_0", kills=2),
             _player("ai_1", kills=1)],
            stories={},
        )
        self.assertFalse(active_victory(state, "rl_0"))


class TestTerminalReward(unittest.TestCase):
    """终局奖励分支。"""

    def _tracker(self):
        return M9RewardTracker("rl_0")

    def test_passive_win_discounted(self):
        tracker = self._tracker()
        state = _state([
            _player("rl_0"), _player("ai_0", kills=2),
        ])
        state.game_winner_snapshot = ["rl_0"]
        reward = tracker._terminal_reward(state, "rl_0")
        self.assertAlmostEqual(reward, PASSIVE_VICTORY_REWARD)
        self.assertLess(reward, ACTIVE_VICTORY_REWARD)

    def test_active_win_full_reward(self):
        tracker = self._tracker()
        state = _state([
            _player("rl_0", kills=2), _player("ai_0", kills=1),
        ])
        state.game_winner_snapshot = ["rl_0"]
        self.assertAlmostEqual(
            tracker._terminal_reward(state, "rl_0"), ACTIVE_VICTORY_REWARD,
        )

    def test_story_win_full_reward(self):
        tracker = self._tracker()
        state = _state(
            [_player("rl_0"), _player("ai_0")],
            stories={"rl_0": 4.0, "ai_0": 0.0},
        )
        state.game_winner_snapshot = ["rl_0"]
        self.assertAlmostEqual(
            tracker._terminal_reward(state, "rl_0"), ACTIVE_VICTORY_REWARD,
        )

    def test_loss_unchanged(self):
        tracker = self._tracker()
        state = _state([
            _player("rl_0"), _player("ai_0", kills=2),
        ])
        state.game_winner_snapshot = ["ai_0"]
        state.base_final_scores = {"rl_0": 3.0, "ai_0": 9.0}
        # 败方名次梯度（非胜者分支不受条款影响）
        reward = tracker._terminal_reward(state, "rl_0")
        self.assertLess(reward, 0.0)


if __name__ == "__main__":
    unittest.main()
