"""M9 警察/T6 机制核心（profile: m9-rfc，警察/T6 重置合同 v0.3）。

- 案件驱动：案件台账（报案人/被报人/证据/阶段）；举报前检失败不耗证据/槽。
- 固定警力：警力=固定编制（读 m9_system.police），不再随玩家加入扩张；
  队长唯一（选举/威信）。
- 掩体：警察单位掩体吸收（A/H 两阶段中的 A 阶段来源之一）。
- 警察局停机：停机后警察只保留中立 NPC 身份（不执法、不保护、不办案）。
- T6 配装：T6 好市民按配装表获得装备（读 m9_talents_extended.t6）。

数值全读 balance.json（[待风洞]），不设第二信源。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from engine.balance import get as bget


def _psys(key: str, default):
    return bget("m9_system", "police", key, default=default)


def _t6(key: str, default):
    return bget("m9_talents_extended", "t6", key, default=default)


@dataclass
class CaseRecord:
    case_id: str = ""
    reporter_id: str = ""
    suspect_id: str = ""
    evidence: int = 0
    phase: str = "reported"          # reported / verified / closed
    lead: bool = False               # 案件线索（唯一通缉相关）


class PoliceStation:
    """案件驱动的警察局：固定警力 + 队长 + 停机。"""

    def __init__(self) -> None:
        self.captain_id: Optional[str] = None
        self.authority: int = 0
        self.permanently_disabled: bool = False   # 警察局停机
        self.cases: List[CaseRecord] = []
        self._roster: List[str] = []              # 固定警力编制（unit ids）
        self._next_case = 0

    def fixed_roster_size(self) -> int:
        return int(_psys("fixed_roster", 3))

    def ensure_roster(self) -> List[str]:
        while len(self._roster) < self.fixed_roster_size():
            self._next_case += 1
            self._roster.append(f"unit{self._next_case}")
        return list(self._roster)

    def is_disabled(self) -> bool:
        return self.permanently_disabled

    def shut_down(self) -> None:
        """警察局停机：只保留中立 NPC 身份。"""
        self.permanently_disabled = True
        self.captain_id = None
        self.cases.clear()

    # ── 案件驱动 ──
    def file_case(self, reporter_id: str, suspect_id: str,
                  evidence: int = 1) -> Optional[CaseRecord]:
        """报案：预检先于消费——停机/无报案权/无证据不建档不耗证据。"""
        if self.is_disabled():
            return None
        if evidence < 1:
            return None
        self._next_case += 1
        case = CaseRecord(case_id=f"c{self._next_case}",
                          reporter_id=reporter_id, suspect_id=suspect_id,
                          evidence=evidence, phase="reported")
        self.cases.append(case)
        return case

    def verify_case(self, case_id: str, lead_ok: bool = True) -> bool:
        """验证案件（唯一通缉 lead 机制）。"""
        case = self.find_case(case_id)
        if case is None or case.phase != "reported":
            return False
        case.lead = lead_ok
        case.phase = "verified"
        return True

    def find_case(self, case_id: str) -> Optional[CaseRecord]:
        return next((c for c in self.cases if c.case_id == case_id), None)

    def close_case(self, case_id: str) -> None:
        case = self.find_case(case_id)
        if case is not None:
            case.phase = "closed"

    def open_cases(self) -> List[CaseRecord]:
        return [c for c in self.cases if c.phase in ("reported", "verified")]


class CoverSystem:
    """掩体：警察/单位掩体在 A 阶段吸收（结算合同 A/H 两阶段）。"""

    def __init__(self) -> None:
        self._cover: Dict[str, int] = {}   # unit_id → 掩体耐久

    def grant(self, unit_id: str, durability: int) -> None:
        self._cover[unit_id] = self._cover.get(unit_id, 0) + max(0, durability)

    def absorb(self, unit_id: str, amount: int) -> int:
        """A 阶段吸收；返回剩余要进 H 阶段的伤害。"""
        remaining = self._cover.get(unit_id, 0) - max(0, amount)
        if remaining <= 0:
            self._cover.pop(unit_id, None)
            return max(0, -remaining)
        self._cover[unit_id] = remaining
        return 0

    def durability(self, unit_id: str) -> int:
        return self._cover.get(unit_id, 0)


def t6_equipment_set() -> List[str]:
    """T6 好市民配装表（读 m9_talents_extended.t6，[待风洞]）。"""
    return list(_t6("equipment", ["防毒面具", "热成像仪"]))
