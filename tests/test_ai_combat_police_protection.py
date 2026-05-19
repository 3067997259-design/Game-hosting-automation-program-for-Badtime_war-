import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from controllers.ai.controller import BasicAIController


class _PoliceEngineStub:
    def __init__(self, protected_player_id, threshold):
        self._protected_player_id = protected_player_id
        self._threshold = threshold

    def is_protected_by_police(self, player_id):
        return player_id == self._protected_player_id

    def get_protection_threshold(self, player_id):
        return self._threshold


class CombatPoliceProtectionTest(unittest.TestCase):
    def _build_legacy_controller(self, estimated_damage, threshold=1.0):
        controller = BasicAIController(new_arch_enabled=False)
        self.assertFalse(hasattr(controller, "_police_mind"))

        weapon = SimpleNamespace(name="高伤害步枪")
        player = SimpleNamespace(
            name="Attacker",
            player_id="p1",
            weapons=[weapon],
            learned_spells=set(),
        )
        target = SimpleNamespace(
            name="Protected",
            player_id="p2",
            is_alive=lambda: True,
        )
        state = SimpleNamespace(
            police_engine=_PoliceEngineStub(target.player_id, threshold),
        )

        controller._pick_target = lambda _player, _state: target
        controller._pick_weapon = lambda _player, _target: weapon
        controller._get_outer_armor_attr = lambda _target: []
        controller._get_inner_armor_attr = lambda _target: []
        controller._get_all_aoe_weapon_names = lambda _player: []
        controller._estimate_talent_adjusted_damage = (
            lambda _player, _weapon: estimated_damage
        )
        controller._get_weapon_range = lambda _weapon: "ranged"
        controller._all_weapons_countered = lambda _player, _target: False
        controller._build_attack_cmd = (
            lambda _player, _target, _weapon, _state, _available:
                [f"attack {_target.name} {_weapon.name}"]
        )

        return controller, player, state

    def test_legacy_police_protection_allows_damage_above_threshold(self):
        controller, player, state = self._build_legacy_controller(
            estimated_damage=1.5,
            threshold=1.0,
        )

        commands = controller._cmd_attack(player, state, ["attack"])

        self.assertEqual(commands, ["attack Protected 高伤害步枪"])

    def test_legacy_police_protection_blocks_damage_at_or_below_threshold(self):
        controller, player, state = self._build_legacy_controller(
            estimated_damage=1.0,
            threshold=1.0,
        )

        commands = controller._cmd_attack(player, state, ["attack"])

        self.assertEqual(commands, [])


if __name__ == "__main__":
    unittest.main()
