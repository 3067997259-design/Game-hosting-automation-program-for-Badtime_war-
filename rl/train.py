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
import os as _os
# SubprocVecEnv(start_method="spawn") 子进程继承父进程的环境变量。
# 必须在 import numpy/torch 之前设置，否则 BLAS 会用默认线程数初始化。
# 16 个子进程 × 32 默认线程 = 512 线程争抢 32 核，导致 load average > 260。
# setdefault 尊重用户显式设置的环境变量。
_os.environ.setdefault("OMP_NUM_THREADS", "1")
_os.environ.setdefault("MKL_NUM_THREADS", "1")
_os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

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
    force_random_talent: bool = False,
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
                    force_random_talent=force_random_talent,
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
        win_threshold: float | None = None,  # None = auto-compute
        window: int = 200,
        verbose: int = 0,
        ent_rebound_coef: float = 0.03,  # stage 升级时的 entropy 回弹值
        ent_rebound_decay_steps: int = 200_000,  # 回弹后衰减回原值的步数
        eval_env=None,
    ):
        super().__init__(verbose)
        self._eval_env = eval_env
        self.stages = stages
        self.ent_rebound_coef = ent_rebound_coef
        self.ent_rebound_decay_steps = ent_rebound_decay_steps
        self._rebound_start_step: int | None = None
        self._original_ent_coef: float | None = None
        # Per-stage thresholds: one per transition (len = len(stages) - 1)
        if win_thresholds and len(win_thresholds) == len(stages) - 1:
            self.win_thresholds = win_thresholds
        elif win_threshold is not None:
            # User provided a single global threshold — apply uniformly
            self.win_thresholds = [win_threshold] * (len(stages) - 1)
        else:
            # Auto-compute: decreasing thresholds based on player count
            # More opponents = lower threshold (random baseline is 1/(n+1))
            self.win_thresholds = []
            for i in range(len(stages) - 1):
                n_opponents = stages[i]
                random_baseline = 1.0 / (n_opponents + 1)
                # Threshold = random_baseline * 1.2 (20% above random)
                # 强制随机天赋时，RL 可能拿到被克制的天赋，1.5x 太高会卡住课程进度
                self.win_thresholds.append(random_baseline * 1.2)
        self.window = window
        self._current_stage = 0
        self._episode_wins: list[bool] = []

    def _on_step(self) -> bool:
        # Entropy 回弹衰减
        if self._rebound_start_step is not None and self._original_ent_coef is not None:
            elapsed = self.num_timesteps - self._rebound_start_step
            if elapsed >= self.ent_rebound_decay_steps:
                self.model.ent_coef = self._original_ent_coef
                self._rebound_start_step = None
                self._original_ent_coef = None
            else:
                # 线性衰减
                progress = elapsed / self.ent_rebound_decay_steps
                self.model.ent_coef = (
                    self.ent_rebound_coef * (1 - progress)
                    + self._original_ent_coef * progress
                )

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

                # Entropy 回弹：临时提高 ent_coef 鼓励重新探索
                # 仅在首次回弹时记录原始值，避免快速连续升级时覆盖真实原值
                if self._original_ent_coef is None:
                    self._original_ent_coef = self.model.ent_coef
                self.model.ent_coef = self.ent_rebound_coef
                self._rebound_start_step = self.num_timesteps

                if self.verbose >= 1:
                    print(f"  [Curriculum] 升级! 对手数: {new_opponents} (stage {self._current_stage}/{len(self.stages)-1}, win_rate={win_rate:.1%}, threshold={threshold:.1%})")
                    print(f"  [Curriculum] Entropy 回弹: ent_coef={self.ent_rebound_coef} (原值={self._original_ent_coef}, 衰减步数={self.ent_rebound_decay_steps})")

        return True

    def _update_envs(self, new_opponents: int):
            """更新所有子环境的对手数量（下次 reset 生效）。"""
            venv = self.training_env
            # env_method uses getattr which penetrates gym.Wrapper via __getattr__
            # set_attr does NOT penetrate wrappers (sets on Monitor, not BadtimeWarEnv)
            venv.env_method("set_num_opponents", new_opponents)
            # 同步更新评估环境
            if self._eval_env is not None:
                self._eval_env.env_method("set_num_opponents", new_opponents)

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
        win_rate_window: int = 200,
        verbose: int = 0,
        curriculum_cb=None,  # CurriculumCallback 引用，None 表示无课程（立即激活）
        max_opponents: int = 5,  # 最终对手数，用于动态计算入池阈值
        eval_env=None,  # 评估环境，用于坍塔检测
        collapse_threshold: float = 0.12,
        no_collapse_detection: bool = False,
        force_talent_cb=None,           # ForceRandomTalentCallback 引用
        talent_grace_steps: int = 2_000_000,   # 天赋自选学习期步数
        min_win_rate_random: float | None = None,  # 强制随机期阈值（默认 baseline × 1.1）
        min_win_rate_mature: float | None = None,  # 成熟期阈值（默认 baseline × 1.3）
    ):
        super().__init__(verbose)
        self.pool = pool
        self.save_freq = save_freq
        self.initial_basic_ai_prob = initial_basic_ai_prob
        self.final_basic_ai_prob = final_basic_ai_prob
        self.anneal_steps = anneal_steps
        random_baseline = 1.0 / (max_opponents + 1)
        if min_win_rate_random is None:
            self.min_win_rate_random = random_baseline * 1.1
        else:
            self.min_win_rate_random = min_win_rate_random
        if min_win_rate_mature is None:
            self.min_win_rate_mature = random_baseline * 1.3
        else:
            self.min_win_rate_mature = min_win_rate_mature
        self.force_talent_cb = force_talent_cb
        self.talent_grace_steps = talent_grace_steps
        self._talent_unlocked_step: int | None = None  # 记录天赋自选解锁的步数
        self.win_rate_window = win_rate_window
        self._episode_wins: list[bool] = []
        self._last_save_step: int = 0
        self.curriculum_cb = curriculum_cb
        self._activated = (curriculum_cb is None)  # 无课程时立即激活
        self._activation_step: int = 0  # 激活时的步数，用于退火计时

        # 坍塔检测
        self.collapse_eval_freq = save_freq * 2
        self.collapse_wr_threshold = collapse_threshold
        self.collapse_consecutive_fails = 0
        self.collapse_trigger_count = 3
        self._collapse_active = False
        self._collapse_original_ent_coef: float | None = None
        self._collapse_original_basic_ai_prob: float | None = None
        self._collapse_enter_step: int = 0
        self._collapse_min_duration: int = 1_000_000  # 坍塌模式最少持续 1M 步
        self._collapse_recovery_count: int = 0
        self._collapse_recovery_target: int = 3  # 连续 3 次达标才退出
        self._eval_env = eval_env if not no_collapse_detection else None

    @property
    def min_win_rate(self) -> float:
        """动态入池阈值：强制随机期/学习期用低阈值，成熟期用高阈值。"""
        # 阶段 1：强制随机天赋仍在生效
        if self.force_talent_cb is not None and not self.force_talent_cb._switched:
            return self.min_win_rate_random

        # 阶段 2：学习期（解锁后 N 步内）——解锁时间由 _on_step 记录
        if (self._talent_unlocked_step is not None
                and self.num_timesteps - self._talent_unlocked_step < self.talent_grace_steps):
            return self.min_win_rate_random

        # 阶段 3：成熟期（或无 force_talent_cb 时直接使用成熟期阈值）
        return self.min_win_rate_mature

    def _on_step(self) -> bool:
        # 记录天赋自选解锁时间点（每步检查，确保在课程激活前也能记录）
        if (self.force_talent_cb is not None
                and self.force_talent_cb._switched
                and self._talent_unlocked_step is None):
            self._talent_unlocked_step = self.num_timesteps

        # 课程模式下，等待最终阶段才激活
        if not self._activated:
            if (self.curriculum_cb is not None
                and self.curriculum_cb._current_stage >= len(self.curriculum_cb.stages) - 1):
                self._activated = True
                self._activation_step = self.num_timesteps
                self._episode_wins.clear()  # 清空课程阶段积累的无效数据
                if self.verbose >= 1:
                    print(f"  [SelfPlay] 激活! 课程已到最终阶段 (step {self.num_timesteps})")
                    print(f"  [SelfPlay] 入池阈值: 随机期={self.min_win_rate_random:.1%}, 成熟期={self.min_win_rate_mature:.1%}")
            else:
                return True  # 课程未到最终阶段，跳过全部自对弈逻辑

        # ── 1. 收集胜率数据 + ELO 更新 ──
        infos = self.locals.get("infos", [])
        for info in infos:
            ep_info = info.get("episode")
            if ep_info is not None:
                winner = info.get("winner")
                rl_won = (winner == "rl_0")
                self._episode_wins.append(rl_won)
                # 主进程更新 ELO（从 info dict 接收对手标识）
                opp_stems = info.get("opponent_stems")
                if opp_stems:
                    for stem in opp_stems:
                        self.pool.update_elo("rl_current", stem, rl_won)

        # ── 2. 定期检查是否保存 + 退火 ──
        if self.n_calls % self.save_freq == 0:
            # 2a. 计算当前胜率
            current_win_rate = None
            if len(self._episode_wins) >= self.win_rate_window:
                recent = self._episode_wins[-self.win_rate_window:]
                current_win_rate = sum(recent) / len(recent)

            # 2b. 质量门控：只有胜率达标才保存（坍塔恢复模式下跳过保存）
            saved = False
            if not self._collapse_active and current_win_rate is not None and current_win_rate >= self.min_win_rate:
                self.pool.save_current_model(self.model, self.num_timesteps, eval_win_rate=current_win_rate)
                self._last_save_step = self.num_timesteps
                saved = True

            # 2c. 退火（坍塌恢复模式下跳过，保持 0.7）
            if not self._collapse_active:
                elapsed = self.num_timesteps - self._activation_step
                progress = min(elapsed / self.anneal_steps, 1.0)
                new_prob = self.initial_basic_ai_prob + (
                    self.final_basic_ai_prob - self.initial_basic_ai_prob
                ) * progress
                self.pool.basic_ai_prob = new_prob
                self.training_env.env_method("update_basic_ai_prob", new_prob)

            # 2d. 日志
            if self.verbose >= 1:
                n_models = len(self.pool.get_available_models())
                wr_str = f"{current_win_rate:.1%}" if current_win_rate is not None else f"N/A (< {self.win_rate_window} episodes)"
                threshold = self.min_win_rate
                phase = "随机天赋" if (self.force_talent_cb and not self.force_talent_cb._switched) else \
                        "自选学习期" if (self._talent_unlocked_step is not None and self.num_timesteps - self._talent_unlocked_step < self.talent_grace_steps) else \
                        "自选成熟期"
                save_str = "SAVED" if saved else f"SKIPPED (need >= {threshold:.0%}, {phase})"
                prob_str = f"{self.pool.basic_ai_prob:.1%}"
                collapse_str = ""
                if self._collapse_active:
                    collapse_str = " | COLLAPSE MODE"
                elif n_models < 2:
                    collapse_str = " | 坍塌检测: 未激活(需≥2模型)"
                print(
                    f"  [SelfPlay] Step {self.num_timesteps} | "
                    f"Win rate: {wr_str} | {save_str} | "
                    f"Pool size: {n_models} | BasicAI prob: {prob_str}"
                    + collapse_str
                )

        # ── 3. 坍塔检测 ──
        if self.n_calls % self.collapse_eval_freq == 0 and self._eval_env is not None:
            pool_size = len(self.pool.get_available_models())
            collapse_eligible = pool_size >= 2
            if not collapse_eligible:
                return True
            # 先清空累积的旧结果（可能由 MaskableEvalCallback 共享 eval_env 产生）
            self._eval_env.env_method("get_episode_outcomes")
            from sb3_contrib.common.maskable.evaluation import evaluate_policy
            evaluate_policy(
                self.model, self._eval_env, n_eval_episodes=20,
                deterministic=True, use_masking=True,
            )
            eval_win_rate = self._compute_eval_win_rate()

            if eval_win_rate is not None and eval_win_rate < self.collapse_wr_threshold:
                self.collapse_consecutive_fails += 1
                if self._collapse_active:
                    self._collapse_recovery_count = 0
                if self.verbose:
                    print(f"  [SelfPlay] \u26a0\ufe0f 坍塔预警 ({self.collapse_consecutive_fails}/{self.collapse_trigger_count}): "
                          f"eval win rate = {eval_win_rate:.1%} < {self.collapse_wr_threshold:.1%}")
            else:
                self.collapse_consecutive_fails = 0
                if self._collapse_active and eval_win_rate is not None and eval_win_rate > self.collapse_wr_threshold * 1.5:
                    self._collapse_recovery_count += 1
                    duration = self.num_timesteps - self._collapse_enter_step
                    if (self._collapse_recovery_count >= self._collapse_recovery_target
                            and duration >= self._collapse_min_duration):
                        self._exit_collapse_mode()
                    elif self.verbose:
                        print(f"  [SelfPlay] 坍塌恢复中 ({self._collapse_recovery_count}/{self._collapse_recovery_target}), "
                              f"已持续 {duration} 步 (最低 {self._collapse_min_duration})")
                elif self._collapse_active and eval_win_rate is not None:
                    self._collapse_recovery_count = 0

            if self.collapse_consecutive_fails >= self.collapse_trigger_count and not self._collapse_active:
                self._enter_collapse_mode()

        return True

    def _compute_eval_win_rate(self) -> float | None:
        """从评估环境收集胜率。"""
        if self._eval_env is None:
            return None
        try:
            infos = self._eval_env.env_method("get_episode_outcomes")
            wins = 0
            total = 0
            for env_outcomes in infos:
                if env_outcomes is not None:
                    for outcome in env_outcomes:
                        total += 1
                        if outcome == "rl_0":
                            wins += 1
            if total == 0:
                return None
            return wins / total
        except Exception:
            return None

    def _enter_collapse_mode(self):
        """进入坍塔恢复模式：暂停入池、提高 BasicAI 概率、提高熵系数"""
        self._collapse_active = True
        self._collapse_enter_step = self.num_timesteps
        self._collapse_recovery_count = 0
        self._collapse_original_ent_coef = self.model.ent_coef
        self._collapse_original_basic_ai_prob = self.pool.basic_ai_prob

        self.model.ent_coef = max(self.model.ent_coef * 2, 0.05)
        self.pool.basic_ai_prob = 0.7
        self.training_env.env_method("update_basic_ai_prob", 0.7)

        if self.verbose:
            print(f"  [SelfPlay] \U0001f6a8 策略坍塔检测触发! "
                  f"ent_coef: {self._collapse_original_ent_coef} \u2192 {self.model.ent_coef}, "
                  f"BasicAI prob: {self._collapse_original_basic_ai_prob:.1%} \u2192 70%")

    def _exit_collapse_mode(self):
        """退出坍塔恢复模式：恢复原始参数"""
        if self._collapse_original_ent_coef is not None:
            self.model.ent_coef = self._collapse_original_ent_coef

        # basic_ai_prob 恢复到退火当前值（不是坍塔前的值）
        elapsed = self.num_timesteps - self._activation_step
        progress = min(elapsed / self.anneal_steps, 1.0)
        normal_prob = self.initial_basic_ai_prob + (
            self.final_basic_ai_prob - self.initial_basic_ai_prob
        ) * progress
        self.pool.basic_ai_prob = normal_prob
        self.training_env.env_method("update_basic_ai_prob", normal_prob)

        self._collapse_active = False
        self.collapse_consecutive_fails = 0
        if self.verbose:
            print(f"  [SelfPlay] \u2705 坍塔恢复完成，参数已恢复")


