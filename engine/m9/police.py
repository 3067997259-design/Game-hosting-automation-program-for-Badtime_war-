"""M9 警察/T6 机制核心（profile: m9-rfc，警察/T6 重置合同 v0.3）。

- 案件驱动：案件台账（报案人/被报人/证据/阶段）；举报前检失败不耗证据/槽；
- 固定警力：警力=固定编制（读 m9_system.police），不再随玩家加入扩张；
  队长唯一（选举/威信）；警力伤亡不补充；
- R2 自动执法：有通缉时分配唯一执法 lead（可用警力最近地点，平局 id 升序）；
  R4 执法：lead 与目标同地点时自动攻击一次（每单位每轮至多一次）；
- 掩体：警察单位掩体吸收（A 阶段来源之一）；
- 警察局停机：停机后警察只保留中立 NPC 身份（不执法、不保护、不办案）；
- G3 结界挂起：通缉案件+lead 挂起，恢复后从挂起点继续；
- T6 配装：T6 好市民按配装白名单为存活警察整备装备（读 m9_talents_extended.t6）。

数值全读 balance.json（[待风洞]），不设第二信源。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from engine import world_clock
from engine.balance import get as bget
from engine.m9.text import m9_text


def _psys(key: str, default):
    return bget("m9_system", "police", key, default=default)


def _t6(key: str, default):
    return bget("m9_talents_extended", "t6", key, default=default)


# T6 配装白名单（v0.3 §5.3 冻结枚举；数值外提至 balance m9_talents_extended.t6）
T6_WEAPON_WHITELIST: tuple = tuple(_t6(
    "weapon_whitelist", default=["警棍", "高斯步枪", "魔法弹幕"]))
T6_ARMOR_WHITELIST: tuple = tuple(_t6(
    "armor_whitelist", default=["盾牌", "陶瓷护甲", "魔法护盾", "AT力场"]))

# 证据资格封闭清单（v0.3 §2.1）
EVIDENCE_VICTIM = "victim"                # 受害者
EVIDENCE_WITNESS = "witness"              # 同地点目击者
EVIDENCE_DETECTOR = "detector"            # 系统归因探测器
EVIDENCE_T6_CLUE = "t6_special_clue"      # T6 特别线索


@dataclass
class CaseRecord:
    case_id: str = ""
    reporter_id: str = ""
    suspect_id: str = ""
    evidence: int = 0
    phase: str = "reported"          # reported / verified / closed
    lead: bool = False               # 案件线索（唯一通缉相关）
    evidence_kind: str = ""          # 证据资格类别（封闭清单之一）


@dataclass
class M9PoliceUnit:
    """M9 警察单位（固定编制成员；非玩家 actor）。"""

    unit_id: str
    hp: int = 12
    max_hp: int = 12
    weapon_name: str = "警棍"
    armor: Any = None              # T6 整备的护甲（ArmorPiece）
    talent: Any = None             # player-like compatibility; police have no talent
    location: Optional[str] = None
    is_stunned: bool = False
    is_shocked: bool = False
    is_petrified: bool = False
    is_submerged: bool = False
    acted_this_round: bool = False   # 本轮已执行自动执法（每轮至多一次）

    _m9_police_actor = True

    @property
    def player_id(self) -> str:
        """Unified target id; police remain NPCs and never receive grants."""
        return f"police:{self.unit_id}"

    @property
    def name(self) -> str:
        return m9_text("police.unit_name", unit_id=self.unit_id)

    @property
    def inner_defense(self) -> Dict[str, int]:
        return {}

    def is_alive(self) -> bool:
        return self.hp > 0

    def is_disabled(self) -> bool:
        return (self.is_stunned or self.is_shocked
                or self.is_petrified or self.is_submerged)

    def is_active(self) -> bool:
        return self.is_alive() and not self.is_disabled()

    def is_on_map(self) -> bool:
        return self.is_alive() and self.location is not None


class PoliceStation:
    """案件驱动的 M9 警察局：固定警力 + 队长 + 通缉 + 自动执法。"""

    def __init__(self) -> None:
        self.captain_id: Optional[str] = None
        self.authority: int = 0
        self.permanently_disabled: bool = False   # 警察局停机
        self.suspended: bool = False              # G3 结界挂起（案件不推进）
        self.cases: List[CaseRecord] = []
        self.lead_id: Optional[str] = None        # 当前执法 lead
        self._roster: List[M9PoliceUnit] = []
        self._candidates: List[str] = []          # 队长候选队列（先到先得）
        self._next_case = 0
        self._cover: Dict[str, int] = {}          # unit_id → 掩体耐久
        self._cover_source: Dict[str, str] = {}   # 被保护 actor → 来源警察
        self._suspension_owner: Optional[str] = None

    # ── 固定编制 ──
    def fixed_roster_size(self) -> int:
        return int(_psys("fixed_roster", 3))

    def ensure_roster(
        self, initial_location: Optional[str] = None,
    ) -> List[M9PoliceUnit]:
        """幂等建立开局固定编制；可由 setup 指定初始生成地点。"""
        while len(self._roster) < self.fixed_roster_size():
            self._next_case += 1
            hp = int(_psys("unit_hp", 12))
            self._roster.append(M9PoliceUnit(f"unit{self._next_case}", hp=hp,
                                             max_hp=hp,
                                             location=initial_location))
        return self._roster

    def units(self) -> List[M9PoliceUnit]:
        return list(self._roster)

    def alive_units(self) -> List[M9PoliceUnit]:
        return [u for u in self._roster if u.is_alive()]

    def active_units(self) -> List[M9PoliceUnit]:
        return [u for u in self._roster if u.is_active()]

    def units_at(self, location: str) -> List[M9PoliceUnit]:
        return [u for u in self._roster
                if u.location == location and u.is_alive()]

    def get_unit(self, unit_id: str) -> Optional[M9PoliceUnit]:
        normalized = unit_id[len("police:"):] \
            if unit_id.startswith("police:") else unit_id
        return next((u for u in self._roster if u.unit_id == normalized), None)

    def is_disabled(self) -> bool:
        return self.permanently_disabled

    def shut_down(self, reason: str = "") -> None:
        """警察局停机：只保留中立 NPC 身份。"""
        self.permanently_disabled = True
        self.captain_id = None
        self.authority = 0
        self.cases.clear()
        self.lead_id = None
        self._candidates.clear()
        self._cover.clear()
        self._cover_source.clear()
        self.suspended = False
        self._suspension_owner = None
        self._record(reason)

    def _record(self, message: str) -> None:
        self.last_message = message

    # ── 世界时钟（M5 警察分级坠落：黄昏撤保护 / 终焉全停，v2.0 §3）──
    def _clock_state(self, game_state: Any = None) -> Any:
        """状态解析：显式参数 → set_state_ref 注入的 _state → None。"""
        if game_state is not None:
            return game_state
        return getattr(self, "_state", None)

    def _clock_cover_withdrawn(self, game_state: Any = None) -> bool:
        """黄昏起掩体保护撤销（m5_clock 关闭时恒 False=不撤）。"""
        state = self._clock_state(game_state)
        if state is None:
            return False
        return world_clock.active_value(state, "police_protection",
                                        default=True) is False

    def _clock_police_halted(self, game_state: Any = None) -> bool:
        """终焉起警察整体停摆（m5_clock 关闭时恒 False=不停摆）。"""
        state = self._clock_state(game_state)
        if state is None:
            return False
        return world_clock.active_value(state, "police_disabled",
                                        default=False) is True

    # ── 案件驱动 ──
    def open_wanted(self) -> Optional[CaseRecord]:
        """当前唯一通缉（verified 案件；无则 None）。"""
        return next((c for c in self.cases
                     if c.phase == "verified" and c.lead), None)

    def has_open_wanted(self) -> bool:
        return self.open_wanted() is not None

    def file_case(self, reporter_id: str, suspect_id: str,
                  evidence: int = 1,
                  evidence_kind: str = EVIDENCE_VICTIM) -> Optional[CaseRecord]:
        """报案：预检先于消费——停机/终焉停摆/无报案权/无证据/已有通缉不建档。"""
        if self.is_disabled() or self.suspended:
            return None
        if self._clock_police_halted():
            return None
        if evidence < 1:
            return None
        if self.has_open_wanted():
            return None
        self._next_case += 1
        case = CaseRecord(case_id=f"c{self._next_case}",
                          reporter_id=reporter_id, suspect_id=suspect_id,
                          evidence=evidence, phase="reported",
                          evidence_kind=evidence_kind)
        self.cases.append(case)
        return case

    def verify_case(self, case_id: str, lead_ok: bool = True) -> bool:
        """验证案件（唯一通缉 lead 机制）。"""
        case = self.find_case(case_id)
        if case is None or case.phase != "reported":
            return False
        case.lead = lead_ok
        case.phase = "verified"
        if not lead_ok:
            case.phase = "closed"
        return True

    def find_case(self, case_id: str) -> Optional[CaseRecord]:
        return next((c for c in self.cases if c.case_id == case_id), None)

    def close_case(self, case_id: str) -> None:
        case = self.find_case(case_id)
        if case is not None:
            case.phase = "closed"
            if self.open_wanted() is None:
                self.lead_id = None

    def close_current_wanted(self, reason: str = "") -> None:
        """结案（目标死亡/离场/解职/停机/终焉/律法诗覆盖）。"""
        wanted = self.open_wanted()
        if wanted is not None:
            wanted.phase = "closed"
        self.lead_id = None
        self._record(reason)

    def open_cases(self) -> List[CaseRecord]:
        return [c for c in self.cases if c.phase in ("reported", "verified")]

    # ── R2 自动执法：lead 分配/队长上任 ──
    def r2_tick(self, game_state: Any, round_num: int) -> List[str]:
        """每个 R2：分配/重分配 lead；队长候选上任。返回状态消息。"""
        if (self.is_disabled() or self.suspended
                or self._clock_police_halted(game_state)):
            return []
        msgs: List[str] = []
        # 队长上任（候选队列头部，R2 判定）
        if self.captain_id is None and self._candidates:
            pid = self._candidates[0]
            p = game_state.get_player(pid)
            if (p is not None and p.is_alive()
                    and not self._is_wanted_target(pid)
                    and not self._has_crime(game_state, pid)
                    and not getattr(getattr(p, "talent", None),
                                    "is_terror", False)):
                self.captain_id = pid
                self._candidates.pop(0)
                self.authority = int(_psys("authority_initial", 3))
                p.is_captain = True
                game_state.log_event("m9_captain", player=pid)
                msgs.append(m9_text("police.captain_appointed",
                                    name=p.name, authority=self.authority))
            else:
                self._candidates.pop(0)  # 失效候选自动让位
        # lead 分配
        wanted = self.open_wanted()
        if wanted is not None:
            lead = self._assign_lead(wanted.suspect_id, game_state)
            if lead is not None:
                msgs.append(m9_text("police.lead_assigned",
                                    lead_id=lead.unit_id,
                                    suspect_id=wanted.suspect_id))
        return msgs

    def _assign_lead(self, target_pid: str,
                     game_state: Any) -> Optional[M9PoliceUnit]:
        """最近地点可用警力；平局按 id 升序。"""
        target = game_state.get_player(target_pid)
        target_loc = getattr(target, "location", None) if target else None
        available = [u for u in self._roster if u.is_active()
                     and not u.acted_this_round]
        if not available:
            available = [u for u in self._roster if u.is_active()]
        if not available:
            self.lead_id = None
            return None

        def distance(u: M9PoliceUnit) -> int:
            if u.location == target_loc:
                return 0
            if u.location is None:
                return 999
            return 1  # 地图距离待风洞；v1 简化为同地点 0 / 异地 1

        best = min(available, key=lambda u: (distance(u), u.unit_id))
        self.lead_id = best.unit_id
        if best.location != target_loc:
            best.location = target_loc  # R2 lead 移动到目标当前地点
        return best

    def _is_wanted_target(self, pid: str) -> bool:
        wanted = self.open_wanted()
        return wanted is not None and wanted.suspect_id == pid

    @staticmethod
    def _has_crime(game_state: Any, pid: str) -> bool:
        records = getattr(getattr(game_state, "police", None),
                          "crime_records", {})
        return bool(records.get(pid))

    # ── R4 执法 ──
    def r4_enforcement(self, game_state: Any, round_num: int) -> List[str]:
        """R4 自动执法：lead 与目标同地点时攻击一次（每单位每轮至多一次）。"""
        if (self.is_disabled() or self.suspended
                or self._clock_police_halted(game_state)):
            return []
        wanted = self.open_wanted()
        if wanted is None:
            return []
        lead = self.get_unit(self.lead_id) if self.lead_id else None
        if lead is None or not lead.is_alive() or lead.is_disabled():
            return []
        target = game_state.get_player(wanted.suspect_id)
        if target is None or not target.is_alive():
            return []
        # Terror 排除在执法目标池外
        t_talent = getattr(target, "talent", None)
        if getattr(t_talent, "is_terror", False):
            return []
        if lead.acted_this_round:
            return []
        if lead.location != target.location:
            return []
        lead.acted_this_round = True
        dmg = self._enforcement_damage(lead)
        result = self._attack_player(game_state, lead, target, dmg)
        dealt = int(result.get("hp_damage", 0))
        game_state.log_event("m9_police_enforcement",
                             unit=lead.unit_id, target=wanted.suspect_id,
                             captain=self.captain_id,
                             damage=dealt, weapon=lead.weapon_name)
        return [m9_text("police.enforcement_hit", unit_id=lead.unit_id,
                        target_name=target.name, damage=dealt)]

    @staticmethod
    def _enforcement_damage(unit: Optional[M9PoliceUnit] = None) -> int:
        """Use the unit's real equipped weapon; baton balance is fallback only."""
        if unit is not None:
            from models.equipment import make_weapon
            weapon = make_weapon(unit.weapon_name)
            if weapon is not None:
                return max(0, int(round(float(weapon.get_effective_damage()))))
        return int(_psys("baton_damage", 4))

    def _attack_player(self, game_state: Any, unit: M9PoliceUnit,
                       target: Any, dmg: int) -> Dict[str, Any]:
        from engine.m9.combat import resolve_damage
        attacker = None  # 无玩家攻击者：不产生击杀归属
        from models.equipment import make_weapon
        weapon = make_weapon(unit.weapon_name)
        if weapon is not None:
            return resolve_damage(
                attacker, target, weapon=weapon, game_state=game_state,
                source_kind="m9_police_enforcement")
        return resolve_damage(
            attacker, target, weapon=None, game_state=game_state,
            raw_damage_override=dmg, damage_attribute_override="普通",
            source_kind="m9_police_enforcement")

    def attack_unit(self, attacker: Any, unit_id: str, weapon: Any = None, *,
                    damage_multiplier: float = 1.0,
                    bonus_damage: float = 0.0,
                    raw_damage_override: Optional[float] = None,
                    damage_attribute_override: Optional[str] = None,
                    armor_pierce_factor: float = 1.0,
                    source_kind: str = "m9_police_attack") -> Dict[str, Any]:
        """Resolve an ordinary player attack against a police NPC.

        The hit uses the shared numeric-v2 armor formula. Police cover is an
        A-phase object and therefore absorbs before the unit's own armor/HP.
        """
        unit = self.get_unit(unit_id)
        if unit is None or not unit.is_alive():
            return {"success": False, "reason": "police_target_invalid"}
        if raw_damage_override is None:
            if weapon is None:
                return {"success": False, "reason": "police_weapon_missing"}
            raw_source = float(weapon.get_effective_damage())
        else:
            raw_source = float(raw_damage_override)
        raw = max(0, int(round(raw_source * damage_multiplier + bonus_damage)))
        raw_after_cover = self.absorb_cover(unit.unit_id, raw)
        if raw_after_cover <= 0:
            damage = 0
            broken: List[str] = []
            defense = 0
        else:
            from combat.numeric_v2 import resolve_hit
            attr = (damage_attribute_override
                    or getattr(getattr(weapon, "attribute", None), "value", "普通"))
            hit = resolve_hit(
                unit, raw_after_cover, attr,
                pierce_factor=float(armor_pierce_factor))
            damage = int(hit["damage"])
            broken = list(hit["broken"])
            defense = int(hit["defense"])
        before = unit.hp
        unit.hp = max(0, unit.hp - damage)
        if unit.hp <= 0:
            self._cover.pop(unit.unit_id, None)
            self._cover_source.pop(unit.unit_id, None)
            if self.lead_id == unit.unit_id:
                self.lead_id = None
        result = {
            "success": True,
            "raw_damage": raw,
            "final_damage": damage,
            "hp_damage": before - unit.hp,
            "target_hp": unit.hp,
            "target_hp_before": before,
            "armor_broken": bool(broken),
            "armor_hit": broken[0] if broken else None,
            "killed": unit.hp <= 0,
            "details": [
                m9_text("police.attack_detail", raw=raw, defense=defense,
                        damage=damage)
                + (m9_text("police.attack_cover_absorbed")
                   if raw_after_cover < raw else "")
            ],
        }
        state = getattr(self, "_state", None)
        if state is not None:
            state.log_event(
                "m9_police_attacked", attacker=getattr(attacker, "player_id", None),
                target=unit.player_id, weapon=getattr(weapon, "name", ""),
                damage=result["hp_damage"], killed=result["killed"],
                source_kind=source_kind)
            # 袭警是犯罪事实；停机只关闭执法推进，不抹去事实。
            police_engine = getattr(state, "police_engine", None)
            if police_engine is not None:
                police_engine.check_and_record_crime(
                    getattr(attacker, "player_id", ""), "伤害警察")
        return result

    # ── 轮次重置 ──
    def r0_tick(self, game_state: Any, round_num: int) -> None:
        """R0：重置执法配额与掩体再验证。"""
        for u in self._roster:
            u.acted_this_round = False
        self.refresh_cover(game_state)

    # ── 掩体（§3.3：每单位至多一层；更大耐久替换）──
    def cover_durability(self, unit_id: str) -> int:
        return self._cover.get(unit_id, 0)

    def grant_cover(self, unit_id: str, durability: int) -> None:
        if (self.is_disabled() or self._clock_cover_withdrawn()
                or self._clock_police_halted()):
            return
        self._cover[unit_id] = max(self._cover.get(unit_id, 0),
                                   max(0, durability))

    def grant_player_cover(self, player_id: str, source_unit_id: str,
                           durability: int) -> None:
        if (self.is_disabled() or self._clock_cover_withdrawn()
                or self._clock_police_halted()):
            return
        if durability >= self._cover.get(player_id, 0):
            self._cover[player_id] = max(0, durability)
            self._cover_source[player_id] = source_unit_id

    def absorb_cover(self, unit_id: str, amount: int) -> int:
        """掩体吸收；返回剩余要进 H 阶段的伤害。"""
        remaining = self._cover.get(unit_id, 0) - max(0, amount)
        if remaining <= 0:
            self._cover.pop(unit_id, None)
            self._cover_source.pop(unit_id, None)
            return max(0, -remaining)
        self._cover[unit_id] = remaining
        return 0

    def refresh_cover(self, game_state: Any) -> None:
        """R0 再验证：同地点存活可行动警力给非通缉单位一层掩体。"""
        self._cover.clear()
        self._cover_source.clear()
        if (self.is_disabled() or self.suspended
                or self._clock_cover_withdrawn(game_state)
                or self._clock_police_halted(game_state)):
            return  # 黄昏起掩体撤销（清空后不再授予）；终焉同理
        wanted = self.open_wanted()
        wanted_suspect = wanted.suspect_id if wanted is not None else None
        cover_value = int(_psys("cover_durability", 2))
        for u in self._roster:
            if not u.is_active() or u.location is None:
                continue
            for pid in game_state.player_order:
                p = game_state.get_player(pid)
                if p is None or not p.is_alive():
                    continue
                if p.location != u.location:
                    continue
                from engine.m9.talents.g3 import attack_crosses_active_barrier
                if attack_crosses_active_barrier(game_state, u, p):
                    continue
                if pid == wanted_suspect or pid == self.captain_id:
                    continue  # 通缉目标与被执法对象不提供保护（被保护方也不得被保护）
                self.grant_player_cover(pid, u.unit_id, cover_value)

    def player_cover(self, player_id: str) -> int:
        """玩家当前掩体耐久（警察掩体）。黄昏/终焉时钟门控直接归零。"""
        if self._clock_cover_withdrawn() or self._clock_police_halted():
            return 0
        source_id = self._cover_source.get(player_id)
        state = getattr(self, "_state", None)
        if source_id is not None and state is not None:
            source = self.get_unit(source_id)
            player = state.get_player(player_id)
            if (source is None or not source.is_active() or player is None
                    or source.location != getattr(player, "location", None)
                    or self._is_wanted_target(player_id)):
                self._cover.pop(player_id, None)
                self._cover_source.pop(player_id, None)
        return self._cover.get(player_id, 0)

    def absorb_player_cover(self, player_id: str, amount: int) -> int:
        return self.absorb_cover(player_id, amount)

    # ── 队长（候选/指挥/威信）──
    def apply_captain(self, player_id: str) -> bool:
        """注册队长候选（占用标准根行动；先到先得，可随时退出）。"""
        if (self.is_disabled() or self.captain_id is not None
                or self._clock_police_halted()):
            return False
        player = self._state_get_player(player_id)
        if getattr(getattr(player, "talent", None), "is_terror", False):
            return False
        if player_id in self._candidates:
            return False
        self._candidates.append(player_id)
        return True

    def withdraw_captain(self, player_id: str) -> bool:
        if player_id in self._candidates:
            self._candidates.remove(player_id)
            return True
        return False

    def candidates(self) -> List[str]:
        return list(self._candidates)

    def captain_command(self, captain_id: str, unit_id: str,
                        command: str, *args: Any) -> str:
        """队长命令：move/attack/wake/redesignate（威信责任）。"""
        if self.is_disabled():
            return m9_text("police.station_disabled")
        if self.captain_id != captain_id:
            return m9_text("police.only_captain_command")
        captain = self._state_get_player(captain_id)
        if command in ("move", "attack") and getattr(
                getattr(captain, "talent", None), "is_terror", False):
            return m9_text("police.terror_captain_cannot_command")
        unit = self.get_unit(unit_id)
        if unit is None or not unit.is_alive():
            return m9_text("police.unit_not_found")
        if command == "wake":
            unit.is_stunned = unit.is_shocked = unit.is_petrified = \
                unit.is_submerged = False
            return m9_text("police.unit_woken", unit_id=unit_id)
        if command == "move":
            if not args:
                return m9_text("police.need_location")
            unit.location = args[0]
            return m9_text("police.unit_moved", unit_id=unit_id, location=args[0])
        if command == "attack":
            if not args:
                return m9_text("police.need_attack_target")
            target_pid = args[0]
            target = self._state_get_player(target_pid)
            if target is None or not target.is_alive():
                return m9_text("police.target_invalid")
            t_talent = getattr(target, "talent", None)
            if getattr(t_talent, "is_terror", False):
                return m9_text("police.terror_not_enforceable")
            if unit.acted_this_round:
                return m9_text("police.unit_already_attacked")
            if unit.location != getattr(target, "location", None):
                return m9_text("police.not_same_location")
            from engine.m9.talents.g3 import attack_crosses_active_barrier
            if attack_crosses_active_barrier(self._state, unit, target):
                return m9_text("police.barrier_blocks_attack")
            unit.acted_this_round = True
            self._attack_player(self._state, unit, target,
                                self._enforcement_damage(unit))
            return m9_text("police.unit_attack_immediate", unit_id=unit_id,
                           target_pid=target_pid)
        if command == "redesignate":
            if not args:
                return m9_text("police.need_new_target")
            wanted = self.open_wanted()
            if wanted is not None:
                wanted.suspect_id = args[0]
                self.lead_id = None
                return m9_text("police.wanted_redesignated", target_pid=args[0])
            return m9_text("police.no_open_wanted")
        return m9_text("police.unknown_command", command=command)

    def set_state_ref(self, game_state: Any) -> None:
        """注入 state 引用（队长攻击等需要）。"""
        self._state = game_state

    def _state_get_player(self, pid: str):
        return getattr(self, "_state", None).get_player(pid) \
            if getattr(self, "_state", None) is not None else None

    def reduce_authority(self, amount: int = 1) -> List[str]:
        """威信降低；归零 → 队长下台并成为当前通缉目标。"""
        if self.captain_id is None or self.permanently_disabled:
            return []
        self.authority = max(0, self.authority - amount)
        if self.authority > 0:
            return []
        ex = self.captain_id
        ex_player = self._state_get_player(ex)
        if ex_player is not None:
            ex_player.is_captain = False
        self.captain_id = None
        self.close_current_wanted(m9_text("police.authority_zero_reason"))
        case = self.file_case(reporter_id="police", suspect_id=ex,
                              evidence=1, evidence_kind=EVIDENCE_DETECTOR)
        if case is not None:
            self.verify_case(case.case_id, lead_ok=True)
        self._record(m9_text("police.authority_zero_record", captain_id=ex))
        return [m9_text("police.authority_zero_message", captain_id=ex)]

    # ── 死亡/离场 ──
    def on_player_death(self, player_id: str) -> None:
        """目标死亡 → 结案（结案条件 1）；队长死亡 → 席位空缺。"""
        wanted = self.open_wanted()
        if wanted is not None and wanted.suspect_id == player_id:
            self.close_current_wanted(m9_text("police.target_death_reason"))
        if self.captain_id == player_id:
            player = self._state_get_player(player_id)
            if player is not None:
                player.is_captain = False
            self.captain_id = None
            self.authority = 0

    def on_target_exit(self, player_id: str) -> None:
        """目标绝对离场 → 结案（结案条件 2）。"""
        self.on_player_death(player_id)

    # ── G3 挂起/恢复 ──
    def set_suspended(self, suspended: bool) -> None:
        """Compatibility hook for direct tests and explicit global effects."""
        self.suspended = suspended
        self._suspension_owner = "legacy" if suspended else None

    def suspend_for_barrier(self, owner_id: str,
                            inside_actor_ids: set[str]) -> bool:
        """Suspend only when the current wanted target is inside this barrier."""
        wanted = self.open_wanted()
        if wanted is None or wanted.suspect_id not in inside_actor_ids:
            return False
        self.suspended = True
        self._suspension_owner = owner_id
        return True

    def resume_barrier(self, owner_id: str) -> None:
        if self._suspension_owner != owner_id:
            return
        self.suspended = False
        self._suspension_owner = None

    def reset(self) -> None:
        self.__init__()


