"""命中/闪避体系纯函数（experiment: m3_accuracy，v2.0 §2.7）。

设计纲领：默认确定性，概率来自投资——香草对砍 100% 命中（零新增认知），
命中骰只在有人为可见性/机动经济掏过钱时出现。

闪避来源（全部从 balance.json accuracy 分区读取）：
    跨地点攻击未锁定        −unlocked_ranged_penalty（v1 远程必有锁，为 M4 弓预铺）
    目标本轮主动移动        −move_evasion（借机攻击豁免——拍板 §1.3）
    隐身属性对位            −stealth_evasion（攻击属性 ∈ 目标隐身集 且 攻击者无对应探测）
    攻击者震荡降级          −degraded_shock_hit_penalty（自消耗，M3 起替代先攻 −2）
总闪避封顶 evasion_cap（命中下限 100−cap）——"打不中的龟壳"违宪（零产出禁令）。

闪避成功 ≠ 落空 = 擦伤：调用方以 force_min 走 numeric_v2 保底结算，
且不触发武器附带效果（震荡等）。
"""
from __future__ import annotations
import random
from typing import Any, Dict, List, Optional, Tuple

from engine.balance import get as bget


def _acc(key: str, default: int) -> int:
    return int(bget("accuracy", key, default=default))


def compute_hit_chance(attacker: Any, target: Any, weapon: Any,
                       game_state: Any = None, *,
                       is_aoo: bool = False) -> Tuple[int, List[str]]:
    """计算本次攻击的命中率（0-100）与构成明细（L1 展示用）。

    返回 (chance, breakdown)。breakdown 为非空修正项的人话列表。
    """
    chance = _acc("base", 100)
    breakdown: List[str] = []

    # ── 跨地点未锁定（远程惩罚，锁定抵消）──
    attacker_loc = getattr(attacker, "location", None) if attacker else None
    target_loc = getattr(target, "location", None)
    if (attacker is not None and attacker_loc is not None
            and target_loc is not None and attacker_loc != target_loc):
        locked = False
        if game_state is not None:
            locked = game_state.markers.has_relation(
                getattr(target, "player_id", ""), "LOCKED_BY",
                getattr(attacker, "player_id", ""))
        if not locked:
            penalty = _acc("unlocked_ranged_penalty", 15)
            chance -= penalty
            breakdown.append(f"跨地点未锁定 −{penalty}")

    # ── 闪避来源（受封顶约束的部分）──
    evasion = 0
    # 擦钩：本轮被钩锁擦中 → 全部闪避加成失效（零产出禁令，钩头挂了一下）
    hook_no_evasion = (game_state is not None
                       and getattr(target, "_hook_no_evasion_round", None)
                       == getattr(game_state, "current_round", -1))

    # 善见天 v3（G5，m7）：被锚定目标对其锚定者的闪避/隐身全失效（永远被注视）
    anchored_by_attacker = False
    if attacker is not None and getattr(target, "_anchored_by", None) is not None:
        from engine import experiments
        if experiments.is_enabled("m7_talents"):
            anchored_by_attacker = (
                getattr(target, "_anchored_by", None)
                == getattr(attacker, "player_id", None))

    if not hook_no_evasion and not anchored_by_attacker:
        if not is_aoo and getattr(target, "moved_this_round", False):
            move_ev = _acc("move_evasion", 15)
            evasion += move_ev
            breakdown.append(f"目标本轮移动 −{move_ev}")

        # 隐身属性对位：攻击属性 ∈ 目标隐身集，且攻击者无对应探测
        weapon_attr = getattr(getattr(weapon, "attribute", None), "value", None)
        if weapon_attr:
            target_stealth = getattr(target, "stealth_attrs", None) or set()
            attacker_detect = (getattr(attacker, "detection_attrs", None) or set()) \
                if attacker else set()
            if weapon_attr in target_stealth and weapon_attr not in attacker_detect:
                stealth_ev = _acc("stealth_evasion", 25)
                evasion += stealth_ev
                breakdown.append(f"隐身（{weapon_attr}）−{stealth_ev}")

        cap = _acc("evasion_cap", 60)
        if evasion > cap:
            breakdown.append(f"闪避封顶 {cap}（原 {evasion}）")
            evasion = cap
    elif breakdown is not None:
        breakdown.append("善见天：永远被注视，闪避失效"
                         if anchored_by_attacker else "擦钩：闪避失效")
    chance -= evasion

    # ── 攻击者震荡降级（自消耗 flag，不计入闪避封顶——是攻方 debuff 非守方投资）──
    if attacker is not None:
        degrade = getattr(attacker, "_resist_degrade_hit_penalty", 0)
        if degrade:
            attacker._resist_degrade_hit_penalty = 0
            chance += degrade  # degrade 为负值
            breakdown.append(f"震荡降级 {degrade}")

    # ── 武器自带命中加成（M4 魔导模块等，挂在武器对象上）──
    weapon_bonus = getattr(weapon, "_bow_hit_bonus", 0) if weapon else 0
    if weapon_bonus:
        chance += weapon_bonus
        breakdown.append(f"制导 +{weapon_bonus}")

    return max(0, min(100, chance)), breakdown


def roll_hit(chance: int) -> Tuple[bool, int]:
    """掷命中骰（d100 ≤ chance 即命中）。走全局 random 保种子确定性。"""
    if chance >= 100:
        return True, 0  # 确定命中不消耗随机数（香草对砍零额外消耗）
    roll = random.randint(1, 100)
    return roll <= chance, roll
