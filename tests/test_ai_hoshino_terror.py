import sys
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace


_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from controllers.ai.controller import BasicAIController


HOSHINO_TALENT_NAME = "大叔我啊，剪短发了"


def _terror_hoshino():
    return SimpleNamespace(
        name="Hoshino",
        player_id="p1",
        is_awake=True,
        talent=SimpleNamespace(
            name=HOSHINO_TALENT_NAME,
            is_terror=True,
        ),
    )


def _state(player):
    return SimpleNamespace(
        current_round=1,
        player_order=[player.player_id],
        get_player=lambda player_id: player if player_id == player.player_id else None,
    )


def _legacy_controller():
    controller = BasicAIController(new_arch_enabled=False)
    controller._update_threat_scores = MethodType(lambda self, player, state: None, controller)
    controller._read_police_state = MethodType(lambda self, state: None, controller)
    controller._update_combat_status = MethodType(lambda self, player, state: None, controller)
    controller._cleanup_dead_players = MethodType(lambda self, state: None, controller)
    controller._needs_virus_cure = MethodType(
        lambda self, player, state: (_ for _ in ()).throw(
            AssertionError("Terror fallback should run before virus handling")
        ),
        controller,
    )
    return controller


class HoshinoTerrorLegacyFallbackTest(unittest.TestCase):
    def test_legacy_arch_terror_attacks_before_generic_logic(self):
        player = _terror_hoshino()
        controller = _legacy_controller()

        self.assertEqual(
            controller._generate_candidates(player, _state(player), ["attack", "move"]),
            ["attack"],
        )

    def test_legacy_arch_terror_forfeits_when_attack_unavailable(self):
        player = _terror_hoshino()
        controller = _legacy_controller()

        self.assertEqual(
            controller._generate_candidates(player, _state(player), ["move"]),
            ["forfeit"],
        )


if __name__ == "__main__":
    unittest.main()
