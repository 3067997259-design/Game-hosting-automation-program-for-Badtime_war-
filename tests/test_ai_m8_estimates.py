"""M8.1 步骤 3：估算接地（D1/D5/D6）——m8_ai 分叉测试。

- on 路径：effective_hp/estimate_talent_adjusted_damage 读 talent_num（D5）；
  estimate_power 读 balance.ai 权重（D6）；克制判定改净伤（D1）。
- off 路径：与旧行为逐字一致（D5 火萤 ×2.0、ardent ×0.5、硬克制二元）。
"""
import unittest
from types import SimpleNamespace

from engine import experiments
from models.equipment import Weapon, WeaponRange
from utils.attribute import Attribute

from controllers.ai.game_query import GameQuery


class _FakePiece:
    def __init__(self, name, attribute, defense_map, durability):
        self.name = name
        self.attribute = attribute
        self.defense_map = defense_map
        self.durability = durability


class _FakeArmor:
    def __init__(self, outer):
        self.outer = outer

    def get_active(self, layer):
        from models.equipment import ArmorLayer
        if layer == ArmorLayer.OUTER:
            return list(self.outer)
        return []


class _FakeTarget:
    def __init__(self, outer=None, inner_defense=None, hp=20, location="home"):
        self.armor = _FakeArmor(outer or [])
        self.inner_defense = inner_defense or {}
        self.hp = hp
        self.location = location


class _FakePlayer:
    def __init__(self, weapons, talent=None, hp=20, has_detection=False):
        self.weapons = weapons
        self.talent = talent
        self.hp = hp
        self.has_detection = has_detection
        self.armor = None


def _weapon(name="小刀", attr=Attribute.ORDINARY, damage=4, wrange=WeaponRange.MELEE,
            requires_charge=False, charged_damage=None, is_charged=False):
    w = Weapon(name, attr, damage, wrange,
               requires_charge=requires_charge, charged_damage=charged_damage)
    w.is_charged = is_charged
    return w


def _firefly_talent():
    return SimpleNamespace(name="火萤IV型-完全燃烧")


def _savior_talent(temp_attack_bonus=2.0, aoe_bonus=1.0):
    return SimpleNamespace(name="愿负世，照拂黎明", is_savior=True,
                           temp_attack_bonus=temp_attack_bonus, aoe_bonus=aoe_bonus)


def _ardent_talent(charges):
    return SimpleNamespace(name="火萤IV型-完全燃烧", temp_hp=0.0,
                           ardent_wish_charges=charges)


def _enable(*flags):
    experiments.reset()
    for f in flags:
        experiments.enable(f)


class EffectiveHpGatedTest(unittest.TestCase):

    def test_on_reads_balance_ardent_value(self) -> None:
        """m8_ai+m7：炽愿每层读 balance.talents.g1.ardent_temp_hp(=3)。"""
        _enable("m8_ai", "m7_talents")
        p = _FakePlayer([], talent=_ardent_talent(2))
        self.assertEqual(GameQuery.get_effective_hp(p), 20 + 6.0)

    def test_on_without_m7_falls_back_v1(self) -> None:
        _enable("m8_ai")
        p = _FakePlayer([], talent=_ardent_talent(2))
        self.assertEqual(GameQuery.get_effective_hp(p), 20 + 1.0)

    def test_off_keeps_verbatim_hardcode(self) -> None:
        _enable()
        p = _FakePlayer([], talent=_ardent_talent(2))
        self.assertEqual(GameQuery.get_effective_hp(p), 20 + 1.0)

    def test_temp_hp_passthrough_both_paths(self) -> None:
        for flags in (("m8_ai",), ()):
            _enable(*flags)
            talent = SimpleNamespace(temp_hp=3.0, ardent_wish_charges=0)
            p = _FakePlayer([], talent=talent)
            self.assertEqual(GameQuery.get_effective_hp(p), 23.0)


class TalentAdjustedDamageGatedTest(unittest.TestCase):

    def test_firefly_on_uses_attack_bonus(self) -> None:
        """m8_ai：火萤 = base + talent_num(attack_bonus)；m7 下 balance=3。"""
        _enable("m8_ai", "m7_talents")
        p = _FakePlayer([_weapon()], talent=_firefly_talent())
        self.assertEqual(GameQuery.estimate_talent_adjusted_damage(p), 4 + 3.0)

    def test_firefly_off_keeps_verbatim_multiplier(self) -> None:
        _enable()
        p = _FakePlayer([_weapon()], talent=_firefly_talent())
        self.assertEqual(GameQuery.estimate_talent_adjusted_damage(p), 8.0)

    def test_savior_live_attributes_both_paths(self) -> None:
        for flags in (("m8_ai",), ()):
            _enable(*flags)
            p = _FakePlayer([_weapon()], talent=_savior_talent())
            self.assertEqual(GameQuery.estimate_talent_adjusted_damage(p), 4 + 2.0)
            p2 = _FakePlayer([_weapon(wrange=WeaponRange.AREA)],
                             talent=_savior_talent())
            area_w = p2.weapons[0]
            self.assertEqual(GameQuery.estimate_talent_adjusted_damage(p2, area_w), 4 + 1.0)


