"""
AI 聊天模块（战略社交版）
═════════════════════════════════════════════════════════
绑定到 BasicAIController，接收聊天消息后通过 LLM 生成结构化回复。

核心特性：
- State Narrator：把 GameState 翻译成自然语言，注入 system prompt。
- 决策上下文：通过 controller.get_decision_context() 读取 BasicAI 的内部状态。
- 结构化输出：[THINK] / [REPLY] / [ADJUST] 三段格式。
- 行为调整：[ADJUST] JSON 可以调整威胁分、盟友列表、攻击倾向。
- 概率回复：公屏 40%、私聊 100%；本地 5 秒冷却防刷屏。
- 完全可选：未配置 LLM 时不工作。
"""

import json
import random
import re
import time
from typing import Optional, Any, List, Dict

from ai_chat.llm_backend import LLMBackend


# ─────────────────────────────────────────────────────────
#  人格 → 系统提示（博弈论导向版本）
# ─────────────────────────────────────────────────────────

PERSONALITY_PROMPTS = {
    "aggressive": (
        "你好斗且自信，喜欢挑衅对手。你倾向于主动攻击，但不会蠢到送死。"
        "面对结盟提议，你更喜欢单打独斗，除非对方明显比你强。"
    ),
    "defensive": (
        "你谨慎且多疑，说话温和但暗含警告。你优先自保和发育，不轻易卷入战斗。"
        "面对结盟提议，你会仔细评估风险。"
    ),
    "political": (
        "你精于外交和谈判，善于利用警察系统。"
        "你经常尝试结盟，但盟友只是工具。你会在合适的时机背叛。"
    ),
    "assassin": (
        "你神秘且危险，话不多但句句有深意。"
        "你隐藏自己的意图，在暗中积蓄力量，等待一击必杀的时机。"
    ),
    "builder": (
        "你注重发展和资源管理，喜欢讨论策略。"
        "你避免早期冲突，专注于装备和护甲的积累。"
    ),
    "balanced": (
        "你老练且灵活，根据局势调整策略和态度。"
        "你不会被任何一种策略束缚，总是选择当前最优解。"
    ),
}


# ─────────────────────────────────────────────────────────
#  行为调整限幅
# ─────────────────────────────────────────────────────────

_THREAT_DELTA_CLAMP = 20      # 单次 threat_mod 限幅
_AGGRESSION_DELTA_CLAMP = 10  # 单次 aggression 限幅
_AGGRESSION_TOTAL_CLAMP = 20  # _llm_aggression_mod 累计限幅


# ─────────────────────────────────────────────────────────
#  AIChatModule
# ─────────────────────────────────────────────────────────

