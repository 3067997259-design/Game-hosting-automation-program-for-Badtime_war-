import sys
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace


_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from controllers.ai.controller import BasicAIController


HOSHINO_TALENT_NAME = "大叔我啊，剪短发了"


def _player(name, player_id, talent=None):
    return SimpleNamespace(
        name=name,
        player_id=player_id,
        talent=talent,
        hp=5.0,
        is_alive=lambda: True,
    )


def _state(*players):
    by_id = {player.player_id: player for player in players}
    return SimpleNamespace(
        player_order=[player.player_id for player in players],
        get_player=lambda player_id: by_id.get(player_id),
    )


class ThreatEvaluationTest(unittest.TestCase):
    def _evaluate_target_score(self, target_talent, *, new_arch_enabled=False):
        controller = BasicAIController(new_arch_enabled=new_arch_enabled)
        player = _player("Observer", "p1")
        target = _player("Hoshino", "p2", target_talent)
        controller._estimate_power = MethodType(lambda self, target: 100.0, controller)

        controller._update_threat_scores(player, _state(player, target))

        return controller._threat_scores[target.name]

    def test_legacy_arch_preserves_terror_threat_boost(self):
        talent = SimpleNamespace(
            name=HOSHINO_TALENT_NAME,
            is_terror=True,
            self_doubt_pending=False,
            tactical_unlocked=False,
            ammo=[],
        )

        self.assertEqual(
            self._evaluate_target_score(talent, new_arch_enabled=False),
            60.0,
        )

    def test_legacy_arch_preserves_self_doubt_threat_boost(self):
        talent = SimpleNamespace(
            name=HOSHINO_TALENT_NAME,
            is_terror=False,
            self_doubt_pending=True,
            tactical_unlocked=False,
            ammo=[],
        )

        self.assertEqual(
            self._evaluate_target_score(talent, new_arch_enabled=False),
            50.0,
        )

    def test_new_arch_terror_threat_matches_legacy_baseline(self):
        talent = SimpleNamespace(
            name=HOSHINO_TALENT_NAME,
            is_terror=True,
            self_doubt_pending=False,
            tactical_unlocked=False,
            ammo=[],
        )

        self.assertEqual(
            self._evaluate_target_score(talent, new_arch_enabled=True),
            60.0,
        )


if __name__ == "__main__":
    unittest.main()
