"""
ChatManager —— 聊天系统（服务端）
═════════════════════════════════
公屏聊天、私聊、AI 聊天集成。
聊天是异步的，不阻塞游戏流程。
"""

import random
import threading
from typing import Any, Optional, Dict, List
from network.protocol import MessageType
from cli.async_output import async_print

# 公屏消息最多允许多少个 AI 回复（防止刷屏）
_MAX_PUBLIC_AI_REPLIES = 2


class ChatManager:
    def __init__(self, server: Any, lobby: Any):
        self.server = server
        self.lobby = lobby
        self._ai_chat_modules: Dict[str, Any] = {}  # player_name → AIChatModule
        self._local_host_name: Optional[str] = None  # 本地房主名（由 handle_host_chat 设置）
        self._tui_chat_callback = None
        self._tui_chat_callback_typing = None

    def set_tui_callback(self, callback):
        """设置 TUI 聊天回调"""
        self._tui_chat_callback = callback

    def set_tui_typing_callback(self, callback):
        """设置 TUI typing indicator 回调"""
        self._tui_chat_callback_typing = callback

    def _host_display(self, sender: str, content: str,
                      channel: str = "public", target: Optional[str] = None):
        """房主本地显示聊天消息（自动选择 TUI 或 async_print）"""
        if self._tui_chat_callback:
            self._tui_chat_callback(sender, content, channel, target)
        else:
            prefix = "[私聊]" if channel == "private" else "[公屏]"
            if channel == "private" and target:
                async_print(f"  {prefix} {sender} → {target}: {content}")
            else:
                async_print(f"  {prefix} {sender}: {content}")

    def _host_typing(self, player_name: str, is_typing: bool):
        """房主本地显示 typing indicator"""
        if self._tui_chat_callback_typing:
            self._tui_chat_callback_typing(player_name, is_typing)
        elif is_typing:
            async_print(f"  💭 {player_name} 正在回复中...")

    def register_ai_chatter(self, player_name: str, module: Any):
        self._ai_chat_modules[player_name] = module

    def notify_game_event(self, event_text: str):
        """向所有使用 AIRI 后端的 AI 推送游戏事件通知。

        AIRI 是有状态的对话 AI——游戏事件（轮次开始、玩家死亡、战斗结果等）
        通过 spark:notify 推送给它，让它实时感知进展。普通 LLM 后端是无状态的
        无需推送（事件信息会随下一次 system prompt 一起带入）。

        多个 AI 槽位可能共享同一个 AIRI 后端实例（_setup_ai_chat 复用 backend），
        此处按 id() 去重，避免对同一 WebSocket 连接重复推送。
        """
        seen_backends: set = set()
        for module in self._ai_chat_modules.values():
            backend = getattr(module, "backend", None)
            if backend is None or not getattr(backend, "is_airi", False):
                continue
            if id(backend) in seen_backends:
                continue
            seen_backends.add(id(backend))
            push = getattr(backend, "push_game_event", None)
            if not callable(push):
                continue
            try:
                push(event_text)
            except Exception:
                pass

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
                if self._tui_chat_callback:
                    self._tui_chat_callback("[系统]", f"找不到玩家: {target}", "public")
                else:
                    async_print(f"  [私聊] 找不到玩家: {target}")
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
        # 公屏消息：随机打乱顺序，避免总是同一个 AI 先回复，并限制最多回复数量
        ai_items: List = list(self._ai_chat_modules.items())
        if not is_private:
            random.shuffle(ai_items)

        replied_count = 0
        for ai_name, module in ai_items:
            # 公屏消息：达到上限后停止（避免多 AI 刷屏）
            if not is_private and replied_count >= _MAX_PUBLIC_AI_REPLIES:
                break

            should_respond = False
            if not is_private:
                should_respond = True
            elif target_name == ai_name:
                should_respond = True

            if should_respond:
                # 发送 typing indicator（开始）
                typing_msg = {
                    "type": MessageType.TYPING_INDICATOR,
                    "player_name": ai_name,
                    "is_typing": True,
                }
                try:
                    self.server.broadcast_sync(typing_msg)
                    if self.lobby.host_plays or self._local_host_name:
                        self._host_typing(ai_name, True)
                except Exception:
                    pass

                try:
                    game_state = self.lobby.game_state if self.lobby else None
                    result = module.on_chat_received(
                        sender, content, is_private, game_state,
                    )
                    if result:
                        replied_count += 1
                        # 兼容：result 可能是 str（旧版）或 dict（新版）
                        if isinstance(result, str):
                            reply_text = result
                            reply_channel = "private" if is_private else "public"
                            reply_target = sender if is_private else None
                        else:
                            reply_text = result.get("text", "")
                            reply_channel = result.get(
                                "channel",
                                "private" if is_private else "public",
                            )
                            reply_target = result.get(
                                "reply_to",
                                sender if is_private else None,
                            )

                        if not reply_text:
                            continue

                        reply_msg = {
                            "type": MessageType.CHAT_MESSAGE,
                            "sender": ai_name,
                            "content": reply_text,
                            "channel": reply_channel,
                            "target": reply_target,
                        }

                        if reply_channel == "private" and reply_target:
                            # 私聊路由：发给目标
                            target_client = self._find_client_by_name(reply_target)
                            if target_client:
                                self.server.send_to_sync(target_client, reply_msg)
                            elif self._is_local_host(reply_target):
                                self._host_display(
                                    ai_name, reply_text,
                                    "private", reply_target,
                                )
                        else:
                            # 公屏路由
                            self.server.broadcast_sync(reply_msg)
                            if self.lobby.host_plays or self._local_host_name:
                                self._host_display(ai_name, reply_text, "public")
                except Exception:
                    pass
                finally:
                    # 清除 typing indicator
                    clear_msg = {
                        "type": MessageType.TYPING_INDICATOR,
                        "player_name": ai_name,
                        "is_typing": False,
                    }
                    try:
                        self.server.broadcast_sync(clear_msg)
                        if self.lobby.host_plays or self._local_host_name:
                            self._host_typing(ai_name, False)
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
