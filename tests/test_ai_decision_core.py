"""BasicAI 决策内核测试（ActionCatalog / ActionSpec / DecisionSnapshot）。

覆盖目标（goal 步骤①/④）：
- ActionCatalog 构建（v2exp 与 m9-rfc 双 profile；无参类型；M9 special 全覆盖）；
- ActionSpec→命令字符串 adapter（to_command / specify / validate）；
- DecisionSnapshot 投影（slot_id / SP / 装备 / M9 世界事实）。
"""
import unittest

from controllers.base import PlayerController

from engine import experiments
from engine.game_state import GameState
from models.player import Player

from controllers.ai.decision.action_catalog import ActionCatalog
from controllers.ai.decision.snapshot import DecisionSnapshot


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


class NeedProviderProfileTest(unittest.TestCase):

    def tearDown(self) -> None:
        experiments.reset()

    def test_m4_retires_home_voucher_provider_only(self) -> None:
        from controllers.ai.constants import need_providers_for
        _enable("hp20")
        self.assertIn(("home", "凭证", "free"), need_providers_for("voucher"))
        experiments.enable("m4_gear")
        self.assertNotIn(("home", "凭证", "free"), need_providers_for("voucher"))
        self.assertIn(("商店", "打工", "free"), need_providers_for("voucher"))


def _world(profile="m9-rfc"):
    if profile == "m9-rfc":
        _enable("m9_rfc", "hp20")
    else:
        _enable("hp20")
    state = GameState()
    p = Player("p1", "玩家1", controller=_C())
    p.is_awake = True
    p.location = "商店"
    p.hp = 20
    p.max_hp = 20
    state.add_player(p)
    p2 = Player("p2", "玩家2", controller=_C())
    p2.is_awake = True
    p2.location = "商店"
    p2.hp = 20
    p2.max_hp = 20
    state.add_player(p2)
    return state, p


class ActionCatalogTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_build_m9_includes_specials(self) -> None:
        state, p = _world("m9-rfc")
        cat = ActionCatalog.build(p, state,
                                  ["wake", "move", "attack", "special", "forfeit"])
        self.assertEqual(cat.profile, "m9-rfc")
        raws = set(cat.raws())
        self.assertIn("forfeit", raws)
        self.assertTrue(any(r.startswith("move ") for r in raws))
        # M9 special 动态列表全覆盖（白名单/竞选队长/交易至少其一）
        self.assertTrue(
            any(("竞选队长" in r or "热线举报" in r or "交易" in r or "PP" in r)
                for r in raws))

    def test_build_v2exp_profile(self) -> None:
        state, p = _world("v2exp")
        cat = ActionCatalog.build(p, state, ["wake", "move", "forfeit"])
        self.assertEqual(cat.profile, "v2exp")

    def test_specify_and_validate(self) -> None:
        state, p = _world("m9-rfc")
        cat = ActionCatalog.build(p, state, ["wake", "forfeit"])
        sc = cat.specify("forfeit", score=1.0, reason="fallback")
        self.assertIsNotNone(sc)
        self.assertEqual(sc.spec.action_type, "forfeit")
        self.assertEqual(sc.score, 1.0)
        self.assertTrue(cat.validate("forfeit"))
        self.assertFalse(cat.validate("attack nobody"))
        self.assertEqual(cat.to_command(sc.spec), "forfeit")  # adapter 往返

    def test_specs_carry_grant_context(self) -> None:
        state, p = _world("m9-rfc")
        grant = type("G", (), {"grant_id": "g9", "allow_instant": True,
                               "allow_public": False})()
        cat = ActionCatalog.build(p, state, ["forfeit"], grant=grant)
        spec = cat.get("forfeit")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.grant_id, "g9")
        self.assertTrue(spec.params.get("allow_instant"))
        self.assertFalse(spec.params.get("allow_public"))

    def test_m9_political_uses_dynamic_captain_special(self) -> None:
        from types import SimpleNamespace
        from controllers.ai.command_builder.police_commands import (
            PoliceCommandBuilder)
        from controllers.ai.game_query import GameQuery
        from controllers.ai.strategies.political_strategy import (
            PoliticalStrategy)

        state, p = _world("m9-rfc")
        builder = PoliceCommandBuilder(GameQuery())
        ctx = SimpleNamespace(political_fallback_level="none")
        cmds = builder.build_police_political(
            p, state, PoliticalStrategy(), ["special"], ctx)
        self.assertEqual(cmds, ["special 竞选队长"])

        state.m9_police.set_state_ref(state)
        self.assertTrue(state.m9_police.apply_captain("p2"))
        state.m9_police.r2_tick(state, 2)
        self.assertEqual(
            GameQuery.political_should_fallback(p, state), "full_balanced")
        self.assertEqual(
            PoliticalStrategy().get_police_stance(p, state), "ignore")


class DecisionSnapshotTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_m9_snapshot_projection(self) -> None:
        from engine.m9.talents.g3 import Mythland9
        state, p = _world("m9-rfc")
        p.talent = Mythland9("p1", state)
        snap = DecisionSnapshot.build(p, state)
        self.assertEqual(snap.actor_id, "p1")
        self.assertEqual(snap.profile, "m9-rfc")
        self.assertEqual(snap.slot_id, "G3")
        self.assertEqual(snap.location, "商店")
        self.assertIsNotNone(snap.m9)
        self.assertEqual(snap.m9.sp, 1)
        self.assertEqual(snap.m9.pp, 0)
        self.assertFalse(snap.m9.barrier_active)  # 未展开

    def test_v2exp_snapshot_has_no_m9(self) -> None:
        state, p = _world("v2exp")
        snap = DecisionSnapshot.build(p, state)
        self.assertEqual(snap.profile, "v2exp")
        self.assertIsNone(snap.m9)


class SlotHookDispatchTest(unittest.TestCase):
    """步骤③：天赋 hook 按 (profile, slot_id) 分派，显示名回退。"""

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def _controller(self):
        from controllers.ai.controller import create_ai_controller
        return create_ai_controller()

    def test_m9_slot_dispatch_g1_g2_g5_g6(self) -> None:
        from engine.m9.talents.g1 import G1MythFire9
        from engine.m9.talents.g2 import Hologram9
        from engine.m9.talents.g5 import Ripple9
        from engine.m9.talents.g6 import CutawayJoke9
        from controllers.ai.m9_adapters import resolve_talent_hook, profile_of
        ctrl = self._controller()
        state = GameState()
        ctrl._game_state = state  # profile 判定用
        for cls in (G1MythFire9, Hologram9, Ripple9, CutawayJoke9):
            p = Player("p1", "玩家1", controller=_C())
            p.talent = cls("p1", state)
            hook = resolve_talent_hook(ctrl, p)
            self.assertIsNotNone(hook, f"{cls.__name__} 无 M9 slot hook")
            self.assertEqual(hook.slot_id, p.talent.slot_id
                             if hasattr(p.talent, "slot_id") else
                             _slot_id_of(p.talent))

    def test_g1_propagation_adapter_targets_dense_enemy_location(self) -> None:
        from engine.m9.talents.g1 import G1MythFire9
        from controllers.ai.m9_adapters import resolve_talent_hook
        ctrl = self._controller()
        state, p = _world("m9-rfc")
        ctrl._game_state = state
        p.talent = G1MythFire9("p1", state)
        p.talent.form = "propagation"
        state.get_player("p2").location = "公园"
        p3 = Player("p3", "玩家3", controller=_C())
        p3.is_awake = True
        p3.location = "公园"
        p3.hp = 20
        state.add_player(p3)
        hook = resolve_talent_hook(ctrl, p)
        self.assertEqual(
            hook.should_override_candidates(p, state, ["move", "attack", "forfeit"]),
            ["move 公园", "forfeit"])

    def test_g1_armorless_low_hp_adapter_flees_before_attacking(self) -> None:
        from engine.m9.talents.g1 import G1MythFire9
        from controllers.ai.m9_adapters import resolve_talent_hook
        ctrl = self._controller()
        state, p = _world("m9-rfc")
        ctrl._game_state = state
        p.talent = G1MythFire9("p1", state)
        p.talent.form = "armorless"
        p.hp = 3
        hook = resolve_talent_hook(ctrl, p)
        out = hook.should_override_candidates(
            p, state, ["move", "attack", "interact", "forfeit"])
        self.assertEqual(out[0], "move home_p1")

    def test_g1_armorless_adapter_equips_before_aggressive_combat(self) -> None:
        from engine.m9.talents.g1 import G1MythFire9
        from controllers.ai.m9_adapters import resolve_talent_hook
        ctrl = self._controller()
        ctrl.personality = "aggressive"
        state, p = _world("m9-rfc")
        ctrl._game_state = state
        p.location = "home_p1"
        p.talent = G1MythFire9("p1", state)
        hook = resolve_talent_hook(ctrl, p)
        out = hook.should_override_candidates(
            p, state, ["move", "interact", "attack", "forfeit"])
        self.assertEqual(out[0], "interact 盾牌")

    def test_g1_full_burn_prefers_legal_attack_then_lock(self) -> None:
        from engine.m9.talents.g1 import G1MythFire9
        from models.equipment import make_bow
        from controllers.ai.m9_adapters import resolve_talent_hook
        ctrl = self._controller()
        state, p = _world("m9-rfc")
        ctrl._game_state = state
        p.talent = G1MythFire9("p1", state)
        p.talent.form = "full_burn"
        p.weapons = [make_bow()]
        target = state.get_player("p2")
        state.markers.add_relation(target.player_id, "LOCKED_BY", p.player_id)
        hook = resolve_talent_hook(ctrl, p)
        out = hook.should_override_candidates(
            p, state, ["move", "lock", "attack", "forfeit"])
        self.assertTrue(out[0].startswith("attack "))
        state.markers.remove_relation(target.player_id, "LOCKED_BY", p.player_id)
        out = hook.should_override_candidates(
            p, state, ["move", "lock", "attack", "forfeit"])
        self.assertTrue(out[0].startswith("lock "))

    def test_g3_adapter_projects_instead_of_empty_barrier(self) -> None:
        from engine.m9.talents.g3 import Mythland9
        from controllers.ai.m9_adapters import resolve_talent_hook
        ctrl = self._controller()
        state, p = _world("m9-rfc")
        ctrl._game_state = state
        p.talent = Mythland9("p1", state)
        hook = resolve_talent_hook(ctrl, p)
        options = ["展开固有结界", "投影魔术"]
        self.assertEqual(hook.handle_choose(
            p, state, "m9_g3", options), "投影魔术")
        state.get_player("p2").location = p.location
        state.current_round = 1
        state.m9_system.set_sp("p1", 2)
        state.m9_system.register_performance("p1", 1)
        state.m9_system.allocate_public_slot(1)
        self.assertEqual(hook.handle_choose(
            p, state, "m9_g3", options), "展开固有结界")

    def test_v2exp_display_name_fallback(self) -> None:
        from engine.m9.talents.g7 import Hoshino9
        from controllers.ai.m9_adapters import resolve_talent_hook
        ctrl = self._controller()
        state = GameState()
        ctrl._game_state = state
        p = Player("p1", "玩家1", controller=_C())
        p.talent = Hoshino9("p1", state)
        # G7 不在 M9 adapter 名单 → 回退显示名注册的 HoshinoAIHook
        hook = resolve_talent_hook(ctrl, p)
        self.assertIsNotNone(hook)
        self.assertEqual(getattr(hook, "talent_name", ""), "大叔我啊，剪短发了")