# ─────────────────────────────────────────────────────────────────────────────
#  强制随机天赋回调
# ─────────────────────────────────────────────────────────────────────────────


class ForceRandomTalentCallback(BaseCallback):
    """在指定步数之前强制随机分配天赋，之后恢复为 rl_talent 参数控制。"""

    def __init__(self, until_step: int, verbose: int = 0):
        super().__init__(verbose)
        self.until_step = until_step
        self._switched = False

    def _on_step(self) -> bool:
        if not self._switched and self.num_timesteps >= self.until_step:
            self.training_env.env_method("set_force_random_talent", False)
            self._switched = True
            if self.verbose:
                print(f"[ForceRandomTalent] 步数 {self.num_timesteps} 达到阈值 "
                      f"{self.until_step}，关闭强制随机天赋")
        return True


# ─────────────────────────────────────────────────────────────────────────────
#  训练主函数
# ─────────────────────────────────────────────────────────────────────────────

def train(args: argparse.Namespace):
    """执行训练流程。"""
    import sys
    import torch

    sys.stderr.write(f"[TRAIN] args: {args}\n")
    sys.stderr.flush()

    # ── 性能设置 ──────────────────────────────────────────────────
    torch.set_num_threads(min(8, os.cpu_count() or 1))

    # ── 路径设置 ──────────────────────────────────────────────────
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_name = f"maskable_ppo_{args.opponents}opp_{timestamp}"
    log_dir = Path("logs") / run_name
    ckpt_dir = Path("checkpoints") / run_name
    log_dir.mkdir(parents=True, exist_ok=True)
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    if args.curriculum:
        stages = list(range(args.curriculum_start, args.opponents + 1))  # 从 curriculum_start 开始，跳过 1v1
        if not stages:
            print(f"Warning: curriculum_start ({args.curriculum_start}) > opponents ({args.opponents}), disabling curriculum.")
            stages = [args.opponents]
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
            sys.stderr.write("[TRAIN] seed model saved to opponent pool\n")
            sys.stderr.flush()
