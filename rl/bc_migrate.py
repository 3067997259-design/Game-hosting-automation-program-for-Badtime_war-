"""
rl/bc_migrate.py
────────────────
将 BC 预训练的 .pt 权重迁移到 MaskablePPO 的 .zip 模型中。

BC 的 MLP 有 3 层：Linear(515,384) → Linear(384,256) → Linear(256,130)
PPO 的 policy head 有 3 层：Linear(192,384) → Linear(384,256) → Linear(256,130)

后两层形状完全匹配，可以直接复制。第一层输入维度不同（515 vs 192），无法迁移。

用法：
    python -m rl.bc_migrate \
        --bc-weights pretrained/g7_bc_best.pt \
        --output pretrained/g7_warmstart.zip \
        --n-stack 30
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


class _DummyEnv(gym.Env):
    """仅用于初始化 MaskablePPO 的占位环境，不会真正运行。"""

    def __init__(self, n_stack: int = 30):
        super().__init__()
        self.observation_space = spaces.Box(
            low=-1.0, high=1.0,
            shape=(n_stack * OBS_DIM,),
            dtype=np.float32,
        )
        self.action_space = spaces.Discrete(ACTION_COUNT)

    def reset(self, **kwargs):
        return np.zeros(self.observation_space.shape, dtype=np.float32), {}

    def step(self, action):
        obs = np.zeros(self.observation_space.shape, dtype=np.float32)
        return obs, 0.0, True, False, {}

    def action_masks(self):
        return np.ones(ACTION_COUNT, dtype=bool)


def migrate(bc_path: str, output_path: str,
            n_stack: int = 30, device: str = "cpu") -> dict:
    """
    执行权重迁移。

    返回一个 dict 描述迁移了哪些层。
    """
    # 1. 加载 BC 权重
    bc_state = torch.load(bc_path, map_location=device, weights_only=True)
    print(f"[MIGRATE] Loaded BC weights from {bc_path}")
    print(f"[MIGRATE] BC keys: {list(bc_state.keys())}")

    # 2. 创建 MaskablePPO（与 train.py 完全相同的架构）
    dummy_env = DummyVecEnv([lambda: _DummyEnv(n_stack=n_stack)])
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
        description="BC → MaskablePPO 权重迁移"
    )
    parser.add_argument("--bc-weights", type=str, required=True,
                        help="BC 预训练权重路径（.pt）")
    parser.add_argument("--output", type=str,
                        default="pretrained/g7_warmstart.zip",
                        help="输出 MaskablePPO 模型路径（.zip）")
    parser.add_argument("--n-stack", type=int, default=30,
                        help="帧堆叠数量（必须与训练时一致）")
    parser.add_argument("--device", type=str, default="cpu",
                        choices=["cpu", "cuda"])
    args = parser.parse_args()

    result = migrate(
        bc_path=args.bc_weights,
        output_path=args.output,
        n_stack=args.n_stack,
        device=args.device,
    )

    if not result:
        print("\n[MIGRATE] WARNING: 没有成功迁移任何层！请检查 BC 权重文件。")
    else:
        print(f"\n[MIGRATE] 迁移完成。使用方式：")
        print(f"  python -m rl.train --resume {args.output} --timesteps 20000000 --curriculum")


if __name__ == "__main__":
    main()
