"""
rl/bc_pretrain.py
─────────────────
G7（星野）行为克隆（Behavior Cloning）预训练。

读取 rl.bc_collector 生成的 .npz 数据（obs / actions / masks），
训练一个简单的 MLP 策略网络来模仿 BasicAI 的决策，产出可用于
后续 PPO 微调的权重文件。

用法：
    python -m rl.bc_pretrain \\
        --data bc_data/g7/g7_bc_data.npz \\
        --epochs 50 \\
        --output pretrained/g7_bc.zip
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

try:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, Dataset, random_split
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "bc_pretrain 需要 torch，请先安装：pip install torch"
    ) from exc

from rl.action_space import ACTION_COUNT
from rl.obs_builder import OBS_DIM


# ─────────────────────────────────────────────────────────────────────────────
#  Dataset
# ─────────────────────────────────────────────────────────────────────────────

class BCDataset(Dataset):
    """从 .npz 加载 (obs, action_idx, mask) 三元组。"""

    def __init__(self, npz_path: str):
        data = np.load(npz_path)
        obs = data["obs"]
        actions = data["actions"]
        masks = data["masks"]

        if obs.ndim != 2 or obs.shape[1] != OBS_DIM:
            raise ValueError(
                f"obs 形状不匹配：expected (N, {OBS_DIM}), got {obs.shape}"
            )
        if masks.shape != (obs.shape[0], ACTION_COUNT):
            raise ValueError(
                f"masks 形状不匹配：expected (N, {ACTION_COUNT}), "
                f"got {masks.shape}"
            )

        self.obs = torch.from_numpy(obs.astype(np.float32))
        self.actions = torch.from_numpy(actions.astype(np.int64))
        self.masks = torch.from_numpy(masks.astype(bool))

    def __len__(self) -> int:
        return int(self.actions.shape[0])

    def __getitem__(self, idx):
        return self.obs[idx], self.actions[idx], self.masks[idx]


# ─────────────────────────────────────────────────────────────────────────────
#  Policy network
# ─────────────────────────────────────────────────────────────────────────────

class BCPolicyNet(nn.Module):
    """
    简单的 MLP 策略网络。

    输入：obs (batch, OBS_DIM)
    输出：动作 logits (batch, ACTION_COUNT)

    支持 action mask：非法动作的 logits 被置为 -inf，
    以便 softmax / argmax 只在合法动作上操作。
    """

    def __init__(self, obs_dim: int = OBS_DIM,
                 action_count: int = ACTION_COUNT,
                 hidden=(384, 256)):
        super().__init__()
        layers = []
        in_dim = obs_dim
        for h in hidden:
            layers.append(nn.Linear(in_dim, h))
            layers.append(nn.ReLU())
            in_dim = h
        layers.append(nn.Linear(in_dim, action_count))
        self.net = nn.Sequential(*layers)

    def forward(self, obs: "torch.Tensor",
                mask: "torch.Tensor | None" = None) -> "torch.Tensor":
        logits = self.net(obs)
        if mask is not None:
            logits = logits.masked_fill(~mask, float("-inf"))
        return logits


# ─────────────────────────────────────────────────────────────────────────────
#  训练主流程
# ─────────────────────────────────────────────────────────────────────────────

def train_bc(data_path: str, output_path: str,
             epochs: int = 50, batch_size: int = 256,
             lr: float = 1e-3, device: str = "auto",
             val_split: float = 0.2, num_workers: int = 0) -> float:
    """训练 BC 策略网络。

    返回最佳验证准确率。保存两个 checkpoint：
      <output_path>_best.pt  : 验证集准确率最高的权重
      <output_path>_final.pt : 最后一个 epoch 的权重
    """
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"

    dataset = BCDataset(data_path)
    print(f"[BC] Loaded {len(dataset)} samples from {data_path}")

    n_val = max(1, int(len(dataset) * val_split))
    n_train = len(dataset) - n_val
    if n_train <= 0:
        raise ValueError("训练样本数量不足，请收集更多数据。")
    train_set, val_set = random_split(
        dataset, [n_train, n_val],
        generator=torch.Generator().manual_seed(0),
    )

    train_loader = DataLoader(
        train_set, batch_size=batch_size, shuffle=True,
        num_workers=num_workers,
    )
    val_loader = DataLoader(
        val_set, batch_size=batch_size, shuffle=False,
        num_workers=num_workers,
    )

    model = BCPolicyNet().to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    criterion = nn.CrossEntropyLoss()

    output_path = str(output_path)
    # 兼容 .zip 后缀（SB3 风格），实际保存为 .pt
    best_ckpt = output_path.replace(".zip", "_best.pt")
    final_ckpt = output_path.replace(".zip", "_final.pt")
    Path(best_ckpt).parent.mkdir(parents=True, exist_ok=True)

    best_val_acc = 0.0
    for epoch in range(epochs):
        # ── train ──
        model.train()
        total_loss = 0.0
        total_correct = 0
        total_samples = 0
        for obs, actions, masks in train_loader:
            obs = obs.to(device)
            actions = actions.to(device)
            masks = masks.to(device)

            logits = model(obs, masks)
            loss = criterion(logits, actions)

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            total_loss += float(loss.item()) * actions.shape[0]
            preds = logits.argmax(dim=1)
            total_correct += int((preds == actions).sum().item())
            total_samples += int(actions.shape[0])

        train_acc = total_correct / max(total_samples, 1)
        train_loss = total_loss / max(total_samples, 1)

        # ── val ──
        model.eval()
        val_correct = 0
        val_total = 0
        with torch.no_grad():
            for obs, actions, masks in val_loader:
                obs = obs.to(device)
                actions = actions.to(device)
                masks = masks.to(device)
                logits = model(obs, masks)
                preds = logits.argmax(dim=1)
                val_correct += int((preds == actions).sum().item())
                val_total += int(actions.shape[0])

        val_acc = val_correct / max(val_total, 1)
        print(f"[BC] Epoch {epoch + 1}/{epochs} | "
              f"loss={train_loss:.4f} | "
              f"train_acc={train_acc:.3f} | val_acc={val_acc:.3f}")

        if val_acc > best_val_acc:
            best_val_acc = val_acc
            torch.save(model.state_dict(), best_ckpt)

    torch.save(model.state_dict(), final_ckpt)
    print(f"[BC] Best val accuracy: {best_val_acc:.3f}")
    print(f"[BC] Saved best -> {best_ckpt}")
    print(f"[BC] Saved final -> {final_ckpt}")
    return best_val_acc


def main():
    parser = argparse.ArgumentParser(description="G7 BC 预训练")
    parser.add_argument("--data", type=str, required=True,
                        help="BC 数据 .npz 路径")
    parser.add_argument("--output", type=str, default="pretrained/g7_bc.zip",
                        help="输出 checkpoint 路径（会生成 _best.pt 和 _final.pt）")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--device", type=str, default="auto",
                        choices=["auto", "cpu", "cuda"])
    parser.add_argument("--val-split", type=float, default=0.2)
    parser.add_argument("--num-workers", type=int, default=0)
    args = parser.parse_args()

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    train_bc(
        data_path=args.data,
        output_path=args.output,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        device=args.device,
        val_split=args.val_split,
        num_workers=args.num_workers,
    )


if __name__ == "__main__":
    main()
