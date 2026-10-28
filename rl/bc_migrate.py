"""
rl/bc_migrate.py
────────────────
将 BC 预训练的 .pt 权重迁移到 SB3 .zip 模型中（热启动种子）。

BC 的 MLP 有 3 层：Linear(539,384) → Linear(384,256) → Linear(256,137)
PPO 的 policy head：Linear(192,384) → Linear(384,256) → Linear(256,137)（GRU 目标）
                或 Linear(192,384) → Linear(384,256) → Linear(256,137)（recurrent 目标，输入同为 LSTM 输出 192）

后两层形状完全匹配，可以直接复制；第一层输入维度不同（539 vs 192，衔接特征层/LSTM），无法迁移——
这正是"浅层 warm-start"：只预热 policy_net 后两层与 action_net，LSTM/特征层随机初始化。

用法：
    # GRU 目标（老路线）
    python -m rl.bc_migrate \
        --bc-weights pretrained/g7_bc_best.pt \
        --output pretrained/g7_warmstart.zip \
        --n-stack 30
    # Recurrent 目标（新路线，n_stack 强制 1）
    python -m rl.bc_migrate --algo maskable-recurrent \
        --bc-weights pretrained/bc_best.pt \
        --output pretrained/bc_warmstart_recurrent.zip
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import torch
import gymnasium as gym
from gymnasium import spaces
from stable_baselines3.common.vec_env import DummyVecEnv
from sb3_contrib import MaskablePPO

from rl.obs_builder import OBS_DIM
from rl.action_space import ACTION_COUNT
from rl.feature_extractor import GRUFeatureExtractor

from rl.maskable_recurrent import MaskableRecurrentPPO


class _DummyEnv(gym.Env):
    """仅用于初始化目标算法的占位环境，不会真正运行。"""

    def __init__(self, n_stack: int = 1, obs_dim: int = OBS_DIM,
                 action_count: int = ACTION_COUNT):
        super().__init__()
        self.observation_space = spaces.Box(
            low=-1.0, high=1.0,
            shape=(n_stack * obs_dim,),
            dtype=np.float32,
        )
        self.action_space = spaces.Discrete(action_count)

    def reset(self, **kwargs):
        return np.zeros(self.observation_space.shape, dtype=np.float32), {}

    def step(self, action):
        obs = np.zeros(self.observation_space.shape, dtype=np.float32)
        return obs, 0.0, True, False, {}

    def action_masks(self):
        return np.ones(self.action_space.n, dtype=bool)


def migrate(bc_path: str, output_path: str,
            n_stack: int = 30, device: str = "cpu",
            algo: str = "maskable",
            obs_dim: Optional[int] = None,
            action_count: Optional[int] = None) -> dict:
    """
    执行权重迁移。

    返回一个 dict 描述迁移了哪些层。
    algo: "maskable"（GRU 30 帧堆叠，老路线）或 "maskable-recurrent"（LSTM 单帧，
    新路线，n_stack 强制 1）。映射表相同：BC MLP 后两层形状与目标 policy_net/
    action_net 重合，只预热这两处；特征层/LSTM 保持随机初始化。
    obs_dim/action_count：目标观测/动作维度（默认 legacy 539/137；M9 以当前 schema 为准），
    用于构造占位 env —— 映射本身按形状自动匹配。
    """
    if algo == "maskable-recurrent":
        n_stack = 1  # recurrent 目标使用单帧 obs
    if obs_dim is None:
        obs_dim = OBS_DIM
    if action_count is None:
        action_count = ACTION_COUNT

    # 1. 加载 BC 权重
    bc_state = torch.load(bc_path, map_location=device, weights_only=True)
    print(f"[MIGRATE] Loaded BC weights from {bc_path}")
    print(f"[MIGRATE] BC keys: {list(bc_state.keys())}")

    # 2. 创建目标算法（与训练完全相同的架构）
    dummy_env = DummyVecEnv([lambda: _DummyEnv(n_stack=n_stack,
                                               obs_dim=obs_dim,
                                               action_count=action_count)])
    if algo == "maskable-recurrent":
        model = MaskableRecurrentPPO(
            policy="MlpLstmPolicy",
            env=dummy_env,
            device=device,
            policy_kwargs=dict(
                lstm_hidden_size=192,
                enable_critic_lstm=True,
                net_arch=dict(pi=[384, 256], vf=[384, 256]),
            ),
            verbose=0,
        )
    else:
        model = MaskablePPO(
            policy="MlpPolicy",
            env=dummy_env,
            device=device,
            policy_kwargs=dict(
                features_extractor_class=GRUFeatureExtractor,
                features_extractor_kwargs=dict(
                    gru_hidden_size=192,
                    proj_size=256,
                    num_layers=1,
                ),
                net_arch=dict(pi=[384, 256], vf=[384, 256]),
            ),
            verbose=0,
        )

    # 3. 获取 PPO policy 的 state_dict
    policy_sd = model.policy.state_dict()
    print(f"[MIGRATE] PPO policy keys ({len(policy_sd)}):")
    for k, v in policy_sd.items():
        print(f"  {k}: {tuple(v.shape)}")

    # 4. 执行迁移
    migrated = {}

    # 映射表：BC key → PPO key
    mapping = {
        "net.2.weight": "mlp_extractor.policy_net.2.weight",
        "net.2.bias":   "mlp_extractor.policy_net.2.bias",
        "net.4.weight": "action_net.weight",
        "net.4.bias":   "action_net.bias",
    }

    for bc_key, ppo_key in mapping.items():
        if bc_key not in bc_state:
            print(f"[MIGRATE] WARNING: BC key '{bc_key}' not found, skipping")
            continue
        if ppo_key not in policy_sd:
            print(f"[MIGRATE] WARNING: PPO key '{ppo_key}' not found, skipping")
            continue

        bc_tensor = bc_state[bc_key]
        ppo_tensor = policy_sd[ppo_key]

        if bc_tensor.shape != ppo_tensor.shape:
            print(f"[MIGRATE] WARNING: Shape mismatch for {bc_key}→{ppo_key}: "
                  f"{tuple(bc_tensor.shape)} vs {tuple(ppo_tensor.shape)}, skipping")
            continue

        policy_sd[ppo_key] = bc_tensor.clone()
        migrated[f"{bc_key} → {ppo_key}"] = tuple(bc_tensor.shape)
        print(f"[MIGRATE] ✓ {bc_key} → {ppo_key} {tuple(bc_tensor.shape)}")

    # 5. 加载修改后的 state_dict
    model.policy.load_state_dict(policy_sd)
    print(f"\n[MIGRATE] Migrated {len(migrated)} / {len(mapping)} layers")

    # 6. 保存为 SB3 .zip 格式
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    model.save(output_path)
    print(f"[MIGRATE] Saved to {output_path}")

    dummy_env.close()
    return migrated


def main():
    parser = argparse.ArgumentParser(
        description="BC → SB3 权重迁移（GRU 老路线 / Recurrent 新路线）"
    )
    parser.add_argument("--algo", type=str, default="maskable",
                        choices=["maskable", "maskable-recurrent"],
                        help="目标算法：maskable=MaskablePPO+GRU(30帧)；"
                             "maskable-recurrent=MaskableRecurrentPPO(LSTM单帧,n_stack=1)")
    parser.add_argument("--bc-weights", type=str, required=True,
                        help="BC 预训练权重路径（.pt）")
    parser.add_argument("--output", type=str,
                        default="pretrained/g7_warmstart.zip",
                        help="输出 SB3 模型路径（.zip）")
    parser.add_argument("--n-stack", type=int, default=30,
                        help="帧堆叠数量（maskable-recurrent 强制为 1）")
    parser.add_argument("--device", type=str, default="cpu",
                        choices=["cpu", "cuda"])
    parser.add_argument("--obs-dim", type=int, default=None,
                        help="目标观测维度（缺省 legacy 539；M9 请读取环境当前 schema）")
    parser.add_argument("--act-dim", type=int, default=None,
                        help="目标动作数（缺省 legacy 137；M9 用 149）")
    args = parser.parse_args()

    result = migrate(
        bc_path=args.bc_weights,
        output_path=args.output,
        n_stack=args.n_stack,
        device=args.device,
        algo=args.algo,
        obs_dim=args.obs_dim,
        action_count=args.act_dim,
    )

    if not result:
        print("\n[MIGRATE] WARNING: 没有成功迁移任何层！请检查 BC 权重文件。")
    else:
        print(f"\n[MIGRATE] 迁移完成。使用方式：")
        if args.algo == "maskable-recurrent":
            print(f"  python -m rl.train --algo maskable-recurrent --seed-model {args.output} ...")
        else:
            print(f"  python -m rl.train --resume {args.output} --timesteps 20000000 --curriculum")


if __name__ == "__main__":
    main()