def _slot_id_of(talent):
    from controllers.ai.decision.snapshot import _slot_id_for
    return _slot_id_for(talent)


class SnapshotV2Test(unittest.TestCase):
    """DecisionSnapshot v2：环境/身体/意图/窗口/痕迹字段（2026-08-12 设计）。"""

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def _world(self, g2=False):
        from engine.m9.talents.g2 import Hologram9
        state = GameState()
        p = Player("p1", "G1", controller=_C())
        p.is_awake = True
        p.location = "商店"
        p.hp = 20
        p.max_hp = 20
        p.credits = 5
        p.vouchers = 1
        state.add_player(p)
        p2 = Player("p2", "敌人", controller=_C())
        p2.is_awake = True
        p2.location = "商店"
        p2.hp = 20
        p2.max_hp = 20
        state.add_player(p2)
        if g2:
            p.talent = Hologram9("p1", state)
        return state, p, p2

    def test_environment_occupancy_and_wallet(self) -> None:
        state, p, p2 = self._world()
        snap = DecisionSnapshot.build(p, state)
        self.assertEqual(snap.credits, 5)
        self.assertEqual(snap.vouchers, 1)
        shop = snap.location_occupancy.get("商店", ())
        self.assertEqual({u.uid for u in shop}, {"p1", "p2"})
        self.assertIn("player", {u.kind for u in shop})
        self.assertEqual(snap.opponent_briefs["p2"].location, "商店")

    def test_g2_shadow_specialization(self) -> None:
        state, p, p2 = self._world(g2=True)
        state.m9_system.set_sp("p1", 1)
        p.talent._create_shadow(p)
        sh = p.talent._shadow()
        sh.location = "公园"
        snap = DecisionSnapshot.build(p, state)
        self.assertEqual(snap.slot_id, "G2")
        self.assertEqual(snap.m9.terminal_state, "shadow")
        self.assertTrue(snap.m9.shadow.present)
        self.assertEqual(snap.m9.shadow.location, "公园")
        self.assertTrue(snap.m9.shadow_create_eligible)

    def test_intent_signals_from_event_log(self) -> None:
        state, p, p2 = self._world()
        state.log_event("move", player="p2", to="军事基地")
        state.log_event("attack", attacker="p2", target="p1", weapon="弓")
        state.log_event("interact", player="p2", item="办理通行证")
        snap = DecisionSnapshot.build(p, state)
        signals = snap.opponent_intent.get("p2", ())
        self.assertEqual(len(signals), 3)
        self.assertEqual(signals[-1].action_type, "interact")
        self.assertEqual(signals[-1].params.get("item"), "办理通行证")

    def test_trace_entry_structure(self) -> None:
        from controllers.ai.decision.snapshot import TraceEntry, ValueBreakdown
        tr = TraceEntry(raw="attack p2 弓", score=1.0, goal="压敌方血线",
                        source="mind",
                        breakdown=ValueBreakdown(
                            gains=("命中期望 3",), risks=("反击 2",),
                            key_fields={"p2_hp": 8}))
        self.assertEqual(tr.raw, "attack p2 弓")
        self.assertEqual(tr.breakdown.gains[0], "命中期望 3")


