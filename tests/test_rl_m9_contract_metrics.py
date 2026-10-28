"""M9 RL 阶段 1：双轨终局指标与可选决策 trace 契约。"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
from gymnasium import spaces

from engine import experiments
from rl.winrate_eval import evaluate_policy, evaluate_winrate


class _OneStepEnv:
    """每局一步结束的评估桩，隔离统计口径而不启动真实引擎。"""

    def __init__(self, episodes: list[dict]) -> None:
        self._episodes = episodes
        self._episode_idx = -1
        self.closed = False
        self.observation_space = spaces.Box(
            low=-1.0, high=1.0, shape=(3,), dtype=np.float32,
        )
        self.action_space = spaces.Discrete(2)

    def reset(self, *, seed=None):
        self._episode_idx += 1
        return np.zeros(3, dtype=np.float32), {}

    def action_masks(self) -> np.ndarray:
        return np.array([True, False])

    def step(self, action: int):
        info = dict(self._episodes[self._episode_idx])
        reward = float(info.pop("reward", 0.0))
        return np.zeros(3, dtype=np.float32), reward, True, False, info

    def close(self) -> None:
        self.closed = True


class _ZeroModel:
    observation_space = spaces.Box(
        low=-1.0, high=1.0, shape=(3,), dtype=np.float32,
    )
    action_space = spaces.Discrete(2)

    def predict(self, obs, **kwargs):
        return np.array([0]), None


def test_schema_error_names_both_versions_and_dimensions() -> None:
    from rl.schema import RLSchemaMismatchError, validate_spaces

    with pytest.raises(RLSchemaMismatchError) as exc_info:
        validate_spaces(
            spaces.Box(-1.0, 1.0, shape=(572,), dtype=np.float32),
            spaces.Discrete(156),
            spaces.Box(-1.0, 1.0, shape=(828,), dtype=np.float32),
            spaces.Discrete(217),
            context="test checkpoint",
        )

    message = str(exc_info.value)
    assert "deprecated-m9-dynamic-home-obs572-act156" in message
    assert "m9-rfc-obs828-act217-v5" in message
    assert "不可直接恢复" in message


def test_evaluation_report_never_aliases_observed_to_official() -> None:
    episodes = [
        {
            "winner": "rl_0",
            "survival_winner": "rl_0",
            "official_settled": False,
            "official_winners": [],
            "rounds": 4,
            "decisions": 3,
            "reward": 2.0,
            "termination_reason": "rl_death_shortcut",
        },
        {
            "winner": "ai_0",
            "survival_winner": "ai_0",
            "official_settled": True,
            "official_winners": ["rl_0"],
            "base_final_scores": {"rl_0": 12.0, "ai_0": 9.5},
            "rl_rank": 1,
            "rl_score_margin": 2.5,
            "rounds": 8,
            "decisions": 5,
            "reward": -1.0,
            "termination_reason": "official_settle",
        },
    ]
    env = _OneStepEnv(episodes)
    report = evaluate_policy(
        _ZeroModel(),
        num_opponents=1,
        n_stack=1,
        n_episodes=2,
        seed=7,
        recurrent=False,
        env_factory=lambda: env,
    )

    assert report.observed_win_rate == pytest.approx(0.5)
    assert report.survival_win_rate == pytest.approx(0.5)
    assert report.official_settlement_rate == pytest.approx(0.5)
    assert report.official_win_rate == pytest.approx(1.0)
    assert report.official_contract_mismatches == 1
    assert report.mean_rank == pytest.approx(1.0)
    assert report.mean_score_margin == pytest.approx(2.5)
    assert report.mean_rounds == pytest.approx(6.0)
    assert report.mean_return == pytest.approx(0.5)
    assert report.mean_decisions == pytest.approx(4.0)
    assert report.termination_reasons == {
        "rl_death_shortcut": 1,
        "official_settle": 1,
    }
    assert report.action_type_counts == {"forfeit": 2}
    assert report.mean_episode_reward_components == {
        "terminal": 0.0,
        "potential": 0.0,
        "events": 0.0,
        "behavior": 0.0,
        "total": 0.0,
    }
    assert env.closed


def test_legacy_winrate_api_keeps_observed_winner_semantics() -> None:
    env = _OneStepEnv([{
        "winner": "rl_0",
        "official_settled": False,
        "official_winners": [],
    }])
    win_rate = evaluate_winrate(
        _ZeroModel(),
        num_opponents=1,
        n_stack=1,
        n_episodes=1,
        seed=11,
        recurrent=False,
        env_factory=lambda: env,
    )
    assert win_rate == pytest.approx(1.0)


def test_episode_summary_reads_frozen_scores_without_settling() -> None:
    from rl.env import BadtimeWarEnv

    env = BadtimeWarEnv.__new__(BadtimeWarEnv)
    env._termination_reason = "official_settle"
    env._decision_counter = 9
    env._rl_player = SimpleNamespace(is_alive=lambda: False, kill_count=2)
    env._state = SimpleNamespace(
        winner="rl_0",
        survival_winner="ai_0",
        game_winner_snapshot=frozenset({"rl_0"}),
        base_final_scores={"rl_0": 12.0, "ai_0": 10.0},
        final_scores={"rl_0": 14.0, "ai_0": 10.0},
        current_round=17,
    )

    summary = env._build_episode_summary()

    assert summary["official_settled"] is True
    assert summary["official_winners"] == ["rl_0"]
    assert summary["rl_rank"] == 1
    assert summary["rl_score_margin"] == pytest.approx(2.0)
    assert summary["display_final_scores"]["rl_0"] == pytest.approx(14.0)
    assert summary["decisions"] == 9


def test_real_env_trace_captures_submitted_decision_semantics() -> None:
    from rl.env import BadtimeWarEnv

    experiments.set_profile("m9-rfc")
    env = BadtimeWarEnv(
        num_opponents=1,
        max_rounds=8,
        opponent_bots=["dummy"],
        enable_talents=False,
        rl_talent=0,
        trace_decisions=True,
    )
    try:
        _obs, info = env.reset(seed=20260820)
        legal = np.flatnonzero(info["action_masks"])
        action = int(next(idx for idx in legal if idx != 0))
        _obs, _reward, _term, _trunc, step_info = env.step(action)
        entry = step_info["decision_trace_entry"]
        assert entry["decision_id"] == 0
        assert entry["mode_before"] == "command"
        assert entry["action_idx"] == action
        assert entry["selected_semantic"]
        assert entry["action_spec"]["stable_key"]
        assert "reward_ledger" in entry
        assert action in entry["legal_actions_before"]
        assert env.get_decision_trace()[-1] == entry
    finally:
        env.close()
        experiments.reset()
