"""T5 combo 音游谱面重做测试（M7 第二阶段，§7.6.1，重置非迁移）。

验证：谱面生成/大类映射/Perfect·Good·Miss 判定/档位结算（FC·Clear·残·无）/
手感火热 buff/FC 追加行动（通用通道）/剧情分/压制作废/v1 回归字节不变。
"""
import random
import unittest

from engine import experiments
from engine.game_state import GameState
from models.player import Player
from controllers.forfeit_controller import ForfeitController
from talents.t5_combo import Combo


_M7 = ("k_initiative", "hp20", "m3_accuracy", "m4_gear",
       "m5_clock", "m6_scoring", "m7_talents")


def _enable_m7():
    for f in _M7:
        experiments.enable(f)


def _make(m7=True):
    experiments.reset()
    if m7:
        _enable_m7()
    state = GameState()
    p = Player("p1", "T5", controller=ForfeitController())
    state.add_player(p)
    t = Combo("p1", state)
    p.talent = t
    p.max_hp = 20
    p.hp = 12
    return state, p, t


def _chart(t, *notes):
    """notes: (category, beat) → 直接铺谱（绕开随机生成，判定确定）。"""
    t.chart_active = True
    t.current_chart = [{"category": c, "beat": b, "result": None} for c, b in notes]


class T5ChartGenTest(unittest.TestCase):
    def tearDown(self):
        experiments.reset()

    def test_generate_length_and_pool(self):
        state, p, t = _make()
        random.seed(123)
        for _ in range(30):
            t.chart_active = False
            t._generate_chart(p, 5)
            self.assertTrue(1 <= len(t.current_chart) <= 3)
            for n in t.current_chart:
                self.assertIn(n["category"], ("move", "attack", "interact", "special", "police"))
                self.assertIsNone(n["result"])

    def test_police_not_in_pool_without_police_action(self):
        state, p, t = _make()
        # 非警察、无犯人 → police 不入池
        self.assertFalse(t._has_police_action(p))

    def test_action_category_map(self):
        self.assertEqual(Combo._ACTION_CATEGORY["shoot"], "attack")
        self.assertEqual(Combo._ACTION_CATEGORY["hook"], "attack")
        self.assertEqual(Combo._ACTION_CATEGORY["report"], "police")
        self.assertEqual(Combo._ACTION_CATEGORY["election"], "police")
        # 非音符动作不在映射里 → None
        for a in ("wake", "lock", "find", "forfeit", "status"):
            self.assertIsNone(Combo._ACTION_CATEGORY.get(a))


class T5JudgmentTest(unittest.TestCase):
    def tearDown(self):
        experiments.reset()

    def test_perfect_on_beat(self):
        state, p, t = _make()
        _chart(t, ("attack", 5))
        state.current_round = 5
        t.on_turn_end(p, "attack")
        self.assertEqual(t.current_chart[0]["result"], "perfect")

    def test_good_off_beat(self):
        state, p, t = _make()
        _chart(t, ("move", 5))
        state.current_round = 6   # 慢了 1 轮
        t.on_turn_end(p, "move")
        self.assertEqual(t.current_chart[0]["result"], "good")

    def test_wrong_category_no_judge(self):
        state, p, t = _make()
        _chart(t, ("attack", 5))
        state.current_round = 5
        t.on_turn_end(p, "move")   # 大类不符
        self.assertIsNone(t.current_chart[0]["result"])

    def test_miss_when_window_closed(self):
        state, p, t = _make()
        # 二音符：note0 窗口 [4,6]，第 6 轮结束未判 → Miss；note1(beat9) 仍 pending → 不结算
        _chart(t, ("special", 5), ("attack", 9))
        t.on_round_end(6)
        self.assertEqual(t.current_chart[0]["result"], "miss")
        self.assertIsNone(t.current_chart[1]["result"])
        self.assertTrue(t.chart_active)   # 未全判完，谱面仍在


