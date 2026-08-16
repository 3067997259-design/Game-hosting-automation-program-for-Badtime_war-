"""解剖 G0 获胜局：终局状态/分数构成/治疗使用分布。

用法: python tools/diag_g0_win.py [--games N] [--seed S]
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
    parser.add_argument("--games", type=int, default=40)
    parser.add_argument("--seed", type=int, default=9000)
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

    g0_wins = 0
    g0_total = 0
    heal_by_slot: dict = {}
    wins_by_talent: dict = {}

    for i in range(args.games):
        _captured.clear()
        random.seed(args.seed + i)
        res = run_single_game(6, collect_digest=True)
        state = _captured[0]
        slot_of = {}
        for p in res["players"]:
            name = TALENT_NUM_TO_NAME.get(p["talent_num"], "无")
            slot_of[p["pid"]] = name
            if p["is_winner"]:
                wins_by_talent[name] = wins_by_talent.get(name, 0) + 1
            if name == "砂狼白子*Terror":
                g0_total += 1
                if p["is_winner"]:
                    g0_wins += 1
        for pid in slot_of:
            counts = (res.get("player_event_counts", {}) or {}).get(pid, {})
            # interact 事件没有 item 细节；用 event_digest 找 治疗
        for line in res.get("event_digest", []):
            if "interact|item=治疗" not in line:
                continue
            pid = None
            for part in line.split("|")[-1].split(","):
                if part.startswith("player="):
                    pid = part[len("player="):]
            if pid:
                slot = slot_of.get(pid, "?")
                heal_by_slot[slot] = heal_by_slot.get(slot, 0) + 1
        # 打印 G0 获胜局的终局解剖
        if slot_of.get(res.get("winner_pid", "")) == "砂狼白子*Terror":
            print(f"===== game {i} (seed {args.seed+i}) G0 WIN, "
                  f"rounds={res['rounds']} =====")
            alive = [pid for pid in state.player_order
                     if state.get_player(pid).is_alive()]
            print(f"  存活: {alive}")
            for p in res["players"]:
                sc = res["final_scores"].get(p["pid"], 0)
                print(f"    {slot_of[p['pid']]:10s} {p['pid']} alive={p['alive']} "
                      f"kills={p['kill_count']} score={sc:.1f}")

    print(f"\n===== {args.games} 局汇总 =====")
    print(f"G0 胜率: {g0_wins}/{g0_total}")
    print("胜场分布:", dict(sorted(wins_by_talent.items(), key=lambda kv: -kv[1])))
    print("治疗使用（interact 治疗）按天赋:", dict(sorted(
        heal_by_slot.items(), key=lambda kv: -kv[1])))


if __name__ == "__main__":
    _main()
