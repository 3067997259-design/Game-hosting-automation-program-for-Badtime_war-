"""Diagnose G2 (神代天赋-请一直注视着我, Hologram9) win-rate collapse under
T3 starfall_damage 2 vs 3.

Usage: python tools/diag_g2_starfall.py [--games N] [--seed S] [--dump K]

For every game containing a G2 player, aggregate (win vs loss buckets):
- G2 light-body death cause / rounds alive / attacks received (by whom);
- SHADOW_CREATED / SHADOW_DISSIPATED (+reason);
- shadow attacks made (incl. kills), attacks received (by whom);
- petrify lifecycle on G2 light body and shadow (apply/shake/break), via
  monkeypatched PetrifyRegistry;
- t3_starfall damage hits on G2 / shadow (via monkeypatched m9 resolve_damage).

--dump K prints a full per-game event/damage trace for the first K G2 games so
the vocabulary and qualitative moments can be read directly.

Read-only: does not modify any game file.
"""
import argparse
import random
import sys
import os
from collections import defaultdict, Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from engine import experiments

G2_NAME = "神代天赋-请一直注视着我"
SHADOW_PREFIX = "G2:shadow@"


def _actor_id(actor) -> str:
    return str(getattr(actor, "player_id",
                       getattr(actor, "unit_id", "")))


