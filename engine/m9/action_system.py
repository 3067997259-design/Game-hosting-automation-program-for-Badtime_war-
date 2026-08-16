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
FULL_EXTRA_SOURCES: tuple = ("t4_hexagram_hojump", "g5_poem_earthfire",
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
                    "petrified_hold", "forfeit", "no_target",
                    "wake_followup", "shadow_dissipated",
                    "terminal_song_conversion")


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
            if kind not in ("aid_rest", "wake", "restricted_followup"):
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
        grant_id = self._new_grant_id()
        grant = ActionGrant(actor_id=actor_id, kind=kind, source_id=source_id,
                            global_round=global_round, parent_grant_id=(
                                parent.grant_id if parent else None),
                            depth=depth, allow_instant=allow_instant,
                            allow_public=allow_public, restricted=restricted)
        grant.with_id(grant_id)
        self._grants[grant_id] = grant
        return grant

    def get(self, grant_id: str) -> Optional[ActionGrant]:
        return self._grants.get(grant_id)

    def release_full_extra_slot(self, global_round: int, actor_id: str,
                                source_id: str) -> bool:
        """同父仲裁撤换：释放该轮该 actor 的 full-extra 名额（仅当仍登记为该来源）。"""
        by_round = self._full_extra_by_round.get(global_round, {})
        if by_round.get(actor_id) == source_id:
            del by_round[actor_id]
            return True
        return False

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

    def members(self) -> List[str]:
        """返回稳定快照，供 R0 只读资格清理。"""
        return list(self._queue)


