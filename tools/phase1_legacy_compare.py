"""
tools/phase1_legacy_compare.py
──────────────────────────────
Phase 1 —— legacy 真环境双管线对照训练。

在 legacy 层（默认稳定口径）上，用完全相同的环境/种子/预算对比两条训练路线：

  [A] baseline（老成果路线）：sb3_contrib.MaskablePPO + GRUFeatureExtractor + n_stack=30
  [B] recurrent（新路线）：   rl.maskable_recurrent.MaskableRecurrentPPO(MlpLstmPolicy) + n_stack=1

两者共用同一套 env 工厂（BadtimeWarEnv，legacy profile，RL 席位无天赋、对手为
BasicAI 随机人格）、同一总步数预算与同一随机种子，按固定间隔对"全 BasicAI 对手"
做确定性评估，输出各自胜率曲线并对比。

用法（仓库根目录）：
    # 小规模 pilot（分钟级，验证机制）
    python tools/phase1_legacy_compare.py --steps 2400 --n-envs 2 --n-steps 128 \
        --eval-every 800 --eval-episodes 8 --seed 0

    # 完整对照（小时级预算，建议后台跑）
    python tools/phase1_legacy_compare.py --steps 50000 --n-envs 4 --n-steps 256 \
        --eval-every 5000 --eval-episodes 20 --seed 42
"""

from __future__ import annotations

import argparse
import os as _os
import sys
import time
from pathlib import Path

# 允许从仓库根直接 `python tools/phase1_legacy_compare.py` 运行
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ── 线程抑制（必须在 import numpy/torch 之前）──────────────────────────────
# RL 每决策都是单样本小网络 forward，torch 默认 intra-op 线程开满核（≈逻辑核数），
# 单样本小矩阵的线程同步开销 ≥ 并行收益，叠加多个游戏线程并发 → 线程过度订阅、
# 吞吐骤降。OMP/MKL/OPENBLAS 指定 1，训练/评估进一步用 torch.set_num_threads 限核。
_os.environ.setdefault("OMP_NUM_THREADS", "1")
_os.environ.setdefault("MKL_NUM_THREADS", "1")
_os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import numpy as np

import torch as th  # noqa: F401  (确保 torch 就绪，便于统一报错)

from engine.experiments import set_profile
from rl.env import BadtimeWarEnv
from rl.obs_builder import OBS_DIM

# 训练路线 A（基线）：MaskablePPO + GRU
from sb3_contrib import MaskablePPO
from rl.feature_extractor import GRUFeatureExtractor
# 训练路线 B（新）：MaskableRecurrentPPO
from rl.maskable_recurrent import MaskableRecurrentPPO


# ─────────────────────────────────────────────────────────────────────────────
#  环境工厂（legacy，RL 席位无天赋；对手 BasicAI 随机人格）
# ─────────────────────────────────────────────────────────────────────────────

def make_env(num_opponents: int, n_stack: int, seed: int, max_rounds: int | None = None):
    """返回一个创建 BadtimeWarEnv 的闭包（n_stack 决定 obs 是单帧还是 30 帧堆叠）。"""
    def _init() -> BadtimeWarEnv:
        env = BadtimeWarEnv(
            num_opponents=num_opponents,
            max_rounds=max_rounds,
            n_stack=n_stack,
            enable_talents=True,
            rl_talent=0,     # RL 席位：无天赋（隔离网络/算法差异，避免天赋选择噪声）
        )
        env.reset(seed=seed)
        return env
    return _init


def policy_kwargs_recurrent(hidden: int) -> dict:
    """MaskableRecurrentPPO 的策略 kwargs：LSTM 隐藏 + 末端 MLP。"""
    return dict(
        lstm_hidden_size=hidden,
        enable_critic_lstm=True,
        net_arch=dict(pi=[hidden], vf=[hidden]),
    )


def policy_kwargs_baseline(hidden: int, n_stack: int) -> dict:
    """MaskablePPO 基线的策略 kwargs：GRU(30 帧窗口) + 末端 MLP（老成果路线）。"""
    return dict(
        features_extractor_class=GRUFeatureExtractor,
        features_extractor_kwargs=dict(
            gru_hidden_size=hidden,
            proj_size=hidden,
            num_layers=1,
        ),
        net_arch=dict(pi=[hidden], vf=[hidden]),
    )


