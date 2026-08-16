"""M9 T1 一刀缭断天赋（profile: m9-rfc，T1 合同 v1.0）。

继承 v2exp OneSlash（武器选择/目标选择/伤害流），覆写 M9 差异：
- 即演 −1 SP / 公演 −2 SP，两者结算同一完整核心斩击；次数语义退役
  （不设 uses_remaining / max_uses / uses_left，无 full-extra 来源）；
- SP 合法性预检先于任何 SP/公演位消费（precheck → dispatch → act）；
- 斩击数值读 `m9_talents_extended.t1.*`：melee_multiplier（默认 2.0）、
  defense_coefficient（默认 0.5）；武器保留自身属性；ignore_counter /
  ignore_last_inner_absorb 在 M9 退役；
- 游侠诗（公演限定）：chase-move 到已 LOCKED_BY 目标地点后斩击并消耗
  标记（ranger_blade / ranger_chase）；即演不消耗；
- 击杀由 M9 管线（resolve_damage → DeathAdjudicator）负责，仅记事件。
"""
from __future__ import annotations

from typing import Any, Optional

from engine.balance import get as bget
from engine.m9.text import m9_text
from talents.t1_one_slash import OneSlash


def _t1(key: str, default):
    return bget("m9_talents_extended", "t1", key, default=default)


