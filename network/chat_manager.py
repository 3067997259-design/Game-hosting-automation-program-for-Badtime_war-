"""
ChatManager —— 聊天系统（服务端）
═════════════════════════════════
公屏聊天、私聊、AI 聊天集成。
聊天是异步的，不阻塞游戏流程。
"""

import threading
from typing import Any, Optional, Dict, List
from network.protocol import MessageType


class ChatManager:
    def __init__(self, server: Any, lobby: Any):
        self.server = server
        self.lobby = lobby
        self._ai_chat_modules: Dict[str, Any] = {}  # player_name → AIChatModule

    def register_ai_chatter(self, player_name: str, module: Any):
        self._ai_chat_modules[player_name] = module

    def handle_chat(self, client_id: str, msg: Dict[str, Any]):
        sender = msg.get("sender", "未知")
        content = msg.get("content", "")
        channel = msg.get("channel", "public")
        target = msg.get("target")

        chat_msg = {
            "type": MessageType.CHAT_MESSAGE,
            "sender": sender,
            "content": content,
            "channel": channel,
            "target": target,
        }

        if channel == "public":
            self.server.broadcast_sync(chat_msg)
            # 房主本地显示（房主不是网络客户端，broadcast 不会到达）
            if self.lobby.host_plays:
                print(f"  [公屏] {sender}: {content}")
            # AI 聊天在后台线程中执行，避免阻塞消息处理
            threading.Thread(
                target=self._trigger_ai_chat,
                args=(sender, content),
                kwargs={"is_private": False},
                daemon=True,
            ).start()
        elif channel == "private" and target:
            # 发送给目标
            target_client = self._find_client_by_name(target)
            if target_client:
                self.server.send_to_sync(target_client, chat_msg)
            else:
                # 目标可能是房主（无 client_id）
                if self.lobby.host_plays and self._is_host_name(target):
                    print(f"  [私聊] {sender} → {target}: {content}")
            # 回显给发送者
            self.server.send_to_sync(client_id, chat_msg)
            # AI 聊天在后台线程中执行
            threading.Thread(
                target=self._trigger_ai_chat,
                args=(sender, content),
                kwargs={"is_private": True, "target_name": target},
                daemon=True,
            ).start()

    def _trigger_ai_chat(
        self, sender: str, content: str,
        is_private: bool = False, target_name: Optional[str] = None,
    ):
        for ai_name, module in self._ai_chat_modules.items():
            should_respond = False
            if not is_private:
                should_respond = True
            elif target_name == ai_name:
                should_respond = True

            if should_respond:
                try:
                    game_state = self.lobby.game_state if self.lobby else None
                    reply = module.on_chat_received(
                        sender, content, is_private, game_state,
                    )
                    if reply:
                        reply_msg = {
                            "type": MessageType.CHAT_MESSAGE,
                            "sender": ai_name,
                            "content": reply,
                            "channel": "private" if is_private else "public",
                            "target": sender if is_private else None,
                        }
                        if is_private:
                            src_client = self._find_client_by_name(sender)
                            if src_client:
                                self.server.send_to_sync(src_client, reply_msg)
                        else:
                            self.server.broadcast_sync(reply_msg)
                            # 房主本地显示 AI 公屏回复
                            if self.lobby.host_plays:
                                print(f"  [公屏] {ai_name}: {reply}")
                except Exception:
                    pass

    def _find_client_by_name(self, player_name: str) -> Optional[str]:
        for slot in self.lobby.slots:
            if slot.player_name == player_name and slot.client_id:
                return slot.client_id
        return None

    def _is_host_name(self, player_name: str) -> bool:
        for slot in self.lobby.slots:
            if slot.slot_type.value == "human_local" and slot.player_name == player_name:
                return True
        return False
