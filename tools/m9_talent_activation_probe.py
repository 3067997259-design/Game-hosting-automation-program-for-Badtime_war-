"""
tools/m9_talent_activation_probe.py
───────────────────────────────────
P5 学生天赋激活探针：单一 checkpoint 在 B 组四课（T6/G1/G6/G7）上的
行为指标验收（§8.1 分诊口径——胜率不适用钻头课，§7.3 教训）。

指标（与可行性探针同源）：
    T6 = hotline 事件；G1 = firefly_supernova_granted（换装副作用）；
    G6/G7 = SP 1→0（即演扣减）。

用法：
    python tools/m9_talent_activation_probe.py --model <ckpt> [--episodes 8]
"""

from __future__ import annotations

import argparse
import os as _os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_os.environ.setdefault("OMP_NUM_THREADS", "1")
_os.environ.setdefault("MKL_NUM_THREADS", "1")

import numpy as np  # noqa: E402

from engine.experiments import set_profile  # noqa: E402

PROFILE = "m9-rfc"

_DRILLS = (
    # (场景, 天赋编号, 槽名, 判定函数后缀)
    ("talent_t6_hotline", 6, "T6"),
    ("talent_g1_burn", 8, "G1"),
    ("talent_g6_borrow", 13, "G6"),
    ("talent_g7_suppress", 14, "G7"),
)

# A 组 10 槽（P5 第三批门禁化）：自然局事件判定——分诊口径的机械化。
# 事件键 = 探针局内该天赋任一专属事件命中即激活。
GROUP_A_SLOTS: tuple[tuple[int, str, tuple[str, ...]], ...] = (
    (1, "T1", ("oneslash",)),
    (2, "T2", ("scissor",)),
    (3, "T3", ("star_attack",)),
    (4, "T4", ("hexagram",)),
    (5, "G0", ("g0_",)),
    (7, "T7", ("resurrection",)),
    (9, "G2", ("SHADOW", "TERMINAL_SONG")),
    (10, "G3", ("m9_g3",)),
    (11, "G4", ("g4_",)),
    (12, "G5", ("crystal_flower", "anchor_script", "MATERIALIZED_BY_ANCHOR")),
)


def run_natural_probe(
    model, talent_number: int, event_keys: tuple[str, ...], episodes: int,
) -> dict:
    """A 组槽位门禁：开天赋自然局（reserve 强制指定）查专属事件。"""
    import random

    from rl.env import BadtimeWarEnv

    hits = 0
    for ep in range(episodes):
        seed = 83_000 + talent_number * 131 + ep
        random.seed(seed)
        np.random.seed(seed % (2**32))
        env = BadtimeWarEnv(
            num_opponents=2, max_rounds=None, n_stack=1,
            enable_talents=True,
            rl_talent=talent_number, rl_talent_reserve=True,
        )
        try:
            obs, _info = env.reset(seed=seed)
            state = None
            done = False
            fired_any = False
            for _ in range(80):
                mask = env.action_masks()
                action, state = model.predict(
                    obs[None], state=state,
                    episode_start=np.array([state is None]),
                    action_masks=mask[None], deterministic=True,
                )
                obs, _r, term, trunc, info = env.step(int(action[0]))
                events = (info.get("reward_ledger") or {}) \
                    .get("event_types") or ()
                if any(
                    any(key in e for key in event_keys) for e in events
                ):
                    fired_any = True
                if term or trunc:
                    break
            hits += bool(fired_any)
        finally:
            env.close()
    return {"hits": hits, "episodes": episodes}


def run_probe(model, scenario: str, talent_number: int, episodes: int) -> dict:
    import random

    from rl.env import BadtimeWarEnv

    hits = 0
    for ep in range(episodes):
        seed = 71_000 + talent_number * 97 + ep
        # BasicAI 对手经全局 random 取随机（不经 env.np_random）——不按局
        # 播种则结果依赖进程内执行历史（四课共用进程时后者被前者污染）。
        random.seed(seed)
        np.random.seed(seed % (2**32))
        env = BadtimeWarEnv(
            num_opponents=2, max_rounds=4, n_stack=1,
            enable_talents=True,
            rl_talent=talent_number, rl_talent_reserve=True,
            action_contract="talent_course", course_scenario=scenario,
        )
        try:
            obs, _info = env.reset(seed=seed)
            state = None
            done = False
            fired: list[str] = []
            # SP 最小值：即演扣减可能被 T2 关注登记（mark_attention +1）
            # 或 grant 失败退款抵消——"终局 SP==0"不是即演证据，
            # "局内 SP 到过 0"才是。
            sp_min = 99
            for _ in range(24):
                mask = env.action_masks()
                action, state = model.predict(
                    obs[None], state=state,
                    episode_start=np.array([state is None]),
                    action_masks=mask[None], deterministic=True,
                )
                obs, _r, term, trunc, info = env.step(int(action[0]))
                fired.extend(
                    (info.get("reward_ledger") or {})
                    .get("event_types") or (),
                )
                m9_live = getattr(env._state, "m9_system", None)
                if m9_live is not None:
                    sp_min = min(sp_min, int(m9_live.get_sp("rl_0")))
                if term or trunc:
                    break
            m9 = getattr(env._state, "m9_system", None)
            final_sp = int(m9.get_sp("rl_0")) if m9 else -1
            if scenario == "talent_t6_hotline":
                ok = "hotline" in fired
            elif scenario == "talent_g1_burn":
                ok = "firefly_supernova_granted" in fired
            else:  # G6/G7：局内 SP 曾到 0（即演发生）
                ok = sp_min == 0
            hits += bool(ok)
        finally:
            env.close()
    return {"hits": hits, "episodes": episodes}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--episodes", type=int, default=8)
    parser.add_argument("--group", choices=["b", "a", "all"], default="b",
                        help="b=B组钻头课；a=A组自然局事件；all=14 槽全量")
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()

    set_profile(PROFILE)
    import rl.distill_student  # noqa: F401 —— 自定义策略类按引用反序列化
    from rl.maskable_recurrent import MaskableRecurrentPPO

    model = MaskableRecurrentPPO.load(args.model, device=args.device)
    print(f"[probe] model={args.model}")
    all_pass = True
    if args.group in ("b", "all"):
        for scenario, number, slot in _DRILLS:
            result = run_probe(model, scenario, number, args.episodes)
            rate = result["hits"] / result["episodes"]
            passed = rate >= 0.75
            all_pass &= passed
            print(
                f"[probe] {slot} {scenario}: {result['hits']}/"
                f"{result['episodes']} = {rate:.0%} "
                f"{'PASS' if passed else 'FAIL'}（阈值 75%）",
            )
    if args.group in ("a", "all"):
        for number, slot, keys in GROUP_A_SLOTS:
            result = run_natural_probe(model, number, keys, args.episodes)
            rate = result["hits"] / result["episodes"]
            passed = rate >= 0.5  # 自然局：单局激活机会 < 1，阈值 50%
            all_pass &= passed
            print(
                f"[probe] {slot} 自然局激活: {result['hits']}/"
                f"{result['episodes']} = {rate:.0%} "
                f"{'PASS' if passed else 'FAIL'}（阈值 50%）",
            )
    print(f"[probe] 总判定: {'PASS' if all_pass else 'FAIL'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
