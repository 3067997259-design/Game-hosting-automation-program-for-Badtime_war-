"""
rl/winrate_eval.py
──────────────────
带状态线的确定性胜率评估（RL 席位 vs 全 BasicAI 对手）。

专为递归（LSTM）策略设计：在决策点间保持并传递 (h, c) 隐藏态，episode
起点重置——与训练/部署的真实推理语义一致。老 MaskablePPO+GRU（30 帧堆叠
obs 自带时序）走同一入口（recurrent=False，predict 无状态）。

单一信源：train.py 的 RecurrentWinRateCallback 与 tools/phase1_legacy_compare.py
共用本函数，避免两处评估口径漂移。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np


@dataclass(frozen=True)
class EvaluationReport:
    """一次确定性评估的结构化结果。

    ``observed_*`` 保留当前环境实际使用的 winner 口径；``official_*`` 只统计
    完成正式 M9 settle 的 episode。二者禁止互相回退，避免生命周期错误被一个
    看似正常的胜率数字掩盖。
    """

    episodes: int
    observed_wins: int
    survival_wins: int
    official_wins: int
    official_settled_episodes: int
    official_contract_mismatches: int
    draws: int
    mean_rank: float | None
    mean_score_margin: float | None
    mean_rounds: float
    mean_return: float
    mean_decisions: float
    termination_reasons: dict[str, int]
    action_type_counts: dict[str, int]
    mean_episode_reward_components: dict[str, float]
    double_kill_episodes: int = 0
    first_kill_episodes: int = 0

    @property
    def double_kill_rate(self) -> float:
        """二杀率 = rl_kill_count>=2 局数 / 总局数。"""
        return self.double_kill_episodes / max(self.episodes, 1)

    @property
    def continuation_rate(self) -> float | None:
        """续战率 = rl_kill_count>=2 / rl_kill_count>=1（无首杀局为 None）。"""
        if self.first_kill_episodes == 0:
            return None
        return self.double_kill_episodes / self.first_kill_episodes

    @property
    def observed_win_rate(self) -> float:
        return self.observed_wins / max(self.episodes, 1)

    @property
    def survival_win_rate(self) -> float:
        return self.survival_wins / max(self.episodes, 1)

    @property
    def official_win_rate(self) -> float | None:
        if self.official_settled_episodes == 0:
            return None
        return self.official_wins / self.official_settled_episodes

    @property
    def official_settlement_rate(self) -> float:
        return self.official_settled_episodes / max(self.episodes, 1)

    def as_dict(self) -> dict[str, object]:
        """返回 JSON 友好的平面摘要。"""
        return {
            "episodes": self.episodes,
            "observed_win_rate": self.observed_win_rate,
            "survival_win_rate": self.survival_win_rate,
            "official_win_rate": self.official_win_rate,
            "official_settlement_rate": self.official_settlement_rate,
            "official_contract_mismatches": self.official_contract_mismatches,
            "draws": self.draws,
            "mean_rank": self.mean_rank,
            "mean_score_margin": self.mean_score_margin,
            "mean_rounds": self.mean_rounds,
            "mean_return": self.mean_return,
            "mean_decisions": self.mean_decisions,
            "termination_reasons": dict(self.termination_reasons),
            "action_type_counts": dict(self.action_type_counts),
            "mean_episode_reward_components": dict(
                self.mean_episode_reward_components
            ),
            "double_kill_rate": self.double_kill_rate,
            "continuation_rate": self.continuation_rate,
            "double_kill_episodes": self.double_kill_episodes,
            "first_kill_episodes": self.first_kill_episodes,
        }


def _mean_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    return float(np.mean(values))


def evaluate_policy(
    model,
    *,
    num_opponents: int,
    n_stack: int,
    n_episodes: int,
    seed: int,
    recurrent: bool,
    env_factory: Callable[[], object] | None = None,
) -> EvaluationReport:
    """确定性评估策略，返回正式/生存双轨指标与生命周期诊断。"""
    if env_factory is None:
        from rl.env import BadtimeWarEnv

        def env_factory() -> object:  # noqa: ANN202 —— 运行时闭包
            return BadtimeWarEnv(
                num_opponents=num_opponents,
                max_rounds=None,
                n_stack=n_stack,
                enable_talents=True,
                rl_talent=0,
            )

    env = env_factory()
    from rl.schema import validate_model_against_env

    validate_model_against_env(model, env, context="evaluation")
    observed_wins = 0
    survival_wins = 0
    official_wins = 0
    official_settled = 0
    official_contract_mismatches = 0
    draws = 0
    ranks: list[float] = []
    margins: list[float] = []
    rounds: list[float] = []
    returns: list[float] = []
    decisions: list[float] = []
    termination_reasons: dict[str, int] = {}
    action_type_counts: dict[str, int] = {}
    reward_component_totals = {
        key: 0.0 for key in ("terminal", "potential", "events", "behavior", "total")
    }
    double_kill_episodes = 0
    first_kill_episodes = 0

    try:
        for i in range(n_episodes):
            obs, _reset_info = env.reset(seed=seed + 1000 + i)
            state = None
            episode_start = np.array([False])
            done = False
            episode_return = 0.0
            decision_count = 0
            info: dict = {}
            while not done:
                # get_wrapper_attr：经 Monitor 等 wrapper 也能取到裸 env 的方法
                get_attr = getattr(env, "get_wrapper_attr", None)
                mask_fn = get_attr("action_masks") if callable(get_attr) \
                    else getattr(env, "action_masks")
                mask = mask_fn()
                if recurrent:
                    action, state = model.predict(
                        obs[None], state=state, episode_start=episode_start,
                        action_masks=mask[None], deterministic=True,
                    )
                else:
                    action, _ = model.predict(
                        obs[None], action_masks=mask[None], deterministic=True,
                    )
                action_idx = int(action[0])
                from rl.action_space import action_family_from_idx
                family = action_family_from_idx(action_idx)
                action_type_counts[family] = action_type_counts.get(family, 0) + 1
                obs, reward, term, trunc, info = env.step(action_idx)
                episode_return += float(reward)
                ledger = info.get("reward_ledger")
                if isinstance(ledger, dict):
                    for key in reward_component_totals:
                        reward_component_totals[key] += float(ledger.get(key, 0.0))
                decision_count += 1
                episode_start = np.array([term or trunc])
                done = term or trunc

            winner = info.get("winner")
            if winner == "rl_0":
                observed_wins += 1
            if winner == "nobody":
                draws += 1
            if info.get("survival_winner") == "rl_0":
                survival_wins += 1
            if bool(info.get("official_settled", False)):
                official_settled += 1
                official_winners = set(info.get("official_winners", ()))
                if "rl_0" in official_winners:
                    official_wins += 1
                base_scores = info.get("base_final_scores", {})
                expected_winners: set[str] = set()
                if isinstance(base_scores, dict) and base_scores:
                    top_score = max(float(score) for score in base_scores.values())
                    expected_winners = {
                        str(pid) for pid, score in base_scores.items()
                        if float(score) == top_score
                    }
                if (
                    not expected_winners
                    or official_winners != expected_winners
                    or winner not in official_winners
                ):
                    official_contract_mismatches += 1

            rank = info.get("rl_rank")
            if rank is not None:
                ranks.append(float(rank))
            margin = info.get("rl_score_margin")
            if margin is not None:
                margins.append(float(margin))
            rounds.append(float(info.get("rounds", 0) or 0))
            returns.append(episode_return)
            decisions.append(float(info.get("decisions", decision_count)))
            reason = str(info.get("termination_reason", "unknown"))
            termination_reasons[reason] = termination_reasons.get(reason, 0) + 1
            kills = int(info.get("rl_kill_count", 0) or 0)
            if kills >= 1:
                first_kill_episodes += 1
            if kills >= 2:
                double_kill_episodes += 1
    finally:
        env.close()

    return EvaluationReport(
        episodes=n_episodes,
        observed_wins=observed_wins,
        survival_wins=survival_wins,
        official_wins=official_wins,
        official_settled_episodes=official_settled,
        official_contract_mismatches=official_contract_mismatches,
        draws=draws,
        mean_rank=_mean_or_none(ranks),
        mean_score_margin=_mean_or_none(margins),
        mean_rounds=float(np.mean(rounds)) if rounds else 0.0,
        mean_return=float(np.mean(returns)) if returns else 0.0,
        mean_decisions=float(np.mean(decisions)) if decisions else 0.0,
        termination_reasons=termination_reasons,
        action_type_counts=action_type_counts,
        mean_episode_reward_components={
            key: value / max(n_episodes, 1)
            for key, value in reward_component_totals.items()
        },
        double_kill_episodes=double_kill_episodes,
        first_kill_episodes=first_kill_episodes,
    )


def evaluate_winrate(
    model,
    *,
    num_opponents: int,
    n_stack: int,
    n_episodes: int,
    seed: int,
    recurrent: bool,
    env_factory: Callable[[], object] | None = None,
) -> float:
    """跑 n_episodes 局，统计 RL 席位胜率（deterministic 决策）。

    Parameters
    ----------
    model : 已训练模型（MaskableRecurrentPPO 或 MaskablePPO）
    num_opponents : 对手数（RL + N 个 BasicAI）
    n_stack : 观测帧堆叠数（recurrent=1；GRU 老路线=30）
    n_episodes : 评估局数
    seed : 评估基准随机种子
    recurrent : True 时做 LSTM 状态穿越（决策点间保持 (h,c)，episode 起点重置）
    env_factory : 返回裸 BadtimeWarEnv 的可调用；缺省用
        ~BadtimeWarEnv(num_opponents, n_stack, enable_talents=True, rl_talent=0)~
    """
    report = evaluate_policy(
        model,
        num_opponents=num_opponents,
        n_stack=n_stack,
        n_episodes=n_episodes,
        seed=seed,
        recurrent=recurrent,
        env_factory=env_factory,
    )
    return report.observed_win_rate
