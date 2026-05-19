import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from controllers.ai.goals.develop_goal import DevelopGoal


class DevelopGoalCompletionTest(unittest.TestCase):
    def test_stealth_completion_uses_bound_helper(self):
        player = SimpleNamespace(
            is_invisible=False,
            items=[SimpleNamespace(name="隐身衣")],
            learned_spells=set(),
        )

        self.assertTrue(DevelopGoal("隐身衣", "商店").is_achieved(player, state=None))

    def test_stealth_completion_without_item_returns_false(self):
        player = SimpleNamespace(
            is_invisible=False,
            items=[],
            learned_spells=set(),
        )

        self.assertFalse(DevelopGoal("隐身衣", "商店").is_achieved(player, state=None))

    def test_virus_immunity_completion_uses_bound_helper(self):
        player = SimpleNamespace(
            items=[SimpleNamespace(name="防毒面具")],
            learned_spells=set(),
            talent=None,
            has_seal=False,
        )

        self.assertTrue(DevelopGoal("防毒面具", "商店").is_achieved(player, state=None))

    def test_virus_immunity_completion_without_immunity_returns_false(self):
        player = SimpleNamespace(
            items=[],
            learned_spells=set(),
            talent=None,
            has_seal=False,
        )

        self.assertFalse(DevelopGoal("防毒面具", "商店").is_achieved(player, state=None))


if __name__ == "__main__":
    unittest.main()
