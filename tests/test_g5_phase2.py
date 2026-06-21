"""G5 phase-2 测试（追忆水源 / 诗换算 / poem reconcile / finale，§7.5）。

P2a：追忆新水源（每轮+1 + 同地点喝彩+1）+ 爱与记忆 ×5。
"""
import unittest

from engine import experiments
from engine.game_state import GameState
from models.player import Player
from controllers.forfeit_controller import ForfeitController
from talents.g5.ripple import Ripple


def _g5_at(loc="商店"):
    st = GameState()
    p = Player("g5", "G5", controller=ForfeitController())
    p.location = loc
    st.add_player(p)
    g5 = Ripple("g5", st)
    p.talent = g5
    return st, p, g5


class ReminiscenceSourceTest(unittest.TestCase):
    def tearDown(self):
        experiments.reset()

    def test_m7_base_per_round(self):
        experiments.enable("m7_talents")
        st, p, g5 = _g5_at()
        g5.reminiscence = 0
        g5.on_round_start(5)            # 无喝彩 → 仅基础 +1
        self.assertEqual(g5.reminiscence, 1)

    def test_m7_applause_bonus_same_location(self):
        experiments.enable("m7_talents")
        st, p, g5 = _g5_at("商店")
        g5.reminiscence = 0
        # 上一轮(4) 商店发生 2 次喝彩（侧表，不入 event_log/golden）
        st._round_applause = [(4, "商店"), (4, "商店"), (4, "魔法所")]  # 异地不算
        g5.on_round_start(5)            # 基础1 + 同地点2 = 3
        self.assertEqual(g5.reminiscence, 3)

    def test_v1_unchanged(self):
        experiments.reset()             # m7 关
        st, p, g5 = _g5_at()
        g5.reminiscence = 0
        g5.acted_last_round = True      # v1：行动了 → +1（>3人时；本局1人 → 0.5）
        g5.on_round_start(5)
        self.assertEqual(g5.reminiscence, 0.5)   # 1 人局 v1 恒 0.5


class PoemDestinyScaleTest(unittest.TestCase):
    def tearDown(self):
        experiments.reset()

    def test_stage_damage_x5(self):
        from talents.talent_balance import talent_num
        experiments.enable("m7_talents")
        self.assertEqual(talent_num("g5", "poem_destiny_stage_damage", v1=0.5), 5)
        experiments.reset()
        self.assertEqual(talent_num("g5", "poem_destiny_stage_damage", v1=0.5), 0.5)


class PoemConversionTest(unittest.TestCase):
    """P2b：群星/负世/守夜人 数值换算（m7 读 balance，v1 原值 fallback）。"""

    def tearDown(self):
        experiments.reset()

    def test_m7_balance_values(self):
        from talents.talent_balance import talent_num
        experiments.enable("m7_talents")
        self.assertEqual(talent_num("g5", "poem_stars_bounce_damage", v1=0.5), 2)
        self.assertEqual(talent_num("g5", "poem_bear_ember", v1=2), 2)
        self.assertEqual(talent_num("g5", "poem_nightwatch_horus", v1=2), 2)

    def test_v1_fallback(self):
        from talents.talent_balance import talent_num
        experiments.reset()
        self.assertEqual(talent_num("g5", "poem_stars_bounce_damage", v1=0.5), 0.5)


class RhythmPoemFCTest(unittest.TestCase):
    """P2c：旋律诗 → 保证 FC（发谱+受限行动+整谱判FC）。"""

    def tearDown(self):
        experiments.reset()

    def _m7(self):
        for f in ("k_initiative", "hp20", "m3_accuracy", "m4_gear",
                  "m5_clock", "m6_scoring", "m7_talents"):
            experiments.enable(f)

    def test_poem_force_full_combo(self):
        self._m7()
        from talents.t5_combo import Combo
        st = GameState(); st.current_round = 3
        p = Player("t5", "T5", controller=ForfeitController())
        p.max_hp = 20; p.hp = 12; p.location = "商店"
        st.add_player(p)
        combo = Combo("t5", st); p.talent = combo
        pet0 = p.pending_extra_turns
        combo.poem_force_full_combo(p)
        self.assertFalse(combo.chart_active)              # 整谱已结算清空
        self.assertGreater(combo.fever_atk, 0)            # FC → 手感火热
        self.assertEqual(p.pending_extra_turns - pet0, 1) # FC → 追加行动

    def test_v1_rhythm_unchanged_path(self):
        # m7 关：poem_force_full_combo 不生效（返回 False），走 v1 旧路径
        experiments.reset()
        from talents.t5_combo import Combo
        st = GameState()
        p = Player("t5", "T5", controller=ForfeitController())
        st.add_player(p)
        combo = Combo("t5", st); p.talent = combo
        self.assertFalse(combo.poem_force_full_combo(p))


class JoyPoemNewG6Test(unittest.TestCase):
    """P2c：欢愉诗 → 新 G6（删 D4、cutaway+笑点）。"""

    def tearDown(self):
        experiments.reset()

    def _setup(self):
        from talents.g5.ripple import Ripple
        from talents.g6_cutaway import CutawayJoke
        st = GameState()
        g5p = Player("g5", "G5", controller=ForfeitController()); st.add_player(g5p)
        g6p = Player("g6", "G6", controller=ForfeitController()); st.add_player(g6p)
        g5 = Ripple("g5", st); g5p.talent = g5
        g6 = CutawayJoke("g6", st); g6p.talent = g6
        return st, g5p, g5, g6p, g6

    def test_m7_no_d4_cutaway_plus_laugh(self):
        for f in ("hp20", "m7_talents"):
            experiments.enable(f)
        st, g5p, g5, g6p, g6 = self._setup()
        g6.laugh_points = 0; g6.cutaway_charges = 0
        g6._d4_force = False; g6._d6_force = False
        g5._poem_joy(g5p, g6p)
        self.assertEqual(g6.cutaway_charges, 1)
        self.assertFalse(g6._d4_force)        # m7：不再戳死 D4
        self.assertEqual(g6.laugh_points, 2)  # 笑点 +X

    def test_v1_keeps_d4(self):
        experiments.reset()
        st, g5p, g5, g6p, g6 = self._setup()
        g6.cutaway_charges = 0; g6._d4_force = False
        g5._poem_joy(g5p, g6p)
        self.assertEqual(g6.cutaway_charges, 1)
        self.assertTrue(g6._d4_force)         # v1：保留 D4


if __name__ == "__main__":
    unittest.main()
