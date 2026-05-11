"""
NetworkController —— 远程人类玩家控制器
═══════════════════════════════════════════
通过 NetworkServer 向对应客户端发送请求，阻塞等待响应。
模式参考 rl/env.py 中 _SyncRLController 的线程同步模式。
"""

from typing import List, Optional, Dict, Any, Set, Tuple
from controllers.base import PlayerController
from network.protocol import MessageType


def _build_action_restrictions(player: Any) -> Dict[str, Any]:
    """根据玩家天赋状态构造行动限制提示（供远程客户端/AIRI 解释）。

    与 engine/action_turn._get_available_actions 中的过滤规则一致。
    返回字段说明：
      - move_disabled: True 表示当前不能 move（如星野架盾）
      - interact_disabled: True 表示当前不能 interact（如星野架盾/持盾）
      - reason: 限制原因的简要文本（用于 AIRI 解释）
      - supernova_available: G1 火萤拥有「超新星过载」次数，下一次 move 可指定原地
      - tactical_macro_mode: G7 战术宏模式（远程接管暂不直接驱动宏，仅作信息提示）
    """
    out: Dict[str, Any] = {}
    talent = getattr(player, "talent", None)
    if talent is None:
        return out

    shield_mode = getattr(talent, "shield_mode", None)
    if shield_mode == "架盾":
        out["move_disabled"] = True
        out["interact_disabled"] = True
        out["reason"] = "星野架盾状态"
    elif shield_mode == "持盾":
        out["interact_disabled"] = True
        out["reason"] = "星野持盾状态"

    if getattr(talent, "has_supernova", False):
        out["supernova_available"] = True

    # G7 战术宏模式（参考 talents/g7/hoshino.py 中的形态/Cost 状态）。
    # 这里仅当玩家明显处于「铁之荷鲁斯」形态且持有 Cost 资源时提示 AIRI
    # 优先使用预制宏；不直接驱动宏执行。
    if hasattr(talent, "cost") and hasattr(talent, "form"):
        try:
            form = getattr(talent, "form", "")
            if isinstance(form, str) and "荷鲁斯" in form:
                out["tactical_macro_mode"] = True
        except Exception:
            pass

    return out


class NetworkController(PlayerController):
    """远程玩家：输入通过 TCP 网络往返。"""

    def __init__(self, client_id: str, server: Any):
        self.client_id = client_id
        self.server = server

    def get_command(
        self,
        player: Any,
        game_state: Any,
        available_actions: List[str],
        context: Optional[Dict] = None,
    ) -> str:
        # 注入天赋相关行动限制，便于远程客户端（包括 AIRI Bot）按状态做出正确决策。
        # 与 engine/action_turn._get_available_actions 中的过滤逻辑保持一致。
        merged_context: Dict[str, Any] = dict(context or {})
        restrictions = _build_action_restrictions(player)
        if restrictions:
            existing = merged_context.get("action_restrictions") or {}
            existing.update(restrictions)
            merged_context["action_restrictions"] = existing

        msg = {
            "type": MessageType.REQUEST_COMMAND,
            "player_name": player.name,
            "player_id": player.player_id,
            "hp": player.hp,
            "max_hp": player.max_hp,
            "location": player.location,
            "available_actions": available_actions,
            "context": merged_context,
        }
        self.server.send_to_sync(self.client_id, msg)
        resp = self.server.wait_for_sync(
            self.client_id, MessageType.COMMAND_RESPONSE
        )
        return resp.get("command", "forfeit")

    def choose(
        self,
        prompt: str,
        options: List[str],
        context: Optional[Dict] = None,
    ) -> str:
        msg = {
            "type": MessageType.REQUEST_CHOOSE,
            "prompt": prompt,
            "options": options,
            "context": context or {},
        }
        self.server.send_to_sync(self.client_id, msg)
        resp = self.server.wait_for_sync(
            self.client_id, MessageType.CHOOSE_RESPONSE
        )
        choice = resp.get("choice", "")
        if choice in options:
            return choice
        return options[0] if options else ""

    def choose_multi(
        self,
        prompt: str,
        options: List[str],
        max_count: int,
        min_count: int = 0,
        context: Optional[Dict] = None,
    ) -> List[str]:
        msg = {
            "type": MessageType.REQUEST_CHOOSE_MULTI,
            "prompt": prompt,
            "options": options,
            "max_count": max_count,
            "min_count": min_count,
            "context": context or {},
        }
        self.server.send_to_sync(self.client_id, msg)
        resp = self.server.wait_for_sync(
            self.client_id, MessageType.CHOOSE_MULTI_RESPONSE
        )
        choices = resp.get("choices", [])
        valid = [c for c in choices if c in options]
        if len(valid) < min_count:
            return options[:min_count]
        return valid[:max_count]

    def confirm(
        self,
        prompt: str,
        context: Optional[Dict] = None,
    ) -> bool:
        msg = {
            "type": MessageType.REQUEST_CONFIRM,
            "prompt": prompt,
            "context": context or {},
        }
        self.server.send_to_sync(self.client_id, msg)
        resp = self.server.wait_for_sync(
            self.client_id, MessageType.CONFIRM_RESPONSE
        )
        return bool(resp.get("result", False))
