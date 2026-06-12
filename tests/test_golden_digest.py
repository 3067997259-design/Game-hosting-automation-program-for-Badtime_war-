"""M0c golden 摘要纯函数单测：字段排序稳定、message 剔除、diff 定位。"""
import unittest

from engine.replay_digest import (
    digest_event, digest_event_log, digest_game, diff_games,
)


class DigestEventTest(unittest.TestCase):

    def test_key_order_stable(self) -> None:
        """同内容不同插入序的事件必须产出同一行。"""
        e1 = {"round": 3, "phase": "r3_actions", "type": "attack",
              "player": "p1", "target": "p2", "damage": 1.5}
        e2 = {"damage": 1.5, "target": "p2", "player": "p1",
              "type": "attack", "phase": "r3_actions", "round": 3}
        self.assertEqual(digest_event(e1), digest_event(e2))

    def test_message_excluded(self) -> None:
        """自由文本字段不进摘要——改文案不算行为变化。"""
        base = {"round": 1, "phase": "r0", "type": "wake", "player": "p1"}
        with_msg = dict(base, message="某段会被随时润色的中文文案")
        self.assertEqual(digest_event(base), digest_event(with_msg))

    def test_float_formatting(self) -> None:
        """浮点固定两位，规避平台/版本 repr 差异。"""
        line = digest_event({"round": 1, "phase": "p", "type": "t", "hp": 2.5})
        self.assertIn("hp=2.50", line)

    def test_event_log_order_preserved(self) -> None:
        log = [
            {"round": 1, "phase": "a", "type": "x"},
            {"round": 2, "phase": "b", "type": "y"},
        ]
        lines = digest_event_log(log)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("R1|"))
        self.assertTrue(lines[1].startswith("R2|"))


class DiffGamesTest(unittest.TestCase):

    def _record(self, **overrides):
        base = {"seed": 42, "winner_pid": "p1", "rounds": 50,
                "draw": False, "draw_reason": "",
                "digest": ["R1|a|x|", "R2|b|y|"]}
        base.update(overrides)
        return base

    def test_identical_records_no_diff(self) -> None:
        a = self._record()
        b = self._record()
        self.assertEqual(diff_games(a, b), [])

    def test_outcome_divergence_reported(self) -> None:
        problems = diff_games(self._record(), self._record(winner_pid="p2"))
        self.assertTrue(any("winner_pid" in p for p in problems))

    def test_digest_divergence_locates_first_line(self) -> None:
        problems = diff_games(
            self._record(),
            self._record(digest=["R1|a|x|", "R2|b|z|"]),
        )
        self.assertTrue(any("@ 行 1" in p for p in problems))

    def test_digest_game_shape(self) -> None:
        result = {"seed": 7, "winner_pid": "p3", "rounds": 12,
                  "draw": False, "draw_reason": ""}
        record = digest_game(result, ["R1|a|x|"])
        self.assertEqual(record["seed"], 7)
        self.assertEqual(record["digest"], ["R1|a|x|"])


if __name__ == "__main__":
    unittest.main()
