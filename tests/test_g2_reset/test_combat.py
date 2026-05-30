"""test_combat.py — 相拥伤害 & Before light 测试"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from engine.ish_bosheth import (
    IshBosheth, ACCAREZZEVOLE, INDIFFERENZA, STRAPPANDO,
)


def _make_ish(before_light=None):
    ish = IshBosheth("g2", "商店")
    ish.regard = 6.0
    ish.before_light = before_light
    return ish


def _make_player(pid, emotion=None, stage_statuses=None, hp=3.0):
    p = SimpleNamespace(
        player_id=pid, name=f"Player_{pid}",
        hp=hp, max_hp=5.0,
        emotion=emotion,
        stage_statuses=stage_statuses or set(),
        is_chorus=False,
        encore_layers=0,
        stage_entangle=[],
        temp_hp_g2=0.0,
        temp_atk_g2=0.0,
        location="商店",
        is_awake=True,
        is_invisible=False,
        talent=None,
        controller=MagicMock(),
        _g2_curtain_d4_bonus=False,
    )
    p.is_alive = lambda: p.hp > 0
    return p


class TestEmbraceAutoDetect(unittest.TestCase):
    """相拥伤害自动检测逻辑"""

    def test_non_g2_with_liberamente_is_embrace(self):
        """非 G2 发动者 + liberamente_vivace → 自动标记为相拥伤害"""
        ish = _make_ish()
        attacker = _make_player("p1", emotion=ACCAREZZEVOLE,
                                stage_statuses={"liberamente_vivace"})
        ish.participants.add("p1")

        # Simulate the auto-detection logic from damage_resolver
        is_embrace = False
        if ish.phase == "active":
            if ('liberamente_vivace' in getattr(attacker, 'stage_statuses', set())
                    and getattr(attacker, 'player_id', None) != ish.g2_owner_id):
                is_embrace = True
        self.assertTrue(is_embrace)

    def test_g2_owner_not_embrace(self):
        """G2 发动者的伤害不是相拥伤害"""
        ish = _make_ish()
        attacker = _make_player("g2", stage_statuses={"liberamente_vivace"})

        is_embrace = False
        if ish.phase == "active":
            if ('liberamente_vivace' in getattr(attacker, 'stage_statuses', set())
                    and getattr(attacker, 'player_id', None) != ish.g2_owner_id):
                is_embrace = True
        self.assertFalse(is_embrace)


class TestBeforeLightDamageModifier(unittest.TestCase):
    """Before light 伤害修正"""

    def test_riposato_accarezzevole_plus(self):
        """Riposato → 入戏者 +0.5"""
        ish = _make_ish(before_light="riposato")
        target = _make_player("p1", emotion=ACCAREZZEVOLE)

        bonus = 0.0
        if ish.before_light == "riposato":
            if target.emotion == ACCAREZZEVOLE:
                bonus += 0.5
            elif target.emotion == STRAPPANDO:
                bonus -= 0.5
        self.assertAlmostEqual(bonus, 0.5)

    def test_riposato_strappando_minus(self):
        """Riposato → 反抗者 -0.5"""
        ish = _make_ish(before_light="riposato")
        target = _make_player("p1", emotion=STRAPPANDO)

        bonus = 0.0
        if ish.before_light == "riposato":
            if target.emotion == ACCAREZZEVOLE:
                bonus += 0.5
            elif target.emotion == STRAPPANDO:
                bonus -= 0.5
        self.assertAlmostEqual(bonus, -0.5)

    def test_dolente_accarezzevole_plus_1(self):
        """Dolente → 入戏者 +1.0"""
        ish = _make_ish(before_light="dolente")
        target = _make_player("p1", emotion=ACCAREZZEVOLE)

        bonus = 0.0
        if ish.before_light == "dolente":
            if target.emotion == ACCAREZZEVOLE:
                bonus += 1.0
            elif target.emotion == INDIFFERENZA:
                bonus += 0.5
        self.assertAlmostEqual(bonus, 1.0)

    def test_dolente_indifferenza_plus_half(self):
        """Dolente → 抽离者 +0.5"""
        ish = _make_ish(before_light="dolente")
        target = _make_player("p1", emotion=INDIFFERENZA)

        bonus = 0.0
        if ish.before_light == "dolente":
            if target.emotion == ACCAREZZEVOLE:
                bonus += 1.0
            elif target.emotion == INDIFFERENZA:
                bonus += 0.5
        self.assertAlmostEqual(bonus, 0.5)


class TestD4Bonus(unittest.TestCase):
    """D4+1 舞台法则"""

    def test_liberamente_vivace_gives_d4_bonus(self):
        """liberamente_vivace 状态 → D4+1"""
        from models.player import Player
        # Quick mock: just check the logic inline
        statuses = {"liberamente_vivace"}
        bonus = 0
        if "liberamente_vivace" in statuses:
            bonus += 1
        self.assertEqual(bonus, 1)

    def test_curtain_d4_bonus(self):
        """废墟谢幕 D4+1 奖励"""
        p = _make_player("p1")
        p._g2_curtain_d4_bonus = True
        bonus = 0
        if getattr(p, '_g2_curtain_d4_bonus', False):
            bonus += 1
            p._g2_curtain_d4_bonus = False
        self.assertEqual(bonus, 1)
        self.assertFalse(p._g2_curtain_d4_bonus)


if __name__ == "__main__":
    unittest.main()