# ─────────────────────────────────────────────────────────────────────────────
#  确定性胜率评估：RL 席位 vs 全 BasicAI 对手
#  单一信源：rl/winrate_eval.py（带状态线 eval，train.py 同款）
# ─────────────────────────────────────────────────────────────────────────────

from rl.winrate_eval import evaluate_winrate


# ─────────────────────────────────────────────────────────────────────────────
#  单路线训练 + 曲线采样
# ─────────────────────────────────────────────────────────────────────────────

def train_and_measure(
    *,
    recurrent: bool,
    steps: int,
    n_envs: int,
    n_steps: int,
    batch_size: int,
    hidden: int,
    eval_every: int,
    eval_episodes: int,
    seed: int,
    out_dir: Path,
    curriculum: bool = False,
    curriculum_stages: list[int] | None = None,
    curriculum_window: int = 200,
    curriculum_threshold: float | None = None,
    curriculum_force_after: int | None = None,
) -> tuple[list[tuple[int, float]], float, Path, int]:
    """训练一条路线并记录 (step, win_rate) 曲线。

    返回 (curve, steps_per_sec, model_path, final_opponents)。
    课程学习启用时：从 stages[0] 个对手起步，按 CurriculumCallback（复用
    rl/train.py 的旧协议：随机基线×1.2 阈值 + 升级熵回弹）在训练/评估间
    同步推进对手数；评估曲线跟随当前 stage。
    """
    from stable_baselines3.common.vec_env import DummyVecEnv
    from stable_baselines3.common.callbacks import BaseCallback

    n_stack = 1 if recurrent else 30
    initial_opponents = (
        curriculum_stages[0] if (curriculum and curriculum_stages) else 2
    )
    # 供 EvalCurve 与课程回调共享的"当前 stage"同步点
    stage_sync: dict[str, object] = {"opponents": initial_opponents, "stage": 0}
    name = "recurrent" if recurrent else "baseline"
    print(f"\n===== [{'B' if recurrent else 'A'}] {name} "
          f"(n_stack={n_stack}, n_envs={n_envs}, steps={steps}, "
          f"起始对手数={initial_opponents}) =====")

    venv = DummyVecEnv(
        [make_env(num_opponents=initial_opponents, n_stack=n_stack, seed=seed + i)
         for i in range(n_envs)]
    )

    common = dict(
        n_steps=n_steps,
        batch_size=batch_size,
        n_epochs=4,
        gamma=0.99,
        gae_lambda=0.95,
        clip_range=0.2,
        ent_coef=0.01,
        seed=seed,
        verbose=0,
        device="cpu",
    )
    if recurrent:
        model = MaskableRecurrentPPO(
            "MlpLstmPolicy", venv,
            policy_kwargs=policy_kwargs_recurrent(hidden),
            **common,
        )
    else:
        model = MaskablePPO(
            "MlpPolicy", venv,
            policy_kwargs=policy_kwargs_baseline(hidden, n_stack),
            **common,
        )

    curve: list[tuple[int, float]] = []
    t0 = time.time()

    # 课程学习回调：复用旧管线 CurriculumCallback，并桥接 stage_sync
    if curriculum and curriculum_stages:
        from rl.train import CurriculumCallback

        class _SyncCurriculum(CurriculumCallback):
            """CurriculumCallback + 两处扩展：

            1) 升级时同步 stage_sync（评估脚本据此切换评估局对手数）；
            2) force_after：dev/test 旋钮，到达该时间步强制升级一次
               （绕过胜率门槛，用来验证 set_num_opponents 传播与评估切换的
               整条链路；正式跑不设它）。
            """

            def __init__(self, *, force_after: int | None = None, **kw):
                super().__init__(**kw)
                self.force_after = force_after
                self._forced = False

            def _update_envs(self, new_opponents: int) -> None:
                super()._update_envs(new_opponents)
                stage_sync["opponents"] = new_opponents
                stage_sync["stage"] = self._current_stage
                print(f"    [curriculum] 升级 stage={self._current_stage} "
                      f"→ 对手数 {new_opponents}")

            def _on_step(self) -> bool:
                if (self.force_after is not None and not self._forced
                        and self.num_timesteps >= self.force_after
                        and self._current_stage < len(self.stages) - 1):
                    self._forced = True
                    self._current_stage += 1
                    self._update_envs(self.stages[self._current_stage])
                    self._episode_wins.clear()
                    print(f"    [curriculum] ⚙ force_after={self.force_after} "
                          f"强制推进 stage={self._current_stage}")
                return super()._on_step()

        curriculum_cb: BaseCallback | None = _SyncCurriculum(
            stages=list(curriculum_stages),
            win_threshold=curriculum_threshold,  # None = 自动（随机基线×1.2）
            window=curriculum_window,
            verbose=1,
            eval_env=None,
            force_after=curriculum_force_after,
        )
    else:
        curriculum_cb = None

    # 用 on-step 回调按 eval_every 采样胜率（训练主循环内执行，代价可控）
    def _current_opponents() -> int:
        return int(stage_sync["opponents"])

    class _EvalCurve(BaseCallback):
        def __init__(self, model_ref, eval_every_, eval_episodes_, seed_):
            super().__init__(verbose=0)
            self.model_ref = model_ref
            self.eval_every_ = eval_every_
            self.eval_episodes_ = eval_episodes_
            self.seed_ = seed_
            self.next_eval = eval_every_  # 按真实总时间步触发的下个评估点

        def _on_step(self) -> bool:
            ts = self.num_timesteps  # 算法累计时间步（= VecEnv.step × n_envs）
            if ts >= self.next_eval:
                self.next_eval += self.eval_every_
                wr = evaluate_winrate(
                    self.model_ref,
                    num_opponents=_current_opponents(),
                    n_stack=n_stack,
                    n_episodes=self.eval_episodes_,
                    seed=9999 + self.seed_,
                    recurrent=recurrent,
                )
                curve.append((ts, wr))
                stage_txt = (f"，stage={stage_sync['stage']}, 对手={_current_opponents()}"
                             if curriculum_cb else "")
                print(f"    [step {ts}] win_rate={wr:.0%}"
                      f"（eval {self.eval_episodes_} 局，deterministic){stage_txt}")
            return True

    callbacks: list = [_EvalCurve(model, eval_every, eval_episodes, seed)]
    if curriculum_cb is not None:
        callbacks.append(curriculum_cb)

    model.learn(
        total_timesteps=steps,
        callback=callbacks,
        progress_bar=False,
    )
    final_opponents = _current_opponents()
    # 训练结束：确保包含 "终局胜率"（与步数标记一致，避免不同步触发点漏采）
    if not curve or curve[-1][0] != steps:
        wr = evaluate_winrate(
            model,
            num_opponents=final_opponents,
            n_stack=n_stack,
            n_episodes=eval_episodes,
            seed=9999 + seed,
            recurrent=recurrent,
        )
        curve.append((steps, wr))
        final_txt = f"，对手={final_opponents}" if curriculum_cb else ""
        print(f"    [step {steps}] win_rate={wr:.0%}（终局{final_txt}）")
    elapsed = time.time() - t0
    sps = steps / max(elapsed, 1e-9)

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}_seed{seed}"
    model.save(str(path))
    model.env.close()
    print(f"    [{('B' if recurrent else 'A')}] 完成：{steps} 步 / {elapsed:.1f}s "
          f"（{sps:.1f} 步/s）；模型 → {path}.zip")
    return curve, sps, path, final_opponents


