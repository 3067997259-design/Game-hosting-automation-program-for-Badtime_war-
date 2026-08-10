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
        self._bets: Dict[str, str] = {}        # 死者 pid → 押注者（已托管）
        self._blackhorse: set = set()
        self._aid_quota_used: Dict[str, int] = {}
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

    # ── 投注 ──
    def place_bet(self, bettor_id: str, target_id: str) -> bool:
        """死者押注生者：托管入账，赌注不被死者消费；绝对死者不能押。"""
        if bettor_id in self._frozen:
            return False
        fee = int(_pp("transfer_fee", 2))
        if self.balance(bettor_id) < fee:
            return False
        self._balance[bettor_id] -= fee
        self._bets[bettor_id] = target_id
        return True

    def has_active_bet(self) -> bool:
        return bool(self._bets)

    def bets(self) -> Dict[str, str]:
        return dict(self._bets)

    # ── 黑马快照 ──
    def recompute_blackhorse(self, alive_ids: List[str], dead_ids: List[str]) -> None:
        """每个 R0 开市结束、tranche/转仓落定后重算（轮中死亡不改快照）。
        黑马 = 无任何死者押注的存活玩家。"""
        betted = set(self._bets.values())
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

    def reset(self) -> None:
        self.__init__()


@dataclass
class FinalScore:
    """评分四步求值（DOC-043）：base 定胜者 → 派彩/黑马加成只改显示终分。"""
    base_final_score: float = 0.0
    display_final_score: float = 0.0
    is_winner: bool = False


class ScoringEngine:
    """arc_count 评分：生死/撤退终态 + 绝对死分流（评分指针 v0.1）。"""

    def __init__(self, pp: PPLedger) -> None:
        self.pp = pp
        self._arc: Dict[str, int] = {}
        self._retreated: set = set()

    def add_arc(self, pid: str, amount: int = 1) -> None:
        if pid not in self.pp._frozen:  # 绝对死亡不进往世层
            self._arc[pid] = self._arc.get(pid, 0) + max(0, amount)

    def mark_retreat(self, pid: str) -> None:
        """G0 撤退：生者公式 0.5，不死亡、不进往世层。"""
        self._retreated.add(pid)

    def arc_count(self, pid: str) -> int:
        return self._arc.get(pid, 0)

    def score(self, pid: str, alive: bool) -> FinalScore:
        """生者：剧情+喝彩+战果 × 存活 复合分；撤退：生者公式 0.5；
        死者（非绝对死亡）：往世公式 = arc_count + 投注收益。"""
        if pid in self._retreated:
            base = 0.5 * self._survivor_component(pid)
        elif alive:
            base = self._survivor_component(pid)
        else:
            base = self._dead_component(pid)
        return FinalScore(base_final_score=base)

    def _survivor_component(self, pid: str) -> float:
        return float(self._arc.get(pid, 0)) + float(self.pp.balance(pid))

    def _dead_component(self, pid: str) -> float:
        return float(self._arc.get(pid, 0)) * 2.0

    def settle(self, alive_ids: List[str], dead_ids: List[str]) -> Dict[str, FinalScore]:
        """四步求值：base 最高者并列进 winner 快照；随后锁市派彩与黑马加成
        （只改 display，不重算胜者）。"""
        results: Dict[str, FinalScore] = {}
        for pid in alive_ids:
            results[pid] = self.score(pid, alive=True)
        for pid in dead_ids:
            if not self.pp.is_frozen(pid):
                results[pid] = self.score(pid, alive=False)
        if not results:
            return results
        top = max(r.base_final_score for r in results.values())
        winners = {pid for pid, r in results.items() if r.base_final_score == top}
        for pid, r in results.items():
            r.is_winner = pid in winners
            r.display_final_score = r.base_final_score
            if r.is_winner and self.pp.is_blackhorse(pid):
                r.display_final_score += self.pp.blackhorse_win_bonus()
        return results
