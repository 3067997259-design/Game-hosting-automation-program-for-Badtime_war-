"""
NetworkClient —— 客户端网络连接
═════════════════════════════════
连接到房主服务器，接收消息并分发，发送用户输入。
"""

import asyncio
import threading
import time
from typing import Dict, Any, Optional, Callable

from network.protocol import (
    MessageType, send_message, recv_message,
)


class NetworkClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 9527):
        self.host = host
        self.port = port

        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None

        # 消息处理器: msg_type → callback(msg_dict)
        self._handlers: Dict[str, Callable] = {}

        # 同步等待支持（客户端 TUI 需要等待服务器的请求）
        self._sync_results: Dict[str, Dict[str, Any]] = {}
        self._sync_events: Dict[str, threading.Event] = {}

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._connected = False

        self.player_name: Optional[str] = None

    # ──────────────────────────────────────────
    #  连接 / 断开
    # ──────────────────────────────────────────

    def connect(self, player_name: str):
        self.player_name = player_name
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        # 等待连接就绪
        deadline = time.time() + 10
        while not self._connected and time.time() < deadline:
            time.sleep(0.05)
        if not self._connected:
            raise ConnectionError(f"无法连接到 {self.host}:{self.port}")

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main())

    async def _main(self):
        try:
            self._reader, self._writer = await asyncio.open_connection(
                self.host, self.port,
            )
            self._connected = True

            # 发送加入消息
            await send_message(self._writer, {
                "type": MessageType.LOBBY_JOIN,
                "player_name": self.player_name,
            })

            # 启动心跳和接收
            await asyncio.gather(
                self._recv_loop(),
                self._heartbeat_loop(),
            )
        except (ConnectionError, OSError) as e:
            print(f"  [Client] 连接失败: {e}")
        finally:
            self._connected = False
            if self._writer:
                self._writer.close()
                try:
                    await self._writer.wait_closed()
                except Exception:
                    pass

    async def _recv_loop(self):
        while self._running and self._reader:
            try:
                msg = await asyncio.wait_for(recv_message(self._reader), timeout=30.0)
            except asyncio.TimeoutError:
                continue
            except (asyncio.IncompleteReadError, ConnectionError):
                print("  [Client] 连接断开")
                break

            if msg is None:
                break

            msg_type = msg.get("type", "")

            # 同步等待
            if msg_type in self._sync_events:
                self._sync_results[msg_type] = msg
                self._sync_events[msg_type].set()
                continue

            # 注册的处理器
            handler = self._handlers.get(msg_type)
            if handler:
                try:
                    handler(msg)
                except Exception as e:
                    print(f"  [Client] 处理器异常 ({msg_type}): {e}")

    async def _heartbeat_loop(self):
        while self._running and self._writer:
            try:
                await send_message(self._writer, {"type": MessageType.HEARTBEAT})
                await asyncio.sleep(5)
            except (ConnectionError, OSError):
                break

    # ──────────────────────────────────────────
    #  消息发送
    # ──────────────────────────────────────────

    def send_sync(self, msg_dict: Dict[str, Any]):
        if self._loop is None or self._writer is None:
            return
        fut = asyncio.run_coroutine_threadsafe(
            send_message(self._writer, msg_dict), self._loop,
        )
        try:
            fut.result(timeout=10)
        except Exception:
            pass

    def wait_for_message(self, msg_type: str, timeout: float = 300.0) -> Dict[str, Any]:
        evt = threading.Event()
        self._sync_events[msg_type] = evt
        self._sync_results.pop(msg_type, None)
        try:
            if not evt.wait(timeout=timeout):
                return {"error": "timeout"}
            return self._sync_results.pop(msg_type, {"error": "no_result"})
        finally:
            self._sync_events.pop(msg_type, None)

    # ──────────────────────────────────────────
    #  处理器注册
    # ──────────────────────────────────────────

    def on(self, msg_type: str, handler: Callable):
        self._handlers[msg_type] = handler

    # ──────────────────────────────────────────
    #  状态
    # ──────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._connected

    def reconnect(self, player_name: str):
        """断线重连"""
        self.player_name = player_name
        self._running = True
        self._thread = threading.Thread(target=self._run_loop_reconnect, daemon=True)
        self._thread.start()
        deadline = time.time() + 10
        while not self._connected and time.time() < deadline:
            time.sleep(0.05)
        if not self._connected:
            raise ConnectionError(f"重连失败: 无法连接到 {self.host}:{self.port}")

    def _run_loop_reconnect(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main_reconnect())

    async def _main_reconnect(self):
        """重连模式：发送 RECONNECT 而非 LOBBY_JOIN"""
        try:
            self._reader, self._writer = await asyncio.open_connection(
                self.host, self.port,
            )
            self._connected = True
            await send_message(self._writer, {
                "type": MessageType.RECONNECT,
                "player_name": self.player_name,
            })
            await asyncio.gather(
                self._recv_loop(),
                self._heartbeat_loop(),
            )
        except (ConnectionError, OSError) as e:
            print(f"  [Client] 重连失败: {e}")
        finally:
            self._connected = False
            if self._writer:
                self._writer.close()
                try:
                    await self._writer.wait_closed()
                except Exception:
                    pass

    def disconnect(self):
        self._running = False
        if self._loop and self._writer:
            async def _close():
                self._writer.close()
                try:
                    await self._writer.wait_closed()
                except Exception:
                    pass
            asyncio.run_coroutine_threadsafe(_close(), self._loop)