# ── 训练环境 ──────────────────────────────────────────────────
    force_random = args.force_random_talent_until > 0
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
            force_random_talent=force_random,
        )
        for i in range(args.n_envs)
    ]
    sys.stderr.write(f"[TRAIN] creating train env (n_envs={args.n_envs}, method=spawn)...\n")
    sys.stderr.flush()
    if args.n_envs > 1:
        train_env = SubprocVecEnv(env_fns, start_method="spawn")   # type: ignore
    else:
        train_env = DummyVecEnv(env_fns) # type: ignore
    sys.stderr.write(f"[TRAIN] train env created successfully\n")
    sys.stderr.flush()

    # ── 评估环境（多进程并行）──────────────────────────────────────
    n_eval_envs = args.n_eval_envs if args.n_eval_envs is not None else args.n_envs
    n_eval_envs = min(n_eval_envs, args.eval_episodes)  # 不超过评估局数
    n_eval_envs = max(n_eval_envs, 1)  # 至少 1 个

    eval_env_fns = [
        make_env(
            num_opponents=initial_opponents if args.curriculum else args.opponents,
            max_rounds=args.max_rounds,
            seed=args.seed + 1000,
            rank=i,
            n_stack=args.n_stack,
            rl_talent=args.rl_talent,
            enable_talents=args.enable_talents,
        )
        for i in range(n_eval_envs)
    ]
    sys.stderr.write(f"[TRAIN] creating eval env (n_eval_envs={n_eval_envs}, method=spawn)...\n")
    sys.stderr.flush()
    if n_eval_envs > 1:
        eval_env = SubprocVecEnv(eval_env_fns, start_method="spawn")
    else:
        eval_env = DummyVecEnv(eval_env_fns)
    sys.stderr.write(f"[TRAIN] eval env created successfully\n")
    sys.stderr.flush()

    # ── 模型 ─────────────────────────────────────────────────────
    sys.stderr.write(f"[TRAIN] resume={args.resume}, preparing model...\n")
    sys.stderr.flush()
    if args.resume:
        print(f"从 {args.resume} 恢复训练")
        model = MaskablePPO.load(
            args.resume,
            env=train_env,
            tensorboard_log=str(log_dir),
            learning_rate=args.lr,
            device=args.device
        )
        # ── 覆盖超参数（.load() 会还原保存时的值，需要用命令行参数覆盖）──
        model.n_steps = args.n_steps
        model.batch_size = args.batch_size
        model.n_epochs = args.n_epochs
        model.ent_coef = args.ent_coef
        model.gamma = args.gamma
        model.gae_lambda = args.gae_lambda
        model.clip_range = lambda _: args.clip_range
        print(f"  超参数已覆盖: n_steps={args.n_steps}, batch_size={args.batch_size}, "
              f"n_epochs={args.n_epochs}, ent_coef={args.ent_coef}")
        sys.stderr.write("[TRAIN] model loaded and hyperparams overridden\n")
        sys.stderr.flush()
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
            ent_rebound_coef=args.ent_rebound,
            ent_rebound_decay_steps=args.ent_rebound_decay,
            eval_env=eval_env,
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

    force_talent_cb = None
    if args.force_random_talent_until > 0:
        force_talent_cb = ForceRandomTalentCallback(
            until_step=args.force_random_talent_until,
            verbose=1,
        )
        callback_list.append(force_talent_cb)

    if args.self_play and opponent_pool is not None:
        self_play_cb = SelfPlayCallback(
            pool=opponent_pool,
            save_freq=max(args.self_play_save_freq // args.n_envs, 1),
            initial_basic_ai_prob=args.initial_basic_ai_prob,
            final_basic_ai_prob=args.final_basic_ai_prob,
            anneal_steps=args.timesteps,
            verbose=1,
            curriculum_cb=curriculum_cb,
            max_opponents=args.opponents,
            eval_env=eval_env,
            collapse_threshold=args.collapse_threshold,
            no_collapse_detection=args.no_collapse_detection,
            force_talent_cb=force_talent_cb,
            talent_grace_steps=args.talent_grace_steps,
        )
        callback_list.append(self_play_cb)

    callbacks = CallbackList(callback_list)
    sys.stderr.write(f"[TRAIN] all callbacks created ({len(callback_list)} callbacks)\n")
    sys.stderr.flush()

    # ── 训练 ─────────────────────────────────────────────────────
    print(f"开始训练: {run_name}")
    print(f"  对手数: {args.opponents}")
    if args.curriculum:
        thresholds_str = ', '.join(f"{t:.0%}" for t in curriculum_cb.win_thresholds)   # type: ignore
        print(f"  课程学习: 启用 ({' → '.join(str(s) for s in stages)}, 阈值 [{thresholds_str}])")
    if args.max_rounds is None:
        if args.curriculum:
            print(f"  最大轮数: 动态（按当前课程人数×50 计算，范围 {(args.curriculum_start+1)*50} ~ {(args.opponents+1)*50}）")
        else:
            default_mr = (args.opponents + 1) * 50
            print(f"  最大轮数: {default_mr}（动态默认，{args.opponents + 1}人 × 50）")
    else:
        print(f"  最大轮数: {args.max_rounds}（手动指定，课程模式下不建议）")
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
    p.add_argument("--batch-size", type=int, default=256,
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
    p.add_argument("--eval-freq", type=int, default=50_000,
                   help="评估频率（步数）")
    p.add_argument("--eval-episodes", type=int, default=20,
                   help="每次评估的局数")
    p.add_argument("--n-eval-envs", type=int, default=None,
                help="评估环境并行数（默认=--n-envs，上限=--eval-episodes）")

    # 恢复训练
    p.add_argument("--resume", type=str, default=None,
                   help="从已有模型恢复训练（.zip 路径）")
    # 帧堆叠
    p.add_argument("--n-stack", type=int, default=30,
                help="帧堆叠数量（1=不堆叠，30=GRU 处理最近30帧）")

    # 课程学习
    p.add_argument("--curriculum", action="store_true",
                help="启用课程学习（从 --curriculum-start 个对手逐步增加到 --opponents 个）")
    p.add_argument("--curriculum-start", type=int, default=2,
                help="课程学习起始对手数（默认2，跳过1v1）")
    p.add_argument("--curriculum-threshold", type=float, default=None,
                help="课程升级胜率阈值（全局，如果未指定则自动计算）")
    p.add_argument("--curriculum-thresholds", type=float, nargs="+", default=None,
                help="每阶段课程升级胜率阈值（例如 0.55 0.40 表示两次升级的阈值）")
    p.add_argument("--ent-rebound", type=float, default=0.03,
                help="课程升级时的 entropy 回弹系数")
    p.add_argument("--ent-rebound-decay", type=int, default=200_000,
                help="entropy 回弹衰减步数")

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
    p.add_argument("--final-basic-ai-prob", type=float, default=0.3,
                help="Self-play 最终 BasicAI 混入概率（不低于0.3，确保策略多样性）")
    p.add_argument("--collapse-threshold", type=float, default=0.12,
                help="坍塌检测胜率阈值（低于此值视为坍塌，默认0.12）")
    p.add_argument("--no-collapse-detection", action="store_true",
                help="禁用策略坍塌检测")

    # 天赋选择参数
    p.add_argument("--rl-talent", type=int, default=None,
                help="RL 天赋编号（None=RL自选, 0=无天赋, 1-14=指定）")
    p.add_argument("--enable-talents", action="store_true", default=True,
                help="启用天赋系统")
    p.add_argument("--no-talents", action="store_false", dest="enable_talents",
                help="禁用天赋系统（无天赋局）")
    p.add_argument("--force-random-talent-until", type=int, default=0,
                help="在此步数之前强制随机分配天赋（0=不强制，由 --rl-talent 控制）")
    p.add_argument("--talent-grace-steps", type=int, default=2_000_000,
                help="天赋自选解锁后的学习期步数（期间入池阈值保持低值）")

    # 设备支持参数
    p.add_argument("--device", type=str, default="auto",
               help="训练设备（auto/cpu/cuda）")



    return p.parse_args()


# ─────────────────────────────────────────────────────────────────────────────
#  入口
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    train(parse_args())
