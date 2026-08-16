"""value function 骨架（talents.md §3）：同源探针 + 统一折算公式。

统一公式骨架：
    value = p_hit·E[damage] + kill_utility·p_lethal·(1−insurance) − case_risk
            − resource_cost − exposure

本批只实现前三项（同源探针 `engine.m9.combat.resolve_hit_probe` 只算账不落 HP，
与结算共用 `numeric_v2.resolve_hit` 信源）；case_risk/resource_cost/exposure
三项留 value function 完整化批次（HP 支付折算/期权定价），结构上预留
`ValueBreakdown` 字段（接入决策痕迹/AIRI 解释）。

接入点：CombatMind._score_target 追加调整项（m9 profile 下生效；v2exp 恒 0）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

# 骨架常量（后续批次移入 balance.ai 风洞）
KILL_UTILITY = 40.0
RANGED_HIT_PROB = 0.85
MELEE_HIT_PROB = 1.0
AREA_HIT_PROB = 1.0
SCALE = 0.5  # 骨架接入的保守缩放（行为冲击可控，风洞可调）
CASE_RISK = 6.0       # 警察活跃且会因本攻击立案/续案时的最小代价
EXPOSURE_FACTOR = 0.5  # 攻击后预期承伤增量折算系数（对称探针）
EXPOSURE_MAX = 10.0   # exposure 封顶，避免单探针噪声主导评分

_ATTRS = ("魔法", "科技", "普通")


def _best_weapon(player: Any) -> Optional[Any]:
    best = None
    best_dmg = -1.0
    for w in getattr(player, "weapons", []) or []:
        if w is None:
            continue
        dmg = float(getattr(w, "base_damage", 0) or 0)
        charged = getattr(w, "is_charged", False)
        if charged:
            dmg = float(getattr(w, "charged_damage", dmg) or dmg)
        if dmg > best_dmg:
            best_dmg = dmg
            best = w
    return best


def _hit_prob(weapon: Any) -> float:
    rng = str(getattr(weapon, "weapon_range", "melee"))
    if rng in ("远程", "ranged"):
        return RANGED_HIT_PROB
    if rng in ("范围", "area"):
        return AREA_HIT_PROB
    return MELEE_HIT_PROB


def expected_damage_probe(attacker: Any, target: Any,
                          weapon: Any = None) -> Tuple[float, Dict[str, Any]]:
    """同源探针：最佳属性 × 命中率的期望伤害（不落 HP）。

    返回 (expected_damage, probe_info)。攻击属性取三属性中结算最高者
    （等价于攻击者选最优层/属性）。目标不可探针/无武器 → (0, info)。
    """
    from engine.m9.combat import resolve_hit_probe
    weapon = weapon or _best_weapon(attacker)
    if weapon is None or target is None or not target.is_alive():
        return 0.0, {"reason": "no_weapon_or_target"}
    raw = int(round(float(getattr(weapon, "base_damage", 0) or 0)))
    if getattr(weapon, "is_charged", False):
        raw = int(round(float(getattr(weapon, "charged_damage", raw) or raw)))
    if raw <= 0:
        return 0.0, {"reason": "zero_damage"}
    best = 0.0
    best_res = None
    for attr in _ATTRS:
        try:
            res = resolve_hit_probe(target, raw, attr)
        except Exception:
            continue
        dmg = float(getattr(res, "damage", 0) or 0)
        if dmg >= best:
            best = dmg
            best_res = res
    p_hit = _hit_prob(weapon)
    expected = best * p_hit
    return expected, {
        "raw": raw,
        "best_attr_damage": best,
        "p_hit": p_hit,
        "grazed": bool(getattr(best_res, "grazed", False))
        if best_res is not None else False,
    }


def _case_risk(player: Any, target: Any, state: Any) -> float:
    """攻击立案/续案的最小风险（M9 警察停机则不扣）。"""
    police = getattr(state, "m9_police", None)
    if police is None:
        return 0.0
    try:
        if police.is_disabled():
            return 0.0
        wanted = police.open_wanted()
    except Exception:
        return 0.0
    if wanted is not None and getattr(wanted, "suspect_id", None) \
            == getattr(player, "player_id", None):
        return CASE_RISK  # 已是通缉目标：继续作案维持案件风险
    if wanted is None:
        return CASE_RISK  # 本次攻击可能立案
    return 0.0  # 已有他人案件：本次攻击不新增立案


def _exposure_cost(attacker: Any, target: Any) -> float:
    """预期承伤增量（对称探针）：目标最佳武器对我方的期望伤害。"""
    try:
        expected, _ = expected_damage_probe(target, attacker)
    except Exception:
        return 0.0
    return min(expected * EXPOSURE_FACTOR, EXPOSURE_MAX)


def combat_value_adjust(player: Any, target: Any, state: Any,
                        snapshot: Any) -> float:
    """CombatMind 评分调整项：p_hit·E[dmg] + kill_utility·p_lethal·(1−保险)
    − case_risk − resource_cost − exposure。

    - p_lethal：期望伤害 ≥ 目标有效 HP 时 = 1（骨架确定性），否则 0；
    - 保险覆盖（T7 挂载）→ 击杀效用 ×0；
    - resource_cost：普通攻击无 SP/HP/追忆支付，恒 0（性能动作的成本在
      T0 门层核算，不在普通攻击探针里重复扣）；
    - case_risk/exposure：最小实现（对称探针），不再是恒 0。
    """
    from engine.m9.gate import m9_enabled
    if not m9_enabled(state):
        return 0.0
    if target is None or not target.is_alive():
        return 0.0
    weapon = _best_weapon(player)
    if weapon is None:
        return 0.0
    expected, info = expected_damage_probe(player, target, weapon)
    if expected <= 0:
        return 0.0
    eff_hp = _effective_hp(target)
    p_lethal = 1.0 if expected >= eff_hp else 0.0
    insurance_cover = 1.0
    if snapshot is not None and snapshot.m9 is not None \
            and snapshot.m9.insurance_mounted:
        insurance_cover = 0.0
    value = expected + KILL_UTILITY * p_lethal * insurance_cover
    value -= _case_risk(player, target, state)
    value -= _exposure_cost(player, target)
    return value * SCALE


def _effective_hp(target: Any) -> float:
    hp = float(getattr(target, "hp", 0) or 0)
    talent = getattr(target, "talent", None)
    if talent is not None:
        hp += float(getattr(talent, "temp_hp", 0.0) or 0.0)
    return max(hp, 0.0)
