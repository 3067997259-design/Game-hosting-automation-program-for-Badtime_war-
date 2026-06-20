"""G5 锚定核心 hp20 测试（M7 第二阶段 5/5，§7.5）。

G5a 部分：信源统一评估器 simulate_path —— 命数对拍 numeric_v2 手算
（减法防御 + 耐久磨损 + 破甲后伤害上升 + 蓄力 + 多武器贪心 + horizon 截断）。
后续 G5b/c 的模板/窗口/破坏性/善见天 v3 测试在本文件追加。
"""
import unittest

from engine import experiments
from models.player import Player
from models.equipment import make_armor, Weapon, WeaponRange
from utils.attribute import Attribute
from controllers.forfeit_controller import ForfeitController
from engine import anchor_eval


def _target(hp=20, with_shield=True):
    experiments.enable("hp20")
    p = Player("t", "T", controller=ForfeitController())
    p.hp = hp
    p.max_hp = hp
    if with_shield:
        p.armor.outer.append(make_armor("盾牌"))   # hp20: 普通防御2, 耐久8
    return p


def _knife():
    return Weapon("小刀", Attribute.ORDINARY, 4, WeaponRange.MELEE)


class SimulatePathTest(unittest.TestCase):
    def tearDown(self):
        experiments.reset()

    def test_kill_through_armor(self):
        # 盾牌 普通防御2/耐久8：raw4→伤2/吸2，破甲4轮(8/2)→裸伤4，剩12HP/4=3轮 → 共7
        r = anchor_eval.simulate_path(_target(), [_knife()],
                                      [("attack", None)] * 15, goal="kill")
        self.assertTrue(r.achieved)
        self.assertEqual(r.rounds, 7)

    def test_kill_no_armor(self):
        # 裸目标 20HP，raw4→伤4 → ceil(20/4)=5
        r = anchor_eval.simulate_path(_target(with_shield=False), [_knife()],
                                      [("attack", None)] * 15, goal="kill")
        self.assertTrue(r.achieved)
        self.assertEqual(r.rounds, 5)

    def test_break_armor(self):
        r = anchor_eval.simulate_path(_target(), [_knife()],
                                      [("attack", None)] * 15,
                                      goal="break_armor", break_piece="盾牌")
        self.assertTrue(r.achieved)
        self.assertEqual(r.rounds, 4)   # 8/2

    def test_horizon_infeasible(self):
        r = anchor_eval.simulate_path(_target(), [_knife()],
                                      [("attack", None)] * 5, goal="kill", horizon=5)
        self.assertFalse(r.achieved)
        self.assertEqual(r.rounds, 5)

    def test_charge_weapon(self):
        g = Weapon("高斯步枪", Attribute.TECH, 6, WeaponRange.RANGED,
                   requires_charge=True, charged_damage=8)
        g.charge_mandatory = True
        naked = _target(with_shield=False)
        seq = [("charge", g)] + [("attack", g)] * 5
        r = anchor_eval.simulate_path(naked, [g], seq, goal="kill")
        self.assertTrue(r.achieved)
        self.assertEqual(r.rounds, 4)   # 蓄力1 + ceil(20/8)=3

    def test_mandatory_charge_cannot_fire_uncharged(self):
        g = Weapon("高斯步枪", Attribute.TECH, 6, WeaponRange.RANGED,
                   requires_charge=True, charged_damage=8)
        g.charge_mandatory = True
        r = anchor_eval.simulate_path(_target(with_shield=False), [g],
                                      [("attack", g)] * 5, goal="kill", horizon=5)
        self.assertFalse(r.achieved)   # 没蓄力，强制蓄力武器打不出

    def test_greedy_picks_higher_net(self):
        # 两把武器：弱普通 vs 强科技；裸目标下贪心选 raw 高者
        weak = Weapon("小刀", Attribute.ORDINARY, 3, WeaponRange.MELEE)
        strong = Weapon("电磁步枪", Attribute.TECH, 7, WeaponRange.RANGED)
        r = anchor_eval.simulate_path(_target(with_shield=False), [weak, strong],
                                      [("attack", None)] * 15, goal="kill")
        self.assertTrue(r.achieved)
        self.assertEqual(r.rounds, 3)   # ceil(20/7)=3，证明选了 strong

    def test_no_weapon_not_achieved(self):
        r = anchor_eval.simulate_path(_target(), [],
                                      [("attack", None)] * 15, goal="kill", horizon=15)
        self.assertFalse(r.achieved)


if __name__ == "__main__":
    unittest.main()