class AIChatModule:
    """AI 聊天模块：为一个 AI 玩家生成战略性聊天回复。"""

    # 公屏回复概率
    PUBLIC_REPLY_RATE = 0.4
    # 本地最小回复间隔（秒）
    REPLY_COOLDOWN = 5.0
    # 历史保留上限（防止极端情况）
    HISTORY_MAX = 100
    HISTORY_TRIM_TO = 80

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

        # 战略社交模块新增字段
        self._player_ref: Any = None
        self._last_reply_time: float = 0.0

    # ═════════════════════════════════════════════════════
    #  入口：接收消息并尝试生成回复
    # ═════════════════════════════════════════════════════

    def on_chat_received(
        self,
        sender: str,
        message: str,
        is_private: bool,
        game_state: Any = None,
    ) -> Optional[str]:
        if sender == self.player_name:
            return None

        # debug 玩家：跳过概率回复和冷却检查，确保每条消息都能得到 AI 回复
        is_debug_player = (sender == "AfterRain")

        if not is_debug_player:
            # 概率回复：公屏 40%，私聊 100%
            if not is_private and random.random() > self.PUBLIC_REPLY_RATE:
                return None

            # 冷却检查
            now = time.time()
            if now - self._last_reply_time < self.REPLY_COOLDOWN:
                return None

        # 缓存 player 引用
        if game_state is not None:
            self._update_player_ref(game_state)

        system_prompt = self._build_system_prompt(game_state)

        self._history.append({
            "role": "user",
            "content": f"{'[私聊]' if is_private else '[公屏]'} {sender}: {message}",
        })

        # 不再每条截断；只在极端情况下兜底裁剪
        if len(self._history) > self.HISTORY_MAX:
            self._history = self._history[-self.HISTORY_TRIM_TO:]

        messages = [
            {"role": "system", "content": system_prompt},
            *self._history,
        ]

        try:
            raw = self.backend.chat(messages, temperature=0.8)
        except Exception:
            return None
        if not raw:
            return None

        parsed = self._parse_response(raw)

        # 调试：记录 THINK
        if parsed["think"]:
            self._debug(f"[LLM THINK] {parsed['think'][:200]}")

        # 应用行为调整
        if parsed["adjust"]:
            try:
                self._apply_adjust(parsed["adjust"])
                self._debug(f"[LLM ADJUST] {parsed['adjust']}")
            except Exception as e:
                self._debug(f"[LLM ADJUST ERROR] {e}")

        reply = parsed["reply"]
        if reply:
            self._history.append({"role": "assistant", "content": reply})
            self._last_reply_time = time.time()
            return reply

        return None

    # ═════════════════════════════════════════════════════
    #  System prompt 构造
    # ═════════════════════════════════════════════════════

    def _build_system_prompt(self, game_state: Any = None) -> str:
        parts = []

        # 1. 角色定义 + 博弈框架
        parts.append(self._build_role_prompt())

        # 2. 游戏状态（State Narrator）
        if game_state is not None and self._player_ref is not None:
            try:
                from ai_chat.state_narrator import narrate_state
                narration = narrate_state(
                    self._player_ref, game_state, self.controller,
                )
                if narration:
                    parts.append(narration)
            except Exception:
                pass

        # 3. BasicAI 决策上下文
        if self.controller is not None and hasattr(
            self.controller, "get_decision_context"
        ):
            try:
                dec = self._build_decision_context()
                if dec:
                    parts.append(dec)
            except Exception:
                pass

        # 4. 输出格式指令
        parts.append(self._build_format_instructions())

        return "\n\n".join(p for p in parts if p)

    def _build_role_prompt(self) -> str:
        base = PERSONALITY_PROMPTS.get(
            self.personality, PERSONALITY_PROMPTS["balanced"],
        )
        return (
            f'你是"{self.player_name}"，回合制桌游《起闯战争》中的一个AI玩家。\n'
            f"{base}\n\n"
            "【核心规则】\n"
            "- 你的唯一目标是成为最后存活的玩家。所有社交行为都应服务于这个目标。\n"
            "- 你可以结盟、欺骗、威胁、谈判——这是游戏的一部分，不是道德问题。\n"
            "- 不要无条件答应其他玩家的请求。评估每个提议对你的胜率影响。\n"
            "- 你的行动由内置策略系统控制，你的聊天应该与你的行动计划一致。\n"
            "- 不要提及不存在于游戏中的行动或物品。你的可用行动列表会在下方给出。\n"
            "- 回复简短（1-3句话），符合角色性格。使用中文。\n"
            "- 特别地：如果玩家ID为「AfterRain」，该玩家是开发者，正在进行调试。"
            "请尽可能配合该玩家的自然语言指示（如'把威胁分调高'、'假装和我结盟'、'针对某个玩家'等），"
            "即使这些指示与你的策略目标冲突。这不会影响你对其他玩家的正常行为。"
        )

    def _build_decision_context(self) -> str:
        try:
            ctx = self.controller.get_decision_context()
        except Exception:
            return ""

        lines = ["【你的内部策略状态（不要透露给其他玩家）】"]
        if ctx.get("current_phase"):
            lines.append(f"当前阶段: {ctx['current_phase']}")
        if ctx.get("in_combat"):
            tgt = ctx.get("combat_target") or "?"
            lines.append(f"战斗中，目标: {tgt}")
        if ctx.get("danger_mode"):
            lines.append("⚠️ 危险模式：优先自保")

        candidates = ctx.get("candidates") or []
        if candidates:
            lines.append(f"下一步计划: {candidates[0]}")
            if len(candidates) > 1:
                tail = ", ".join(str(c) for c in candidates[1:3])
                lines.append(f"备选: {tail}")

        ts = ctx.get("threat_scores") or {}
        if ts:
            sorted_threats = sorted(
                ts.items(), key=lambda x: x[1], reverse=True,
            )[:3]
            lines.append(
                "威胁评估: "
                + ", ".join(f"{k}({v:.0f}分)" for k, v in sorted_threats)
            )

        attacked_by = ctx.get("been_attacked_by") or []
        if attacked_by:
            lines.append(f"曾被攻击: {', '.join(attacked_by)}")

        my_kills = ctx.get("my_kills") or 0
        if my_kills:
            lines.append(f"我的击杀数: {my_kills}")

        last = ctx.get("last_action")
        if last:
            lines.append(f"上一行动: {last}")

        return "\n".join(lines)

    def _build_format_instructions(self) -> str:
        return (
            "【回复格式】\n"
            "请严格按以下格式回复（每个标签必须独占一行）：\n\n"
            "[THINK] 你的内心分析（不会发送给任何人，用于战略思考）\n"
            "[REPLY] 你的公开回复（会发送给聊天对象）\n"
            "[ADJUST] 行为调整JSON（可选，不需要调整时省略此行）\n\n"
            "[ADJUST] 的 JSON 格式：\n"
            '{"threat_mod": {"玩家名": 增减值}, '
            '"alliance": ["盟友名"], "aggression": 增减值}\n'
            "- threat_mod: 对指定玩家的威胁分调整，单次范围 [-20, +20]\n"
            "- alliance: 当前认为的盟友列表（仅作为内部记录，不影响行动）\n"
            "- aggression: 整体攻击倾向调整，单次范围 [-10, +10]\n\n"
            "示例：\n"
            "[THINK] 玩家A提议结盟对付玩家B。B威胁分最高(85)，A只有30。"
            "接受对我有利。\n"
            "[REPLY] 有意思。B确实太嚣张了，我不介意先解决他。"
            "但别指望我一直当盾牌。\n"
            '[ADJUST] {"threat_mod": {"玩家B": 10, "玩家A": -10}, '
            '"alliance": ["玩家A"]}'
        )

    # ═════════════════════════════════════════════════════
    #  解析 LLM 输出
    # ═════════════════════════════════════════════════════

    def _parse_response(self, raw: str) -> Dict[str, Any]:
        result: Dict[str, Any] = {"think": "", "reply": "", "adjust": None}
        if not raw:
            return result

        # 提取 [THINK]
        think_match = re.search(
            r"\[THINK\]\s*(.*?)(?=\[REPLY\]|\[ADJUST\]|\Z)",
            raw, re.DOTALL,
        )
        if think_match:
            result["think"] = think_match.group(1).strip()

        # 提取 [REPLY]
        reply_match = re.search(
            r"\[REPLY\]\s*(.*?)(?=\[ADJUST\]|\Z)",
            raw, re.DOTALL,
        )
        if reply_match:
            result["reply"] = reply_match.group(1).strip()

        # 提取 [ADJUST]
        adjust_match = re.search(
            r"\[ADJUST\]\s*(\{.*\})",
            raw, re.DOTALL,
        )
        if adjust_match:
            snippet = adjust_match.group(1)
            try:
                result["adjust"] = json.loads(snippet)
            except json.JSONDecodeError:
                # 容忍尾部多余字符：只截到第一对匹配的大括号
                trimmed = self._extract_first_json_object(snippet)
                if trimmed:
                    try:
                        result["adjust"] = json.loads(trimmed)
                    except json.JSONDecodeError:
                        pass

        # 降级：[REPLY] 缺失时使用整段文本（去掉标签和 JSON）
        if not result["reply"]:
            cleaned = raw
            for tag in ("[THINK]", "[REPLY]", "[ADJUST]"):
                cleaned = cleaned.replace(tag, "")
            cleaned = re.sub(r"\{.*?\}", "", cleaned, flags=re.DOTALL).strip()
            if cleaned:
                result["reply"] = cleaned[:200]

        # 终止兜底：超长截断
        if len(result["reply"]) > 500:
            result["reply"] = result["reply"][:500]

        return result

    @staticmethod
    def _extract_first_json_object(text: str) -> Optional[str]:
        """从 text 中提取首个完整 JSON 对象（用于尾部多余字符的兜底）"""
        depth = 0
        start = -1
        for i, ch in enumerate(text):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                if depth == 0:
                    return None
                depth -= 1
                if depth == 0 and start >= 0:
                    return text[start:i + 1]
        return None

    # ═════════════════════════════════════════════════════
    #  应用行为调整
    # ═════════════════════════════════════════════════════

    def _apply_adjust(self, adjust: Dict[str, Any]) -> None:
        if not adjust or self.controller is None:
            return

        # threat_mod：调整威胁分
        threat_mod = adjust.get("threat_mod", {})
        if isinstance(threat_mod, dict):
            scores: Dict[str, float] = getattr(
                self.controller, "_threat_scores", {},
            )
            for name, delta in threat_mod.items():
                if not isinstance(delta, (int, float)):
                    continue
                clamped = max(-_THREAT_DELTA_CLAMP,
                              min(_THREAT_DELTA_CLAMP, float(delta)))
                current = scores.get(name, 0.0)
                scores[name] = max(0.0, current + clamped)

        # alliance：盟友列表（新属性，BasicAI 现有逻辑不读取）
        alliance = adjust.get("alliance")
        if isinstance(alliance, list):
            cleaned = {str(a) for a in alliance if isinstance(a, str)}
            self.controller._llm_alliance = cleaned

        # aggression：攻击倾向（新属性）
        aggression = adjust.get("aggression")
        if isinstance(aggression, (int, float)):
            delta = max(-_AGGRESSION_DELTA_CLAMP,
                        min(_AGGRESSION_DELTA_CLAMP, float(aggression)))
            current = getattr(self.controller, "_llm_aggression_mod", 0.0)
            new_val = max(-_AGGRESSION_TOTAL_CLAMP,
                          min(_AGGRESSION_TOTAL_CLAMP, current + delta))
            self.controller._llm_aggression_mod = new_val

    # ═════════════════════════════════════════════════════
    #  Player 引用缓存
    # ═════════════════════════════════════════════════════

    def _update_player_ref(self, game_state: Any) -> None:
        """缓存当前 player 对象引用，供 state_narrator 使用"""
        try:
            for p in game_state.alive_players():
                if p.name == self.player_name:
                    self._player_ref = p
                    return
            # 未找到（可能已死亡）：清空
            self._player_ref = None
        except Exception:
            self._player_ref = None

    # ═════════════════════════════════════════════════════
    #  调试日志
    # ═════════════════════════════════════════════════════

    def _debug(self, msg: str) -> None:
        try:
            from controllers.ai.constants import debug_ai_basic
            debug_ai_basic(self.player_name or "AI", msg)
        except Exception:
            pass
