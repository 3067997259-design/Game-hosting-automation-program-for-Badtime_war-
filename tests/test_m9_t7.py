"""M9 T7 死者苏生机制单测（批次 2）：系统级保险台账、即演/公演挂载、
全局唯一/不可重挂、普通死亡兑现、absolute_death 不赔付、兑现后落幕、
SP=0/彼岸 SP=2、只结算一次、T7 死后伏笔仍存续。"""
import unittest
from types import SimpleNamespace

from engine import experiments
from engine.game_state import GameState
from models.equipment import ArmorLayer, ArmorPiece
from models.player import Player
from controllers.forfeit_controller import ForfeitController
from utils.attribute import Attribute

from engine.m9.gate import ensure_state_mechanisms
from engine.m9.talents.t7 import Resurrection9, revive_hp


class _FixedController(ForfeitController):
    def __init__(self, *choices):
        super().__init__()
        self._choices = list(choices)

    def choose(self, prompt, options, context=None):
        if self._choices:
            choice = self._choices.pop(0)
            if choice in options:
                return choice
        return super().choose(prompt, options, context)


def _enable(*flags):
    experiments.reset()
    for f in flags:
        experiments.enable(f)


def _make(pids=("p1", "p2"), controller=None):
    state = GameState()
    ensure_state_mechanisms(state)
    players = {}
    for pid in pids:
        p = Player(pid, pid.upper(),
                   controller=controller or ForfeitController())
        state.add_player(p)
        p.max_hp = 20
        p.hp = 20
        p.location = "商店"
        players[pid] = p
    t = Resurrection9("p1", state)
    players["p1"].talent = t
    return state, players, t


def _regen_shield(name="魔法护盾"):
    return ArmorPiece(name, Attribute.MAGIC, ArmorLayer.OUTER, 5.0,
                      can_regen=True)


class InsuranceRegistryTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_mount_global_unique_no_overwrite(self) -> None:
        from engine.m9.insurance import InsuranceRegistry
        reg = InsuranceRegistry()
        self.assertTrue(reg.mount("p1", "p2"))
        self.assertFalse(reg.mount("p1", "p3"))   # 不可重挂/覆盖
        self.assertFalse(reg.mount("p4", "p2"))
        self.assertEqual(reg.mounted_target(), "p2")
        self.assertFalse(reg.is_retired())

    def test_cash_in_once_then_retired(self) -> None:
        from engine.m9.insurance import InsuranceRegistry
        reg = InsuranceRegistry()
        reg.mount("p1", "p2")
        rec = reg.cash_in()
        self.assertIsNotNone(rec)
        self.assertTrue(reg.is_retired())
        self.assertIsNone(reg.cash_in())          # 只结算一次
        self.assertFalse(reg.mount("p1", "p3"))   # 落幕不可再挂

    def test_record_survives_source_death(self) -> None:
        state, players, t = _make()
        t.on_register()
        # 挂载后 T7（p1）死亡：伏笔仍存在
        state.m9_insurance.mount("p1", "p2")
        players["p1"].hp = 0
        self.assertIsNotNone(state.m9_insurance.record())

    def test_m9_instance_has_no_legacy_learning_or_mount_fields(self) -> None:
        _state, _players, talent = _make()
        for field in ("learned", "learn_progress", "mounted_on", "used"):
            self.assertFalse(hasattr(talent, field), field)


class ResurrectionMountTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_improvise_mount_consumes_sp_and_dual_attention(self) -> None:
        state, players, t = _make(
            controller=_FixedController("即演（1 SP）", "P2"))
        m9 = state.m9_system
        m9.set_sp("p1", 1)
        self.assertIsNotNone(t.get_t0_option(players["p1"]))
        msg, ok = t.execute_t0(players["p1"])
        self.assertTrue(ok, msg)
        # 即演 −1 SP，随后发起者关注 +1（双向关注共同上限内）
        self.assertEqual(m9.get_sp("p1"), 1)
        self.assertEqual(state.m9_insurance.mounted_target(), "p2")
        # 双向关注：目标与 T7 各 +1（开局 SP=1，关注后 =2）
        self.assertEqual(m9.get_sp("p2"), 2)

    def test_public_mount_requires_seat(self) -> None:
        state, players, t = _make(controller=_FixedController("公演（2 SP）", "P2"))
        m9 = state.m9_system
        m9.set_sp("p1", 2)
        m9.register_performance("p1", 1)
        m9.assign_public_slot(1)
        msg, ok = t.execute_t0(players["p1"])
        self.assertTrue(ok, msg)
        # 公演 −2 SP，随后发起者关注 +1
        self.assertEqual(m9.get_sp("p1"), 1)
        self.assertEqual(state.m9_insurance.mounted_target(), "p2")

    def test_mount_target_can_be_self(self) -> None:
        state, players, t = _make()
        # 强制选自己：控制器的 choose 返回首个 = 玩家名列表首个（p1 或 p2 顺序）
        names = [state.get_player(pid).name for pid in state.player_order]
        self.assertIn("P1", names)
        state.m9_system.set_sp("p1", 1)
        t._mount_targets()
        msg, ok = t.execute_t0(players["p1"])
        self.assertTrue(ok, msg)
        target = state.m9_insurance.mounted_target()
        self.assertTrue(target in ("p1", "p2"))

    def test_no_mount_after_retired(self) -> None:
        state, players, t = _make()
        state.m9_insurance.mount("p1", "p2")
        state.m9_insurance.cash_in()
        self.assertIsNone(t.get_t0_option(players["p1"]))
        msg, ok = t.execute_t0(players["p1"])
        self.assertFalse(ok)

    def test_mount_precheck_fails_without_sp(self) -> None:
        state, players, t = _make()
        state.m9_system.set_sp("p1", 0)
        self.assertIsNone(t.get_t0_option(players["p1"]))  # SP=0
        msg, ok = t.execute_t0(players["p1"])
        self.assertFalse(ok)
        self.assertFalse(state.m9_insurance.is_mounted())


class ResurrectionCashInTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def _mounted(self):
        state, players, t = _make()
        state.m9_insurance.mount("p1", "p2")
        return state, players, t

    def test_ordinary_death_cash_in(self) -> None:
        state, players, t = self._mounted()
        p2 = players["p2"]
        p2.hp = 0
        result = t.on_death_check(p2, None)
        self.assertIsNotNone(result)
        self.assertTrue(result["prevent_death"])
        self.assertEqual(p2.hp, revive_hp())            # revive_hp（bget 参数化）
        self.assertEqual(p2.location, "home_p2")       # 家中复活
        self.assertTrue(p2.is_awake)
        self.assertEqual(state.m9_system.get_sp("p2"), 0)  # 普通复活 SP=0
        self.assertTrue(state.m9_insurance.is_retired())
        self.assertTrue(state.m9_insurance.is_cashed_in())

    def test_death_check_settles_exactly_once(self) -> None:
        state, players, t = self._mounted()
        p2 = players["p2"]
        p2.hp = 0
        first = t.on_death_check(p2, None)
        self.assertIsNotNone(first)
        second = t.on_death_check(p2, None)
        self.assertIsNone(second)                      # 只结算一次
        self.assertTrue(state.m9_insurance.is_retired())

    def test_other_target_not_cashed(self) -> None:
        state, players, t = self._mounted()
        result = t.on_death_check(players["p1"], None)  # 伏笔在 p2 上
        self.assertIsNone(result)
        self.assertFalse(state.m9_insurance.is_cashed_in())

    def test_regen_shields_restored_once(self) -> None:
        state, players, t = self._mounted()
        p2 = players["p2"]
        shield = _regen_shield()
        shield.is_broken = True
        p2.armor.outer.append(shield)
        p2.hp = 0
        t.on_death_check(p2, None)
        self.assertFalse(shield.is_broken)
        self.assertEqual(shield.current_hp, shield.max_hp)

    def test_absolute_death_skip_is_pipeline_level(self) -> None:
        """absolute_death 不赔付：DeathAdjudicator 在免死链之前分流。"""
        from engine.m9.combat import DeathAdjudicator, resolve_damage
        from engine.m9.gate import ensure_state_mechanisms
        state, players, t = self._mounted()
        p2 = players["p2"]
        p2.hp = 4
        attacker = SimpleNamespace(player_id="p3", name="袭击者",
                                   talent=None)
        state.add_player(Player("p3", "P3", controller=ForfeitController()))
        adjudicator = DeathAdjudicator(state)
        kind = adjudicator.adjudicate(p2, attacker, "g7_terror")
        self.assertEqual(kind, "dead")
        self.assertFalse(state.m9_insurance.is_cashed_in())  # 不赔付
        # DIRECT_DAMAGE（非绝对）仍可被保险承接
        p2.hp = 4
        r = resolve_damage(attacker, p2, weapon=None, game_state=state,
                           raw_damage_override=4,
                           damage_attribute_override="__无视__",
                           source_kind="g0_crossfire")
        self.assertFalse(r.get("killed"))
        self.assertTrue(state.m9_insurance.is_cashed_in())

    def test_far_shore_revive_sp2_and_marker_consumed(self) -> None:
        state, players, t = self._mounted()
        p2 = players["p2"]
        p2.talent = SimpleNamespace(
            m9_poem_markers={"far_shore_watch": True}, name="测试")
        p2.hp = 0
        result = t.on_death_check(p2, None)
        self.assertIsNotNone(result)
        self.assertEqual(state.m9_system.get_sp("p2"), 2)  # 彼岸强化
        self.assertFalse(p2.talent.m9_poem_markers["far_shore_watch"])
        self.assertTrue(any(w.name == "小刀" for w in p2.weapons))

    def test_burn_death_uses_global_insurance_before_finalization(self) -> None:
        from engine.round_manager import RoundManager

        state, players, _talent = _make()
        p2 = players["p2"]
        state.m9_insurance.mount("p1", "p2")
        p2.hp = 1
        p2.burn_stacks = 1

        RoundManager(state)._process_burn_stacks_m4()

        self.assertEqual(p2.hp, revive_hp())
        self.assertTrue(state.m9_insurance.is_cashed_in())
        self.assertFalse(any(e.get("type") == "death"
                             and e.get("player") == "p2"
                             for e in state.event_log))

    def test_kill_not_counted_for_killer(self) -> None:
        """兑现路径不产生击杀（管线 prevented → killed=False）。"""
        from engine.m9.combat import resolve_damage
        state, players, t = self._mounted()
        attacker = SimpleNamespace(player_id="p3", name="袭击者",
                                   talent=None, kill_count=0)
        state.add_player(Player("p3", "P3", controller=ForfeitController()))
        p2 = players["p2"]
        p2.hp = 4
        r = resolve_damage(attacker, p2, weapon=None, game_state=state,
                           raw_damage_override=8,
                           damage_attribute_override="普通")
        self.assertFalse(r.get("killed"))
        self.assertEqual(attacker.kill_count, 0)
        self.assertEqual(p2.location, "home_p2")


if __name__ == "__main__":
    unittest.main()
