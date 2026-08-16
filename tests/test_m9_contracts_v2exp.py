"""M9 合同测试补缺（B-2）—— 对 v2exp 现行结构中可先测的缺口项。

缺口清单来源：B0–B3 审计（control T1/T2 entry、acted_this_round 语义、
full-extra 白名单/上限/递归、T4 两→一、T5 FC 时延、G6 模板池、G7 单收尾、
T3 SP 合法性、T7 死亡后持久化、G1 三熵、G4 十二烬/挑战/响应）。

本文件只测「结构未变、今天即可断言」的项；需要 M9 引擎机制（ActionGrant、
SP 0/1/2、slot_resolved/resolution_kind、m9-rfc profile）的合同断言
统一标注“随 B-3 迁移同步”，见 docs/audits/m9-contract-test-gap-ledger-v0.1.md。
"""
import random
import unittest
from types import SimpleNamespace

from engine import experiments
from engine.game_state import GameState
from engine.round_manager import RoundManager
from models.player import Player
from controllers.base import PlayerController
from controllers.forfeit_controller import ForfeitController

from talents.g1_firefly import G1MythFire
from talents.g4_savior import Savior
from talents.t3_star import Star
from talents.t4_hexagram import Hexagram
from talents.t5_combo import Combo
from talents.t7_resurrection import Resurrection


_M7 = ("k_initiative", "hp20", "m3_accuracy", "m4_gear",
       "m5_clock", "m6_scoring", "m7_talents")


def _enable(*flags):
    experiments.reset()
    for f in flags:
        experiments.enable(f)


class _FakeState:
    """T7 持久化测试用最小状态：玩家仍保留在 dict（死亡不删除对象）。"""

    def __init__(self, players):
        self._players = players
        self.markers = SimpleNamespace(on_player_death=lambda pid: None)

    def get_player(self, pid):
        return self._players.get(pid)

    def alive_players(self):
        return [p for p in self._players.values() if p.is_alive()]

    def log_event(self, *args, **kwargs):
        pass


def _fake_player(pid, name, location="home", alive=True, hp=5.0, talent=None):
    p = SimpleNamespace(player_id=pid, name=name, location=location,
                        is_awake=True, hp=hp, max_hp=5.0, armor=None,
                        talent=talent, is_alive=lambda: alive)
    p.is_dead = not alive
    return p


class T7DeathPersistenceTest(unittest.TestCase):
    """缺口项：T7 死亡后持久化——已挂载保险在 T7 本人死亡后仍须生效（v2exp 即已隐含）。"""

    def setUp(self) -> None:
        _enable()
        self.t7_player = _fake_player("p1", "T7")
        self.target = _fake_player("p2", "目标")
        self.t7 = Resurrection("p1", None)
        self.t7.learned = True
        self.t7.mounted_on = "p2"
        self.t7_player.talent = self.t7

    def tearDown(self) -> None:
        experiments.reset()

    def test_insurance_fires_after_t7_death(self) -> None:
        """T7 死亡后（对象仍在 players dict 中），目标致死仍触发 prevent_death。"""
        state = _FakeState({"p1": self.t7_player, "p2": self.target})
        self.t7.state = state
        # T7 本人死亡（不删对象，R4 死亡检查循环仍遍历其天赋）
        self.t7_player.is_alive = lambda: False
        result = self.t7.on_death_check(self.target, "test_source")
        self.assertIsNotNone(result)
        self.assertTrue(result["prevent_death"])
        self.assertEqual(self.t7.used, True)
        self.assertEqual(self.target.location, "home_p2")
        self.assertTrue(self.target.is_awake)

    def test_insurance_global_once_after_trigger(self) -> None:
        """兑现后 used=True → 第二次致死不再触发（现有全局一次语义）。"""
        state = _FakeState({"p1": self.t7_player, "p2": self.target})
        self.t7.state = state
        self.t7.on_death_check(self.target, "s")
        second = self.t7.on_death_check(self.target, "s")
        self.assertIsNone(second)


