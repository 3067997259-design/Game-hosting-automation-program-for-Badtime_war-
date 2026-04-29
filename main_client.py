"""
客户端入口
═══════════
连接到房主服务器 → 显示客户端 TUI → 等待游戏开始 → 接收事件/发送命令
"""

import argparse
import queue
import sys
import threading
import time
from typing import Optional


_STDIN_EOF = None  # sentinel


def _start_stdin_reader() -> queue.Queue:
    """启动后台线程读取 stdin 行，通过 queue 传递给主线程（跨平台）。"""
    q: queue.Queue = queue.Queue()

    def _reader():
        try:
            for line in sys.stdin:
                q.put(line.rstrip("\n"))
        except (EOFError, OSError):
            pass
        finally:
            q.put(_STDIN_EOF)

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    return q


def _read_line(stdin_q: queue.Queue) -> Optional[str]:
    """从 stdin queue 读取一行，EOF 时返回 None。"""
    line = stdin_q.get()
    if line is _STDIN_EOF:
        stdin_q.put(_STDIN_EOF)  # 放回哨兵供后续调用者立即感知
        return None
    return line

from network.client import NetworkClient
from network.protocol import MessageType


def main():
    parser = argparse.ArgumentParser(description="起闯战争 - 客户端")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="服务器地址")
    parser.add_argument("--port", type=int, default=9527, help="服务器端口（默认 9527）")
    parser.add_argument("--name", type=str, default=None, help="玩家名称")
    parser.add_argument("--tui", action="store_true", help="使用 Textual TUI")
    args = parser.parse_args()

    player_name = args.name
    if not player_name:
        player_name = input("  请输入你的名字: ").strip()
        if not player_name:
            player_name = "远程玩家"

    print(f"\n  ═══════════════════════════════════════")
    print(f"    起闯战争 - 客户端")
    print(f"  ═══════════════════════════════════════")
    print(f"  服务器: {args.host}:{args.port}")
    print(f"  玩家: {player_name}")
    print()

    # 连接服务器
    client = NetworkClient(host=args.host, port=args.port)
    try:
        client.connect(player_name)
        print(f"  [Client] 已连接到服务器")
    except ConnectionError as e:
        print(f"  [Client] 连接失败: {e}")
        sys.exit(1)

    if args.tui:
        _run_with_tui(client, player_name)
    else:
        _run_cli_mode(client, player_name)


