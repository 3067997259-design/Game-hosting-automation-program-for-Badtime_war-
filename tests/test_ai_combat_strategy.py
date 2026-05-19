import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from controllers.ai.evaluation_mixin import EvaluationMixin


class _CombatDecisionHarness(EvaluationMixin):
    def __init__(self, strategy):
        self.personality = "defensive"
        self._strategy = strategy
        self._game_state = None
        self._political_in_balanced_fallback = False

    def _has_firefly_talent(self, player):
        return False

    def _has_hoshino_talent(self, player):
        return False

    def _count_outer_armor(self, player):
        return 1

    def _count_inner_armor(self, player):
        return 0

    def _get_effective_hp(self, player):
        return 2.0

    def _is_at_disadvantage(self, player, target):
        return True

    def _firefly_supernova_threat(self, player, state):
        return False

    def _all_weapons_countered(self, player, target):
        return False


class _OverrideContinueStrategy:
    def __init__(self):
        self.called = False

    def should_continue_combat(self, player, target, is_at_disadvantage):
        self.called = True
        self.received_disadvantage = is_at_disadvantage
        return True


class _DefaultStrategy:
    def __init__(self):
        self.called = False

    def should_continue_combat(self, player, target, is_at_disadvantage):
        self.called = True
        return None


class CombatStrategyDecisionTest(unittest.TestCase):
    def _alive_player(self):
        return SimpleNamespace(is_alive=lambda: True, is_captain=False)

    def test_strategy_can_override_defensive_disadvantage_retreat(self):
        strategy = _OverrideContinueStrategy()
        harness = _CombatDecisionHarness(strategy)

        self.assertTrue(harness._should_continue_combat(self._alive_player(), self._alive_player()))
        self.assertTrue(strategy.called)
        self.assertTrue(strategy.received_disadvantage)

    def test_defensive_disadvantage_remains_fallback_when_strategy_defaults(self):
        strategy = _DefaultStrategy()
        harness = _CombatDecisionHarness(strategy)

        self.assertFalse(harness._should_continue_combat(self._alive_player(), self._alive_player()))
        self.assertTrue(strategy.called)


if __name__ == "__main__":
    unittest.main()
