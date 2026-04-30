"""
DisplayBroadcaster —— 网络显示桥接
══════════════════════════════════════
替换 cli/display.py 中的函数，将显示消息通过网络发送。
与 stats_runner.py 的 monkey-patch 模式一致。

信息可见性规则：
- 广播：show_round_header, show_action_turn_header, show_result, show_death,
  show_victory, show_police_status, show_virus_status 等
- 定向：show_player_status, show_available_actions, prompt_input,
  prompt_secret, prompt_choice 等
"""

import threading
from typing import Any, Optional

from cli import display as _display_module
from network.protocol import MessageType


# 使用 threading.local 追踪"当前玩家"
_current_context = threading.local()

_original_display: dict[str, Any] = {}

# 表示"当前回合属于 AI，不应把定向信息显示到房主控制台，也无需网络发送"。
# 与 client_id is None（房主自己的回合）和 client_id == <某远程 id>（远程玩家）区分。
AI_CONTEXT_SENTINEL = "__ai__"


def get_current_client_id() -> Optional[str]:
    return getattr(_current_context, "client_id", None)


def set_current_context(client_id: Optional[str], player_name: Optional[str] = None):
    _current_context.client_id = client_id
    _current_context.player_name = player_name


class DisplayBroadcaster:
    def __init__(self, server: Any, lobby: Any):
        self.server = server
        self.lobby = lobby
        self._tui_callback = None
        self._tui_app = None

    def set_tui_callback(self, callback, app=None):
        """设置 TUI 回调，替代 print() 进行房主本地显示"""
        self._tui_callback = callback
        self._tui_app = app

    def install(self):
        # 广播类函数
        broadcast_funcs = {
            "show_banner": self._make_broadcast("show_banner"),
            "show_round_header": self._make_broadcast("show_round_header"),
            "show_phase": self._make_broadcast("show_phase"),
            "show_d4_results": self._make_broadcast("show_d4_results"),
            "show_action_turn_header": self._make_broadcast("show_action_turn_header"),
            "show_result": self._make_broadcast("show_result"),
            "show_error": self._make_broadcast("show_error"),
            "show_info": self._make_broadcast("show_info"),
            "show_victory": self._make_broadcast("show_victory"),
            "show_death": self._make_broadcast("show_death"),
            "show_police_status": self._make_broadcast("show_police_status"),
            "show_virus_status": self._make_broadcast("show_virus_status"),
            "show_police_enforcement": self._make_broadcast("show_police_enforcement"),
            "show_virus_deaths": self._make_broadcast("show_virus_deaths"),
            "show_all_players_status": self._make_broadcast("show_all_players_status"),
            "show_help": self._make_broadcast("show_help"),
            "show_critical": self._make_broadcast("show_critical"),
            "show_warning": self._make_broadcast("show_warning"),
            "show_prompt": self._make_broadcast("show_prompt"),
            "clear_screen": self._make_broadcast("clear_screen"),
        }

        # 定向类函数
        directed_funcs = {
            "show_player_status": self._make_directed("show_player_status"),
            "show_available_actions": self._make_directed("show_available_actions"),
        }

        # 保存原始函数并替换
        for name, func in {**broadcast_funcs, **directed_funcs}.items():
            if hasattr(_display_module, name):
                _original_display[name] = getattr(_display_module, name)
                setattr(_display_module, name, func)

        # TUI 模式下替换 prompt 函数，让房主输入通过 TUI 输入框获取
        if self._tui_callback and self._tui_app:
            for pname in ("prompt_input", "prompt_choice", "prompt_secret"):
                if hasattr(_display_module, pname):
                    _original_display[pname] = getattr(_display_module, pname)

            def tui_prompt_input(player_name):
                self._tui_callback({
                    "event": "show_prompt",
                    "args": [f"[{player_name}] 请输入指令 >"],
                })
                cmd_input = self._tui_app.query_one("#cmd-input")
                return cmd_input.wait_for_input(timeout=300)

            def tui_prompt_choice(prompt_text, options):
                lines = [f"  {prompt_text}"]
                for i, opt in enumerate(options, 1):
                    lines.append(f"    {i}. {opt}")
                self._tui_callback({
                    "event": "show_info",
                    "args": ["\n".join(lines)],
                })
                cmd_input = self._tui_app.query_one("#cmd-input")
                while True:
                    raw = cmd_input.wait_for_input(timeout=300)
                    try:
                        idx = int(raw) - 1
                        if 0 <= idx < len(options):
                            return options[idx]
                    except ValueError:
                        pass
                    if raw in options:
                        return raw
                    for opt in options:
                        if raw.lower() in opt.lower():
                            return opt
                    self._tui_callback({
                        "event": "show_error",
                        "args": ["请输入有效的选项。"],
                    })

            def tui_prompt_secret(prompt_text):
                self._tui_callback({
                    "event": "show_prompt",
                    "args": [f"🔒 {prompt_text} >"],
                })
                cmd_input = self._tui_app.query_one("#cmd-input")
                return cmd_input.wait_for_input(timeout=300)

            setattr(_display_module, "prompt_input", tui_prompt_input)
            setattr(_display_module, "prompt_choice", tui_prompt_choice)
            setattr(_display_module, "prompt_secret", tui_prompt_secret)

        # 非 TUI 模式下 prompt 函数不替换 —— 由 NetworkController 处理

    def uninstall(self):
        for name, func in _original_display.items():
            setattr(_display_module, name, func)
        _original_display.clear()

    def _make_broadcast(self, func_name: str):
        def wrapper(*args, **kwargs):
            # 本地房主也能看到
            if self.lobby.host_plays:
                if self._tui_callback:
                    self._tui_callback({
                        "event": func_name,
                        "args": _serialize_args(args),
                        "kwargs": _serialize_kwargs(kwargs),
                    })
                else:
                    original = _original_display.get(func_name)
                    if original:
                        try:
                            original(*args, **kwargs)
                        except Exception:
                            pass

            # 网络广播
            msg = {
                "type": MessageType.GAME_EVENT,
                "event": func_name,
                "args": _serialize_args(args),
                "kwargs": _serialize_kwargs(kwargs),
            }
            try:
                self.server.broadcast_sync(msg)
            except Exception:
                pass

        return wrapper

    def _make_directed(self, func_name: str):
        def wrapper(*args, **kwargs):
            client_id = get_current_client_id()

            # AI 回合：既不在房主本地显示（防泄露私密信息），也不通过网络发送
            if client_id == AI_CONTEXT_SENTINEL:
                return

            # 本地房主：仅当定向目标是房主自己时才本地显示
            if self.lobby.host_plays and client_id is None:
                if self._tui_callback:
                    self._tui_callback({
                        "event": func_name,
                        "args": _serialize_args(args),
                        "kwargs": _serialize_kwargs(kwargs),
                        "directed": True,
                    })
                else:
                    original = _original_display.get(func_name)
                    if original:
                        try:
                            original(*args, **kwargs)
                        except Exception:
                            pass

            # 定向发送给远程客户端
            if client_id and client_id != AI_CONTEXT_SENTINEL:
                msg = {
                    "type": MessageType.GAME_EVENT,
                    "event": func_name,
                    "args": _serialize_args(args),
                    "kwargs": _serialize_kwargs(kwargs),
                    "directed": True,
                }
                try:
                    self.server.send_to_sync(client_id, msg)
                except Exception:
                    pass

        return wrapper


def _serialize_args(args: tuple) -> list:
    result = []
    for a in args:
        if hasattr(a, "describe_status"):
            result.append({"_type": "player", "status": a.describe_status(), "name": a.name})
        elif hasattr(a, "police") and hasattr(a, "virus"):
            result.append({"_type": "game_state", "round": a.current_round})
        else:
            try:
                import json
                json.dumps(a)
                result.append(a)
            except (TypeError, ValueError):
                result.append(str(a))
    return result


def _serialize_kwargs(kwargs: dict) -> dict:
    result = {}
    for k, v in kwargs.items():
        try:
            import json
            json.dumps(v)
            result[k] = v
        except (TypeError, ValueError):
            result[k] = str(v)
    return result
