"""
rl/diagnose_selfplay.py
───────────────────────
Self-play 多进程性能诊断脚本。

在单进程（DummyVecEnv）和多进程（SubprocVecEnv）模式下运行极少量的游戏步数，
收集每个关键操作的精确耗时，帮助定位 self-play 多进程训练极慢的根因。

用法：
    python -m rl.diagnose_selfplay \
        --seed-model pretrained/g7_warmstart.zip \
        --opponents 5 \
        --n-envs 16 \
        --n-steps 200
"""

from __future__ import annotations
import os as _os
# SubprocVecEnv(start_method="spawn") 子进程继承父进程的环境变量。
# 必须在 import numpy/torch 之前设置，否则 BLAS 会用默认线程数初始化。
# 16 个子进程 × 32 默认线程 = 512 线程争抢 32 核，导致 load average > 260。
# setdefault 尊重用户显式设置的环境变量。
_os.environ.setdefault("OMP_NUM_THREADS", "1")
_os.environ.setdefault("MKL_NUM_THREADS", "1")
_os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv

from sb3_contrib import MaskablePPO

from rl.env import BadtimeWarEnv


# ─────────────────────────────────────────────────────────────────────────────
#  DiagnosticEnv：带细粒度计时的诊断版环境
# ─────────────────────────────────────────────────────────────────────────────


class DiagnosticEnv(BadtimeWarEnv):
    """带细粒度计时的诊断版环境。"""

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self._diag_timings: Dict[str, Any] = {}
        self._reset_count = 0
        self._step_count = 0
        self._opponent_timings: List[Dict[str, Any]] = []

    def reset(self, **kwargs: Any):
        t0 = time.perf_counter()

        # monkey-patch opponent_pool.sample_opponent_controller
        original_sample = None
        sample_timings: List[Dict[str, Any]] = []
        if self.opponent_pool is not None:
            original_sample = self.opponent_pool.sample_opponent_controller

            def timed_sample():
                st = time.perf_counter()
                result = original_sample()
                elapsed = time.perf_counter() - st
                ctrl = result[0] if isinstance(result, tuple) else result
                ctrl_type = type(ctrl).__name__
                sample_timings.append({"type": ctrl_type, "time_ms": elapsed * 1000})
                return result

            self.opponent_pool.sample_opponent_controller = timed_sample

        obs, info = super().reset(**kwargs)

        # 恢复原始方法
        if original_sample is not None:
            self.opponent_pool.sample_opponent_controller = original_sample

        t_total = time.perf_counter() - t0
        self._reset_count += 1

        # 把计时数据塞进 info dict（SubprocVecEnv 会传回主进程）
        info["_diag_reset"] = {
            "reset_id": self._reset_count,
            "total_ms": t_total * 1000,
            "sample_timings": sample_timings,
            "n_opponents": self.num_opponents,
            "pid": os.getpid(),
        }

        # monkey-patch 当前局所有对手控制器的 get_command 和 _rl_choose
        self._patch_opponent_controllers()

        return obs, info

    def _patch_opponent_controllers(self) -> None:
        """给当前局的所有对手控制器添加计时包装。"""
        if self._state is None:
            return
        for pid in self._state.player_order:
            if pid == "rl_0":
                continue
            player = self._state.get_player(pid)
            if player is None or player.controller is None:
                continue
            ctrl = player.controller
            ctrl_type = type(ctrl).__name__

            # 只包装 RL 对手控制器（有 _predict_action 方法的）
            if hasattr(ctrl, '_predict_action'):
                original_get_cmd = ctrl.get_command
                original_rl_choose = ctrl._rl_choose
                timings_ref = self._opponent_timings

                def make_timed_get_cmd(orig, ctype, timings):
                    def timed_get_cmd(player, game_state, available_actions, context=None):
                        st = time.perf_counter()
                        result = orig(player, game_state, available_actions, context)
                        elapsed = time.perf_counter() - st
                        timings.append({"op": "get_command", "type": ctype, "time_ms": elapsed * 1000})
                        return result
                    return timed_get_cmd

                def make_timed_choose(orig, ctype, timings):
                    def timed_choose(prompt, options, context=None):
                        st = time.perf_counter()
                        result = orig(prompt, options, context)
                        elapsed = time.perf_counter() - st
                        timings.append({"op": "_rl_choose", "type": ctype, "time_ms": elapsed * 1000})
                        return result
                    return timed_choose

                ctrl.get_command = make_timed_get_cmd(original_get_cmd, ctrl_type, timings_ref)
                ctrl._rl_choose = make_timed_choose(original_rl_choose, ctrl_type, timings_ref)

    def step(self, action: int):
        t0 = time.perf_counter()
        obs, reward, terminated, truncated, info = super().step(action)
        t_total = time.perf_counter() - t0

        self._step_count += 1

        # 收集自上次 step 以来的对手计时数据
        opp_timings = list(self._opponent_timings)
        self._opponent_timings.clear()

        info["_diag_step"] = {
            "step_id": self._step_count,
            "total_ms": t_total * 1000,
            "opponent_calls": opp_timings,
            "n_opp_get_cmd": sum(1 for t in opp_timings if t["op"] == "get_command"),
            "n_opp_choose": sum(1 for t in opp_timings if t["op"] == "_rl_choose"),
            "pid": os.getpid(),
        }

        if terminated or truncated:
            info["_diag_game_over"] = {
                "total_steps_this_game": self._step_count,
                "winner": getattr(self._state, "winner", None),
            }
            self._step_count = 0

        return obs, reward, terminated, truncated, info


