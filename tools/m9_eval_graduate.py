"""
tools/m9_eval_graduate.py
────────────────────────
M9 毕业评估：专家栈 vs 全 BasicAI 对手（docs/m9/rl/m9_rl_next_steps.md §3/§4）。

- 3p 基线：--num-opponents 2（无天赋三人局）→ 产出回归锚点 JSON；
- 多人零样本（P1）：--num-opponents 3/4/5，不训练直接评估；
- 指标（评估口径单一信源 rl/winrate_eval.py）：
    正式胜率 = official_wins / official_settled_episodes；
    二杀率   = rl_kill_count>=2 局数 / 总局数（决策点最大可见值口径）；
    续战率   = rl_kill_count>=2 / rl_kill_count>=1；
    切换率   = 每局专家切换次数均值（P1 路由冲突量化）。

运行：
    python tools/m9_eval_graduate.py --num-opponents 2 --seeds 5 --episodes 40
    python tools/m9_eval_graduate.py --num-opponents 4 --seeds 3 --episodes 20 \
        --out checkpoints/zeroshot_5p.json
"""

from __future__ import annotations

import argparse
import json
import os as _os
import sys
from pathlib import Path
from typing import Any, Dict, List

# 允许从仓库根直接运行；线程抑制须在 import numpy/torch 前
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_os.environ.setdefault("OMP_NUM_THREADS", "1")
_os.environ.setdefault("MKL_NUM_THREADS", "1")
_os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import numpy as np  # noqa: E402

from engine.experiments import set_profile  # noqa: E402


PROFILE = "m9-rfc"


def _aggregate(stack: Any, episodes: int) -> Dict[str, float]:
    """从专家栈的按局汇总里聚合二杀/续战/切换指标。"""
    summaries = stack.episode_summaries
    double_kills = sum(s.final_kill_count >= 2 for s in summaries)
    first_kills = sum(s.final_kill_count >= 1 for s in summaries)
    total_switches = sum(s.switches for s in summaries)
    return {
        "double_kill_rate": double_kills / max(episodes, 1),
        "continuation_rate": (
            double_kills / first_kills if first_kills else None
        ),
        "mean_switches_per_episode": total_switches / max(episodes, 1),
    }


def _expert_usage(stack: Any) -> Dict[str, int]:
    usage: Dict[str, int] = {}
    for summary in stack.episode_summaries:
        for expert, count in summary.routing_counts.items():
            usage[expert] = usage.get(expert, 0) + count
    return dict(sorted(usage.items(), key=lambda kv: -kv[1]))


