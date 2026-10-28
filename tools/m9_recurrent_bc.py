"""Incremental recurrent behavior cloning for an existing M9 checkpoint.

Unlike ``rl.bc_migrate``, this tool does not create a fresh random LSTM.  It
loads a trained recurrent PPO checkpoint and makes small supervised updates to
the actor only.  Two sequence datasets are collected before training:

* ``combat_retarget`` oracle trajectories teach first-kill -> second-target
  replanning in the same hidden state;
* deterministic 1v1 trajectories from the source checkpoint anchor behavior
  that the checkpoint already knows.

The critic and feature extractor are frozen.  Every loss is evaluated over a
complete episode with a zero initial recurrent state and the original per-step
action mask, so action labels and LSTM timing stay aligned.
"""

from __future__ import annotations

import argparse
import contextlib
from dataclasses import dataclass
import io
import json
import os
from pathlib import Path
import random
import sys
from typing import Iterable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

import numpy as np
import torch

from engine.experiments import set_profile
from rl.action_space import apply_action_contract, idx_to_command
from rl.env import BadtimeWarEnv
from rl.maskable_recurrent import MaskableRecurrentPPO
from rl.obs_builder_m9 import M9_BOW_CONTEXT_BASE, M9_BOW_CONTEXT_DIM
from tools.rung0_chain_probe import (
    _bow_module_oracle_action,
    _bow_loadout_oracle_action,
    _economy_pursuit_finish_oracle_action,
    _economy_pursuit_oracle_action, _oracle_action,
    _attribute_armor_oracle_action, _detection_oracle_action,
    _DETECTION_COMMANDS, _detection_skill_mask,
    _assassin_opening_prep_needed,
    _assassin_opening_prep_skill_mask,
    _fire_antiarmor_oracle_action, _sharpen_oracle_action,
    _fire_antiarmor_skill_mask,
    _natural_bow_loadout_skill_mask,
    _survival_heal_oracle_action,
    _target_commitment_mask, _verb,
)


PROFILE = "m9-rfc"
_BOW_LOADOUT_MODULES = ("力量", "火矢", "魔导", "穿甲", "冲击", "无限")


def _bow_loadout_quotas(episodes: int) -> dict[str, int]:
    """Return an exact near-uniform quota instead of trusting random balance."""
    base, remainder = divmod(max(int(episodes), 0), len(_BOW_LOADOUT_MODULES))
    return {
        name: base + int(index < remainder)
        for index, name in enumerate(_BOW_LOADOUT_MODULES)
    }


def _source_policy_observation(obs: np.ndarray, scenario: str) -> np.ndarray:
    """Keep old checkpoints on their trained all-zero option-tail contract."""
    if scenario != "equipment_bow_loadout":
        return obs
    sanitized = np.asarray(obs, dtype=np.float32).copy()
    start = M9_BOW_CONTEXT_BASE
    end = start + M9_BOW_CONTEXT_DIM
    if sanitized.shape[-1] >= end:
        sanitized[..., start:end] = 0.0
    return sanitized


@dataclass(frozen=True)
class EpisodeSequence:
    observations: np.ndarray
    actions: np.ndarray
    masks: np.ndarray
    weights: np.ndarray | None = None

    def __post_init__(self) -> None:
        length = int(self.actions.shape[0])
        if self.observations.ndim != 2 or self.observations.shape[0] != length:
            raise ValueError("episode observations/actions shape mismatch")
        if self.masks.ndim != 2 or self.masks.shape[0] != length:
            raise ValueError("episode masks/actions shape mismatch")
        if length == 0:
            raise ValueError("empty episode sequence")
        if np.any(self.actions < 0) or np.any(self.actions >= self.masks.shape[1]):
            raise ValueError("episode contains an out-of-range action")
        rows = np.arange(length)
        if not bool(np.all(self.masks[rows, self.actions])):
            raise ValueError("episode contains an action illegal under its saved mask")
        if self.weights is not None:
            if self.weights.shape != (length,):
                raise ValueError("episode weights/actions shape mismatch")
            if not bool(np.all(np.isfinite(self.weights))) or bool(
                np.any(self.weights <= 0)
            ):
                raise ValueError("episode weights must be finite and positive")


def _scenario_success_kills(scenario: str) -> int:
    """Return the attributable-kill objective owned by this option lesson."""
    return 1 if scenario == "combat_assassin_pressure" else 2


