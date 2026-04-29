"""
AI 聊天模块
═══════════
绑定到 BasicAIController，接收聊天消息后通过 LLM 生成回复。
完全可选——没有配置 LLM 时 AI 不参与聊天。
"""

from typing import Optional, Any, List, Dict
from ai_chat.llm_backend import LLMBackend


# AI 性格 → 系统提示映射
PERSONALITY_PROMPTS = {
    "aggressive": "你是一个好斗的桌游玩家，喜欢挑衅对手，语气强势但不失幽默。",
    "defensive": "你是一个谨慎的桌游玩家，说话温和但暗含警告，总是在评估风险。",
    "political": "你是一个精于政治的桌游玩家，善于外交和谈判，经常尝试结盟。",
    "assassin": "你是一个神秘的桌游玩家，话不多但句句有深意，给人一种危险的感觉。",
    "builder": "你是一个注重发展的桌游玩家，喜欢讨论策略和资源管理。",
    "balanced": "你是一个老练的桌游玩家，根据局势灵活调整策略和态度。",
}


class AIChatModule:
    """AI 聊天模块：为一个 AI 玩家生成聊天回复。"""

    def __init__(
        self,
        player_name: str,
        personality: str,
        backend: LLMBackend,
        controller: Any = None,
    ):
        self.player_name = player_name
        self.personality = personality
        self.backend = backend
        self.controller = controller
        self._history: List[Dict[str, str]] = []

    def on_chat_received(
        self,
        sender: str,
        message: str,
        is_private: bool,
        game_state: Any = None,
    ) -> Optional[str]:
        if sender == self.player_name:
            return None

        system_prompt = self._build_system_prompt(game_state)

        self._history.append({
            "role": "user",
            "content": f"{'[私聊]' if is_private else '[公屏]'} {sender}: {message}",
        })

        # 保留最近 10 条历史
        if len(self._history) > 10:
            self._history = self._history[-10:]

        messages = [
            {"role": "system", "content": system_prompt},
            *self._history,
        ]

        reply = self.backend.chat(messages, temperature=0.8)
        if reply:
            self._history.append({"role": "assistant", "content": reply})
            return reply
        return None

    def _build_system_prompt(self, game_state: Any = None) -> str:
        base = PERSONALITY_PROMPTS.get(self.personality, PERSONALITY_PROMPTS["balanced"])
        parts = [
            f'你是"{self.player_name}"，一个回合制桌游《起闯战争》中的AI玩家。',
            base,
            "回复简短（1-2句话），符合角色性格。使用中文。",
        ]

        if game_state:
            parts.append(f"当前轮次: {game_state.current_round}")
            alive = [p.name for p in game_state.alive_players()]
            parts.append(f"存活玩家: {', '.join(alive)}")

        if self.controller and hasattr(self.controller, "_threat_scores"):
            threats = self.controller._threat_scores
            if threats:
                top_threats = sorted(threats.items(), key=lambda x: x[1], reverse=True)[:3]
                parts.append(
                    "威胁评估: " + ", ".join(f"{k}({v:.1f})" for k, v in top_threats)
                )

        return "\n".join(parts)
