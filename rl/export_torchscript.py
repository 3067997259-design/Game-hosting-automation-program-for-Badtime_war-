"""
rl/export_torchscript.py
────────────────────────
把训练好的 MaskablePPO 策略导出为 TorchScript，用于 self-play 对手推理。

self-play 对手每步只需要 argmax logits（确定性策略），SB3 的
``MaskablePPO.predict()`` 里 Python/numpy 开销远大于 GRU 前向本身。
将 features_extractor + policy_net + action_net 三段合并成一个
``nn.Module`` 再 ``torch.jit.trace`` 出来，在子进程里可直接 ``__call__``，
省掉 predict 的所有包装层，实测可将推理耗时下降 2–5 倍。

CLI 用法（同时会验证导出模型的 logits 与原模型是否一致）::

    python -m rl.export_torchscript --model path/to/xxx.zip \
           --output path/to/xxx.pts --n-stack 30
"""

from __future__ import annotations

import argparse
import copy
import logging
from pathlib import Path
from typing import Union

import numpy as np
import torch
import torch.nn as nn
from sb3_contrib import MaskablePPO

from rl.action_space import ACTION_COUNT
from rl.obs_builder import OBS_DIM

logger = logging.getLogger(__name__)


class PolicyForward(nn.Module):
    """将 features_extractor + mlp_extractor.policy_net + action_net 合并为单一模块。

    只负责从扁平化的 stacked obs 到 action logits 的前向传播，
    不包含 value head、action sampling、action masking 等 SB3 逻辑。
    """

    def __init__(
        self,
        features_extractor: nn.Module,
        policy_net: nn.Module,
        action_net: nn.Module,
    ):
        super().__init__()
        self.features_extractor = features_extractor
        self.policy_net = policy_net
        self.action_net = action_net

    def forward(self, obs: torch.Tensor) -> torch.Tensor:
        features = self.features_extractor(obs)
        latent = self.policy_net(features)
        return self.action_net(latent)


def export_torchscript(
    model_or_path: Union[str, Path, MaskablePPO],
    output_path: Union[str, Path],
    n_stack: int = 30,
) -> Path:
    """导出 MaskablePPO 策略为 TorchScript 文件。

    Parameters
    ----------
    model_or_path:
        已加载的 ``MaskablePPO`` 实例，或指向 ``.zip`` checkpoint 的路径。
    output_path:
        输出 ``.pts`` 路径（扩展名仅作区分，内容为 TorchScript）。
    n_stack:
        帧堆叠数；必须与训练时保持一致，否则 GRU 输入 shape 会不匹配。

    Returns
    -------
    Path
        实际写入磁盘的 ``.pts`` 路径。
    """
    if isinstance(model_or_path, (str, Path)):
        model = MaskablePPO.load(str(model_or_path))
    else:
        model = model_or_path

    policy = model.policy
    # 深拷贝子模块：save_current_model 期间传入的是正在训练的 live model，
    # 直接提取子模块引用后再调用 .eval() / .to("cpu") 会原地改动训练模型
    # （把 features_extractor / policy_net / action_net 搬到 CPU，而
    # value_net 等仍在 GPU 上），下一次 PPO.predict 会因 device mismatch 崩溃。
    features_extractor = copy.deepcopy(policy.features_extractor)
    policy_net = copy.deepcopy(policy.mlp_extractor.policy_net)
    action_net = copy.deepcopy(policy.action_net)

    policy_forward = PolicyForward(features_extractor, policy_net, action_net)
    policy_forward.eval()

    # MaskablePPO 用 CPU 或 CUDA 加载都有可能，trace 前统一迁到 CPU，
    # 让导出的 .pts 在任何子进程里都能直接加载使用。深拷贝之后在 CPU 上做
    # in-place 搬运是安全的，不会影响原模型。
    policy_forward = policy_forward.to("cpu")

    dummy_input = torch.zeros(1, n_stack * OBS_DIM, dtype=torch.float32)

    with torch.no_grad():
        scripted = torch.jit.trace(policy_forward, dummy_input)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    scripted.save(str(output_path))

    return output_path


def _verify_parity(
    model: MaskablePPO,
    jit_path: Union[str, Path],
    n_stack: int,
    n_samples: int = 8,
    atol: float = 1e-5,
) -> None:
    """用若干随机输入对比原始 policy 与 TorchScript 的 logits 是否一致。"""
    jit_model = torch.jit.load(str(jit_path))
    jit_model.eval()

    device = model.policy.device
    rng = np.random.default_rng(0)

    max_abs_diff = 0.0
    for i in range(n_samples):
        obs_np = rng.standard_normal((1, n_stack * OBS_DIM)).astype(np.float32)
        obs_t_orig = torch.as_tensor(obs_np, device=device)
        obs_t_jit = torch.as_tensor(obs_np)

        with torch.no_grad():
            features = model.policy.features_extractor(obs_t_orig)
            latent = model.policy.mlp_extractor.policy_net(features)
            logits_orig = model.policy.action_net(latent).cpu().numpy()

            logits_jit = jit_model(obs_t_jit).numpy()

        diff = float(np.max(np.abs(logits_orig - logits_jit)))
        max_abs_diff = max(max_abs_diff, diff)

    if max_abs_diff > atol:
        raise RuntimeError(
            f"TorchScript 导出精度验证失败：max |logits_orig - logits_jit| "
            f"= {max_abs_diff:.3e} > atol={atol:.1e}"
        )

    logger.info(
        "TorchScript 精度验证通过：max |Δlogits| = %.3e （%d 个随机样本）",
        max_abs_diff,
        n_samples,
    )
    print(
        f"[export_torchscript] parity OK: max |Δlogits| = {max_abs_diff:.3e} "
        f"over {n_samples} random samples, ACTION_COUNT={ACTION_COUNT}"
    )


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="导出 MaskablePPO 策略为 TorchScript (.pts) 文件"
    )
    parser.add_argument(
        "--model", type=str, required=True,
        help="输入 MaskablePPO checkpoint 路径（.zip）",
    )
    parser.add_argument(
        "--output", type=str, required=True,
        help="输出 TorchScript 路径（推荐 .pts 扩展名）",
    )
    parser.add_argument(
        "--n-stack", type=int, default=30,
        help="帧堆叠数，必须与训练时保持一致（默认 30）",
    )
    parser.add_argument(
        "--no-verify", action="store_true",
        help="跳过精度验证（默认会跑 8 个随机输入对比 logits）",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    model = MaskablePPO.load(args.model)
    out = export_torchscript(model, args.output, n_stack=args.n_stack)
    print(f"[export_torchscript] wrote {out}")

    if not args.no_verify:
        _verify_parity(model, out, n_stack=args.n_stack)


if __name__ == "__main__":
    _main()