def _run_cli_mode(client: NetworkClient, player_name: str):
    """CLI 模式：单线程 stdin，避免多线程竞争输入。"""
    game_started = threading.Event()
    game_finished = threading.Event()
    # 挂起的服务器请求（由消息处理器填充，主线程消费）
    pending_request = {"msg": None, "msg_type": None}
    pending_lock = threading.Lock()
    pending_event = threading.Event()

    # 注册事件处理器
    def on_game_event(msg):
        event = msg.get("event", "")
        args = msg.get("args", [])
        _print_event(event, args)
        if event == "game_finished":
            game_finished.set()
            pending_event.set()  # 唤醒主循环

    def on_lobby_update(msg):
        state = msg.get("room_state", "")
        if state == "in_game":
            print("\n  [系统] 游戏开始！")
            game_started.set()
        slots = msg.get("slots", [])
        for s in slots:
            print(f"    [{s['slot_id']}] {s['slot_type']:12s} | "
                  f"{s.get('player_name', '空'):10s}")

    def on_chat(msg):
        sender = msg.get("sender", "")
        content = msg.get("content", "")
        channel = msg.get("channel", "public")
        prefix = "[私聊]" if channel == "private" else "[公屏]"
        print(f"  {prefix} {sender}: {content}")

    def on_disconnect(msg):
        name = msg.get("player_name", "")
        action = msg.get("action", "")
        print(f"  [断线] {name}: {action}")

    # 服务器请求统一通过 pending_request 传递给主线程
    def on_server_request(msg_type):
        def handler(msg):
            with pending_lock:
                pending_request["msg"] = msg
                pending_request["msg_type"] = msg_type
            pending_event.set()
        return handler

    client.on(MessageType.GAME_EVENT, on_game_event)
    client.on(MessageType.LOBBY_UPDATE, on_lobby_update)
    client.on(MessageType.CHAT_MESSAGE, on_chat)
    client.on(MessageType.DISCONNECT_NOTICE, on_disconnect)
    client.on(MessageType.REQUEST_COMMAND, on_server_request(MessageType.REQUEST_COMMAND))
    client.on(MessageType.REQUEST_CHOOSE, on_server_request(MessageType.REQUEST_CHOOSE))
    client.on(MessageType.REQUEST_CHOOSE_MULTI, on_server_request(MessageType.REQUEST_CHOOSE_MULTI))
    client.on(MessageType.REQUEST_CONFIRM, on_server_request(MessageType.REQUEST_CONFIRM))

    print("  等待游戏开始...（输入 /chat <内容> 发送聊天）")
    game_started.wait()

    # 后台线程读 stdin，通过 queue 传递完整行（跨平台，不阻塞主循环）
    stdin_q = _start_stdin_reader()
    idle_prompted = False
    try:
        while client.is_connected and not game_finished.is_set():
            # 检查是否有挂起的服务器请求
            with pending_lock:
                req_msg = pending_request["msg"]
                req_type = pending_request["msg_type"]
                pending_request["msg"] = None
                pending_request["msg_type"] = None

            if req_msg is not None:
                _handle_request(client, req_msg, req_type, player_name, stdin_q)
                pending_event.clear()
                idle_prompted = False
                continue

            # 没有挂起请求时，提示输入（聊天或等待）
            if not idle_prompted:
                print("  (等待服务器指令... 输入 /chat <内容> 聊天)")
                idle_prompted = True
            # 用短超时轮询，以便及时响应服务器请求
            pending_event.wait(timeout=0.5)
            if pending_event.is_set():
                pending_event.clear()
                continue

            # 无挂起请求 → 非阻塞检查 stdin queue
            try:
                raw = stdin_q.get_nowait()
            except queue.Empty:
                continue
            if raw is _STDIN_EOF:
                break
            if raw.strip():
                _handle_chat_input(client, raw.strip(), player_name)

    except KeyboardInterrupt:
        pass
    finally:
        client.disconnect()
        print("\n  已断开连接。")


def _handle_chat_input(client, raw: str, player_name: str):
    """处理聊天输入（仅主线程调用）。"""
    if raw.startswith("/chat "):
        client.send_sync({
            "type": MessageType.CHAT_SEND,
            "sender": player_name,
            "content": raw[6:],
            "channel": "public",
        })
    elif raw.startswith("/whisper "):
        parts = raw[9:].split(" ", 1)
        if len(parts) >= 2:
            client.send_sync({
                "type": MessageType.CHAT_SEND,
                "sender": player_name,
                "content": parts[1],
                "channel": "private",
                "target": parts[0],
            })
    else:
        print("  提示: /chat <内容> 公屏聊天, /whisper <玩家名> <内容> 私聊")


