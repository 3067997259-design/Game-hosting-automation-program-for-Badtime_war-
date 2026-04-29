"""
NetworkServer —— 基于 asyncio 的 TCP 房主服务器
══════════════════════════════════════════════════
管理客户端连接，提供同步/异步消息收发接口。
引擎在同步线程中调用 send_to_sync / wait_for_sync，
内部通过 asyncio.Event + threading.Event 桥接。
"""

import asyncio
import threading
import uuid
import time
from typing import Dict, Optional, Any, Callable, List, Set

from network.protocol import (
    MessageType, send_message, recv_message, HEADER_SIZE,
)


class NetworkServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 9527):
        self.host = host
        self.port = port

        # client_id → (reader, writer)
        self._clients: Dict[str, tuple] = {}
        # client_id → asyncio.Queue  (收到的消息)
        self._queues: Dict[str, asyncio.Queue] = {}
        # client_id → player_name（登录后绑定）
        self._client_names: Dict[str, str] = {}

        # 同步等待支持
        self._sync_results: Dict[str, Dict[str, Any]] = {}
        self._sync_events: Dict[str, threading.Event] = {}

        # 心跳追踪
        self._last_heartbeat: Dict[str, float] = {}

        # 回调
        self._on_client_connect: Optional[Callable] = None
        self._on_client_disconnect: Optional[Callable] = None
        self._on_message: Optional[Callable] = None

        # asyncio 事件循环（在独立线程中运行）
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._server: Optional[asyncio.AbstractServer] = None
        self._running = False

    # ──────────────────────────────────────────
    #  启动 / 停止
    # ──────────────────────────────────────────

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        # 等待事件循环就绪
        while self._loop is None or not self._loop.is_running():
            time.sleep(0.05)

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    async def _serve(self):
        self._server = await asyncio.start_server(
            self._handle_client, self.host, self.port,
        )
        async with self._server:
            await self._server.serve_forever()

    def stop(self):
        self._running = False
        if self._loop and self._server:
            self._loop.call_soon_threadsafe(self._server.close)

    # ──────────────────────────────────────────
    #  客户端连接处理
    # ──────────────────────────────────────────

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        client_id = str(uuid.uuid4())[:8]
        self._clients[client_id] = (reader, writer)
        self._queues[client_id] = asyncio.Queue()
        self._last_heartbeat[client_id] = time.time()

        addr = writer.get_extra_info("peername")
        print(f"  [Server] 客户端 {client_id} 已连接: {addr}")

        if self._on_client_connect:
            self._on_client_connect(client_id)

        try:
            while self._running:
                try:
                    msg = await asyncio.wait_for(recv_message(reader), timeout=30.0)
                except asyncio.TimeoutError:
                    # 检查心跳
                    if time.time() - self._last_heartbeat.get(client_id, 0) > 15:
                        print(f"  [Server] 客户端 {client_id} 心跳超时，断开连接")
                        break
                    continue
                except (asyncio.IncompleteReadError, ConnectionError):
                    break

                if msg is None:
                    break

                msg_type = msg.get("type", "")

                # 心跳
                if msg_type == MessageType.HEARTBEAT:
                    self._last_heartbeat[client_id] = time.time()
                    await send_message(writer, {"type": MessageType.HEARTBEAT_ACK})
                    continue

                # 同步等待的消息
                raw_type = msg_type.value if hasattr(msg_type, 'value') else msg_type
                wait_key = f"{client_id}:{raw_type}"
                if wait_key in self._sync_events:
                    self._sync_results[wait_key] = msg
                    self._sync_events[wait_key].set()
                    continue

                # 通用回调（在 executor 中执行，避免阻塞事件循环）
                if self._on_message:
                    await self._loop.run_in_executor(
                        None, self._on_message, client_id, msg,
                    )

                # 放入队列
                await self._queues[client_id].put(msg)

        except Exception as e:
            print(f"  [Server] 客户端 {client_id} 异常: {e}")
        finally:
            self._cleanup_client(client_id)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    def _cleanup_client(self, client_id: str):
        self._clients.pop(client_id, None)
        self._queues.pop(client_id, None)
        self._last_heartbeat.pop(client_id, None)
        # 唤醒所有挂起的 wait_for_sync 调用，避免游戏线程阻塞 5 分钟
        for wait_key, evt in list(self._sync_events.items()):
            if wait_key.startswith(f"{client_id}:"):
                self._sync_results[wait_key] = {"error": "disconnected"}
                evt.set()
        print(f"  [Server] 客户端 {client_id} 已断开")
        if self._on_client_disconnect:
            # 在 executor 中执行，避免从 async 上下文调用 broadcast_sync 时死锁
            if self._loop and self._loop.is_running():
                self._loop.run_in_executor(
                    None, self._on_client_disconnect, client_id,
                )
            else:
                self._on_client_disconnect(client_id)

    # ──────────────────────────────────────────
    #  异步发送
    # ──────────────────────────────────────────

    async def _async_send(self, client_id: str, msg_dict: Dict[str, Any]):
        if client_id not in self._clients:
            return
        _, writer = self._clients[client_id]
        try:
            await send_message(writer, msg_dict)
        except (ConnectionError, OSError):
            pass  # 由 _handle_client 的 finally 块统一清理

    async def _async_broadcast(
        self, msg_dict: Dict[str, Any], exclude: Optional[Set[str]] = None
    ):
        exclude = exclude or set()
        tasks = []
        for cid in list(self._clients.keys()):
            if cid not in exclude:
                tasks.append(self._async_send(cid, msg_dict))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ──────────────────────────────────────────
    #  同步接口（供引擎线程调用）
    # ──────────────────────────────────────────

    def send_to_sync(self, client_id: str, msg_dict: Dict[str, Any]):
        if self._loop is None:
            return
        fut = asyncio.run_coroutine_threadsafe(
            self._async_send(client_id, msg_dict), self._loop
        )
        fut.result(timeout=10)

    def broadcast_sync(
        self, msg_dict: Dict[str, Any], exclude: Optional[Set[str]] = None
    ):
        if self._loop is None:
            return
        fut = asyncio.run_coroutine_threadsafe(
            self._async_broadcast(msg_dict, exclude), self._loop
        )
        fut.result(timeout=10)

    def wait_for_sync(
        self,
        client_id: str,
        msg_type: str,
        timeout: float = 300.0,
    ) -> Dict[str, Any]:
        raw_type = msg_type.value if hasattr(msg_type, 'value') else str(msg_type)
        wait_key = f"{client_id}:{raw_type}"
        evt = threading.Event()
        self._sync_events[wait_key] = evt
        self._sync_results.pop(wait_key, None)

        try:
            if not evt.wait(timeout=timeout):
                return {"error": "timeout"}
            return self._sync_results.pop(wait_key, {"error": "no_result"})
        finally:
            self._sync_events.pop(wait_key, None)

    # ──────────────────────────────────────────
    #  便捷方法
    # ──────────────────────────────────────────

    @property
    def connected_clients(self) -> List[str]:
        return list(self._clients.keys())

    def get_client_name(self, client_id: str) -> Optional[str]:
        return self._client_names.get(client_id)

    def set_client_name(self, client_id: str, name: str):
        self._client_names[client_id] = name

    def is_connected(self, client_id: str) -> bool:
        return client_id in self._clients
