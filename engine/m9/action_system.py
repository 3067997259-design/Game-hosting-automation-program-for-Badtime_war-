"""M9 动作系统机制核心（profile: m9-rfc，行动系统 RFC v0.8）。

独立于 v2exp 流水线：本包只被 m9-rfc profile 的接入层引用，v2exp 代码不 import 本包，
保证 v2exp 行为回归不漂。数值一律读 `data/balance.json` 的 `m9_system.*`（[待风洞]）。

覆盖合同结构：
- 全员单行动槽：每个 actor 每全局轮一个标准槽（`full_extra` 例外，受上限与递归闸约束）；
- SP 分层 0/1/2：即演 −1、公演 −2，SP 是演出资源不是行动次数；
- 关注/报名窗口：每轮关注额度、公演 FIFO 队列（队首失效不递补、永久移除可重报）；
- ActionGrant：信封携带 actor/kind/source/父链/深度/即演/公演；
- 受限菜单：`restricted_followup` 只允许受限菜单，不给完整菜单；
- 三源完整额外行动：优先级固定 T4 或跃 > 地火诗 > G4 负世主动燃尽；每人每轮至多一个，
  候选落选整体丢弃（不延期不结转）；递归深度闸防套娃。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from engine.balance import get as bget

# ── SP 分层（冻结结构，非数值旋钮）──
SP_IMPROVISE_COST = 1   # 即演
SP_PUBLIC_COST = 2      # 公演
SP_MAX = 2

# ── 三源完整额外行动候选（审计 v0.1 纪律 5，优先级从高到低）──
FULL_EXTRA_SOURCES: tuple = ("t4_hexagram_hojump", "g5_earthfire_poem",
                             "g4_savior_active_burn")

# ── 标准受限行动菜单（v0.8 §4 受限菜单；冻结枚举）──
RESTRICTED_MENU: tuple = ("move", "interact", "attack", "find", "lock")


def max_full_extra_per_round() -> int:
    """每人每全局轮完整额外行动上限（当前 1，读 m9_system，[待风洞]）。"""
    return int(bget("m9_system", "action", "full_extra_per_round", default=1))


def max_grant_depth() -> int:
    """ActionGrant 递归深度上限（当前 2，防 full-extra 套娃）。"""
    return int(bget("m9_system", "action", "grant_depth", default=2))


@dataclass
class ActionGrant:
    """一次行动授予：信封携带全部事实，杜绝从单一布尔反推槽位结果。"""
    actor_id: str
    kind: str                    # standard / full_extra / aid_rest / wake
    source_id: str               # 来源白名单键
    global_round: int = 0
    grant_id: str = ""
    parent_grant_id: Optional[str] = None
    depth: int = 0
    allow_instant: bool = False  # 即演（−1 SP）
    allow_public: bool = False   # 公演（−2 SP）
    restricted: bool = False     # 受限菜单（不给完整菜单）

    def with_id(self, grant_id: str) -> "ActionGrant":
        self.grant_id = grant_id
        return self


@dataclass
class SlotOutcome:
    """槽位收尾事实（resolution_kind 枚举值见 RESOLUTION_KINDS）。"""
    slot_assigned: bool = False
    slot_resolved: bool = False
    resolution_kind: str = ""
    root_action_performed: bool = False
    performance_performed: bool = False
    voluntary_forfeit: bool = False
    suppressed: bool = False
    grant_id: str = ""


RESOLUTION_KINDS = ("action_performed", "suppressed", "aid_rest", "wake",
                    "petrified_hold", "forfeit", "no_target")


class GrantLedger:
    """ActionGrant 台账：去重、每轮每人 full-extra 上限、递归深度闸。"""

    def __init__(self) -> None:
        self._grants: Dict[str, ActionGrant] = {}
        self._full_extra_by_round: Dict[int, Dict[str, str]] = {}
        self._next_id = 0

    def _new_grant_id(self) -> str:
        self._next_id += 1
        return f"g{self._next_id}"

    def issue(self, actor_id: str, kind: str, source_id: str,
              global_round: int, parent: Optional[ActionGrant] = None,
              allow_instant: bool = False, allow_public: bool = False,
              restricted: bool = False) -> Optional[ActionGrant]:
        """发行授予；违反白名单/上限/深度返回 None（不发行、不抛错）。"""
        if source_id not in FULL_EXTRA_SOURCES and kind != "standard":
            if kind not in ("aid_rest", "wake"):
                return None
        depth = 0
        if parent is not None:
            depth = parent.depth + 1
            if depth > max_grant_depth():
                return None
        if kind == "full_extra":
            by_round = self._full_extra_by_round.setdefault(global_round, {})
            # 同一玩家同轮完整额外行动至多一个（落选候选整体丢弃）
            if actor_id in by_round:
                return None
            by_round[actor_id] = source_id
        grant = ActionGrant(actor_id=actor_id, kind=kind, source_id=source_id,
                            global_round=global_round, parent_grant_id=(
                                parent.grant_id if parent else None),
                            depth=depth, allow_instant=allow_instant,
                            allow_public=allow_public, restricted=restricted)
        self._grants[self._new_grant_id()] = grant
        return grant

    def get(self, grant_id: str) -> Optional[ActionGrant]:
        return self._grants.get(grant_id)

    def reset(self) -> None:
        self._grants.clear()
        self._full_extra_by_round.clear()
        self._next_id = 0


class PublicPerformanceQueue:
    """公演队列（v0.8 §6.2 失效纪律）：FIFO；队首失效永久移除、不递补；
    被赤原猎风等强制移除后需从队尾重报。"""

    def __init__(self) -> None:
        self._queue: List[str] = []

    def enqueue(self, actor_id: str) -> None:
        if actor_id not in self._queue:
            self._queue.append(actor_id)

    def head(self) -> Optional[str]:
        return self._queue[0] if self._queue else None

    def is_in_queue(self, actor_id: str) -> bool:
        return actor_id in self._queue

    def remove_permanently(self, actor_id: str) -> bool:
        """永久移除（失效或赤原猎风）。返回是否曾在队中。"""
        if actor_id in self._queue:
            self._queue.remove(actor_id)
            return True
        return False

    def reenqueue_from_tail(self, actor_id: str) -> None:
        self.remove_permanently(actor_id)
        self.enqueue(actor_id)

    def __len__(self) -> int:
        return len(self._queue)


class ActionSystem:
    """M9 行动系统门面：SP、关注额度、槽位收尾、派发与三源仲裁。"""

    def __init__(self, grant_ledger: Optional[GrantLedger] = None) -> None:
        self.ledger = grant_ledger or GrantLedger()
        self.queue = PublicPerformanceQueue()
        self.sp: Dict[str, int] = {}
        self._attention_used: Dict[int, int] = {}
        self._slots: Dict[str, SlotOutcome] = {}

    # ── SP ──
    def get_sp(self, actor_id: str) -> int:
        return self.sp.get(actor_id, 0)

    def set_sp(self, actor_id: str, value: int) -> None:
        self.sp[actor_id] = max(0, min(value, SP_MAX))

    def spend_sp(self, actor_id: str, cost: int) -> bool:
        """预检先于消费：SP 不足返回 False 且不改状态。"""
        if cost <= 0:
            return True
        if self.get_sp(actor_id) < cost:
            return False
        self.sp[actor_id] -= cost
        return True

    # ── 关注额度（每轮关注额度，读 m9_system，[待风洞]）──
    def attention_quota(self) -> int:
        return int(bget("m9_system", "action", "attention_per_round", default=1))

    def can_attend(self, global_round: int) -> bool:
        return self._attention_used.get(global_round, 0) < self.attention_quota()

    def mark_attention(self, global_round: int) -> bool:
        if not self.can_attend(global_round):
            return False
        self._attention_used[global_round] = self._attention_used.get(
            global_round, 0) + 1
        return True

    # ── 报名/关注窗口：R0 报名 → 分配公演位（FIFO）──
    def register_performance(self, actor_id: str, global_round: int) -> bool:
        """R0 报名窗口：SP≥2 才可报名；升 SP 后最早下轮报名。"""
        if self.get_sp(actor_id) < SP_PUBLIC_COST:
            return False
        if not self.can_attend(global_round):
            return False
        self.queue.enqueue(actor_id)
        self.mark_attention(global_round)
        return True

    def assign_public_slot(self, global_round: int) -> Optional[str]:
        """分配本轮公演位：队首失效（SP<2）→ 永久移除、不递补。"""
        while True:
            head = self.queue.head()
            if head is None:
                return None
            if self.get_sp(head) < SP_PUBLIC_COST:
                self.queue.remove_permanently(head)
                continue
            return head

    # ── 即演/公演派发 ──
    def dispatch_improvise(self, actor_id: str, global_round: int,
                           source_id: str = "improvise",
                           parent: Optional[ActionGrant] = None,
                           restricted: bool = True) -> Optional[ActionGrant]:
        """即演：−1 SP，不进公演队列，无独立冷却（审计 v0.1 场景 26）。"""
        if not self.spend_sp(actor_id, SP_IMPROVISE_COST):
            return None
        return self.ledger.issue(actor_id, "standard", source_id, global_round,
                                 parent=parent, allow_instant=True,
                                 restricted=restricted)

    def dispatch_public(self, actor_id: str, global_round: int,
                        source_id: str = "public",
                        parent: Optional[ActionGrant] = None) -> Optional[ActionGrant]:
        """公演：预检（公演位）先于消费（−2 SP），位失则 SP 不动。"""
        if self.assign_public_slot(global_round) != actor_id:
            return None
        if not self.spend_sp(actor_id, SP_PUBLIC_COST):
            return None
        self.queue.remove_permanently(actor_id)  # 公演位消费
        return self.ledger.issue(actor_id, "standard", source_id, global_round,
                                 parent=parent, allow_public=True)

    def dispatch_full_extra(self, actor_id: str, global_round: int,
                            source_id: str,
                            parent: Optional[ActionGrant] = None,
                            allow_instant: bool = True,
                            allow_public: bool = False) -> Optional[ActionGrant]:
        """完整额外行动：三源白名单 + 每轮每人上限 + 深度闸（台账内部执行）。"""
        return self.ledger.issue(actor_id, "full_extra", source_id, global_round,
                                 parent=parent, allow_instant=allow_instant,
                                 allow_public=allow_public)

    def pick_full_extra_candidate(self, available_sources: List[str]) -> Optional[str]:
        """同轮多候选仲裁：只保留最高优先级一个，其余整体丢弃。"""
        for src in FULL_EXTRA_SOURCES:
            if src in available_sources:
                return src
        return None

    # ── 槽位收尾（统一 finalization）──
    def assign_slot(self, actor_id: str) -> str:
        slot_id = f"{actor_id}:{len(self._slots)}"
        self._slots[slot_id] = SlotOutcome(slot_assigned=True, grant_id=slot_id)
        return slot_id

    def resolve_slot(self, slot_id: str, *, root_action: bool = False,
                     kind: str = "action_performed", suppressed: bool = False,
                     voluntary_forfeit: bool = False) -> None:
        """统一收尾：所有槽（标准/控制消费/full-extra/aid_rest）都必须经此写出
        slot_resolved 与 resolution_kind。"""
        outcome = self._slots.get(slot_id)
        if outcome is None:
            return
        outcome.slot_resolved = True
        outcome.resolution_kind = kind if kind in RESOLUTION_KINDS else "action_performed"
        outcome.root_action_performed = root_action
        outcome.suppressed = suppressed
        outcome.voluntary_forfeit = voluntary_forfeit

    def outcome(self, slot_id: str) -> Optional[SlotOutcome]:
        return self._slots.get(slot_id)

    def reset(self) -> None:
        self.ledger.reset()
        self.queue = PublicPerformanceQueue()
        self.sp.clear()
        self._attention_used.clear()
        self._slots.clear()
