"""
rl/diagnose_single_game.py
───────────────────────────
直接用游戏引擎（不经过 BadtimeWarEnv / SubprocVecEnv）跑一局 6 人自对弈游戏，
全程输出游戏日志，并对每个玩家的每次 get_command / choose / confirm 调用进行
精确计时。用于定位 self-play 多进程训练极慢的根因。

用法：
    python -m rl.diagnose_single_game \
        --model checkpoints/maskable_ppo_5opp_20260423_000706/model_22600000_steps.zip \
        --opponents 5 \
        --rl-opponents 3 \
        --n-stack 30
"""

from __future__ import annotations

import argparse
import random
import time
import traceback
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import torch
from sb3_contrib import MaskablePPO

from engine.game_state import GameState
from engine.round_manager import RoundManager
from engine.game_setup import (
    TALENT_TABLE,
    AI_PERSONALITIES,
    AI_DISABLED_TALENTS,
)
from models.player import Player
from controllers.ai_basic import BasicAIController
from rl.self_play import (
    TorchScriptRLController,
    OpponentRLController,
)


# ─────────────────────────────────────────────────────────────────────────────
#  TimingStats：调用计时收集器
# ─────────────────────────────────────────────────────────────────────────────

class TimingStats:
    def __init__(self):
        self.calls: Dict[tuple, List[float]] = defaultdict(list)
        self.current_round: int = 0
        self.call_log: List[tuple] = []


# ─────────────────────────────────────────────────────────────────────────────
#  模型加载
# ─────────────────────────────────────────────────────────────────────────────

def load_controller(model_path: str, n_stack: int):
    """加载模型，优先 TorchScript，回退 MaskablePPO。"""
    pts_path = Path(model_path).with_suffix(".pts")
    if pts_path.exists():
        return TorchScriptRLController(pts_path=str(pts_path), n_stack=n_stack)

    # 尝试导出 TorchScript
    try:
        from rl.export_torchscript import export_torchscript
        model = MaskablePPO.load(model_path)
        export_torchscript(model, str(pts_path), n_stack=n_stack)
        jit_model = torch.jit.load(str(pts_path))
        jit_model.eval()
        return TorchScriptRLController(n_stack=n_stack, _jit_model=jit_model)
    except Exception:
        pass

    # 回退到完整模型
    model = MaskablePPO.load(model_path)
    return OpponentRLController(n_stack=n_stack, _model=model)


# ─────────────────────────────────────────────────────────────────────────────
#  计时装饰器（monkey-patch）
# ─────────────────────────────────────────────────────────────────────────────

def wrap_controller(player: Player, stats: TimingStats):
    """给控制器的 get_command / choose / confirm 套上计时装饰器。"""
    ctrl = player.controller
    player_name = player.name
    ctrl_type = type(ctrl).__name__

    original_get_command = ctrl.get_command
    original_choose = ctrl.choose
    original_confirm = ctrl.confirm

    def timed_get_command(player, game_state, available_actions, context=None):
        stats.current_round = game_state.current_round
        attempt = (context or {}).get("attempt", 1)
        t0 = time.perf_counter()
        result = original_get_command(player, game_state, available_actions, context)
        elapsed = (time.perf_counter() - t0) * 1000

        key = (player_name, "get_command", f"attempt={attempt}")
        stats.calls[key].append(elapsed)
        stats.call_log.append((
            game_state.current_round, player_name, "get_command",
            f"attempt={attempt}", elapsed, str(result)[:50],
        ))

        print(f"  [{game_state.current_round:>3}\u8f6e] {player_name}({ctrl_type}) "
              f"get_command \u2192 {str(result)[:40]:<40} {elapsed:>8.2f}ms")

        return result

    def timed_choose(prompt, options, context=None):
        situation = (context or {}).get("situation", "unknown")
        t0 = time.perf_counter()
        result = original_choose(prompt, options, context)
        elapsed = (time.perf_counter() - t0) * 1000

        key = (player_name, "choose", situation)
        stats.calls[key].append(elapsed)
        stats.call_log.append((
            stats.current_round, player_name, "choose",
            situation, elapsed, str(result)[:50],
        ))

        print(f"  [{stats.current_round:>3}\u8f6e] {player_name}({ctrl_type}) "
              f"choose({situation}) \u2192 {str(result)[:30]:<30} {elapsed:>8.2f}ms "
              f"[{len(options)}\u4e2a\u9009\u9879]")

        return result

    def timed_confirm(prompt, context=None):
        phase = (context or {}).get("phase", "unknown")
        t0 = time.perf_counter()
        result = original_confirm(prompt, context)
        elapsed = (time.perf_counter() - t0) * 1000

        key = (player_name, "confirm", phase)
        stats.calls[key].append(elapsed)
        stats.call_log.append((
            stats.current_round, player_name, "confirm",
            phase, elapsed, str(result),
        ))

        print(f"  [{stats.current_round:>3}\u8f6e] {player_name}({ctrl_type}) "
              f"confirm({phase}) \u2192 {result!s:<30} {elapsed:>8.2f}ms")

        return result

    ctrl.get_command = timed_get_command
    ctrl.choose = timed_choose
    ctrl.confirm = timed_confirm