class EstimatePowerGatedTest(unittest.TestCase):

    def test_on_reads_balance_ai_weights(self) -> None:
        """默认权重与旧魔数相同 → 平凡玩家 on/off 同分。"""
        _enable("m8_ai")
        p = _FakePlayer([_weapon()], has_detection=True)
        self.assertEqual(GameQuery.estimate_power(p), 20 * 10 + 4 * 15 + 5)
        _enable()
        self.assertEqual(GameQuery.estimate_power(p), 20 * 10 + 4 * 15 + 5)

    def test_on_firefly_grounded_bonus_flows_into_power(self) -> None:
        """m8_ai 火萤 +3（非 ×2）→ power 的伤害项随之接地。"""
        _enable("m8_ai")
        p = _FakePlayer([_weapon()], talent=_firefly_talent())
        self.assertEqual(GameQuery.estimate_power(p), 20 * 10 + (4 + 3.0) * 15)

    def test_off_firefly_multiplier_flows_into_power(self) -> None:
        _enable()
        p = _FakePlayer([_weapon()], talent=_firefly_talent())
        self.assertEqual(GameQuery.estimate_power(p), 20 * 10 + 8.0 * 15)


class HardCounterGatedTest(unittest.TestCase):

    def _magic_armor_target(self):
        return _FakeTarget(outer=[_FakePiece("魔法护盾", Attribute.MAGIC,
                                             {"魔法": 2}, 12)])

    def test_all_weapons_countered_on_net_damage(self) -> None:
        """m8_ai：科技 vs 魔法甲 → 净伤 4 > 0 仍有效 → 不被判定为全被克制。"""
        _enable("m8_ai")
        p = _FakePlayer([_weapon(name="科技枪", attr=Attribute.TECH, damage=4)])
        self.assertFalse(GameQuery.all_weapons_countered(p, self._magic_armor_target()))

    def test_all_weapons_countered_off_binary(self) -> None:
        """off：科技 vs 魔法甲 → 硬克制二元，整把作废。"""
        _enable()
        p = _FakePlayer([_weapon(name="科技枪", attr=Attribute.TECH, damage=4)])
        self.assertTrue(GameQuery.all_weapons_countered(p, self._magic_armor_target()))

    def test_best_effective_on_uses_net_damage(self) -> None:
        """on：对普通-2 盾牌，小刀净伤 = 4−2 = 2；科技对魔法甲 = 满伤 4。"""
        _enable("m8_ai")
        p = _FakePlayer([_weapon()])
        shield = _FakeTarget(outer=[_FakePiece("盾牌", Attribute.ORDINARY, {"普通": 2}, 8)])
        self.assertEqual(GameQuery.best_effective_weapon_damage(p, shield), 2.0)
        p2 = _FakePlayer([_weapon(name="科技枪", attr=Attribute.TECH, damage=4)])
        self.assertEqual(GameQuery.best_effective_weapon_damage(p2, self._magic_armor_target()), 4.0)

    def test_best_effective_off_raw_with_binary_filter(self) -> None:
        """off：裸伤不过滤减防；科技 vs 魔法甲被整把跳过 → 0。"""
        _enable()
        p = _FakePlayer([_weapon()])
        shield = _FakeTarget(outer=[_FakePiece("盾牌", Attribute.ORDINARY, {"普通": 2}, 8)])
        self.assertEqual(GameQuery.best_effective_weapon_damage(p, shield), 4.0)
        p2 = _FakePlayer([_weapon(name="科技枪", attr=Attribute.TECH, damage=4)])
        self.assertEqual(GameQuery.best_effective_weapon_damage(p2, self._magic_armor_target()), 0.0)

    def test_has_effective_aoe_on_net_damage(self) -> None:
        """on：电磁步枪(科技) vs 魔法甲 → 净伤 4>0 → 有效（off 为 False）。"""
        _enable("m8_ai")
        p = _FakePlayer([_weapon(name="电磁步枪", attr=Attribute.TECH, damage=4,
                                 wrange=WeaponRange.RANGED, requires_charge=True,
                                 charged_damage=9, is_charged=True)])
        self.assertTrue(GameQuery.has_effective_aoe_against(p, self._magic_armor_target()))

    def test_has_effective_aoe_off_binary(self) -> None:
        _enable()
        p = _FakePlayer([_weapon(name="电磁步枪", attr=Attribute.TECH, damage=4,
                                 wrange=WeaponRange.RANGED, requires_charge=True,
                                 charged_damage=9, is_charged=True)])
        self.assertFalse(GameQuery.has_effective_aoe_against(p, self._magic_armor_target()))


if __name__ == "__main__":
    unittest.main()
