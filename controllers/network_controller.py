"""
NetworkController —— 远程人类玩家控制器
═══════════════════════════════════════════
通过 NetworkServer 向对应客户端发送请求，阻塞等待响应。
模式参考 rl/env.py 中 _SyncRLController 的线程同步模式。
"""

from typing import List, Optional, Dict, Any
from controllers.base import PlayerController
from network.protocol import MessageType


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
        msg = {
            "type": MessageType.REQUEST_COMMAND,
            "player_name": player.name,
            "player_id": player.player_id,
            "hp": player.hp,
            "max_hp": player.max_hp,
            "location": player.location,
            "available_actions": available_actions,
            "context": context or {},
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
