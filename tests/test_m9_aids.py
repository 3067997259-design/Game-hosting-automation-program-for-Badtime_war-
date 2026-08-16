"""M9 魂援被动双通道接线 + 死者投注决策测试（B4 v0.4 §5.1/§四）。"""
import unittest
from types import SimpleNamespace

from engine import experiments
from engine.game_state import GameState
from engine.m9.gate import ensure_state_mechanisms
from models.player import Player
from controllers.forfeit_controller import ForfeitController

from engine.m9.aids import (
    afterlife_members, trigger_passive_attack_aid,
    trigger_passive_defense_aid,
)


def _enable(*flags):
    experiments.reset()
    for f in flags:
        experiments.enable(f)


def _player(pid, hp=20, slot=None):
    p = Player(pid, pid.upper(), controller=ForfeitController())
    p.hp = hp
    p.max_hp = 20
    if slot:
        p.talent_slot_id = slot
    return p


class AfterlifeTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_afterlife_members_excludes_absolute_and_retreated(self) -> None:
        state = GameState()
        ensure_state_mechanisms(state)
        alive = _player("p1")
        state.add_player(alive)
        dead = _player("p2")
        dead.hp = 0
        state.add_player(dead)
        absolute = _player("p3")
        absolute.hp = 0
        state.add_player(absolute)
        state.m9_pp.freeze("p3")                     # 绝对死亡：不进往世层
        retreated = _player("p4")
        retreated.hp = 0
        state.add_player(retreated)
        retreated.talent = SimpleNamespace(
            is_retreated=lambda: True)              # G0 撤退：不进往世层
        self.assertEqual(afterlife_members(state), ["p2"])


class PassiveAidTriggerTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def _world(self):
        state = GameState()
        ensure_state_mechanisms(state)
        attacker = _player("p1")
        target = _player("p2")
        provider = _player("p3", slot="T1")          # 死者提供 T1 进攻援助
        provider.hp = 0
        state.add_player(attacker)
        state.add_player(target)
        state.add_player(provider)
        return state, attacker, target, provider

    def test_attack_aid_defers_when_afterlife_empty(self) -> None:
        state, attacker, target, provider = self._world()
        provider.hp = 20                              # 无人死亡：往世层空
        hit = SimpleNamespace(damage=1, broken=[])
        trigger_passive_attack_aid(attacker, target, state, hit)
        self.assertFalse(getattr(
            attacker, "_m9_aid_passive_attack_done", False))
        self.assertEqual(state.m9_pp.aid_quota_left("p1"), 4)

    def test_attack_aid_triggers_once_with_provider_reward(self) -> None:
        state, attacker, target, provider = self._world()
        hit = SimpleNamespace(damage=1, broken=[])
        trigger_passive_attack_aid(attacker, target, state, hit)
        self.assertTrue(getattr(attacker, "_m9_aid_passive_attack_done", False))
        self.assertTrue(getattr(attacker, "_aid_t1_zero_def_once", False))
        self.assertEqual(state.m9_pp.aid_quota_left("p1"), 3)
        self.assertEqual(state.m9_pp.aid_earnings("p3"),
                         state.m9_pp.passive_aid_reward())
        events = [e for e in state.event_log if e.get("type") == "aid_effect"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["talent"], "T1")
        # 第二次攻击不再触发
        state.m9_pp._aid_quota_used["p1"] = 0
        trigger_passive_attack_aid(attacker, target, state, hit)
        self.assertEqual(state.m9_pp.aid_quota_left("p1"), 4)

    def test_defense_aid_triggers_on_low_hp_survives(self) -> None:
        state = GameState()
        ensure_state_mechanisms(state)
        victim = _player("p1")
        victim.hp = 3
        enemy = _player("p2")
        provider = _player("p3", slot="T4")          # T4 防御：当身免疫标记
        provider.hp = 0
        state.add_player(victim)
        state.add_player(enemy)
        state.add_player(provider)
        hit = SimpleNamespace(damage=4, broken=[], attribute="普通")
        trigger_passive_defense_aid(victim, enemy, state, hit)
        self.assertTrue(getattr(victim, "_m9_aid_passive_defense_done", False))
        self.assertIsNotNone(getattr(victim, "_aid_t4_immune_once_round", None))
        self.assertEqual(state.m9_pp.aid_quota_left("p1"), 3)

    def test_defense_aid_no_trigger_on_lethal_or_high_hp(self) -> None:
        state = GameState()
        ensure_state_mechanisms(state)
        victim = _player("p1")
        victim.hp = 0                                # 已死亡：不触发
        enemy = _player("p2")
        provider = _player("p3", slot="T4")
        provider.hp = 0
        state.add_player(victim)
        state.add_player(enemy)
        state.add_player(provider)
        hit = SimpleNamespace(damage=4, broken=[], attribute="普通")
        trigger_passive_defense_aid(victim, enemy, state, hit)
        self.assertFalse(getattr(
            victim, "_m9_aid_passive_defense_done", False))
        victim.hp = 9                                # 高于阈值：不触发
        trigger_passive_defense_aid(victim, enemy, state, hit)
        self.assertFalse(getattr(
            victim, "_m9_aid_passive_defense_done", False))


class BetWindowDecisionTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_dead_bettor_decisions(self) -> None:
        from controllers.ai.decision.t0_policy import m9_decide_choose
        state = GameState()
        ensure_state_mechanisms(state)
        dead = _player("p1")
        dead.hp = 0
        state.add_player(dead)
        strong = _player("p2")
        strong.kill_count = 3
        state.add_player(strong)
        weak = _player("p3")
        weak.kill_count = 0
        state.add_player(weak)
        state.m9_pp.earn("p1", 5)
        ctrl = SimpleNamespace(personality="balanced", _player=dead,
                               _game_state=state)
        out = m9_decide_choose(
            ctrl, "P1 开市窗口：", ["押注", "追加", "转仓", "不交易"],
            {}, state)
        self.assertEqual(out, "押注")
        out = m9_decide_choose(
            ctrl, "押注对象：", ["P2", "P3"], {}, state)
        self.assertEqual(out, "P2")                   # 击杀数高的强者
        out = m9_decide_choose(
            ctrl, "押注 PP（≥1）：", ["1", "2", "3", "4", "5"], {}, state)
        self.assertEqual(out, "3")                    # 一半预算（5→3）
        state.m9_pp.spend("p1", 5)
        out = m9_decide_choose(
            ctrl, "P1 开市窗口：", ["押注", "追加", "转仓", "不交易"],
            {}, state)
        self.assertEqual(out, "不交易")               # 无余额


if __name__ == "__main__":
    unittest.main()
