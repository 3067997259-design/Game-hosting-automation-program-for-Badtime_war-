"""G0 风洞行为追踪：打印每天赋为 G0 的玩家逐轮动作 + 天赋状态。

用法: python tools/diag_g0_trace.py [--games N] [--seed S]

只读：不修改任何游戏文件。
"""
import argparse
import random
import sys
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine import experiments


def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=4)
    parser.add_argument("--seed", type=int, default=7000)
    args = parser.parse_args()

    experiments.reset()
    experiments.set_profile("m9-rfc")

    from stats_runner import run_single_game, TALENT_NUM_TO_NAME
    from engine.round_manager import RoundManager

    _captured = []

    def _capture_loop(self):
        _captured.append(self.state)
        return _RoundManager_run_game_loop(self)

    _RoundManager_run_game_loop = RoundManager.run_game_loop
    RoundManager.run_game_loop = _capture_loop

    for i in range(args.games):
        _captured.clear()
        random.seed(args.seed + i)
        res = run_single_game(6, collect_digest=True)
        state_obj = _captured[0] if _captured else None
        g0_pids = [
            p["pid"] for p in res["players"]
            if TALENT_NUM_TO_NAME.get(p["talent_num"]) == "砂狼白子*Terror"
        ]
        print(f"\n===== game {i} (seed {args.seed + i}) G0 pids={g0_pids} "
              f"draw={res.get('draw_reason')} =====")
        for pid in g0_pids:
            player = state_obj.get_player(pid) if state_obj is not None else None
            t = getattr(player, "talent", None)
            if t is not None:
                drone = getattr(t, "drone", None)
                print(f"  G0[{pid}] hp={getattr(player, 'hp', None)}/"
                      f"{getattr(player, 'max_hp', None)} "
                      f"sp={state_obj.m9_system.get_sp(pid) if getattr(state_obj, 'm9_system', None) else '?'} "
                      f"drone={drone} "
                      f"breath_active={getattr(t, 'breath_active', None)} "
                      f"retreated={getattr(t, 'retreated', None)} "
                      f"relics={getattr(t, 'relics', None)} "
                      f"kills={getattr(player, 'kill_count', 0)} "
                      f"weapons={[w.name for w in getattr(player, 'weapons', []) if w]}")
        digest = res.get("event_digest", [])
        for line in digest:
            if any(f"player={pid}" in line or f"attacker={pid}" in line
                   for pid in g0_pids):
                print(f"  {line}")


if __name__ == "__main__":
    _main()