def _collect_sequences(
    model: MaskableRecurrentPPO,
    *,
    episodes: int,
    seed: int,
    oracle: bool,
    target_scenario: str | None = None,
    success_only: bool = False,
    execute_model_with_oracle_labels: bool = False,
    label_model: MaskableRecurrentPPO | None = None,
    bow_move_weight: float = 64.0,
    bow_install_weight: float = 8.0,
    bow_infinite_weight: float = 1.0,
) -> list[EpisodeSequence]:
    """Collect full recurrent episodes before any model parameter is changed."""
    if episodes <= 0:
        return []
    if oracle and label_model is not None:
        raise ValueError("oracle and label_model are mutually exclusive")

    scenario = target_scenario or ""
    if oracle or target_scenario is not None:
        scenario = target_scenario or "combat_retarget"
        if scenario == "equipment_sharpen_chain":
            max_rounds = 9
        elif scenario in {"equipment_fire_antiarmor", "equipment_bow_loadout"}:
            max_rounds = 40
        elif scenario.startswith("visibility_"):
            max_rounds = 8
        elif scenario.startswith("attribute_"):
            max_rounds = 4
        elif scenario == "survival_armor_repair":
            max_rounds = 16
        elif scenario.startswith("economy_armored_pursuit_supply"):
            max_rounds = 40
        elif scenario == "economy_pursuit_supply":
            max_rounds = 30
        elif scenario.startswith("survival_"):
            max_rounds = 14
        else:
            max_rounds = 25 if scenario.startswith("combat_retarget") else 40
        env = BadtimeWarEnv(
            num_opponents=2,
            max_rounds=max_rounds,
            enable_talents=False,
            rl_talent=0,
            opponent_bots=None,
            opponent_personalities=(
                ["assassin", "assassin"]
                if scenario == "combat_assassin_pressure" else None
            ),
            action_contract=(
                "combat_economy"
                if scenario == "combat_assassin_pressure"
                else "economy_course"
                if scenario.startswith("economy_")
                else "survival_course"
                if scenario.startswith("survival_")
                else "equipment_course"
                if scenario in {
                    "equipment_sharpen_chain", "equipment_fire_antiarmor",
                    "equipment_bow_loadout",
                    "visibility_thermal_chain",
                    "visibility_magic_chain", "visibility_tech_chain",
                    "attribute_magic_armor_choice", "attribute_tech_armor_choice",
                }
                else "combat_core"
            ),
            course_scenario=scenario,
        )
    else:
        env = BadtimeWarEnv(
            num_opponents=1,
            max_rounds=40,
            enable_talents=False,
            rl_talent=0,
            opponent_bots=None,
            action_contract=None,
            course_scenario=None,
        )

    result: list[EpisodeSequence] = []
    bow_quotas = _bow_loadout_quotas(episodes)
    bow_counts = {name: 0 for name in _BOW_LOADOUT_MODULES}
    attempts = 0
    max_attempts = max(episodes * (25 if success_only else 10), episodes)
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            while len(result) < episodes and attempts < max_attempts:
                obs, _ = env.reset(seed=seed + attempts)
                attempts += 1
                bow_case = getattr(
                    env._state, "rl_bow_loadout_target_module", None,
                )
                if (
                    scenario == "equipment_bow_loadout"
                    and bow_counts.get(bow_case, 0) >= bow_quotas.get(bow_case, 0)
                ):
                    continue
                recurrent_state = None
                label_recurrent_state = None
                episode_start = np.array([True])
                observations: list[np.ndarray] = []
                actions: list[int] = []
                masks: list[np.ndarray] = []
                sample_weights: list[float] = []
                done = False
                while not done:
                    mask = env.action_masks()
                    if oracle:
                        action = (
                            _economy_pursuit_oracle_action(
                                mask, env._rl_player, env._state,
                            )
                            if scenario in {
                                "economy_pursuit_supply",
                                "economy_armored_pursuit_supply",
                                "economy_armored_pursuit_supply_slot0",
                            }
                            else _sharpen_oracle_action(
                                mask, env._rl_player, env._state,
                            )
                            if scenario == "equipment_sharpen_chain"
                            else _fire_antiarmor_oracle_action(
                                mask, env._rl_player, env._state,
                            )
                            if scenario == "equipment_fire_antiarmor"
                            else _bow_loadout_oracle_action(
                                mask, env._rl_player, env._state,
                            )
                            if scenario == "equipment_bow_loadout"
                            else _detection_oracle_action(
                                mask, env._rl_player, env._state,
                            )
                            if scenario.startswith("visibility_")
                            else _attribute_armor_oracle_action(
                                mask, env._rl_player, env._state,
                            )
                            if scenario.startswith("attribute_")
                            else _survival_heal_oracle_action(
                                mask, env._rl_player, env._state,
                            )
                            if scenario.startswith("survival_")
                            else _oracle_action(mask, env._rl_player, env._state)
                        )
                        executed_action = action
                        if execute_model_with_oracle_labels:
                            source_obs = _source_policy_observation(obs, scenario)
                            predicted, recurrent_state = model.predict(
                                source_obs[None],
                                state=recurrent_state,
                                episode_start=episode_start,
                                action_masks=mask[None],
                                deterministic=True,
                            )
                            executed_action = int(predicted[0])
                    else:
                        source_obs = _source_policy_observation(obs, scenario)
                        executed, recurrent_state = model.predict(
                            source_obs[None],
                            state=recurrent_state,
                            episode_start=episode_start,
                            action_masks=mask[None],
                            deterministic=True,
                        )
                        executed_action = int(executed[0])
                        action = executed_action
                        if label_model is not None:
                            labelled, label_recurrent_state = label_model.predict(
                                source_obs[None],
                                state=label_recurrent_state,
                                episode_start=episode_start,
                                action_masks=mask[None],
                                deterministic=True,
                            )
                            action = int(labelled[0])

                    observations.append(np.asarray(obs, dtype=np.float32).copy())
                    actions.append(int(action))
                    masks.append(np.asarray(mask, dtype=bool).copy())
                    weight = 1.0
                    if scenario in {
                        "economy_pursuit_supply",
                        "economy_armored_pursuit_supply",
                        "economy_armored_pursuit_supply_slot0",
                    } and not env._choose_mode:
                        command = idx_to_command(
                            int(action), env._rl_player, env._state,
                        )
                        if command == "move 商店":
                            # 每局仅一次，未加权模型在 100/100 离线状态都把它
                            # 错判成 lock；它是进入正确状态分布的根动作。
                            weight = 64.0
                        elif command == "interact 打工":
                            # 旧模型只命中约一半打工标签；缺钱时的转换同样是
                            # 严格前置，但出现次数约为 move 的两倍。
                            weight = 32.0
                        elif command == "interact 箭矢补给":
                            # s138 已能 100% 区分补给，保留温和权重防遗忘。
                            weight = 8.0
                    elif scenario == "equipment_bow_loadout" and not env._choose_mode:
                        command = idx_to_command(
                            int(action), env._rl_player, env._state,
                        )
                        # 第一拍的四设施选择承载真正的条件分类；第二拍已被
                        # 地点合法性缩到一至两个模块。早期 4:16 权重让模型
                        # 取得虚假的 50% 总准确率，却把所有首拍坍缩到军基。
                        weight = (
                            float(bow_install_weight)
                            if command.startswith("interact ")
                            else float(bow_move_weight)
                        )
                        if bow_case == "无限":
                            weight *= float(bow_infinite_weight)
                    sample_weights.append(weight)
                    obs, _reward, terminated, truncated, _info = env.step(executed_action)
                    done = bool(terminated or truncated)
                    if (
                        scenario == "combat_assassin_pressure"
                        and int(getattr(env._rl_player, "kill_count", 0) or 0) >= 1
                    ):
                        # The opening option terminates at its first kill; do
                        # not imitate actions owned by the continuation policy.
                        done = True
                    episode_start = np.array([done])

                # 生存专家只负责首拍机制选择，之后立即交还主战斗策略。
                # 完整 oracle 轨迹仍被执行以供 success_only 验证，但不把源策略
                # 已掌握的后续 7 步混入监督梯度、淹没唯一的新动作标签。
                keep = (
                    1 if scenario.startswith("survival_")
                    or scenario.startswith("visibility_")
                    or scenario.startswith("attribute_")
                    or scenario == "equipment_fire_antiarmor"
                    else 2 if scenario == "equipment_bow_loadout"
                    else 2 if scenario == "equipment_sharpen_chain"
                    else len(actions)
                )
                sequence = EpisodeSequence(
                    observations=np.asarray(observations[:keep], dtype=np.float32),
                    actions=np.asarray(actions[:keep], dtype=np.int64),
                    masks=np.asarray(masks[:keep], dtype=bool),
                    weights=(
                        np.asarray(sample_weights[:keep], dtype=np.float32)
                        if scenario in {
                            "economy_pursuit_supply",
                            "economy_armored_pursuit_supply",
                            "economy_armored_pursuit_supply_slot0",
                            "equipment_bow_loadout",
                        }
                        else None
                    ),
                )
                if success_only and int(
                    getattr(env._rl_player, "kill_count", 0) or 0
                ) < _scenario_success_kills(scenario):
                    continue
                result.append(sequence)
                if scenario == "equipment_bow_loadout":
                    bow_counts[bow_case] += 1
        finally:
            env.close()
    if len(result) < episodes:
        raise RuntimeError(
            f"only collected {len(result)}/{episodes} successful target episodes "
            f"after {attempts} attempts"
        )
    return result


def _collect_hybrid_fullhp_sequences(
    student_model: MaskableRecurrentPPO,
    prekill_teacher_model: MaskableRecurrentPPO,
    *,
    episodes: int,
    seed: int,
    execute_student: bool,
    success_only: bool,
) -> list[EpisodeSequence]:
    """正常 20HP 双教师：首杀前保留强开局策略，首杀后切 oracle。"""
    if episodes <= 0:
        return []
    env = BadtimeWarEnv(
        num_opponents=2,
        max_rounds=40,
        enable_talents=False,
        rl_talent=0,
        opponent_bots=None,
        action_contract="combat_core",
        course_scenario="combat_multi_full_hp",
    )
    result: list[EpisodeSequence] = []
    attempts = 0
    max_attempts = max(episodes * (25 if success_only else 10), episodes)
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            while len(result) < episodes and attempts < max_attempts:
                obs, _ = env.reset(seed=seed + attempts)
                attempts += 1
                student_state = None
                teacher_state = None
                episode_start = np.array([True])
                observations: list[np.ndarray] = []
                actions: list[int] = []
                masks: list[np.ndarray] = []
                done = False
                while not done:
                    mask = env.action_masks()
                    teacher_predicted, teacher_state = prekill_teacher_model.predict(
                        obs[None],
                        state=teacher_state,
                        episode_start=episode_start,
                        action_masks=mask[None],
                        deterministic=True,
                    )
                    if int(getattr(env._rl_player, "kill_count", 0) or 0) >= 1:
                        label_action = _oracle_action(
                            mask, env._rl_player, env._state,
                        )
                    else:
                        label_action = int(teacher_predicted[0])

                    executed_action = label_action
                    if execute_student:
                        student_predicted, student_state = student_model.predict(
                            obs[None],
                            state=student_state,
                            episode_start=episode_start,
                            action_masks=mask[None],
                            deterministic=True,
                        )
                        executed_action = int(student_predicted[0])

                    observations.append(np.asarray(obs, dtype=np.float32).copy())
                    actions.append(int(label_action))
                    masks.append(np.asarray(mask, dtype=bool).copy())
                    obs, _reward, terminated, truncated, _info = env.step(
                        executed_action,
                    )
                    done = bool(terminated or truncated)
                    episode_start = np.array([done])

                sequence = EpisodeSequence(
                    observations=np.asarray(observations, dtype=np.float32),
                    actions=np.asarray(actions, dtype=np.int64),
                    masks=np.asarray(masks, dtype=bool),
                )
                if success_only and int(
                    getattr(env._rl_player, "kill_count", 0) or 0
                ) < 2:
                    continue
                result.append(sequence)
        finally:
            env.close()
    if len(result) < episodes:
        raise RuntimeError(
            f"only collected {len(result)}/{episodes} successful hybrid fullhp "
            f"episodes after {attempts} attempts"
        )
    return result


