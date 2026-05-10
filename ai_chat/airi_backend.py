"""
AIRI WebSocket LLM 后端
═══════════════════════
将 LLM 调用转发给 AIRI（Animated Intelligent Real-time Interactive 角色），
让 AIRI 作为 BasicAI 的"聊天皮肤"——游戏决策仍由 BasicAI 处理，
AIRI 只负责社交聊天，其回复中的 [ADJUST] 段可以反过来影响 BasicAI 的决策。

与普通 OpenAI 后端不同，AIRI 是有记忆、有自己角色卡的有状态对话 AI。
本后端不向 AIRI 发送游戏 system prompt（避免覆盖 AIRI 自己的人设）：

1. 仅在连接时发送一次"游戏背景说明 + 回复格式建议"，不立人设。
2. 游戏状态通过 push_game_state() → context:update 推送（结构化）。
3. 游戏事件通过 push_game_event() → spark:notify 推送（实时通知）。
4. chat() 只把当前玩家的最新一条聊天内容通过 input:text 发给 AIRI。
"""

from typing import Any, Dict, List

from ai_chat.llm_backend import LLMBackend
from ai_chat.airi_connection import AiriConnection


class AiriBackend(LLMBackend):
    """AIRI WebSocket 后端：将 LLM 调用转发给 AIRI。"""

    def __init__(
        self,
        ws_url: str,
        auth_token: str = "",
        module_id: str = "badtime-war-bridge",
        player_name: str = "AI",
        personality: str = "balanced",
        chat_timeout: int = 30,
        heartbeat_interval: int = 30,
        max_reconnect_attempts: int = 10,
    ):
        self._conn = AiriConnection(
            ws_url,
            module_id,
            auth_token,
            heartbeat_interval=heartbeat_interval,
            max_reconnect_attempts=max_reconnect_attempts,
        )
        self._chat_timeout = chat_timeout
        self._player_name = player_name
        self._personality = personality
        self._connected = False

    @property
    def is_airi(self) -> bool:
        return True

    def connect(self):
        """连接 AIRI 并发送一次性的游戏背景说明。"""
        self._conn.connect()
        self._connected = True
        self._send_role_setup()

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
    ) -> str:
        """实现 LLMBackend.chat()。

        AIRI 是有状态的对话 AI：
        - 角色卡/人设由 AIRI 自己管理，不在此处覆盖。
        - 游戏状态通过 push_game_state()/push_game_event() 外部推送。
        - 本方法只把玩家的最新一条聊天内容通过 input:text 发给 AIRI。
        """
        if not self._connected:
            return ""

        text = self._format_messages(messages)
        if not text:
            return ""

        # 清空残留响应，发送实际聊天内容并等待回复
        self._conn.drain_responses()
        self._conn.send_text(text)
        reply = self._conn.wait_for_response(timeout=self._chat_timeout)
        return reply or ""

    def _format_messages(self, messages: List[Dict[str, str]]) -> str:
        """AIRI 模式：只提取玩家的聊天内容，不发送系统指令。"""
        # 只取最后一条 user 消息（即玩家的实际聊天）
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                # 去掉残留的 [公屏]/[私聊] 前缀（AIRI 不需要这些标记，
                # 它通过自然对话理解上下文；这里仅作向后兼容防御）
                for prefix in ("[公屏] ", "[私聊] "):
                    if content.startswith(prefix):
                        content = content[len(prefix):]
                        break
                return content
        return ""

    def push_game_state(self, state_narration: str, decision_context: str = ""):
        """通过 context:update 向 AIRI 推送游戏状态。

        与 system prompt 不同，context:update 是结构化的副通道——AIRI 会
        把它当作背景信息使用，而不是会显示在聊天窗口里的指令文本。
        """
        if not self._connected:
            return
        context: Dict[str, Any] = {"game_state": state_narration}
        if decision_context:
            context["strategy"] = decision_context
        self._conn.send_context(context)

    def push_game_event(self, event_text: str):
        """通过 spark:notify 向 AIRI 推送游戏事件通知。

        用于轮次开始、玩家死亡、战斗结果等离散事件。AIRI 收到后会作为
        实时通知处理，可以触发其反应（如评论、表情等）。
        """
        self.notify(event_text)

    def notify(self, event_text: str):
        """向 AIRI 推送游戏事件通知（实时感知）。"""
        if self._connected:
            self._conn.send_notify(event_text)

    def send_context(self, context: Dict[str, Any]):
        """向 AIRI 推送结构化游戏上下文。"""
        if self._connected:
            self._conn.send_context(context)

    @property
    def is_connected(self) -> bool:
        return self._connected and self._conn.is_connected

    def _send_role_setup(self):
        """向 AIRI 发送一次性的游戏背景和回复格式建议。

        不立人设——AIRI 有自己的角色卡和记忆系统，游戏只告诉它现在
        参与的是什么场景，以及如果想配合解析时建议使用哪种格式。
        """
        setup = (
            '【游戏背景】\n'
            '你正在参与一个回合制桌游《起闯战争》。\n'
            '你的游戏行动由内置策略系统自动执行，你只负责社交聊天。\n'
            '你会收到游戏进展的通知，请据此与其他玩家互动。\n\n'
            '【回复格式】\n'
            '当你回复其他玩家的聊天时，请尽量使用以下格式：\n'
            '[THINK] 你的内心想法（不会发送给任何人）\n'
            '[REPLY] 你的回复内容（会发送给聊天对象）\n'
            '[ADJUST] 行为调整JSON（可选，格式：'
            '{"threat_mod": {"玩家名": 增减值}, '
            '"alliance": ["盟友名"], "aggression": 增减值}）\n'
            '如果你不想用这个格式，直接回复也可以。\n'
        )
        self._conn.send_text(setup)
        self._conn.wait_for_response(timeout=15)
        self._conn.drain_responses()
