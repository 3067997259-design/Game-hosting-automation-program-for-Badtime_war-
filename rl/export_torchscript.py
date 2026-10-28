"""
rl/export_torchscript.py
────────────────────────
把训练好的策略导出为 TorchScript，用于 self-play 对手推理。

self-play 对手每步只需要 argmax logits（确定性策略），SB3 的
``predict()`` 里 Python/numpy 开销远大于网络前向本身。
将 features_extractor + policy_net + action_net 三段合并成一个
``nn.Module`` 再 ``torch.jit.trace`` 出来，在子进程里可直接 ``__call__``，
省掉 predict 的所有包装层，实测可将推理耗时下降 2–5 倍。

两种导出：
  - 无状态（MaskablePPO + GRU 30 帧堆叠）：forward(obs) → logits；
  - 有状态（MaskableRecurrentPPO，单帧 obs）：forward(obs, reset) → logits，
    模块内部自持 actor LSTM (h, c)，调用方新局首次决策传 reset=1.0 归零。

CLI 用法（同时会验证导出模型的 logits 与原模型是否一致）::

    # 无状态（老路线）
    python -m rl.export_torchscript --model path/to/xxx.zip \\
           --output path/to/xxx.pts --n-stack 30
    # 有状态（新路线）
    python -m rl.export_torchscript --model path/to/recurrent.zip \\
           --output path/to/recurrent.pts --recurrent
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


class RecurrentPolicyForward(nn.Module):
    """有状态 actor 前向：单帧 obs → action logits。

    内部自持 actor LSTM 的 (h, c) 隐藏态（buffer），跨调用持久；调用方在
    新局首次决策时传 reset=1.0 归零。只含 actor 链路：
        features_extractor → lstm_actor → mlp_extractor.policy_net → action_net
    供 deterministic argmax 推理（与 MaskableRecurrentPPO.predict 的
    deterministic 路径等价）。
    """

    def __init__(
        self,
        features_extractor: nn.Module,
        lstm_actor: nn.Module,
        actor_net: nn.Module,
        action_net: nn.Module,
        num_layers: int,
        hidden_size: int,
    ):
        super().__init__()
        self.features_extractor = features_extractor
        self.lstm_actor = lstm_actor
        self.actor_net = actor_net
        self.action_net = action_net
        # actor LSTM 的持久隐藏态（torch.jit.trace 下 buffer 跨调用保留）
        self.register_buffer("h", torch.zeros(num_layers, 1, hidden_size))
        self.register_buffer("c", torch.zeros(num_layers, 1, hidden_size))

    def forward(self, obs: torch.Tensor, reset: torch.Tensor) -> torch.Tensor:
        h = torch.where(reset > 0.5, torch.zeros_like(self.h), self.h)
        c = torch.where(reset > 0.5, torch.zeros_like(self.c), self.c)
        features = self.features_extractor(obs)          # (1, feat)
        x = features.unsqueeze(1)                         # (1, 1, feat)
        _, (h2, c2) = self.lstm_actor(x, (h, c))          # h2: (L, 1, H)
        self.h.copy_(h2)
        self.c.copy_(c2)
        latent = self.actor_net(h2[-1])                   # 最后一层最后一个时间步 → (1, H)
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


def _is_recurrent_policy(model) -> bool:
    """判断模型策略是否为 MaskableRecurrentActorCriticPolicy 族。"""
    from rl.maskable_recurrent.policies import MaskableRecurrentActorCriticPolicy
    return isinstance(getattr(model, "policy", None), MaskableRecurrentActorCriticPolicy)


def _load_model_for_export(model_or_path: Union[str, Path]):
    """按 checkpoint 实际策略类型加载（MaskablePPO / MaskableRecurrentPPO）。"""
    from rl.self_play import load_policy
    return load_policy(str(model_or_path))


def export_torchscript_recurrent(
    model_or_path: Union[str, Path, "MaskableRecurrentPPO"],
    output_path: Union[str, Path],
) -> Path:
    """导出 MaskableRecurrentPPO 的 actor 为有状态 TorchScript (.pts)。

    合约：forward(obs, reset) → logits；内部自持 actor LSTM (h,c)（buffer），
    调用方新局首次决策传 reset=1.0 归零。适用于 self-play 对手 / 风洞推理
    （deterministic argmax，与 predict(..., deterministic=True) 等价）。

    Parameters
    ----------
    model_or_path:
        已加载的 MaskableRecurrentPPO 实例，或 .zip checkpoint 路径。
    output_path:
        输出 .pts 路径。
    """
    from rl.maskable_recurrent import MaskableRecurrentPPO

    if isinstance(model_or_path, (str, Path)):
        model = _load_model_for_export(model_or_path)
    else:
        model = model_or_path
    if not isinstance(model, MaskableRecurrentPPO):
        raise TypeError("export_torchscript_recurrent 需要 MaskableRecurrentPPO")

    policy = model.policy
    num_layers = int(policy.lstm_actor.num_layers)
    hidden_size = int(policy.lstm_actor.hidden_size)

    # 深拷贝子模块（避免原地改动 live 训练模型的 device/状态）
    features_extractor = copy.deepcopy(policy.features_extractor)
    lstm_actor = copy.deepcopy(policy.lstm_actor)
    actor_net = copy.deepcopy(policy.mlp_extractor.policy_net)
    action_net = copy.deepcopy(policy.action_net)

    module = RecurrentPolicyForward(
        features_extractor, lstm_actor, actor_net, action_net,
        num_layers, hidden_size,
    )
    module.eval().to("cpu")

    obs = torch.zeros(1, OBS_DIM, dtype=torch.float32)
    reset = torch.zeros(1, dtype=torch.float32)
    with torch.no_grad():
        # check_trace=False：有状态模块的 buffer 更新会令 torch 内部 eager 对比
        # 对 requires-grad 张量做 numpy 而误报 TracerWarning；正确性由
        # _verify_parity_recurrent 的独立多帧随机 reset 校验覆盖（强于单输入 check_trace）。
        scripted = torch.jit.trace(module, (obs, reset), check_trace=False)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    scripted.save(str(output_path))
    return output_path


def _verify_parity_recurrent(
    model: "MaskableRecurrentPPO",
    jit_path: Union[str, Path],
    n_samples: int = 6,
    seq_len: int = 4,
    atol: float = 1e-5,
) -> None:
    """随机多帧序列（含随机 reset 点）对比原 policy actor 与导出模块 logits 一致性。"""
    jit_model = torch.jit.load(str(jit_path))
    jit_model.eval()

    policy = model.policy
    num_layers = int(policy.lstm_actor.num_layers)
    hidden_size = int(policy.lstm_actor.hidden_size)
    rng = np.random.default_rng(0)

    max_diff = 0.0
    for _ in range(n_samples):
        h = torch.zeros(num_layers, 1, hidden_size)
        c = torch.zeros(num_layers, 1, hidden_size)
        for t in range(seq_len):
            obs_np = rng.standard_normal((1, OBS_DIM)).astype(np.float32)
            reset = 1.0 if (t == 0 or rng.random() < 0.2) else 0.0

            with torch.no_grad():
                if reset > 0.5:
                    h = torch.zeros(num_layers, 1, hidden_size)
                    c = torch.zeros(num_layers, 1, hidden_size)
                features = policy.features_extractor(torch.as_tensor(obs_np))
                _, (h2, c2) = policy.lstm_actor(features.unsqueeze(1), (h, c))
                h, c = h2, c2
                latent = policy.mlp_extractor.policy_net(h2[-1])
                logits_ref = policy.action_net(latent).cpu().numpy()

                logits_jit = jit_model(
                    torch.as_tensor(obs_np), torch.tensor([float(reset)])
                ).numpy()
            max_diff = max(max_diff, float(np.abs(logits_ref - logits_jit).max()))

    if max_diff > atol:
        raise RuntimeError(
            f"Recurrent TorchScript 导出精度验证失败：max |Δlogits| = "
            f"{max_diff:.3e} > atol={atol:.1e}"
        )
    print(
        f"[export_torchscript] recurrent parity OK: max |Δlogits| = {max_diff:.3e} "
        f"over {n_samples}×{seq_len} 帧（随机 reset 点），hidden={hidden_size}"
    )


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
    parser.add_argument(
        "--recurrent", action="store_true",
        help="导出 MaskableRecurrentPPO 的有状态 actor（forward(obs, reset) → logits）",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.recurrent:
        model = _load_model_for_export(args.model)
        out = export_torchscript_recurrent(model, args.output)
        print(f"[export_torchscript] wrote recurrent stateful {out}")
        if not args.no_verify:
            _verify_parity_recurrent(model, out)
        return

    model = MaskablePPO.load(args.model)
    out = export_torchscript(model, args.output, n_stack=args.n_stack)
    print(f"[export_torchscript] wrote {out}")

    if not args.no_verify:
        _verify_parity(model, out, n_stack=args.n_stack)


if __name__ == "__main__":
    _main()