def _handle_request(client, msg, msg_type, player_name, stdin_q: queue.Queue):
    """处理服务器发来的请求（所有 stdin 读取均通过 stdin_q）。"""
    if msg_type == MessageType.REQUEST_COMMAND:
        print(f"\n  [{player_name}] 请输入指令:")
        actions = msg.get("available_actions", [])
        if actions:
            print(f"  可选行动: {', '.join(actions)}")
        print(f"  [{player_name}] > ", end="", flush=True)
        raw = _read_line(stdin_q)
        if raw is None:
            raw = "forfeit"
        client.send_sync({
            "type": MessageType.COMMAND_RESPONSE,
            "command": raw.strip() or "forfeit",
        })

    elif msg_type == MessageType.REQUEST_CHOOSE:
        prompt = msg.get("prompt", "请选择")
        options = msg.get("options", [])
        print(f"\n  {prompt}")
        for i, opt in enumerate(options, 1):
            print(f"    {i}. {opt}")
        while True:
            print("  请选择（编号）> ", end="", flush=True)
            raw = _read_line(stdin_q)
            if raw is None:
                choice = options[0] if options else ""
                client.send_sync({"type": MessageType.CHOOSE_RESPONSE, "choice": choice})
                return
            raw = raw.strip()
            try:
                idx = int(raw) - 1
                if 0 <= idx < len(options):
                    client.send_sync({
                        "type": MessageType.CHOOSE_RESPONSE,
                        "choice": options[idx],
                    })
                    break
            except ValueError:
                if raw in options:
                    client.send_sync({
                        "type": MessageType.CHOOSE_RESPONSE,
                        "choice": raw,
                    })
                    break
            print("  无效选择，请重试。")

    elif msg_type == MessageType.REQUEST_CHOOSE_MULTI:
        prompt = msg.get("prompt", "请选择")
        options = msg.get("options", [])
        max_count = msg.get("max_count", 1)
        min_count = msg.get("min_count", 0)
        print(f"\n  {prompt} (选 {min_count}~{max_count} 个)")
        for i, opt in enumerate(options, 1):
            print(f"    {i}. {opt}")
        selected = []
        while len(selected) < max_count:
            print(f"  选择（已选{len(selected)}/{max_count}，输入0结束）> ", end="", flush=True)
            raw = _read_line(stdin_q)
            if raw is None:
                break
            raw = raw.strip()
            if raw == "0" and len(selected) >= min_count:
                break
            try:
                idx = int(raw) - 1
                if 0 <= idx < len(options) and options[idx] not in selected:
                    selected.append(options[idx])
            except ValueError:
                pass
        client.send_sync({
            "type": MessageType.CHOOSE_MULTI_RESPONSE,
            "choices": selected,
        })

    elif msg_type == MessageType.REQUEST_CONFIRM:
        prompt = msg.get("prompt", "确认？")
        print(f"  {prompt} (y/n) > ", end="", flush=True)
        raw = _read_line(stdin_q)
        if raw is None:
            raw = "n"
        raw = raw.strip().lower()
        client.send_sync({
            "type": MessageType.CONFIRM_RESPONSE,
            "result": raw in ("y", "yes", "是"),
        })


def _print_event(func: str, args: list):
    """在 CLI 中打印游戏事件。"""
    if func == "show_round_header":
        rn = args[0] if args else "?"
        print(f"\n{'='*50}\n  全局轮次 {rn}\n{'='*50}")
    elif func == "show_phase":
        print(f"\n--- {args[0] if args else ''} ---")
    elif func == "show_action_turn_header":
        name = args[0] if args else "?"
        print(f"\n{'─'*40}\n  轮到 {name} 行动\n{'─'*40}")
    elif func == "show_result":
        if args:
            print(f"  {args[0]}")
    elif func == "show_info":
        if args:
            print(f"  {args[0]}")
    elif func == "show_error":
        if args:
            print(f"  [错误] {args[0]}")
    elif func == "show_victory":
        name = args[0] if args else "?"
        print(f"\n  {name} 获得了最终胜利！")
    elif func == "show_death":
        name = args[0] if args else "?"
        cause = args[1] if len(args) > 1 else "未知"
        print(f"  {name} 死亡！原因：{cause}")
    elif func == "show_player_status":
        if args and isinstance(args[0], dict):
            print(f"  {args[0].get('status', '')}")
    elif func == "clear_screen":
        pass
    elif func == "game_finished":
        print("\n  [系统] 游戏结束！")


def _run_with_tui(client, player_name):
    """Textual TUI 模式。"""
    try:
        from tui.app import BadtimeWarTUI
    except ImportError:
        print("  [错误] 需要安装 textual: pip install textual")
        _run_cli_mode(client, player_name)
        return

    app = BadtimeWarTUI(
        is_host=False,
        client=client,
    )
    app.run()
    client.disconnect()


if __name__ == "__main__":
    main()
