"""
AIRI WebSocket LLM 后端
═══════════════════════
将 LLM 调用转发给 AIRI（Animated Intelligent Real-time Interactive 角色），
让 AIRI 作为 BasicAI 的"聊天皮肤"——游戏决策仍由 BasicAI 处理，
AIRI 只负责社交聊天，其回复中的 [ADJUST] 段可以反过来影响 BasicAI 的决策。

与普通 OpenAI 后端不同，AIRI 是有记忆的有状态对话 AI。本后端不再每条
消息都把完整 system prompt 拼成文本发送，而是：
1. 角色设定通过 _send_role_setup() 在连接时仅发送一次
2. 游戏状态通过 spark:notify 增量推送（仅在变化时）
3. chat() 只发送实际的玩家聊天内容
"""

from typing import Any, Dict, List

from ai_chat.llm_backend import LLMBackend
from ai_chat.airi_connection import AiriConnection


# system prompt 中的动态状态段（按出现顺序），用于增量推送
_DYNAMIC_SECTION_HEADERS = (
    "【你的状态】",
    "【对手情报】",
    "【你的天赋】",
    "【全局状态】",
    "【战略评估】",
    "【当前可用行动】",
    "【你的内部策略状态",
)

# system prompt 中的所有可识别 section header（用于切片定位）
_ALL_SECTION_HEADERS = _DYNAMIC_SECTION_HEADERS + (
    "【回复格式】",
    "【核心规则】",
)


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
        self._last_context_hash: int = 0

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
        """实现 LLMBackend.chat()。

        AIRI 是有状态的对话 AI：角色卡仅在连接时通过 _send_role_setup
        发送一次；游戏状态通过 spark:notify 增量推送；本方法只把当前
        玩家的最新一条聊天内容通过 input:text 发给 AIRI。
        """
        if not self._connected:
            return ""

        # 1) 提取 system 消息中的游戏状态，按需通过 spark:notify 推送
        system_msg = ""
        for msg in messages:
            if msg.get("role") == "system":
                system_msg = msg.get("content", "")
                break
        if system_msg:
            self._push_game_context(system_msg)

        # 2) 只提取最后一条 user 消息（实际聊天内容）
        last_user_msg = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                last_user_msg = msg.get("content", "")
                break

        if not last_user_msg:
            return ""

        # 3) 清空残留响应，发送实际聊天内容并等待回复
        self._conn.drain_responses()
        self._conn.send_text(last_user_msg)
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

    def _push_game_context(self, system_prompt: str):
        """从 system prompt 中提取游戏状态段，按需通过 spark:notify 推送。

        只在内容变化时推送以避免重复刷屏。不发送角色定义和格式指令
        （这些在 _send_role_setup 中已发过），只发送动态变化的状态段。
        """
        prompt_hash = hash(system_prompt)
        if prompt_hash == self._last_context_hash:
            return
        self._last_context_hash = prompt_hash

        sections: List[str] = []
        for header in _DYNAMIC_SECTION_HEADERS:
            idx = system_prompt.find(header)
            if idx < 0:
                continue
            # 找到下一个任意 section 的起始位置作为切片终点
            search_from = idx + len(header)
            next_idx = len(system_prompt)
            for other_header in _ALL_SECTION_HEADERS:
                other_idx = system_prompt.find(other_header, search_from)
                if 0 <= other_idx < next_idx:
                    next_idx = other_idx
            sections.append(system_prompt[idx:next_idx].strip())

        if sections:
            context_text = "\n".join(sections)
            self._conn.send_notify(f"[游戏状态更新]\n{context_text}")

    def _send_role_setup(self):
        """向 AIRI 发送角色设定（仅首次连接时）。

        角色设定不覆盖 AIRI 自身的 persona——它只是告诉 AIRI 它正在
        参与的游戏框架、自己的游戏角色名、以及期望的回复格式。
        """
        setup = (
            f'[游戏角色设定]\n'
            f'你现在加入了一个朋友之间的回合制桌游《起闯战争》。\n'
            f'你的游戏角色名是"{self._player_name}"，性格是{self._personality}。\n'
            f'你的游戏目标是成为最后存活的玩家，但这是朋友局，氛围轻松有趣。\n'
            f'你的游戏行动由内置策略系统自动执行，你只负责社交聊天。\n'
            f'你会不定期收到游戏状态更新通知，请据此调整你的社交策略。\n\n'
            f'回复聊天时，请在回复开头加上 [REPLY] 标签。\n'
            f'如果你想记录内心想法，在前面加 [THINK]（不会发给其他人）。\n'
            f'如果你想调整游戏策略，在最后加 [ADJUST] 和一个JSON。\n'
            f'例如：\n'
            f'[THINK] 他在试探我\n'
            f'[REPLY] 哈哈，你想多了～\n'
            f'[ADJUST] {{"threat_mod": {{"某玩家": 10}}}}\n\n'
            f'不加标签也没关系，我会把你的回复当作 [REPLY] 处理。\n'
            f'请用中文回复，保持简短（1-3句话）。'
        )
        self._conn.send_text(setup)
        # 等待 AIRI 消化角色设定
        self._conn.wait_for_response(timeout=15)
        self._conn.drain_responses()
