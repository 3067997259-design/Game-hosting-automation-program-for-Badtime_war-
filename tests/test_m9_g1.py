"""M9 G1 燃烧循环机制单测（阶段 4）：三形态/失熵/R4 冻结序/繁育绝对死替代/
完全燃烧窗口/超新星 move 触发。"""
import unittest
from types import SimpleNamespace

from engine import experiments
from engine.game_state import GameState
from models.equipment import ArmorLayer, ArmorPiece
from models.player import Player
from controllers.forfeit_controller import ForfeitController
from utils.attribute import Attribute

from engine.m9.gate import ensure_state_mechanisms
from engine.m9.talents.g1 import (
    FORM_ARMORLESS, FORM_FULL_BURN, FORM_PROPAGATION, FORM_SECONDARY,
    G1MythFire9,
)


def _enable(*flags):
    experiments.reset()
    for f in flags:
        experiments.enable(f)


def _make():
    state = GameState()
    ensure_state_mechanisms(state)
    p = Player("p1", "G1", controller=ForfeitController())
    state.add_player(p)
    p.max_hp = 20
    p.hp = 20
    t = G1MythFire9("p1", state)
    p.talent = t
    state.m9_system.set_sp("p1", 2)
    return state, p, t


class FormTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_dress_improvise_spends_sp_and_grants_supernova(self) -> None:
        state, p, t = _make()
        msg, ok = t.execute_t0(p)
        self.assertTrue(ok)
        self.assertEqual(t.form, FORM_SECONDARY)
        self.assertEqual(state.m9_system.get_sp("p1"), 1)
        self.assertTrue(t.has_supernova)

    def test_full_burn_public_spends_sp2(self) -> None:
        """完全燃烧：2 SP 公演（ForfeitController 默认选首个选项）→ 进入窗口。"""
        state, p, t = _make()
        t.form = FORM_SECONDARY
        state.m9_system.set_sp("p1", 2)
        msg, ok = t.execute_t0(p)
        self.assertTrue(ok)
        self.assertEqual(t.form, FORM_FULL_BURN)
        self.assertEqual(state.m9_system.get_sp("p1"), 0)
        self.assertIsNotNone(t.full_burn_until)

    def test_form_damage_modifiers(self) -> None:
        state, p, t = _make()
        self.assertEqual(t.m9_modify_outgoing(p, None, None, 5), 3)   # 卸甲惩罚
        t.form = FORM_SECONDARY
        self.assertEqual(t.m9_modify_outgoing(p, None, None, 5), 8)   # +3
        t.form = FORM_FULL_BURN
        self.assertEqual(t.m9_modify_outgoing(p, None, None, 5), 9)   # +4

    def test_lethal_in_full_burn_enters_propagation(self) -> None:
        state, p, t = _make()
        t.form = FORM_FULL_BURN
        t.full_burn_until = 9
        p.hp = 0
        kind = t.m9_on_lethal(p, None, "normal")
        self.assertEqual(kind, "g1_propagation")
        self.assertEqual(t.form, FORM_PROPAGATION)
        self.assertGreater(p.hp, 0)  # propagation_hp

    def test_absolute_death_bypasses_propagation(self) -> None:
        state, p, t = _make()
        t.form = FORM_FULL_BURN
        self.assertIsNone(t.m9_on_lethal(p, None, "g7_terror"))
        self.assertEqual(t.form, FORM_FULL_BURN)  # 不进入繁育


