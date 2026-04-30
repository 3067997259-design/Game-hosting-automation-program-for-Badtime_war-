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
        self._local_host_name: Optional[str] = None  # 本地房主名（由 handle_host_chat 设置）
        self._tui_chat_callback = None

    def set_tui_callback(self, callback):
        """设置 TUI 聊天回调"""
        self._tui_chat_callback = callback

    def _host_display(self, sender: str, content: str,
                      channel: str = "public", target: Optional[str] = None):
        """房主本地显示聊天消息（自动选择 TUI 或 print）"""
        if self._tui_chat_callback:
            self._tui_chat_callback(sender, content, channel, target)
        else:
            prefix = "[私聊]" if channel == "private" else "[公屏]"
            if channel == "private" and target:
                print(f"  {prefix} {sender} → {target}: {content}")
            else:
                print(f"  {prefix} {sender}: {content}")

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
                self._host_display(sender, content, "public")
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
                    self._host_display(sender, content, "private", target)
            # 回显给发送者
            self.server.send_to_sync(client_id, chat_msg)
            # AI 聊天在后台线程中执行
            threading.Thread(
                target=self._trigger_ai_chat,
                args=(sender, content),
                kwargs={"is_private": True, "target_name": target},
                daemon=True,
            ).start()

    def handle_host_chat(self, host_name: str, content: str,
                         channel: str = "public", target: Optional[str] = None):
        """房主发送聊天（房主没有 client_id，需要单独处理）"""
        self._local_host_name = host_name
        chat_msg = {
            "type": MessageType.CHAT_MESSAGE,
            "sender": host_name,
            "content": content,
            "channel": channel,
            "target": target,
        }

        if channel == "public":
            # 广播给所有远程客户端
            self.server.broadcast_sync(chat_msg)
            # 房主本地回显
            self._host_display(host_name, content, "public")
            # 触发 AI 聊天
            threading.Thread(
                target=self._trigger_ai_chat,
                args=(host_name, content),
                kwargs={"is_private": False},
                daemon=True,
            ).start()
        elif channel == "private" and target:
            # 发送给目标客户端
            target_client = self._find_client_by_name(target)
            if target_client:
                self.server.send_to_sync(target_client, chat_msg)
                self._host_display(host_name, content, "private", target)
            elif target in self._ai_chat_modules:
                self._host_display(host_name, content, "private", target)
            else:
                self._host_display(host_name, f"找不到玩家: {target}", "private")
            # 触发 AI 聊天
            threading.Thread(
                target=self._trigger_ai_chat,
                args=(host_name, content),
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
                            elif self._is_local_host(sender):
                                self._host_display(ai_name, reply, "private", sender)
                        else:
                            self.server.broadcast_sync(reply_msg)
                            # 房主本地显示 AI 公屏回复（broadcast 不会到达本地）
                            if self.lobby.host_plays or self._local_host_name:
                                self._host_display(ai_name, reply, "public")
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

    def _is_local_host(self, sender: str) -> bool:
        """判断 sender 是否为本地房主（参与游戏或观战均适用）"""
        if self._local_host_name and sender == self._local_host_name:
            return True
        return self.lobby.host_plays and self._is_host_name(sender)
