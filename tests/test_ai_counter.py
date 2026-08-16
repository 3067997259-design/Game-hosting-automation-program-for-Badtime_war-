"""counter 层测试（capabilities 类目声明 + 反制模板 + 破界响应）。

覆盖：G3 结界类目声明、G4 焚诏/救世主类目、结界内被困 AI 的破界产出、
结界外无破界、未知槽位回退。
"""
import unittest

from controllers.base import PlayerController

from engine import experiments
from engine.game_state import GameState
from models.player import Player


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


class CounterLayerTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def _world(self):
        from engine.m9.talents.g3 import Mythland9
        from engine.m9.talents.g4 import Savior9
        state = GameState()
        g3 = Player("p1", "G3", controller=_C())
        g3.is_awake = True
        g3.location = "公园"
        g3.hp = 20
        g3.max_hp = 20
        g3.talent = Mythland9("p1", state)
        state.add_player(g3)
        trapped = Player("p2", "被困者", controller=_C())
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
        return state, g3, trapped, g4

    def test_capabilities_declaration(self) -> None:
        from controllers.ai.decision.snapshot import ProjectedSnapshot
        from controllers.ai.counter import capabilities_of
        state, g3, trapped, g4 = self._world()
        snap = ProjectedSnapshot.build(trapped, state)
        self.assertEqual(capabilities_of(snap, "p1"), {"barrier"})
        self.assertEqual(capabilities_of(snap, "p3"), {"ritual", "temp_hp"})
        self.assertEqual(capabilities_of(snap, "p2"), set())  # 无天赋槽

    def test_break_barrier_response_inside(self) -> None:
        from controllers.ai.decision.snapshot import ProjectedSnapshot
        from controllers.ai.counter import counter_candidates
        state, g3, trapped, g4 = self._world()
        state.m9_system.set_sp("p1", 2)
        state.m9_system.register_performance("p1", 1)
        state.m9_system.allocate_public_slot(1)
        g3.talent._expand_barrier(g3, state.m9_system, 1)
        snap = ProjectedSnapshot.build(trapped, state)
        cmds = counter_candidates(
            trapped, snap, ["wake", "move", "special", "attack", "forfeit"])
        self.assertIn("special 破界", cmds)

    def test_no_counter_outside_barrier(self) -> None:
        from controllers.ai.decision.snapshot import ProjectedSnapshot
        from controllers.ai.counter import counter_candidates
        state, g3, trapped, g4 = self._world()
        snap = ProjectedSnapshot.build(trapped, state)
        self.assertEqual(
            counter_candidates(trapped, snap, ["wake", "move"]), [])

    def test_same_location_late_arrival_is_not_misread_as_captured(self) -> None:
        from controllers.ai.decision.snapshot import ProjectedSnapshot
        from controllers.ai.counter import counter_candidates
        state, g3, trapped, g4 = self._world()
        g4.location = "商店"
        state.m9_system.set_sp("p1", 2)
        state.m9_system.register_performance("p1", 1)
        state.m9_system.allocate_public_slot(1)
        g3.talent._expand_barrier(g3, state.m9_system, 1)
        self.assertIn("p2", g3.talent.captured)
        g4.location = "公园"  # 展开后才到原地点，仍在结界外
        snap = ProjectedSnapshot.build(g4, state)
        self.assertNotIn("p3", snap.m9.barrier_captured)
        self.assertEqual(counter_candidates(
            g4, snap, ["special", "forfeit"]), [])

    def test_ritual_pressure_g4_high_divinity(self) -> None:
        """A5：对手 G4 火种≥8 → 施压打断候选（RITUAL 类目首例）。"""
        from engine.m9.talents.g4 import Savior9
        from models.equipment import Weapon, WeaponRange
        from controllers.ai.decision.snapshot import ProjectedSnapshot
        from controllers.ai.counter import counter_candidates
        state = GameState()
        g4 = Player("p1", "G4", controller=_C())
        g4.is_awake = True
        g4.location = "商店"
        g4.hp = 20
        g4.max_hp = 20
        g4.talent = Savior9("p1", state)
        state.add_player(g4)
        presser = Player("p2", "施压者", controller=_C())
        presser.is_awake = True
        presser.location = "公园"
        presser.hp = 20
        presser.max_hp = 20
        presser.weapons = [Weapon("拳击", "普通", 2, WeaponRange.MELEE)]
        state.add_player(presser)
        g4.talent.divinity = 9
        snap = ProjectedSnapshot.build(presser, state)
        cmds = counter_candidates(presser, snap, ["attack", "move"],
                                  state=state)
        self.assertEqual(cmds, ["attack G4 拳击"])
        # 火种低 → 不施压
        g4.talent.divinity = 3
        cmds = counter_candidates(presser, snap, ["attack", "move"],
                                  state=state)
        self.assertEqual(cmds, [])

    def test_ritual_pressure_g5_active_anchor(self) -> None:
        """RITUAL：对手 G5 激活锚定中 → 施压锚定者（打断脚本槽）。"""
        from engine.m9.talents.g5 import Ripple9
        from models.equipment import Weapon, WeaponRange
        from controllers.ai.decision.snapshot import ProjectedSnapshot
        from controllers.ai.counter import counter_candidates
        state = GameState()
        g5 = Player("p1", "G5", controller=_C())
        g5.is_awake = True
        g5.location = "商店"
        g5.hp = 20
        g5.max_hp = 20
        g5.talent = Ripple9("p1", state)
        state.add_player(g5)
        presser = Player("p2", "施压者", controller=_C())
        presser.is_awake = True
        presser.location = "公园"
        presser.hp = 20
        presser.max_hp = 20
        presser.weapons = [Weapon("拳击", "普通", 2, WeaponRange.MELEE)]
        state.add_player(presser)
        g5.talent.active_anchor = True
        snap = ProjectedSnapshot.build(presser, state)
        cmds = counter_candidates(presser, snap, ["attack", "move"],
                                  state=state)
        self.assertEqual(cmds, ["attack G5 拳击"])
        # 锚定未激活 → 不施压
        g5.talent.active_anchor = False
        cmds = counter_candidates(presser, snap, ["attack", "move"],
                                  state=state)
        self.assertEqual(cmds, [])

    def test_burst_window_pressure_g1_supernova_or_full_burn(self) -> None:
        """BURST_WINDOW：对手 G1 持超新星 / 完全燃烧窗口 → 压制（风洞 R29
        解剖：对手对 G1 仅 0.12-0.20 攻击/存活轮，需反打而非只散开）。"""
        from engine.m9.talents.g1 import G1MythFire9
        from models.equipment import Weapon, WeaponRange
        from controllers.ai.decision.snapshot import ProjectedSnapshot
        from controllers.ai.counter import counter_candidates
        state = GameState()
        g1 = Player("p1", "G1", controller=_C())
        g1.is_awake = True
        g1.location = "商店"
        g1.hp = 20
        g1.max_hp = 20
        g1.talent = G1MythFire9("p1", state)
        state.add_player(g1)
        presser = Player("p2", "施压者", controller=_C())
        presser.is_awake = True
        presser.location = "公园"
        presser.hp = 20
        presser.max_hp = 20
        presser.weapons = [Weapon("拳击", "普通", 2, WeaponRange.MELEE)]
        state.add_player(presser)
        # 持超新星 → 施压
        g1.talent.has_supernova = True
        snap = ProjectedSnapshot.build(presser, state)
        cmds = counter_candidates(presser, snap, ["attack", "move"],
                                  state=state)
        self.assertEqual(cmds, ["attack G1 拳击"])
        # 无超新星、非完全燃烧 → 不施压（普通威胁走常规目标选择）
        g1.talent.has_supernova = False
        cmds = counter_candidates(presser, snap, ["attack", "move"],
                                  state=state)
        self.assertEqual(cmds, [])
        # 完全燃烧窗口 → 施压
        g1.talent.form = "full_burn"
        cmds = counter_candidates(presser, snap, ["attack", "move"],
                                  state=state)
        self.assertEqual(cmds, ["attack G1 拳击"])

    def test_burst_window_pressure_g0_drone_crossfire(self) -> None:
        """G0 Terror：无人机在场且非呼吸免疫期 → 压制（击杀连坐无人机）。"""
        from engine.m9.talents.g0 import ShirokoTerror9
        from models.equipment import Weapon, WeaponRange
        from controllers.ai.decision.snapshot import ProjectedSnapshot
        from controllers.ai.counter import counter_candidates
        state = GameState()
        g0 = Player("p1", "Terror", controller=_C())
        g0.is_awake = True
        g0.location = "商店"
        g0.hp = 20
        g0.max_hp = 20
        g0.talent = ShirokoTerror9("p1", state)
        state.add_player(g0)
        presser = Player("p2", "施压者", controller=_C())
        presser.is_awake = True
        presser.location = "公园"
        presser.hp = 20
        presser.max_hp = 20
        presser.weapons = [Weapon("拳击", "普通", 2, WeaponRange.MELEE)]
        state.add_player(presser)
        # 无无人机 → 不施压
        snap = ProjectedSnapshot.build(presser, state)
        cmds = counter_candidates(presser, snap, ["attack", "move"],
                                  state=state)
        self.assertEqual(cmds, [])
        # 无人机在场 → 施压
        g0.talent.drone = object()
        snap = ProjectedSnapshot.build(presser, state)
        cmds = counter_candidates(presser, snap, ["attack", "move"],
                                  state=state)
        self.assertEqual(cmds, ["attack Terror 拳击"])
        # 呼吸免疫期 → 不施压（免疫期内攻击无效）
        g0.talent.breath_active = True
        snap = ProjectedSnapshot.build(presser, state)
        cmds = counter_candidates(presser, snap, ["attack", "move"],
                                  state=state)
        self.assertEqual(cmds, [])

    def test_break_barrier_full_chain_6p(self) -> None:
        """6 人 all_ai M9 局全链路：被困 AI 的 orchestrator 发起破界。"""
        from engine.m9.talents.g3 import Mythland9
        from engine.m9.talents.g4 import Savior9
        from controllers.ai.controller import create_ai_controller
        state = GameState()
        g3 = Player("p1", "G3持有者", controller=_C())
        g3.is_awake = True
        g3.location = "公园"
        g3.hp = 20
        g3.max_hp = 20
        g3.talent = Mythland9("p1", state)
        state.add_player(g3)
        trapped = Player("p2", "被困AI", controller=create_ai_controller())
        trapped.is_awake = True
        trapped.location = "公园"
        trapped.hp = 20
        trapped.max_hp = 20
        state.add_player(trapped)
        for i, loc in enumerate(["商店", "军事基地", "医院", "警局"]):
            other = Player(f"p{i + 3}", f"AI{i + 3}",
                           controller=create_ai_controller())
            other.is_awake = True
            other.location = loc
            other.hp = 20
            other.max_hp = 20
            state.add_player(other)
        g4 = Player("g4slot", "G4", controller=_C())
        g4.is_awake = True
        g4.location = "商店"
        g4.hp = 20
        g4.max_hp = 20
        g4.talent = Savior9("g4slot", state)
        state.add_player(g4)
        state.m9_system.set_sp("p1", 2)
        state.m9_system.register_performance("p1", 1)
        state.m9_system.allocate_public_slot(1)
        g3.talent._expand_barrier(g3, state.m9_system, 1)
        self.assertTrue(g3.talent._is_trapped(trapped))
        cmd = trapped.controller.get_command(
            trapped, state, ["attack", "special"],
            context={"round_num": 1})
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        self.assertIn("special 破界", cmd_str)

    def test_break_barrier_preempts_trapped_players_own_talent_hook(self) -> None:
        """真实 M9 人人有天赋：结构反制不能被被困者自己的 hook 提前截走。"""
        from engine.m9.talents.g1 import G1MythFire9
        from engine.m9.talents.g3 import Mythland9
        from controllers.ai.controller import create_ai_controller
        state = GameState()
        g3 = Player("p1", "G3持有者", controller=_C())
        g3.is_awake = True
        g3.location = "公园"
        g3.hp = g3.max_hp = 20
        g3.talent = Mythland9("p1", state)
        state.add_player(g3)
        trapped = Player(
            "p2", "有天赋的被困AI",
            controller=create_ai_controller(personality="aggressive"))
        trapped.is_awake = True
        trapped.location = "公园"
        trapped.hp = trapped.max_hp = 20
        trapped.talent = G1MythFire9("p2", state)
        trapped.talent.form = "propagation"  # 自身 hook 原本会优先 move
        state.add_player(trapped)
        state.m9_system.set_sp("p1", 2)
        state.m9_system.register_performance("p1", 1)
        state.m9_system.allocate_public_slot(1)
        g3.talent._expand_barrier(g3, state.m9_system, 1)
        cmd = trapped.controller.get_command(
            trapped, state, ["attack", "move", "special", "forfeit"],
            context={"round_num": 1})
        self.assertEqual(str(cmd), "special 破界")


if __name__ == "__main__":
    unittest.main()
