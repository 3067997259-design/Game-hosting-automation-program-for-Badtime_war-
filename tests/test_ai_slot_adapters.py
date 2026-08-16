"""G0/G2/G6 slot adapter 策略回归测试（M9 空壳 adapter 补肉批次）。"""
import unittest
from types import SimpleNamespace

from engine import experiments
from engine.game_state import GameState
from engine.m9.gate import ensure_state_mechanisms
from engine.m9.talents.g2 import ShadowActor, Hologram9
from models.equipment import make_weapon
from models.player import Player
from controllers.forfeit_controller import ForfeitController
from controllers.ai.controller import create_ai_controller
from controllers.ai.m9_adapters import resolve_talent_hook, _G0Adapter, _G5Adapter, _G6Adapter


def _enable(*flags):
    experiments.reset()
    for flag in flags:
        experiments.enable(flag)


def _world():
    state = GameState()
    ensure_state_mechanisms(state)
    ctrl = create_ai_controller()
    p = Player("p1", "玩家1", controller=ctrl)
    p.is_awake = True
    p.location = "商店"
    p.hp = p.max_hp = 20
    state.add_player(p)
    return state, p, ctrl


def _add(state, pid, location, hp=20):
    other = Player(pid, pid.upper(), controller=ForfeitController())
    other.is_awake = True
    other.location = location
    other.hp = other.max_hp = hp
    state.add_player(other)
    return other


class G2ShadowResolveTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_shadow_resolves_to_owner_g2_adapter(self) -> None:
        state, p, ctrl = _world()
        ctrl._game_state = state
        p.talent = Hologram9("p1", state)
        shadow = ShadowActor("p1", "商店", 8, ctrl)
        state.m9_shadows[shadow.actor_id] = shadow
        p.talent.current_shadow_id = shadow.actor_id
        hook = resolve_talent_hook(ctrl, shadow)
        self.assertIsNotNone(hook)
        self.assertEqual(getattr(hook, "slot_id", ""), "G2")

    def test_shadow_prefers_attack_candidates(self) -> None:
        state, p, ctrl = _world()
        ctrl._game_state = state
        p.talent = Hologram9("p1", state)
        shadow = ShadowActor("p1", "商店", 8, ctrl)
        shadow.weapons.append(make_weapon("小刀"))
        state.m9_shadows[shadow.actor_id] = shadow
        p.talent.current_shadow_id = shadow.actor_id
        target = _add(state, "p2", "商店")
        state.markers.init_player(shadow.player_id)
        state.markers.add_relation(shadow.player_id, "ENGAGED_WITH",
                                   target.player_id)
        hook = resolve_talent_hook(ctrl, shadow)
        out = hook.should_override_candidates(
            shadow, state, ["move", "interact", "find", "lock", "attack",
                            "forfeit"])
        self.assertIsNotNone(out)
        self.assertTrue(out[0].startswith("attack "))

    def test_light_body_flees_when_shadow_alive_and_enemy_nearby(self) -> None:
        state, p, ctrl = _world()
        ctrl._game_state = state
        p.talent = Hologram9("p1", state)
        shadow = ShadowActor("p1", "商店", 8, ctrl)
        state.m9_shadows[shadow.actor_id] = shadow
        p.talent.current_shadow_id = shadow.actor_id
        _add(state, "p2", "商店")
        hook = resolve_talent_hook(ctrl, p)
        out = hook.should_override_candidates(
            p, state, ["move", "interact", "find", "lock", "attack", "forfeit"])
        self.assertIsNotNone(out)
        self.assertTrue(out[0].startswith("move "))

    def test_light_body_does_not_flee_from_killable_enemy(self) -> None:
        """有底线避战：同地点残血可击杀目标在场 → 不逃，交回通用管道收割。"""
        state, p, ctrl = _world()
        ctrl._game_state = state
        p.talent = Hologram9("p1", state)
        shadow = ShadowActor("p1", "商店", 8, ctrl)
        state.m9_shadows[shadow.actor_id] = shadow
        p.talent.current_shadow_id = shadow.actor_id
        victim = _add(state, "p2", "商店")
        victim.hp = 1.0  # 拳击 2 伤害即可击杀
        hook = resolve_talent_hook(ctrl, p)
        out = hook.should_override_candidates(
            p, state, ["move", "interact", "find", "lock", "attack", "forfeit"])
        self.assertIsNone(out)


