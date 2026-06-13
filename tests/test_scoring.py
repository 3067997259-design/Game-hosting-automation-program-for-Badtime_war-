"""M6 评分制测试：战果分 / 存活系数 / 终分公式 / winner 双轨 / v1 回归。"""
import unittest

from engine import experiments
from engine.game_state import GameState
from engine import scoring
from models.player import Player
from controllers.forfeit_controller import ForfeitController


def _player(pid, state, **kw):
    p = Player(pid, f"玩家{pid}", controller=ForfeitController())
    p.is_awake = True
    p.location = "商店"
    for k, v in kw.items():
        setattr(p, k, v)
    state.add_player(p)
    return p


class BattleScoreTest(unittest.TestCase):

    def setUp(self):
        experiments.reset()
        experiments.enable("m6_scoring")
        self.state = GameState()

    def tearDown(self):
        experiments.reset()

    def test_kill_and_damage(self):
        p = _player("p1", self.state, kill_count=2, damage_dealt=45)
        # 2 击杀×3 + ⌈45/20⌉=2 → 6+2=8
        self.assertEqual(scoring.battle_score(p), 8)

    def test_damage_cap(self):
        p = _player("p1", self.state, kill_count=0, damage_dealt=1000)
        self.assertEqual(scoring.battle_score(p), 5)  # 上限 5

    def test_survival_coefficient_alive(self):
        p = _player("p1", self.state)
        self.assertEqual(scoring.survival_coefficient(p, 20), 1.5)

    def test_survival_coefficient_dead_proportional(self):
        p = _player("p1", self.state, hp=0, death_round=10)
        # max(0.5, 10/20) = 0.5
        self.assertEqual(scoring.survival_coefficient(p, 20), 0.5)
        p2 = _player("p2", self.state, hp=0, death_round=18)
        # max(0.5, 18/20=0.9) = 0.9
        self.assertEqual(scoring.survival_coefficient(p2, 20), 0.9)

    def test_final_score_components(self):
        p = _player("p1", self.state, kill_count=1, damage_dealt=0,
                    applause=2, story_score=15, afterlife_score=4)
        # (15剧情+2喝彩+3战果)×1.5存活 + 4往世×0.5 = 30+2 = 32
        self.assertEqual(scoring.final_score(p, 20), 32.0)


class WinnerDualTrackTest(unittest.TestCase):

    def setUp(self):
        experiments.reset()
        experiments.enable("m6_scoring")

    def tearDown(self):
        experiments.reset()

    def test_dead_killer_beats_passive_survivor(self):
        """杀人多的死者终分 > 不杀人的存活者（评价体系转向）。"""
        state = GameState()
        survivor = _player("p1", state)  # 活着但无战果
        dead_killer = _player("p2", state, hp=0, death_round=18,
                              kill_count=3, damage_dealt=40)
        scores = scoring.compute_all(state)
        # survivor: 0×1.5=0；killer: (3×3+2)×0.9=9.9
        self.assertGreater(scores["p2"], scores["p1"])


class V1RegressionTest(unittest.TestCase):
    def setUp(self):
        experiments.reset()

    def test_no_m6_no_score(self):
        state = GameState()
        p = _player("p1", state)
        self.assertEqual(p.applause, 0)
        self.assertEqual(p.damage_dealt, 0)
        self.assertFalse(p.is_star)


if __name__ == "__main__":
    unittest.main()
