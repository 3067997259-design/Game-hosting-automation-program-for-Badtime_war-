"""
tools/probe_env_scaling.py
──────────────────────────
裸引擎并行容量探针：绕开 SB3 主循环/策略推理/IPC，K 个独立进程各自
用随机合法动作步进 BadtimeWarEnv，测聚合决策率随 K 的伸缩性。

用途：区分 collect 侧吞吐墙的归属——
  - 聚合随 K 线性增长 → 引擎并行容量充足，墙在 RL 主循环（推理/IPC/缓冲）；
  - 聚合钉在与训练相同的水平 → 墙是引擎×核数（加核有效，改主循环无效）。

用法（仓库根目录）：
    python tools/probe_env_scaling.py [--workers 1 2 4 8 14] [--steps 400]
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _worker(args: tuple[int, int, int]) -> tuple[int, float]:
    """单进程：构建 env，随机合法动作步进 n_steps，返回 (steps, 秒)。"""
    seed, n_steps, num_opponents = args
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    # 子进程不需要 GPU：屏蔽 CUDA DLL 加载，避免每进程数 GB 的虚拟内存 commit
    # （否则多 worker 下触发 WinError 1455 页面文件不足）
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    import numpy as np

    # 构建期静音（与 rl.train.make_env 相同处理）
    devnull = open(os.devnull, "w")
    old_stdout = sys.stdout
    sys.stdout = devnull
    try:
        from rl.env import BadtimeWarEnv
        env = BadtimeWarEnv(num_opponents=num_opponents, max_rounds=24)
        obs, info = env.reset(seed=seed)
    finally:
        sys.stdout = old_stdout
        devnull.close()

    rng = np.random.default_rng(seed)
    masks = info["action_masks"]
    t0 = time.perf_counter()
    for _ in range(n_steps):
        legal = np.flatnonzero(masks)
        action = int(rng.choice(legal))
        obs, _r, term, trunc, info = env.step(action)
        if term or trunc:
            obs, info = env.reset()
        masks = info["action_masks"]
    return n_steps, time.perf_counter() - t0


def main() -> None:
    parser = argparse.ArgumentParser(description="裸引擎并行容量探针")
    parser.add_argument("--workers", type=int, nargs="+", default=[1, 2, 4, 8, 14])
    parser.add_argument("--steps", type=int, default=400, help="每 worker 步数")
    parser.add_argument("--opponents", type=int, default=5)
    args = parser.parse_args()

    print(f"CPU 逻辑核: {os.cpu_count()}；每 worker {args.steps} 步，"
          f"opponents={args.opponents}，max_rounds=24")
    ctx = mp.get_context("spawn")
    for k in args.workers:
        jobs = [(1000 + i, args.steps, args.opponents) for i in range(k)]
        with ctx.Pool(k) as pool:
            t0 = time.perf_counter()
            results = pool.map(_worker, jobs)
            wall = time.perf_counter() - t0
        total_steps = sum(s for s, _ in results)
        per_env = [s / t for s, t in results]
        aggregate = total_steps / wall
        print(f"K={k:>2}: 聚合 {aggregate:7.1f} 决策/s（含进程启动）；"
              f"纯步进聚合 {sum(per_env):7.1f}/s；"
              f"单 env {min(per_env):.1f}–{max(per_env):.1f}/s")


if __name__ == "__main__":
    main()
