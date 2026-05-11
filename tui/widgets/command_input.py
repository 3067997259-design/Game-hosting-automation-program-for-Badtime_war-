"""命令输入框 —— 支持游戏命令、管理命令和聊天前缀"""

import threading
from textual.widgets import Input
from textual.message import Message


# 房主大厅阶段可用的管理命令前缀
_MANAGEMENT_CMDS = (
    "ai", "rl", "airi", "policy", "status", "start", "chatmode", "debug", "name",
)


class CommandSubmitted(Message):
    """命令提交消息。"""
    def __init__(self, value: str, cmd_type: str = "game", target: str = None):
        super().__init__()
        self.value = value
        # cmd_type: "game" / "chat" / "whisper" / "management"
        self.cmd_type = cmd_type
        self.target = target


class CommandInput(Input):
    """
    底部命令输入框。
    - 直接输入 = 游戏命令
    - /chat <内容> = 公屏聊天
    - /whisper <玩家名> <内容> = 私聊
    - 房主大厅阶段：ai / rl / policy / status = 房间管理命令
    """

    DEFAULT_CSS = """
    CommandInput {
        dock: bottom;
        height: 3;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(placeholder="/chat <内容> 公屏聊天 | /whisper <玩家名> <内容> 私聊", **kwargs)
        self._pending_event = threading.Event()
        self._pending_value = ""

    def update_placeholder_for_game(self):
        """游戏开始后更新 placeholder"""
        self.placeholder = "游戏指令 | /chat 聊天 | /whisper <玩家> 私聊 | help 帮助 | F1 完整帮助"

    def update_placeholder_for_lobby(self):
        """大厅阶段的 placeholder"""
        self.placeholder = "/chat <内容> 公屏聊天 | /whisper <玩家名> <内容> 私聊"

    def update_placeholder_for_host_lobby(self):
        """房主大厅阶段的 placeholder（包含管理命令提示）"""
        self.placeholder = "ai/rl/policy/status 管理 | /chat 聊天 | /whisper <玩家> 私聊"

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
        elif self._is_host_lobby_phase() and self._is_management_cmd(raw):
            # 房主大厅阶段：转发为管理命令，不唤醒游戏线程
            self.post_message(CommandSubmitted(raw, cmd_type="management"))
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

    # ──────────────────────────────────────────
    #  辅助
    # ──────────────────────────────────────────

    def _is_management_cmd(self, raw: str) -> bool:
        first = raw.split(" ", 1)[0].lower()
        return first in _MANAGEMENT_CMDS

    def _is_host_lobby_phase(self) -> bool:
        """仅在「房主 + 大厅等待中」时把管理命令拦截下来。"""
        try:
            app = self.app
            if not getattr(app, "is_host", False):
                return False
            lobby = getattr(app, "lobby", None)
            if lobby is None:
                return False
            state = getattr(lobby, "state", None)
            if state is None:
                return False
            value = getattr(state, "value", None)
            return value == "waiting"
        except Exception:
            return False
