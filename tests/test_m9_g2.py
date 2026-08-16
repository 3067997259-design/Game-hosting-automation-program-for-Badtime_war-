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
    terminal_area_for, terminal_move_redirect,
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
        from engine.balance import get as bget
        shadow_hp = int(bget("m9_talents_extended", "g2", "shadow_hp", default=8))
        state, p, t = _make(sp=2, choices=("创建影身（即演 1 SP）",))
        msg, ok = t.execute_t0(p)
        self.assertTrue(ok)
        self.assertEqual(state.m9_system.get_sp("p1"), 1)
        shadow = t._shadow()
        self.assertIsNotNone(shadow)
        self.assertEqual(shadow.location, "商店")
        self.assertEqual(shadow.max_hp, shadow_hp)  # shadow_hp 键

    def test_shadow_inherits_spare_weapon(self) -> None:
        """裁决 A：影身继承光身一件备用实装武器（真实转移）；主战保留。"""
        from models.equipment import make_weapon
        state, p, t = _make(sp=2, choices=("创建影身（即演 1 SP）",))
        baton = make_weapon("警棍")
        knife = make_weapon("小刀")
        p.weapons = [baton, knife]
        msg, ok = t.execute_t0(p)
        self.assertTrue(ok)
        shadow = t._shadow()
        self.assertIsNotNone(shadow)
        # 一件转交影身、一件（主战）留在光身（两武器伤害可能相等，
        # 只断言分配而非具体归属；影身自带拳击保底不计入）
        inherited = [w for w in shadow.weapons if w.name != "拳击"]
        owner_real = [w for w in p.weapons if w.name != "拳击"]
        self.assertEqual(len(inherited), 1)
        self.assertEqual(len(owner_real), 1)
        combined = sorted([inherited[0].name, owner_real[0].name])
        self.assertEqual(combined, sorted(["小刀", "警棍"]))

    def test_shadow_weapon_returned_on_dissipation(self) -> None:
        from models.equipment import make_weapon
        state, p, t = _make(sp=2, choices=("创建影身（即演 1 SP）",))
        p.weapons = [make_weapon("警棍"), make_weapon("小刀")]
        t.execute_t0(p)
        shadow = t._shadow()
        self.assertIsNotNone(shadow)
        t.dissipate(shadow)
        self.assertEqual(sorted(w.name for w in p.weapons), ["小刀", "警棍"])

    def test_shadow_no_transfer_with_single_weapon(self) -> None:
        """单件实装不转移：不缴械光身（影身仅剩拳击保底）。"""
        from models.equipment import make_weapon
        state, p, t = _make(sp=2, choices=("创建影身（即演 1 SP）",))
        p.weapons = [make_weapon("小刀")]
        t.execute_t0(p)
        shadow = t._shadow()
        self.assertIsNotNone(shadow)
        # 影身自带拳击保底（裁决 A+ 补全），但不拿走光身唯一实装
        self.assertEqual([w.name for w in shadow.weapons], ["拳击"])
        self.assertEqual(len(p.weapons), 1)

    def test_shadow_inherits_all_spares_and_half_credits(self) -> None:
        """裁决 A+：影身继承全部备用武器（光身留主战）+ 半数信用点；
        消散时武器与信用点贷款归还。"""
        from models.equipment import make_weapon
        state, p, t = _make(sp=2, choices=("创建影身（即演 1 SP）",))
        gauss = make_weapon("高斯步枪")
        baton = make_weapon("警棍")
        knife = make_weapon("小刀")
        p.weapons = [gauss, baton, knife]
        p.credits = 10
        t.execute_t0(p)
        shadow = t._shadow()
        self.assertIsNotNone(shadow)
        # 伤害最高的一件（主战）留在光身，其余全部给影身
        self.assertEqual([w.name for w in p.weapons],
                         [max((gauss, baton, knife),
                              key=lambda w: w.base_damage).name])
        inherited = [w for w in shadow.weapons if w.name != "拳击"]
        self.assertEqual(sorted(w.name for w in inherited),
                         sorted(w.name for w in (gauss, baton, knife)
                                if w is not max((gauss, baton, knife),
                                                key=lambda w: w.base_damage)))
        # 半数信用点转移
        self.assertEqual(p.credits, 5)
        self.assertEqual(shadow.credits, 5)
        # 消散：武器与信用点归还
        t.dissipate(shadow)
        self.assertEqual(sorted(w.name for w in p.weapons),
                         sorted([w.name for w in (gauss, baton, knife)]))
        self.assertEqual(p.credits, 10)

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


