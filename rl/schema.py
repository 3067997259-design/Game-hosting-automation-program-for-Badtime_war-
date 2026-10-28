"""RL observation/action schema 版本与 checkpoint 兼容性预检。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


M9_SCHEMA_VERSION = "m9-rfc-obs918-act217-v6"
M9_SCHEMA_VERSION_V5 = "m9-rfc-obs828-act217-v5"
LEGACY_SCHEMA_VERSION = "legacy-obs539-act137-v1"


class RLSchemaMismatchError(ValueError):
    """checkpoint 与目标环境的 observation/action 布局不兼容。"""


@dataclass(frozen=True)
class SpaceSignature:
    observation_dim: int
    action_count: int
    version: str


def _space_signature(observation_space: Any, action_space: Any) -> SpaceSignature:
    obs_shape = getattr(observation_space, "shape", None)
    action_count = getattr(action_space, "n", None)
    if not obs_shape or action_count is None:
        raise TypeError(
            "RL schema 预检仅支持一维 Box observation 与 Discrete action space"
        )
    observation_dim = int(obs_shape[0])
    action_count = int(action_count)
    known_versions = {
        (539, 137): LEGACY_SCHEMA_VERSION,
        (918, 217): M9_SCHEMA_VERSION,
        (828, 217): M9_SCHEMA_VERSION_V5,
        (572, 217): "deprecated-m9-action-spec-no-option-features-obs572-act217-v4",
        (572, 185): "deprecated-m9-full-interact-obs572-act185-v3",
        (572, 160): "deprecated-m9-fixed-home-obs572-act160-v2",
        (570, 149): "deprecated-m9-prototype-obs570-act149",
    }
    version = known_versions.get(
        (observation_dim, action_count),
        f"unknown-obs{observation_dim}-act{action_count}",
    )
    if observation_dim == 572 and 156 <= action_count <= 159:
        version = f"deprecated-m9-dynamic-home-obs572-act{action_count}"
    return SpaceSignature(observation_dim, action_count, version)


def validate_spaces(
    model_observation_space: Any,
    model_action_space: Any,
    env_observation_space: Any,
    env_action_space: Any,
    *,
    context: str = "checkpoint",
) -> SpaceSignature:
    """验证模型与环境 schema 一致，失败时给出可操作的版本错误。"""
    model = _space_signature(model_observation_space, model_action_space)
    env = _space_signature(env_observation_space, env_action_space)
    if (
        model.observation_dim != env.observation_dim
        or model.action_count != env.action_count
    ):
        raise RLSchemaMismatchError(
            f"RL schema mismatch ({context}): model={model.version} "
            f"[obs={model.observation_dim}, act={model.action_count}], "
            f"env={env.version} [obs={env.observation_dim}, act={env.action_count}]. "
            "不同动作/观察语义的 checkpoint 不可直接恢复；请使用对应旧环境评估，"
            "或在新 schema 下重新训练/显式迁移。"
        )
    return env


def validate_model_against_env(
    model: Any,
    env: Any,
    *,
    context: str = "loaded model",
) -> SpaceSignature:
    """验证已加载模型与裸/Vec 环境的空间。"""
    return validate_spaces(
        model.observation_space,
        model.action_space,
        env.observation_space,
        env.action_space,
        context=context,
    )


def validate_checkpoint_against_env(
    checkpoint: str | Path,
    env: Any,
    *,
    context: str = "checkpoint",
) -> SpaceSignature:
    """在 SB3 ``load(..., env=...)`` 前读取 zip 空间并给出明确版本错误。"""
    from stable_baselines3.common.save_util import load_from_zip_file

    data, _params, _variables = load_from_zip_file(
        str(checkpoint), device="cpu", print_system_info=False,
    )
    if data is None or "observation_space" not in data or "action_space" not in data:
        raise RLSchemaMismatchError(
            f"RL schema mismatch ({context}): checkpoint 未包含空间元数据：{checkpoint}"
        )
    return validate_spaces(
        data["observation_space"],
        data["action_space"],
        env.observation_space,
        env.action_space,
        context=f"{context}: {checkpoint}",
    )
