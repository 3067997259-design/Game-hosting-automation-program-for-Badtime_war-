"""穿甲模块接线修复测试（M4，§2.8）。

回归一个 M4 遗留债：穿甲模块的"防御×0.5/×0.25"曾从未生效（compute_shot 漏读
armor_pierce、shoot 没传 pierce_factor）。本测验证 compute_shot 产出正确的
armor_pierce_factor，且 numeric_v2 按该系数减防。
"""
import unittest

from engine import experiments
from models.player import Player
from controllers.forfeit_controller import ForfeitController


def _player():
    experiments.enable("hp20")
    experiments.enable("m4_gear")
    p = Player("p", "P", controller=ForfeitController())
    p.bow_modules = []
    return p


class ComputeShotPierceTest(unittest.TestCase):
    def tearDown(self):
        experiments.reset()

    def test_no_pierce_default(self):
        from engine.bow_modules import compute_shot
        p = _player()
        self.assertEqual(compute_shot(p)["armor_pierce_factor"], 1.0)

    def test_single_pierce(self):
        from engine.bow_modules import compute_shot
        p = _player(); p.bow_modules = ["穿甲"]
        shot = compute_shot(p)
        self.assertEqual(shot["armor_pierce_factor"], 0.5)
        self.assertEqual(shot["weapon"].attribute.value, "科技")  # 穿甲改属性→科

    def test_double_pierce(self):
        from engine.bow_modules import compute_shot
        p = _player(); p.bow_modules = ["穿甲", "穿甲"]
        self.assertEqual(compute_shot(p)["armor_pierce_factor"], 0.25)

    def test_pierce_plus_power_stack(self):
        from engine.bow_modules import compute_shot
        p = _player(); p.bow_modules = ["穿甲", "力量"]
        shot = compute_shot(p)
        self.assertEqual(shot["armor_pierce_factor"], 0.5)   # 穿甲
        self.assertEqual(shot["weapon"].base_damage, 3 + 2)  # 力量 +2
        self.assertEqual(shot["weapon"].attribute.value, "科技")


class PierceDamageTest(unittest.TestCase):
    """numeric_v2 按 pierce_factor 减防（穿甲真生效）。"""
    def tearDown(self):
        experiments.reset()

    def test_pierce_halves_defense(self):
        experiments.enable("hp20")
        from combat.numeric_v2 import resolve_hit
        from models.equipment import ArmorPiece, ArmorLayer

        def _target():
            t = Player("t", "T", controller=ForfeitController())
            t.hp = 20
            piece = ArmorPiece("陶瓷护甲", None, ArmorLayer.OUTER, 20,
                               defense_map={"科技": 4}, durability=20)
            t.armor.outer.append(piece)
            return t

        # raw5 科技 vs 防御4（保底 ⌈5×.25⌉=2）：
        #   无穿甲 → max(5-4, 2)=2；穿甲0.5 → 防御2 → max(5-2, 2)=3
        plain = resolve_hit(_target(), 5, "科技", pierce_factor=1.0)
        pierced = resolve_hit(_target(), 5, "科技", pierce_factor=0.5)
        self.assertEqual(plain["damage"], 2)
        self.assertEqual(pierced["damage"], 3)
        self.assertGreater(pierced["damage"], plain["damage"])   # 穿甲真减防


if __name__ == "__main__":
    unittest.main()
