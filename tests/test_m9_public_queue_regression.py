"""M9 公演队列纪律回归测试（handoff 项「公演失败时队列不变」）。

适配器 T0 公演路径只能消费 R0 已固化的公演 holder，禁止临时报名/改写队列。
空队列 + 已固化 None holder 时，T0 不得再提供注定失败的「公演」分支：

1. T6 GoodCitizen9（联防整备）：T0 无公演位 → 不询问公演/即演，直接走即演，
   队列/holder 不被污染，SP 只按即演 −1、账本发行即演 grant、装备移交；
2. G3 Mythland9（展开固有结界）：T0 无公演位 → 跳过「展开固有结界」分支直接
   进入投影魔术；投影失败也只在消费前取消：队列/SP/账本/结界状态全不变。
"""
import unittest

from controllers.base import PlayerController

from engine import experiments
from engine.game_state import GameState
from models.equipment import make_weapon
from models.player import Player

from engine.m9.gate import ensure_state_mechanisms
from engine.m9.talents.g3 import Mythland9
from engine.m9.talents.t6 import GoodCitizen9


def _enable(*flags):
    experiments.reset()
    for f in flags:
        experiments.enable(f)


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


def _make(*pids, talent_cls=None):
    """创建 state + 玩家（hp20）；pids[0] 挂 talent_cls（None = 不挂）。"""
    state = GameState()
    ensure_state_mechanisms(state)
    state.current_round = 1
    main = None
    others = []
    for i, pid in enumerate(pids):
        p = Player(pid, f"玩家{i}", controller=_RecordingController())
        state.add_player(p)
        p.max_hp = 20
        p.hp = 20
        p.location = "公园"
        if i == 0:
            main = p
        else:
            others.append(p)
    if talent_cls is not None:
        main.talent = talent_cls(main.player_id, state)
    return state, main, others


class T6PublicQueueRegressionTest(unittest.TestCase):
    """T6 公演联防整备：无 holder 时公演在消费前取消，队列不被污染。"""

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_t6_without_public_seat_improvises_without_queue_mutation(self) -> None:
        state, t6, _ = _make("t", talent_cls=GoodCitizen9)
        m9 = state.m9_system
        m9.set_sp("t", 2)
        # R0：空队列 → 本轮公演 holder 固定为 None（T0 只消费该 holder）
        self.assertIsNone(m9.allocate_public_slot(1))
        # 同地点存活警察 + 白名单装备 → 公演路径可见
        station = state.m9_police
        roster = station.ensure_roster()
        roster[0].location = "公园"
        baton = make_weapon("警棍")
        t6.weapons.append(baton)
        option = t6.talent.get_t0_option(t6)
        self.assertIsNotNone(option)
        self.assertEqual(option["m9_kind"], "t6_equip")
        # T0：holder=None → 不询问公演/即演，直接走即演（不再提供注定失败的公演分支）
        state.current_phase = "r3_actions"
        t6.controller = _RecordingController("警棍")
        msg, ok = t6.talent.execute_t0(t6)
        self.assertTrue(ok, msg)
        self.assertIn("整备", msg)
        # 队列/holder 不被污染：无报名、无公演分配；演出类型为即演
        self.assertEqual(m9.queue.members(), [])
        self.assertIsNone(m9._public_holder_by_round.get(1))
        self.assertEqual(m9._performance_kind, "improvise")
        # 即演只 −1 SP；发行一个即演 grant；装备已移交警察
        self.assertEqual(m9.get_sp("t"), 1)
        self.assertEqual(len(list(m9.ledger._grants.values())), 1)
        self.assertNotIn(baton, t6.weapons)
        self.assertEqual(roster[0].weapon_name, "警棍")


class G3PublicQueueRegressionTest(unittest.TestCase):
    """G3 展开固有结界：无 holder 时 _expand_barrier 在消费前取消。"""

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_g3_without_public_seat_skips_expand_without_queue_mutation(self) -> None:
        state, g3, (p2,) = _make("g3", "p2", talent_cls=Mythland9)
        t = g3.talent
        m9 = state.m9_system
        m9.set_sp("g3", 2)
        # R0：空队列 → 本轮公演 holder 固定为 None
        self.assertIsNone(m9.allocate_public_slot(1))
        # R0 报名阶段：SP=2 → 公演路径（展开固有结界）可见
        option = t.get_t0_option(g3)
        self.assertIsNotNone(option)
        self.assertEqual(option["m9_kind"], "g3_barrier_expand")
        # T0：holder=None → 跳过「展开固有结界」分支，直接进入投影魔术
        # （不再把注定失败的「公演」分支交给玩家选）；投影无合法目标时消费前取消
        state.current_phase = "r3_actions"
        msg, ok = t.execute_t0(g3)
        self.assertFalse(ok)
        self.assertIn("无合法目标", msg)
        # 队列/holder 不变：无报名、无污染；结界未展开
        self.assertEqual(m9.queue.members(), [])
        self.assertIsNone(m9._public_holder_by_round.get(1))
        # 无 SP/魔力消费、无 grant 发行、无捕捉/结界状态
        self.assertEqual(m9.get_sp("g3"), 2)
        self.assertEqual(list(m9.ledger._grants.values()), [])
        self.assertFalse(t.barrier_active)
        self.assertEqual(t.captured, [])


if __name__ == "__main__":
    unittest.main()
