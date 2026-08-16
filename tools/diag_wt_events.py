"""风洞诊断工具：天赋 × 事件频率聚合 + 平局死因链采样。

用法: python tools/diag_wt_events.py [--games N] [--seed S]

- 聚合每天赋：pick/胜场/胜率 + 关键天赋事件每局均值（超新星/天星/六爻/复活/
  影身/终曲/救世主/锚定/水晶花/热线/遗物/地点摧毁/繁育死亡）+ 攻击/死亡事件均值；
- 对 draw_reason == "all_dead_no_g7" 的局打印前 K 局的事件摘要尾部（死因链）；
- 顺带输出终分胜者在 all_dead 局中的归属（验证 m9 settle 是否照常给出胜者）。

只读：不修改任何游戏文件。
"""
import argparse
import random
import sys
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from collections import defaultdict

from engine import experiments


def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=200)
    parser.add_argument("--seed", type=int, default=4000)
    args = parser.parse_args()

    experiments.reset()
    experiments.set_profile("m9-rfc")

    from stats_runner import run_single_game, TALENT_NUM_TO_NAME

    talent_events = {
        "oneslash_attack": "T1",
        "star_attack": "T3",
        "hexagram_cast": "T4",
        "resurrection_mount": "T7",
        "resurrection_trigger": "T7",
        "hotline": "T6",
        "special_clue": "T6",
        "SHADOW_CREATED": "G2",
        "TERMINAL_SONG_COMMITTED": "G2",
        "g4_savior_enter": "G4",
        "g4_ember": "G4",
        "anchor_script_committed": "G5",
        "crystal_flower": "G5",
        "love_wish_granted": "G5",
        "location_destroyed": "G1",
        "g1_propagation_death": "G1",
        "attack": "ALL",
        "death": "ALL",
        "m9_trade": "ALL",
    }

    picks = defaultdict(int)
    wins = defaultdict(int)
    evt_sums = defaultdict(lambda: defaultdict(int))
    draw_reasons = defaultdict(int)
    all_dead_tails: list[list[str]] = []
    all_dead_winner_credited = 0
    all_dead_total = 0
    # 魂援/世界援助聚合（B4 §5.3）：aid_effect 事件按 talent= 字段计数
    aid_by_talent = defaultdict(int)
    aid_by_player_talent: dict[str, dict[str, int]] = {}
    world_poem_heals = 0

    for i in range(args.games):
        random.seed(args.seed + i)
        res = run_single_game(
            6, collect_digest=True)
        for p in res["players"]:
            if p.get("is_rl"):
                continue
            num = p["talent_num"]
            name = TALENT_NUM_TO_NAME.get(num, "无")
            picks[name] += 1
            if p["is_winner"]:
                wins[name] += 1
            counts = (res.get("player_event_counts", {}) or {}).get(
                p["pid"], {})
            for evt in talent_events:
                evt_sums[name][evt] += counts.get(evt, 0)
        for line in res.get("event_digest", []):
            if "aid_effect" in line:
                tal = None
                for part in line.split("|")[-1].split(","):
                    if part.startswith("talent="):
                        tal = part[len("talent="):]
                if tal:
                    aid_by_talent[tal] += 1
            if "world_poem_heal" in line:
                world_poem_heals += 1
        if res.get("draw") and res.get("draw_reason") == "all_dead_no_g7":
            all_dead_total += 1
            if res.get("winner_pid") and res["winner_pid"] != "nobody":
                all_dead_winner_credited += 1
            if len(all_dead_tails) < 5:
                all_dead_tails.append(res.get("event_digest", [])[-22:])
        draw_reasons[res.get("draw_reason", "")] += 1

    print(f"\n===== 天赋事件均值（{args.games} 局，m9-rfc）=====")
    print(f"{'天赋':16s} {'Pick':>5s} {'胜率':>8s} " +
          " ".join(f"{evt[:10]:>10s}" for evt in talent_events))
    for name in sorted(picks, key=lambda n: -(wins[n] / picks[n])):
        row = f"{name:16s} {picks[name]:>5d} {wins[name]/picks[name]*100:>7.1f}%"
        for evt in talent_events:
            row += f" {evt_sums[name][evt]/picks[name]:>10.2f}"
        print(row)

    print(f"\n===== 平局原因分布 =====")
    for k, v in sorted(draw_reasons.items(), key=lambda kv: -kv[1]):
        print(f"  {k or '(无)':24s} {v}")
    print(f"\n===== 魂援/援助（{args.games} 局）=====")
    total_aid = sum(aid_by_talent.values())
    print(f"  aid_effect 总次数: {total_aid}（{total_aid/args.games:.2f}/局）")
    for tal in sorted(aid_by_talent, key=lambda t: -aid_by_talent[t]):
        print(f"    {tal:8s} {aid_by_talent[tal]}")
    print(f"  world_poem_heal（世界援助治疗）: {world_poem_heals} "
          f"（{world_poem_heals/args.games:.2f}/局）")
    print(f"\n===== all_dead_no_g7 局终分胜者归属 =====")
    print(f"  {all_dead_winner_credited}/{all_dead_total} 局有终分胜者")
    for idx, tail in enumerate(all_dead_tails):
        print(f"\n--- all_dead 样本 {idx+1} 事件尾部 ---")
        for line in tail:
            print(f"  {line}")


if __name__ == "__main__":
    _main()
