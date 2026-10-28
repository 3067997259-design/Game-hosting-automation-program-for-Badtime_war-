"""M9 奖励归因、去重、正式终局与 ledger 契约。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import rl.reward as legacy_reward
from rl.reward_m9 import (
    M9RewardTracker, m9_behavior_penalty, m9_event_reward,
    m9_supplemental_event_reward,
)


def _player():
    return SimpleNamespace(
        player_id="rl_0", no_action_streak=0, talent=None,
    )


def _state(*, game_over=False):
    def _get_player(pid):
        return SimpleNamespace(
            player_id=pid,
            kill_count=1 if pid == "ai_0" else 0,
            hp=1,
        )

    return SimpleNamespace(
        game_over=game_over,
        winner="ai_0",
        game_winner_snapshot=frozenset({"rl_0"}) if game_over else None,
        base_final_scores={"rl_0": 12.0, "ai_0": 9.0},
        event_log=[], player_order=["rl_0", "ai_0"], current_round=3,
        get_player=_get_player,
    )


def test_only_active_police_command_is_rewarded() -> None:
    automatic = {"type": "m9_police_enforcement", "captain": "rl_0"}
    commanded = {"type": "m9_police_command_attack", "captain": "rl_0"}
    elected = {"type": "m9_captain", "player": "rl_0"}
    assert m9_supplemental_event_reward([automatic, elected], "rl_0") == 0.0
    assert m9_supplemental_event_reward([commanded], "rl_0") == 8.0
    assert m9_supplemental_event_reward([commanded], "ai_0") == 0.0


def test_legacy_m9_event_is_not_double_counted() -> None:
    events = [{"type": "star_attack", "player": "rl_0"}]
    legacy = legacy_reward.event_reward(events, "rl_0")
    assert legacy == 8.0
    assert m9_supplemental_event_reward(events, "rl_0") == 0.0
    assert m9_event_reward(events, "rl_0") == legacy


def test_terminal_uses_official_snapshot_not_ambiguous_winner_field() -> None:
    tracker = M9RewardTracker("rl_0", gamma=1.0, alpha=0.0, beta=0.0)
    tracker._prev_potential = 0.0
    tracker._event_cursor = 0
    reward = tracker.compute(_player(), _state(game_over=True), "forfeit", True)

    # 胜者分支生效（非败方 -100/名次梯度）；该 fixture 里 RL 0 杀 0 剧情，
    # 按主动胜利条款（DOC-054）为空胜利打折
    from rl.reward_m9 import PASSIVE_VICTORY_REWARD

    assert reward == PASSIVE_VICTORY_REWARD
    assert tracker.last_ledger.terminal == PASSIVE_VICTORY_REWARD
    assert tracker.last_ledger.total == reward


def test_potential_is_scaled_and_ledger_sums_exactly() -> None:
    tracker = M9RewardTracker("rl_0", gamma=1.0, alpha=0.3, beta=0.5)
    tracker._prev_potential = 100.0
    tracker._event_cursor = 0
    state = _state(game_over=False)
    state.event_log.append({
        "type": "m9_police_command_attack", "captain": "rl_0",
    })
    with patch("rl.reward_m9.m9_potential", return_value=110.0):
        reward = tracker.compute(_player(), state, "move", True)

    ledger = tracker.last_ledger
    assert ledger.potential_raw == 10.0
    assert ledger.potential == 1.0
    assert ledger.m9_events_raw == 8.0
    assert ledger.events == pytest.approx(2.4)
    assert reward == pytest.approx(ledger.total)
    assert reward == pytest.approx(
        ledger.terminal + ledger.potential + ledger.events + ledger.behavior
    )
    assert tracker.episode_ledger()["total"] == pytest.approx(reward)


def test_m9_voluntary_forfeit_has_real_escalating_cost() -> None:
    player = _player()
    state = _state()
    first = m9_behavior_penalty(
        player, state, "forfeit", True, 0, [],
    )
    second = m9_behavior_penalty(
        player, state, "forfeit", True, 0, [],
    )
    assert first == -2.0
    assert second == -3.0


def test_m9_forfeit_exempts_g0_breath_and_forced_only_action() -> None:
    player = _player()
    state = _state()
    g0_event = [{
        "type": "g0_breath_forfeit_heal", "player": "rl_0", "gained": 4,
    }]
    assert m9_behavior_penalty(
        player, state, "forfeit", True, 0, g0_event,
    ) == 0.0
    assert m9_behavior_penalty(
        player, state, "forfeit", True, 0, [], forced_forfeit=True,
    ) == 0.0
