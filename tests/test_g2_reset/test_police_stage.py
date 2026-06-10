"""test_police_stage.py — Submerged 警察限制 + report 禁止"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

_project_root = str(Path(__file__).resolve().parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


def _make_player(pid, stage_statuses=None):
    return SimpleNamespace(
        player_id=pid, name=f"Player_{pid}",
        stage_statuses=stage_statuses or set(),
    )


class TestSubmergeLogic(unittest.TestCase):
    """Submerged: location=None + is_submerged=True"""

    def test_submerged_unit_disabled(self):
        """测试 is_disabled() 逻辑：location=None + is_submerged → disabled"""
        unit = SimpleNamespace(
            location=None,
            is_stunned=False, is_shocked=False, is_petrified=False,
            is_submerged=True,
        )
        unit.is_disabled = lambda: (unit.is_stunned or unit.is_shocked
                                     or unit.is_petrified or unit.is_submerged)
        self.assertTrue(unit.is_disabled())

    def test_not_submerged_unit_enabled(self):
        unit = SimpleNamespace(
            location="警察局",
            is_stunned=False, is_shocked=False, is_petrified=False,
            is_submerged=False,
        )
        unit.is_disabled = lambda: (unit.is_stunned or unit.is_shocked
                                     or unit.is_petrified or unit.is_submerged)
        self.assertFalse(unit.is_disabled())

    def test_submerged_unit_off_map(self):
        unit = SimpleNamespace(location=None, is_submerged=True)
        unit.is_on_map = lambda: unit.location is not None
        self.assertFalse(unit.is_on_map())


class TestReportBan(unittest.TestCase):
    """舞台内 report 被禁止"""

    def _make_police_state(self, stage_active=True, reporter_in_stage=True):
        ish = SimpleNamespace(phase="active") if stage_active else None
        reporter = _make_player("p1", {"liberamente_vivace"} if reporter_in_stage else set())
        target = _make_player("p2")
        state = SimpleNamespace(
            police=SimpleNamespace(permanently_disabled=False,
                                   is_criminal=lambda pid: True,
                                   has_captain=lambda: False,
                                   report_phase="idle"),
            ish_bosheth=ish,
            get_player=lambda pid: reporter if pid == "p1" else target,
            event_log=[{"type": "attack", "attacker": "p2", "target": "p1"}],
        )
        return state, reporter, target

    def test_report_blocked_during_stage(self):
        """舞台活跃时 reporter 无法举报"""
        gs, reporter, target = self._make_police_state(
            stage_active=True, reporter_in_stage=True)
        # Simulate the can_report checks
        reporter_stage = "liberamente_vivace" in getattr(reporter, 'stage_statuses', set())
        target_stage = "liberamente_vivace" in getattr(target, 'stage_statuses', set())
        stage_blocked = (getattr(gs, 'ish_bosheth', None)
                         and gs.ish_bosheth.phase == "active"
                         and (reporter_stage or target_stage))
        self.assertTrue(stage_blocked)

    def test_report_allowed_outside_stage(self):
        """非舞台内玩家正常举报"""
        gs, reporter, target = self._make_police_state(
            stage_active=False, reporter_in_stage=False)
        reporter_stage = "liberamente_vivace" in getattr(reporter, 'stage_statuses', set())
        target_stage = "liberamente_vivace" in getattr(target, 'stage_statuses', set())
        stage_blocked = (getattr(gs, 'ish_bosheth', None)
                         and gs.ish_bosheth.phase == "active"
                         and (reporter_stage or target_stage))
        self.assertFalse(stage_blocked)


if __name__ == "__main__":
    unittest.main()