# ─────────────────────────────────────────────────────────────────────────────
#  天赋分配（复用 stats_runner 的随机模式）
# ─────────────────────────────────────────────────────────────────────────────

def assign_talents(game_state: GameState):
    """为所有玩家随机分配天赋（均匀随机，排除 AI_DISABLED_TALENTS）。"""
    taken: set = set()
    for pid in game_state.player_order:
        player = game_state.get_player(pid)
        if player is None:
            continue
        available = [
            (n, name, cls, desc)
            for n, name, cls, desc in TALENT_TABLE
            if n not in taken and n not in AI_DISABLED_TALENTS
        ]
        if not available:
            continue
        chosen = random.choice(available)
        n, name, cls, desc = chosen
        talent_inst = cls(pid, game_state)
        player.talent = talent_inst
        player.talent_name = name
        talent_inst.on_register()
        taken.add(n)


# ─────────────────────────────────────────────────────────────────────────────
#  游戏创建
# ─────────────────────────────────────────────────────────────────────────────

def run_diagnosed_game(args):
    """创建游戏并运行带诊断的完整一局。"""
    if args.seed is not None:
        random.seed(args.seed)
        np.random.seed(args.seed)
        torch.manual_seed(args.seed)

    game_state = GameState()

    # 创建 RL "主角"玩家（用 OpponentRLController 子类，不走 env 同步）
    rl_main = load_controller(args.model, args.n_stack)
    rl_main.reset_game_state()
    main_player = Player("p0", "RL_Main", rl_main)
    game_state.add_player(main_player)

    # 创建对手
    controllers = []
    for i in range(args.opponents):
        if i < args.rl_opponents:
            ctrl = load_controller(args.model, args.n_stack)
            ctrl.reset_game_state()
            name = f"RL_{i + 1}"
        else:
            ctrl = BasicAIController(personality=random.choice(AI_PERSONALITIES))
            name = f"AI_{i + 1}"
        p = Player(f"p{i + 1}", name, ctrl)
        game_state.add_player(p)
        controllers.append(ctrl)

    # 随机化顺序
    random.shuffle(game_state.player_order)

    # 天赋分配
    if args.enable_talents:
        assign_talents(game_state)

    # 为每个 RL 控制器设置 player_ref（天赋分配后）
    for pid in game_state.player_order:
        player = game_state.get_player(pid)
        if player is None:
            continue
        ctrl = player.controller
        if isinstance(ctrl, (TorchScriptRLController, OpponentRLController)):
            ctrl.set_player_ref(player, game_state)

    # 设置最大轮数
    game_state.max_rounds = GameState.compute_default_max_rounds(
        len(game_state.player_order)
    )

    # 计时统计
    stats = TimingStats()
    for pid in game_state.player_order:
        player = game_state.get_player(pid)
        if player is not None:
            wrap_controller(player, stats)

    # 运行游戏
    run_game_with_diagnostics(game_state, stats)


# ─────────────────────────────────────────────────────────────────────────────
#  游戏运行 + 每轮摘要
# ─────────────────────────────────────────────────────────────────────────────

