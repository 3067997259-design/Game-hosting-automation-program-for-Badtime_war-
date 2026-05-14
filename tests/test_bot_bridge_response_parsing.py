import sys
import unittest
from pathlib import Path


_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from bot_bridge import BotBridge, ResponseParser


class _FakeAiri:
    def __init__(self, reply):
        self.reply = reply

    def drain_responses(self):
        return []

    def send_text(self, _text):
        pass

    def wait_for_response(self, timeout):
        return self.reply


class TestBotBridgeResponseParsing(unittest.TestCase):
    def _bridge_with_reply(self, reply):
        bridge = BotBridge.__new__(BotBridge)
        bridge.airi = _FakeAiri(reply)
        bridge.action_timeout = 1
        return bridge

    def test_bracketed_number_selects_action_type(self):
        bridge = self._bridge_with_reply("[1]")

        result = bridge._ask_action_type(
            ["move 商店", "attack Alice 木剑"],
            {},
        )

        self.assertEqual(result, "move")

    def test_bracketed_number_selects_action_parameters(self):
        bridge = self._bridge_with_reply("[2]")

        result = bridge._ask_action_parameters(
            "move",
            {"available_actions": ["move 商店", "move 医院"]},
            {},
            ["move"],
        )

        self.assertEqual(result, "move 医院")

    def test_timestamp_prefix_does_not_consume_bracketed_choice(self):
        bridge = self._bridge_with_reply("[2026-05-13 09:11:05] [2]")

        result = bridge._ask_action_type(
            ["move 商店", "attack Alice 木剑"],
            {},
        )

        self.assertEqual(result, "attack")

    def test_clean_selection_reply_keeps_bracketed_number(self):
        self.assertEqual(BotBridge._clean_selection_reply("[1]"), "[1]")
        self.assertEqual(
            BotBridge._clean_selection_reply("[2026-05-13 09:11:05] [1]"),
            "[1]",
        )

    def test_extract_action_accepts_enriched_action_dicts(self):
        actions = [
            {"usage": "射击", "description": "远程攻击已锁定/正面的目标", "cost": 2},
            {"usage": "terminal", "description": "结束战术指令宏", "cost": 0},
        ]

        self.assertEqual(
            ResponseParser.extract_action("ACTION: 射击 Alice", actions),
            "射击 Alice",
        )
        self.assertEqual(
            ResponseParser.extract_action("terminal", actions),
            "terminal",
        )


if __name__ == "__main__":
    unittest.main()
