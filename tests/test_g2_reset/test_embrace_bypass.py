"""test_embrace_bypass.py — 相拥伤害穿透各天赋的临时HP吸收测试"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from talents.base_talent import BaseTalent


class _MockGameState:
    def __init__(self, player):
        self._player = player

    def get_player(self, pid):
        return self._player


def _make_g1(charges=2):
    """构造 G1 Firefly 天赋 mock（仅测试 receive_damage_to_temp_hp）。"""
    from talents.g1_firefly import G1MythFire
    gs = _MockGameState(SimpleNamespace(name="Firefly"))
    g1 = G1MythFire.__new__(G1MythFire)  # bypass __init__
    g1.player_id = "p1"
    g1.state = gs
    g1.ardent_wish_charges = charges
    return g1


def _make_g7(halos_active=3, terror=False, terror_hp=0.0, perm_hp=0.0):
    """构造 G7 Hoshino 天赋 mock。"""
    from talents.g7.hoshino import Hoshino
    gs = _MockGameState(SimpleNamespace(name="Hoshino"))
    g7 = Hoshino.__new__(Hoshino)
    g7.player_id = "p2"
    g7.state = gs
    g7.is_terror = terror
    g7.terror_extra_hp = terror_hp
    g7.permanent_extra_hp = perm_hp
    g7.halos = [{"active": i < halos_active, "cooldown_remaining": 0, "recovering": False}
                for i in range(3)]
    g7._halo_consume_one = lambda: None  # no-op for test
    return g7


class TestG1EmbraceBypass(unittest.TestCase):
    """G1 炽愿：embrace 绕过"""

    def test_normal_damage_absorbed(self):
        g1 = _make_g1(charges=2)  # 2 charges = 1.0 HP buffer
        remaining = g1.receive_damage_to_temp_hp(0.8, is_embrace=False)
        self.assertAlmostEqual(remaining, 0.0)  # 0.8 ≤ 1.0 → fully absorbed

    def test_embrace_bypasses_ardent_wish(self):
        g1 = _make_g1(charges=3)  # 3 charges = 1.5 HP buffer
        remaining = g1.receive_damage_to_temp_hp(1.0, is_embrace=True)
        self.assertAlmostEqual(remaining, 1.0)  # no absorption

    def test_embrace_does_not_consume_charges(self):
        g1 = _make_g1(charges=2)
        g1.receive_damage_to_temp_hp(1.0, is_embrace=True)
        self.assertEqual(g1.ardent_wish_charges, 2)  # unchanged


class TestG7EmbraceBypass(unittest.TestCase):
    """G7 星野：embrace 跳过 Terror/perm HP，但保留光环"""

    def test_embrace_skips_terror_hp(self):
        g7 = _make_g7(halos_active=0, terror=True, terror_hp=2.0)
        remaining = g7.receive_damage_to_temp_hp(1.0, is_embrace=True)
        self.assertAlmostEqual(remaining, 1.0)  # terror_hp bypassed, no halos

    def test_embrace_skips_permanent_hp(self):
        g7 = _make_g7(halos_active=0, perm_hp=1.0)
        remaining = g7.receive_damage_to_temp_hp(0.8, is_embrace=True)
        self.assertAlmostEqual(remaining, 0.8)  # perm_hp bypassed

    def test_embrace_preserves_halo_absorption(self):
        g7 = _make_g7(halos_active=2, terror=True, terror_hp=3.0)
        g7._halo_consume_one = MagicMock()
        remaining = g7.receive_damage_to_temp_hp(1.0, is_embrace=True)
        # 2 halos × 0.5 each = 1.0 → fully absorbed
        self.assertAlmostEqual(remaining, 0.0)
        self.assertEqual(g7._halo_consume_one.call_count, 2)

    def test_normal_mode_all_layers(self):
        g7 = _make_g7(halos_active=2, perm_hp=0.5)
        g7._halo_consume_one = MagicMock()
        remaining = g7.receive_damage_to_temp_hp(1.5, is_embrace=False)
        # perm_hp 0.5 → remaining 1.0 → 2 halos × 0.5 → 0
        self.assertAlmostEqual(remaining, 0.0)


class TestBaseTalentNoop(unittest.TestCase):
    """BaseTalent 默认 no-op"""

    def test_base_talent_returns_damage(self):
        bt = BaseTalent("p_x", SimpleNamespace())
        self.assertAlmostEqual(bt.receive_damage_to_temp_hp(2.0), 2.0)
        self.assertAlmostEqual(bt.receive_damage_to_temp_hp(2.0, is_embrace=True), 2.0)


if __name__ == "__main__":
    unittest.main()