def run_game_with_diagnostics(game_state: GameState, stats: TimingStats):
    round_mgr = RoundManager(game_state)

    print(f"\n{'=' * 80}")
    print(f"  \u6e38\u620f\u5f00\u59cb: {len(game_state.player_order)} \u4eba\u5c40")
    print(f"  \u6700\u5927\u8f6e\u6570: {game_state.max_rounds}")
    print(f"  \u73a9\u5bb6:")
    for pid in game_state.player_order:
        p = game_state.get_player(pid)
        if p is None:
            continue
        ctrl_type = type(p.controller).__name__
        talent_name = getattr(p, "talent_name", "\u65e0") or "\u65e0"
        print(f"    {p.name} ({ctrl_type}) \u5929\u8d4b=\u3010{talent_name}\u3011")
    print(f"{'=' * 80}\n")

    game_start_time = time.perf_counter()

    try:
        while not game_state.game_over:
            round_num = game_state.current_round + 1
            round_t0 = time.perf_counter()

            _line = "\u2500" * 60
            print(f"\n{_line}")
            print(f"  \u25b6 \u8f6e\u6b21 {round_num} \u5f00\u59cb")
            print(f"{_line}")

            # 打印存活玩家状态
            for pid in game_state.player_order:
                p = game_state.get_player(pid)
                if p and p.is_alive():
                    talent = getattr(p, "talent_name", "\u65e0") or "\u65e0"
                    print(
                        f"    {p.name}: HP={p.hp:.1f}/{p.max_hp:.1f} "
                        f"\u4f4d\u7f6e={p.location or '?'} "
                        f"\u51fb\u6740={p.kill_count} "
                        f"\u5929\u8d4b=\u3010{talent}\u3011"
                    )
            print()

            round_mgr.run_one_round()

            round_elapsed = (time.perf_counter() - round_t0) * 1000

            # 轮次摘要
            round_calls = [
                c for c in stats.call_log if c[0] == game_state.current_round
            ]

            print(f"\n  \u2500\u2500 \u8f6e\u6b21 {game_state.current_round} \u6458\u8981 \u2500\u2500")
            print(f"  \u603b\u8017\u65f6: {round_elapsed:.1f}ms | \u8c03\u7528\u6b21\u6570: {len(round_calls)}")

            # 按玩家统计
            player_call_counts = Counter(c[1] for c in round_calls)
            player_call_times: Dict[str, float] = defaultdict(float)
            for c in round_calls:
                player_call_times[c[1]] += c[4]
            for pname, count in player_call_counts.most_common():
                total_ms = player_call_times[pname]
                print(f"    {pname}: {count}\u6b21\u8c03\u7528, \u603b\u8ba1{total_ms:.1f}ms")

            # 检查胜利
            winner_id = game_state.check_victory()
            if winner_id:
                game_state.game_over = True
                game_state.winner = winner_id
                break

            if game_state.is_max_rounds_reached():
                game_state.game_over = True
                game_state.winner = "nobody"
                break

            # 安全网
            if game_state.current_round > game_state.max_rounds * 1.5:
                print(
                    f"\n  \u26a0\ufe0f \u5b89\u5168\u7f51\u89e6\u53d1\uff1a"
                    f"\u8f6e\u6b21 {game_state.current_round} "
                    f"\u8d85\u8fc7 max_rounds*1.5"
                )
                game_state.game_over = True
                game_state.winner = "nobody"
                break

    except Exception as e:
        print(f"\n  \u274c \u6e38\u620f\u5d29\u6e83: {e}")
        traceback.print_exc()
        game_state.game_over = True
        game_state.winner = "nobody"

    game_elapsed = (time.perf_counter() - game_start_time) * 1000
    print_final_stats(game_state, stats, game_elapsed)


# ─────────────────────────────────────────────────────────────────────────────
#  最终统计输出
# ─────────────────────────────────────────────────────────────────────────────

