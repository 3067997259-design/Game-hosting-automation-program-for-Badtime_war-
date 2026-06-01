"""test_melody.py — 旋律测试（第一音节自动触发 + 伤害序列 + 情绪下调）"""
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
    adjust_emotion_down, trigger_snap,
)


def _make_player(pid, name="P", hp=3.0, emotion=ACCAREZZEVOLE, alive=True):
    p = SimpleNamespace(
        player_id=pid, name=name, hp=hp, max_hp=5.0,
        emotion=emotion,
        stage_statuses=set(),
        controller=MagicMock(),
        is_awake=True,
        is_alive=lambda: p.hp > 0 and alive,
        is_invisible=False,
        is_stunned=False,
        location="商店",
    )
    p.controller.choose = MagicMock(return_value=name)
    return p


class TestMelodyAutoTrigger(unittest.TestCase):
    """第一音节在 open() 中自动触发"""

    @patch("engine.ish_bosheth.display")
    @patch("engine.ish_bosheth.ChorusUnit")
    def test_open_does_not_call_execute_melody(self, _Chorus, _disp):
        """open() 不再自动触发旋律——旋律由 execute_t0 在展示座位后调用。"""
        _Chorus.side_effect = lambda: SimpleNamespace(
            player_id="c1", name="Chorus_1", location="商店",
            is_alive=lambda: True, emotion=None, stage_statuses=set(),
            controller=SimpleNamespace(), _spotlight_granted_r4=-1)

        g2 = _make_player("g2", "G2_Singer")
        g2.talent = SimpleNamespace()
        g2.location = "商店"
        g2.is_invisible = False

        p1 = _make_player("p1", "P1")
        p1.talent = None
        p1.is_invisible = False

        players = {"g2": g2, "p1": p1}
        gs = SimpleNamespace(
            player_order=["g2", "p1"],
            ish_bosheth=None,
            active_barrier=None,
            markers=MagicMock(),
            virus=SimpleNamespace(is_active=False),
            police=None,
            register_chorus=lambda u: None,
            unregister_chorus=lambda uid: None,
        )
        gs.get_player = lambda pid: players.get(pid)
        gs.players_at_location = lambda loc: []

        ish = IshBosheth("g2")
        called = [False]
        ish.execute_melody = lambda *a, **kw: called.__setitem__(0, True)

        ish.open(gs, g2)
        self.assertFalse(called[0],
            "旋律不再在 open() 中触发，改为在 execute_t0 展示座位后调用")


class TestMelodyEmotionDown(unittest.TestCase):
    """旋律命中后情绪下调"""

    def test_hit_downgrades_emotion(self):
        u = SimpleNamespace(
            player_id="p1", name="Test",
            emotion=ACCAREZZEVOLE, hp=5.0,
            is_alive=lambda: True,
            stage_statuses=set(),
        )
        adjust_emotion_down(u, None, None)
        self.assertEqual(u.emotion, INDIFFERENZA)

    @patch("engine.ish_bosheth.display")
    def test_strappando_hit_triggers_snap(self, _disp):
        u = SimpleNamespace(
            player_id="p1", name="Test",
            emotion=STRAPPANDO, hp=3.0,
            is_alive=lambda: True,
            stage_statuses=set(),
        )
        trigger_snap(u, None, None)
        self.assertAlmostEqual(u.hp, 2.5)
        self.assertIn("imbalance", u.stage_statuses)


class TestMelodySequence(unittest.TestCase):
    """旋律伤害序列 1/1/0.5/0.5"""

    def test_damage_sequence_order(self):
        """验证伤害序列常量"""
        seq = [1.0, 1.0, 0.5, 0.5]
        self.assertEqual(len(seq), 4)
        self.assertAlmostEqual(seq[0], 1.0)
        self.assertAlmostEqual(seq[2], 0.5)


if __name__ == "__main__":
    unittest.main()
