"""M9 G4 救世主轮回机制单测（阶段 5）：火种 W2、完整/残缺进入、SP 置 2、
形态内致死消耗、6 tick 寿命（建立轮不 tick）、退场不落幕、负世 full_extra、
焚诏拉条裁决（反击/天裁池均分与余数序）。"""
import unittest
from types import SimpleNamespace

from engine import experiments
from engine.game_state import GameState
from models.player import Player
from controllers.forfeit_controller import ForfeitController

from engine.m9.gate import ensure_state_mechanisms
from engine.m9.talents.g4 import (
    ChallengeAdjudicator, FORM_FULL, FORM_HUMAN, FORM_INCOMPLETE, Savior9,
)


def _enable(*flags):
    experiments.reset()
    for f in flags:
        experiments.enable(f)


def _make():
    state = GameState()
    ensure_state_mechanisms(state)
    p = Player("p1", "G4", controller=ForfeitController())
    state.add_player(p)
    p.max_hp = 20
    p.hp = 20
    t = Savior9("p1", state)
    p.talent = t
    return state, p, t


class EmberW2Test(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_hostile_and_positive_firsts_cap_2_per_round(self) -> None:
        state, p, t = _make()
        attacker = SimpleNamespace(player_id="p2", name="攻击者")
        t.on_being_attacked(attacker, None)
        t.on_being_attacked(attacker, None)      # 二次敌对不计
        t.on_positive_talent_used(attacker)       # 正面首次 +1
        t.on_positive_talent_used(attacker)       # 二次不计
        self.assertEqual(t.divinity, 2)
        # 下一轮重置
        state.current_round = 2
        t.on_being_attacked(attacker, None)
        self.assertEqual(t.divinity, 3)

    def test_human_form_only_and_cap_12(self) -> None:
        state, p, t = _make()
        attacker = SimpleNamespace(player_id="p2", name="攻击者")
        for r in range(1, 8):
            state.current_round = r
            t.on_being_attacked(attacker, None)
            t.on_positive_talent_used(attacker)
        self.assertEqual(t.divinity, 12)  # 7 轮 × 2 = 14 → 封顶 12
        t.on_being_attacked(attacker, None)
        self.assertEqual(t.divinity, 12)

    def test_m9_on_hit_feeds_hostile(self) -> None:
        state, p, t = _make()
        hit = SimpleNamespace(_attacker=SimpleNamespace(player_id="p2"))
        t.m9_on_hit(hit)
        self.assertEqual(t.divinity, 1)


class FormEntryTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_full_entry_on_lethal_at_12(self) -> None:
        state, p, t = _make()
        t.divinity = 12
        t.ember = 12
        result = t.on_death_check(p, None)
        self.assertIsNotNone(result)
        self.assertTrue(result["prevent_death"])
        self.assertEqual(t.form, FORM_FULL)
        self.assertEqual(state.m9_system.get_sp("p1"), 2)  # SP 置 2
        self.assertEqual(t.divinity, 0)

    def test_incomplete_entry_below_12(self) -> None:
        state, p, t = _make()
        t.divinity = 5
        result = t.on_death_check(p, None)
        self.assertIsNotNone(result)
        self.assertEqual(t.form, FORM_INCOMPLETE)

    def test_in_savior_lethal_is_consumption_not_death(self) -> None:
        state, p, t = _make()
        t.divinity = 12
        t.on_death_check(p, None)
        p.hp = 0
        kind = t.m9_on_lethal(p, None, "normal")
        self.assertEqual(kind, "g4_savior_consume")
        self.assertGreater(p.hp, 0)  # 余烬生命消耗后存活

    def test_exit_does_not_retire(self) -> None:
        state, p, t = _make()
        t.divinity = 12
        t.on_death_check(p, None)
        t._exit_savior_state()
        self.assertEqual(t.form, FORM_HUMAN)
        self.assertFalse(t.spent)  # M9：不永久落幕
        self.assertFalse(t.is_savior)

    def test_entry_round_r4_does_not_tick(self) -> None:
        state, p, t = _make()
        t.divinity = 12
        t.on_death_check(p, None)
        state.current_round = 5
        t.on_round_end(5)
        self.assertEqual(t.form_ticks, 6)  # 建立轮不 tick
        state.current_round = 6
        t.on_round_end(6)
        self.assertEqual(t.form_ticks, 5)

    def test_active_burn_dispatches_full_extra(self) -> None:
        state, p, t = _make()
        t.divinity = 12
        t.ember = 12
        t.m9_burden_unlocked = True  # 负世诗解锁主动燃尽（合同 §七）
        option = t.get_t0_option(p)
        self.assertIsNotNone(option)
        msg, ok = t.execute_t0(p)
        self.assertTrue(ok)
        self.assertEqual(t.form, FORM_FULL)


class ChallengePerformanceTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def _scene(self, choices=()):
        from controllers.base import PlayerController

        class _C(PlayerController):
            def __init__(self, seq):
                self.seq = list(seq)
                self.i = 0

            def _next(self, options):
                if self.i < len(self.seq):
                    c = self.seq[self.i]
                    self.i += 1
                    return c if c in options else options[0]
                return options[0]

            def get_command(self, player, game_state, available_actions,
                            context=None):
                return "forfeit"

            def choose(self, prompt, options, context=None):
                return self._next(options)

            def choose_multi(self, prompt, options, max_count, min_count=0,
                             context=None):
                return options[:max_count]

            def confirm(self, prompt, context=None):
                return True

        state = GameState()
        ensure_state_mechanisms(state)
        p = Player("p1", "G4", controller=_C(choices))
        state.add_player(p)
        p.max_hp = 20
        p.hp = 20
        t = Savior9("p1", state)
        p.talent = t
        state.m9_system.set_sp("p1", 2)
        t.divinity = 12
        t.on_death_check(p, None)  # 完整形态
        return state, p, t

    def test_challenge_refuse_gets_judgment_absolute_death(self) -> None:
        """全员拒战：天裁池 = S×J 全部分给拒战者（DIRECT_DAMAGE+absolute_dead）。"""
        state, p, t = self._scene()
        other = Player("p2", "路人", controller=ForfeitController())
        other.location = "商店"
        other.hp = 3
        state.add_player(other)
        t.ruin_damage = 3
        msg, ok = t.execute_t0(p)
        self.assertTrue(ok)
        self.assertFalse(other.is_alive())  # 天裁打死（absolute_death）
        self.assertEqual(state.m9_system.get_sp("p1"), 0)  # 公演扣 2

    def test_challenge_attack_gets_counter(self) -> None:
        state, p, t = self._scene(choices=("攻击",))
        other = Player("p2", "攻击者", controller=ForfeitController())
        other.location = "商店"
        other.hp = 20
        state.add_player(other)
        from models.equipment import Weapon, WeaponRange
        from utils.attribute import Attribute
        other.weapons.append(
            Weapon("小刀", Attribute.ORDINARY, 2, WeaponRange.MELEE))
        t.ruin_damage = 3
        msg, ok = t.execute_t0(p)
        self.assertTrue(ok)
        # 反击池 3 → 攻击者受 3 伤（先攻序唯一）
        self.assertLess(other.hp, 20)
        self.assertIn("反击", msg)

    def test_challenge_option_only_in_form_with_ruin(self) -> None:
        state, p, t = self._scene()
        option = t.get_t0_option(p)
        self.assertIsNotNone(option)
        self.assertEqual(option["m9_kind"], "g4_challenge")
        t.ruin_damage = 0
        self.assertIsNone(t.get_t0_option(p))


class ChallengeAdjudicatorTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc")

    def tearDown(self) -> None:
        experiments.reset()

    def test_counter_pool_split_among_attackers(self) -> None:
        snap = [("a", 10, True), ("b", 20, True), ("c", 5, True)]
        adj = ChallengeAdjudicator(snap, counter_total=9, judgment_per_segment=2)
        result = adj.resolve({"a": "attack", "b": "attack", "c": "refuse"})
        # 固定池整数均分 + 余数给先攻高者：b(20) 先 → 5，a(10) → 4
        self.assertEqual(result["counters"]["b"], 5.0)
        self.assertEqual(result["counters"]["a"], 4.0)
        self.assertEqual(result["judgments"]["c"], 2.0)  # S=1 × J=2

    def test_all_refuse_full_pool(self) -> None:
        snap = [("a", 10, True), ("b", 5, True)]
        adj = ChallengeAdjudicator(snap, counter_total=6, judgment_per_segment=2)
        result = adj.resolve({"a": "refuse", "b": "refuse"})
        self.assertEqual(result["judgments"]["a"], 2.0)
        self.assertEqual(result["judgments"]["b"], 2.0)
        self.assertEqual(result["counters"], {})

    def test_remainder_by_initiative_desc_then_id(self) -> None:
        """池 7 分给 2 人 → 3/4；余数给先攻高者。"""
        snap = [("low", 1, True), ("high", 99, True)]
        adj = ChallengeAdjudicator(snap, counter_total=7, judgment_per_segment=2)
        result = adj.resolve({"low": "attack", "high": "attack"})
        self.assertEqual(result["counters"]["high"], 4.0)
        self.assertEqual(result["counters"]["low"], 3.0)


if __name__ == "__main__":
    unittest.main()
