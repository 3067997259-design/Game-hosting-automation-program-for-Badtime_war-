"""
集成测试 —— 网络模块
═══════════════════════
可在无网络连接、无 LLM、无 RL 模型的环境下运行。

覆盖：
1. 帧协议编解码（同步 + 异步）
2. ForfeitController 所有方法返回值
3. NetworkController 接口一致性（是否实现了 PlayerController 的所有抽象方法）
4. LLM 后端工厂函数
5. RL 检测函数
6. LobbyManager 单元测试
"""

import asyncio
import inspect
import json
import socket
import struct
import sys
import threading
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

# ──────────────────────────────────────────────
#  确保项目根目录在 sys.path
# ──────────────────────────────────────────────
from pathlib import Path

_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from network.protocol import (
    HEADER_FMT,
    HEADER_SIZE,
    MAX_MSG_SIZE,
    MessageType,
    _encode,
    recv_message,
    recv_message_sync,
    send_message,
    send_message_sync,
)
from controllers.base import PlayerController
from controllers.forfeit_controller import ForfeitController
from controllers.network_controller import NetworkController


# ═══════════════════════════════════════════════
#  1. 帧协议编解码
# ═══════════════════════════════════════════════

class TestFrameProtocol(unittest.TestCase):
    """帧协议：4 字节大端长度前缀 + UTF-8 JSON payload"""

    # ── 同步 ──

    def test_encode_decode_sync_roundtrip(self):
        """编码后通过 socket-pair 同步解码，数据一致。"""
        msg = {"type": "test", "value": 42, "中文": "你好"}
        s_send, s_recv = socket.socketpair()
        try:
            send_message_sync(s_send, msg)
            result = recv_message_sync(s_recv)
            self.assertEqual(result, msg)
        finally:
            s_send.close()
            s_recv.close()

    def test_encode_format(self):
        """_encode 生成 4 字节大端长度 + UTF-8 JSON。"""
        msg = {"a": 1}
        data = _encode(msg)
        payload_bytes = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        expected_header = struct.pack(HEADER_FMT, len(payload_bytes))
        self.assertEqual(data[:HEADER_SIZE], expected_header)
        self.assertEqual(data[HEADER_SIZE:], payload_bytes)

    def test_encode_empty_dict(self):
        """空字典也能正确编解码。"""
        msg = {}
        s_send, s_recv = socket.socketpair()
        try:
            send_message_sync(s_send, msg)
            result = recv_message_sync(s_recv)
            self.assertEqual(result, msg)
        finally:
            s_send.close()
            s_recv.close()

    def test_recv_sync_returns_none_on_closed(self):
        """对端关闭时 recv_message_sync 返回 None。"""
        s_send, s_recv = socket.socketpair()
        s_send.close()
        result = recv_message_sync(s_recv)
        self.assertIsNone(result)
        s_recv.close()

    def test_large_message_sync(self):
        """较大消息也能正确传输。"""
        msg = {"data": "x" * 100_000}
        s_send, s_recv = socket.socketpair()
        try:
            send_message_sync(s_send, msg)
            result = recv_message_sync(s_recv)
            self.assertEqual(result, msg)
        finally:
            s_send.close()
            s_recv.close()

    def test_multiple_messages_sync(self):
        """连续发送多条消息后可按序接收。"""
        messages = [
            {"type": "a", "i": 0},
            {"type": "b", "i": 1},
            {"type": "c", "i": 2},
        ]
        s_send, s_recv = socket.socketpair()
        try:
            for m in messages:
                send_message_sync(s_send, m)
            for m in messages:
                result = recv_message_sync(s_recv)
                self.assertEqual(result, m)
        finally:
            s_send.close()
            s_recv.close()

    # ── 异步 ──

    def test_encode_decode_async_roundtrip(self):
        """异步编解码 roundtrip。"""

        async def _run():
            msg = {"type": "async_test", "val": [1, 2, 3]}
            reader, writer = await asyncio.open_connection(
                *await _create_echo_server(msg)
            )
            result = await recv_message(reader)
            writer.close()
            await writer.wait_closed()
            return result

        async def _create_echo_server(msg):
            """创建一个发送单条消息后关闭的临时服务器。"""
            ready = asyncio.Event()
            addr_holder: list = []

            async def _handle(reader, writer):
                await send_message(writer, msg)
                writer.close()
                await writer.wait_closed()

            server = await asyncio.start_server(_handle, "127.0.0.1", 0)
            sockets = server.sockets
            host, port = sockets[0].getsockname()
            addr_holder.extend([host, port])

            async def _serve():
                async with server:
                    ready.set()
                    await server.serve_forever()

            task = asyncio.create_task(_serve())
            await ready.wait()

            async def _connect_and_close():
                r, w = await asyncio.open_connection(host, port)
                result = await recv_message(r)
                w.close()
                await w.wait_closed()
                server.close()
                await server.wait_closed()
                task.cancel()
                return result

            return host, port

        msg = {"type": "async_test", "val": [1, 2, 3]}

        async def _full_test():
            ready = asyncio.Event()
            result_holder: list = []

            async def _handle(reader, writer):
                await send_message(writer, msg)
                writer.close()
                await writer.wait_closed()

            server = await asyncio.start_server(_handle, "127.0.0.1", 0)
            host, port = server.sockets[0].getsockname()

            r, w = await asyncio.open_connection(host, port)
            result = await recv_message(r)
            w.close()
            await w.wait_closed()
            server.close()
            await server.wait_closed()
            return result

        result = asyncio.run(_full_test())
        self.assertEqual(result, msg)


