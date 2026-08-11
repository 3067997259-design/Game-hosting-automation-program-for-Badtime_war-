"""M9 G0 世界援助机制（profile: m9-rfc，世界援助合同 v0.1）。

- 激活门槛：本局存在至少一笔有效押注后「昨日的同伴」才生效；开局普惠窗口不触发。
- 黑马快照：每个 R0 开市结束、tranche/转仓落定后重算；轮中死亡不改快照。
- 星野追演：每名黑马每全局轮第一次合法攻击根命中后，对同一目标追加基础近战追演；
  追演是根行动内限定步骤（非 ActionGrant）；命中时目标获得「震荡」（标准受限菜单，
  同级不叠加、高位控制不覆盖）；不设压制版本。
- 绫音急救：每个 R4，每名存活黑马所在地点所有单位回复 world_poem_g0_heal 点 HP；
  不分敌我；同地点同 R4 至多一次；来源 WORLD_RULE/world_poem_g0_aid，无 player
  provider（不给 G4 火种、不占魂援额度、不触发援助 PP、不适用 G0×G7 +20%）。
- G0 本人不提供/结算/获得任何 PP/资源/决策权（纯机制遗产）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from engine.balance import get as bget

WORLD_RULE_SOURCE_ID = "world_poem_g0_aid"
WORLD_RULE_SOURCE_KIND = "WORLD_RULE"


def heal_value() -> float:
    """绫音急救每 R4 回复量（首轮 1，[待风洞]，DOC-048）。"""
    return float(bget("m9_system", "pp", "world_poem_g0_heal", default=1))


class WorldPoemAid:
    """「昨日的同伴」：G0 世界援助的机制状态机。"""

    def __init__(self, has_g0_in_pool: bool, pp: Any) -> None:
        self.has_g0_in_pool = has_g0_in_pool
        self.pp = pp                          # m9.pp.PPLedger（黑马快照信源）
        self.activated = False                # 激活门槛
        self._attack_followup_used: Dict[str, int] = {}  # 黑马 → 全局轮次
        self._r4_healed: Dict[int, set] = {}  # 全局轮 → 已治疗地点集合

    def recompute(self, global_round: int, alive_ids: List[str],
                  dead_ids: List[str]) -> None:
        """每个 R0 开市结束、tranche 与转仓落定后调用：重算黑马快照与激活门槛。
        黑马快照与 G0 无关（无 G0 局「此诗，献予世界」同样重算）。"""
        self.pp.recompute_blackhorse(alive_ids, dead_ids)
        if not self.has_g0_in_pool:
            return
        if self.pp.has_active_bet():
            self.activated = True

    # ── 星野追演 ──
    def should_followup_attack(self, blackhorse_id: str, global_round: int) -> bool:
        """每名黑马每全局轮第一次合法攻击根命中后触发追演。"""
        if not self.activated:
            return False
        if not self.pp.is_blackhorse(blackhorse_id):
            return False
        if self._attack_followup_used.get(blackhorse_id, -1) >= global_round:
            return False
        self._attack_followup_used[blackhorse_id] = global_round
        return True

    def followup_punch_raw(self, base_damage: float) -> float:
        """追演基础近战：无合法近战武器时退化为拳击（参照 G4 焚诏 challenge_punch）。"""
        return max(1.0, float(base_damage))

    # ── 绫音急救 ──
    def heal_amount(self) -> float:
        return heal_value()

    def can_heal_location(self, global_round: int, location: str) -> bool:
        """同一地点同一 R4 至多结算一次绫音急救（多名黑马共享不叠加）。"""
        healed = self._r4_healed.setdefault(global_round, set())
        if location in healed:
            return False
        healed.add(location)
        return True

    def source_tag(self) -> tuple:
        return (WORLD_RULE_SOURCE_KIND, WORLD_RULE_SOURCE_ID)

    def is_g0_beneficiary(self, pid: str) -> bool:
        """G0 自己存活且无押注时也可以是接受者（同伴也守护她）。"""
        return self.pp.is_blackhorse(pid)

    def reset(self) -> None:
        self.activated = False
        self._attack_followup_used.clear()
        self._r4_healed.clear()
