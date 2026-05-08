"""
AIRI Bot Bridge
═══════════════
将游戏服务器的 TCP 协议翻译为 AIRI 的 WebSocket 协议。
本脚本作为独立进程运行，不需要修改游戏或 AIRI 的任何源码。

启动方式：
  python bot_bridge.py
  python bot_bridge.py --config config/airi_bridge_config.json
"""

import argparse
import asyncio
import json
import logging
import re
import sys
import threading
import time
import uuid
from typing import Dict, Any, Optional, List, Callable

# 复用游戏项目的网络客户端
from network.client import NetworkClient
from network.protocol import MessageType

logging.basicConfig(
    level=logging.INFO,
    format="  [Bridge %(levelname)s] %(message)s"
)
log = logging.getLogger("bot_bridge")


# ══════════════════════════════════════════════════════════════════
#  AIRI WebSocket 连接管理
# ══════════════════════════════════════════════════════════════════

class AiriConnection:
    """管理与 AIRI WebSocket 服务器的连接。"""

    def __init__(self, ws_url: str, module_id: str, auth_token: str = ""):
        self.ws_url = ws_url
        self.module_id = module_id
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

        try:
            async with websockets.connect(self.ws_url) as ws:
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
                    "moduleId": self.module_id,
                    "type": "external",
                    "name": "Badtime War Bridge",
                    "capabilities": ["input", "context"],
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
                    "moduleId": self.module_id,
                    "type": "external",
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


# ══════════════════════════════════════════════════════════════════
#  回复解析器
# ══════════════════════════════════════════════════════════════════

class ResponseParser:
    """从 AIRI 的自然语言回复中提取游戏指令。"""

    # 行动指令的正则模式
    ACTION_PATTERNS = [
        r"ACTION:\s*(.+)",            # 标准格式: ACTION: move 商店
        r"行动:\s*(.+)",               # 中文格式
        r"我(?:选择|决定|要)(.+)",       # 自然语言: 我选择移动到商店
    ]

    # 选择指令的正则模式
    CHOOSE_PATTERNS = [
        r"CHOOSE:\s*(\d+)",           # 标准格式: CHOOSE: 3
        r"选择:\s*(\d+)",
        r"我选(?:择)?(?:第)?(\d+)",
    ]

    # 确认指令的正则模式
    CONFIRM_PATTERNS = [
        r"CONFIRM:\s*(y|n|yes|no|是|否)",
        r"确认:\s*(y|n|yes|no|是|否)",
    ]

    # 游戏指令关键词 → 指令前缀映射
    COMMAND_KEYWORDS = {
        "移动": "move", "去": "move", "前往": "move",
        "攻击": "attack", "打": "attack",
        "交互": "interact", "使用": "interact",
        "锁定": "lock", "找到": "find",
        "放弃": "forfeit", "跳过": "forfeit",
        "起床": "wake",
        "举报": "report", "集结": "assemble",
        "追踪": "track", "加入警察": "recruit",
        "竞选": "election", "指定": "designate",
        "研究": "study",
    }

    @classmethod
    def extract_action(cls, text: str, available_actions: List[str]) -> Optional[str]:
        """从回复中提取行动指令。返回 None 表示无法解析。"""
        text = text.strip()

        # 1. 尝试标准格式
        for pattern in cls.ACTION_PATTERNS:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                cmd = m.group(1).strip()
                # 验证指令是否以合法行动开头
                for action in available_actions:
                    if cmd.lower().startswith(action.lower()):
                        return cmd
                return cmd  # 即使不在列表中也返回，让服务器验证

        # 2. 尝试关键词匹配
        for keyword, prefix in cls.COMMAND_KEYWORDS.items():
            if keyword in text:
                # 尝试提取关键词后面的参数
                idx = text.index(keyword) + len(keyword)
                rest = text[idx:].strip().strip("到去了").strip()
                if rest:
                    return f"{prefix} {rest}"
                if prefix in ("forfeit", "wake", "assemble", "track",
                              "recruit", "election", "study"):
                    return prefix

        # 3. 直接检查回复是否就是一个合法指令
        for action in available_actions:
            if text.lower().startswith(action.lower()):
                return text

        return None

    @classmethod
    def extract_choice(cls, text: str, options: List[str]) -> Optional[str]:
        """从回复中提取选择。"""
        for pattern in cls.CHOOSE_PATTERNS:
            m = re.search(pattern, text)
            if m:
                try:
                    idx = int(m.group(1)) - 1
                    if 0 <= idx < len(options):
                        return options[idx]
                except ValueError:
                    pass

        # 直接匹配选项文本
        for opt in options:
            if opt and opt in text:
                return opt

        return None

    @classmethod
    def extract_confirm(cls, text: str) -> Optional[bool]:
        """从回复中提取确认结果。"""
        for pattern in cls.CONFIRM_PATTERNS:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                val = m.group(1).lower()
                return val in ("y", "yes", "是")

        # 模糊匹配
        positive = any(w in text for w in ("好", "可以", "确认", "同意", "是的", "没问题"))
        negative = any(w in text for w in ("不", "否", "拒绝", "算了", "取消"))
        if positive and not negative:
            return True
        if negative:
            return False

        return None