class OneSlash9(OneSlash):
    """M9 T1（m9-rfc 实例化；与 v2exp 类同名 name 保字符串引用兼容）。"""

    name = "一刀缭断"
    description = m9_text("talents.t1.description")

    def __init__(self, player_id: str, game_state: Any) -> None:
        super().__init__(player_id, game_state)
        # 次数语义退役：删除 v2exp 字段（合同：无 uses_remaining/max_uses）
        for attr in ("uses_remaining", "max_uses"):
            if hasattr(self, attr):
                delattr(self, attr)
        self.m9_poem_markers = {}  # 游侠诗标记（ranger_blade/ranger_chase）

    def describe_status(self) -> str:
        return m9_text("talents.t1.status")

    # ════════════════════════════════════════════════════════
    #  合法性与目标检索（预检先于 SP 消费）
    # ════════════════════════════════════════════════════════

    def _legal_melee_weapons(self, player: Any) -> list:
        from models.equipment import WeaponRange
        return [w for w in getattr(player, "weapons", [])
                if w is not None
                and w.weapon_range == WeaponRange.MELEE
                and not getattr(w, "_hexagram_disabled", False)]

    def _legal_targets(self, player: Any) -> list:
        engaged = self.state.markers.get_related(
            player.player_id, "ENGAGED_WITH")
        targets = []
        for eid in engaged:
            ep = self.state.get_actor(eid)
            if ep and ep.is_alive() and ep.location == player.location:
                targets.append(ep)
        return targets

    def _ranger_marker(self) -> Optional[str]:
        markers = getattr(self, "m9_poem_markers", None)
        if not markers:
            return None
        for key in ("ranger_blade", "ranger_chase"):
            if markers.get(key):
                return key
        return None

    def _ranger_chase_targets(self) -> list:
        candidates = []
        actors = self.state.iter_actors() if hasattr(
            self.state, "iter_actors") else (
                self.state.get_player(pid) for pid in self.state.player_order)
        for ep in actors:
            actor_id = getattr(
                ep, "player_id", getattr(ep, "unit_id", "")) if ep else ""
            if actor_id == self.player_id:
                continue
            if (ep and ep.is_alive() and self.state.markers.has_relation(
                    actor_id, "LOCKED_BY", self.player_id)):
                candidates.append(ep)
        return candidates

    def _ranger_chase_target(self, player: Any) -> Optional[Any]:
        candidates = self._ranger_chase_targets()
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        try:
            names = [t.name for t in candidates]
            choice = player.controller.choose(
                m9_text("talents.t1.choose_chase_target_prompt"), names,
                context={"phase": "T0", "situation": "oneslash_chase_target"})
            return next((t for t in candidates if t.name == choice),
                        candidates[0])
        except Exception:
            return candidates[0]

    def _precheck(self, player: Any) -> bool:
        if not self._legal_melee_weapons(player):
            return False
        if self._legal_targets(player):
            return True
        return (self._ranger_marker() is not None
                and bool(self._ranger_chase_targets()))

    # ════════════════════════════════════════════════════════
    #  T0 入口：即演 / 公演（同一完整核心斩击）
    # ════════════════════════════════════════════════════════

    def get_t0_option(self, player: Any) -> Optional[dict]:
        from engine.m9.gate import m9_enabled
        if not m9_enabled(self.state):
            return None
        if not self._precheck(player):
            return None
        m9 = getattr(self.state, "m9_system", None)
        if m9 is None:
            return None
        sp = m9.get_sp(self.player_id)
        mult = float(_t1("melee_multiplier", 1.5))
        mult_s = f"{mult:g}"
        if sp >= 2:
            return {"name": m9_text("talents.t1.t0.name_public"),
                    "description": m9_text("talents.t1.t0.description_public",
                                           mult=mult_s),
                    "m9_kind": "t1_performance"}
        if sp >= 1:
            return {"name": m9_text("talents.t1.t0.name_improvise"),
                    "description": m9_text("talents.t1.t0.description_improvise",
                                           mult=mult_s),
                    "m9_kind": "t1_improvise"}
        return None

    def execute_t0(self, player: Any):
        from engine.m9.gate import m9_enabled
        if not m9_enabled(self.state):
            return m9_text("talents.t1.err_m9_disabled"), False
        m9 = getattr(self.state, "m9_system", None)
        if m9 is None:
            return m9_text("talents.t1.err_m9_not_mounted"), False
        round_num = getattr(self.state, "current_round", 1)

        # 预检先于任何 SP/公演位消费
        weapons = self._legal_melee_weapons(player)
        if not weapons:
            return m9_text("talents.t1.err_no_melee_weapon"), False
        targets = self._legal_targets(player)
        marker_key = self._ranger_marker()
        chase_target = (self._ranger_chase_target(player)
                        if marker_key is not None else None)
        if not targets and chase_target is None:
            return m9_text("talents.t1.err_no_face_target"), False

        sp = m9.get_sp(self.player_id)
        if sp >= 2:
            public_ready = m9.assign_public_slot(round_num) == player.player_id
            option_public = m9_text("talents.t1.option_public")
            option_improvise = m9_text("talents.t1.option_improvise")
            options = [option_public, option_improvise] if public_ready \
                else [option_improvise]
            try:
                mode = player.controller.choose(
                    m9_text("talents.t1.choose_performance_mode_prompt"), options,
                    context={"phase": "T0", "situation": "t1_performance_mode"})
            except (AttributeError, TypeError, ValueError):
                mode = options[0]
            if "公演" in mode:
                if not self._ensure_public_seat(player, m9, round_num):
                    return m9_text("talents.t1.err_sp_or_public_seat"), False
                # 公演：游侠诗可先 chase-move 到已锁定目标再斩击
                if chase_target is not None:
                    self._chase_to(player, chase_target)
                    self._consume_ranger_marker(marker_key)
                    target = chase_target
                else:
                    target = self._pick_target(player, targets)
                return self._slash(player, target, weapons), True
            if not targets:
                return m9_text("talents.t1.err_improvise_no_face_target"), False
            if m9.dispatch_improvise(self.player_id, round_num) is None:
                return m9_text("talents.t1.err_sp_insufficient_cancel"), False
            target = self._pick_target(player, targets)
            return self._slash(player, target, weapons), True
        if sp >= 1:
            # 即演：不消费游侠诗标记
            if m9.dispatch_improvise(self.player_id, round_num) is None:
                return m9_text("talents.t1.err_sp_insufficient_cancel"), False
            target = self._pick_target(player, targets)
            return self._slash(player, target, weapons), True
        return m9_text("talents.t1.err_sp_insufficient"), False

    # ════════════════════════════════════════════════════════
    #  编排辅助：公演位 / 追猎 / 选择 / 斩击
    # ════════════════════════════════════════════════════════

    @staticmethod
    def _ensure_public_seat(player: Any, m9: Any, round_num: int) -> bool:
        if m9.assign_public_slot(round_num) != player.player_id:
            return False
        return m9.dispatch_public(player.player_id, round_num) is not None

    def _chase_to(self, player: Any, target: Any) -> None:
        old = player.location
        if target.location != old:
            player.location = target.location
            player.moved_this_round = True
            self.state.markers.on_player_move(player.player_id)
        self.state.log_event("t1_chase", player=self.player_id,
                             target=target.player_id,
                             from_loc=old, to_loc=target.location)

    def _consume_ranger_marker(self, key: Optional[str]) -> None:
        markers = getattr(self, "m9_poem_markers", None)
        if markers and key in markers:
            markers.pop(key, None)

    def _pick_weapon(self, player: Any, weapons: list) -> Any:
        if not weapons:
            return None
        if len(weapons) == 1:
            return weapons[0]
        names = [w.name for w in weapons]
        choice = player.controller.choose(
            m9_text("talents.t1.choose_weapon_prompt"), names,
            context={"phase": "T0", "situation": "oneslash_pick_weapon"})
        picked = next((w for w in weapons if w.name == choice), None)
        return picked if picked is not None else weapons[0]

    def _pick_target(self, player: Any, targets: list) -> Any:
        if not targets:
            return None
        if len(targets) == 1:
            return targets[0]
        names = [t.name for t in targets]
        choice = player.controller.choose(
            m9_text("talents.t1.choose_target_prompt"), names,
            context={"phase": "T0", "situation": "oneslash_pick_target"})
        picked = next((t for t in targets if t.name == choice), None)
        return picked if picked is not None else targets[0]

    def _slash(self, player: Any, target: Any, weapons: list) -> str:
        from engine.m9.combat import resolve_damage
        if target is None or not weapons:
            return m9_text("talents.t1.err_no_target_or_weapon")
        weapon = self._pick_weapon(player, weapons)
        result = resolve_damage(
            attacker=player,
            target=target,
            weapon=weapon,
            game_state=self.state,
            damage_multiplier=float(_t1("melee_multiplier", 2.0)),
            armor_pierce_factor=float(_t1("defense_coefficient", 0.5)),
            is_talent_attack=True,
            source_kind="t1_core_slash",
        )
        lines = [
            m9_text("talents.t1.slash_header", player=player.name,
                    weapon=weapon.name, target=target.name),
            m9_text("talents.t1.slash_note"),
        ]
        for detail in result.get("details", []):
            lines.append(f"   {detail}")
        if result.get("killed"):
            lines.append(m9_text("talents.t1.slash_killed", name=target.name))
        self.state.log_event("oneslash_attack", player=self.player_id,
                             target=target.player_id, weapon=weapon.name,
                             damage=result.get("final_damage", 0),
                             killed=result.get("killed", False))
        return "\n".join(lines)
