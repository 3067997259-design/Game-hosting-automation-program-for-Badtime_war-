"""M3 可见性测试：属性对位矩阵 / 行动隐匿广播过滤 / proxy 裁剪 / v1 回退。"""
import unittest

from engine import experiments
from engine.game_state import GameState
from engine.visibility import can_see
from engine.visibility_proxy import VisibilityProxy, RedactedPlayer
from models.player import Player
from controllers.forfeit_controller import ForfeitController


class _RecordingController(ForfeitController):
    """记录收到的事件（行动隐匿广播过滤验证用）。"""

    def __init__(self):
        self.events = []

    def on_event(self, event):
        self.events.append(event)


def _player(pid, state, controller=None):
    p = Player(pid, f"玩家{pid}", controller=controller or ForfeitController())
    p.is_awake = True
    p.location = "商店"
    state.add_player(p)
    return p


def _go_stealth(player, state, attr_item):
    """让玩家进入隐身（INVISIBLE 标记 + m3 属性轨）。"""
    player.is_invisible = True
    player.grant_visibility_item(attr_item)
    state.markers.on_player_go_invisible(
        player.player_id, list(state.players.values()))


class AttributeMatchupTest(unittest.TestCase):
    """3 隐身 × 3 探测对位矩阵（拍板 §1.2：任一对应探测即可见）。"""

    def setUp(self) -> None:
        experiments.reset()
        experiments.enable("m3_accuracy")

    def tearDown(self) -> None:
        experiments.reset()

    def _matrix_case(self, stealth_item, detect_item, expect_visible):
        state = GameState()
        observer = _player("p1", state)
        target = _player("p2", state)
        _go_stealth(target, state, stealth_item)
        if detect_item:
            observer.has_detection = True
            observer.grant_visibility_item(detect_item)
        self.assertEqual(can_see(observer, target, state), expect_visible,
                         f"{stealth_item} vs {detect_item}")

    def test_matchup_matrix(self) -> None:
        # 对位 → 可见
        self._matrix_case("隐身衣", "热成像仪", True)
        self._matrix_case("隐身术", "探测魔法", True)
        self._matrix_case("隐形涂层", "雷达", True)
        # 错位 → 不可见（m3 下属性必须对位）
        self._matrix_case("隐身衣", "雷达", False)
        self._matrix_case("隐身术", "热成像仪", False)
        self._matrix_case("隐形涂层", "探测魔法", False)
        # 无探测 → 不可见
        self._matrix_case("隐身衣", None, False)

    def test_non_stealth_always_visible(self) -> None:
        state = GameState()
        observer = _player("p1", state)
        target = _player("p2", state)
        self.assertTrue(can_see(observer, target, state))

    def test_v1_fallback_when_disabled(self) -> None:
        """m3 关闭：回退全局布尔探测（v1 语义）。"""
        experiments.reset()
        state = GameState()
        observer = _player("p1", state)
        target = _player("p2", state)
        target.is_invisible = True
        state.markers.on_player_go_invisible(
            target.player_id, list(state.players.values()))
        self.assertFalse(can_see(observer, target, state))
        observer.has_detection = True
        self.assertTrue(can_see(observer, target, state))


class ActionConcealmentTest(unittest.TestCase):
    """行动隐匿：广播过滤 + event_log 全量。"""

    def setUp(self) -> None:
        experiments.reset()
        experiments.enable("m3_accuracy")
        self.state = GameState()
        self.watcher_ctrl = _RecordingController()
        self.detector_ctrl = _RecordingController()
        self.stealthy = _player("p1", self.state)
        self.watcher = _player("p2", self.state, self.watcher_ctrl)
        self.detector = _player("p3", self.state, self.detector_ctrl)
        self.detector.has_detection = True
        self.detector.grant_visibility_item("热成像仪")
        _go_stealth(self.stealthy, self.state, "隐身衣")  # 普隐 vs 普探

    def tearDown(self) -> None:
        experiments.reset()

    def test_broadcast_filtered_by_observer(self) -> None:
        self.state.log_event("move", player="p1",
                             from_loc="商店", to_loc="医院")
        watcher_moves = [e for e in self.watcher_ctrl.events if e["type"] == "move"]
        detector_moves = [e for e in self.detector_ctrl.events if e["type"] == "move"]
        self.assertEqual(len(watcher_moves), 0, "无对应探测者不应收到隐身者行动")
        self.assertEqual(len(detector_moves), 1, "对位探测者应收到")

    def test_event_log_stores_full(self) -> None:
        """event_log 本体存全量——golden 回放不受广播过滤影响。"""
        self.state.log_event("move", player="p1", from_loc="商店", to_loc="医院")
        moves = [e for e in self.state.event_log if e["type"] == "move"]
        self.assertEqual(len(moves), 1)

    def test_own_events_always_received(self) -> None:
        """actor 自己永远收到自己的事件。"""
        own_ctrl = _RecordingController()
        self.stealthy.controller = own_ctrl
        self.state.log_event("move", player="p1", from_loc="商店", to_loc="医院")
        self.assertEqual(len([e for e in own_ctrl.events if e["type"] == "move"]), 1)


class VisibilityProxyTest(unittest.TestCase):

    def setUp(self) -> None:
        experiments.reset()
        experiments.enable("m3_accuracy")
        self.state = GameState()
        self.observer = _player("p1", self.state)
        self.stealthy = _player("p2", self.state)
        _go_stealth(self.stealthy, self.state, "隐身术")
        self.proxy = VisibilityProxy(self.state, "p1")

    def tearDown(self) -> None:
        experiments.reset()

    def test_concealed_player_redacted(self) -> None:
        view = self.proxy.get_player("p2")
        self.assertIsInstance(view, RedactedPlayer)
        self.assertIsNone(view.location)
        self.assertFalse(view.is_on_map())
        # 明牌字段透传（HP/名字）
        self.assertEqual(view.hp, self.stealthy.hp)
        self.assertEqual(view.name, self.stealthy.name)

    def test_self_and_visible_players_passthrough(self) -> None:
        self.assertIs(self.proxy.get_player("p1"), self.observer)
        # 对位探测后透传真身
        self.observer.has_detection = True
        self.observer.grant_visibility_item("探测魔法")
        self.assertIs(self.proxy.get_player("p2"), self.stealthy)

    def test_players_at_location_excludes_concealed(self) -> None:
        names = [p.player_id for p in self.proxy.players_at_location("商店")]
        self.assertIn("p1", names)
        self.assertNotIn("p2", names)


if __name__ == "__main__":
    unittest.main()
