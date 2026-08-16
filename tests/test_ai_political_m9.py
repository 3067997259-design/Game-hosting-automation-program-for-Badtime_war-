"""political 人格 M9 队长认知回归测试：发育完成门在 M9 警务下的正确语义。"""
import unittest

from engine import experiments
from engine.game_state import GameState
from engine.m9.gate import ensure_state_mechanisms
from models.player import Player
from models.equipment import make_weapon
from controllers.forfeit_controller import ForfeitController
from controllers.ai.strategies.political_strategy import PoliticalStrategy


def _enable(*flags):
    experiments.reset()
    for flag in flags:
        experiments.enable(flag)


class PoliticalM9DevelopTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def _make(self):
        state = GameState()
        ensure_state_mechanisms(state)
        p = Player("p1", "政客", controller=ForfeitController())
        p.is_awake = True
        p.location = "警察局"
        p.hp = p.max_hp = 20
        state.add_player(p)
        return state, p

    def _complete(self, state, p):
        strat = PoliticalStrategy()
        return strat.is_development_complete(
            p, state,
            count_outer_armor=lambda pl: 1,
            count_inner_armor=lambda pl: 0,
            has_real_weapon=True, has_pass=False, has_stealth=False,
            real_weapon_count=1)

    def test_no_captain_blocks_development_during_grace_window(self) -> None:
        """竞选窗口内（R<5）无队长：保持警察路线，发育不放行。"""
        state, p = self._make()
        state.current_round = 2
        self.assertFalse(self._complete(state, p))

    def test_no_captain_after_grace_falls_back(self) -> None:
        """竞选窗口（R≥5）过后仍无队长：政治路线已死 → 基础装备放行战斗。"""
        state, p = self._make()
        state.current_round = 6
        self.assertTrue(self._complete(state, p))

    def test_fallback_level_none_until_grace_then_full_balanced(self) -> None:
        from controllers.ai.game_query import GameQuery
        state, p = self._make()
        state.current_round = 2
        self.assertEqual(GameQuery.political_should_fallback(p, state), "none")
        state.current_round = 6
        self.assertEqual(
            GameQuery.political_should_fallback(p, state), "full_balanced")

    def test_t6_political_never_falls_back_without_captain(self) -> None:
        """T6 的胜利路径就是警察线：无队长时永不降级（R35 负优化回退）。"""
        from engine.m9.talents.t6 import GoodCitizen9
        from controllers.ai.game_query import GameQuery
        state, p = self._make()
        p.talent = GoodCitizen9("p1", state)
        state.current_round = 6
        self.assertEqual(GameQuery.political_should_fallback(p, state), "none")
        self.assertFalse(self._complete(state, p))

    def test_self_captain_completes_with_basic_kit(self) -> None:
        state, p = self._make()
        state.m9_police.set_state_ref(state)
        state.m9_police.apply_captain("p1")
        state.m9_police.r2_tick(state, 2)  # R2 上任 → 回写 is_captain
        self.assertTrue(self._complete(state, p))

    def test_foreign_captain_falls_back_to_combat_readiness(self) -> None:
        """队长被他人占据：政治路线无回报 → 基础装备齐即放行战斗。"""
        state, p = self._make()
        other = Player("p2", "路人", controller=ForfeitController())
        state.add_player(other)
        state.m9_police.set_state_ref(state)
        state.m9_police.apply_captain("p2")
        state.m9_police.r2_tick(state, 2)
        self.assertTrue(self._complete(state, p))

    def test_disabled_station_falls_back(self) -> None:
        state, p = self._make()
        state.m9_police.set_state_ref(state)
        state.m9_police.shut_down()
        self.assertTrue(self._complete(state, p))


if __name__ == "__main__":
    unittest.main()
