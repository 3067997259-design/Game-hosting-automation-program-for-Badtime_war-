"""G5（往世的涟漪）实况聚合：win/loss 行为签名 + 锚定/诗篇/arc 通道使用。

用法: python tools/diag_g5_trace.py [--games N] [--seed S]

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

G5_NAME = "神代天赋-往世的涟漪"


def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=80)
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
        return {"games": 0, "wins": 0, "kills": 0, "damage": 0.0,
                "attacks": 0, "anchor_commit": 0, "anchor_material": 0,
                "ripple": 0, "crystal": 0, "double_closure": 0,
                "deaths": 0, "death_causes": defaultdict(int),
                "survived": 0}

    stats = blank()

    for i in range(args.games):
        _captured.clear()
        random.seed(args.seed + i)
        res = run_single_game(6, collect_digest=True)
        state = _captured[0]
        g5_pid = None
        for p in res["players"]:
            if TALENT_NUM_TO_NAME.get(p["talent_num"]) == G5_NAME:
                g5_pid = p["pid"]
                break
        if g5_pid is None:
            continue
        stats["games"] += 1
        for p in res["players"]:
            if p["pid"] != g5_pid:
                continue
            if p.get("is_winner"):
                stats["wins"] += 1
            stats["kills"] += int(p.get("kill_count", 0) or 0)
            stats["damage"] += float(p.get("damage_dealt", 0) or 0)
        for pid, counts in (res.get("player_event_counts", {}) or {}).items():
            if pid == g5_pid:
                stats["attacks"] += int(counts.get("attack", 0) or 0)
        g5_dead = False
        for e in state.event_log:
            if e.get("player") != g5_pid:
                continue
            t = e.get("type", "")
            if t == "anchor_script_committed":
                stats["anchor_commit"] += 1
            elif t == "MATERIALIZED_BY_ANCHOR":
                stats["anchor_material"] += 1
            elif t == "crystal_flower":
                stats["crystal"] += 1
            elif t == "g5_double_closure":
                stats["double_closure"] += 1
            elif t == "death":
                stats["deaths"] += 1
                stats["death_causes"][str(e.get("cause"))] += 1
                g5_dead = True
        if not g5_dead:
            stats["survived"] += 1

    g = max(1, stats["games"])
    print(f"\n===== G5 实况（{stats['games']} 局，seed {args.seed}）=====")
    print(f"胜率 {stats['wins']/g*100:.1f}%  场均击杀 {stats['kills']/g:.2f}  "
          f"场均伤害 {stats['damage']/g:.1f}  场均攻击 {stats['attacks']/g:.2f}")
    print(f"锚定提交 {stats['anchor_commit']/g:.2f}/局  锚定实体化 "
          f"{stats['anchor_material']/g:.2f}/局  水晶花 {stats['crystal']/g:.2f}/局  "
          f"双锚闭合 {stats['double_closure']/g:.2f}/局")
    print(f"死亡 {stats['deaths']}/局（原因 {dict(stats['death_causes'])}）  "
          f"生还局 {stats['survived']}（{stats['survived']/g*100:.0f}%）")


if __name__ == "__main__":
    _main()
