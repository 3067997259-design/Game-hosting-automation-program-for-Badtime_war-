"""
tools/m9_distill_train.py
────────────────────────
P4 统一学生训练（docs/m9/rl/m9_rl_next_steps.md §7）。

阶段 1（distill）：在 P3 数据集（bc_data/m9_distill，§6.2 排除清单生效）上
蒸馏单一 checkpoint——动作 NLL + teacher-logit KL + expert_id 辅助 CE，
按 expert_id 逆频率加权；验证 NLL 早停。

阶段 2（ppo）：小学习率 MaskableRecurrentPPO 微调（自然三人局），带
BC-anchor 回调（每 N 个 rollout 用蒸馏数据补几步监督梯度防遗忘），
周期性 winrate_eval 评估。

用法：
    python tools/m9_distill_train.py distill --out checkpoints/p4_student_v1
    python tools/m9_distill_train.py ppo --model checkpoints/p4_student_v1/distilled.zip \
        --steps 500000 --out checkpoints/p4_student_v1

注意：P4 checkpoint 含自定义策略类，任何加载方需先
``import rl.distill_student``（pickle 按引用序列化）。
"""

from __future__ import annotations

import argparse
import json
import os as _os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# 允许从仓库根直接运行；线程抑制须在 import numpy/torch 前
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_os.environ.setdefault("OMP_NUM_THREADS", "1")
_os.environ.setdefault("MKL_NUM_THREADS", "1")
_os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import numpy as np  # noqa: E402
import torch as th  # noqa: E402

from engine.experiments import set_profile  # noqa: E402

PROFILE = "m9-rfc"


def run_distill(args: argparse.Namespace) -> int:
    import torch as th

    set_profile(PROFILE)
    th.set_num_threads(max(1, args.threads))

    from stable_baselines3.common.vec_env import DummyVecEnv
    from rl.distill_student import (
        N_EXPERTS, load_distill_dataset, save_history, train_distillation,
    )
    from rl.env import BadtimeWarEnv
    from rl.maskable_recurrent import MaskableRecurrentPPO
    from rl.distill_student import DistillStudentPolicy

    episodes = load_distill_dataset(args.data)
    rng = np.random.default_rng(args.seed)
    order = rng.permutation(len(episodes))
    n_val = max(4, int(len(episodes) * args.val_frac))
    val_idx = set(order[:n_val].tolist())
    val_episodes = [episodes[i] for i in sorted(val_idx)]
    train_episodes = [episodes[i] for i in range(len(episodes)) if i not in val_idx]
    steps_train = sum(int(ep.actions.shape[0]) for ep in train_episodes)
    print(
        f"[data] {len(episodes)} 局（排除清单生效），train {len(train_episodes)}"
        f" 局/{steps_train} 步，val {len(val_episodes)} 局",
    )

    env = DummyVecEnv([
        lambda: BadtimeWarEnv(
            num_opponents=2, n_stack=1, max_rounds=None,
            enable_talents=False, rl_talent=0,
        ),
    ])
    if args.init:
        model = MaskableRecurrentPPO.load(args.init, env=env, device=args.device)
        print(f"[init] 从 {args.init} 续蒸（P4.1 定向补强口径）")
    else:
        model = MaskableRecurrentPPO(
            DistillStudentPolicy, env,
            n_steps=256, batch_size=256, n_epochs=4,
            learning_rate=3e-4, ent_coef=0.0, seed=args.seed,
            device=args.device, verbose=0,
            policy_kwargs=dict(
                distill_n_experts=N_EXPERTS, lstm_hidden_size=192,
                enable_critic_lstm=True,
                net_arch=dict(pi=[384, 256], vf=[384, 256]),
            ),
        )

    started = time.time()
    history = train_distillation(
        model.policy, train_episodes, val_episodes,
        epochs=args.epochs, learning_rate=args.lr,
        batch_episodes=args.batch_episodes,
        kl_coef=args.kl_coef, aux_coef=args.aux_coef,
        patience=args.patience, seed=args.seed,
        log_fn=lambda r: print(
            f"[distill] epoch {r['epoch']}: "
            f"train_nll={r['train_nll']:.4f} | "
            f"val_nll={r['val_nll']:.4f} val_kl={r['val_kl']:.4f} "
            f"aux_acc={r['val_aux_accuracy']:.1%}",
            flush=True,
        ),
    )
    print(
        f"[distill] 完成：{len(history)} epoch，"
        f"{time.time() - started:.0f}s，"
        f"best val_nll={min(r['val_nll'] for r in history):.4f}",
    )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    model.save(str(out_dir / "distilled.zip"))
    save_history(out_dir / "distill_history.json", history)
    env.close()
    print(f"[distill] 模型: {out_dir / 'distilled.zip'}")
    return 0


