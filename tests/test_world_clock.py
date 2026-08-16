"""M5 白昼世界时钟测试：阶段边界 / 阶段效果 / 限量营业 / 嫌疑 / 击杀掉落 / v1 回归。"""
import random
import unittest

from engine import experiments
from engine import world_clock
from engine.game_state import GameState
from models.player import Player
from controllers.forfeit_controller import ForfeitController


def _state(num_players=6):
    state = GameState()
    for i in range(num_players):
        p = Player(f"p{i+1}", f"玩家{i+1}", controller=ForfeitController())
        p.is_awake = True
        p.location = "商店"
        state.add_player(p)
    return state


class PhaseBoundaryTest(unittest.TestCase):

    def setUp(self):
        experiments.reset()
        experiments.enable("m5_clock")

    def tearDown(self):
        experiments.reset()

    def test_six_player_segments(self):
        """6 人局段长 = 6+6 = 12：黎明1-12 / 白昼13-24 / 黄昏25-36 / 终焉37+。"""
        state = _state(6)
        cases = [(1, world_clock.DAWN), (12, world_clock.DAWN),
                 (13, world_clock.DAY), (24, world_clock.DAY),
                 (25, world_clock.DUSK), (36, world_clock.DUSK),
                 (37, world_clock.APOCALYPSE), (100, world_clock.APOCALYPSE)]
        for rnd, expected in cases:
            state.current_round = rnd
            self.assertEqual(world_clock.current_phase(state), expected,
                             f"轮 {rnd} 应为 {expected}")

    def test_two_player_segments(self):
        """2 人局段长 = 6+2 = 8：黄昏从 17 起。"""
        state = _state(2)
        state.current_round = 16
        self.assertEqual(world_clock.current_phase(state), world_clock.DAY)
        state.current_round = 17
        self.assertEqual(world_clock.current_phase(state), world_clock.DUSK)

    def test_disabled_always_dawn(self):
        experiments.reset()
        state = _state(6)
        state.current_round = 50
        self.assertEqual(world_clock.current_phase(state), world_clock.DAWN)

    def test_phase_value_read(self):
        self.assertEqual(
            world_clock.phase_value(world_clock.DUSK, "global_damage_bonus"), 1)
        self.assertEqual(
            world_clock.phase_value(world_clock.APOCALYPSE, "end_of_round_true_damage"), 2)


class PhaseEffectTest(unittest.TestCase):
    """阶段效果：构造指定轮数直接断言修正生效。"""

    def setUp(self):
        experiments.reset()
        for f in ("k_initiative", "hp20", "m3_accuracy", "m4_gear", "m5_clock"):
            experiments.enable(f)

    def tearDown(self):
        experiments.reset()

    def test_dusk_damage_bonus(self):
        """黄昏全体伤害 +1。"""
        from combat.damage_resolver import resolve_damage
        from models.equipment import make_weapon
        state = _state(6)
        state.current_round = 25  # 黄昏
        a, t = state.get_player("p1"), state.get_player("p2")
        result = resolve_damage(a, t, make_weapon("小刀"), state)
        self.assertEqual(result["final_damage"], 5)  # 小刀 4 + 黄昏 1

    def test_dawn_no_bonus(self):
        from combat.damage_resolver import resolve_damage
        from models.equipment import make_weapon
        state = _state(6)
        state.current_round = 5  # 黎明
        a, t = state.get_player("p1"), state.get_player("p2")
        result = resolve_damage(a, t, make_weapon("小刀"), state)
        self.assertEqual(result["final_damage"], 4)

    def test_apocalypse_true_damage(self):
        """终焉每轮末全体 −2 真伤。"""
        from engine.round_manager import RoundManager
        state = _state(6)
        state.current_round = 40  # 终焉
        rm = RoundManager(state)
        before = {pid: state.get_player(pid).hp for pid in state.player_order}
        rm._process_apocalypse_damage()
        for pid in state.player_order:
            self.assertEqual(state.get_player(pid).hp, before[pid] - 2)


