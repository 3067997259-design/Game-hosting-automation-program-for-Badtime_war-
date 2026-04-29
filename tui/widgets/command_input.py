"""命令输入框 —— 支持游戏命令和聊天前缀"""

import threading
from textual.widgets import Input
from textual.message import Message


class CommandSubmitted(Message):
    """命令提交消息。"""
    def __init__(self, value: str, cmd_type: str = "game", target: str = None):
        super().__init__()
        self.value = value
        self.cmd_type = cmd_type  # "game", "chat", "whisper"
        self.target = target


class CommandInput(Input):
    """
    底部命令输入框。
    - 直接输入 = 游戏命令
    - /chat <内容> = 公屏聊天
    - /whisper <玩家名> <内容> = 私聊
    """

    DEFAULT_CSS = """
    CommandInput {
        dock: bottom;
        height: 3;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(placeholder="输入命令...", **kwargs)
        self._pending_event = threading.Event()
        self._pending_value = ""

    def on_input_submitted(self, event: Input.Submitted):
        raw = event.value.strip()
        if not raw:
            return

        self.value = ""

        if raw.startswith("/chat "):
            content = raw[6:].strip()
            self.post_message(CommandSubmitted(content, cmd_type="chat"))
        elif raw.startswith("/whisper "):
            parts = raw[9:].strip().split(" ", 1)
            if len(parts) >= 2:
                target, content = parts
                self.post_message(
                    CommandSubmitted(content, cmd_type="whisper", target=target)
                )
            else:
                self.post_message(
                    CommandSubmitted(raw, cmd_type="game")
                )
        else:
            self.post_message(CommandSubmitted(raw, cmd_type="game"))

            # 唤醒同步等待
            self._pending_value = raw
            self._pending_event.set()

    def wait_for_input(self, timeout: float = 300.0) -> str:
        self._pending_event.clear()
        self._pending_value = ""
        self._pending_event.wait(timeout=timeout)
        return self._pending_value
