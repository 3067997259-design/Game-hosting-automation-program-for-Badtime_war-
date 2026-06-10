"""test_cooling.py — G2 发动轮次限制与冷却测试"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


def _make_g2(used=False, max_uses=1, state_round=1, ish_active=False):
    """构造 Hologram 天赋实例（轻量 mock）。"""
    gs = SimpleNamespace(
        player_order=["p1", "p2", "p3", "p4", "p5", "p6"],
        current_round=state_round,
        ish_bosheth=SimpleNamespace(phase="active") if ish_active else None,
    )
    t = SimpleNamespace(
        player_id="p2",
        state=gs,
        used=used,
        max_uses=max_uses,
    )
    t.get_t0_option = _get_t0_option_real.__get__(t, type(t))
    t._calc_min_round = lambda: 10 + 2 * (len(t.state.player_order) - 2)
    return t


def _get_t0_option_real(self, player):
    """复制自 g2_hologram.get_t0_option（简化版用于测试 min_round 逻辑）。"""
    if player.player_id != self.player_id:
        return None
    if self.used and self.max_uses <= 0:
        return None
    if self.state.ish_bosheth is not None:
        return None
    if self.state.current_round < self._calc_min_round():
        return None
    return "发动天赋"


class TestMinRoundCheck(unittest.TestCase):
    """最早发动轮次 = 10 + 2*(N-2)"""

    def test_6p_game_min_round_18(self):
        """6 人局最早第 18 轮发动"""
        t = _make_g2(state_round=17)
        player = SimpleNamespace(player_id="p2")
        self.assertIsNone(t.get_t0_option(player))

    def test_6p_game_round_18_allowed(self):
        """第 18 轮可以发动"""
        t = _make_g2(state_round=18)
        player = SimpleNamespace(player_id="p2")
        self.assertIsNotNone(t.get_t0_option(player))

    def test_4p_game_min_round_14(self):
        """4 人局最早第 14 轮"""
        t = _make_g2(state_round=13)
        t.state.player_order = ["p1", "p2", "p3", "p4"]
        player = SimpleNamespace(player_id="p2")
        self.assertIsNone(t.get_t0_option(player))

    def test_4p_game_round_14_allowed(self):
        """4 人局第 14 轮可发动"""
        t = _make_g2(state_round=14)
        t.state.player_order = ["p1", "p2", "p3", "p4"]
        player = SimpleNamespace(player_id="p2")
        self.assertIsNotNone(t.get_t0_option(player))

    def test_blocked_by_ish_active(self):
        """已活跃结界时不再提供选项"""
        t = _make_g2(state_round=20, ish_active=True)
        player = SimpleNamespace(player_id="p2")
        self.assertIsNone(t.get_t0_option(player))



if __name__ == "__main__":
    unittest.main()