class RationingSuspicionTest(unittest.TestCase):
    """限量营业 + 白昼首攻嫌疑。"""

    def setUp(self):
        experiments.reset()
        for f in ("k_initiative", "hp20", "m3_accuracy", "m4_gear", "m5_clock"):
            experiments.enable(f)

    def tearDown(self):
        experiments.reset()

    def test_rationing_blocks_second_take(self):
        from cli.validator import validate_interact
        state = _state(6)
        state.current_round = 13  # 白昼（限量营业）
        state._rationing_used = set()
        p = state.get_player("p1")
        p.location = "商店"
        p.credits = 20
        # 首次打工放行
        ok1, _ = validate_interact(p, "打工", state)
        self.assertTrue(ok1)
        # 标记本轮该地点该条目已用
        state._rationing_used.add(("商店", "打工"))
        ok2, reason = validate_interact(p, "打工", state)
        self.assertFalse(ok2)
        self.assertIn("限量", reason)

    def test_first_attack_suspicion_not_crime(self):
        from engine.round_manager import RoundManager
        state = _state(6)
        state.current_round = 13  # 白昼
        state._first_attack_done = set()
        rm = RoundManager(state)
        attacker = state.get_player("p1")
        # 伪造一条本轮成功攻击事件
        state.log_event("attack", attacker="p1",
                        result={"success": True, "final_damage": 4})
        rm._check_attack_crime(attacker)
        self.assertTrue(attacker.is_suspect)
        self.assertFalse(getattr(attacker, "is_criminal", False))


class KillLootTest(unittest.TestCase):
    """击杀掉落：死者 credits/箭/装备入地面，find 拾取。"""

    def setUp(self):
        experiments.reset()
        for f in ("k_initiative", "hp20", "m3_accuracy", "m4_gear", "m5_clock"):
            experiments.enable(f)

    def tearDown(self):
        experiments.reset()

    def test_loot_drops_on_death(self):
        state = _state(6)
        state.current_round = 13  # 白昼（kill_drop 启用）
        victim = state.get_player("p2")
        victim.location = "商店"
        victim.credits = 7
        victim.arrows = 2
        victim.hp = 0  # 死亡
        state.drop_loot_on_death(victim)
        pile = state.ground_loot.get("商店")
        self.assertIsNotNone(pile)
        self.assertEqual(pile["credits"], 7)
        self.assertEqual(pile["arrows"], 2)
        self.assertEqual(victim.credits, 0)

    def test_loot_idempotent(self):
        state = _state(6)
        state.current_round = 13
        victim = state.get_player("p2")
        victim.location = "商店"
        victim.credits = 5
        victim.hp = 0
        state.drop_loot_on_death(victim)
        state.drop_loot_on_death(victim)  # 重复
        self.assertEqual(state.ground_loot["商店"]["credits"], 5)

    def test_no_drop_in_dawn(self):
        state = _state(6)
        state.current_round = 5  # 黎明（kill_drop 未启用）
        victim = state.get_player("p2")
        victim.location = "商店"
        victim.credits = 5
        victim.hp = 0
        state.drop_loot_on_death(victim)
        self.assertNotIn("商店", state.ground_loot)

    def test_find_picks_up_loot(self):
        from actions.find_target import execute as find_exec
        state = _state(6)
        state.current_round = 13
        finder = state.get_player("p1")
        finder.location = "商店"
        finder.credits = 0
        target = state.get_player("p2")
        target.location = "商店"
        state.ground_loot = {"商店": {"credits": 9, "arrows": 0,
                                       "items": [], "weapons": []}}
        find_exec(finder, "p2", state)
        self.assertEqual(finder.credits, 9)


class PoliceFalloffTest(unittest.TestCase):
    """警察分级坠落：黄昏撤保护、终焉全停。"""

    def setUp(self):
        experiments.reset()
        for f in ("k_initiative", "hp20", "m4_gear", "m5_clock"):
            experiments.enable(f)

    def tearDown(self):
        experiments.reset()

    def test_dusk_removes_protection(self):
        from engine.police_system import PoliceEngine
        state = _state(6)
        pe = PoliceEngine(state)
        state.police_engine = pe
        state.current_round = 25  # 黄昏
        # 黄昏阶段保护阈值恒 0（不论是否被保护）
        self.assertEqual(pe.get_protection_threshold("p1"), 0.0)

    def test_apocalypse_disables_enforcement(self):
        from engine.police_system import PoliceEngine
        state = _state(6)
        pe = PoliceEngine(state)
        state.police_engine = pe
        state.current_round = 40  # 终焉
        self.assertEqual(pe.process_end_of_round(), [])


if __name__ == "__main__":
    unittest.main()