def _collect_real_postkill_economy_sequences(
    student_model: MaskableRecurrentPPO,
    prekill_teacher_model: MaskableRecurrentPPO,
    *,
    episodes: int,
    seed: int,
    execute_student: bool,
    success_only: bool,
) -> list[EpisodeSequence]:
    """Collect economy-oracle labels after s142 earns a real first kill.

    The opening teacher acts in normal ``combat_economy``.  The continuation
    student is warmed from the first observation under its deployment contract,
    then either executes its own post-kill actions (DAgger) or observes the
    economy oracle's successful recovery trajectory.
    """
    if episodes <= 0:
        return []
    env = BadtimeWarEnv(
        num_opponents=2,
        max_rounds=40,
        enable_talents=False,
        rl_talent=0,
        opponent_bots=None,
        action_contract="combat_economy",
        course_scenario=None,
    )
    result: list[EpisodeSequence] = []
    attempts = 0
    max_attempts = max(episodes * (50 if success_only else 10), episodes)
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            while len(result) < episodes and attempts < max_attempts:
                episode_seed = seed + attempts
                attempts += 1
                random.seed(episode_seed)
                np.random.seed(episode_seed)
                obs, _ = env.reset(seed=episode_seed)
                student_state = None
                teacher_state = None
                episode_start = np.array([True])
                observations: list[np.ndarray] = []
                actions: list[int] = []
                masks: list[np.ndarray] = []
                weights: list[float] = []
                saw_first_kill = False
                done = False
                while not done:
                    full_mask = env.action_masks()
                    expert_mask = apply_action_contract(
                        full_mask,
                        "economy_pursuit_finish",
                        choose_mode=env._choose_mode,
                    )
                    student_predicted, student_state = student_model.predict(
                        obs[None],
                        state=student_state,
                        episode_start=episode_start,
                        action_masks=expert_mask[None],
                        deterministic=True,
                    )
                    student_action = int(student_predicted[0])
                    after_kill = int(
                        getattr(env._rl_player, "kill_count", 0) or 0
                    ) >= 1
                    if after_kill:
                        saw_first_kill = True
                        label_action = _economy_pursuit_finish_oracle_action(
                            expert_mask, env._rl_player, env._state,
                        )
                        executed_action = (
                            student_action if execute_student else label_action
                        )
                        command = idx_to_command(
                            label_action, env._rl_player, env._state,
                        )
                        sample_weight = {
                            "move 商店": 64.0,
                            "move 医院": 64.0,
                            "interact 打工": 32.0,
                            "interact 箭矢补给": 8.0,
                            "attack AI_0 小刀": 32.0,
                            "attack AI_1 小刀": 32.0,
                        }.get(command, 1.0)
                    else:
                        teacher_predicted, teacher_state = (
                            prekill_teacher_model.predict(
                                obs[None],
                                state=teacher_state,
                                episode_start=episode_start,
                                action_masks=full_mask[None],
                                deterministic=True,
                            )
                        )
                        executed_action = int(teacher_predicted[0])
                        # Preserve the student's warm-up behavior without
                        # letting opening labels dominate post-kill corrections.
                        label_action = student_action
                        sample_weight = 0.05

                    observations.append(np.asarray(obs, dtype=np.float32).copy())
                    actions.append(int(label_action))
                    masks.append(np.asarray(expert_mask, dtype=bool).copy())
                    weights.append(float(sample_weight))
                    obs, _reward, terminated, truncated, _info = env.step(
                        executed_action,
                    )
                    done = bool(terminated or truncated)
                    episode_start = np.array([done])

                final_kills = int(getattr(env._rl_player, "kill_count", 0) or 0)
                if not saw_first_kill:
                    continue
                if success_only and final_kills < 2:
                    continue
                result.append(EpisodeSequence(
                    observations=np.asarray(observations, dtype=np.float32),
                    actions=np.asarray(actions, dtype=np.int64),
                    masks=np.asarray(masks, dtype=bool),
                    weights=np.asarray(weights, dtype=np.float32),
                ))
        finally:
            env.close()
    if len(result) < episodes:
        raise RuntimeError(
            f"only collected {len(result)}/{episodes} real postkill economy "
            f"episodes after {attempts} attempts"
        )
    return result


def _collect_real_assassin_handoff_sequences(
    model: MaskableRecurrentPPO,
    *,
    episodes: int,
    seed: int,
) -> list[EpisodeSequence]:
    """Collect successful opening fragments after a real detector handoff.

    Both opponents remain complete assassin-personality BasicAI controllers.
    The source policy executes the ordinary opening, the deterministic
    visibility option owns move-to-facility and detector acquisition, and the
    actor state is reset exactly as in deployment.  Only actions after that
    option terminates and through the first attributable kill are supervised.
    """
    if episodes <= 0:
        return []
    env = BadtimeWarEnv(
        num_opponents=2,
        max_rounds=40,
        enable_talents=False,
        rl_talent=0,
        opponent_bots=None,
        opponent_personalities=["assassin", "assassin"],
        action_contract=None,
        course_scenario=None,
    )
    result: list[EpisodeSequence] = []
    attempts = 0
    max_attempts = max(episodes * 15, episodes)
    detector_commands = set(_DETECTION_COMMANDS.values())
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            while len(result) < episodes and attempts < max_attempts:
                episode_seed = seed + attempts
                attempts += 1
                random.seed(episode_seed)
                np.random.seed(episode_seed)
                obs, _ = env.reset(seed=episode_seed)
                recurrent_state = None
                episode_start = np.array([True])
                handoff_complete = False
                observations: list[np.ndarray] = []
                actions: list[int] = []
                masks: list[np.ndarray] = []
                done = False
                while not done:
                    full_mask = env.action_masks()
                    base_mask = apply_action_contract(
                        full_mask,
                        "combat_economy",
                        choose_mode=env._choose_mode,
                    )
                    predicted, recurrent_state = model.predict(
                        obs[None],
                        state=recurrent_state,
                        episode_start=episode_start,
                        action_masks=base_mask[None],
                        deterministic=True,
                    )
                    base_action = int(predicted[0])
                    detection_action = None
                    if not env._choose_mode:
                        focused, target_id, _attr = _detection_skill_mask(
                            full_mask, env._rl_player, env._state,
                        )
                        if target_id is not None:
                            legal = np.flatnonzero(focused)
                            if legal.size == 1:
                                detection_action = int(legal[0])
                    executed_action = (
                        detection_action
                        if detection_action is not None else base_action
                    )
                    if handoff_complete and detection_action is None:
                        observations.append(
                            np.asarray(obs, dtype=np.float32).copy()
                        )
                        actions.append(base_action)
                        masks.append(np.asarray(base_mask, dtype=bool).copy())

                    command = None
                    if detection_action is not None:
                        command = idx_to_command(
                            detection_action, env._rl_player, env._state,
                        )
                    obs, _reward, terminated, truncated, _info = env.step(
                        executed_action,
                    )
                    done = bool(terminated or truncated)
                    if command in detector_commands:
                        handoff_complete = True
                        recurrent_state = None
                    if (
                        handoff_complete
                        and int(getattr(env._rl_player, "kill_count", 0) or 0) >= 1
                    ):
                        done = True
                    episode_start = np.array([done])

                if (
                    not handoff_complete
                    or int(getattr(env._rl_player, "kill_count", 0) or 0) < 1
                    or not actions
                ):
                    continue
                result.append(EpisodeSequence(
                    observations=np.asarray(observations, dtype=np.float32),
                    actions=np.asarray(actions, dtype=np.int64),
                    masks=np.asarray(masks, dtype=bool),
                ))
        finally:
            env.close()
    if len(result) < episodes:
        raise RuntimeError(
            f"only collected {len(result)}/{episodes} successful real assassin "
            f"handoffs after {attempts} attempts"
        )
    return result