class EntropyR4OrderTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_entropy_accumulates_and_settles(self) -> None:
        """失熵累积 → 阈值结算：炽愿抵扣优先，其次碎外甲。"""
        state, p, t = _make()
        piece = ArmorPiece("盾牌", Attribute.ORDINARY, ArmorLayer.OUTER, 1.0,
                           defense_map={"普通": 2}, durability=8)
        p.armor.equip(piece)
        t.ardent_wish_charges = 1
        t.form = FORM_SECONDARY
        t.entropy = 5.0
        t.on_round_end(3)  # +2 → 7 ≥ 6 → 炽愿抵扣
        self.assertEqual(t.entropy, 3.0)
        self.assertEqual(t.ardent_wish_charges, 0)
        self.assertEqual(len(p.armor.get_active(ArmorLayer.OUTER)), 1)  # 甲未碎

    def test_settle_destroys_outer_armor_without_ardent(self) -> None:
        state, p, t = _make()
        piece = ArmorPiece("盾牌", Attribute.ORDINARY, ArmorLayer.OUTER, 1.0,
                           defense_map={"普通": 2}, durability=8)
        p.armor.equip(piece)
        t.ardent_wish_charges = 0
        t.form = FORM_SECONDARY
        t.entropy = 6.0
        t.on_round_end(3)
        self.assertEqual(len(p.armor.get_active(ArmorLayer.OUTER)), 0)

    def test_armorless_rest_reduces_entropy(self) -> None:
        state, p, t = _make()
        t.form = FORM_ARMORLESS
        p.last_action_type = "move"
        t.entropy = 5.0
        t.on_round_end(3)  # +1 −1(调息) = 5
        self.assertEqual(t.entropy, 5.0)

    def test_full_burn_window_expiry_forced_armorless(self) -> None:
        state, p, t = _make()
        t.form = FORM_FULL_BURN
        t.full_burn_until = 5
        t.entropy = 0.0
        t.on_round_end(6)
        self.assertEqual(t.form, FORM_ARMORLESS)

    def test_propagation_countdown_absolute_death(self) -> None:
        state, p, t = _make()
        t.form = FORM_PROPAGATION
        t.propagation_rounds = 1
        p.hp = 1
        t.on_round_end(4)
        self.assertEqual(p.hp, 0)
        self.assertFalse(p.is_alive())


class SupernovaOnMoveTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_propagation_move_triggers_once_per_round(self) -> None:
        state, p, t = _make()
        t.form = FORM_PROPAGATION
        t.propagation_rounds = 3
        other = Player("p2", "目标", controller=ForfeitController())
        other.location = "商店"
        other.hp = 10
        state.add_player(other)
        p.location = "商店"
        t.m9_on_root_move(p)
        self.assertEqual(state.m9_system.get_sp("p1"), 2)  # 结构性能力不耗 SP
        self.assertEqual(state.get_player("p2").hp, 2)    # 8 伤
        t.m9_on_root_move(p)  # 同轮第二次 → 不触发
        self.assertEqual(state.get_player("p2").hp, 2)
        t2 = G1MythFire9("p1", state)
        t2.form = FORM_SECONDARY  # 非繁育形态不触发
        t2.m9_on_root_move(p)
        self.assertEqual(state.get_player("p2").hp, 2)


class LocationDestructionTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_supernova_destroys_and_evicts_location(self) -> None:
        state, p, t = _make()
        t.form = FORM_PROPAGATION
        t.propagation_rounds = 3
        other = Player("p2", "目标", controller=ForfeitController())
        other.location = "医院"
        other.hp = 20
        state.add_player(other)
        p.location = "医院"
        t.m9_on_root_move(p)
        self.assertIn("医院", state.m9_destroyed_locations)
        self.assertEqual(state.get_player("p2").location, "home")  # 逐出

    def test_home_never_destroyed(self) -> None:
        state, p, t = _make()
        t.form = FORM_PROPAGATION
        t.propagation_rounds = 3
        p.location = "home"
        t.m9_on_root_move(p)
        self.assertNotIn("home", state.m9_destroyed_locations)

    def test_eviction_includes_shadow_actors(self) -> None:
        from engine.m9.talents.g2 import Hologram9
        state, p, t = _make()
        t.form = FORM_PROPAGATION
        t.propagation_rounds = 3
        g2 = Player("p2", "G2", controller=ForfeitController())
        g2.location = "医院"
        g2.hp = 20
        state.add_player(g2)
        h = Hologram9("p2", state)
        g2.talent = h
        actor = h._create_shadow(g2)
        actor.location = "医院"
        p.location = "医院"
        t.m9_on_root_move(p)
        self.assertEqual(g2.location, "home")
        self.assertEqual(actor.location, "home")


if __name__ == "__main__":
    unittest.main()
