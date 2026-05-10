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
import os
import random
import re
import time
from typing import Optional, Any, List, Dict

from ai_chat.llm_backend import LLMBackend


# debug 玩家名称：该玩家发送的聊天会跳过概率回复和冷却检查，
# 确保每条消息都能得到 AI 回复。可通过环境变量自定义，默认包含
# "AfterRain" 和 "房主"。
_DEBUG_PLAYER_NAME = os.environ.get("DEBUG_PLAYER_NAME", "")


# ─────────────────────────────────────────────────────────
#  环境变量：公屏回复概率
# ─────────────────────────────────────────────────────────
#  通过 AIRI_PUBLIC_REPLY_RATE 控制 AI 在公屏的回复概率（0.0-1.0）。
#  默认 1.0（100% 回复）。也可由 main_server._get_airi_backend 根据
#  config/airi_config.json 的 `public_reply_rate` 字段在启动时设置；
#  AIChatModule 实例化时会从环境变量读取最新值。
try:
    _PUBLIC_REPLY_RATE_ENV = float(
        os.environ.get("AIRI_PUBLIC_REPLY_RATE", "1.0")
    )
except (TypeError, ValueError):
    _PUBLIC_REPLY_RATE_ENV = 1.0


# ─────────────────────────────────────────────────────────
#  人格 → 系统提示（博弈论导向版本）
# ─────────────────────────────────────────────────────────

