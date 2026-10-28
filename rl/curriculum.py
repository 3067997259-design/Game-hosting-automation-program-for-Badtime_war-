"""M9 课程晋级的纯数据门禁。

训练回调只负责收集 episode info；本模块负责把正式结算、胜率、名次、奖励
退化和结算契约合成可测试的晋级证据。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class CurriculumMetrics:
    episodes: int
    official_mode: bool
    win_rate: float
    settlement_rate: float
    contract_rate: float
    mean_rank: float | None
    mean_behavior_reward: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class CurriculumGateDecision:
    promote: bool
    metrics: CurriculumMetrics
    reasons: tuple[str, ...]


def _official_contract_valid(info: Mapping[str, object]) -> bool:
    if not bool(info.get("official_settled", False)):
        return False
    winners = {str(pid) for pid in (info.get("official_winners") or [])}
    scores = info.get("base_final_scores")
    if not winners or not isinstance(scores, dict) or not scores:
        return False
    numeric = {str(pid): float(score) for pid, score in scores.items()}
    top = max(numeric.values())
    expected = {pid for pid, score in numeric.items() if score == top}
    return winners == expected


def summarize_curriculum_window(
    infos: Sequence[Mapping[str, object]],
) -> CurriculumMetrics:
    episodes = len(infos)
    if episodes == 0:
        return CurriculumMetrics(0, False, 0.0, 0.0, 0.0, None, 0.0)

    official_mode = any("official_settled" in info for info in infos)
    if official_mode:
        settled = [bool(info.get("official_settled", False)) for info in infos]
        wins = [
            bool(ok and "rl_0" in (info.get("official_winners") or []))
            for ok, info in zip(settled, infos)
        ]
        contracts = [_official_contract_valid(info) for info in infos]
        ranks = [float(info["rl_rank"]) for info in infos if info.get("rl_rank") is not None]
        settlement_rate = sum(settled) / episodes
        contract_rate = sum(contracts) / episodes
    else:
        wins = [info.get("winner") == "rl_0" for info in infos]
        ranks = []
        settlement_rate = 1.0
        contract_rate = 1.0

    behavior_values = []
    for info in infos:
        ledger = info.get("episode_reward_ledger")
        if isinstance(ledger, dict):
            behavior_values.append(float(ledger.get("behavior", 0.0)))

    return CurriculumMetrics(
        episodes=episodes,
        official_mode=official_mode,
        win_rate=sum(wins) / episodes,
        settlement_rate=settlement_rate,
        contract_rate=contract_rate,
        mean_rank=(sum(ranks) / len(ranks)) if ranks else None,
        mean_behavior_reward=(
            sum(behavior_values) / len(behavior_values) if behavior_values else 0.0
        ),
    )


def evaluate_curriculum_gate(
    infos: Sequence[Mapping[str, object]],
    *,
    win_threshold: float,
    min_settlement_rate: float = 1.0,
    min_contract_rate: float = 1.0,
    min_mean_behavior_reward: float = -25.0,
) -> CurriculumGateDecision:
    metrics = summarize_curriculum_window(infos)
    reasons: list[str] = []
    if metrics.win_rate < win_threshold:
        reasons.append(
            f"win_rate {metrics.win_rate:.3f} < {win_threshold:.3f}"
        )
    if metrics.official_mode and metrics.settlement_rate < min_settlement_rate:
        reasons.append(
            f"settlement_rate {metrics.settlement_rate:.3f} < {min_settlement_rate:.3f}"
        )
    if metrics.official_mode and metrics.contract_rate < min_contract_rate:
        reasons.append(
            f"contract_rate {metrics.contract_rate:.3f} < {min_contract_rate:.3f}"
        )
    if metrics.mean_behavior_reward < min_mean_behavior_reward:
        reasons.append(
            "mean_behavior_reward "
            f"{metrics.mean_behavior_reward:.3f} < {min_mean_behavior_reward:.3f}"
        )
    return CurriculumGateDecision(not reasons, metrics, tuple(reasons))
