"""M9 G0 箭矢转化 + find 顺带拾取回归测试（v2.0 §2.8 风险换弹药）。

覆盖 actions/find_target.py 对 `receive_arrows` 字典返回值的正确消费
（Bug 1 回归：旧实现把整堆/整堆掉落清零，并把 dict 嵌进消息）：

1. 部分消耗：弹匣近满时只扣实际转化箭数，箭堆保留剩余；
2. 全额消耗：空弹匣取尽箭堆；
3. 零空间：弹匣已满不动箭堆（仅提示）；
4. 地面掉落路径（_pick_up_loot）同样只扣已转化箭数；
5. 非 G0 持弓玩家仍走 legacy `take` 分支（不受 G0 改造影响）。
"""
import unittest

from controllers.base import PlayerController

from engine import experiments
from engine.balance import get as bget
from engine.game_state import GameState
from models.player import Player

import actions.find_target as find_target
from engine.m9.gate import ensure_state_mechanisms
from engine.m9.talents.g0 import ShirokoTerror9


def _enable(*flags):
    experiments.reset()
    for f in flags:
        experiments.enable(f)


def _g0(key, default):
    return bget("m9_talents_extended", "g0", key, default=default)


class _RecordingController(PlayerController):
    """记录 choose 调用，返回预设选择序列（耗尽后回退首个选项）。"""

    def __init__(self, *choices):
        super().__init__()
        self.calls = []
        self._choices = list(choices)

    def choose(self, prompt, options, context=None):
        self.calls.append((prompt, list(options)))
        if self._choices:
            choice = self._choices.pop(0)
            return choice if choice in options else options[0]
        return options[0]

    def get_command(self, player, game_state, available_actions, context=None):
        return "forfeit"

    def choose_multi(self, prompt, options, max_count, min_count=0, context=None):
        return list(options)[:max_count]

    def confirm(self, prompt, context=None):
        return True


def _make(*pids):
    """创建 state + 玩家（hp20）+ G0 天赋；pids[0] 为 G0（同 test_m9_g3._make）。"""
    state = GameState()
    ensure_state_mechanisms(state)
    state.current_round = 1
    g0 = None
    others = []
    for i, pid in enumerate(pids):
        p = Player(pid, f"玩家{i}", controller=_RecordingController())
        state.add_player(p)
        p.max_hp = 20
        p.hp = 20
        p.location = "公园"
        if i == 0:
            g0 = p
        else:
            others.append(p)
    t = ShirokoTerror9(g0.player_id, state)
    g0.talent = t
    return state, g0, t, others


class ArrowPileFindTest(unittest.TestCase):
    """find 顺带拾取箭堆：部分/全额/零空间消耗。"""

    def setUp(self) -> None:
        # m4_gear 打开 find 拾取分支（engine.economy.m4_enabled）
        _enable("m9_rfc", "hp20", "m4_gear")

    def tearDown(self) -> None:
        experiments.reset()

    def test_partial_consumption_keeps_remaining_pile(self) -> None:
        """弹匣 29/30、箭堆 5：只转化 1 支箭，箭堆剩 4，消息无 dict 泄漏。"""
        state, g0, t, (p2,) = _make("p1", "p2")
        capacity = int(_g0("ar_magazine", 30))
        t.magazine = capacity - 1                     # 只够 1 发子弹
        state.arrow_piles = {"公园": 5}
        msg = find_target.execute(g0, "p2", state)
        self.assertEqual(state.arrow_piles["公园"], 4)  # 只扣实际转化箭数
        self.assertEqual(t.magazine, capacity)
        self.assertIn("1 发子弹", msg)                  # int 装填数，非 dict
        self.assertNotIn("{", msg)

    def test_full_consumption_empties_pile(self) -> None:
        """空弹匣 + 箭堆 2：全额转化，箭堆归零、弹匣按比例装弹。"""
        state, g0, t, (p2,) = _make("p1", "p2")
        ratio = int(_g0("arrow_to_bullet_ratio", 3))
        t.magazine = 0
        state.arrow_piles = {"公园": 2}
        msg = find_target.execute(g0, "p2", state)
        self.assertEqual(state.arrow_piles["公园"], 0)
        self.assertEqual(t.magazine, 2 * ratio)
        self.assertIn("2 支箭", msg)
        self.assertNotIn("{", msg)

    def test_full_magazine_leaves_pile_untouched(self) -> None:
        """弹匣已满：箭堆原样保留，仅提示，不产生误导性拾取消息。"""
        state, g0, t, (p2,) = _make("p1", "p2")
        capacity = int(_g0("ar_magazine", 30))
        t.magazine = capacity
        state.arrow_piles = {"公园": 5}
        msg = find_target.execute(g0, "p2", state)
        self.assertEqual(state.arrow_piles["公园"], 5)
        self.assertEqual(t.magazine, capacity)
        self.assertIn("弹匣已满", msg)
        self.assertNotIn("{", msg)


class GroundLootFindTest(unittest.TestCase):
    """find 顺带拾取地面掉落箭（_pick_up_loot）：只扣已转化箭数。"""

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20", "m4_gear")

    def tearDown(self) -> None:
        experiments.reset()

    def test_ground_loot_partial_consumption(self) -> None:
        """掉落箭 5、弹匣只够 1 发子弹：掉落剩 4，拾取描述无 dict。"""
        state, g0, t, (p2,) = _make("p1", "p2")
        capacity = int(_g0("ar_magazine", 30))
        t.magazine = capacity - 1
        state.ground_loot = {"公园": {"arrows": 5, "credits": 0,
                                      "weapons": [], "armor": [],
                                      "items": []}}
        msg = find_target.execute(g0, "p2", state)
        loot = state.ground_loot["公园"]
        self.assertEqual(loot["arrows"], 4)           # 只扣实际转化箭数
        self.assertEqual(t.magazine, capacity)
        self.assertIn("1 支箭→1 发子弹", msg)
        self.assertNotIn("{", msg)


class LegacyBowBranchTest(unittest.TestCase):
    """非 G0 持弓玩家：仍走 legacy `take` 分支（弹匣逻辑不介入）。"""

    def setUp(self) -> None:
        _enable("m4_gear")   # 弓是 m4_gear 起始装备；不开 m9_rfc

    def tearDown(self) -> None:
        experiments.reset()

    def test_legacy_bow_take_branch_untouched(self) -> None:
        """无 G0 天赋 + 持弓：走 take 分支拾取箭矢（max_arrows 6）。"""
        state = GameState()
        state.current_round = 1
        p1 = Player("p1", "弓手", controller=_RecordingController())
        p2 = Player("p2", "目标", controller=_RecordingController())
        state.add_player(p1)
        state.add_player(p2)
        p1.max_hp = 20
        p1.hp = 20
        p2.max_hp = 20
        p2.hp = 20
        p1.location = "公园"
        p2.location = "公园"
        self.assertTrue(p1.has_weapon("弓"))          # m4_gear 起始装备
        p1.arrows = 3                                  # 弓空间 6 − 3 = 3
        state.arrow_piles = {"公园": 5}
        msg = find_target.execute(p1, "p2", state)
        self.assertEqual(state.arrow_piles["公园"], 2)  # 5 − 3
        self.assertEqual(p1.arrows, 6)
        self.assertIn("🏹", msg)
        self.assertNotIn("{", msg)


if __name__ == "__main__":
    unittest.main()
