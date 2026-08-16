"""M9 通用决策面测试（t0_policy：T0 发动 / R0 公演报名 / 石化挣脱 / 焚诏拉条）。"""
import unittest
from types import SimpleNamespace

from engine import experiments
from engine.game_state import GameState
from models.player import Player

from controllers.ai.decision.t0_policy import (
    m9_decide_choose, should_accept_burn_challenge,
    should_activate_t0, should_attempt_breakout,
    should_continue_breakout, should_register_public,
)
from controllers.base import PlayerController


def _enable(*flags):
    experiments.reset()
    for f in flags:
        experiments.enable(f)


class _C(PlayerController):
    def get_command(self, player, game_state, available_actions, context=None):
        return "forfeit"

    def choose(self, prompt, options, context=None):
        return options[0] if options else ""

    def choose_multi(self, prompt, options, max_count, min_count=0, context=None):
        return list(options)[:max_count]

    def confirm(self, prompt, context=None):
        return True


class T0PolicyTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def _ctrl(self, personality="balanced"):
        state = GameState()
        p = Player("p1", "AI1", controller=_C())
        p.is_awake = True
        p.location = "公园"
        p.hp = 20
        p.max_hp = 20
        state.add_player(p)
        p2 = Player("p2", "AI2", controller=_C())
        p2.is_awake = True
        p2.location = "商店"
        p2.hp = 20
        p2.max_hp = 20
        state.add_player(p2)
        state.m9_system.set_sp("p1", 2)
        state.m9_system.set_sp("p2", 2)
        ctrl = SimpleNamespace(personality=personality,
                               _player=p, _game_state=state)
        return ctrl, state, p

    # ── T0 发动 ──

    def test_activate_basic(self) -> None:
        self.assertTrue(should_activate_t0("T1", 2, "balanced", 20, 20))
        self.assertFalse(should_activate_t0("T1", 0, "balanced", 20, 20))
        self.assertFalse(should_activate_t0("T1", 1, "defensive", 20, 20))
        self.assertTrue(should_activate_t0("T1", 1, "aggressive", 20, 20))

    def test_activate_g0_hp_gate(self) -> None:
        self.assertFalse(should_activate_t0("G0", 2, "aggressive", 9, 20))
        self.assertTrue(should_activate_t0("G0", 2, "aggressive", 10, 20))

    def test_t4_gate_opens_whenever_sp_available(self) -> None:
        ctrl, state, p = self._ctrl(personality="balanced")
        p.talent = SimpleNamespace(slot_id="T4")
        # 裁决 A：即演不消费回合（纯额外资源），SP≥1 即发动，与地点/敌人无关
        self.assertTrue(should_activate_t0(
            "T4", 1, "balanced", 20, 20, state=state, player=p))
        self.assertFalse(should_activate_t0(
            "T4", 0, "balanced", 20, 20, state=state, player=p))

    def test_t4_hexagram_performance_prefers_improvise(self) -> None:
        ctrl, state, p = self._ctrl(personality="balanced")
        state.current_round = 3
        # 无天机/非持有者 → 即演（不消费回合）
        out = m9_decide_choose(
            ctrl, "六爻演出：", ["即演", "公演"], {}, state)
        self.assertEqual(out, "即演")
        # 持有公演位但无天机 → 仍即演（登台公演已确认对 T4 净负，R52 回退）
        state.m9_system._public_holder_by_round[3] = p.player_id
        out = m9_decide_choose(
            ctrl, "六爻演出：", ["即演", "公演"], {}, state)
        self.assertEqual(out, "即演")
        # 持有公演位且阴阳诗天机可用 → 公演（可指定卦象）
        p.talent = SimpleNamespace(
            slot_id="T4", m9_poem_markers={"yin_yang_tianji": 1})
        out = m9_decide_choose(
            ctrl, "六爻演出：", ["即演", "公演"], {}, state)
        self.assertEqual(out, "公演")

    def test_g6_gate_opens_only_for_borrowable_attack_core(self) -> None:
        from unittest import mock
        from engine.m9.talents import g6 as g6mod

        class _FakeMech:
            def __init__(self, pool):
                pass

            def borrowable_core_keys(self, state):
                return ["t1_one_slash", "g3_reality_marble"]

            def precheck_borrow(self, player, key, state):
                return key == "t1_one_slash"

        ctrl, state, p = self._ctrl(personality="balanced")
        p.talent = SimpleNamespace(slot_id="G6", joy_extend=False)
        state.current_round = 3
        # 无公演位 → 不发动（即演=普通行动+SP税，不重演）
        self.assertFalse(should_activate_t0(
            "G6", 2, "balanced", 20, 20, state=state, player=p))
        # 有公演位 + 可借用攻击核心预检通过 → 发动
        state.m9_system._public_holder_by_round[3] = p.player_id
        with mock.patch.object(g6mod, "G6Mechanics", _FakeMech):
            self.assertTrue(should_activate_t0(
                "G6", 2, "balanced", 20, 20, state=state, player=p))

    def test_g6_performance_choices_prefer_borrow_path(self) -> None:
        from unittest import mock
        from engine.m9.talents import g6 as g6mod

        class _FakeMech:
            def __init__(self, pool):
                pass

            def precheck_borrow(self, player, key, state):
                return key == "t2_scissor_rush"

            def borrowable_core_keys(self, state):
                return ["t2_scissor_rush"]

        ctrl, state, p = self._ctrl(personality="balanced")
        # 无公演位 → 即演（审计修复：此前恒公演，即演路径死路）
        out = m9_decide_choose(
            ctrl, "即演重演或公演？", ["即演", "公演"], {}, state)
        self.assertEqual(out, "即演")
        # 无核心可借 → 公演路径走召唤援助
        out = m9_decide_choose(
            ctrl, "公演路径：", ["借用核心", "召唤援助"], {}, state)
        self.assertEqual(out, "召唤援助")
        state.current_round = 3
        state.m9_system._public_holder_by_round[3] = p.player_id
        with mock.patch.object(g6mod, "G6Mechanics", _FakeMech):
            out = m9_decide_choose(
                ctrl, "即演重演或公演？", ["即演", "公演"], {}, state)
            self.assertEqual(out, "公演")
            out = m9_decide_choose(
                ctrl, "公演路径：", ["借用核心", "召唤援助"], {}, state)
            self.assertEqual(out, "借用核心")
            out = m9_decide_choose(
                ctrl, "选择借用核心",
                ["t1_one_slash", "t2_scissor_rush"], {}, state)
            self.assertEqual(out, "t2_scissor_rush")

    def test_g3_t0_gate_only_for_barrier_actions_or_expand(self) -> None:
        ctrl, state, p = self._ctrl(personality="balanced")
        p.talent = SimpleNamespace(slot_id="G3", barrier_active=False)
        # 无结界 + SP=1 → 发动走投影魔术（投影只耗魔力不耗 SP）
        self.assertTrue(should_activate_t0(
            "G3", 1, "balanced", 20, 20, state=state, player=p))
        # 无结界 + SP≥2（即使无同地点敌人/无公演位）→ 发动走投影魔术
        # （审计：旧门把投影停摆，SP≥2 无位时 T0 被“展开”锁死）
        self.assertTrue(should_activate_t0(
            "G3", 2, "balanced", 20, 20, state=state, player=p))
        # 同地点有敌 + 持公演位 → 发动（展开无限剑制）
        state.get_player("p2").location = p.location
        state.current_round = 3
        state.m9_system._public_holder_by_round[3] = p.player_id
        self.assertTrue(should_activate_t0(
            "G3", 2, "balanced", 20, 20, state=state, player=p))
        # 结界内 → 总是发动（螺旋剑/剑阵/崩坏战斗套件）
        p.talent.barrier_active = True
        self.assertTrue(should_activate_t0(
            "G3", 1, "balanced", 20, 20, state=state, player=p))

    def test_g3_inside_action_spiral_when_captured_else_break(self) -> None:
        ctrl, state, p = self._ctrl(personality="balanced")
        p.talent = SimpleNamespace(slot_id="G3")
        p.talent._captured_alive = lambda: ["p2"]
        out = m9_decide_choose(
            ctrl, "选择结界内行动",
            ["螺旋剑连发", "剑阵", "投影创建", "破界"], {}, state)
        self.assertEqual(out, "螺旋剑连发")
        p.talent._captured_alive = lambda: []
        out = m9_decide_choose(
            ctrl, "选择结界内行动",
            ["螺旋剑连发", "剑阵", "投影创建", "破界"], {}, state)
        self.assertEqual(out, "破界")

    def test_g3_collapse_only_when_lethal(self) -> None:
        ctrl, state, p = self._ctrl(personality="balanced")
        target = state.get_player("p2")
        target.hp = 20
        p.talent = SimpleNamespace(
            slot_id="G3", main_target="p2", armament_pool=[])
        p.talent.ideal_styles = {"a", "b", "c"}  # 理想燃烧：结构伤害 5+2×3
        p.talent._captured_alive = lambda: ["p2"]
        p.talent._collapse_legal = lambda: True
        out = m9_decide_choose(
            ctrl, "选择结界内行动",
            ["螺旋剑连发", "剑阵", "投影创建", "破界", "幻想崩坏"], {}, state)
        self.assertEqual(out, "螺旋剑连发")  # 打不死 → 不崩坏
        target.hp = 6  # 结构伤害 ≥ 承伤 → 可终结
        out = m9_decide_choose(
            ctrl, "选择结界内行动",
            ["螺旋剑连发", "剑阵", "投影创建", "破界", "幻想崩坏"], {}, state)
        self.assertEqual(out, "幻想崩坏")

    def test_g3_chain_stop_when_upkeep_not_covered(self) -> None:
        ctrl, state, p = self._ctrl(personality="balanced")
        p.talent = SimpleNamespace(slot_id="G3", magic=6, temp_magic=4)
        p.talent._upkeep_cost = lambda: 2
        p.talent.chain = SimpleNamespace(
            cumulative_magic=2, next_segment_cost=lambda: 4)
        out = m9_decide_choose(
            ctrl, "是否继续连发？", ["继续连发", "停止"], {}, state)
        self.assertEqual(out, "继续连发")  # 6+4−2−4=4 ≥ 2
        p.talent.magic = 2
        p.talent.temp_magic = 0
        p.talent.chain = SimpleNamespace(
            cumulative_magic=0, next_segment_cost=lambda: 2)
        out = m9_decide_choose(
            ctrl, "是否继续连发？", ["继续连发", "停止"], {}, state)
        self.assertEqual(out, "停止")  # 2−0−2=0 < 2

    def test_g3_terminal_collapse_and_outside_mode(self) -> None:
        ctrl, state, p = self._ctrl(personality="balanced")
        target = state.get_player("p2")
        p.talent = SimpleNamespace(slot_id="G3", main_target="p2")
        p.talent.ideal_styles = {"a", "b", "c"}  # 结构伤害 5+2×3=11
        target.hp = 20
        out = m9_decide_choose(
            ctrl, "是否结算终段幻想崩坏？", ["是", "否"], {}, state)
        self.assertEqual(out, "否")
        target.hp = 6
        out = m9_decide_choose(
            ctrl, "是否结算终段幻想崩坏？", ["是", "否"], {}, state)
        self.assertEqual(out, "是")
        # 结界外：持位且有同点目标才展开；否则投影魔术
        target.location = p.location
        state.current_round = 3
        state.m9_system._public_holder_by_round[3] = p.player_id
        out = m9_decide_choose(
            ctrl, "选择结界外行动",
            ["展开固有结界", "投影魔术"], {}, state)
        self.assertEqual(out, "展开固有结界")
        state.m9_system._public_holder_by_round[3] = "p2"
        out = m9_decide_choose(
            ctrl, "选择结界外行动",
            ["展开固有结界", "投影魔术"], {}, state)
        self.assertEqual(out, "投影魔术")

    def test_t2_performance_mode_public_only_for_distant_target(self) -> None:
        ctrl, state, p = self._ctrl(personality="balanced")
        target = state.get_player("p2")
        target.location = p.location  # 同地点 → 即演（1 SP 足够）
        p.talent = SimpleNamespace(slot_id="T2")
        p.talent._core_targets = lambda pl: [target]
        out = m9_decide_choose(
            ctrl, "选择剪刀手一突演出方式：",
            ["公演（2 SP）", "即演（1 SP）"], {}, state)
        self.assertEqual(out, "即演（1 SP）")
        target.location = "医院"  # 异地点 → 公演（附赠追猎位移）
        out = m9_decide_choose(
            ctrl, "选择剪刀手一突演出方式：",
            ["公演（2 SP）", "即演（1 SP）"], {}, state)
        self.assertEqual(out, "公演（2 SP）")

    def test_t7_mount_mode_and_self_target(self) -> None:
        ctrl, state, p = self._ctrl(personality="balanced")
        p.talent = SimpleNamespace(slot_id="T7")
        out = m9_decide_choose(
            ctrl, "挂载方式：", ["即演（1 SP）", "公演（2 SP）"], {}, state)
        self.assertEqual(out, "即演（1 SP）")
        # 挂载目标=自己：保险兑现=目标死亡后复活，挂别人是送对手第二条命
        out = m9_decide_choose(
            ctrl, "选择挂载「死者苏生」的目标：", ["AI1", "AI2"],
            {"situation": "resurrection_pick_target"}, state)
        self.assertEqual(out, "AI1")

    def test_g1_secondary_can_open_free_unload_menu(self) -> None:
        _, state, p = self._ctrl(personality="defensive")
        p.talent = SimpleNamespace(slot_id="G1", form="secondary", entropy=5)
        self.assertTrue(should_activate_t0(
            "G1", 0, "defensive", 20, 20, state=state, player=p))

    def test_g1_secondary_holds_form_until_burn_or_unload_window(self) -> None:
        _, state, p = self._ctrl(personality="aggressive")
        p.talent = SimpleNamespace(slot_id="G1", form="secondary", entropy=2)
        state.current_round = 4
        self.assertFalse(should_activate_t0(
            "G1", 2, "aggressive", 20, 20, state=state, player=p))
        state.m9_system._public_holder_by_round[4] = p.player_id
        self.assertTrue(should_activate_t0(
            "G1", 2, "aggressive", 20, 20, state=state, player=p))

    def test_g1_armorless_dresses_without_outer_armor(self) -> None:
        """AI 逻辑修正：secondary 攻防修正全面优于 armorless，无外甲也允许着装。"""
        _, state, p = self._ctrl(personality="aggressive")
        p.talent = SimpleNamespace(slot_id="G1", form="armorless", entropy=0)
        self.assertTrue(should_activate_t0(
            "G1", 1, "aggressive", 20, 20, state=state, player=p))
        from models.equipment import make_armor
        p.add_armor(make_armor("盾牌"))
        self.assertTrue(should_activate_t0(
            "G1", 1, "aggressive", 20, 20, state=state, player=p))

    def test_g1_declaration_unloads_without_public_combat_window(self) -> None:
        from controllers.ai.decision.c_policy import c_decide_choose

        ctrl, state, p = self._ctrl(personality="aggressive")
        p.talent = SimpleNamespace(slot_id="G1", form="secondary", entropy=3)
        out = c_decide_choose(
            ctrl, "火萤宣言：",
            ["完全燃烧（公演 2 SP）", "卸甲宣言（免费）"], None, state)
        self.assertEqual(out, "卸甲宣言（免费）")

    def test_g1_declaration_uses_public_burn_in_local_combat(self) -> None:
        from controllers.ai.decision.c_policy import c_decide_choose

        ctrl, state, p = self._ctrl(personality="aggressive")
        # 真实轮次：第一次次级燃烧 R4 后是 entropy=2，而不是人工态 1。
        p.talent = SimpleNamespace(slot_id="G1", form="secondary", entropy=2)
        state.get_player("p2").location = p.location
        state.current_round = 4
        state.m9_system._public_holder_by_round[4] = p.player_id
        out = c_decide_choose(
            ctrl, "火萤宣言：",
            ["完全燃烧（公演 2 SP）", "卸甲宣言（免费）"], None, state)
        self.assertEqual(out, "完全燃烧（公演 2 SP）")

    def test_activate_g2_terminal_guard(self) -> None:
        ctrl, state, p = self._ctrl()
        p.talent = SimpleNamespace(
            slot_id="G2",
            _shadow=lambda: SimpleNamespace(is_terminal_singer=False))
        state.current_round = 10
        state.max_rounds = 50
        # 前期不交终曲承诺
        self.assertFalse(should_activate_t0(
            "G2", 2, "aggressive", 20, 20, state=state, player=p))
        state.current_round = 40
        # 中后期也必须先拿到本轮公演位。
        self.assertFalse(should_activate_t0(
            "G2", 2, "aggressive", 20, 20, state=state, player=p))
        state.m9_system._public_holder_by_round[40] = p.player_id
        self.assertTrue(should_activate_t0(
            "G2", 2, "aggressive", 20, 20, state=state, player=p))

    def test_activate_t7_insurance_guard(self) -> None:
        ctrl, state, p = self._ctrl()
        p.talent = SimpleNamespace(slot_id="T7")
        # R1 先观察一轮；R2 起必须在目标死亡前挂载。
        state.current_round = 1
        self.assertFalse(should_activate_t0(
            "T7", 2, "aggressive", 20, 20, state=state, player=p))
        state.current_round = 2
        # 登台激励：SP<2 时低风险窗口先蓄势等 SP2（R5 前），不再立即即演。
        self.assertFalse(should_activate_t0(
            "T7", 1, "defensive", 20, 20, state=state, player=p))
        state.current_round = 5
        self.assertTrue(should_activate_t0(
            "T7", 1, "defensive", 20, 20, state=state, player=p))
        # R1 已残血则不等待。
        state.current_round = 1
        self.assertTrue(should_activate_t0(
            "T7", 2, "aggressive", 8, 20, state=state, player=p))

    # ── R0 公演报名 ──

    def test_register_public(self) -> None:
        self.assertTrue(should_register_public("T3", 2, "balanced"))
        self.assertTrue(should_register_public("G3", 2, "defensive"))
        self.assertTrue(should_register_public("G4", 2, "balanced"))
        # arc RFC v0.1 登台激励：未点亮第一章的低频公演槽位也报名一次
        self.assertTrue(should_register_public("G4", 2, "defensive"))
        self.assertTrue(should_register_public("T1", 2, "balanced"))
        self.assertTrue(should_register_public("T1", 2, "aggressive"))
        self.assertFalse(should_register_public("T3", 1, "aggressive"))
        self.assertTrue(should_register_public("G7", 2, "balanced"))

    def test_g0_public_requires_drone_and_real_payoff(self) -> None:
        _, state, p = self._ctrl(personality="builder")
        p.talent = SimpleNamespace(slot_id="G0", drone=None, relics=[])
        self.assertFalse(should_register_public(
            "G0", 2, "builder", state=state, player=p))

        p.talent.drone = {"hp": 5}
        enemy = state.get_player("p2")
        enemy.location = p.location
        enemy.hp = 1  # R18：crossfire_damage 2→1，炮火击杀线同步下调
        self.assertTrue(should_register_public(
            "G0", 2, "builder", state=state, player=p))

        state.current_round = 2
        state.m9_system._public_holder_by_round[2] = p.player_id
        self.assertTrue(should_activate_t0(
            "G0", 2, "builder", p.hp, p.max_hp,
            state=state, player=p))
        state.m9_system._public_holder_by_round.clear()
        self.assertFalse(should_activate_t0(
            "G0", 2, "builder", p.hp, p.max_hp,
            state=state, player=p))

    def test_g3_registers_only_with_capture_target(self) -> None:
        _, state, p = self._ctrl(personality="defensive")
        p.talent = SimpleNamespace(slot_id="G3")
        self.assertFalse(should_register_public(
            "G3", 2, "defensive", state=state, player=p))
        state.get_player("p2").location = p.location
        self.assertTrue(should_register_public(
            "G3", 2, "defensive", state=state, player=p))

    def test_g1_secondary_registers_public_across_personalities(self) -> None:
        _, state, p = self._ctrl(personality="defensive")
        p.talent = SimpleNamespace(slot_id="G1", form="secondary", entropy=2)
        self.assertTrue(should_register_public(
            "G1", 2, "defensive", state=state, player=p))
        p.talent.entropy = 4
        # 满燃门已放宽到 entropy≤4（旧 ≤2 把完全燃烧锁死）。
        self.assertTrue(should_register_public(
            "G1", 2, "defensive", state=state, player=p))

    # ── 石化挣脱 ──

    def test_breakout(self) -> None:
        self.assertFalse(should_attempt_breakout(0, "aggressive", True))
        self.assertTrue(should_attempt_breakout(1, "aggressive", False))
        self.assertFalse(should_attempt_breakout(1, "defensive", False))
        self.assertTrue(should_attempt_breakout(1, "defensive", True))
        self.assertTrue(should_continue_breakout(1))
        self.assertFalse(should_continue_breakout(0))

    # ── 焚诏拉条 ──

    def test_burn_challenge(self) -> None:
        self.assertTrue(should_accept_burn_challenge("aggressive", 20, 20))
        self.assertFalse(should_accept_burn_challenge("defensive", 20, 20))
        self.assertTrue(should_accept_burn_challenge("balanced", 15, 20))
        self.assertFalse(should_accept_burn_challenge("balanced", 8, 20))

    # ── m9_decide_choose 集成 ──

    def _t0_context(self):
        return {"phase": "T0", "situation": "talent_t0",
                "talent_name": "X", "talent_desc": ""}

    def test_decide_t0_activate(self) -> None:
        ctrl, state, p = self._ctrl()
        p.talent = SimpleNamespace(slot_id="T1")
        from models.equipment import make_weapon
        p.weapons.append(make_weapon("小刀"))
        p.weapons[0].base_damage = 7  # hp20 磨刀小刀：T1 武器就绪
        out = m9_decide_choose(
            ctrl, "是否在本回合开始时发动天赋？",
            ["发动天赋", "不发动，正常行动"], self._t0_context(), state)
        self.assertEqual(out, "发动天赋")

    def test_decide_t0_hold_last_sp(self) -> None:
        ctrl, state, p = self._ctrl(personality="defensive")
        p.talent = SimpleNamespace(slot_id="T1")
        state.m9_system.set_sp("p1", 1)
        out = m9_decide_choose(
            ctrl, "是否在本回合开始时发动天赋？",
            ["发动天赋", "不发动，正常行动"], self._t0_context(), state)
        self.assertEqual(out, "不发动，正常行动")

    def test_g0_first_summon_waits_for_sp2_chain(self) -> None:
        """G0 召唤门：SP=1 无脑召唤会让无人机 3 tick 内过期白烧 HP；
        必须 SP≥2 且血线允许（召唤后留 1 SP，次轮回 2 接炮火链）。"""
        ctrl, state, p = self._ctrl(personality="builder")
        p.talent = SimpleNamespace(slot_id="G0", drone=None)
        state.m9_system.set_sp("p1", 1)
        out = m9_decide_choose(
            ctrl, "是否在本回合开始时发动天赋？",
            ["发动天赋", "不发动，正常行动"], self._t0_context(), state)
        self.assertEqual(out, "不发动，正常行动")
        state.m9_system.set_sp("p1", 2)
        out = m9_decide_choose(
            ctrl, "是否在本回合开始时发动天赋？",
            ["发动天赋", "不发动，正常行动"], self._t0_context(), state)
        self.assertEqual(out, "发动天赋")

    def test_g7_does_not_click_public_without_seat(self) -> None:
        _, state, p = self._ctrl(personality="balanced")
        p.talent = SimpleNamespace(slot_id="G7", tactical_unlocked=True)
        state.current_round = 3
        self.assertFalse(should_activate_t0(
            "G7", 2, "balanced", 20, 20, state=state, player=p))
        state.m9_system._public_holder_by_round[3] = p.player_id
        self.assertTrue(should_activate_t0(
            "G7", 2, "balanced", 20, 20, state=state, player=p))
        # 审计：战术未解锁时补给是死库存，不点、也不报名。
        p.talent.tactical_unlocked = False
        self.assertFalse(should_activate_t0(
            "G7", 2, "balanced", 20, 20, state=state, player=p))
        self.assertFalse(should_register_public(
            "G7", 2, "balanced", state=state, player=p))

    def test_decide_register_public(self) -> None:
        ctrl, state, p = self._ctrl()
        p.talent = SimpleNamespace(slot_id="T3")
        out = m9_decide_choose(
            ctrl, "SP 已满：保留即演权限或报名公演？", ["保留", "报名公演"],
            {"phase": "M9_PUBLIC_REGISTRATION"}, state)
        self.assertEqual(out, "报名公演")
        p.talent = SimpleNamespace(slot_id="T1")
        out = m9_decide_choose(
            ctrl, "SP 已满：保留即演权限或报名公演？", ["保留", "报名公演"],
            {"phase": "M9_PUBLIC_REGISTRATION"}, state)
        self.assertEqual(out, "报名公演")  # 登台激励：未登台者报名一次

    def test_decide_petrified(self) -> None:
        ctrl, state, p = self._ctrl()
        p.talent = SimpleNamespace(slot_id="T1")
        out = m9_decide_choose(
            ctrl, "选择处理方式：",
            ["保持石化（跳过本槽，不获SP）", "尝试挣脱（1 SP/次，50%）"],
            {"phase": "T0", "situation": "petrified"}, state)
        self.assertIn("尝试挣脱", out)
        state.m9_system.set_sp("p1", 0)
        out = m9_decide_choose(
            ctrl, "选择处理方式：",
            ["保持石化（跳过本槽，不获SP）", "尝试挣脱（1 SP/次，50%）"],
            {"phase": "T0", "situation": "petrified"}, state)
        self.assertIn("保持石化", out)

    def test_decide_breakout_retry(self) -> None:
        ctrl, state, p = self._ctrl()
        out = m9_decide_choose(
            ctrl, "是否再尝试一次？", ["继续尝试", "放弃（本槽收尾）"],
            None, state)
        self.assertEqual(out, "继续尝试")
        state.m9_system.set_sp("p1", 0)
        out = m9_decide_choose(
            ctrl, "是否再尝试一次？", ["继续尝试", "放弃（本槽收尾）"],
            None, state)
        self.assertEqual(out, "放弃（本槽收尾）")

    def test_decide_burn_challenge(self) -> None:
        ctrl, state, p = self._ctrl(personality="aggressive")
        p.talent = SimpleNamespace(slot_id="T1")
        out = m9_decide_choose(
            ctrl, "焚诏拉条：AI1 选择攻击或拒战？", ["攻击", "拒战"],
            None, state)
        self.assertEqual(out, "攻击")
        ctrl.personality = "defensive"
        out = m9_decide_choose(
            ctrl, "焚诏拉条：AI1 选择攻击或拒战？", ["攻击", "拒战"],
            None, state)
        self.assertEqual(out, "拒战")

    def test_decide_passthrough(self) -> None:
        """非 M9 决策面放行（返回 None，旧层接管）。"""
        ctrl, state, p = self._ctrl()
        p.talent = SimpleNamespace(slot_id="T4")
        out = m9_decide_choose(
            ctrl, "指定卦象：", ["乾", "坤"], {"situation": "hexagram_my_choice"},
            state)
        self.assertIsNone(out)

    def test_controller_choose_wiring(self) -> None:
        """BasicAIController.choose 在 M9 下路由到通用决策面。"""
        from controllers.ai.controller import create_ai_controller
        state = GameState()
        p = Player("p1", "AI1", controller=create_ai_controller())
        p.is_awake = True
        p.location = "公园"
        p.hp = 20
        p.max_hp = 20
        p.talent = SimpleNamespace(slot_id="T3")
        state.add_player(p)
        state.m9_system.set_sp("p1", 2)
        # T3 只有公演入口：新门要求持有本轮公演位才发动（审计：46% 空转）。
        state.m9_system._public_holder_by_round[state.current_round] = "p1"
        ctrl = p.controller
        ctrl._player = p
        ctrl._game_state = state
        ctrl.personality = "balanced"
        out = ctrl.choose(
            "是否在本回合开始时发动天赋？",
            ["发动天赋", "不发动，正常行动"],
            {"phase": "T0", "situation": "talent_t0",
             "talent_name": "X", "talent_desc": ""})
        self.assertEqual(out, "发动天赋")
        out = ctrl.choose(
            "SP 已满：保留即演权限或报名公演？", ["保留", "报名公演"],
            {"phase": "M9_PUBLIC_REGISTRATION"})
        self.assertEqual(out, "报名公演")

    def test_controller_first_t0_uses_context_before_orchestrator_binding(self) -> None:
        """首次 T0 早于 generate_command；不能依赖 _player/_game_state 已绑定。"""
        from controllers.ai.controller import create_ai_controller
        state = GameState()
        ctrl = create_ai_controller(personality="aggressive")
        p = Player("p1", "AI1", controller=ctrl)
        p.is_awake = True
        p.location = "公园"
        p.hp = 20
        p.max_hp = 20
        p.talent = SimpleNamespace(slot_id="G1", form="armorless", entropy=0)
        state.add_player(p)
        state.m9_system.set_sp("p1", 1)
        self.assertIsNone(getattr(ctrl, "_player", None))
        out = ctrl.choose(
            "是否在本回合开始时发动天赋？",
            ["发动天赋", "不发动，正常行动"],
            {"phase": "T0", "situation": "talent_t0",
             "talent_name": "着装宣言：次级燃烧", "m9_kind": "g1_dress",
             "player": p, "game_state": state})
        self.assertEqual(out, "发动天赋")

    def test_controller_decision_stats(self) -> None:
        """B3：决策计数器随 choose 决策累加（风洞采集源）。"""
        from controllers.ai.controller import create_ai_controller
        state = GameState()
        p = Player("p1", "AI1", controller=create_ai_controller())
        p.is_awake = True
        p.location = "公园"
        p.hp = 20
        p.max_hp = 20
        p.talent = SimpleNamespace(slot_id="T1")
        from models.equipment import make_weapon
        p.weapons.append(make_weapon("小刀"))
        p.weapons[0].base_damage = 7  # hp20 磨刀小刀：T1 武器就绪
        state.add_player(p)
        state.m9_system.set_sp("p1", 2)
        ctrl = p.controller
        ctrl._player = p
        ctrl._game_state = state
        ctrl.personality = "aggressive"
        ctrl.choose("是否在本回合开始时发动天赋？",
                    ["发动天赋", "不发动，正常行动"],
                    {"phase": "T0", "situation": "talent_t0",
                     "talent_name": "X", "talent_desc": ""})
        ctrl.choose("选择处理方式：",
                    ["保持石化（跳过本槽，不获SP）", "尝试挣脱（1 SP/次，50%）"],
                    {"phase": "T0", "situation": "petrified"})
        ctrl.choose("是否再尝试一次？", ["继续尝试", "放弃（本槽收尾）"], None)
        stats = ctrl._decision_stats
        self.assertEqual(stats["t0_activated"], 1)
        self.assertEqual(stats["breakout_attempts"], 2)

    def test_controller_decision_stats_does_not_count_decline(self) -> None:
        from controllers.ai.controller import create_ai_controller
        state = GameState()
        p = Player("p1", "AI1", controller=create_ai_controller())
        p.is_awake = True
        p.location = "公园"
        p.hp = 9
        p.max_hp = 20
        p.talent = SimpleNamespace(slot_id="G0")
        state.add_player(p)
        state.m9_system.set_sp("p1", 2)
        ctrl = p.controller
        ctrl._player = p
        ctrl._game_state = state
        ctrl.personality = "aggressive"
        out = ctrl.choose(
            "是否在本回合开始时发动天赋？",
            ["发动天赋", "不发动，正常行动"],
            {"phase": "T0", "situation": "talent_t0",
             "talent_name": "G0", "talent_desc": "", "player": p,
             "game_state": state})
        self.assertEqual(out, "不发动，正常行动")
        self.assertEqual(ctrl._decision_stats["t0_activated"], 0)

    def test_summarize_process_metrics(self) -> None:
        """B3：stats_runner 聚合纯函数（每局均值 + 每轮速率）。"""
        import stats_runner as sr
        samples = [
            ({"t0_activated": 2, "public_registered": 1,
              "breakout_attempts": 0, "barrier_break_offered": 1}, 20),
            ({"t0_activated": 0, "public_registered": 0,
              "breakout_attempts": 2, "barrier_break_offered": 0}, 10),
        ]
        summ = sr._summarize_process_metrics(samples)
        self.assertEqual(summ["t0_activated_per_game"], 1.0)
        self.assertEqual(summ["breakout_attempts_per_game"], 1.0)
        self.assertEqual(summ["t0_activated_per_round"], 2 / 30)
        self.assertEqual(summ["public_registered_per_round"], 1 / 30)


if __name__ == "__main__":
    unittest.main()