# ═══════════════════════════════════════════════
#  2. ForfeitController
# ═══════════════════════════════════════════════

class TestForfeitController(unittest.TestCase):
    """ForfeitController：所有操作返回安全默认值。"""

    def setUp(self):
        self.ctrl = ForfeitController()

    def test_get_command_returns_forfeit(self):
        result = self.ctrl.get_command(
            player=MagicMock(),
            game_state=MagicMock(),
            available_actions=["move", "attack", "forfeit"],
        )
        self.assertEqual(result, "forfeit")

    def test_get_command_ignores_available_actions(self):
        result = self.ctrl.get_command(
            player=MagicMock(),
            game_state=MagicMock(),
            available_actions=[],
        )
        self.assertEqual(result, "forfeit")

    def test_choose_returns_first_option(self):
        result = self.ctrl.choose("选择", ["A", "B", "C"])
        self.assertEqual(result, "A")

    def test_choose_empty_options_returns_empty(self):
        result = self.ctrl.choose("选择", [])
        self.assertEqual(result, "")

    def test_choose_multi_with_min_count(self):
        result = self.ctrl.choose_multi("选择", ["A", "B", "C"], max_count=3, min_count=2)
        self.assertEqual(result, ["A", "B"])

    def test_choose_multi_zero_min_returns_empty(self):
        result = self.ctrl.choose_multi("选择", ["A", "B"], max_count=2, min_count=0)
        self.assertEqual(result, [])

    def test_confirm_returns_false(self):
        result = self.ctrl.confirm("确认？")
        self.assertFalse(result)

    def test_is_player_controller(self):
        self.assertIsInstance(self.ctrl, PlayerController)

    def test_on_event_does_not_raise(self):
        self.ctrl.on_event({"type": "attack", "attacker": "A"})


# ═══════════════════════════════════════════════
#  3. NetworkController 接口一致性
# ═══════════════════════════════════════════════

class TestNetworkControllerInterface(unittest.TestCase):
    """NetworkController 必须实现 PlayerController 的所有抽象方法。"""

    def test_inherits_player_controller(self):
        self.assertTrue(issubclass(NetworkController, PlayerController))

    def test_all_abstract_methods_implemented(self):
        abstract_methods = set()
        for name, method in inspect.getmembers(PlayerController):
            if getattr(method, "__isabstractmethod__", False):
                abstract_methods.add(name)

        for name in abstract_methods:
            self.assertTrue(
                hasattr(NetworkController, name),
                f"NetworkController 缺少抽象方法: {name}",
            )
            impl = getattr(NetworkController, name)
            self.assertFalse(
                getattr(impl, "__isabstractmethod__", False),
                f"NetworkController 未实现抽象方法: {name}",
            )

    def test_instantiation_requires_client_id_and_server(self):
        mock_server = MagicMock()
        ctrl = NetworkController("test-client-id", mock_server)
        self.assertEqual(ctrl.client_id, "test-client-id")
        self.assertIs(ctrl.server, mock_server)

    def test_method_signatures_match_base(self):
        """方法签名参数名与基类一致。"""
        for method_name in ("get_command", "choose", "choose_multi", "confirm"):
            base_sig = inspect.signature(getattr(PlayerController, method_name))
            impl_sig = inspect.signature(getattr(NetworkController, method_name))
            base_params = list(base_sig.parameters.keys())
            impl_params = list(impl_sig.parameters.keys())
            self.assertEqual(
                base_params, impl_params,
                f"{method_name} 签名不匹配: base={base_params}, impl={impl_params}",
            )