# ══════════════════════════════════════════════════════════════════
#  核心 Bridge 类
# ══════════════════════════════════════════════════════════════════

class BotBridge:
    """核心桥接逻辑：连接游戏服务器和 AIRI。"""

    # 用于过滤聊天回复中的格式化指令
    _FORMAT_CMD_RE = re.compile(r"(ACTION|CHOOSE|CONFIRM):\s*.+", re.IGNORECASE)

    def __init__(self, config: dict):
        self.config = config
        self.bot_name = config.get("bot_name", "AIRI_Bot")
        self.action_timeout = config.get("action_timeout", 60)
        self.chat_timeout = config.get("chat_reply_timeout", 30)

        # 游戏客户端
        self.game_client = NetworkClient(
            host=config.get("game_server_host", "127.0.0.1"),
            port=config.get("game_server_port", 9527),
        )

        # AIRI 连接
        self.airi = AiriConnection(
            ws_url=config.get("airi_ws_url", "ws://localhost:6121/ws"),
            module_id=config.get("module_id", "badtime-war-bridge"),
            auth_token=config.get("airi_auth_token", ""),
        )

        # 游戏状态追踪
        self.game_started = threading.Event()
        self.game_finished = threading.Event()
        self.game_events: List[str] = []  # 最近的游戏事件日志
        self.pending_request: Dict[str, Any] = {"msg": None, "msg_type": None}
        self.pending_lock = threading.Lock()
        self.pending_event = threading.Event()

    # ──────────────────────────────────────────
    #  启动
    # ──────────────────────────────────────────

    def start(self):
        """启动 Bridge：连接游戏服务器和 AIRI。"""
        # 1. 连接 AIRI
        log.info(f"正在连接 AIRI: {self.config.get('airi_ws_url')}")
        self.airi.connect()
        log.info("AIRI 连接成功")

        # 2. 向 AIRI 发送角色设定
        self._send_role_setup()

        # 3. 连接游戏服务器
        host = self.config.get("game_server_host", "127.0.0.1")
        port = self.config.get("game_server_port", 9527)
        log.info(f"正在连接游戏服务器: {host}:{port}")
        self.game_client.connect(self.bot_name)
        log.info("游戏服务器连接成功")

        # 4. 注册游戏事件处理器
        self._register_handlers()

        # 5. 主循环
        self._main_loop()

    def _send_role_setup(self):
        """向 AIRI 发送游戏角色设定。"""
        setup_text = (
            "你现在正在参与一个叫《起闯战争》的回合制桌游。"
            "你的目标是成为最后存活的玩家。"
            "游戏中你需要做出行动决策（移动、攻击、交互等）和社交决策（聊天、结盟、欺骗等）。\n\n"
            "当我告诉你'轮到你行动了'时，请用以下格式回复你的行动：\n"
            "ACTION: <你的行动指令>\n"
            "例如：ACTION: move 商店\n"
            "例如：ACTION: attack 玩家A 小刀\n"
            "例如：ACTION: forfeit\n\n"
            "当我让你选择时，请用以下格式回复：\n"
            "CHOOSE: <选项编号>\n\n"
            "当我让你确认时，请用以下格式回复：\n"
            "CONFIRM: y 或 CONFIRM: n\n\n"
            "其他时候你可以自由聊天，你的聊天内容会被发送到游戏的公屏。"
        )
        self.airi.send_text(setup_text)
        # 等待 AIRI 处理角色设定（丢弃这次回复）
        self.airi.wait_for_response(timeout=15)
        self.airi.drain_responses()  # 清空队列
        log.info("角色设定已发送")

    # ──────────────────────────────────────────
    #  游戏事件处理
    # ──────────────────────────────────────────

    def _register_handlers(self):
        """注册游戏服务器的消息处理器。"""
        self.game_client.on(MessageType.GAME_EVENT, self._on_game_event)
        self.game_client.on(MessageType.LOBBY_UPDATE, self._on_lobby_update)
        self.game_client.on(MessageType.CHAT_MESSAGE, self._on_chat_message)
        self.game_client.on(MessageType.DISCONNECT_NOTICE, self._on_disconnect)

        # 服务器请求 → pending_request
        for mt in (
            MessageType.REQUEST_COMMAND,
            MessageType.REQUEST_CHOOSE,
            MessageType.REQUEST_CHOOSE_MULTI,
            MessageType.REQUEST_CONFIRM,
        ):
            self.game_client.on(mt, self._make_request_handler(mt))

    def _make_request_handler(self, msg_type):
        def handler(msg):
            with self.pending_lock:
                self.pending_request["msg"] = msg
                self.pending_request["msg_type"] = msg_type
            self.pending_event.set()
        return handler

    def _on_game_event(self, msg):
        """处理游戏事件：记录日志 + 转发给 AIRI。"""
        event = msg.get("event", "")
        args = msg.get("args", [])

        # 构建人类可读的事件描述
        desc = self._format_event(event, args)
        if desc:
            self.game_events.append(desc)
            # 保留最近 50 条
            if len(self.game_events) > 50:
                self.game_events = self.game_events[-50:]
            # 转发给 AIRI
            self.airi.send_notify(desc)
            log.info(f"游戏事件: {desc[:80]}")

        if event == "game_finished":
            self.game_finished.set()
            self.pending_event.set()

    def _format_event(self, event: str, args: list) -> str:
        """将游戏事件格式化为自然语言。"""
        if event == "show_round_header":
            return f"=== 第 {args[0] if args else '?'} 轮 ==="
        if event == "show_phase":
            return f"--- {args[0] if args else ''} ---"
        if event == "show_action_turn_header":
            return f"轮到 {args[0] if args else '?'} 行动"
        if event == "show_result":
            return str(args[0]) if args else ""
        if event == "show_info":
            return str(args[0]) if args else ""
        if event == "show_death":
            name = args[0] if args else "?"
            cause = args[1] if len(args) > 1 else "未知"
            return f"{name} 死亡！原因：{cause}"
        if event == "show_victory":
            return f"{args[0] if args else '?'} 获得了最终胜利！"
        if event == "show_error":
            return f"[错误] {args[0] if args else ''}"
        return ""

    def _on_lobby_update(self, msg):
        state = msg.get("room_state", "")
        if state == "in_game":
            log.info("游戏开始！")
            self.game_started.set()
            self.airi.send_notify("游戏正式开始了！准备好战斗吧。")
        slots = msg.get("slots", [])
        for s in slots:
            log.info(
                f"  [{s.get('slot_id', '?')}] "
                f"{str(s.get('slot_type', '')):12s} | "
                f"{s.get('player_name', '空')}"
            )

    def _on_chat_message(self, msg):
        """收到聊天消息 → 转发给 AIRI。"""
        sender = msg.get("sender", "")
        content = msg.get("content", "")
        channel = msg.get("channel", "public")

        if sender == self.bot_name:
            return  # 不转发自己的消息

        prefix = "[私聊]" if channel == "private" else "[公屏]"
        text = f"{prefix} {sender}: {content}"
        log.info(f"聊天: {text}")

        # 发给 AIRI
        self.airi.send_text(text)

        # 等待 AIRI 的聊天回复（非阻塞，用短超时）
        reply = self.airi.wait_for_response(timeout=self.chat_timeout)
        if reply and not reply.startswith("COMMAND:"):
            # 过滤掉格式化指令，只发送纯聊天内容
            clean_reply = self._FORMAT_CMD_RE.sub("", reply).strip()
            if clean_reply:
                payload = {
                    "type": MessageType.CHAT_SEND,
                    "sender": self.bot_name,
                    "content": clean_reply,
                    "channel": channel,
                }
                if channel == "private":
                    payload["target"] = sender
                self.game_client.send_sync(payload)
                log.info(f"AIRI 回复: {clean_reply[:80]}")

    def _on_disconnect(self, msg):
        name = msg.get("player_name", "")
        action = msg.get("action", "")
        log.info(f"断线通知: {name} {action}")
        self.airi.send_notify(f"玩家 {name} {action}")

    # ──────────────────────────────────────────
    #  主循环
    # ──────────────────────────────────────────

    def _flush_idle_chat(self):
        """处理 AIRI 的主动聊天（非请求触发的回复）。"""
        for reply in self.airi.drain_responses():
            if reply.startswith("COMMAND:"):
                continue
            clean = self._FORMAT_CMD_RE.sub("", reply).strip()
            if clean:
                self.game_client.send_sync({
                    "type": MessageType.CHAT_SEND,
                    "sender": self.bot_name,
                    "content": clean,
                    "channel": "public",
                })

    def _main_loop(self):
        """主循环：等待游戏开始，然后处理服务器请求。"""
        log.info("等待游戏开始...")

        try:
            while not self.game_started.is_set():
                if self.game_started.wait(timeout=1):
                    break
                # 处理 AIRI 的主动聊天（如果有）
                self._flush_idle_chat()

            log.info("游戏已开始，进入主循环")

            while self.game_client.is_connected and not self.game_finished.is_set():
                # 检查挂起的请求
                with self.pending_lock:
                    req_msg = self.pending_request["msg"]
                    req_type = self.pending_request["msg_type"]
                    self.pending_request["msg"] = None
                    self.pending_request["msg_type"] = None

                if req_msg is not None:
                    self._handle_request(req_msg, req_type)
                    self.pending_event.clear()
                    continue

                # 等待下一个请求
                self.pending_event.wait(timeout=1.0)
                if self.pending_event.is_set():
                    self.pending_event.clear()
                    continue

                # 处理 AIRI 的主动聊天
                self._flush_idle_chat()

        except KeyboardInterrupt:
            log.info("Bridge 被中断")
        finally:
            self.game_client.disconnect()
            log.info("已断开连接")

    # ──────────────────────────────────────────
    #  请求分发
    # ──────────────────────────────────────────

    def _handle_request(self, msg: dict, msg_type):
        """处理服务器的请求：转发给 AIRI，解析回复，发送响应。"""
        if msg_type == MessageType.REQUEST_COMMAND:
            self._handle_command_request(msg)
        elif msg_type == MessageType.REQUEST_CHOOSE:
            self._handle_choose_request(msg)
        elif msg_type == MessageType.REQUEST_CHOOSE_MULTI:
            self._handle_choose_multi_request(msg)
        elif msg_type == MessageType.REQUEST_CONFIRM:
            self._handle_confirm_request(msg)

    def _handle_command_request(self, msg: dict):
        """处理行动请求。"""
        actions = msg.get("available_actions", [])
        context = msg.get("context", {})

        # 构建发给 AIRI 的提示
        prompt = (
            f"轮到你行动了！（第 {context.get('round', '?')} 轮）\n"
            f"可选行动: {', '.join(actions)}\n"
            f"请用 ACTION: <指令> 的格式回复你的行动。"
        )

        # 清空之前的回复
        self.airi.drain_responses()

        # 发送给 AIRI
        self.airi.send_text(prompt)
        log.info(f"请求行动: 可选 {actions}")

        # 等待回复
        command = "forfeit"  # 默认 forfeit
        reply = self.airi.wait_for_response(timeout=self.action_timeout)

        if reply:
            log.info(f"AIRI 原始回复: {reply[:200]}")
            parsed = ResponseParser.extract_action(reply, actions)
            if parsed:
                command = parsed
                log.info(f"解析出行动: {command}")
            else:
                log.warning(f"无法解析行动，使用 forfeit。原始回复: {reply[:200]}")
        else:
            log.warning("AIRI 超时未回复，使用 forfeit")

        self.game_client.send_sync({
            "type": MessageType.COMMAND_RESPONSE,
            "command": command,
        })

    def _handle_choose_request(self, msg: dict):
        """处理选择请求（如天赋选择）。"""
        prompt_text = msg.get("prompt", "请选择")
        options = msg.get("options", [])

        text = f"{prompt_text}\n"
        for i, opt in enumerate(options, 1):
            text += f"  {i}. {opt}\n"
        text += "请用 CHOOSE: <编号> 的格式回复。"

        self.airi.drain_responses()
        self.airi.send_text(text)
        log.info(f"请求选择: {options}")

        choice = options[0] if options else ""
        reply = self.airi.wait_for_response(timeout=self.action_timeout)

        if reply:
            log.info(f"AIRI 原始回复: {reply[:200]}")
            parsed = ResponseParser.extract_choice(reply, options)
            if parsed:
                choice = parsed
                log.info(f"解析出选择: {choice}")
            else:
                log.warning(f"无法解析选择，使用默认: {choice}")

        self.game_client.send_sync({
            "type": MessageType.CHOOSE_RESPONSE,
            "choice": choice,
        })

    def _handle_choose_multi_request(self, msg: dict):
        """处理多选请求。"""
        prompt_text = msg.get("prompt", "请选择")
        options = msg.get("options", [])
        max_count = msg.get("max_count", 1)
        min_count = msg.get("min_count", 0)

        text = f"{prompt_text} (选 {min_count}~{max_count} 个)\n"
        for i, opt in enumerate(options, 1):
            text += f"  {i}. {opt}\n"
        text += "请用 CHOOSE: <编号1>,<编号2> 的格式回复（逗号分隔）。"

        self.airi.drain_responses()
        self.airi.send_text(text)
        log.info(f"请求多选: {options}")

        selected: List[str] = []
        reply = self.airi.wait_for_response(timeout=self.action_timeout)
        if reply:
            log.info(f"AIRI 原始回复: {reply[:200]}")
            # 提取所有数字
            numbers = re.findall(r"\d+", reply)
            for n in numbers:
                try:
                    idx = int(n) - 1
                except ValueError:
                    continue
                if 0 <= idx < len(options) and options[idx] not in selected:
                    selected.append(options[idx])
                    if len(selected) >= max_count:
                        break

        # 不足 min_count 时尝试用前 N 个选项补齐，避免服务器超时
        if len(selected) < min_count:
            for opt in options:
                if opt not in selected:
                    selected.append(opt)
                    if len(selected) >= min_count:
                        break

        self.game_client.send_sync({
            "type": MessageType.CHOOSE_MULTI_RESPONSE,
            "choices": selected,
        })

    def _handle_confirm_request(self, msg: dict):
        """处理确认请求。"""
        prompt_text = msg.get("prompt", "确认？")

        self.airi.drain_responses()
        self.airi.send_text(f"{prompt_text}\n请用 CONFIRM: y 或 CONFIRM: n 回复。")
        log.info(f"请求确认: {prompt_text}")

        result = False
        reply = self.airi.wait_for_response(timeout=self.action_timeout)
        if reply:
            log.info(f"AIRI 原始回复: {reply[:200]}")
            parsed = ResponseParser.extract_confirm(reply)
            if parsed is not None:
                result = parsed

        self.game_client.send_sync({
            "type": MessageType.CONFIRM_RESPONSE,
            "result": result,
        })