class T5FCDelayTest(unittest.TestCase):
    """缺口项：T5 FC 时延——T2 判定、R4 结算，FC 追加行动落在下一轮（DOC-024 v2exp 现状）。"""

    def tearDown(self) -> None:
        experiments.reset()

    def test_fc_grant_delayed_to_next_round_extra_turns(self) -> None:
        """FC 结算只写 pending_extra_turns=1，不立即插队——即“时延”本体。"""
        _enable(*_M7)
        state = GameState()
        p = Player("p1", "T5", controller=ForfeitController())
        state.add_player(p)
        t = Combo("p1", state)
        p.talent = t
        p.max_hp = 20
        p.hp = 12
        t.chart_active = True
        t.current_chart = [{"category": "attack", "beat": 5, "result": "perfect"}]
        t._resolve_chart(p, 6)
        self.assertEqual(t.current_chart, [])
        self.assertEqual(p.pending_extra_turns, 1)
        # 槽位记录：M9 的“FC 不再产生 full_extra、T5 退役转 G0”断言随 B-3 迁移同步


class HexagramTwoPhaseTest(unittest.TestCase):
    """缺口项：T4 两→一——v2exp 现状为 hexagram_extra_turn=2（两次额外回合），
    M9 改为单个 full_extra ActionGrant（随 B-3 迁移同步）。"""

    def setUp(self) -> None:
        _enable()
        self.state = GameState()
        self.p = Player("p1", "T4", controller=ForfeitController())
        self.state.add_player(self.p)
        self.t = Hexagram("p1", self.state)
        self.p.talent = self.t

    def tearDown(self) -> None:
        experiments.reset()

    def test_scissors_paper_grants_two_extra_turns(self) -> None:
        self.t.charges = 2
        self.t._scissors_paper(self.p, self.p)
        self.assertEqual(self.p.hexagram_extra_turn, 2)

    def test_t0_option_gated_by_charges(self) -> None:
        self.t.charges = 0
        self.assertIsNone(self.t.get_t0_option(self.p))
        self.t.charges = 1
        self.assertIsNotNone(self.t.get_t0_option(self.p))


class StarUsesGateTest(unittest.TestCase):
    """缺口项：T3 SP 合法性——v2exp 现状用 uses_remaining 门控（无 SP 层）；
    M9 的“仅 2 SP 公演、删除即演入口”断言随 B-3 迁移同步。"""

    def setUp(self) -> None:
        _enable()
        self.state = GameState()
        self.p = Player("p1", "T3", controller=ForfeitController())
        self.p2 = Player("p2", "路人", controller=ForfeitController())
        self.state.add_player(self.p)
        self.state.add_player(self.p2)
        self.t = Star("p1", self.state)
        self.p.talent = self.t

    def tearDown(self) -> None:
        experiments.reset()

    def test_t0_option_none_when_uses_exhausted(self) -> None:
        self.t.uses_remaining = 0
        self.assertIsNone(self.t.get_t0_option(self.p))
        self.t.uses_remaining = 1
        self.assertIsNotNone(self.t.get_t0_option(self.p))


class FireflyDebuffOrderTest(unittest.TestCase):
    """缺口项：G1 三熵——v2exp 现状为每 2 轮 debuff：炽愿先抵扣、外甲优先；
    失熵量表/三形态断言随 B-3 迁移同步。"""

    def setUp(self) -> None:
        _enable()
        self.state = GameState()
        self.p = Player("p1", "G1", controller=ForfeitController())
        self.state.add_player(self.p)
        self.t = G1MythFire("p1", self.state)
        self.p.talent = self.t

    def tearDown(self) -> None:
        experiments.reset()

    def test_ardent_wish_deducts_before_debuff(self) -> None:
        from models.equipment import ArmorLayer, ArmorPiece
        from utils.attribute import Attribute
        shield = ArmorPiece("盾牌", Attribute.ORDINARY, ArmorLayer.OUTER, 1.0,
                            defense_map={"普通": 2}, durability=8)
        self.p.armor.equip(shield)
        self.t.ardent_wish_charges = 2
        self.t._debuff_last_settled_round = 5
        self.t._try_debuff_settle(self.p, 7)
        self.assertEqual(self.t.ardent_wish_charges, 1)
        self.assertEqual(self.t.debuff_tick_count, 1)

    def test_outer_armor_destroyed_first(self) -> None:
        from models.equipment import ArmorLayer, ArmorPiece
        from utils.attribute import Attribute
        outer = ArmorPiece("盾牌", Attribute.ORDINARY, ArmorLayer.OUTER, 1.0,
                           defense_map={"普通": 2}, durability=8)
        inner = ArmorPiece("晶化皮肤", Attribute.ORDINARY, ArmorLayer.INNER, 1.0)
        self.p.armor.equip(outer)
        self.p.armor.equip(inner)
        self.t._execute_debuff(self.p, 7)
        self.assertEqual(len(self.p.armor.get_active(ArmorLayer.OUTER)), 0)
        self.assertEqual(len(self.p.armor.get_active(ArmorLayer.INNER)), 1)