class G0AdapterTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_drone_and_sp2_moves_to_dense_enemy_location(self) -> None:
        state, p, ctrl = _world()
        ctrl._game_state = state
        p.talent = SimpleNamespace(drone=object())
        state.m9_system.set_sp("p1", 2)
        _add(state, "p2", "医院")
        _add(state, "p3", "医院")
        hook = _G0Adapter(ctrl)
        out = hook.should_override_candidates(
            p, state, ["move", "interact", "attack", "forfeit"])
        self.assertEqual(out, ["move 医院", "forfeit"])

    def test_no_override_without_sp(self) -> None:
        state, p, ctrl = _world()
        p.talent = SimpleNamespace(drone=object())
        state.m9_system.set_sp("p1", 1)
        hook = _G0Adapter(ctrl)
        self.assertIsNone(hook.should_override_candidates(
            p, state, ["move", "forfeit"]))

    def test_sp2_moves_to_dense_without_drone_planning(self) -> None:
        """布局不再要求无人机在场：SP≥2 先占敌人密集点（炮火链前置）。"""
        state, p, ctrl = _world()
        p.talent = SimpleNamespace(drone=None)
        state.m9_system.set_sp("p1", 2)
        _add(state, "p2", "医院")
        _add(state, "p3", "医院")
        hook = _G0Adapter(ctrl)
        out = hook.should_override_candidates(
            p, state, ["move", "interact", "attack", "forfeit"])
        self.assertEqual(out, ["move 医院", "forfeit"])

    def test_breath_forfeits_until_above_threshold_then_fights(self) -> None:
        """呼吸重设计：未过 40% 止损线先 forfeit 回血；过线后放行主战。"""
        state, p, ctrl = _world()
        ctrl._game_state = state
        p.hp = 1
        p.max_hp = 20
        p.talent = SimpleNamespace(breath_active=True, drone=None)
        state.m9_system.set_sp("p1", 1)
        hook = _G0Adapter(ctrl)
        out = hook.should_override_candidates(
            p, state, ["move", "attack", "forfeit"])
        self.assertEqual(out, ["forfeit"])
        p.hp = 9  # > 20 × 40%
        out = hook.should_override_candidates(
            p, state, ["move", "attack", "forfeit"])
        self.assertIsNone(out)

    def test_low_hp_heals_before_positioning(self) -> None:
        """苟活优先：低血时先医院治疗，不执行炮火布局。"""
        _enable("m9_rfc", "hp20", "m4_gear")
        state, p, ctrl = _world()
        ctrl._game_state = state
        p.hp = 5
        p.max_hp = 20
        p.talent = SimpleNamespace(drone=object(), breath_active=False)
        state.m9_system.set_sp("p1", 2)
        _add(state, "p2", "医院")
        _add(state, "p3", "医院")
        hook = _G0Adapter(ctrl)
        out = hook.should_override_candidates(
            p, state, ["move", "interact", "attack", "forfeit"])
        self.assertEqual(out, ["move 医院", "forfeit"])
        # 已在医院且无钱 → 打工攒治疗费；有钱 → 治疗
        p.location = "医院"
        p.credits = 0
        out = hook.should_override_candidates(
            p, state, ["move", "interact", "attack", "forfeit"])
        self.assertEqual(out, ["interact 打工", "forfeit"])
        p.credits = 5
        out = hook.should_override_candidates(
            p, state, ["move", "interact", "attack", "forfeit"])
        self.assertEqual(out, ["interact 治疗", "forfeit"])


class G6AdapterTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_public_holder_moves_to_dense_location_only_with_t3(self) -> None:
        state, p, ctrl = _world()
        ctrl._game_state = state
        state.m9_system.set_sp("p1", 2)
        state.current_round = 3
        state.m9_system._public_holder_by_round = {3: "p1"}
        _add(state, "p2", "魔法所")
        _add(state, "p3", "魔法所")
        hook = _G6Adapter(ctrl)
        # 无 T3 来源在场：不再冲进敌人堆送血。
        out = hook.should_override_candidates(
            p, state, ["move", "interact", "attack", "forfeit"])
        self.assertIsNone(out)
        # T3 在场：移动到密集点布局天星借用。
        state.get_player("p2").talent_slot_id = "T3"
        out = hook.should_override_candidates(
            p, state, ["move", "interact", "attack", "forfeit"])
        self.assertEqual(out, ["move 魔法所", "forfeit"])

    def test_no_override_without_public_holder(self) -> None:
        state, p, ctrl = _world()
        state.m9_system.set_sp("p1", 2)
        state.current_round = 3
        _add(state, "p2", "魔法所")
        _add(state, "p3", "魔法所")
        hook = _G6Adapter(ctrl)
        self.assertIsNone(hook.should_override_candidates(
            p, state, ["move", "forfeit"]))


