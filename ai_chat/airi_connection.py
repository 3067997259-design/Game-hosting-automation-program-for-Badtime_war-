"""
AIRI WebSocket 连接管理
═══════════════════════
共享模块：被 bot_bridge.py（独立玩家模式）和
ai_chat/airi_backend.py（BasicAI 聊天皮肤模式）共同引用。
"""

import asyncio
import json
import logging
import threading
import time
import uuid
from typing import Dict, Any, Optional, List, Callable


log = logging.getLogger("airi_connection")


class AiriConnection:
    """管理与 AIRI WebSocket 服务器的连接。"""

    def __init__(self, ws_url: str, module_id: str, auth_token: str = ""):
        self.ws_url = ws_url
        self.module_id = module_id  # 作为 plugin.id（稳定标识）
        self.instance_id = f"{module_id}-{uuid.uuid4().hex[:8]}"  # 实例 ID（每次运行唯一）
        self.auth_token = auth_token
        self._ws = None  # websockets 连接对象
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._connected = False
        self._response_queue: Optional[asyncio.Queue] = None
        self._message_handlers: Dict[str, Callable] = {}

    def connect(self):
        """在后台线程启动 asyncio 事件循环，连接 AIRI。"""
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        deadline = time.time() + 15
        while not self._connected and time.time() < deadline:
            time.sleep(0.1)
        if not self._connected:
            raise ConnectionError(f"无法连接到 AIRI: {self.ws_url}")

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        finally:
            self._loop.close()

    async def _main(self):
        try:
            import websockets
        except ImportError:
            log.error("缺少 websockets 库，请运行: pip install websockets")
            return

        # 在事件循环中创建 Queue，确保绑定到正确的循环
        self._response_queue = asyncio.Queue()

        connect_kwargs = {}
        if self.ws_url.startswith("wss://"):
            import ssl as _ssl
            ssl_ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = _ssl.CERT_NONE
            connect_kwargs["ssl"] = ssl_ctx

        try:
            async with websockets.connect(self.ws_url, **connect_kwargs) as ws:
                self._ws = ws
                self._connected = True
                log.info(f"已连接到 AIRI: {self.ws_url}")

                # 认证（如果配置了 token）
                if self.auth_token:
                    await self._send_event("module:authenticate", {
                        "token": self.auth_token,
                    })

                # 注册模块
                await self._send_event("module:announce", {
                    "name": "Badtime War Bridge",
                    "identity": {
                        "id": self.instance_id,
                        "kind": "plugin",
                        "plugin": {
                            "id": self.module_id,
                        },
                    },
                })

                # 接收循环
                async for raw_msg in ws:
                    try:
                        msg = json.loads(raw_msg)
                    except json.JSONDecodeError:
                        log.warning(f"收到非 JSON 消息: {str(raw_msg)[:100]}")
                        continue

                    msg_type = msg.get("type", "")

                    # 处理 AI 回复
                    if msg_type in (
                        "output:gen-ai:chat:message",
                        "output:gen-ai:chat:complete",
                    ):
                        text = msg.get("data", {}).get("text", "")
                        if text:
                            await self._response_queue.put(text)

                    # 处理 AIRI 主动发起的命令（类似 Minecraft 的 spark:command）
                    if msg_type == "spark:command":
                        command = msg.get("data", {}).get("command", "")
                        if command:
                            await self._response_queue.put(f"COMMAND:{command}")

                    # 自定义处理器
                    handler = self._message_handlers.get(msg_type)
                    if handler:
                        try:
                            handler(msg)
                        except Exception as e:
                            log.warning(f"自定义处理器异常 ({msg_type}): {e}")

        except Exception as e:
            log.error(f"AIRI 连接异常: {e}")
        finally:
            self._connected = False
            self._ws = None

    async def _send_event(self, event_type: str, data: dict):
        """发送 AIRI 格式的事件消息。"""
        msg = {
            "type": event_type,
            "data": data,
            "metadata": {
                "source": {
                    "id": self.instance_id,
                    "kind": "plugin",
                    "plugin": {
                        "id": self.module_id,
                    },
                },
                "event": {
                    "id": str(uuid.uuid4()),
                },
            },
        }
        if self._ws:
            await self._ws.send(json.dumps(msg, ensure_ascii=False))

    def send_text(self, text: str):
        """从外部线程向 AIRI 发送文本消息（input:text）。"""
        if self._loop and self._connected:
            asyncio.run_coroutine_threadsafe(
                self._send_event("input:text", {"text": text}),
                self._loop,
            )

    def send_context(self, context: dict):
        """向 AIRI 推送游戏上下文（context:update）。"""
        if self._loop and self._connected:
            asyncio.run_coroutine_threadsafe(
                self._send_event("context:update", context),
                self._loop,
            )

    def send_notify(self, message: str):
        """向 AIRI 发送通知（spark:notify）。"""
        if self._loop and self._connected:
            asyncio.run_coroutine_threadsafe(
                self._send_event("spark:notify", {"message": message}),
                self._loop,
            )

    def wait_for_response(self, timeout: float = 60.0) -> Optional[str]:
        """同步等待 AIRI 的下一条回复。"""
        if not self._loop or self._response_queue is None:
            return None
        future = asyncio.run_coroutine_threadsafe(
            asyncio.wait_for(self._response_queue.get(), timeout=timeout),
            self._loop,
        )
        try:
            return future.result(timeout=timeout + 5)
        except Exception:
            return None

    def drain_responses(self) -> List[str]:
        """非阻塞地取出队列中所有待处理的回复。"""
        results: List[str] = []
        if not self._loop or self._response_queue is None:
            return results
        while True:
            try:
                future = asyncio.run_coroutine_threadsafe(
                    asyncio.wait_for(self._response_queue.get(), timeout=0.01),
                    self._loop,
                )
                text = future.result(timeout=0.1)
                if text:
                    results.append(text)
            except Exception:
                break
        return results

    @property
    def is_connected(self) -> bool:
        return self._connected