def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=150)
    parser.add_argument("--seed", type=int, default=3000)
    parser.add_argument("--dump", type=int, default=0,
                        help="dump full detail for the first K G2 games")
    parser.add_argument("--starfall", type=int, default=None,
                        help="runtime override of m9_talents_extended.t3.starfall_damage")
    args = parser.parse_args()

    experiments.reset()
    experiments.set_profile("m9-rfc")

    if args.starfall is not None:
        import engine.balance as _bal
        _data = _bal._load()
        _data.setdefault("m9_talents_extended", {}).setdefault(
            "t3", {})["starfall_damage"] = args.starfall

    from stats_runner import (
        run_single_game, _silence_display, _silence_prompt_manager,
        _restore_display, _restore_prompt_manager,
    )
    from engine.round_manager import RoundManager
    from engine.m9 import combat as m9_combat
    from engine.m9.petrify import PetrifyRegistry

    # ── instrumentation ──
    _captured = []
    _real_run_game_loop = RoundManager.run_game_loop

    def _capture_loop(self):
        _captured.append(self.state)
        return _real_run_game_loop(self)

    RoundManager.run_game_loop = _capture_loop

    _dmg_log = []
    _seq = [0]
    _real_resolve = m9_combat.resolve_damage

    def _wrap_resolve(attacker, target, weapon, game_state, *a, **kw):
        _seq[0] += 1
        seq = _seq[0]
        tgt = _actor_id(target) if target is not None else None
        atk = _actor_id(attacker) if attacker is not None else None
        petrify = getattr(game_state, "m9_petrify", None)
        was_petrified = bool(petrify and petrify.is_petrified(tgt))
        shake_before = petrify.shake_count(tgt) if petrify else 0
        rnd = getattr(game_state, "current_round", 0)
        try:
            r = _real_resolve(attacker, target, weapon, game_state, *a, **kw)
        except Exception:
            raise
        shake_after = petrify.shake_count(tgt) if petrify else 0
        petrified_after = bool(petrify and petrify.is_petrified(tgt))
        _dmg_log.append({
            "seq": seq, "round": rnd, "attacker": atk, "target": tgt,
            "source_kind": kw.get("source_kind"),
            "was_petrified": was_petrified, "petrified_after": petrified_after,
            "shake_before": shake_before, "shake_after": shake_after,
            "hp_damage": r.get("hp_damage", 0) if isinstance(r, dict) else 0,
            "final_damage": r.get("final_damage", 0) if isinstance(r, dict) else 0,
            "killed": r.get("killed", False) if isinstance(r, dict) else False,
            "m9_kind": r.get("m9_kind", "") if isinstance(r, dict) else "",
        })
        return r

    m9_combat.resolve_damage = _wrap_resolve

    _petrify_log = []
    _real_apply = PetrifyRegistry.apply
    _real_remove = PetrifyRegistry.remove
    _real_eff = PetrifyRegistry.on_effective_hit

    def _wrap_apply(self, game_state, actor, *a, **kw):
        aid = _actor_id(actor)
        _petrify_log.append({
            "round": getattr(game_state, "current_round", 0), "actor": aid,
            "kind": "apply", "source": kw.get("source_pid"),
            "locked": kw.get("locked", False)})
        return _real_apply(self, game_state, actor, *a, **kw)

    def _wrap_remove(self, game_state, actor, *a, **kw):
        aid = _actor_id(actor)
        _petrify_log.append({
            "round": getattr(game_state, "current_round", 0), "actor": aid,
            "kind": "remove"})
        return _real_remove(self, game_state, actor, *a, **kw)

    def _wrap_eff(self, game_state, actor, *a, **kw):
        aid = _actor_id(actor)
        _petrify_log.append({
            "round": getattr(game_state, "current_round", 0), "actor": aid,
            "kind": "shake", "before": self.shake_count(aid)})
        return _real_eff(self, game_state, actor, *a, **kw)

    PetrifyRegistry.apply = _wrap_apply
    PetrifyRegistry.remove = _wrap_remove
    PetrifyRegistry.on_effective_hit = _wrap_eff

    _silence_display()
    _silence_prompt_manager()

    def blank():
        return {
            "games": 0, "rounds": 0, "wins": 0,
            "g2_alive_rounds": 0,
            "g2_deaths": 0, "g2_death_causes": Counter(),
            "g2_killers": Counter(),
            "attacks_on_g2": Counter(), "dmg_on_g2": 0.0,
            "starfall_hits_g2": 0, "starfall_dmg_g2": 0.0,
            "petrify_g2": 0, "shake_g2": 0, "break_g2": 0,
            "shadow_created": 0, "shadow_dissipated": 0,
            "shadow_dissipate_reasons": Counter(),
            "shadow_attacks": 0, "shadow_kills": 0,
            "shadow_attack_petrified": 0, "shadow_broke_petrify": 0,
            "attacks_on_shadow": Counter(), "dmg_on_shadow": 0.0,
            "starfall_hits_shadow": 0, "starfall_dmg_shadow": 0.0,
            "petrify_shadow": 0, "shake_shadow": 0, "break_shadow": 0,
            "star_attacks_total": 0, "t3_present": 0,
            "final_score": 0.0, "arc_count": 0, "damage_dealt": 0.0,
            "kill_count": 0, "death_round": 0, "placement_rank": 0,
            "terminal_committed": 0, "terminal_heard": 0, "terminal_ended": 0,
            "crime_g2": 0, "suspicion_g2": 0, "wanted_g2": 0,
            "police_attacks_g2": 0, "police_alive_end": 0.0,
            "winner_talent": Counter(), "winner_personality": Counter(),
            "shadow_forfeits": 0, "shadow_action_turns": 0,
        }

    wins = blank()
    losses = blank()

    def add_pair(b, k, v=1):
        b[k] += v

    # vocabulary dump accumulators
    vocab = Counter()
    g2_games = 0

    for i in range(args.games):
        _captured.clear()
        _dmg_log.clear()
        _petrify_log.clear()
        _seq[0] = 0
        random.seed(args.seed + i)
        try:
            res = run_single_game(6, collect_digest=True)
        except Exception as e:
            print(f"[game {i}] exception: {e}")
            continue
        if not _captured:
            continue
        state = _captured[0]

        g2_pid = None
        g2_entry = None
        for p in res.get("players", []):
            vocab.update([p.get("talent_name", "")])
            if p.get("talent_name") == G2_NAME:
                g2_pid = p["pid"]
                g2_entry = p
        if g2_pid is None:
            continue
        g2_games += 1

        # learn event vocabulary across ALL games (cheap)
        for e in state.event_log:
            vocab[f"ev:{e.get('type','')}"] += 1

        g2_win = bool(g2_entry.get("is_winner"))
        bucket = wins if g2_win else losses
        bucket["games"] += 1
        bucket["rounds"] += res.get("rounds", 0)
        if g2_win:
            bucket["wins"] += 1

        shadow_id = f"{SHADOW_PREFIX}{g2_pid}"

        # labels for attackers
        def label(aid):
            if not aid:
                return "none"
            if aid == g2_pid:
                return "G2光身"
            if aid == shadow_id:
                return "G2影身"
            a = state.get_player(aid)
            if a is not None:
                tn = getattr(a, "talent_name", None) or \
                    (getattr(a.talent, "name", "") if a.talent else "")
                return tn or f"无天赋({aid})"
            if str(aid).startswith("police:"):
                return "警察"
            return str(aid)

        # T3 present in this game?
        t3_present = any(
            state.get_player(pid) is not None
            and getattr(state.get_player(pid).talent, "name", "") == "天星"
            for pid in state.player_order)
        if t3_present:
            bucket["t3_present"] += 1

        # death events
        g2_dead_round = None
        for e in state.event_log:
            if e.get("type") != "death":
                continue
            if e.get("player") == g2_pid:
                bucket["g2_deaths"] += 1
                bucket["g2_death_causes"][str(e.get("cause"))] += 1
                bucket["g2_killers"][label(e.get("killer"))] += 1
                g2_dead_round = int(e.get("round", 0) or 0)
        bucket["g2_alive_rounds"] += (
            g2_dead_round if g2_dead_round else res.get("rounds", 0))

        # shadow lifecycle
        for e in state.event_log:
            if e.get("type") == "SHADOW_CREATED" and e.get("player") == g2_pid:
                bucket["shadow_created"] += 1
            elif e.get("type") == "SHADOW_DISSIPATED" and e.get("player") == g2_pid:
                bucket["shadow_dissipated"] += 1
                bucket["shadow_dissipate_reasons"][str(e.get("reason"))] += 1
            elif e.get("type") == "star_attack":
                bucket["star_attacks_total"] += 1

        # attacks (event_log) on G2 / shadow + shadow attacks
        for e in state.event_log:
            if e.get("type") != "attack":
                continue
            attacker = e.get("attacker")
            target = e.get("target")
            r = e.get("result") or {}
            if target == g2_pid:
                bucket["attacks_on_g2"][label(attacker)] += 1
                bucket["dmg_on_g2"] += float(r.get("hp_damage", 0) or 0)
            elif target == shadow_id:
                bucket["attacks_on_shadow"][label(attacker)] += 1
                bucket["dmg_on_shadow"] += float(r.get("hp_damage", 0) or 0)
            if attacker == shadow_id:
                bucket["shadow_attacks"] += 1
                if r.get("killed"):
                    bucket["shadow_kills"] += 1

        # resolve_damage log: starfall hits + shadow-on-petrified + breaks
        for d in _dmg_log:
            sk = d.get("source_kind")
            if sk == "t3_starfall":
                if d["target"] == g2_pid:
                    bucket["starfall_hits_g2"] += 1
                    bucket["starfall_dmg_g2"] += float(d["hp_damage"])
                elif d["target"] == shadow_id:
                    bucket["starfall_hits_shadow"] += 1
                    bucket["starfall_dmg_shadow"] += float(d["hp_damage"])
            if d["attacker"] == shadow_id and d["target"] not in (g2_pid, shadow_id):
                if d["was_petrified"]:
                    bucket["shadow_attack_petrified"] += 1
                if d["was_petrified"] and not d["petrified_after"] \
                        and sk != "t3_starfall":
                    bucket["shadow_broke_petrify"] += 1

        # petrify lifecycle for g2 / shadow
        for p in _petrify_log:
            if p["actor"] == g2_pid:
                if p["kind"] == "apply":
                    bucket["petrify_g2"] += 1
                elif p["kind"] == "shake":
                    bucket["shake_g2"] += 1
                    if p["before"] >= 1:
                        bucket["break_g2"] += 1
            elif p["actor"] == shadow_id:
                if p["kind"] == "apply":
                    bucket["petrify_shadow"] += 1
                elif p["kind"] == "shake":
                    bucket["shake_shadow"] += 1
                    if p["before"] >= 1:
                        bucket["break_shadow"] += 1

        # ── scoring / terminal / police / crime signals ──
        g2_player = state.get_player(g2_pid)
        bucket["final_score"] += float(res.get("final_scores", {}).get(
            g2_pid, 0) or 0)
        bucket["damage_dealt"] += float(getattr(g2_player, "damage_dealt", 0) or 0)
        bucket["kill_count"] += int(getattr(g2_player, "kill_count", 0) or 0)
        if g2_dead_round:
            bucket["death_round"] += g2_dead_round
        scoring = getattr(state, "m9_scoring", None)
        if scoring is not None:
            bucket["arc_count"] += int(scoring.arc_count(g2_pid))
            bucket["placement_rank"] += int(
                getattr(scoring, "_placement_rank", {}).get(g2_pid, 0) or 0)

        sh = state.m9_shadows.get(shadow_id)
        if sh is not None:
            bucket["shadow_action_turns"] += int(
                getattr(sh, "total_action_turns", 0) or 0)

        for e in state.event_log:
            t = e.get("type")
            if t == "TERMINAL_SONG_COMMITTED" and e.get("player") == g2_pid:
                bucket["terminal_committed"] += 1
            elif t == "g2_last_song_heard" and e.get("player") == g2_pid:
                bucket["terminal_heard"] += 1
            elif t == "TERMINAL_SONG_ENDED" and e.get("player") == g2_pid:
                bucket["terminal_ended"] += 1
            elif t == "crime" and (e.get("player") in (g2_pid, shadow_id)):
                bucket["crime_g2"] += 1
            elif t == "suspicion" and e.get("player") in (g2_pid, shadow_id):
                bucket["suspicion_g2"] += 1
            elif t == "wanted" and e.get("player") in (g2_pid, shadow_id):
                bucket["wanted_g2"] += 1
            elif t == "attack" and e.get("target") == g2_pid \
                    and str(e.get("attacker", "")).startswith("police:"):
                bucket["police_attacks_g2"] += 1

        m9_police = getattr(state, "m9_police", None)
        if m9_police is not None:
            roster = getattr(m9_police, "_roster", None) or \
                getattr(m9_police, "roster", []) or []
            bucket["police_alive_end"] += sum(
                1 for u in roster if getattr(u, "alive", True)
                and getattr(u, "hp", 0) > 0)

        winner_pid = res.get("winner_pid")
        if winner_pid and winner_pid != "nobody":
            wp = state.get_player(winner_pid)
            if wp is not None:
                bucket["winner_talent"][getattr(wp, "talent_name", "无")] += 1
                bucket["winner_personality"][
                    getattr(getattr(wp, "controller", None),
                            "personality", "unknown")] += 1

        if args.dump and g2_games <= args.dump:
            _dump_game(i, res, state, g2_pid, shadow_id, _dmg_log, _petrify_log)

    _restore_display()
    _restore_prompt_manager()

    # ── print vocabulary (distinct event types + talent names) ──
    print(f"\n===== 事件/天赋词汇表（{args.games} 局，seed {args.seed}）=====")
    for k, v in sorted(vocab.items(), key=lambda kv: -kv[1]):
        print(f"  {k:40s} {v}")

    print(f"\n===== G2 引擎解剖（{args.games} 局，含 G2 的局 {g2_games}，seed {args.seed}）=====")
    for label, b in (("G2 胜局", wins), ("G2 负局", losses)):
        g = b["games"]
        if g == 0:
            print(f"\n── {label}: 无样本 ──")
            continue
        print(f"\n── {label}（{g} 局）──")
        print(f"  平均轮次 {b['rounds']/g:.1f}")
        print(f"  T3 在局率 {b['t3_present']}/{g}")
        print(f"  全场天星 star_attack 次数/局 {b['star_attacks_total']/g:.2f}")
        print(f"  ─ 光身 ─")
        print(f"   存活轮 {b['g2_alive_rounds']/g:.1f}; 死亡 {b['g2_deaths']} 次")
        print(f"   死因: {dict(b['g2_death_causes'])}")
        print(f"   击杀者: {dict(b['g2_killers'])}")
        print(f"   被攻击 {sum(b['attacks_on_g2'].values())} 次（每存活轮 "
              f"{sum(b['attacks_on_g2'].values())/max(1,b['g2_alive_rounds']):.3f}）; "
              f"累计伤害 {b['dmg_on_g2']/g:.1f}/局")
        print(f"   攻击者分布: {dict(b['attacks_on_g2'])}")
        print(f"   天星命中光身 {b['starfall_hits_g2']/g:.2f}/局 "
              f"({b['starfall_dmg_g2']/g:.1f} dmg/局)")
        print(f"   石化施加 {b['petrify_g2']/g:.2f}/局; 摇晃 {b['shake_g2']/g:.2f}; "
              f"解除 {b['break_g2']/g:.2f}")
        print(f"  ─ 影身 ─")
        print(f"   创建 {b['shadow_created']/g:.2f}/局; 消散 {b['shadow_dissipated']/g:.2f}/局")
        print(f"   消散原因: {dict(b['shadow_dissipate_reasons'])}")
        print(f"   影身攻击 {b['shadow_attacks']/g:.2f}/局（击杀 {b['shadow_kills']/g:.2f}）")
        print(f"   影身攻击石化目标 {b['shadow_attack_petrified']/g:.2f}/局; "
              f"影身攻击解除石化 {b['shadow_broke_petrify']/g:.2f}/局")
        print(f"   影身被攻击 {sum(b['attacks_on_shadow'].values())} 次; "
              f"累计伤害 {b['dmg_on_shadow']/g:.1f}/局")
        print(f"   影身被攻击者: {dict(b['attacks_on_shadow'])}")
        print(f"   天星命中影身 {b['starfall_hits_shadow']/g:.2f}/局 "
              f"({b['starfall_dmg_shadow']/g:.1f} dmg/局)")
        print(f"   影身石化施加 {b['petrify_shadow']/g:.2f}/局; 摇晃 "
              f"{b['shake_shadow']/g:.2f}; 解除 {b['break_shadow']/g:.2f}")
        print(f"   影身动作轮 {b['shadow_action_turns']/g:.1f}/局")
        print(f"  ─ 评分/终曲/警察 ─")
        print(f"   终分 {b['final_score']/g:.2f}; arc {b['arc_count']/g:.2f}; "
              f"击杀 {b['kill_count']/g:.2f}; 伤害 {b['damage_dealt']/g:.1f}; "
              f"名次 {b['placement_rank']/g:.2f}（1=最晚出局）")
        print(f"   死亡轮 {b['death_round']/max(1,b['g2_deaths']):.1f}")
        print(f"   终曲承诺 {b['terminal_committed']/g:.2f}/局; 被听见 "
              f"{b['terminal_heard']/g:.2f}/局; 结束 {b['terminal_ended']/g:.2f}/局")
        print(f"   犯罪 {b['crime_g2']/g:.2f}/局; 嫌疑 {b['suspicion_g2']/g:.2f}; "
              f"通缉 {b['wanted_g2']/g:.2f}")
        print(f"   警察攻击光身 {b['police_attacks_g2']/g:.2f}/局; "
              f"警察存活终局 {b['police_alive_end']/g:.2f}/局")
        print(f"   本局胜者天赋: {dict(b['winner_talent'])}")
        print(f"   本局胜者人格: {dict(b['winner_personality'])}")


