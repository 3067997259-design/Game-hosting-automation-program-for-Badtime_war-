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

    def test_low_ember_entry_is_only_allowed_on_first_lethal(self) -> None:
        state, p, t = _make()
        t.divinity = 5
        first = t.on_death_check(p, None)
        self.assertIsNotNone(first)
        self.assertEqual(t.form, FORM_INCOMPLETE)

        t._exit_savior_state()
        t.divinity = 11
        second = t.on_death_check(p, None)
        self.assertIsNone(second)
        self.assertEqual(t.form, FORM_HUMAN)
        self.assertEqual(t.divinity, 11)

        t.divinity = 12
        third = t.on_death_check(p, None)
        self.assertIsNotNone(third)
        self.assertEqual(t.form, FORM_FULL)

    def test_full_first_lethal_also_consumes_low_ember_grace(self) -> None:
        state, p, t = _make()
        t.divinity = 12
        first = t.on_death_check(p, None)
        self.assertIsNotNone(first)
        self.assertEqual(t.form, FORM_FULL)

        t._exit_savior_state()
        t.divinity = 11
        second = t.on_death_check(p, None)
        self.assertIsNone(second)
        self.assertEqual(t.form, FORM_HUMAN)

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
        from engine.balance import get as _bget
        dur = int(_bget("m9_talents_extended", "g4",
                        "full_duration_r4", default=6))
        state, p, t = _make()
        t.divinity = 12
        t.on_death_check(p, None)
        state.current_round = 5
        t.on_round_end(5)
        self.assertEqual(t.form_ticks, dur)  # 建立轮不 tick
        state.current_round = 6
        t.on_round_end(6)
        self.assertEqual(t.form_ticks, dur - 1)

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

    def test_active_first_entry_consumes_low_ember_grace(self) -> None:
        state, p, t = _make()
        t.divinity = 12
        t.ember = 12
        t.m9_burden_unlocked = True
        msg, ok = t.execute_t0(p)
        self.assertTrue(ok, msg)

        t._exit_savior_state()
        t.divinity = 11
        self.assertIsNone(t.on_death_check(p, None))
        self.assertEqual(t.form, FORM_HUMAN)


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
        state.m9_system.register_performance("p1", state.current_round)
        state.m9_system.allocate_public_slot(state.current_round)
        t.divinity = 12
        t.on_death_check(p, None)  # 完整形态
        return state, p, t

    def test_challenge_refuse_gets_judgment_absolute_death(self) -> None:
        """全员拒战：天裁池 = S×J 全部分给拒战者（DIRECT_DAMAGE+absolute_dead）。

        R7 机制压顶：judgment_per_segment 2→1，单人池 = 1×1 = 1。
        """
        state, p, t = self._scene()
        other = Player("p2", "路人", controller=ForfeitController())
        other.location = "商店"
        other.hp = 1
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

    def test_challenge_forced_exit_cancels_counter_and_judgment(self) -> None:
        """审计 v0.1 场景 15（G4 真正打断）：某次挑战攻击迫使 G4 退出形态 →
        停止后续响应并取消反击与天裁，只执行无额外载荷退场清理。"""
        state, p, t = self._scene(choices=("攻击",))
        killer = Player("p2", "强者", controller=ForfeitController())
        killer.location = "商店"
        killer.hp = 20
        state.add_player(killer)
        from models.equipment import Weapon, WeaponRange
        from utils.attribute import Attribute
        killer.weapons.append(
            Weapon("电磁步枪", Attribute.TECH, 30, WeaponRange.RANGED))
        t.ruin_damage = 3
        p.hp = 1
        t.ember_hp = 1  # 单次响应即可耗尽余烬、迫使退出形态
        msg, ok = t.execute_t0(p)
        self.assertTrue(ok)
        # 强制退场：返回消息含退出形态，且不出现实际反击/天裁结算段
        self.assertIn("退出形态", msg)
        self.assertNotIn("[反击]", msg)
        self.assertNotIn("[天裁]", msg)
        # 无额外载荷的退场清理：毁伤清空、回人形态、拉条减伤清除
        self.assertEqual(t.form, FORM_HUMAN)
        self.assertEqual(t.ruin_damage, 0)
        self.assertEqual(getattr(t, "_challenge_reduction", 0), 0)


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


class HumanPerformanceTest(unittest.TestCase):
    """人形态即演/公演：近战单体 +2 火种 / 公演扫击所有 engaged 目标 +2 火种。"""

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def _scene(self, sp=1, choices=()):
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
        p.location = "商店"
        t = Savior9("p1", state)
        p.talent = t
        state.m9_system.set_sp("p1", sp)
        return state, p, t

    def _add_engaged(self, state, p, pid="p2", hp=10):
        from models.equipment import Weapon, WeaponRange
        from utils.attribute import Attribute
        other = Player(pid, pid.upper(), controller=ForfeitController())
        other.location = p.location
        other.max_hp = 20
        other.hp = hp
        state.add_player(other)
        p.weapons.append(Weapon("小刀", Attribute.ORDINARY, 4,
                                WeaponRange.MELEE))
        state.markers.add_relation(p.player_id, "ENGAGED_WITH", other.player_id)
        return other

    def test_improvise_attacks_and_grants_ember(self) -> None:
        state, p, t = self._scene(sp=1, choices=("即演（1 SP）", "小刀", "P2"))
        other = self._add_engaged(state, p)
        before = other.hp
        option = t.get_t0_option(p)
        self.assertIsNotNone(option)
        self.assertEqual(option["m9_kind"], "g4_human_performance")
        msg, ok = t.execute_t0(p)
        self.assertTrue(ok, msg)
        self.assertLess(other.hp, before)
        self.assertEqual(state.m9_system.get_sp("p1"), 0)
        self.assertEqual(t.divinity, 1)  # R8：human_performance_ember 2→1

    def test_public_hits_all_engaged_and_grants_ember(self) -> None:
        state, p, t = self._scene(sp=2, choices=("公演（2 SP）", "小刀"))
        a = self._add_engaged(state, p, pid="p2", hp=10)
        b = self._add_engaged(state, p, pid="p3", hp=10)
        state.m9_system.register_performance("p1", state.current_round)
        state.m9_system.allocate_public_slot(state.current_round)
        before_a, before_b = a.hp, b.hp
        msg, ok = t.execute_t0(p)
        self.assertTrue(ok, msg)
        self.assertLess(a.hp, before_a)
        self.assertLess(b.hp, before_b)
        self.assertEqual(state.m9_system.get_sp("p1"), 0)
        self.assertEqual(t.divinity, 1)  # R8：human_performance_ember 2→1

    def test_no_option_without_engaged_target(self) -> None:
        state, p, t = self._scene(sp=2)
        from models.equipment import Weapon, WeaponRange
        from utils.attribute import Attribute
        p.weapons.append(Weapon("小刀", Attribute.ORDINARY, 4,
                                WeaponRange.MELEE))
        self.assertIsNone(t.get_t0_option(p))


if __name__ == "__main__":
    unittest.main()