class G5AdapterTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_pursues_script_attack_slot(self) -> None:
        from engine.m9.talents.g5 import Ripple9, EventCandidate
        state, p, ctrl = _world()
        ctrl._game_state = state
        p.talent = Ripple9("p1", state)
        target = _add(state, "p2", "商店", hp=3)
        p.weapons.append(make_weapon("小刀"))
        state.markers.init_player(p.player_id)
        state.markers.add_relation(p.player_id, "ENGAGED_WITH",
                                   target.player_id)
        p.talent.active_anchor = True
        p.talent.anchor_slot_index = 0
        p.talent.anchor_candidates = [
            EventCandidate("DEFEAT", 0, "p2", ("attack", "p2", "小刀")),
        ]
        hook = _G5Adapter(ctrl)
        out = hook.should_override_candidates(
            p, state, ["move", "interact", "attack", "forfeit"])
        self.assertIsNotNone(out)
        self.assertTrue(out[0].startswith("attack P2"))
        self.assertIn("小刀", out[0])

    def test_chases_unreachable_script_target(self) -> None:
        """目标不可攻击 → 主动移动追击（可达性门要求锚定者全程追击）。"""
        from engine.m9.talents.g5 import Ripple9, EventCandidate
        state, p, ctrl = _world()
        ctrl._game_state = state
        p.talent = Ripple9("p1", state)
        _add(state, "p2", "医院", hp=3)
        p.weapons.append(make_weapon("小刀"))
        p.talent.active_anchor = True
        p.talent.anchor_slot_index = 0
        p.talent.anchor_candidates = [
            EventCandidate("DEFEAT", 0, "p2", ("attack", "p2", "小刀")),
        ]
        hook = _G5Adapter(ctrl)
        out = hook.should_override_candidates(
            p, state, ["move", "attack", "find", "forfeit"])
        self.assertEqual(out, ["move 医院", "forfeit"])

    def test_no_override_without_active_anchor(self) -> None:
        from engine.m9.talents.g5 import Ripple9
        state, p, ctrl = _world()
        ctrl._game_state = state
        p.talent = Ripple9("p1", state)
        hook = _G5Adapter(ctrl)
        self.assertIsNone(hook.should_override_candidates(
            p, state, ["move", "interact", "attack", "forfeit"]))


class T6AdapterTripTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_captain_candidate_with_kit_visits_police_location(self) -> None:
        from controllers.ai.m9_adapters import _T6Adapter
        from models.player import ArmorSlots
        from models.equipment import make_armor
        state, p, ctrl = _world()
        ctrl._game_state = state
        p.location = "医院"
        p.weapons.append(make_weapon("小刀"))
        p.armor = ArmorSlots()
        p.armor.outer.append(make_armor("盾牌"))
        state.m9_system.set_sp("p1", 1)
        state.m9_police.set_state_ref(state)
        state.m9_police.ensure_roster(initial_location="警察局")
        hook = _T6Adapter(ctrl)
        out = hook.should_override_candidates(
            p, state, ["move", "interact", "attack", "forfeit"])
        self.assertEqual(out, ["move 警察局", "forfeit"])

    def test_no_trip_without_kit_or_sp(self) -> None:
        from controllers.ai.m9_adapters import _T6Adapter
        state, p, ctrl = _world()
        ctrl._game_state = state
        p.location = "医院"
        state.m9_system.set_sp("p1", 1)
        state.m9_police.set_state_ref(state)
        state.m9_police.ensure_roster(initial_location="警察局")
        hook = _T6Adapter(ctrl)
        self.assertIsNone(hook.should_override_candidates(
            p, state, ["move", "forfeit"]))


if __name__ == "__main__":
    unittest.main()
