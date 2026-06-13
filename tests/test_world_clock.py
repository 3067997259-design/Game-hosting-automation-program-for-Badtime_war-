"""M5 白昼世界时钟测试：阶段边界 / 阶段效果 / 限量营业 / 嫌疑 / 击杀掉落 / v1 回归。"""
import random
import unittest

from engine import experiments
from engine import world_clock
from engine.game_state import GameState
from models.player import Player
from controllers.forfeit_controller import ForfeitController


def _state(num_players=6):
    state = GameState()
    for i in range(num_players):
        p = Player(f"p{i+1}", f"玩家{i+1}", controller=ForfeitController())
        p.is_awake = True
        p.location = "商店"
        state.add_player(p)
    return state


class PhaseBoundaryTest(unittest.TestCase):

    def setUp(self):
        experiments.reset()
        experiments.enable("m5_clock")

    def tearDown(self):
        experiments.reset()

    def test_six_player_segments(self):
        """6 人局段长 = 6+6 = 12：黎明1-12 / 白昼13-24 / 黄昏25-36 / 终焉37+。"""
        state = _state(6)
        cases = [(1, world_clock.DAWN), (12, world_clock.DAWN),
                 (13, world_clock.DAY), (24, world_clock.DAY),
                 (25, world_clock.DUSK), (36, world_clock.DUSK),
                 (37, world_clock.APOCALYPSE), (100, world_clock.APOCALYPSE)]
        for rnd, expected in cases:
            state.current_round = rnd
            self.assertEqual(world_clock.current_phase(state), expected,
                             f"轮 {rnd} 应为 {expected}")

    def test_two_player_segments(self):
        """2 人局段长 = 6+2 = 8：黄昏从 17 起。"""
        state = _state(2)
        state.current_round = 16
        self.assertEqual(world_clock.current_phase(state), world_clock.DAY)
        state.current_round = 17
        self.assertEqual(world_clock.current_phase(state), world_clock.DUSK)

    def test_disabled_always_dawn(self):
        experiments.reset()
        state = _state(6)
        state.current_round = 50
        self.assertEqual(world_clock.current_phase(state), world_clock.DAWN)

    def test_phase_value_read(self):
        self.assertEqual(
            world_clock.phase_value(world_clock.DUSK, "global_damage_bonus"), 1)
        self.assertEqual(
            world_clock.phase_value(world_clock.APOCALYPSE, "end_of_round_true_damage"), 2)


if __name__ == "__main__":
    unittest.main()
