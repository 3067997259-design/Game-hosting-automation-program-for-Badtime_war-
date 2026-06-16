"""HP20 整数数值模型核心纯函数（experiment: hp20，v2.0 §2.1）。

新公式全部收口在此模块，damage_resolver 的 hp20 分支只调用不实现——
便于表驱动单测，避免在 22 步流水线里散落新逻辑。

核心规则：
    单次伤害 = max(裸伤 − 对应属性防御总和, ⌈裸伤 × 25%⌉)
    外甲每吸收 1 点伤害消耗 1 点耐久（吸收量 = 裸伤 − 实伤）
    多甲分摊：按各甲对该属性的防御贡献比例，余数给贡献最高者（拍板 §13-8）
    硬克制（攻击整次无效）废除——属性差异由防御表表达
"""
from __future__ import annotations
import math
from typing import Any, Dict, List, Tuple

from engine.balance import get as bget


def min_damage(raw_damage: int) -> int:
    """保底伤害：⌈裸伤 × 25%⌉。口诀：怎么打都至少掉四分之一。"""
    ratio = bget("hp20", "min_damage_ratio", default=0.25)
    return max(1, math.ceil(raw_damage * ratio))


def compute_damage(raw_damage: int, defense: int) -> int:
    """核心伤害公式：max(裸伤 − 防御, 保底)。"""
    return max(raw_damage - max(0, defense), min_damage(raw_damage))


def piece_defense(piece: Any, attack_attr_name: str) -> int:
    """单件外甲对指定攻击属性的防御值（耐久归零 = 0）。"""
    if getattr(piece, "durability", 0) <= 0:
        return 0
    defense_map = getattr(piece, "defense_map", None) or {}
    return int(defense_map.get(attack_attr_name, 0))


def compute_defense(target: Any, attack_attr_name: str) -> Tuple[int, List[Any]]:
    """目标对指定攻击属性的总防御。

    = Σ 外甲（耐久>0）对该属性的主防/副防 + 内甲永久属性（inner_defense）。
    返回 (总防御, 参与防御的外甲列表)——后者供耐久分摊使用。
    """
    total = 0
    contributing: List[Any] = []
    armor = getattr(target, "armor", None)
    outer = list(getattr(armor, "outer", []) or []) if armor else []
    for piece in outer:
        d = piece_defense(piece, attack_attr_name)
        if d > 0:
            total += d
            contributing.append(piece)
    inner = getattr(target, "inner_defense", None) or {}
    total += int(inner.get(attack_attr_name, 0))
    return total, contributing


def distribute_durability(pieces: List[Any], absorbed: int,
                          attack_attr_name: str) -> List[Any]:
    """把吸收量按防御贡献比例分摊为耐久消耗（拍板 §13-8）。

    余数给贡献最高者（并列时取列表序靠前者——穿戴序，确定性稳定）。
    耐久扣至 0 即碎。返回本次被击碎的外甲列表。
    """
    if absorbed <= 0 or not pieces:
        return []
    contributions = [(piece_defense(p, attack_attr_name), p) for p in pieces]
    total_contrib = sum(c for c, _ in contributions)
    if total_contrib <= 0:
        return []

    shares = []
    for contrib, piece in contributions:
        share = absorbed * contrib // total_contrib
        shares.append([share, contrib, piece])
    remainder = absorbed - sum(s[0] for s in shares)
    if remainder > 0:
        # 余数给贡献最高者（并列取首个）
        best = max(shares, key=lambda s: s[1])
        best[0] += remainder

    broken: List[Any] = []
    for share, _contrib, piece in shares:
        if share <= 0:
            continue
        piece.durability = max(0, piece.durability - share)
        if piece.durability == 0:
            broken.append(piece)
    return broken


def resolve_hit(target: Any, raw_damage: int, attack_attr_name: str,
                force_min: bool = False, pierce_factor: float = 1.0) -> Dict[str, Any]:
    """一次命中的完整数值结算（不写 HP，只算账+磨耐久）。

    force_min: 强制按保底结算（警察保护 / M3 起的擦伤）。
    pierce_factor: 防御计算系数（M7 穿甲/防御减半，1.0=正常，0.5=防御按半计，
        0.25=穿甲按 25% 计）——耐久仍按实际吸收量磨损。
    返回 {damage, defense, absorbed, grazed, broken: [甲名]}。
    """
    defense, contributing = compute_defense(target, attack_attr_name)
    effective_defense = int(round(defense * pierce_factor)) if pierce_factor != 1.0 else defense
    if force_min:
        damage = min_damage(raw_damage)
    else:
        damage = compute_damage(raw_damage, effective_defense)
    absorbed = raw_damage - damage
    broken = distribute_durability(contributing, absorbed, attack_attr_name)
    return {
        "damage": damage,
        "defense": defense,
        "absorbed": absorbed,
        "grazed": force_min or (defense > 0 and damage == min_damage(raw_damage)),
        "broken": [getattr(p, "name", "?") for p in broken],
    }


def severe_injury_threshold() -> int:
    """重伤线（定值，拍板 §13-10）。"""
    return bget("hp20", "severe_injury_threshold", default=5)


def is_severely_injured(player: Any) -> bool:
    return 0 < player.hp <= severe_injury_threshold()
