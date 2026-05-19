import sys
import unittest
from pathlib import Path


_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from controllers.ai.goals.base_goal import GoalStack
from controllers.ai.goals.combat_goal import CombatGoal


class GoalStackPriorityReplacementTest(unittest.TestCase):
    def test_lower_priority_same_type_goal_does_not_replace_existing_goal(self):
        stack = GoalStack()
        terror_goal = CombatGoal("terror", "Terror", priority=9)
        kill_goal = CombatGoal("kill", "Kill", priority=7)

        stack.push(terror_goal)
        stack.push(kill_goal)

        self.assertEqual([goal.target_id for goal in stack.all_goals], ["terror"])
        self.assertIs(stack.top(), terror_goal)

    def test_equal_priority_same_type_goal_still_replaces_existing_goal(self):
        stack = GoalStack()
        first_goal = CombatGoal("first", "First", priority=7)
        second_goal = CombatGoal("second", "Second", priority=7)

        stack.push(first_goal)
        stack.push(second_goal)

        self.assertEqual([goal.target_id for goal in stack.all_goals], ["second"])
        self.assertIs(stack.top(), second_goal)

    def test_higher_priority_same_type_goal_replaces_existing_goal(self):
        stack = GoalStack()
        kill_goal = CombatGoal("kill", "Kill", priority=7)
        terror_goal = CombatGoal("terror", "Terror", priority=9)

        stack.push(kill_goal)
        stack.push(terror_goal)

        self.assertEqual([goal.target_id for goal in stack.all_goals], ["terror"])
        self.assertIs(stack.top(), terror_goal)


if __name__ == "__main__":
    unittest.main()
