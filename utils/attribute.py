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


# 公开导出：与私有表同一对象，供 controllers/ai/constants.py 等引用
EFFECTIVE_AGAINST = _EFFECTIVE

# 护甲属性 → 能克制它的异属性武器属性（同属性可打但不算克制）。
# 程序化推导自 _EFFECTIVE，保证与克制规则永不漂移。
# COUNTER_ATTRIBUTE 为枚举键；COUNTER_ATTRIBUTE_NAME 为中文名键（"普通"→"科技" 等）。
COUNTER_ATTRIBUTE = {}
COUNTER_ATTRIBUTE_NAME = {}
for _armor in (Attribute.ORDINARY, Attribute.MAGIC, Attribute.TECH):
    for _weapon in (Attribute.ORDINARY, Attribute.MAGIC, Attribute.TECH):
        if _weapon is not _armor and _armor in _EFFECTIVE[_weapon]:
            COUNTER_ATTRIBUTE[_armor] = _weapon
            COUNTER_ATTRIBUTE_NAME[_armor.value] = _weapon.value


def is_effective(weapon_attr: Attribute, armor_attr: Attribute) -> bool:
    """判定武器属性对护甲属性是否有效（True=能打，False=被克制无效）"""
    if weapon_attr == Attribute.TRUE:
        return True
    return armor_attr in _EFFECTIVE.get(weapon_attr, set())