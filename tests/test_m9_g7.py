"""M9 G7 战术压制机制单测（阶段 3）：起床受限追演、连续射击重置、Terror
DIRECT_DAMAGE+absolute_dead、R0 即演豁免、门控替换工厂。"""
import random
import unittest
from types import SimpleNamespace

from engine import experiments
from engine.game_state import GameState
from engine.round_manager import RoundManager
from models.player import Player
from controllers.base import PlayerController
from controllers.forfeit_controller import ForfeitController

from engine.m9.gate import ensure_state_mechanisms, m9_talent_class
from engine.m9.talents.g7 import Hoshino9
from talents.g7.hoshino import Hoshino


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
        return True


class WakeFollowupTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")
        self.state = GameState()
        ensure_state_mechanisms(self.state)
        self.p = Player("p1", "G7",
                        controller=_FixedChoiceController("临战-Archer", "结束"))
        self.state.add_player(self.p)
        self.t = Hoshino9("p1", self.state)
        self.p.talent = self.t

    def tearDown(self) -> None:
        experiments.reset()

    def test_archer_wake_marks_followup_not_extra_turn(self) -> None:
        self.t.form = "临战-Archer"
        self.t.on_wakeup(self.p, self.state)
        self.assertTrue(self.t.wake_followup_available)
        self.assertFalse(getattr(self.p, "hoshino_wakeup_extra_turn", False))

    def test_wake_followup_end_returns_wake(self) -> None:
        self.t.form = "临战-Archer"
        self.t.wake_followup_available = True
        from engine.action_turn import ActionTurnManager
        follow = self.t.m9_wake_followup(self.p, ActionTurnManager(self.state))
        self.assertEqual(follow, "wake")
        self.assertFalse(self.t.wake_followup_available)


class ShootStreakResetTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")
        self.state = GameState()
        ensure_state_mechanisms(self.state)
        self.p = Player("p1", "G7", controller=ForfeitController())
        self.state.add_player(self.p)
        self.t = Hoshino9("p1", self.state)
        self.p.talent = self.t
        self.t.shoot_streak = 2

    def tearDown(self) -> None:
        experiments.reset()

    def test_end_shield_does_not_reset_streak(self) -> None:
        """M9：结束盾牌不重置连续射击计数（v2exp 重置 → 覆写为不清）。"""
        self.t.shield_mode = "架盾"
        self.t._end_shield_mode(self.p)
        self.assertEqual(self.t.shoot_streak, 2)

    def test_non_shoot_attack_resets_streak(self) -> None:
        """非射击攻击（引擎挂点调用 m9_reset_shoot_streak）→ 重置。"""
        self.t.m9_reset_shoot_streak()
        self.assertEqual(self.t.shoot_streak, 0)


class TerrorIdentityTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_terror_attack_is_absolute_death(self) -> None:
        """Terror 批量：DIRECT_DAMAGE + absolute_dead（T7 保险不赔付）。"""
        from engine.m9.combat import DeathAdjudicator
        from engine.m9.combat import is_absolute_death_source
        self.assertTrue(is_absolute_death_source("g7_terror"))
        insured = SimpleNamespace(
            on_death_check=lambda t, a: {"prevent_death": True, "new_hp": 10})
        target = SimpleNamespace(player_id="t1", hp=0, talent=insured)
        kind = DeathAdjudicator(None).adjudicate(target, None, "g7_terror")
        self.assertEqual(kind, "dead")  # 保险被跳过

    def test_terror_batch_damages_all_players(self) -> None:
        state = GameState()
        ensure_state_mechanisms(state)
        attacker = Player("p1", "星野", controller=ForfeitController())
        state.add_player(attacker)
        for i in range(2):
            t = Player(f"p{i+2}", f"目标{i+1}", controller=ForfeitController())
            t.location = "商店"
            t.hp = 3 if i == 0 else 10  # 一个致死、一个存活 → 未全灭
            state.add_player(t)
        t = Hoshino9("p1", state)
        attacker.talent = t
        t.is_terror = True
        t.form = "临战-Archer"
        t.terror_extra_hp = 20
        msg = t._terror_attack(attacker)
        self.assertLessEqual(state.get_player("p2").hp, 0)
        self.assertEqual(state.get_player("p3").hp, 6)  # 10-4
        self.assertLess(t.terror_extra_hp, 20)  # 未全灭 → 扣 cost


class RoundStartExemptTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")
        self.state = GameState()
        ensure_state_mechanisms(self.state)
        self.p = Player("p1", "G7", controller=ForfeitController())
        self.state.add_player(self.p)
        self.t = Hoshino9("p1", self.state)
        self.p.talent = self.t

    def tearDown(self) -> None:
        experiments.reset()

    def test_improvise_exempt_cancels_fatigue_minus_one(self) -> None:
        """即演豁免：R0 回满后不扣失却之痛 −1。"""
        self.t._macro_used_this_round = True
        self.t.m9_mark_improvise_exempt()
        self.t.cost = 5
        self.t.on_round_start(2)
        self.assertEqual(self.t.cost, 5)

    def test_fatigue_applies_without_exempt(self) -> None:
        self.t._macro_used_this_round = True
        self.t.cost = 5
        self.t.on_round_start(2)
        self.assertEqual(self.t.cost, 4)


class GateFactoryTest(unittest.TestCase):

    def tearDown(self) -> None:
        experiments.reset()

    def test_factory_swaps_g7_class_only_in_m9(self) -> None:
        _enable("m9_rfc")
        self.assertEqual(m9_talent_class(Hoshino), Hoshino9)
        _enable()
        self.assertEqual(m9_talent_class(Hoshino), Hoshino)


if __name__ == "__main__":
    unittest.main()
