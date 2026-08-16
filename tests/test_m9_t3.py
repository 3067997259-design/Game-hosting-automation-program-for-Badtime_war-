"""M9 T3 天星合同测试（批次 0 先行：2026-08-11 地点裁决）。

新断言（DOC-045 追加裁决 + T3/T7 v0.3 增量）：
- 公演报名时不保存目标地点；执行时读取发动者当前地点；
- 不出现地点选择 UI（execute_t0 全程不调 controller.choose 选地点/选目标）；
- 只影响当前地点的其他合法单位；施法者自身排除；
- 其余 T3 合同不漂移：无即演入口、单次完整核心（不追加第二次攻击）、
  defense_coefficient=0 完全穿防、无 uses_remaining、石化施加。

adapter 位于 engine.m9.talents.t3.Star9；实现前本文件经 importorskip 跳过。
"""
import unittest
import unittest.mock
import pytest

from controllers.base import PlayerController

from engine import experiments
from engine.game_state import GameState
from models.player import Player
from controllers.forfeit_controller import ForfeitController

from engine.m9.gate import ensure_state_mechanisms

Star9 = pytest.importorskip("engine.m9.talents.t3").Star9


def _enable(*flags):
    experiments.reset()
    for f in flags:
        experiments.enable(f)


class _RecordingController(PlayerController):
    """记录 choose 调用，默认返回首个选项。"""

    def __init__(self, *choices):
        super().__init__()
        self.calls = []
        self._choices = list(choices)

    def choose(self, prompt, options, context=None):
        self.calls.append((prompt, list(options)))
        if self._choices:
            choice = self._choices.pop(0)
            return choice if choice in options else options[0]
        return options[0]

    def get_command(self, player, game_state, available_actions, context=None):
        return "forfeit"

    def choose_multi(self, prompt, options, max_count, min_count=0, context=None):
        return list(options)[:max_count]

    def confirm(self, prompt, context=None):
        return True


def _make(*pids):
    """创建 state + 玩家 + T3 天赋；返回 (state, performer, talent, others)。"""
    state = GameState()
    ensure_state_mechanisms(state)
    others = []
    performer = None
    for i, pid in enumerate(pids):
        p = Player(pid, f"玩家{i}", controller=_RecordingController())
        state.add_player(p)
        p.max_hp = 20
        p.hp = 20
        p.location = "公园"
        if i == 0:
            performer = p
        else:
            others.append(p)
    t = Star9(performer.player_id, state)
    performer.talent = t
    return state, performer, t, others


class StarLocationContractTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def _seat_and_sp(self, state, pid, round_num=1):
        """SP=2 + 公演位归属该玩家。"""
        m9 = state.m9_system
        m9.set_sp(pid, 2)
        m9.register_performance(pid, round_num)
        self.assertEqual(m9.assign_public_slot(round_num), pid)

    def test_execution_reads_current_location_not_register_location(self) -> None:
        """报名后、执行前改变发动者地点：天星落在新地点（报名不锁定地点）。"""
        state, performer, t, others = _make("p1", "p2", "p3")
        p2, p3 = others
        state.m9_system.set_sp("p1", 2)
        state.m9_system.register_performance("p1", 1)
        self.assertEqual(state.m9_system.assign_public_slot(1), "p1")
        # 报名后发动者移动
        performer.location = "学校"
        p2.location = "学校"
        p3.location = "公园"
        msg, ok = t.execute_t0(performer)
        self.assertTrue(ok, msg)
        self.assertEqual(p3.hp, 20)                       # 原报名地点无人受伤
        self.assertLess(p2.hp, 20)                        # 当前地点单位受伤
        self.assertEqual(performer.hp, 20)                # 施法者自身不命中

    def test_no_location_selection_ui(self) -> None:
        """execute_t0 全程不出现地点选择（也不选择目标：AOE 由当前地点决定）。"""
        state, performer, t, others = _make("p1", "p2", "p3")
        self._seat_and_sp(state, "p1")
        msg, ok = t.execute_t0(performer)
        self.assertTrue(ok, msg)
        controller = performer.controller
        self.assertIsInstance(controller, _RecordingController)
        self.assertEqual(controller.calls, [])            # 无任何 choose 调用

    def test_only_other_units_at_current_location_affected(self) -> None:
        state, performer, t, others = _make("p1", "p2", "p3", "p4")
        p2, p3, p4 = others
        p2.location = "公园"
        p3.location = "公园"
        p4.location = "医院"
        self._seat_and_sp(state, "p1")
        msg, ok = t.execute_t0(performer)
        self.assertTrue(ok, msg)
        self.assertLess(p2.hp, 20)
        self.assertLess(p3.hp, 20)
        self.assertEqual(p4.hp, 20)                       # 异地不受伤
        self.assertEqual(performer.hp, 20)                # 同地点也不命中自己
        self.assertTrue(state.markers.has("p2", "PETRIFIED"))
        self.assertTrue(state.markers.has("p3", "PETRIFIED"))
        self.assertFalse(state.markers.has("p4", "PETRIFIED"))

    def test_shadow_at_location_is_affected(self) -> None:
        """影身（统一 actor）也属于当前地点合法单位。"""
        from types import SimpleNamespace
        state, performer, t, others = _make("p1", "p2")
        p2 = others[0]
        shadow = SimpleNamespace(player_id="p2@shadow", owner_pid="p2",
                                 location="公园", hp=20, is_alive=lambda: True,
                                 talent=None)
        state.m9_shadows["p2@shadow"] = shadow
        self._seat_and_sp(state, "p1")
        msg, ok = t.execute_t0(performer)
        self.assertTrue(ok, msg)
        self.assertLess(shadow.hp, 20)

    def test_public_only_no_improvise_and_no_uses_remaining(self) -> None:
        state, performer, t, others = _make("p1", "p2")
        m9 = state.m9_system
        # SP<2：无任何 T0 选项
        m9.set_sp("p1", 1)
        self.assertIsNone(t.get_t0_option(performer))
        # 满足公演条件才出现
        m9.set_sp("p1", 2)
        m9.register_performance("p1", 1)
        option = t.get_t0_option(performer)
        self.assertIsNotNone(option)
        self.assertNotIn("improvise", option.get("m9_kind", ""))
        # 无次数制
        self.assertFalse(hasattr(t, "uses_remaining"))
        # 执行后 SP 扣 2
        msg, ok = t.execute_t0(performer)
        self.assertTrue(ok, msg)
        self.assertEqual(m9.get_sp("p1"), 0)

    def test_full_pierce_defense_coefficient_zero(self) -> None:
        """defense_coefficient=0：完全穿防，不受属性防御影响（仍受 flat 减伤）。"""
        from models.equipment import ArmorLayer, ArmorPiece
        from utils.attribute import Attribute
        from engine.balance import get as bget
        state, performer, t, others = _make("p1", "p2")
        p2 = others[0]
        starfall = int(bget("m9_talents_extended", "t3", "starfall_damage",
                            default=4))
        p2.armor.equip(ArmorPiece(
            "陶瓷护甲", Attribute.TECH, ArmorLayer.OUTER, 10.0))
        p2.hp = 20
        self._seat_and_sp(state, "p1")
        msg, ok = t.execute_t0(performer)
        self.assertTrue(ok, msg)
        self.assertEqual(p2.hp, 20 - starfall)            # 无防御吸收

    def test_single_core_no_second_attack(self) -> None:
        """一次完整核心：每个单位只承受一次伤害应用与一次石化。"""
        state, performer, t, others = _make("p1", "p2")
        p2 = others[0]
        p2.hp = 20
        self._seat_and_sp(state, "p1")
        msg, ok = t.execute_t0(performer)
        self.assertTrue(ok, msg)
        from engine.balance import get as bget
        starfall = int(bget("m9_talents_extended", "t3",
                            "starfall_damage", default=4))
        self.assertEqual(p2.hp, 20 - starfall)
        self.assertEqual(p2.hp, 20 - starfall)             # 恰好一次，无追加段

    def test_aoe_death_flows_through_pipeline(self) -> None:
        """AOE 击杀经由 m9 结算管线（hp 归零即死），不自行清理死亡。"""
        from engine.balance import get as bget
        starfall = int(bget("m9_talents_extended", "t3",
                            "starfall_damage", default=4))
        state, performer, t, others = _make("p1", "p2")
        p2 = others[0]
        p2.hp = starfall
        self._seat_and_sp(state, "p1")
        msg, ok = t.execute_t0(performer)
        self.assertTrue(ok, msg)
        self.assertEqual(p2.hp, 0)

    def test_execute_revalidates_current_location_before_spend(self) -> None:
        state, performer, t, others = _make("p1", "p2")
        p2 = others[0]
        self._seat_and_sp(state, "p1")
        self.assertIsNotNone(t.get_t0_option(performer))
        p2.location = "医院"

        msg, ok = t.execute_t0(performer)

        self.assertFalse(ok, msg)
        self.assertEqual(state.m9_system.get_sp("p1"), 2)
        self.assertEqual(p2.hp, 20)

    def test_m9_police_uses_damage_and_petrify_registries(self) -> None:
        state, performer, t, _others = _make("p1", "p2")
        unit = state.m9_police.ensure_roster()[0]
        unit.location = performer.location
        self._seat_and_sp(state, "p1")

        msg, ok = t.execute_t0(performer)

        self.assertTrue(ok, msg)
        from engine.balance import get as bget
        starfall = int(bget("m9_talents_extended", "t3",
                            "starfall_damage", default=4))
        self.assertEqual(unit.hp, unit.max_hp - starfall)
        self.assertTrue(state.m9_petrify.is_petrified(unit.player_id))

    def test_stars_marker_bounces_twice_then_consumes(self) -> None:
        state, performer, t, others = _make("p1", "p2")
        p2 = others[0]
        t.m9_poem_markers = {"stars_bounce": True}
        self._seat_and_sp(state, "p1")

        msg, ok = t.execute_t0(performer)

        self.assertTrue(ok, msg)
        from engine.balance import get as bget
        starfall = int(bget("m9_talents_extended", "t3",
                            "starfall_damage", default=4))
        bounce = int(bget("m9_system", "pp", "t3_aoe_damage", default=2))
        self.assertEqual(p2.hp, 20 - starfall - bounce * 2)  # 天星 + 群星弹射 ×2
        self.assertFalse(t.m9_poem_markers["stars_bounce"])


