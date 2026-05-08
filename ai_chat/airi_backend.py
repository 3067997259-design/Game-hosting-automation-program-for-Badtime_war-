"""
AIRI WebSocket LLM 后端
═══════════════════════
将 LLM 调用转发给 AIRI（Animated Intelligent Real-time Interactive 角色），
让 AIRI 作为 BasicAI 的"聊天皮肤"——游戏决策仍由 BasicAI 处理，
AIRI 只负责社交聊天，其回复中的 [ADJUST] 段可以反过来影响 BasicAI 的决策。
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
    ):
        self._conn = AiriConnection(ws_url, module_id, auth_token)
        self._chat_timeout = chat_timeout
        self._player_name = player_name
        self._personality = personality
        self._connected = False

    def connect(self):
        """连接 AIRI 并发送角色设定。"""
        self._conn.connect()
        self._connected = True
        self._send_role_setup()

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
    ) -> str:
        """实现 LLMBackend.chat()：将 messages 转为文本发给 AIRI。"""
        if not self._connected:
            return ""
        # 将 OpenAI 格式的 messages 列表转为单段文本
        text = self._format_messages(messages)
        self._conn.drain_responses()
        self._conn.send_text(text)
        reply = self._conn.wait_for_response(timeout=self._chat_timeout)
        return reply or ""

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

    def _format_messages(self, messages: List[Dict[str, str]]) -> str:
        """将 OpenAI 格式 messages 转为单段文本供 AIRI 理解。"""
        parts: List[str] = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "system":
                parts.append(f"[系统指令]\n{content}")
            elif role == "user":
                parts.append(content)
            elif role == "assistant":
                parts.append(f"[你之前的回复] {content}")
        return "\n\n".join(parts)

    def _send_role_setup(self):
        """向 AIRI 发送角色设定（首次连接时）。"""
        setup = (
            f'你是"{self._player_name}"，回合制桌游《起闯战争》中的一个AI玩家。\n'
            f'你的性格是：{self._personality}\n'
            '你的目标是成为最后存活的玩家。你可以结盟、欺骗、威胁、谈判。\n'
            '你的游戏行动由内置策略系统自动执行，你只负责社交聊天。\n'
            '你会收到游戏进展的通知，请据此调整你的社交策略。\n\n'
            '回复时请使用以下格式：\n'
            '[THINK] 你的内心分析（不会发送给任何人）\n'
            '[REPLY] 你的公开回复（会发送给聊天对象）\n'
            '[ADJUST] 行为调整JSON（可选）\n'
            '格式：{"threat_mod": {"玩家名": 增减值}, '
            '"alliance": ["盟友名"], "aggression": 增减值}\n'
        )
        self._conn.send_text(setup)
        self._conn.wait_for_response(timeout=15)
        self._conn.drain_responses()
