"""
结构化诊断工具 —— DiagCollector & DiagReport
═══════════════════════════════════════════════
用于分析新架构 orchestrator 在批量运行中的 forfeit / fallback / draw 模式。

DiagCollector: 每个 BasicAIController 实例持有一个，收集局内事件。
DiagReport:    在 stats_runner.run_batch 中使用，聚合多局诊断数据并输出报告。
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional


class DiagCollector:
    """每局每个 AI controller 的诊断数据收集器。"""

    def __init__(self) -> None:
        self.forfeit_events: List[Dict[str, Any]] = []
        self.fallback_events: List[Dict[str, Any]] = []
        self.round_snapshots: List[Dict[str, Any]] = []
        self._candidate_attempts: List[Dict[str, Any]] = []

    # ── 记录方法 ──

    def record_candidate_attempt(
        self,
        round_num: int,
        attempt: int,
        cmd: str,
        parse_ok: bool,
        valid: bool,
        reason: str,
        executed: bool,
    ) -> None:
        try:
            self._candidate_attempts.append({
                "round": round_num,
                "attempt": attempt,
                "cmd": cmd,
                "parse_ok": parse_ok,
                "valid": valid,
                "reason": reason,
                "executed": executed,
            })
        except Exception:
            pass

    def record_forfeit(
        self,
        round_num: int,
        player_name: str,
        talent: str,
        personality: str,
        reason: str,
        candidates: List[str],
        available_actions: List[str],
        reject_reasons: List[str],
    ) -> None:
        try:
            self.forfeit_events.append({
                "round": round_num,
                "player_name": player_name,
                "talent": talent,
                "personality": personality,
                "reason": reason,
                "candidates": list(candidates) if candidates else [],
                "available_actions": list(available_actions) if available_actions else [],
                "reject_reasons": list(reject_reasons) if reject_reasons else [],
            })
        except Exception:
            pass

    def record_round_snapshot(self, round_num: int, state: Any) -> None:
        try:
            alive = state.alive_players()
            players_info = []
            for p in alive:
                players_info.append({
                    "pid": p.player_id,
                    "hp": round(p.hp, 2),
                    "max_hp": round(p.max_hp, 2),
                    "loc": getattr(p, 'location', ''),
                    "talent": getattr(p, 'talent_name', ''),
                    "kill_count": getattr(p, 'kill_count', 0),
                })
            self.round_snapshots.append({
                "round": round_num,
                "alive_count": len(alive),
                "players": players_info,
            })
        except Exception:
            pass

    def clear_round_snapshots(self) -> None:
        self.round_snapshots.clear()

    def export_summary(self) -> Dict[str, Any]:
        try:
            reject_reasons: List[str] = []
            for evt in self.forfeit_events:
                reject_reasons.extend(evt.get("reject_reasons", []))
            for att in self._candidate_attempts:
                if not att.get("valid") and att.get("reason"):
                    reject_reasons.append(att["reason"])

            return {
                "forfeit_events": list(self.forfeit_events),
                "fallback_events": list(self.fallback_events),
                "round_snapshots": list(self.round_snapshots),
                "candidate_attempts": list(self._candidate_attempts),
                "reject_reason_counts": dict(Counter(reject_reasons)),
            }
        except Exception:
            return {}


class DiagReport:
    """聚合多局诊断数据并输出结构化报告。"""

    def __init__(self) -> None:
        self._games: List[Dict[str, Any]] = []
        self._forfeit_all: List[Dict[str, Any]] = []
        self._fallback_all: List[Dict[str, Any]] = []
        self._candidate_attempts_all: List[Dict[str, Any]] = []
        self._round_snapshots_by_game: List[Dict[str, Any]] = []
        self._draw_details: List[Dict[str, Any]] = []
        self._reject_reason_counter: Counter = Counter()

    def add_game(self, game_idx: int, result: Dict[str, Any]) -> None:
        try:
            diag_data = result.get("diagnostics", {})
            draw_detail = result.get("draw_detail")
            is_draw = result.get("draw", False)

            game_entry: Dict[str, Any] = {
                "game_idx": game_idx,
                "draw": is_draw,
                "draw_reason": result.get("draw_reason", ""),
                "rounds": result.get("rounds", 0),
            }

            for pid, pdata in diag_data.items():
                for evt in pdata.get("forfeit_events", []):
                    evt_copy = dict(evt)
                    evt_copy["game_idx"] = game_idx
                    self._forfeit_all.append(evt_copy)
                for evt in pdata.get("fallback_events", []):
                    evt_copy = dict(evt)
                    evt_copy["game_idx"] = game_idx
                    self._fallback_all.append(evt_copy)
                for att in pdata.get("candidate_attempts", []):
                    att_copy = dict(att)
                    att_copy["game_idx"] = game_idx
                    att_copy["pid"] = pid
                    self._candidate_attempts_all.append(att_copy)
                if is_draw and pdata.get("round_snapshots"):
                    self._round_snapshots_by_game.append({
                        "game_idx": game_idx,
                        "pid": pid,
                        "snapshots": pdata.get("round_snapshots", []),
                    })
                reason_counts = pdata.get("reject_reason_counts", {})
                for reason, cnt in reason_counts.items():
                    self._reject_reason_counter[reason] += cnt

            if is_draw and draw_detail:
                dd = dict(draw_detail)
                dd["game_idx"] = game_idx
                self._draw_details.append(dd)

            self._games.append(game_entry)
        except Exception:
            pass

    # ── 报告输出 ──

    def print_forfeit_summary(self) -> None:
        total_games = len(self._games)
        if not self._forfeit_all:
            print("\n  (无 forfeit 事件)")
            return

        print(f"\n{'=' * 70}")
        print(f"  Forfeit 归因分析（{total_games}局）")
        print(f"{'=' * 70}")

        # 按原因分类
        by_reason: Dict[str, List[Dict]] = defaultdict(list)
        for evt in self._forfeit_all:
            by_reason[evt.get("reason", "unknown")].append(evt)

        total_forfeits = len(self._forfeit_all)
        for reason, events in sorted(by_reason.items(), key=lambda x: -len(x[1])):
            count = len(events)
            pct = count / total_forfeits * 100 if total_forfeits else 0
            # Top 天赋
            talent_counter = Counter(e.get("talent", "") for e in events)
            top_talents = ", ".join(
                f"{t}x{c}" for t, c in talent_counter.most_common(5) if t
            )
            print(f"  {reason:<28s} {count:>5d}  {pct:5.1f}%    Top天赋: {top_talents}")

        # Top 被拒原因
        if self._reject_reason_counter:
            print(f"\n  Top 被拒原因（validate reason）：")
            for reason, cnt in self._reject_reason_counter.most_common(10):
                print(f"    \"{reason}\" x {cnt}")

    def print_fallback_summary(self) -> None:
        total_games = len(self._games)
        if not self._fallback_all:
            print("\n  (无 fallback 事件)")
            return

        print(f"\n{'=' * 70}")
        print(f"  Fallback 分析（{total_games}局）")
        print(f"{'=' * 70}")

        total_fallbacks = len(self._fallback_all)
        print(f"  总 fallback 次数: {total_fallbacks}")

        # 按 attempt_count 分布
        attempt_dist = Counter(e.get("attempt_count", 0) for e in self._fallback_all)
        print(f"\n  重试次数分布：")
        for attempts, cnt in sorted(attempt_dist.items()):
            print(f"    {attempts}次重试: {cnt}次")

        # 按天赋分类
        talent_counter = Counter(e.get("talent", "") for e in self._fallback_all)
        if talent_counter:
            print(f"\n  按天赋分类：")
            for talent, cnt in talent_counter.most_common(10):
                print(f"    {talent or '(无天赋)'}: {cnt}次")

    def print_draw_analysis(self) -> None:
        total_games = len(self._games)
        draw_games = [g for g in self._games if g.get("draw")]
        max_round_draws = [g for g in draw_games if g.get("draw_reason") == "max_rounds"]

        if not self._draw_details:
            print(f"\n  (无 max_rounds 平局的详细数据)")
            return

        print(f"\n{'=' * 70}")
        print(f"  平局深度分析（max_rounds 类，共 {len(self._draw_details)} 局）")
        print(f"{'=' * 70}")

        # 最终存活人数分布
        alive_dist = Counter(
            len(dd.get("final_alive", [])) for dd in self._draw_details
        )
        print(f"\n  最终存活人数分布：")
        for alive_count, cnt in sorted(alive_dist.items()):
            pct = cnt / len(self._draw_details) * 100
            print(f"    {alive_count}人: {cnt}局({pct:.0f}%)")

        # 天赋组合热点（最终 2 人对峙）
        two_player_draws = [
            dd for dd in self._draw_details
            if len(dd.get("final_alive", [])) == 2
        ]
        if two_player_draws:
            combo_counter: Counter = Counter()
            for dd in two_player_draws:
                talents = sorted(
                    p.get("talent", "") for p in dd["final_alive"]
                )
                combo_counter[" + ".join(talents)] += 1
            print(f"\n  天赋组合热点（最终 2 人对峙）：")
            for combo, cnt in combo_counter.most_common(10):
                print(f"    {combo}: {cnt}局")

        # 最后20轮行为模式分类
        forfeit_heavy = 0
        attack_no_kill = 0
        no_attack = 0
        other_pattern = 0

        for dd in self._draw_details:
            action_types = dd.get("last_20_action_types", {})
            forfeit_count = dd.get("last_20_forfeit_count", 0)
            total_actions = sum(action_types.values()) if action_types else 1

            if total_actions > 0 and forfeit_count / max(total_actions, 1) > 0.5:
                forfeit_heavy += 1
            elif action_types.get("attack", 0) > 0:
                # 有攻击但可能 0 击杀（通过存活人数推测）
                attack_no_kill += 1
            elif action_types.get("attack", 0) == 0:
                no_attack += 1
            else:
                other_pattern += 1

        n = len(self._draw_details)
        print(f"\n  最后20轮行为模式分类：")
        if forfeit_heavy:
            print(f"    forfeit占比>50%: {forfeit_heavy}局({forfeit_heavy/n*100:.0f}%) <- AI 不知道做什么")
        if attack_no_kill:
            print(f"    有攻击但0击杀:   {attack_no_kill}局({attack_no_kill/n*100:.0f}%) <- 攻击无效/免死")
        if no_attack:
            print(f"    无攻击(纯发育):  {no_attack}局({no_attack/n*100:.0f}%) <- 互相回避")
        if other_pattern:
            print(f"    其他模式:        {other_pattern}局({other_pattern/n*100:.0f}%)")

    def save_raw(self, path: str) -> None:
        """保存聚合后的结构化数据到 JSON 文件。"""
        try:
            import os
            os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
            data = {
                "total_games": len(self._games),
                "forfeit_events": self._forfeit_all,
                "fallback_events": self._fallback_all,
                "candidate_attempts": self._candidate_attempts_all,
                "round_snapshots": self._round_snapshots_by_game,
                "draw_details": self._draw_details,
                "reject_reason_counts": dict(self._reject_reason_counter),
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"\n  诊断原始数据已保存到: {path}")
        except Exception as e:
            print(f"\n  保存诊断数据失败: {e}")
