"""M8.1 步骤 2：GameQuery net_damage / rounds_to_kill 原语表驱动单测（m8_ai on）。

原语为新增代码（无旧行为可保），调用点才做 m8_ai 门控（步骤 3–5 消费端收口）。
期望值全部手工按 numeric_v2 公式推导：damage = max(raw − defense, ⌈raw×25%⌉)。
"""
import unittest

from engine import experiments
from models.equipment import Weapon, WeaponRange
from utils.attribute import Attribute

from controllers.ai.game_query import GameQuery


class _FakePiece:
    def __init__(self, name, defense_map, durability):
        self.name = name
        self.defense_map = defense_map
        self.durability = durability


class _FakeArmor:
    def __init__(self, outer):
        self.outer = outer


class _FakeTarget:
    def __init__(self, outer=None, inner_defense=None, hp=20, location="home"):
        self.armor = _FakeArmor(outer or [])
        self.inner_defense = inner_defense or {}
        self.hp = hp
        self.location = location


class _FakeAttacker:
    def __init__(self, weapons, location="home", player_id="p1"):
        self.weapons = weapons
        self.location = location
        self.player_id = player_id


def _weapon(name="手斧", attr=Attribute.ORDINARY, damage=5, wrange=WeaponRange.MELEE,
            requires_charge=False, charged_damage=None, is_charged=False):
    w = Weapon(name, attr, damage, wrange,
               requires_charge=requires_charge, charged_damage=charged_damage)
    w.is_charged = is_charged
    return w


class NetDamageTableTest(unittest.TestCase):

    def setUp(self) -> None:
        experiments.reset()
        experiments.enable("m8_ai")

    def tearDown(self) -> None:
        experiments.reset()

    def _assert_table(self, rows):
        """表驱动：每行 (weapon, target, expected)。"""
        attacker = _FakeAttacker([])
        for weapon, target, expected in rows:
            with self.subTest(weapon=weapon.name, hp=target.hp):
                self.assertEqual(
                    GameQuery.net_damage(attacker, weapon, target), expected)

    def test_subtractive_against_single_piece(self) -> None:
        target = _FakeTarget(outer=[_FakePiece("盾牌", {"普通": 2}, 8)])
        rows = [
            (_weapon(damage=5), target, 3.0),
            (_weapon(damage=4), target, 2.0),
        ]
        self._assert_table(rows)

    def test_defense_capped_by_min_damage_floor(self) -> None:
        rows = [
            (_weapon(damage=4), _FakeTarget(outer=[_FakePiece("盾牌", {"普通": 10}, 8)]), 1.0),
            (_weapon(damage=8), _FakeTarget(outer=[_FakePiece("盾牌", {"普通": 8}, 8)]), 2.0),
        ]
        self._assert_table(rows)

    def test_inner_defense_aggregates(self) -> None:
        target = _FakeTarget(outer=[_FakePiece("盾牌", {"普通": 2}, 8)],
                             inner_defense={"普通": 4})
        # 6 伤 vs 6 防 → 吃满也至少掉 ⌈6×25%⌉=2
        self.assertEqual(GameQuery.net_damage(_FakeAttacker([]),
                                              _weapon(damage=6), target), 2.0)
        self.assertEqual(GameQuery.net_damage(_FakeAttacker([]),
                                              _weapon(damage=10), target), 4.0)

    def test_multi_piece_aggregation(self) -> None:
        target = _FakeTarget(
            outer=[_FakePiece("盾牌", {"普通": 2}, 8),
                   _FakePiece("陶瓷护甲", {"普通": 3, "科技": 1}, 12)],
            inner_defense={"普通": 1},
        )
        self.assertEqual(GameQuery.net_damage(_FakeAttacker([]),
                                              _weapon(damage=10), target), 4.0)
        self.assertEqual(GameQuery.net_damage(_FakeAttacker([]),
                                              _weapon(damage=5, attr=Attribute.TECH), target), 4.0)

    def test_broken_piece_contributes_zero(self) -> None:
        target = _FakeTarget(outer=[_FakePiece("盾牌", {"普通": 2}, 0)])
        self.assertEqual(GameQuery.net_damage(_FakeAttacker([]),
                                              _weapon(damage=5), target), 5.0)

    def test_charged_weapon_uses_charged_damage(self) -> None:
        target = _FakeTarget(outer=[_FakePiece("盾牌", {"普通": 2, "科技": 2}, 8)])
        charged = _weapon(name="电磁步枪", attr=Attribute.TECH, damage=5,
                          wrange=WeaponRange.RANGED, requires_charge=True,
                          charged_damage=9, is_charged=True)
        self.assertEqual(GameQuery.net_damage(_FakeAttacker([]), charged, target), 7.0)
        charged.is_charged = False
        self.assertEqual(GameQuery.net_damage(_FakeAttacker([]), charged, target), 3.0)

    def test_true_attribute_ignores_defense(self) -> None:
        target = _FakeTarget(outer=[_FakePiece("盾牌", {"普通": 2}, 8)])
        self.assertEqual(GameQuery.net_damage(_FakeAttacker([]),
                                              _weapon(attr=Attribute.TRUE, damage=5), target), 5.0)

    def test_no_armor_full_raw(self) -> None:
        self.assertEqual(GameQuery.net_damage(_FakeAttacker([]),
                                              _weapon(damage=5), _FakeTarget()), 5.0)

    def test_primitives_flag_independent(self) -> None:
        """原语是纯管道：m8_ai 关也不应报错（门控在调用点）。"""
        experiments.reset()
        target = _FakeTarget(outer=[_FakePiece("盾牌", {"普通": 2}, 8)])
        self.assertEqual(GameQuery.net_damage(_FakeAttacker([]),
                                              _weapon(damage=5), target), 3.0)


