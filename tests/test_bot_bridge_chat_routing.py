import sys
import unittest
from pathlib import Path

_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from bot_bridge import BotBridge
from network.protocol import MessageType


class FakeAiri:
    def __init__(self):
        self.sent_texts = []
        self.responses = []

    def send_text(self, text: str):
        self.sent_texts.append(text)

    def drain_responses(self):
        drained = list(self.responses)
        self.responses.clear()
        return drained


class FakeGameClient:
    def __init__(self):
        self.sent_messages = []

    def send_sync(self, msg):
        self.sent_messages.append(msg)


class TestBotBridgeChatRouting(unittest.TestCase):
    def setUp(self):
        self.bridge = BotBridge({"bot_name": "AIRI_Bot"})
        self.airi = FakeAiri()
        self.game_client = FakeGameClient()
        self.bridge.airi = self.airi
        self.bridge.game_client = self.game_client

    def test_private_idle_chat_reply_preserves_private_route(self):
        self.bridge._on_chat_message({
            "sender": "Alice",
            "content": "秘密计划",
            "channel": "private",
            "target": "AIRI_Bot",
        })

        self.airi.responses.append("收到，我会保密。")
        self.bridge._flush_idle_chat()

        self.assertEqual(len(self.game_client.sent_messages), 1)
        sent = self.game_client.sent_messages[0]
        self.assertEqual(sent["type"], MessageType.CHAT_SEND)
        self.assertEqual(sent["sender"], "AIRI_Bot")
        self.assertEqual(sent["content"], "收到，我会保密。")
        self.assertEqual(sent["channel"], "private")
        self.assertEqual(sent["target"], "Alice")

    def test_public_idle_chat_reply_remains_public(self):
        self.bridge._on_chat_message({
            "sender": "Bob",
            "content": "大家好",
            "channel": "public",
        })

        self.airi.responses.append("你好！")
        self.bridge._flush_idle_chat()

        self.assertEqual(len(self.game_client.sent_messages), 1)
        sent = self.game_client.sent_messages[0]
        self.assertEqual(sent["channel"], "public")
        self.assertNotIn("target", sent)

    def test_command_reply_consumes_queued_route(self):
        self.bridge._on_chat_message({
            "sender": "Alice",
            "content": "秘密计划",
            "channel": "private",
            "target": "AIRI_Bot",
        })
        self.bridge._on_chat_message({
            "sender": "Bob",
            "content": "大家好",
            "channel": "public",
        })

        self.airi.responses.extend(["COMMAND:move shop", "你好！"])
        self.bridge._flush_idle_chat()

        self.assertEqual(len(self.game_client.sent_messages), 1)
        sent = self.game_client.sent_messages[0]
        self.assertEqual(sent["channel"], "public")
        self.assertNotIn("target", sent)
        self.assertFalse(self.bridge._idle_chat_routes)

    def test_self_chat_is_ignored_and_does_not_claim_next_reply(self):
        self.bridge._on_chat_message({
            "sender": "AIRI_Bot",
            "content": "echo",
            "channel": "private",
            "target": "Alice",
        })

        self.airi.responses.append("主动发言")
        self.bridge._flush_idle_chat()

        self.assertEqual(len(self.game_client.sent_messages), 1)
        sent = self.game_client.sent_messages[0]
        self.assertEqual(sent["channel"], "public")
        self.assertNotIn("target", sent)


if __name__ == "__main__":
    unittest.main()
