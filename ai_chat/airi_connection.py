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

    def __init__(
        self,
        ws_url: str,
        module_id: str,
        auth_token: str = "",
        heartbeat_interval: int = 30,
        max_reconnect_attempts: int = 10,
    ):
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
        # 心跳与重连
        self._heartbeat_interval = max(1, int(heartbeat_interval))
        self._max_reconnect_attempts = max(0, int(max_reconnect_attempts))
        self._reconnect_delay = 5  # 重连退避（秒）
        self._should_reconnect = True

    def connect(self):
        """在后台线程启动 asyncio 事件循环，连接 AIRI。"""
        self._should_reconnect = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        deadline = time.time() + 15
        while not self._connected and time.time() < deadline:
            time.sleep(0.1)
        if not self._connected:
            # 首次连接失败：停止后台重连，避免事件循环常驻
            self._should_reconnect = False
            raise ConnectionError(f"无法连接到 AIRI: {self.ws_url}")

    def disconnect(self):
        """主动断开连接并停止重连。"""
        self._should_reconnect = False
        ws = self._ws
        loop = self._loop
        if ws is not None and loop is not None and loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(ws.close(), loop)
            except Exception:
                pass
        self._connected = False

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

        connect_kwargs = {}
        if self.ws_url.startswith("wss://"):
            import ssl as _ssl
            ssl_ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = _ssl.CERT_NONE
            connect_kwargs["ssl"] = ssl_ctx

        reconnect_attempts = 0

        while self._should_reconnect and reconnect_attempts <= self._max_reconnect_attempts:
            try:
                # 每次连接重建 Queue，避免历史回复污染新会话
                self._response_queue = asyncio.Queue()

                async with websockets.connect(self.ws_url, **connect_kwargs) as ws:
                    self._ws = ws
                    self._connected = True
                    if reconnect_attempts > 0:
                        log.info(f"AIRI 重连成功: {self.ws_url}")
                    else:
                        log.info(f"已连接到 AIRI: {self.ws_url}")
                    reconnect_attempts = 0  # 连接成功后清零

                    # 认证（如果配置了 token）
                    if self.auth_token:
                        await self._send_event("module:authenticate", {
                            "token": self.auth_token,
                        })

                    # 注册模块
                    identity = {
                        "id": f"{self.module_id}-instance",
                        "kind": "plugin",
                        "plugin": {"id": self.module_id},
                    }
                    await self._send_event("module:announce", {
                        "name": "Badtime War Bridge",
                        "identity": identity,
                    }, source_override=identity)

                    # 同时运行接收循环和心跳循环；任意一个先结束就取消另一个
                    recv_task = asyncio.ensure_future(self._recv_loop(ws))
                    hb_task = asyncio.ensure_future(self._heartbeat_loop())
                    try:
                        done, pending = await asyncio.wait(
                            [recv_task, hb_task],
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                    finally:
                        for task in (recv_task, hb_task):
                            if not task.done():
                                task.cancel()
                        # 等待取消完成，避免协程泄漏
                        for task in (recv_task, hb_task):
                            try:
                                await task
                            except (asyncio.CancelledError, Exception):
                                pass
                    # 把先结束那个任务的异常抛出，交给外层重连逻辑
                    for task in done:
                        exc = task.exception()
                        if exc is not None:
                            raise exc

            except Exception as e:
                log.error(f"AIRI 连接异常: {e}")
            finally:
                self._connected = False
                self._ws = None

            if not self._should_reconnect:
                break

            reconnect_attempts += 1
            if reconnect_attempts > self._max_reconnect_attempts:
                log.error(
                    f"AIRI 已达到最大重连次数 ({self._max_reconnect_attempts})，停止重连"
                )
                break

            log.info(
                f"{self._reconnect_delay} 秒后尝试重连 AIRI "
                f"({reconnect_attempts}/{self._max_reconnect_attempts})"
            )
            try:
                await asyncio.sleep(self._reconnect_delay)
            except asyncio.CancelledError:
                break

    async def _recv_loop(self, ws):
        """接收循环：消费 AIRI 推送的消息。"""
        async for raw_msg in ws:
            try:
                msg = json.loads(raw_msg)
            except json.JSONDecodeError:
                log.warning(f"收到非 JSON 消息: {str(raw_msg)[:100]}")
                continue

            # SuperJSON 解包
            if "json" in msg and isinstance(msg["json"], dict):
                msg = msg["json"]

            msg_type = msg.get("type", "")

            # 处理 AI 回复
            if msg_type in (
                "output:gen-ai:chat:message",
                "output:gen-ai:chat:complete",
            ):
                # 优先从 message.content 提取实际回复
                message_obj = msg.get("data", {}).get("message", {})
                text = message_obj.get("content", "") if isinstance(message_obj, dict) else ""
                if not text:
                    # 降级：尝试 data.text（兼容旧版本）
                    text = msg.get("data", {}).get("text", "")
                if text and self._response_queue is not None:
                    await self._response_queue.put(text)

            # 处理 AIRI 主动发起的命令（类似 Minecraft 的 spark:command）
            if msg_type == "spark:command":
                command = msg.get("data", {}).get("command", "")
                if command and self._response_queue is not None:
                    await self._response_queue.put(f"COMMAND:{command}")

            # 自定义处理器
            handler = self._message_handlers.get(msg_type)
            if handler:
                try:
                    handler(msg)
                except Exception as e:
                    log.warning(f"自定义处理器异常 ({msg_type}): {e}")

    async def _heartbeat_loop(self):
        """心跳循环：定期发送应用层 ping 保持连接活性。

        websockets 库自身有 WebSocket 协议级别的 ping/pong；这里发送的
        是 AIRI 应用层事件，用于让对端在收不到任何业务消息的空闲期
        仍然知道我们是活的（部分中间件会因长时间无任何数据而断开）。
        AIRI 若不识别该事件类型可安全忽略。
        """
        while True:
            try:
                await asyncio.sleep(self._heartbeat_interval)
                if self._ws is None:
                    break
                await self._send_event("ping", {})
                log.debug("[AIRI] 已发送心跳 ping")
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning(f"[AIRI] 心跳发送失败: {e}")
                break

    async def _send_event(self, event_type: str, data: dict, source_override: Optional[dict] = None):
        """发送 AIRI 格式的事件消息。"""
        source = source_override or {
            "id": f"{self.module_id}-instance",
            "kind": "plugin",
            "plugin": {"id": self.module_id},
        }
        msg = {
            "type": event_type,
            "data": data,
            "metadata": {
                "source": source,
                "event": {
                    "id": str(uuid.uuid4()),
                },
            },
        }
        if self._ws:
            await self._ws.send(json.dumps(msg, ensure_ascii=False))

    def send_text(self, text: str):
        """从外部线程向 AIRI 发送文本消息（input:text）。"""
        if not self._connected:
            preview = text[:50] + ("..." if len(text) > 50 else "")
            log.warning(f"[AIRI] 连接已断开，丢弃文本消息: {preview}")
            return
        if self._loop is None:
            log.warning("[AIRI] 事件循环未运行，无法发送文本消息")
            return
        asyncio.run_coroutine_threadsafe(
            self._send_event("input:text", {"text": text}),
            self._loop,
        )

    def send_context(self, context: dict):
        """向 AIRI 推送游戏上下文（context:update）。"""
        if not self._connected:
            log.warning("[AIRI] 连接已断开，丢弃 context:update")
            return
        if self._loop is None:
            log.warning("[AIRI] 事件循环未运行，无法推送上下文")
            return
        asyncio.run_coroutine_threadsafe(
            self._send_event("context:update", context),
            self._loop,
        )

    def send_notify(
        self,
        message: str,
        kind: str = "ping",
        urgency: str = "immediate",
        headline: Optional[str] = None,
        destinations: Optional[List[str]] = None,
    ):
        """向 AIRI 发送通知（spark:notify）。

        AIRI 协议要求 spark:notify payload 至少包含：
        - id / eventId: 本次事件的唯一 ID（重试时 eventId 可保持不变）
        - kind: "alarm"（紧急）/"ping"（普通通知）/"reminder"（提醒）
        - urgency: "immediate"/"soon"/"later"
        - headline: 简短标题（最多 100 字符）
        - note: 详细内容（可选）
        - destinations: 路由目标，例如 ["character"] 路由到角色模块

        Args:
            message: 通知详细内容，会作为 ``note`` 字段发送。
            kind: 事件类型，默认 ``ping``；非法值会回落到 ``ping``。
            urgency: 紧急程度，默认 ``immediate``；非法值回落到 ``immediate``。
            headline: 简短标题。若为 None 则取 ``message`` 第一行并截至 100 字符。
            destinations: 路由列表。默认 ``["character"]``。
        """
        if not self._connected:
            preview = message[:50] + ("..." if len(message) > 50 else "")
            log.warning(f"[AIRI] 连接已断开，丢弃通知: {preview}")
            return
        if self._loop is None:
            log.warning("[AIRI] 事件循环未运行，无法发送通知")
            return

        valid_kinds = {"alarm", "ping", "reminder"}
        if kind not in valid_kinds:
            kind = "ping"
        valid_urgency = {"immediate", "soon", "later"}
        if urgency not in valid_urgency:
            urgency = "immediate"

        if headline is None:
            first_line = message.split("\n", 1)[0].strip()
            headline = (first_line or message)[:100]

        payload = {
            "id": str(uuid.uuid4()),
            "eventId": str(uuid.uuid4()),
            "kind": kind,
            "urgency": urgency,
            "headline": headline,
            "note": message,
            "destinations": destinations or ["character"],
        }
        asyncio.run_coroutine_threadsafe(
            self._send_event("spark:notify", payload),
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
