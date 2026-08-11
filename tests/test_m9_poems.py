"""M9 G5 诗篇十四首执行器单测（补完-4）：共享入口预检/爱愿/地火 full_extra/
守夜人接受链/负世火种与解锁/爱与记忆段数成长/简化标记/飞萤回响标记接线。"""
import unittest
from types import SimpleNamespace

from engine import experiments
from engine.game_state import GameState
from models.player import Player
from controllers.base import PlayerController
from controllers.forfeit_controller import ForfeitController

from engine.m9.gate import ensure_state_mechanisms
from engine.m9.talents.g5 import Ripple9, FORM_DEMIURGE
from engine.m9.talents.poems import POEM_TARGETS, SIMPLIFIED_MARKERS


def _enable(*flags):
    experiments.reset()
    for f in flags:
        experiments.enable(f)


class _FixedController(PlayerController):
    def __init__(self, *choices):
        self._choices = list(choices)
        self._i = 0

    def _next(self, options):
        if self._i < len(self._choices):
            c = self._choices[self._i]
            self._i += 1
            return c if c in options else options[0]
        return options[0]

    def get_command(self, player, game_state, available_actions, context=None):
        return "forfeit"

    def choose(self, prompt, options, context=None):
        return self._next(options)

    def choose_multi(self, prompt, options, max_count, min_count=0, context=None):
        return options[:max_count]

    def confirm(self, prompt, context=None):
        return self._next(["是"])


class _FakeTalent:
    def __init__(self, pid="", state=None):
        self.name = ""


def _make():
    state = GameState()
    ensure_state_mechanisms(state)
    p = Player("p1", "G5", controller=_FixedController())
    state.add_player(p)
    p.max_hp = 20
    p.hp = 20
    t = Ripple9("p1", state)
    p.talent = t
    t.form = FORM_DEMIURGE
    t.sealed_reminiscence = 50
    state.m9_system.set_sp("p1", 2)
    return state, p, t


def _add_target(state, pid, talent_cls=None, slot_name=""):
    t = Player(pid, pid.upper(), controller=_FixedController("是"))
    state.add_player(t)
    if talent_cls is not None:
        t.talent = talent_cls(pid, state)
        t.talent.name = slot_name
    return t


class SharedEntryTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_recite_requires_demiurge(self) -> None:
        state, p, t = _make()
        t.form = "cyrene"
        msg = t.recite_poem("游侠", "p2")
        self.assertIn("仅德谬歌", msg)

    def test_recite_requires_talent_binding(self) -> None:
        state, p, t = _make()
        _add_target(state, "p2")  # 无天赋
        msg = t.recite_poem("游侠", "p2")
        self.assertIn("未持有", msg)

    def test_recite_cost_and_love_wish(self) -> None:
        state, p, t = _make()
        _add_target(state, "p2", _FakeTalent, "T1")
        _add_target(state, "p9")  # 第三人：双人局不施加爱愿
        msg = t.recite_poem("游侠", "p2")
        self.assertNotIn("❌", msg)
        self.assertEqual(t.sealed_reminiscence, 50 - 12)  # poem_cost
        self.assertEqual(state.m9_system.get_sp("p1"), 0)  # 公演扣 2
        self.assertEqual(t.love_wish.get("p2"), 6)        # 爱愿

    def test_love_wish_blocks_anchor_and_ticks(self) -> None:
        state, p, t = _make()
        _add_target(state, "p2", _FakeTalent, "T1")
        _add_target(state, "p9")
        t.recite_poem("游侠", "p2")
        t.on_round_end(5)
        self.assertEqual(t.love_wish.get("p2"), 5)
        for _ in range(6):
            t.on_round_end(6)
        self.assertNotIn("p2", t.love_wish)


class PoemEffectsTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_earthfire_dispatches_full_extra(self) -> None:
        state, p, t = _make()
        target = _add_target(state, "p2", _FakeTalent, "T2")
        msg = t.recite_poem("地火", "p2")
        self.assertNotIn("❌", msg)
        self.assertTrue(target.is_invisible)  # 隐身

    def test_nightwatch_accept_chain(self) -> None:
        state, p, t = _make()
        talent = SimpleNamespace(
            color=5, color_is_null=False, is_terror=True, terror_extra_hp=5.0,
            permanent_extra_hp=1.0, fusion_shield_done=True, iron_horus_hp=0,
            tactical_unlocked=False)
        talent.name = "G7"
        target = _add_target(state, "p2", _FakeTalent, "G7")
        target.talent = talent
        msg = t.recite_poem("守夜人", "p2")
        self.assertNotIn("❌", msg)
        self.assertTrue(talent.color_is_null)
        self.assertFalse(talent.is_terror)
        self.assertEqual(talent.permanent_extra_hp, 1.0 + 5.0 - 2.0)  # +转化−代价
        self.assertEqual(talent.iron_horus_hp, 2)  # armor_restore

    def test_burden_unlocks_active_burn_and_ember(self) -> None:
        state, p, t = _make()
        from engine.m9.talents.g4 import Savior9
        g4 = Player("p2", "G4", controller=ForfeitController())
        state.add_player(g4)
        g4.max_hp = 20
        g4.hp = 20
        s = Savior9("p2", state)
        g4.talent = s
        s.divinity = 11
        s.ember = 11
        msg = t.recite_poem("负世", "p2")
        self.assertNotIn("❌", msg)
        self.assertEqual(s.divinity, 12)  # +1（正来源配额内）
        self.assertTrue(s.m9_burden_unlocked)
        # 主动燃尽选项解锁
        option = s.get_t0_option(g4)
        self.assertIsNotNone(option)
        self.assertEqual(option["m9_kind"], "g4_active_burn")

    def test_destiny_growth(self) -> None:
        state, p, t = _make()
        other = Player("p2", "P2", controller=ForfeitController())
        other.hp = 20
        state.add_player(other)
        msg1 = t.recite_poem("爱与记忆", "p1")
        self.assertNotIn("❌", msg1)
        state.m9_system.set_sp("p1", 2)
        state.current_round += 1  # 新轮：attention/公演位重置
        msg2 = t.recite_poem("爱与记忆", "p1")
        self.assertNotIn("❌", msg2)
        # 段数成长：n=3（4 人局起点 3）→ 4 段
        self.assertEqual(t.poems._destiny_stages, 2)

    def test_simplified_markers_whitelist(self) -> None:
        state, p, t = _make()
        target = _add_target(state, "p2")
        self.assertTrue(
            t.poems.grant_simplified_marker("p2", "ranger_chase"))
        self.assertFalse(
            t.poems.grant_simplified_marker("p2", "not_a_marker"))
        self.assertEqual(len(SIMPLIFIED_MARKERS), 7)
        self.assertEqual(len(POEM_TARGETS), 14)


class FireflyEchoIntegrationTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_firefly_echo_marker_reduces_entropy(self) -> None:
        from engine.m9.talents.g1 import G1MythFire9
        state, p, t = _make()
        g1p = Player("p2", "G1", controller=ForfeitController())
        state.add_player(g1p)
        g1p.max_hp = 20
        g1p.hp = 20
        g1 = G1MythFire9("p2", state)
        g1p.talent = g1
        g1p.last_action_type = "move"  # 非攻击 → 调息
        g1.form = "armorless"
        g1.entropy = 5.0
        msg = t.recite_poem("飞萤", "p2")
        self.assertNotIn("❌", msg)
        g1.on_round_end(5)
        # +1 累积 −1(回响) −2(调息+1) = 3
        self.assertEqual(g1.entropy, 3.0)
        self.assertEqual(g1.m9_poem_markers["firefly_echo"], 5)  # tick 递减