# ─────────────────────────────────────────────────────────────────────────────
#  环境工厂
# ─────────────────────────────────────────────────────────────────────────────


def make_diagnostic_env(
    num_opponents: int,
    max_rounds: Optional[int],
    seed: int,
    rank: int,
    n_stack: int,
    opponent_pool: Any,
    rl_talent: Optional[int],
    enable_talents: bool,
):
    """返回一个创建 DiagnosticEnv 的闭包，供 DummyVecEnv / SubprocVecEnv 使用。"""

    def _init():
        devnull = open(os.devnull, 'w')
        old_stdout = sys.stdout
        sys.stdout = devnull
        try:
            env = DiagnosticEnv(
                num_opponents=num_opponents,
                max_rounds=max_rounds,
                n_stack=n_stack,
                opponent_pool=opponent_pool,
                enable_talents=enable_talents,
                rl_talent=rl_talent,
            )
            env = Monitor(env)
            env.reset(seed=seed + rank)
        finally:
            sys.stdout = old_stdout
            devnull.close()
        return env

    return _init


# ─────────────────────────────────────────────────────────────────────────────
#  诊断运行
# ─────────────────────────────────────────────────────────────────────────────


def run_diagnostic(
    n_envs: int,
    n_steps: int,
    args: argparse.Namespace,
    opponent_pool: Any,
) -> Dict[str, Any]:
    """手动步进 env，收集计时数据。"""

    env_fns = [
        make_diagnostic_env(
            num_opponents=args.opponents,
            max_rounds=None,
            seed=args.seed,
            rank=i,
            n_stack=args.n_stack,
            opponent_pool=opponent_pool,
            rl_talent=args.rl_talent,
            enable_talents=True,
        )
        for i in range(n_envs)
    ]

    if n_envs > 1:
        env = SubprocVecEnv(env_fns, start_method="spawn")
    else:
        env = DummyVecEnv(env_fns)

    results: Dict[str, Any] = {
        "reset_timings": [],
        "step_timings": [],
        "opponent_call_timings": [],
        "wall_clock_steps": [],
        "wall_clock_resets": [],
        "games_completed": 0,
    }

    # 初始 reset
    t0 = time.perf_counter()
    obs = env.reset()
    t_reset = time.perf_counter() - t0
    results["wall_clock_resets"].append(t_reset * 1000)

    # SubprocVecEnv.reset() 不返回 info（SB3 的限制），
    # reset 的内部计时只能在 DummyVecEnv 模式下获取。

    # 手动步进
    for step_i in range(n_steps):
        # 生成随机合法动作
        if hasattr(env, 'env_method'):
            masks = env.env_method("action_masks")
        else:
            masks = [env.envs[0].action_masks()]

        actions = []
        for mask in masks:
            valid_actions = np.where(mask)[0]
            if len(valid_actions) > 0:
                actions.append(np.random.choice(valid_actions))
            else:
                actions.append(0)  # forfeit
        actions = np.array(actions)

        t0 = time.perf_counter()
        obs, rewards, dones, infos = env.step(actions)
        t_step = time.perf_counter() - t0

        results["wall_clock_steps"].append(t_step * 1000)

        # 从 info dict 中提取诊断数据
        for info in (infos if isinstance(infos, (list, np.ndarray)) else [infos]):
            if isinstance(info, dict):
                if "_diag_step" in info:
                    results["step_timings"].append(info["_diag_step"])
                    results["opponent_call_timings"].extend(
                        info["_diag_step"].get("opponent_calls", [])
                    )
                if "_diag_reset" in info:
                    results["reset_timings"].append(info["_diag_reset"])
                if "_diag_game_over" in info:
                    results["games_completed"] += 1

    env.close()
    return results


