"""G7 星野 hp20 换算测试（M7 第二阶段，§7.4）+ v1 回归。

逐条覆盖 §7.4 换算表：荷鲁斯 20 耐久 / 架盾≤6 / 持盾-5 / 被动-3 / 光环 3 点池 /
荷鲁斯之眼单发 6 / 破甲-6 耐久 / Terror 折算 / 免死 12。
"""
import unittest

from engine import experiments
from engine.game_state import GameState
from models.player import Player
from models.equipment import make_armor, ArmorLayer
from controllers.forfeit_controller import ForfeitController
from engine.game_setup import TALENT_TABLE


def _enable():
    for f in ("k_initiative", "hp20", "m3_accuracy", "m4_gear",
              "m5_clock", "m6_scoring", "m7_talents"):
        experiments.enable(f)


def _hoshino_cls():
    return next(c for n, nm, c, d in TALENT_TABLE if "剪短发" in nm)


def _make_hoshino(state, pid="p1"):
    """构造星野（不调 on_register 以避开形态 choose）。"""
    talent = _hoshino_cls()(pid, state)
    talent.form = "临战-shielder"
    return talent


class G7ConversionTest(unittest.TestCase):

    def setUp(self):
        experiments.reset()
        _enable()
        self.state = GameState()
        self.p = Player("p1", "星野", controller=ForfeitController())
        self.p.location = "商店"
        self.state.add_player(self.p)
        self.talent = _make_hoshino(self.state, "p1")
        self.p.talent = self.talent

    def tearDown(self):
        experiments.reset()

    # ---- 融合：荷鲁斯 20 耐久 ----
    def test_fusion_durability_20(self):
        self.p.add_armor(make_armor("盾牌"))
        self.p.add_armor(make_armor("AT力场"))
        self.talent._check_fusion(self.p)
        self.assertTrue(self.talent.fusion_shield_done)
        self.assertEqual(self.talent.iron_horus_hp, 20)
        self.assertEqual(self.talent.iron_horus_max_hp, 20)

    def test_repair_plus_8(self):
        self.talent.fusion_shield_done = True
        self.talent.iron_horus_hp = 4
        self.talent.iron_horus_max_hp = 20
        self.p.add_armor(make_armor("盾牌"))
        self.talent._repair_horus(self.p, "盾牌")
        self.assertEqual(self.talent.iron_horus_hp, 12)  # 4 + 8

    # ---- 架盾：≤6 完全格挡 / >6 扣 3 耐久 ----
    def test_shield_block_under_threshold(self):
        from combat.damage_resolver import _g7_m7_shield_filter
        self.talent.iron_horus_hp = 20
        result = {"final_damage": 5, "success": True, "details": []}
        _g7_m7_shield_filter(self.talent, 6, result, self.state)
        self.assertEqual(result["final_damage"], 0)
        self.assertEqual(self.talent.iron_horus_hp, 20)  # 未扣耐久

    def test_shield_overflow_durability(self):
        from combat.damage_resolver import _g7_m7_shield_filter
        self.talent.iron_horus_hp = 20
        result = {"final_damage": 9, "success": True, "details": []}
        _g7_m7_shield_filter(self.talent, 9, result, self.state)
        self.assertEqual(result["final_damage"], 0)
        self.assertEqual(self.talent.iron_horus_hp, 17)  # 20 - 3

    # ---- 持盾：全属性 -5 减法 ----
    def test_hold_subtractive_defense(self):
        from combat.damage_resolver import _g7_m7_hold_absorb
        self.talent.shield_mode = "持盾"
        self.talent.iron_horus_hp = 20
        result = {"final_damage": 10, "success": True, "details": []}
        handled, raw = _g7_m7_hold_absorb(self.talent, 10, result, self.state)
        # 10 - 5 = 5 由耐久吸收 → 全挡，耐久 20-5=15
        self.assertTrue(handled)
        self.assertEqual(self.talent.iron_horus_hp, 15)

    # ---- 被动：全属性 -3 + 保留 2 耐久 + 光环溢出 ----
    def test_passive_defense_and_reserve(self):
        from combat.damage_resolver import _g7_m7_passive
        self.talent.fusion_shield_done = True
        self.talent.iron_horus_hp = 5
        # 来 10 伤：-3=7；可吸收 = 5-2(reserve)=3 → 耐久→2，剩 4；无光环 → 溢出 4
        for h in self.talent.halos:
            h["active"] = False
        result = {"final_damage": 10, "success": True, "details": []}
        handled, raw = _g7_m7_passive(self.talent, 10, result)
        self.assertFalse(handled)
        self.assertEqual(self.talent.iron_horus_hp, 2)  # 保留 reserve
        self.assertEqual(raw, 4)

    # ---- 光环：每层 3 点护体池 ----
    def test_halo_pool_3_points(self):
        for h in self.talent.halos:
            self.talent._halo_activate(h)
        self.assertEqual(self.talent.halos[0]["value"], 3)
        # 3 层共 9 点，来 10 伤 → 吸 9 剩 1
        remaining = self.talent.receive_damage_to_temp_hp(10)
        self.assertEqual(remaining, 1)
        self.assertEqual(sum(1 for h in self.talent.halos if h["active"]), 0)

    # ---- 荷鲁斯之眼：单发裸伤 6（弹丸 2 × 3）----
    def test_eye_pellet_damage(self):
        from talents.talent_balance import talent_num
        self.assertEqual(talent_num("g7", "eye_pellet_damage", v1=0.5), 2)

    # ---- 破甲：-6 耐久 ----
    def test_armor_pierce_durability(self):
        target = Player("p2", "受", controller=ForfeitController())
        self.state.add_player(target)
        armor = make_armor("陶瓷护甲")
        target.add_armor(armor)
        before = armor.durability
        self.talent._armor_pierce_durability(target)
        self.assertEqual(armor.durability, max(0, before - 6))

    # ---- Terror 折算：horus÷4 + 光环×3 + 甲×3，保底 12 ----
    def test_terror_conversion(self):
        self.talent.iron_horus_hp = 20      # ÷4 = 5
        for h in self.talent.halos:
            self.talent._halo_activate(h)    # 3 层 ×3 = 9
        self.p.hp = 1
        self.talent._enter_terror(self.p)
        self.assertEqual(self.talent.terror_extra_hp, 14)  # 5 + 9 + 0甲 = 14 (>floor12)

    def test_terror_floor_12(self):
        self.talent.iron_horus_hp = 0
        for h in self.talent.halos:
            h["active"] = False
        self.p.hp = 1
        self.talent._enter_terror(self.p)
        self.assertEqual(self.talent.terror_extra_hp, 12)  # 保底

    # ---- 免死 12 ----
    def test_revive_hp_12(self):
        self.talent._combat_continuation_immunity = True
        self.p.hp = 0
        r = self.talent.on_death_check(self.p, None)
        self.assertEqual(r["new_hp"], 12)


class G7V1RegressionTest(unittest.TestCase):
    """m7 关闭：星野 v1 量纲不变。"""

    def setUp(self):
        experiments.reset()
        experiments.enable("hp20")  # hp20 但不开 m7

    def tearDown(self):
        experiments.reset()

    def test_v1_fusion_durability_2(self):
        state = GameState()
        p = Player("p1", "星野", controller=ForfeitController())
        state.add_player(p)
        talent = _make_hoshino(state, "p1")
        p.add_armor(make_armor("盾牌"))
        p.add_armor(make_armor("AT力场"))
        talent._check_fusion(p)
        self.assertEqual(talent.iron_horus_hp, 2)  # v1

    def test_v1_pellet_and_revive(self):
        from talents.talent_balance import talent_num
        self.assertEqual(talent_num("g7", "eye_pellet_damage", v1=0.5), 0.5)
        state = GameState()
        p = Player("p1", "星野", controller=ForfeitController())
        state.add_player(p)
        talent = _make_hoshino(state, "p1")
        talent._combat_continuation_immunity = True
        p.hp = 0
        r = talent.on_death_check(p, None)
        self.assertEqual(r["new_hp"], 1.0)  # v1


if __name__ == "__main__":
    unittest.main()
