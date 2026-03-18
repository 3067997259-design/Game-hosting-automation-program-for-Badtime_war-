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
  
  
# ─────────────────────────────────────────────────────────────────────────────  
#  环境工厂  
# ─────────────────────────────────────────────────────────────────────────────  
  
def make_env(  
    num_opponents: int = 3,  
    max_rounds: int = 50,  
    seed: int = 0,  
    rank: int = 0,  
):  
    """返回一个创建 BadtimeWarEnv 的闭包，供 DummyVecEnv 使用。"""  
    def _init():  
        env = BadtimeWarEnv(  
            num_opponents=num_opponents,  
            max_rounds=max_rounds,  
        )  
        env = Monitor(env)  
        env.reset(seed=seed + rank)  
        return env  
    return _init  
  
  
# ─────────────────────────────────────────────────────────────────────────────  
#  自定义回调：打印训练摘要  
# ─────────────────────────────────────────────────────────────────────────────  
  
class WinRateCallback(BaseCallback):  
    """  
    每 `check_freq` 步统计最近 `window` 局的胜率并记录到 TensorBoard。  
    依赖 Monitor wrapper 写入的 episode info。  
    """  
  
    def __init__(self, check_freq: int = 2048, window: int = 100, verbose: int = 0):  
        super().__init__(verbose)  
        self.check_freq = check_freq  
        self.window = window  
        self._episode_rewards: list[float] = []  
  
    def _on_step(self) -> bool:  
        # 收集 episode 结束信息  
        infos = self.locals.get("infos", [])  
        for info in infos:  
            ep_info = info.get("episode")  
            if ep_info is not None:  
                self._episode_rewards.append(ep_info["r"])  
  
        if self.n_calls % self.check_freq == 0 and self._episode_rewards:  
            recent = self._episode_rewards[-self.window:]  
            # 胜利 = 终局奖励 > 0（获胜时 terminal_reward = +100）  
            wins = sum(1 for r in recent if r > 50)  
            win_rate = wins / len(recent)  
            mean_reward = np.mean(recent)  
  
            self.logger.record("custom/win_rate", win_rate)  
            self.logger.record("custom/mean_episode_reward", mean_reward)  
            self.logger.record("custom/episodes_total", len(self._episode_rewards))  
  
            if self.verbose >= 1:  
                print(  
                    f"[Step {self.num_timesteps}] "  
                    f"Win rate: {win_rate:.1%} | "  
                    f"Mean reward: {mean_reward:.1f} | "  
                    f"Episodes: {len(self._episode_rewards)}"  
                )  
  
        return True  
  
  
# ─────────────────────────────────────────────────────────────────────────────  
#  训练主函数  
# ─────────────────────────────────────────────────────────────────────────────  
  
def train(args: argparse.Namespace):  
    """执行训练流程。"""  
  
    # ── 路径设置 ──────────────────────────────────────────────────  
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")  
    run_name = f"maskable_ppo_{args.opponents}opp_{timestamp}"  
    log_dir = Path("logs") / run_name  
    ckpt_dir = Path("checkpoints") / run_name  
    log_dir.mkdir(parents=True, exist_ok=True)  
    ckpt_dir.mkdir(parents=True, exist_ok=True)  
  
    # ── 训练环境 ──────────────────────────────────────────────────  
    train_env = DummyVecEnv([  
        make_env(  
            num_opponents=args.opponents,  
            max_rounds=args.max_rounds,  
            seed=args.seed,  
            rank=i,  
        )  
        for i in range(args.n_envs)  
    ])  
  
    # ── 评估环境 ──────────────────────────────────────────────────  
    eval_env = DummyVecEnv([  
        make_env(  
            num_opponents=args.opponents,  
            max_rounds=args.max_rounds,  
            seed=args.seed + 1000,  
            rank=0,  
        )  
    ])  
  
    # ── 模型 ─────────────────────────────────────────────────────  
    if args.resume:  
        print(f"从 {args.resume} 恢复训练")  
        model = MaskablePPO.load(  
            args.resume,  
            env=train_env,  
            tensorboard_log=str(log_dir),  
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
                net_arch=dict(pi=[256, 256], vf=[256, 256]),  
            ),  
            tensorboard_log=str(log_dir),  
            verbose=1,  
            seed=args.seed,  
        )  
  
    # ── 回调 ─────────────────────────────────────────────────────  
    callbacks = CallbackList([  
        # 定期保存 checkpoint  
        CheckpointCallback(  
            save_freq=max(args.ckpt_freq // args.n_envs, 1),  
            save_path=str(ckpt_dir),  
            name_prefix="model",  
            save_replay_buffer=False,  
            save_vecnormalize=False,  
        ),  
        # 定期评估并保存最佳模型  
        MaskableEvalCallback(  
            eval_env,  
            best_model_save_path=str(ckpt_dir / "best"),  
            log_path=str(log_dir / "eval"),  
            eval_freq=max(args.eval_freq // args.n_envs, 1),  
            n_eval_episodes=args.eval_episodes,  
            deterministic=True,  
        ),  
        # 胜率统计  
        WinRateCallback(  
            check_freq=args.n_steps,  
            window=100,  
            verbose=1,  
        ),  
    ])  
  
    # ── 训练 ─────────────────────────────────────────────────────  
    print(f"开始训练: {run_name}")  
    print(f"  对手数: {args.opponents}")  
    print(f"  最大轮数: {args.max_rounds}")  
    print(f"  总步数: {args.timesteps:,}")  
    print(f"  并行环境: {args.n_envs}")  
    print(f"  日志目录: {log_dir}")  
    print(f"  检查点目录: {ckpt_dir}")  
    print()  
  
    model.learn(  
        total_timesteps=args.timesteps,  
        callback=callbacks,  
        progress_bar=True,  
    )  
  
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
    p.add_argument("--max-rounds", type=int, default=50,  
                   help="每局最大轮数")  
  
    # 训练参数  
    p.add_argument("--timesteps", type=int, default=1_000_000,  
                   help="总训练步数")  
    p.add_argument("--n-envs", type=int, default=1,  
                   help="并行环境数（线程模型下建议为 1）")  
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
  
    return p.parse_args()  
  
  
# ─────────────────────────────────────────────────────────────────────────────  
#  入口  
# ─────────────────────────────────────────────────────────────────────────────  
  
if __name__ == "__main__":  
    train(parse_args())