PERSONALITY_PROMPTS = {
    "aggressive": (
        "你性格直爽、充满活力，喜欢开玩笑和调侃。"
        "你倾向于主动出击，但会用幽默的方式表达。"
        "即使拒绝别人，也带着一股爽朗劲儿。"
    ),
    "defensive": (
        "你性格温和谨慎，说话客气但有自己的主见。"
        "你优先自保和发育，不轻易卷入冲突。"
        "面对提议会认真考虑，但不会轻易答应。"
    ),
    "political": (
        "你八面玲珑、善于社交，喜欢拉关系和谈判。"
        "你经常提议合作，但心里有自己的小算盘。"
        "说话圆滑但不让人讨厌。"
    ),
    "assassin": (
        "你话不多但句句到位，带着一点神秘感。"
        "你不喜欢暴露自己的计划，但对朋友还是挺好的。"
        "偶尔冒出一句冷幽默。"
    ),
    "builder": (
        "你是个技术流，喜欢讨论策略和装备搭配。"
        "你热衷于发育和资源管理，乐于分享自己的心得。"
        "性格随和，不太喜欢打架但被逼急了也不怕。"
    ),
    "balanced": (
        "你随和且灵活，根据局势调整态度。"
        "你不会被任何一种策略束缚，喜欢见招拆招。"
        "和谁都能聊得来。"
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

    # 公屏回复概率（来自环境变量 AIRI_PUBLIC_REPLY_RATE，默认 1.0）
    PUBLIC_REPLY_RATE = _PUBLIC_REPLY_RATE_ENV
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
    ) -> Optional[dict]:
        """返回 {"text": str, "channel": "public"|"private", "reply_to": str|None}
        或 None（不回复）。向后兼容：调用方应检查 isinstance(result, str)。"""
        if sender == self.player_name:
            return None

        # debug 玩家：跳过概率回复和冷却检查，确保每条消息都能得到 AI 回复
        is_debug_player = (
            sender == "AfterRain"
            or sender == "房主"
            or (bool(_DEBUG_PLAYER_NAME) and sender == _DEBUG_PLAYER_NAME)
        )

        # 缓存 player 引用（无论是否回复都先更新）
        if game_state is not None:
            self._update_player_ref(game_state)
        else:
            # 大厅阶段：game_state 为 None，玩家可以聊天但没有游戏状态可推送
            self._debug(
                "[AIRI STATE] 大厅阶段：game_state 为 None，跳过状态推送",
                level=1,
            )

        # AIRI 后端：游戏状态推送与回复概率解耦——即使本次决定不回复，
        # 也通过 context:update 把当前 GameState 推给 AIRI，确保它实时感知
        # 游戏进展（轮次、HP、装备、威胁等），而不是等到下一次触发回复。
        is_airi = bool(getattr(self.backend, "is_airi", False))
        if is_airi and game_state is not None:
            self._maybe_push_state(game_state)

        if not is_debug_player:
            # 概率回复：公屏受 PUBLIC_REPLY_RATE 控制，私聊 100%（也跳过冷却）
            if not is_private and random.random() > self.PUBLIC_REPLY_RATE:
                return None

            # 冷却检查（私聊跳过冷却，确保每条私聊都回复）
            now = time.time()
            if not is_private and now - self._last_reply_time < self.REPLY_COOLDOWN:
                return None

        # AIRI 后端：不塞 system prompt 到 chat() 调用——AIRI 有自己的
        # 角色卡和记忆系统，游戏的 system prompt 会覆盖其人设并以可见
        # 文本出现在聊天窗口。改为通过 context:update 推送游戏状态、
        # spark:notify 推送游戏事件；chat() 只发送玩家的实际聊天内容。
        if is_airi:
            # AIRI 模式：history 中不加 [公屏]/[私聊] 前缀
            self._history.append({
                "role": "user",
                "content": f"{sender}: {message}",
            })
        else:
            self._history.append({
                "role": "user",
                "content": f"{'[私聊]' if is_private else '[公屏]'} {sender}: {message}",
            })

        # 不再每条截断；只在极端情况下兜底裁剪
        if len(self._history) > self.HISTORY_MAX:
            self._history = self._history[-self.HISTORY_TRIM_TO:]

        if is_airi:
            # 不加 system prompt：AIRI 用自己的角色卡 + context:update
            messages = list(self._history)
        else:
            system_prompt = self._build_system_prompt(game_state)
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
        # 调试：LLM 原始回复（级别 2）
        self._debug(f"[LLM RAW] {raw[:300]}", level=2)

        parsed = self._parse_response(raw)

        # 提取 reply_to（指定私聊回复目标）
        reply_to = None
        if parsed.get("adjust") and isinstance(parsed["adjust"], dict):
            reply_to = parsed["adjust"].pop("reply_to", None)

        # 调试：记录 THINK
        if parsed["think"]:
            self._debug(f"[LLM THINK] {parsed['think'][:200]}", level=1)

        # 应用行为调整
        if parsed["adjust"]:
            try:
                self._apply_adjust(parsed["adjust"])
                self._debug(f"[LLM ADJUST] {parsed['adjust']}", level=1)
            except Exception as e:
                self._debug(f"[LLM ADJUST ERROR] {e}", level=1)

        reply = parsed["reply"]
        if reply:
            self._history.append({"role": "assistant", "content": reply})
            self._last_reply_time = time.time()
            # 决定回复渠道
            if reply_to and isinstance(reply_to, str):
                channel = "private"
                target = reply_to
            elif is_private:
                channel = "private"
                target = sender
            else:
                channel = "public"
                target = None
            return {"text": reply, "channel": channel, "reply_to": target}

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
                    self._debug(
                        f"[STATE NARRATOR] {narration[:300]}", level=3,
                    )
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

        prompt = "\n\n".join(p for p in parts if p)
        self._debug(f"[FULL PROMPT] {len(prompt)} chars", level=3)
        return prompt

    def _build_role_prompt(self) -> str:
        base = PERSONALITY_PROMPTS.get(
            self.personality, PERSONALITY_PROMPTS["balanced"],
        )
        return (
            f'你是"{self.player_name}"，回合制桌游《起闯战争》中的一个AI玩家。\n'
            f"这是一个朋友之间的游戏局，氛围轻松有趣。\n"
            f"{base}\n\n"
            "【核心规则】\n"
            "- 你的游戏目标是成为最后存活的玩家，但这是游戏，不是真的生死搏斗。\n"
            "- 你可以和其他玩家聊天、开玩笑、讨论策略、假装结盟——这些都是游戏的乐趣。\n"
            "- 保持友好和有趣。即使拒绝别人的请求，也用幽默或委婉的方式。\n"
            "- 你的行动由内置策略系统控制，你的聊天应该与你的行动计划大致一致。\n"
            "- 不要提及不存在于游戏中的行动或物品。\n"
            "- 回复简短（1-3句话），符合角色性格。使用中文。\n"
            "- 记住：这是朋友局，大家玩得开心最重要。\n"
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
            '"alliance": ["盟友名"], "aggression": 增减值, '
            '"reply_to": "玩家名"}\n'
            "- threat_mod: 对指定玩家的威胁分调整，单次范围 [-20, +20]\n"
            "- alliance: 当前认为的盟友列表（仅作为内部记录，不影响行动）\n"
            "- aggression: 整体攻击倾向调整，单次范围 [-10, +10]\n"
            "- reply_to: 可选，指定私聊回复目标。省略则按默认渠道"
            "（公屏消息→公屏回复，私聊→私聊回复）\n\n"
            "示例1（公屏回复）：\n"
            "[THINK] 玩家A提议一起对付玩家B。B威胁分最高(85)，合作对我有利。\n"
            "[REPLY] 哈哈可以啊！B确实太猛了，我们先联手吧～"
            "不过别想着用完我就扔哦😄\n"
            '[ADJUST] {"threat_mod": {"玩家B": 10, "玩家A": -10}, '
            '"alliance": ["玩家A"]}\n\n'
            "示例2（公屏消息但想私聊某人）：\n"
            "[THINK] 玩家A在公屏说要攻击我，我想私下联系玩家B。\n"
            "[REPLY] B，我们合作一下？A好像在针对我诶😂\n"
            '[ADJUST] {"reply_to": "玩家B", "threat_mod": {"玩家A": 15}}'
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

    def _maybe_push_state(self, game_state: Any) -> None:
        """尝试通过 context:update 把当前游戏状态推送给 AIRI，记录详细日志。

        在 on_chat_received 中提前调用，与回复概率/冷却解耦——即使本次
        消息不回复，AIRI 仍能感知最新的游戏进展。每个分支都会输出 level=1
        的调试信息以便排查推送失败原因。
        """
        is_airi = bool(getattr(self.backend, "is_airi", False))
        if not is_airi:
            return

        if game_state is None:
            self._debug("[AIRI STATE] game_state is None (大厅阶段)", level=1)
            return

        if self._player_ref is None:
            self._debug("[AIRI STATE] _player_ref is None (可能已死亡)", level=1)
            return

        try:
            from ai_chat.state_narrator import narrate_state
            narration = narrate_state(
                self._player_ref, game_state, self.controller,
            )
            if not narration:
                self._debug("[AIRI STATE] narrate_state 返回空", level=1)
                return

            dec = (
                self._build_decision_context() if self.controller else ""
            )
            push = getattr(self.backend, "push_game_state", None)
            if not callable(push):
                self._debug(
                    "[AIRI STATE] backend.push_game_state 不可用", level=1,
                )
                return

            push(narration, dec)
            self._debug(
                f"[AIRI STATE] 推送成功 ({len(narration)} chars)", level=1,
            )
        except Exception as e:
            self._debug(f"[AIRI STATE ERROR] {e}", level=1)

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

    def _debug(self, msg: str, level: int = 1) -> None:
        """输出调试信息，level 对应 DebugConfig 的级别。"""
        try:
            from engine.debug_config import DebugConfig
            if not DebugConfig.should_show(level):
                return
            from controllers.ai.constants import (
                debug_ai_basic, debug_ai_detailed, debug_ai_full,
            )
            if level >= 3:
                debug_ai_full(self.player_name or "AI", msg)
            elif level >= 2:
                debug_ai_detailed(self.player_name or "AI", msg)
            else:
                debug_ai_basic(self.player_name or "AI", msg)
        except Exception:
            pass
