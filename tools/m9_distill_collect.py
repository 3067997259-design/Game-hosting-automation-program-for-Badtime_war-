"""
tools/m9_distill_collect.py
──────────────────────────
P3 统一蒸馏数据采集（docs/m9/rl/m9_rl_next_steps.md §6）。

执行者：冻结专家栈（rl/expert_stack 十专家 + 确定性路由）。专家栈以
LegacyObsDimWrapper(828) 消费观测，数据集记录 **918 维原生观测**（P4 学生
口径）+ 每步 teacher logits（被选专家的掩码概率分布）+ expert_id（门控监督
标签）+ 路由理由 + 逐步奖励与 episode 边界。

采样矩阵（cell）：
    natural_{3,4,5,6}p     自然局（无天赋，全 BasicAI 对手）
    talent_pilot_3p        随机天赋自然局（P5 预热切片）
    course:<scenario>      原子课程场景（动作合同/轮上限沿用
                           tools/m9_recurrent_bc 的映射表）
    course_t1 / course_t3  天赋课程（rl_talent_reserve 预留池强制指定）

输出：每 cell 一个 npz 分片 + manifest.json；`--verify` 复载校验不变量
（动作合法 / probs 有限且和≈1 / expert_id 在册 / 边界标记完整）。

用法：
    python tools/m9_distill_collect.py --preset sample --out bc_data/m9_distill
    python tools/m9_distill_collect.py --preset full  --out bc_data/m9_distill
    python tools/m9_distill_collect.py --verify bc_data/m9_distill
"""

from __future__ import annotations

import argparse
import json
import os as _os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# 允许从仓库根直接运行；线程抑制须在 import numpy/torch 前
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_os.environ.setdefault("OMP_NUM_THREADS", "1")
_os.environ.setdefault("MKL_NUM_THREADS", "1")
_os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import numpy as np  # noqa: E402

from engine.experiments import set_profile  # noqa: E402

PROFILE = "m9-rfc"

# 专家 ID 稳定序（npz 里的 expert_id 下标即此表位次）
EXPERT_ID_ORDER: tuple[str, ...] = (
    "opening", "postkill", "pp_finish", "survival_heal", "armor_repair",
    "sharpen", "detection", "attribute", "fire_antiarmor", "police",
)

# 课程场景 → (动作合同, 轮上限)。映射与 tools/m9_recurrent_bc._collect_sequences
# 及 tools/m9_rl_train 的自动合同选择同源（该二者内嵌无法直接复用；改动须同步）。
# P4.1 勘误：pp/police/talent 三组课曾漏映射到默认 combat_core——教师是在
# 各自钻头合同（pp_course/police_course/talent_course）下受训的，错合同下
# 教师行为退化（pp 课实测来回踱步不攻击），标签因此作废。
_COURSE_CONTRACTS: Dict[str, tuple[str, int]] = {
    "economy_pursuit_supply": ("economy_course", 30),
    "economy_armored_pursuit_supply": ("economy_course", 40),
    "economy_armored_pursuit_supply_slot0": ("economy_course", 40),
    "economy_bow_supply": ("economy_course", 40),
    "equipment_bow_loadout": ("equipment_course", 40),
    "survival_armor_repair": ("survival_course", 16),
    "equipment_sharpen_chain": ("equipment_course", 9),
    "visibility_thermal_chain": ("equipment_course", 8),
    "visibility_magic_chain": ("equipment_course", 8),
    "visibility_tech_chain": ("equipment_course", 8),
    "attribute_magic_armor_choice": ("equipment_course", 4),
    "attribute_tech_armor_choice": ("equipment_course", 4),
    "combat_retarget": ("combat_core", 25),
    "combat_retarget_static": ("combat_core", 25),
    "pp_damage_finish": ("pp_course", 40),
    "pp_damage_choice": ("pp_course", 40),
    "police_command": ("police_course", 40),
    "police_election": ("police_course", 40),
    "talent_t1_improvise": ("talent_course", 40),
    "talent_t3_public": ("talent_course", 40),
    # P5 首批 B 组课（§8.2）：短靶场轮限即可（首课只考发动）
    "talent_g1_burn": ("talent_course", 4),
    "talent_g6_borrow": ("talent_course", 4),
    "talent_g7_suppress": ("talent_course", 4),
    "talent_t6_hotline": ("talent_course", 4),
}
_COURSE_DEFAULT_CONTRACT = ("combat_core", 40)
_TALENT_COURSES: Dict[str, int] = {
    # 场景 → 强制预留的 rl_talent 编号（注册表 legacy_number）
    "talent_t1_improvise": 1,
    "talent_t3_public": 3,
    "talent_g1_burn": 8,
    "talent_g6_borrow": 13,
    "talent_g7_suppress": 14,
    "talent_t6_hotline": 6,
}

