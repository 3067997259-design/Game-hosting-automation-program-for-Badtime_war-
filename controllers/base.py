"""
PlayerController 抽象基类
────────────────────────
所有"输入来源"（人类键盘、AI策略、未来网络玩家）都实现这个接口。
规则层（parse/validate/execute）完全不关心输入从哪里来。
"""

from abc import ABC, abstractmethod
from typing import List, Optional, Any, Dict


class PlayerController(ABC):
    """
    玩家控制器抽象基类。

    约定：
    - get_command()  → 返回一条 CLI 命令字符串（与人类在终端输入的格式完全一致）
    - choose()       → 从给定选项列表中选一个，返回选中项的字符串
    - choose_multi() → 从给定选项列表中选多个（如地动山摇选震荡目标）
    - confirm()      → 是/否判断
    - on_event()     → 接收公开事件日志（AI 可用来更新内部状态；Human 可忽略）
    """

    @abstractmethod
    def get_command(
        self,
        player: Any,          # Player 对象
        game_state: Any,      # GameState 对象
        available_actions: List[str],  # 当前合法的行动类型列表
        context: Optional[Dict] = None
    ) -> str:
        """
        返回一条完整的 CLI 命令字符串。
        例如: "move 商店", "attack 玩家A 小刀 外层普通", "forfeit"

        规则层会对返回值进行 parse → validate，
        如果不合法会再次调用（或由调用方处理重试）。
        """
        ...

    @abstractmethod
    def choose(
        self,
        prompt: str,
        options: List[str],
        context: Optional[Dict] = None
    ) -> str:
        """
        从 options 中选择一个并返回。
        用于：天赋T0是否发动、石化二选一、加入警察三选二、等。

        返回值必须是 options 中的某一项。
        """
        ...

    @abstractmethod
    def choose_multi(
        self,
        prompt: str,
        options: List[str],
        max_count: int,
        min_count: int = 0,
        context: Optional[Dict] = None
    ) -> List[str]:
        """
        从 options 中选择 min_count~max_count 个。
        用于：地动山摇选震荡目标（最多2个）、等。

        返回值是 options 的子集列表。
        """
        ...

    @abstractmethod
    def confirm(
        self,
        prompt: str,
        context: Optional[Dict] = None
    ) -> bool:
        """
        是/否判断。
        用于：响应窗口"是否发动你给路打油"、等。
        """
        ...

    def on_event(self, event: Dict) -> None:
        """
        接收公开游戏事件（可选实现）。
        Human 通常忽略（信息已在屏幕上）；
        AI 可用来更新威胁评估、记忆对手行为。

        event 格式示例:
        {"type": "attack", "attacker": "A", "target": "B", "damage": 1, "weapon": "小刀"}
        {"type": "move", "player": "A", "from": "家", "to": "商店"}
        {"type": "police_dispatch", "target": "C"}
        """
        pass