# ══════════════════════════════════════════════════════════════════
#  入口
# ══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="AIRI Bot Bridge - 起闯战争")
    parser.add_argument(
        "--config", type=str, default="config/airi_bridge_config.json",
        help="配置文件路径",
    )
    parser.add_argument("--name", type=str, default=None, help="Bot 名称（覆盖配置文件）")
    parser.add_argument("--debug", action="store_true", help="启用调试日志")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # 加载配置
    try:
        with open(args.config, "r", encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError:
        log.error(f"配置文件不存在: {args.config}")
        log.info(
            "请复制 config/airi_bridge_config.example.json "
            "为 config/airi_bridge_config.json 并修改"
        )
        sys.exit(1)
    except json.JSONDecodeError as e:
        log.error(f"配置文件格式错误: {e}")
        sys.exit(1)

    if args.name:
        config["bot_name"] = args.name

    print(f"\n  ═══════════════════════════════════════")
    print(f"    起闯战争 - AIRI Bot Bridge")
    print(f"  ═══════════════════════════════════════")
    print(f"  AIRI:    {config.get('airi_ws_url', 'ws://localhost:6121/ws')}")
    print(
        f"  游戏服务器: "
        f"{config.get('game_server_host', '127.0.0.1')}:"
        f"{config.get('game_server_port', 9527)}"
    )
    print(f"  Bot 名称: {config.get('bot_name', 'AIRI_Bot')}")
    print()

    bridge = BotBridge(config)
    try:
        bridge.start()
    except ConnectionError as e:
        log.error(str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
