"""
rl/profile_train.py
───────────────────
训练性能诊断脚本。

跑一段短训练（默认 ~200k 步），详细输出每个训练迭代中各阶段（rollout 收集、
PPO update、单步耗时分布等）的耗时，帮助定位性能瓶颈。

用法：
    # 纯 rollout + update 性能（不含评估）
    python -u -m rl.profile_train \
        --resume pretrained/g7_warmstart.zip \
        --opponents 5 \
        --n-envs 16 \
        --n-steps 2048 \
        --batch-size 512 \
        --curriculum --curriculum-start 1 \
        --timesteps 200000

    # 对比：包含评估回调
    python -u -m rl.profile_train \
        --resume pretrained/g7_warmstart.zip \
        --opponents 5 \
        --n-envs 16 \
        --n-steps 2048 \
        --batch-size 512 \
        --curriculum --curriculum-start 1 \
        --timesteps 200000 \
        --with-eval --eval-freq 50000
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from stable_baselines3.common.callbacks import BaseCallback, CallbackList
from stable_baselines3.common.monitor import Monitor  # noqa: F401 (re-exported via rl.train)
from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv

from sb3_contrib import MaskablePPO
from sb3_contrib.common.maskable.callbacks import MaskableEvalCallback

from rl.train import (
    CurriculumCallback,
    WinRateCallback,
    make_env,
)
from rl.feature_extractor import GRUFeatureExtractor


# ─────────────────────────────────────────────────────────────────────────────
#  Profiling 回调
# ─────────────────────────────────────────────────────────────────────────────


class ProfilingCallback(BaseCallback):
    """记录每个训练迭代中各阶段的耗时。

    设计说明：
    - 必须放在 callback_list 的第一个，这样它的 `_on_rollout_start/end` 最先/最后
      被调用，计时最准确。
    - `_on_step()` 记录的是相邻两次 `_on_step` 调用之间的间隔，由于本回调排第一，
      它测到的间隔 ≈ env.step() 时间 + 其他 callback 的 `_on_step()` 开销。
    """

    def __init__(self, verbose: int = 1):
        super().__init__(verbose)
        self._rollout_start_time: float | None = None
        self._rollout_end_time: float | None = None
        self._step_times: list[float] = []
        self._last_step_time: float | None = None
        self._iteration = 0

        # 累计统计
        self._total_rollout = 0.0
        self._total_update = 0.0
        self._total_steps = 0

    # ── rollout 开始 ─────────────────────────────────────────────
    def _on_rollout_start(self) -> None:
        now = time.perf_counter()

        # 如果有上一轮的 rollout_end，那 rollout_end → 现在 = update 耗时
        if self._rollout_end_time is not None and self._rollout_start_time is not None:
            update_time = now - self._rollout_end_time
            self._total_update += update_time

            rollout_time = self._rollout_end_time - self._rollout_start_time
            n_steps = len(self._step_times)
            avg_step = sum(self._step_times) / max(n_steps, 1)
            max_step = max(self._step_times) if self._step_times else 0.0
            min_step = min(self._step_times) if self._step_times else 0.0
            sorted_times = sorted(self._step_times)
            median_step = (
                sorted_times[len(sorted_times) // 2]
                if sorted_times
                else 0.0
            )
            p95 = (
                sorted_times[int(len(sorted_times) * 0.95)]
                if sorted_times
                else 0.0
            )
            p99 = (
                sorted_times[int(len(sorted_times) * 0.99)]
                if sorted_times
                else 0.0
            )
            steps_per_sec = n_steps / rollout_time if rollout_time > 0 else 0.0

            slow_threshold = avg_step * 3
            slow_count = sum(1 for t in self._step_times if t > slow_threshold)

            total_iter = rollout_time + update_time
            rollout_pct = rollout_time / total_iter * 100 if total_iter > 0 else 0.0
            update_pct = update_time / total_iter * 100 if total_iter > 0 else 0.0
            cum_total = self._total_rollout + self._total_update
            cum_sps = self._total_steps / cum_total if cum_total > 0 else 0.0

            print(f"\n{'=' * 70}")
            print(f"  迭代 {self._iteration} 性能报告")
            print(f"{'=' * 70}")
            print(
                f"  Rollout 收集:  {rollout_time:8.2f}s  "
                f"({n_steps} 步, {steps_per_sec:.0f} steps/s)"
            )
            print(f"  PPO Update:    {update_time:8.2f}s")
            print(f"  总计:          {total_iter:8.2f}s")
            print(
                f"  占比:          Rollout {rollout_pct:.1f}% | "
                f"Update {update_pct:.1f}%"
            )
            print("  ──────────────────────────────────────")
            print(
                f"  单步耗时:  avg={avg_step * 1000:.2f}ms  "
                f"min={min_step * 1000:.2f}ms  "
                f"max={max_step * 1000:.2f}ms  "
                f"median={median_step * 1000:.2f}ms"
            )
            print(
                f"  百分位:    P50={median_step * 1000:.2f}ms  "
                f"P95={p95 * 1000:.2f}ms  P99={p99 * 1000:.2f}ms"
            )
            print(
                f"  慢步骤(>{slow_threshold * 1000:.1f}ms): "
                f"{slow_count}/{n_steps} ({slow_count / max(n_steps, 1) * 100:.1f}%)"
            )
            if slow_count > 0:
                slow_indices = [
                    (t, i) for i, t in enumerate(self._step_times) if t > slow_threshold
                ]
                slow_indices.sort(reverse=True)
                print("  最慢5步:")
                for t, idx in slow_indices[:5]:
                    print(f"    Step {idx}: {t * 1000:.2f}ms")
            if self._iteration == 1 and len(self._step_times) >= 20:
                print("  首轮前20步逐步耗时:")
                for j in range(20):
                    print(f"    Step {j + 1}: {self._step_times[j] * 1000:.2f}ms")
            print("  ──────────────────────────────────────")
            print(
                f"  累计: Rollout {self._total_rollout:.1f}s | "
                f"Update {self._total_update:.1f}s | "
                f"总步数 {self._total_steps} | 平均 {cum_sps:.0f} steps/s"
            )
            print(f"{'=' * 70}\n")

        self._rollout_start_time = now
        self._step_times = []
        self._last_step_time = now
        self._iteration += 1

    # ── 每步 ────────────────────────────────────────────────────
    def _on_step(self) -> bool:
        now = time.perf_counter()
        if self._last_step_time is not None:
            elapsed = now - self._last_step_time
            self._step_times.append(elapsed)
            self._total_steps += 1
        self._last_step_time = now
        return True

    # ── rollout 结束 ─────────────────────────────────────────────
    def _on_rollout_end(self) -> None:
        self._rollout_end_time = time.perf_counter()
        if self._rollout_start_time is not None:
            rollout_time = self._rollout_end_time - self._rollout_start_time
            self._total_rollout += rollout_time

    # ── 训练结束总结 ─────────────────────────────────────────────
    def _on_training_end(self) -> None:
        total = self._total_rollout + self._total_update
        avg_sps = self._total_steps / total if total > 0 else 0.0
        rollout_pct = self._total_rollout / total * 100 if total > 0 else 0.0
        update_pct = self._total_update / total * 100 if total > 0 else 0.0

        print(f"\n{'#' * 70}")
        print("  训练完成 — 总体性能摘要")
        print(f"{'#' * 70}")
        print(f"  总耗时:        {total:.1f}s")
        print(f"  总步数:        {self._total_steps}")
        print(f"  平均吞吐:      {avg_sps:.0f} steps/s")
        print(
            f"  Rollout 总计:  {self._total_rollout:.1f}s ({rollout_pct:.1f}%)"
        )
        print(f"  Update 总计:   {self._total_update:.1f}s ({update_pct:.1f}%)")
        print(f"  迭代次数:      {self._iteration}")
        print(f"{'#' * 70}\n")


# ─────────────────────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="训练性能诊断")

    # 环境参数
    p.add_argument("--resume", type=str, default=None,
                   help="从已有模型恢复（.zip 路径）")
    p.add_argument("--opponents", type=int, default=3,
                   help="对手数量 (1-5)")
    p.add_argument("--max-rounds", type=int, default=None,
                   help="每局最大轮数（默认：动态计算）")

    # 训练参数（profile 模式默认值与 train.py 典型值一致）
    p.add_argument("--n-envs", type=int, default=16)
    p.add_argument("--n-steps", type=int, default=2048)
    p.add_argument("--batch-size", type=int, default=512)
    p.add_argument("--n-epochs", type=int, default=10)
    p.add_argument("--timesteps", type=int, default=200_000)
    p.add_argument("--n-stack", type=int, default=30)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--device", type=str, default="auto")

    # 天赋
    p.add_argument("--rl-talent", type=int, default=None)
    p.add_argument("--enable-talents", action="store_true", default=True)
    p.add_argument("--no-talents", action="store_false", dest="enable_talents")

    # 课程学习
    p.add_argument("--curriculum", action="store_true")
    p.add_argument("--curriculum-start", type=int, default=1)
    p.add_argument("--curriculum-threshold", type=float, default=0.55)
    p.add_argument("--curriculum-thresholds", type=float, nargs="+", default=None)
    p.add_argument("--ent-rebound", type=float, default=0.03)
    p.add_argument("--ent-rebound-decay", type=int, default=200_000)

    # 评估（默认禁用，用 --with-eval 开启）
    p.add_argument("--with-eval", action="store_true",
                   help="包含评估回调（用于对比有/无评估时的性能差异）")
    p.add_argument("--eval-freq", type=int, default=50_000)
    p.add_argument("--eval-episodes", type=int, default=20)
    p.add_argument("--n-eval-envs", type=int, default=None)

    # Self-play（诊断模式）
    p.add_argument("--self-play", action="store_true",
                   help="启用 self-play（用于诊断 self-play 对训练速度的影响）")
    p.add_argument("--seed-model", type=str, default=None,
                   help="Self-play 种子模型路径（.zip），放入对手池作为初始对手")
    p.add_argument("--pool-size", type=int, default=20,
                   help="对手池最大模型数量")
    p.add_argument("--initial-basic-ai-prob", type=float, default=0.5,
                   help="Self-play 初始 BasicAI 混入概率")

    # PPO 超参数
    p.add_argument("--lr", type=float, default=3e-4)
    p.add_argument("--ent-coef", type=float, default=0.03)
    p.add_argument("--gamma", type=float, default=0.99)
    p.add_argument("--gae-lambda", type=float, default=0.95)
    p.add_argument("--clip-range", type=float, default=0.2)

    return p.parse_args()


# ─────────────────────────────────────────────────────────────────────────────
#  主函数
# ─────────────────────────────────────────────────────────────────────────────


def main() -> None:
    import torch

    args = parse_args()

    # ── 性能设置（与 train.py 一致）─────────────────────────────
    torch.set_num_threads(min(8, os.cpu_count() or 1))

    # ── 参数摘要 ─────────────────────────────────────────────────
    buffer_size = args.n_envs * args.n_steps
    updates_per_iter = args.n_epochs * max(buffer_size // args.batch_size, 1)
    n_iterations = max(args.timesteps // buffer_size, 1)

    print("=" * 70)
    print("  训练性能诊断 (Profile Mode)")
    print("=" * 70)
    print(f"  总步数:          {args.timesteps:,}")
    print(f"  并行环境:        {args.n_envs}")
    print(f"  n_steps:         {args.n_steps}")
    print(f"  batch_size:      {args.batch_size}")
    print(f"  n_epochs:        {args.n_epochs}")
    print(f"  buffer_size:     {buffer_size:,} (n_envs × n_steps)")
    print(f"  updates/iter:    {updates_per_iter} (n_epochs × buffer/batch)")
    print(f"  预计迭代次数:    {n_iterations}")
    print(f"  课程学习:        {'启用' if args.curriculum else '禁用'}")
    print(f"  评估回调:        {'启用' if args.with_eval else '禁用'}")
    print(f"  Self-play:       {'启用' if args.self_play else '禁用'}")
    if args.self_play and args.seed_model:
        print(f"  种子模型:        {args.seed_model}")
    print(f"  恢复模型:        {args.resume or '(无，从头训练)'}")
    print(f"  设备:            {args.device}")
    print("=" * 70)

    # ── 日志目录（诊断模式，不保存 checkpoint）──────────────────
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_name = f"profile_{args.opponents}opp_{timestamp}"
    log_dir = Path("logs") / run_name
    log_dir.mkdir(parents=True, exist_ok=True)
    print(f"  日志目录:        {log_dir}")
    print("=" * 70)

    # ── 课程阶段 ────────────────────────────────────────────────
    if args.curriculum:
        stages = list(range(args.curriculum_start, args.opponents + 1))
        if not stages:
            print(
                f"Warning: curriculum_start ({args.curriculum_start}) > "
                f"opponents ({args.opponents}), disabling curriculum."
            )
            stages = [args.opponents]
        initial_opponents = stages[0]
    else:
        stages = []
        initial_opponents = args.opponents

    # ── Self-play 对手池（可选）────────────────────────────────
    opponent_pool = None
    if args.self_play:
        from rl.self_play import OpponentPool
        opponent_pool = OpponentPool(
            pool_dir=str(log_dir / "opponent_pool"),
            n_stack=args.n_stack,
            max_pool_size=args.pool_size,
            basic_ai_prob=args.initial_basic_ai_prob,
        )
        if args.seed_model:
            from sb3_contrib import MaskablePPO as _MaskablePPO
            seed_m = _MaskablePPO.load(args.seed_model)
            opponent_pool.save_current_model(seed_m, step=0)
            del seed_m
        print(f"  Self-play:       启用 (seed_model={args.seed_model})")
    else:
        print(f"  Self-play:       禁用")

    # ── 训练环境 ────────────────────────────────────────────────
    env_fns = [
        make_env(
            num_opponents=initial_opponents,
            max_rounds=args.max_rounds,
            seed=args.seed,
            rank=i,
            n_stack=args.n_stack,
            opponent_pool=opponent_pool,
            rl_talent=args.rl_talent,
            enable_talents=args.enable_talents,
        )
        for i in range(args.n_envs)
    ]
    if args.n_envs > 1:
        train_env = SubprocVecEnv(env_fns, start_method="spawn")  # type: ignore
    else:
        train_env = DummyVecEnv(env_fns)  # type: ignore

    # ── 评估环境（可选）─────────────────────────────────────────
    eval_env = None
    if args.with_eval:
        n_eval_envs = args.n_eval_envs if args.n_eval_envs is not None else args.n_envs
        n_eval_envs = min(n_eval_envs, args.eval_episodes)
        n_eval_envs = max(n_eval_envs, 1)

        eval_env_fns = [
            make_env(
                num_opponents=initial_opponents,
                max_rounds=args.max_rounds,
                seed=args.seed + 1000,
                rank=i,
                n_stack=args.n_stack,
                rl_talent=args.rl_talent,
                enable_talents=args.enable_talents,
            )
            for i in range(n_eval_envs)
        ]
        if n_eval_envs > 1:
            eval_env = SubprocVecEnv(eval_env_fns, start_method="spawn")
        else:
            eval_env = DummyVecEnv(eval_env_fns)

    # ── 模型 ─────────────────────────────────────────────────────
    if args.resume:
        print(f"\n从 {args.resume} 恢复训练")
        model = MaskablePPO.load(
            args.resume,
            env=train_env,
            tensorboard_log=str(log_dir),
            learning_rate=args.lr,
            device=args.device,
        )
        # ── 覆盖超参数 ──
        model.n_steps = args.n_steps
        model.batch_size = args.batch_size
        model.n_epochs = args.n_epochs
        model.ent_coef = args.ent_coef
        model.gamma = args.gamma
        model.gae_lambda = args.gae_lambda
        model.clip_range = lambda _: args.clip_range
        print(f"  超参数已覆盖: n_steps={args.n_steps}, batch_size={args.batch_size}, "
              f"n_epochs={args.n_epochs}, ent_coef={args.ent_coef}")
    else:
        model = MaskablePPO(
            policy="MlpPolicy",
            env=train_env,
            learning_rate=args.lr,
            n_steps=args.n_steps,
            batch_size=args.batch_size,
            n_epochs=args.n_epochs,
            gamma=args.gamma,
            gae_lambda=args.gae_lambda,
            clip_range=args.clip_range,
            ent_coef=args.ent_coef,
            vf_coef=0.5,
            max_grad_norm=0.5,
            device=args.device,
            policy_kwargs=dict(
                features_extractor_class=GRUFeatureExtractor,
                features_extractor_kwargs=dict(
                    gru_hidden_size=192,
                    proj_size=256,
                    num_layers=1,
                ),
                net_arch=dict(pi=[384, 256], vf=[384, 256]),
            ),
            tensorboard_log=str(log_dir),
            verbose=0,
            seed=args.seed,
        )

    # ── 回调：ProfilingCallback 必须排第一 ──────────────────────
    profiling_cb = ProfilingCallback(verbose=1)

    curriculum_cb: CurriculumCallback | None = None
    if args.curriculum:
        curriculum_cb = CurriculumCallback(
            stages=stages,
            win_thresholds=args.curriculum_thresholds,
            win_threshold=args.curriculum_threshold,
            window=200,
            verbose=1,
            ent_rebound_coef=args.ent_rebound,
            ent_rebound_decay_steps=args.ent_rebound_decay,
            eval_env=eval_env,
        )

    win_rate_cb = WinRateCallback(
        check_freq=args.n_steps,
        window=100,
        verbose=1,
        curriculum_cb=curriculum_cb,
    )

    callback_list: list[BaseCallback] = [profiling_cb, win_rate_cb]

    if curriculum_cb is not None:
        callback_list.append(curriculum_cb)

    if args.with_eval and eval_env is not None:
        callback_list.append(
            MaskableEvalCallback(
                eval_env,
                best_model_save_path=None,
                log_path=str(log_dir / "eval"),
                eval_freq=max(args.eval_freq // args.n_envs, 1),
                n_eval_episodes=args.eval_episodes,
                deterministic=True,
            )
        )

    callbacks = CallbackList(callback_list)

    # ── 训练 ─────────────────────────────────────────────────────
    print(f"\n开始诊断训练: {run_name}\n")
    sys.stdout.flush()

    model.learn(
        total_timesteps=args.timesteps,
        callback=callbacks,
        progress_bar=True,
    )

    train_env.close()
    if eval_env is not None:
        eval_env.close()


if __name__ == "__main__":
    main()
