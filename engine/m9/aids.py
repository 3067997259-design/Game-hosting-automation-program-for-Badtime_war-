"""M9 魂援·26 项天赋援助执行器（profile: m9-rfc，B4 RFC v0.4 §5.3）。

结构：`AID_EFFECTS[槽位][attack|defense] = fn(requestor, game_state, ctx) -> str`。
- 主动援助：生者发起、出价 PP 转移给成交死者、天赋专属效果生效；
- 被动援助：系统指派提供者，提供者得 `aid_passive_reward`（铸造）；
- 公共结构（额度/成交/提供者指派/PP 转移）由 `PPLedger` 提供，本模块只做效果；
- 触发钩子（主动附着攻击根 / 防御标记行动 / 被动首攻/濒死）在引擎接线层调用；
- G5 简化标记白名单、G6 可复制模板、G3 前摇截断、T3 `aid_rest` 走结构协议。

数值一律读 `m9_system.pp.*`（[待风洞]）；不另设隐藏默认值。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from engine.balance import get as bget
from engine.m9.text import m9_text


def _pp(key: str, default):
    return bget("m9_system", "pp", key, default=default)


def _round_half_up(x: float) -> int:
    """round half up（正数）；RFC 多处 `round_half_up` 语义。"""
    return int(x + 0.5) if x >= 0 else -int(-x + 0.5)


def _pids_of(game_state: Any) -> list:
    return list(getattr(game_state, "player_order", []))


def _alive_others(game_state: Any, exclude_pid: str) -> list:
    return [pid for pid in _pids_of(game_state)
            if pid != exclude_pid
            and game_state.get_player(pid).is_alive()]


def _log(game_state: Any, event: str, **kw) -> None:
    try:
        game_state.log_event(event, **kw)
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════
#  原初天赋
# ════════════════════════════════════════════════════════════════

def _t1_attack(requestor: Any, game_state: Any, ctx: dict) -> str:
    """T1 进攻援助：本次攻击属性防御 E 按 0 计（护甲耐久与掩体照常）。"""
    requestor._aid_t1_zero_def_once = True
    _log(game_state, "aid_effect", talent="T1", kind="attack",
         player=requestor.player_id)
    return m9_text("aids.t1_attack")


def _t1_defense(requestor: Any, game_state: Any, ctx: dict) -> str:
    """T1 防御援助：R=round_half_up(H×ratio)，以 R 沿原攻击属性反伤一次。"""
    ratio = float(_pp("t1_counter_ratio", 0.5))
    incoming = ctx.get("incoming_hit") or ctx.get("hit")
    if incoming is None:
        return m9_text("aids.t1_defense_no_incoming")
    H = float(getattr(incoming, "damage", ctx.get("damage", 0)))
    R = _round_half_up(H * ratio)
    attr = str(getattr(incoming, "attribute", "普通") or "普通")
    attacker = ctx.get("attacker")
    if attacker is None or R <= 0:
        return m9_text("aids.t1_defense_no_attacker_or_zero")
    from engine.m9.combat import resolve_damage
    from engine.m9.combat import is_absolute_death_source  # noqa
    resolve_damage(
        requestor, attacker, None, game_state,
        raw_damage_override=R, damage_attribute_override=attr,
        source_kind="t1_aid_reflect",
        _skip_outgoing_hook=True,
    )
    _log(game_state, "aid_effect", talent="T1", kind="defense",
         player=requestor.player_id, reflect=R)
    return m9_text("aids.t1_defense_reflect", reflect=R)


def _t2_attack(requestor: Any, game_state: Any, ctx: dict) -> str:
    """T2 进攻援助：削减目标最外层可破护甲至多 t2_armor_steal 耐久；
    按实际损失量修复请求者最缺损的外层护甲。"""
    target = ctx.get("target")
    steal = int(_pp("t2_armor_steal", 2))
    if target is None:
        return m9_text("aids.t2_attack_no_target")
    reduced = 0
    target_armor = getattr(target, "armor_layers", None) or []
    for layer in reversed(target_armor):  # 最外层在后
        if getattr(layer, "broken", False) or getattr(layer, "durability", 0) <= 0:
            continue
        before = int(getattr(layer, "durability", 0))
        cut = min(steal, before)
        if cut > 0:
            layer.durability = before - cut
            reduced += cut
        break
    repaired = 0
    if reduced > 0:
        mine = getattr(requestor, "armor_layers", None) or []
        candidates = [l for l in mine
                      if not getattr(l, "broken", False)]
        if candidates:
            worst = max(candidates, key=lambda l: (getattr(l, "max_durability", 0)
                                                   - getattr(l, "durability", 0)))
            gain = min(reduced, getattr(worst, "max_durability", 0)
                       - getattr(worst, "durability", 0))
            worst.durability = getattr(worst, "durability", 0) + gain
            repaired = gain
    _log(game_state, "aid_effect", talent="T2", kind="attack",
         player=requestor.player_id, reduced=reduced, repaired=repaired)
    return m9_text("aids.t2_attack_armor", reduced=reduced, repaired=repaired)


def _t2_defense(requestor: Any, game_state: Any, ctx: dict) -> str:
    """T2 防御援助：+t2_evasion_boost 点闪避，持续到本全局轮 R4。"""
    boost = int(_pp("t2_evasion_boost", 15))
    rnd = getattr(game_state, "current_round", 1)
    requestor._aid_t2_evasion_until = rnd
    requestor._aid_t2_evasion_boost = boost
    _log(game_state, "aid_effect", talent="T2", kind="defense",
         player=requestor.player_id, boost=boost, until=rnd)
    return m9_text("aids.t2_defense_evasion", boost=boost)


def _t3_attack(requestor: Any, game_state: Any, ctx: dict) -> str:
    """T3 进攻援助：载体攻击同时以同一载荷命中目标地点所有其他玩家单位，
    排除请求者；独立结算，不附加石化/摇晃/尘世之锁。"""
    target = ctx.get("target")
    raw = float(ctx.get("raw", 0))
    attr = str(ctx.get("attr", "普通") or "普通")
    if target is None or raw <= 0:
        return m9_text("aids.t3_attack_no_target_or_empty")
    from engine.m9.combat import resolve_damage
    loc = getattr(target, "location", None)
    hits = 0
    for pid in _alive_others(game_state, requestor.player_id):
        p = game_state.get_player(pid)
        if getattr(p, "location", None) != loc:
            continue
        resolve_damage(
            requestor, p, None, game_state,
            raw_damage_override=raw, damage_attribute_override=attr,
            source_kind="t3_aid_aoe", _skip_outgoing_hook=True)
        hits += 1
    _log(game_state, "aid_effect", talent="T3", kind="attack",
         player=requestor.player_id, location=loc, hits=hits)
    return m9_text("aids.t3_attack_aoe", location=loc, hits=hits)


def _t3_defense(requestor: Any, game_state: Any, ctx: dict) -> str:
    """T3 防御援助：t3_aid_rest_pending——绝对伤害免疫，下一次实际 ActionGrant
    在控制裁决前改写为 aid_rest 并消费（见 resolution.AidRestTracker）。"""
    requestor._aid_t3_rest_pending = True
    _log(game_state, "aid_effect", talent="T3", kind="defense",
         player=requestor.player_id)
    return m9_text("aids.t3_defense_aid_rest")


def _t4_attack(requestor: Any, game_state: Any, ctx: dict) -> str:
    """T4 进攻援助：目标仍存活则施加结构状态 EXILED 数个未来 R4 tick。"""
    target = ctx.get("target")
    duration = int(_pp("t4_exile_duration", 1))
    if target is None:
        return m9_text("aids.t4_attack_no_target")
    if not getattr(target, "is_alive", lambda: False)():
        return m9_text("aids.t4_attack_target_dead")
    rnd = getattr(game_state, "current_round", 1)
    target._m9_exiled_until = rnd + duration
    target.location = None
    target.is_awake = False
    _log(game_state, "aid_effect", talent="T4", kind="attack",
         player=requestor.player_id, target=target.player_id, ticks=duration)
    return m9_text("aids.t4_attack_exiled", name=target.name, duration=duration)


def _t4_defense(requestor: Any, game_state: Any, ctx: dict) -> str:
    """T4 防御援助：免疫本全局轮结束前你受到的下一次伤害。"""
    rnd = getattr(game_state, "current_round", 1)
    requestor._aid_t4_immune_once_round = rnd
    _log(game_state, "aid_effect", talent="T4", kind="defense",
         player=requestor.player_id)
    return m9_text("aids.t4_defense_immune_once")


def _t6_attack(requestor: Any, game_state: Any, ctx: dict) -> str:
    """T6 进攻援助：当前地点召唤带 1 合法武器的临时演出警察；当前通缉目标
    同地点时攻击一次后消散。"""
    from engine.m9.police import temporary_performance_police
    return temporary_performance_police(
        game_state, requestor, attack=True)


def _t6_defense(requestor: Any, game_state: Any, ctx: dict) -> str:
    """T6 防御援助：召唤带 1 护甲的临时演出警察，解除普通 debuff/控制后消散。"""
    from engine.m9.police import temporary_performance_police
    return temporary_performance_police(
        game_state, requestor, attack=False)


def _t7_attack(requestor: Any, game_state: Any, ctx: dict) -> str:
    """T7 进攻援助：t7_regen_duration 轮内生命恢复量 +t7_regen_boost。"""
    duration = int(_pp("t7_regen_duration", 2))
    boost = float(_pp("t7_regen_boost", 1))
    rnd = getattr(game_state, "current_round", 1)
    requestor._aid_t7_regen_until = rnd + duration
    requestor._aid_t7_regen_boost = boost
    _log(game_state, "aid_effect", talent="T7", kind="attack",
         player=requestor.player_id, duration=duration, boost=boost)
    return m9_text("aids.t7_attack_regen", duration=duration, boost=boost)


def _t7_defense(requestor: Any, game_state: Any, ctx: dict) -> str:
    """T7 防御援助：下一次攻击保留 1 HP；溢出每点消耗 t7_pp_absorb_ratio PP。"""
    ratio = float(_pp("t7_pp_absorb_ratio", 1))
    requestor._aid_t7_keep1_ratio = ratio
    _log(game_state, "aid_effect", talent="T7", kind="defense",
         player=requestor.player_id, ratio=ratio)
    return m9_text("aids.t7_defense_keep1")


# ════════════════════════════════════════════════════════════════
#  神代天赋
# ════════════════════════════════════════════════════════════════

def _g1_attack(requestor: Any, game_state: Any, ctx: dict) -> str:
    """G1 进攻援助：位移到地点并对该地点所有单位造成 g1_aoe_damage 伤害 +
    g1_burn_stacks 层灼烧；排除被援助者（接收者）。"""
    from engine.m9.combat import resolve_damage
    target = ctx.get("target")
    loc = ctx.get("location") or getattr(target, "location", None)
    if not loc:
        loc = getattr(requestor, "location", None)
    dmg = float(_pp("g1_aoe_damage", 2))
    burns = int(_pp("g1_burn_stacks", 2))
    old = getattr(requestor, "location", None)
    requestor.location = loc
    for pid in _alive_others(game_state, requestor.player_id):
        p = game_state.get_player(pid)
        if getattr(p, "location", None) != loc:
            continue
        resolve_damage(
            requestor, p, None, game_state,
            raw_damage_override=dmg, damage_attribute_override="普通",
            source_kind="g1_aid_aoe", _skip_outgoing_hook=True)
        if hasattr(p.talent, "add_burn_stacks"):
            try:
                p.talent.add_burn_stacks(burns)
            except Exception:
                pass
    _log(game_state, "aid_effect", talent="G1", kind="attack",
         player=requestor.player_id, location=loc,
         from_loc=old, damage=dmg, burns=burns)
    return m9_text("aids.g1_attack_aoe", location=loc, damage=dmg, burns=burns)


def _g1_defense(requestor: Any, game_state: Any, ctx: dict) -> str:
    """G1 防御援助：位移到地点并对该地点所有单位造成震荡；排除被援助者。"""
    target = ctx.get("target")
    loc = ctx.get("location") or getattr(target, "location", None)
    if not loc:
        loc = getattr(requestor, "location", None)
    requestor.location = loc
    markers = getattr(game_state, "markers", None)
    for pid in _alive_others(game_state, requestor.player_id):
        p = game_state.get_player(pid)
        if getattr(p, "location", None) != loc:
            continue
        if markers is not None and not getattr(p, "is_shocked", False):
            p.is_shocked = True
            markers.add(pid, "SHOCKED")
    _log(game_state, "aid_effect", talent="G1", kind="defense",
         player=requestor.player_id, location=loc)
    return m9_text("aids.g1_defense_shock", location=loc)


def _g2_attack(requestor: Any, game_state: Any, ctx: dict) -> str:
    """G2 进攻援助：请求者可从未托管 PP 再支付至多 g2_pp_to_atk_cap，
    按 ratio 转为本次攻击固定加值（独立于出价并销毁）。"""
    cap = float(_pp("g2_pp_to_atk_cap", 5))
    ratio = float(_pp("g2_pp_to_atk_ratio", 0.5))
    pp = getattr(game_state, "m9_pp", None)
    if pp is None:
        return m9_text("aids.g2_attack_ledger_missing")
    paid = min(cap, pp.balance(requestor.player_id))
    if paid <= 0:
        return m9_text("aids.g2_attack_no_pp")
    pp.spend(requestor.player_id, int(paid))
    bonus = _round_half_up(paid * ratio)
    requestor._aid_g2_atk_bonus = getattr(
        requestor, "_aid_g2_atk_bonus", 0) + bonus
    _log(game_state, "aid_effect", talent="G2", kind="attack",
         player=requestor.player_id, paid=int(paid), bonus=bonus)
    return m9_text("aids.g2_attack_pay", paid=int(paid), bonus=bonus)


def _g2_defense(requestor: Any, game_state: Any, ctx: dict) -> str:
    """G2 防御援助：请求者支付至多 g2_pp_to_hp_cap，按 ratio 恢复 HP。"""
    cap = float(_pp("g2_pp_to_hp_cap", 5))
    ratio = float(_pp("g2_pp_to_hp_ratio", 1))
    pp = getattr(game_state, "m9_pp", None)
    if pp is None:
        return m9_text("aids.g2_defense_ledger_missing")
    paid = min(cap, pp.balance(requestor.player_id))
    if paid <= 0:
        return m9_text("aids.g2_defense_no_pp")
    pp.spend(requestor.player_id, int(paid))
    heal = _round_half_up(paid * ratio)
    requestor.hp = min(getattr(requestor, "max_hp", requestor.hp),
                       getattr(requestor, "hp", 0) + heal)
    _log(game_state, "aid_effect", talent="G2", kind="defense",
         player=requestor.player_id, paid=int(paid), heal=heal)
    return m9_text("aids.g2_defense_pay", paid=int(paid), heal=heal)


def _g3_attack(requestor: Any, game_state: Any, ctx: dict) -> str:
    """G3 进攻援助：本回合内攻击到的单位当前轮结束前不能 move。"""
    rnd = getattr(game_state, "current_round", 1)
    requestor._aid_g3_no_move_round = rnd
    _log(game_state, "aid_effect", talent="G3", kind="attack",
         player=requestor.player_id)
    return m9_text("aids.g3_attack_no_move")


def _g3_defense(requestor: Any, game_state: Any, ctx: dict) -> str:
    """G3 防御援助：前摇截断——下一次以你为目标的攻击型即演/公演的主动
    天赋载荷与演出追演被截断，降级为合法基础攻击（B5-V5）。"""
    requestor._aid_g3_frontcut_pending = True
    _log(game_state, "aid_effect", talent="G3", kind="defense",
         player=requestor.player_id)
    return m9_text("aids.g3_defense_frontcut")


def _g4_attack(requestor: Any, game_state: Any, ctx: dict) -> str:
    """G4 进攻援助：g4_ramping_duration R4 ticks；n 次攻击后下一次攻击
    获得 round_half_up(n×ratio) 固定加值，结算后 n+1。"""
    duration = int(_pp("g4_ramping_duration", 3))
    ratio = float(_pp("g4_ramping_atk", 0.5))
    rnd = getattr(game_state, "current_round", 1)
    requestor._aid_g4_ramp_until = rnd + duration
    requestor._aid_g4_ramp_atk = ratio
    requestor._aid_g4_ramp_count = 0
    _log(game_state, "aid_effect", talent="G4", kind="attack",
         player=requestor.player_id, duration=duration, ratio=ratio)
    return m9_text("aids.g4_attack_ramping", duration=duration, ratio=ratio)


def _g4_defense(requestor: Any, game_state: Any, ctx: dict) -> str:
    """G4 防御援助：n 次承受后下一次来袭固定减伤 round_half_up(n×ratio)，
    不低于公共 25% 下限。"""
    duration = int(_pp("g4_ramping_duration", 3))
    ratio = float(_pp("g4_ramping_def", 0.5))
    rnd = getattr(game_state, "current_round", 1)
    requestor._aid_g4_def_until = rnd + duration
    requestor._aid_g4_def_ratio = ratio
    requestor._aid_g4_def_count = 0
    _log(game_state, "aid_effect", talent="G4", kind="defense",
         player=requestor.player_id, duration=duration, ratio=ratio)
    return m9_text("aids.g4_defense_ramping", duration=duration, ratio=ratio)


def _g5_aid(requestor: Any, game_state: Any, ctx: dict) -> str:
    """G5 援助（攻防同）：从白名单授予一枚简化标记诗篇（§2.15 七枚）。"""
    marker = ctx.get("marker")
    if marker is None:
        return m9_text("aids.g5_need_marker")
    from engine.m9.talents.poems import grant_simplified_marker
    ok = grant_simplified_marker(requestor, marker)
    if not ok:
        return m9_text("aids.g5_marker_invalid", marker=marker)
    _log(game_state, "aid_effect", talent="G5", kind="aid",
         player=requestor.player_id, marker=marker)
    return m9_text("aids.g5_marker_granted", marker=marker)


def _g6_aid(requestor: Any, game_state: Any, ctx: dict) -> str:
    """G6 援助（攻防同）：以请求者自身状态重演一次可复制普通行动大类模板
    （不另收 SP、不进 T0、不授予完整额外行动；不调用天赋公演）。"""
    category = ctx.get("category")
    if category not in ("move", "interact", "find", "lock", "attack"):
        return m9_text("aids.g6_no_template")
    from engine.m9.executor import execute_category
    msg, ok = execute_category(requestor, game_state, category)
    _log(game_state, "aid_effect", talent="G6", kind="aid",
         player=requestor.player_id, category=category)
    if ok:
        return msg or m9_text("aids.g6_replay", action_category=category)
    return m9_text("aids.g6_replay_failed", action_category=category, msg=msg)


def _g7_attack(requestor: Any, game_state: Any, ctx: dict) -> str:
    """G7 进攻援助：点射目标 g7_snipe_damage 普通伤害 + VULNERABLE
    g7_vulnerable g7_vulnerable_duration R4；随后请求者所在地 AOE
    g7_splash_damage（除请求者外）。"""
    from engine.m9.combat import resolve_damage
    target = ctx.get("target")
    if target is None:
        return m9_text("aids.g7_attack_no_target")
    snipe = float(_pp("g7_snipe_damage", 3))
    vuln = int(_pp("g7_vulnerable", 2))
    vdur = int(_pp("g7_vulnerable_duration", 2))
    splash = float(_pp("g7_splash_damage", 2))
    rnd = getattr(game_state, "current_round", 1)
    resolve_damage(
        requestor, target, None, game_state,
        raw_damage_override=snipe, damage_attribute_override="普通",
        source_kind="g7_aid_snipe", _skip_outgoing_hook=True)
    target._aid_vulnerable = getattr(target, "_aid_vulnerable", 0) + vuln
    target._aid_vulnerable_until = rnd + vdur
    loc = getattr(requestor, "location", None)
    for pid in _alive_others(game_state, requestor.player_id):
        p = game_state.get_player(pid)
        if getattr(p, "location", None) != loc:
            continue
        resolve_damage(
            requestor, p, None, game_state,
            raw_damage_override=splash, damage_attribute_override="普通",
            source_kind="g7_aid_splash", _skip_outgoing_hook=True)
    _log(game_state, "aid_effect", talent="G7", kind="attack",
         player=requestor.player_id, target=target.player_id,
         snipe=snipe, vulnerable=vuln, splash=splash)
    return m9_text("aids.g7_attack_snipe", name=target.name, snipe=snipe,
                   vulnerable=vuln, vdur=vdur, splash=splash)


def _g7_defense(requestor: Any, game_state: Any, ctx: dict) -> str:
    """G7 防御援助：冲刺到合法地点并获得普通 COVERED 掩体
    max(round_half_up(当前HP×ratio), g7_cover_min) 耐久，g7_cover_duration R4。"""
    loc = ctx.get("location")
    if not loc:
        loc = getattr(requestor, "location", None)
    from engine.m9.police import grant_cover_to_player
    ratio = float(_pp("g7_cover_ratio", 0.5))
    minimum = float(_pp("g7_cover_min", 5))
    duration = int(_pp("g7_cover_duration", 2))
    hp = getattr(requestor, "hp", 0)
    dur_amt = max(_round_half_up(hp * ratio), _round_half_up(minimum))
    requestor.location = loc
    grant_cover_to_player(
        game_state, requestor.player_id, dur_amt, duration)
    _log(game_state, "aid_effect", talent="G7", kind="defense",
         player=requestor.player_id, location=loc, cover=dur_amt)
    return m9_text("aids.g7_defense_cover", location=loc, cover=dur_amt,
                   duration=duration)


# ════════════════════════════════════════════════════════════════
#  派发表
# ════════════════════════════════════════════════════════════════

AID_EFFECTS: Dict[str, Dict[str, Callable]] = {
    "T1": {"attack": _t1_attack, "defense": _t1_defense},
    "T2": {"attack": _t2_attack, "defense": _t2_defense},
    "T3": {"attack": _t3_attack, "defense": _t3_defense},
    "T4": {"attack": _t4_attack, "defense": _t4_defense},
    "T6": {"attack": _t6_attack, "defense": _t6_defense},
    "T7": {"attack": _t7_attack, "defense": _t7_defense},
    "G1": {"attack": _g1_attack, "defense": _g1_defense},
    "G2": {"attack": _g2_attack, "defense": _g2_defense},
    "G3": {"attack": _g3_attack, "defense": _g3_defense},
    "G4": {"attack": _g4_attack, "defense": _g4_defense},
    "G5": {"attack": _g5_aid, "defense": _g5_aid},
    "G6": {"attack": _g6_aid, "defense": _g6_aid},
    "G7": {"attack": _g7_attack, "defense": _g7_defense},
}

# G5 援助可合格简化标记白名单（B4 RFC v0.4 §5.3）
G5_AID_MARKERS: tuple = (
    "游侠", "群星", "阴阳", "永恒", "飞萤", "追光", "明天")


def aid_kind_slots(kind: str) -> list:
    """某方向（attack/defense）可用的天赋槽位（供调用方选援助提供者/效果）。"""
    return [slot for slot, ef in AID_EFFECTS.items() if kind in ef]


def run_aid(slot: str, kind: str, requestor: Any, game_state: Any,
            ctx: Optional[dict] = None) -> str:
    """执行一枚天赋援助效果。slot ∈ AID_EFFECTS；kind ∈ attack/defense/aid。"""
    slot_map = AID_EFFECTS.get(slot)
    if slot_map is None:
        return m9_text("aids.unknown_slot", slot=slot)
    fn = slot_map.get(kind)
    if fn is None:
        fn = slot_map.get("attack") if kind == "aid" else None
    if fn is None:
        return m9_text("aids.slot_no_kind", slot=slot, kind=kind)
    return fn(requestor, game_state, ctx or {})


# ════════════════════════════════════════════════════════════
#  被动援助触发（B4 §5.1/§5.2：首攻 / 濒死 + 往世层非空 → 系统指派）
#  引擎接线层 = combat.resolve_damage（本模块只提供纯判定与执行）
# ════════════════════════════════════════════════════════════

def afterlife_members(state: Any) -> list:
    """往世层成员：已死亡、非绝对死亡（PP 未冻结）、未撤退的玩家。"""
    pp = getattr(state, "m9_pp", None)
    out = []
    for pid in getattr(state, "player_order", []):
        p = state.get_player(pid)
        if p is None or p.is_alive():
            continue
        if pp is not None and pp.is_frozen(pid):
            continue  # absolute_dead：不进往世层
        talent = getattr(p, "talent", None)
        if talent is not None and getattr(
                talent, "is_retreated", lambda: False)():
            continue
        out.append(pid)
    return out


def _slot_of_player(p: Any) -> Optional[str]:
    if p is None:
        return None
    slot = getattr(p, "talent_slot_id", None)
    if slot:
        return str(slot)
    talent = getattr(p, "talent", None)
    return getattr(talent, "slot_id", None)


def _passive_deal(state: Any, requestor: Any, kind: str,
                  ctx: dict) -> bool:
    """被动成交：指派提供者 → 执行效果 → 额度/被动奖励/事件。"""
    pp = getattr(state, "m9_pp", None)
    if pp is None:
        return False
    dead = afterlife_members(state)
    if not dead:
        return False
    provider = pp.pick_passive_provider(requestor.player_id, dead)
    if provider is None:
        return False
    slot = _slot_of_player(state.get_player(provider))
    if slot is None or slot not in AID_EFFECTS:
        return False
    try:
        msg = run_aid(slot, kind, requestor, state, ctx)
    except Exception:
        return False
    if str(msg or "").startswith("❌"):
        return False
    pp.use_aid_quota(requestor.player_id)
    pp.record_aid_provided(provider)
    reward = int(pp.passive_aid_reward())
    pp.earn(provider, reward)
    pp.record_aid_earned(provider, reward)
    # aid_effect 事件由各效果函数自行登记（_t1_attack 等 _log 调用）
    return True


def trigger_passive_attack_aid(attacker: Any, target: Any, state: Any,
                               hit: Any) -> None:
    """被动进攻援助：每名玩家每局首次攻击、且往世层非空时自动触发。

    往世层为空 → 不消耗、不标记，顺延到下一次攻击再判定（A1 合取）。
    """
    if attacker is None or state is None or not getattr(
            attacker, "player_id", None):
        return
    if getattr(attacker, "_m9_aid_passive_attack_done", False):
        return
    if not afterlife_members(state):
        return
    attacker._m9_aid_passive_attack_done = True
    _passive_deal(state, attacker, "attack",
                  {"attacker": attacker, "target": target, "hit": hit,
                   "requestor": attacker, "game_state": state})


def trigger_passive_defense_aid(target: Any, attacker: Any, state: Any,
                                hit: Any) -> None:
    """被动防御援助：每名玩家每局首次在敌对伤害事件后存活且
    HP ≤ aid_hp_threshold、且往世层非空时自动触发（自我代价/致死不触发）。"""
    if target is None or state is None or not getattr(
            target, "player_id", None):
        return
    if not getattr(target, "is_alive", lambda: False)():
        return
    if attacker is None or getattr(attacker, "player_id", None) is None:
        return  # 环境/无攻击者来源不触发
    if getattr(target, "_m9_aid_passive_defense_done", False):
        return
    if not afterlife_members(state):
        return
    threshold = float(_pp("aid_hp_threshold", 4))
    if float(getattr(target, "hp", 0) or 0) > threshold:
        return
    target._m9_aid_passive_defense_done = True
    _passive_deal(state, target, "defense",
                  {"attacker": attacker, "target": target,
                   "incoming_hit": hit, "hit": hit,
                   "requestor": target, "game_state": state})
