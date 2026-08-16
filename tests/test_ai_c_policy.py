"""C 层简单版测试：choose 目标选择启发式 + T6 三 special + 锚定脚本 + G1 守卫。"""
import unittest
from types import SimpleNamespace

from engine import experiments
from engine.game_state import GameState
from models.player import Player

from controllers.base import PlayerController
from controllers.ai.decision.c_policy import (
    anchor_script, c_decide_choose,
)
from controllers.ai.decision.t0_policy import should_activate_t0


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


class CPolicyTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def _world(self, threat=None):
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
        p3 = Player("p3", "AI3", controller=_C())
        p3.is_awake = True
        p3.location = "医院"
        p3.hp = 20
        p3.max_hp = 20
        state.add_player(p3)
        ctrl = SimpleNamespace(
            personality="balanced", _player=p, _game_state=state,
            _threat_scores=threat or {})
        return ctrl, state, p, p2, p3

    # ── 目标选择 ──

    def test_target_situations_pick_max_threat(self) -> None:
        ctrl, state, p, p2, p3 = self._world({"AI3": 300.0, "AI2": 100.0})
        out = c_decide_choose(
            ctrl, "群星弹射目标：", ["AI2", "AI3"],
            {"phase": "T0", "situation": "t3_stars_bounce_target"}, state)
        self.assertEqual(out, "AI3")
        out = c_decide_choose(
            ctrl, "复活目标：", ["AI1", "AI2", "AI3"],
            {"phase": "T0", "situation": "resurrection_pick_target"}, state)
        self.assertEqual(out, "AI1")

    def test_poem_for_top_threat_slot(self) -> None:
        ctrl, state, p, p2, p3 = self._world({"AI2": 300.0})
        p2.talent = SimpleNamespace(slot_id="T7")
        options = ["游侠", "地火", "彼岸", "负世", "明天"]
        out = c_decide_choose(
            ctrl, "献诗：选择诗篇：", options, None, state)
        self.assertEqual(out, "彼岸")

    def test_shadow_action(self) -> None:
        from models.equipment import Weapon, WeaponRange
        ctrl, state, p, p2, p3 = self._world()
        shadow = SimpleNamespace(
            location="商店", weapons=[Weapon("小刀", "普通", 4,
                                            WeaponRange.MELEE)],
            owner_pid="p1")
        ctrl._player = shadow
        out = c_decide_choose(
            ctrl, "AI1 影身行动：",
            ["move", "interact", "find", "lock", "attack", "forfeit",
             "消散影身"], None, state)
        self.assertEqual(out, "attack")
        shadow.weapons = []
        out = c_decide_choose(
            ctrl, "AI1 影身行动：",
            ["move", "interact", "find", "lock", "attack", "forfeit",
             "消散影身"], None, state)
        self.assertEqual(out, "forfeit")

    def test_g6_cutaway_prompts(self) -> None:
        ctrl, state, p, p2, p3 = self._world()
        out = c_decide_choose(
            ctrl, "即演重演或公演？", ["即演", "公演"], None, state)
        self.assertEqual(out, "即演")
        out = c_decide_choose(
            ctrl, "选择重演类别：", ["move", "interact", "attack"], None, state)
        self.assertEqual(out, "attack")
        out = c_decide_choose(
            ctrl, "选择借用核心：",
            ["t1_one_slash", "t3_heavenly_star", "g3_reality_marble"],
            None, state)
        # 无近处敌人且无真实武器 → 不借天星（地点 AOE 无目标），取首个
        self.assertEqual(out, "t1_one_slash")
        # 有近处敌人 → 天星优先
        p2.location = p.location
        out = c_decide_choose(
            ctrl, "选择借用核心：",
            ["t1_one_slash", "t3_heavenly_star", "g3_reality_marble"],
            None, state)
        self.assertEqual(out, "t3_heavenly_star")
        # 无近处敌人但有真实武器 → 一刀缭断
        p2.location = "商店"
        p.weapons.append(
            __import__("models.equipment", fromlist=["make_weapon"]).make_weapon(
                "小刀"))
        out = c_decide_choose(
            ctrl, "选择借用核心：",
            ["t1_one_slash", "t3_heavenly_star", "g3_reality_marble"],
            None, state)
        self.assertEqual(out, "t1_one_slash")
        # 无近处敌人、无武器、有六爻 → 六爻
        p.weapons.clear()
        out = c_decide_choose(
            ctrl, "选择借用核心：",
            ["t4_hexagram", "t3_heavenly_star", "g3_reality_marble"],
            None, state)
        self.assertEqual(out, "t4_hexagram")

        state.current_round = 3
        state.m9_system = SimpleNamespace(
            _public_holder_by_round={3: p.player_id})
        out = c_decide_choose(
            ctrl, "即演重演或公演？", ["即演", "公演"], None, state)
        self.assertEqual(out, "公演")

        ctrl._threat_scores = {p2.name: 2.0, p3.name: 9.0}
        out = c_decide_choose(
            ctrl, "选择借用核心目标：", [p2.name, p3.name], None, state)
        self.assertEqual(out, p3.name)

    def test_t6_equip_mode(self) -> None:
        ctrl, state, p, p2, p3 = self._world()
        out = c_decide_choose(
            ctrl, "选择联防整备方式", ["即演", "公演"], None, state)
        self.assertEqual(out, "即演")
        ctrl.personality = "defensive"
        out = c_decide_choose(
            ctrl, "整备类型：", ["警棍", "盾牌", "高斯步枪"], None, state)
        self.assertEqual(out, "盾牌")

    def test_g0_public_prefers_relic_when_available(self) -> None:
        ctrl, state, p, *_ = self._world()
        p.talent = SimpleNamespace(relics=[{"slot": "T3"}])
        out = c_decide_choose(
            ctrl, "G0 公演：", ["十字炮火", "遗物支援技"], None, state)
        self.assertEqual(out, "遗物支援技")
        p.talent.relics = []
        out = c_decide_choose(
            ctrl, "G0 公演：", ["十字炮火", "遗物支援技"], None, state)
        self.assertEqual(out, "十字炮火")

    def test_tianji_skip(self) -> None:
        ctrl, state, p, p2, p3 = self._world()
        out = c_decide_choose(
            ctrl, "阴阳的天机：", ["不指定", "指定卦象"], None, state)
        self.assertEqual(out, "不指定")

    def test_passthrough_unknown(self) -> None:
        ctrl, state, p, p2, p3 = self._world()
        out = c_decide_choose(
            ctrl, "出拳：", ["石头", "剪刀", "布"],
            {"situation": "hexagram_my_choice"}, state)
        self.assertIsNone(out)

    # ── T6 三 special ──

    def _t6_world(self, personality="balanced"):
        from engine.m9.police import PoliceStation
        state = GameState()
        t6 = Player("p1", "T6", controller=_C())
        t6.is_awake = True
        t6.location = "公园"
        t6.hp = 20
        t6.max_hp = 20
        state.add_player(t6)
        suspect = Player("p2", "嫌疑人", controller=_C())
        suspect.is_awake = True
        suspect.location = "商店"
        suspect.hp = 20
        suspect.max_hp = 20
        state.add_player(suspect)
        state.m9_police = PoliceStation()
        state.m9_police.set_state_ref(state)
        from controllers.ai.m9_adapters import _T6Adapter
        ctrl = SimpleNamespace(personality=personality)
        return state, t6, suspect, _T6Adapter(ctrl)

    def test_t6_hotline_uses_evidence_only_without_wanted(self) -> None:
        state, t6, suspect, adapter = self._t6_world()
        t6.talent = SimpleNamespace(
            _evidence_for=lambda pid: ("t6_clue", pid == suspect.player_id))
        cmds = adapter.get_talent_special_candidates(
            t6, state, ["special", "move"])
        self.assertIn("special 热线举报嫌疑人", cmds)

        case = state.m9_police.file_case("r1", "p2", evidence=1)
        state.m9_police.verify_case(case.case_id)
        cmds = adapter.get_talent_special_candidates(
            t6, state, ["special", "move"])
        self.assertFalse(any("热线举报" in c for c in cmds))

    def test_t6_no_hotline_for_self(self) -> None:
        state, t6, suspect, adapter = self._t6_world()
        case = state.m9_police.file_case("r1", "p1", evidence=1)
        state.m9_police.verify_case(case.case_id)
        cmds = adapter.get_talent_special_candidates(
            t6, state, ["special", "move"])
        self.assertFalse(any("热线举报" in c for c in cmds))

    def test_t6_captain_election_personality(self) -> None:
        state, t6, suspect, adapter = self._t6_world("balanced")
        cmds = adapter.get_talent_special_candidates(
            t6, state, ["special", "move"])
        self.assertIn("special 竞选队长", cmds)
        state, t6, suspect, adapter = self._t6_world("defensive")
        cmds = adapter.get_talent_special_candidates(
            t6, state, ["special", "move"])
        self.assertNotIn("special 竞选队长", cmds)

    def test_t6_command_move_as_captain(self) -> None:
        state, t6, suspect, adapter = self._t6_world("balanced")
        state.m9_police.captain_id = "p1"
        state.m9_police._roster = [
            SimpleNamespace(unit_id="unit1", location="商店", is_alive=lambda: True)]
        cmds = adapter.get_talent_special_candidates(
            t6, state, ["special", "move"])
        self.assertIn("special 指挥unit1移动", cmds)

    # ── G5 锚定脚本 ──

    def test_anchor_script_empty_without_failable_prophecy(self) -> None:
        """无武器且无地面遗落物 → 构造不出可落空预言 → 空脚本（放弃锚定）。"""
        ctrl, state, p, p2, p3 = self._world()
        self.assertEqual(anchor_script(p, state), [])

    def test_anchor_script_real_prediction_padded(self) -> None:
        """真实预言脚本：近战可击倒对手 → find 前置 + attack，move 垫至 anchor_min_k。"""
        from models.equipment import Weapon, WeaponRange
        from utils.attribute import Attribute
        ctrl, state, p, p2, p3 = self._world()
        p.weapons.append(Weapon("小刀", Attribute.ORDINARY, 4, WeaponRange.MELEE))
        p2.hp = 3  # 探针伤害 4 ≥ 3 → DEFEAT 候选
        script = anchor_script(p, state)
        self.assertEqual(script[0], ("find", "p2"))
        self.assertEqual(script[1], ("attack", "p2", "小刀"))
        self.assertEqual(len(script), 3)          # anchor_min_k 调参后为 3
        self.assertTrue(all(kind == "move" for kind, _ in script[2:]))
        locs = [loc for _, loc in script[2:]]
        self.assertNotIn("公园", locs)            # 排除当前位置

    # ── G1 失熵守卫 ──

    def test_g1_entropy_guard(self) -> None:
        ctrl, state, p, p2, p3 = self._world()
        p.talent = SimpleNamespace(slot_id="G1", entropy=5.0)
        self.assertFalse(should_activate_t0(
            "G1", 2, "aggressive", 20, 20, state=state, player=p))
        p.talent.entropy = 2.0
        self.assertTrue(should_activate_t0(
            "G1", 2, "aggressive", 20, 20, state=state, player=p))

    # ── G5 T0 门：真实预言构造 ──

    def test_g5_t0_gate_requires_real_prediction(self) -> None:
        from engine.m9.gate import ensure_state_mechanisms
        from engine.m9.talents.g5 import Ripple9
        ctrl, state, p, p2, p3 = self._world()
        ensure_state_mechanisms(state)
        p.talent = Ripple9("p1", state)
        p.talent.form = "demiurge"
        p.talent.sealed_reminiscence = 10
        p.talent._ripple_used_this_cycle = True  # 微澜已用：只剩锚定入口
        state.ground_loot = {}                   # 无地面遗落物
        state.m9_system.set_sp("p1", 2)
        holders = getattr(state.m9_system, "_public_holder_by_round", {})
        holders[int(getattr(state, "current_round", 1))] = "p1"
        # 无武器且无地面遗落物 → 无真实预言 → 不开锚定入口（无微澜）
        self.assertFalse(should_activate_t0(
            "G5", 2, "balanced", 20, 20, state=state, player=p))
        # 有可击倒目标 → 锚定入口开启
        from models.equipment import Weapon, WeaponRange
        from utils.attribute import Attribute
        p.weapons.append(Weapon("小刀", Attribute.ORDINARY, 4, WeaponRange.MELEE))
        p2.hp = 3
        self.assertTrue(should_activate_t0(
            "G5", 2, "balanced", 20, 20, state=state, player=p))


if __name__ == "__main__":
    unittest.main()
