"""
rl/distill_student.py
─────────────────────
P4 统一学生（docs/m9/rl/m9_rl_next_steps.md §7 第一步）：

单一动作头 + 专家 ID 辅助预测头，共享 LSTM 主干。

- ``DistillStudentPolicy``：MaskableRecurrentActorCriticPolicy 的薄扩展——
  在 policy_net 潜层上加 ``expert_head``（10 类）。蒸馏损失 =
  动作 NLL + teacher-logit KL + expert_id 交叉熵（按 expert_id 逆频率加权）。
  aux 头梯度流入主干（表征被迫区分技能）；``policy.optimizer`` 在 _build
  时创建、不含 expert_head——PPO 微调阶段 aux 头自动冻结。
- ``load_distill_dataset``：读取 P3 分片并按 episode_starts 切回完整局
  （每局 k 个转换 + 1 个尾拍 next-state）。
- ``train_distillation``：整局 BPTT（零初始递归状态，掩码同刻），验证集
  NLL 早停，结束时恢复最优参数。

版本耦合：forward_actor_aux 依赖 sb3-contrib 2.7 的 _process_sequence/
extract_features 内部路径（与 rl/maskable_recurrent 同一耦合面）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from random import Random
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import torch as th
import torch.nn.functional as F

from rl.maskable_recurrent.policies import (
    MaskableRecurrentActorCriticPolicy, _apply_masking_safely,
)

N_EXPERTS = 10

# P4 排除清单（docs/m9/rl/m9_rl_next_steps.md §6.2：教师一步死 cell）。
# P4.1 勘误后移除 course_pp_damage_choice：pp_course 合同修正 +
# 路由粘性后 s121 在该课健康执行（751 步完整局），不再是退化数据。
DISTILL_EXCLUDED_CELLS: Tuple[str, ...] = (
    "course_survival_heal_counterattack",
    "course_survival_armor_counterattack",
)


@dataclass(frozen=True)
class DistillEpisode:
    """一局蒸馏样本：k 个转换 + 1 个尾拍观测。"""

    cell: str
    observations: np.ndarray        # (k+1, obs_dim) float32
    action_masks: np.ndarray        # (k, act) bool
    actions: np.ndarray             # (k,) int64
    expert_ids: np.ndarray          # (k,) int64
    teacher_probs: np.ndarray       # (k, act) float32


def _cell_from_path(path: Path) -> str:
    return path.stem


def load_distill_dataset(
    data_dir: str | Path,
    *,
    exclude: Sequence[str] = DISTILL_EXCLUDED_CELLS,
) -> List[DistillEpisode]:
    """读取全部（除排除清单外的）npz 分片并切回完整局。"""
    episodes: List[DistillEpisode] = []
    excluded = set(exclude)
    for path in sorted(Path(data_dir).glob("*.npz")):
        if path.stem in excluded:
            continue
        data = np.load(path, allow_pickle=False)
        starts = np.asarray(data["episode_starts"], dtype=bool)
        obs = np.asarray(data["observations"], dtype=np.float32)
        masks = np.asarray(data["action_masks"], dtype=bool)
        actions = np.asarray(data["actions"], dtype=np.int64)
        experts = np.asarray(data["expert_ids"], dtype=np.int64)
        probs = np.asarray(data["teacher_probs"], dtype=np.float32)

        boundaries = list(np.flatnonzero(starts))
        if not boundaries or not bool(starts[0]):
            raise AssertionError(f"{path.name}: episode_starts 结构非法")
        for idx, begin in enumerate(boundaries):
            end = boundaries[idx + 1] if idx + 1 < len(boundaries) else len(actions)
            k = end - begin
            if k <= 0:
                continue
            episodes.append(DistillEpisode(
                cell=_cell_from_path(path),
                observations=obs[begin:begin + k + 1],
                action_masks=masks[begin:end],
                actions=actions[begin:end],
                expert_ids=experts[begin:end],
                teacher_probs=probs[begin:end],
            ))
    if not episodes:
        raise ValueError(f"{data_dir} 内没有可用蒸馏样本")
    return episodes


def expert_balance_weights(
    episodes: Sequence[DistillEpisode],
    *,
    n_experts: int = N_EXPERTS,
    temperature: float = 0.5,
) -> np.ndarray:
    """按 expert_id 逆频率加权（sqrt 温度），均值归一到 1。

    温度 0.5 = 平方根逆频率：比纯逆频率温和，避免 pp_finish（0.3%）
    一类极稀有类把有效批量压缩到个位步。
    """
    counts = np.zeros(n_experts, dtype=np.float64)
    for episode in episodes:
        np.add.at(counts, episode.expert_ids, 1.0)
    total = counts.sum()
    weights = np.zeros(n_experts, dtype=np.float64)
    for expert in range(n_experts):
        if counts[expert] > 0:
            weights[expert] = (total / (n_experts * counts[expert])) ** temperature
    if weights.sum() > 0:
        weights = weights / weights.mean()
    return weights.astype(np.float32)


class DistillStudentPolicy(MaskableRecurrentActorCriticPolicy):
    """带专家 ID 辅助头的单动作头递归策略。"""

    def __init__(self, *args: Any, distill_n_experts: int = N_EXPERTS,
                 **kwargs: Any) -> None:
        self._distill_n_experts = int(distill_n_experts)
        super().__init__(*args, **kwargs)
        latent_dim = int(self.action_net.in_features)
        self.expert_head = th.nn.Linear(latent_dim, self._distill_n_experts)
        # 说明：policy.optimizer 已在 super()._build() 中创建，不含
        # expert_head——PPO 微调阶段 aux 头冻结（蒸馏阶段用独立优化器）。

    def forward_actor_aux(
        self,
        obs: th.Tensor,
        lstm_states: Tuple[th.Tensor, th.Tensor],
        episode_starts: th.Tensor,
    ) -> Tuple[Any, th.Tensor, Tuple[th.Tensor, th.Tensor]]:
        """返回（未掩码分布, expert logits, 下一递归状态）。

        forward 路径与 get_distribution 完全同源
        （extract_features → _process_sequence → policy_net）。
        """
        from stable_baselines3.common.policies import ActorCriticPolicy

        features = super(ActorCriticPolicy, self).extract_features(
            obs, self.pi_features_extractor,
        )
        latent_pi, lstm_states = self._process_sequence(
            features, lstm_states, episode_starts, self.lstm_actor,
        )
        latent_pi = self.mlp_extractor.forward_actor(latent_pi)
        aux_logits = self.expert_head(latent_pi)
        distribution = self._get_action_dist_from_latent(latent_pi)
        return distribution, aux_logits, lstm_states


def _zero_lstm_state(policy: DistillStudentPolicy) -> Tuple[th.Tensor, th.Tensor]:
    shape = (
        int(policy.lstm_actor.num_layers), 1, int(policy.lstm_actor.hidden_size),
    )
    hidden = th.zeros(shape, dtype=th.float32, device=policy.device)
    return hidden, hidden.clone()


def sequence_distill_loss(
    policy: DistillStudentPolicy,
    episode: DistillEpisode,
    expert_weights: np.ndarray,
    *,
    kl_coef: float = 1.0,
    aux_coef: float = 0.5,
) -> Tuple[th.Tensor, Dict[str, float]]:
    """单局蒸馏损失：加权 [NLL + kl_coef·KL + aux_coef·CE]。"""
    length = int(episode.actions.shape[0])
    obs = th.as_tensor(episode.observations[:length], dtype=th.float32,
                       device=policy.device)
    masks = th.as_tensor(episode.action_masks, device=policy.device)
    actions = th.as_tensor(episode.actions, device=policy.device)
    experts = th.as_tensor(episode.expert_ids, device=policy.device)
    teacher = th.as_tensor(episode.teacher_probs, dtype=th.float32,
                           device=policy.device)
    starts = th.zeros(length, dtype=th.float32, device=policy.device)
    starts[0] = 1.0

    distribution, aux_logits, _next = policy.forward_actor_aux(
        obs, _zero_lstm_state(policy), starts,
    )
    _apply_masking_safely(distribution, masks)

    log_probs = distribution.log_prob(actions)
    nll = -log_probs

    student_probs = distribution.distribution.probs
    log_student = th.log(student_probs + 1e-10)
    log_teacher = th.log(teacher + 1e-10)
    kl = th.where(
        teacher > 0,
        teacher * (log_teacher - log_student),
        th.zeros_like(teacher),
    ).sum(dim=1)

    aux_ce = F.cross_entropy(aux_logits, experts, reduction="none")

    step_weights = th.as_tensor(
        expert_weights[episode.expert_ids], dtype=th.float32,
        device=policy.device,
    )
    loss = (step_weights * (nll + kl_coef * kl + aux_coef * aux_ce)).mean()

    metrics = {
        "nll": float(nll.mean().detach().item()),
        "kl": float(kl.mean().detach().item()),
        "aux_ce": float(aux_ce.mean().detach().item()),
    }
    return loss, metrics


@th.no_grad()
def evaluate_distillation(
    policy: DistillStudentPolicy,
    episodes: Sequence[DistillEpisode],
    expert_weights: Optional[np.ndarray] = None,
    *,
    kl_coef: float = 1.0,
    aux_coef: float = 0.5,
) -> Dict[str, float]:
    """无加权验证指标：NLL / KL / aux 准确率（与训练损失同口径的均值）。"""
    if expert_weights is None:
        expert_weights = np.ones(N_EXPERTS, dtype=np.float32)
    policy.set_training_mode(False)
    nll_sum = kl_sum = ce_sum = correct = steps = 0.0
    for episode in episodes:
        _, metrics = sequence_distill_loss(
            policy, episode, expert_weights,
            kl_coef=kl_coef, aux_coef=aux_coef,
        )
        length = int(episode.actions.shape[0])
        nll_sum += metrics["nll"] * length
        kl_sum += metrics["kl"] * length
        ce_sum += metrics["aux_ce"] * length
        steps += length
        # aux 准确率
        obs = th.as_tensor(episode.observations[:length], dtype=th.float32,
                           device=policy.device)
        starts = th.zeros(length, dtype=th.float32, device=policy.device)
        starts[0] = 1.0
        _dist, aux_logits, _s = policy.forward_actor_aux(
            obs, _zero_lstm_state(policy), starts,
        )
        pred = aux_logits.argmax(dim=1).cpu().numpy()
        correct += float((pred == episode.expert_ids).sum())
    return {
        "val_nll": nll_sum / max(steps, 1.0),
        "val_kl": kl_sum / max(steps, 1.0),
        "val_aux_ce": ce_sum / max(steps, 1.0),
        "val_aux_accuracy": correct / max(steps, 1.0),
        "steps": steps,
    }


def _chunks(items: List[DistillEpisode], size: int) -> List[List[DistillEpisode]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def train_distillation(
    policy: DistillStudentPolicy,
    episodes: List[DistillEpisode],
    val_episodes: List[DistillEpisode],
    *,
    epochs: int,
    learning_rate: float,
    batch_episodes: int = 8,
    kl_coef: float = 1.0,
    aux_coef: float = 0.5,
    patience: int = 2,
    seed: int = 104,
    log_fn: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> List[Dict[str, Any]]:
    """整局 BPTT 蒸馏；验证 NLL 早停，结束恢复最优参数。

    优化器覆盖 policy 全部参数（critic 无梯度来源，等效冻结）；
    结束后丢弃本优化器（PPO 续训不继承矩，沿用 m9_recurrent_bc 纪律）。
    """
    if not episodes:
        raise ValueError("训练序列为空")
    if epochs <= 0 or batch_episodes <= 0:
        raise ValueError("epochs / batch_episodes 必须为正")
    if not val_episodes:
        raise ValueError("验证序列为空（至少保留少量局做早停）")

    weights = expert_balance_weights(episodes)
    trainable = [p for p in policy.parameters() if p.requires_grad]
    optimizer = th.optim.Adam(trainable, lr=learning_rate)
    rng = Random(seed)
    history: List[Dict[str, Any]] = []
    best_val = float("inf")
    best_state: Optional[Dict[str, th.Tensor]] = None
    stale = 0

    for epoch in range(1, epochs + 1):
        order = list(episodes)
        rng.shuffle(order)
        batches = _chunks(order, batch_episodes)
        totals = {"nll": 0.0, "kl": 0.0, "aux_ce": 0.0}
        steps_done = 0
        policy.set_training_mode(True)
        for batch in batches:
            optimizer.zero_grad(set_to_none=True)
            batch_loss = th.zeros((), device=policy.device)
            batch_steps = 0
            for episode in batch:
                loss, metrics = sequence_distill_loss(
                    policy, episode, weights,
                    kl_coef=kl_coef, aux_coef=aux_coef,
                )
                length = int(episode.actions.shape[0])
                batch_loss = batch_loss + loss * length
                batch_steps += length
                for key in totals:
                    totals[key] += metrics[key] * length
            (batch_loss / max(batch_steps, 1)).backward()
            th.nn.utils.clip_grad_norm_(trainable, max_norm=0.5)
            optimizer.step()
            steps_done += batch_steps

        val = evaluate_distillation(
            policy, val_episodes, kl_coef=kl_coef, aux_coef=aux_coef,
        )
        record: Dict[str, Any] = {
            "epoch": epoch,
            "train_nll": totals["nll"] / max(steps_done, 1),
            "train_kl": totals["kl"] / max(steps_done, 1),
            "train_aux_ce": totals["aux_ce"] / max(steps_done, 1),
            **val,
        }
        history.append(record)
        if log_fn is not None:
            log_fn(record)
        # 早停判据与训练损失同构：NLL 单项平台后 aux 头仍在进步时不叫停
        score = val["val_nll"] + aux_coef * val["val_aux_ce"]
        if score < best_val - 1e-4:
            best_val = score
            best_state = {
                key: value.detach().cpu().clone()
                for key, value in policy.state_dict().items()
            }
            stale = 0
        else:
            stale += 1
            if stale >= patience:
                break

    if best_state is not None:
        policy.load_state_dict(best_state)
    return history


def save_history(path: str | Path, history: List[Dict[str, Any]]) -> None:
    Path(path).write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8",
    )