class SaviorDivinityCapTest(unittest.TestCase):
    """缺口项：G4 十二烬/挑战/响应——v2exp 现状 divinity 封顶 12（余烬与挑战快照随 B-3）。"""

    def setUp(self) -> None:
        _enable()
        self.state = GameState()
        self.p = Player("p1", "G4", controller=ForfeitController())
        self.state.add_player(self.p)
        self.t = Savior("p1", self.state)
        self.p.talent = self.t

    def tearDown(self) -> None:
        experiments.reset()

    def test_divinity_capped_at_twelve(self) -> None:
        for _ in range(20):
            self.t.gain_divinity(1)
        self.assertEqual(self.t.divinity, 12)
        self.assertEqual(self.t.divinity, self.t.MAX_DIVINITY)


class ActedThisRoundSemanticsTest(unittest.TestCase):
    """缺口项：acted_this_round 语义——v2exp 现状是单一布尔（forfeit 置 True、
    status/shock_recover 不置）；M9 的逐槽位 outcome 记录断言随 B-3 迁移同步。"""

    def setUp(self) -> None:
        _enable("k_initiative")

    def tearDown(self) -> None:
        experiments.reset()

    def test_forfeit_sets_flag_true(self) -> None:
        """ForfeitController 全弃权：执行真实 R3 后行动者 acted_this_round=True，
        K 模式坐牢者保持 False（现状语义；逐槽位记录随 B-3）。"""
        state = GameState()
        for i in range(4):
            p = Player(f"p{i+1}", f"玩家{i+1}", controller=ForfeitController())
            p.is_awake = True
            p.location = "商店"
            state.add_player(p)
        rm = RoundManager(state)
        random.seed(5)
        rm._phase_r1()
        rm._phase_r3()
        acted = set(state.round_winners)
        for pid in state.player_order:
            p = state.get_player(pid)
            if pid in acted:
                self.assertTrue(p.acted_this_round, f"{pid} 弃权应视为行动")
                self.assertGreaterEqual(p.total_action_turns, 1)
            else:
                self.assertFalse(p.acted_this_round, f"{pid} 坐牢不应置行动标记")


class _FixedChoiceController(PlayerController):
    """测试用控制器：choose 恒返回预定项。"""

    def __init__(self, choice):
        self._choice = choice

    def get_command(self, player, game_state, available_actions, context=None):
        return "forfeit"

    def choose(self, prompt, options, context=None):
        return self._choice

    def choose_multi(self, prompt, options, max_count, min_count=0, context=None):
        return options[:max_count]

    def confirm(self, prompt, context=None):
        return True


class PetrifyControlEntryTest(unittest.TestCase):
    """缺口项：control T1/T2 entry——石化进入行动槽的 v2exp 现状（T0 二选一）；
    M9 的压制/统一收尾断言随 B-3 迁移同步。"""

    def tearDown(self) -> None:
        experiments.reset()

    def test_petrify_hold_skips_slot(self) -> None:
        _enable()
        state = GameState()
        p = Player("p1", "石化者",
                   controller=_FixedChoiceController("保持石化（本回合跳过）"))
        p.is_awake = True
        state.add_player(p)
        state.markers.add("p1", "PETRIFIED")
        p.is_petrified = True
        from engine.action_turn import ActionTurnManager
        result = ActionTurnManager(state)._phase_t0(p)
        self.assertEqual(result, "petrify_skip")
        self.assertTrue(p.is_petrified)

    def test_petrify_release_clears_and_damages(self) -> None:
        _enable("hp20")
        state = GameState()
        p = Player("p1", "石化者",
                   controller=_FixedChoiceController("解除石化（受0.5伤害）"))
        p.is_awake = True
        p.hp = 10.0
        state.add_player(p)
        state.markers.add("p1", "PETRIFIED")
        p.is_petrified = True
        from engine.action_turn import ActionTurnManager
        ActionTurnManager(state)._phase_t0(p)
        self.assertFalse(p.is_petrified)
        self.assertFalse(state.markers.has("p1", "PETRIFIED"))
        self.assertEqual(p.hp, 8.0)  # hp20: petrify_release_damage=2


if __name__ == "__main__":
    unittest.main()