class T5ResolveTest(unittest.TestCase):
    def tearDown(self):
        experiments.reset()

    def test_fc_full_combo(self):
        state, p, t = _make()
        _chart(t, ("attack", 5), ("move", 6), ("interact", 7))
        for r, a in ((5, "attack"), (6, "move"), (7, "interact")):
            state.current_round = r
            t.on_turn_end(p, a)
        state.current_round = 8
        hp0, sc0, pet0 = p.hp, getattr(p, "story_score", 0), p.pending_extra_turns
        t.on_round_end(8)
        self.assertGreater(t.fever_atk, 0)              # 手感火热
        self.assertEqual(p.pending_extra_turns - pet0, 1)  # FC 追加行动
        self.assertGreater(p.hp - hp0, 0)               # 回血
        self.assertEqual(p.story_score - sc0, 2 * 3 + 5)   # 3×perfect + fc_bonus
        self.assertFalse(t.chart_active)                # 谱面清空

    def test_clear_no_extra_turn(self):
        state, p, t = _make()
        _chart(t, ("attack", 5), ("move", 6))
        state.current_round = 5; t.on_turn_end(p, "attack")   # Perfect
        state.current_round = 7; t.on_turn_end(p, "move")     # 慢1 → Good（窗口[5,7]）
        state.current_round = 8
        pet0 = p.pending_extra_turns
        t.on_round_end(8)
        self.assertGreater(t.fever_atk, 0)              # Clear 也给 fever
        self.assertEqual(p.pending_extra_turns - pet0, 0)  # 非 FC 无追加行动

    def test_all_miss_no_buff(self):
        state, p, t = _make()
        _chart(t, ("special", 5))
        t.on_round_end(6)   # 全 Miss
        self.assertEqual(t.fever_atk, 0)
        self.assertFalse(t.chart_active)

    def test_fever_modifies_damage(self):
        state, p, t = _make()
        t.fever_atk = 2
        t.fever_until_round = 10
        state.current_round = 9
        mod = t.modify_outgoing_damage(p, p, None, 5)
        self.assertEqual(mod, {"bonus_damage": 2})
        state.current_round = 11   # 过期
        self.assertIsNone(t.modify_outgoing_damage(p, p, None, 5))


class T5SuppressionTest(unittest.TestCase):
    def tearDown(self):
        experiments.reset()

    def test_suppression_no_judge_and_clears(self):
        state, p, t = _make()
        _chart(t, ("attack", 5))
        p._mythland_talent_suppressed = True
        state.current_round = 5
        t.on_turn_end(p, "attack")               # 压制 → 不判
        self.assertIsNone(t.current_chart[0]["result"])
        t.on_round_start(5)                       # 压制 → 谱面作废
        self.assertFalse(t.chart_active)
        self.assertEqual(t.current_chart, [])


class T5GenericChannelTest(unittest.TestCase):
    def tearDown(self):
        experiments.reset()

    def test_grant_extra_turn_sets_field(self):
        state, p, t = _make()
        self.assertEqual(p.pending_extra_turns, 0)
        t.grant_extra_turn(p, 1)
        t.grant_extra_turn(p, 2)
        self.assertEqual(p.pending_extra_turns, 3)


class T5V1RegressionTest(unittest.TestCase):
    """m7 关：旧 combo 行为字节不变（连续 3 轮 → D4/D6 force）。"""
    def tearDown(self):
        experiments.reset()

    def test_v1_streak_triggers_d4_d6(self):
        state, p, t = _make(m7=False)
        p.max_hp = 2.0; p.hp = 1.0
        for r in range(1, 4):
            p.acted_this_round = True
            t.on_round_end(r)
        self.assertTrue(t._d4_force)
        self.assertTrue(t._d6_force)
        self.assertEqual(t.on_d4_bonus(p), 3)
        self.assertEqual(t.on_d6_bonus(p), 5)

    def test_v1_no_chart(self):
        state, p, t = _make(m7=False)
        t.on_round_start(1)
        self.assertFalse(t.chart_active)   # v1 不发谱


if __name__ == "__main__":
    unittest.main()
