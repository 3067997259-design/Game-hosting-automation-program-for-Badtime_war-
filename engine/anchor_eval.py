"""锚定理想路径评估器（G5「往世的涟漪」，experiment: m7_talents，v2.0 §7.5）。

**信源统一**：战斗账目只调 `combat.numeric_v2`（与真引擎逐字节一致），评估器**不重新实现**
任何伤害规则。工作在"理想干净交手"高度——目标被动、百分百命中、不重放 22 步流水线，也不
预判防御方反应型天赋钩子（那些归监控期"破坏性行动"）。目标被动 → 整条推演确定，
复杂度 O(horizon × 武器)，与旧 naive 闭式公式同量级。

唯一前向模型 `simulate_path` 之上挂三种"出牌人"：命运路线模板（预填序列）、人类自定路径
（人类输入序列）、未来涌现式搜索（本站不做）。三者共用本评估器，信源统一只需守住这一处。

动作 mini-language（每个动作 = 1 轮）：
  ("move", loc) / ("find",) / ("lock",) / ("charge", weapon) / ("attack", weapon_or_None)
attack 的 weapon 为 None 时自动选"对当前目标进度最大"的武器（按 goal 贪心，硬克制已废）。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, List, Optional

from combat import numeric_v2
from models.equipment import WeaponRange


@dataclass
class SimResult:
    """推演结果。rounds = 命数（达成时用了几轮；未达成时为已走轮数）。

    attr_counts：路径中每攻击属性的命中次数（{属性中文名: 次数}），供 G5 开拓·防御公式
    （contrib(X)=n_X）使用。弓按 compute_shot 解析后的真实属性记。
    """
    achieved: bool
    rounds: int
    log: List[str] = field(default_factory=list)
    attr_counts: dict = field(default_factory=dict)


class _TargetProj:
    """目标轻量可变投影：只含 numeric_v2 需要的 armor.outer / inner_defense / hp。

    deepcopy 外甲与 inner_defense，使 resolve_hit 在副本上磨耐久而不动真目标。
    """

    def __init__(self, target: Any):
        self.armor = copy.deepcopy(getattr(target, "armor", None))
        self.inner_defense = copy.deepcopy(getattr(target, "inner_defense", {}) or {})
        self.hp = float(getattr(target, "hp", 0))


# ================================================================
#  武器/伤害（全部经 numeric_v2，信源统一）
# ================================================================

def _raw_of(weapon: Any, charged: set) -> int:
    """武器裸伤：已蓄力且有蓄力伤害 → charged_damage，否则 base_damage。"""
    if (getattr(weapon, "requires_charge", False)
            and weapon.name in charged
            and getattr(weapon, "charged_damage", None)):
        return int(weapon.charged_damage)
    return int(getattr(weapon, "base_damage", 0))


def _can_fire(weapon: Any, charged: set) -> bool:
    """强制蓄力武器未蓄力则不能开火。"""
    if getattr(weapon, "requires_charge", False) and getattr(weapon, "charge_mandatory", True):
        return weapon.name in charged
    return True


def _piece_durability(proj: _TargetProj, piece_name: Optional[str]) -> int:
    armor = getattr(proj, "armor", None)
    if not armor or not piece_name:
        return 0
    for p in getattr(armor, "outer", []) or []:
        if p.name == piece_name:
            return p.durability
    return 0


def _goal_progress_weapon(proj: _TargetProj, weapons: List[Any], charged: set,
                          goal: str, break_piece: Optional[str]):
    """贪心选当前对 goal 进度最大的武器（在投影副本上试打，确定且信源统一）。

    kill → 选 HP 减损最大；break_armor → 选目标护甲件耐久减损最大。
    返回 (weapon, raw, attr) 或 None。硬克制已废 → 无作废分支。
    """
    best = None
    best_gain = 0
    for w in weapons:
        if not _can_fire(w, charged):
            continue
        attr = getattr(getattr(w, "attribute", None), "value", None)
        if attr is None:
            continue
        raw = _raw_of(w, charged)
        if raw <= 0:
            continue
        trial = copy.deepcopy(proj)
        before_dura = _piece_durability(trial, break_piece)
        res = numeric_v2.resolve_hit(trial, raw, attr,
                                     pierce_factor=getattr(w, "_pierce_factor", 1.0))
        if goal == "kill":
            gain = res["damage"]                      # HP 减损
        else:
            gain = before_dura - _piece_durability(trial, break_piece)  # 该件耐久减损
        if gain > best_gain:
            best_gain = gain
            best = (w, raw, attr)
    return best


# ================================================================
#  前置动作（canonical 规则：move/find/lock/charge）
# ================================================================

def has_engaged(state: Any, caster: Any, target: Any) -> bool:
    markers = getattr(state, "markers", None)
    if markers and hasattr(markers, "has_relation"):
        return markers.has_relation(caster.player_id, "ENGAGED_WITH", target.player_id)
    return False


def has_locked(state: Any, caster: Any, target: Any) -> bool:
    markers = getattr(state, "markers", None)
    if markers and hasattr(markers, "has_relation"):
        return markers.has_relation(target.player_id, "LOCKED_BY", caster.player_id)
    return False


def prep_actions(state: Any, caster: Any, target: Any, weapon: Any) -> List[tuple]:
    """到达"能用 weapon 打 target"所需的前置动作序列（move/find/lock/charge）。

    移植旧 _calculate_prep_rounds，但产出动作序列供 simulate_path 消费。
    """
    actions: List[tuple] = []
    wr = getattr(weapon, "weapon_range", None)
    if wr == WeaponRange.MELEE:
        if getattr(caster, "location", None) != getattr(target, "location", None):
            actions.append(("move", getattr(target, "location", None)))
        if not has_engaged(state, caster, target):
            actions.append(("find",))
    elif wr == WeaponRange.RANGED:
        if not has_locked(state, caster, target):
            actions.append(("lock",))
    if (getattr(weapon, "requires_charge", False)
            and not getattr(weapon, "is_charged", False)):
        actions.append(("charge", weapon))
    return actions


def resolve_weapons(caster: Any) -> List[Any]:
    """把 caster 武器栏里的弓替换为按已装模块解析后的有效武器（评估器弓感知）。

    弓走 `bow_modules.compute_shot`（穿甲→科/火矢→魔/力量+伤），结果武器带 `_pierce_factor`
    供 simulate_path 喂 resolve_hit。其它武器原样（pierce 1.0）。信源统一——属性/伤害/穿甲
    全部由 compute_shot 这一处决定，评估器不另算。
    """
    weapons = list(getattr(caster, "weapons", []))
    if not hasattr(caster, "bow_modules"):
        return weapons  # 无 m4 弓系统 → 武器原样
    out: List[Any] = []
    for w in weapons:
        tags = getattr(w, "special_tags", None) or []
        if "bow" in tags or getattr(w, "name", "") == "弓":
            try:
                from engine.bow_modules import compute_shot
                shot = compute_shot(caster)
                ew = shot["weapon"]
                ew._pierce_factor = shot.get("armor_pierce_factor", 1.0)
                out.append(ew)
            except Exception:
                out.append(w)
        else:
            out.append(w)
    return out


# ================================================================
#  评估器：唯一前向模型
# ================================================================

def simulate_path(target: Any, weapons: List[Any], sequence: List[tuple], *,
                  goal: str = "kill", break_piece: Optional[str] = None,
                  horizon: Optional[int] = None) -> SimResult:
    """在目标投影上推演动作序列，返回 (是否达成事件, 命数=用了几轮)。

    goal="kill" → 目标 HP≤0；goal="break_armor" → break_piece 耐久≤0。
    attack 动作 weapon=None 时按 goal 贪心自动选武器（信源统一，调 numeric_v2）。
    达成即提前返回；horizon 截断。
    """
    proj = _TargetProj(target)
    charged = set(w.name for w in weapons if getattr(w, "is_charged", False))
    log: List[str] = []
    attr_counts: dict = {}

    def goal_met() -> bool:
        if goal == "kill":
            return proj.hp <= 0
        return _piece_durability(proj, break_piece) <= 0

    if goal_met():
        return SimResult(True, 0, log, attr_counts)

    rounds = 0
    for action in sequence:
        rounds += 1
        kind = action[0]
        if kind == "charge":
            charged.add(action[1].name)
        elif kind == "attack":
            picked = None
            if action[1] is not None:
                w = action[1]
                if _can_fire(w, charged):
                    attr = getattr(getattr(w, "attribute", None), "value", None)
                    if attr is not None:
                        picked = (w, _raw_of(w, charged), attr)
            else:
                picked = _goal_progress_weapon(proj, weapons, charged, goal, break_piece)
            if picked is not None:
                w, raw, attr = picked
                res = numeric_v2.resolve_hit(proj, raw, attr,
                                             pierce_factor=getattr(w, "_pierce_factor", 1.0))
                proj.hp -= res["damage"]
                attr_counts[attr] = attr_counts.get(attr, 0) + 1   # 开拓·防御公式数据源
                log.append(f"R{rounds} {w.name}({attr}) raw{raw}→伤{res['damage']} "
                           f"吸{res['absorbed']} hp{proj.hp:.0f}")
        # move/find/lock 仅消耗一轮（前置），账目不变
        if goal_met():
            return SimResult(True, rounds, log, attr_counts)
        if horizon is not None and rounds >= horizon:
            break

    return SimResult(goal_met(), rounds, log, attr_counts)


def build_direct_sequence(state: Any, caster: Any, target: Any, weapons: List[Any], *,
                          goal: str = "kill", break_piece: Optional[str] = None,
                          horizon: int = 8) -> List[tuple]:
    """直取底线序列：选当前对 goal 最优武器 → 该武器前置 → 连击至 horizon。

    这是"神谕发给玩家的默认牌"的底线（可达性检查），**不是**策展的命运路线模板
    （那些经 anchor_templates 注册，本站预留接口）。发动者不满意可自传序列覆盖。
    """
    proj = _TargetProj(target)
    charged = set(w.name for w in weapons if getattr(w, "is_charged", False))
    pick = _goal_progress_weapon(proj, weapons, charged, goal, break_piece)
    if pick is None:
        return []
    weapon = pick[0]
    seq = prep_actions(state, caster, target, weapon)
    seq += [("attack", None)] * max(1, horizon)
    return seq
