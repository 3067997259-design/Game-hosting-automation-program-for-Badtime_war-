"""M2 HP20 集成测试：开关下的端到端行为断言 + v1 路径回归。

setUp/tearDown 负责实验开关收尾，不污染同进程其他测试。
"""
import unittest

from engine import experiments
from engine.game_state import GameState
from models.player import Player
from models.equipment import make_weapon, make_armor
from controllers.forfeit_controller import ForfeitController
from combat.damage_resolver import resolve_damage


def _player(pid: str, state: GameState) -> Player:
    p = Player(pid, f"玩家{pid}", controller=ForfeitController())
    p.is_awake = True
    p.location = "商店"
    state.add_player(p)
    return p


class Hp20PipelineTest(unittest.TestCase):

    def setUp(self) -> None:
        experiments.reset()
        experiments.enable("hp20")
        self.state = GameState()
        self.attacker = _player("p1", self.state)
        self.target = _player("p2", self.state)

    def tearDown(self) -> None:
        experiments.reset()

    def test_initial_values(self) -> None:
        self.assertEqual(self.target.hp, 20)
        self.assertEqual(self.target.max_hp, 20)

    def test_knife_hits_naked_for_4(self) -> None:
        knife = make_weapon("小刀")
        result = resolve_damage(self.attacker, self.target, knife, self.state)
        self.assertEqual(result["final_damage"], 4)
        self.assertEqual(self.target.hp, 16)

    def test_armor_subtracts_and_wears(self) -> None:
        """陶瓷（普3）vs 小刀 4 → 实伤 1、耐久 12→9。"""
        ceramic = make_armor("陶瓷护甲")
        self.target.armor.outer.append(ceramic)
        knife = make_weapon("小刀")
        result = resolve_damage(self.attacker, self.target, knife, self.state)
        self.assertEqual(result["final_damage"], 1)
        self.assertEqual(self.target.hp, 19)
        self.assertEqual(ceramic.durability, 9)

    def test_no_hard_counter(self) -> None:
        """硬克制废除：魔法弹幕打陶瓷（v1 会被克制无效）照常结算。"""
        ceramic = make_armor("陶瓷护甲")
        self.target.armor.outer.append(ceramic)
        barrage = make_weapon("魔法弹幕")
        result = resolve_damage(self.attacker, self.target, barrage, self.state)
        # 陶瓷无魔防 → 5 点全进
        self.assertEqual(result["final_damage"], 5)
        self.assertTrue(result["success"])

    def test_armor_breaks_and_is_removed(self) -> None:
        """盾牌耐久 8：磨刀连击至耐久归零后从槽位移除。"""
        shield = make_armor("盾牌")
        self.target.armor.outer.append(shield)
        knife = make_weapon("小刀")
        knife.base_damage = 7  # 磨刀小刀
        for _ in range(3):  # 每刀吸收 2（防2），3 刀 6 + 第4刀碎
            resolve_damage(self.attacker, self.target, knife, self.state)
        resolve_damage(self.attacker, self.target, knife, self.state)
        self.assertNotIn(shield, self.target.armor.outer)

    def test_hp_zero_is_death_not_stun(self) -> None:
        """HP≤0 直接死亡，不再有 0 血躺尸眩晕。"""
        self.target.hp = 3
        knife = make_weapon("小刀")
        result = resolve_damage(self.attacker, self.target, knife, self.state)
        self.assertTrue(result["killed"])
        self.assertFalse(result["stunned"])
        self.assertFalse(self.target.is_alive())

    def test_no_stun_above_zero(self) -> None:
        """HP 降到低值（v1 会眩晕的区间）不触发眩晕。"""
        self.target.hp = 5
        knife = make_weapon("小刀")
        result = resolve_damage(self.attacker, self.target, knife, self.state)
        self.assertFalse(result["stunned"])
        self.assertEqual(self.target.hp, 1)
        self.assertTrue(self.target.is_alive())

    def test_severe_injury_initiative_penalty(self) -> None:
        """重伤（HP≤5）先攻 −2。"""
        self.target.hp = 4
        self.assertEqual(self.target.get_initiative_bonus(), -2)
        self.target.hp = 6
        self.assertEqual(self.target.get_initiative_bonus(), 0)


class Hp20SurgeryTest(unittest.TestCase):

    def setUp(self) -> None:
        experiments.reset()
        experiments.enable("hp20")
        self.state = GameState()
        self.p = _player("p1", self.state)
        self.p.vouchers = 2

    def tearDown(self) -> None:
        experiments.reset()

    def test_extra_heart(self) -> None:
        from locations.hospital import do_interact
        self.p.hp = 10
        do_interact(self.p, "额外心脏手术", self.state)
        self.assertEqual(self.p.max_hp, 24)
        self.assertEqual(self.p.hp, 14)
        self.assertEqual(self.p.vouchers, 0)
        self.assertIn("额外心脏", self.p.surgeries_done)

    def test_crystal_skin_is_permanent_defense(self) -> None:
        from locations.hospital import do_interact
        do_interact(self.p, "晶化皮肤手术", self.state)
        self.assertEqual(self.p.inner_defense.get("普通"), 1)
        self.assertEqual(self.p.inner_defense.get("科技"), 1)
        # 不再产生内甲 piece
        self.assertEqual(len(self.p.armor.inner), 0)

    def test_surgery_once_per_life(self) -> None:
        from locations.hospital import can_interact, do_interact
        do_interact(self.p, "不老泉手术", self.state)
        self.assertEqual(self.p.regen_per_round, 1)
        self.p.vouchers = 1
        ok, _reason = can_interact(self.p, "不老泉手术", self.state)
        self.assertFalse(ok)


class V1RegressionTest(unittest.TestCase):
    """开关关闭：v1 量纲与行为不变（防 hp20 分支泄漏）。"""

    def setUp(self) -> None:
        experiments.reset()

    def test_v1_initial_values(self) -> None:
        state = GameState()
        p = _player("p1", state)
        self.assertEqual(p.hp, 1.0)
        self.assertEqual(make_weapon("小刀").base_damage, 1.0)
        shield = make_armor("盾牌")
        self.assertEqual(shield.durability, 0)  # v1 不带耐久
        self.assertEqual(shield.current_hp, 1.0)


if __name__ == "__main__":
    unittest.main()