class CoverSystem:
    """掩体（纯逻辑）：单位掩体在 A 阶段吸收（结算合同 A/H 两阶段）。

    PoliceStation 内部的玩家掩体也复用同一语义（station.grant_cover /
    absorb_cover / player_cover）。
    """

    def __init__(self) -> None:
        self._cover: Dict[str, int] = {}

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
    """T6 好市民配装表 = 冻结白名单并集（v0.3 §5.3）。"""
    return list(T6_WEAPON_WHITELIST) + list(T6_ARMOR_WHITELIST)


# ════════════════════════════════════════════════════════════════
#  魂援/遗物共享助手（B4 RFC v0.4 §5.3 / G0/G6 联动 W10）
# ════════════════════════════════════════════════════════════════

def grant_cover_to_player(game_state: Any, player_id: str,
                          durability: float, duration: int) -> None:
    """G7 防御援助：给玩家一个基于持续轮次的普通 COVERED 掩体。

    掩体存于玩家 `_aid_g7_cover_*`（compat 结算 A 阶段吸收，见
    `engine/m9/combat._apply_police_cover`），到期由 R0 清理。
    """
    if game_state is None or not player_id:
        return
    player = game_state.get_player(player_id)
    if player is None:
        return
    rnd = getattr(game_state, "current_round", 1)
    player._aid_g7_cover_durability = max(
        getattr(player, "_aid_g7_cover_durability", 0), int(durability))
    player._aid_g7_cover_until = rnd + int(duration)


