"""
Textual TUI 客户端应用
═══════════════════════
分区布局：
- 顶部：游戏信息区
- 中部：游戏日志区（可滚动 RichLog）
- 底部左：公屏聊天
- 底部右：私聊（Tab 切换）
- 最底部：命令输入框
- 房主额外有管理面板
"""

import threading
from typing import Optional, Any

from textual.app import App, ComposeResult
from textual.containers import Vertical, Horizontal, Container
from textual.widgets import Header, Footer, Static, RichLog, TabbedContent, TabPane
from textual.css.query import NoMatches

from tui.widgets.game_info import GameInfoWidget
from tui.widgets.game_log import GameLogWidget
from tui.widgets.chat_panel import ChatPanel
from tui.widgets.command_input import CommandInput, CommandSubmitted
from tui.widgets.host_panel import HostPanel, SlotConfigRequest
from network.protocol import MessageType


class BadtimeWarTUI(App):
    """起闯战争 TUI 客户端。"""

    CSS = """
    #game-info {
        height: 4;
        border: solid blue;
        padding: 0 1;
    }
    #game-log {
        height: 1fr;
        border: solid white;
    }
    #bottom-area {
        height: 12;
    }
    #chat-area {
        width: 1fr;
        border: solid yellow;
    }
    #host-area {
        width: 40;
        display: none;
    }
    #host-area.visible {
        display: block;
    }
    CommandInput {
        dock: bottom;
        height: 3;
    }
    """

    BINDINGS = [
        ("ctrl+q", "quit", "退出"),
    ]

    def __init__(
        self,
        is_host: bool = False,
        lobby: Any = None,
        client: Any = None,
        server: Any = None,
        start_game_callback: Any = None,
        chat_manager: Any = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.is_host = is_host
        self.lobby = lobby
        self.client = client
        self.server = server
        self.start_game_callback = start_game_callback
        self.chat_manager = chat_manager
        self._input_widget: Optional[CommandInput] = None
        self._chat_panel: Optional[ChatPanel] = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield GameInfoWidget(id="game-info")
        yield GameLogWidget(id="game-log")
        with Horizontal(id="bottom-area"):
            yield ChatPanel(id="chat-area")
            yield HostPanel(lobby=self.lobby, id="host-area")
        yield CommandInput(id="cmd-input")
        yield Footer()

    def on_mount(self):
        self._input_widget = self.query_one("#cmd-input", CommandInput)
        self._chat_panel = self.query_one("#chat-area", ChatPanel)

        if self.is_host and self.lobby:
            host_area = self.query_one("#host-area")
            host_area.add_class("visible")
            try:
                host_panel = self.query_one(HostPanel)
                host_panel.refresh_slots()
            except NoMatches:
                pass

        # 如果是客户端，注册消息处理器
        if self.client:
            self.client.on(MessageType.GAME_EVENT, self._on_game_event)
            self.client.on(MessageType.CHAT_MESSAGE, self._on_chat_message)
            self.client.on(MessageType.LOBBY_UPDATE, self._on_lobby_update)
            self.client.on(MessageType.DISCONNECT_NOTICE, self._on_disconnect_notice)

    # ──────────────────────────────────────────
    #  游戏事件处理
    # ──────────────────────────────────────────

    def _on_game_event(self, msg: dict):
        self.call_from_thread(self._handle_game_event, msg)

    def _handle_game_event(self, msg: dict):
        try:
            info = self.query_one("#game-info", GameInfoWidget)
            info.update_from_event(msg)
        except NoMatches:
            pass
        try:
            log = self.query_one("#game-log", GameLogWidget)
            log.append_event(msg)
        except NoMatches:
            pass

    def _on_chat_message(self, msg: dict):
        self.call_from_thread(self._handle_chat, msg)

    def _handle_chat(self, msg: dict):
        if self._chat_panel:
            self._chat_panel.add_message(
                sender=msg.get("sender", ""),
                content=msg.get("content", ""),
                channel=msg.get("channel", "public"),
                target=msg.get("target"),
            )

    def _on_lobby_update(self, msg: dict):
        self.call_from_thread(self._handle_lobby_update, msg)

    def _handle_lobby_update(self, msg: dict):
        try:
            log = self.query_one("#game-log", GameLogWidget)
            log.write("  [大厅] 房间状态已更新")
        except NoMatches:
            pass
        if self.is_host:
            try:
                host_panel = self.query_one(HostPanel)
                host_panel.refresh_slots()
            except NoMatches:
                pass

    def _on_disconnect_notice(self, msg: dict):
        self.call_from_thread(self._handle_disconnect, msg)

    def _handle_disconnect(self, msg: dict):
        name = msg.get("player_name", "")
        action = msg.get("action", "")
        try:
            log = self.query_one("#game-log", GameLogWidget)
            if action == "waiting_reconnect":
                log.write(f"  [断线] {name} 已断线，等待重连...")
            elif action == "ai_takeover":
                log.write(f"  [断线] {name} 已断线，AI 接管")
            elif action == "reconnected":
                log.write(f"  [重连] {name} 已重新连接")
        except NoMatches:
            pass

    # ──────────────────────────────────────────
    #  外部线程推送接口
    # ──────────────────────────────────────────

    def push_game_event(self, msg: dict):
        """从外部线程推送游戏事件到 TUI"""
        self.call_from_thread(self._handle_game_event, msg)

    def push_chat_message(self, sender: str, content: str,
                          channel: str = "public", target: str = None):
        """从外部线程推送聊天消息到 TUI"""
        self.call_from_thread(self._handle_chat, {
            "sender": sender,
            "content": content,
            "channel": channel,
            "target": target,
        })

    # ──────────────────────────────────────────
    #  命令处理
    # ──────────────────────────────────────────

    def on_command_submitted(self, event: CommandSubmitted):
        if event.cmd_type == "chat":
            self._send_chat(event.value, "public")
        elif event.cmd_type == "whisper":
            self._send_chat(event.value, "private", event.target)
        # game 命令由 CommandInput 的同步等待机制处理

    def _send_chat(self, content: str, channel: str, target: str = None):
        msg = {
            "type": MessageType.CHAT_SEND,
            "sender": self.client.player_name if self.client else "房主",
            "content": content,
            "channel": channel,
            "target": target,
        }
        if self.client:
            self.client.send_sync(msg)
        elif self.server:
            # 房主通过 ChatManager 处理（触发 AI 聊天 + 广播）
            host_name = "房主"
            if self.lobby:
                from network.lobby import SlotType
                for slot in self.lobby.slots:
                    if slot.slot_type == SlotType.HUMAN_LOCAL and slot.player_name:
                        host_name = slot.player_name
                        break
            if self.chat_manager:
                self.chat_manager.handle_host_chat(
                    host_name, content, channel, target,
                )
            else:
                chat_msg = {
                    "type": MessageType.CHAT_MESSAGE,
                    "sender": host_name,
                    "content": content,
                    "channel": channel,
                    "target": target,
                }
                self.server.broadcast_sync(chat_msg)
            if self._chat_panel and not self.chat_manager:
                self._chat_panel.add_message(host_name, content, channel, target)

    # ──────────────────────────────────────────
    #  房主管理
    # ──────────────────────────────────────────

    def on_slot_config_request(self, event: SlotConfigRequest):
        if event.action == "start_game" and self.lobby:
            try:
                log = self.query_one("#game-log", GameLogWidget)
                if self.lobby.can_start():
                    log.write("  [系统] 游戏即将开始...")
                    if self.start_game_callback:
                        threading.Thread(
                            target=self.start_game_callback,
                            daemon=True,
                        ).start()
                else:
                    log.write("  [系统] 还有空位未填满，无法开始")
            except NoMatches:
                pass

    # ──────────────────────────────────────────
    #  外部日志写入
    # ──────────────────────────────────────────

    def write_log(self, text: str):
        try:
            log = self.query_one("#game-log", GameLogWidget)
            self.call_from_thread(log.write, text)
        except NoMatches:
            pass
