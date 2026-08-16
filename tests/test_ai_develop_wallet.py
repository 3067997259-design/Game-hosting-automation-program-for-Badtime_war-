"""AI 发育钱包（D3 接地）回归测试：m4 起信用点取代凭证，无限打工循环修复。

- m4/m9：credits 驱动——没钱才打工、够价即购买、voucher 需求在 credits≥1 时满足；
- legacy（无 m4_gear）：vouchers 行为逐字节保留。
"""
import unittest

from engine import experiments
from engine.game_state import GameState
from models.player import Player
from models.equipment import make_weapon
from controllers.forfeit_controller import ForfeitController
from controllers.ai.game_query import GameQuery
from controllers.ai.minds.develop_mind import DevelopMind
from controllers.ai.strategies.registry import create_strategy


def _make(flags):
    experiments.reset()
    for flag in flags:
        experiments.enable(flag)
    state = GameState()
    p = Player("p1", "AI", controller=ForfeitController())
    p.is_awake = True
    p.location = "商店"
    p.hp = p.max_hp = 20
    state.add_player(p)
    p2 = Player("p2", "路人", controller=ForfeitController())
    p2.is_awake = True
    p2.location = "医院"
    p2.hp = p2.max_hp = 20
    state.add_player(p2)
    return state, p


class DevelopWalletTest(unittest.TestCase):

    def tearDown(self) -> None:
        experiments.reset()

    def _assess(self, state, p):
        mind = DevelopMind(debug_name="AI", query=GameQuery())
        strategy = create_strategy("balanced")
        snap = mind.assess(p, state, strategy)
        return snap.data

    def test_m4_no_credits_works_first(self) -> None:
        state, p = _make(["m4_gear", "hp20"])
        p.credits = 0
        data = self._assess(state, p)
        self.assertIn("voucher", [n for n, _ in data["unmet_needs"]])
        self.assertIn("interact 打工", data["current_location_actions"])

    def test_m4_credits_satisfy_voucher_need_and_stop_work(self) -> None:
        state, p = _make(["m4_gear", "hp20"])
        p.credits = 2
        data = self._assess(state, p)
        self.assertNotIn("voucher", [n for n, _ in data["unmet_needs"]])
        self.assertNotIn("interact 打工", data["current_location_actions"])

    def test_m4_armor_price_gates_purchase(self) -> None:
        """陶瓷护甲价 3：credits=2 时买不起（该提供者被过滤，不再反复尝试）；
        credits=3 时可买。voucher 需求已满足 → 不再无限打工。"""
        state, p = _make(["m4_gear", "hp20"])
        p.weapons.append(make_weapon("小刀"))
        p.credits = 2
        data = self._assess(state, p)
        cmds = data["current_location_actions"]
        self.assertNotIn("interact 陶瓷护甲", cmds)
        self.assertNotIn("interact 打工", cmds)  # voucher 已满足，不能无限打工
        p.credits = 3
        data = self._assess(state, p)
        cmds = data["current_location_actions"]
        self.assertIn("interact 陶瓷护甲", cmds)
        self.assertNotIn("interact 打工", cmds)

    def test_legacy_voucher_behavior_unchanged(self) -> None:
        state, p = _make(["hp20"])
        p.vouchers = 0
        data = self._assess(state, p)
        self.assertIn("interact 打工", data["current_location_actions"])
        self.assertIn("voucher", [n for n, _ in data["unmet_needs"]])
        p.vouchers = 1
        data = self._assess(state, p)
        self.assertNotIn("interact 打工", data["current_location_actions"])


if __name__ == "__main__":
    unittest.main()
