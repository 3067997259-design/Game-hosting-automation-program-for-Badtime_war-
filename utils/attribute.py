"""属性与克制系统"""

from enum import Enum


class Attribute(Enum):
    ORDINARY = "普通"
    MAGIC = "魔法"
    TECH = "科技"
    TRUE = "无视属性克制"


# 有效攻击关系表：EFFECTIVE[攻击方] 包含 能有效打击的护甲属性集合
_EFFECTIVE = {
    Attribute.ORDINARY: {Attribute.ORDINARY, Attribute.MAGIC},
    Attribute.MAGIC:    {Attribute.MAGIC, Attribute.TECH},
    Attribute.TECH:     {Attribute.TECH, Attribute.ORDINARY},
    Attribute.TRUE:     {Attribute.ORDINARY, Attribute.MAGIC, Attribute.TECH},
}


def is_effective(weapon_attr: Attribute, armor_attr: Attribute) -> bool:
    """判定武器属性对护甲属性是否有效（True=能打，False=被克制无效）"""
    if weapon_attr == Attribute.TRUE:
        return True
    return armor_attr in _EFFECTIVE.get(weapon_attr, set())