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
from actions.move import has_active_supernova
from cli.validator import validate_move
from engine.action_turn import ActionTurnManager

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
        """着装宣言：耗 1 SP、不消费行动槽（合同 §2.0 换装宣言不占槽）。"""
        state, p, t = _make()
        msg, ok = t.execute_t0(p)
        self.assertFalse(ok)  # 宣言不占槽，玩家仍可正常行动
        self.assertEqual(t.form, FORM_SECONDARY)
        self.assertEqual(state.m9_system.get_sp("p1"), 1)
        self.assertTrue(t.has_supernova)

    def test_m9_supernova_payload_uses_full_burn_bonus(self) -> None:
        from engine.balance import get as bget
        base = int(bget("m9_talents_extended", "g1", "supernova_damage",
                        default=8))
        bonus = int(bget("m9_talents_extended", "g1",
                         "full_burn_supernova_bonus", default=2))
        burns = int(bget("m9_talents_extended", "g1", "supernova_burn",
                         default=2))
        ardent_cap = int(bget("m9_talents_extended", "g1", "ardent_cap",
                              default=6))
        state, p, t = _make()
        t.form = FORM_SECONDARY
        self.assertEqual(t.supernova_payload(), (base, 0.5, burns))
        t.form = FORM_FULL_BURN
        self.assertEqual(t.supernova_payload(), (base + bonus, 0.5, burns + 2))
        self.assertEqual(t.max_ardent_wish_charges, ardent_cap)

    def test_m9_supernova_hits_fixed_police_roster_without_double_kill(self) -> None:
        from engine.balance import get as bget
        dmg = int(bget("m9_talents_extended", "g1", "supernova_damage",
                       default=8))
        state, p, t = _make()
        target = Player("p2", "目标", controller=ForfeitController())
        state.add_player(target)
        p.location = target.location = "警察局"
        target.hp = dmg
        roster = state.m9_police.ensure_roster("警察局")
        unit = roster[0]
        unit.hp = dmg
        t.form = FORM_SECONDARY
        t.has_supernova = True

        t.trigger_supernova(p, "警察局", state)

        self.assertEqual(target.hp, 0)
        self.assertEqual(unit.hp, 0)
        self.assertEqual(p.kill_count, 1)  # 玩家击杀只由 finalize_death 记一次
        self.assertTrue(t.has_supernova)  # 本次已消费；击杀按合同又授予下一次
        event = next(e for e in reversed(state.event_log)
                     if e["type"] == "firefly_supernova")
        self.assertEqual(event["hits"], 1 + len(roster))
        self.assertEqual(event["kills"], 2)

    def test_full_burn_public_spends_sp2(self) -> None:
        """完全燃烧：2 SP 公演（ForfeitController 默认选首个选项）→ 进入窗口。"""
        state, p, t = _make()
        t.form = FORM_SECONDARY
        state.m9_system.set_sp("p1", 2)
        state.m9_system.register_performance("p1", state.current_round)
        state.m9_system.allocate_public_slot(state.current_round)
        msg, ok = t.execute_t0(p)
        self.assertTrue(ok)
        self.assertEqual(t.form, FORM_FULL_BURN)
        self.assertEqual(state.m9_system.get_sp("p1"), 0)
        self.assertIsNotNone(t.full_burn_until)

    def test_form_damage_modifiers(self) -> None:
        from engine.balance import get as bget
        penalty = int(bget("m9_talents_extended", "g1",
                           "unarmored_atk_penalty", default=2))
        sam = int(bget("m9_talents_extended", "g1", "sam_atk_bonus", default=3))
        full = int(bget("m9_talents_extended", "g1",
                        "full_burn_atk_bonus", default=4))
        state, p, t = _make()
        self.assertEqual(t.m9_modify_outgoing(p, None, None, 5), 5 - penalty)
        t.form = FORM_SECONDARY
        self.assertEqual(t.m9_modify_outgoing(p, None, None, 5), 5 + sam)
        t.form = FORM_FULL_BURN
        self.assertEqual(t.m9_modify_outgoing(p, None, None, 5), 5 + full)

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

    def test_free_find_never_targets_remote_player(self) -> None:
        state, p, t = _make()
        p.location = "商店"
        other = Player("p2", "远处目标", controller=ForfeitController())
        other.location = "医院"
        other.hp = 20
        state.add_player(other)
        self.assertFalse(t.free_find_available(state.current_round))
        other.location = "商店"
        self.assertTrue(t.free_find_available(state.current_round))

    def test_secondary_blocks_development_actions(self) -> None:
        state, p, t = _make()
        p.is_awake = True
        t.form = FORM_SECONDARY
        names, _ = ActionTurnManager(state)._get_available_actions(p)
        self.assertNotIn("find", names)
        self.assertNotIn("interact", names)

    def test_unloaded_supernova_cannot_trigger_same_location_move(self) -> None:
        state, p, t = _make()
        p.is_awake = True
        p.location = "商店"
        t.form = FORM_SECONDARY
        t.has_supernova = True
        self.assertTrue(has_active_supernova(p))
        self.assertTrue(validate_move(p, "商店", state)[0])
        t.form = FORM_ARMORLESS
        self.assertFalse(has_active_supernova(p))
        self.assertFalse(validate_move(p, "商店", state)[0])


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

    def test_unload_round_uses_form_at_action_start(self) -> None:
        state, p, t = _make()
        state.current_round = 5
        t.form = FORM_SECONDARY
        t.entropy = 0.0
        p.last_action_type = "move"
        t.on_turn_start(p)
        t.form = FORM_ARMORLESS
        t.on_round_end(5)
        # 次级档 +2，再按卸甲调息 -1；若错误按卸甲档只会得到 0。
        self.assertEqual(t.entropy, 1.0)

    def test_full_burn_expiry_always_settles_below_threshold(self) -> None:
        from engine.balance import get as bget
        hp_loss = int(bget("m9_talents_extended", "g1", "entropy_hp_loss",
                           default=4))
        state, p, t = _make()
        t.form = FORM_FULL_BURN
        t.full_burn_until = 5
        t.entropy = 0.0
        t.ardent_wish_charges = 0
        p.hp = 20
        t.on_round_end(5)
        self.assertEqual(t.form, FORM_ARMORLESS)
        self.assertEqual(p.hp, 20 - hp_loss)
        self.assertEqual(t.entropy, 0.0)

    def test_propagation_countdown_absolute_death(self) -> None:
        state, p, t = _make()
        t.form = FORM_PROPAGATION
        t.propagation_rounds = 1
        p.hp = 1
        t.on_round_end(4)
        self.assertEqual(p.hp, 0)
        self.assertFalse(p.is_alive())

    def test_propagation_establishment_round_does_not_tick(self) -> None:
        state, p, t = _make()
        state.current_round = 4
        t._enter_propagation(p)
        before = t.propagation_rounds
        t.on_round_end(4)
        self.assertEqual(t.propagation_rounds, before)
        t.on_round_end(5)
        self.assertEqual(t.propagation_rounds, before - 1)


class SupernovaOnMoveTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_propagation_move_triggers_once_per_round(self) -> None:
        from engine.balance import get as bget
        dmg = int(bget("m9_talents_extended", "g1", "supernova_damage",
                       default=8))
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
        self.assertEqual(state.get_player("p2").hp, 10 - dmg)  # 繁育超新星伤害
        t.m9_on_root_move(p)  # 同轮第二次 → 不触发
        self.assertEqual(state.get_player("p2").hp, 10 - dmg)
        t2 = G1MythFire9("p1", state)
        t2.form = FORM_SECONDARY  # 非繁育形态不触发
        t2.m9_on_root_move(p)
        self.assertEqual(state.get_player("p2").hp, 10 - dmg)


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
        self.assertEqual(state.get_player("p2").location, "home_p2")  # 逐出

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
        self.assertEqual(g2.location, "home_p2")
        self.assertEqual(actor.location, "home_p2")


class _ChoiceController(ForfeitController):
    """返回预置序列的 choose；耗尽后取首个选项。"""

    def __init__(self, *choices):
        super().__init__()
        self._queue = list(choices)

    def choose(self, prompt, options, context=None):
        if self._queue:
            return self._queue.pop(0)
        return options[0] if options else ""


class DressGatingTest(unittest.TestCase):
    """裁决 A+C+虚弱期：着装超新星额度 / 卸甲冷却 / 燃烧殆尽锁定。"""

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_dress_supernova_grant_capped_per_game(self) -> None:
        from engine.balance import get as bget
        cap = int(bget("m9_talents_extended", "g1",
                       "supernova_grant_cap", default=3))
        state, p, t = _make()
        for i in range(cap):
            t.has_supernova = False
            self.assertTrue(t._dress_grant_supernova(p))
            self.assertTrue(t.has_supernova)
        # 第 cap+1 次：额度耗尽，不再授予
        t.has_supernova = False
        self.assertFalse(t._dress_grant_supernova(p))
        self.assertFalse(t.has_supernova)

    def test_dress_grant_cap_lifted_in_propagation(self) -> None:
        state, p, t = _make()
        t._dress_supernova_grants = 99  # 额度早已耗尽
        t.form = FORM_PROPAGATION
        t.has_supernova = False
        self.assertTrue(t._dress_grant_supernova(p))  # 繁育形态解除上限
        self.assertTrue(t.has_supernova)

    def test_undress_starts_dress_cooldown(self) -> None:
        state, p, t = _make()
        state.current_round = 4
        t.form = FORM_SECONDARY
        p.controller = _ChoiceController("卸甲宣言（免费）")
        msg, ok = t.execute_t0(p)
        self.assertFalse(ok)
        self.assertEqual(t.form, FORM_ARMORLESS)
        # 同轮不可立即再着装（冷却 1 轮）
        state.m9_system.set_sp("p1", 2)
        self.assertIsNone(t.get_t0_option(p))
        self.assertFalse(t._dress_available(5))   # 第 5 轮仍在冷却（>5 才解）
        self.assertTrue(t._dress_available(6))

    def test_burnout_lockout_blocks_dress_for_lockout_rounds(self) -> None:
        from engine.balance import get as bget
        lockout = int(bget("m9_talents_extended", "g1",
                           "burnout_dress_lockout_rounds", default=2))
        state, p, t = _make()
        state.current_round = 10
        t.form = FORM_FULL_BURN
        t.full_burn_until = 10  # 本轮到期
        p.is_awake = True
        t.on_round_end(10)
        self.assertEqual(t.form, FORM_ARMORLESS)
        self.assertEqual(t._burnout_lockout_until, 10 + lockout)
        # 锁定期内不可着装
        self.assertFalse(t._dress_available(11))
        self.assertFalse(t._dress_available(10 + lockout))
        self.assertTrue(t._dress_available(10 + lockout + 1))
        # T0 菜单同样不出现着装
        state.m9_system.set_sp("p1", 2)
        self.assertIsNone(t.get_t0_option(p))

    def test_burnout_lockout_applies_entropy_settle_once(self) -> None:
        """燃烧殆尽：立即承受一次失熵结算（合同 §2.3），并带锁定。
        entropy 0 + 完全燃烧累积 3 < 阈值 6 → 仅燃烧殆尽这一次结算。"""
        state, p, t = _make()
        state.current_round = 5
        t.form = FORM_FULL_BURN
        t.full_burn_until = 5
        t.entropy = 0.0
        p.armor.outer.clear()  # 无外甲 → 结算直接扣 HP
        p.hp = 20
        from engine.balance import get as bget
        hp_loss = int(bget("m9_talents_extended", "g1",
                           "entropy_hp_loss", default=4))
        t.on_round_end(5)
        self.assertEqual(p.hp, 20 - hp_loss)
        self.assertEqual(t.entropy, 0.0)  # max(0, 0+3−reset)
        self.assertGreater(t._burnout_lockout_until, 0)


if __name__ == "__main__":
    unittest.main()