def _collect_real_assassin_postkill_sequences(
    student_model: MaskableRecurrentPPO,
    opening_model: MaskableRecurrentPPO,
    antiarmor_model: MaskableRecurrentPPO,
    *,
    episodes: int,
    seed: int,
) -> list[EpisodeSequence]:
    """Collect successful second-kill labels in full double-assassin games.

    The opening matches the retained hierarchical probe: s142 acts through the
    combat-economy contract, the visibility option acquires magic detection,
    and the public double-magic state may prepare ceramic armor plus arrows.
    The continuation student is warmed from reset.  After a real first kill,
    the economy pursuit oracle both acts and labels; only attributable second-
    kill episodes are kept.
    """
    if episodes <= 0:
        return []
    env = BadtimeWarEnv(
        num_opponents=2,
        max_rounds=40,
        enable_talents=False,
        rl_talent=0,
        opponent_bots=None,
        opponent_personalities=["assassin", "assassin"],
        action_contract=None,
        course_scenario=None,
    )
    result: list[EpisodeSequence] = []
    attempts = 0
    max_attempts = max(episodes * 100, episodes)
    detector_commands = set(_DETECTION_COMMANDS.values())
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            while len(result) < episodes and attempts < max_attempts:
                episode_seed = seed + attempts
                attempts += 1
                random.seed(episode_seed)
                np.random.seed(episode_seed)
                obs, _ = env.reset(seed=episode_seed)
                student_state = None
                opening_state = None
                antiarmor_state = None
                episode_start = np.array([True])
                prep_active = False
                saw_first_kill = False
                observations: list[np.ndarray] = []
                actions: list[int] = []
                masks: list[np.ndarray] = []
                weights: list[float] = []
                done = False
                while not done:
                    full_mask = env.action_masks()
                    opening_mask = apply_action_contract(
                        full_mask,
                        "combat_economy",
                        choose_mode=env._choose_mode,
                    )
                    continuation_mask = apply_action_contract(
                        full_mask,
                        "economy_pursuit_finish",
                        choose_mode=env._choose_mode,
                    )
                    student_predicted, student_state = student_model.predict(
                        obs[None],
                        state=student_state,
                        episode_start=episode_start,
                        action_masks=continuation_mask[None],
                        deterministic=True,
                    )
                    student_action = int(student_predicted[0])
                    antiarmor_target = None
                    antiarmor_action = None
                    antiarmor_mask = full_mask
                    if not env._choose_mode:
                        (
                            antiarmor_mask,
                            antiarmor_target,
                            _antiarmor_kind,
                        ) = _fire_antiarmor_skill_mask(
                            full_mask, env._rl_player, env._state,
                        )
                    antiarmor_predicted, antiarmor_state = (
                        antiarmor_model.predict(
                            obs[None],
                            state=antiarmor_state,
                            episode_start=episode_start,
                            action_masks=antiarmor_mask[None],
                            deterministic=True,
                        )
                    )
                    if antiarmor_target is not None:
                        antiarmor_action = int(antiarmor_predicted[0])
                    after_kill = int(
                        getattr(env._rl_player, "kill_count", 0) or 0
                    ) >= 1
                    selected_detector = False
                    detector_command = None

                    if after_kill:
                        saw_first_kill = True
                        if antiarmor_action is not None:
                            # External option action: execute it so the oracle
                            # sees the deployed state, but do not force the
                            # continuation head to imitate a masked-out module.
                            executed_action = antiarmor_action
                            label_action = student_action
                            sample_weight = 0.05
                        else:
                            label_action = _economy_pursuit_finish_oracle_action(
                                continuation_mask, env._rl_player, env._state,
                            )
                            executed_action = int(label_action)
                            command = idx_to_command(
                                label_action, env._rl_player, env._state,
                            )
                            sample_weight = {
                                "move 商店": 64.0,
                                "move 医院": 64.0,
                                "interact 打工": 32.0,
                                "interact 箭矢补给": 8.0,
                                "attack AI_0 小刀": 32.0,
                                "attack AI_1 小刀": 32.0,
                            }.get(command, 1.0)
                    else:
                        if (
                            not prep_active
                            and not env._choose_mode
                            and _assassin_opening_prep_needed(
                                env._rl_player, env._state,
                            )
                        ):
                            prep_active = True

                        routed_action = None
                        if prep_active and not env._choose_mode:
                            prep_mask, prep_kind, prep_done = (
                                _assassin_opening_prep_skill_mask(
                                    full_mask, env._rl_player, env._state,
                                )
                            )
                            if prep_kind is not None:
                                legal = np.flatnonzero(prep_mask)
                                if legal.size == 1:
                                    routed_action = int(legal[0])
                            elif prep_done:
                                prep_active = False

                        if routed_action is None and not env._choose_mode:
                            detection_mask, target_id, _attr = (
                                _detection_skill_mask(
                                    full_mask, env._rl_player, env._state,
                                )
                            )
                            if target_id is not None:
                                legal = np.flatnonzero(detection_mask)
                                if legal.size == 1:
                                    routed_action = int(legal[0])
                                    selected_detector = True
                                    detector_command = idx_to_command(
                                        routed_action,
                                        env._rl_player,
                                        env._state,
                                    )

                        opening_predicted, opening_state = opening_model.predict(
                            obs[None],
                            state=opening_state,
                            episode_start=episode_start,
                            action_masks=opening_mask[None],
                            deterministic=True,
                        )
                        executed_action = (
                            routed_action
                            if routed_action is not None
                            else int(opening_predicted[0])
                        )
                        # Preserve the warmed student's recurrent trajectory;
                        # opening labels are anchors, not the learning target.
                        label_action = student_action
                        sample_weight = 0.05

                    observations.append(np.asarray(obs, dtype=np.float32).copy())
                    actions.append(int(label_action))
                    masks.append(np.asarray(continuation_mask, dtype=bool).copy())
                    weights.append(float(sample_weight))
                    obs, _reward, terminated, truncated, _info = env.step(
                        executed_action,
                    )
                    done = bool(terminated or truncated)
                    if selected_detector and detector_command in detector_commands:
                        opening_state = None
                        prep_active = _assassin_opening_prep_needed(
                            env._rl_player, env._state,
                        )
                    episode_start = np.array([done])

                if not saw_first_kill:
                    continue
                if int(getattr(env._rl_player, "kill_count", 0) or 0) < 2:
                    continue
                result.append(EpisodeSequence(
                    observations=np.asarray(observations, dtype=np.float32),
                    actions=np.asarray(actions, dtype=np.int64),
                    masks=np.asarray(masks, dtype=bool),
                    weights=np.asarray(weights, dtype=np.float32),
                ))
        finally:
            env.close()
    if len(result) < episodes:
        raise RuntimeError(
            f"only collected {len(result)}/{episodes} successful real assassin "
            f"postkill episodes after {attempts} attempts"
        )
    return result


def _collect_natural_postkill_bow_sequences(
    student_model: MaskableRecurrentPPO,
    opening_model: MaskableRecurrentPPO,
    continuation_model: MaskableRecurrentPPO,
    *,
    episodes: int,
    seed: int,
    execute_student: bool,
    bow_move_weight: float,
    bow_install_weight: float,
) -> list[EpisodeSequence]:
    """Collect cold-start two-step bow labels from real three-player games.

    s142 and s148 play the same opening/continuation contracts used by the
    deployment probe.  Once the label-free natural router sees a clear module
    need, the sequence begins at that observation with a zero recurrent state.
    Oracle samples expose the intended move→install path; DAgger samples let
    s158 act and label the state reached after a wrong first action.
    """
    if episodes <= 0:
        return []
    env = BadtimeWarEnv(
        num_opponents=2,
        max_rounds=40,
        enable_talents=False,
        rl_talent=0,
        opponent_bots=None,
        action_contract=None,
        course_scenario=None,
        enable_bow_context=True,
    )
    env._rl_base_action_contract = "combat_economy"
    result: list[EpisodeSequence] = []
    attempts = 0
    max_attempts = max(episodes * 100, episodes)
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            while len(result) < episodes and attempts < max_attempts:
                episode_seed = seed + attempts
                attempts += 1
                random.seed(episode_seed)
                np.random.seed(episode_seed)
                obs, _ = env.reset(seed=episode_seed)
                opening_state = None
                continuation_state = None
                expert_state = None
                episode_start = np.array([True])
                observations: list[np.ndarray] = []
                actions: list[int] = []
                masks: list[np.ndarray] = []
                weights: list[float] = []
                routed_module: str | None = None
                done = False
                while not done:
                    full_mask = env.action_masks()
                    old_obs = _source_policy_observation(
                        obs, "equipment_bow_loadout",
                    )
                    after_kill = int(
                        getattr(env._rl_player, "kill_count", 0) or 0
                    ) >= 1

                    if routed_module is None and after_kill and not env._choose_mode:
                        (
                            _routed_mask,
                            routed_target,
                            natural_module,
                        ) = _natural_bow_loadout_skill_mask(
                            full_mask, env._rl_player, env._state,
                        )
                        if routed_target is not None:
                            routed_module = natural_module

                    if routed_module is not None:
                        expert_mask = apply_action_contract(
                            full_mask,
                            "bow_loadout_drill",
                            choose_mode=env._choose_mode,
                        )
                        label_action = _bow_module_oracle_action(
                            expert_mask,
                            env._rl_player,
                            env._state,
                            routed_module,
                        )
                        if label_action is None:
                            break
                        expert_start = np.array([expert_state is None])
                        predicted, expert_state = student_model.predict(
                            obs[None],
                            state=expert_state,
                            episode_start=expert_start,
                            action_masks=expert_mask[None],
                            deterministic=True,
                        )
                        executed_action = (
                            int(predicted[0])
                            if execute_student else int(label_action)
                        )
                        label_command = idx_to_command(
                            int(label_action), env._rl_player, env._state,
                        )
                        observations.append(
                            np.asarray(obs, dtype=np.float32).copy()
                        )
                        actions.append(int(label_action))
                        masks.append(np.asarray(expert_mask, dtype=bool).copy())
                        weights.append(
                            float(bow_install_weight)
                            if label_command.startswith("interact ")
                            else float(bow_move_weight)
                        )
                    else:
                        opening_mask = apply_action_contract(
                            full_mask,
                            "combat_economy",
                            choose_mode=env._choose_mode,
                        )
                        continuation_mask = apply_action_contract(
                            full_mask,
                            "economy_pursuit_finish",
                            choose_mode=env._choose_mode,
                        )
                        opening_predicted, opening_state = opening_model.predict(
                            old_obs[None],
                            state=opening_state,
                            episode_start=episode_start,
                            action_masks=opening_mask[None],
                            deterministic=True,
                        )
                        continuation_predicted, continuation_state = (
                            continuation_model.predict(
                                old_obs[None],
                                state=continuation_state,
                                episode_start=episode_start,
                                action_masks=continuation_mask[None],
                                deterministic=True,
                            )
                        )
                        executed_action = int(
                            continuation_predicted[0]
                            if after_kill else opening_predicted[0]
                        )

                    obs, _reward, terminated, truncated, _info = env.step(
                        executed_action,
                    )
                    done = bool(terminated or truncated)
                    episode_start = np.array([done])
                    if routed_module is not None and len(actions) >= 2:
                        break

                if actions:
                    result.append(EpisodeSequence(
                        observations=np.asarray(observations, dtype=np.float32),
                        actions=np.asarray(actions, dtype=np.int64),
                        masks=np.asarray(masks, dtype=bool),
                        weights=np.asarray(weights, dtype=np.float32),
                    ))
        finally:
            env.close()
    if len(result) < episodes:
        raise RuntimeError(
            f"only collected {len(result)}/{episodes} natural postkill bow "
            f"episodes after {attempts} attempts"
        )
    return result


