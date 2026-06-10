"""test_lifecycle.py — ish-bosheth 生命周期测试"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from engine.ish_bosheth import (
    IshBosheth, ACCAREZZEVOLE, INDIFFERENZA, STRAPPANDO,
)


def _make_player(pid, name="P", hp=3.0, location="商店", alive=True,
                 emotion=None, talent=None, **kw):
    p = SimpleNamespace(
        player_id=pid, name=name, hp=hp, max_hp=5.0,
        location=location, is_awake=True,
        is_invisible=False, is_stunned=False,
        emotion=emotion,
        stage_statuses=set(),
        encore_layers=0,
        stage_entangle=[],
        temp_hp_g2=0.0,
        temp_atk_g2=0.0,
        talent=talent,
        controller=MagicMock(),
        _g2_curtain_d4_bonus=False,
        **kw,
    )
    p.is_alive = lambda: p.hp > 0 and alive
    return p


def _make_game_state(players, ish=None):
    gs = SimpleNamespace(
        player_order=[p.player_id for p in players],
        ish_bosheth=ish,
        active_barrier=None,
        markers=MagicMock(),
        virus=SimpleNamespace(is_active=False),
    )
    _player_map = {p.player_id: p for p in players}
    gs.get_player = lambda pid: _player_map.get(pid)
    gs.get_all_alive_players = lambda: [p for p in players if p.is_alive()]
    return gs


class TestIshBoshethInit(unittest.TestCase):
    """IshBosheth 初始化与基本属性"""

    def test_initial_state(self):
        ish = IshBosheth("g2_owner")
        self.assertEqual(ish.g2_owner_id, "g2_owner")
        self.assertEqual(ish.g2_home, "home_g2_owner")
        self.assertEqual(ish.phase, "active")
        self.assertEqual(ish.regard, 0.0)
        self.assertEqual(ish.regard_cap, 8.0)
        self.assertEqual(ish.r4_count, 0)
        self.assertEqual(ish.cumulative_delta_regard, 0.0)
        self.assertFalse(ish.melody_1_used)
        self.assertFalse(ish.melody_2_used)
        self.assertFalse(ish.melody_3_used)


class TestR4Hook(unittest.TestCase):
    """R4 衰减 & Regard 变化"""

    def _setup_ish(self, n_acc=1, n_str=1, n_ind=1):
        emotions = ([ACCAREZZEVOLE] * n_acc +
                    [STRAPPANDO] * n_str +
                    [INDIFFERENZA] * n_ind)
        players = [_make_player("g2", "G2", talent=MagicMock())]
        for i, emo in enumerate(emotions):
            players.append(_make_player(f"p{i}", f"Player{i}", emotion=emo))

        ish = IshBosheth("g2")
        ish.regard = 6.0
        for p in players[1:]:
            ish.participants.add(p.player_id)
            p.stage_statuses.add("liberamente_vivace")
        gs = _make_game_state(players, ish)
        gs.ish_bosheth = ish
        return ish, gs, players

    @patch("engine.ish_bosheth.display")
    def test_r4_regard_decay(self, _disp):
        # v0.7: Acc 中性, Ind +1.0, Str -0.5. 1/1/1 = -1 +1 -0.5 = -0.5 net
        ish, gs, _ = self._setup_ish(n_acc=1, n_str=1, n_ind=1)
        initial = ish.regard
        ish.on_r4(gs)
        self.assertAlmostEqual(ish.regard, initial - 0.5)
        self.assertEqual(ish.r4_count, 1)

    @patch("engine.ish_bosheth.display")
    def test_r4_cumulative_delta_unlock(self, _disp):
        # v0.7: 累计 ΔRegard 解锁。每轮 net=-0.5 → |Δ|=0.5
        ish, gs, _ = self._setup_ish(n_acc=1, n_str=1, n_ind=1)
        ish.regard = ish.regard_cap  # 8.0，正常起始
        for _ in range(6):
            ish.on_r4(gs)
        self.assertGreaterEqual(ish.cumulative_delta_regard, 3.0)
        songs = ish.get_available_songs()
        song_names = [s['name'] for s in songs]
        self.assertIn("旋律·第一音节", song_names)
        self.assertNotIn("旋律·第二间章", song_names)

    @patch("engine.ish_bosheth.display")
    def test_r4_regard_zero_pending_curtain(self, _disp):
        # v0.7: Ind 翻倍 → net=-0.5. regard=0.5 → 0.0 after one round
        ish, gs, _ = self._setup_ish(n_acc=1, n_str=1, n_ind=1)
        ish.regard = 0.5
        ish.on_r4(gs)
        self.assertEqual(ish.phase, "pending_curtain")

    @patch("engine.ish_bosheth.display")
    def test_r4_max_duration(self, _disp):
        ish, gs, _ = self._setup_ish()
        ish.regard = 100.0  # prevent regard exhaustion
        for _ in range(8):
            ish.on_r4(gs)
        self.assertEqual(ish.phase, "pending_curtain")


class TestEndIshBosheth(unittest.TestCase):
    """end_ish_bosheth 清理"""

    @patch("engine.ish_bosheth.display")
    def test_end_clears_player_state(self, _disp):
        p1 = _make_player("p1", "P1", emotion=ACCAREZZEVOLE)
        p1.stage_statuses.add("liberamente_vivace")
        p1.encore_layers = 2
        g2 = _make_player("g2", "G2")
        g2.stage_statuses.add("liberamente_vivace")

        ish = IshBosheth("g2")
        ish.participants = {"p1"}
        gs = _make_game_state([g2, p1], ish)
        gs.ish_bosheth = ish

        ish.end_ish_bosheth("curtain", gs)

        self.assertIsNone(p1.emotion)
        self.assertEqual(len(p1.stage_statuses), 0)
        self.assertEqual(p1.encore_layers, 0)
        self.assertEqual(ish.phase, "ended")
        self.assertIsNone(gs.ish_bosheth)

    @patch("engine.ish_bosheth.display")
    def test_end_break_gives_d4_bonus(self, _disp):
        breaker = _make_player("p1", "Breaker", emotion=STRAPPANDO)
        g2 = _make_player("g2", "G2")
        ish = IshBosheth("g2")
        ish.participants = {"p1"}
        gs = _make_game_state([g2, breaker], ish)
        gs.ish_bosheth = ish

        ish.end_ish_bosheth("break", gs, breaker_id="p1")
        self.assertTrue(breaker._g2_curtain_d4_bonus)


if __name__ == "__main__":
    unittest.main()
