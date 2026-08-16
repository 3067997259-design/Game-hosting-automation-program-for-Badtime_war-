"""M9 T6 朝阳好市民天赋（profile: m9-rfc，警察/T6 重置合同 v0.3 §5）。

- 常驻：特别线索持久化（event_log 登记，仅存活/复活的 T6 可作证据使用；
  其他玩家不能凭特别线索单独完成普通举报）；不再改写全局犯罪集合；
- 市民热线：标准根行动（不读 SP、不占 T0），任意地点可举报；仍受
  证据/存活/世界时钟/当前通缉合法性约束（举报前检失败不耗证据/槽）；
- 2 SP 公演联防整备：为一名存活警察替换一件白名单装备（武器/护甲）；
  执行时必须有存活警察，否则演出在消费 SP 前取消；
- 无案件/无犯罪时不生成虚构案件或补偿（和平局举报通道自然变窄）；
- 竞选减免（get_election_rounds_reduction）退役，不再提供。

数值一律读 `m9_talents_extended.t6.*`（[待风洞]）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from engine.balance import get as bget
from engine.m9.text import m9_text
from talents.t6_good_citizen import GoodCitizen

from engine.m9.police import (
    EVIDENCE_T6_CLUE,
    EVIDENCE_VICTIM,
    EVIDENCE_WITNESS,
    T6_ARMOR_WHITELIST,
    T6_WEAPON_WHITELIST,
)


def _t6(key: str, default):
    return bget("m9_talents_extended", "t6", key, default=default)


class GoodCitizen9(GoodCitizen):
    """M9 T6（m9-rfc 实例化；与 v2exp 类同名 name 保字符串引用兼容）。"""

    name = "朝阳好市民"

    def __init__(self, player_id: str, game_state: Any) -> None:
        super().__init__(player_id, game_state)

    # ════════════════════════════════════════════════════════
    #  常驻：不改写全局犯罪集合（退役项不生效）
    # ════════════════════════════════════════════════════════

    def on_register(self):
        """M9：不再改写全局犯罪集合（v0.3 §5.4 退役）。"""
        from engine.m9.gate import m9_enabled
        if not m9_enabled(self.state):
            return super().on_register()
        return None

    def get_election_rounds_reduction(self):
        """M9：竞选减免退役。"""
        from engine.m9.gate import m9_enabled
        if not m9_enabled(self.state):
            return super().get_election_rounds_reduction()
        return 0

    def allows_remote_report(self):
        """M9：市民热线任意地点可举报（根行动，不读 SP）。"""
        return True

    # ════════════════════════════════════════════════════════
    #  特别线索（v0.3 §5.1：持久化 + 使用权受限）
    # ════════════════════════════════════════════════════════

    def record_special_clue(self, suspect_id: str, clue_type: str) -> None:
        """登记一条特别线索到 event_log（T6 死亡后仍存在）。"""
        self.state.log_event("special_clue", player=self.player_id,
                             suspect=suspect_id, clue=clue_type,
                             round=self.state.current_round)

    def special_clues_for(self, suspect_id: str) -> List[Dict[str, Any]]:
        """T6 可用的特别线索（仅存活/复活的 T6 本人可读）。"""
        me = self.state.get_player(self.player_id)
        if me is None or not me.is_alive():
            return []
        return [
            e for e in getattr(self.state, "event_log", [])
            if e.get("type") == "special_clue"
            and e.get("suspect") == suspect_id
        ]

    def any_special_clues(self) -> bool:
        return any(
            e.get("type") == "special_clue"
            for e in getattr(self.state, "event_log", [])
        )

    # ════════════════════════════════════════════════════════
    #  市民热线（标准根行动，不读 SP）
    # ════════════════════════════════════════════════════════

    def hotline_report(self, suspect_id: str) -> str:
        """市民热线：任意地点举报；举报前检失败不耗证据/槽。

        证据资格封闭清单：受害者/同地点目击者/系统归因探测器/T6 特别线索。
        成功立即登记唯一通缉（v0.3 §2.2：举报成功立即登记通缉）。
        """
        from engine.m9.gate import m9_enabled
        if not m9_enabled(self.state):
            return m9_text("talents.t6.err_m9_not_enabled")
        station = getattr(self.state, "m9_police", None)
        if station is None:
            return m9_text("talents.t6.err_police_not_mounted")
        suspect = self.state.get_player(suspect_id)
        me = self.state.get_player(self.player_id)
        if me is None or not me.is_alive():
            return m9_text("talents.t6.err_reporter_invalid")
        if suspect is None or not suspect.is_alive():
            return m9_text("talents.t6.err_target_invalid")
        if suspect_id == self.player_id:
            return m9_text("talents.t6.err_self_report")
        if station.is_disabled():
            return m9_text("talents.t6.err_police_disabled")
        if station.has_open_wanted():
            return m9_text("talents.t6.err_wanted_exists")
        from engine.m9.talents.g3 import active_barrier
        barrier = active_barrier(self.state)
        if barrier is not None and (
                barrier._is_inside(me) or barrier._is_inside(suspect)):
            return m9_text("talents.t6.err_barrier_blocks_report")
        evidence_kind, ok = self._evidence_for(suspect_id)
        if not ok:
            return m9_text("talents.t6.err_no_legal_evidence")
        case = station.file_case(
            reporter_id=self.player_id, suspect_id=suspect_id,
            evidence=1, evidence_kind=evidence_kind)
        if case is None:
            return m9_text("talents.t6.err_case_failed")
        station.verify_case(case.case_id, lead_ok=True)
        self.state.log_event("hotline", player=self.player_id,
                             target=suspect_id)
        return m9_text("talents.t6.hotline_success", reporter=me.name,
                       suspect=suspect.name, evidence=evidence_kind)

    def _evidence_for(self, suspect_id: str):
        """Evidence must be bound to the event, never inferred from current location."""
        if self.special_clues_for(suspect_id):
            return EVIDENCE_T6_CLUE, True
        for event in reversed(getattr(self.state, "event_log", [])):
            if event.get("type") != "attack" \
                    or event.get("attacker") != suspect_id:
                continue
            if self.player_id in event.get("witnesses", []):
                return EVIDENCE_WITNESS, True
            if event.get("target") == self.player_id:
                return EVIDENCE_VICTIM, True
        records = getattr(getattr(self.state, "police", None),
                          "crime_records", {})
        if records.get(suspect_id):
            from engine.m9.police import EVIDENCE_DETECTOR
            return EVIDENCE_DETECTOR, True
        return "", False

    # ════════════════════════════════════════════════════════
    #  T0 入口：2 SP 公演联防整备
    # ════════════════════════════════════════════════════════

    def get_t0_option(self, player: Any) -> Optional[dict]:
        """SP≥1 and a reachable unit plus physical equipment are available."""
        from engine.m9.gate import m9_enabled
        if not m9_enabled(self.state):
            return None
        m9 = getattr(self.state, "m9_system", None)
        if m9 is None or m9.get_sp(self.player_id) < 1:
            return None
        station = getattr(self.state, "m9_police", None)
        if station is None or station.is_disabled():
            return None
        if not self._equipment_candidates(player, station):
            return None
        sp = m9.get_sp(self.player_id)
        return {
            "name": m9_text("talents.t6.t0.name"),
            "description": (m9_text("talents.t6.t0.description_public") if sp >= 2
                            else m9_text("talents.t6.t0.description_improvise")),
            "m9_kind": "t6_equip",
        }

    def execute_t0(self, player: Any):
        """联防整备：执行时必须有存活警察，否则在消费 SP 前取消。"""
        from engine.m9.gate import m9_enabled
        if not m9_enabled(self.state):
            return m9_text("talents.t6.err_talent_disabled"), False
        m9 = getattr(self.state, "m9_system", None)
        station = getattr(self.state, "m9_police", None)
        if m9 is None or station is None:
            return m9_text("talents.t6.err_m9_not_mounted"), False
        if station.is_disabled():
            return m9_text("talents.t6.err_police_disabled"), False
        candidates = self._equipment_candidates(player, station)
        if not candidates:
            return m9_text("talents.t6.err_no_candidates_cancel"), False
        sp = m9.get_sp(self.player_id)
        if sp < 1:
            return m9_text("talents.t6.err_sp_insufficient_cancel"), False
        round_num = getattr(self.state, "current_round", 1)
        public_ready = sp >= 2 \
            and m9.assign_public_slot(round_num) == self.player_id
        mode = "即演"
        if public_ready:
            mode = self._choose(
                player, m9_text("talents.t6.choose_mode_prompt"),
                [m9_text("talents.t6.option_improvise"),
                 m9_text("talents.t6.option_public")])
        plan = self._choose_equipment_plan(player, candidates)
        if plan is None:
            return m9_text("talents.t6.err_no_plan_cancel"), False
        if mode == "公演":
            if not self._ensure_public_seat(player, m9, round_num):
                return m9_text("talents.t6.err_sp_or_public_seat"), False
        elif m9.dispatch_improvise(
                player.player_id, round_num, source_id="t6_equip") is None:
            return m9_text("talents.t6.err_improvise_unavailable"), False
        msg = self._apply_equipment_plan(player, plan)
        return msg, True

    @staticmethod
    def _ensure_public_seat(player: Any, m9: Any, round_num: int) -> bool:
        if m9.assign_public_slot(round_num) != player.player_id:
            return False
        return m9.dispatch_public(player.player_id, round_num) is not None

    def _equipment_candidates(self, player: Any, station: Any) -> List[tuple]:
        """Closed-list physical equipment × same-location living police.

        审计修复：过滤“警员已持有同名装备”的重复整备——此前反复给
        unit1 换同名武器/护甲，零边际警力。
        """
        from engine.m9.talents.g3 import attack_crosses_active_barrier
        units = [
            unit for unit in station.alive_units()
            if unit.location == getattr(player, "location", None)
            and not attack_crosses_active_barrier(self.state, player, unit)
        ]
        weapons = [weapon for weapon in getattr(player, "weapons", [])
                   if weapon is not None and weapon.name in T6_WEAPON_WHITELIST]
        armors = [piece for piece in player.armor.get_all_active()
                  if piece.name in T6_ARMOR_WHITELIST]
        candidates: List[tuple] = []
        for unit in units:
            for weapon in weapons:
                if getattr(unit, "_m9_t6_weapon", None) == weapon.name:
                    continue  # 已整备过同名武器，无边际收益
                candidates.append((unit, "武器", weapon))
            for piece in armors:
                if getattr(unit, "_m9_t6_armor", None) == piece.name:
                    continue  # 已整备过同名护甲
                candidates.append((unit, "护甲", piece))
        return candidates

    @staticmethod
    def _slot_label(slot: str) -> str:
        if slot == "武器":
            return m9_text("talents.t6.slot_weapon")
        return m9_text("talents.t6.slot_armor")

    def _choose_equipment_plan(self, player: Any,
                               candidates: List[tuple]) -> Optional[tuple]:
        unit_names = list(dict.fromkeys(unit.unit_id for unit, _, _ in candidates))
        unit_name = self._choose(player, m9_text("talents.t6.choose_unit_prompt"),
                                unit_names)
        narrowed = [c for c in candidates if c[0].unit_id == unit_name]
        slots = list(dict.fromkeys(slot for _, slot, _ in narrowed))
        slot_labels = [self._slot_label(slot) for slot in slots]
        slot_label = self._choose(player, m9_text("talents.t6.choose_slot_prompt"),
                                 slot_labels)
        slot = next((s for s, label in zip(slots, slot_labels)
                     if label == slot_label), slots[0])
        narrowed = [c for c in narrowed if c[1] == slot]
        names = [equipment.name for _, _, equipment in narrowed]
        name = self._choose(
            player,
            m9_text("talents.t6.choose_equipment_prompt",
                    slot=self._slot_label(slot)),
            names)
        return next((c for c in narrowed if c[2].name == name), None)

    def _apply_equipment_plan(self, player: Any, plan: tuple) -> str:
        unit, slot, equipment = plan
        if slot == "武器":
            player.weapons.remove(equipment)
            unit.weapon_name = equipment.name
            unit._m9_t6_weapon = equipment.name
            self.state.log_event("t6_equip", player=self.player_id,
                                 unit=unit.unit_id, weapon=equipment.name)
            return m9_text("talents.t6.equip_success", player=player.name,
                           unit=unit.unit_id, equipment=equipment.name)
        layer = player.armor._get_layer_list(equipment.layer)
        layer.remove(equipment)
        unit.armor = equipment
        unit._m9_t6_armor = equipment.name
        self.state.log_event("t6_equip", player=self.player_id,
                             unit=unit.unit_id, armor=equipment.name)
        return m9_text("talents.t6.equip_success", player=player.name,
                       unit=unit.unit_id, equipment=equipment.name)

    @staticmethod
    def _choose(player: Any, prompt: str, options: List[str]) -> str:
        if not options:
            return ""
        controller = getattr(player, "controller", None)
        try:
            choice = controller.choose(
                prompt, list(options),
                context={"phase": "T0", "player": player})
        except Exception:
            choice = options[0]
        return choice if choice in options else options[0]

    def describe_status(self) -> str:
        clues = self.any_special_clues()
        clue_text = m9_text("talents.t6.clue_yes" if clues else "talents.t6.clue_no")
        return m9_text("talents.t6.status", clues=clue_text)
