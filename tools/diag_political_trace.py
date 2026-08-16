"""political 人格行为签名：与全场对比的行为聚合（深挖用）。

用法: python tools/diag_political_trace.py [--games N] [--seed S]

对每个 political 玩家（及全场对照）聚合：
- 胜率 / 存活轮 / 击杀 / 伤害；
- 事件日志行为计数：attack（主动攻击）、被攻击、interact、move 等；
- 结论输出 political 与全场的每存活轮攻击率对比。

只读：不修改任何游戏文件。
"""
import argparse
import random
import sys
import os
from collections import defaultdict

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine import experiments


def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=30)
    parser.add_argument("--seed", type=int, default=3000)
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

    def blank():
        return {"games": 0, "attacks": 0, "attacked": 0, "interacts": 0,
                "moves": 0, "special": 0, "rounds": 0, "wins": 0, "kills": 0,
                "damage": 0.0, "captain": 0}

    pol = blank()
    field = blank()

    for i in range(args.games):
        _captured.clear()
        random.seed(args.seed + i)
        res = run_single_game(6, collect_digest=True)
        state = _captured[0]
        polit = [p["pid"] for p in res["players"]
                 if p.get("personality") == "political"]
        if not polit:
            continue
        for p in res["players"]:
            pid = p["pid"]
            bucket = pol if pid in polit else field
            bucket["games"] += 1
            if p.get("is_winner"):
                bucket["wins"] += 1
            bucket["kills"] += int(p.get("kill_count", 0) or 0)
            bucket["damage"] += float(p.get("damage_dealt", 0) or 0)
        for pid, counts in (res.get("player_event_counts", {}) or {}).items():
            bucket = pol if pid in polit else field
            bucket["attacks"] += int(counts.get("attack", 0) or 0)
            bucket["interacts"] += int(counts.get("interact", 0) or 0)
            bucket["moves"] += int(counts.get("move", 0) or 0)
            bucket["special"] += int(counts.get("special", 0) or 0)
        # 被攻击：事件日志 target 侧计数
        for e in state.event_log:
            if e.get("type") != "attack":
                continue
            tgt = e.get("target")
            if tgt in polit:
                pol["attacked"] += 1
            elif tgt:
                field["attacked"] += 1
        # 存活轮：死亡轮或终局轮
        for pid in polit:
            p = state.get_player(pid)
            death_r = int(getattr(p, "death_round", 0) or 0)
            pol["rounds"] += death_r if death_r else res.get("rounds", 0)
            if getattr(p, "is_captain", False):
                pol["captain"] += 1
        for pid in state.player_order:
            if pid in polit:
                continue
            p = state.get_player(pid)
            death_r = int(getattr(p, "death_round", 0) or 0)
            field["rounds"] += death_r if death_r else res.get("rounds", 0)

    def show(label, b):
        g = max(1, b["games"])
        print(f"{label}: 局数 {b['games']}  胜率 {b['wins']/g*100:.1f}%  "
              f"场均击杀 {b['kills']/g:.2f}  场均伤害 {b['damage']/g:.1f}  "
              f"场均攻击 {b['attacks']/g:.2f}  被攻击 {b['attacked']/g:.2f}  "
              f"interact {b['interacts']/g:.2f}  move {b['moves']/g:.2f}  "
              f"special {b['special']/g:.2f}  每存活轮攻击 "
              f"{b['attacks']/max(1,b['rounds']):.3f}")

    print(f"\n===== political 行为签名（{args.games} 局，seed {args.seed}）=====")
    show("political", pol)
    show("全场对照", field)
    if pol["games"] > 0:
        print(f"\npolitical 每存活轮攻击率 / 全场 = "
              f"{(pol['attacks']/max(1,pol['rounds'])) / max(0.001, field['attacks']/max(1,field['rounds'])):.2f}×")
        print(f"political 当上队长次数: {pol['captain']}")


if __name__ == "__main__":
    _main()