# B 组场景：教师栈/BasicAI 均不覆盖 → 采集时 choose 通道用贪心 oracle 标签
# （§8.1 预案；探针逻辑同源）。teacher_probs 用被选动作的 one-hot——
# KL(one_hot‖student) 数学上退化为 NLL，蒸馏损失结构不变。
ORACLE_LABEL_CELLS: frozenset = frozenset({
    "course_g1", "course_g6", "course_g7", "course_t6",
})
_ORACLE_CHOOSE_KEYS: tuple[str, ...] = (
    "发动", "即演", "换装", "宣言", "公演", "插入", "笑话", "表演", "演出",
)


def _oracle_choose_pick(env: Any, mask: np.ndarray) -> Optional[int]:
    """B 组 oracle 标签：talent_t0 choose 选发动项；T6 命令通道点亮热线槽。

    G1/G6/G7 走 T0 choose（situation=talent_t0）；T6 热线是根行动 special
    （命令通道）——热线槽合法时优先举报 ai_0 槽。举报成功后引擎关闭
    新通缉（has_open_wanted），后续拍自动回落专家标签。
    """
    if bool(getattr(env, "_choose_mode", False)):
        situation = str(
            (getattr(env, "_choose_context", {}) or {}).get("situation", ""),
        )
        if situation != "talent_t0":
            return None
        from rl.action_space import idx_to_choose_option

        target = getattr(env, "unwrapped", env)
        for idx in np.flatnonzero(mask):
            option = str(idx_to_choose_option(
                int(idx), env._choose_options, situation,
                getattr(target, "_current_player", None),
                getattr(target, "_current_game_state", None),
            ))
            if any(key in option for key in _ORACLE_CHOOSE_KEYS):
                return int(idx)
        return None
    # 命令通道：热线槽合法 → 举报首个对手槽
    from rl.action_space import (
        IDX_SPECIAL_TARGET_M9_BASE, M9_TARGET_SLOTS, idx_to_command,
    )
    target = getattr(env, "unwrapped", env)
    for idx in np.flatnonzero(mask):
        if not (
            IDX_SPECIAL_TARGET_M9_BASE <= idx
            < IDX_SPECIAL_TARGET_M9_BASE + M9_TARGET_SLOTS
        ):
            continue
        try:
            cmd = idx_to_command(
                int(idx),
                getattr(target, "_rl_player", None),
                getattr(target, "_state", None),
            )
        except Exception:
            continue
        if cmd.startswith("special 热线举报"):
            return int(idx)
    return None

# env._apply_course_scenario 认可的全量场景（course: 前缀默认全集）
_ALL_COURSE_SCENARIOS: tuple[str, ...] = (
    "combat_multi_quarter_hp", "combat_multi_half_hp", "combat_multi_full_hp",
    "combat_assassin_pressure",
    "combat_retarget", "combat_retarget_static",
    "economy_bow_supply", "economy_pursuit_supply",
    "economy_armored_pursuit_supply", "economy_armored_pursuit_supply_slot0",
    "equipment_fire_antiarmor", "equipment_bow_loadout",
    "pp_damage_finish", "pp_damage_choice",
    "survival_heal_counterattack", "survival_armor_counterattack",
    "survival_armor_repair", "equipment_sharpen_chain",
    "visibility_thermal_chain", "visibility_magic_chain",
    "visibility_tech_chain", "attribute_magic_armor_choice",
    "attribute_tech_armor_choice",
    "police_command", "police_election",
    "talent_t1_improvise", "talent_t3_public",
)


