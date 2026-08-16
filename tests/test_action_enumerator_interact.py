"""公共交互动作枚举必须与真实 can_interact 预检一致。"""

from cli.parser import parse
from engine import experiments
from engine.action_enumerator import build_action_options
from engine.game_state import GameState
from engine.m9.gate import ensure_state_mechanisms
from models.player import Player
from controllers.forfeit_controller import ForfeitController


def test_m9_interact_enumerator_filters_unaffordable_paid_items() -> None:
    experiments.reset()
    for flag in ("hp20", "m4_gear", "m9_rfc"):
        experiments.enable(flag)
    try:
        state = GameState()
        ensure_state_mechanisms(state)
        player = Player("p1", "AI", controller=ForfeitController())
        player.location = "商店"
        player.is_awake = True
        player.vouchers = 1  # 旧枚举会因此误判付费项目合法
        player.credits = 0
        state.add_player(player)

        options = build_action_options(player, state, ["interact"])["interact"]
        assert "interact 陶瓷护甲" not in options
        assert "interact 打工" in options
    finally:
        experiments.reset()


def test_move_home_parses_to_own_home_but_home_px_passes_through() -> None:
    """游戏语义：裸 move home 映射为自己的家；move home_pX 原样通过（他人住宅）。"""
    assert parse("move home", "p2") == {"action": "move", "destination": "home_p2"}
    assert parse("move 回家", "p2") == {"action": "move", "destination": "home_p2"}
    assert parse("move home_p3", "p2") == {
        "action": "move", "destination": "home_p3"}


def test_move_enumerator_lists_other_players_homes() -> None:
    """AI 移动目录必须包含他人住宅（home_{pid}），否则追击/布局命令会被丢弃。"""
    experiments.reset()
    for flag in ("hp20", "m4_gear", "m9_rfc"):
        experiments.enable(flag)
    try:
        state = GameState()
        ensure_state_mechanisms(state)
        player = Player("p1", "AI", controller=ForfeitController())
        player.location = "商店"
        player.is_awake = True
        state.add_player(player)
        other = Player("p2", "OTHER", controller=ForfeitController())
        other.location = "医院"
        other.is_awake = True
        state.add_player(other)

        moves = build_action_options(player, state, ["move"])["move"]
        assert "move home_p2" in moves          # 他人住宅是合法目的地
        assert "move home_p1" not in moves      # 自己的家不重复列出
        assert "move home" in moves             # 自己的家（parser 映射）
        assert "move 医院" in moves
    finally:
        experiments.reset()
