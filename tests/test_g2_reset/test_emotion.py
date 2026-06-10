"""test_emotion.py — 情绪系统测试"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from engine.ish_bosheth import (
    ACCAREZZEVOLE, INDIFFERENZA, STRAPPANDO,
    EMOTION_ORDER,
    adjust_emotion_up, adjust_emotion_down, trigger_snap,
    IshBosheth,
)


def _unit(emotion, hp=3.0, is_chorus=False):
    u = SimpleNamespace(
        emotion=emotion, hp=hp, is_chorus=is_chorus,
        stage_statuses=set(), name="TestUnit",
    )
    u.is_alive = lambda: u.hp > 0
    return u


class TestEmotionOrder(unittest.TestCase):
    """情绪顺序常量"""

    def test_order(self):
        self.assertEqual(EMOTION_ORDER, [STRAPPANDO, INDIFFERENZA, ACCAREZZEVOLE])


class TestAdjustEmotionUp(unittest.TestCase):
    """情绪上调"""

    @patch("engine.ish_bosheth.display")
    def test_strappando_to_indifferenza(self, _disp):
        u = _unit(STRAPPANDO)
        adjust_emotion_up(u)
        self.assertEqual(u.emotion, INDIFFERENZA)

    @patch("engine.ish_bosheth.display")
    def test_indifferenza_to_accarezzevole(self, _disp):
        u = _unit(INDIFFERENZA)
        adjust_emotion_up(u)
        self.assertEqual(u.emotion, ACCAREZZEVOLE)

    @patch("engine.ish_bosheth.display")
    def test_accarezzevole_stays(self, _disp):
        u = _unit(ACCAREZZEVOLE)
        adjust_emotion_up(u)
        self.assertEqual(u.emotion, ACCAREZZEVOLE)


class TestAdjustEmotionDown(unittest.TestCase):
    """情绪下调"""

    @patch("engine.ish_bosheth.display")
    def test_accarezzevole_to_indifferenza(self, _disp):
        u = _unit(ACCAREZZEVOLE)
        adjust_emotion_down(u)
        self.assertEqual(u.emotion, INDIFFERENZA)

    @patch("engine.ish_bosheth.display")
    def test_indifferenza_to_strappando(self, _disp):
        u = _unit(INDIFFERENZA)
        adjust_emotion_down(u)
        self.assertEqual(u.emotion, STRAPPANDO)

    @patch("engine.ish_bosheth.display")
    def test_strappando_triggers_snap(self, _disp):
        u = _unit(STRAPPANDO, hp=3.0)
        adjust_emotion_down(u)
        # snap should deal 0.5 damage
        self.assertAlmostEqual(u.hp, 2.5)
        self.assertIn("imbalance", u.stage_statuses)


class TestTriggerSnap(unittest.TestCase):
    """断弦"""

    @patch("engine.ish_bosheth.display")
    def test_snap_damage_and_imbalance(self, _disp):
        u = _unit(STRAPPANDO, hp=2.0)
        trigger_snap(u)
        self.assertAlmostEqual(u.hp, 1.5)
        self.assertIn("imbalance", u.stage_statuses)


class TestMaNonTroppo(unittest.TestCase):
    """ma non troppo 情绪校正"""

    @patch("engine.ish_bosheth.display")
    def test_three_same_emotion_corrected(self, _disp):
        """3 个真实观众同情绪 → 至少一人被改变"""
        ish = IshBosheth("g2")
        players = []
        for i in range(3):
            p = _unit(ACCAREZZEVOLE)
            p.player_id = f"p{i}"
            p.stage_statuses.add("liberamente_vivace")
            players.append(p)

        ish.participants = {p.player_id for p in players}
        gs = SimpleNamespace(ish_bosheth=ish)
        _map = {p.player_id: p for p in players}
        gs.get_player = lambda pid: _map.get(pid)

        ish.ma_non_troppo(gs)

        emotions = {p.emotion for p in players}
        # At least 2 different emotions should be present now
        self.assertGreaterEqual(len(emotions), 2)


if __name__ == "__main__":
    unittest.main()
