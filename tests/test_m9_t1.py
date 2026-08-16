"""M9 T1 一刀缭断机制单测（阶段 5）：SP 预检先于消费、即演/公演同一核心斩击、
伤害×2 防御减半（穿甲）、游侠诗公演 chase-move、次数语义退役、profile 隔离、
ActionGrant 台账与致死管线。"""
import math
import unittest

from engine import experiments
from engine.game_state import GameState
from models.player import Player
from controllers.forfeit_controller import ForfeitController

from engine.balance import get as bget
from engine.m9.gate import ensure_state_mechanisms
from engine.m9.talents.t1 import OneSlash9


class _InstantController(ForfeitController):
    def choose(self, prompt, options, context=None):
        if (context or {}).get("situation") == "t1_performance_mode":
            return "即演（1 SP）"
        return super().choose(prompt, options, context)


def _enable(*flags):
    experiments.reset()
    for f in flags:
        experiments.enable(f)


def _make():
    state = GameState()
    ensure_state_mechanisms(state)
    p = Player("p1", "T1", controller=ForfeitController())
    state.add_player(p)
    p.max_hp = 20
    p.hp = 20
    t = OneSlash9("p1", state)
    p.talent = t
    return state, p, t


def _melee_scene():
    """p1 持近战武器 + p2 面对面同地点（无护甲，20 HP）。"""
    from models.equipment import Weapon, WeaponRange
    from utils.attribute import Attribute
    state, p, t = _make()
    p.weapons = [Weapon("小刀", Attribute.ORDINARY, 2, WeaponRange.MELEE)]
    p.location = "商店"
    p2 = Player("p2", "目标", controller=ForfeitController())
    p2.location = "商店"
    p2.hp = 20
    state.add_player(p2)
    state.markers.set_engaged("p1", "p2")
    return state, p, t, p2


def _mult():
    return float(bget("m9_talents_extended", "t1", "melee_multiplier",
                      default=2.0))


def _pierce():
    return float(bget("m9_talents_extended", "t1", "defense_coefficient",
                      default=0.5))


class SPPrecheckTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_no_melee_weapon_option_none_sp_untouched(self) -> None:
        from models.equipment import Weapon, WeaponRange
        from utils.attribute import Attribute
        state, p, t = _make()
        p.weapons = [Weapon("弓", Attribute.ORDINARY, 3, WeaponRange.RANGED)]
        state.m9_system.set_sp("p1", 2)
        self.assertIsNone(t.get_t0_option(p))
        self.assertEqual(state.m9_system.get_sp("p1"), 2)
        msg, ok = t.execute_t0(p)
        self.assertFalse(ok)
        self.assertEqual(state.m9_system.get_sp("p1"), 2)

    def test_no_face_to_face_target_sp_untouched(self) -> None:
        from models.equipment import Weapon, WeaponRange
        from utils.attribute import Attribute
        state, p, t = _make()
        p.weapons = [Weapon("小刀", Attribute.ORDINARY, 2, WeaponRange.MELEE)]
        p.location = "商店"
        other = Player("p2", "路人", controller=ForfeitController())
        other.location = "商店"
        state.add_player(other)
        state.m9_system.set_sp("p1", 2)
        self.assertIsNone(t.get_t0_option(p))
        self.assertEqual(state.m9_system.get_sp("p1"), 2)
        msg, ok = t.execute_t0(p)
        self.assertFalse(ok)
        self.assertEqual(state.m9_system.get_sp("p1"), 2)

    def test_insufficient_sp_dispatch_fails_sp_unchanged(self) -> None:
        state, p, t, p2 = _melee_scene()
        state.m9_system.set_sp("p1", 0)  # 开局默认 1，显式归零
        self.assertEqual(state.m9_system.get_sp("p1"), 0)
        self.assertIsNone(t.get_t0_option(p))
        msg, ok = t.execute_t0(p)
        self.assertFalse(ok)
        self.assertEqual(state.m9_system.get_sp("p1"), 0)
        self.assertEqual(p2.hp, 20)


class ImproviseTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def _armored_scene(self):
        from models.equipment import ArmorLayer, ArmorPiece
        from utils.attribute import Attribute
        state, p, t, p2 = _melee_scene()
        piece = ArmorPiece("盾牌", Attribute.ORDINARY, ArmorLayer.OUTER, 1.0,
                           defense_map={"普通": 4}, durability=8)
        p2.add_armor(piece)
        state.m9_system.set_sp("p1", 1)
        return state, p, t, p2, piece

    def test_improvise_sp1_succeeds_damage_x2_defense_halved(self) -> None:
        from models.equipment import Weapon, WeaponRange
        from utils.attribute import Attribute
        state, p, t, p2, piece = self._armored_scene()
        p.weapons = [Weapon("大剑", Attribute.ORDINARY, 4, WeaponRange.MELEE)]
        msg, ok = t.execute_t0(p)
        self.assertTrue(ok)
        self.assertEqual(state.m9_system.get_sp("p1"), 0)
        raw = int(round(4 * _mult()))
        eff_def = int(round(4 * _pierce()))
        expected = max(raw - eff_def, max(1, math.ceil(raw * 0.25)))
        self.assertEqual(p2.hp, 20 - expected)
        self.assertGreater(expected, max(raw - 4, 1))  # 防御减半高于不减半
        self.assertEqual(piece.durability, 8 - (raw - expected))  # 防御已生效
        self.assertIn("一刀缭断", msg)

    def test_sp2_can_choose_improvise_and_keep_one_sp(self) -> None:
        state, p, t, p2 = _melee_scene()
        p.controller = _InstantController()
        state.m9_system.set_sp("p1", 2)

        _msg, ok = t.execute_t0(p)

        self.assertTrue(ok)
        self.assertEqual(state.m9_system.get_sp("p1"), 1)
        self.assertEqual(state.m9_system.performance_kind, "improvise")

    def test_engaged_shadow_actor_is_a_legal_target(self) -> None:
        from models.equipment import Weapon, WeaponRange
        from utils.attribute import Attribute

        state, p, t = _make()
        p.location = "商店"
        p.weapons = [Weapon("小刀", Attribute.ORDINARY, 2, WeaponRange.MELEE)]
        shadow = Player("p2@shadow", "影身", controller=ForfeitController())
        shadow.location = "商店"
        shadow.hp = 20
        state.m9_shadows[shadow.player_id] = shadow
        state.markers.init_player(shadow.player_id)
        state.markers.set_engaged("p1", shadow.player_id)
        state.m9_system.set_sp("p1", 1)

        _msg, ok = t.execute_t0(p)

        self.assertTrue(ok)
        self.assertLess(shadow.hp, 20)

    def test_improvise_unarmored_full_multiplier(self) -> None:
        state, p, t, p2 = _melee_scene()
        state.m9_system.set_sp("p1", 1)
        msg, ok = t.execute_t0(p)
        self.assertTrue(ok)
        raw = int(round(2 * _mult()))
        self.assertEqual(p2.hp, 20 - raw)  # 无甲：武器伤 ×2
        self.assertEqual(state.m9_system.get_sp("p1"), 0)


class PublicTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_public_sp2_success_sp0_queue_removed(self) -> None:
        state, p, t, p2 = _melee_scene()
        state.m9_system.set_sp("p1", 2)
        state.m9_system.register_performance("p1", state.current_round)
        state.m9_system.allocate_public_slot(state.current_round)
        msg, ok = t.execute_t0(p)
        self.assertTrue(ok)
        self.assertEqual(state.m9_system.get_sp("p1"), 0)
        self.assertFalse(state.m9_system.queue.is_in_queue("p1"))
        self.assertLess(p2.hp, 20)

    def test_no_public_seat_falls_back_to_available_improvise(self) -> None:
        state, p, t, p2 = _melee_scene()
        state.m9_system.set_sp("p1", 2)
        holder = Player("p3", "占位", controller=ForfeitController())
        state.add_player(holder)
        state.m9_system.set_sp("p3", 2)
        state.m9_system.register_performance("p3", state.current_round)
        state.m9_system.allocate_public_slot(state.current_round)
        msg, ok = t.execute_t0(p)
        self.assertTrue(ok)
        self.assertEqual(state.m9_system.get_sp("p1"), 1)
        self.assertLess(p2.hp, 20)


class RangerPoemTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_public_chase_moves_and_consumes_marker(self) -> None:
        from models.equipment import Weapon, WeaponRange
        from utils.attribute import Attribute
        state, p, t = _make()
        p.weapons = [Weapon("小刀", Attribute.ORDINARY, 2, WeaponRange.MELEE)]
        p.location = "商店"
        p2 = Player("p2", "锁定目标", controller=ForfeitController())
        p2.location = "警察局"
        p2.hp = 10
        state.add_player(p2)
        state.markers.add_relation("p2", "LOCKED_BY", "p1")
        t.m9_poem_markers["ranger_blade"] = True
        state.m9_system.set_sp("p1", 2)
        state.m9_system.register_performance("p1", state.current_round)
        state.m9_system.allocate_public_slot(state.current_round)
        msg, ok = t.execute_t0(p)
        self.assertTrue(ok)
        self.assertEqual(p.location, "警察局")  # chase-move 到锁定目标地点
        self.assertNotIn("ranger_blade", t.m9_poem_markers)  # 公演消耗
        self.assertEqual(state.m9_system.get_sp("p1"), 0)
        self.assertLess(p2.hp, 10)
        self.assertIn("一刀缭断", msg)

    def test_improvise_does_not_consume_marker(self) -> None:
        state, p, t, p2 = _melee_scene()
        t.m9_poem_markers["ranger_blade"] = True
        state.m9_system.set_sp("p1", 1)
        msg, ok = t.execute_t0(p)
        self.assertTrue(ok)
        self.assertIn("ranger_blade", t.m9_poem_markers)  # 即演不消耗
        self.assertEqual(state.m9_system.get_sp("p1"), 0)
        self.assertLess(p2.hp, 20)


class UsesRetiredTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_no_uses_fields_on_instance(self) -> None:
        state, p, t = _make()
        self.assertFalse(hasattr(t, "uses_remaining"))
        self.assertFalse(hasattr(t, "max_uses"))
        self.assertFalse(hasattr(t, "uses_left"))


class ProfileIsolationTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("hp20")  # m9_rfc 关闭

    def tearDown(self) -> None:
        experiments.reset()

    def test_m9_disabled_graceful_skip(self) -> None:
        state, p, t = _make()
        p.location = "商店"
        other = Player("p2", "路人", controller=ForfeitController())
        other.location = "商店"
        state.add_player(other)
        self.assertIsNone(t.get_t0_option(p))
        msg, ok = t.execute_t0(p)
        self.assertFalse(ok)  # 优雅跳过，不崩溃


class ActionGrantFlowTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_improvise_grant_recorded(self) -> None:
        state, p, t, p2 = _melee_scene()
        state.m9_system.set_sp("p1", 1)
        msg, ok = t.execute_t0(p)
        self.assertTrue(ok)
        grants = state.m9_system.ledger._grants
        self.assertEqual(len(grants), 1)
        grant = next(iter(grants.values()))
        self.assertEqual(grant.actor_id, "p1")
        self.assertEqual(grant.kind, "standard")
        self.assertTrue(grant.allow_instant)
        self.assertEqual(state.m9_system.get_sp("p1"), 0)

    def test_public_grant_recorded(self) -> None:
        state, p, t, p2 = _melee_scene()
        state.m9_system.set_sp("p1", 2)
        state.m9_system.register_performance("p1", state.current_round)
        state.m9_system.allocate_public_slot(state.current_round)
        msg, ok = t.execute_t0(p)
        self.assertTrue(ok)
        grants = state.m9_system.ledger._grants
        self.assertEqual(len(grants), 1)
        grant = next(iter(grants.values()))
        self.assertTrue(grant.allow_public)
        self.assertEqual(state.m9_system.get_sp("p1"), 0)


class LethalPipelineTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_lethal_sets_hp_zero_without_manual_kill(self) -> None:
        state, p, t, p2 = _melee_scene()
        p2.hp = 1
        state.m9_system.set_sp("p1", 1)
        msg, ok = t.execute_t0(p)
        self.assertTrue(ok)
        self.assertEqual(p2.hp, 0)  # 致死由 M9 管线收尾
        self.assertEqual(p.kill_count, 1)  # 公共死亡收尾统一记一次击杀
        self.assertIn("斩杀", msg)


if __name__ == "__main__":
    unittest.main()
