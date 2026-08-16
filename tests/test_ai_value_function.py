"""value function 骨架测试（同源探针 + 折算公式接入 CombatMind 评分）。"""
import unittest

from engine import experiments
from engine.game_state import GameState
from models.equipment import Weapon, WeaponRange
from models.player import Player

from controllers.base import PlayerController
from controllers.ai.decision.value import (
    combat_value_adjust, expected_damage_probe,
)


def _enable(*flags):
    experiments.reset()
    for f in flags:
        experiments.enable(f)


class _C(PlayerController):
    def get_command(self, player, game_state, available_actions, context=None):
        return "forfeit"

    def choose(self, prompt, options, context=None):
        return options[0] if options else ""

    def choose_multi(self, prompt, options, max_count, min_count=0, context=None):
        return list(options)[:max_count]

    def confirm(self, prompt, context=None):
        return True


class _FakeInsurance:
    def is_mounted(self) -> bool:
        return True


class ValueFunctionTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def _world(self):
        state = GameState()
        p = Player("p1", "AI1", controller=_C())
        p.is_awake = True
        p.location = "公园"
        p.hp = 20
        p.max_hp = 20
        p.weapons = [Weapon("小刀", "普通", 4, WeaponRange.MELEE)]
        state.add_player(p)
        p2 = Player("p2", "AI2", controller=_C())
        p2.is_awake = True
        p2.location = "公园"
        p2.hp = 20
        p2.max_hp = 20
        state.add_player(p2)
        return state, p, p2

    def _add_armor(self, target, defense_map):
        from models.equipment import ArmorLayer, ArmorPiece
        target.armor.outer.append(ArmorPiece(
            "陶瓷护甲", None, ArmorLayer.OUTER, 10,
            defense_map=defense_map, durability=10))

    def test_probe_armor_reduces_expected_damage(self) -> None:
        state, p, p2 = self._world()
        dmg_plain, _ = expected_damage_probe(p, p2)
        self.assertGreater(dmg_plain, 0)
        # 目标穿甲 → 期望伤害下降（同源结算探针）
        self._add_armor(p2, {"魔法": 3, "科技": 3, "普通": 3})
        dmg_armored, info = expected_damage_probe(p, p2)
        self.assertLessEqual(dmg_armored, dmg_plain)

    def test_combat_value_adjust_m9_only(self) -> None:
        from controllers.ai.decision.snapshot import ProjectedSnapshot
        state, p, p2 = self._world()
        p2.hp = 4  # 可被一刀致死 → p_lethal=1（击杀效用项生效）
        snap = ProjectedSnapshot.build(p, state)
        adj = combat_value_adjust(p, p2, state, snap)
        self.assertGreater(adj, 0)
        # 保险挂载 → 击杀效用归零，仅剩期望伤害项
        state.m9_insurance = _FakeInsurance()
        snap = ProjectedSnapshot.build(p, state)
        adj_insured = combat_value_adjust(p, p2, state, snap)
        self.assertLess(adj_insured, adj)

    def test_combat_value_adjust_v2exp_zero(self) -> None:
        from controllers.ai.decision.snapshot import ProjectedSnapshot
        experiments.reset()
        experiments.disable("m9_rfc")
        state = GameState()
        p = Player("p1", "AI1", controller=_C())
        p.is_awake = True
        p.location = "公园"
        p.hp = 20
        p.max_hp = 20
        p.weapons = [Weapon("小刀", "普通", 4, WeaponRange.MELEE)]
        state.add_player(p)
        p2 = Player("p2", "AI2", controller=_C())
        p2.is_awake = True
        p2.location = "公园"
        p2.hp = 20
        p2.max_hp = 20
        state.add_player(p2)
        snap = ProjectedSnapshot.build(p, state)
        self.assertEqual(combat_value_adjust(p, p2, state, snap), 0.0)

    def test_combat_mind_score_uses_value_adjust(self) -> None:
        """接入断言：无护甲残血目标评分高于满甲满血目标（含探针项）。"""
        from controllers.ai.minds.combat_mind import CombatMind
        from controllers.ai.decision.snapshot import ProjectedSnapshot
        from controllers.ai.strategies.base_strategy import BasePersonalityStrategy
        state, p, p2 = self._world()
        p3 = Player("p3", "AI3", controller=_C())
        p3.is_awake = True
        p3.location = "公园"
        p3.hp = 20
        p3.max_hp = 20
        state.add_player(p3)
        # p2 无甲残血、p3 满血满甲
        p2.hp = 4
        self._add_armor(p3, {"魔法": 3, "科技": 3, "普通": 3})
        snap = ProjectedSnapshot.build(p, state)
        mind = CombatMind(debug_name="C")
        kw = dict(strategy=BasePersonalityStrategy(),
                  threat_scores={}, police_protected=set(),
                  police_stance="ignore", police_mind=None,
                  llm_alliance=[], terror_defense=None,
                  combat_target=None, in_combat=False,
                  star_follow_up_rounds=0, avg_threat=0.0,
                  llm_aggression_mod=0.0, players_who_attacked=set(),
                  snapshot=snap)
        score_p2 = mind._score_target(p, p2, state, **kw)
        score_p3 = mind._score_target(p, p3, state, **kw)
        self.assertGreater(score_p2, score_p3)


if __name__ == "__main__":
    unittest.main()
