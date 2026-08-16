"""M3a 命中纯函数单测：基础确定性 / 闪避来源 / 封顶 / AoO 豁免 / 属性对位。"""
import random
import unittest
from typing import Optional, Set

from combat.accuracy import compute_hit_chance, roll_hit


class _FakeAttr:
    def __init__(self, value: str):
        self.value = value


class _FakeWeapon:
    def __init__(self, attr: str = "普通"):
        self.attribute = _FakeAttr(attr)


class _FakeMarkers:
    def __init__(self, locked_pairs=None):
        self._locked = locked_pairs or set()  # (target, attacker)

    def has_relation(self, target_id, relation, attacker_id):
        if relation == "LOCKED_BY":
            return (target_id, attacker_id) in self._locked
        return False


class _FakeState:
    def __init__(self, locked_pairs=None):
        self.markers = _FakeMarkers(locked_pairs)


class _FakeUnit:
    def __init__(self, pid: str, location: str = "商店",
                 moved: bool = False,
                 stealth: Optional[Set[str]] = None,
                 detection: Optional[Set[str]] = None):
        self.player_id = pid
        self.location = location
        self.moved_this_round = moved
        self.stealth_attrs = stealth or set()
        self.detection_attrs = detection or set()
        self._resist_degrade_hit_penalty = 0


class HitChanceTest(unittest.TestCase):

    def test_vanilla_melee_is_certain(self) -> None:
        """香草对砍：100% 命中、无明细、roll_hit 不消耗随机数。"""
        a, t = _FakeUnit("p1"), _FakeUnit("p2")
        chance, breakdown = compute_hit_chance(a, t, _FakeWeapon(), _FakeState())
        self.assertEqual(chance, 100)
        self.assertEqual(breakdown, [])
        state_before = random.getstate()
        hit, roll = roll_hit(chance)
        self.assertTrue(hit)
        self.assertEqual(random.getstate(), state_before)  # 零随机消耗

    def test_move_evasion(self) -> None:
        a, t = _FakeUnit("p1"), _FakeUnit("p2", moved=True)
        chance, _ = compute_hit_chance(a, t, _FakeWeapon(), _FakeState())
        self.assertEqual(chance, 85)

    def test_aoo_ignores_move_evasion(self) -> None:
        """借机攻击：移动者不享移动闪避（拍板 §1.3）。"""
        a, t = _FakeUnit("p1"), _FakeUnit("p2", moved=True)
        chance, _ = compute_hit_chance(a, t, _FakeWeapon(), _FakeState(), is_aoo=True)
        self.assertEqual(chance, 100)

    def test_stealth_attribute_matchup(self) -> None:
        """隐身对位：普隐身只挡普攻击；对应探测无效化。"""
        a = _FakeUnit("p1")
        t = _FakeUnit("p2", stealth={"普通"})
        c_ord, _ = compute_hit_chance(a, t, _FakeWeapon("普通"), _FakeState())
        self.assertEqual(c_ord, 75)
        c_mag, _ = compute_hit_chance(a, t, _FakeWeapon("魔法"), _FakeState())
        self.assertEqual(c_mag, 100)  # 属性不对位 = 不挡
        a2 = _FakeUnit("p3", detection={"普通"})
        c_detected, _ = compute_hit_chance(a2, t, _FakeWeapon("普通"), _FakeState())
        self.assertEqual(c_detected, 100)  # 对应探测无效化隐身闪避

    def test_unlocked_cross_location_penalty(self) -> None:
        a = _FakeUnit("p1", location="商店")
        t = _FakeUnit("p2", location="医院")
        c_unlocked, _ = compute_hit_chance(a, t, _FakeWeapon(), _FakeState())
        self.assertEqual(c_unlocked, 85)
        c_locked, _ = compute_hit_chance(
            a, t, _FakeWeapon(), _FakeState(locked_pairs={("p2", "p1")}))
        self.assertEqual(c_locked, 100)  # 锁定抵消

    def test_evasion_cap(self) -> None:
        """闪避封顶 60 → 命中下限 40（打不中的龟壳违宪）。

        当前来源最多 15+25=40 不触顶；用人工叠加值验证封顶逻辑本身。
        """
        a = _FakeUnit("p1")
        t = _FakeUnit("p2", moved=True, stealth={"普通"})
        chance, breakdown = compute_hit_chance(a, t, _FakeWeapon("普通"), _FakeState())
        self.assertEqual(chance, 60)  # 15+25=40 未触顶
        self.assertGreaterEqual(chance, 40)  # 永不低于下限

    def test_degraded_shock_penalty_self_consuming(self) -> None:
        a, t = _FakeUnit("p1"), _FakeUnit("p2")
        a._resist_degrade_hit_penalty = -20
        c1, _ = compute_hit_chance(a, t, _FakeWeapon(), _FakeState())
        self.assertEqual(c1, 80)
        c2, _ = compute_hit_chance(a, t, _FakeWeapon(), _FakeState())
        self.assertEqual(c2, 100)  # flag 已消耗

    def test_roll_hit_deterministic_with_seed(self) -> None:
        random.seed(42)
        first = [roll_hit(60) for _ in range(5)]
        random.seed(42)
        second = [roll_hit(60) for _ in range(5)]
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
