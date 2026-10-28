"""M9 RL 阶段 2：正式生命周期、往世层决策与固定 schema 契约。"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import numpy as np
import pytest
from gymnasium import spaces

from engine import experiments


@pytest.fixture(autouse=True)
def _m9_profile():
    experiments.set_profile("m9-rfc")
    try:
        yield
    finally:
        experiments.reset()


def test_finalize_winner_exposes_base_and_display_score_tracks() -> None:
    from engine.m9.pp import FinalScore
    from engine.round_manager import RoundManager

    results = {
        "rl_0": FinalScore(
            base_final_score=12.0, display_final_score=15.0, is_winner=True,
        ),
        "ai_0": FinalScore(
            base_final_score=10.0, display_final_score=10.0, is_winner=False,
        ),
    }
    players = {
        "rl_0": SimpleNamespace(is_alive=lambda: False),
        "ai_0": SimpleNamespace(is_alive=lambda: True),
    }
    scoring = SimpleNamespace(settle=lambda alive, dead, state: results)
    state = SimpleNamespace(
        player_order=["rl_0", "ai_0"],
        get_player=players.get,
        m9_scoring=scoring,
    )
    manager = RoundManager.__new__(RoundManager)
    manager.state = state

    winner = manager._finalize_winner("ai_0")

    assert winner == "rl_0"
    assert state.game_winner_snapshot == frozenset({"rl_0"})
    assert state.base_final_scores == {"rl_0": 12.0, "ai_0": 10.0}
    assert state.final_scores == {"rl_0": 15.0, "ai_0": 10.0}


def test_run_game_does_not_terminate_when_rl_player_dies() -> None:
    from rl.env import BadtimeWarEnv

    class _Manager:
        def __init__(self) -> None:
            self.rounds = 0

        def run_one_round(self) -> None:
            self.rounds += 1

        def _finalize_winner(self, survival_winner: str) -> str:
            assert survival_winner == "ai_0"
            return "rl_0"

    manager = _Manager()

    class _State:
        game_over = False
        winner = None
        survival_winner = None

        def check_victory(self):
            return "ai_0" if manager.rounds >= 2 else None

        @staticmethod
        def is_max_rounds_reached() -> bool:
            return False

    env = BadtimeWarEnv.__new__(BadtimeWarEnv)
    env._state = _State()
    env._round_manager = manager
    env._rl_controller = SimpleNamespace(_cache_player_ref=lambda player, state: None)
    env._rl_player = SimpleNamespace(is_alive=lambda: False)
    env.enable_talents = False
    env._game_over_flag = False
    env._max_rounds_reached = False
    env._termination_reason = None
    env._thread_error = None
    env._choose_mode = False
    env._obs_event = threading.Event()

    env._run_game()

    assert manager.rounds == 2, "RL 死亡后必须继续推进到正式终局"
    assert env._state.survival_winner == "ai_0"
    assert env._state.winner == "rl_0", "死者可以按正式得分反超生存胜者"
    assert env._termination_reason == "official_survival_end"


def test_dead_player_choose_uses_choose_mask_not_forfeit_fallback() -> None:
    from rl.action_space import ACTION_COUNT_M9_FIXED, IDX_CHOOSE_BASE
    from rl.env import BadtimeWarEnv

    env = BadtimeWarEnv.__new__(BadtimeWarEnv)
    env._state = SimpleNamespace(
        game_over=False,
        m9_enabled=True,
        player_order=["rl_0", "ai_0"],
    )
    env._rl_player = SimpleNamespace(is_alive=lambda: False)
    env._choose_mode = True
    env._choose_context = {"situation": ""}
    env._choose_options = ["押注", "跳过"]
    env.action_space = spaces.Discrete(ACTION_COUNT_M9_FIXED)

    mask = env.action_masks()

    assert len(mask) == ACTION_COUNT_M9_FIXED
    assert np.flatnonzero(mask).tolist() == [IDX_CHOOSE_BASE, IDX_CHOOSE_BASE + 1]


def test_max_rounds_finishes_with_official_score_settlement() -> None:
    from rl.action_space import ACTION_COUNT_M9_FIXED
    from rl.env import BadtimeWarEnv

    env = BadtimeWarEnv(
        num_opponents=1,
        max_rounds=4,
        opponent_bots=["dummy"],
        enable_talents=False,
        rl_talent=0,
    )
    try:
        _obs, info = env.reset(seed=20260820)
        done = False
        while not done:
            assert len(info["action_masks"]) == ACTION_COUNT_M9_FIXED
            action = int(np.flatnonzero(info["action_masks"])[0])
            _obs, _reward, terminated, truncated, info = env.step(action)
            done = terminated or truncated

        assert info["termination_reason"] == "official_max_rounds"
        assert info["official_settled"] is True
        assert info["winner"] in info["official_winners"]
        assert set(info["base_final_scores"]) == {"rl_0", "ai_0"}
        assert info["rl_rank"] is not None
        assert info["rl_score_margin"] is not None
    finally:
        env.close()


def test_choose_reward_attribution_uses_submitted_mode_snapshot() -> None:
    from rl.env import BadtimeWarEnv

    env = BadtimeWarEnv(
        num_opponents=1,
        max_rounds=8,
        opponent_bots=["dummy"],
        enable_talents=True,
        rl_talent=None,
        trace_decisions=True,
    )
    try:
        _obs, info = env.reset(seed=20260820)
        action = int(np.flatnonzero(info["action_masks"])[0])
        _obs, _reward, _terminated, _truncated, info = env.step(action)
        entry = info["decision_trace_entry"]
        assert entry["mode_before"] == "choose"
        assert entry["situation_before"] == "talent_pick"
        assert entry["action_type_attributed"] == "choose"
    finally:
        env.close()