class RecordingTruncateWrapper:
    """截断给教师（828），完整 918 观测留给数据集。

    不走 gymnasium Wrapper（其 __getattr__ 对 _rl_player 等私有属性抛
    AttributeError）；显式持有内层 env，属性一律透传内层。
    """

    def __init__(self, env: Any, target_dim: int) -> None:
        from rl.obs_builder_m9 import OBS_DIM_M9_TOTAL

        inner_dim = int(env.observation_space.shape[0])
        if len(env.observation_space.shape) != 1 or inner_dim < target_dim:
            raise ValueError(
                f"内层观测 shape={env.observation_space.shape} 不满足"
                f"尾部截断到 {target_dim} 的前提"
            )
        if inner_dim != OBS_DIM_M9_TOTAL:
            raise RuntimeError(
                f"内层观测宽度 {inner_dim} != OBS_DIM_M9_TOTAL"
                f" {OBS_DIM_M9_TOTAL}——布局漂移，中止采集"
            )
        self.inner = env
        self._target = int(target_dim)
        self.last_full_obs: Optional[np.ndarray] = None

    def _cut(self, obs: np.ndarray) -> np.ndarray:
        self.last_full_obs = np.asarray(obs, dtype=np.float32).copy()
        return self.last_full_obs[: self._target]

    def reset(self, **kwargs: Any):
        full, info = self.inner.reset(**kwargs)
        return self._cut(full), info

    def step(self, action):
        full, reward, term, trunc, info = self.inner.step(action)
        return self._cut(full), reward, term, trunc, info

    def close(self) -> None:
        self.inner.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def _course_contract(scenario: str) -> tuple[str, int]:
    return _COURSE_CONTRACTS.get(scenario, _COURSE_DEFAULT_CONTRACT)


def build_cell_env(cell: str, seed: int) -> Any:
    """按 cell 名构造（已包装的）采集环境。"""
    from rl.env import BadtimeWarEnv
    from rl.obs_builder_m9 import OBS_DIM_M9_V5_CORE

    if cell.startswith("natural_"):
        players = int(cell.split("_")[1].rstrip("p"))
        env = BadtimeWarEnv(
            num_opponents=players - 1, max_rounds=None, n_stack=1,
            enable_talents=False, rl_talent=0,
        )
    elif cell == "talent_pilot_3p":
        env = BadtimeWarEnv(
            num_opponents=2, max_rounds=None, n_stack=1,
            enable_talents=True, rl_talent=None,
        )
    elif cell.startswith("course_"):
        # 天赋课程：预留池强制指定（P3 裁决 2026-08-22）
        scenario = {
            "course_t1": "talent_t1_improvise",
            "course_t3": "talent_t3_public",
            "course_g1": "talent_g1_burn",
            "course_g6": "talent_g6_borrow",
            "course_g7": "talent_g7_suppress",
            "course_t6": "talent_t6_hotline",
        }.get(cell)
        if scenario is None:
            raise ValueError(f"未知天赋课 cell: {cell!r}")
        contract, rounds = _course_contract(scenario)
        env = BadtimeWarEnv(
            num_opponents=2, max_rounds=rounds, n_stack=1,
            enable_talents=True,
            rl_talent=_TALENT_COURSES[scenario], rl_talent_reserve=True,
            action_contract=contract, course_scenario=scenario,
        )
    elif cell.startswith("course:"):
        scenario = cell.split(":", 1)[1]
        contract, rounds = _course_contract(scenario)
        env = BadtimeWarEnv(
            num_opponents=2, max_rounds=rounds, n_stack=1,
            enable_talents=False, rl_talent=0,
            action_contract=contract, course_scenario=scenario,
        )
    else:
        raise ValueError(f"未知采集 cell: {cell!r}")
    return RecordingTruncateWrapper(env, OBS_DIM_M9_V5_CORE)


