"""
rl/train.py
───────────
MaskablePPO 训练入口脚本

用法：
    # 默认训练
    python -m rl.train

    # 自定义参数
    python -m rl.train --timesteps 500000 --opponents 3 --max-rounds 50 --seed 42

    # 继续训练
    python -m rl.train --resume checkpoints/best_model.zip
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
from stable_baselines3.common.callbacks import (
    BaseCallback,
    CallbackList,
    CheckpointCallback,
    EvalCallback,
)
from stable_baselines3.common.logger import configure
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv

from sb3_contrib import MaskablePPO
from sb3_contrib.common.maskable.callbacks import MaskableEvalCallback

from rl.env import BadtimeWarEnv
from rl.feature_extractor import GRUFeatureExtractor
from typing import TYPE_CHECKING
from typing import Optional

if TYPE_CHECKING:
    from rl.self_play import OpponentPool


# ─────────────────────────────────────────────────────────────────────────────
#  环境工厂
# ─────────────────────────────────────────────────────────────────────────────

def make_env(
    num_opponents: int = 3,
    max_rounds: Optional[int] = None,
    seed: int = 0,
    rank: int = 0,
    n_stack: int = 1,
    opponent_pool=None,
    rl_talent: Optional[int] = None,
    enable_talents: bool = True,
):
    """返回一个创建 BadtimeWarEnv 的闭包，供 DummyVecEnv 使用。"""
    def _init():
            import os, sys
            # Suppress prompt_manager initialization prints
            devnull = open(os.devnull, 'w')
            old_stdout = sys.stdout
            sys.stdout = devnull
            try:
                env = BadtimeWarEnv(
                    num_opponents=num_opponents,
                    max_rounds=max_rounds,
                    n_stack=n_stack,
                    opponent_pool=opponent_pool,
                    rl_talent=rl_talent,
                    enable_talents=enable_talents,
                )
                env = Monitor(env)
                env.reset(seed=seed + rank)
            finally:
                sys.stdout = old_stdout
                devnull.close()
            return env
    return _init


# ─────────────────────────────────────────────────────────────────────────────
#  自定义回调：打印训练摘要
# ─────────────────────────────────────────────────────────────────────────────


class WinRateCallback(BaseCallback):
    """
    每 `check_freq` 步统计最近 `window` 局的胜率并记录到 TensorBoard。
    依赖 env.step() 在 info dict 中写入的 "winner" 字段。
    """
    def __init__(self, check_freq: int = 2048, window: int = 100, verbose: int = 0,
                 curriculum_cb: "CurriculumCallback | None" = None):
        super().__init__(verbose)
        self.check_freq = check_freq
        self.window = window
        self._episode_rewards: list[float] = []
        self._episode_wins: list[bool] = []
        self._curriculum_cb = curriculum_cb

    def _on_step(self) -> bool:
        infos = self.locals.get("infos", [])
        for info in infos:
            ep_info = info.get("episode")
            if ep_info is not None:
                self._episode_rewards.append(ep_info["r"])
                # 从 info dict 读取实际胜者（env.step 写入）
                winner = info.get("winner")
                self._episode_wins.append(winner == "rl_0")

        if self.n_calls % self.check_freq == 0 and self._episode_wins:
            recent_wins = self._episode_wins[-self.window:]
            recent_rewards = self._episode_rewards[-self.window:]
            win_rate = sum(recent_wins) / len(recent_wins)
            mean_reward = np.mean(recent_rewards)

            self.logger.record("custom/win_rate", win_rate)
            self.logger.record("custom/mean_episode_reward", mean_reward)
            self.logger.record("custom/episodes_total", len(self._episode_wins))

            if self.verbose >= 1:
                stage_info = ""
                if self._curriculum_cb is not None:
                    cb = self._curriculum_cb
                    stage = cb._current_stage
                    n_opp = cb.stages[stage] if stage < len(cb.stages) else "?"
                    stage_info = f" | Opponents: {n_opp} (stage {stage}/{len(cb.stages)-1})"
                print(
                    f"[Step {self.num_timesteps}] "
                    f"Win rate: {win_rate:.1%} | "
                    f"Mean reward: {mean_reward:.1f} | "
                    f"Episodes: {len(self._episode_wins)}"
                    f"{stage_info}"
                )

        return True

class CurriculumCallback(BaseCallback):
    def __init__(
        self,
        stages: list[int],
        win_thresholds: list[float] | None = None,
        win_threshold: float = 0.55,  # fallback default
        window: int = 200,
        verbose: int = 0,
    ):
        super().__init__(verbose)
        self.stages = stages
        # Per-stage thresholds: one per transition (len = len(stages) - 1)
        if win_thresholds and len(win_thresholds) == len(stages) - 1:
            self.win_thresholds = win_thresholds
        elif win_threshold != 0.55 or win_thresholds is not None:
            # User provided a single global threshold — apply uniformly
            self.win_thresholds = [win_threshold] * (len(stages) - 1)
        else:
            # Auto-compute: decreasing thresholds based on player count
            # More opponents = lower threshold (random baseline is 1/(n+1))
            self.win_thresholds = []
            for i in range(len(stages) - 1):
                n_opponents = stages[i]
                random_baseline = 1.0 / (n_opponents + 1)
                # Threshold = random_baseline * 1.3 (30% above random)
                self.win_thresholds.append(max(random_baseline * 1.3, 0.35))
        self.window = window
        self._current_stage = 0
        self._episode_wins: list[bool] = []

    def _on_step(self) -> bool:
        infos = self.locals.get("infos", [])
        for info in infos:
            ep_info = info.get("episode")
            if ep_info is not None:
                winner = info.get("winner", None)
                self._episode_wins.append(winner == "rl_0")

        # 检查是否升级
        if (
            self._current_stage < len(self.stages) - 1
            and len(self._episode_wins) >= self.window
        ):
            recent = self._episode_wins[-self.window:]
            win_rate = sum(recent) / len(recent)
            threshold = self.win_thresholds[self._current_stage] if self._current_stage < len(self.win_thresholds) else 0.55
            if win_rate >= threshold:
                self._current_stage += 1
                new_opponents = self.stages[self._current_stage]
                self._update_envs(new_opponents)
                self._episode_wins.clear()  # 重置统计

                if self.verbose >= 1:
                    print(f"  [Curriculum] 升级! 对手数: {new_opponents} (stage {self._current_stage}/{len(self.stages)-1}, win_rate={win_rate:.1%}, threshold={threshold:.1%})")

        return True

    def _update_envs(self, new_opponents: int):
            """更新所有子环境的对手数量（下次 reset 生效）。"""
            venv = self.training_env
            # env_method uses getattr which penetrates gym.Wrapper via __getattr__
            # set_attr does NOT penetrate wrappers (sets on Monitor, not BadtimeWarEnv)
            venv.env_method("set_num_opponents", new_opponents)

class SelfPlayCallback(BaseCallback):
    """
    Self-play 回调：质量门控保存 + BasicAI 概率退火。

    - 持续追踪最近 N 局的胜率
    - 只有胜率 >= min_win_rate 时才保存模型到对手池
    - BasicAI 概率退火与保存解耦，按步数线性退火
    - 保存频率仍受 save_freq 限制（即使胜率达标，两次保存之间至少间隔 save_freq 步）
    """
    def __init__(
        self,
        pool: "OpponentPool",
        save_freq: int = 500_000,
        initial_basic_ai_prob: float = 0.5,
        final_basic_ai_prob: float = 0.1,
        anneal_steps: int = 5_000_000,
        min_win_rate: float = 0.45,
        win_rate_window: int = 200,
        verbose: int = 0,
    ):
        super().__init__(verbose)
        self.pool = pool
        self.save_freq = save_freq
        self.initial_basic_ai_prob = initial_basic_ai_prob
        self.final_basic_ai_prob = final_basic_ai_prob
        self.anneal_steps = anneal_steps
        self.min_win_rate = min_win_rate
        self.win_rate_window = win_rate_window
        self._episode_wins: list[bool] = []
        self._last_save_step: int = 0

    def _on_step(self) -> bool:
        # ── 1. 收集胜率数据 ──
        infos = self.locals.get("infos", [])
        for info in infos:
            ep_info = info.get("episode")
            if ep_info is not None:
                winner = info.get("winner")
                self._episode_wins.append(winner == "rl_0")

        # ── 2. 定期检查是否保存 + 退火 ──
        if self.n_calls % self.save_freq == 0:
            # 2a. 计算当前胜率
            current_win_rate = None
            if len(self._episode_wins) >= self.win_rate_window:
                recent = self._episode_wins[-self.win_rate_window:]
                current_win_rate = sum(recent) / len(recent)

            # 2b. 质量门控：只有胜率达标才保存
            saved = False
            if current_win_rate is not None and current_win_rate >= self.min_win_rate:
                self.pool.save_current_model(self.model, self.num_timesteps)
                self._last_save_step = self.num_timesteps
                saved = True

            # 2c. 退火（无论是否保存都执行）
            progress = min(self.num_timesteps / self.anneal_steps, 1.0)
            new_prob = self.initial_basic_ai_prob + (
                self.final_basic_ai_prob - self.initial_basic_ai_prob
            ) * progress
            self.pool.basic_ai_prob = new_prob
            self.training_env.env_method("update_basic_ai_prob", new_prob)

            # 2d. 日志
            if self.verbose >= 1:
                n_models = len(self.pool.get_available_models())
                wr_str = f"{current_win_rate:.1%}" if current_win_rate is not None else f"N/A (< {self.win_rate_window} episodes)"
                save_str = "SAVED" if saved else f"SKIPPED (need >= {self.min_win_rate:.0%})"
                print(
                    f"  [SelfPlay] Step {self.num_timesteps} | "
                    f"Win rate: {wr_str} | {save_str} | "
                    f"Pool size: {n_models} | BasicAI prob: {new_prob:.1%}"
                )

        return True


# ─────────────────────────────────────────────────────────────────────────────
#  训练主函数
# ─────────────────────────────────────────────────────────────────────────────

def train(args: argparse.Namespace):
    import sys
    sys.stderr.write(f"[TRAIN] args: {args}\n")
    sys.stderr.flush()

    """执行训练流程。"""

    # ── 路径设置 ──────────────────────────────────────────────────
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_name = f"maskable_ppo_{args.opponents}opp_{timestamp}"
    log_dir = Path("logs") / run_name
    ckpt_dir = Path("checkpoints") / run_name
    log_dir.mkdir(parents=True, exist_ok=True)
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    if args.curriculum:
        stages = list(range(1, args.opponents + 1))
        initial_opponents = stages[0]
    else:
        stages = []
        initial_opponents = args.opponents
    # 在创建环境之前
    opponent_pool = None
    if args.self_play:
        from rl.self_play import OpponentPool
        opponent_pool = OpponentPool(
            pool_dir=str(ckpt_dir / "opponent_pool"),
            n_stack=args.n_stack,
            max_pool_size=args.pool_size,
            basic_ai_prob=args.initial_basic_ai_prob,
        )
        # 如果提供了种子模型，先放入对手池
        if args.seed_model:
            from sb3_contrib import MaskablePPO as _MaskablePPO
            seed_m = _MaskablePPO.load(args.seed_model)
            opponent_pool.save_current_model(seed_m, step=0)
            del seed_m
# ── 训练环境 ──────────────────────────────────────────────────
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
        train_env = SubprocVecEnv(env_fns, start_method="spawn")   # type: ignore
    else:
        train_env = DummyVecEnv(env_fns) # type: ignore

    # ── 评估环境 ──────────────────────────────────────────────────
    eval_env = DummyVecEnv([
        make_env(
            num_opponents=args.opponents,
            max_rounds=args.max_rounds,
            seed=args.seed + 1000,
            rank=0,
            n_stack=args.n_stack,
            rl_talent=args.rl_talent,
            enable_talents=args.enable_talents,
        )
    ])

    # ── 模型 ─────────────────────────────────────────────────────
    if args.resume:
        print(f"从 {args.resume} 恢复训练")
        model = MaskablePPO.load(
            args.resume,
            env=train_env,
            tensorboard_log=str(log_dir),
            learning_rate=args.lr,  # ← 加这一行
        )
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

    # ── 回调 ─────────────────────────────────────────────────────
# Create curriculum callback first (if needed) so WinRateCallback can reference it
    curriculum_cb = None
    if args.curriculum:
        curriculum_cb = CurriculumCallback(
            stages=stages,
            win_thresholds=args.curriculum_thresholds,
            win_threshold=args.curriculum_threshold,
            window=200,
            verbose=1,
        )

    callback_list = [
        CheckpointCallback(
            save_freq=max(args.ckpt_freq // args.n_envs, 1),
            save_path=str(ckpt_dir),
            name_prefix="model",
            save_replay_buffer=False,
            save_vecnormalize=False,
        ),
        MaskableEvalCallback(
            eval_env,
            best_model_save_path=str(ckpt_dir / "best"),
            log_path=str(log_dir / "eval"),
            eval_freq=max(args.eval_freq // args.n_envs, 1),
            n_eval_episodes=args.eval_episodes,
            deterministic=True,
        ),
        WinRateCallback(
            check_freq=args.n_steps,
            window=100,
            verbose=1,
            curriculum_cb=curriculum_cb,
        ),
    ]

    if curriculum_cb is not None:
        callback_list.append(curriculum_cb)

    if args.self_play and opponent_pool is not None:
        self_play_cb = SelfPlayCallback(
            pool=opponent_pool,
            save_freq=max(args.self_play_save_freq // args.n_envs, 1),
            initial_basic_ai_prob=args.initial_basic_ai_prob,
            final_basic_ai_prob=args.final_basic_ai_prob,
            anneal_steps=args.timesteps,
            min_win_rate=args.min_save_win_rate,
            verbose=1,
        )
        callback_list.append(self_play_cb)

    callbacks = CallbackList(callback_list)

    # ── 训练 ─────────────────────────────────────────────────────
    print(f"开始训练: {run_name}")
    print(f"  对手数: {args.opponents}")
    if args.curriculum:
        thresholds_str = ', '.join(f"{t:.0%}" for t in curriculum_cb.win_thresholds)   # type: ignore
        print(f"  课程学习: 启用 ({' → '.join(str(s) for s in stages)}, 阈值 [{thresholds_str}])")
    if args.max_rounds is not None:
        print(f"  最大轮数: {args.max_rounds}（手动指定）")
    else:
        default_mr = (args.opponents + 1) * 50
        print(f"  最大轮数: {default_mr}（动态默认，{args.opponents + 1}人 × 50）")
    print(f"  总步数: {args.timesteps:,}")
    print(f"  并行环境: {args.n_envs}")
    print(f"  日志目录: {log_dir}")
    print(f"  检查点目录: {ckpt_dir}")
    print()

    sys.stderr.write("[TRAIN] starting learn...\n")
    sys.stderr.flush()

    model.learn(
        total_timesteps=args.timesteps,
        callback=callbacks,
        progress_bar=True,
    )

    sys.stderr.write("[TRAIN] learn done\n")
    sys.stderr.flush()

    # ── 保存最终模型 ─────────────────────────────────────────────
    final_path = ckpt_dir / "final_model"
    model.save(str(final_path))
    print(f"\n训练完成，最终模型已保存至 {final_path}")

    train_env.close()
    eval_env.close()




# ─────────────────────────────────────────────────────────────────────────────
#  CLI 参数
# ─────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Badtime War — MaskablePPO 训练脚本"
    )

    # 环境参数
    p.add_argument("--opponents", type=int, default=3,
                   help="对手数量 (1-5)")
    p.add_argument("--max-rounds", type=int, default=None,
                   help="每局最大轮数（默认：动态计算，多一个人多增加五十轮）")

    # 训练参数
    p.add_argument("--timesteps", type=int, default=1_000_000,
                   help="总训练步数")
    p.add_argument("--n-envs", type=int, default=1,
                   help="并行环境数（>1 时使用 SubprocVecEnv 多进程并行）")
    p.add_argument("--seed", type=int, default=42,
                   help="随机种子")

    # PPO 超参数
    p.add_argument("--lr", type=float, default=3e-4,
                   help="学习率")
    p.add_argument("--n-steps", type=int, default=2048,
                   help="每次 rollout 的步数")
    p.add_argument("--batch-size", type=int, default=64,
                   help="Mini-batch 大小")
    p.add_argument("--n-epochs", type=int, default=10,
                   help="每次更新的 epoch 数")
    p.add_argument("--gamma", type=float, default=0.99,
                   help="折扣因子")
    p.add_argument("--gae-lambda", type=float, default=0.95,
                   help="GAE lambda")
    p.add_argument("--clip-range", type=float, default=0.2,
                   help="PPO clip range")
    p.add_argument("--ent-coef", type=float, default=0.01,
                   help="熵系数（鼓励探索）")

    # 回调参数
    p.add_argument("--ckpt-freq", type=int, default=50_000,
                   help="Checkpoint 保存频率（步数）")
    p.add_argument("--eval-freq", type=int, default=10_000,
                   help="评估频率（步数）")
    p.add_argument("--eval-episodes", type=int, default=20,
                   help="每次评估的局数")

    # 恢复训练
    p.add_argument("--resume", type=str, default=None,
                   help="从已有模型恢复训练（.zip 路径）")
    # 帧堆叠
    p.add_argument("--n-stack", type=int, default=30,
                help="帧堆叠数量（1=不堆叠，30=GRU 处理最近30帧）")

    # 课程学习
    p.add_argument("--curriculum", action="store_true",
                help="启用课程学习（从1个对手逐步增加到 --opponents 个）")
    p.add_argument("--curriculum-threshold", type=float, default=0.55,
                help="课程升级胜率阈值（全局，如果未指定 --curriculum-thresholds）")
    p.add_argument("--curriculum-thresholds", type=float, nargs="+", default=None,
                help="每阶段课程升级胜率阈值（例如 0.55 0.40 表示两次升级的阈值）")

    # Self-play 参数
    p.add_argument("--self-play", action="store_true",
                help="启用 self-play 训练")
    p.add_argument("--seed-model", type=str, default=None,
                help="Self-play 种子模型路径（.zip）")
    p.add_argument("--pool-size", type=int, default=20,
                help="对手池最大模型数量")
    p.add_argument("--self-play-save-freq", type=int, default=500_000,
                help="Self-play 模型保存频率（步数）")
    p.add_argument("--initial-basic-ai-prob", type=float, default=0.5,
                help="Self-play 初始 BasicAI 混入概率")
    p.add_argument("--final-basic-ai-prob", type=float, default=0.1,
                help="Self-play 最终 BasicAI 混入概率")
    p.add_argument("--min-save-win-rate", type=float, default=0.45,
                help="Self-play 质量门控：胜率低于此值时不保存模型到对手池")

    #天赋选择参数
    p.add_argument("--rl-talent", type=int, default=None,
                help="RL 天赋编号（None=RL自选, 0=无天赋, 1-14=指定）")
    p.add_argument("--enable-talents", action="store_true", default=True,
                help="启用天赋系统")
    p.add_argument("--no-talents", action="store_false", dest="enable_talents",
                help="禁用天赋系统（无天赋局）")

    return p.parse_args()


# ─────────────────────────────────────────────────────────────────────────────
#  入口
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    train(parse_args())