def run_evaluation(args: argparse.Namespace) -> int:
    set_profile(PROFILE)

    from rl.env import BadtimeWarEnv
    from rl.expert_stack import (
        ExpertStackModel, load_experts, make_env_facts_fn,
    )
    from rl.obs_builder_m9 import OBS_DIM_M9_V5_CORE
    from rl.obs_compat import LegacyObsDimWrapper
    from rl.schema import validate_model_against_env
    from rl.winrate_eval import evaluate_policy

    if args.single:
        return run_single_model_evaluation(args)

    experts = load_experts(device=args.device)
    print(f"[stack] 已加载 {len(experts)} 个专家: {sorted(experts)}")

    seed_reports: List[Dict[str, Any]] = []
    official_rates: List[float] = []
    settled_total = 0
    official_wins_total = 0
    kills_metrics_total: Dict[str, float] = {
        "double_kill_rate": 0.0,
        "continuation_rate": 0.0,
        "mean_switches_per_episode": 0.0,
    }
    usage_total: Dict[str, int] = {}

    for seed_idx, seed in enumerate(
        range(args.seed_base, args.seed_base + args.seeds),
    ):
        env = LegacyObsDimWrapper(
            BadtimeWarEnv(
                num_opponents=args.num_opponents,
                max_rounds=None,
                n_stack=1,
                enable_talents=False,
                rl_talent=0,
            ),
            OBS_DIM_M9_V5_CORE,  # 专家栈冻结在 v5 核心宽度（828）
        )
        stack = ExpertStackModel(experts, make_env_facts_fn(env))
        validate_model_against_env(
            stack, env, context=f"expert stack (seed {seed})",
        )
        report = evaluate_policy(
            stack,
            num_opponents=args.num_opponents,
            n_stack=1,
            n_episodes=args.episodes,
            seed=seed,
            recurrent=True,
            env_factory=lambda: env,
        )
        stack.flush()  # 最后一局没有下一个 episode 边界，手动收口
        if len(stack.episode_summaries) != args.episodes:
            print(
                f"[warn] seed {seed}: 局汇总数 "
                f"{len(stack.episode_summaries)} != episodes "
                f"{args.episodes}（可能存在 0 决策局或边界检测失效）",
            )
        kills = _aggregate(stack, args.episodes)
        usage = _expert_usage(stack)
        seed_reports.append({
            "seed": seed,
            "episodes": args.episodes,
            "official_win_rate": report.official_win_rate,
            "observed_win_rate": report.observed_win_rate,
            "official_settlement_rate": report.official_settlement_rate,
            "official_contract_mismatches": report.official_contract_mismatches,
            "mean_rank": report.mean_rank,
            "mean_rounds": report.mean_rounds,
            "mean_decisions": report.mean_decisions,
            "double_kill_rate": kills["double_kill_rate"],
            "continuation_rate": kills["continuation_rate"],
            "mean_switches_per_episode": kills["mean_switches_per_episode"],
            "expert_usage": usage,
            "termination_reasons": report.termination_reasons,
        })
        if report.official_win_rate is not None:
            official_rates.append(report.official_win_rate)
        settled_total += report.official_settled_episodes
        official_wins_total += report.official_wins
        for key in kills_metrics_total:
            value = kills[key]
            kills_metrics_total[key] += float(value) if value is not None else 0.0
        for expert, count in usage.items():
            usage_total[expert] = usage_total.get(expert, 0) + count
        wr = (
            f"{report.official_win_rate:.0%}"
            if report.official_win_rate is not None else "n/a"
        )
        cont = (
            f"{kills['continuation_rate']:.0%}"
            if kills["continuation_rate"] is not None else "n/a"
        )
        print(
            f"[seed {seed}] official_win_rate={wr} | "
            f"settlement={report.official_settlement_rate:.0%} | "
            f"double_kill={kills['double_kill_rate']:.0%} | "
            f"continuation={cont}"
        )

    continuation_values = [
        r["continuation_rate"] for r in seed_reports
        if r["continuation_rate"] is not None
    ]
    overall = {
        "profile": PROFILE,
        "num_opponents": args.num_opponents,
        "players": args.num_opponents + 1,
        "seeds": args.seeds,
        "episodes_per_seed": args.episodes,
        "talents": False,
        "opponents": "basic_ai",
        "official_win_rate": (
            official_wins_total / settled_total if settled_total else None
        ),
        "official_win_rate_seed_mean": (
            float(np.mean(official_rates)) if official_rates else None
        ),
        "official_win_rate_seed_min": (
            float(np.min(official_rates)) if official_rates else None
        ),
        "official_settled_episodes": settled_total,
        "double_kill_rate": kills_metrics_total["double_kill_rate"] / args.seeds,
        "continuation_rate": (
            float(np.mean(continuation_values)) if continuation_values else None
        ),
        "mean_switches_per_episode": (
            kills_metrics_total["mean_switches_per_episode"] / args.seeds
        ),
        "expert_usage_total": dict(
            sorted(usage_total.items(), key=lambda kv: -kv[1]),
        ),
        "per_seed": seed_reports,
    }

    out = Path(args.out) if args.out else Path(
        f"checkpoints/graduation_baseline_{args.num_opponents + 1}p.json",
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(overall, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    wr = (
        f"{overall['official_win_rate']:.0%}"
        if overall["official_win_rate"] is not None else "n/a"
    )
    cont = (
        f"{overall['continuation_rate']:.0%}"
        if overall["continuation_rate"] is not None else "n/a"
    )
    print(
        f"[final {args.num_opponents + 1}p] official_win_rate={wr} | "
        f"double_kill={overall['double_kill_rate']:.0%} | "
        f"continuation={cont} | "
        f"switches/ep={overall['mean_switches_per_episode']:.1f}"
    )
    print(f"[final] 专家用量: {overall['expert_usage_total']}")
    print(f"[final] 结果落盘: {out}")
    return 0


def run_single_model_evaluation(args: argparse.Namespace) -> int:
    """单一 checkpoint（P4 学生）评估：原生 918 观测，无专家栈。

    --course <scenario> 时按 P3 采集同款合同/轮上限构造课程 env
    （门禁①原子课抽检口径）。
    """
    import rl.distill_student  # noqa: F401 —— 自定义策略类按引用反序列化
    from rl.env import BadtimeWarEnv
    from rl.maskable_recurrent import MaskableRecurrentPPO
    from rl.winrate_eval import evaluate_policy

    course = getattr(args, "course", None)
    rl_talent = getattr(args, "rl_talent", None) or 0
    random_talents = bool(getattr(args, "random_talents", False))
    if course:
        from tools.m9_distill_collect import _course_contract

        contract, rounds = _course_contract(course)

        def env_factory():
            return BadtimeWarEnv(
                num_opponents=args.num_opponents, max_rounds=rounds,
                n_stack=1, enable_talents=False, rl_talent=0,
                action_contract=contract, course_scenario=course,
            )
    elif rl_talent:
        # P5 逐槽基线：预留池强制指定天赋（对手不可抢占）
        def env_factory():
            return BadtimeWarEnv(
                num_opponents=args.num_opponents, max_rounds=None,
                n_stack=1, enable_talents=True,
                rl_talent=rl_talent, rl_talent_reserve=True,
            )
    elif random_talents:
        # P5 随机天赋门禁：全桌开天赋，RL 随机分配（force_random_talent
        # 先于 AI 选，保证均匀）
        def env_factory():
            return BadtimeWarEnv(
                num_opponents=args.num_opponents, max_rounds=None,
                n_stack=1, enable_talents=True, force_random_talent=True,
            )
    else:

        def env_factory():
            return BadtimeWarEnv(
                num_opponents=args.num_opponents, max_rounds=None,
                n_stack=1, enable_talents=False, rl_talent=0,
            )

    model = MaskableRecurrentPPO.load(args.single, device=args.device)
    print(f"[single] 已加载 {args.single}"
          + (f" | course={course}" if course else ""))

    seed_reports: List[Dict[str, Any]] = []
    official_rates: List[float] = []
    settled_total = 0
    official_wins_total = 0
    dk_total = fk_total = 0

    for seed in range(args.seed_base, args.seed_base + args.seeds):
        report = evaluate_policy(
            model,
            num_opponents=args.num_opponents,
            n_stack=1,
            n_episodes=args.episodes,
            seed=seed,
            recurrent=True,
            env_factory=env_factory,
        )
        cont = (
            f"{report.continuation_rate:.0%}"
            if report.continuation_rate is not None else "n/a"
        )
        wr = (
            f"{report.official_win_rate:.0%}"
            if report.official_win_rate is not None else "n/a"
        )
        print(
            f"[seed {seed}] official_win_rate={wr} | "
            f"settlement={report.official_settlement_rate:.0%} | "
            f"double_kill={report.double_kill_rate:.0%} | "
            f"continuation={cont}",
        )
        seed_reports.append({
            "seed": seed,
            "official_win_rate": report.official_win_rate,
            "observed_win_rate": report.observed_win_rate,
            "official_settlement_rate": report.official_settlement_rate,
            "mean_rank": report.mean_rank,
            "mean_rounds": report.mean_rounds,
            "double_kill_rate": report.double_kill_rate,
            "continuation_rate": report.continuation_rate,
            "termination_reasons": report.termination_reasons,
        })
        if report.official_win_rate is not None:
            official_rates.append(report.official_win_rate)
        settled_total += report.official_settled_episodes
        official_wins_total += report.official_wins
        dk_total += report.double_kill_episodes
        fk_total += report.first_kill_episodes

    continuation_values = [
        r["continuation_rate"] for r in seed_reports
        if r["continuation_rate"] is not None
    ]
    overall = {
        "profile": PROFILE,
        "mode": "single",
        "model": args.single,
        "course": course,
        "rl_talent": rl_talent,
        "random_talents": random_talents,
        "num_opponents": args.num_opponents,
        "players": args.num_opponents + 1,
        "seeds": args.seeds,
        "episodes_per_seed": args.episodes,
        "talents": False,
        "opponents": "basic_ai",
        "official_win_rate": (
            official_wins_total / settled_total if settled_total else None
        ),
        "official_win_rate_seed_mean": (
            float(np.mean(official_rates)) if official_rates else None
        ),
        "double_kill_rate": dk_total / max(args.seeds * args.episodes, 1),
        "continuation_rate": (
            float(np.mean(continuation_values)) if continuation_values else None
        ),
        "first_kill_episodes": fk_total,
        "double_kill_episodes": dk_total,
        "per_seed": seed_reports,
    }
    out = Path(args.out) if args.out else Path(
        f"checkpoints/single_{args.num_opponents + 1}p.json",
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(overall, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    wr = (
        f"{overall['official_win_rate']:.0%}"
        if overall["official_win_rate"] is not None else "n/a"
    )
    cont = (
        f"{overall['continuation_rate']:.0%}"
        if overall["continuation_rate"] is not None else "n/a"
    )
    print(
        f"[final single {args.num_opponents + 1}p] official_win_rate={wr} | "
        f"double_kill={overall['double_kill_rate']:.0%} | "
        f"continuation={cont}",
    )
    print(f"[final] 结果落盘: {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--num-opponents", type=int, default=2,
                        help="对手数（2=三人局基线；3/4/5=多人零样本）")
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--episodes", type=int, default=40)
    parser.add_argument("--seed-base", type=int, default=20260822)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--single", default=None,
                        help="单一 checkpoint 路径（P4 学生；原生 918 观测）")
    parser.add_argument("--course", default=None,
                        help="原子课程场景（与 --single 组合；合同/轮上限与 P3 采集同款）")
    parser.add_argument("--rl-talent", type=int, default=None,
                        help="强制 RL 天赋编号 1-14（与 --single 组合；预留池保证分配）")
    parser.add_argument("--random-talents", action="store_true",
                        help="全桌开天赋 + RL 随机天赋（P5 随机天赋门禁口径）")
    parser.add_argument("--out", default=None,
                        help="结果 JSON 路径；缺省 checkpoints/graduation_baseline_Np.json")
    return parser


def main() -> int:
    return run_evaluation(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
