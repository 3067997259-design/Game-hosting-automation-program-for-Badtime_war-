"""M9 G6 模板池机制单测（阶段 2）：记录/去重/窗口/排除、即演预检、公演借用
预检与或跃重掷、T0 入口、R3 记录与槽收尾接线。"""
import random
import unittest
from types import SimpleNamespace

from engine import experiments
from engine.game_state import GameState
from engine.round_manager import RoundManager
from models.equipment import Weapon, WeaponRange
from models.player import Player
from controllers.base import PlayerController
from controllers.forfeit_controller import ForfeitController
from utils.attribute import Attribute

from engine.m9.gate import ensure_state_mechanisms
from engine.m9.talents.g6 import (
    CutawayJoke9, G6Mechanics, G6TemplatePool, HOJUMP_RESULT_KEY,
)


def _enable(*flags):
    experiments.reset()
    for f in flags:
        experiments.enable(f)


class _FixedChoiceController(PlayerController):
    def __init__(self, *choices):
        self._choices = list(choices)
        self._i = 0

    def _next(self, options):
        if self._i < len(self._choices):
            choice = self._choices[self._i]
            self._i += 1
            return choice if choice in options else options[0]
        return options[0]

    def get_command(self, player, game_state, available_actions, context=None):
        return "forfeit"

    def choose(self, prompt, options, context=None):
        return self._next(options)

    def choose_multi(self, prompt, options, max_count, min_count=0, context=None):
        return options[:max_count]

    def confirm(self, prompt, context=None):
        return True


class TemplatePoolTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc")

    def tearDown(self) -> None:
        experiments.reset()

    def test_record_and_dedupe_by_category(self) -> None:
        pool = G6TemplatePool()
        self.assertTrue(pool.record(3, "attack", "商店", "p1"))
        self.assertFalse(pool.record(3, "attack", "商店", "p2"))  # 同轮同类别去重
        self.assertTrue(pool.record(3, "move", "医院", "p2"))
        cats = pool.categories(3)
        self.assertEqual(len(cats), 2)

    def test_exclusions_and_normalization(self) -> None:
        pool = G6TemplatePool()
        self.assertFalse(pool.record(3, "wake", "家", "p1"))
        self.assertFalse(pool.record(3, "forfeit", "商店", "p1"))
        self.assertFalse(pool.record(3, "police_status", "警察局", "p1"))
        self.assertTrue(pool.record(3, "shoot", "商店", "p1"))  # 归一 attack
        self.assertTrue(pool.record(3, "find", "商店", "p2"))

    def test_window_trim(self) -> None:
        pool = G6TemplatePool()
        pool.record(3, "attack", "A", "p1")
        pool.record(5, "move", "B", "p2")
        self.assertEqual(len(pool.categories(5)), 1)  # 3 轮已出窗
        self.assertEqual(len(pool.categories(5, joy_extended=True)), 2)

    def test_improvise_legal_needs_weapon_and_target_for_attack(self) -> None:
        pool = G6TemplatePool()
        pool.record(2, "attack", "A", "p1")
        pool.record(2, "move", "B", "p2")
        state = GameState()
        p1 = Player("p1", "G6", controller=ForfeitController())
        p1.location = "home"
        p2 = Player("p2", "路人", controller=ForfeitController())
        p2.location = "home"
        state.add_player(p1)
        state.add_player(p2)
        mech = G6Mechanics(pool)
        naked = p1
        naked.weapons.clear()  # Player 自带拳击，清空后无武器
        self.assertEqual(mech.improvise_legal_categories(naked, 2, state), ["move"])
        armed = p1
        armed.weapons.append(Weapon("小刀", Attribute.ORDINARY, 4,
                                    WeaponRange.MELEE))
        self.assertEqual(mech.improvise_legal_categories(armed, 2, state),
                         ["move", "attack"])


class BorrowPrecheckTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc")

    def tearDown(self) -> None:
        experiments.reset()

    def test_borrowable_whitelist(self) -> None:
        mech = G6Mechanics()
        keys = mech.borrowable_core_keys()
        self.assertEqual(
            set(keys),
            {"t1_one_slash", "t2_scissor_rush", "t3_heavenly_star",
             "t4_hexagram", "g3_reality_marble", "g4_savior"})
        self.assertNotIn("g2_holographic", keys)  # G2 不在白名单

    def test_precheck_requires_weapon_for_attack_cores(self) -> None:
        mech = G6Mechanics()
        naked = SimpleNamespace(weapons=[])
        armed = SimpleNamespace(weapons=[object()])
        self.assertFalse(mech.precheck_borrow(naked, "t1_one_slash"))
        self.assertTrue(mech.precheck_borrow(armed, "t1_one_slash"))
        self.assertTrue(mech.precheck_borrow(naked, "t4_hexagram"))  # 猜拳不依赖装备

    def test_hexagram_reroll_never_hojump(self) -> None:
        self.assertEqual(
            G6Mechanics.hexagram_reroll_until_legal(["scissors_paper", "both_rock"]),
            "both_rock")
        self.assertEqual(
            G6Mechanics.hexagram_reroll_until_legal(
                ["scissors_paper", "scissors_paper", "rock_paper"]),
            "rock_paper")


