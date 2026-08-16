"""M8.1 步骤 5：消费端收口单测——weapon_score×3 / 警察穿透 / 星野弹药 / 护甲克制。

- on 路径：全部经 net_damage（净伤语义，克制折进防御表）。
- off 路径：逐字旧行为（裸伤 + 硬克制二元）。
"""
import unittest
from types import SimpleNamespace

from engine import experiments
from models.equipment import Weapon, WeaponRange
from utils.attribute import Attribute

from controllers.ai.game_query import GameQuery
from controllers.ai.minds.combat_mind import CombatMind
from controllers.ai.minds.police_mind import PoliceMind


class _FakePiece:
    def __init__(self, name, attribute, defense_map, durability):
        self.name = name
        self.attribute = attribute
        self.defense_map = defense_map
        self.durability = durability
        self.is_broken = False
        from models.equipment import ArmorLayer
        self.layer = ArmorLayer.OUTER


class _FakeArmor:
    def __init__(self, outer):
        self.outer = outer

    def get_active(self, layer):
        from models.equipment import ArmorLayer
        if layer == ArmorLayer.OUTER:
            return list(self.outer)
        return []

    def get_all_active(self):
        return list(self.outer)


class _FakeTarget:
    def __init__(self, outer=None, inner_defense=None, hp=20, location="home",
                 player_id="t1", name="T1"):
        self.armor = _FakeArmor(outer or [])
        self.inner_defense = inner_defense or {}
        self.hp = hp
        self.location = location
        self.player_id = player_id
        self.name = name

    def is_alive(self):
        return self.hp > 0


class _FakePlayer:
    def __init__(self, weapons, talent=None, hp=20, player_id="p1", name="P1",
                 location="home"):
        self.weapons = weapons
        self.talent = talent
        self.hp = hp
        self.player_id = player_id
        self.name = name
        self.location = location
        self.armor = None
        self.is_captain = False

    def is_alive(self):
        return self.hp > 0


def _weapon(name="小刀", attr=Attribute.ORDINARY, damage=4, wrange=WeaponRange.MELEE,
            requires_charge=False, charged_damage=None, is_charged=False):
    w = Weapon(name, attr, damage, wrange,
               requires_charge=requires_charge, charged_damage=charged_damage)
    w.is_charged = is_charged
    return w


def _magic_shield():
    return _FakePiece("魔法护盾", Attribute.MAGIC, {"魔法": 3}, 12)


def _enable(*flags):
    experiments.reset()
    for f in flags:
        experiments.enable(f)


class PickWeaponGatedTest(unittest.TestCase):

    def setUp(self) -> None:
        self.player = _FakePlayer([
            _weapon(name="科技枪", attr=Attribute.TECH, damage=4),
            _weapon(name="小刀", attr=Attribute.ORDINARY, damage=4),
        ])
        self.target = _FakeTarget(outer=[_magic_shield()])
        self.mind = CombatMind(debug_name="test", query=GameQuery)

    def tearDown(self) -> None:
        experiments.reset()

    def test_on_prefers_net_damage(self) -> None:
        """魔法甲只防魔法：on 下科技枪净伤 4 = 小刀净伤 4 → 稳定序取首（科技枪）。"""
        _enable("m8_ai")
        picked = self.mind._pick_weapon(self.player, self.target)
        self.assertEqual(picked.name, "科技枪")

    def test_off_prefers_effective_binary(self) -> None:
        """off：科技 vs 魔法甲被二元克制 −50 → 选小刀。"""
        _enable()
        picked = self.mind._pick_weapon(self.player, self.target)
        self.assertEqual(picked.name, "小刀")


class PoliceProtectionGatedTest(unittest.TestCase):

    def setUp(self) -> None:
        self.player = _FakePlayer([_weapon(name="小刀", damage=4)])
        self.target = _FakeTarget(outer=[_FakePiece("盾牌", Attribute.ORDINARY,
                                                    {"普通": 2}, 8)])
        self.mind = PoliceMind(debug_name="test", query=GameQuery)

    def tearDown(self) -> None:
        experiments.reset()

    def _call(self):
        return self.mind.can_damage_through_protection(
            self.player, self.target, None,
            talent_adjusted_damage=4.0,
            outer_armor_attrs=set([Attribute.ORDINARY]),
            inner_armor_attrs=set(),
            aoe_weapon_names=[],
            player_weapons=self.player.weapons,
            learned_spells=set(),
        )

    def test_on_net_damage_below_threshold(self) -> None:
        """on：净伤 4−2=2 ≤ 阈值 3 → 无法穿透（off 裸伤 4 > 3 可硬穿）。"""
        _enable("m8_ai")
        pe = SimpleNamespace(is_protected_by_police=lambda pid: True,
                             get_protection_threshold=lambda pid: 3)
        can, _ = self._call_with(pe, 3)
        self.assertFalse(can)

    def _call_with(self, pe, threshold):
        return self.mind.can_damage_through_protection(
            self.player, self.target, SimpleNamespace(police_engine=pe),
            talent_adjusted_damage=4.0,
            outer_armor_attrs=set([Attribute.ORDINARY]),
            inner_armor_attrs=set(),
            aoe_weapon_names=[],
            player_weapons=self.player.weapons,
            learned_spells=set(),
        )

    def test_off_raw_above_threshold(self) -> None:
        _enable()
        pe = SimpleNamespace(is_protected_by_police=lambda pid: True,
                             get_protection_threshold=lambda pid: 3)
        can, _ = self._call_with(pe, 3)
        self.assertTrue(can)


class HoshinoAmmoGatedTest(unittest.TestCase):

    def setUp(self) -> None:
        self.target = _FakeTarget(outer=[_FakePiece("盾牌", Attribute.ORDINARY,
                                                    {"普通": 2}, 8)])
        self.talent = SimpleNamespace(ammo=[{"attribute": "魔法"}])

    def tearDown(self) -> None:
        experiments.reset()

    def test_on_any_net_damage_counts(self) -> None:
        """on：魔法弹 vs 普通-2 盾 → 净伤 > 0 仍有效（无硬克制）。"""
        _enable("m8_ai")
        from controllers.ai.talents.hoshino_impl import HoshinoImpl
        impl = HoshinoImpl(controller=None)
        player = _FakePlayer([], talent=self.talent)
        self.assertTrue(impl._hoshino_can_effectively_shoot(player, self.target))

    def test_off_binary_counter_blocks(self) -> None:
        """off：魔法弹无法克制普通甲 → 判定无效。"""
        _enable()
        from controllers.ai.talents.hoshino_impl import HoshinoImpl
        impl = HoshinoImpl(controller=None)
        player = _FakePlayer([], talent=self.talent)
        self.assertFalse(impl._hoshino_can_effectively_shoot(player, self.target))


if __name__ == "__main__":
    unittest.main()
