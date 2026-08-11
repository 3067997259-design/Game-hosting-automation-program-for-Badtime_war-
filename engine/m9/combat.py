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

from typing import Any, Dict, List, Optional

from engine import experiments
from combat import numeric_v2
from engine.m9.resolution import HitResolution


# 绝对死亡来源白名单（t3_t7 §2.2/§2.5：G5 锚定强制、G4 死星天裁、G7 Terror、
# G1 繁育倒计时）——保险/免死/归家/形态替代全部跳过，直进死亡清理
ABSOLUTE_DEATH_SOURCES: tuple = ("g7_terror", "g4_judgment", "g5_anchor",
                                 "g1_propagation")


def m9_enabled() -> bool:
    """M9 结算路径开关。"""
    return experiments.is_enabled("m9_rfc")


def is_absolute_death_source(source_kind: Optional[str]) -> bool:
    return source_kind == "absolute_death" or source_kind in ABSOLUTE_DEATH_SOURCES


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
        if talent is not None and hasattr(talent, "m9_on_lethal"):
            try:
                kind = talent.m9_on_lethal(target, attacker, source_kind)
            except Exception:
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
    }


def _apply_attack(target: Any, raw_int: int, attr: str, *,
                  pierce_factor: float = 1.0,
                  direct_damage: bool = False) -> HitResolution:
    """A 阶段 + H 阶段账目：DIRECT_DAMAGE 直接 H=A（跳过防御/护甲/固定减伤），
    否则经 numeric_v2.resolve_hit（减法防御 + 耐久磨损 + 25% 保底）。"""
    if direct_damage:
        return HitResolution(raw=raw_int, attribute=attr, defense=0,
                             damage=raw_int, a_phase_absorbed=0, broken=[],
                             grazed=False, effective_hit=raw_int >= 1,
                             direct_damage=True)
    res = numeric_v2.resolve_hit(target, raw_int, attr, pierce_factor=pierce_factor)
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
                   source_kind: Optional[str] = None) -> Dict[str, Any]:
    """M9 完整伤害结算（签名与 damage_resolver.resolve_damage 兼容）。

    source_kind 是 M9 扩展参数：默认 None（普通攻击）；绝对死亡来源传入
    "g7_terror" / "g4_judgment" / "g5_anchor" / "g1_propagation" / "absolute_death"。
    """
    result = _base_result(target)
    target_hp_before = getattr(target, "hp", 0)
    result["target_hp_before"] = target_hp_before

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

    # 天赋 modify_outgoing（M9 协议：m9 天赋类实现；无则跳过）
    t_attacker = getattr(attacker, "talent", None) if attacker else None
    if t_attacker is not None and hasattr(t_attacker, "m9_modify_outgoing"):
        try:
            raw = t_attacker.m9_modify_outgoing(attacker, target, weapon, raw)
        except Exception:
            pass

    raw_int = max(0, int(round(raw)))
    result["raw_damage"] = float(raw)
    if raw_int <= 0:
        result["success"] = True
        result["reason"] = "zero_damage"
        return result

    # A/H 两阶段账目（DIRECT_DAMAGE 身份由调用方经 source_kind 或 direct 标志给出）
    hit = _apply_attack(target, raw_int, attr, pierce_factor=armor_pierce_factor)

    # H 阶段易伤/减伤挂载（M9 天赋协议）
    t_target = getattr(target, "talent", None)
    if t_target is not None and hasattr(t_target, "m9_modify_incoming"):
        try:
            t_target.m9_modify_incoming(hit)
        except Exception:
            pass

    result["final_damage"] = float(hit.damage)
    result["armor_broken"] = bool(hit.broken)
    result["armor_hit"] = hit.broken[0] if hit.broken else None
    result["details"].append(
        f"{hit.raw}(裸伤) − {hit.defense}(防御) = {hit.damage} 伤害"
        + ("（直达伤害）" if hit.direct_damage else ""))

    # H 阶段扣 HP
    remaining = max(0, hit.damage)
    if remaining > 0:
        target.hp = max(0, getattr(target, "hp", 0) - remaining)
    result["hp_damage"] = round(target_hp_before - getattr(target, "hp", 0), 2)
    result["target_hp"] = getattr(target, "hp", 0)
    result["success"] = True

    # 死亡裁决
    if getattr(target, "hp", 0) <= 0:
        adjudicator = DeathAdjudicator(game_state)
        kind = adjudicator.adjudicate(target, attacker, source_kind)
        result["m9_kind"] = kind
        if kind == "dead":
            result["killed"] = True
            result["absolute_dead"] = is_absolute_death_source(source_kind)
    return result


def resolve_hit_probe(target: Any, raw_int: int, attr: str,
                      pierce_factor: float = 1.0) -> HitResolution:
    """只算账不落 HP 的 A/H 探针（AI 评估/预检用，信源与结算同一函数）。"""
    return _apply_attack(target, raw_int, attr, pierce_factor=pierce_factor)
