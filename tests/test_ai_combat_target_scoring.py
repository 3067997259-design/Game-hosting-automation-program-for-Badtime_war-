import sys
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace


_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from controllers.ai.controller import BasicAIController
from controllers.ai.strategies.aggressive_strategy import AggressiveStrategy


def _target(name, player_id):
    return SimpleNamespace(
        name=name,
        player_id=player_id,
        hp=5.0,
        talent=None,
        is_captain=False,
        is_police=False,
        is_alive=lambda: True,
    )


def _state(*targets):
    by_id = {target.player_id: target for target in targets}
    return SimpleNamespace(
        player_order=[target.player_id for target in targets],
        get_player=lambda player_id: by_id.get(player_id),
        police_engine=None,
    )


def _neutralize_target_scoring(controller, power_by_name):
    controller._threat_scores = {}
    controller._been_attacked_by = set()
    controller._llm_alliance = set()

    controller._is_valid_attack_target = MethodType(lambda self, p, t, s: True, controller)
    controller._same_location = MethodType(lambda self, p, t: False, controller)
    controller._get_effective_hp = MethodType(lambda self, target: 5.0, controller)
    controller._count_outer_armor = MethodType(lambda self, target: 0, controller)
    controller._count_inner_armor = MethodType(lambda self, target: 0, controller)
    controller._has_firefly_talent = MethodType(lambda self, p: False, controller)
    controller._has_unused_mythland = MethodType(lambda self, target: False, controller)
    controller._all_weapons_countered = MethodType(lambda self, p, t: False, controller)
    controller._has_hoshino_talent = MethodType(lambda self, p: False, controller)
    controller._is_in_savior_state = MethodType(lambda self, p: False, controller)
    controller._has_savior_talent = MethodType(lambda self, p: False, controller)
    controller._target_is_firefly = MethodType(lambda self, target: False, controller)
    controller._estimate_power = MethodType(
        lambda self, target: power_by_name[getattr(target, "name", "")],
        controller,
    )


class RecordingAggressiveStrategy(AggressiveStrategy):
    def __init__(self):
        self.base_scores = []

    def modify_target_score(self, target, base_score, player,
                            players_who_attacked, is_passive, target_power):
        self.base_scores.append(base_score)
        return super().modify_target_score(
            target, base_score, player,
            players_who_attacked=players_who_attacked,
            is_passive=is_passive,
            target_power=target_power,
        )


class CombatTargetScoringTest(unittest.TestCase):
    def test_aggressive_strategy_does_not_receive_legacy_passive_bonus(self):
        controller = BasicAIController(personality="aggressive", new_arch_enabled=True)
        target = _target("Passive", "p2")
        player = SimpleNamespace(player_id="p1", name="Attacker", weapons=[])
        strategy = RecordingAggressiveStrategy()

        controller._players_who_attacked = set()
        controller._strategy = strategy
        _neutralize_target_scoring(controller, {"Passive": 100.0})

        self.assertIs(controller._pick_target(player, _state(target)), target)
        self.assertEqual(strategy.base_scores, [0])

    def test_aggressive_legacy_passive_bonus_remains_without_strategy(self):
        controller = BasicAIController(personality="aggressive", new_arch_enabled=False)
        active = _target("Active", "p2")
        passive = _target("Passive", "p3")
        player = SimpleNamespace(player_id="p1", name="Attacker", weapons=[])

        controller._players_who_attacked = {"Active", "p2"}
        _neutralize_target_scoring(
            controller,
            {
                "Active": 41.0,
                "Passive": 40.0,
            },
        )

        self.assertIs(controller._pick_target(player, _state(active, passive)), passive)


if __name__ == "__main__":
    unittest.main()