# ─────────────────────────────────────────────────────────────────────────────
#  入口
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 1 legacy 双管线对照（含课程学习）")
    parser.add_argument("--steps", type=int, default=2400, help="每路线总步数")
    parser.add_argument("--n-envs", type=int, default=2, help="并行环境数（DummyVecEnv）")
    parser.add_argument("--n-steps", type=int, default=128, help="每次更新 rollout 步数")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--hidden", type=int, default=64, help="LSTM/GRU 隐藏维与末端 MLP 宽")
    parser.add_argument("--eval-every", type=int, default=800)
    parser.add_argument("--eval-episodes", type=int, default=8)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", type=str, default="checkpoints/phase1")
    parser.add_argument("--torch-threads", type=int, default=2,
                        help="torch intra-op 线程数（防线程过度订阅；单样本 forward 建议 1-2）")
    parser.add_argument("--no-baseline", action="store_true", help="只跑 recurrent（跳过 A）")
    # ── 课程学习 ──
    parser.add_argument("--opponents", type=int, default=2,
                        help="固定模式下的对手数（课程模式的最终上限参考）")
    parser.add_argument("--curriculum", action="store_true",
                        help="启用课程学习（复用旧管线 CurriculumCallback：升级阈值=随机基线×1.2）")
    parser.add_argument("--curriculum-stages", type=int, nargs="+", default=None,
                        help="课程阶段（各阶段对手数），如：2 3；缺省 [2, <--opponents> 或 2]")
    parser.add_argument("--curriculum-window", type=int, default=200,
                        help="课程升级判定的局数窗口（旧管线默认 200）")
    parser.add_argument("--curriculum-threshold", type=float, default=None,
                        help="课程升级胜率阈值（缺省自动=随机基线×1.2）")
    parser.add_argument("--curriculum-force-after", type=int, default=None,
                        help="dev/test：到达该时间步强制升级一次（验证课程链路，正式跑勿用）")
    args = parser.parse_args()

    # 必须在构建 env 之前固定 profile：legacy 稳定层
    set_profile("legacy")
    th.set_num_threads(args.torch_threads)
    print(f"torch intra-op threads = {th.get_num_threads()}"
          f"（防线程过度订阅；默认 14 → 抑制为 {args.torch_threads}）")
    cur_stages: list[int] | None = None
    if args.curriculum:
        cur_stages = args.curriculum_stages or (
            sorted({2, args.opponents}) if args.opponents > 2 else [2]
        )
    cur_ctx = (f"课程={cur_stages}（window={args.curriculum_window}, "
               f"threshold={args.curriculum_threshold or 'auto'}）"
               if cur_stages else "固定对手数=2")
    print(f"profile=legacy | seed={args.seed} | steps/路线 = {args.steps} | {cur_ctx}")
    print(f"OBS_DIM={OBS_DIM}（recurrent 单帧 539；baseline 30 帧堆叠 {OBS_DIM * 30}）")

    curriculum_kw = dict(
        curriculum=args.curriculum,
        curriculum_stages=cur_stages,
        curriculum_window=args.curriculum_window,
        curriculum_threshold=args.curriculum_threshold,
        curriculum_force_after=args.curriculum_force_after,
    )

    out_dir = Path(args.out)
    path_b: Path | None = None
    # label → (curve, final_opponents, steps_per_sec)
    curves: dict[str, tuple[list[tuple[int, float]], int, float]] = {}
    if not args.no_baseline:
        cb, sps_b, path_b, fin_b = train_and_measure(
            recurrent=False,
            steps=args.steps, n_envs=args.n_envs, n_steps=args.n_steps,
            batch_size=args.batch_size, hidden=args.hidden,
            eval_every=args.eval_every, eval_episodes=args.eval_episodes,
            seed=args.seed, out_dir=out_dir, **curriculum_kw,
        )
        curves["A baseline(MaskablePPO+GRU,n_stack=30)"] = (cb, fin_b, sps_b)

    cr, sps_r, path_r, fin_r = train_and_measure(
        recurrent=True,
        steps=args.steps, n_envs=args.n_envs, n_steps=args.n_steps,
        batch_size=args.batch_size, hidden=args.hidden,
        eval_every=args.eval_every, eval_episodes=args.eval_episodes,
        seed=args.seed, out_dir=out_dir, **curriculum_kw,
    )
    curves["B recurrent(MaskableRecurrentPPO,n_stack=1)"] = (cr, fin_r, sps_r)

    # ── 对照表 ──
    print("\n" + "=" * 82)
    print("Phase 1b 对照（legacy 层，同 seed / 同预算 / 同一课程协议 / 同一 eval 协议）")
    print("=" * 82)
    print(f"{'路线':<40}{'步/s':>11}{'最终胜率':>13}{'终局对手数':>12}")
    for label, (c, fin_opp, sps_) in curves.items():
        final_wr = c[-1][1] if c else float("nan")
        print(f"{label:<40}{sps_:>11.1f}{final_wr:>12.1%}{fin_opp:>12d}")
    print("\n胜率曲线（step → win_rate）：")
    for label, (c, fin_opp, _sps) in curves.items():
        print(f"  {label}[终局对手 {fin_opp}]: "
              + " -> ".join(f"{s}:{w:.0%}" for s, w in c))
    saved = " / ".join(str(p) + ".zip" for p in (path_b, path_r) if p is not None)
    print(f"\n模型已保存：{saved}")
    print("=" * 82)
    return 0


if __name__ == "__main__":
    sys.exit(main())
