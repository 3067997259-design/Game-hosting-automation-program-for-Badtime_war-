"""M9 G3「无限剑制」结界移动限制测试（固有结界 RFC v0.2 §5.3/§6.1/§214）。

覆盖：被困单位普通移动不能离开结界地点（含 G3 自身）、强制位移豁免并
同步释出结界身份（captured/original_locations/main_target 清空）、
新抵达单位进入结界地点不补入捕捉、原地"移动"不触发结界检查、
无结界/无 M9 profile 时移动完全不受影响（回归）。
"""
import unittest

from controllers.base import PlayerController

from engine import experiments
from engine.game_state import GameState
from models.player import Player

from actions.move import execute as move_execute
from engine.m9.gate import ensure_state_mechanisms
from engine.m9.talents.g3 import Mythland9


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


def _make(*pids):
    """创建 state + 玩家（hp20）+ G3 天赋；pids[0] 为 G3。"""
    state = GameState()
    ensure_state_mechanisms(state)
    state.current_round = 1
    g3 = None
    others = []
    for i, pid in enumerate(pids):
        p = Player(pid, f"玩家{i}", controller=_RecordingController())
        state.add_player(p)
        p.max_hp = 20
        p.hp = 20
        p.location = "公园"
        if i == 0:
            g3 = p
        else:
            others.append(p)
    t = Mythland9(g3.player_id, state)
    g3.talent = t
    return state, g3, t, others


def _seat(state, pid, round_num=1):
    """R0 公演位：SP=2 + 报名 + 固化本轮唯一公演位（与 round_manager R0 同序）。"""
    m9 = state.m9_system
    m9.set_sp(pid, 2)
    m9.register_performance(pid, round_num)
    m9.allocate_public_slot(round_num)


def _blocked_events(state):
    """结界阻塞事件（reason=g3_barrier 的 move_blocked）。"""
    return [e for e in state.event_log
            if e.get("type") == "move_blocked"
            and e.get("reason") == "g3_barrier"]


def _expanded():
    """展开结界后的 state：G3=p1 于「公园」，p2 被捕捉且为主目标。"""
    state, g3, t, others = _make("p1", "p2")
    _seat(state, "p1")
    msg, ok = t.execute_t0(g3)
    assert ok, msg
    return state, g3, t, others


class BarrierMoveBlockTest(unittest.TestCase):
    """结界展开后：被困单位普通移动不能离开结界地点（§5.3）。"""

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_trapped_player_normal_move_blocked(self) -> None:
        state, g3, t, others = _expanded()
        p2 = others[0]
        result = move_execute(p2, "商店", state)
        self.assertEqual(p2.location, "公园")              # 位置未变
        self.assertTrue(t.barrier_active)                  # 结界完好
        self.assertEqual(t.barrier_anchor_durability, t.barrier_anchor_max)
        self.assertEqual(t.captured, ["p2"])               # 捕捉快照未变
        self.assertEqual(t.main_target, "p2")
        blocked = _blocked_events(state)
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0]["player"], "p2")
        moves = [e for e in state.event_log if e.get("type") == "move"]
        self.assertEqual(moves, [])                        # 未产生移动事件
        self.assertIn("结界", result)

    def test_g3_itself_normal_move_blocked(self) -> None:
        state, g3, t, others = _expanded()
        result = move_execute(g3, "商店", state)
        self.assertEqual(g3.location, "公园")
        self.assertTrue(t.barrier_active)
        self.assertEqual(t.captured, ["p2"])
        blocked = _blocked_events(state)
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0]["player"], "p1")
        moves = [e for e in state.event_log if e.get("type") == "move"]
        self.assertEqual(moves, [])
        self.assertIn("结界", result)

    def test_same_location_move_not_blocked(self) -> None:
        """原地"移动"（destination == old_location）不触发结界检查。"""
        state, g3, t, others = _expanded()
        p2 = others[0]
        result = move_execute(p2, "公园", state)
        self.assertEqual(p2.location, "公园")
        self.assertEqual(_blocked_events(state), [])
        self.assertEqual(t.captured, ["p2"])               # 身份未变
        self.assertTrue(t._is_trapped(p2))