def _collect_commitment_fullhp_sequences(
    student_model: MaskableRecurrentPPO,
    teacher_model: MaskableRecurrentPPO,
    *,
    episodes: int,
    seed: int,
    execute_student: bool,
    postkill_weight: float,
    transition_weight: float,
) -> list[EpisodeSequence]:
    """Distil successful target commitment into an unrestricted policy.

    The teacher sees the normal action mask before the first kill and a focused
    live-target mask afterwards.  Crucially, sequences store the *normal* mask,
    so BC must raise the chosen action above all unrelated legal actions rather
    than depending on the curriculum restriction at inference time.
    """
    if episodes <= 0:
        return []
    env = BadtimeWarEnv(
        num_opponents=2,
        max_rounds=40,
        enable_talents=False,
        rl_talent=0,
        opponent_bots=None,
        action_contract="combat_core",
        course_scenario="combat_multi_full_hp",
    )
    result: list[EpisodeSequence] = []
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            for episode in range(episodes):
                obs, _ = env.reset(seed=seed + episode)
                student_state = None
                teacher_state = None
                episode_start = np.array([True])
                observations: list[np.ndarray] = []
                actions: list[int] = []
                masks: list[np.ndarray] = []
                weights: list[float] = []
                done = False
                while not done:
                    mask = env.action_masks()
                    teacher_mask = mask
                    after_kill = int(
                        getattr(env._rl_player, "kill_count", 0) or 0
                    ) >= 1
                    if after_kill:
                        teacher_mask, _ = _target_commitment_mask(
                            mask, env._rl_player, env._state,
                        )
                    teacher_predicted, teacher_state = teacher_model.predict(
                        obs[None],
                        state=teacher_state,
                        episode_start=episode_start,
                        action_masks=teacher_mask[None],
                        deterministic=True,
                    )
                    label_action = int(teacher_predicted[0])
                    executed_action = label_action
                    if execute_student:
                        student_predicted, student_state = student_model.predict(
                            obs[None],
                            state=student_state,
                            episode_start=episode_start,
                            action_masks=mask[None],
                            deterministic=True,
                        )
                        executed_action = int(student_predicted[0])

                    observations.append(np.asarray(obs, dtype=np.float32).copy())
                    actions.append(label_action)
                    masks.append(np.asarray(mask, dtype=bool).copy())
                    sample_weight = 1.0
                    if after_kill:
                        teacher_command = idx_to_command(
                            label_action, env._rl_player, env._state,
                        )
                        sample_weight = (
                            transition_weight
                            if _verb(teacher_command) in {
                                "wake", "interact", "lock", "move", "find",
                            }
                            else postkill_weight
                        )
                    weights.append(float(sample_weight))
                    obs, _reward, terminated, truncated, _info = env.step(
                        executed_action,
                    )
                    done = bool(terminated or truncated)
                    episode_start = np.array([done])

                result.append(EpisodeSequence(
                    observations=np.asarray(observations, dtype=np.float32),
                    actions=np.asarray(actions, dtype=np.int64),
                    masks=np.asarray(masks, dtype=bool),
                    weights=np.asarray(weights, dtype=np.float32),
                ))
        finally:
            env.close()
    return result


def _actor_initial_state(policy, *, batch_size: int = 1):
    shape = (
        int(policy.lstm_actor.num_layers),
        int(batch_size),
        int(policy.lstm_actor.hidden_size),
    )
    hidden = torch.zeros(shape, dtype=torch.float32, device=policy.device)
    cell = torch.zeros_like(hidden)
    return hidden, cell


def _sequence_log_probs(policy, sequence: EpisodeSequence) -> torch.Tensor:
    obs = torch.as_tensor(
        sequence.observations, dtype=torch.float32, device=policy.device,
    )
    actions = torch.as_tensor(
        sequence.actions, dtype=torch.long, device=policy.device,
    )
    starts = torch.zeros(len(sequence.actions), dtype=torch.float32, device=policy.device)
    starts[0] = 1.0
    distribution, _ = policy.get_distribution(
        obs,
        _actor_initial_state(policy),
        starts,
        action_masks=sequence.masks,
    )
    return distribution.log_prob(actions)


def _mean_nll(policy, sequences: Sequence[EpisodeSequence]) -> torch.Tensor:
    if not sequences:
        return torch.zeros((), dtype=torch.float32, device=policy.device)
    weighted_losses: list[torch.Tensor] = []
    weights: list[torch.Tensor] = []
    for sequence in sequences:
        log_probs = _sequence_log_probs(policy, sequence)
        sample_weights = torch.as_tensor(
            sequence.weights if sequence.weights is not None
            else np.ones(len(sequence.actions), dtype=np.float32),
            dtype=torch.float32,
            device=policy.device,
        )
        weighted_losses.append(-log_probs * sample_weights)
        weights.append(sample_weights)
    return torch.cat(weighted_losses).sum() / torch.cat(weights).sum()


@torch.no_grad()
def _dataset_metrics(policy, sequences: Sequence[EpisodeSequence]) -> dict[str, float]:
    policy.set_training_mode(False)
    correct = 0
    samples = 0
    total_nll = 0.0
    for sequence in sequences:
        obs = torch.as_tensor(
            sequence.observations, dtype=torch.float32, device=policy.device,
        )
        starts = torch.zeros(len(sequence.actions), dtype=torch.float32, device=policy.device)
        starts[0] = 1.0
        distribution, _ = policy.get_distribution(
            obs,
            _actor_initial_state(policy),
            starts,
            action_masks=sequence.masks,
        )
        actions = torch.as_tensor(
            sequence.actions, dtype=torch.long, device=policy.device,
        )
        log_probs = distribution.log_prob(actions)
        predicted = distribution.get_actions(deterministic=True)
        correct += int((predicted == actions).sum().item())
        samples += int(actions.numel())
        total_nll += float((-log_probs).sum().item())
    return {
        "samples": float(samples),
        "accuracy": correct / max(samples, 1),
        "nll": total_nll / max(samples, 1),
    }


def _chunks(items: Sequence[EpisodeSequence], size: int) -> list[list[EpisodeSequence]]:
    return [list(items[i:i + size]) for i in range(0, len(items), size)]