def collect_cell(
    cell: str,
    *,
    episodes: int,
    seed_base: int,
    experts: Dict[str, Any],
    device_logits_fn: Callable,
    forced_expert: Optional[str] = None,
) -> Dict[str, Any]:
    """采集一个 cell；返回待落盘的分片字典与统计。

    B 组 cell（ORACLE_LABEL_CELLS）：talent_t0 choose 由贪心 oracle 顶替
    动作与标签（教师栈零激活，标签必须来自 oracle）；teacher_probs 用被选
    动作 one-hot——KL(one_hot‖student) 退化为 NLL，蒸馏损失结构不变。
    """
    from rl.expert_stack import (
        ExpertStackModel, make_env_facts_fn,
    )
    from rl.obs_builder_m9 import OBS_DIM_M9_TOTAL
    from rl.schema import M9_SCHEMA_VERSION

    env = build_cell_env(cell, seed_base)
    stack = ExpertStackModel(
        experts, make_env_facts_fn(env), logits_fn=device_logits_fn,
    )
    stack.capture_enabled = True
    stack.forced_expert = forced_expert
    oracle_label = cell in ORACLE_LABEL_CELLS

    obs_list: List[np.ndarray] = []
    mask_list: List[np.ndarray] = []
    action_list: List[int] = []
    expert_list: List[int] = []
    reason_list: List[str] = []
    probs_list: List[np.ndarray] = []
    reward_list: List[float] = []
    start_list: List[bool] = []

    for episode in range(episodes):
        obs, _info = env.reset(seed=seed_base + 1000 + episode)
        state = None
        episode_start = np.array([False])
        done = False
        while not done:
            mask = env.action_masks()
            full_obs = env.last_full_obs
            action, state = stack.predict(
                obs[None] if obs.ndim == 1 else obs,
                state=state,
                episode_start=episode_start,
                action_masks=mask,
                deterministic=True,
            )
            action_idx = int(action[0])
            capture = stack.capture_last or {}
            probs = capture.get("probs")
            if oracle_label:
                override = _oracle_choose_pick(env, mask)
                if override is not None:
                    action_idx = override
                    onehot = np.zeros(len(mask), dtype=np.float32)
                    onehot[action_idx] = 1.0
                    probs = onehot

            obs_list.append(full_obs)
            mask_list.append(np.asarray(mask, dtype=bool).copy())
            action_list.append(action_idx)
            expert_list.append(EXPERT_ID_ORDER.index(capture["expert"]))
            reason_list.append(str(capture.get("reason", "")))
            probs_list.append(
                np.asarray(probs, dtype=np.float32)
                if probs is not None
                else np.full(len(mask), np.nan, dtype=np.float32)
            )
            start_list.append(bool(capture.get("boundary", False)))

            obs, reward, term, trunc, info = env.step(action_idx)
            reward_list.append(float(reward))
            episode_start = np.array([term or trunc])
            done = bool(term or trunc)
        # 尾拍完整观测也入册（供 P4 做 next-state 势能差分）
        obs_list.append(env.last_full_obs.copy())

    stack.flush()
    env.close()

    expert_counts: Dict[str, int] = {}
    for summary in stack.episode_summaries:
        for key, count in summary.routing_counts.items():
            expert_counts[key] = expert_counts.get(key, 0) + count
    return {
        "cell": cell,
        "episodes": episodes,
        "seed_base": seed_base,
        "schema_version": M9_SCHEMA_VERSION,
        "obs_dim": OBS_DIM_M9_TOTAL,
        "expert_id_order": list(EXPERT_ID_ORDER),
        "observations": np.stack(obs_list),
        "action_masks": np.stack(mask_list),
        "actions": np.asarray(action_list, dtype=np.int64),
        "expert_ids": np.asarray(expert_list, dtype=np.int64),
        "reasons": np.asarray(reason_list, dtype=np.str_),
        "teacher_probs": np.stack(probs_list),
        "rewards": np.asarray(reward_list, dtype=np.float32),
        "episode_starts": np.asarray(start_list, dtype=bool),
        "expert_usage": expert_counts,
        "mean_reward": float(np.mean(reward_list)) if reward_list else 0.0,
    }


def save_shard(out_dir: Path, shard: Dict[str, Any]) -> Path:
    """npz 分片（数组）+ 统计进 manifest 由调用方汇总。"""
    arrays = {
        key: value for key, value in shard.items()
        if isinstance(value, np.ndarray)
    }
    path = out_dir / f"{shard['cell'].replace(':', '_')}.npz"
    np.savez_compressed(path, **arrays)
    return path


def verify_dir(out_dir: Path) -> int:
    """复载全部分片并校验不变量；返回校验步数，违例即抛错。"""
    from rl.obs_builder_m9 import OBS_DIM_M9_TOTAL

    total = 0
    for path in sorted(out_dir.glob("*.npz")):
        data = np.load(path, allow_pickle=False)
        obs = data["observations"]
        masks = data["action_masks"]
        actions = data["actions"]
        experts = data["expert_ids"]
        probs = data["teacher_probs"]
        starts = data["episode_starts"]
        episodes = int(starts.sum())
        if episodes == 0 or not bool(starts[0]):
            raise AssertionError(f"{path.name}: 首拍未标记 episode_start")
        if obs.shape[0] != masks.shape[0] + episodes:
            raise AssertionError(
                f"{path.name}: 观测行数应 = 转换数 + 局数（尾拍 next-state）"
            )
        if obs.shape[1] != OBS_DIM_M9_TOTAL:
            raise AssertionError(f"{path.name}: obs 维度 {obs.shape[1]}")
        if not bool(np.all(np.isfinite(probs))):
            raise AssertionError(f"{path.name}: teacher_probs 含非有限值")
        sums = probs.sum(axis=1)
        if not bool(np.all(np.abs(sums - 1.0) < 1e-3)):
            raise AssertionError(f"{path.name}: probs 行和偏离 1")
        rows = np.arange(actions.shape[0])
        if not bool(np.all(masks[rows, actions])):
            raise AssertionError(f"{path.name}: 存在违反自身掩码的动作")
        if not bool(np.all(probs[rows, actions] > 0)):
            raise AssertionError(f"{path.name}: 被选动作概率为 0（logits 不同步）")
        if experts.min() < 0 or experts.max() >= len(EXPERT_ID_ORDER):
            raise AssertionError(f"{path.name}: expert_id 越界")
        total += int(actions.shape[0])
    return total