class G6T0EntryTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def _make(self, sp=2, choices=()):
        state = GameState()
        ensure_state_mechanisms(state)
        p = Player("p1", "G6", controller=_FixedChoiceController(*choices))
        p.location = "医院"
        state.add_player(p)
        t = CutawayJoke9("p1", state)
        p.talent = t
        state.m9_system.set_sp("p1", sp)
        return state, p, t

    def test_improvise_option_at_sp1(self) -> None:
        state, p, t = self._make(sp=1)
        state.g6_template_pool.record(1, "move", "商店", "p2")
        state.current_round = 2
        option = t.get_t0_option(p)
        self.assertIsNotNone(option)
        self.assertEqual(option["m9_kind"], "g6_improvise")

    def test_public_option_at_sp2(self) -> None:
        state, p, t = self._make(sp=2)
        state.g6_template_pool.record(1, "attack", "商店", "p2")
        state.current_round = 2
        p.weapons.append(Weapon("小刀", Attribute.ORDINARY, 4, WeaponRange.MELEE))
        other = Player("p2", "路人", controller=ForfeitController())
        other.location = "商店"
        state.add_player(other)
        option = t.get_t0_option(p)
        self.assertEqual(option["m9_kind"], "g6_improvise")  # 即演优先

    def test_no_option_without_sp(self) -> None:
        state, p, t = self._make(sp=0)
        self.assertIsNone(t.get_t0_option(p))

    def test_improvise_spends_sp_and_replays(self) -> None:
        """即演：预检通过 → 扣 1 SP → 真实重演 move 类别。"""
        state, p, t = self._make(sp=2, choices=("即演", "医院"))
        state.g6_template_pool.record(1, "move", "医院", "p2")
        state.current_round = 2
        msg, ok = t.execute_t0(p)
        self.assertTrue(ok)
        self.assertEqual(state.m9_system.get_sp("p1"), 1)
        self.assertIsInstance(msg, str)  # move 执行成功返回描述

    def test_improvise_illegal_category_cancels_before_sp(self) -> None:
        """类别参数不合法（无武器且无目标 attack）：演出在消费 SP 前取消。"""
        state, p, t = self._make(sp=2, choices=("即演", "attack"))
        p.weapons.clear()
        state.g6_template_pool.record(1, "attack", "商店", "p2")
        state.current_round = 2
        msg, ok = t.execute_t0(p)
        self.assertEqual(state.m9_system.get_sp("p1"), 2)  # SP 未动
        self.assertFalse(ok)

    def test_public_borrow_hexagram_no_full_extra(self) -> None:
        """公演借六爻：或跃组合 → 重掷，绝不设 hexagram_extra_turn。"""
        state, p, t = self._make(sp=2, choices=("公演", "t4_hexagram",
                                                "剪刀", "布",  # 或跃 → 重掷
                                                "石头", "剪刀"))  # 亢龙有悔
        state.current_round = 2
        other = Player("p2", "路人", controller=_FixedChoiceController("布", "剪刀"))
        state.add_player(other)
        msg, ok = t.execute_t0(p)
        self.assertTrue(ok)
        self.assertEqual(state.m9_system.get_sp("p1"), 0)  # 公演扣 2 SP
        self.assertNotIn("或跃", msg)
        self.assertFalse(getattr(p, "hexagram_extra_turn", False))


class RoundManagerWiringTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "k_initiative")

    def tearDown(self) -> None:
        experiments.reset()

    def test_r3_records_template_and_slot_outcome(self) -> None:
        """真实 R3：行动完成 → 模板池记录 + m9 槽收尾（root_action_performed）。"""
        state = GameState()
        ensure_state_mechanisms(state)
        for i in range(3):
            p = Player(f"p{i+1}", f"玩家{i+1}", controller=ForfeitController())
            p.is_awake = True
            p.location = "商店"
            state.add_player(p)
        rm = RoundManager(state)
        random.seed(5)
        rm._phase_r1()
        rm._phase_r3()
        # forfeit 行动 → 槽已收尾（resolution_kind=forfeit），模板池记录 forfeit 应被排除
        pool = state.g6_template_pool
        self.assertEqual(len(pool.categories(state.current_round)), 0)
        # 槽位事实存在且已 resolve
        outcomes = [s for s in state.m9_system._slots.values()]
        self.assertTrue(len(outcomes) >= 1)
        self.assertTrue(all(o.slot_resolved for o in outcomes))


if __name__ == "__main__":
    unittest.main()
