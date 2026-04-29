"""
HumanController —— 人类玩家控制器
──────────────────────────────────
内部调用 cli/display.py 的 prompt 函数，
行为与改动前完全一致，确保人类玩家体验零变化。
"""

from typing import Callable, List, Optional, Dict, Any
from controllers.base import PlayerController
from cli import display

# 模块级聊天回调：由 main_server 在游戏启动前注入，
# 使得 HumanController 可在回合中拦截 /chat 和 /whisper 命令。
_chat_handler: Optional[Callable[[str], bool]] = None


def set_chat_handler(handler: Optional[Callable[[str], bool]]):
    """注册/注销聊天命令回调。handler(raw) 返回 True 表示已处理。"""
    global _chat_handler
    _chat_handler = handler


class HumanController(PlayerController):
    """人类玩家：所有输入都来自键盘终端。"""

    def get_command(
        self,
        player: Any,
        game_state: Any,
        available_actions: List[str],
        context: Optional[Dict] = None
    ) -> str:
        """
        显示状态，等待人类输入命令。
        注意：available_actions 的展示已经在 _phase_t1 中由
        display.show_available_actions 完成，这里只负责读输入。
        如果输入是聊天命令（/chat, /whisper），交由 _chat_handler 处理后重新提示。
        """
        display.show_player_status(player, game_state)
        while True:
            raw = display.prompt_input(player.name)
            if _chat_handler and raw.startswith(("/chat ", "/whisper ")):
                _chat_handler(raw)
                continue
            return raw

    def choose(
        self,
        prompt: str,
        options: List[str],
        context: Optional[Dict] = None
    ) -> str:
        """
        向人类展示选项列表，等待选择。
        直接调用 display.prompt_choice。
        """
        return display.prompt_choice(prompt, options)

    def choose_multi(
        self,
        prompt: str,
        options: List[str],
        max_count: int,
        min_count: int = 0,
        context: Optional[Dict] = None
    ) -> List[str]:
        """
        多选。循环调用 prompt_choice 直到选够或玩家选择"完成"。
        """
        selected = []
        remaining = list(options)

        while len(selected) < max_count:
            if len(selected) >= min_count:
                remaining_with_done = remaining + ["跳过"]
            else:
                remaining_with_done = list(remaining)

            choice = display.prompt_choice(
                f"{prompt} (已选{len(selected)}/{max_count})",
                remaining_with_done
            )

            if choice == "跳过":
                break

            selected.append(choice)
            if choice in remaining:
                remaining.remove(choice)

        return selected

    def confirm(
        self,
        prompt: str,
        context: Optional[Dict] = None
    ) -> bool:
        """
        是/否确认。
        """
        choice = display.prompt_choice(prompt, ["是", "否"])
        return choice == "是"