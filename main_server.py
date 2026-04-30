"""
房主服务器入口
═══════════════
启动 TCP 服务器 → 显示房主 TUI → 等待玩家加入 → 配置 AI → 开始游戏
游戏循环在独立线程中运行（与 rl/env.py 的 _run_game 模式一致）
"""

import argparse
import platform
import random
import select
import sys
import threading
import time
from typing import Optional

from network.server import NetworkServer
from network.lobby import LobbyManager, SlotType, DisconnectPolicy
from network.display_bridge import DisplayBroadcaster, set_current_context
from network.chat_manager import ChatManager
from network.disconnect import DisconnectMonitor
from network.protocol import MessageType
from engine.round_manager import RoundManager
from engine.game_setup import (
    TALENT_TABLE, AI_PERSONALITIES, _talent_selection,
)
from ai_chat.llm_backend import create_backend
from ai_chat.ai_chatter import AIChatModule


def main():
    parser = argparse.ArgumentParser(description="起闯战争 - 房主服务器")
    parser.add_argument("--port", type=int, default=9527, help="监听端口（默认 9527）")
    parser.add_argument("--players", type=int, default=2, help="总人数（2-6，默认 2）")
    parser.add_argument("--no-host-play", action="store_true", help="房主不参与游戏")
    parser.add_argument("--tui", action="store_true", help="使用 Textual TUI（需安装 textual）")
    args = parser.parse_args()

    total_players = max(2, min(6, args.players))
    host_plays = not args.no_host_play

    print(f"\n  ═══════════════════════════════════════")
    print(f"    起闯战争 - 局域网联机服务器")
    print(f"  ═══════════════════════════════════════")
    print(f"  端口: {args.port}")
    print(f"  人数: {total_players}")
    print(f"  房主参与: {'是' if host_plays else '否'}")
    print()

    # 启动网络服务器
    server = NetworkServer(port=args.port)
    server.start()
    print(f"  [Server] 服务器已启动，监听端口 {args.port}")

    # 创建大厅
    lobby = LobbyManager(total_players, host_plays, server)

    # 创建聊天管理器
    chat_manager = ChatManager(server, lobby)

    # 处理客户端消息
    def on_message(client_id: str, msg: dict):
        msg_type = msg.get("type", "")
        if msg_type == MessageType.LOBBY_JOIN:
            name = msg.get("player_name", f"玩家_{client_id}")
            slot = lobby.on_player_join(client_id, name)
            if slot:
                server.set_client_name(client_id, name)
                print(f"  [Lobby] {name} 加入了房间 (slot {slot.slot_id})")
            else:
                server.send_to_sync(client_id, {
                    "type": MessageType.DISCONNECT_NOTICE,
                    "reason": "房间已满",
                })
        elif msg_type == MessageType.CHAT_SEND:
            chat_manager.handle_chat(client_id, msg)
        elif msg_type == MessageType.RECONNECT:
            name = msg.get("player_name", "")
            if lobby.handle_reconnect(client_id, name):
                print(f"  [Lobby] {name} 重连成功")
            else:
                print(f"  [Lobby] {name} 重连失败")

    server._on_message = on_message

    # 处理断线
    def on_disconnect(client_id: str):
        if lobby.state.value == "in_game":
            lobby.handle_disconnect(client_id)
        else:
            lobby.on_player_leave(client_id)

    server._on_client_disconnect = on_disconnect

    # 断线检测
    monitor = DisconnectMonitor(server, lobby)
    monitor.start()

    # 根据是否启用 TUI 决定交互模式
    if args.tui:
        _run_with_tui(server, lobby, chat_manager, host_plays, monitor)
    else:
        _run_cli_mode(server, lobby, chat_manager, host_plays, monitor)


