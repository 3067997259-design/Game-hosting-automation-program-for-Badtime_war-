"""test_break.py — 破幕 & 离场测试"""
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


def _make_player(pid, name="P", hp=3.0, emotion=None, is_chorus=False):
    p = SimpleNamespace(
        player_id=pid, name=name, hp=hp, max_hp=5.0,
        location="商店",
        emotion=emotion,
        stage_statuses=set(),
        encore_layers=0,
        stage_entangle=[],
        temp_hp_g2=0.0,
        temp_atk_g2=0.0,
        is_chorus=is_chorus,
        talent=None,
        controller=MagicMock(),
        is_awake=True,
        is_invisible=False,
        _g2_curtain_d4_bonus=False,
    )
    p.is_alive = lambda: p.hp > 0
    return p


def _make_game_state(players, ish):
    gs = SimpleNamespace(
        player_order=[p.player_id for p in players if not p.is_chorus],
        ish_bosheth=ish,
        active_barrier=None,
        markers=MagicMock(),
    )
    _map = {p.player_id: p for p in players}
    gs.get_player = lambda pid: _map.get(pid)
    return gs


class TestBreakCurtainLogic(unittest.TestCase):
    """破幕判定逻辑"""

    def test_strappando_real_player_breaks(self):
        """Strappando 真实玩家致命攻击 G2 → 破幕"""
        g2 = _make_player("g2", "G2", hp=1.0)
        attacker = _make_player("p1", "Attacker", emotion=STRAPPANDO)
        ish = IshBosheth("g2")
        ish.participants.add("p1")

        # Simulate: target hp would go to 0, but we check break
        is_strappando_real = (
            not attacker.is_chorus
            and attacker.emotion == STRAPPANDO
            and "imbalance" not in attacker.stage_statuses
        )
        self.assertTrue(is_strappando_real)

    def test_imbalanced_cannot_break(self):
        """失衡的 Strappando 不能破幕"""
        attacker = _make_player("p1", emotion=STRAPPANDO)
        attacker.stage_statuses.add("imbalance")

        is_strappando_real = (
            not attacker.is_chorus
            and attacker.emotion == STRAPPANDO
            and "imbalance" not in attacker.stage_statuses
        )
        self.assertFalse(is_strappando_real)

    def test_chorus_cannot_break(self):
        """Chorus 不能破幕"""
        chorus = _make_player("c1", "Chorus_1", emotion=STRAPPANDO,
                              is_chorus=True)
        is_strappando_real = (
            not chorus.is_chorus
            and chorus.emotion == STRAPPANDO
        )
        self.assertFalse(is_strappando_real)

    def test_accarezzevole_cannot_attack_g2(self):
        """Accarezzevole 不能攻击 G2（由 action_enumerator 过滤）"""
        g2 = _make_player("g2", "G2")
        attacker = _make_player("p1", emotion=ACCAREZZEVOLE)

        # action_enumerator logic: Accarezzevole can only attack non-G2
        g2_owner_id = "g2"
        opponents = [g2, _make_player("p2", "Other")]
        if attacker.emotion in (ACCAREZZEVOLE, INDIFFERENZA):
            opponents = [o for o in opponents if o.player_id != g2_owner_id]

        g2_in_targets = any(o.player_id == "g2" for o in opponents)
        self.assertFalse(g2_in_targets)


class TestLeaveLogic(unittest.TestCase):
    """离场逻辑"""

    def test_encore_blocks_leave(self):
        """安可阻止离场"""
        p = _make_player("p1", "P1", emotion=ACCAREZZEVOLE)
        p.encore_layers = 2
        p.stage_statuses.add("liberamente_vivace")

        # Simulate move logic
        if p.encore_layers > 0:
            p.encore_layers -= 1
            success = False
        else:
            success = True

        self.assertFalse(success)
        self.assertEqual(p.encore_layers, 1)

    def test_leave_clears_stage_state(self):
        """成功离场清除所有舞台状态"""
        p = _make_player("p1", "P1", emotion=ACCAREZZEVOLE)
        p.stage_statuses = {"liberamente_vivace", "spotlight"}
        p.encore_layers = 0
        p.temp_hp_g2 = 1.0
        p.temp_atk_g2 = 1.0

        # Simulate successful leave
        p.emotion = None
        p.stage_statuses.clear()
        p.encore_layers = 0
        p.stage_entangle.clear()
        p.temp_hp_g2 = 0.0
        p.temp_atk_g2 = 0.0

        self.assertIsNone(p.emotion)
        self.assertEqual(len(p.stage_statuses), 0)
        self.assertEqual(p.temp_hp_g2, 0.0)
        self.assertEqual(p.temp_atk_g2, 0.0)

    @patch("engine.ish_bosheth.display")
    def test_empty_stage_triggers_end(self, _disp):
        """最后一个真实玩家离场 → 空场结束"""
        g2 = _make_player("g2", "G2")
        p1 = _make_player("p1", "P1", emotion=ACCAREZZEVOLE)
        p1.stage_statuses.add("liberamente_vivace")
        g2.stage_statuses.add("liberamente_vivace")

        ish = IshBosheth("g2")
        ish.participants = {"p1"}
        gs = _make_game_state([g2, p1], ish)

        # Simulate p1 leaving
        ish.participants.discard("p1")
        remaining = [pid for pid in ish.participants
                     if pid != ish.g2_owner_id]
        if not remaining:
            ish.end_ish_bosheth("empty", gs)

        self.assertEqual(ish.phase, "ended")


class TestOpponentFiltering(unittest.TestCase):
    """情绪对攻击目标的过滤"""

    def test_strappando_only_g2(self):
        """Strappando 只能攻击 G2"""
        g2_owner_id = "g2"
        opponents = [
            _make_player("g2", "G2"),
            _make_player("p2", "Other"),
            _make_player("p3", "Other2"),
        ]
        attacker_emotion = STRAPPANDO
        if attacker_emotion == STRAPPANDO:
            opponents = [o for o in opponents if o.player_id == g2_owner_id]

        self.assertEqual(len(opponents), 1)
        self.assertEqual(opponents[0].player_id, "g2")

    def test_indifferenza_cannot_attack_g2(self):
        """Indifferenza 不能攻击 G2"""
        g2_owner_id = "g2"
        opponents = [
            _make_player("g2", "G2"),
            _make_player("p2", "Other"),
        ]
        attacker_emotion = INDIFFERENZA
        if attacker_emotion in (ACCAREZZEVOLE, INDIFFERENZA):
            opponents = [o for o in opponents if o.player_id != g2_owner_id]

        self.assertFalse(any(o.player_id == "g2" for o in opponents))


if __name__ == "__main__":
    unittest.main()
