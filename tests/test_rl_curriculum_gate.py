"""课程只能凭正式结算和健康策略证据晋级。"""

from __future__ import annotations

from rl.curriculum import evaluate_curriculum_gate, summarize_curriculum_window


def _info(*, official_win: bool, settled: bool = True,
          contract: bool = True, behavior: float = -2.0) -> dict:
    scores = {"rl_0": 12.0, "ai_0": 9.0} if official_win else {
        "rl_0": 8.0, "ai_0": 11.0,
    }
    winners = ["rl_0"] if official_win else ["ai_0"]
    if not contract:
        winners = ["ai_0"] if official_win else ["rl_0"]
    return {
        "episode": {"r": 1.0},
        "winner": "rl_0",  # 故意与 official 冲突，证明 observed 不参与 M9 晋级
        "official_settled": settled,
        "official_winners": winners if settled else [],
        "base_final_scores": scores if settled else {},
        "rl_rank": 1 if official_win else 2,
        "episode_reward_ledger": {"behavior": behavior},
    }


def test_official_loss_cannot_be_hidden_by_observed_winner() -> None:
    infos = [_info(official_win=False) for _ in range(10)]
    metrics = summarize_curriculum_window(infos)
    assert metrics.official_mode
    assert metrics.win_rate == 0.0
    assert not evaluate_curriculum_gate(infos, win_threshold=0.5).promote


def test_healthy_official_window_promotes() -> None:
    infos = [_info(official_win=i < 6) for i in range(10)]
    decision = evaluate_curriculum_gate(infos, win_threshold=0.55)
    assert decision.promote
    assert decision.metrics.win_rate == 0.6
    assert decision.metrics.settlement_rate == 1.0
    assert decision.metrics.contract_rate == 1.0


def test_incomplete_or_invalid_settlement_blocks_promotion() -> None:
    incomplete = [_info(official_win=True) for _ in range(9)] + [
        _info(official_win=True, settled=False)
    ]
    invalid = [_info(official_win=True) for _ in range(9)] + [
        _info(official_win=True, contract=False)
    ]
    assert not evaluate_curriculum_gate(incomplete, win_threshold=0.5).promote
    assert not evaluate_curriculum_gate(invalid, win_threshold=0.5).promote


def test_behavior_collapse_blocks_high_win_rate() -> None:
    infos = [_info(official_win=True, behavior=-40.0) for _ in range(10)]
    decision = evaluate_curriculum_gate(infos, win_threshold=0.5)
    assert not decision.promote
    assert any("mean_behavior_reward" in reason for reason in decision.reasons)
