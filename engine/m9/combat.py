"""M9 战斗结算路径（profile: m9-rfc，结算合同 v0.3）。

A/H 两阶段（`numeric_v2.resolve_hit` 账目）+ `DIRECT_DAMAGE` 身份 + `absolute_dead`
分流 + M9 天赋钩子协议（易伤/减伤/致死替代挂载点）。

架构：v2exp 管线不 import 本模块；`combat.damage_resolver.resolve_damage` 在
`is_enabled("m9_rfc")` 时改调本模块（`resolve_damage`，result 结构兼容），
从而 `resolve_area_damage` / `resolve_location_damage` / attack / hook / shoot / move
全部自动收敛到 M9 语义。v2exp profile 走原管线（字节不变）。

M9 天赋钩子协议（六天赋机制模块实现，阶段 2-7）：
- `talent.m9_on_lethal(target, attacker, source_kind)` -> Optional[str]：致死替代，
  返回替代 kind（g5_homecoming / g1_propagation / g4_savior_consume /
  g2_shadow_dissipated）或 None（不替代）。
- `talent.m9_modify_incoming(hit)`：H 阶段易伤/减伤挂载（G2 终曲易伤、G4 毁伤、
  G7 盾防御等），就地修改 hit.damage。
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

from engine import experiments
from combat import numeric_v2
from engine.m9.resolution import HitResolution
from engine.m9.text import m9_text

_LOGGER = logging.getLogger("engine.m9.combat")


def _log_hook_failure(context: str, exc: Exception) -> None:
    """天赋/援助钩子异常不炸全局回合，但必须留下可诊断痕迹。"""
    _LOGGER.warning("M9 combat %s failed: %s", context, exc)


# 绝对死亡来源白名单（t3_t7 §2.2/§2.5：G5 锚定强制、G4 死星天裁、G7 Terror、
# G1 繁育倒计时）——保险/免死/归家/形态替代全部跳过，直进死亡清理
ABSOLUTE_DEATH_SOURCES: tuple = ("g7_terror", "g4_judgment", "g5_anchor",
                                 "g1_propagation")

# DIRECT_DAMAGE 与 absolute_death 正交：这里只跳过 A 阶段防御，不推导死亡结果。
# 2026-09 风洞 R7：g0_crossfire 移出白名单——十字炮火改走命中+护甲管线
# （G0 头部 27.8%，护甲可减免是唯一机制级压顶通道）。
DIRECT_DAMAGE_SOURCES: tuple = (
    "g4_counter",
    "g4_judgment",
    "g7_terror",
    "world_clock_apocalypse",
)


def m9_enabled() -> bool:
    """M9 结算路径开关。"""
    return experiments.is_enabled("m9_rfc")


def is_absolute_death_source(source_kind: Optional[str]) -> bool:
    return source_kind == "absolute_death" or source_kind in ABSOLUTE_DEATH_SOURCES


def _pp(key: str, default):
    from engine.balance import get as _bget
    return _bget("m9_system", "pp", key, default=default)


def _award_kill_pp(game_state: Any, killer: Any, victim: Any,
                   source_kind: Optional[str],
                   armor_broken: bool = False) -> None:
    """B4 §3.2 PP 生成事件（击杀类）：first_kill/revenge/armor_break/endgame/clutch。

    - 首杀：本局第一次击杀（全局唯一）；
    - 复仇击杀：击杀曾击杀过你的玩家（每名击杀者一次）；
    - 破甲击杀：单次攻击破甲并击杀（每名击杀者一次）；
    - 终焉击杀：世界时钟终焉阶段的击杀（每名击杀者一次）；
    - 绝境击杀：击杀者 HP ≤ 20% 上限时的击杀（每名击杀者一次）。

    击杀归因经 `_combat_score_owner`（影身/召唤物战果归所属玩家）。
    数值读 `m9_system.pp`。
    """
    if game_state is None or killer is None:
        return
    killer = _combat_score_owner(game_state, killer)
    killer_pid = getattr(killer, "player_id", None)
    if not killer_pid or not hasattr(killer, "player_id"):
        return
    pp = getattr(game_state, "m9_pp", None)
    if pp is None:
        return
    used = getattr(game_state, "_m9_pp_events_used", None)
    if used is None:
        used = set()
        game_state._m9_pp_events_used = used
    if ("__global__", "first_kill") not in used:
        used.add(("__global__", "first_kill"))
        pp.earn(killer_pid, int(_pp("first_kill", 2)))
    victim_pid = getattr(victim, "player_id", None)
    if _was_killed_by(game_state, killer_pid, victim_pid) \
            and (killer_pid, "revenge_kill") not in used:
        used.add((killer_pid, "revenge_kill"))
        pp.earn(killer_pid, int(_pp("revenge_kill", 3)))
    if armor_broken and (killer_pid, "armor_break_kill") not in used:
        used.add((killer_pid, "armor_break_kill"))
        pp.earn(killer_pid, int(_pp("armor_break_kill", 2)))
    if _in_apocalypse(game_state) and (killer_pid, "endgame_kill") not in used:
        used.add((killer_pid, "endgame_kill"))
        pp.earn(killer_pid, int(_pp("endgame_kill", 2)))
    max_hp = float(getattr(killer, "max_hp", 20) or 20)
    if float(getattr(killer, "hp", 0)) <= max_hp * 0.2 \
            and (killer_pid, "clutch_kill") not in used:
        used.add((killer_pid, "clutch_kill"))
        pp.earn(killer_pid, int(_pp("clutch_kill", 2)))


def _was_killed_by(game_state: Any, killer_pid: str,
                   victim_pid: Optional[str]) -> bool:
    """victim 是否曾击杀 killer（复仇击杀判定：扫描公开死亡事件）。"""
    if not victim_pid:
        return False
    for ev in getattr(game_state, "event_log", []):
        if not isinstance(ev, dict) or ev.get("type") != "death":
            continue
        if ev.get("player") == killer_pid and ev.get("killer") == victim_pid:
            return True
    return False


def _in_apocalypse(game_state: Any) -> bool:
    """世界时钟是否处于终焉阶段（终焉击杀判定）。"""
    try:
        from engine import world_clock
        return world_clock.current_phase(game_state) == world_clock.APOCALYPSE
    except Exception as exc:
        _log_hook_failure("world clock phase check", exc)
        return False


def _armor_broken_only(broken: List[str]) -> bool:
    """hit.broken 中是否存在真实护甲击破（掩体不计入破甲）。"""
    return any(piece for piece in broken if "掩体" not in str(piece))


def is_direct_damage_source(source_kind: Optional[str]) -> bool:
    return source_kind == "direct_damage" or source_kind in DIRECT_DAMAGE_SOURCES


def _combat_score_owner(game_state: Any, actor: Any) -> Any:
    """把影身/独立召唤物的战果归到所属玩家；普通玩家保持原身份。"""
    if actor is None or game_state is None:
        return actor
    owner_pid = getattr(actor, "owner_pid", None)
    if owner_pid:
        owner = game_state.get_player(owner_pid)
        if owner is not None:
            return owner
    return actor


class DeathAdjudicator:
    """致死分流（结算合同 §6）：绝对死亡 → 天赋致死替代 → 免死/保险 → 真死亡。

    真死亡返回 kind="dead"（killed=True，触发上层玩家死亡流程）；
    替代返回对应 kind（killed=False，非玩家死亡，不触发 T7/往世层/击杀记录）。
    """

    def __init__(self, game_state: Any) -> None:
        self.game_state = game_state

    def adjudicate(self, target: Any, attacker: Any,
                   source_kind: Optional[str]) -> str:
        # 1. 绝对死亡：跳过一切替代/免死/保险（G5 归家、G1 繁育、G4 形态消耗、
        #    G2 影身消散、T7、G4 人形态免死全部不生效）
        if is_absolute_death_source(source_kind):
            return "dead"

        # 2. M9 天赋致死替代协议（六天赋机制：G5 归家/G1 繁育/G4 形态内消耗/
        #    G2 影身消散）
        talent = getattr(target, "talent", None)
        if talent is None and getattr(target, "_m9_shadow_actor", False):
            owner = self.game_state.get_player(getattr(target, "owner_pid", ""))
            talent = getattr(owner, "talent", None)
        if talent is not None and hasattr(talent, "m9_on_lethal"):
            try:
                kind = talent.m9_on_lethal(target, attacker, source_kind)
            except Exception as exc:
                _log_hook_failure("m9_on_lethal", exc)
                kind = None
            if kind:
                return kind

        # 3. 免死/保险（复用 v2exp 天赋死亡链：T7 死者苏生、G4 人形态免死等——
        #    非绝对死亡时按原语义赔付；M9 天赋对象同样实现 on_death_check 时照常）
        from combat.damage_resolver import _talent_death_check
        if _talent_death_check(target, attacker, self.game_state):
            return "prevented"

        # 4. 真死亡
        return "dead"


def _base_result(target: Any) -> Dict[str, Any]:
    return {
        "success": False,
        "reason": "",
        "raw_damage": 0,
        "final_damage": 0,
        "armor_hit": None,
        "armor_broken": False,
        "hp_damage": 0,
        "target_hp": getattr(target, "hp", 0),
        "target_hp_before": getattr(target, "hp", 0),
        "stunned": False,
        "shocked": False,
        "killed": False,
        "details": [],
        "m9_kind": "",
        "absolute_dead": False,
        "death_finalized": False,
    }


def _apply_attack(target: Any, raw_int: int, attr: str, *,
                  pierce_factor: float = 1.0,
                  direct_damage: bool = False,
                  force_min: bool = False,
                  read_only: bool = False) -> HitResolution:
    """A 阶段 + H 阶段账目：DIRECT_DAMAGE 直接 H=A（跳过防御/护甲/固定减伤），
    否则经 numeric_v2.resolve_hit（减法防御 + 耐久磨损 + 25% 保底）。

    read_only：探针模式——账目照算（damage/broken 照出），但耐久磨损不落盘。
    投影/AI 评估是只读操作，不得改写世界状态（docs/m9/ai/talents.md §3.1）。
    """
    if direct_damage:
        return HitResolution(raw=raw_int, attribute=attr, defense=0,
                             damage=raw_int, a_phase_absorbed=0, broken=[],
                             grazed=False, effective_hit=raw_int >= 1,
                             direct_damage=True)
    armor = getattr(target, "armor", None)
    snapshot = None
    if read_only and armor is not None:
        getter = getattr(armor, "get_all_active", None)
        if callable(getter):
            pieces = list(getter() or [])
        else:
            pieces = list(getattr(armor, "outer", []) or []) + \
                list(getattr(armor, "inner", []) or [])
        snapshot = [(p, getattr(p, "durability", 0)) for p in pieces
                    if p is not None]
    res = numeric_v2.resolve_hit(
        target, raw_int, attr, force_min=force_min,
        pierce_factor=pierce_factor)
    if snapshot is not None and armor is not None:
        for piece, dur in snapshot:
            try:
                piece.durability = dur
            except Exception as exc:
                _log_hook_failure("read-only armor snapshot restore", exc)
    return HitResolution(
        raw=raw_int,
        attribute=attr,
        defense=res["defense"],
        damage=res["damage"],
        a_phase_absorbed=res["absorbed"],
        broken=list(res["broken"]),
        grazed=res["grazed"],
        effective_hit=(res["damage"] >= 1) or bool(res["broken"]),
    )


def _break_love_wish_from_attack(attacker: Any, target: Any) -> None:
    talent = getattr(attacker, "talent", None)
    if talent is None or getattr(
            attacker, "_cutaway_suppress_attacker_hooks", False):
        return
    if hasattr(talent, "break_love_wish"):
        talent.break_love_wish(getattr(target, "player_id", ""))


def _love_wish_blocks(attacker: Any, target: Any) -> bool:
    if attacker is None or target is None:
        return False
    talent = getattr(target, "talent", None)
    return bool(
        talent is not None
        and hasattr(talent, "has_love_wish")
        and talent.has_love_wish(getattr(attacker, "player_id", ""))
    )


def _feed_g5_combat(game_state: Any, attacker: Any, target: Any,
                    effective: bool) -> None:
    """G5 追忆喂入（DOC-046）：任意有效战斗每轮一次；小昔涟亲历再叠加。

    `effective` = 实际 HP 损失或护甲击破（未命中/无有效伤害不算亲历）。
    仅当本局存在 G5 小昔涟时生效；两层去重由 `Ripple9.feed_combat_round` 承担。
    """
    if not effective or game_state is None:
        return
    for pid in getattr(game_state, "player_order", []):
        p = game_state.get_player(pid)
        if p is None or p.talent is None:
            continue
        if hasattr(p.talent, "feed_combat_round"):
            personal = (
                getattr(attacker, "player_id", None) == pid
                or getattr(target, "player_id", None) == pid
            )
            try:
                p.talent.feed_combat_round(personal=personal)
            except Exception as exc:
                _log_hook_failure("G5 feed_combat_round", exc)
            break


def _apply_spotlight_focus(game_state: Any, attacker: Any,
                           hit: HitResolution) -> None:
    """G2 追光诗（诗篇 v0.1 §2 追光）：G2 普通影身的下一次合法攻击获得
    `poem_spotlight_damage` 固定攻击方加值；攻击结算后标记消费；造成有效伤害
    时结算后恢复影身 `poem_spotlight_shadow_heal` HP（不超过最大 HP）。"""
    if game_state is None or hit is None:
        return
    if not getattr(attacker, "_m9_shadow_actor", False):
        return
    owner_pid = getattr(attacker, "owner_pid", "")
    owner = game_state.get_player(owner_pid) if owner_pid else None
    if owner is None or owner.talent is None:
        return
    markers = getattr(owner.talent, "m9_poem_markers", None)
    if not markers or not markers.pop("spotlight_focus", None):
        return
    from engine.balance import get as bget
    bonus = int(bget("m9_talents_extended", "g5",
                     "poem_spotlight_damage", default=1))
    heal = int(bget("m9_talents_extended", "g5",
                    "poem_spotlight_shadow_heal", default=1))
    hit.damage += bonus
    hit._spotlight_heal = heal
    game_state.log_event("poem_spotlight_consumed", player=owner_pid,
                         bonus=bonus, heal=heal)


def _world_poem_followup(game_state: Any, attacker: Any, target: Any,
                         effective: bool) -> None:
    """G0 世界援助·星野追演（合同 v0.1 §八场景 3/4/7）：每名黑马每全局轮
    第一次合法攻击根命中后，对同一目标追加一段基础近战追演；追演命中且目标
    仍存活时施加「震荡」（同级不叠加、高位控制不覆盖）。

    追演自身递归本结算器，但 `should_followup_attack` 已按轮去重，不会循环；
    追演不入 G0×G7 联动、不给 G4 火种（来源走普通近战账目）。
    """
    if not effective or game_state is None or attacker is None:
        return
    attacker_pid = getattr(attacker, "player_id", None)
    if attacker_pid is None or getattr(
            attacker, "_cutaway_suppress_attacker_hooks", False):
        return
    from engine.m9.g0_world_poem import world_poem_aid_of
    aid = world_poem_aid_of(game_state)
    if aid is None or not aid.activated:
        return
    if not aid.should_followup_attack(
            attacker_pid, getattr(game_state, "current_round", 0)):
        return
    if not getattr(target, "is_alive", lambda: False)():
        return
    base = 1.0
    for w in getattr(attacker, "weapons", []) or []:
        if w is not None and getattr(w, "get_effective_damage", None) is not None:
            try:
                base = max(base, float(w.get_effective_damage()))
            except Exception as exc:
                _log_hook_failure("world poem weapon damage read", exc)
    punch = aid.followup_punch_raw(base)
    punch = max(0.0, float(punch))
    if punch <= 0:
        return
    try:
        followup = resolve_damage(
            attacker, target, None, game_state,
            raw_damage_override=punch,
            damage_attribute_override="普通",
            source_kind="world_poem_followup",
            _skip_outgoing_hook=True,
        )
    except Exception as exc:
        _log_hook_failure("world poem followup resolve", exc)
        return
    if not getattr(target, "is_alive", lambda: False)():
        return
    if followup.get("final_damage", 0) < 1 and not followup.get("armor_broken"):
        return
    # 震荡：标准受限菜单，同级不叠加、高位控制不覆盖（不设压制版本）。
    markers = getattr(game_state, "markers", None)
    if markers is not None and not getattr(target, "is_shocked", False):
        if not markers.has(getattr(target, "player_id", ""), "PETRIFIED") \
                and not getattr(target, "_m9_suppressed", False):
            target.is_shocked = True
            markers.add(getattr(target, "player_id", ""), "SHOCKED")
            game_state.log_event("world_poem_shock", attacker=attacker_pid,
                                 target=getattr(target, "player_id", None))


def resolve_damage(attacker, target, weapon, game_state,
                   target_layer=None, target_armor_attr=None,
                   ignore_element=False, damage_multiplier=1.0,
                   bonus_damage=0.0,
                   ignore_counter=False,
                   ignore_last_inner_absorb=False,
                   raw_damage_override=None,
                   damage_attribute_override=None,
                   is_talent_attack=False,
                   is_love_poem=False,
                   is_embrace_damage=False,
                   *,
                   displacement_only=False,
                   is_opportunity_attack=False,
                   armor_pierce_factor=1.0,
                   accuracy_bonus=0,
                   source_kind: Optional[str] = None,
                   _skip_outgoing_hook: bool = False) -> Dict[str, Any]:
    """M9 完整伤害结算（签名与 damage_resolver.resolve_damage 兼容）。

    source_kind 是 M9 扩展参数：默认 None（普通攻击）；绝对死亡来源传入
    "g7_terror" / "g4_judgment" / "g5_anchor" / "g1_propagation" / "absolute_death"。
    """
    result = _base_result(target)
    target_hp_before = getattr(target, "hp", 0)
    result["target_hp_before"] = target_hp_before

    _break_love_wish_from_attack(attacker, target)
    if _love_wish_blocks(attacker, target):
        result["success"] = True
        result["reason"] = m9_text("combat.reason_love_wish_immune")
        result["details"].append(
            m9_text("combat.love_wish_blocks", attacker_name=attacker.name,
                    target_name=target.name))
        return result

    # raw 计算：武器有效伤害（含蓄力）/ 无武器 override；乘数 + 加伤
    raw = 0.0
    attr = "普通"
    if weapon is not None:
        raw = float(weapon.get_effective_damage())
        attr_obj = getattr(weapon, "attribute", None)
        attr = getattr(attr_obj, "value", "普通")
    else:
        raw = float(raw_damage_override or 0.0)
        attr = damage_attribute_override or "__无视__"
    raw = raw * damage_multiplier + bonus_damage

    # M9 PP 加伤（special PP加伤，2026-09 修复）：只作用于下一次真实伤害结算。
    if attacker is not None:
        pp_bonus = getattr(attacker, "_m9_pp_damage_bonus", 0)
        if pp_bonus:
            raw += float(pp_bonus)
            attacker._m9_pp_damage_bonus = 0
            result["details"].append(
                m9_text("combat.pp_bonus_detail", pp_bonus=f"{pp_bonus:g}"))

    # 天赋出伤协议：prepare 可以为主段改属性/穿防，并声明后续独立
    # 命中段（G0 无人机）。独立段递归本结算器，但禁止再次准备出伤
    # 计划，避免 G0 递归及其他天赋二次改写固定段。
    t_attacker = getattr(attacker, "talent", None) if attacker else None
    outgoing_plan = None
    after_settlement = None
    bonus_hits: List[Dict[str, Any]] = []
    if (not _skip_outgoing_hook and t_attacker is not None
            and hasattr(t_attacker, "m9_prepare_outgoing")):
        try:
            candidate = t_attacker.m9_prepare_outgoing(
                attacker, target, weapon, raw)
            if isinstance(candidate, dict):
                outgoing_plan = candidate
        except Exception as exc:
            _log_hook_failure("m9_prepare_outgoing", exc)
    if outgoing_plan is not None:
        raw = float(outgoing_plan.get("raw", raw))
        attr = str(outgoing_plan.get("attribute", attr))
        armor_pierce_factor = float(outgoing_plan.get(
            "armor_pierce_factor", armor_pierce_factor))
        declared_hits = outgoing_plan.get("bonus_hits", [])
        if isinstance(declared_hits, list):
            bonus_hits = [segment for segment in declared_hits
                          if isinstance(segment, dict)]
        callback = outgoing_plan.get("after_settlement")
        if callable(callback):
            after_settlement = callback
    elif (not _skip_outgoing_hook and t_attacker is not None
          and hasattr(t_attacker, "m9_modify_outgoing")):
        try:
            raw = t_attacker.m9_modify_outgoing(attacker, target, weapon, raw)
        except Exception as exc:
            _log_hook_failure("m9_modify_outgoing", exc)

    raw_int = max(0, int(round(raw)))
    result["raw_damage"] = float(raw)
    if raw_int <= 0:
        result["success"] = True
        result["reason"] = "zero_damage"
        if after_settlement is not None:
            after_settlement(result)
        return result

    # M3 命中层：M9 伤害同样服从“闪避失败 = 擦伤”合同；命中加值只
    # 修改 chance，绝不能混入 raw damage。DIRECT_DAMAGE 不掷命中。
    grazed_by_evasion = False
    if not is_direct_damage_source(source_kind):
        try:
            from engine import experiments as _exp
            if _exp.is_enabled("m3_accuracy"):
                from combat.accuracy import compute_hit_chance, roll_hit
                chance, _breakdown = compute_hit_chance(
                    attacker, target, weapon, game_state,
                    is_aoo=is_opportunity_attack)
                talent_bonus = 0
                if (t_attacker is not None
                        and hasattr(t_attacker, "m9_accuracy_bonus")):
                    talent_bonus = int(t_attacker.m9_accuracy_bonus(
                        attacker, target, weapon, source_kind))
                chance = max(0, min(100, int(chance)
                                    + int(accuracy_bonus or 0)
                                    + talent_bonus))
                if chance < 100:
                    was_hit, roll = roll_hit(chance)
                    if was_hit:
                        result["details"].append(
                            m9_text("combat.hit_roll_hit", chance=chance, roll=roll))
                    else:
                        grazed_by_evasion = True
                        result["grazed_by_evasion"] = True
                        result["details"].append(
                            m9_text("combat.hit_roll_grazed", chance=chance,
                                    roll=roll))
        except Exception as exc:
            _log_hook_failure("accuracy hit roll", exc)

    # 被动进攻援助（B4 §5.1）：首攻 + 往世层非空 → 系统指派死者提供效果。
    # 在伤害结算前触发，标记型效果（如 T1 防御归零）可附着于本次攻击。
    if game_state is not None and attacker is not None \
            and getattr(game_state, "m9_enabled", False):
        try:
            from engine.m9.aids import trigger_passive_attack_aid
            trigger_passive_attack_aid(attacker, target, game_state, None)
        except Exception as exc:
            _log_hook_failure("passive attack aid", exc)

    # A/H 两阶段账目（DIRECT_DAMAGE 身份由调用方经 source_kind 或 direct 标志给出）
    hit = _apply_attack(
        target,
        raw_int,
        attr,
        pierce_factor=armor_pierce_factor,
        direct_damage=is_direct_damage_source(source_kind),
        force_min=grazed_by_evasion,
    )
    hit._attacker = attacker
    _apply_police_cover(game_state, target, hit)
    # G2 追光诗：普通影身攻击的固定攻击方加值（标记随本次结算消费）
    _apply_spotlight_focus(game_state, attacker, hit)

    # H 阶段易伤/减伤挂载（M9 天赋协议）
    t_target = getattr(target, "talent", None)
    if t_target is not None and hasattr(t_target, "m9_modify_incoming"):
        try:
            t_target.m9_modify_incoming(hit)
        except Exception as exc:
            _log_hook_failure("m9_modify_incoming", exc)

    # 终曲区域：全员易伤（攻击方侧固定加值，合同 G2 §8.1）
    area = _terminal_area_of(game_state, target)
    if area is not None:
        hit.damage += area.vulnerability()

    # 结算后事件钩子（火种计数/标记；G4 W2 敌对来源等）
    if t_target is not None and hasattr(t_target, "m9_on_hit"):
        try:
            t_target.m9_on_hit(hit)
        except Exception as exc:
            _log_hook_failure("m9_on_hit", exc)

    # 攻击方侧 M9 钩子（T2 攻击回盾等；借用来源不触发）
    if attacker is not None and not getattr(
            attacker, "_cutaway_suppress_attacker_hooks", False):
        t_attacker_hook = getattr(attacker, "talent", None)
        if t_attacker_hook is not None and hasattr(
                t_attacker_hook, "m9_on_attack"):
            try:
                t_attacker_hook.m9_on_attack(hit, target)
            except Exception as exc:
                _log_hook_failure("m9_on_attack", exc)

    # 近战攻击造成伤害后：隐身临时失效（README 9.3.3；M9 收敛同语义——
    # T2 零击杀隐身豁免）
    from models.equipment import WeaponRange
    if (attacker is not None and weapon is not None and game_state is not None
            and getattr(game_state, "markers", None) is not None
            and not getattr(attacker, "_cutaway_skip_stealth_suppress", False)
            and not (attacker.talent
                     and getattr(attacker.talent, "stealth_on_zero_kills", False)
                     and getattr(attacker, "kill_count", 0) == 0)
            and getattr(weapon, "weapon_range", None) is WeaponRange.MELEE
            and hit.damage >= 1
            and game_state.markers.has(attacker.player_id, "INVISIBLE")
            and game_state.markers.has_relation(
                attacker.player_id, "ENGAGED_WITH",
                getattr(target, "player_id", ""))):
        try:
            game_state.markers.on_engaged_melee_attack_by_invisible(
                attacker.player_id, getattr(target, "player_id", ""))
            result["stealth_suppressed"] = True
        except Exception as exc:
            _log_hook_failure("stealth suppress marker", exc)

    result["final_damage"] = float(hit.damage)
    result["armor_broken"] = bool(hit.broken)
    result["armor_hit"] = hit.broken[0] if hit.broken else None
    result["details"].append(
        m9_text("combat.damage_detail", raw=hit.raw, defense=hit.defense,
                damage=hit.damage)
        + (m9_text("combat.damage_direct_suffix") if hit.direct_damage else ""))

    # H 阶段扣 HP：天赋 temp-HP 吸收链（G7 光环/盾、G4 第二血条、G1 炽愿——
    # 兼容 v2exp 天赋的 receive_damage_to_temp_hp 协议，M9 天赋同样实现）
    remaining = max(0, hit.damage)
    t_target = getattr(target, "talent", None)
    if remaining > 0 and t_target is not None and hasattr(
            t_target, "receive_damage_to_temp_hp"):
        try:
            remaining = t_target.receive_damage_to_temp_hp(
                remaining, is_embrace=False)
            remaining = max(0, float(remaining))
        except Exception as exc:
            _log_hook_failure("receive_damage_to_temp_hp", exc)
    # 终曲伤害共享（shared_post_mitigation）：每个承受者独立进入死亡裁决。
    allocations = _damage_distribution(game_state, target, int(remaining))
    adjudicator = DeathAdjudicator(game_state)
    for member, amount in allocations:
        if amount <= 0:
            continue
        was_alive = member.is_alive()
        member_hp_before = getattr(member, "hp", 0)
        member.hp = max(0, member_hp_before - amount)
        actual_hp_damage = max(0, member_hp_before - member.hp)
        if actual_hp_damage > 0 and game_state is not None:
            game_state.record_combat_damage(
                _combat_score_owner(game_state, attacker),
                _combat_score_owner(game_state, member),
                actual_hp_damage,
            )
        if member is not target and member_hp_before - member.hp >= 1:
            petrify = getattr(game_state, "m9_petrify", None)
            if petrify is not None:
                petrify.on_effective_hit(game_state, member)
        if member is target or not was_alive or member.hp > 0:
            continue
        kind = adjudicator.adjudicate(member, attacker, source_kind)
        if kind == "dead":
            finalize_death(
                game_state, member, attacker,
                source_kind=source_kind, cause="m9_shared_damage")

    target_hp_after_hit = getattr(target, "hp", 0)
    # 被动防御援助（B4 §5.1）：敌对伤害后存活且 HP≤阈值 + 往世层非空。
    # 一击致死不触发（此处 hp>0 才进入）；自我代价不经本结算器故天然排除。
    if game_state is not None and target_hp_after_hit > 0 \
            and getattr(game_state, "m9_enabled", False):
        try:
            from engine.m9.aids import trigger_passive_defense_aid
            trigger_passive_defense_aid(target, attacker, game_state, hit)
        except Exception as exc:
            _log_hook_failure("passive defense aid", exc)
    # 死亡裁决
    if getattr(target, "hp", 0) <= 0:
        adjudicator = DeathAdjudicator(game_state)
        kind = adjudicator.adjudicate(target, attacker, source_kind)
        result["m9_kind"] = kind
        if kind == "dead":
            result["killed"] = True
            result["absolute_dead"] = is_absolute_death_source(source_kind)
            result["death_finalized"] = finalize_death(
                game_state, target, attacker,
                source_kind=source_kind, cause=source_kind or "attack",
                armor_broken=_armor_broken_only(hit.broken))
    result["hp_damage"] = round(
        max(0, target_hp_before - target_hp_after_hit), 2)
    result["target_hp"] = getattr(target, "hp", 0)
    result["success"] = True
    # 有效命中必须以实际 HP 损失或护甲/掩体击破为事实；临时 HP 全额吸收不摇晃。
    petrify = getattr(game_state, "m9_petrify", None)
    if petrify is not None and (
            result["hp_damage"] >= 1 or bool(hit.broken)):
        petrify.on_effective_hit(game_state, target)
    # 追光诗第二效果：有效伤害结算后恢复影身 HP（诗篇 v0.1 §2 追光②）
    shadow_heal = int(getattr(hit, "_spotlight_heal", 0))
    if shadow_heal > 0 and (result["hp_damage"] >= 1 or bool(hit.broken)):
        attacker.hp = min(getattr(attacker, "max_hp", getattr(attacker, "hp", 0)),
                          getattr(attacker, "hp", 0) + shadow_heal)

    # G5 追忆喂入：任意有效战斗每轮一次 + 小昔涟亲历叠加（DOC-046）
    _feed_g5_combat(
        game_state, attacker, target,
        effective=(result["hp_damage"] >= 1 or bool(hit.broken)))
    # G0 世界援助·星野追演：黑马首次有效攻击根命中后追加近战+震荡
    _world_poem_followup(
        game_state, attacker, target,
        effective=(result["hp_damage"] >= 1 or bool(hit.broken)))

    # 追加段只在前一段结算后目标仍存活时发生；每段都独立走
    # 属性防御、护甲耐久、临时 HP 和死亡裁决，外层只聚合公开结果。
    for index, segment in enumerate(bonus_hits, start=1):
        if not getattr(target, "is_alive", lambda: False)():
            break
        segment_raw = max(0.0, float(segment.get("raw", 0.0)))
        if segment_raw <= 0:
            continue
        segment_attr = str(segment.get("attribute", "普通"))
        segment_source = segment.get("source_kind")
        segment_pierce = float(segment.get("armor_pierce_factor", 1.0))
        child = resolve_damage(
            attacker, target, None, game_state,
            raw_damage_override=segment_raw,
            damage_attribute_override=segment_attr,
            armor_pierce_factor=segment_pierce,
            source_kind=segment_source,
            _skip_outgoing_hook=True,
        )
        result["raw_damage"] += float(child.get("raw_damage", 0))
        result["final_damage"] += float(child.get("final_damage", 0))
        result["armor_broken"] = bool(
            result["armor_broken"] or child.get("armor_broken", False))
        if result["armor_hit"] is None and child.get("armor_hit") is not None:
            result["armor_hit"] = child["armor_hit"]
        result["killed"] = bool(result["killed"] or child.get("killed", False))
        result["absolute_dead"] = bool(
            result["absolute_dead"] or child.get("absolute_dead", False))
        result["death_finalized"] = bool(
            result["death_finalized"] or child.get("death_finalized", False))
        if child.get("m9_kind"):
            result["m9_kind"] = child["m9_kind"]
        result["details"].extend(
            m9_text("combat.bonus_segment_detail", index=index, detail=detail)
            for detail in child.get("details", []))
        result["target_hp"] = getattr(target, "hp", 0)
        result["hp_damage"] = round(
            max(0, target_hp_before - getattr(target, "hp", 0)), 2)

    if after_settlement is not None:
        after_settlement(result)
    return result


def _terminal_area_of(game_state: Any, target: Any):
    """目标所在地点的终曲区域（若存在）。"""
    if game_state is None:
        return None
    loc = getattr(target, "location", None)
    for owner in getattr(game_state, "player_order", []):
        p = game_state.get_player(owner)
        if p is None or p.talent is None:
            continue
        talent = p.talent
        if hasattr(talent, "area_for_target"):
            try:
                area = talent.area_for_target(target)
            except Exception as exc:
                _log_hook_failure("terminal area_for_target", exc)
                area = None
            if area is not None:
                return area
    return None


def _apply_police_cover(game_state: Any, target: Any,
                        hit: HitResolution) -> None:
    """把 T6 警察掩体 + G7 援助掩体并入 A 阶段，并维持原始 raw 的 25% 总保底。"""
    if game_state is None or hit.direct_damage:
        return
    player_id = getattr(target, "player_id", "")
    if not player_id:
        return
    station = getattr(game_state, "m9_police", None)
    cover = 0
    if station is not None:
        cover = int(station.player_cover(player_id))
    aid_cover = int(getattr(target, "_aid_g7_cover_durability", 0))
    if cover <= 0 and aid_cover <= 0:
        return
    floor = numeric_v2.min_damage(hit.raw)
    if cover > 0:
        requested = min(cover, max(0, hit.damage - floor))
        if requested > 0:
            overflow = station.absorb_player_cover(player_id, requested)
            absorbed = max(0, requested - overflow)
            hit.damage = max(floor, hit.damage - absorbed)
            hit.a_phase_absorbed += absorbed
            if absorbed >= cover:
                hit.broken.append("警察掩体")
    if aid_cover > 0:
        rnd = getattr(game_state, "current_round", 1)
        if getattr(target, "_aid_g7_cover_until", 0) >= rnd:
            requested = min(aid_cover, max(0, hit.damage - floor))
            if requested > 0:
                target._aid_g7_cover_durability = max(
                    0, aid_cover - requested)
                hit.damage = max(floor, hit.damage - requested)
                hit.a_phase_absorbed += requested
                if target._aid_g7_cover_durability <= 0:
                    hit.broken.append("援助掩体")


def _damage_distribution(game_state: Any, target: Any,
                         damage: int) -> List[tuple[Any, int]]:
    """终曲伤害共享（合同 G2 §8.2）：S = floor(H0 × ratio) 分给区域内所有
    存活单位（含原目标），余数按 actor_id 升序各 +1；总量守恒，无重复共享。"""
    area = _terminal_area_of(game_state, target)
    if area is None or damage <= 0:
        return [(target, damage)]
    ratio = area.damage_share_ratio()
    if ratio <= 0:
        return [(target, damage)]
    loc = getattr(target, "location", None)
    actors = game_state.iter_actors() if hasattr(
        game_state, "iter_actors") else (
            game_state.get_player(pid) for pid in game_state.player_order)
    members = [
        actor for actor in actors
        if actor is not None
        and actor.is_alive()
        and getattr(actor, "location", None) == loc
    ]
    if len(members) <= 1:
        return [(target, damage)]
    shared = min(damage, int(math.floor(damage * ratio)))
    if shared <= 0:
        return [(target, damage)]
    per = shared // len(members)
    rem = shared % len(members)
    ordered = sorted(members, key=lambda m: _actor_id(m))
    # 修复：以 actor_id 为 dict 键（警察单位对象不可哈希；终曲共享崩溃根因）
    allocations: Dict[str, int] = {_actor_id(m): per for m in ordered}
    for i, m in enumerate(ordered):
        if i < rem:
            allocations[_actor_id(m)] += 1
    allocations[_actor_id(target)] = (
        allocations.get(_actor_id(target), 0) + damage - shared)
    return [(member, allocations.get(_actor_id(member), 0))
            for member in ordered]


def _actor_id(actor: Any) -> str:
    return str(getattr(actor, "player_id", getattr(actor, "unit_id", "")))


def finalize_death(game_state: Any, target: Any, attacker: Any = None, *,
                   source_kind: Optional[str] = None,
                   cause: Optional[str] = None,
                   armor_broken: bool = False) -> bool:
    """幂等完成一次 M9 真死亡收尾。

    这是伤害、共享伤害、环境伤害和显式 HP 成本共同使用的唯一写边界。
    它不执行免死裁决；需要免死/保险的来源应先经过 ``DeathAdjudicator``。
    返回 ``True`` 表示本次真正完成了收尾，重复调用返回 ``False``。
    """
    if target is None or getattr(target, "hp", 0) > 0:
        return False
    if getattr(target, "_m9_death_finalized", False):
        return False
    target._m9_death_finalized = True
    target.hp = 0
    target_id = _actor_id(target)
    killer_id = _actor_id(attacker) if attacker is not None else None

    # 绝对死亡分流（结算 v0.3 §6.8）：PP 冻结——不可支出/不衰减，已托管赌注仍
    # 终局结算；不进往世层、不参与投注/转仓/魂援。G0 撤退/G5 闭合各自冻结，
    # 此处覆盖 G4 天裁、G7 Terror、G5 锚定、G1 繁育倒计时的绝对致死路径。
    if is_absolute_death_source(source_kind) and getattr(target, "player_id", None):
        m9_pp = getattr(game_state, "m9_pp", None) if game_state is not None else None
        if m9_pp is not None and hasattr(m9_pp, "freeze"):
            try:
                m9_pp.freeze(target.player_id)
            except Exception as exc:
                _log_hook_failure("PP freeze on absolute death", exc)

    if attacker is not None and hasattr(attacker, "kill_count"):
        attacker.kill_count += 1
        # B4 §3.2 PP 生成事件（击杀类）：首杀/复仇/破甲/终焉/绝境
        _award_kill_pp(game_state, attacker, target, source_kind,
                       armor_broken=armor_broken)
    attacker_talent = getattr(attacker, "talent", None) if attacker else None
    if (attacker_talent is not None and hasattr(attacker_talent, "on_kill")
            and not getattr(attacker, "_cutaway_suppress_attacker_hooks", False)):
        attacker_talent.on_kill(attacker, target)
    target_talent = getattr(target, "talent", None)
    if target_talent is not None and hasattr(target_talent, "cleanup_on_death"):
        target_talent.cleanup_on_death()
    if target_talent is not None and hasattr(
            target_talent, "on_player_death_check"):
        target_talent.on_player_death_check(target)

    if game_state is None:
        return True
    markers = getattr(game_state, "markers", None)
    if markers is not None and target_id:
        markers.on_player_death(target_id)
    police_engine = getattr(game_state, "police_engine", None)
    if police_engine is not None and target_id:
        police_engine.on_player_death(target_id)
    petrify = getattr(game_state, "m9_petrify", None)
    if petrify is not None:
        petrify.remove_by_id(target_id)

    ish = getattr(game_state, "ish_bosheth", None)
    if (ish is not None and getattr(ish, "phase", None) in ("active", "duet")
            and target_id == getattr(ish, "g2_owner_id", None)):
        ish.end_ish_bosheth("death", game_state)
    if target_id in getattr(game_state, "players", {}):
        drop_loot = getattr(game_state, "drop_loot_on_death", None)
        if drop_loot is not None:
            drop_loot(target)

    game_state.log_event(
        "death", player=target_id, killer=killer_id,
        cause=cause or source_kind or "m9_damage",
        source_kind=source_kind,
    )
    from engine.round_manager import RoundManager
    RoundManager.notify_all_talents_of_death(
        game_state, target_id, killer_id=killer_id)
    return True


def adjudicate_and_finalize_death(
        game_state: Any, target: Any, attacker: Any = None, *,
        source_kind: Optional[str] = None,
        cause: Optional[str] = None) -> str:
    """对已经降至 0 HP 的 actor 执行免死裁决并在需要时完成真死亡。"""
    if target is None or getattr(target, "hp", 0) > 0:
        return "survived"
    kind = DeathAdjudicator(game_state).adjudicate(
        target, attacker, source_kind)
    if kind == "dead":
        finalize_death(
            game_state, target, attacker,
            source_kind=source_kind, cause=cause)
    return kind


def resolve_hit_probe(target: Any, raw_int: int, attr: str,
                      pierce_factor: float = 1.0) -> HitResolution:
    """只算账不落 HP/耐久的 A/H 探针（AI 评估/预检用，信源与结算同一函数）。"""
    return _apply_attack(target, raw_int, attr, pierce_factor=pierce_factor,
                         read_only=True)
