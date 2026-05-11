"""
AiriController —— AIRI 作为独立玩家的本地控制器
═══════════════════════════════════════════════════
与 bot_bridge.py 的差异：bot_bridge 是「外部进程通过 TCP + AIRI 桥接」
模式（玩家以远程客户端身份加入房间）；AiriController 则是「本地进程
内直接挂载」模式，作为 PlayerController 子类放入 lobby.controllers 中。

设计原则：
- 大量复用 bot_bridge 中已经完成的「指令意图说明 / 天赋意图字典 /
  战术宏意图字典 / 响应解析器」，避免重复实现导致两个分支偏离。
- 失败时使用与 bot_bridge 一致的智能 fallback。
- 不允许 AIRI 自主组合 G7 战术宏：当判定为战术宏模式时，明确提示
  AIRI「从预制宏中选」。
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any, Dict, List, Optional

from controllers.base import PlayerController
from ai_chat.airi_connection import AiriConnection
import bot_bridge as bb
from controllers.network_controller import _build_action_restrictions

log = logging.getLogger("airi_controller")

_DEFAULT_WS_URL = "ws://localhost:6121/ws"
_DEFAULT_TIMEOUT = 60.0


class AiriController(PlayerController):
    """AIRI 作为本地独立玩家的 PlayerController 实现。

    - 通过 AiriConnection 与 AIRI WebSocket 服务器通信。
    - 每次需要决策时构造 prompt → send_text → wait_for_response → 解析。
    - 复用 bot_bridge 的 CommandIntentExplainer / ResponseParser /
      INTENT 字典系列，保证两条路径的解释完全一致。
    """

    # 多个 AiriController 实例可共享一条 WebSocket 连接；以 ws_url 作 key
    _shared_connections: Dict[str, AiriConnection] = {}
    _shared_lock = threading.Lock()

    def __init__(
        self,
        player_id: str,
        player_name: str,
        airi_config: Optional[Dict[str, Any]] = None,
    ):
        self.player_id = player_id
        self.player_name = player_name
        self.airi_config = dict(airi_config or {})

        self.ws_url: str = self.airi_config.get("ws_url", _DEFAULT_WS_URL)
        self.module_id: str = self.airi_config.get(
            "module_id", f"airi-bot-{player_id}"
        )
        self.auth_token: str = self.airi_config.get("auth_token", "")
        self.action_timeout: float = float(
            self.airi_config.get("action_timeout", _DEFAULT_TIMEOUT)
        )
        self.choose_timeout: float = float(
            self.airi_config.get("choose_timeout", self.action_timeout)
        )
        self.confirm_timeout: float = float(
            self.airi_config.get("confirm_timeout", self.action_timeout)
        )

        self._lock = threading.Lock()
        self._conn: Optional[AiriConnection] = None
        self._ensure_connected()

    # ──────────────────────────────────────────
    #  连接生命周期
    # ──────────────────────────────────────────

    def _ensure_connected(self) -> None:
        """惰性建立 AIRI WebSocket 连接（同一 ws_url 共享一条物理连接）。"""
        with AiriController._shared_lock:
            conn = AiriController._shared_connections.get(self.ws_url)
            if conn is None or not getattr(conn, "is_connected", False):
                conn = AiriConnection(
                    ws_url=self.ws_url,
                    module_id=self.module_id,
                    auth_token=self.auth_token,
                )
                try:
                    conn.connect()
                except Exception as e:
                    log.error(
                        f"[AiriController] 连接 AIRI 失败 ws_url={self.ws_url}: {e}"
                    )
                    self._conn = None
                    return
                AiriController._shared_connections[self.ws_url] = conn
            self._conn = conn

    def _disconnected_fallback(
        self,
        kind: str,
        actions_or_options: Any = None,
    ) -> Any:
        """连接不可用时，统一退回稳妥默认值。"""
        log.warning(f"[AiriController] AIRI 不可用，{kind} 走默认 fallback")
        if kind == "command":
            return "forfeit"
        if kind == "choose":
            opts = actions_or_options or []
            return opts[0] if opts else ""
        if kind == "choose_multi":
            return []
        if kind == "confirm":
            return False
        return None

    # ──────────────────────────────────────────
    #  PlayerController 接口实现
    # ──────────────────────────────────────────

    def get_command(
        self,
        player: Any,
        game_state: Any,
        available_actions: List[str],
        context: Optional[Dict] = None,
    ) -> str:
        if self._conn is None or not self._conn.is_connected:
            return self._disconnected_fallback("command")

        merged_context: Dict[str, Any] = dict(context or {})
        # 注入天赋相关行动限制，与 NetworkController 完全对齐。
        restrictions = _build_action_restrictions(player)
        if restrictions:
            existing = merged_context.get("action_restrictions") or {}
            existing.update(restrictions)
            merged_context["action_restrictions"] = existing

        msg = {
            "player_name": getattr(player, "name", self.player_name),
            "player_id": getattr(player, "player_id", self.player_id),
            "hp": getattr(player, "hp", None),
            "max_hp": getattr(player, "max_hp", None),
            "location": getattr(player, "location", ""),
            "available_actions": available_actions,
            "context": merged_context,
        }

        with self._lock:
            for attempt in range(3):
                prompt = self._build_command_prompt(
                    msg, available_actions, merged_context, attempt
                )
                self._conn.drain_responses()
                self._conn.send_text(prompt)
                reply = self._conn.wait_for_response(timeout=self.action_timeout)
                if not reply:
                    log.warning(
                        f"[AiriController] get_command 第 {attempt+1} 次超时"
                    )
                    continue
                parsed = bb.ResponseParser.extract_action(
                    reply, available_actions
                )
                if parsed:
                    log.info(f"[AiriController] 解析到行动: {parsed}")
                    return parsed
                log.warning(
                    f"[AiriController] 第 {attempt+1} 次无法解析行动"
                )
            return self._smart_fallback_command(available_actions, merged_context)

    def choose(
        self,
        prompt: str,
        options: List[str],
        context: Optional[Dict] = None,
    ) -> str:
        if not options:
            return ""
        if self._conn is None or not self._conn.is_connected:
            return self._disconnected_fallback("choose", options)

        ctx: Dict[str, Any] = dict(context or {})
        situation = ctx.get("situation", "")

        intent_block = self._build_choose_intent_block(situation, options, ctx)

        with self._lock:
            for attempt in range(2):
                options_block = "\n".join(
                    f"  {i}. {opt}" for i, opt in enumerate(options, 1)
                )
                if attempt == 0:
                    parts = [prompt]
                    if intent_block:
                        parts.extend(["", intent_block])
                    parts.extend([
                        "",
                        "【可选项】",
                        options_block,
                        "",
                        "请用 CHOOSE: <编号> 的格式回复（编号从 1 开始）。",
                    ])
                    text = "\n".join(parts)
                else:
                    text = (
                        "⚠️ 上一次回复无法解析为选项编号。请只回复一行：\n"
                        f"CHOOSE: <1~{len(options)}>\n\n"
                        f"{prompt}\n"
                        f"{options_block}"
                    )

                self._conn.drain_responses()
                self._conn.send_text(text)
                reply = self._conn.wait_for_response(timeout=self.choose_timeout)
                if not reply:
                    log.warning(f"[AiriController] choose 第 {attempt+1} 次超时")
                    continue
                parsed = bb.ResponseParser.extract_choice(reply, options)
                if parsed:
                    return parsed
                log.warning(
                    f"[AiriController] 第 {attempt+1} 次无法解析选择"
                )
        return self._smart_fallback_choice(situation, options)

    def choose_multi(
        self,
        prompt: str,
        options: List[str],
        max_count: int,
        min_count: int = 0,
        context: Optional[Dict] = None,
    ) -> List[str]:
        if not options:
            return []
        if self._conn is None or not self._conn.is_connected:
            return self._disconnected_fallback("choose_multi")

        text = f"{prompt}（选 {min_count}~{max_count} 个）\n"
        for i, opt in enumerate(options, 1):
            text += f"  {i}. {opt}\n"
        text += "请用 CHOOSE: <编号1>,<编号2> 的格式回复（逗号分隔）。"

        with self._lock:
            self._conn.drain_responses()
            self._conn.send_text(text)
            reply = self._conn.wait_for_response(timeout=self.choose_timeout)

        selected: List[str] = []
        if reply:
            for n in re.findall(r"\d+", reply):
                try:
                    idx = int(n) - 1
                except ValueError:
                    continue
                if 0 <= idx < len(options) and options[idx] not in selected:
                    selected.append(options[idx])
                    if len(selected) >= max_count:
                        break
        if len(selected) < min_count:
            selected = list(options[:min_count])
        return selected[:max_count]

    def confirm(
        self,
        prompt: str,
        context: Optional[Dict] = None,
    ) -> bool:
        if self._conn is None or not self._conn.is_connected:
            return self._disconnected_fallback("confirm")

        text = (
            f"{prompt}\n\n"
            "请回复「是」或「否」。"
        )

        with self._lock:
            self._conn.drain_responses()
            self._conn.send_text(text)
            reply = self._conn.wait_for_response(timeout=self.confirm_timeout)

        if reply:
            parsed = bb.ResponseParser.extract_choice(reply, ["是", "否"])
            if parsed == "是":
                return True
            if parsed == "否":
                return False
            # 兜底：英文 yes/no
            if re.search(r"\byes\b|\bok\b|\bsure\b", reply, re.IGNORECASE):
                return True
            if re.search(r"\bno\b|\bcancel\b", reply, re.IGNORECASE):
                return False
        return False

    # ──────────────────────────────────────────
    #  prompt 构造（复用 bot_bridge 的逻辑）
    # ──────────────────────────────────────────

    def _build_command_prompt(
        self,
        msg: Dict[str, Any],
        actions: List[Any],
        context: Dict[str, Any],
        attempt: int,
    ) -> str:
        """复用 bot_bridge.BotBridge._build_command_prompt 的实现。

        BotBridge 实例的该方法没有用到 self.airi/self.game_client（它只
        负责构造字符串），因此可以直接把当前 controller 作为伪 self 传入。
        但为减少耦合，这里复制粘贴 bot_bridge 中的核心逻辑会带来漂移风险；
        因此我们改为通过一个临时 BotBridge 桩对象间接调用。
        """
        # 临时构造一个 BotBridge 实例只用于 prompt 拼接是过重的；改为
        # 直接复刻关键拼接逻辑，但所有意图字典 / 解释器都从 bot_bridge 引用。
        action_lines: List[str] = []
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
        intent_block = bb.CommandIntentExplainer.build_intent_block(actions)

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
            header = "⚠️ 上一次回复无法解析。请严格遵守格式要求重新决策。"
            tail = (
                "必须回复一行以 ACTION: 开头的指令，紧跟一个合法行动前缀。\n"
                "合法前缀只能是：move / attack / interact / lock / find / "
                "forfeit / wake / report / assemble / track / recruit / "
                "election / designate / study / special / split / police。\n"
                "示例：ACTION: forfeit"
            )
        else:
            header = (
                "⚠️ 仍然无法识别你的行动。请只输出一行内容，不要解释。"
            )
            tail = (
                "只回复一行，例如：ACTION: forfeit\n"
                "或：ACTION: move <地点>\n"
                "否则系统会自动为你选一个稳妥的默认行动。"
            )

        parts = [
            header, "", "【当前状况】", situation, "", "【可选行动】", actions_text,
        ]
        if intent_block:
            parts.extend(["", "【指令战略意图】", intent_block])

        restrictions = (context or {}).get("action_restrictions") or {}
        restriction_lines: List[str] = []
        if restrictions.get("move_disabled"):
            restriction_lines.append(
                "- 你目前不能 move（"
                f"{restrictions.get('reason', '当前天赋状态限制')}）。"
            )
        if restrictions.get("interact_disabled"):
            restriction_lines.append(
                "- 你目前不能 interact（"
                f"{restrictions.get('reason', '当前天赋状态限制')}）。"
            )
        if restrictions.get("supernova_available"):
            restriction_lines.append(
                "- 你拥有 G1 火萤的『超新星过载』：下一次 move 可指定目的地"
                "为当前地点（原地触发），对当地所有单位造成 1 点无视克制伤害"
                "并施加灼烧；触发后失熵症 debuff 后延 3 轮。"
            )
        if restrictions.get("tactical_macro_mode"):
            restriction_lines.append(
                "- 你目前处于 G7 战术宏模式：请从 BasicAI 预制宏中选择，"
                "不要自主组合宏序列。预制宏包括：基础攻击宏 / 反队长接近宏 / "
                "反队长无盾宏 / 补刀+转火宏 / 全力射击宏。"
            )
        if restriction_lines:
            parts.extend(["", "【行动限制】"] + restriction_lines)

        parts.extend(["", tail])
        return "\n".join(parts)

    def _build_choose_intent_block(
        self,
        situation: str,
        options: List[str],
        context: Dict[str, Any],
    ) -> str:
        """复用 bot_bridge 中的 choose 意图构造逻辑。"""
        lines: List[str] = []

        if situation == "talent_t0":
            talent_name = (context or {}).get("talent_name", "") or ""
            talent_desc = (context or {}).get("talent_desc", "") or ""
            info = bb.TALENT_T0_INTENT_MAP.get(talent_name, {})
            lines.append(f"【天赋 T0 决策】当前天赋：{talent_name or '未知'}")
            if talent_desc:
                lines.append(f"  原始描述：{talent_desc}")
            if info:
                lines.append(f"  战略意图：{info.get('intent', '')}")
                expl = info.get("explanation", "")
                if expl:
                    lines.append(f"  解释：{expl}")
                trig = info.get("trigger_condition", "")
                if trig:
                    lines.append(f"  推荐触发条件：{trig}")
                val = info.get("strategic_value", "")
                if val:
                    lines.append(f"  战略价值：{val}")
            else:
                lines.append(
                    "  （没有内置该天赋的意图说明，请严格依据原始描述与当前局势判断。）"
                )
            lines.append("一般来说，选项形如 [发动天赋, 不发动，正常行动]。")
            return "\n".join(lines)

        sub_info = bb.TALENT_SUB_DECISION_INTENT.get(situation)
        if sub_info:
            lines.append(f"【子决策意图】situation = {situation}")
            lines.append(f"  战略意图：{sub_info.get('intent', '')}")
            expl = sub_info.get("explanation", "")
            if expl:
                lines.append(f"  解释：{expl}")
            sug = sub_info.get("suggestion", "")
            if sug:
                lines.append(f"  参考建议：{sug}")
            return "\n".join(lines)

        block = bb.CommandIntentExplainer.build_intent_block(options)
        if block:
            return f"【选项战略意图】\n{block}"
        return ""

    # ──────────────────────────────────────────
    #  Fallback 策略（与 bot_bridge 对齐）
    # ──────────────────────────────────────────

    @staticmethod
    def _smart_fallback_command(
        actions: List[Any],
        context: Dict[str, Any],
    ) -> str:
        """与 bot_bridge 一致的智能 fallback。"""
        if not actions:
            return "forfeit"
        # 字符串前缀集合
        prefixes = {
            (a.split()[0] if isinstance(a, str) and a else (
                a.get("usage", "").split()[0] if isinstance(a, dict) else ""
            ))
            for a in actions
        }
        if "forfeit" in prefixes:
            return "forfeit"
        # 最后兜底取第一个 action 的字符串形式
        first = actions[0]
        if isinstance(first, dict):
            usage = first.get("usage", "")
            return usage.split()[0] if usage else "forfeit"
        return str(first).split()[0]

    @staticmethod
    def _smart_fallback_choice(situation: str, options: List[str]) -> str:
        if not options:
            return ""
        if situation == "talent_t0":
            for opt in options:
                if isinstance(opt, str) and ("不发动" in opt or opt == "否"):
                    return opt
        return options[0]