# ─────────────────────────────────────────────────────────────────────────────
#  统计辅助
# ─────────────────────────────────────────────────────────────────────────────


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = int(len(s) * pct / 100)
    idx = min(idx, len(s) - 1)
    return s[idx]


def _avg(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


# ─────────────────────────────────────────────────────────────────────────────
#  报告输出
# ─────────────────────────────────────────────────────────────────────────────


def print_report(label: str, results: Dict[str, Any]) -> None:
    wall_steps = results["wall_clock_steps"]
    wall_resets = results["wall_clock_resets"]
    step_timings = results["step_timings"]
    reset_timings = results["reset_timings"]
    opp_timings = results["opponent_call_timings"]

    print(f"\n=== {label} 诊断报告 ===\n")
    print(f"  总步数: {len(wall_steps)} | 完成游戏: {results['games_completed']}")

    # ── 主进程视角 ──
    print("\n  ── 主进程视角 ──")
    if wall_resets:
        print(
            f"  reset() 耗时:  avg={_avg(wall_resets):.1f}ms  "
            f"min={min(wall_resets):.1f}ms  max={max(wall_resets):.1f}ms"
        )
    if wall_steps:
        print(
            f"  step()  耗时:  avg={_avg(wall_steps):.1f}ms  "
            f"min={min(wall_steps):.1f}ms  max={max(wall_steps):.1f}ms  "
            f"P50={_percentile(wall_steps, 50):.1f}  "
            f"P95={_percentile(wall_steps, 95):.1f}  "
            f"P99={_percentile(wall_steps, 99):.1f}"
        )

    # ── 子进程内部 ──
    print("\n  ── 子进程内部（仅 DummyVecEnv 可获取）──")
    if reset_timings:
        reset_totals = [r["total_ms"] for r in reset_timings]
        print("  reset() 内部:")
        print(f"    总耗时:                avg={_avg(reset_totals):.1f}ms")

        # sample_opponent 统计
        all_samples: List[Dict[str, Any]] = []
        for r in reset_timings:
            all_samples.extend(r.get("sample_timings", []))
        if all_samples:
            by_type: Dict[str, List[float]] = {}
            for s in all_samples:
                by_type.setdefault(s["type"], []).append(s["time_ms"])
            parts = []
            for t, times in sorted(by_type.items()):
                parts.append(f"{t}: {len(times)}次 avg={_avg(times):.1f}ms")
            print(
                f"    sample_opponent() ×{len(all_samples)}:  "
                f"avg={_avg([s['time_ms'] for s in all_samples]):.1f}ms "
                f"({' | '.join(parts)})"
            )
    else:
        print("  （SubprocVecEnv 模式下无法获取子进程内部 reset 计时）")

    if step_timings:
        step_totals = [s["total_ms"] for s in step_timings]
        print("\n  step() 内部:")
        print(f"    总耗时:                avg={_avg(step_totals):.1f}ms")

        get_cmd_times = [t["time_ms"] for t in opp_timings if t["op"] == "get_command"]
        choose_times = [t["time_ms"] for t in opp_timings if t["op"] == "_rl_choose"]

        if get_cmd_times:
            print(
                f"    对手 get_command():    {len(get_cmd_times)}次调用, "
                f"avg={_avg(get_cmd_times):.1f}ms, "
                f"P95={_percentile(get_cmd_times, 95):.1f}ms"
            )
        if choose_times:
            print(
                f"    对手 _rl_choose():     {len(choose_times)}次调用, "
                f"avg={_avg(choose_times):.1f}ms, "
                f"P95={_percentile(choose_times, 95):.1f}ms"
            )
    else:
        print("\n  （SubprocVecEnv 模式下无法获取子进程内部 step 计时）")

    # ── 对手推理统计（按类型分组）──
    if opp_timings:
        print("\n  ── 对手推理统计 ──")
        by_type_op: Dict[str, Dict[str, List[float]]] = {}
        for t in opp_timings:
            by_type_op.setdefault(t["type"], {}).setdefault(t["op"], []).append(t["time_ms"])

        for ctrl_type, ops in sorted(by_type_op.items()):
            print(f"  {ctrl_type}:")
            for op_name, times in sorted(ops.items()):
                print(
                    f"    {op_name}: {len(times)}次, "
                    f"avg={_avg(times):.1f}ms, "
                    f"P95={_percentile(times, 95):.1f}ms"
                )


def print_comparison(single: Dict[str, Any], multi: Dict[str, Any]) -> None:
    print(f"\n{'=' * 70}")
    print("  单进程 vs 多进程对比")
    print(f"{'=' * 70}")

    single_avg = _avg(single["wall_clock_steps"]) if single["wall_clock_steps"] else 0.0
    multi_avg = _avg(multi["wall_clock_steps"]) if multi["wall_clock_steps"] else 0.0

    print("\n  step() 主进程视角:")
    print(f"    单进程: avg={single_avg:.1f}ms")
    print(f"    多进程: avg={multi_avg:.1f}ms")

    if single_avg > 0:
        ratio = multi_avg / single_avg
        print(f"    倍率:   {ratio:.1f}x（多进程/单进程）")

        print()
        if ratio > 3:
            print("  → 问题在 SubprocVecEnv 通信或子进程争抢")
        elif ratio > 1.5:
            print("  → 多进程有一定开销，可能是 pickle 序列化或进程间通信瓶颈")
        else:
            print("  → 多进程开销不大，问题可能在 SB3 learn() 循环的其他开销")

    # 额外统计
    single_resets = single["wall_clock_resets"]
    multi_resets = multi["wall_clock_resets"]
    if single_resets and multi_resets:
        print(f"\n  reset() 主进程视角:")
        print(f"    单进程: avg={_avg(single_resets):.1f}ms")
        print(f"    多进程: avg={_avg(multi_resets):.1f}ms")

    print()


# ─────────────────────────────────────────────────────────────────────────────
#  CLI 参数
# ─────────────────────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Self-play 多进程性能诊断"
    )
    p.add_argument("--seed-model", type=str, required=True,
                   help="种子模型路径（.zip）")
    p.add_argument("--opponents", type=int, default=5,
                   help="对手数量 (1-5)")
    p.add_argument("--n-envs", type=int, default=16,
                   help="多进程环境数")
    p.add_argument("--n-steps", type=int, default=200,
                   help="每个阶段的步数")
    p.add_argument("--n-stack", type=int, default=30,
                   help="帧堆叠数量")
    p.add_argument("--seed", type=int, default=42,
                   help="随机种子")
    p.add_argument("--basic-ai-prob", type=float, default=0.5,
                   help="BasicAI 混入概率")
    p.add_argument("--rl-talent", type=int, default=None,
                   help="RL 天赋编号（None=RL自选, 0=无天赋）")
    return p.parse_args()