class BarrierForcedExitTest(unittest.TestCase):
    """强制位移豁免（最高级规则）：移动成功并同步释出结界身份（§5.3/§214）。"""

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_forced_exempt_move_succeeds_and_releases(self) -> None:
        for flag in ("_hexagram_forced_move", "_ripple_forced_move",
                     "_hologram_pull"):
            with self.subTest(flag=flag):
                state, g3, t, others = _expanded()
                p2 = others[0]
                setattr(p2, flag, True)
                result = move_execute(p2, "商店", state)
                self.assertEqual(p2.location, "商店")      # 强制位移成功
                self.assertNotIn("p2", t.captured)         # 结界身份已释出
                self.assertNotIn("p2", t.original_locations)
                self.assertIsNone(t.main_target)           # §214 立即清空
                self.assertFalse(t._is_trapped(p2))
                self.assertTrue(t.barrier_active)          # 结界本身仍在
                self.assertEqual(_blocked_events(state), [])
                releases = [e for e in state.event_log
                            if e.get("type") == "m9_g3_release"]
                self.assertEqual(len(releases), 1)
                self.assertEqual(releases[0]["released"], "p2")
                self.assertEqual(releases[0]["reason"], "forced_exit")
                moves = [e for e in state.event_log if e.get("type") == "move"]
                self.assertEqual(len(moves), 1)

    def test_main_target_cleared_on_forced_exit(self) -> None:
        """显式指定主目标后强制退场 → 主目标立即清空（§214）。"""
        state, g3, t, others = _expanded()
        p2 = others[0]
        t.main_target = "p2"
        p2._ripple_forced_move = True
        move_execute(p2, "医院", state)
        self.assertEqual(p2.location, "医院")
        self.assertIsNone(t.main_target)


class BarrierEntryAndRegressionTest(unittest.TestCase):
    """进入结界地点不补入捕捉（§5.3 新抵达单位仍在外部）；无结界回归。"""

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_outsider_move_into_barrier_allowed_not_captured(self) -> None:
        state, g3, t, others = _expanded()
        # 展开后新抵达原地点的单位仍在外部普通地点，不补入结界
        p3 = Player("p3", "局外人", controller=_RecordingController())
        state.add_player(p3)
        p3.max_hp = 20
        p3.hp = 20
        p3.location = "商店"
        result = move_execute(p3, "公园", state)
        self.assertEqual(p3.location, "公园")              # 允许进入
        self.assertNotIn("p3", t.captured)                 # 不补入捕捉
        self.assertNotIn("p3", t.original_locations)
        self.assertFalse(t._is_trapped(p3))
        self.assertEqual(_blocked_events(state), [])

    def test_no_barrier_movement_unaffected(self) -> None:
        """结界未展开：移动完全不受影响（回归）。"""
        state, g3, t, others = _make("p1", "p2")
        p2 = others[0]
        result = move_execute(p2, "商店", state)
        self.assertEqual(p2.location, "商店")
        self.assertIn("移动", result)
        moves = [e for e in state.event_log if e.get("type") == "move"]
        self.assertEqual(len(moves), 1)
        self.assertEqual(_blocked_events(state), [])
        # G3 自身未展开结界时也可正常移动
        result = move_execute(g3, "医院", state)
        self.assertEqual(g3.location, "医院")

    def test_no_m9_profile_movement_unaffected(self) -> None:
        """无 M9 profile 的裸 state：移动完全不受影响（回归）。"""
        state = GameState()
        pa = Player("a", "甲", controller=_RecordingController())
        state.add_player(pa)
        pa.location = "公园"
        result = move_execute(pa, "医院", state)
        self.assertEqual(pa.location, "医院")
        self.assertIn("移动", result)

    def test_location_display_handles_none(self) -> None:
        """显示层安全网：loc_id 为 None 返回“未知”，不再 AttributeError。"""
        from actions.move import get_location_display_name
        state = GameState()
        self.assertEqual(get_location_display_name(None, state), "未知")
        self.assertEqual(get_location_display_name("商店", state), "商店")


if __name__ == "__main__":
    unittest.main()
