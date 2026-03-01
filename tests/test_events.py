"""
EventType 枚举测试
==================
重点验证：
1. EventType 枚举值与原有裸字符串完全相等（向后兼容）
2. log_event 同时接受 EventType 枚举和裸字符串
3. 事件日志可以用原有字符串方式（event["type"] == "attack"）正确检索
"""

import pytest

from engine.events import EventType
from engine.game_state import GameState
from models.player import Player


class TestEventTypeCompatibility:
    """EventType 继承 str，确保与裸字符串相等。"""

    def test_attack_equals_string(self):
        assert EventType.ATTACK == "attack"

    def test_death_equals_string(self):
        assert EventType.DEATH == "death"

    def test_crime_equals_string(self):
        assert EventType.CRIME == "crime"

    def test_round_start_equals_string(self):
        assert EventType.ROUND_START == "round_start"

    def test_can_use_in_string_comparison(self):
        event_type = EventType.ATTACK
        # 模拟原有代码：event.get("type") == "attack"
        assert event_type == "attack"
        assert "attack" == event_type


class TestLogEventWithEnum:
    @pytest.fixture
    def state(self):
        s = GameState()
        s.add_player(Player("p1", "Alice"))
        s.add_player(Player("p2", "Bob"))
        return s

    def test_log_with_enum(self, state):
        state.log_event(EventType.ATTACK, attacker="p1", target="p2")
        assert len(state.event_log) == 1

    def test_log_with_string_still_works(self, state):
        """裸字符串仍可用，向后兼容。"""
        state.log_event("attack", attacker="p1", target="p2")
        assert len(state.event_log) == 1

    def test_enum_and_string_both_findable(self, state):
        """用 EventType 记录，用裸字符串检索（原有代码模式）能找到。"""
        state.log_event(EventType.ATTACK, attacker="p1", target="p2", result={"success": True})
        state.log_event(EventType.DEATH, player="p2", cause="attack")

        attack_events = [e for e in state.event_log if e["type"] == "attack"]
        death_events  = [e for e in state.event_log if e["type"] == "death"]

        assert len(attack_events) == 1
        assert len(death_events) == 1
        assert attack_events[0]["attacker"] == "p1"

    def test_event_log_check_attack_crime_pattern(self, state):
        """
        模拟 round_manager._check_attack_crime 的检索模式：
        reversed(event_log) 中查找 type=="attack" + attacker + round 匹配。
        验证枚举值不会破坏该模式。
        """
        state.current_round = 2
        state.log_event(EventType.ATTACK,
                        attacker="p1",
                        round=2,
                        result={"success": True})

        found = None
        for event in reversed(state.event_log):
            if (event.get("type") == "attack"
                    and event.get("attacker") == "p1"
                    and event.get("round") == 2):
                found = event
                break

        assert found is not None
        assert found["result"]["success"] is True

    def test_all_event_types_are_strings(self):
        """所有 EventType 值都是合法字符串。"""
        for et in EventType:
            assert isinstance(et.value, str)
            assert len(et.value) > 0
