"""G7 风洞行为追踪：打印每天赋为 G7 的玩家逐轮动作摘要。

用法: python tools/diag_g7_trace.py [--games N] [--seed S]

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
    parser.add_argument("--games", type=int, default=6)
    parser.add_argument("--seed", type=int, default=5000)
    args = parser.parse_args()

    experiments.reset()
    experiments.set_profile("m9-rfc")

    from stats_runner import run_single_game, TALENT_NUM_TO_NAME
    from engine.round_manager import RoundManager
    from controllers.ai.talents.hoshino_hook import HoshinoAIHook

    # 捕获每局的 game_state 引用（只读探针，不改游戏文件）
    _captured = []
    _hook_calls = []

    def _capture_loop(self):
        _captured.append(self.state)
        return _RoundManager_run_game_loop(self)

    _RoundManager_run_game_loop = RoundManager.run_game_loop
    RoundManager.run_game_loop = _capture_loop

    _orig_override = HoshinoAIHook.should_override_candidates

    def _log_override(self, player, state, available):
        out = _orig_override(self, player, state, available)
        t = getattr(player, "talent", None)
        if t is not None and getattr(t, "tactical_unlocked", False):
            can_shoot = bool(self._hoshino._hoshino_has_ammo(player)) or bool(
                self._hoshino._hoshino_find_consumable_for_reload(player))
            horus_ok = self._hoshino._hoshino_iron_horus_hp(player) > 0
            from controllers.ai.game_query import GameQuery
            same = GameQuery.get_same_location_targets(player, state)
            _hook_calls.append(
                (getattr(player, "player_id", "?"),
                 getattr(state, "current_round", "?"), out,
                 f"can_shoot={can_shoot} horus={horus_ok} "
                 f"special={'special' in available} "
                 f"same={[p.name for p in same]} "
                 f"loc={GameQuery.get_location_str(player)}"))
        else:
            _hook_calls.append(
                (getattr(player, "player_id", "?"),
                 getattr(state, "current_round", "?"), out, ""))
        return out

    HoshinoAIHook.should_override_candidates = _log_override

    for i in range(args.games):
        _captured.clear()
        _hook_calls.clear()
        random.seed(args.seed + i)
        res = run_single_game(6, collect_digest=True)
        state_obj = _captured[0] if _captured else None
        g7_pids = [
            p["pid"] for p in res["players"]
            if TALENT_NUM_TO_NAME.get(p["talent_num"]) == "神代天赋-大叔我啊，剪短发了"
        ]
        print(f"\n===== game {i} (seed {args.seed + i}) G7 pids={g7_pids} "
              f"draw={res.get('draw_reason')} =====")
        for pid in g7_pids:
            print(f"  hook calls for {pid}:")
            for hp, rnd, out, flags in _hook_calls:
                if hp == pid and flags:
                    print(f"    R{rnd}: {out}  [{flags}]")
        for pid in g7_pids:
            player = state_obj.get_player(pid) if state_obj is not None else None
            t = getattr(player, "talent", None)
            if t is not None:
                print(f"  G7[{pid}] form={getattr(t, 'form', None)} "
                      f"tactical={getattr(t, 'tactical_unlocked', None)} "
                      f"fusion_s={getattr(t, 'fusion_shield_done', None)} "
                      f"fusion_w={getattr(t, 'fusion_weapon_done', None)} "
                      f"horus={getattr(t, 'iron_horus_hp', None)}/"
                      f"{getattr(t, 'iron_horus_max_hp', None)} "
                      f"ammo={len(getattr(t, 'ammo', []) or [])} "
                      f"shield={getattr(t, 'shield_mode', None)} "
                      f"weapons={[w.name for w in getattr(player, 'weapons', []) if w]} "
                      f"items={[getattr(i, 'name', '') for i in getattr(player, 'items', []) or [] if i]}")
        digest = res.get("event_digest", [])
        for line in digest:
            if any(f"player={pid}" in line or f"attacker={pid}" in line
                   for pid in g7_pids):
                print(f"  {line}")


if __name__ == "__main__":
    _main()
