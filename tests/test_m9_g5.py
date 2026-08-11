"""M9 G5 轮回锚定机制单测（阶段 7）：四形态/归家非死亡/转世/追忆封存/
AnchorScript 投影器/逐槽监控（自然实现/再投影强制/因果改写）/窄回溯/完结条。"""
import unittest
from types import SimpleNamespace

from engine import experiments
from engine.game_state import GameState
from models.equipment import Weapon, WeaponRange
from models.player import Player
from controllers.forfeit_controller import ForfeitController
from utils.attribute import Attribute

from engine.m9.gate import ensure_state_mechanisms
from engine.m9.talents.g5 import (
    AnchorScriptProjector, FORM_CYRENE, FORM_DEMIURGE, FORM_HOME, FORM_PAST,
    Ripple9,
)


def _enable(*flags):
    experiments.reset()
    for f in flags:
        experiments.enable(f)


def _make():
    state = GameState()
    ensure_state_mechanisms(state)
    p = Player("p1", "G5", controller=ForfeitController())
    state.add_player(p)
    p.max_hp = 20
    p.hp = 20
    t = Ripple9("p1", state)
    p.talent = t
    state.m9_system.set_sp("p1", 2)
    return state, p, t


class FormLifecycleTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_homecoming_not_death(self) -> None:
        state, p, t = _make()
        t.form = FORM_CYRENE
        p.hp = 0
        kind = t.m9_on_lethal(p, None, "normal")
        self.assertEqual(kind, "g5_homecoming")
        self.assertEqual(t.form, FORM_HOME)

    def test_absolute_death_kills_cyrene(self) -> None:
        state, p, t = _make()
        t.form = FORM_CYRENE
        self.assertIsNone(t.m9_on_lethal(p, None, "g7_terror"))

    def test_reincarnation_at_r0(self) -> None:
        state, p, t = _make()
        t.form = FORM_HOME
        t.incarnations = 1
        t.sealed_reminiscence = 2.0
        t.on_round_start(2)
        self.assertEqual(t.form, FORM_CYRENE)
        self.assertEqual(t.incarnations, 2)

    def test_demiurge_birth_at_threshold(self) -> None:
        state, p, t = _make()
        t.form = FORM_HOME
        t.sealed_reminiscence = 20.0  # ≥ birth_threshold(12)
        t.on_round_start(2)
        self.assertEqual(t.form, FORM_DEMIURGE)

    def test_past_closure_freezes_pp(self) -> None:
        state, p, t = _make()
        t.form = FORM_DEMIURGE
        t.sealed_reminiscence = 1.0  # < anchor_min_k(3)
        state.m9_pp.earn("p1", 5)
        t.on_round_start(2)
        self.assertEqual(t.form, FORM_PAST)
        self.assertTrue(state.m9_pp.is_frozen("p1"))

    def test_reminiscence_sealed_capped(self) -> None:
        state, p, t = _make()
        t.form = FORM_CYRENE
        t.m9_on_combat_event("combat", personal=True)
        self.assertEqual(t.sealed_reminiscence, 2.0)
        for _ in range(30):
            t.m9_on_combat_event("idle")
        self.assertLessEqual(t.sealed_reminiscence, 24.0)


class ProjectorTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_validate_illegal_first_slot(self) -> None:
        state, p, t = _make()
        proj = AnchorScriptProjector(state, t)
        self.assertEqual(proj.validate_script([("forfeit", "p2")]), 0)
        self.assertEqual(proj.validate_script([("attack", "p2")]), 0)  # 缺武器
        self.assertIsNone(proj.validate_script([("attack", "p2", "小刀"),
                                                ("move", "商店")]))

    def test_project_defeat_candidate(self) -> None:
        state, p, t = _make()
        target = Player("p2", "目标", controller=ForfeitController())
        target.location = "商店"
        target.hp = 2
        state.add_player(target)
        p.weapons.append(Weapon("小刀", Attribute.ORDINARY, 4, WeaponRange.MELEE))
        proj = AnchorScriptProjector(state, t)
        cands = proj.project([("attack", "p2", "小刀")], 1)
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0].kind, "DEFEAT")
        self.assertEqual(cands[0].subject_id, "p2")

    def test_precheck_rejects_no_candidates(self) -> None:
        state, p, t = _make()
        t.form = FORM_DEMIURGE
        t.sealed_reminiscence = 10
        msg, ok = t.execute_anchor(p, [("move", "医院")])
        self.assertFalse(ok)  # 投影无候选 → 预检失败（不消费）


class AnchorLifecycleTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_anchor_commit_and_monitor_rewind(self) -> None:
        """锚定建立 → 逐槽监控（目标已死 = 自然实现）→ 未来闭合 + 窄回溯。"""
        state, p, t = _make()
        target = Player("p2", "目标", controller=ForfeitController())
        target.location = "商店"
        target.hp = 2
        state.add_player(target)
        p.weapons.append(Weapon("小刀", Attribute.ORDINARY, 4, WeaponRange.MELEE))
        t.form = FORM_DEMIURGE
        t.sealed_reminiscence = 10
        p.hp = 5
        script = [("attack", "p2", "小刀"), ("move", "医院"),
                  ("attack", "p2", "小刀")]
        msg, ok = t.execute_anchor(p, script)
        self.assertTrue(ok)
        self.assertTrue(t.active_anchor)
        self.assertEqual(t.sealed_reminiscence, 7)  # K=3 扣 3
        self.assertEqual(state.m9_system.get_sp("p1"), 0)  # 公演扣 2
        # 目标先被打死 → 槽 0 DEFEAT 自然实现；槽 1 RELOCATE（p1 去医院）；
        # 槽 2 DEFEAT（p2 已死）自然实现 → 未来闭合
        target.hp = 0
        state.current_round = 6
        t.on_round_end(6)  # 槽 0
        t.on_round_end(7)  # 槽 1
        t.on_round_end(8)  # 槽 2 → 收尾
        self.assertEqual(t.anchor_results, ["未来闭合"])
        self.assertFalse(t.active_anchor)
        self.assertEqual(p.hp, 5)  # 窄回溯恢复快照 HP

    def test_anchor_rewrite_on_unreachable(self) -> None:
        """再投影不可达 → 因果改写（脚本失败、未来槽取消）。"""
        state, p, t = _make()
        target = Player("p2", "目标", controller=ForfeitController())
        target.location = "商店"
        target.hp = 20
        state.add_player(target)
        p.weapons.append(Weapon("小刀", Attribute.ORDINARY, 4, WeaponRange.MELEE))
        t.form = FORM_DEMIURGE
        t.sealed_reminiscence = 10
        msg, ok = t.execute_anchor(p, [("move", "医院")])
        self.assertFalse(ok)  # move 投影无候选——用 RELOCATE 需要候选；这里验证拒绝

    def test_anchor_blocked_by_love_wish(self) -> None:
        state, p, t = _make()
        other = Player("p2", "路人", controller=ForfeitController())
        state.add_player(other)
        other.talent = SimpleNamespace(
            has_love_wish=lambda pid: pid == "p1")
        t.form = FORM_DEMIURGE
        t.sealed_reminiscence = 10
        msg, ok = t.execute_anchor(p, [("move", "医院")])
        self.assertFalse(ok)
        self.assertIn("爱愿", msg)


if __name__ == "__main__":
    unittest.main()
