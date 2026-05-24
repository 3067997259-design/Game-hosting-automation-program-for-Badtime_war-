import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from controllers.ai.command_builder.combat_commands import CombatCommandBuilder
from controllers.ai.context import OrchestratorContext
from controllers.ai.game_query import GameQuery
from utils.attribute import Attribute


class RearmCommandBuilderTest(unittest.TestCase):
    def _builder(self):
        return CombatCommandBuilder(GameQuery())

    def _player(self, has_pass):
        return SimpleNamespace(
            name="AI",
            location="商店",
            vouchers=1,
            has_military_pass=has_pass,
            learned_spells=set(),
            weapons=[SimpleNamespace(name="魔法弹幕", attribute=Attribute.MAGIC)],
        )

    def test_rearm_without_military_pass_does_not_move_to_military_base(self):
        cmds = self._builder().build_rearm(
            self._player(has_pass=False), SimpleNamespace(players={}),
            strategy=None, available=["move", "interact"], ctx=OrchestratorContext(),
        )

        self.assertEqual(cmds, ["move 魔法所"])

    def test_rearm_with_military_pass_can_seek_tech_weapon(self):
        cmds = self._builder().build_rearm(
            self._player(has_pass=True), SimpleNamespace(players={}),
            strategy=None, available=["move", "interact"], ctx=OrchestratorContext(),
        )

        self.assertEqual(cmds, ["move 军事基地"])


if __name__ == "__main__":
    unittest.main()
