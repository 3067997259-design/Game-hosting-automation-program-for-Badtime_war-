"""Reviewer persistent-bug 修复回归测试。

覆盖跨轮审查标记的 4 个局部 bug：
1. ScriptBot.has_armor_named 忽略 is_broken；
2. PoliceUnit.reset_to_initial 不尊重 hp20 量纲；
3. damage_resolver 石化被攻击解除的显示计算使用 petrify_dmg；
4. G1 炽愿临时 HP 读取 talent_num 活值（hp20=3 / v1=0.5）。
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from engine import experiments
from engine.game_state import GameState
from models.equipment import make_armor, make_weapon
from models.player import Player
from models.police import PoliceUnit
from controllers.bots.script_bot import ScriptBotController
from controllers.forfeit_controller import ForfeitController


class PersistentBugRegressionTest(unittest.TestCase):
    def tearDown(self) -> None:
        experiments.reset()

    def test_has_armor_named_ignores_broken_armor(self) -> None:
        armor = SimpleNamespace(
            outer=[SimpleNamespace(name="盾牌", is_broken=True)],
            inner=[SimpleNamespace(name="陶瓷护甲", is_broken=False)],
        )
        player = SimpleNamespace(armor=armor)
        self.assertFalse(ScriptBotController.has_armor_named(player, "盾牌"))
        self.assertTrue(ScriptBotController.has_armor_named(player, "陶瓷护甲"))

    def test_police_reset_keeps_hp20_scale(self) -> None:
        experiments.reset()
        experiments.enable("hp20")
        unit = PoliceUnit("p1")
        unit.hp = 5
        unit.outer_armor_name = "盾牌"
        unit.outer_armor = make_armor("盾牌")
        unit.reset_to_initial()
        from engine.balance import get as bget
        self.assertEqual(unit.hp, bget("police", "hp", default=12))
        self.assertIsNone(unit.outer_armor_name)
        self.assertIsNone(unit.outer_armor)

    def test_police_reset_keeps_v1_scale(self) -> None:
        experiments.reset()
        unit = PoliceUnit("p1")
        unit.hp = 0.2
        unit.outer_armor_name = None
        unit.outer_armor = None
        unit.reset_to_initial()
        self.assertEqual(unit.hp, 1.0)
        self.assertEqual(unit.outer_armor_name, "盾牌")
        self.assertIsNotNone(unit.outer_armor)

    def test_petrify_attack_release_display_uses_hp20_damage(self) -> None:
        experiments.reset()
        experiments.enable("hp20")
        state = GameState()
        attacker = Player("p1", "攻击者", controller=ForfeitController())
        target = Player("p2", "石化者", controller=ForfeitController())
        attacker.is_awake = True
        target.is_awake = True
        attacker.location = "商店"
        target.location = "商店"
        state.add_player(attacker)
        state.add_player(target)
        target.hp = 10.0
        target.is_petrified = True
        state.markers.add("p2", "PETRIFIED")
        from talents.g1_firefly import G1MythFire
        g1 = G1MythFire.__new__(G1MythFire)
        g1.player_id = "p2"
        g1.state = state
        g1.ardent_wish_charges = 0  # 无炽愿，额外伤害全额落到 HP
        target.talent = g1
        from combat.damage_resolver import resolve_damage
        result = resolve_damage(attacker, target, make_weapon("小刀"), state)
        text = " | ".join(result.get("details", []))
        self.assertIn("额外受2伤害", text)
        self.assertNotIn("吸收-1.5", text)
        self.assertEqual(target.hp, 6.0)  # G1 减伤后小刀 2 + 石化解除 2

    def test_g1_ardent_temp_hp_uses_talent_num_hp20(self) -> None:
        experiments.reset()
        experiments.enable("m7_talents")
        from talents.g1_firefly import G1MythFire
        player = SimpleNamespace(name="Firefly")
        g1 = G1MythFire.__new__(G1MythFire)
        g1.player_id = "p1"
        g1.state = SimpleNamespace(get_player=lambda pid: player)
        g1.ardent_wish_charges = 2
        with patch("talents.g1_firefly.prompt_manager.show"):
            remaining = g1.receive_damage_to_temp_hp(5.0)
        self.assertEqual(remaining, 0.0)      # 2×3=6 可全额吸收 5
        self.assertEqual(g1.ardent_wish_charges, 0)  # 消耗 ceil(5/3)=2 层


if __name__ == "__main__":
    unittest.main()
