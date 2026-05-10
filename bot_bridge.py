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
import json
import logging
import re
import sys
import threading
from typing import Dict, Any, Optional, List

# 复用游戏项目的网络客户端
from network.client import NetworkClient
from network.protocol import MessageType
from ai_chat.airi_connection import AiriConnection

logging.basicConfig(
    level=logging.INFO,
    format="  [Bridge %(levelname)s] %(message)s"
)
log = logging.getLogger("bot_bridge")


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
#  指令战略意图解释器
# ══════════════════════════════════════════════════════════════════

class CommandIntentExplainer:
    """将游戏指令映射为人类可读的战略意图说明。

    目标：
    - 让 AIRI 不只看到"可选: move/attack/forfeit"这种孤立列表，
      还能理解每个指令在《起闯战争》游戏规则下的战略意义。
    - 避免 AIRI 因为不懂规则而产生胡乱选择或误解（例如把 forfeit
      理解为投降）。

    属性克制（来源：README §8.2）：
        普通 → 魔法（有效）
        魔法 → 科技（有效）
        科技 → 普通（有效）
        同属性相互有效
        若武器属性被护甲属性克制，攻击无效（护甲不消耗）。
    即三种属性形成循环克制 普通 → 魔法 → 科技 → 普通；同属性互克。
    """

    # 指令前缀（小写）→ 简短战略意图说明
    INTENT_MAP: Dict[str, str] = {
        "move": (
            "移动到指定地点；用于接近目标、跑去关键设施（商店、医院、"
            "军事基地、警察局等）或脱离危险区域。是控制位置的核心手段。"
        ),
        "attack": (
            "使用某件武器攻击目标。属性克制循环为：普通→魔法→科技→普通，"
            "同属性相互有效；若武器被护甲克制则攻击无效（护甲不消耗）。"
            "格式：attack <目标> <武器> [层 属性]。优先打能造成有效伤害的目标。"
        ),
        "interact": (
            "与当前地点的设施/物品互动（购买、手术、研究、领取奖励等）。"
            "通常用于补给、强化、获取关键凭证。"
        ),
        "lock": (
            "锁定一个目标玩家用于后续追踪/打击；不会直接造成伤害，"
            "是一种信息/战术先手布置。"
        ),
        "find": (
            "暴露/找到一个隐匿的玩家，使其位置可见；适合在情报缺失时"
            "打破对手隐身或应对潜伏威胁。"
        ),
        "forfeit": (
            "放弃本次行动（既不进攻也不移动也不交互）。仅在没有任何更优"
            "选择、或主动避战节奏时使用，并非投降游戏。"
        ),
        "wake": (
            "起床动作：自己尚未起床时使其出现在自己家中；或唤醒同地点"
            "处于 debuff 的警察单位（wake <警察ID>）。"
        ),
        "report": (
            "在警察局举报有犯罪记录的玩家，启动警察响应流程；"
            "是不直接出手却能借刀杀人的关键政治手段。"
        ),
        "assemble": (
            "作为举报人集结警察出动，开始对被举报者的执法行动。"
        ),
        "track": (
            "作为举报人引导警察立刻追踪目标到达其位置；用于关键时刻"
            "快速锁敌。"
        ),
        # 服务器端注册的内部 action 名是 track_guide（cli/parser.py 把 track 映射到
        # track_guide），这里同时收录两种 key，避免 build_intent_block 漏掉。
        "track_guide": (
            "作为举报人引导警察立刻追踪目标到达其位置；用于关键时刻"
            "快速锁敌。"
        ),
        "recruit": (
            "在警察局加入警队（无犯罪记录、无既有警察时可用），"
            "换取三选二奖励、获得警察身份与执法资格。"
        ),
        "election": (
            "竞选警察队长，需在警察局连续推进进度；当上队长后可指定执法"
            "目标、做研究性学习、控制威信资源。"
        ),
        "designate": (
            "队长专属：指定警察的执法目标；用于把警力对准你想清除的玩家。"
        ),
        "study": (
            "队长专属：在警察局做研究性学习，威信+1；威信归零会重置警察"
            "系统，因此守住威信很重要。"
        ),
        "special": (
            "使用角色专属/特殊操作（天赋技能等）；具体效果取决于当前"
            "角色，通常是改变战局的高价值操作。"
        ),
        "split": (
            "作为队长拆分警队（split <警队ID>）：把一支警队拆成两支独立警队，"
            "用于扩大警力覆盖、分散兵力或解除原警队的纠缠状态。"
        ),
        # 队长操控警察。命令前缀同时有 police 和 police_command 两种来源：
        # - 服务器 _get_available_actions 返回 action 名 "police_command"
        # - 玩家输入是 "police move/equip/attack ..."
        # 两个 key 都收录，确保 INTENT_MAP 命中。
        "police": (
            "队长专属：直接操控警察执行 move/equip/attack 等子命令；"
            "用于亲自调度警队、配装、指定打击目标。"
        ),
        "police_command": (
            "队长专属：直接操控警察执行 move/equip/attack 等子命令；"
            "用于亲自调度警队、配装、指定打击目标。"
        ),
        # 唤醒处于 debuff 的警察单位（wake_police <警察ID>）；
        # 玩家也可以用 wake <警察ID> 触发同一行为。
        "wake_police": (
            "唤醒处于 debuff 状态的同地点警察单位，使其恢复行动能力。"
        ),
    }

    @classmethod
    def explain(cls, command: str) -> str:
        """返回指令的战略意图说明。command 可以是完整指令（含参数）
        或仅指令前缀；解析失败时返回空字符串。"""
        if not command:
            return ""
        prefix = command.strip().split()[0].lower()
        return cls.INTENT_MAP.get(prefix, "")

    @classmethod
    def explain_action_dict(cls, action: Any) -> str:
        """针对服务器下发的 action 描述（可能是 str 或 dict）返回意图说明。"""
        if isinstance(action, dict):
            usage = action.get("usage") or action.get("name") or ""
            return cls.explain(usage)
        if isinstance(action, str):
            return cls.explain(action)
        return ""

    @classmethod
    def build_intent_block(cls, actions: List[Any]) -> str:
        """为一组可选行动生成多行的意图说明文本，用作 prompt 增强。"""
        lines: List[str] = []
        seen_prefixes = set()
        for action in actions:
            if isinstance(action, dict):
                key = (action.get("usage") or action.get("name") or "").strip()
            else:
                key = str(action).strip()
            if not key:
                continue
            prefix = key.split()[0].lower()
            if prefix in seen_prefixes:
                continue
            intent = cls.INTENT_MAP.get(prefix)
            if not intent:
                continue
            seen_prefixes.add(prefix)
            lines.append(f"- {prefix}: {intent}")
        return "\n".join(lines)


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
            heartbeat_interval=int(config.get("heartbeat_interval", 30)),
            max_reconnect_attempts=int(
                config.get("max_reconnect_attempts", 10)
            ),
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
        """处理行动请求：构建带战略意图的 prompt + 多层重试 + 智能 fallback。"""
        actions = msg.get("available_actions", [])
        context = msg.get("context", {})

        log.info(f"请求行动: 可选 {actions}")
        command = self._try_get_command_with_retry(msg, actions, context)

        if not command:
            command = self._smart_fallback_command(msg, actions, context)
            log.warning(f"AIRI 无有效回复，使用智能 fallback: {command}")

        self.game_client.send_sync({
            "type": MessageType.COMMAND_RESPONSE,
            "command": command,
        })

    # ──────────────────────────────────────────
    #  Prompt 构建 / 多层重试 / 智能 fallback
    # ──────────────────────────────────────────

    def _build_command_prompt(
        self,
        msg: dict,
        actions: List[Any],
        context: Dict[str, Any],
        attempt: int,
    ) -> str:
        """构建行动 prompt。attempt=0 是首次，>=1 是重试（更明确）。

        字段布局（与 NetworkController.get_command 一致）：
        - 顶层 msg：hp / max_hp / location / player_name / available_actions
        - 嵌套 context：phase / round / attempt 等
        因此生命/位置从 msg 读，轮次/阶段从 context 读。
        """
        action_lines = []
        for action in actions:
            if isinstance(action, dict):
                usage = action.get("usage", "")
                name = action.get("name", "")
                desc = action.get("description", "")
                if usage:
                    line = f"- {usage}"
                    if name and name not in usage:
                        line += f"（{name}）"
                    if desc:
                        line += f" — {desc}"
                    action_lines.append(line)
            else:
                action_lines.append(f"- {action}")

        actions_text = "\n".join(action_lines) if action_lines else "(无可用行动)"
        intent_block = CommandIntentExplainer.build_intent_block(actions)

        round_no = context.get("round", "?")
        phase = context.get("phase", "")
        location = msg.get("location", "")
        hp = msg.get("hp")
        max_hp = msg.get("max_hp")

        situation_lines: List[str] = [f"当前轮次：第 {round_no} 轮"]
        if phase:
            situation_lines.append(f"阶段：{phase}")
        if location:
            situation_lines.append(f"你的位置：{location}")
        # 这里使用 is not None 而不是 != ''，避免 hp/max_hp 是 0 或空串时的
        # 类型混淆；NetworkController 下发的是 int，但旧路径也可能是 None。
        if hp is not None and max_hp is not None and max_hp != "":
            situation_lines.append(f"生命值：{hp}/{max_hp}")
        situation = "\n".join(situation_lines)

        if attempt == 0:
            header = "轮到你行动了！请基于游戏状态选择最合适的行动。"
            tail = (
                "请用以下格式回复（必须以 ACTION: 开头，否则会被忽略）：\n"
                "ACTION: <完整指令>\n"
                "例如：ACTION: move 商店\n"
                "例如：ACTION: attack 玩家A 小刀 外层 普通\n"
                "例如：ACTION: forfeit"
            )
        elif attempt == 1:
            header = (
                "⚠️ 上一次回复无法解析。请严格遵守格式要求重新决策。"
            )
            tail = (
                "必须回复一行以 ACTION: 开头的指令，紧跟一个合法行动前缀。\n"
                "合法前缀只能是：move / attack / interact / lock / find / "
                "forfeit / wake / report / assemble / track / recruit / "
                "election / designate / study / special / split / police。\n"
                "示例：ACTION: forfeit"
            )
        else:
            header = (
                "⚠️ 仍然无法识别你的行动。请只输出一行内容，不要解释、"
                "不要思考、不要 [THINK]/[REPLY]/[ADJUST] 段。"
            )
            tail = (
                "只回复一行，例如：ACTION: forfeit\n"
                "或：ACTION: move <地点>\n"
                "否则系统会自动为你选一个稳妥的默认行动。"
            )

        parts = [header, "", "【当前状况】", situation, "", "【可选行动】", actions_text]
        if intent_block:
            parts.extend(["", "【指令战略意图】", intent_block])
        parts.extend(["", tail])
        return "\n".join(parts)

    def _try_get_command_with_retry(
        self,
        msg: dict,
        actions: List[Any],
        context: Dict[str, Any],
        max_attempts: int = 3,
    ) -> Optional[str]:
        """多层重试机制：尝试解析 AIRI 的行动回复，失败时使用渐进式提示重试。

        返回解析成功的指令字符串；全部失败时返回 None，由调用方触发
        智能 fallback。
        """
        action_prefixes = self._extract_action_prefixes(actions)

        for attempt in range(max_attempts):
            prompt = self._build_command_prompt(msg, actions, context, attempt)
            self.airi.drain_responses()
            self.airi.send_text(prompt)

            reply = self.airi.wait_for_response(timeout=self.action_timeout)
            if not reply:
                log.warning(f"AIRI 第 {attempt + 1} 次尝试超时未回复")
                continue

            log.info(f"AIRI 第 {attempt + 1} 次原始回复: {reply[:200]}")
            parsed = ResponseParser.extract_action(reply, action_prefixes)
            if parsed:
                intent = CommandIntentExplainer.explain(parsed)
                if intent:
                    log.info(f"解析出行动: {parsed}（意图：{intent[:60]}）")
                else:
                    log.info(f"解析出行动: {parsed}")
                return parsed

            log.warning(
                f"第 {attempt + 1} 次无法解析行动。原始回复: {reply[:200]}"
            )

        return None

    def _extract_action_prefixes(self, actions: List[Any]) -> List[str]:
        """从 actions（可能是 str 或 dict 列表）中抽取可用指令前缀。"""
        prefixes: List[str] = []
        for action in actions:
            if isinstance(action, dict):
                usage = action.get("usage", "")
                if usage:
                    prefix = usage.strip().split()[0]
                    if prefix and prefix not in prefixes:
                        prefixes.append(prefix)
            elif isinstance(action, str):
                prefix = action.strip().split()[0]
                if prefix and prefix not in prefixes:
                    prefixes.append(prefix)
        return prefixes

    # 这些指令前缀在 cli/parser.py 中允许「无参数」直接解析成功；fallback 只能从
    # 这个集合里挑「裸前缀」回复，否则游戏服务器会因 "len(parts) < 2" 而拒绝指令。
    _BARE_OK_PREFIXES = frozenset({
        "forfeit",
        "wake",
        "assemble",
        "track",       # parser 接受 track，并映射到 track_guide
        "track_guide",
        "recruit",
        "election",
        "study",
    })

    def _smart_fallback_command(
        self,
        msg: dict,
        actions: List[Any],
        context: Dict[str, Any],
    ) -> str:
        """智能 fallback：在 AIRI 全部回复都无法解析时，根据当前上下文
        选择一个相对稳妥的默认行动，而不是无脑 forfeit 卡死局面。

        关键约束：服务器解析器要求 move/interact/attack/lock/find/report/designate
        /special/split/police_command/wake_police 等指令必须带参数，否则会被
        直接拒绝。因此 fallback 只能返回：
          - 一个完整的「带参指令」（例如 "move 医院"），或
          - 一个属于 _BARE_OK_PREFIXES 的无参指令（forfeit / wake / ...）。

        优先级：
        1. 若尚未起床：wake（起床），让自己进入可行动状态。
        2. 若血量低且可以 move：move 医院（带参，安全）。
        3. 若在补给/强化型地点且可以 interact：interact <默认项目>（带参）。
        4. 若 _BARE_OK_PREFIXES 中存在唯一非 forfeit 的可选项：直接选它。
        5. 否则 forfeit。

        hp / max_hp / location 从顶层 msg 读取（NetworkController 在那里下发），
        不是从 context。
        """
        prefixes = self._extract_action_prefixes(actions)
        location = msg.get("location", "")
        hp = msg.get("hp")
        max_hp = msg.get("max_hp")

        if "wake" in prefixes:
            return "wake"

        # 血量危险且可移动：去医院（带参，安全）
        try:
            if (hp is not None and max_hp not in (None, 0)
                    and float(hp) / float(max_hp) <= 0.4
                    and "move" in prefixes
                    and location != "医院"):
                return "move 医院"
        except (TypeError, ValueError):
            pass

        # 在补给/强化型地点且可 interact：用一个该地点的默认交互项目
        # （bare "interact" 会被 cli/parser.py:38-39 拒绝）。
        if "interact" in prefixes:
            default_item = self._default_interact_item_for(location)
            if default_item:
                return f"interact {default_item}"

        # 在 _BARE_OK_PREFIXES 内挑一个唯一的非 forfeit 选项；带参指令不能裸返回。
        bare_ok_non_forfeit = [
            p for p in prefixes
            if p != "forfeit" and p in self._BARE_OK_PREFIXES
        ]
        if len(bare_ok_non_forfeit) == 1:
            return bare_ok_non_forfeit[0]

        if "forfeit" in prefixes:
            return "forfeit"
        # 走到这里说明服务器没下发 forfeit（极少见，比如 wake-only 状态），
        # 此时只能从允许裸返回的前缀里硬挑一个，避免发出会被拒绝的带参指令。
        if bare_ok_non_forfeit:
            return bare_ok_non_forfeit[0]
        return "forfeit"

    @staticmethod
    def _default_interact_item_for(location: str) -> str:
        """给 interact 选一个该地点最可能成功的默认项目；未知地点返回空串。"""
        # 仅给出最稳妥的兜底；不试图穷举所有项目，避免误用罕见交互。
        defaults = {
            "医院": "打工",
            "商店": "打工",
            "军事基地": "办理通行证",
            "魔法所": "魔法护盾",
        }

    def _handle_choose_request(self, msg: dict):
        """处理选择请求（如天赋选择）：渐进式提示 + 强健的 fallback。"""
        prompt_text = msg.get("prompt", "请选择")
        options = msg.get("options", [])

        if not options:
            log.warning("choose 请求选项为空，回复空串")
            self.game_client.send_sync({
                "type": MessageType.CHOOSE_RESPONSE,
                "choice": "",
            })
            return

        log.info(f"请求选择: {options}")
        choice: Optional[str] = None

        for attempt in range(2):
            options_block = "\n".join(
                f"  {i}. {opt}" for i, opt in enumerate(options, 1)
            )
            if attempt == 0:
                text = (
                    f"{prompt_text}\n"
                    f"{options_block}\n"
                    "请用 CHOOSE: <编号> 的格式回复（编号从 1 开始）。"
                )
            else:
                text = (
                    "⚠️ 上一次回复无法解析为选项编号。请只回复一行：\n"
                    f"CHOOSE: <1~{len(options)}>\n\n"
                    f"{prompt_text}\n"
                    f"{options_block}"
                )

            self.airi.drain_responses()
            self.airi.send_text(text)
            reply = self.airi.wait_for_response(timeout=self.action_timeout)
            if not reply:
                log.warning(f"choose 第 {attempt + 1} 次超时未回复")
                continue
            log.info(f"AIRI 第 {attempt + 1} 次原始回复: {reply[:200]}")
            parsed = ResponseParser.extract_choice(reply, options)
            if parsed:
                choice = parsed
                log.info(f"解析出选择: {choice}")
                break
            log.warning(f"第 {attempt + 1} 次无法解析选择")

        if choice is None:
            choice = options[0]
            log.warning(f"choose 全部尝试失败，使用首个选项作为 fallback: {choice}")

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
        """处理确认请求：渐进式提示 + 安全 fallback（默认拒绝）。"""
        prompt_text = msg.get("prompt", "确认？")
        log.info(f"请求确认: {prompt_text}")

        result: Optional[bool] = None

        for attempt in range(2):
            if attempt == 0:
                text = (
                    f"{prompt_text}\n"
                    "请用 CONFIRM: y（同意）或 CONFIRM: n（拒绝）回复。"
                )
            else:
                text = (
                    "⚠️ 上一次回复无法识别为是/否。请只回复一行：\n"
                    "CONFIRM: y  或  CONFIRM: n\n\n"
                    f"问题：{prompt_text}"
                )

            self.airi.drain_responses()
            self.airi.send_text(text)
            reply = self.airi.wait_for_response(timeout=self.action_timeout)
            if not reply:
                log.warning(f"confirm 第 {attempt + 1} 次超时未回复")
                continue
            log.info(f"AIRI 第 {attempt + 1} 次原始回复: {reply[:200]}")
            parsed = ResponseParser.extract_confirm(reply)
            if parsed is not None:
                result = parsed
                log.info(f"解析出确认: {result}")
                break
            log.warning(f"第 {attempt + 1} 次无法解析确认")

        if result is None:
            # 安全 fallback：未确认则视为拒绝，避免误触发不可逆操作
            result = False
            log.warning("confirm 全部尝试失败，使用安全默认值: False（拒绝）")

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
