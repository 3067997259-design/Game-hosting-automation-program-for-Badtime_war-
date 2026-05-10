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
    /* 主内容区：游戏开始前显示 host-area（房主）或 game-log（远程客户端）；
       游戏开始后隐藏 host-area，显示 game-log。 */
    #host-area {
        height: 1fr;
        display: none;
    }
    #host-area.lobby-mode {
        display: block;
    }
    #game-log {
        height: 1fr;
        border: solid white;
    }
    #game-log.lobby-mode-hidden {
        display: none;
    }
    #bottom-area {
        height: 12;
    }
    #chat-area {
        width: 1fr;
        border: solid yellow;
    }
    CommandInput {
        dock: bottom;
        height: 3;
    }
    """

    BINDINGS = [
        ("ctrl+q", "quit", "退出"),
        ("f1", "show_help", "帮助"),
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
        self._game_starting = False
        self._game_help_shown = False
        self._input_widget: Optional[CommandInput] = None
        self._chat_panel: Optional[ChatPanel] = None
        # 客户端模式下，服务器主动请求（REQUEST_*）的待响应状态
        self._pending_request = None  # (msg_type_str, msg_data)
        self._pending_request_lock = threading.Lock()
        self._multi_selected: list = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield GameInfoWidget(id="game-info")
        # host-area 与 game-log 共享主内容区（通过 CSS class 切换显示）
        yield HostPanel(lobby=self.lobby, id="host-area")
        yield GameLogWidget(id="game-log")
        with Horizontal(id="bottom-area"):
            yield ChatPanel(id="chat-area")
        yield CommandInput(id="cmd-input")
        yield Footer()

    def on_mount(self):
        self._input_widget = self.query_one("#cmd-input", CommandInput)
        self._chat_panel = self.query_one("#chat-area", ChatPanel)

        if self.is_host and self.lobby:
            # 房主：游戏开始前 host panel 占据主内容区，游戏日志暂时隐藏
            try:
                host_area = self.query_one("#host-area")
                host_area.add_class("lobby-mode")
                game_log = self.query_one("#game-log", GameLogWidget)
                game_log.add_class("lobby-mode-hidden")
                host_panel = self.query_one(HostPanel)
                host_panel.refresh_slots()
            except NoMatches:
                pass
            if self._input_widget:
                self._input_widget.update_placeholder_for_host_lobby()

        # 如果是客户端，注册消息处理器
        if self.client:
            self.client.on(MessageType.GAME_EVENT, self._on_game_event)
            self.client.on(MessageType.CHAT_MESSAGE, self._on_chat_message)
            self.client.on(MessageType.LOBBY_UPDATE, self._on_lobby_update)
            self.client.on(MessageType.DISCONNECT_NOTICE, self._on_disconnect_notice)
            self.client.on(MessageType.TYPING_INDICATOR, self._on_typing_indicator)
            # 服务器请求类消息（天赋选择、行动指令、确认等）
            self.client.on(MessageType.REQUEST_COMMAND, self._on_request_command)
            self.client.on(MessageType.REQUEST_CHOOSE, self._on_request_choose)
            self.client.on(MessageType.REQUEST_CHOOSE_MULTI, self._on_request_choose_multi)
            self.client.on(MessageType.REQUEST_CONFIRM, self._on_request_confirm)

        # 写入初始帮助信息
        try:
            log = self.query_one("#game-log", GameLogWidget)
            log.write("  ═══════════════════════════════════════")
            log.write("  起闯战争 - 局域网联机")
            log.write("  ═══════════════════════════════════════")
            log.write("")
            log.write("  聊天指令：")
            log.write("    /chat <内容>           - 公屏聊天")
            log.write("    /whisper <玩家名> <内容> - 私聊")
            log.write("")
            if self.is_host:
                log.write("  房主操作（游戏开始前）：")
                log.write("    在主区面板中查看槽位状态，或在下方输入框中使用：")
                log.write("      ai <slot号> [性格]         - 设置基础 AI")
                log.write("      rl <slot号>                - 设置 RL AI")
                log.write("      policy <slot号> <wait|ai>  - 设置断线策略")
                log.write("      chatmode <slot号> <airi|llm|off> - 设置聊天后端")
                log.write("      debug <0-3>                - 设置调试级别")
                log.write("      name <新名字>              - 修改房主玩家ID")
                log.write("      status                     - 刷新状态")
                log.write("      start                      - 开始游戏（与按钮等效）")
                log.write("    所有位置就绪后点击「开始游戏」按钮或输入 start")
            else:
                log.write("  等待房主开始游戏...")
                log.write("  你可以使用 /chat 和 /whisper 与其他玩家聊天")
            log.write("")
            log.write("  （游戏开始后输入 help 或按 F1 查看游戏指令）")
        except NoMatches:
            pass

    # ──────────────────────────────────────────
    #  游戏事件处理
    # ──────────────────────────────────────────

    def _on_game_event(self, msg: dict):
        self.call_from_thread(self._handle_game_event, msg)

    def _handle_game_event(self, msg: dict):
        # 第一次收到游戏事件时显示指令提示并切换到游戏布局
        if not self._game_help_shown:
            self._game_help_shown = True
            self._switch_to_game_mode()
            try:
                log = self.query_one("#game-log", GameLogWidget)
                log.write("")
                log.write("  ─── 游戏已开始 ───")
                log.write("  你的回合时可用指令：move / interact / attack / lock / find / special / forfeit")
                log.write("  查看类（不消耗行动）：status / allstatus / police / help")
                log.write("  聊天：/chat <内容> | /whisper <玩家名> <内容>")
                log.write("  按 F1 查看完整帮助")
                log.write("")
            except NoMatches:
                pass
            # 更新输入框 placeholder
            if self._input_widget:
                self._input_widget.update_placeholder_for_game()
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
                self_name=self._self_name(),
            )

    def _self_name(self) -> str:
        """当前 TUI 视角下「我」的玩家名（用于私聊频道标题区分主体）。"""
        if self.client and getattr(self.client, "player_name", None):
            return self.client.player_name
        if self.is_host and self.lobby:
            try:
                from network.lobby import SlotType
                for slot in self.lobby.slots:
                    if slot.slot_type == SlotType.HUMAN_LOCAL and slot.player_name:
                        return slot.player_name
            except Exception:
                pass
            return "房主"
        return ""

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

    def _on_typing_indicator(self, msg: dict):
        if self._thread_id == threading.get_ident():
            self._handle_typing(msg)
        else:
            self.call_from_thread(self._handle_typing, msg)

    def _handle_typing(self, msg: dict):
        player_name = msg.get("player_name", "")
        is_typing = msg.get("is_typing", False)
        if self._chat_panel:
            self._chat_panel.set_typing(player_name, is_typing)

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
    #  服务器请求处理（客户端模式：天赋选择、行动指令、确认等）
    # ──────────────────────────────────────────

    def _on_request_command(self, msg: dict):
        """服务器请求游戏指令。"""
        with self._pending_request_lock:
            self._pending_request = ("command", msg)
        if self._thread_id == threading.get_ident():
            self._show_command_request(msg)
        else:
            self.call_from_thread(self._show_command_request, msg)

    def _show_command_request(self, msg: dict):
        player_name = msg.get("player_name", "")
        actions = msg.get("available_actions", [])
        hp = msg.get("hp", "?")
        max_hp = msg.get("max_hp", "?")
        location = msg.get("location", "?")
        try:
            log = self.query_one("#game-log", GameLogWidget)
            log.write("")
            log.write(f"  ▶ 轮到你行动 [{player_name}]")
            log.write(f"  HP: {hp}/{max_hp} | 位置: {location}")
            if actions:
                log.write(f"  可选行动: {', '.join(actions)}")
            log.write("  请在下方输入框中输入指令（输入 help 查看帮助）")
        except NoMatches:
            pass
        except Exception:
            pass

    def _on_request_choose(self, msg: dict):
        """服务器请求选择。"""
        with self._pending_request_lock:
            self._pending_request = ("choose", msg)
        if self._thread_id == threading.get_ident():
            self._show_choose_request(msg)
        else:
            self.call_from_thread(self._show_choose_request, msg)

    def _show_choose_request(self, msg: dict):
        prompt = msg.get("prompt", "请选择")
        options = msg.get("options", [])
        try:
            log = self.query_one("#game-log", GameLogWidget)
            log.write("")
            log.write(f"  {prompt}")
            for i, opt in enumerate(options, 1):
                log.write(f"    {i}. {opt}")
            log.write("  请在下方输入框中输入编号")
        except NoMatches:
            pass
        except Exception:
            pass

    def _on_request_choose_multi(self, msg: dict):
        """服务器请求多选。"""
        with self._pending_request_lock:
            self._pending_request = ("choose_multi", msg)
            self._multi_selected = []
        if self._thread_id == threading.get_ident():
            self._show_choose_multi_request(msg)
        else:
            self.call_from_thread(self._show_choose_multi_request, msg)

    def _show_choose_multi_request(self, msg: dict):
        prompt = msg.get("prompt", "请选择")
        options = msg.get("options", [])
        max_count = msg.get("max_count", 1)
        min_count = msg.get("min_count", 0)
        try:
            log = self.query_one("#game-log", GameLogWidget)
            log.write("")
            log.write(f"  {prompt} (选 {min_count}~{max_count} 个，输入 0 结束选择)")
            for i, opt in enumerate(options, 1):
                log.write(f"    {i}. {opt}")
        except NoMatches:
            pass
        except Exception:
            pass

    def _on_request_confirm(self, msg: dict):
        """服务器请求确认。"""
        with self._pending_request_lock:
            self._pending_request = ("confirm", msg)
        if self._thread_id == threading.get_ident():
            self._show_confirm_request(msg)
        else:
            self.call_from_thread(self._show_confirm_request, msg)

    def _show_confirm_request(self, msg: dict):
        prompt = msg.get("prompt", "确认？")
        try:
            log = self.query_one("#game-log", GameLogWidget)
            log.write("")
            log.write(f"  {prompt}")
            log.write("  请输入 y 或 n")
        except NoMatches:
            pass
        except Exception:
            pass

    def _respond_to_pending(self, pending, user_input: str):
        """根据 pending request 类型向服务器发送响应。"""
        req_type, msg_data = pending

        if req_type == "command":
            # 允许在行动回合中发聊天，不消耗行动
            if self._is_chat_input(user_input):
                self._dispatch_inline_chat(user_input, "行动指令")
                return
            with self._pending_request_lock:
                self._pending_request = None
            self.client.send_sync({
                "type": MessageType.COMMAND_RESPONSE,
                "command": user_input or "forfeit",
            })

        elif req_type == "choose":
            if self._is_chat_input(user_input):
                self._dispatch_inline_chat(user_input, "选择")
                return
            options = msg_data.get("options", [])
            choice = None
            try:
                idx = int(user_input) - 1
                if 0 <= idx < len(options):
                    choice = options[idx]
            except ValueError:
                if user_input in options:
                    choice = user_input
            if choice is None:
                # 无效选择：保留 pending，提示用户重试
                self._log_to_game("  无效选择，请重试")
                return
            with self._pending_request_lock:
                self._pending_request = None
            self.client.send_sync({
                "type": MessageType.CHOOSE_RESPONSE,
                "choice": choice,
            })

        elif req_type == "choose_multi":
            if self._is_chat_input(user_input):
                self._dispatch_inline_chat(user_input, "多选")
                return
            options = msg_data.get("options", [])
            max_count = msg_data.get("max_count", 1)
            min_count = msg_data.get("min_count", 0)
            if not isinstance(getattr(self, "_multi_selected", None), list):
                self._multi_selected = []

            if user_input == "0" and len(self._multi_selected) >= min_count:
                with self._pending_request_lock:
                    self._pending_request = None
                    selected = list(self._multi_selected)
                    self._multi_selected = []
                self.client.send_sync({
                    "type": MessageType.CHOOSE_MULTI_RESPONSE,
                    "choices": selected,
                })
                return

            if user_input == "0":
                # 用户尝试结束选择但还没达到 min_count，给出针对性提示
                self._log_to_game(
                    f"  至少需要选择 {min_count} 个，当前已选 {len(self._multi_selected)} 个"
                )
                return

            try:
                idx = int(user_input) - 1
                if 0 <= idx < len(options) and options[idx] not in self._multi_selected:
                    self._multi_selected.append(options[idx])
                    self._log_to_game(
                        f"  已选: {options[idx]} ({len(self._multi_selected)}/{max_count})"
                    )
                    if len(self._multi_selected) >= max_count:
                        with self._pending_request_lock:
                            self._pending_request = None
                            selected = list(self._multi_selected)
                            self._multi_selected = []
                        self.client.send_sync({
                            "type": MessageType.CHOOSE_MULTI_RESPONSE,
                            "choices": selected,
                        })
                        return
                else:
                    self._log_to_game("  无效选择，请重试")
            except (ValueError, IndexError):
                self._log_to_game("  无效选择，请重试")
            # 不清除 pending，继续等待更多选择

        elif req_type == "confirm":
            if self._is_chat_input(user_input):
                self._dispatch_inline_chat(user_input, "确认")
                return
            with self._pending_request_lock:
                self._pending_request = None
            result = user_input.lower() in ("y", "yes", "是")
            self.client.send_sync({
                "type": MessageType.CONFIRM_RESPONSE,
                "result": result,
            })

    @staticmethod
    def _is_chat_input(raw: str) -> bool:
        """识别 /chat 与 /whisper（含「裸命令」与「带参数」两种写法）。"""
        if raw == "/chat" or raw == "/whisper":
            return True
        return raw.startswith("/chat ") or raw.startswith("/whisper ")

    def _send_chat_from_input(self, raw: str) -> bool:
        """从游戏指令输入中解析聊天命令（不消耗行动）。

        返回 True 表示成功发送，False 表示输入格式错误（已在 game-log
        打印提示，pending request 应保留以便用户重试）。
        """
        if raw.startswith("/chat"):
            # 兼容 "/chat" 与 "/chat <内容>"
            content = raw[len("/chat"):].lstrip()
            if not content:
                self._log_to_game("  ⚠ /chat 需要内容，例如：/chat 你好")
                return False
            self._send_chat(content, "public")
            return True
        if raw.startswith("/whisper"):
            rest = raw[len("/whisper"):].lstrip()
            if not rest:
                self._log_to_game(
                    "  ⚠ /whisper 需要目标和内容，例如：/whisper 玩家名 你好"
                )
                return False
            parts = rest.split(" ", 1)
            if len(parts) < 2 or not parts[1].strip():
                self._log_to_game(
                    f"  ⚠ /whisper 需要内容，例如：/whisper {parts[0]} 你好"
                )
                return False
            target = parts[0].strip()
            content = parts[1].strip()
            if not target:
                self._log_to_game(
                    "  ⚠ /whisper 需要目标，例如：/whisper 玩家名 你好"
                )
                return False
            self._send_chat(content, "private", target)
            return True
        return False

    def _dispatch_inline_chat(self, raw: str, action_label: str) -> None:
        """在 pending REQUEST_* 期间处理 /chat 或 /whisper：发送聊天并打印反馈。"""
        ok = self._send_chat_from_input(raw)
        if ok:
            self._log_to_game(f"  [聊天已发送，{action_label}仍在等候你输入]")

    # ──────────────────────────────────────────
    #  外部线程推送接口
    # ──────────────────────────────────────────

    def push_game_event(self, msg: dict):
        """推送游戏事件到 TUI（自动判断线程）"""
        if self._thread_id == threading.get_ident():
            self._handle_game_event(msg)
        else:
            self.call_from_thread(self._handle_game_event, msg)

    def push_chat_message(self, sender: str, content: str,
                          channel: str = "public", target: str = None):
        """推送聊天消息到 TUI（自动判断线程）"""
        chat_data = {
            "sender": sender,
            "content": content,
            "channel": channel,
            "target": target,
        }
        if self._thread_id == threading.get_ident():
            self._handle_chat(chat_data)
        else:
            self.call_from_thread(self._handle_chat, chat_data)

    # ──────────────────────────────────────────
    #  命令处理
    # ──────────────────────────────────────────

    _PENDING_ACTION_LABELS = {
        "command": "行动指令",
        "choose": "选择",
        "choose_multi": "多选",
        "confirm": "确认",
    }

    def _log_pending_chat_feedback(self) -> None:
        """聊天/私聊期间若仍有待响应请求，给出「不消耗行动」反馈。"""
        with self._pending_request_lock:
            pending = self._pending_request
        if not (pending and self.client):
            return
        label = self._PENDING_ACTION_LABELS.get(pending[0], pending[0])
        self._log_to_game(f"  [聊天已发送，{label}仍在等候你输入]")

    def on_command_submitted(self, event: CommandSubmitted):
        if event.cmd_type == "chat":
            self._send_chat(event.value, "public")
            self._log_pending_chat_feedback()
        elif event.cmd_type == "whisper":
            self._send_chat(event.value, "private", event.target)
            self._log_pending_chat_feedback()
        elif event.cmd_type == "management":
            self._handle_management_cmd(event.value)
        else:
            # "game" 类型命令
            # 客户端模式：优先响应来自服务器的 pending request
            with self._pending_request_lock:
                pending = self._pending_request
            if pending and self.client:
                self._respond_to_pending(pending, event.value)
            # 否则：服务端 TUI 的同步等待已由 CommandInput.on_input_submitted
            # 在 post_message 时同步唤醒（_pending_value / _pending_event）。
            # 这里不再重复 set，避免对非消耗回合的 status/help/allstatus 等
            # 在游戏线程已重新进入 wait_for_input 后被异步重复触发。

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
                self._chat_panel.add_message(
                    host_name, content, channel, target,
                    self_name=self._self_name(),
                )

    # ──────────────────────────────────────────
    #  房主管理
    # ──────────────────────────────────────────

    # ──────────────────────────────────────────
    #  房主管理命令（游戏开始前）
    # ──────────────────────────────────────────

    def _handle_management_cmd(self, raw: str):
        """处理房主管理命令（ai/rl/policy/status）"""
        if not self.is_host or not self.lobby:
            self._log_to_game("  [管理] 非房主或大厅未就绪，无法执行管理命令")
            return

        parts = raw.split()
        if not parts:
            return
        cmd = parts[0].lower()

        if cmd == "status":
            self._refresh_host_panel()
            info = self.lobby.get_lobby_info()
            self._log_to_game(f"  房间状态: {info['room_state']}")

        elif cmd == "ai":
            if len(parts) < 2:
                self._log_to_game("  用法: ai <slot_id> [性格]")
                return
            try:
                slot_id = int(parts[1])
            except ValueError:
                self._log_to_game("  无效的 slot_id")
                return
            try:
                from engine.game_setup import AI_PERSONALITIES
            except Exception:
                AI_PERSONALITIES = (
                    "balanced", "aggressive", "defensive",
                    "political", "assassin", "builder",
                )
            personality = parts[2] if len(parts) >= 3 else "balanced"
            if personality not in AI_PERSONALITIES:
                self._log_to_game(f"  可选性格: {', '.join(AI_PERSONALITIES)}")
                return
            try:
                ok = self.lobby.set_slot_ai(slot_id, "basic", personality)
            except Exception as e:
                self._log_to_game(f"  ✗ 设置失败: {e}")
                return
            if ok:
                self._refresh_host_panel()
                self._log_to_game(f"  ✓ Slot {slot_id} 设为 AI ({personality})")
            else:
                self._log_to_game("  ✗ 设置失败（槽位不可用）")

        elif cmd == "rl":
            if len(parts) < 2:
                self._log_to_game("  用法: rl <slot_id>")
                return
            try:
                slot_id = int(parts[1])
            except ValueError:
                self._log_to_game("  无效的 slot_id")
                return
            try:
                from network.rl_detect import detect_rl_availability
                rl_info = detect_rl_availability()
            except Exception as e:
                self._log_to_game(f"  ✗ RL 检测失败: {e}")
                return
            if not rl_info["available"]:
                self._log_to_game("  ✗ RL 不可用（缺少模型或依赖）")
                return
            model = rl_info["models"][0] if rl_info["models"] else None
            try:
                ok = self.lobby.set_slot_ai(slot_id, "rl", rl_model_path=model)
            except Exception as e:
                self._log_to_game(f"  ✗ 设置失败: {e}")
                return
            if ok:
                self._refresh_host_panel()
                self._log_to_game(f"  ✓ Slot {slot_id} 设为 RL AI")
            else:
                self._log_to_game("  ✗ 设置失败（槽位不可用）")

        elif cmd == "policy":
            if len(parts) < 3:
                self._log_to_game("  用法: policy <slot_id> <wait|ai>")
                return
            try:
                slot_id = int(parts[1])
            except ValueError:
                self._log_to_game("  无效的 slot_id")
                return
            try:
                from network.lobby import DisconnectPolicy
            except Exception as e:
                self._log_to_game(f"  ✗ 导入失败: {e}")
                return
            policy_str = parts[2].lower()
            if policy_str == "wait":
                try:
                    self.lobby.set_disconnect_policy(slot_id, DisconnectPolicy.WAIT_RECONNECT)
                except Exception as e:
                    self._log_to_game(f"  ✗ 设置失败: {e}")
                    return
                self._refresh_host_panel()
                self._log_to_game(f"  ✓ Slot {slot_id} 断线策略: 等待重连")
            elif policy_str == "ai":
                try:
                    self.lobby.set_disconnect_policy(slot_id, DisconnectPolicy.AI_TAKEOVER)
                except Exception as e:
                    self._log_to_game(f"  ✗ 设置失败: {e}")
                    return
                self._refresh_host_panel()
                self._log_to_game(f"  ✓ Slot {slot_id} 断线策略: AI 接管")
            else:
                self._log_to_game("  可选策略: wait (等待重连), ai (AI接管)")

        elif cmd == "chatmode":
            if len(parts) < 3:
                self._log_to_game("  用法: chatmode <slot_id> <airi|llm|off>")
                self._log_to_game("    airi - 使用 AIRI WebSocket 后端")
                self._log_to_game("    llm  - 使用普通 LLM 后端")
                self._log_to_game("    off  - 该 AI 不参与聊天")
                return
            try:
                slot_id = int(parts[1])
            except ValueError:
                self._log_to_game("  无效的 slot_id")
                return
            backend_choice = parts[2].lower()
            if backend_choice not in ("airi", "llm", "off"):
                self._log_to_game("  可选: airi / llm / off")
                return
            if self.lobby.set_chat_backend(slot_id, backend_choice):
                self._refresh_host_panel()
                self._log_to_game(f"  ✓ Slot {slot_id} 聊天后端设为: {backend_choice}")
            else:
                self._log_to_game("  ✗ 设置失败（槽位不是 AI 类型）")

        elif cmd == "debug":
            if len(parts) < 2:
                self._log_to_game("  用法: debug <0-3>")
                self._log_to_game(
                    "    0=关闭, 1=基本, 2=详细, 3=完整"
                )
                return
            try:
                level = int(parts[1])
            except ValueError:
                self._log_to_game("  无效的调试级别（应为整数 0-3）")
                return
            if level < 0 or level > 3:
                self._log_to_game("  调试级别范围: 0-3")
                return
            try:
                from engine.debug_config import DebugConfig
            except Exception as e:
                self._log_to_game(f"  ✗ 加载调试配置失败: {e}")
                return
            DebugConfig.set_debug_mode(level > 0, max(level, 1))
            self._refresh_host_panel()
            if level == 0:
                self._log_to_game("  ✓ 调试模式已关闭")
            else:
                self._log_to_game(f"  ✓ 调试级别设为: {level}")

        elif cmd == "name":
            if len(parts) < 2:
                self._log_to_game("  用法: name <新名字>")
                return
            new_name = " ".join(parts[1:]).strip()
            if not new_name:
                self._log_to_game("  名字不能为空")
                return
            try:
                from network.lobby import SlotType
            except Exception as e:
                self._log_to_game(f"  ✗ 导入失败: {e}")
                return
            host_slot = self.lobby.slots[0] if self.lobby.slots else None
            if host_slot is None or host_slot.slot_type != SlotType.HUMAN_LOCAL:
                self._log_to_game("  ✗ 房主不参与游戏，无法修改ID")
                return
            old_name = host_slot.player_name or "房主"
            host_slot.player_name = new_name
            self._refresh_host_panel()
            self._log_to_game(f"  ✓ 房主ID: {old_name} → {new_name}")

        elif cmd == "start":
            # 与「开始游戏」按钮等价：触发 SlotConfigRequest(action="start_game")
            self.post_message(SlotConfigRequest(slot_id=0, action="start_game"))

        else:
            self._log_to_game(f"  未知管理命令: {raw}")
            self._log_to_game(
                "  可用: ai <slot> [性格] | rl <slot> | policy <slot> <wait|ai> | chatmode <slot> <airi|llm|off> | debug <0-3> | name <新名字> | status | start"
            )

    def _refresh_host_panel(self):
        try:
            host_panel = self.query_one(HostPanel)
            host_panel.refresh_slots()
        except NoMatches:
            pass

    def _log_to_game(self, text: str):
        """写入命令反馈：游戏日志区 + 房主面板（大厅模式下日志区被隐藏）。"""
        try:
            log = self.query_one("#game-log", GameLogWidget)
            log.write(text)
        except NoMatches:
            pass
        # 大厅模式下游戏日志区被隐藏，把反馈也写到房主面板上
        if self.is_host:
            try:
                host_panel = self.query_one(HostPanel)
                host_panel.log_feedback(text)
            except NoMatches:
                pass

    def _switch_to_game_mode(self):
        """游戏开始后切换布局：隐藏 host panel，显示 game log。"""
        try:
            host_area = self.query_one("#host-area")
            host_area.remove_class("lobby-mode")
        except NoMatches:
            pass
        try:
            game_log = self.query_one("#game-log", GameLogWidget)
            game_log.remove_class("lobby-mode-hidden")
        except NoMatches:
            pass

    def on_slot_config_request(self, event: SlotConfigRequest):
        if event.action == "start_game" and self.lobby:
            # 注意：大厅模式下 #game-log 被隐藏，必须用 _log_to_game 把消息也送到房主面板
            if self.lobby.can_start() and self.lobby.state.value == "waiting" and not self._game_starting:
                self._game_starting = True
                self._log_to_game("  [系统] 游戏即将开始...")
                if self.start_game_callback:
                    threading.Thread(
                        target=self.start_game_callback,
                        daemon=True,
                    ).start()
            elif self.lobby.state.value != "waiting":
                self._log_to_game("  [系统] 游戏已在进行中")
            elif self._game_starting:
                self._log_to_game("  [系统] 游戏正在启动中...")
            else:
                self._log_to_game("  [系统] 还有空位未填满，无法开始")

    # ──────────────────────────────────────────
    #  帮助
    # ──────────────────────────────────────────

    def action_show_help(self):
        """F1 显示帮助"""
        try:
            log = self.query_one("#game-log", GameLogWidget)
            log.write("")
            log.write("  ─── 指令帮助 ───")
            log.write("  游戏指令（在你的行动回合输入）：")
            log.write("    move <地点>              - 移动到其他地点")
            log.write("    interact <项目名>         - 与当前地点交互")
            log.write("    lock <玩家名>             - 锁定目标（远程前置）")
            log.write("    find <玩家名>             - 找到目标（近战前置）")
            log.write("    attack <目标> <武器> [层 属性] - 攻击")
            log.write("    special <操作名>          - 特殊操作")
            log.write("    forfeit                  - 放弃行动")
            log.write("  警察系统：")
            log.write("    report / assemble / track / recruit / election / designate / split / study")
            log.write("  查看（不消耗行动）：")
            log.write("    status / allstatus / police / help")
            log.write("  聊天：")
            log.write("    /chat <内容>             - 公屏聊天")
            log.write("    /whisper <玩家名> <内容>   - 私聊")
            log.write("  快捷键：F1 帮助 | Ctrl+Q 退出")
            log.write("")
        except NoMatches:
            pass

    # ──────────────────────────────────────────
    #  外部日志写入
    # ──────────────────────────────────────────

    def write_log(self, text: str):
        """写入日志到游戏日志区（自动判断线程）"""
        try:
            log = self.query_one("#game-log", GameLogWidget)
            if self._thread_id == threading.get_ident():
                log.write(text)
            else:
                self.call_from_thread(log.write, text)
        except NoMatches:
            pass
