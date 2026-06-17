"""G3 神话之外 hp20 迁移测试（M7 第二阶段，§7.6，仅迁移不重设计）。

G3 无自身伤害量纲——本站只验证：max_barrier_rounds 旋钮 + 天赋压制在 hp20 下
正确关掉已换算天赋（G1 -2 / G7 盾）不崩、不漏。压制二元悬崖重设计待 M8。
"""
import unittest

from engine import experiments
from engine.game_state import GameState
from models.player import Player
from controllers.forfeit_controller import ForfeitController
from engine.game_setup import TALENT_TABLE


def _enable_m7():
    for f in ("k_initiative", "hp20", "m3_accuracy", "m4_gear",
              "m5_clock", "m6_scoring", "m7_talents"):
        experiments.enable(f)


def _talent_cls(keyword):
    return next(c for n, nm, c, d in TALENT_TABLE if keyword in nm)


class G3KnobTest(unittest.TestCase):

    def tearDown(self):
        experiments.reset()

    def test_max_barrier_rounds_m7(self):
        experiments.reset()
        _enable_m7()
        state = GameState()
        p = Player("p1", "G3", controller=ForfeitController())
        state.add_player(p)
        t = _talent_cls("神话之外")("p1", state)
        self.assertEqual(t.max_barrier_rounds, 5)

    def test_max_barrier_rounds_v1(self):
        experiments.reset()  # 无 m7
        state = GameState()
        p = Player("p1", "G3", controller=ForfeitController())
        state.add_player(p)
        t = _talent_cls("神话之外")("p1", state)
        self.assertEqual(t.max_barrier_rounds, 5)


class G3SuppressionHp20Test(unittest.TestCase):
    """天赋压制在 hp20 下正确关掉已换算天赋（不崩、不漏）。"""

    def setUp(self):
        experiments.reset()
        _enable_m7()

    def tearDown(self):
        experiments.reset()

    def _attacker_target(self, target_talent_kw):
        state = GameState()
        a = Player("p1", "攻", controller=ForfeitController())
        a.location = "商店"
        state.add_player(a)
        t = Player("p2", "受", controller=ForfeitController())
        t.location = "商店"
        state.add_player(t)
        t.talent = _talent_cls(target_talent_kw)("p2", state)
        return state, a, t

    def test_suppression_disables_g1_defense(self):
        from combat.damage_resolver import resolve_damage
        state, a, t = self._attacker_target("火萤")
        # 基线：未压制，G1 受伤 -2（m7）→ 5-2=3
        hp0 = t.hp
        resolve_damage(a, t, None, state, raw_damage_override=5.0,
                       damage_attribute_override="无视属性克制", ignore_counter=True)
        self.assertEqual(hp0 - t.hp, 3)
        # 压制后：G1 -2 被关掉 → 全额 5
        t.hp = hp0
        t._mythland_talent_suppressed = True
        resolve_damage(a, t, None, state, raw_damage_override=5.0,
                       damage_attribute_override="无视属性克制", ignore_counter=True)
        self.assertEqual(hp0 - t.hp, 5)

    def test_suppression_disables_g7_shield(self):
        from combat.damage_resolver import resolve_damage
        state, a, t = self._attacker_target("剪短发")
        t.talent.fusion_shield_done = True
        t.talent.iron_horus_hp = 20      # hp20 巨盾
        t.talent.iron_horus_max_hp = 20
        t.talent.shield_mode = None       # 被动保护
        hp0 = t.hp
        # 压制：被动盾被关掉 → 伤害直达 HP，荷鲁斯耐久不消耗
        t._mythland_talent_suppressed = True
        resolve_damage(a, t, None, state, raw_damage_override=5.0,
                       damage_attribute_override="无视属性克制", ignore_counter=True)
        self.assertEqual(t.talent.iron_horus_hp, 20)  # 盾未介入
        self.assertEqual(hp0 - t.hp, 5)               # 全额落 HP

    def test_barrier_setup_suppresses_converted_target(self):
        """_setup_barrier_state 对已换算天赋目标置位压制不崩。"""
        state, caster, victim = self._attacker_target("火萤")
        caster.talent = _talent_cls("神话之外")("p1", state)
        g3 = caster.talent
        g3.active = True
        g3.barrier_players = ["p1", "p2"]
        g3.barrier_location = "商店"
        g3._setup_barrier_state()
        self.assertTrue(getattr(victim, "_mythland_talent_suppressed", False))
        self.assertFalse(getattr(caster, "_mythland_talent_suppressed", False))


if __name__ == "__main__":
    unittest.main()