def _handle_host_chat_input(raw: str, chat_manager: ChatManager, lobby):
    """解析房主的聊天输入并发送。返回 True 表示已处理。"""
    host_name = "房主"
    for slot in lobby.slots:
        if slot.slot_type == SlotType.HUMAN_LOCAL and slot.player_name:
            host_name = slot.player_name
            break

    if raw.startswith("/chat "):
        content = raw[6:].strip()
        if content:
            chat_manager.handle_host_chat(host_name, content, "public")
        return True
    elif raw.startswith("/whisper "):
        parts = raw[9:].strip().split(" ", 1)
        if len(parts) >= 2:
            target, content = parts[0], parts[1]
            chat_manager.handle_host_chat(host_name, content, "private", target)
        else:
            print("  用法: /whisper <玩家名> <内容>")
        return True
    return False


def _run_cli_mode(server, lobby, chat_manager, host_plays, monitor):
    """CLI 模式：简单的命令行交互。"""
    print("\n  ─── 大厅管理（输入命令）───")
    print("  命令：")
    print("    status    - 查看房间状态")
    print("    ai <slot> [personality] - 设置 AI")
    print("    rl <slot> - 设置 RL AI")
    print("    policy <slot> <wait|ai> - 设置断线策略")
    print("    start     - 开始游戏")
    print("    /chat <内容>           - 公屏聊天")
    print("    /whisper <玩家名> <内容> - 私聊")
    print("    quit      - 退出")
    print()

    while lobby.state.value == "waiting":
        try:
            raw = input("  [房主] > ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not raw:
            continue

        # 拦截聊天命令
        if _handle_host_chat_input(raw, chat_manager, lobby):
            continue

        parts = raw.split()
        cmd = parts[0].lower()

        if cmd == "status":
            info = lobby.get_lobby_info()
            print(f"\n  房间状态: {info['room_state']}")
            for s in info["slots"]:
                print(f"    [{s['slot_id']}] {s['slot_type']:12s} | "
                      f"{s['player_name'] or '空':10s} | "
                      f"{'已连接' if s['is_connected'] else '未连接'}")
            print()

        elif cmd == "ai":
            if len(parts) < 2:
                print("  用法: ai <slot_id> [personality]")
                continue
            try:
                slot_id = int(parts[1])
            except ValueError:
                print("  无效的 slot_id")
                continue
            personality = parts[2] if len(parts) > 2 else "balanced"
            if personality not in AI_PERSONALITIES:
                print(f"  可选性格: {', '.join(AI_PERSONALITIES)}")
                continue
            if lobby.set_slot_ai(slot_id, "basic", personality):
                print(f"  Slot {slot_id} 设为 AI ({personality})")
            else:
                print("  设置失败（槽位不可用）")

        elif cmd == "rl":
            if len(parts) < 2:
                print("  用法: rl <slot_id>")
                continue
            try:
                slot_id = int(parts[1])
            except ValueError:
                print("  无效的 slot_id")
                continue
            from network.rl_detect import detect_rl_availability
            rl_info = detect_rl_availability()
            if rl_info["available"]:
                model = rl_info["models"][0] if rl_info["models"] else None
                if lobby.set_slot_ai(slot_id, "rl", rl_model_path=model):
                    print(f"  Slot {slot_id} 设为 RL AI")
                else:
                    print("  设置失败")
            else:
                print("  RL 不可用（缺少模型或依赖）")

        elif cmd == "policy":
            if len(parts) < 3:
                print("  用法: policy <slot_id> <wait|ai>")
                continue
            try:
                slot_id = int(parts[1])
            except ValueError:
                print("  无效的 slot_id")
                continue
            policy_str = parts[2].lower()
            if policy_str == "wait":
                lobby.set_disconnect_policy(slot_id, DisconnectPolicy.WAIT_RECONNECT)
                print(f"  Slot {slot_id} 断线策略: 等待重连")
            elif policy_str == "ai":
                lobby.set_disconnect_policy(slot_id, DisconnectPolicy.AI_TAKEOVER)
                print(f"  Slot {slot_id} 断线策略: AI 接管")
            else:
                print("  可选策略: wait (等待重连), ai (AI接管)")

        elif cmd == "start":
            if not lobby.can_start():
                print("  还有空位未填满，无法开始")
                continue
            break

        elif cmd == "quit":
            server.stop()
            monitor.stop()
            sys.exit(0)

    if lobby.state.value == "waiting":
        # 注册聊天回调，使 HumanController 在回合中也能处理 /chat、/whisper
        from controllers.human import set_chat_handler
        set_chat_handler(lambda raw: _handle_host_chat_input(raw, chat_manager, lobby))

        _start_game(server, lobby, chat_manager, host_plays)

    # 游戏阶段
    if host_plays:
        # 房主参与游戏：stdin 由游戏线程的 HumanController 持有，
        # 聊天命令已通过 set_chat_handler 在 HumanController 中拦截处理。
        # 主线程仅等待游戏结束。
        print("\n  ─── 游戏进行中 ───")
        print("  在你的回合中输入 /chat <内容> 或 /whisper <玩家名> <内容> 即可聊天\n")
        try:
            while lobby.state.value == "in_game":
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n  游戏被中断。")
        finally:
            set_chat_handler(None)
            monitor.stop()
            server.stop()
    else:
        # 房主不参与游戏（观战模式）：主线程可安全读取 stdin
        print("\n  ─── 游戏进行中 ───")
        print("  输入 /chat <内容> 公屏聊天，/whisper <玩家名> <内容> 私聊")
        print("  输入 status 查看状态，Ctrl+C 中断游戏\n")
        try:
            while lobby.state.value == "in_game":
                try:
                    if platform.system() == "Windows":
                        import msvcrt
                        if msvcrt.kbhit():
                            raw = input().strip()
                        else:
                            time.sleep(0.5)
                            continue
                    else:
                        readable, _, _ = select.select([sys.stdin], [], [], 1.0)
                        if not readable:
                            continue
                        raw_line = sys.stdin.readline()
                        if not raw_line:          # EOF — avoid busy-loop
                            time.sleep(1)
                            continue
                        raw = raw_line.strip()
                except EOFError:
                    continue

                if not raw:
                    continue

                # 聊天命令
                if _handle_host_chat_input(raw, chat_manager, lobby):
                    continue

                # 管理命令
                if raw.lower() == "status":
                    info = lobby.get_lobby_info()
                    print(f"\n  游戏状态: {info['room_state']}")
                    for s in info["slots"]:
                        print(f"    [{s['slot_id']}] {s['slot_type']:12s} | "
                              f"{s['player_name'] or '空':10s} | "
                              f"{'已连接' if s['is_connected'] else '未连接'}")
                    print()
                else:
                    print(f"  未知命令: {raw}（可用: /chat, /whisper, status）")

        except KeyboardInterrupt:
            print("\n  游戏被中断。")
        finally:
            set_chat_handler(None)
            monitor.stop()
            server.stop()


def _run_with_tui(server, lobby, chat_manager, host_plays, monitor):
    """Textual TUI 模式。"""
    try:
        from tui.app import BadtimeWarTUI
    except ImportError:
        print("  [错误] 需要安装 textual: pip install textual")
        _run_cli_mode(server, lobby, chat_manager, host_plays, monitor)
        return

    app = None

    def start_game_callback():
        """TUI 中点击"开始游戏"时的回调"""
        nonlocal app
        _start_game_tui(server, lobby, chat_manager, host_plays, app)

    app = BadtimeWarTUI(
        is_host=True,
        lobby=lobby,
        server=server,
        start_game_callback=start_game_callback,
        chat_manager=chat_manager,
    )

    # 注册 ChatManager 的 TUI 回调
    chat_manager.set_tui_callback(app.push_chat_message)

    # TUI 运行（阻塞）
    app.run()
    monitor.stop()
    server.stop()


def _start_game_tui(server, lobby, chat_manager, host_plays, app):
    """TUI 模式下的游戏启动逻辑（在后台线程中运行）"""
    broadcaster = None
    try:
        game_state = lobby.start_game()

        # 安装 Display 桥接（TUI 模式）
        broadcaster = DisplayBroadcaster(server, lobby)
        broadcaster.set_tui_callback(app.push_game_event, app=app)
        broadcaster.install()

        # 设置 AI 聊天
        _setup_ai_chat(lobby, chat_manager, game_state)

        # 通知所有客户端游戏开始
        server.broadcast_sync({
            "type": MessageType.LOBBY_UPDATE,
            "room_state": "in_game",
            "slots": [s.to_dict() for s in lobby.slots],
        })

        # 天赋选择（TUI 版本）
        ai_players_info = []
        for slot in lobby.slots:
            if slot.slot_type in (SlotType.BASIC_AI, SlotType.RL_AI):
                pid = f"p{slot.slot_id}"
                personality = slot.personality or "balanced"
                ai_players_info.append((pid, slot.player_name, personality))

        # 通过 TUI 询问是否启用天赋
        app.push_game_event({"event": "show_info", "args": ["是否启用天赋系统？在输入框输入 y 或 n"]})
        cmd_input = app.query_one("#cmd-input")
        raw = cmd_input.wait_for_input(timeout=120)
        if raw.lower() in ("y", "yes", "是"):
            _network_talent_selection_tui(game_state, ai_players_info, lobby, app)

        # 游戏循环
        round_mgr = RoundManager(game_state)
        lobby.round_manager = round_mgr

        _patch_engine_context(game_state, lobby)
        round_mgr.run_game_loop()
    except Exception as e:
        app.push_game_event({"event": "show_error", "args": [f"游戏异常: {e}"]})
    finally:
        if broadcaster:
            broadcaster.uninstall()
        lobby.state = lobby.state.__class__("finished")
        app._game_starting = False
        server.broadcast_sync({
            "type": MessageType.GAME_EVENT,
            "event": "game_finished",
            "args": [],
            "kwargs": {},
        })


def _network_talent_selection_tui(game_state, ai_players_info, lobby, app):
    """TUI 版天赋选择：远程玩家通过 NetworkController.choose()，本地房主用 TUI 输入"""
    from controllers.network_controller import NetworkController
    from controllers.human import HumanController
    from engine.game_setup import (
        _ai_pick_talent, AI_DISABLED_TALENTS,
    )

    ai_pids = {info[0] for info in ai_players_info}
    ai_personality_map = {info[0]: info[2] for info in ai_players_info}
    taken = set()

    lines = ["\n  可选天赋（每个天赋仅能被1人选取）："]
    for num, name, cls, desc in TALENT_TABLE:
        lines.append(f"    {num}. 【{name}】{desc}")
    lines.append("    0. 不选天赋")
    app.push_game_event({"event": "show_info", "args": ["\n".join(lines)]})

    for pid in game_state.player_order:
        player = game_state.get_player(pid)
        available = [(n, name, cls, desc) for n, name, cls, desc in TALENT_TABLE
                     if n not in taken]
        if not available:
            app.push_game_event({"event": "show_info",
                                 "args": [f"  所有天赋已被选完，{player.name} 无天赋。"]})
            continue

        # AI 自动选择
        if pid in ai_pids:
            personality = ai_personality_map.get(pid, "balanced")
            ai_available = [(n, name, cls, desc) for n, name, cls, desc in available
                           if n not in AI_DISABLED_TALENTS]
            if not ai_available:
                app.push_game_event({"event": "show_info",
                                     "args": [f"  {player.name}（AI）无可用天赋。"]})
                continue
            chosen = _ai_pick_talent(personality, ai_available, taken)
            if chosen is None:
                app.push_game_event({"event": "show_info",
                                     "args": [f"  {player.name}（AI）无可用天赋。"]})
                continue
            n, name, cls = chosen
            talent_inst = cls(pid, game_state)
            player.talent = talent_inst
            player.talent_name = name
            talent_inst.on_register()
            talent_inst.show_activation(player_name=player.name, show_lore=True)
            taken.add(n)
            app.push_game_event({"event": "show_info",
                                 "args": [f"  \U0001f916 {player.name}（AI·{personality}）自动选择天赋【{name}】"]})
            continue

        # 远程玩家：通过 controller.choose()
        if isinstance(player.controller, NetworkController):
            options = [f"{n}. 【{name}】{desc}" for n, name, cls, desc in available]
            options.append("0. 不选天赋")
            choice = player.controller.choose(
                f"{player.name} 选择天赋",
                options,
            )
            choice_num = 0
            try:
                choice_num = int(choice.split(".")[0])
            except (ValueError, IndexError):
                pass

            if choice_num == 0:
                app.push_game_event({"event": "show_info",
                                     "args": [f"  {player.name} 选择不使用天赋。"]})
                continue

            matched = None
            for n, name, cls, desc in TALENT_TABLE:
                if n == choice_num and n not in taken:
                    matched = (n, name, cls)
                    break

            if matched:
                n, name, cls = matched
                talent_inst = cls(pid, game_state)
                player.talent = talent_inst
                player.talent_name = name
                talent_inst.on_register()
                talent_inst.show_activation(player_name=player.name, show_lore=True)
                taken.add(n)
                app.push_game_event({"event": "show_info",
                                     "args": [f"  \u2713 {player.name} 获得天赋【{name}】！"]})
            else:
                app.push_game_event({"event": "show_info",
                                     "args": [f"  {player.name} 选择无效，跳过天赋。"]})
            continue

        # 本地房主：用 TUI 输入
        if isinstance(player.controller, HumanController):
            sel_lines = [f"\n  ── {player.name} 选择天赋 ──"]
            for n, name, cls, desc in available:
                sel_lines.append(f"    {n}. 【{name}】{desc}")
            sel_lines.append("    0. 不选")
            app.push_game_event({"event": "show_info", "args": ["\n".join(sel_lines)]})

            cmd_input = app.query_one("#cmd-input")
            while True:
                raw = cmd_input.wait_for_input(timeout=300)
                if raw == "0":
                    app.push_game_event({"event": "show_info",
                                         "args": [f"  {player.name} 选择不使用天赋。"]})
                    break
                try:
                    choice_num = int(raw)
                except ValueError:
                    app.push_game_event({"event": "show_error", "args": ["请输入有效编号。"]})
                    continue

                if choice_num in taken:
                    app.push_game_event({"event": "show_error", "args": ["该天赋已被其他玩家选走！"]})
                    continue

                matched = None
                for n, name, cls, desc in TALENT_TABLE:
                    if n == choice_num:
                        matched = (n, name, cls)
                        break

                if not matched:
                    app.push_game_event({"event": "show_error", "args": ["无效编号。"]})
                    continue

                n, name, cls = matched
                talent_inst = cls(pid, game_state)
                player.talent = talent_inst
                player.talent_name = name
                talent_inst.on_register()
                talent_inst.show_activation(player_name=player.name, show_lore=True)
                taken.add(n)
                app.push_game_event({"event": "show_info",
                                     "args": [f"  \u2713 {player.name} 获得天赋【{name}】！"]})
                break

    # 汇总
    summary_lines = ["\n  ─── 天赋分配结果 ───"]
    for pid in game_state.player_order:
        p = game_state.get_player(pid)
        t = p.talent_name if p.talent_name else "无"
        is_ai = "\U0001f916" if pid in ai_pids else "\U0001f464"
        summary_lines.append(f"    {is_ai} {p.name}: {t}")
    app.push_game_event({"event": "show_info", "args": ["\n".join(summary_lines)]})


def _start_game(server, lobby, chat_manager, host_plays):
    """启动游戏。"""
    print("\n  ─── 游戏启动 ───")

    game_state = lobby.start_game()

    # 安装 Display 桥接
    broadcaster = DisplayBroadcaster(server, lobby)
    broadcaster.install()

    # 设置 AI 聊天
    _setup_ai_chat(lobby, chat_manager, game_state)

    # 通知所有客户端游戏开始
    server.broadcast_sync({
        "type": MessageType.LOBBY_UPDATE,
        "room_state": "in_game",
        "slots": [s.to_dict() for s in lobby.slots],
    })

    # 天赋选择
    ai_players_info = []
    for slot in lobby.slots:
        if slot.slot_type in (SlotType.BASIC_AI, SlotType.RL_AI):
            pid = f"p{slot.slot_id}"
            personality = slot.personality or "balanced"
            ai_players_info.append((pid, slot.player_name, personality))

    print("  是否启用天赋系统？")
    while True:
        raw = input("  (y/n，默认n)：").strip().lower()
        if raw in ("y", "yes", "是"):
            _network_talent_selection(game_state, ai_players_info, lobby)
            break
        elif raw in ("n", "no", "否", ""):
            print("  天赋系统未启用。")
            break

    # 在独立线程中运行游戏循环
    round_mgr = RoundManager(game_state)
    lobby.round_manager = round_mgr

    def game_thread():
        try:
            # 设置 NetworkController 的上下文
            _patch_engine_context(game_state, lobby)
            round_mgr.run_game_loop()
        except Exception as e:
            print(f"  [Game] 游戏异常: {e}")
        finally:
            broadcaster.uninstall()
            lobby.state = lobby.state.__class__("finished")
            server.broadcast_sync({
                "type": MessageType.GAME_EVENT,
                "event": "game_finished",
                "args": [],
                "kwargs": {},
            })

    t = threading.Thread(target=game_thread, daemon=True)
    t.start()
    print("  [Game] 游戏循环已启动")


def _network_talent_selection(game_state, ai_players_info, lobby):
    """联机版天赋选择：远程玩家通过 NetworkController.choose()，本地房主用 input()"""
    from controllers.network_controller import NetworkController
    from controllers.human import HumanController
    from engine.game_setup import (
        _ai_pick_talent, AI_DISABLED_TALENTS,
    )

    ai_pids = {info[0] for info in ai_players_info}
    ai_personality_map = {info[0]: info[2] for info in ai_players_info}
    taken = set()

    print(f"\n  可选天赋（每个天赋仅能被1人选取）：")
    for num, name, cls, desc in TALENT_TABLE:
        print(f"    {num}. 【{name}】{desc}")
    print(f"    0. 不选天赋")

    for pid in game_state.player_order:
        player = game_state.get_player(pid)
        available = [(n, name, cls, desc) for n, name, cls, desc in TALENT_TABLE
                     if n not in taken]
        if not available:
            print(f"  所有天赋已被选完，{player.name} 无天赋。")
            continue

        # AI 自动选择
        if pid in ai_pids:
            personality = ai_personality_map.get(pid, "balanced")
            ai_available = [(n, name, cls, desc) for n, name, cls, desc in available
                           if n not in AI_DISABLED_TALENTS]
            if not ai_available:
                print(f"  {player.name}（AI）无可用天赋（全部被禁用或已选走）。")
                continue
            chosen = _ai_pick_talent(personality, ai_available, taken)
            if chosen is None:
                print(f"  {player.name}（AI）无可用天赋。")
                continue
            n, name, cls = chosen
            talent_inst = cls(pid, game_state)
            player.talent = talent_inst
            player.talent_name = name
            talent_inst.on_register()
            talent_inst.show_activation(player_name=player.name, show_lore=True)
            taken.add(n)
            print(f"  \U0001f916 {player.name}（AI·{personality}）自动选择天赋【{name}】")
            continue

        # 远程玩家：通过 controller.choose()
        if isinstance(player.controller, NetworkController):
            options = [f"{n}. 【{name}】{desc}" for n, name, cls, desc in available]
            options.append("0. 不选天赋")
            choice = player.controller.choose(
                f"{player.name} 选择天赋",
                options,
            )
            # 解析选择结果
            choice_num = 0
            try:
                choice_num = int(choice.split(".")[0])
            except (ValueError, IndexError):
                pass

            if choice_num == 0:
                print(f"  {player.name} 选择不使用天赋。")
                continue

            matched = None
            for n, name, cls, desc in TALENT_TABLE:
                if n == choice_num and n not in taken:
                    matched = (n, name, cls)
                    break

            if matched:
                n, name, cls = matched
                talent_inst = cls(pid, game_state)
                player.talent = talent_inst
                player.talent_name = name
                talent_inst.on_register()
                talent_inst.show_activation(player_name=player.name, show_lore=True)
                taken.add(n)
                print(f"  \u2713 {player.name} 获得天赋【{name}】！")
            else:
                print(f"  {player.name} 选择无效，跳过天赋。")
            continue

        # 本地房主：用 input()
        if isinstance(player.controller, HumanController):
            print(f"\n  ── {player.name} 选择天赋 ──")
            for n, name, cls, desc in available:
                print(f"    {n}. 【{name}】{desc}")
            print(f"    0. 不选")

            while True:
                raw = input(f"  [{player.name}] 请输入天赋编号：").strip()
                if raw == "0":
                    print(f"  {player.name} 选择不使用天赋。")
                    break
                try:
                    choice_num = int(raw)
                except ValueError:
                    print("  请输入有效编号。")
                    continue

                if choice_num in taken:
                    print("  该天赋已被其他玩家选走！")
                    continue

                matched = None
                for n, name, cls, desc in TALENT_TABLE:
                    if n == choice_num:
                        matched = (n, name, cls)
                        break

                if not matched:
                    print("  无效编号。")
                    continue

                n, name, cls = matched
                talent_inst = cls(pid, game_state)
                player.talent = talent_inst
                player.talent_name = name
                talent_inst.on_register()
                talent_inst.show_activation(player_name=player.name, show_lore=True)
                taken.add(n)
                print(f"  \u2713 {player.name} 获得天赋【{name}】！")
                break

    # 汇总
    print(f"\n  ─── 天赋分配结果 ───")
    for pid in game_state.player_order:
        p = game_state.get_player(pid)
        t = p.talent_name if p.talent_name else "无"
        is_ai = "\U0001f916" if pid in ai_pids else "\U0001f464"
        print(f"    {is_ai} {p.name}: {t}")


def _patch_engine_context(game_state, lobby):
    """
    在引擎执行整个玩家回合期间设置当前上下文。
    通过 monkey-patch ActionTurnManager.execute_action_turn 实现，
    确保 show_available_actions / show_player_status 等定向函数
    在整个回合期间都能正确路由到远程客户端。

    上下文语义（见 network/display_bridge.py:_make_directed）：
    - NetworkController → client_id = 远程客户端 id，定向发送到该客户端
    - HumanController   → client_id = None，在本地房主控制台显示
    - 其他（AI）        → client_id = AI_CONTEXT_SENTINEL，既不本地显示也不发送，
                          避免房主看到 AI 玩家的私密信息（手牌、可选行动等）
    """
    from controllers.network_controller import NetworkController
    from controllers.human import HumanController
    from engine.action_turn import ActionTurnManager
    from network.display_bridge import AI_CONTEXT_SENTINEL

    def _context_for(player):
        if isinstance(player.controller, NetworkController):
            return (player.controller.client_id, player.name)
        if isinstance(player.controller, HumanController):
            return (None, player.name)
        return (AI_CONTEXT_SENTINEL, player.name)

    original_execute = ActionTurnManager.execute_action_turn

    def patched_execute(self_atm, player):
        cid, pname = _context_for(player)
        set_current_context(cid, pname)
        try:
            return original_execute(self_atm, player)
        finally:
            set_current_context(None, None)

    ActionTurnManager.execute_action_turn = patched_execute

    # 同样 patch execute_single_action（结界内的简化行动回合）
    original_single = ActionTurnManager.execute_single_action

    def patched_single(self_atm, player):
        cid, pname = _context_for(player)
        set_current_context(cid, pname)
        try:
            return original_single(self_atm, player)
        finally:
            set_current_context(None, None)

    ActionTurnManager.execute_single_action = patched_single


def _setup_ai_chat(lobby, chat_manager, game_state):
    """为配置了 LLM 的 AI 玩家设置聊天模块。"""
    backend = create_backend()
    if backend is None:
        return

    from controllers.ai_basic import BasicAIController

    for slot in lobby.slots:
        if slot.slot_type == SlotType.BASIC_AI:
            pid = f"p{slot.slot_id}"
            player = game_state.get_player(pid)
            if player and isinstance(player.controller, BasicAIController):
                module = AIChatModule(
                    player_name=slot.player_name or f"AI-{slot.slot_id}",
                    personality=slot.personality or "balanced",
                    backend=backend,
                    controller=player.controller,
                )
                chat_manager.register_ai_chatter(
                    slot.player_name or f"AI-{slot.slot_id}", module,
                )


if __name__ == "__main__":
    main()
