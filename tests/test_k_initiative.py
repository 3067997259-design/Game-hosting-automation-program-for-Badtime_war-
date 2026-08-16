"""M1 K 常量行动制测试：先攻判定 / 配额坐牢 / 保底退役 / 借机攻击。

全部行为挂 experiment k_initiative；setUp/tearDown 负责开关收尾，
保证不污染同进程其他测试（v1 路径）。
"""
import random
import unittest

from engine import experiments
from engine.game_state import GameState
from engine.round_manager import RoundManager
from models.player import Player
from controllers.forfeit_controller import ForfeitController


def _make_state(num_players: int = 4) -> tuple:
    state = GameState()
    for i in range(num_players):
        pid = f"p{i+1}"
        p = Player(pid, f"玩家{i+1}", controller=ForfeitController())
        p.is_awake = True
        p.location = "商店"
        state.add_player(p)
    rm = RoundManager(state)
    return state, rm


class InitiativePhaseTest(unittest.TestCase):

    def setUp(self) -> None:
        experiments.reset()

    def tearDown(self) -> None:
        experiments.reset()

    def test_v1_path_untouched_when_disabled(self) -> None:
        """开关关闭：R1 走 D4 旧路径，d4_results 有值、initiative_results 无。"""
        state, rm = _make_state()
        random.seed(1)
        rm._phase_r1()
        self.assertTrue(state.d4_results)
        self.assertFalse(hasattr(state, "initiative_results")
                         and state.initiative_results)

    def test_k_quota_one_sitout(self) -> None:
        """开关开启：K = 存活数 − 1，恰一人坐牢，d4_results 保持空。"""
        experiments.enable("k_initiative")
        state, rm = _make_state(4)
        random.seed(2)
        rm._phase_r1()
        self.assertEqual(len(state.round_winners), 3)
        self.assertFalse(state.d4_results)
        self.assertEqual(len(state.initiative_results), 4)

    def test_order_follows_initiative_desc(self) -> None:
        """行动序 = 先攻总值降序。"""
        experiments.enable("k_initiative")
        state, rm = _make_state(4)
        random.seed(3)
        rm._phase_r1()
        totals = [state.initiative_results[pid][2] for pid in state.round_winners]
        self.assertEqual(totals, sorted(totals, reverse=True))
        # 坐牢者总值不高于队尾行动者（tiebreak 允许同分）
        sitout = [pid for pid in state.initiative_results
                  if pid not in state.round_winners]
        self.assertEqual(len(sitout), 1)
        self.assertLessEqual(state.initiative_results[sitout[0]][2], totals[-1])

    def test_tiebreak_deterministic(self) -> None:
        """同种子两遍：行动序完全一致（补掷与排序键的确定性）。"""
        experiments.enable("k_initiative")
        orders = []
        for _ in range(2):
            state, rm = _make_state(5)
            random.seed(7)
            rm._phase_r1()
            orders.append(list(state.round_winners))
        self.assertEqual(orders[0], orders[1])

    def test_r2_skipped(self) -> None:
        """K 模式下 R2 不再重排 round_winners。"""
        experiments.enable("k_initiative")
        state, rm = _make_state(4)
        random.seed(4)
        rm._phase_r1()
        before = list(state.round_winners)
        rm._phase_r2()
        self.assertEqual(state.round_winners, before)

    def test_no_action_streak_retired(self) -> None:
        """K 模式：R3 后坐牢者 acted_this_round=False（喂 G6），但 streak 不自增。"""
        experiments.enable("k_initiative")
        state, rm = _make_state(3)
        random.seed(5)
        for p in state.players.values():
            p.acted_this_round = False
        rm._phase_r1()
        rm._phase_r3()
        for pid in state.player_order:
            p = state.get_player(pid)
            self.assertEqual(p.no_action_streak, 0,
                             f"{pid} 的 streak 在 K 模式下不应自增")
        sitout = [pid for pid in state.player_order
                  if pid not in state.round_winners]
        for pid in sitout:
            self.assertFalse(state.get_player(pid).acted_this_round)

    def test_initiative_bonus_aggregates_both_dice(self) -> None:
        """get_initiative_bonus = d4 加成 + d6 加成。"""
        state, _ = _make_state(2)
        p = state.get_player("p1")
        p._duet_d4_bonus = True   # +1（自消耗）
        p._duet_d6_bonus = True   # +1（自消耗）
        self.assertEqual(p.get_initiative_bonus(), 2)
        self.assertEqual(p.get_initiative_bonus(), 0)  # flag 已消耗


class OpportunityAttackTest(unittest.TestCase):

    def setUp(self) -> None:
        experiments.reset()
        experiments.enable("k_initiative")
        self.state, _ = _make_state(2)
        self.mover = self.state.get_player("p1")
        self.opp = self.state.get_player("p2")
        self.state.current_round = 5
        # 建立双向交战
        self.state.markers.add_relation("p1", "ENGAGED_WITH", "p2")
        self.state.markers.add_relation("p2", "ENGAGED_WITH", "p1")

    def tearDown(self) -> None:
        experiments.reset()

    def _move(self) -> str:
        from actions.move import execute
        return execute(self.mover, "医院", self.state)

    def test_aoo_triggers_on_engaged_move(self) -> None:
        self._move()
        aoo = [e for e in self.state.event_log
               if e.get("type") == "opportunity_attack"]
        self.assertEqual(len(aoo), 1)
        self.assertEqual(aoo[0]["attacker"], "p2")
        self.assertEqual(self.opp._aoo_used_round, 5)

    def test_aoo_once_per_round(self) -> None:
        self.opp._aoo_used_round = 5  # 本轮已用过
        self._move()
        aoo = [e for e in self.state.event_log
               if e.get("type") == "opportunity_attack"]
        self.assertEqual(len(aoo), 0)

    def test_no_aoo_without_engagement(self) -> None:
        self.state.markers.remove_relation("p1", "ENGAGED_WITH", "p2")
        self.state.markers.remove_relation("p2", "ENGAGED_WITH", "p1")
        self._move()
        aoo = [e for e in self.state.event_log
               if e.get("type") == "opportunity_attack"]
        self.assertEqual(len(aoo), 0)

    def test_no_aoo_when_disabled(self) -> None:
        experiments.disable("k_initiative")
        self._move()
        aoo = [e for e in self.state.event_log
               if e.get("type") == "opportunity_attack"]
        self.assertEqual(len(aoo), 0)

    def test_forced_move_exempt(self) -> None:
        self.mover._hexagram_forced_move = True
        self._move()
        aoo = [e for e in self.state.event_log
               if e.get("type") == "opportunity_attack"]
        self.assertEqual(len(aoo), 0)

    def test_mover_stunned_aborts_move(self) -> None:
        """mover 被打到眩晕/死亡 → 移动中止留在原地。"""
        self.mover.hp = 0.5  # 拳击 0.5 即可打倒
        result = self._move()
        self.assertEqual(self.mover.location, "商店")
        self.assertIn("中止", result)


if __name__ == "__main__":
    unittest.main()
