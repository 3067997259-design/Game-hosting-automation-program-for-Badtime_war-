"""武器、护甲、物品的数据定义（Phase 2 完整版）"""

from enum import Enum


class WeaponRange(Enum):
    MELEE = "近战"
    RANGED = "远程"
    AREA = "范围"


class ArmorLayer(Enum):
    OUTER = "外层"
    INNER = "内层"


class Weapon:
    def __init__(self, name, attribute, base_damage, weapon_range,
                 requires_charge=False, charged_damage=None,
                 is_electric=False, special_tags=None):
        self.name = name
        self.attribute = attribute
        self.base_damage = base_damage
        self.weapon_range = weapon_range
        self.requires_charge = requires_charge
        self.is_charged = False
        self.charged_damage = charged_damage
        self.is_electric = is_electric
        self.special_tags = special_tags or []

    def get_effective_damage(self):
        if self.requires_charge and self.is_charged and self.charged_damage:
            return self.charged_damage
        return self.base_damage

    def __repr__(self):
        dmg = self.get_effective_damage()
        charge_str = ""
        if self.requires_charge:
            charge_str = " ⚡已蓄力" if self.is_charged else " (需蓄力)"
        return f"{self.name}({self.attribute.value} {dmg}{charge_str})"


class ArmorPiece:
    def __init__(self, name, attribute, layer, max_hp,
                 priority=0, can_regen=False, special_tags=None):
        self.name = name
        self.attribute = attribute
        self.layer = layer
        self.max_hp = max_hp
        self.current_hp = max_hp
        self.is_broken = False
        self.priority = priority
        self.can_regen = can_regen
        self.special_tags = special_tags or []

    def __repr__(self):
        status = "破碎" if self.is_broken else f"{self.current_hp}/{self.max_hp}"
        return f"{self.name}({self.attribute.value} {self.layer.value} {status})"


class Item:
    def __init__(self, name, item_type, effects=None):
        self.name = name
        self.item_type = item_type
        self.effects = effects or {}

    def __repr__(self):
        return f"{self.name}"


# ============ 预制工厂 ============

from utils.attribute import Attribute


def make_weapon(name):
    """根据名称创建标准武器"""
    table = {
        "拳击": lambda: Weapon("拳击", Attribute.ORDINARY, 0.5, WeaponRange.MELEE),
        "小刀": lambda: Weapon("小刀", Attribute.ORDINARY, 1.0, WeaponRange.MELEE),
        "警棍": lambda: Weapon("警棍", Attribute.ORDINARY, 1.0, WeaponRange.MELEE),
        "魔法弹幕": lambda: Weapon("魔法弹幕", Attribute.MAGIC, 1.0, WeaponRange.MELEE),
        "远程魔法弹幕": lambda: Weapon("远程魔法弹幕", Attribute.MAGIC, 1.0, WeaponRange.RANGED),
        "地震": lambda: Weapon("地震", Attribute.MAGIC, 0.5, WeaponRange.AREA),
        "地动山摇": lambda: Weapon("地动山摇", Attribute.MAGIC, 0.5, WeaponRange.AREA,
                               special_tags=["shock_2_targets"]),
        "电磁步枪": lambda: Weapon("电磁步枪", Attribute.TECH, 0.5, WeaponRange.AREA,
                               requires_charge=True, is_electric=True,
                               special_tags=["stun_on_hit", "hits_all_detected"]),
        "高斯步枪": lambda: Weapon("高斯步枪", Attribute.TECH, 1.0, WeaponRange.MELEE,
                               requires_charge=True, charged_damage=2.0),
        "导弹": lambda: Weapon("导弹", Attribute.TECH, 1.0, WeaponRange.RANGED,
                             special_tags=["missile"]),
    }
    factory = table.get(name)
    if factory:
        return factory()
    return None


def make_armor(name):
    """根据名称创建标准护甲"""
    table = {
        "盾牌": lambda: ArmorPiece(
            "盾牌", Attribute.ORDINARY, ArmorLayer.OUTER, 1.0,
            priority=100, special_tags=["shield_priority"]),
        "陶瓷护甲": lambda: ArmorPiece(
            "陶瓷护甲", Attribute.TECH, ArmorLayer.OUTER, 1.0,  # 修复：改为科技属性
            special_tags=["immune_electric"]),
        "魔法护盾": lambda: ArmorPiece(
            "魔法护盾", Attribute.MAGIC, ArmorLayer.OUTER, 1.0,
            can_regen=True),
        "AT力场": lambda: ArmorPiece(
            "AT力场", Attribute.TECH, ArmorLayer.OUTER, 1.0,
            can_regen=True),
        "晶化皮肤": lambda: ArmorPiece(
            "晶化皮肤", Attribute.TECH, ArmorLayer.INNER, 1.0),
        "额外心脏": lambda: ArmorPiece(
            "额外心脏", Attribute.ORDINARY, ArmorLayer.INNER, 1.0),
        "不老泉": lambda: ArmorPiece(
            "不老泉", Attribute.MAGIC, ArmorLayer.INNER, 1.0),
    }
    factory = table.get(name)
    if factory:
        return factory()
    return None


def make_item(name):
    """根据名称创建标准物品"""
    table = {
        "防毒面具": lambda: Item("防毒面具", "passive", {"grant": "virus_immune"}),
        "磨刀石": lambda: Item("磨刀石", "consumable", {"type": "upgrade", "target": "knife"}),
        "隐身衣": lambda: Item("隐身衣", "passive", {"grant": "invisible"}),
        "热成像仪": lambda: Item("热成像仪", "passive", {"grant": "detect"}),
        "隐形涂层": lambda: Item("隐形涂层", "passive", {"grant": "invisible"}),
        "雷达": lambda: Item("雷达", "tool", {"grant": "detect"}),
        "探测魔法": lambda: Item("探测魔法", "passive", {"grant": "detect"}),  # 新增
    }
    factory = table.get(name)
    if factory:
        return factory()
    return None