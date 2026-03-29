"""AI 控制器常量定义"""
from models.equipment import make_weapon
from utils.attribute import Attribute
from engine.debug_config import (
    debug_ai, debug_ai_basic, debug_ai_detailed, debug_ai_full,
    debug_ai_combat_state, debug_ai_kill_opportunity,
    debug_ai_missile_attack, debug_ai_candidate_commands,
    debug_ai_attack_generation, debug_ai_development_plan,
    debug_ai_talent_selection, debug_system, debug_warning,
    debug_error, debug_info
)


EQUIPMENT_LOCATION = {
    "警棍": {"警察局"},
    "高斯步枪": {"军事基地"},
    "魔法弹幕": {"魔法所"},
    "盾牌": {"商店", "home"},
    "陶瓷护甲": {"商店"},
    "魔法护盾": {"魔法所"},
    "AT力场": {"军事基地"},
}

LOCATIONS = ["home", "商店", "魔法所", "医院", "军事基地", "警察局"]
LOCATION_ITEMS = {
    "home": ["凭证", "小刀", "盾牌"],
    "商店": ["打工", "小刀", "磨刀石", "隐身衣", "热成像仪", "陶瓷护甲", "防毒面具"],
    "魔法所": ["魔法护盾", "魔法弹幕", "远程魔法弹幕", "封闭",
              "地震", "地动山摇", "隐身术", "探测魔法"],
    "医院": ["打工", "晶化皮肤手术", "额外心脏手术", "不老泉手术",
            "防毒面具", "释放病毒"],
    "军事基地": ["通行证", "AT力场", "电磁步枪", "导弹控制权", "高斯步枪",
               "雷达", "隐形涂层"],
    "警察局": [],
}

NEED_PROVIDERS = {
    "weapon": [
        ("home", "小刀", "free"),
        ("商店", "小刀", "voucher"),
        ("魔法所", "魔法弹幕", "free"),       # 学习1回合
        ("军事基地", "高斯步枪", "pass"),
    ],
    "outer_armor": [
        ("home", "盾牌", "free"),
        ("商店", "陶瓷护甲", "voucher"),
        ("魔法所", "魔法护盾", "free"),        # 学习1回合
        ("军事基地", "AT力场", "pass"),
    ],
    "inner_armor": [
        ("医院", "晶化皮肤手术", "voucher_consume"),
        ("医院", "额外心脏手术", "voucher_consume"),
        ("医院", "不老泉手术", "voucher_consume"),
    ],
    "detection": [
        ("商店", "热成像仪", "voucher"),
        ("魔法所", "探测魔法", "free"),
        ("军事基地", "雷达", "pass"),
    ],
    "stealth": [
        ("商店", "隐身衣", "voucher"),
        ("魔法所", "隐身术", "free"),
        ("军事基地", "隐形涂层", "pass"),
    ],
    "voucher": [
        ("home", "凭证", "free"),
        ("商店", "打工", "free"),
        ("医院", "打工", "free"),
    ],
    "second_weapon": [
        ("魔法所", "魔法弹幕", "free"),
        ("军事基地", "高斯步枪", "pass"),
        ("商店", "小刀", "voucher"),
    ],
    "second_outer_armor": [
        ("商店", "陶瓷护甲", "voucher"),
        ("魔法所", "魔法护盾", "free"),
        ("军事基地", "AT力场", "pass"),
    ],
    "military_pass": [
        ("军事基地", "通行证", "free"),
    ],
}
# 属性克制：attacker_attr → 能有效打的 armor_attr 集合
EFFECTIVE_AGAINST = {
    Attribute.ORDINARY: {Attribute.ORDINARY, Attribute.MAGIC},
    Attribute.MAGIC: {Attribute.MAGIC, Attribute.TECH},
    Attribute.TECH: {Attribute.TECH, Attribute.ORDINARY},
    Attribute.TRUE: {Attribute.ORDINARY, Attribute.MAGIC, Attribute.TECH},
}
# 警察相关常量
POLICE_AOE_WEAPONS = {"地震", "地动山摇", "电磁步枪", "天星"}
# 法术前置
SPELL_PREREQUISITES = {
    "远程魔法弹幕": ["魔法弹幕"],
    "地动山摇": ["地震"],
    "地震": [],
    "魔法弹幕": [],
    "探测魔法": [],
    "魔法护盾": [],
    "封闭": [],
    "隐身术": [],
}

# 人格 → 需求优先级列表（按重要性排序）
# 每个元素是 (need_key, condition_fn_name)
# condition_fn_name 是 None 表示无条件需要，否则是检查方法名
PERSONALITY_NEEDS = {
    "aggressive": [
        "voucher",           # 先拿凭证
        "weapon",            # 拿武器
        "outer_armor",       # 拿1件外甲
        "second_weapon",     # 拿第2件不同属性武器（新增）
        "second_outer_armor", # 拿第2件外甲（新增）
    ],
    "defensive": [
        "voucher",
        "outer_armor",       # 先拿甲
        "second_outer_armor", # 第2件外甲
        "weapon",
        "detection",
        "inner_armor",
    ],
    "assassin": [
        "voucher",
        "weapon",
        "outer_armor",
        "stealth",           # 隐身
        "second_weapon",     # 第2件武器（新增）
        "second_outer_armor", # 第2件外甲（新增）
    ],
    "balanced": [
        "voucher",
        "weapon",
        "outer_armor",
        "second_outer_armor",
        "detection",
        "inner_armor",       # 新增
    ],
    "builder": [
        "voucher",
        "outer_armor",
        "weapon",
        "second_outer_armor",
        "inner_armor",
        # builder 还需要通行证和军事基地装备，这个特殊处理
    ],
    "political": [
        "voucher",
        "weapon",
        "outer_armor",
        # political 的特殊逻辑（去警察局）保留在外部
    ],
}