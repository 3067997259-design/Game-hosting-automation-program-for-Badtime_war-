"""解剖 G1（火萤·完全燃烧）获胜引擎：对手压制行为 / 击杀来源 / 灼烧与繁育。

用法: python tools/diag_g1_trace.py [--games N] [--seed S]

每局含 G1 的局按事件日志聚合：
- 对手对 G1 的攻击次数（按 G1 存活轮归一）→ 检测"回避 G1"还是"打不动"；
- G1 击杀来源（普通攻击 result.killed / 超新星 kills / 灼烧 death cause=burn）；
- 完全燃烧期自愈（g1_fullburn_lifesteal）、繁育（g1_propagation_death）、
  超新星（firefly_supernova）次数；
- G1 胜/负局分组对比。

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

G1_NAME = "神代天赋-火萤IV型-完全燃烧"


def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=120)
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

    def agg_blank():
        return {
            "games": 0,
            "attacks_on_g1": 0,       # 对手攻击 G1 次数
            "g1_alive_rounds": 0,     # G1 存活轮数（归一基数）
            "attacks_on_others": 0,   # 对照组：对手攻击其他存活玩家的次数
            "others_alive_rounds": 0,  # 对照组存活轮数（全部非 G1 玩家之和）
            "attacks_in_ls_rounds": 0,  # 完全燃烧活跃轮（吸血轮）里对手攻击 G1 次数
            "ls_rounds": 0,           # 有吸血事件的轮数（完全燃烧活跃代理）
            "g1_attacks": 0,          # G1 攻击次数
            "g1_weapon_kills": 0,     # G1 普通攻击击杀
            "g1_supernova": 0,        # 超新星次数
            "g1_supernova_kills": 0,
            "g1_lifesteal": 0,        # 吸血事件数
            "g1_lifesteal_hp": 0,
            "g1_propagation": 0,      # 繁育触发次数
            "burn_deaths": 0,         # 全场灼烧致死（cause=burn）
            "g1_deaths": 0,
            "g1_death_causes": defaultdict(int),
            "rounds": 0,
        }

    wins = agg_blank()
    losses = agg_blank()

    for i in range(args.games):
        _captured.clear()
        random.seed(args.seed + i)
        res = run_single_game(6, collect_digest=True)
        state = _captured[0]
        slot_of = {}
        g1_pid = None
        for p in res["players"]:
            name = TALENT_NUM_TO_NAME.get(p["talent_num"], "无")
            slot_of[p["pid"]] = name
            if name == G1_NAME:
                g1_pid = p["pid"]
        if g1_pid is None:
            continue

        g1_win = slot_of.get(res.get("winner_pid", "")) == G1_NAME
        bucket = wins if g1_win else losses
        bucket["games"] += 1
        bucket["rounds"] += res.get("rounds", 0)

        ls_rounds = set()
        g1_dead_round = None
        others_alive_rounds = 0
        for pid in state.player_order:
            if pid == g1_pid:
                continue
            p = state.get_player(pid)
            if p is None:
                continue
            death_r = int(getattr(p, "death_round", 0) or 0)
            others_alive_rounds += (death_r
                                    if death_r else res.get("rounds", 0))
        bucket["others_alive_rounds"] += others_alive_rounds
        for e in state.event_log:
            rnd = int(e.get("round", 0) or 0)
            typ = e.get("type", "")
            if typ == "attack":
                if e.get("attacker") == g1_pid:
                    bucket["g1_attacks"] += 1
                    r = e.get("result") or {}
                    if r.get("killed"):
                        bucket["g1_weapon_kills"] += 1
                elif e.get("target") == g1_pid:
                    bucket["attacks_on_g1"] += 1
                    if rnd in ls_rounds:
                        bucket["attacks_in_ls_rounds"] += 1
                else:
                    # 对照组：非 G1 之间的攻击（含 G1 攻击他人不计入）
                    bucket["attacks_on_others"] += 1
            elif typ == "g1_fullburn_lifesteal" and e.get("player") == g1_pid:
                bucket["g1_lifesteal"] += 1
                bucket["g1_lifesteal_hp"] += int(e.get("heal", 0) or 0)
                ls_rounds.add(rnd)
            elif typ == "g1_propagation_death" and e.get("player") == g1_pid:
                bucket["g1_propagation"] += 1
            elif typ == "firefly_supernova" and e.get("player") == g1_pid:
                bucket["g1_supernova"] += 1
                bucket["g1_supernova_kills"] += int(e.get("kills", 0) or 0)
            elif typ == "death":
                if e.get("cause") == "burn":
                    bucket["burn_deaths"] += 1
                if e.get("player") == g1_pid:
                    bucket["g1_deaths"] += 1
                    bucket["g1_death_causes"][str(e.get("cause"))] += 1
                    g1_dead_round = rnd
        bucket["ls_rounds"] += len(ls_rounds)
        # G1 存活轮数（死亡轮之前；生还则全局长）
        bucket["g1_alive_rounds"] += (g1_dead_round
                                      if g1_dead_round is not None
                                      else res.get("rounds", 0))

    def rate(b):
        return b["attacks_on_g1"] / max(1, b["g1_alive_rounds"])

    def rate_others(b):
        return b["attacks_on_others"] / max(1, b["others_alive_rounds"])

    print(f"\n===== G1 获胜引擎解剖（{args.games} 局，seed {args.seed}）=====")
    for label, b in (("G1 胜局", wins), ("G1 负局", losses)):
        g = b["games"]
        if g == 0:
            print(f"{label}: 无样本")
            continue
        print(f"\n── {label}（{g} 局）──")
        print(f"  对手攻击 G1: {rate(b):.2f} 次/存活轮"
              f"（共 {b['attacks_on_g1']} 次 / {b['g1_alive_rounds']} 存活轮）")
        print(f"  [对照] 非 G1 互攻: {rate_others(b):.2f} 次/存活轮"
              f"（共 {b['attacks_on_others']} 次 / {b['others_alive_rounds']} 存活轮）"
              f"——G1 被攻比率 {rate(b) / max(0.001, rate_others(b)):.2f}×")
        print(f"  完全燃烧活跃轮: {b['ls_rounds']}，其中被攻击 {b['attacks_in_ls_rounds']} 次")
        print(f"  G1 攻击 {b['g1_attacks']} 次（击杀 {b['g1_weapon_kills']}）; "
              f"超新星 {b['g1_supernova']} 次（击杀 {b['g1_supernova_kills']}）")
        print(f"  自愈 {b['g1_lifesteal']} 次 / {b['g1_lifesteal_hp']} HP; "
              f"繁育触发 {b['g1_propagation']} 次")
        print(f"  全场灼烧致死 {b['burn_deaths']} 人; G1 死亡 {b['g1_deaths']} 次 "
              f"（原因: {dict(b['g1_death_causes'])}）")
        print(f"  平均轮次 {b['rounds'] / g:.1f}")


if __name__ == "__main__":
    _main()