class ActionSystem:
    """M9 行动系统门面：SP、关注额度、槽位收尾、派发与三源仲裁。"""

    def __init__(self, grant_ledger: Optional[GrantLedger] = None) -> None:
        self.ledger = grant_ledger or GrantLedger()
        self.queue = PublicPerformanceQueue()
        self.sp: Dict[str, int] = {}
        self._attention_used: Dict[int, set[str]] = {}
        self._ready_to_register: List[str] = []
        self._public_holder_by_round: Dict[int, Optional[str]] = {}
        self._public_assignment_done: set[int] = set()
        self._pending_full_extra: List[ActionGrant] = []
        self._current_grant: Optional[ActionGrant] = None
        self._performance_actor_id: Optional[str] = None
        self._performance_kind: Optional[str] = None
        self._slots: Dict[str, SlotOutcome] = {}
        # 三章制完结条接线（arc RFC v0.1；gate.ensure_state_mechanisms 注入）
        self.arc_ledger: Any = None
        self._arc_state: Any = None

    def attach_arc(self, ledger: Any, game_state: Any) -> None:
        """注入 ChapterLedger 与 game_state（幂等）。"""
        self.arc_ledger = ledger
        self._arc_state = game_state

    # ── SP ──
    def get_sp(self, actor_id: str) -> int:
        return self.sp.get(actor_id, 0)

    def register_player(self, actor_id: str) -> None:
        """登记玩家级资源；M9 开局 SP=1，重复登记不覆盖现有进度。"""
        self.sp.setdefault(actor_id, 1)

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

    def on_actor_exit(self, actor_id: str, *, clear_sp: bool = True) -> None:
        """死亡/离开普通层的公共清理；重复调用保持幂等。"""
        if clear_sp:
            self.set_sp(actor_id, 0)
        self.queue.remove_permanently(actor_id)
        for round_num, holder in list(self._public_holder_by_round.items()):
            if holder == actor_id:
                self._public_holder_by_round[round_num] = None
        self._ready_to_register = [
            pid for pid in self._ready_to_register if pid != actor_id]
        self._pending_full_extra = [
            grant for grant in self._pending_full_extra
            if grant.actor_id != actor_id]
        self.finalize_actor_grants(actor_id)

    def finalize_actor_grants(self, actor_id: str) -> None:
        """将死亡 actor 已发行但未执行的 grant 以 suppressed 幂等收尾。"""
        for grant in self.ledger._grants.values():
            if grant.actor_id != actor_id:
                continue
            outcome = self._slots.get(grant.grant_id)
            if outcome is not None and outcome.slot_resolved:
                continue
            if outcome is None:
                self._slots[grant.grant_id] = SlotOutcome(
                    slot_assigned=True, grant_id=grant.grant_id)
            self.resolve_slot(
                grant.grant_id, kind="suppressed", suppressed=True)

    # ── 关注额度（每轮关注额度，读 m9_system，[待风洞]）──
    def attention_quota(self) -> int:
        return int(bget("m9_system", "action", "attention_per_round", default=1))

    def can_attend(self, global_round: int, actor_id: str) -> bool:
        used = self._attention_used.get(global_round, set())
        return actor_id not in used and self.get_sp(actor_id) < SP_MAX

    def mark_attention(self, global_round: int, actor_id: str) -> bool:
        """登记一次玩家级关注并推进 SP；SP2 不消耗本轮额度。"""
        if not self.can_attend(global_round, actor_id):
            return False
        self._attention_used.setdefault(global_round, set()).add(actor_id)
        before = self.get_sp(actor_id)
        self.set_sp(actor_id, before + 1)
        if before < SP_MAX and self.get_sp(actor_id) == SP_MAX \
                and actor_id not in self._ready_to_register:
            self._ready_to_register.append(actor_id)
        return True

    def drain_ready_to_register(self) -> List[str]:
        ready = list(self._ready_to_register)
        self._ready_to_register.clear()
        return ready

    # ── 报名/关注窗口：R0 报名 → 分配公演位（FIFO）──
    def register_performance(self, actor_id: str, global_round: int) -> bool:
        """R0 报名窗口：SP≥2 才可报名；升 SP 后最早下轮报名。"""
        if self.get_sp(actor_id) < SP_PUBLIC_COST:
            return False
        self.queue.enqueue(actor_id)
        return True

    def begin_round(self, global_round: int) -> None:
        """打开新一轮的公演分配窗口；关注额度按轮号天然隔离。"""
        self._public_holder_by_round.pop(global_round, None)
        self._public_assignment_done.discard(global_round)
        if self.arc_ledger is not None and self._arc_state is not None:
            self.arc_ledger.scan(self._arc_state)

    def allocate_public_slot(self, global_round: int, eligibility=None) -> Optional[str]:
        """R0 固化唯一公演位；原队首失效时本轮为空，绝不递补。

        登台优先（arc RFC v0.1 §3.1）：未点亮第一章的合格候选按原 FIFO 序
        优先于已登台者；同档内保持 FIFO。
        """
        if global_round in self._public_assignment_done:
            return self._public_holder_by_round.get(global_round)
        self._public_assignment_done.add(global_round)

        def is_eligible(actor_id: str) -> bool:
            if self.get_sp(actor_id) < SP_PUBLIC_COST:
                return False
            return True if eligibility is None else bool(eligibility(actor_id))

        original_head = self.queue.head()
        for actor_id in self.queue.members():
            if not is_eligible(actor_id):
                self.queue.remove_permanently(actor_id)
        holder: Optional[str] = None
        if original_head is not None and self.queue.is_in_queue(original_head) \
                and is_eligible(original_head):
            eligible = [aid for aid in self.queue.members() if is_eligible(aid)]
            if self.arc_ledger is not None:
                holder = next(
                    (aid for aid in eligible
                     if not self.arc_ledger.has_debut(aid)), None)
            if holder is None:
                holder = eligible[0] if eligible else None
        # 原队首失效：本轮公演位保持为空（v0.8 §6.2 不递补），登台优先不破例
        self._public_holder_by_round[global_round] = holder
        return holder

    def assign_public_slot(self, global_round: int) -> Optional[str]:
        """分配本轮公演位：队首失效（SP<2）→ 永久移除、不递补。"""
        return self.allocate_public_slot(global_round)

    # ── 即演/公演派发 ──
    def dispatch_improvise(self, actor_id: str, global_round: int,
                           source_id: str = "improvise",
                           parent: Optional[ActionGrant] = None,
                           restricted: bool = True) -> Optional[ActionGrant]:
        """即演：−1 SP，不进公演队列，无独立冷却（审计 v0.1 场景 26）。"""
        if self._current_grant is not None \
                and not self._current_grant.allow_instant:
            return None
        if not self.spend_sp(actor_id, SP_IMPROVISE_COST):
            return None
        self.queue.remove_permanently(actor_id)
        self._performance_actor_id = actor_id
        self._performance_kind = "improvise"
        grant = self.ledger.issue(
            actor_id, "standard", source_id, global_round,
            parent=parent or self._current_grant, allow_instant=True,
            restricted=restricted)
        if grant is None:
            self.set_sp(actor_id, self.get_sp(actor_id) + SP_IMPROVISE_COST)
            self._performance_actor_id = None
            self._performance_kind = None
        return grant

    def dispatch_public(self, actor_id: str, global_round: int,
                        source_id: str = "public",
                        parent: Optional[ActionGrant] = None) -> Optional[ActionGrant]:
        """公演：预检（公演位）先于消费（−2 SP），位失则 SP 不动。"""
        if self._current_grant is not None \
                and not self._current_grant.allow_public:
            return None
        if self.assign_public_slot(global_round) != actor_id:
            return None
        if not self.spend_sp(actor_id, SP_PUBLIC_COST):
            return None
        self.queue.remove_permanently(actor_id)  # 公演位消费
        self._performance_actor_id = actor_id
        self._performance_kind = "public"
        grant = self.ledger.issue(
            actor_id, "standard", source_id, global_round,
            parent=parent or self._current_grant, allow_public=True)
        if grant is None:
            self.set_sp(actor_id, self.get_sp(actor_id) + SP_PUBLIC_COST)
            self._performance_actor_id = None
            self._performance_kind = None
        return grant

    def dispatch_full_extra(self, actor_id: str, global_round: int,
                            source_id: str,
                            parent: Optional[ActionGrant] = None,
                            allow_instant: bool = True,
                            allow_public: bool = False) -> Optional[ActionGrant]:
        """完整额外行动：三源白名单 + 每轮每人上限 + 深度闸（台账内部执行）。

        同父事件仲裁（v0.8 §3.2.8）：同一根行动内多个完整额外行动候选只保留
        最高优先级来源（T4 或跃 > 地火 > 负世），其余整体丢弃。仲裁必须先于
        台账每轮上限检查——否则高优先级候选会被上限直接拒发（"先成立者获得"
        只适用于不同父事件）。"""
        if self._current_grant is not None \
                and self._current_grant.kind == "full_extra":
            return None
        parent_grant = parent or self._current_grant
        parent_id = getattr(parent_grant, "grant_id", None) \
            if parent_grant is not None else None
        if parent_id is not None:
            replaced = None
            for existing in self._pending_full_extra:
                if getattr(existing, "kind", "") != "full_extra":
                    continue  # 仲裁只针对完整额外行动候选，不动受限追加
                if getattr(existing, "parent_grant_id", None) == parent_id \
                        and existing.source_id != source_id:
                    winner = self.pick_full_extra_candidate(
                        [existing.source_id, source_id])
                    if winner == existing.source_id:
                        return None  # 新候选低优先级：整体丢弃
                    replaced = existing
                    break
            if replaced is not None:
                self._pending_full_extra.remove(replaced)
                self.ledger.release_full_extra_slot(
                    global_round, actor_id, replaced.source_id)
        grant = self.ledger.issue(
            actor_id, "full_extra", source_id, global_round,
            parent=parent_grant,
            allow_instant=allow_instant, allow_public=allow_public)
        if grant is not None:
            self._pending_full_extra.append(grant)
        return grant

    def issue_standard(self, actor_id: str, global_round: int, *,
                       allow_instant: bool = True,
                       allow_public: bool = True,
                       restricted: bool = False) -> ActionGrant:
        """为 R1 合法 actor 创建唯一标准授予。"""
        grant = self.ledger.issue(
            actor_id, "standard", "round_standard", global_round,
            allow_instant=allow_instant, allow_public=allow_public,
            restricted=restricted)
        if grant is None:  # standard 永远允许；保留显式失败以免静默丢槽
            raise RuntimeError(f"cannot issue standard grant for {actor_id}")
        return grant

    def dispatch_restricted_followup(self, actor_id: str, global_round: int,
                                     source_id: str) -> Optional[ActionGrant]:
        """受限追加动作（G1 完全燃烧 §2.3）：标准行动结算后紧跟一个仅限
        move/attack 的受限根行动；不重新开放 T0、不授即演/公演、depth=1。"""
        if self._current_grant is not None \
                and self._current_grant.kind == "full_extra":
            return None
        grant = self.ledger.issue(
            actor_id, "restricted_followup", source_id, global_round,
            parent=self._current_grant,
            allow_instant=False, allow_public=False, restricted=True)
        if grant is not None:
            self._pending_full_extra.append(grant)
        return grant

    def begin_grant(self, grant: ActionGrant) -> None:
        self._current_grant = grant
        self._performance_actor_id = None
        self._performance_kind = None

    def end_grant(self, grant: ActionGrant) -> None:
        if self._current_grant is grant:
            self._current_grant = None
            self._performance_actor_id = None
            self._performance_kind = None

    @property
    def current_grant(self) -> Optional[ActionGrant]:
        return self._current_grant

    @property
    def performance_actor_id(self) -> Optional[str]:
        return self._performance_actor_id

    @property
    def performance_kind(self) -> Optional[str]:
        """当前 grant 内已实际完成的演出类型：improvise / public。"""
        return self._performance_kind

    def drain_pending_full_extra(self) -> List[ActionGrant]:
        pending = list(self._pending_full_extra)
        self._pending_full_extra.clear()
        return pending

    def pick_full_extra_candidate(self, available_sources: List[str]) -> Optional[str]:
        """同轮多候选仲裁：只保留最高优先级一个，其余整体丢弃。"""
        for src in FULL_EXTRA_SOURCES:
            if src in available_sources:
                return src
        return None

    # ── 槽位收尾（统一 finalization）──
    def assign_slot(self, actor_id: str,
                    grant: Optional[ActionGrant] = None) -> str:
        active = grant or self._current_grant
        slot_id = active.grant_id if active is not None \
            else f"{actor_id}:{len(self._slots)}"
        self._slots[slot_id] = SlotOutcome(
            slot_assigned=True,
            grant_id=active.grant_id if active is not None else slot_id,
        )
        return slot_id

    def resolve_slot(self, slot_id: str, *, root_action: bool = False,
                     kind: str = "action_performed", suppressed: bool = False,
                     voluntary_forfeit: bool = False,
                     performance_performed: bool = False) -> None:
        """统一收尾：所有槽（标准/控制消费/full-extra/aid_rest）都必须经此写出
        slot_resolved 与 resolution_kind。"""
        outcome = self._slots.get(slot_id)
        if outcome is None:
            return
        outcome.slot_resolved = True
        outcome.resolution_kind = kind if kind in RESOLUTION_KINDS else "action_performed"
        outcome.root_action_performed = root_action
        outcome.performance_performed = performance_performed
        outcome.suppressed = suppressed
        outcome.voluntary_forfeit = voluntary_forfeit
        # 三章制完结条实况挂钩（arc RFC v0.1）
        if self.arc_ledger is not None:
            grant = self.ledger.get(outcome.grant_id)
            actor_id = grant.actor_id if grant is not None else None
            global_round = grant.global_round if grant is not None else 0
            if performance_performed and self._performance_kind == "public" \
                    and actor_id is not None:
                self.arc_ledger.on_public_performance(actor_id, global_round)
            if grant is not None and grant.kind == "full_extra" and root_action:
                self.arc_ledger.mark_full_extra_round(actor_id, global_round)

    def outcome(self, slot_id: str) -> Optional[SlotOutcome]:
        return self._slots.get(slot_id)

    def reset(self) -> None:
        self.ledger.reset()
        self.queue = PublicPerformanceQueue()
        self.sp.clear()
        self._attention_used.clear()
        self._ready_to_register.clear()
        self._public_holder_by_round.clear()
        self._public_assignment_done.clear()
        self._pending_full_extra.clear()
        self._current_grant = None
        self._performance_actor_id = None
        self._performance_kind = None
        self._slots.clear()
        self.arc_ledger = None
        self._arc_state = None