def clear_expired_aid_covers(game_state: Any) -> None:
    """R0：清理到期的援助/遗物掩体与脆弱标记（防御侧结构状态）。"""
    rnd = getattr(game_state, "current_round", 1)
    for pid in getattr(game_state, "player_order", []):
        p = game_state.get_player(pid)
        if p is None:
            continue
        if getattr(p, "_aid_g7_cover_until", 0) < rnd:
            p._aid_g7_cover_durability = 0
            p._aid_g7_cover_until = 0
        if getattr(p, "_aid_vulnerable_until", 0) < rnd:
            p._aid_vulnerable = 0
            p._aid_vulnerable_until = 0


def temporary_performance_police(game_state: Any, requestor: Any,
                                 attack: bool) -> str:
    """T6 魂援·临时演出警察（B4 §5.3；W10 G0/T6 遗物同构）。

    不占编制、不继承/推进案件，作用后即消散：
    - attack=True：当前通缉目标与请求者同地点时，用 1 把合法武器攻击一次；
    - attack=False：解除请求者的普通 debuff 与普通控制后消散。
    """
    from engine.m9.combat import resolve_damage
    if game_state is None or requestor is None:
        return m9_text("police.temp_police_missing_params")
    if attack:
        station = getattr(game_state, "m9_police", None)
        wanted = station.open_wanted() if station is not None else None
        suspect_id = wanted.suspect_id if wanted is not None else None
        suspect = (game_state.get_player(suspect_id)
                   if suspect_id else None)
        if suspect is None or not suspect.is_alive():
            return m9_text("police.temp_police_no_target")
        if getattr(suspect, "location", None) != getattr(requestor, "location", None):
            return m9_text("police.temp_police_target_not_here")
        base = 3.0
        for w in getattr(requestor, "weapons", []) or []:
            if w is not None and getattr(w, "get_effective_damage", None):
                try:
                    base = max(base, float(w.get_effective_damage()))
                except Exception:
                    pass
        resolve_damage(
            requestor, suspect, None, game_state,
            raw_damage_override=base, damage_attribute_override="普通",
            source_kind="t6_aid_temp_police", _skip_outgoing_hook=True)
        return m9_text("police.temp_police_attack", target_name=suspect.name)
    markers = getattr(game_state, "markers", None)
    pid = getattr(requestor, "player_id", "")
    if markers is not None and pid:
        for m in ("STUNNED", "SHOCKED", "PETRIFIED"):
            markers.remove(pid, m)
        requestor.is_stunned = False
        requestor.is_shocked = False
    return m9_text("police.temp_police_dispel")
