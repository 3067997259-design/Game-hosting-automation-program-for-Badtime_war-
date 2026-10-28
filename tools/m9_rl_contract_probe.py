"""M9 RL 阶段 1 契约探针：输出可复现的规则/评估/schema 基线。

默认使用“总选第一个合法动作”的确定性策略；也可加载现有递归 checkpoint。
探针只读取正式结算快照，不会主动调用 settle 或改变游戏结果。
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

import numpy as np
from gymnasium import spaces

from engine.experiments import reset, set_profile
from rl.action_space import ACTION_COUNT_M9_FIXED, rl_action_count
from rl.env import BadtimeWarEnv
from rl.obs_builder_m9 import OBS_DIM_M9_TOTAL
from rl.winrate_eval import evaluate_policy


class _FirstLegalModel:
    """确定性接线基线：始终选择 mask 中下标最小的合法动作。"""

    observation_space = spaces.Box(
        low=-1.0, high=1.0, shape=(OBS_DIM_M9_TOTAL,), dtype=np.float32,
    )
    action_space = spaces.Discrete(ACTION_COUNT_M9_FIXED)

    def predict(self, obs, *, action_masks, **kwargs):
        legal = np.flatnonzero(action_masks[0])
        if not len(legal):
            raise RuntimeError("动作 mask 为空，环境没有提供任何合法决策")
        return np.array([int(legal[0])]), None


class _RandomLegalModel(_FirstLegalModel):
    """带独立 RNG 的随机合法策略，用于大样本生命周期契约验证。"""

    def __init__(self, seed: int) -> None:
        self._rng = np.random.default_rng(seed)

    def predict(self, obs, *, action_masks, **kwargs):
        legal = np.flatnonzero(action_masks[0])
        if not len(legal):
            raise RuntimeError("动作 mask 为空，环境没有提供任何合法决策")
        return np.array([int(self._rng.choice(legal))]), None


def _schema_snapshot() -> tuple[list[dict[str, int]], list[dict[str, object]]]:
    rows: list[dict[str, int]] = []
    findings: list[dict[str, object]] = []
    for opponents in range(1, 6):
        env = BadtimeWarEnv(
            num_opponents=opponents,
            max_rounds=8,
            enable_talents=False,
            rl_talent=0,
        )
        rows.append({
            "players": opponents + 1,
            "observation_dim": int(env.observation_space.shape[0]),
            "action_count": int(env.action_space.n),
        })
        env.close()

    action_counts = {row["action_count"] for row in rows}
    if len(action_counts) != 1:
        findings.append({
            "code": "action_schema_varies_by_player_count",
            "severity": "blocker",
            "evidence": sorted(action_counts),
        })

    env = BadtimeWarEnv(
        num_opponents=1,
        max_rounds=8,
        enable_talents=False,
        rl_talent=0,
    )
    before = int(env.action_space.n)
    env.set_num_opponents(2)
    expected = rl_action_count(n_players=3)
    after = int(env.action_space.n)
    env.close()
    if after != expected:
        findings.append({
            "code": "curriculum_action_space_stale",
            "severity": "blocker",
            "evidence": {
                "before": before,
                "after_set_num_opponents": after,
                "expected": expected,
            },
        })
    return rows, findings


def _semantic_findings() -> list[dict[str, object]]:
    """检查不需要训练即可证明的观察/动作/奖励语义缺口。"""
    findings: list[dict[str, object]] = []

    from rl.action_space import (
        STRATEGIC_CHOOSE_SITUATIONS,
        STRATEGIC_SITUATIONS,
        _cmd_to_interact_idx,
    )
    from rl.obs_builder import _CHOOSE_SITUATION_MAP

    choose_unobserved = sorted(
        STRATEGIC_CHOOSE_SITUATIONS - set(_CHOOSE_SITUATION_MAP)
    )
    if choose_unobserved:
        findings.append({
            "code": "strategic_choose_semantics_unobserved",
            "severity": "blocker",
            "evidence": choose_unobserved,
        })

    choose_registry_drift = sorted(
        STRATEGIC_CHOOSE_SITUATIONS ^ STRATEGIC_SITUATIONS
    )
    if choose_registry_drift:
        findings.append({
            "code": "choose_registry_definitions_drift",
            "severity": "warning",
            "evidence": choose_registry_drift,
        })

    from locations import (
        home,
        hospital,
        magic_institute,
        military_base,
        police_station,
        shop,
    )

    menu_items = {
        str(item)
        for module in (
            home, shop, magic_institute, hospital, military_base, police_station,
        )
        for item in module.get_menu()
        if not str(item).startswith("_")
    }
    interact_unmapped = sorted(
        item for item in menu_items
        if _cmd_to_interact_idx(f"interact {item}") < 0
    )
    if interact_unmapped:
        findings.append({
            "code": "engine_interact_items_unmapped",
            "severity": "blocker",
            "evidence": interact_unmapped,
        })

    env = BadtimeWarEnv(
        num_opponents=1,
        max_rounds=8,
        opponent_bots=["dummy"],
        enable_talents=True,
        rl_talent=1,
    )
    try:
        obs, _info = env.reset(seed=20260820)
        talent_class = type(env._rl_player.talent).__name__
        talent_onehot_sum = float(np.sum(obs[287:301]))
    finally:
        env.close()
    if talent_onehot_sum == 0.0:
        findings.append({
            "code": "m9_talent_identity_unobserved",
            "severity": "blocker",
            "evidence": {
                "talent_class": talent_class,
                "talent_onehot_sum": talent_onehot_sum,
            },
        })

    from rl.reward_m9 import m9_event_reward

    enforcement_reward = m9_event_reward(
        [{"type": "m9_police_enforcement", "captain": "rl_0"}],
        "rl_0",
    )
    if enforcement_reward == 0.0:
        findings.append({
            "code": "police_reward_owner_unattributed",
            "severity": "warning",
            "evidence": {
                "event_field": "captain",
                "reward": enforcement_reward,
            },
        })
    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description="M9 RL 规则契约与评估基线探针")
    parser.add_argument("--model", type=str, default=None)
    parser.add_argument("--random", action="store_true", help="使用固定种子的随机合法策略")
    parser.add_argument("--episodes", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20260820)
    parser.add_argument("--opponents", type=int, default=1)
    parser.add_argument("--max-rounds", type=int, default=25)
    parser.add_argument("--opponent-bot", type=str, default="dummy")
    parser.add_argument("--talents", action="store_true")
    parser.add_argument(
        "--strict", action="store_true",
        help="发现 blocker 时返回非零；阶段 1 记录当前缺陷时默认不启用",
    )
    args = parser.parse_args()

    set_profile("m9-rfc")
    try:
        schema, findings = _schema_snapshot()
        findings.extend(_semantic_findings())
        if args.model and args.random:
            parser.error("--model 与 --random 不能同时使用")
        if args.model:
            from rl.maskable_recurrent import MaskableRecurrentPPO

            model = MaskableRecurrentPPO.load(args.model, device="cpu")
            recurrent = True
            policy_name = args.model
        elif args.random:
            model = _RandomLegalModel(args.seed)
            recurrent = False
            policy_name = "random_legal"
        else:
            model = _FirstLegalModel()
            recurrent = False
            policy_name = "first_legal"

        def env_factory():
            return BadtimeWarEnv(
                num_opponents=args.opponents,
                max_rounds=args.max_rounds,
                n_stack=1,
                opponent_bots=[args.opponent_bot] * args.opponents,
                enable_talents=args.talents,
                rl_talent=None if args.talents else 0,
            )

        # 引擎展示在探针中没有诊断价值，只保留最终 JSON。
        with contextlib.redirect_stdout(io.StringIO()):
            report = evaluate_policy(
                model,
                num_opponents=args.opponents,
                n_stack=1,
                n_episodes=args.episodes,
                seed=args.seed,
                recurrent=recurrent,
                env_factory=env_factory,
            )

        if report.official_settlement_rate < 1.0:
            findings.append({
                "code": "official_settlement_incomplete",
                "severity": "blocker",
                "evidence": {
                    "settled": report.official_settled_episodes,
                    "episodes": report.episodes,
                    "termination_reasons": report.termination_reasons,
                },
            })
        if report.official_contract_mismatches:
            findings.append({
                "code": "official_winner_score_mismatch",
                "severity": "blocker",
                "evidence": {
                    "mismatches": report.official_contract_mismatches,
                    "episodes": report.episodes,
                },
            })

        payload = {
            "profile": "m9-rfc",
            "policy": policy_name,
            "schema": schema,
            "evaluation": report.as_dict(),
            "findings": findings,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        if args.strict and any(item["severity"] == "blocker" for item in findings):
            raise SystemExit(1)
    finally:
        reset()


if __name__ == "__main__":
    main()