def _preset_cells(preset: str) -> Dict[str, int]:
    if preset == "sample":
        return {
            "natural_3p": 3,
            "talent_pilot_3p": 2,
            "course:survival_heal_counterattack": 2,
            "course:economy_pursuit_supply": 2,
            "course_t3": 2,
        }
    if preset == "talent_batch":
        # P5 第二批：B 组四课（oracle 标签）+ A 组预热自然局
        return {
            "course_g1": 60,
            "course_g6": 60,
            "course_g7": 60,
            "course_t6": 60,
            "talent_pilot_3p": 80,
        }
    if preset == "full":
        cells: Dict[str, int] = {
            f"natural_{players}p": 150 for players in (3, 4, 5, 6)
        }
        cells["talent_pilot_3p"] = 50
        for scenario in _ALL_COURSE_SCENARIOS:
            cells[f"course:{scenario}"] = 40
        return cells
    raise ValueError(f"未知 preset: {preset!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset", choices=["sample", "full", "talent_batch"],
                        default="sample")
    parser.add_argument("--cells", default=None,
                        help="逗号分隔的指定 cell 列表（增量重采）；覆盖 preset")
    parser.add_argument("--episodes-override", type=int, default=None,
                        help="覆盖 preset 的每 cell 局数（小步调试用）")
    parser.add_argument("--out", default="bc_data/m9_distill")
    parser.add_argument("--seed-base", type=int, default=20260822)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--force-expert", default=None,
                        help="强制所有决策路由到指定专家（police 等命令模态课"
                             "程的公开状态路由不可达，需显式指派教师）")
    parser.add_argument("--verify", default=None,
                        help="只校验已有目录（不采集）")
    args = parser.parse_args()

    if args.verify:
        steps = verify_dir(Path(args.verify))
        print(f"[verify] {args.verify}: 不变量全部通过，共 {steps} 步")
        return 0

    set_profile(PROFILE)
    from rl.expert_stack import expert_masked_probs, load_experts

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    experts = load_experts(device=args.device)
    print(f"[stack] 已加载 {len(experts)} 个专家")

    cells = _preset_cells(args.preset)
    if args.cells:
        requested = [c.strip() for c in args.cells.split(",") if c.strip()]
        unknown = [c for c in requested if c not in cells]
        if unknown:
            raise SystemExit(f"未知 cell: {unknown}（可选: {sorted(cells)}）")
        cells = {c: cells[c] for c in requested}
    manifest: Dict[str, Any] = {
        "profile": PROFILE,
        "preset": args.preset,
        "seed_base": args.seed_base,
        "cells": {},
    }
    # 增量重采：合并已存在的 manifest，只覆盖本次采集的 cell
    manifest_path = out_dir / "manifest.json"
    if manifest_path.exists():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(previous.get("cells"), dict):
                manifest["cells"].update(previous["cells"])
        except (OSError, json.JSONDecodeError):
            pass
    for cell_idx, (cell, episodes) in enumerate(cells.items()):
        if args.episodes_override is not None:
            episodes = args.episodes_override
        print(f"[collect] {cell} × {episodes} …", flush=True)
        shard = collect_cell(
            cell,
            episodes=episodes,
            seed_base=args.seed_base + cell_idx * 100_003,
            experts=experts,
            device_logits_fn=expert_masked_probs,
            forced_expert=args.force_expert,
        )
        path = save_shard(out_dir, shard)
        manifest["cells"][cell] = {
            key: value for key, value in shard.items()
            if not isinstance(value, np.ndarray)
        }
        manifest["cells"][cell]["shard"] = path.name
        manifest["cells"][cell]["shard_bytes"] = path.stat().st_size
        print(
            f"[collect] {cell}: {shard['actions'].shape[0]} 步 → "
            f"{path.name}（{path.stat().st_size / 1e6:.1f} MB, "
            f"mean_reward={shard['mean_reward']:.2f}）",
            flush=True,
        )

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    steps = verify_dir(out_dir)
    print(f"[done] 全部 cell 落盘，校验通过，共 {steps} 步 → {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
