"""
冒烟测试（Smoke Tests）
=======================
目标：在不启动 CLI / 不调用 input() 的前提下，
验证核心数据结构可以被正确构建和操作。

这组测试不验证游戏规则是否正确，
只保证"基础构件不会在导入或初始化时崩溃"。
"""

import pytest

from utils.rng import Rng, set_rng, reset_rng
from engine.game_state import GameState
from models.player import Player


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def fixed_rng():
    """每个测试用固定序列替换全局 Rng，测试后还原。"""
    set_rng(Rng(fixed_sequence=[2, 3, 1, 4, 2, 3]))
    yield
    reset_rng()


@pytest.fixture
def two_player_state():
    """返回一个含两名玩家的 GameState，玩家均处于睡眠状态。"""
    state = GameState()
    alice = Player("p1", "Alice")
    bob   = Player("p2", "Bob")
    state.add_player(alice)
    state.add_player(bob)
    return state


# ── 测试：Rng ─────────────────────────────────────────────────────────


class TestRng:
    def test_fixed_sequence_d4(self):
        rng = Rng(fixed_sequence=[1, 2, 3, 4])
        assert rng.d4() == 1
        assert rng.d4() == 2
        assert rng.d4() == 3
        assert rng.d4() == 4

    def test_fixed_sequence_wraps(self):
        """序列耗尽后循环，不抛异常。"""
        rng = Rng(fixed_sequence=[3])
        assert rng.d4() == 3
        assert rng.d4() == 3   # 回绕

    def test_real_random_in_range_d6(self):
        rng = Rng(seed=42)
        for _ in range(20):
            assert 1 <= rng.d6() <= 6

    def test_roll_generic(self):
        rng = Rng(fixed_sequence=[5])
        assert rng.roll(6) == 5


# ── 测试：GameState 基础构建 ──────────────────────────────────────────


class TestGameStateInit:
    def test_can_create_empty_state(self):
        state = GameState()
        assert state.current_round == 0
        assert state.game_over is False
        assert state.players == {}

    def test_add_player(self, two_player_state):
        state = two_player_state
        assert len(state.players) == 2
        assert state.get_player("p1").name == "Alice"
        assert state.get_player("p2").name == "Bob"

    def test_player_order_preserved(self, two_player_state):
        assert two_player_state.player_order == ["p1", "p2"]

    def test_alive_players_initial(self, two_player_state):
        """初始状态下两名玩家都存活（hp > 0）。"""
        alive = two_player_state.alive_players()
        assert len(alive) == 2

    def test_check_victory_no_winner_with_two(self, two_player_state):
        """两人存活时没有赢家。"""
        assert two_player_state.check_victory() is None


# ── 测试：Player 基础属性 ─────────────────────────────────────────────


class TestPlayer:
    def test_initial_hp(self):
        p = Player("p1", "Test")
        assert p.hp == 1.0
        assert p.max_hp == 1.0

    def test_is_alive(self):
        p = Player("p1", "Test")
        assert p.is_alive() is True
        p.hp = 0
        assert p.is_alive() is False

    def test_initial_not_awake(self):
        """新玩家默认处于睡眠状态（尚未 wake up）。"""
        p = Player("p1", "Test")
        assert p.is_awake is False

    def test_has_punch_weapon_by_default(self):
        """玩家默认持有拳击武器。"""
        p = Player("p1", "Test")
        assert p.has_weapon("拳击")

    def test_d4_bonus_no_streak(self):
        """没有连续未行动时，D4 加成为 0。"""
        p = Player("p1", "Test")
        assert p.get_d4_bonus() == 0

    def test_d4_bonus_with_streak(self):
        """连续3回合未行动时，D4 加成为 1。"""
        p = Player("p1", "Test")
        p.no_action_streak = 3
        assert p.get_d4_bonus() == 1


# ── 测试：log_event ───────────────────────────────────────────────────


class TestEventLog:
    def test_log_event_appends(self, two_player_state):
        state = two_player_state
        state.log_event("test_event", player="p1", value=42)
        assert len(state.event_log) == 1
        entry = state.event_log[0]
        assert entry["type"] == "test_event"
        assert entry["player"] == "p1"
        assert entry["value"] == 42

    def test_log_event_records_round(self, two_player_state):
        state = two_player_state
        state.current_round = 3
        state.log_event("round_check")
        assert state.event_log[0]["round"] == 3


# ── 测试：MarkerManager 基础行为 ──────────────────────────────────────


class TestMarkers:
    def test_init_player_has_sleeping(self, two_player_state):
        state = two_player_state
        assert state.markers.has("p1", "SLEEPING")

    def test_add_and_remove_marker(self, two_player_state):
        state = two_player_state
        state.markers.add("p1", "STUNNED")
        assert state.markers.has("p1", "STUNNED")
        state.markers.remove("p1", "STUNNED")
        assert not state.markers.has("p1", "STUNNED")

    def test_death_clears_markers(self, two_player_state):
        state = two_player_state
        state.markers.add("p1", "INVISIBLE")
        state.markers.on_player_death("p1")
        assert not state.markers.has("p1", "INVISIBLE")
