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
                 is_electric=False, charge_mandatory=True,special_tags=None):
        self.name = name
        self.attribute = attribute
        self.base_damage = base_damage
        self.weapon_range = weapon_range
        self.requires_charge = requires_charge
        self.is_charged = False
        self.charge_mandatory = charge_mandatory
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
            if self.is_charged:
                charge_str = " ⚡已蓄力"
            elif self.charge_mandatory:
                charge_str = " (需蓄力)"
            else:
                charge_str = " (可蓄力)"
        return f"{self.name}({self.attribute.value} {dmg}{charge_str})"

class ArmorPiece:
    def __init__(self, name, attribute, layer, max_hp,
                 priority=0, can_regen=False, special_tags=None,
                 defense_map=None, durability=0):
        self.name = name
        self.attribute = attribute
        self.layer = layer
        self.max_hp = max_hp
        self.current_hp = max_hp
        self.is_broken = False
        self.priority = priority
        self.can_regen = can_regen
        self.special_tags = special_tags or []
        # HP20 数值模型字段（experiment: hp20，v2.0 §2.3）——v1 路径不使用
        self.defense_map = defense_map or {}   # 属性中文名 → 防御值（主防/副防）
        self.durability = durability           # 耐久；归零即碎
        self.max_durability = durability

    def __repr__(self):
        if self.defense_map:  # hp20 形态
            defs = "/".join(f"{k}-{v}" for k, v in self.defense_map.items())
            return f"{self.name}({defs} 耐久{self.durability}/{self.max_durability})"
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


def _hp20_enabled():
    from engine import experiments
    return experiments.is_enabled("hp20")


def make_weapon(name):
    """根据名称创建标准武器。hp20 开关下伤害从 balance.json weapons 表读取。"""
    weapon = _make_weapon_v1(name)
    if weapon is not None and _hp20_enabled():
        from engine.balance import get as bget
        spec = bget("weapons", name, default=None)
        if isinstance(spec, dict):
            weapon.base_damage = spec.get("damage", weapon.base_damage)
            if weapon.charged_damage is not None:
                weapon.charged_damage = spec.get("charged_damage",
                                                 weapon.charged_damage)
    return weapon


def make_bow():
    """弓（M4，experiment: m4_gear）：人手一份的跨地点武器。

    RANGED 但带 no_lock_required tag——锁定不是前置只是命中加成
    （未锁定吃 accuracy.unlocked_ranged_penalty）。伤害/属性按已装
    模块在 shoot 路径动态换算，此处只是基础形态。
    """
    from engine.balance import get as bget
    return Weapon("弓", Attribute.ORDINARY,
                  bget("bow", "damage", default=3),
                  WeaponRange.RANGED,
                  special_tags=["bow", "no_lock_required"])


def _make_weapon_v1(name):
    """v1 武器定义（hp20 关闭时的原始路径，逐字节不变）"""
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
                               requires_charge=True, charge_mandatory=False, charged_damage=2.0),
        "导弹": lambda: Weapon("导弹", Attribute.TECH, 1.0, WeaponRange.RANGED,
                             special_tags=["missile"]),
    }
    factory = table.get(name)
    if factory:
        return factory()
    return None


def make_armor(name):
    """根据名称创建标准护甲。hp20 开关下外甲附加 defense_map/durability。

    注意：hp20 下医院内甲（晶化皮肤/额外心脏/不老泉）不再作为 ArmorPiece
    创建——改为玩家永久属性（locations/hospital.py 的 hp20 分支），
    本工厂仅在 v1 路径下产出它们。
    """
    piece = _make_armor_v1(name)
    if piece is not None and _hp20_enabled() and piece.layer == ArmorLayer.OUTER:
        from engine.balance import get as bget
        spec = bget("armor", name, default=None)
        if isinstance(spec, dict):
            piece.defense_map = dict(spec.get("defense", {}))
            piece.durability = spec.get("durability", 0)
            piece.max_durability = piece.durability
    return piece


def _make_armor_v1(name):
    """v1 护甲定义（hp20 关闭时的原始路径，逐字节不变）"""
    table = {
        "盾牌": lambda: ArmorPiece(
            "盾牌", Attribute.ORDINARY, ArmorLayer.OUTER, 1.0,
            priority=100, special_tags=["shield_priority"]),
        "陶瓷护甲": lambda: ArmorPiece(
            "陶瓷护甲", Attribute.ORDINARY, ArmorLayer.OUTER, 1.0,  # 恢复为普通属性
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