# ═══════════════════════════════════════════════
#  4. LLM 后端工厂函数
# ═══════════════════════════════════════════════

class TestLLMBackendFactory(unittest.TestCase):
    """create_backend 和 load_llm_config 在无配置时不崩溃。"""

    def test_create_backend_no_config_returns_none(self):
        from ai_chat.llm_backend import create_backend
        result = create_backend(config=None)
        self.assertIsNone(result)

    def test_create_backend_openai_config(self):
        from ai_chat.llm_backend import create_backend, OpenAIBackend
        config = {"backend": "openai", "api_key": "fake", "model": "test"}
        result = create_backend(config)
        if result is not None:
            self.assertIsInstance(result, OpenAIBackend)
            self.assertEqual(result.model, "test")

    def test_create_backend_ollama_config(self):
        from ai_chat.llm_backend import create_backend, OllamaBackend
        config = {"backend": "ollama", "host": "http://localhost:11434", "model": "llama3"}
        result = create_backend(config)
        self.assertIsInstance(result, OllamaBackend)
        self.assertEqual(result.model, "llama3")

    def test_create_backend_unknown_type_returns_none(self):
        from ai_chat.llm_backend import create_backend
        result = create_backend({"backend": "unknown"})
        self.assertIsNone(result)

    def test_load_llm_config_no_file_returns_none(self):
        from ai_chat.llm_backend import load_llm_config
        with patch("os.path.exists", return_value=False):
            result = load_llm_config()
            self.assertIsNone(result)


# ═══════════════════════════════════════════════
#  5. RL 检测函数
# ═══════════════════════════════════════════════

class TestRLDetection(unittest.TestCase):
    """detect_rl_availability 不崩溃、返回正确结构。"""

    def test_returns_dict_with_expected_keys(self):
        from network.rl_detect import detect_rl_availability
        result = detect_rl_availability()
        self.assertIsInstance(result, dict)
        self.assertIn("available", result)
        self.assertIn("models", result)
        self.assertIn("has_opponent_controller", result)
        self.assertIn("has_torchscript_controller", result)

    def test_available_is_bool(self):
        from network.rl_detect import detect_rl_availability
        result = detect_rl_availability()
        self.assertIsInstance(result["available"], bool)

    def test_models_is_list(self):
        from network.rl_detect import detect_rl_availability
        result = detect_rl_availability()
        self.assertIsInstance(result["models"], list)

    def test_no_models_means_not_available(self):
        """无模型文件且无控制器时 available 应为 False。"""
        from network.rl_detect import detect_rl_availability
        with patch("os.path.isdir", return_value=False):
            with patch("network.rl_detect.detect_rl_availability.__module__"):
                pass
        result = detect_rl_availability()
        if not result["models"] and not result["has_opponent_controller"] and not result["has_torchscript_controller"]:
            self.assertFalse(result["available"])


# ═══════════════════════════════════════════════
#  6. LobbyManager 单元测试
# ═══════════════════════════════════════════════

