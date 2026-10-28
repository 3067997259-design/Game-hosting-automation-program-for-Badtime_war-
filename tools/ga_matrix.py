"""GA 固定矩阵：按人数 × seed 跑完整对局，只打印汇总，不写任何文件。

阵容按 seed 轮转全部十四个已注册槽位，控制器为最小合法策略；与 I1～I3 验收矩阵口径一致。
这是运行稳定性检查，不是 BasicAI 策略或平衡结论。任一局失败时退出码为 1。

用法：python -B tools/ga_matrix.py [--players 2,4,6] [--seeds 20] [--timeout 15]
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.game_state import GameState  # noqa: E402
from engine.ga.controllers import MinimalController  # noqa: E402
from engine.ga.runtime import run_game  # noqa: E402
from engine.ga.setup import TALENTS  # noqa: E402


def run_group(players: int, seeds: int, timeout: float) -> tuple[list[int], list[str]]:
    """跑一个人数分组，返回各局轮数与失败说明。"""
    slots = tuple(TALENTS)
    rounds: list[int] = []
    failures: list[str] = []
    for seed in range(seeds):
        lineup = [slots[(seed + index) % len(slots)] for index in range(players)]
        state = GameState.new_ga([f"p{index}" for index in range(players)], seed=seed,
                                 talents={f"p{index}": slot for index, slot in enumerate(lineup)})
        try:
            run_game(state, {actor: MinimalController() for actor in state.players},
                     timeout_seconds=timeout)
            rounds.append(state.current_round)
        except Exception:  # noqa: BLE001 - 汇总所有失败并保留最小复现信息
            last = traceback.format_exc().strip().splitlines()[-1]
            failures.append(f"players={players} seed={seed} lineup={','.join(lineup)}: {last}")
    return rounds, failures


def main() -> int:
    """解析参数、逐组运行并打印汇总。"""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--players", default="2,4,6", help="逗号分隔的人数，例如 2,4,6")
    parser.add_argument("--seeds", type=int, default=20, help="每个人数跑 seed 0..N-1")
    parser.add_argument("--timeout", type=float, default=15.0, help="每局合作式超时（秒）")
    args = parser.parse_args()

    all_failures: list[str] = []
    started = time.monotonic()
    for players in (int(item) for item in args.players.split(",")):
        rounds, failures = run_group(players, args.seeds, args.timeout)
        all_failures.extend(failures)
        summary = (f"{min(rounds)}~{max(rounds)} 轮，平均 {statistics.mean(rounds):.2f}"
                   if rounds else "无完成对局")
        print(f"{players} 人：通过 {len(rounds)}/{args.seeds}，{summary}", flush=True)
    print(f"总耗时 {time.monotonic() - started:.1f} 秒")
    for failure in all_failures:
        print("失败：" + failure)
    return 1 if all_failures else 0


if __name__ == "__main__":
    sys.exit(main())
