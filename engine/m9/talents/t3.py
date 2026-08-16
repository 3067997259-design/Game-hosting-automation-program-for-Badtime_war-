"""M9 T3 天星天赋（profile: m9-rfc，T3/T7 迁移合同 v0.3 + 2026-08-11 地点裁决）。

- 仅 2 SP 公演，无即演入口；占用标准行动槽；
- 公演**实际执行时**读取发动者当前所在地点原地释放：不提供地点选择 UI、
  报名公演时不锁定地点（DOC-045 追加裁决）；
- 对当前地点内除施法者本人以外的所有合法单位（玩家/影身/Chorus/警察）结算
  一次完整 AOE + 石化核心：starfall_damage 无属性伤害、defense_coefficient=0
  （完全穿防）、仍受 flat 减伤与 25% 下限；不是 DIRECT_DAMAGE；
- 石化经 `engine.m9.petrify.PetrifyRegistry` 统一生命周期（摇晃/挣脱/尘世之锁）；
- 死亡经 m9 结算管线（resolve_damage → DeathAdjudicator），不自行清理死亡；
- 删除 uses_remaining；删除涟漪弹射旧路径（弹射标记 `stars_bounce` 由诗篇另行实现）。

数值一律读 `m9_talents_extended.t3.*`（[待风洞]）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from engine.balance import get as bget
from engine.m9.text import m9_text
from talents.t3_star import Star


def _t3(key: str, default):
    return bget("m9_talents_extended", "t3", key, default=default)


def starfall_damage() -> int:
    return int(_t3("starfall_damage", 4))


def _petrify_target(game_state: Any, target: Any, source_pid: str) -> None:
    """对目标施加统一石化（玩家/影身/警察通用）。"""
    talent = getattr(target, "talent", None)
    if (talent is not None and hasattr(talent, "is_immune_to_debuff")
            and talent.is_immune_to_debuff("petrify")):
        return
    petrify = getattr(game_state, "m9_petrify", None)
    if petrify is None:
        return
    try:
        petrify.apply(game_state, target, source_pid=source_pid)
    except Exception:
        pass


def _aoe_targets(game_state: Any, location: str,
                 exclude_pid: str) -> List[Any]:
    """当前地点的全部合法战斗单位（除施法者本人）。

    玩家/Chorus/影身经 iter_actors；警察（legacy PoliceData 与 M9
    PoliceStation）分别枚举；源绑定附属对象（如 G0 无人机）随其主人作用域。
    """
    targets: List[Any] = []
    seen = set()
    iter_actors = getattr(game_state, "iter_actors", None)
    if iter_actors is not None:
        for actor in iter_actors():
            actor_id = getattr(
                actor, "player_id", getattr(actor, "unit_id", ""))
            if actor is None or actor_id == exclude_pid:
                continue
            if not getattr(actor, "is_alive", lambda: False)():
                continue
            if getattr(actor, "location", None) != location:
                continue
            seen.add(actor_id)
            targets.append(actor)
    police = getattr(game_state, "police", None)
    if police is not None and hasattr(police, "units_at"):
        for unit in police.units_at(location):
            unit_id = getattr(
                unit, "player_id", getattr(unit, "unit_id", ""))
            if unit_id in seen:
                continue
            seen.add(unit_id)
            targets.append(unit)
    m9_police = getattr(game_state, "m9_police", None)
    if m9_police is not None and hasattr(m9_police, "units_at"):
        for unit in m9_police.units_at(location):
            unit_id = getattr(
                unit, "player_id", getattr(unit, "unit_id", ""))
            if unit_id in seen:
                continue
            seen.add(unit_id)
            targets.append(unit)
    return targets


def starfall_core(player: Any, game_state: Any, *,
                  location: Optional[str] = None,
                  source_pid: Optional[str] = None,
                  allow_stars_bounce: bool = False) -> str:
    """天星完整核心（公演/借用共用）：当前地点 AOE + 石化。

    location 不传时在执行时读取发动者当前地点（2026-08-11 裁决）；
    死亡结算走 m9 管线，本函数不清理死亡。
    """
    from engine.m9.combat import resolve_damage
    if location is None:
        location = getattr(player, "location", None)
    if not location:
        return m9_text("talents.t3.err_no_location")
    source_pid = source_pid or player.player_id
    dmg = starfall_damage()
    targets = _aoe_targets(game_state, location, exclude_pid=source_pid)
    lines = [m9_text("talents.t3.cast_header", player=player.name,
                     location=location)]
    effective_hits: List[Any] = []
    kill_count = 0
    for target in targets:
        old_hp = getattr(target, "hp", 0)
        r = resolve_damage(
            player, target, weapon=None, game_state=game_state,
            raw_damage_override=dmg,
            damage_attribute_override="__无视__",
            armor_pierce_factor=0.0,
            is_talent_attack=True,
            source_kind="t3_starfall")
        new_hp = getattr(target, "hp", 0)
        actor_name = getattr(
            target, "name", getattr(target, "unit_id", m9_text("talents.t3.unknown_unit")))
        lines.append(
            m9_text("talents.t3.damage_line", name=actor_name,
                    damage=f"{r['hp_damage']}", old_hp=f"{old_hp}",
                    new_hp=f"{new_hp}"))
        if r.get("final_damage", 0) >= 1 or r.get("armor_broken"):
            effective_hits.append(target)
        if r.get("killed"):
            kill_count += 1
            lines.append(m9_text("talents.t3.kill_line", name=actor_name))
        elif new_hp > 0:
            was_petrified = getattr(target, "is_petrified", False)
            _petrify_target(game_state, target, source_pid)
            if getattr(target, "is_petrified", False) or was_petrified:
                lines.append(m9_text("talents.t3.petrify_line", name=actor_name))
    if allow_stars_bounce and effective_hits:
        lines.extend(_consume_stars_bounce(player, game_state, location))
    game_state.log_event("star_attack", player=source_pid, location=location,
                         hits=len(effective_hits), kills=kill_count)
    return "\n".join(lines)


def _consume_stars_bounce(player: Any, game_state: Any,
                          location: str) -> List[str]:
    """群星诗：下一次真实天星公演命中后，选一名合法单位追加两次普通伤害。"""
    from engine.m9.combat import resolve_damage
    talent = getattr(player, "talent", None)
    markers = getattr(talent, "m9_poem_markers", None) if talent else None
    if not markers or not markers.get("stars_bounce"):
        return []
    candidates = _aoe_targets(game_state, location, exclude_pid=player.player_id)
    if not candidates:
        return []
    names = [getattr(t, "name", getattr(t, "unit_id", m9_text("talents.t3.unknown_unit")))
             for t in candidates]
    try:
        choice = player.controller.choose(
            m9_text("talents.t3.choose_bounce_target_prompt"), names,
            context={"phase": "T0", "situation": "t3_stars_bounce_target"})
    except (AttributeError, TypeError, ValueError):
        choice = names[0]
    target = next((t for t, name in zip(candidates, names) if name == choice),
                  candidates[0])
    markers["stars_bounce"] = False
    damage = int(bget(
        "m9_talents_extended", "g5", "poem_stars_bounce_damage", default=2))
    target_name = getattr(target, "name",
                           getattr(target, "unit_id", m9_text("talents.t3.unit")))
    lines = [m9_text("talents.t3.bounce_lock_line", name=target_name)]
    for _ in range(2):
        if not target.is_alive():
            break
        result = resolve_damage(
            player, target, weapon=None, game_state=game_state,
            raw_damage_override=damage,
            damage_attribute_override="__无视__",
            armor_pierce_factor=0.0,
            is_talent_attack=True,
            source_kind="t3_stars_bounce")
        lines.append(m9_text("talents.t3.bounce_damage_line",
                             damage=f"{result.get('hp_damage', 0)}"))
    return lines


class Star9(Star):
    """M9 T3（m9-rfc 实例化；与 v2exp 类同名 name 保字符串引用兼容）。"""

    name = "天星"

    def __init__(self, player_id: str, game_state: Any) -> None:
        super().__init__(player_id, game_state)
        # M9：删除次数制（uses_remaining 等旧字段不再参与）
        if hasattr(self, "uses_remaining"):
            del self.uses_remaining
        self.ripple_bounce_count = 0  # 旧弹射计数退役，仅保留属性兼容

    def get_t0_option(self, player: Any) -> Optional[dict]:
        """仅公演入口：SP≥2 且当前地点存在合法目标时才出现。"""
        from engine.m9.gate import m9_enabled
        if not m9_enabled(self.state):
            return None
        m9 = getattr(self.state, "m9_system", None)
        if m9 is None or m9.get_sp(self.player_id) < 2:
            return None
        round_num = getattr(self.state, "current_round", 1)
        phase = getattr(self.state, "current_phase", "")
        if phase == "r3_actions" \
                and m9._public_holder_by_round.get(round_num) != self.player_id:
            return None  # T0 时无本轮公演位：不展示必失败的选项
        location = getattr(player, "location", None)
        if not location:
            return None
        if not _aoe_targets(self.state, location, exclude_pid=self.player_id):
            return None
        return {
            "name": m9_text("talents.t3.t0.name"),
            "description": m9_text("talents.t3.t0.description"),
            "m9_kind": "t3_starfall",
        }

    def execute_t0(self, player: Any):
        """公演：预检先于 SP 消费；执行时读取发动者当前地点。"""
        from engine.m9.gate import m9_enabled
        if not m9_enabled(self.state):
            return m9_text("talents.t3.err_m9_disabled"), False
        m9 = getattr(self.state, "m9_system", None)
        if m9 is None:
            return m9_text("talents.t3.err_m9_not_mounted"), False
        if m9.get_sp(self.player_id) < 2:
            return m9_text("talents.t3.err_sp_insufficient_cancel"), False
        location = getattr(player, "location", None)
        if not location:
            return m9_text("talents.t3.err_no_location_cancel"), False
        if not _aoe_targets(self.state, location, exclude_pid=self.player_id):
            return m9_text("talents.t3.err_no_target_cancel"), False
        round_num = getattr(self.state, "current_round", 1)
        if not self._ensure_public_seat(player, m9, round_num):
            return m9_text("talents.t3.err_sp_or_public_seat_cancel"), False
        # 执行时读取当前地点（不锁定报名时地点）
        msg = starfall_core(
            player, self.state, allow_stars_bounce=True)
        return msg, True

    @staticmethod
    def _ensure_public_seat(player: Any, m9: Any, round_num: int) -> bool:
        if m9.assign_public_slot(round_num) != player.player_id:
            return False
        return m9.dispatch_public(player.player_id, round_num) is not None

    def describe_status(self) -> str:
        return m9_text("talents.t3.status")

    # ── G6 借用核心（结算同地点 AOE + 基础石化，v0.2 已登记）──
    @staticmethod
    def borrow_starfall(player: Any, game_state: Any,
                        source_pid: Optional[str] = None) -> str:
        """G6 借用：以借用者状态结算同地点 AOE + 基础石化（无公演待遇）。"""
        return starfall_core(player, game_state, source_pid=source_pid or player.player_id)
