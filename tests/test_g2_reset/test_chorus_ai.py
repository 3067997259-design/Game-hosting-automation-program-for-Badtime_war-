"""test_chorus_ai.py — Chorus 生成 + D4 参与 + 情绪过滤攻击"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from engine.ish_bosheth import ACCAREZZEVOLE, INDIFFERENZA, STRAPPANDO
from models.chorus import ChorusUnit


class TestChorusCreation(unittest.TestCase):
    """Chorus 单位创建"""

    def test_chorus_has_equipment(self):
        c = ChorusUnit()
        self.assertTrue(len(c.weapons) >= 1)
        self.assertEqual(len(c.armor.outer), 1)
        self.assertEqual(len(c.armor.inner), 1)
        self.assertTrue(c.is_alive())

    def test_chorus_has_compat_fields(self):
        c = ChorusUnit()
        self.assertTrue(c.is_chorus)
        self.assertIsNone(c.talent)
        self.assertIsNone(c.controller)
        self.assertFalse(c.is_police)
        self.assertEqual(c.vouchers, 0)

    def test_chorus_ids_are_unique(self):
        c1 = ChorusUnit()
        c2 = ChorusUnit()
        self.assertNotEqual(c1.player_id, c2.player_id)


class TestChorusTargetFiltering(unittest.TestCase):
    """Chorus 攻击目标情绪过滤"""

    def _make_targets(self):
        # v0.6: 声部必须正确设置
        g2 = SimpleNamespace(player_id="g2", name="G2", hp=3.0, max_hp=5.0,
                             is_alive=lambda: True, is_on_map=lambda: True, emotion=None)
        p1 = SimpleNamespace(player_id="p1", name="P1", hp=3.0, max_hp=5.0,
                             is_alive=lambda: True, is_on_map=lambda: True,
                             emotion=ACCAREZZEVOLE)  # Acc → Str 的目标
        p2 = SimpleNamespace(player_id="p2", name="P2", hp=3.0, max_hp=5.0,
                             is_alive=lambda: True, is_on_map=lambda: True,
                             emotion=STRAPPANDO)     # Str → Acc 的目标
        ish = SimpleNamespace(
            g2_owner_id="g2", chorus_list=[],
            participants={"p1", "p2"},
        )
        gs = SimpleNamespace(
            player_order=["g2", "p1", "p2"],
            ish_bosheth=ish,
        )
        gs.get_player = lambda pid: {"g2": g2, "p1": p1, "p2": p2}.get(pid)
        return gs, ish

    def test_strappando_chorus_only_targets_g2_and_acc(self):
        """v0.6 Str: 攻击 Acc 和 G2"""
        gs, ish = self._make_targets()
        from controllers.chorus_controller import ChorusController
        cc = ChorusController()
        chorus = SimpleNamespace(player_id="c1", emotion=STRAPPANDO)
        targets = cc._get_legal_targets(gs, chorus, ish)
        target_ids = [t.player_id for t in targets]
        self.assertIn("g2", target_ids)
        self.assertIn("p1", target_ids)   # p1 is Acc → valid target
        self.assertNotIn("p2", target_ids)  # p2 is Str → same voice

    def test_accarezzevole_chorus_excludes_g2(self):
        """v0.6 Acc: 攻击 Str，不攻击 G2"""
        gs, ish = self._make_targets()
        from controllers.chorus_controller import ChorusController
        cc = ChorusController()
        chorus = SimpleNamespace(player_id="c1", emotion=ACCAREZZEVOLE)
        targets = cc._get_legal_targets(gs, chorus, ish)
        target_ids = [t.player_id for t in targets]
        self.assertNotIn("g2", target_ids)
        self.assertIn("p2", target_ids)   # p2 is Str → valid target


class TestChorusControllerRandom(unittest.TestCase):
    """ChorusController 随机选择"""

    def test_choose_random_from_options(self):
        from controllers.chorus_controller import ChorusController
        cc = ChorusController()
        options = ["A", "B", "C"]
        for _ in range(10):
            result = cc.choose("test", options)
            self.assertIn(result, options)

    def test_confirm_always_false(self):
        from controllers.chorus_controller import ChorusController
        cc = ChorusController()
        self.assertFalse(cc.confirm("test"))


if __name__ == "__main__":
    unittest.main()