def train_incremental_bc(
    model: MaskableRecurrentPPO,
    target_sequences: Sequence[EpisodeSequence],
    anchor_sequences: Sequence[EpisodeSequence],
    *,
    epochs: int,
    learning_rate: float,
    anchor_weight: float,
    batch_episodes: int,
    seed: int,
) -> list[dict[str, float]]:
    """Update only the recurrent actor while replaying source-policy anchors."""
    if not target_sequences:
        raise ValueError("target_sequences must not be empty")
    if epochs <= 0 or batch_episodes <= 0:
        raise ValueError("epochs and batch_episodes must be positive")

    policy = model.policy
    train_prefixes = ("lstm_actor.", "mlp_extractor.policy_net.", "action_net.")
    trainable: list[torch.nn.Parameter] = []
    for name, parameter in policy.named_parameters():
        parameter.requires_grad_(name.startswith(train_prefixes))
        if parameter.requires_grad:
            trainable.append(parameter)
    optimizer = torch.optim.Adam(trainable, lr=learning_rate)
    rng = random.Random(seed)
    history: list[dict[str, float]] = []

    for epoch in range(1, epochs + 1):
        target_order = list(target_sequences)
        anchor_order = list(anchor_sequences)
        rng.shuffle(target_order)
        rng.shuffle(anchor_order)
        target_batches = _chunks(target_order, batch_episodes)
        anchor_batches = _chunks(anchor_order, batch_episodes)
        n_batches = max(len(target_batches), len(anchor_batches), 1)
        epoch_target = 0.0
        epoch_anchor = 0.0

        policy.set_training_mode(True)
        for batch_idx in range(n_batches):
            target_batch = target_batches[batch_idx % len(target_batches)]
            anchor_batch = (
                anchor_batches[batch_idx % len(anchor_batches)]
                if anchor_batches else []
            )
            target_loss = _mean_nll(policy, target_batch)
            anchor_loss = _mean_nll(policy, anchor_batch)
            loss = target_loss + anchor_weight * anchor_loss
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(trainable, max_norm=0.5)
            optimizer.step()
            epoch_target += float(target_loss.detach().item())
            epoch_anchor += float(anchor_loss.detach().item())

        metrics = {
            "epoch": float(epoch),
            "target_batch_nll": epoch_target / n_batches,
            "anchor_batch_nll": epoch_anchor / n_batches,
        }
        metrics.update({
            f"target_{key}": value
            for key, value in _dataset_metrics(policy, target_sequences).items()
        })
        metrics.update({
            f"anchor_{key}": value
            for key, value in _dataset_metrics(policy, anchor_sequences).items()
        })
        history.append(metrics)
        print(json.dumps(metrics, ensure_ascii=False, sort_keys=True))

    # A later PPO continuation should not inherit optimizer moments from before BC.
    policy.optimizer.state.clear()
    for parameter in policy.parameters():
        parameter.requires_grad_(True)
    return history


