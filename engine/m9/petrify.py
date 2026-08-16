"""M9 统一石化/尘世之锁生命周期（profile: m9-rfc，T3/T7 v0.3 §1.2 石化口径）。

- 施加：标记 PETRIFIED + actor.is_petrified；持续 `petrify_duration` 个未来 R4 tick
  （建立轮 R4 不 tick，从建立后的第一个 R4 开始计数）；
- 被动解除：统一有效伤害口径（H≥1 入 HP 或击破护甲/掩体）第一次给「摇晃」，
  第二次解除；尘世之锁无被动解除；
- T0 选择：forfeit（保持石化、不获 SP、消耗槽）或同槽至多两次挣脱（各 1 SP、
  50% 判定；同一全局轮次上限两次，额外槽不重置）；
- 尘世之锁（群星诗升级）：只能被锁者自己 T0 挣脱或 forfeit 到期；重施刷新——
  同一名 T3 再次天星命中把剩余时间刷新为基础持续时间；锁不消失、不退化；
- 幂等：重复施加刷新持续（同源）或忽略（异源已有锁）；移除清标记。
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from engine.balance import get as bget

PETRIFY_MARKER = "PETRIFIED"


def petrify_duration() -> int:
    """基础石化持续轮数（未来 R4 tick 数；[待风洞]）。"""
    return int(bget("m9_talents_extended", "t3", "petrify_duration", default=2))


def break_success_probability() -> float:
    """挣脱成功率（v0.3 冻结语义 50%；数值外提 [待风洞]）。"""
    return float(bget(
        "m9_talents_extended", "t3", "break_success_probability", default=0.5))


@dataclass
class PetrifyState:
    """单个 actor 的石化账目。"""

    actor_id: str
    remaining_ticks: int          # 剩余未来 R4 tick 数
    start_round: int              # 建立轮（其 R4 不 tick）
    source_pid: str               # 施加者（尘世之锁刷新判定用）
    locked: bool = False          # 尘世之锁：无被动解除
    shake_count: int = 0          # 有效伤害摇晃计数（0/1；第二次解除）


class PetrifyRegistry:
    """挂在 game_state.m9_petrify 的系统级石化台账。"""

    def __init__(self) -> None:
        self._states: Dict[str, PetrifyState] = {}
        self._break_used: Dict[int, Dict[str, int]] = {}  # 轮 → 玩家 → 尝试次数

    # ── 查询 ──
    def is_petrified(self, actor_id: str) -> bool:
        return actor_id in self._states

    def state_of(self, actor_id: str) -> Optional[PetrifyState]:
        return self._states.get(actor_id)

    def is_locked(self, actor_id: str) -> bool:
        st = self._states.get(actor_id)
        return st is not None and st.locked

    # ── 施加/刷新 ──
    def apply(self, game_state: Any, actor: Any, *,
              source_pid: str,
              duration: Optional[int] = None,
              locked: bool = False) -> None:
        """施加石化；同源重复命中刷新剩余时间，异源锁定目标不覆盖。"""
        actor_id = getattr(actor, "player_id", getattr(actor, "unit_id", ""))
        if not actor_id:
            return
        ticks = duration if duration is not None else petrify_duration()
        existing = self._states.get(actor_id)
        if existing is not None and existing.locked:
            if existing.source_pid == source_pid:
                self._refresh(actor_id, ticks)
            return
        self._states[actor_id] = PetrifyState(
            actor_id=actor_id, remaining_ticks=ticks,
            start_round=getattr(game_state, "current_round", 1),
            source_pid=source_pid, locked=locked)
        self._mark(game_state, actor, True)

    def _refresh(self, actor_id: str, duration: int) -> None:
        """尘世之锁重施刷新：剩余时间重置为基础持续时间（T3/T7 v0.3 §1.2）。"""
        st = self._states[actor_id]
        st.remaining_ticks = duration

    def convert_to_lock(self, source_pid: str) -> int:
        """群星诗：该 T3 施加的全部现存石化升级为尘世之锁。返回升级数。"""
        upgraded = 0
        for st in self._states.values():
            if st.source_pid == source_pid and not st.locked:
                st.locked = True
                st.shake_count = 0
                upgraded += 1
        return upgraded

    # ── 移除 ──
    def remove(self, game_state: Any, actor: Any) -> None:
        actor_id = getattr(actor, "player_id", getattr(actor, "unit_id", ""))
        self._states.pop(actor_id, None)
        self._mark(game_state, actor, False)

    def remove_by_id(self, actor_id: str) -> None:
        self._states.pop(actor_id, None)

    # ── 统一有效伤害：摇晃 → 第二次解除 ──
    def on_effective_hit(self, game_state: Any, actor: Any) -> None:
        """结算 v0.3 §6.7 口径：H≥1 或击破护甲/掩体视为有效伤害。"""
        actor_id = getattr(actor, "player_id", getattr(actor, "unit_id", ""))
        st = self._states.get(actor_id)
        if st is None or st.locked:
            return
        st.shake_count += 1
        if st.shake_count >= 2:
            self.remove(game_state, actor)

    def shake_count(self, actor_id: str) -> int:
        st = self._states.get(actor_id)
        return st.shake_count if st is not None else 0

    # ── R4 tick（建立轮 R4 不 tick）──
    def on_r4_tick(self, game_state: Any, round_num: int) -> None:
        expired = []
        for actor_id, st in self._states.items():
            if st.start_round >= round_num:
                continue  # 建立轮 R4 不 tick
            st.remaining_ticks -= 1
            if st.remaining_ticks <= 0:
                expired.append(actor_id)
        for actor_id in expired:
            actor = self._actor_of(game_state, actor_id)
            if actor is not None:
                self.remove(game_state, actor)
            else:
                self.remove_by_id(actor_id)

    # ── T0 挣脱（同槽至多两次；同轮上限两次）──
    def break_attempts_left(self, round_num: int, actor_id: str) -> int:
        used = self._break_used.get(round_num, {}).get(actor_id, 0)
        return max(0, 2 - used)

    def _record_break(self, round_num: int, actor_id: str) -> None:
        used = self._break_used.setdefault(round_num, {}).get(actor_id, 0)
        self._break_used[round_num][actor_id] = used + 1

    def attempt_break(self, game_state: Any, actor: Any,
                      round_num: int) -> bool:
        """一次 50% 挣脱判定；成功返回 True 并移除石化。"""
        actor_id = getattr(actor, "player_id", getattr(actor, "unit_id", ""))
        self._record_break(round_num, actor_id)
        if random.random() < break_success_probability():
            self.remove(game_state, actor)
            return True
        return False

    def reset_round(self, round_num: int) -> None:
        """R0 清理过期轮次的挣脱计数（防泄漏）。"""
        self._break_used = {r: used for r, used in self._break_used.items()
                            if r >= round_num - 1}

    # ── 辅助 ──
    @staticmethod
    def _actor_of(game_state: Any, actor_id: str) -> Optional[Any]:
        get_actor = getattr(game_state, "get_actor", game_state.get_player)
        actor = get_actor(actor_id)
        if actor is not None:
            return actor
        shadows = getattr(game_state, "m9_shadows", {})
        return shadows.get(actor_id)

    @staticmethod
    def _mark(game_state: Any, actor: Any, value: bool) -> None:
        actor_id = getattr(actor, "player_id", getattr(actor, "unit_id", ""))
        actor.is_petrified = value
        markers = getattr(game_state, "markers", None)
        if markers is None:
            return
        try:
            if value:
                if not markers.has(actor_id, PETRIFY_MARKER):
                    markers.add(actor_id, PETRIFY_MARKER)
            else:
                if markers.has(actor_id, PETRIFY_MARKER):
                    markers.on_petrify_recover(actor_id)
        except Exception:
            pass

    def reset(self) -> None:
        self._states.clear()
        self._break_used.clear()