def print_final_stats(game_state: GameState, stats: TimingStats, game_elapsed_ms: float):
    print(f"\n{'=' * 80}")
    print(f"  \u6e38\u620f\u7ed3\u675f")
    print(f"{'=' * 80}")
    print(f"  \u603b\u8f6e\u6b21: {game_state.current_round}")
    print(f"  \u603b\u8017\u65f6: {game_elapsed_ms:.1f}ms ({game_elapsed_ms / 1000:.1f}s)")
    print(f"  \u80dc\u8005: {game_state.winner}")
    print(f"  \u603b\u8c03\u7528\u6b21\u6570: {len(stats.call_log)}")

    # 按方法类型统计
    print(f"\n  \u2500\u2500 \u6309\u65b9\u6cd5\u7c7b\u578b\u7edf\u8ba1 \u2500\u2500")
    method_stats: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "total_ms": 0.0, "max_ms": 0.0}
    )
    for _, pname, method, situation, elapsed, _ in stats.call_log:
        key = f"{method}({situation})" if method != "get_command" else "get_command"
        method_stats[key]["count"] += 1
        method_stats[key]["total_ms"] += elapsed
        method_stats[key]["max_ms"] = max(method_stats[key]["max_ms"], elapsed)

    for key, s in sorted(method_stats.items(), key=lambda x: -x[1]["total_ms"]):
        avg = s["total_ms"] / s["count"] if s["count"] > 0 else 0
        print(
            f"    {key:<45} {s['count']:>5}\u6b21  "
            f"\u603b\u8ba1={s['total_ms']:>8.1f}ms  "
            f"avg={avg:>6.2f}ms  "
            f"max={s['max_ms']:>8.2f}ms"
        )

    # 按玩家统计
    print(f"\n  \u2500\u2500 \u6309\u73a9\u5bb6\u7edf\u8ba1 \u2500\u2500")
    player_stats: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "total_ms": 0.0}
    )
    for _, pname, method, situation, elapsed, _ in stats.call_log:
        player_stats[pname]["count"] += 1
        player_stats[pname]["total_ms"] += elapsed

    for pname, s in sorted(player_stats.items(), key=lambda x: -x[1]["total_ms"]):
        avg = s["total_ms"] / s["count"] if s["count"] > 0 else 0
        print(
            f"    {pname:<15} {s['count']:>5}\u6b21  "
            f"\u603b\u8ba1={s['total_ms']:>8.1f}ms  avg={avg:>6.2f}ms"
        )

    # 慢调用 Top 10
    print(f"\n  \u2500\u2500 \u6700\u6162\u7684 10 \u6b21\u8c03\u7528 \u2500\u2500")
    sorted_calls = sorted(stats.call_log, key=lambda x: -x[4])[:10]
    for round_num, pname, method, situation, elapsed, result in sorted_calls:
        print(
            f"    [{round_num:>3}\u8f6e] {pname} {method}({situation}) "
            f"\u2192 {result[:30]} {elapsed:.2f}ms"
        )

    # 每轮耗时趋势
    print(f"\n  \u2500\u2500 \u6bcf\u8f6e\u8c03\u7528\u6b21\u6570\u548c\u8017\u65f6\u8d8b\u52bf \u2500\u2500")
    round_data: Dict[int, Dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "total_ms": 0.0}
    )
    for round_num, pname, method, situation, elapsed, _ in stats.call_log:
        round_data[round_num]["count"] += 1
        round_data[round_num]["total_ms"] += elapsed

    for r in sorted(round_data.keys()):
        d = round_data[r]
        avg = d["total_ms"] / d["count"] if d["count"] > 0 else 0
        bar = "\u2588" * min(int(d["total_ms"] / 100), 50)
        print(
            f"    \u8f6e\u6b21{r:>3}: {d['count']:>4}\u6b21\u8c03\u7528  "
            f"\u603b\u8ba1={d['total_ms']:>8.1f}ms  avg={avg:>6.2f}ms  {bar}"
        )


# ─────────────────────────────────────────────────────────────────────────────
#  入口
# ─────────────────────────────────────────────────────────────────────────────

def main(args):
    run_diagnosed_game(args)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="\u5355\u5c40\u81ea\u5bf9\u5f08\u8bca\u65ad")
    p.add_argument("--model", type=str, required=True, help="\u6a21\u578b\u8def\u5f84 (.zip)")
    p.add_argument("--opponents", type=int, default=5, help="\u5bf9\u624b\u6570\u91cf")
    p.add_argument(
        "--rl-opponents",
        type=int,
        default=None,
        help="RL \u5bf9\u624b\u6570\u91cf\uff08\u9ed8\u8ba4=\u5168\u90e8\u5bf9\u624b\uff09",
    )
    p.add_argument("--n-stack", type=int, default=30)
    p.add_argument("--enable-talents", action="store_true", default=True)
    p.add_argument("--no-talents", action="store_false", dest="enable_talents")
    p.add_argument("--seed", type=int, default=None)
    args = p.parse_args()

    if args.rl_opponents is None:
        args.rl_opponents = args.opponents

    main(args)