# ─────────────────────────────────────────────────────────────────────────────
#  主函数
# ─────────────────────────────────────────────────────────────────────────────


def main() -> None:
    import torch

    args = parse_args()

    torch.set_num_threads(min(8, os.cpu_count() or 1))

    # 创建 opponent_pool
    from rl.self_play import OpponentPool

    pool_dir = Path("logs") / "diagnose_pool"
    opponent_pool = OpponentPool(
        pool_dir=str(pool_dir),
        n_stack=args.n_stack,
        max_pool_size=20,
        basic_ai_prob=args.basic_ai_prob,
    )
    seed_m = MaskablePPO.load(args.seed_model)
    opponent_pool.save_current_model(seed_m, step=0)
    del seed_m

    print(f"\n{'=' * 70}")
    print("  Self-play 多进程性能诊断")
    print(f"{'=' * 70}")
    print(f"  种子模型:    {args.seed_model}")
    print(f"  对手数:      {args.opponents}")
    print(f"  多进程环境数: {args.n_envs}")
    print(f"  每阶段步数:  {args.n_steps}")
    print(f"  BasicAI 概率: {args.basic_ai_prob}")
    print(f"{'=' * 70}\n")

    # ── 阶段 1：单进程诊断 ──
    print("=" * 70)
    print("  阶段 1：单进程诊断 (DummyVecEnv, n_envs=1)")
    print("=" * 70)
    single_results = run_diagnostic(
        n_envs=1, n_steps=args.n_steps, args=args, opponent_pool=opponent_pool,
    )
    print_report("单进程", single_results)

    # ── 阶段 2：多进程诊断 ──
    print("=" * 70)
    print(f"  阶段 2：多进程诊断 (SubprocVecEnv, n_envs={args.n_envs})")
    print("=" * 70)
    multi_results = run_diagnostic(
        n_envs=args.n_envs, n_steps=args.n_steps, args=args, opponent_pool=opponent_pool,
    )
    print_report("多进程", multi_results)

    # ── 阶段 3：对比 ──
    print_comparison(single_results, multi_results)


if __name__ == "__main__":
    main()