class RoundsToKillTest(unittest.TestCase):

    def setUp(self) -> None:
        experiments.reset()
        experiments.enable("m8_ai")

    def tearDown(self) -> None:
        experiments.reset()

    def test_kill_with_break_through(self) -> None:
        """5 伤 vs 盾牌(普通2,耐久8) 20hp：4 发×3=12 磨盾，2 发×5=10，+1 find = 7 轮。"""
        attacker = _FakeAttacker([_weapon(damage=5)])
        target = _FakeTarget(outer=[_FakePiece("盾牌", {"普通": 2}, 8)], hp=20)
        self.assertEqual(GameQuery.rounds_to_kill(attacker, target, state=None), 7)

    def test_beyond_horizon_returns_none(self) -> None:
        attacker = _FakeAttacker([_weapon(damage=1)])
        target = _FakeTarget(hp=20)
        self.assertIsNone(GameQuery.rounds_to_kill(attacker, target, state=None, horizon=8))

    def test_dead_target_zero_rounds(self) -> None:
        attacker = _FakeAttacker([_weapon(damage=5)])
        target = _FakeTarget(hp=0)
        self.assertEqual(GameQuery.rounds_to_kill(attacker, target, state=None), 0)

    def test_no_weapons_returns_none(self) -> None:
        self.assertIsNone(GameQuery.rounds_to_kill(_FakeAttacker([]), _FakeTarget(), state=None))

    def test_horizon_shortcuts_none(self) -> None:
        """horizon=3 时 20hp 打不死 → None（而默认 horizon 8 可杀）。"""
        attacker = _FakeAttacker([_weapon(damage=5)])
        target = _FakeTarget(outer=[_FakePiece("盾牌", {"普通": 2}, 8)], hp=20)
        self.assertIsNone(GameQuery.rounds_to_kill(attacker, target, state=None, horizon=3))
        self.assertEqual(GameQuery.rounds_to_kill(attacker, target, state=None), 7)

    def test_does_not_mutate_real_target(self) -> None:
        attacker = _FakeAttacker([_weapon(damage=5)])
        target = _FakeTarget(outer=[_FakePiece("盾牌", {"普通": 2}, 8)], hp=20)
        GameQuery.rounds_to_kill(attacker, target, state=None)
        self.assertEqual(target.hp, 20)
        self.assertEqual(target.armor.outer[0].durability, 8)


if __name__ == "__main__":
    unittest.main()
