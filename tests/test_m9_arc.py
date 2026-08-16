"""M9 通用剧情分三章制完结条测试（arc RFC v0.1）。"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from engine.m9.arc import (
    CHAPTER_CURTAIN,
    CHAPTER_DEBUT,
    CHAPTER_SPOTLIGHT,
    ChapterLedger,
    _owner_of_actor,
)
from engine.m9.action_system import ActionSystem
from engine.m9.pp import PPLedger, ScoringEngine


def _player(pid: str, slot: str) -> SimpleNamespace:
    return SimpleNamespace(player_id=pid, talent_slot_id=slot,
                           talent=SimpleNamespace(is_terror=False))


class _FakeState:
    def __init__(self) -> None:
        self.players = {}
        self.player_order: list = []
        self.event_log: list = []
        self.current_round = 1
        self.current_phase = "test"
        self.m9_pp = PPLedger()
        self.m9_scoring = ScoringEngine(self.m9_pp, self)

    def get_player(self, pid: str):
        return self.players.get(pid)

    def log_event(self, event_type: str, **kwargs) -> None:
        self.event_log.append({"round": self.current_round,
                               "phase": self.current_phase,
                               "type": event_type, **kwargs})


class ChapterLedgerUnitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = _FakeState()
        self.state.players = {"p1": _player("p1", "T1"),
                              "p2": _player("p2", "G5")}
        self.state.player_order = ["p1", "p2"]
        self.ledger = ChapterLedger(self.state)
        self.state.m9_arc = self.ledger

    def test_owner_of_shadow_actor(self) -> None:
        self.assertEqual(_owner_of_actor("G2:shadow@p1"), "p1")
        self.assertEqual(_owner_of_actor("p2"), "p2")
        self.assertIsNone(_owner_of_actor(None))

    def test_debut_grant_is_ordered_and_idempotent(self) -> None:
        self.ledger.on_public_performance("p1", 1)
        self.assertTrue(self.ledger.has_debut("p1"))
        self.assertEqual(self.state.m9_scoring.arc_count("p1"), 1)
        self.assertEqual(self.state.m9_pp.balance("p1"), 1)
        self.ledger.on_public_performance("p1", 2)
        self.assertEqual(self.state.m9_scoring.arc_count("p1"), 1)

    def test_sequential_unlock_blocks_spotlight_before_debut(self) -> None:
        self.state.log_event("crystal_flower", player="p2")
        self.ledger.scan(self.state)
        self.assertFalse(self.ledger.has_chapter("p2", CHAPTER_SPOTLIGHT))
        self.assertEqual(self.state.m9_scoring.arc_count("p2"), 0)
        # 登台后新发生的同一类事件才解锁第二章（已扫描事件不重放）
        self.ledger.on_public_performance("p2", 1)
        self.state.log_event("crystal_flower", player="p2")
        self.ledger.scan(self.state)
        self.assertTrue(self.ledger.has_chapter("p2", CHAPTER_SPOTLIGHT))

    def test_arc_cap(self) -> None:
        self.ledger.on_public_performance("p1", 1)
        self.state.log_event("oneslash_attack", player="p1", killed=True)
        self.state.log_event("death", player="p2", killer="p1",
                             source_kind="t1_core_slash")
        self.ledger.scan(self.state)
        # cap=3：登台+高光+谢幕后不再增长
        self.ledger.scan(self.state)
        self.assertEqual(self.state.m9_scoring.arc_count("p1"), 3)
        from engine.balance import get as _bget
        arc_weight = float(_bget(
            "m9_system", "scoring_m9", "arc_weight", default=1.5))
        self.assertLessEqual(self.state.m9_scoring._story("p1"),
                             3 * arc_weight + 1e-9)

    def test_t3_spotlight_hits_field(self) -> None:
        self.ledger.on_public_performance("p1", 1)
        self.state.log_event("star_attack", player="p1", location="商店",
                             hits=2, kills=0)
        self.ledger.scan(self.state)
        self.assertTrue(self.ledger.has_chapter("p1", CHAPTER_SPOTLIGHT))

    def test_g2_last_song_curtain(self) -> None:
        p3 = _player("p3", "G2")
        self.state.players["p3"] = p3
        self.state.player_order.append("p3")
        self.ledger.on_public_performance("p3", 1)
        self.ledger._grant_ordered("p3", CHAPTER_SPOTLIGHT)
        self.state.log_event("g2_last_song_heard", player="p3")
        self.ledger.scan(self.state)
        self.assertTrue(self.ledger.has_chapter("p3", CHAPTER_CURTAIN))

    def test_g7_terror_state_predicate(self) -> None:
        p4 = _player("p4", "G7")
        self.state.players["p4"] = p4
        self.state.player_order.append("p4")
        self.ledger.on_public_performance("p4", 1)
        self.state.log_event("death", player="p2", killer="p4",
                             source_kind="g7_kill_approx")
        self.ledger.scan(self.state)  # 高光章（第一版任意击杀近似）
        self.assertTrue(self.ledger.has_chapter("p4", CHAPTER_SPOTLIGHT))
        p4.talent.is_terror = True
        self.ledger.scan(self.state)  # 状态谓词：Terror 进入 → 谢幕章
        self.assertTrue(self.ledger.has_chapter("p4", CHAPTER_CURTAIN))


class DebutPriorityQueueTest(unittest.TestCase):
    def test_unperformed_candidate_gets_priority(self) -> None:
        state = _FakeState()
        ledger = ChapterLedger(state)
        m9 = ActionSystem()
        m9.attach_arc(ledger, state)
        m9.register_player("a")
        m9.register_player("b")
        m9.set_sp("a", 2)
        m9.set_sp("b", 2)
        m9.queue.enqueue("a")
        m9.queue.enqueue("b")
        ledger.on_public_performance("a", 1)
        holder = m9.allocate_public_slot(1)
        self.assertEqual(holder, "b")  # 未登台者优先于队首已登台者

    def test_fifo_within_same_class(self) -> None:
        state = _FakeState()
        ledger = ChapterLedger(state)
        m9 = ActionSystem()
        m9.attach_arc(ledger, state)
        m9.register_player("a")
        m9.register_player("b")
        m9.set_sp("a", 2)
        m9.set_sp("b", 2)
        m9.queue.enqueue("a")
        m9.queue.enqueue("b")
        # 两人都未登台：保持 FIFO
        self.assertEqual(m9.allocate_public_slot(1), "a")


if __name__ == "__main__":
    unittest.main()
