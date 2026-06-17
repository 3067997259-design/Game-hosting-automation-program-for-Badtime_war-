"""G2 ish-bosheth hp20 再锚定测试（M7 第二阶段，§11.4）+ v1 回归。

覆盖：装甲度公式 / pivot6 / 旋律序列 / 物料牌量纲 / duet 转化率 / embrace 绕天赋防御 /
完整谢幕完结条 +15。
"""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from engine import experiments
from engine.game_state import GameState
from models.player import Player
from models.equipment import make_armor
from controllers.forfeit_controller import ForfeitController
from engine.game_setup import TALENT_TABLE
from engine.ish_bosheth import (
    get_armor_rating, get_total_defense_hp, _calc_stability,
    _calc_melody_damage, _g2_num,
)


def _enable():
    for f in ("k_initiative", "hp20", "m3_accuracy", "m4_gear",
              "m5_clock", "m6_scoring", "m7_talents"):
        experiments.enable(f)


class G2ConversionTest(unittest.TestCase):

    def setUp(self):
        experiments.reset()
        _enable()

    def tearDown(self):
        experiments.reset()

    # ---- 装甲度 = Σ(主防+副防) + 总耐久/6 ----
    def test_armor_rating_formula(self):
        state = GameState()
        p = Player("p1", "受", controller=ForfeitController())
        state.add_player(p)
        armor = make_armor("陶瓷护甲")  # hp20 下有 defense_map + durability
        p.add_armor(armor)
        expected = sum(armor.defense_map.values()) + armor.durability / 6.0
        self.assertAlmostEqual(get_armor_rating(p), expected, places=4)

    # ---- 安定値 pivot 重锚到 6 ----
    def test_stability_uses_pivot_6(self):
        self.assertEqual(_g2_num("stability_pivot", v1=3.5), 6.0)
        p = SimpleNamespace(max_hp=20, armor=None, inner_defense={},
                            talent=None)
        # 无护甲 → 装甲度 0；base(cum=6→0.5..) ；armor_mod=(0-6)*0.4=-2.4
        stab = _calc_stability(p, cumulative_delta=6.0, decay_factor=1.0, ish=None)
        # base = clamp(6/6-0.5, ...) = 0.5; armor_mod = (0-6)*0.4 = -2.4
        self.assertAlmostEqual(stab, 0.5 + (0 - 6.0) * 0.4, places=4)

    # ---- 旋律序列 hp20 ----
    def test_melody_seq_hp20(self):
        self.assertEqual(list(_g2_num("melody_seq_1", v1=[1, 1])), [5, 3, 3, 3])
        self.assertEqual(list(_g2_num("melody_seq_2", v1=[1, 1])), [5, 5, 3, 3])
        self.assertEqual(list(_g2_num("melody_seq_3", v1=[1, 1])), [8, 8, 5, 5])

    def test_melody_damage_floor_1(self):
        # base_dmg 5，stability 极低 → floor 1（m7）
        dmg = _calc_melody_damage(5.0, -0.99, 20, 20)
        self.assertEqual(dmg, 1)

    # ---- 物料牌量纲 ----
    def test_card_glow_stick_value(self):
        from engine.cards.glow_stick import GlowStick
        p = SimpleNamespace(name="P", _card_damage_bonus=0.0,
                            _card_damage_bonus_voice_filter=None)
        ish = SimpleNamespace(phase="active")
        GlowStick().play(p, ish, None)
        self.assertEqual(p._card_damage_bonus, 2)

    def test_card_boo_value(self):
        self.assertEqual(_g2_num("card_boo_damage_taken", v1=0.5), 2)

    def test_card_bouquet_value(self):
        self.assertEqual(_g2_num("card_bouquet_temp_hp", v1=0.5), 3)

    def test_card_support_cheer_value(self):
        self.assertEqual(_g2_num("card_support_cheer_temp_hp", v1=0.5), 2)

    # ---- duet 热力转化率 ----
    def test_duet_conversion_rate(self):
        self.assertEqual(_g2_num("duet_heat_conversion", v1=0.5), 0.1)

    # ---- embrace 无视天赋防御（G1 -2），不无视护甲 ----
    def test_embrace_bypasses_g1_defense(self):
        from combat.damage_resolver import resolve_damage
        state = GameState()
        a = Player("p1", "攻", controller=ForfeitController())
        a.location = "商店"
        state.add_player(a)
        t = Player("p2", "火萤", controller=ForfeitController())
        t.location = "商店"
        state.add_player(t)
        g1cls = next(c for n, nm, c, d in TALENT_TABLE if "火萤" in nm)
        t.talent = g1cls("p2", state)
        hp_before = t.hp
        # embrace 5 点：G1 -2 被无视 → 全额 5 落 HP
        resolve_damage(a, t, None, state, raw_damage_override=5.0,
                       damage_attribute_override="无视属性克制",
                       is_embrace_damage=True, ignore_counter=True)
        self.assertEqual(hp_before - t.hp, 5)

    # ---- 完整谢幕完结条 +15 ----
    def test_finale_curtain_score(self):
        from engine import finale_conditions
        p = Player("p1", "G2", controller=ForfeitController())
        ok = finale_conditions.mark(p, "g2_curtain", None)
        self.assertTrue(ok)
        self.assertEqual(p.story_score, 15)


class G2V1RegressionTest(unittest.TestCase):
    """m7 关闭：G2 v1 量纲不变。"""

    def setUp(self):
        experiments.reset()
        experiments.enable("hp20")  # hp20 但不开 m7

    def tearDown(self):
        experiments.reset()

    def test_v1_stability_uses_total_defense_hp(self):
        self.assertEqual(_g2_num("stability_pivot", v1=3.5), 3.5)
        # v1 下 _calc_stability 走 get_total_defense_hp（=max_hp+...），非装甲度
        p = SimpleNamespace(max_hp=1.0, armor=None, talent=None)
        td = get_total_defense_hp(p)
        self.assertEqual(td, 1.0)

    def test_v1_melody_seq(self):
        self.assertEqual(list(_g2_num("melody_seq_1", v1=[1.0, 0.5, 0.5, 0.5])),
                         [1.0, 0.5, 0.5, 0.5])

    def test_v1_card_value(self):
        self.assertEqual(_g2_num("card_boo_damage_taken", v1=0.5), 0.5)


if __name__ == "__main__":
    unittest.main()