class TestLobbyManager(unittest.TestCase):
    """LobbyManager：创建房间、玩家加入、AI 设置、can_start。"""

    def _make_lobby(self, total: int = 2, host_plays: bool = True) -> "LobbyManager":
        from network.lobby import LobbyManager
        mock_server = MagicMock()
        return LobbyManager(total, host_plays, mock_server)

    # ── 创建房间 ──

    def test_create_room_host_plays(self):
        lobby = self._make_lobby(3, host_plays=True)
        self.assertEqual(lobby.total_players, 3)
        self.assertTrue(lobby.host_plays)
        self.assertEqual(len(lobby.slots), 3)
        from network.lobby import SlotType
        self.assertEqual(lobby.slots[0].slot_type, SlotType.HUMAN_LOCAL)
        self.assertEqual(lobby.slots[0].player_name, "房主")

    def test_create_room_no_host(self):
        lobby = self._make_lobby(2, host_plays=False)
        from network.lobby import SlotType
        for slot in lobby.slots:
            self.assertEqual(slot.slot_type, SlotType.EMPTY)

    def test_total_players_clamped(self):
        lobby_min = self._make_lobby(0, host_plays=False)
        self.assertEqual(lobby_min.total_players, 2)
        lobby_max = self._make_lobby(100, host_plays=False)
        self.assertEqual(lobby_max.total_players, 6)

    # ── 玩家加入 ──

    def test_player_join(self):
        lobby = self._make_lobby(2, host_plays=True)
        from network.lobby import SlotType
        slot = lobby.on_player_join("client-1", "Alice")
        self.assertIsNotNone(slot)
        self.assertEqual(slot.slot_type, SlotType.HUMAN_REMOTE)
        self.assertEqual(slot.player_name, "Alice")
        self.assertTrue(slot.is_connected)

    def test_player_join_full_room(self):
        lobby = self._make_lobby(2, host_plays=True)
        lobby.on_player_join("client-1", "Alice")
        slot = lobby.on_player_join("client-2", "Bob")
        self.assertIsNone(slot)

    def test_player_leave_waiting(self):
        lobby = self._make_lobby(3, host_plays=True)
        lobby.on_player_join("client-1", "Alice")
        result = lobby.on_player_leave("client-1")
        self.assertIsNotNone(result)
        from network.lobby import SlotType
        self.assertEqual(result.slot_type, SlotType.EMPTY)

    # ── AI 设置 ──

    def test_set_ai_basic(self):
        lobby = self._make_lobby(2, host_plays=False)
        ok = lobby.set_slot_ai(1, "basic", "aggressive")
        self.assertTrue(ok)
        from network.lobby import SlotType
        self.assertEqual(lobby.slots[0].slot_type, SlotType.BASIC_AI)
        self.assertEqual(lobby.slots[0].personality, "aggressive")

    def test_set_ai_rl(self):
        lobby = self._make_lobby(2, host_plays=False)
        ok = lobby.set_slot_ai(1, "rl", rl_model_path="/fake/model.zip")
        self.assertTrue(ok)
        from network.lobby import SlotType
        self.assertEqual(lobby.slots[0].slot_type, SlotType.RL_AI)

    def test_set_ai_on_occupied_human_slot_fails(self):
        lobby = self._make_lobby(2, host_plays=True)
        ok = lobby.set_slot_ai(1, "basic")
        self.assertFalse(ok)

    # ── can_start ──

    def test_can_start_all_filled(self):
        lobby = self._make_lobby(2, host_plays=True)
        lobby.set_slot_ai(2, "basic")
        self.assertTrue(lobby.can_start())

    def test_can_start_has_empty(self):
        lobby = self._make_lobby(3, host_plays=True)
        lobby.set_slot_ai(2, "basic")
        self.assertFalse(lobby.can_start())

    # ── get_lobby_info ──

    def test_get_lobby_info_structure(self):
        lobby = self._make_lobby(2, host_plays=True)
        info = lobby.get_lobby_info()
        self.assertIn("total_players", info)
        self.assertIn("room_state", info)
        self.assertIn("host_plays", info)
        self.assertIn("slots", info)
        self.assertEqual(info["total_players"], 2)
        self.assertTrue(info["host_plays"])
        self.assertEqual(info["room_state"], "waiting")

    # ── disconnect_policy ──

    def test_set_disconnect_policy(self):
        lobby = self._make_lobby(2, host_plays=True)
        lobby.on_player_join("client-1", "Alice")
        from network.lobby import DisconnectPolicy
        lobby.set_disconnect_policy(2, DisconnectPolicy.AI_TAKEOVER)
        self.assertEqual(lobby.slots[1].disconnect_policy, DisconnectPolicy.AI_TAKEOVER)


# ═══════════════════════════════════════════════
#  MessageType 枚举完整性
# ═══════════════════════════════════════════════

class TestMessageType(unittest.TestCase):
    """MessageType 枚举值可作为字符串使用。"""

    def test_all_members_are_strings(self):
        for member in MessageType:
            self.assertIsInstance(member.value, str)

    def test_request_command_value(self):
        self.assertEqual(MessageType.REQUEST_COMMAND, "request_command")

    def test_lobby_join_value(self):
        self.assertEqual(MessageType.LOBBY_JOIN, "lobby_join")


if __name__ == "__main__":
    unittest.main()
