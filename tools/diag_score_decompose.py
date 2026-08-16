"""终分成分分解：原始榜 vs 纯净榜（剥离援助/投注成分）——援助系统测量卫生工具。

用法: python tools/diag_score_decompose.py [--games N] [--seed S]

每局对每个玩家分解：
  剧情分=arc×arc_weight；战果分=击杀×kill_weight+伤害×damage_weight；
  生者系数 1.5 / 撤退 0.5；PP=台账余额；援助收益=死者 aid_earnings；
  投注派彩=显示终分−base。
  纯净分 = base − aid_earnings（死者）；生者 base 不变。
  原始榜 = 按显示终分；纯净榜 = 按纯净分（并列按 player_order 取首个）。

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
    parser.add_argument("--games", type=int, default=100)
    parser.add_argument("--seed", type=int, default=3000)
    args = parser.parse_args()

    experiments.reset()
    experiments.set_profile("m9-rfc")

    from collections import defaultdict
    from stats_runner import run_single_game, TALENT_NUM_TO_NAME
    from engine.round_manager import RoundManager

    _captured = []

    def _capture_loop(self):
        _captured.append(self.state)
        return _RoundManager_run_game_loop(self)

    _RoundManager_run_game_loop = RoundManager.run_game_loop
    RoundManager.run_game_loop = _capture_loop

    agg = defaultdict(lambda: defaultdict(float))
    raw_wins = defaultdict(int)
    pure_wins = defaultdict(int)
    picks = defaultdict(int)

    for i in range(args.games):
        _captured.clear()
        random.seed(args.seed + i)
        res = run_single_game(6, collect_digest=True)
        state = _captured[0]
        scoring = getattr(state, "m9_scoring", None)
        pp = getattr(state, "m9_pp", None)
        if scoring is None or pp is None:
            continue
        alive_ids = {pid for pid in state.player_order
                     if state.get_player(pid).is_alive()}
        dead_ids = [pid for pid in state.player_order
                    if pid not in alive_ids]
        settled = scoring.settle(list(alive_ids), dead_ids, state)
        rows = {}
        for p in res["players"]:
            pid = p["pid"]
            fs = settled.get(pid)
            if fs is None:
                continue
            base = fs.base_final_score
            display = fs.display_final_score
            payout = display - base
            aid_earn = float(pp.aid_earnings(pid))
            rows[pid] = {
                "base": base, "display": display, "payout": payout,
                "aid": aid_earn, "pure": base - aid_earn,
            }
            name = TALENT_NUM_TO_NAME.get(p["talent_num"], "无")
            picks[name] += 1
            a = agg[name]
            a["base"] += base
            a["display"] += display
            a["payout"] += payout
            a["aid"] += aid_earn
        # 原始榜（显示终分）
        order = {pid: k for k, pid in enumerate(state.player_order)}
        raw_winner = max(rows, key=lambda pid: (rows[pid]["display"],
                                                -order[pid]))
        pure_winner = max(rows, key=lambda pid: (rows[pid]["pure"],
                                                 -order[pid]))
        for p in res["players"]:
            name = TALENT_NUM_TO_NAME.get(p["talent_num"], "无")
            if p["pid"] == raw_winner:
                raw_wins[name] += 1
            if p["pid"] == pure_winner:
                pure_wins[name] += 1

    print(f"\n===== 终分成分分解（{args.games} 局，m9-rfc）=====")
    print(f"{'天赋':14s} {'Pick':>4s} {'原始胜':>7s} {'纯净胜':>7s} "
          f"{'平均显示分':>9s} {'平均base':>9s} {'援助收益':>8s} {'投注派彩':>8s}")
    for name in sorted(picks, key=lambda n: -(raw_wins[n] / picks[n])):
        a = agg[name]
        n = picks[name]
        print(f"{name:14s} {n:4d} {raw_wins[name]/n*100:6.1f}% "
              f"{pure_wins[name]/n*100:6.1f}% "
              f"{a['display']/n:9.2f} {a['base']/n:9.2f} "
              f"{a['aid']/n:8.2f} {a['payout']/n:8.2f}")


if __name__ == "__main__":
    _main()
