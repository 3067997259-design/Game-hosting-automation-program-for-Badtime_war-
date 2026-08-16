"""M9 PP / 往世层机制核心（profile: m9-rfc，B4 投注 v0.4 + 评分指针 v0.1）。

- PP 货币：事件生成（first_kill/revenge_kill/.../arc_progress）、生前消费（重掷/
  加成/探测/销案）、衰减（死者免衰减）；数值全读 `m9_system.pp`（[待风洞]）。
- 投注：transfer_fee、黑马（无死者押注的生者）+10 终分、死者囚徒困境；
  绝对死亡者 PP 冻结（不参与投注/转仓/魂援），已托管赌注仍终局结算。
- 魂援：四次援助额度、被动援助固定奖励；世界援助（WORLD_RULE）不占额度、
  不触发援助 PP、无 player provider。
- 评分：arc_count 评分 + 四步求值（DOC-043）——先算排除派彩/黑马加成的
  base_final_score 定胜者快照，再锁市/派彩/黑马加成。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from engine.balance import get as bget


def _pp(key: str, default):
    return bget("m9_system", "pp", key, default=default)


class PPLedger:
    """PP 台账：余额、衰减、消费、投注、黑马快照、魂援额度。"""

    def __init__(self) -> None:
        self._balance: Dict[str, int] = {}
        self._frozen: Dict[str, int] = {}      # absolute_dead：冻结但保留终局结算
        self._bets: Dict[str, Dict[str, List[dict]]] = {}
        self._aid_earned: Dict[str, float] = {}
        self._blackhorse: set = set()
        self._aid_quota_used: Dict[str, int] = {}
        self._aid_provided: Dict[str, int] = {}   # 死者被动提供次数（防早死刷分工厂）
        self._blackhorse_atk: float = float(_pp("blackhorse_atk", 1))
        self._blackhorse_def: float = float(_pp("blackhorse_def", 2))
        self._blackhorse_bonus: float = float(_pp("blackhorse_bonus", 10))
        self._aid_passive_reward: float = float(_pp("aid_passive_reward", 1))
        self._aid_quota_max: int = int(_pp("aid_quota_per_round", 4))

    def balance(self, pid: str) -> int:
        return self._balance.get(pid, 0)

    def earn(self, pid: str, amount: int) -> None:
        if pid in self._frozen:
            return  # 绝对死亡：PP 冻结
        self._balance[pid] = self._balance.get(pid, 0) + max(0, amount)

    def spend(self, pid: str, amount: int) -> bool:
        """预检先于消费。"""
        if amount <= 0:
            return True
        if self.balance(pid) < amount:
            return False
        self._balance[pid] -= amount
        return True

    def freeze(self, pid: str) -> None:
        """绝对死亡分流：余额冻结、不再收入；已托管赌注仍终局结算。"""
        if pid not in self._frozen:
            self._frozen[pid] = self._balance.get(pid, 0)

    def is_frozen(self, pid: str) -> bool:
        return pid in self._frozen

    def decay(self, pid: str, amount: Optional[int] = None) -> None:
        """死者免衰减；衰减到 min_floor 为止。"""
        if pid in self._frozen:
            return
        rate = amount if amount is not None else int(_pp("decay_rate", 1))
        floor = int(_pp("min_floor", 0))
        self._balance[pid] = max(floor, self._balance.get(pid, 0) - rate)

    # ── 投注（B4 v0.4 §四）──
    # _bets: 死者 pid → 目标 pid → [{"amount", "tranche_alive"}, ...]
    # 同目标追加免转会费、按追加时存活人数分层；转仓花 transfer_fee 按当前赔率重定价；
    # 被押者死亡 → tranche 销毁；被押者在胜者快照 → 赢回本金+本金×赔率（存活人数:1）。
    def place_bet(self, bettor_id: str, target_id: str,
                  amount: Optional[int] = None,
                  alive_count: Optional[int] = None) -> bool:
        """死者押注生者：托管入账，赌注不被死者消费；绝对死者不能押。"""
        if bettor_id in self._frozen:
            return False
        stake = 1 if amount is None else max(1, int(amount))
        if self.balance(bettor_id) < stake:
            return False
        self._balance[bettor_id] -= stake
        bets = self._bets.setdefault(bettor_id, {})
        tranches = bets.setdefault(target_id, [])
        tranches.append({
            "amount": stake,
            "tranche_alive": (alive_count if alive_count is not None
                              else max(1, len(self._blackhorse))),
        })
        return True

    def transfer_bet(self, bettor_id: str, old_target: str, new_target: str,
                     alive_count: Optional[int] = None) -> bool:
        """转仓：整仓存活注金、花 transfer_fee、按当前存活人数重新定价。"""
        if bettor_id in self._frozen:
            return False
        bets = self._bets.get(bettor_id, {})
        if old_target not in bets:
            return False
        fee = int(_pp("transfer_fee", 2))
        if self.balance(bettor_id) < fee:
            return False
        total = sum(t["amount"] for t in bets.pop(old_target))
        self._balance[bettor_id] -= fee
        new_tranches = bets.setdefault(new_target, [])
        new_tranches.append({
            "amount": total,
            "tranche_alive": (alive_count if alive_count is not None
                              else max(1, len(self._blackhorse))),
        })
        return True

    def bet_targets(self, bettor_id: str) -> Dict[str, List[dict]]:
        """死者当前押注对象 → tranche 列表。"""
        return {t: list(tr) for t, tr in self._bets.get(bettor_id, {}).items()}

    def total_bet_on(self, target_id: str) -> int:
        """所有死者押在某一生者上的 PP 总额（成交排序用）。"""
        return sum(t["amount"]
                   for bets in self._bets.values()
                   for t in bets.get(target_id, []))

    def has_active_bet(self) -> bool:
        return any(self._bets.values())

    def bets(self) -> Dict[str, Dict[str, List[dict]]]:
        return {d: {t: list(tr) for t, tr in ts.items()}
                for d, ts in self._bets.items()}

    def odds_for(self, alive_count: int) -> int:
        """赔率 = min(存活人数, 2):1（B4 §4.2 人数反比 + 裁决封顶 2:1）。

        封顶原因：存活人数:1 让早死者以 4-5:1 押中胜者得 12-25 显示分，
        与名次加成（封顶 +10）互相打架，构成"早死刷投注"套利。
        """
        return max(1, min(int(alive_count), 2))

    def bet_payout(self, bettor_id: str, winners) -> float:
        """死者终局赌注收益：被押者在胜者快照 → 各 tranche 本金×赔率回本；
        被押者死亡/落败 → tranche 销毁（收益 0）。"""
        total = 0.0
        for target_id, tranches in self._bets.get(bettor_id, {}).items():
            if target_id not in winners:
                continue
            for tr in tranches:
                odds = self.odds_for(tr.get("tranche_alive", 1))
                total += tr["amount"] + tr["amount"] * odds
        return total

    # ── 魂援收益 ──
    def record_aid_earned(self, pid: str, amount: float) -> None:
        """被动援助固定奖励 / 主动转移给死者：计入死者援助收益（评分用）。"""
        self._aid_earned[pid] = self._aid_earned.get(pid, 0.0) + max(0.0, float(amount))

    def aid_earnings(self, pid: str) -> float:
        return self._aid_earned.get(pid, 0.0)

    # ── 黑马快照 ──
    def recompute_blackhorse(self, alive_ids: List[str], dead_ids: List[str]) -> None:
        """每个 R0 开市结束、tranche/转仓落定后重算（轮中死亡不改快照）。
        黑马 = 无任何死者押注的存活玩家。"""
        betted = {t for bets in self._bets.values() for t in bets}
        self._blackhorse = {pid for pid in alive_ids if pid not in betted}

    def is_blackhorse(self, pid: str) -> bool:
        return pid in self._blackhorse

    def blackhorse_attack_bonus(self) -> float:
        return self._blackhorse_atk

    def blackhorse_defense_bonus(self) -> float:
        return self._blackhorse_def

    def blackhorse_win_bonus(self) -> float:
        return self._blackhorse_bonus

    # ── 魂援 ──
    def aid_quota_left(self, pid: str) -> int:
        return self._aid_quota_max - self._aid_quota_used.get(pid, 0)

    def use_aid_quota(self, pid: str) -> bool:
        if self.aid_quota_left(pid) <= 0:
            return False
        self._aid_quota_used[pid] = self._aid_quota_used.get(pid, 0) + 1
        return True

    def passive_aid_reward(self) -> float:
        return self._aid_passive_reward

    def aid_provider_cap(self) -> int:
        return int(_pp("aid_provider_cap", 4))

    def aid_provided_count(self, pid: str) -> int:
        return self._aid_provided.get(pid, 0)

    def aid_provider_can(self, pid: str) -> bool:
        return self.aid_provided_count(pid) < self.aid_provider_cap()

    def record_aid_provided(self, pid: str) -> None:
        self._aid_provided[pid] = self._aid_provided.get(pid, 0) + 1

    def transfer_pp(self, from_pid: str, to_pid: str, amount: int) -> bool:
        """主动援助：生者出价 PP 转移给成交死者（冻结者不能收/付）。"""
        if amount <= 0:
            return True
        if from_pid in self._frozen or to_pid in self._frozen:
            return False
        if self.balance(from_pid) < amount:
            return False
        self._balance[from_pid] -= amount
        self._balance[to_pid] = self._balance.get(to_pid, 0) + amount
        return True

    def pick_passive_provider(self, alive_pid: str, dead_ids: List[str]) -> Optional[str]:
        """被动援助：系统指派提供者——押注该生者 PP 最多→最早死亡→ID 最小；
        提供者受每局次数上限（aid_provider_cap）约束，额度耗尽者不再候选。"""
        eligible = [d for d in dead_ids if self.aid_provider_can(d)]
        if not eligible:
            return None
        bettors = [dead for dead in eligible
                   if alive_pid in self._bets.get(dead, {})]
        if bettors:
            return max(bettors, key=lambda d: self._bet_amount(d, alive_pid))
        return sorted(eligible)[0]

    def _bet_amount(self, bettor_id: str, target_id: str) -> int:
        return sum(t["amount"]
                   for t in self._bets.get(bettor_id, {}).get(target_id, []))

    def pick_active_recipient(self, alive_pid: str, dead_ids: List[str],
                              bid: int) -> Optional[str]:
        """主动援助成交（B4 §5.2）：对请求者当前押注 PP 最多→最早死→ID 最小；
        提供者受每局次数上限约束。"""
        eligible = [d for d in dead_ids if self.aid_provider_can(d)]
        if not eligible:
            return None
        bettors = [dead for dead in eligible
                   if alive_pid in self._bets.get(dead, {})]
        if bettors:
            return max(bettors, key=lambda d: self._bet_amount(d, alive_pid))
        return sorted(eligible)[0]

    def reset(self) -> None:
        self.__init__()


@dataclass
class FinalScore:
    """评分四步求值（DOC-043）：base 定胜者 → 派彩/黑马加成只改显示终分。"""
    base_final_score: float = 0.0
    display_final_score: float = 0.0
    is_winner: bool = False


class ScoringEngine:
    """arc_count 评分（评分指针 v0.1 §二/§三/§四/§五）。

    终分 = 剧情分 + 战果分 + PP + 出局名次加成；死者另计援助收益（投注派彩
    只在显示终分阶段加入）。
    剧情分 = arc_count × arc_weight；战果分 = 击杀×kill_weight + 总伤害×damage_weight。
    名次加成 = placement_step × (名次 − 1)；唯一生还者额外 +last_survivor_bonus
    （存活系数 1.5/0.5 已退役，转化为名次加成——出局越晚赚得越多）。
    四步求值（DOC-043）：base 定胜者快照 → 锁市派彩 + 黑马加成（只改 display）。
    """

    def __init__(self, pp: PPLedger, game_state: Any = None) -> None:
        self.pp = pp
        self._state = game_state
        self._arc: Dict[str, int] = {}
        self._retreated: set = set()
        # 出局名次加成（裁决：存活系数转化为名次加成——出局越晚赚得越多，
        # 最后生还者额外加成）
        self._placement_rank: Dict[str, int] = {}
        self._placement_step: float = float(bget(
            "m9_system", "scoring_m9", "placement_step", default=2))
        self._last_survivor_bonus: float = float(bget(
            "m9_system", "scoring_m9", "last_survivor_bonus", default=4))
        self._last_survivor_pid: Optional[str] = None
        # 槽位得分系数（2026-09 R24 用户批准）：只读 balance 的通用平衡通道，
        # 不改玩法机制；缺省 1.0。
        self._talent_multipliers: Dict[str, float] = {}
        raw_table = bget(
            "m9_system", "scoring_m9", "talent_score_multiplier",
            default={}) or {}
        if isinstance(raw_table, dict):
            for slot_id, value in raw_table.items():
                try:
                    self._talent_multipliers[str(slot_id).upper()] = float(value)
                except (TypeError, ValueError):
                    pass

    def attach_state(self, game_state: Any) -> None:
        self._state = game_state

    def add_arc(self, pid: str, amount: int = 1) -> None:
        if pid not in self.pp._frozen:  # 绝对死亡不进往世层
            self._arc[pid] = self._arc.get(pid, 0) + max(0, amount)

    def mark_retreat(self, pid: str) -> None:
        """G0 撤退 / G5 因果闭合：生者公式 0.5，不死亡、不进往世层。"""
        self._retreated.add(pid)

    def arc_count(self, pid: str) -> int:
        return self._arc.get(pid, 0)

    def _story(self, pid: str) -> float:
        arc = int(self._arc.get(pid, 0))
        cap = int(bget("m9_system", "scoring_m9", "arc_cap", default=3))
        return float(min(arc, cap)) * float(
            bget("m9_system", "scoring_m9", "arc_weight", default=2))

    def _battle(self, pid: str) -> float:
        if self._state is None:
            return 0.0
        p = self._state.get_player(pid)
        if p is None:
            return 0.0
        kills = float(getattr(p, "kill_count", 0))
        dmg = float(getattr(p, "damage_dealt", 0))
        kw = float(bget("m9_system", "scoring_m9", "kill_weight", default=3))
        dw = float(bget("m9_system", "scoring_m9", "damage_weight", default=0.1))
        return kills * kw + dmg * dw

    def _placement(self, pid: str) -> float:
        """出局名次加成：step × (名次 − 1)；唯一生还者额外 +last_survivor_bonus。"""
        rank = self._placement_rank.get(pid, 1)
        bonus = self._placement_step * (rank - 1)
        if self._last_survivor_pid is not None and pid == self._last_survivor_pid:
            bonus += self._last_survivor_bonus
        return bonus

    def _slot_of(self, pid: str) -> str:
        """槽位解析：读 player.talent_slot_id，空值返回 ""。"""
        if self._state is None:
            return ""
        player = self._state.get_player(pid)
        if player is None:
            return ""
        return str(getattr(player, "talent_slot_id", "") or "")

    def score(self, pid: str, alive: bool) -> FinalScore:
        """base_final_score：剧情 + 战果 + PP + 名次加成（死者另计援助收益）。

        存活系数（1.5/0.5）已退役，转化为出局名次加成：出局越晚赚得越多。
        槽位得分系数作用于合计后的 base（胜率极差收敛通道）。
        """
        base = (self._story(pid) + self._battle(pid)
                + float(self.pp.balance(pid)) + self._placement(pid))
        if not alive:
            base += float(self.pp.aid_earnings(pid))
        multiplier = self._talent_multipliers.get(self._slot_of(pid), 1.0)
        return FinalScore(base_final_score=base * multiplier)

    def settle(self, alive_ids: List[str], dead_ids: List[str],
               game_state: Any = None) -> Dict[str, FinalScore]:
        """四步求值：名次加成 → base 定胜者快照 → 锁市派彩 + 黑马加成（只改 display）。"""
        if game_state is not None:
            self.attach_state(game_state)
        state = self._state
        arc_ledger = getattr(state, "m9_arc", None)
        if arc_ledger is not None and hasattr(arc_ledger, "scan"):
            try:
                arc_ledger.scan(state)  # 终局兜底扫描最后一轮事件
            except Exception:
                pass
        alive_set = set(alive_ids)
        if state is not None:
            order = list(getattr(state, "player_order", []) or [])
        else:
            order = list(dead_ids) + list(alive_ids)

        def exit_round(pid: str) -> int:
            p = state.get_player(pid) if state is not None else None
            if p is None:
                return 0
            return int(getattr(p, "death_round", 0) or 0) \
                or int(getattr(p, "_m9_exit_round", 0) or 0)

        dead_sorted = sorted((pid for pid in order if pid not in alive_set),
                             key=exit_round)
        alive_sorted = [pid for pid in order if pid in alive_set]
        ranked = dead_sorted + alive_sorted
        self._placement_rank = {pid: i + 1 for i, pid in enumerate(ranked)}
        self._last_survivor_pid = alive_sorted[0] if len(alive_sorted) == 1 else None

        results: Dict[str, FinalScore] = {}
        for pid in alive_ids:
            results[pid] = self.score(pid, alive=True)
        for pid in dead_ids:
            results[pid] = self.score(pid, alive=False)
        if not results:
            return results
        top = max(r.base_final_score for r in results.values())
        winners = {pid for pid, r in results.items() if r.base_final_score == top}
        for pid, r in results.items():
            r.is_winner = pid in winners
            r.display_final_score = r.base_final_score
            # 锁市派彩：死者赌注收益（被押者在快照才回本+赔率；仅显示终分）
            if pid not in alive_ids:
                r.display_final_score += self.pp.bet_payout(pid, winners)
            # 黑马胜利加成（不参与胜者求值）
            if r.is_winner and self.pp.is_blackhorse(pid):
                r.display_final_score += self.pp.blackhorse_win_bonus()
        return results