class BCDistillAnchor:
    """PPO 微调期间的蒸馏锚点步（防遗忘）。"""

    def __init__(self, episodes, policy, *, lr: float, kl_coef: float,
                 aux_coef: float, seed: int) -> None:
        from rl.distill_student import (
            expert_balance_weights, sequence_distill_loss,
        )

        self._episodes = episodes
        self._policy = policy
        self._weights = expert_balance_weights(episodes)
        self._kl_coef = kl_coef
        self._aux_coef = aux_coef
        self._rng = np.random.default_rng(seed)
        self._loss_fn = sequence_distill_loss
        trainable = [p for p in policy.parameters() if p.requires_grad]
        self._optimizer = th.optim.Adam(trainable, lr=lr)

    def step(self, batches: int) -> Dict[str, float]:
        self._policy.set_training_mode(True)
        totals = {"nll": 0.0, "kl": 0.0, "aux_ce": 0.0}
        steps = 0
        for _ in range(batches):
            idx = int(self._rng.integers(len(self._episodes)))
            episode = self._episodes[idx]
            self._optimizer.zero_grad(set_to_none=True)
            loss, metrics = self._loss_fn(
                self._policy, episode, self._weights,
                kl_coef=self._kl_coef, aux_coef=self._aux_coef,
            )
            loss.backward()
            th.nn.utils.clip_grad_norm_(
                [p for p in self._policy.parameters() if p.requires_grad],
                max_norm=0.5,
            )
            self._optimizer.step()
            length = int(episode.actions.shape[0])
            for key in totals:
                totals[key] += metrics[key] * length
            steps += length
        return {key: value / max(steps, 1) for key, value in totals.items()}


def _scenario_for_worker(scenarios, worker_idx: int):
    """worker 级课程场景循环分配；'natural'/'none' 与空表 → 自然局。"""
    if not scenarios:
        return None
    value = scenarios[worker_idx % len(scenarios)]
    return None if value in ("natural", "none") else value


# P6a-1 自然局伪场景（用户裁决 2026-08-23）：支持 worker 混合人数/天赋。
# 语义 = (对手数, 开天赋+随机分配, league 对手池)；普通课场景名走
# _course_contract。league=True 时对手经 OpponentPool 采样（918 快照池 +
# basic_ai_prob 混入 BasicAI；专家栈 828 不入池——obs 口径不兼容，留 P6b）。
_NATURAL_PSEUDO: dict[str, tuple[int, bool, bool]] = {
    "natural_3p": (2, False, False),
    "natural_3p_talent": (2, True, False),
    "natural_3p_league": (2, True, True),
    "natural_4p": (3, False, False),
    "natural_4p_talent": (3, True, False),
    "natural_5p": (4, False, False),
    "natural_5p_talent": (4, True, False),
    "natural_6p": (5, False, False),
    "natural_6p_talent": (5, True, False),
    "natural_6p_league": (5, True, True),
}