def _summarize_lengths(sequences: Iterable[EpisodeSequence]) -> dict[str, float]:
    lengths = [len(sequence.actions) for sequence in sequences]
    return {
        "episodes": float(len(lengths)),
        "samples": float(sum(lengths)),
        "mean_length": float(np.mean(lengths)) if lengths else 0.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="在现有 M9 recurrent PPO 上做增量序列 BC",
    )
    parser.add_argument("--model", required=True, help="源 recurrent PPO checkpoint")
    parser.add_argument("--output", required=True, help="输出 checkpoint (.zip)")
    parser.add_argument("--target-episodes", type=int, default=160)
    parser.add_argument("--anchor-episodes", type=int, default=160)
    parser.add_argument(
        "--multi-anchor-scenarios", default=None,
        help="逗号分隔的多人课程锚点；由 anchor teacher 自己执行并标注",
    )
    parser.add_argument(
        "--multi-anchor-episodes", type=int, default=0,
        help="每个 --multi-anchor-scenarios 场景收集的锚点局数",
    )
    parser.add_argument("--epochs", type=int, default=4)
    parser.add_argument("--batch-episodes", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=1e-5)
    parser.add_argument("--anchor-weight", type=float, default=1.0)
    parser.add_argument("--bow-move-weight", type=float, default=64.0)
    parser.add_argument("--bow-install-weight", type=float, default=8.0)
    parser.add_argument("--bow-infinite-weight", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=103)
    parser.add_argument(
        "--target-scenario",
        choices=[
            "combat_retarget", "combat_retarget_static",
            "combat_multi_quarter_hp", "combat_multi_half_hp",
            "combat_multi_full_hp",
            "combat_assassin_pressure",
            "economy_pursuit_supply",
            "economy_armored_pursuit_supply",
            "economy_armored_pursuit_supply_slot0",
            "survival_heal_counterattack", "survival_armor_counterattack",
            "survival_armor_repair",
            "equipment_sharpen_chain", "equipment_fire_antiarmor",
            "equipment_bow_loadout",
            "visibility_thermal_chain", "visibility_magic_chain",
            "visibility_tech_chain", "attribute_magic_armor_choice",
            "attribute_tech_armor_choice",
        ],
        default="combat_retarget",
    )
    parser.add_argument(
        "--target-scenarios", default=None,
        help="逗号分隔的联合回放场景；设置后覆盖 --target-scenario，"
             "--target-episodes 表示每个场景的接受轨迹数",
    )
    parser.add_argument(
        "--target-success-only", action="store_true",
        help="仅保留达到该 option 亲手击杀职责的目标轨迹",
    )
    parser.add_argument(
        "--target-self-teacher", action="store_true",
        help="目标场景由源模型执行并用其成功动作自标注；用于过滤式成功回放",
    )
    parser.add_argument(
        "--dagger-scenario",
        choices=[
            "combat_retarget", "combat_multi_quarter_hp",
            "combat_multi_half_hp", "combat_multi_full_hp",
        ],
        default=None,
        help="让源策略执行，但在其访问到的每个状态记录 oracle 纠正标签",
    )
    parser.add_argument(
        "--dagger-scenarios", default=None,
        help="逗号分隔的联合 DAgger 场景；覆盖 --dagger-scenario，"
             "--dagger-episodes 表示每个场景的轨迹数",
    )
    parser.add_argument("--dagger-episodes", type=int, default=0)
    parser.add_argument(
        "--anchor-teacher-model", default=None,
        help="可选稳定教师 checkpoint；其自身轨迹用作锚点标签",
    )
    parser.add_argument(
        "--anchor-dagger-episodes", type=int, default=0,
        help="让当前策略执行完整 1v1，由 anchor teacher 逐步标注",
    )
    parser.add_argument(
        "--hybrid-fullhp-teacher-model", default=None,
        help="20HP 双教师的首杀前策略 checkpoint；首杀后自动切 oracle",
    )
    parser.add_argument("--hybrid-fullhp-success-episodes", type=int, default=0)
    parser.add_argument("--hybrid-fullhp-dagger-episodes", type=int, default=0)
    parser.add_argument(
        "--real-postkill-teacher-model", default=None,
        help="真实3人combat_economy首杀前教师；首杀后由经济oracle标注",
    )
    parser.add_argument("--real-postkill-success-episodes", type=int, default=0)
    parser.add_argument("--real-postkill-dagger-episodes", type=int, default=0)
    parser.add_argument(
        "--real-assassin-handoff-episodes", type=int, default=0,
        help="真实双刺客局中仅收集探测 option 交棒后至首杀的成功片段",
    )
    parser.add_argument(
        "--real-assassin-postkill-opening-model", default=None,
        help="真实双刺客二杀采集的 combat_economy 开局模型",
    )
    parser.add_argument(
        "--real-assassin-postkill-antiarmor-model", default=None,
        help="真实双刺客二杀采集时按部署顺序执行的反陶瓷专家",
    )
    parser.add_argument(
        "--real-assassin-postkill-episodes", type=int, default=0,
        help="真实双刺客完整局中接受的 oracle 二杀成功序列数",
    )
    parser.add_argument(
        "--natural-bow-opening-model", default=None,
        help="真实三人局触发前使用的 combat_economy 开局模型",
    )
    parser.add_argument(
        "--natural-bow-continuation-model", default=None,
        help="真实三人局首杀后等待明确弓模块窗口时使用的续战模型",
    )
    parser.add_argument("--natural-bow-oracle-episodes", type=int, default=0)
    parser.add_argument("--natural-bow-dagger-episodes", type=int, default=0)
    parser.add_argument(
        "--commitment-fullhp-teacher-model", default=None,
        help="正常20HP目标承诺教师；首杀后用存活目标mask产生蒸馏标签",
    )
    parser.add_argument("--commitment-fullhp-success-episodes", type=int, default=0)
    parser.add_argument("--commitment-fullhp-dagger-episodes", type=int, default=0)
    parser.add_argument("--commitment-postkill-weight", type=float, default=3.0)
    parser.add_argument("--commitment-transition-weight", type=float, default=12.0)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()

    if args.target_episodes < 0 or args.anchor_episodes < 0:
        parser.error("target/anchor episodes must be >= 0")
    if args.anchor_weight < 0:
        parser.error("anchor-weight must be >= 0")
    if (
        args.bow_move_weight <= 0
        or args.bow_install_weight <= 0
        or args.bow_infinite_weight <= 0
    ):
        parser.error("bow move/install weights must be > 0")

    set_profile(PROFILE)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    model = MaskableRecurrentPPO.load(args.model, device=args.device)
    allowed_scenarios = {
        "combat_retarget", "combat_retarget_static",
        "combat_multi_quarter_hp", "combat_multi_half_hp",
        "combat_multi_full_hp",
        "combat_assassin_pressure",
        "economy_pursuit_supply",
        "economy_armored_pursuit_supply",
        "economy_armored_pursuit_supply_slot0",
        "survival_heal_counterattack", "survival_armor_counterattack",
        "survival_armor_repair",
        "equipment_sharpen_chain", "equipment_fire_antiarmor",
        "equipment_bow_loadout",
        "visibility_thermal_chain", "visibility_magic_chain",
        "visibility_tech_chain", "attribute_magic_armor_choice",
        "attribute_tech_armor_choice",
    }
    target_scenarios = [args.target_scenario]
    if args.target_scenarios:
        target_scenarios = [
            value.strip() for value in args.target_scenarios.split(",")
            if value.strip()
        ]
        unknown = set(target_scenarios) - allowed_scenarios
        if unknown:
            parser.error(f"未知 --target-scenarios: {sorted(unknown)}")
        if not target_scenarios:
            parser.error("--target-scenarios 不得为空")
    target: list[EpisodeSequence] = []
    target_sequences_by_scenario: dict[str, list[EpisodeSequence]] = {}
    target_by_scenario: dict[str, dict[str, float]] = {}
    for scenario_idx, scenario in enumerate(target_scenarios):
        sequences = _collect_sequences(
            model,
            episodes=args.target_episodes,
            seed=args.seed * 1000 + 11 + scenario_idx * 100_000,
            oracle=not args.target_self_teacher,
            target_scenario=scenario,
            success_only=args.target_success_only,
            bow_move_weight=args.bow_move_weight,
            bow_install_weight=args.bow_install_weight,
            bow_infinite_weight=args.bow_infinite_weight,
        )
        target.extend(sequences)
        target_sequences_by_scenario.setdefault(scenario, []).extend(sequences)
        target_by_scenario[scenario] = _summarize_lengths(
            target_sequences_by_scenario[scenario],
        )
    if args.dagger_episodes < 0:
        parser.error("--dagger-episodes must be >= 0")
    if args.anchor_dagger_episodes < 0:
        parser.error("--anchor-dagger-episodes must be >= 0")
    if args.hybrid_fullhp_success_episodes < 0 or args.hybrid_fullhp_dagger_episodes < 0:
        parser.error("hybrid fullhp episode counts must be >= 0")
    if (
        args.hybrid_fullhp_success_episodes or args.hybrid_fullhp_dagger_episodes
    ) and not args.hybrid_fullhp_teacher_model:
        parser.error("hybrid fullhp episodes > 0 时必须设置 teacher model")
    if (
        args.real_postkill_success_episodes < 0
        or args.real_postkill_dagger_episodes < 0
    ):
        parser.error("real postkill episode counts must be >= 0")
    if (
        args.real_postkill_success_episodes
        or args.real_postkill_dagger_episodes
    ) and not args.real_postkill_teacher_model:
        parser.error("real postkill episodes > 0 时必须设置 teacher model")
    if args.real_assassin_handoff_episodes < 0:
        parser.error("real assassin handoff episodes must be >= 0")
    if args.real_assassin_postkill_episodes < 0:
        parser.error("real assassin postkill episodes must be >= 0")
    if (
        args.real_assassin_postkill_episodes
        and not (
            args.real_assassin_postkill_opening_model
            and args.real_assassin_postkill_antiarmor_model
        )
    ):
        parser.error(
            "real assassin postkill episodes > 0 时必须设置 opening/antiarmor model"
        )
    if (
        args.natural_bow_oracle_episodes < 0
        or args.natural_bow_dagger_episodes < 0
    ):
        parser.error("natural bow episode counts must be >= 0")
    if (
        args.natural_bow_oracle_episodes
        or args.natural_bow_dagger_episodes
    ) and not (
        args.natural_bow_opening_model
        and args.natural_bow_continuation_model
    ):
        parser.error(
            "natural bow episodes > 0 时必须同时设置 opening/continuation model"
        )
    if (
        args.commitment_fullhp_success_episodes < 0
        or args.commitment_fullhp_dagger_episodes < 0
    ):
        parser.error("commitment fullhp episode counts must be >= 0")
    if (
        args.commitment_fullhp_success_episodes
        or args.commitment_fullhp_dagger_episodes
    ) and not args.commitment_fullhp_teacher_model:
        parser.error("commitment fullhp episodes > 0 时必须设置 teacher model")
    if args.commitment_postkill_weight <= 0 or args.commitment_transition_weight <= 0:
        parser.error("commitment weights must be positive")
    if args.multi_anchor_episodes < 0:
        parser.error("--multi-anchor-episodes must be >= 0")
    dagger_scenarios = [args.dagger_scenario] if args.dagger_scenario else []
    if args.dagger_scenarios:
        dagger_scenarios = [
            value.strip() for value in args.dagger_scenarios.split(",")
            if value.strip()
        ]
        unknown = set(dagger_scenarios) - allowed_scenarios
        if unknown:
            parser.error(f"未知 --dagger-scenarios: {sorted(unknown)}")
    if args.dagger_episodes and not dagger_scenarios:
        parser.error("--dagger-episodes > 0 时必须设置 DAgger 场景")
    for scenario_idx, scenario in enumerate(dagger_scenarios):
        dagger_key = f"dagger:{scenario}"
        dagger_sequences = _collect_sequences(
            model,
            episodes=args.dagger_episodes,
            seed=args.seed * 1000 + 900_011 + scenario_idx * 100_000,
            oracle=True,
            target_scenario=scenario,
            success_only=False,
            execute_model_with_oracle_labels=True,
            bow_move_weight=args.bow_move_weight,
            bow_install_weight=args.bow_install_weight,
            bow_infinite_weight=args.bow_infinite_weight,
        )
        target.extend(dagger_sequences)
        target_sequences_by_scenario.setdefault(dagger_key, []).extend(
            dagger_sequences,
        )
        target_by_scenario[dagger_key] = _summarize_lengths(
            target_sequences_by_scenario[dagger_key],
        )
    if args.hybrid_fullhp_teacher_model:
        hybrid_teacher = MaskableRecurrentPPO.load(
            args.hybrid_fullhp_teacher_model,
            device=args.device,
        )
        hybrid_specs = (
            (
                "hybrid:combat_multi_full_hp:success",
                args.hybrid_fullhp_success_episodes,
                False,
                True,
            ),
            (
                "hybrid:combat_multi_full_hp:dagger",
                args.hybrid_fullhp_dagger_episodes,
                True,
                False,
            ),
        )
        for hybrid_idx, (key, count, execute_student, success_only) in enumerate(
            hybrid_specs,
        ):
            sequences = _collect_hybrid_fullhp_sequences(
                model,
                hybrid_teacher,
                episodes=count,
                seed=args.seed * 1000 + 1_900_011 + hybrid_idx * 100_000,
                execute_student=execute_student,
                success_only=success_only,
            )
            target.extend(sequences)
            target_sequences_by_scenario[key] = sequences
            target_by_scenario[key] = _summarize_lengths(sequences)
    if args.real_postkill_teacher_model:
        real_postkill_teacher = MaskableRecurrentPPO.load(
            args.real_postkill_teacher_model,
            device=args.device,
        )
        real_postkill_specs = (
            (
                "real_postkill:economy:success",
                args.real_postkill_success_episodes,
                False,
                True,
            ),
            (
                "real_postkill:economy:dagger",
                args.real_postkill_dagger_episodes,
                True,
                False,
            ),
        )
        for real_idx, (key, count, execute_student, success_only) in enumerate(
            real_postkill_specs,
        ):
            sequences = _collect_real_postkill_economy_sequences(
                model,
                real_postkill_teacher,
                episodes=count,
                seed=args.seed * 1000 + 2_400_011 + real_idx * 100_000,
                execute_student=execute_student,
                success_only=success_only,
            )
            target.extend(sequences)
            target_sequences_by_scenario[key] = sequences
            target_by_scenario[key] = _summarize_lengths(sequences)
    if args.real_assassin_handoff_episodes:
        key = "real_opening:assassin_handoff:success"
        sequences = _collect_real_assassin_handoff_sequences(
            model,
            episodes=args.real_assassin_handoff_episodes,
            seed=args.seed * 1000 + 2_600_011,
        )
        target.extend(sequences)
        target_sequences_by_scenario[key] = sequences
        target_by_scenario[key] = _summarize_lengths(sequences)
    if args.real_assassin_postkill_episodes:
        key = "real_postkill:assassin:oracle_success"
        assassin_opening = MaskableRecurrentPPO.load(
            args.real_assassin_postkill_opening_model,
            device=args.device,
        )
        assassin_antiarmor = MaskableRecurrentPPO.load(
            args.real_assassin_postkill_antiarmor_model,
            device=args.device,
        )
        sequences = _collect_real_assassin_postkill_sequences(
            model,
            assassin_opening,
            assassin_antiarmor,
            episodes=args.real_assassin_postkill_episodes,
            seed=args.seed * 1000 + 2_650_011,
        )
        target.extend(sequences)
        target_sequences_by_scenario[key] = sequences
        target_by_scenario[key] = _summarize_lengths(sequences)
    if args.natural_bow_opening_model and args.natural_bow_continuation_model:
        natural_bow_opening = MaskableRecurrentPPO.load(
            args.natural_bow_opening_model,
            device=args.device,
        )
        natural_bow_continuation = MaskableRecurrentPPO.load(
            args.natural_bow_continuation_model,
            device=args.device,
        )
        natural_bow_specs = (
            (
                "natural_postkill:bow:oracle",
                args.natural_bow_oracle_episodes,
                False,
            ),
            (
                "natural_postkill:bow:dagger",
                args.natural_bow_dagger_episodes,
                True,
            ),
        )
        for natural_idx, (key, count, execute_student) in enumerate(
            natural_bow_specs
        ):
            sequences = _collect_natural_postkill_bow_sequences(
                model,
                natural_bow_opening,
                natural_bow_continuation,
                episodes=count,
                seed=args.seed * 1000 + 2_700_011 + natural_idx * 100_000,
                execute_student=execute_student,
                bow_move_weight=args.bow_move_weight,
                bow_install_weight=args.bow_install_weight,
            )
            target.extend(sequences)
            target_sequences_by_scenario[key] = sequences
            target_by_scenario[key] = _summarize_lengths(sequences)
    if args.commitment_fullhp_teacher_model:
        commitment_teacher = MaskableRecurrentPPO.load(
            args.commitment_fullhp_teacher_model,
            device=args.device,
        )
        commitment_specs = (
            (
                "commitment:combat_multi_full_hp:success",
                args.commitment_fullhp_success_episodes,
                False,
            ),
            (
                "commitment:combat_multi_full_hp:dagger",
                args.commitment_fullhp_dagger_episodes,
                True,
            ),
        )
        for commitment_idx, (key, count, execute_student) in enumerate(
            commitment_specs,
        ):
            sequences = _collect_commitment_fullhp_sequences(
                model,
                commitment_teacher,
                episodes=count,
                seed=args.seed * 1000 + 2_900_011 + commitment_idx * 100_000,
                execute_student=execute_student,
                postkill_weight=args.commitment_postkill_weight,
                transition_weight=args.commitment_transition_weight,
            )
            target.extend(sequences)
            target_sequences_by_scenario[key] = sequences
            target_by_scenario[key] = _summarize_lengths(sequences)
    anchor_teacher = model
    if args.anchor_teacher_model:
        anchor_teacher = MaskableRecurrentPPO.load(
            args.anchor_teacher_model,
            device=args.device,
        )
    anchor_self = _collect_sequences(
        anchor_teacher,
        episodes=args.anchor_episodes,
        seed=args.seed * 1000 + 50011,
        oracle=False,
    )
    anchor_dagger: list[EpisodeSequence] = []
    if args.anchor_dagger_episodes:
        if not args.anchor_teacher_model:
            parser.error(
                "--anchor-dagger-episodes > 0 时必须设置 "
                "--anchor-teacher-model",
            )
        anchor_dagger = _collect_sequences(
            model,
            episodes=args.anchor_dagger_episodes,
            seed=args.seed * 1000 + 1_500_011,
            oracle=False,
            label_model=anchor_teacher,
        )
    multi_anchor_scenarios: list[str] = []
    if args.multi_anchor_scenarios:
        multi_anchor_scenarios = [
            value.strip() for value in args.multi_anchor_scenarios.split(",")
            if value.strip()
        ]
        unknown = set(multi_anchor_scenarios) - allowed_scenarios
        if unknown:
            parser.error(f"未知 --multi-anchor-scenarios: {sorted(unknown)}")
    if args.multi_anchor_episodes and not multi_anchor_scenarios:
        parser.error("--multi-anchor-episodes > 0 时必须设置多人锚点场景")
    multi_anchor: list[EpisodeSequence] = []
    multi_anchor_by_scenario: dict[str, dict[str, float]] = {}
    for scenario_idx, scenario in enumerate(multi_anchor_scenarios):
        sequences = _collect_sequences(
            anchor_teacher,
            episodes=args.multi_anchor_episodes,
            seed=args.seed * 1000 + 3_500_011 + scenario_idx * 100_000,
            oracle=False,
            target_scenario=scenario,
        )
        multi_anchor.extend(sequences)
        multi_anchor_by_scenario[scenario] = _summarize_lengths(sequences)
    anchor = anchor_self + anchor_dagger + multi_anchor
    if not target:
        parser.error("至少需要一种 target/DAgger/hybrid/commitment 训练序列")
    print(json.dumps({
        "target": _summarize_lengths(target),
        "target_by_scenario": target_by_scenario,
        "anchor": _summarize_lengths(anchor),
        "anchor_self": _summarize_lengths(anchor_self),
        "anchor_dagger": _summarize_lengths(anchor_dagger),
        "multi_anchor": _summarize_lengths(multi_anchor),
        "multi_anchor_by_scenario": multi_anchor_by_scenario,
        "before_target": _dataset_metrics(model.policy, target),
        "before_target_by_scenario": {
            scenario: _dataset_metrics(model.policy, sequences)
            for scenario, sequences in target_sequences_by_scenario.items()
        },
        "before_anchor": _dataset_metrics(model.policy, anchor),
    }, ensure_ascii=False, sort_keys=True))

    history = train_incremental_bc(
        model,
        target,
        anchor,
        epochs=args.epochs,
        learning_rate=args.learning_rate,
        anchor_weight=args.anchor_weight,
        batch_episodes=args.batch_episodes,
        seed=args.seed,
    )
    after_target_by_scenario = {
        scenario: _dataset_metrics(model.policy, sequences)
        for scenario, sequences in target_sequences_by_scenario.items()
    }
    print(json.dumps({
        "after_target_by_scenario": after_target_by_scenario,
    }, ensure_ascii=False, sort_keys=True))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(output))
    summary_path = output.with_suffix(".bc.json")
    summary_path.write_text(json.dumps({
        "source_model": str(args.model),
        "output_model": str(output),
        "target_scenarios": target_scenarios,
        "dagger_scenarios": dagger_scenarios,
        "dagger_episodes": int(args.dagger_episodes),
        "anchor_teacher_model": args.anchor_teacher_model,
        "anchor_dagger_episodes": int(args.anchor_dagger_episodes),
        "hybrid_fullhp_teacher_model": args.hybrid_fullhp_teacher_model,
        "hybrid_fullhp_success_episodes": int(args.hybrid_fullhp_success_episodes),
        "hybrid_fullhp_dagger_episodes": int(args.hybrid_fullhp_dagger_episodes),
        "real_postkill_teacher_model": args.real_postkill_teacher_model,
        "real_postkill_success_episodes": int(
            args.real_postkill_success_episodes
        ),
        "real_postkill_dagger_episodes": int(
            args.real_postkill_dagger_episodes
        ),
        "real_assassin_handoff_episodes": int(
            args.real_assassin_handoff_episodes
        ),
        "real_assassin_postkill_opening_model": (
            args.real_assassin_postkill_opening_model
        ),
        "real_assassin_postkill_antiarmor_model": (
            args.real_assassin_postkill_antiarmor_model
        ),
        "real_assassin_postkill_episodes": int(
            args.real_assassin_postkill_episodes
        ),
        "natural_bow_opening_model": args.natural_bow_opening_model,
        "natural_bow_continuation_model": args.natural_bow_continuation_model,
        "natural_bow_oracle_episodes": int(
            args.natural_bow_oracle_episodes
        ),
        "natural_bow_dagger_episodes": int(
            args.natural_bow_dagger_episodes
        ),
        "commitment_fullhp_teacher_model": args.commitment_fullhp_teacher_model,
        "commitment_fullhp_success_episodes": int(
            args.commitment_fullhp_success_episodes
        ),
        "commitment_fullhp_dagger_episodes": int(
            args.commitment_fullhp_dagger_episodes
        ),
        "commitment_postkill_weight": float(args.commitment_postkill_weight),
        "commitment_transition_weight": float(args.commitment_transition_weight),
        "multi_anchor_scenarios": multi_anchor_scenarios,
        "multi_anchor_episodes": int(args.multi_anchor_episodes),
        "target_success_only": bool(args.target_success_only),
        "target_self_teacher": bool(args.target_self_teacher),
        "bow_move_weight": float(args.bow_move_weight),
        "bow_install_weight": float(args.bow_install_weight),
        "bow_infinite_weight": float(args.bow_infinite_weight),
        "target": _summarize_lengths(target),
        "target_by_scenario": target_by_scenario,
        "after_target_by_scenario": after_target_by_scenario,
        "anchor": _summarize_lengths(anchor),
        "anchor_self": _summarize_lengths(anchor_self),
        "anchor_dagger": _summarize_lengths(anchor_dagger),
        "multi_anchor": _summarize_lengths(multi_anchor),
        "multi_anchor_by_scenario": multi_anchor_by_scenario,
        "history": history,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[BC] saved {output} and {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