def _dump_game(i, res, state, g2_pid, shadow_id, dmg_log, petrify_log):
    print(f"\n{'='*80}\nGAME {i}  winner={res.get('winner_pid')}  "
          f"rounds={res.get('rounds')}  draw={res.get('draw_reason')}")
    for p in res.get("players", []):
        print(f"  pid={p['pid']} talent={p['talent_name']} "
              f"pers={p.get('personality')} win={p.get('is_winner')} "
              f"alive={p.get('alive')} kills={p.get('kill_count')}")
    print(f"  G2={g2_pid} shadow={shadow_id}")
    sh = state.m9_shadows.get(shadow_id)
    if sh is not None:
        print(f"  shadow final: hp={sh.hp} loc={sh.location} "
              f"weapons={[getattr(w,'name','') for w in sh.weapons]} "
              f"credits={sh.credits} action_turns={sh.total_action_turns} "
              f"last_action={sh.last_action_type} petrified={sh.is_petrified} "
              f"terminal={sh.is_terminal_singer}")
    else:
        print("  shadow final: (dissipated/absent)")
    print("  -- dmg_log (t3_starfall or shadow/g2 involved) --")
    for d in dmg_log:
        if (d["source_kind"] == "t3_starfall"
                or d["attacker"] in (g2_pid, shadow_id)
                or d["target"] in (g2_pid, shadow_id)):
            print(f"    r{d['round']} atk={d['attacker']} tgt={d['target']} "
                  f"src={d['source_kind']} hp_dmg={d['hp_damage']} "
                  f"wasPetrified={d['was_petrified']} "
                  f"shake {d['shake_before']}->{d['shake_after']} "
                  f"killed={d['killed']} m9={d['m9_kind']}")
    print("  -- petrify_log (G2/shadow) --")
    for p in petrify_log:
        if p["actor"] in (g2_pid, shadow_id):
            print(f"    r{p['round']} {p['actor']} {p['kind']} "
                  f"before={p.get('before','-')} src={p.get('source','-')}")
    print("  -- shadow full action trace --")
    for e in state.event_log:
        if e.get("player") == shadow_id or e.get("attacker") == shadow_id \
                or e.get("target") == shadow_id:
            t = e.get("type")
            if t in ("move", "interact", "lock", "find", "forfeit", "attack"):
                print(f"    r{e.get('round')} {t} player={e.get('player')} "
                      f"attacker={e.get('attacker')} target={e.get('target')}")
    print("  -- relevant event_log --")
    for e in state.event_log:
        t = e.get("type")
        if t in ("death", "SHADOW_CREATED", "SHADOW_DISSIPATED", "star_attack",
                 "attack", "TERMINAL_SONG_COMMITTED", "g2_last_song_heard",
                 "TERMINAL_SONG_ENDED", "crime", "suspicion", "wanted"):
            keep = False
            if t == "attack":
                keep = (e.get("attacker") in (g2_pid, shadow_id)
                        or e.get("target") in (g2_pid, shadow_id))
            else:
                keep = (e.get("player") == g2_pid
                        or e.get("killer") == g2_pid
                        or (t == "star_attack" and e.get("player") != ""))
            if not keep:
                continue
            r = e.get("result") or {}
            print(f"    r{e.get('round')} {t} player={e.get('player')} "
                  f"attacker={e.get('attacker')} target={e.get('target')} "
                  f"killer={e.get('killer')} cause={e.get('cause')} "
                  f"reason={e.get('reason')} "
                  f"killed={r.get('killed','') if isinstance(r,dict) else ''} "
                  f"hp_dmg={r.get('hp_damage','') if isinstance(r,dict) else ''}")


if __name__ == "__main__":
    _main()
