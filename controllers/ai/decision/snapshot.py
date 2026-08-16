"""BasicAI 决策内核：DecisionSnapshot v2（不可变决策快照 + 派生评估层）。

结构原则（2026-08-12 设计裁决）：
- **ProjectedSnapshot**：state → snapshot 的全量不可变投影，每决策点构建一次；
  state_version 相同 → 快照内容相同 → 决策可复现（全量投影，一致性优先）。
- **AssessmentLayer**：minds 的派生输出（威胁分/地点威胁/目标日志），按 minds
  执行顺序填充，不污染投影。
- 我方单位**特化**而非泛化：G2 影身用 `shadow` 字段、G0 无人机用 `g0_drone`，
  未来新单位加字段，不建抽象。
- `opponent_intent`：构建时单次扫描 event_log，按对手 pid 取最近 K=5 条动作事件。
- `decision_trace`：每候选一条（供 AIRI 自然语言解释）。

兼容：保留 `build(player, state, grant)` 接口、`M9Facts` 名与 `_slot_id_for`。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from engine.m9.text import m9_text


def _slot_id_for(talent: Any) -> str:
    """天赋稳定槽位（T1..G7）；无法解析返回空串。"""
    if talent is None:
        return ""
    try:
        from engine.m9.talent_registry import (
            M9_TALENT_REGISTRY, registration_for_legacy_class)
        cls_path = f"{type(talent).__module__}.{type(talent).__name__}"
        reg = registration_for_legacy_class(type(talent))
        if reg is None:
            reg = next(
                (item for item in M9_TALENT_REGISTRY.values()
                 if getattr(item, "m9_class_path", None) == cls_path),
                None)
        if reg is not None:
            return reg.slot_id
    except Exception:
        pass
    return getattr(talent, "slot_id", "") or ""


# ════════════════════════════════════════════════════════════════════
#  投影数据类
# ════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class UnitBrief:
    """单位摘要（玩家/警察/影身/无人机统一只作环境描述）。"""
    uid: str
    name: str
    kind: str                 # player / police / shadow / drone / chorus
    location: Optional[str]
    hp: int
    max_hp: int
    alive: bool
    cc: Tuple[str, ...] = ()  # 受限控制标记（stunned/shocked/petrified）
    slot_id: str = ""         # 天赋稳定槽位（player 单位；对手 capabilities 解析用）
    is_captain: bool = False  # 警队队长（威胁评估用）


@dataclass(frozen=True)
class IntentSignal:
    """对手意图信号（event_log 最近动作推导）。"""
    action_type: str
    params: Dict[str, Any] = field(default_factory=dict)
    round: int = 0


@dataclass(frozen=True)
class ShadowBrief:
    """G2 影身摘要（特化；bodies 泛化已否决）。"""
    present: bool
    location: Optional[str]
    hp: float
    max_hp: float
    weapons: Tuple[str, ...] = ()
    armor: Tuple[str, ...] = ()
    controllable: bool = True    # 终曲歌者 False


@dataclass(frozen=True)
class DroneBrief:
    """G0 无人机摘要（特化）。"""
    present: bool
    location: Optional[str]
    hp: float
    rounds_left: int


@dataclass(frozen=True)
class WindowBrief:
    """状态窗口（计划条件执行的基础）。"""
    remaining: int                # 剩余全局轮（0=本轮到期末）
    note: str = ""


@dataclass(frozen=True)
class M9Facts:
    """M9 世界事实投影（m9-rfc 下非 None）。"""

    police_cases: int = 0
    police_wanted: Optional[str] = None
    police_lead: Optional[str] = None
    police_captain: Optional[str] = None
    police_disabled: bool = False
    barrier_active: bool = False
    barrier_location: Optional[str] = None
    barrier_captured: Tuple[str, ...] = ()
    destroyed_locations: Tuple[str, ...] = ()
    public_holder: Optional[str] = None
    sp: int = 0
    pp: int = 0
    shadow: Optional[ShadowBrief] = None
    shadow_create_eligible: bool = True
    terminal_state: str = "none"     # none / shadow / singer
    g0_drone: Optional[DroneBrief] = None
    terminal_areas: Tuple[Dict[str, Any], ...] = ()
    insurance_mounted: bool = False
    petrified: bool = False
    love_wish_on_me: Tuple[str, ...] = ()
    propagation_active: bool = False  # G1 繁育形态（绝对死亡倒计时窗口）

    @classmethod
    def of(cls, game_state: Any, actor_id: str) -> Optional["M9Facts"]:
        from engine.m9.gate import m9_enabled
        if not m9_enabled(game_state):
            return None
        police = getattr(game_state, "m9_police", None)
        wanted = lead = captain = None
        disabled = False
        if police is not None:
            try:
                w = police.open_wanted()
                wanted = getattr(w, "suspect_id", None) if w is not None else None
                lead = police.lead_id
                captain = police.captain_id
                disabled = police.is_disabled()
            except Exception:
                pass
        barrier = None
        try:
            from engine.m9.talents.g3 import active_barrier
            barrier = active_barrier(game_state)
        except Exception:
            barrier = None
        m9 = getattr(game_state, "m9_system", None)
        sp = m9.get_sp(actor_id) if m9 is not None else 0
        pp = 0
        ledger = getattr(game_state, "m9_pp", None)
        if ledger is not None:
            try:
                pp = ledger.balance(actor_id)
            except Exception:
                pass
        # G2 影身（特化）
        shadow_brief = None
        create_eligible = True
        terminal_state = "none"
        player = game_state.get_player(actor_id)
        talent = getattr(player, "talent", None) if player is not None else None
        if talent is not None and getattr(talent, "name", "") in (
                "神代天赋-请一直注视着我", "请一直，注视着我", "请一直注视着我"):
            try:
                sh = talent._shadow()
                if sh is not None:
                    if sh.is_terminal_singer:
                        terminal_state = "singer"
                    else:
                        terminal_state = "shadow"
                        shadow_brief = ShadowBrief(
                            present=True,
                            location=getattr(sh, "location", None),
                            hp=float(getattr(sh, "hp", 0)),
                            max_hp=float(getattr(sh, "max_hp", 0)),
                            weapons=tuple(
                                getattr(w, "name", "?")
                                for w in getattr(sh, "weapons", []) or []
                                if w is not None),
                            armor=tuple(
                                getattr(p, "name", "?")
                                for p in getattr(getattr(sh, "armor", None),
                                                 "get_active", lambda l: [])("outer")
                                or []),
                            controllable=not sh.is_terminal_singer,
                        )
                create_eligible = bool(
                    getattr(talent, "shadow_creation_eligible", True))
            except Exception:
                pass
        # G0 无人机（特化）
        drone_brief = None
        if talent is not None and hasattr(talent, "_drone"):
            try:
                drone = talent._drone()
                if drone is not None and getattr(drone, "is_alive", lambda: False)():
                    drone_brief = DroneBrief(
                        present=True,
                        location=getattr(drone, "location", None),
                        hp=float(getattr(drone, "hp", 0)),
                        rounds_left=max(
                            0, int(getattr(drone, "rounds_left", 0))),
                    )
            except Exception:
                pass
        # G1 繁育形态窗口
        propagation = False
        if talent is not None and getattr(talent, "form", "") == "propagation":
            propagation = True
        # 终曲区域
        areas = []
        try:
            for area in getattr(game_state, "m9_terminal_areas", {}).values():
                areas.append({
                    "location": getattr(area, "location", None),
                    "remaining": max(0, getattr(area, "ticks_left", 0)
                                     if hasattr(area, "ticks_left") else 0),
                })
        except Exception:
            pass
        return cls(
            police_cases=len(getattr(police, "cases", []) or [])
            if police is not None else 0,
            police_wanted=wanted, police_lead=lead, police_captain=captain,
            police_disabled=disabled,
            barrier_active=barrier is not None,
            barrier_location=getattr(barrier, "barrier_location", None)
            if barrier is not None else None,
            barrier_captured=tuple(
                getattr(barrier, "captured", ()) or ())
            if barrier is not None else (),
            destroyed_locations=tuple(
                getattr(game_state, "m9_destroyed_locations", set()) or ()),
            public_holder=_public_holder_of(game_state, m9),
            sp=sp, pp=pp,
            shadow=shadow_brief, shadow_create_eligible=create_eligible,
            terminal_state=terminal_state, g0_drone=drone_brief,
            terminal_areas=tuple(areas),
            insurance_mounted=bool(
                getattr(game_state, "m9_insurance", None)
                and getattr(game_state.m9_insurance, "is_mounted", lambda: False)()),
            petrified=bool(getattr(game_state, "m9_petrify", None)
                           and getattr(game_state.m9_petrify, "is_petrified",
                                       lambda _: False)(actor_id)),
            love_wish_on_me=_love_wish_on(game_state, actor_id),
            propagation_active=propagation,
        )


def _public_holder_of(game_state: Any, m9: Any) -> Optional[str]:
    """当前全局轮的公演位持有者（_public_holder_by_round[round]）。"""
    if m9 is None:
        return None
    holder_map = getattr(m9, "_public_holder_by_round", {}) or {}
    return holder_map.get(getattr(game_state, "current_round", -1))


def _love_wish_on(game_state: Any, actor_id: str) -> Tuple[str, ...]:
    out = []
    for pid in getattr(game_state, "player_order", []):
        p = game_state.get_player(pid)
        if p is None or p.talent is None:
            continue
        try:
            if hasattr(p.talent, "has_love_wish") \
                    and p.talent.has_love_wish(actor_id):
                out.append(pid)
        except Exception:
            continue
    return tuple(out)


# ════════════════════════════════════════════════════════════════════
#  决策痕迹（每候选一条，供 AIRI 解释）
# ════════════════════════════════════════════════════════════════════

@dataclass
class ValueBreakdown:
    """候选的价值分解（反"犯傻"的可解释依据）。"""
    gains: Tuple[str, ...] = ()      # 收益项（自然语言）
    risks: Tuple[str, ...] = ()      # 风险项
    key_fields: Dict[str, Any] = field(default_factory=dict)  # 关键字段值


@dataclass
class TraceEntry:
    """一条候选决策痕迹。"""
    raw: str
    score: float
    goal: str = ""
    breakdown: Optional[ValueBreakdown] = None
    source: str = ""                 # mind / hook / counter / goal / fallback


# ════════════════════════════════════════════════════════════════════
#  ProjectedSnapshot：全量不可变投影
# ════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ProjectedSnapshot:
    # 身份层
    actor_id: str
    profile: str
    slot_id: str
    grant_id: str = ""
    grant_kind: str = ""
    allow_instant: bool = False
    allow_public: bool = False
    state_version: int = 0
    # 自身层
    hp: int = 0
    max_hp: int = 20
    location: Optional[str] = None
    sp: int = 0
    weapons: Tuple[str, ...] = ()
    items: Tuple[str, ...] = ()
    armor_summary: Dict[str, Any] = field(default_factory=dict)
    credits: int = 0
    vouchers: int = 0
    has_military_pass: bool = False
    has_detection: bool = False
    cc_flags: Dict[str, bool] = field(default_factory=dict)
    # 环境层
    location_occupancy: Dict[str, Tuple[UnitBrief, ...]] = field(
        default_factory=dict)
    police_presence: Dict[str, int] = field(default_factory=dict)
    # 对手层（投影部分）
    opponent_briefs: Dict[str, UnitBrief] = field(default_factory=dict)
    opponent_intent: Dict[str, Tuple[IntentSignal, ...]] = field(
        default_factory=dict)
    # 时间层
    current_round: int = 0
    max_rounds: int = 50
    world_phase: str = "day"
    active_windows: Dict[str, WindowBrief] = field(default_factory=dict)
    # M9 层
    m9: Optional[M9Facts] = None

    @classmethod
    def build(cls, player: Any, game_state: Any,
              grant: Any = None) -> "ProjectedSnapshot":
        from engine.m9.gate import m9_enabled
        profile = "m9-rfc" if m9_enabled(game_state) else "v2exp"
        actor_id = getattr(player, "player_id", "")
        talent = getattr(player, "talent", None)
        slot_id = _slot_id_for(talent)
        m9 = getattr(game_state, "m9_system", None)
        sp = m9.get_sp(actor_id) if m9 is not None else 0
        armor = getattr(player, "armor", None)
        armor_summary = {}
        if armor is not None:
            try:
                armor_summary = {
                    "outer": tuple(getattr(p, "name", "?")
                                   for p in armor.get_active("outer") or []),
                    "inner": tuple(getattr(p, "name", "?")
                                   for p in armor.get_active("inner") or []),
                }
            except Exception:
                armor_summary = {}
        cc = {}
        for flag in ("is_stunned", "is_shocked", "is_petrified"):
            cc[flag.replace("is_", "")] = bool(getattr(player, flag, False))
        occupancy, police_presence = _project_environment(game_state, actor_id)
        opp_briefs = _project_opponents(game_state, actor_id)
        opp_intent = _project_intents(game_state, actor_id, k=5)
        windows = _project_windows(game_state, player)
        try:
            from engine import world_clock
            phase = world_clock.current_phase(game_state)
        except Exception:
            phase = "day"
        return cls(
            actor_id=actor_id, profile=profile, slot_id=slot_id,
            grant_id=getattr(grant, "grant_id", "") if grant is not None else "",
            grant_kind=getattr(grant, "kind", "") if grant is not None else "",
            allow_instant=bool(getattr(grant, "allow_instant", False))
            if grant is not None else True,
            allow_public=bool(getattr(grant, "allow_public", False))
            if grant is not None else True,
            state_version=getattr(game_state, "current_round", 0),
            hp=int(getattr(player, "hp", 0)),
            max_hp=int(getattr(player, "max_hp", 20)),
            location=getattr(player, "location", None),
            sp=sp,
            weapons=tuple(getattr(w, "name", "?")
                          for w in getattr(player, "weapons", []) or []
                          if w is not None),
            items=tuple(getattr(i, "name", "?")
                        for i in getattr(player, "items", []) or []
                        if i is not None),
            armor_summary=armor_summary,
            credits=int(getattr(player, "credits", 0)),
            vouchers=int(getattr(player, "vouchers", 0)),
            has_military_pass=bool(getattr(player, "has_military_pass", False)),
            has_detection=bool(getattr(player, "has_detection", False)),
            cc_flags=cc,
            location_occupancy=occupancy,
            police_presence=police_presence,
            opponent_briefs=opp_briefs,
            opponent_intent=opp_intent,
            current_round=getattr(game_state, "current_round", 0),
            max_rounds=int(getattr(game_state, "max_rounds", 50)
                           if getattr(game_state, "max_rounds", None)
                           else 50),
            world_phase=phase,
            active_windows=windows,
            m9=M9Facts.of(game_state, actor_id),
        )


# ════════════════════════════════════════════════════════════════════
#  环境/对手/意图/窗口投影
# ════════════════════════════════════════════════════════════════════

def _project_environment(game_state: Any, self_id: str):
    """地点占有表 + 警察分布（含影身/无人机等可枚举单位）。"""
    occupancy: Dict[str, List[UnitBrief]] = {}
    police_presence: Dict[str, int] = {}
    units = []
    for pid in getattr(game_state, "player_order", []):
        p = game_state.get_player(pid)
        if p is not None:
            units.append((p, "player"))
    try:
        for actor in game_state.iter_actors():
            if getattr(actor, "_m9_shadow_actor", False):
                units.append((actor, "shadow"))
            elif getattr(actor, "_m9_drone_actor", False):
                units.append((actor, "drone"))
            elif getattr(actor, "_m9_police_actor", False):
                units.append((actor, "police"))
            elif getattr(actor, "is_chorus", False):
                units.append((actor, "chorus"))
    except Exception:
        pass
    for actor, kind in units:
        loc = getattr(actor, "location", None)
        if loc is None:
            continue
        brief = UnitBrief(
            uid=getattr(actor, "player_id", str(id(actor))),
            name=getattr(actor, "name", "?"),
            kind=kind, location=loc,
            hp=int(getattr(actor, "hp", 0)),
            max_hp=int(getattr(actor, "max_hp", 20)),
            alive=bool(getattr(actor, "is_alive", lambda: False)()),
            cc=tuple(
                f for f in ("stunned", "shocked", "petrified")
                if getattr(actor, f"is_{f}", False)),
        )
        occupancy.setdefault(loc, []).append(brief)
        if kind == "police":
            police_presence[loc] = police_presence.get(loc, 0) + 1
    return ({loc: tuple(v) for loc, v in occupancy.items()},
            police_presence)


def _project_opponents(game_state: Any, self_id: str):
    """对手玩家摘要（位置/HP/装备摘要/cc/天赋槽位）。"""
    out: Dict[str, UnitBrief] = {}
    for pid in getattr(game_state, "player_order", []):
        if pid == self_id:
            continue
        p = game_state.get_player(pid)
        if p is None:
            continue
        out[pid] = UnitBrief(
            uid=pid, name=getattr(p, "name", pid), kind="player",
            location=getattr(p, "location", None),
            hp=int(getattr(p, "hp", 0)),
            max_hp=int(getattr(p, "max_hp", 20)),
            alive=bool(p.is_alive()),
            cc=tuple(f for f in ("stunned", "shocked", "petrified")
                     if getattr(p, f"is_{f}", False)),
            slot_id=_slot_id_for(getattr(p, "talent", None)),
            is_captain=bool(getattr(p, "is_captain", False)),
        )
    return out


def _project_intents(game_state: Any, self_id: str, k: int = 5):
    """event_log 反向有界扫描：按对手 pid 取最近 k 条动作事件。

    只取有明确行为信号的类型（move/attack/lock/find/interact/special）；
    intent 是"他在干什么"的轻量信号，非完整日志。旧实现每次决策全量扫描
    event_log（决策点 × 事件总数），改为从尾部回扫：所有对手都凑满 k 条
    或达到扫描上限即停。
    """
    SIGNAL_TYPES = ("move", "attack", "lock", "find", "interact", "special")
    per_pid: Dict[str, List[IntentSignal]] = {}
    opponent_pids = [
        pid for pid in getattr(game_state, "player_order", [])
        if pid != self_id
    ]
    if not opponent_pids:
        return {}
    filled: set[str] = set()
    scan_limit = max(32, k * len(opponent_pids) * 3)
    scanned = 0
    for ev in reversed(getattr(game_state, "event_log", []) or []):
        if scanned >= scan_limit:
            break
        scanned += 1
        if not isinstance(ev, dict):
            continue
        etype = ev.get("type")
        if etype not in SIGNAL_TYPES:
            continue
        pid = ev.get("player") or ev.get("attacker")
        if not isinstance(pid, str) or pid == self_id \
                or pid not in opponent_pids:
            continue
        params = {}
        for key in ("to", "from_loc", "target", "item", "weapon",
                    "location", "operation"):
            if ev.get(key) is not None:
                params[key] = ev[key]
        sig = IntentSignal(action_type=etype, params=params,
                           round=int(ev.get("round", 0)))
        bucket = per_pid.setdefault(pid, [])
        if len(bucket) < k:
            bucket.append(sig)
        if len(bucket) >= k:
            filled.add(pid)
        if len(filled) >= len(opponent_pids):
            break
    return {pid: tuple(reversed(sigs))
            for pid, sigs in per_pid.items() if sigs}


def _project_windows(game_state: Any, player: Any):
    """状态窗口：烟雾弹/结界/形态/爱愿等（计划条件执行的基础）。

    第一版取 M9 相关窗口 + G7 烟雾弹；扩展窗口类型时在此登记。
    """
    windows: Dict[str, WindowBrief] = {}
    rnd = getattr(game_state, "current_round", 0)
    talent = getattr(player, "talent", None)
    if talent is not None:
        # G1 繁育倒计时（绝对死亡窗口）
        prop_rounds = getattr(talent, "propagation_rounds", None)
        if prop_rounds is not None and getattr(talent, "form", "") == "propagation":
            windows["propagation_death"] = WindowBrief(
                remaining=max(0, int(prop_rounds)),
                note=m9_text("ai.snapshot.window_propagation_death"))
        # G1 完全燃烧窗口
        burn_until = getattr(talent, "full_burn_until", None)
        if burn_until is not None:
            windows["full_burn"] = WindowBrief(
                remaining=max(0, int(burn_until) - rnd),
                note=m9_text("ai.snapshot.window_full_burn"))
        # G2 影身创建轮
        sh = None
        try:
            sh = talent._shadow() if hasattr(talent, "_shadow") else None
        except Exception:
            sh = None
        if sh is not None and not getattr(sh, "is_terminal_singer", False):
            created = int(getattr(sh, "created_round", 0))
            windows["shadow_slot_next_round"] = WindowBrief(
                remaining=1 if created >= rnd else 0,
                note=m9_text("ai.snapshot.window_shadow_slot"))
    # G7 烟雾弹区域（延续到下轮 R4）
    try:
        smoke = getattr(game_state, "_hoshino_smoke_zones", {}) or {}
        for loc, until in smoke.items():
            if until >= rnd:
                windows.setdefault(f"smoke:{loc}", WindowBrief(
                    remaining=max(0, until - rnd),
                    note=m9_text("ai.snapshot.window_smoke")))
    except Exception:
        pass
    return windows


# ════════════════════════════════════════════════════════════════════
#  AssessmentLayer：minds 派生输出（按执行顺序填充）
# ════════════════════════════════════════════════════════════════════

@dataclass
class AssessmentLayer:
    """minds 的派生评估（分析局势的输出层，不污染 ProjectedSnapshot）。"""

    threat_scores: Dict[str, float] = field(default_factory=dict)
    location_threat: Dict[str, float] = field(default_factory=dict)
    in_combat: bool = False
    combat_target: Optional[str] = None
    danger_mode: bool = False
    kill_targets: Tuple[str, ...] = ()
    best_weapon_damage: float = 0.0
    goal_lifecycle: List[Dict[str, Any]] = field(default_factory=list)
    notes: Dict[str, str] = field(default_factory=dict)


# 兼容别名：v1 调用方仍可用 DecisionSnapshot 名（build 返回 ProjectedSnapshot）
DecisionSnapshot = ProjectedSnapshot