def _area_scene():
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


class TerminalAreaEffectsTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_vulnerability_boosts_damage_in_area(self) -> None:
        from engine.m9.combat import _terminal_area_of
        from engine.balance import get as _bget
        state, t, actor, other = _area_scene()
        area = _terminal_area_of(state, other)
        self.assertIsNotNone(area)
        self.assertEqual(area.vulnerability(),
                         _bget("m9_talents_extended", "g2",
                               "terminal_vulnerability", default=0))

    def test_damage_sharing_conserves_total(self) -> None:
        from engine.m9.combat import _damage_distribution
        state, t, actor, other = _area_scene()
        other.hp = 10
        distribution = _damage_distribution(state, other, 8)
        by_id = {member.player_id: amount for member, amount in distribution}
        # R19：ratio=0.2 → S=floor(8×0.2)=1；4 成员余 1 给 id 序首位（影身），
        # 原目标 p2 再 +8-1=7 → shadow 1 / p1 0 / p2 7，总量守恒
        self.assertEqual(sum(by_id.values()), 8)
        self.assertEqual(by_id[other.player_id], 7)
        self.assertEqual(by_id[actor.player_id], 1)
        self.assertEqual(by_id["p1"], 0)

    def test_listener_tick_grants_arc_once(self) -> None:
        state, t, actor, other = _area_scene()
        for r in range(1, 5):
            state.current_round = r
            t.on_round_end(r)
        self.assertTrue(t.terminal_area.arc_granted)
        self.assertGreaterEqual(t.terminal_area.witnessed_ticks, 2)


class TerminalSuppressionRedirectTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_suppress_grant_consumes_use_once(self) -> None:
        state, t, actor, other = _area_scene()
        m9 = state.m9_system
        # R12：terminal_suppression_uses 1→2
        self.assertTrue(t.suppress_grant("p2", m9))
        self.assertFalse(t.terminal_area.suppression_used())  # 还剩 1 次
        self.assertTrue(t.suppress_grant("p2", m9))
        self.assertTrue(t.terminal_area.suppression_used())    # 耗尽
        self.assertFalse(t.suppress_grant("p2", m9))  # 次数耗尽
        self.assertFalse(t.suppress_grant("p1", m9))  # G2 自己不可压制

    def test_suppress_grant_requires_same_location(self) -> None:
        state, t, actor, other = _area_scene()
        far = Player("p9", "远者", controller=ForfeitController())
        far.location = "医院"
        state.add_player(far)
        self.assertFalse(t.suppress_grant("p9", state.m9_system))

    def test_move_redirect_always_when_chance_1(self) -> None:
        from engine.m9.talents.g2 import TerminalArea
        state, t, actor, other = _area_scene()
        # 强制命中：rng 恒 0
        dest = terminal_move_redirect(state, other, "医院", rng=lambda: 0.0)
        self.assertEqual(dest, "商店")  # 歌者位置

    def test_move_redirect_never_when_chance_0(self) -> None:
        state, t, actor, other = _area_scene()
        t.terminal_area.move_redirect_chance = lambda: 0.0  # 类型不符——直接改键
        # 用 rng 恒 1（> 0.5 不偏转）
        dest = terminal_move_redirect(state, other, "医院", rng=lambda: 1.0)
        self.assertIsNone(dest)

    def test_move_redirect_skips_singer_destination(self) -> None:
        state, t, actor, other = _area_scene()
        dest = terminal_move_redirect(state, other, "商店", rng=lambda: 0.0)
        self.assertIsNone(dest)  # 目的地已是歌者位置 → 不偏转


class SpotlightPoemRegressionTest(unittest.TestCase):
    """追光诗消费回归：_apply_spotlight_focus 必须可执行（bget 局部导入）。"""

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_apply_spotlight_consumes_and_adds_bonus(self) -> None:
        from engine.m9.combat import _apply_spotlight_focus
        from engine.m9.resolution import HitResolution
        state, p, t = _make()
        t.m9_poem_markers = {"spotlight_focus": True}
        attacker = SimpleNamespace(
            _m9_shadow_actor=True, owner_pid="p1", player_id="g2:shadow")
        hit = HitResolution(damage=3.0)
        _apply_spotlight_focus(state, attacker, hit)
        self.assertGreater(hit.damage, 3.0)
        self.assertNotIn("spotlight_focus", t.m9_poem_markers)


if __name__ == "__main__":
    unittest.main()
