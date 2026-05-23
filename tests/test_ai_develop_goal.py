import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from controllers.ai.goals.develop_goal import DevelopGoal
from controllers.ai.goals.base_goal import GoalStack
from controllers.ai.orchestrator import DecisionOrchestrator


class _StubDevelopBuilder:
    def build_develop(self, *args, **kwargs):
        return ["move 商店"]


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


class DevelopGoalPersistenceTest(unittest.TestCase):
    def test_existing_develop_goal_keeps_original_destination(self):
        stack = GoalStack()
        original_goal = DevelopGoal("电磁步枪", "军事基地")
        stack.push(original_goal)

        orchestrator = DecisionOrchestrator(
            strategy=SimpleNamespace(personality_name="balanced"),
            goal_stack=stack,
            talent_hooks={},
            minds=[],
            controller=SimpleNamespace(),
        )
        orchestrator._build_ctx = lambda _state: None
        orchestrator._develop_cmd = _StubDevelopBuilder()

        player = SimpleNamespace(name="AI", location="home")
        develop_assessment = SimpleNamespace(data={
            "development_complete": False,
            "best_location": "商店",
            "unmet_needs": [
                ("outer_armor", [("商店", "陶瓷护甲", 0)]),
            ],
        })

        cmds = orchestrator._handle_develop(
            player, SimpleNamespace(), ["move"], {"develop": develop_assessment}, 2)

        self.assertEqual(cmds, [])
        self.assertIs(stack.top(), original_goal)
        self.assertEqual(original_goal.target_location, "军事基地")
        self.assertEqual(
            orchestrator._collect_goal_commands(player, SimpleNamespace(), ["move"], []),
            ["move 军事基地"],
        )


if __name__ == "__main__":
    unittest.main()
