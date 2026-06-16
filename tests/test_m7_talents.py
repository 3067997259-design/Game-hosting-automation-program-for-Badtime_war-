"""M7 第一阶段天赋换算测试：9 换算型天赋 hp20 量纲 + v1 回归。"""
import unittest

from engine import experiments
from engine.game_state import GameState
from models.player import Player
from models.equipment import make_weapon, make_armor
from controllers.forfeit_controller import ForfeitController
from engine.game_setup import TALENT_TABLE


def _enable():
    for f in ("k_initiative", "hp20", "m3_accuracy", "m4_gear", "m7_talents"):
        experiments.enable(f)


def _talent_cls(keyword):
    return next(c for n, nm, c, d in TALENT_TABLE if keyword in nm)


def _state_with(attacker_talent=None, target_talent=None):
    state = GameState()
    a = Player("p1", "攻", controller=ForfeitController())
    a.is_awake = True
    a.location = "商店"
    state.add_player(a)
    t = Player("p2", "受", controller=ForfeitController())
    t.is_awake = True
    t.location = "商店"
    state.add_player(t)
    if attacker_talent:
        a.talent = _talent_cls(attacker_talent)("p1", state)
        a.talent.on_register()
    if target_talent:
        t.talent = _talent_cls(target_talent)("p2", state)
        t.talent.on_register()
    state.markers.add_relation("p1", "ENGAGED_WITH", "p2")
    state.markers.add_relation("p2", "ENGAGED_WITH", "p1")
    return state, a, t


class TalentConversionTest(unittest.TestCase):

    def setUp(self):
        experiments.reset()
        _enable()

    def tearDown(self):
        experiments.reset()

    def test_g1_attack_bonus(self):
        from combat.damage_resolver import resolve_damage
        state, a, t = _state_with(attacker_talent="火萤")
        r = resolve_damage(a, t, make_weapon("小刀"), state)
        self.assertEqual(r["final_damage"], 7)  # 小刀4 + 攻击+3

    def test_g1_defense_reduction(self):
        from combat.damage_resolver import resolve_damage
        state, a, t = _state_with(target_talent="火萤")
        r = resolve_damage(a, t, make_weapon("小刀"), state)
        self.assertEqual(r["final_damage"], 2)  # 小刀4 − 防御2

    def test_g1_self_heal(self):
        state, a, _ = _state_with(attacker_talent="火萤")
        a.hp = 8
        a.talent.on_turn_start(a)
        self.assertEqual(a.hp, 11)  # HP<10 回3

    def test_g4_savior_temp_hp(self):
        state, a, _ = _state_with(attacker_talent="愿负世")
        a.talent.divinity = 6
        a.hp = 0
        r = a.talent.on_death_check(a, None)
        self.assertEqual(r["new_hp"], 1)
        self.assertEqual(a.talent.temp_hp, 12)         # 6×2
        self.assertEqual(a.talent.temp_attack_bonus, 6)
        self.assertEqual(a.talent.savior_duration, 4)  # 2+ceil(6/3)

    def test_t7_revive_hp(self):
        from talents.talent_balance import talent_num
        self.assertEqual(talent_num("t7", "revive_hp", v1=1.0), 12)

    def test_t1_double_pierce(self):
        from combat.damage_resolver import resolve_damage
        state, a, t = _state_with()
        t.armor.outer.append(make_armor("陶瓷护甲"))  # 普防3
        r = resolve_damage(a, t, make_weapon("小刀"), state,
                           damage_multiplier=2.0, ignore_counter=True,
                           armor_pierce_factor=0.5)
        # 8 裸 − round(3×0.5)=1 → 但 round(1.5)=2 → 8-2=6
        self.assertEqual(r["final_damage"], 6)

    def test_t2_shield_durability_recovery(self):
        from talents.talent_balance import talent_num
        self.assertEqual(talent_num("t2", "shield_recovery_durability", v1=4), 4)


class V1RegressionTest(unittest.TestCase):
    """m7 关闭：天赋 v1 量纲不变。"""

    def setUp(self):
        experiments.reset()

    def test_g1_v1_multiplier(self):
        from engine.game_state import GameState as GS
        state = GS()
        a = Player("p1", "火萤", controller=ForfeitController())
        state.add_player(a)
        a.talent = _talent_cls("火萤")("p1", state)
        mod = a.talent.modify_outgoing_damage(a, None, None, 1.0)
        self.assertEqual(mod, {"damage_multiplier_override": 2.0})  # v1 ×2


if __name__ == "__main__":
    unittest.main()
