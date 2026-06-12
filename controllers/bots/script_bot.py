"""ScriptBotController —— 风洞 bot 基类。

设计约定：
- personality 属性 = bot 名，复用 stats_runner 现有的人格统计分桶。
- choose/confirm 走保守默认（同 ForfeitController），子类只写 decide()。
- 指令全部走 cli/parser.py 现有语法；非法指令由引擎重试耗尽机制
  自动降级为 forfeit（engine/action_turn.py），bot 不需要自带合法性判断。
- bot 不选天赋（stats_runner --lineup 装配时走无天赋路径）。
"""
from typing import Any, Dict, List, Optional

from controllers.base import PlayerController


class ScriptBotController(PlayerController):
    """脚本 bot 基类：起床逻辑 + 保守 choose 默认值，策略由子类 decide() 提供。"""

    BOT_NAME = "script"

    def __init__(self) -> None:
        self.personality = self.BOT_NAME  # 复用 stats_runner 人格分桶

    # ── 子类唯一需要覆盖的方法 ──────────────────────────────

    def decide(self, player: Any, game_state: Any) -> str:
        """返回本回合指令（cli/parser.py 语法）。"""
        return "forfeit"

    # ── PlayerController 接口 ──────────────────────────────

    def get_command(
        self,
        player: Any,
        game_state: Any,
        available_actions: List[str],
        context: Optional[Dict] = None,
    ) -> str:
        if not getattr(player, "is_awake", True):
            return "wake"
        return self.decide(player, game_state)

    def choose(
        self,
        prompt: str,
        options: List[str],
        context: Optional[Dict] = None,
    ) -> str:
        return options[0] if options else ""

    def choose_multi(
        self,
        prompt: str,
        options: List[str],
        max_count: int,
        min_count: int = 0,
        context: Optional[Dict] = None,
    ) -> List[str]:
        return options[:min_count] if min_count > 0 else []

    def confirm(
        self,
        prompt: str,
        context: Optional[Dict] = None,
    ) -> bool:
        return False

    # ── 共享查询助手（只读，不碰 GameState） ──────────────────

    @staticmethod
    def alive_enemies(player: Any, game_state: Any) -> List[Any]:
        """按 player_order 返回存活敌人（顺序稳定，保证确定性）。"""
        result = []
        for pid in game_state.player_order:
            if pid == player.player_id:
                continue
            p = game_state.get_player(pid)
            if p is not None and p.is_alive():
                result.append(p)
        return result

    @staticmethod
    def enemies_here(player: Any, game_state: Any) -> List[Any]:
        """同地点存活敌人。"""
        return [p for p in ScriptBotController.alive_enemies(player, game_state)
                if p.location == player.location]

    @staticmethod
    def has_weapon(player: Any, name: str) -> bool:
        return any(getattr(w, "name", None) == name for w in player.weapons if w)

    @staticmethod
    def count_outer_armor(player: Any) -> int:
        return len(getattr(player.armor, "outer", []) or [])

    @staticmethod
    def has_armor_named(player: Any, name: str) -> bool:
        outer = getattr(player.armor, "outer", []) or []
        inner = getattr(player.armor, "inner", []) or []
        return any(getattr(a, "name", "") == name for a in list(outer) + list(inner))

    @staticmethod
    def my_home(player: Any) -> str:
        return f"home_{player.player_id}"

    @staticmethod
    def is_engaged(player: Any, target: Any, game_state: Any) -> bool:
        """是否已与目标面对面（近战攻击的前置，由 find 建立）。"""
        return game_state.markers.has_relation(
            player.player_id, "ENGAGED_WITH", target.player_id)
