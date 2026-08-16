"""M9 T7 全局一次性保险伏笔（profile: m9-rfc，T3/T7 迁移合同 v0.3 §2.2）。

- 全局唯一：全场同一时刻至多一个「保险伏笔」；挂载后不得覆盖、不得重挂；
- 兑现后 T7 永久落幕：标记消耗，不能再挂载第二个伏笔；
- 保险在 T7 持有者死亡后继续存在（伏笔独立于 T7 生死）；
- 兑现、落幕与标记清理只结算一次（幂等）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class InsuranceRecord:
    """一个已挂载的保险伏笔。"""

    source_pid: str        # 挂载者（T7 持有者）
    target_pid: str        # 被保人
    cashed_in: bool = False


class InsuranceRegistry:
    """挂在 game_state.m9_insurance 的系统级保险台账。"""

    def __init__(self) -> None:
        self._record: Optional[InsuranceRecord] = None
        self._retired: bool = False

    # ── 查询 ──
    def record(self) -> Optional[InsuranceRecord]:
        return self._record

    def is_mounted(self) -> bool:
        return self._record is not None

    def is_retired(self) -> bool:
        return self._retired

    def is_cashed_in(self) -> bool:
        return self._record is not None and self._record.cashed_in

    def mounted_target(self) -> Optional[str]:
        return self._record.target_pid if self._record is not None else None

    # ── 挂载（全局唯一；不可覆盖、不可重挂）──
    def mount(self, source_pid: str, target_pid: str) -> bool:
        """挂载伏笔；已有伏笔或已落幕返回 False（预检先于任何消费）。"""
        if self._retired or self._record is not None:
            return False
        self._record = InsuranceRecord(source_pid=source_pid,
                                       target_pid=target_pid)
        return True

    # ── 兑现（被动触发；只结算一次）──
    def cash_in(self) -> Optional[InsuranceRecord]:
        """兑现：消耗伏笔并永久落幕。返回被兑现的记录；无伏笔/已兑现返回 None。"""
        if self._record is None or self._record.cashed_in:
            return None
        self._record.cashed_in = True
        self._retired = True
        return self._record

    def reset(self) -> None:
        self._record = None
        self._retired = False
