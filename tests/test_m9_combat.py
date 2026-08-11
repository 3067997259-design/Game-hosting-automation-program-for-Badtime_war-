"""M9 结算路径单测（阶段 1）：A/H 两阶段、DIRECT_DAMAGE、absolute_dead 分流、
M9 天赋钩子协议、result 结构兼容、v2exp 门控隔离。"""
import unittest
from types import SimpleNamespace

from engine import experiments
from engine.m9 import combat
from engine.m9.combat import DeathAdjudicator, resolve_damage
from models.equipment import ArmorLayer, ArmorPiece, Weapon, WeaponRange
from utils.attribute import Attribute


def _enable(*flags):
    experiments.reset()
    for f in flags:
        experiments.enable(f)


class _FakePlayer:
    def __init__(self, pid="p1", hp=20, talent=None, armor=None):
        self.player_id = pid
        self.hp = hp
        self.max_hp = 20
        self.talent = talent
        self.armor = armor

    def is_alive(self):
        return self.hp > 0


def _weapon(name="小刀", attr=Attribute.ORDINARY, damage=4):
    return Weapon(name, attr, damage, WeaponRange.MELEE)


def _shield_target(hp=20):
    piece = ArmorPiece("盾牌", Attribute.ORDINARY, ArmorLayer.OUTER, 1.0,
                       defense_map={"普通": 2}, durability=8)
    armor = type("Armor", (), {"outer": [piece]})()
    return _FakePlayer(hp=hp, armor=armor), piece


class AHPhaseTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_subtractive_defense_and_durability(self) -> None:
        """5 伤 vs 普通-2 盾：A 阶段吸收 2（耐久 8→6），H 阶段 3。"""
        target, piece = _shield_target()
        r = resolve_damage(_FakePlayer("a"), target, _weapon(damage=5), None)
        self.assertEqual(r["final_damage"], 3)
        self.assertEqual(r["hp_damage"], 3)
        self.assertEqual(piece.durability, 6)
        self.assertTrue(r["success"])

    def test_min_damage_floor(self) -> None:
        """4 伤 vs 普通-10 防：吃满也至少掉 1（⌈4×25%⌉）。"""
        piece = ArmorPiece("盾牌", Attribute.ORDINARY, ArmorLayer.OUTER, 1.0,
                           defense_map={"普通": 10}, durability=8)
        target = _FakePlayer(hp=20,
                             armor=type("A", (), {"outer": [piece]})())
        r = resolve_damage(_FakePlayer("a"), target, _weapon(damage=4), None)
        self.assertEqual(r["final_damage"], 1)

    def test_broken_armor_reported(self) -> None:
        """盾耐久 2、6 伤：吸收 2 磨碎，其余 4 进 HP。"""
        piece = ArmorPiece("盾牌", Attribute.ORDINARY, ArmorLayer.OUTER, 1.0,
                           defense_map={"普通": 2}, durability=2)
        target = _FakePlayer(hp=20,
                             armor=type("A", (), {"outer": [piece]})())
        r = resolve_damage(_FakePlayer("a"), target, _weapon(damage=6), None)
        self.assertTrue(r["armor_broken"])
        self.assertEqual(r["armor_hit"], "盾牌")
        self.assertEqual(r["hp_damage"], 4)


class DirectDamageTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_direct_damage_identity_h_equals_a(self) -> None:
        """DIRECT_DAMAGE：跳过防御/护甲，H=A（伤 5 直扣 5，盾耐久不动）。"""
        target, piece = _shield_target()
        hit = combat._apply_attack(target, 5, "普通", direct_damage=True)
        self.assertEqual(hit.damage, 5)
        self.assertEqual(hit.defense, 0)
        self.assertEqual(piece.durability, 8)
        self.assertTrue(hit.direct_damage)
        self.assertFalse(hit.absolute_death)


class DeathAdjudicationTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_absolute_death_skips_insurance(self) -> None:
        """绝对死亡来源：T7 保险不赔付，直进死亡。"""
        insured = SimpleNamespace(m9_on_lethal=None)
        talent = SimpleNamespace(on_death_check=lambda t, a: {"prevent_death": True,
                                                              "new_hp": 10})
        target = _FakePlayer(hp=0, talent=talent)
        kind = DeathAdjudicator(None).adjudicate(target, None, "g7_terror")
        self.assertEqual(kind, "dead")

    def test_normal_death_pays_insurance(self) -> None:
        """普通来源：免死/保险照常赔付。"""
        talent = SimpleNamespace(on_death_check=lambda t, a: {"prevent_death": True,
                                                              "new_hp": 10})
        target = _FakePlayer(hp=0, talent=talent)
        kind = DeathAdjudicator(None).adjudicate(target, None, "normal")
        self.assertEqual(kind, "prevented")
        self.assertEqual(target.hp, 10)

    def test_talent_lethal_substitute(self) -> None:
        """M9 天赋协议：m9_on_lethal 返回替代 kind → 非玩家死亡。"""
        talent = SimpleNamespace(m9_on_lethal=lambda t, a, s: "g5_homecoming")
        target = _FakePlayer(hp=0, talent=talent)
        kind = DeathAdjudicator(None).adjudicate(target, None, "normal")
        self.assertEqual(kind, "g5_homecoming")

    def test_absolute_death_beats_substitute(self) -> None:
        """绝对死亡压制所有替代（G5 归家/G1 繁育等）。"""
        talent = SimpleNamespace(m9_on_lethal=lambda t, a, s: "g5_homecoming")
        target = _FakePlayer(hp=0, talent=talent)
        kind = DeathAdjudicator(None).adjudicate(target, None, "g5_anchor")
        self.assertEqual(kind, "dead")


class TalentHookProtocolTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_incoming_vulnerability_hook(self) -> None:
        """m9_modify_incoming：H 阶段易伤挂载（G2 终曲全员易伤等）。"""
        class _Hooked:
            def m9_modify_incoming(self, hit):
                hit.damage += 2  # 易伤 +2

        target, _ = _shield_target(hp=20)
        target.talent = _Hooked()
        r = resolve_damage(_FakePlayer("a"), target, _weapon(damage=5), None)
        self.assertEqual(r["final_damage"], 5)  # 3 + 2

    def test_outgoing_modifier_hook(self) -> None:
        class _Boost:
            def m9_modify_outgoing(self, attacker, target, weapon, raw):
                return raw + 3  # 强化普攻 +3

        attacker = _FakePlayer("a", talent=_Boost())
        target, _ = _shield_target()
        r = resolve_damage(attacker, target, _weapon(damage=5), None)
        self.assertEqual(r["final_damage"], 6)  # 8-2

    def test_result_structure_compatible(self) -> None:
        """result dict 与 v2exp 消费端兼容（killed/hp_damage/target_hp/...）。"""
        target, _ = _shield_target(hp=3)
        r = resolve_damage(_FakePlayer("a"), target, _weapon(damage=5), None)
        self.assertEqual(r["killed"], True)
        self.assertEqual(r["target_hp"], 0)
        for key in ("success", "raw_damage", "final_damage", "armor_broken",
                    "hp_damage", "target_hp_before", "details"):
            self.assertIn(key, r)


class GateIsolationTest(unittest.TestCase):

    def tearDown(self) -> None:
        experiments.reset()

    def test_v2exp_path_untouched(self) -> None:
        """m9_rfc 关：resolve_damage 走 v2exp 原管线（成功结构一致、防御照旧）。"""
        _enable("hp20")
        from combat.damage_resolver import resolve_damage as v2_resolve
        target, _ = _shield_target()
        r = v2_resolve(_FakePlayer("a"), target, _weapon(damage=5), None)
        self.assertEqual(r["final_damage"], 3)
        self.assertNotIn("m9_kind", r)

    def test_m9_gate_active(self) -> None:
        _enable("m9_rfc", "hp20")
        from combat.damage_resolver import resolve_damage as v2_resolve
        target, _ = _shield_target()
        r = v2_resolve(_FakePlayer("a"), target, _weapon(damage=5), None)
        self.assertEqual(r["m9_kind"], "")


if __name__ == "__main__":
    unittest.main()
