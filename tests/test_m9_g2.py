"""M9 G2 光影双身机制单测（阶段 6）：影身创建/消散归还/终曲承诺永久锁死/
终曲区域易伤与伤害共享/听众 tick/代理槽解析。"""
import unittest
from types import SimpleNamespace

from engine import experiments
from engine.game_state import GameState
from models.player import Player
from controllers.base import PlayerController
from controllers.forfeit_controller import ForfeitController

from engine.m9.gate import ensure_state_mechanisms
from engine.m9.talents.g2 import (
    Hologram9, ShadowActor, TerminalArea, shadow_actor_for,
)
from engine.m9.talents.g2 import shadow_actor_id


def _enable(*flags):
    experiments.reset()
    for f in flags:
        experiments.enable(f)


class _FixedChoiceController(PlayerController):
    def __init__(self, *choices):
        self._choices = list(choices)
        self._i = 0

    def _next(self, options):
        if self._i < len(self._choices):
            c = self._choices[self._i]
            self._i += 1
            return c if c in options else options[0]
        return options[0]

    def get_command(self, player, game_state, available_actions, context=None):
        return "forfeit"

    def choose(self, prompt, options, context=None):
        return self._next(options)

    def choose_multi(self, prompt, options, max_count, min_count=0, context=None):
        return options[:max_count]

    def confirm(self, prompt, context=None):
        return True


def _make(sp=2, choices=()):
    state = GameState()
    ensure_state_mechanisms(state)
    p = Player("p1", "G2", controller=_FixedChoiceController(*choices))
    p.location = "商店"
    state.add_player(p)
    t = Hologram9("p1", state)
    p.talent = t
    state.m9_system.set_sp("p1", sp)
    return state, p, t


class ShadowLifecycleTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_create_shadow_improvise(self) -> None:
        state, p, t = _make(sp=2, choices=("创建影身（即演 1 SP）",))
        msg, ok = t.execute_t0(p)
        self.assertTrue(ok)
        self.assertEqual(state.m9_system.get_sp("p1"), 1)
        shadow = t._shadow()
        self.assertIsNotNone(shadow)
        self.assertEqual(shadow.location, "商店")
        self.assertEqual(shadow.max_hp, 8)  # shadow_hp 键

    def test_dissipate_returns_item_and_removes_actor(self) -> None:
        state, p, t = _make()
        item = SimpleNamespace(name="小刀")
        actor = t._create_shadow(p)
        actor.held_items.append(item)
        t.dissipate(actor)
        self.assertIsNone(t._shadow())
        self.assertIn(item, p.items)  # 归还光身
        self.assertNotIn(actor.actor_id, state.m9_shadows)

    def test_shadow_lethal_is_not_player_death(self) -> None:
        state, p, t = _make()
        actor = t._create_shadow(p)
        actor.hp = 0
        kind = t.m9_on_lethal(actor, None, "normal")
        self.assertEqual(kind, "g2_shadow_dissipated")
        self.assertIsNone(t._shadow())
        self.assertTrue(p.is_alive())  # 光身无碍

    def test_terminal_commit_permanently_locks_eligibility(self) -> None:
        state, p, t = _make(sp=2)
        actor = t._create_shadow(p)
        t._commit_terminal(p, actor)
        self.assertFalse(t.shadow_creation_eligible)
        self.assertTrue(actor.is_terminal_singer)
        self.assertIsNotNone(t.terminal_area)
        # 不可逆：再走创建路径也失败
        msg, ok = t.execute_t0(p)
        self.assertFalse(ok)

    def test_shadow_actor_resolution(self) -> None:
        state, p, t = _make()
        actor = t._create_shadow(p)
        self.assertEqual(shadow_actor_for(state, actor.actor_id), actor)
        self.assertIsNone(shadow_actor_for(state, "p1"))


class TerminalAreaEffectsTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def _area_scene(self):
        state = GameState()
        ensure_state_mechanisms(state)
        g2 = Player("p1", "G2", controller=ForfeitController())
        g2.location = "商店"
        state.add_player(g2)
        t = Hologram9("p1", state)
        g2.talent = t
        actor = t._create_shadow(g2)
        t._commit_terminal(g2, actor)
        other = Player("p2", "路人", controller=ForfeitController())
        other.location = "商店"
        other.hp = 10
        state.add_player(other)
        return state, t, actor, other

    def test_vulnerability_boosts_damage_in_area(self) -> None:
        from engine.m9.combat import _terminal_area_of
        state, t, actor, other = self._area_scene()
        area = _terminal_area_of(state, other)
        self.assertIsNotNone(area)
        self.assertEqual(area.vulnerability(), 1)

    def test_damage_sharing_conserves_total(self) -> None:
        from engine.m9.combat import _share_damage
        state, t, actor, other = self._area_scene()
        other.hp = 10
        remaining = _share_damage(state, other, 8)
        # 共享集 3 成员（光身 p1/歌者影身/路人 p2），S=8 → 3/3/2，总量守恒
        self.assertEqual(remaining, 0)
        self.assertEqual(other.hp, 8)
        self.assertEqual(actor.hp, 5)
        self.assertEqual(state.get_player("p1").hp, 17)

    def test_listener_tick_grants_arc_once(self) -> None:
        state, t, actor, other = self._area_scene()
        for r in range(1, 5):
            state.current_round = r
            t.on_round_end(r)
        self.assertTrue(t.terminal_area.arc_granted)
        self.assertGreaterEqual(t.terminal_area.witnessed_ticks, 3)


if __name__ == "__main__":
    unittest.main()