class MindsSnapshotTest(unittest.TestCase):
    """minds 快照化：快照优先路径 + 派生层消费（2026-08-12 管线主线）。"""

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def _world(self):
        state = GameState()
        p = Player("p1", "AI1", controller=_C())
        p.is_awake = True
        p.location = "公园"
        p.hp = 20
        p.max_hp = 20
        state.add_player(p)
        p2 = Player("p2", "AI2", controller=_C())
        p2.is_awake = True
        p2.location = "公园"
        p2.hp = 20
        p2.max_hp = 20
        state.add_player(p2)
        p3 = Player("p3", "AI3", controller=_C())
        p3.is_awake = True
        p3.location = "军事基地"
        p3.hp = 20
        p3.max_hp = 20
        state.add_player(p3)
        return state, p, p2, p3

    def test_threat_mind_snapshot_path(self) -> None:
        from controllers.ai.minds.threat_mind import ThreatMind
        from controllers.ai.decision.snapshot import ProjectedSnapshot
        from controllers.ai.strategies.base_strategy import BasePersonalityStrategy
        state, p, p2, p3 = self._world()
        p2.is_captain = True  # 队长威胁修正走快照 brief.is_captain
        snap = ProjectedSnapshot.build(p, state)
        mind = ThreatMind(debug_name="T")
        result = mind.assess(p, state, BasePersonalityStrategy(),
                             snapshot=snap, assessment=None)
        self.assertIn("AI2", result.data["threat_scores"])
        # 队长在威胁中：快照 brief.is_captain 生效（AI2 威胁 > AI3 基础）
        self.assertGreater(
            result.data["threat_scores"].get("AI2", 0),
            result.data["threat_scores"].get("AI3", 0))

    def test_develop_mind_snapshot_location(self) -> None:
        from controllers.ai.minds.develop_mind import DevelopMind
        from controllers.ai.decision.snapshot import ProjectedSnapshot
        from controllers.ai.strategies.base_strategy import BasePersonalityStrategy
        state, p, p2, p3 = self._world()
        snap = ProjectedSnapshot.build(p, state)
        mind = DevelopMind(debug_name="D")
        result = mind.assess(p, state, BasePersonalityStrategy(),
                             snapshot=snap, assessment=None)
        crowding = mind._snapshot_derived(
            snap, type("A", (), {"notes": {}})(), "公园")
        # _score_location 快照路径：公园有 1 敌（p2），军事基地有 1 敌（p3）
        score_park = mind._score_location("公园", p, state, snapshot=snap)
        score_mil = mind._score_location("军事基地", p, state, snapshot=snap)
        self.assertEqual(score_park, 75.0)   # 100 - 1×25
        self.assertEqual(score_mil, 75.0)

    def test_develop_mind_snapshot_excludes_dead(self) -> None:
        """P1 回归：快照路径不把死亡玩家计为拥挤（与 count_enemies_at 等价）。"""
        from controllers.ai.minds.develop_mind import DevelopMind
        from controllers.ai.decision.snapshot import ProjectedSnapshot
        state, p, p2, p3 = self._world()
        p2.hp = 0  # p2 死亡但仍在 player_order
        snap = ProjectedSnapshot.build(p, state)
        mind = DevelopMind(debug_name="D")
        self.assertEqual(mind._score_location("公园", p, state, snapshot=snap),
                         mind._score_location("公园", p, state, snapshot=None))

    def test_m8_ai_weight_channel(self) -> None:
        """B1：m9-rfc 下 m8_ai 生效，estimate_power 读 balance.ai 权重。"""
        from engine import experiments
        experiments.reset()
        experiments.set_profile("m9-rfc")
        self.assertTrue(experiments.is_enabled("m8_ai"))
        from engine.balance import get as _bget
        from controllers.ai.game_query import GameQuery
        state, p, p2, p3 = self._world()
        hp_w = float(_bget("ai", "hp_weight", default=10))
        dmg_w = float(_bget("ai", "damage_weight", default=15))
        expected = p.hp * hp_w
        for w in p.weapons:
            if w:
                expected += GameQuery.estimate_talent_adjusted_damage(p, w) * dmg_w
        expected += GameQuery.count_outer_armor(p) * float(
            _bget("ai", "outer_armor_weight", default=20))
        expected += GameQuery.count_inner_armor(p) * float(
            _bget("ai", "inner_armor_weight", default=15))
        got = GameQuery.estimate_power(p)
        self.assertAlmostEqual(got, expected, places=6)
        # 关掉 m8_ai → 回退旧魔数路径（同一组默认值，仍应一致）
        experiments.disable("m8_ai")
        self.assertAlmostEqual(GameQuery.estimate_power(p), got, places=6)
        experiments.reset()

    def test_m9_facts_public_holder(self) -> None:
        """B2：M9Facts.public_holder 从 _public_holder_by_round 读当前轮持有者。"""
        from controllers.ai.decision.snapshot import ProjectedSnapshot
        state, p, p2, p3 = self._world()
        state.current_round = 3
        state.m9_system._public_holder_by_round[3] = "p2"
        snap = ProjectedSnapshot.build(p, state)
        self.assertEqual(snap.m9.public_holder, "p2")
        state.current_round = 4
        snap = ProjectedSnapshot.build(p, state)
        self.assertIsNone(snap.m9.public_holder)

    def test_police_mind_m9_wanted(self) -> None:
        """M9 接入：警察判定走 m9 事实（通缉 → 举报目标识别 + 追击阶段）。"""
        from controllers.ai.minds.police_mind import PoliceMind
        from controllers.ai.decision.snapshot import ProjectedSnapshot
        from controllers.ai.strategies.base_strategy import BasePersonalityStrategy
        from engine.m9.police import PoliceStation
        from controllers.ai.controller import create_ai_controller
        state, p, p2, p3 = self._world()
        p.controller = create_ai_controller()
        state.m9_police = PoliceStation()
        state.m9_police.set_state_ref(state)
        case = state.m9_police.file_case("r1", "p1", evidence=1)
        state.m9_police.verify_case(case.case_id)
        snap = ProjectedSnapshot.build(p, state)
        mind = PoliceMind(debug_name="P")
        sit = mind.assess(p, state, {
            "m9_police": True,
            "wanted": "p1",
            "captain": None,
            "disabled": False,
            "roster": [],
        }, {}, "公园", snapshot=snap, assessment=None)
        self.assertTrue(sit.police_exists)
        self.assertTrue(sit.i_am_report_target)
        self.assertEqual(sit.report_phase, "reported")
        self.assertEqual(snap.m9.police_wanted, "p1")

    def test_combat_target_derived_consumption(self) -> None:
        """派生层消费集成：CombatMind 无目标时，COMBAT 阶段用
        AssessmentLayer.combat_target 降级进入战斗。"""
        from unittest.mock import patch
        from controllers.ai.controller import create_ai_controller
        state, p, p2, p3 = self._world()
        p.controller = create_ai_controller()
        orch = p.controller._orchestrator
        asm = type("A", (), {"combat_target": "p2", "notes": {}})()
        orch._assessment = asm
        orch._in_combat = False
        snapshots = {
            "combat": type("S", (), {"data": {
                "combat_ready": True, "best_target": None,
                "all_countered": False, "viable_targets": [],
            }})(),
            "develop": type("S", (), {"data": {
                "development_complete": True}}) (),
        }
        with patch.object(orch, "_build_forced_attack_commands",
                          return_value=["attack AI2 弓"]):
            cmds = orch._handle_combat(p, state, ["attack"], snapshots, 1)
        self.assertEqual(cmds, ["attack AI2 弓"])
        self.assertTrue(orch._in_combat)
        self.assertEqual(orch._combat_target.player_id, "p2")
        self.assertEqual(orch._combat_target.name, "AI2")

    def test_orchestrator_derived_consumption(self) -> None:
        from controllers.ai.controller import create_ai_controller
        state, p, p2, p3 = self._world()
        p.controller = create_ai_controller()
        orch = p.controller._orchestrator
        # 模拟 minds 输出：派生层 location_threat 高 → move 高威胁地点被过滤
        # 威胁分量级 ≈ estimate_power（hp20 单个对手 200-400）：
        # 800（2+ 人压点）剔除；250（单对手抢点）保留
        orch._assessment = type("A", (), {
            "location_threat": {"军事基地": 800.0, "公园": 10.0, "商店": 250.0},
            "combat_target": None,
            "notes": {}})()
        orch._in_combat = False
        filtered = orch._filter_high_threat_moves(
            ["move 军事基地", "move 公园", "move 商店", "attack p2 弓"], "公园")
        self.assertNotIn("move 军事基地", filtered)  # 2+ 对手压点被剔除
        self.assertIn("move 公园", filtered)
        self.assertIn("move 商店", filtered)  # 单对手抢点不剔除
        self.assertIn("attack p2 弓", filtered)

    def test_orchestrator_barrier_action_filter(self) -> None:
        """M9 事实接入：结界内被困 AI 的可用动作被清洗（移除移动类）。"""
        from engine.m9.talents.g3 import Mythland9, active_barrier as m9_barrier
        from engine.m9.talents.g4 import Savior9
        from controllers.ai.controller import create_ai_controller
        state = GameState()
        g3 = Player("p1", "G3", controller=_C())
        g3.is_awake = True
        g3.location = "公园"
        g3.hp = 20
        g3.max_hp = 20
        g3.talent = Mythland9("p1", state)
        state.add_player(g3)
        trapped = Player("p2", "被困者", controller=create_ai_controller())
        trapped.is_awake = True
        trapped.location = "公园"
        trapped.hp = 20
        trapped.max_hp = 20
        state.add_player(trapped)
        g4 = Player("p3", "G4", controller=_C())
        g4.is_awake = True
        g4.location = "商店"
        g4.hp = 20
        g4.max_hp = 20
        g4.talent = Savior9("p3", state)
        state.add_player(g4)
        state.m9_system.set_sp("p1", 2)
        state.m9_system.register_performance("p1", 1)
        state.m9_system.allocate_public_slot(1)
        g3.talent._expand_barrier(g3, state.m9_system, 1)
        self.assertTrue(m9_barrier(state).barrier_active)
        self.assertTrue(m9_barrier(state)._is_trapped(trapped))
        orch = trapped.controller._orchestrator
        cleaned = orch._filter_barrier_actions(
            trapped, state,
            ["wake", "move", "interact 商店", "attack p1 弓", "special 破界"])
        self.assertNotIn("move", cleaned)
        self.assertNotIn("interact 商店", cleaned)
        self.assertIn("attack p1 弓", cleaned)
        self.assertIn("special 破界", cleaned)
        # 结界外玩家不受影响
        cleaned_out = orch._filter_barrier_actions(g4, state, ["move", "attack"])
        self.assertIn("move", cleaned_out)

    def test_orchestrator_destroyed_move_filter(self) -> None:
        """M9 事实接入：move 到 m9_destroyed_locations 的指令被过滤。"""
        from controllers.ai.controller import create_ai_controller
        state, p, p2, p3 = self._world()
        p.controller = create_ai_controller()
        orch = p.controller._orchestrator
        state.m9_destroyed_locations = ["医院"]
        from controllers.ai.decision.snapshot import DecisionSnapshot
        orch._snapshot = DecisionSnapshot.build(p, state)
        filtered = orch._filter_destroyed_moves(
            ["move 医院", "move 公园", "attack p2 弓"])
        self.assertNotIn("move 医院", filtered)
        self.assertIn("move 公园", filtered)
        self.assertIn("attack p2 弓", filtered)


if __name__ == "__main__":
    unittest.main()