def run_ppo(args: argparse.Namespace) -> int:
    set_profile(PROFILE)

    import torch as th
    from stable_baselines3.common.callbacks import BaseCallback
    from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv

    import rl.distill_student  # noqa: F401 —— pickle 按引用反序列化策略类
    from rl.env import BadtimeWarEnv
    from rl.maskable_recurrent import MaskableRecurrentPPO
    from rl.winrate_eval import evaluate_policy
    from tools.m9_distill_collect import _course_contract

    scenario_list = [
        s.strip() for s in args.scenarios.split(",") if s.strip()
    ] if args.scenarios else []
    worker_scenarios = [
        _scenario_for_worker(scenario_list, i) for i in range(args.n_envs)
    ]
    print(f"worker 课程分配: {worker_scenarios}")

    def make(i: int):
        scenario = worker_scenarios[i]

        # spawn 子进程不继承主进程 profile——必须在 worker 内部重设
        # （m9_rl_train 同款纪律），否则 obs 维度回落 legacy 539
        def _factory():
            set_profile(PROFILE)
            if scenario in _NATURAL_PSEUDO:
                n_opponents, talent, league = _NATURAL_PSEUDO[scenario]
                pool = None
                if league:
                    import rl.distill_student  # noqa: F401 —— 池内自定义策略类反序列化
                    from rl.self_play import OpponentPool

                    pool = OpponentPool(
                        args.league_pool, n_stack=1,
                        basic_ai_prob=args.league_basic_ai_prob,
                        recurrent=True,
                    )
                return BadtimeWarEnv(
                    num_opponents=n_opponents, n_stack=1,
                    max_rounds=None, enable_talents=talent,
                    force_random_talent=talent, rl_talent=None,
                    opponent_pool=pool,
                )
            if scenario is None:
                return BadtimeWarEnv(
                    num_opponents=2, n_stack=1,
                    max_rounds=None, enable_talents=args.force_random_talent,
                    force_random_talent=args.force_random_talent,
                    rl_talent=None,
                )
            contract, rounds = _course_contract(scenario)
            return BadtimeWarEnv(
                num_opponents=2, n_stack=1, max_rounds=rounds,
                enable_talents=False, rl_talent=0,
                action_contract=contract, course_scenario=scenario,
            )
        return _factory

    if args.vec == "subproc" and args.n_envs > 1:
        venv = SubprocVecEnv([make(i) for i in range(args.n_envs)],
                             start_method="spawn")
    else:
        venv = DummyVecEnv([make(i) for i in range(args.n_envs)])

    model = MaskableRecurrentPPO.load(
        args.model, env=venv, device=args.device,
        learning_rate=args.learning_rate, ent_coef=args.ent_coef,
        seed=args.seed,
    )

    anchor_episodes = None
    if args.anchor_data:
        from rl.distill_student import load_distill_dataset

        anchor_episodes = load_distill_dataset(args.anchor_data)
        print(f"[anchor] {len(anchor_episodes)} 局蒸馏锚点就绪")

    class EvalAnchorCallback(BaseCallback):
        """周期评估 + 蒸馏锚点。"""

        def __init__(self) -> None:
            super().__init__()
            self._anchor: Optional[BCDistillAnchor] = None
            self._rollouts = 0
            self._eval_count = 0

        def _setup_model(self) -> None:
            pass

        def _on_training_start(self) -> None:
            if anchor_episodes is not None and args.anchor_batches > 0:
                self._anchor = BCDistillAnchor(
                    anchor_episodes, self.model.policy,
                    lr=args.anchor_lr, kl_coef=args.kl_coef,
                    aux_coef=args.aux_coef, seed=args.seed + 7,
                )

        def _on_rollout_end(self) -> None:
            self._rollouts += 1
            if (
                self._anchor is not None
                and self._rollouts % args.anchor_every == 0
            ):
                metrics = self._anchor.step(args.anchor_batches)
                self.logger.record("anchor/nll", metrics["nll"])
                self.logger.record("anchor/kl", metrics["kl"])

            if self.num_timesteps >= self._next_eval():
                self._eval_count += 1
                eval_n_opp = int(args.eval_num_opponents)
                report = evaluate_policy(
                    self.model,
                    num_opponents=eval_n_opp, n_stack=1,
                    n_episodes=args.eval_episodes,
                    seed=args.seed + 991 + self._eval_count,
                    recurrent=True,
                    env_factory=lambda: BadtimeWarEnv(
                        num_opponents=eval_n_opp, n_stack=1, max_rounds=None,
                        enable_talents=False, rl_talent=0,
                    ),
                )
                wr = report.official_win_rate
                wr_text = f"{wr:.0%}" if wr is not None else "n/a"
                print(
                    f"[eval {eval_n_opp + 1}p] step {self.num_timesteps}: "
                    f"official_win_rate={wr_text}, "
                    f"settlement={report.official_settlement_rate:.0%}",
                    flush=True,
                )
                self.logger.record("eval/official_win_rate",
                                   wr if wr is not None else -1.0)

        def _next_eval(self) -> int:
            return (self._eval_count + 1) * args.eval_every

        def _on_step(self) -> bool:
            return True

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    model.learn(
        total_timesteps=args.steps,
        callback=EvalAnchorCallback(),
        tb_log_name="p4_ppo",
    )
    model.save(str(out_dir / "finetuned.zip"))
    print(
        f"[ppo] 完成 {args.steps} 步，{time.time() - started:.0f}s → "
        f"{out_dir / 'finetuned.zip'}",
    )

    final = evaluate_policy(
        model,
        num_opponents=2, n_stack=1,
        n_episodes=args.final_eval_episodes,
        seed=args.seed + 4242, recurrent=True,
        env_factory=lambda: BadtimeWarEnv(
            num_opponents=2, n_stack=1, max_rounds=None,
            enable_talents=False, rl_talent=0,
        ),
    )
    summary = {
        "model": str(out_dir / "finetuned.zip"),
        "steps": args.steps,
        "official_win_rate": final.official_win_rate,
        "observed_win_rate": final.observed_win_rate,
        "official_settlement_rate": final.official_settlement_rate,
        "mean_rank": final.mean_rank,
        "mean_rounds": final.mean_rounds,
    }
    (out_dir / "finetune_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"[ppo] 终评: {json.dumps(summary, ensure_ascii=False)}")
    venv.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="phase", required=True)

    d = sub.add_parser("distill", help="阶段 1：蒸馏")
    d.add_argument("--data", default="bc_data/m9_distill")
    d.add_argument("--out", default="checkpoints/p4_student_v1")
    d.add_argument("--init", default=None,
                   help="从已有 checkpoint 续蒸（定向补强；缺省从零开始）")
    d.add_argument("--epochs", type=int, default=12)
    d.add_argument("--patience", type=int, default=2)
    d.add_argument("--lr", type=float, default=3e-4)
    d.add_argument("--batch-episodes", type=int, default=8)
    d.add_argument("--val-frac", type=float, default=0.05)
    d.add_argument("--kl-coef", type=float, default=1.0)
    d.add_argument("--aux-coef", type=float, default=0.5)
    d.add_argument("--seed", type=int, default=20260822)
    d.add_argument("--device", default="cpu")
    d.add_argument("--threads", type=int, default=4,
                   help="蒸馏阶段 torch 线程数（无 venv 争抢，可>1）")

    p = sub.add_parser("ppo", help="阶段 2：PPO 微调")
    p.add_argument("--model", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--steps", type=int, default=500000)
    p.add_argument("--n-envs", type=int, default=8)
    p.add_argument("--vec", choices=["subproc", "dummy"], default="subproc")
    p.add_argument("--learning-rate", type=float, default=1e-4)
    p.add_argument("--ent-coef", type=float, default=0.01)
    p.add_argument("--anchor-data", default="bc_data/m9_distill")
    p.add_argument("--scenarios", default=None,
                   help="逗号分隔 worker 场景：课场景名或自然局伪场景"
                        "（natural_3p[_talent]/natural_4p[_talent]/"
                        "natural_5p[_talent]/natural_6p[_talent]）；"
                        "natural/none=3p 自然局；缺省全 3p 自然局")
    p.add_argument("--force-random-talent", action="store_true",
                   help="自然局 worker 开天赋 + RL 随机天赋（A 组收量口径）")
    p.add_argument("--eval-num-opponents", type=int, default=2,
                   help="训练内周期评估的对手数（默认 2=3p）")
    p.add_argument("--league-pool", default="checkpoints/league_pool",
                   help="league 对手池目录（opponent_step_*.zip；918 系快照）")
    p.add_argument("--league-basic-ai-prob", type=float, default=0.3,
                   help="league 场景中对手采样为 BasicAI 的概率")
    p.add_argument("--anchor-every", type=int, default=5,
                   help="每 N 个 rollout 做一轮锚点")
    p.add_argument("--anchor-batches", type=int, default=4)
    p.add_argument("--anchor-lr", type=float, default=1e-5)
    p.add_argument("--kl-coef", type=float, default=1.0)
    p.add_argument("--aux-coef", type=float, default=0.5)
    p.add_argument("--eval-every", type=int, default=25000)
    p.add_argument("--eval-episodes", type=int, default=8)
    p.add_argument("--final-eval-episodes", type=int, default=40)
    p.add_argument("--seed", type=int, default=20260822)
    p.add_argument("--device", default="cpu")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.phase == "distill":
        return run_distill(args)
    return run_ppo(args)


if __name__ == "__main__":
    raise SystemExit(main())