class PetrifyRegistryAuditTest(unittest.TestCase):
    """审计 v0.1 场景 8/10：有效伤害统一 + T3 同槽挣脱。"""

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def _scene(self):
        state = GameState()
        ensure_state_mechanisms(state)
        p = Player("p1", "T3", controller=ForfeitController())
        state.add_player(p)
        return state, p

    def test_effective_damage_only_advances_shake(self) -> None:
        """场景 8：A=0 或只损耗未破护甲/掩体不推进摇晃；H≥1 或耐久归零推进。
        摇晃推进门控在结算层：combat.resolve_damage 仅对有效命中调用
        petrify.on_effective_hit（A=0 先以 zero_damage 短路）。"""
        from engine.m9.combat import resolve_damage
        state, p = self._scene()
        attacker = Player("p2", "路人", controller=ForfeitController())
        state.add_player(attacker)
        reg = state.m9_petrify
        reg.apply(state, p, duration=4, source_pid="p3")
        # A=0 → zero_damage 短路，不推进摇晃
        r0 = resolve_damage(
            attacker, p, None, state, raw_damage_override=0,
            damage_attribute_override="普通", _skip_outgoing_hook=True)
        self.assertEqual(r0["reason"], "zero_damage")
        self.assertEqual(reg.shake_count("p1"), 0)
        # H≥1 → 推进一次摇晃
        r1 = resolve_damage(
            attacker, p, None, state, raw_damage_override=5,
            damage_attribute_override="普通", _skip_outgoing_hook=True)
        self.assertGreaterEqual(r1["hp_damage"], 1)
        self.assertEqual(reg.shake_count("p1"), 1)
        # 第二次有效命中 → 摇晃解除
        r2 = resolve_damage(
            attacker, p, None, state, raw_damage_override=5,
            damage_attribute_override="普通", _skip_outgoing_hook=True)
        self.assertNotIn("p1", reg._states)

    def test_t3_same_slot_two_break_attempts(self) -> None:
        """场景 10：同槽至多两次挣脱（各 1 SP、50%）；成功返回正常菜单。"""
        import random
        from engine.m9.petrify import PetrifyRegistry
        state, p = self._scene()
        reg = state.m9_petrify
        reg.apply(state, p, duration=4, source_pid="p3")
        # 第一次失败
        with unittest.mock.patch("random.random", return_value=0.9):
            ok1 = reg.attempt_break(state, p, 1)
        self.assertFalse(ok1)
        self.assertEqual(reg.break_attempts_left(1, "p1"), 1)  # 同槽第二次可用
        # 第二次成功
        with unittest.mock.patch("random.random", return_value=0.1):
            ok2 = reg.attempt_break(state, p, 1)
        self.assertTrue(ok2)
        self.assertNotIn("p1", reg._states)
        self.assertEqual(reg.break_attempts_left(1, "p1"), 0)  # 次数耗尽


if __name__ == "__main__":
    unittest.main()
