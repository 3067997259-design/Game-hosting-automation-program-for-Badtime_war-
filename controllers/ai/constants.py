"""AI 控制器常量定义

信源约定（2025-06 refactor）：
- LOCATIONS → engine.action_tables（引擎唯一定义）
- SPELL_PREREQUISITES → 从 action_tables 推导（引擎唯一定义）
- EFFECTIVE_AGAINST → utils.attribute（属性层唯一定义）
- LOCATION_ITEMS → 硬编码顺序（AI 行为敏感），与 ITEM_LOCATIONS 一致性由模块级 assert 保证
- EQUIPMENT_LOCATION / NEED_PROVIDERS / PERSONALITY_NEEDS / POLICE_AOE_WEAPONS → AI 层专属数据
"""
from typing import Dict, List

from utils.attribute import Attribute, EFFECTIVE_AGAINST
from engine.action_tables import (
    LOCATIONS, ITEM_LOCATIONS as _ENGINE_ITEM_LOCATIONS,
    SPELL_PREREQUISITES as _ENGINE_SPELL_PREREQUISITES,
)
from engine.debug_config import (
    debug_ai, debug_ai_basic, debug_ai_detailed, debug_ai_full,
    debug_ai_combat_state, debug_ai_kill_opportunity,
    debug_ai_missile_attack, debug_ai_candidate_commands,
    debug_ai_attack_generation, debug_ai_development_plan,
    debug_ai_talent_selection, debug_system, debug_warning,
    debug_error, debug_info
)

# ── 信源归并：从 action_tables 推导 SPELL_PREREQUISITES ──────────────
# action_tables 只记录有前置的法术（Dict[str,str]），AI 需要全量 Dict[str,List[str]]
_MAGIC_ITEMS = {item for item, locs in _ENGINE_ITEM_LOCATIONS.items() if "魔法所" in locs}
_SPELL_PREREQUISITES_RAW: Dict[str, List[str]] = {}
for _item in _MAGIC_ITEMS:
    _prereq = _ENGINE_SPELL_PREREQUISITES.get(_item, "")
    _SPELL_PREREQUISITES_RAW[_item] = [_prereq] if _prereq else []
SPELL_PREREQUISITES = _SPELL_PREREQUISITES_RAW
# 模块加载时验证与旧硬编码一致（防回退）
assert set(SPELL_PREREQUISITES.keys()) == _MAGIC_ITEMS, \
    f"SPELL_PREREQUISITES keys mismatch: {set(SPELL_PREREQUISITES.keys()) ^ _MAGIC_ITEMS}"


EQUIPMENT_LOCATION = {
    "警棍": {"警察局"},
    "高斯步枪": {"军事基地"},
    "魔法弹幕": {"魔法所"},
    "盾牌": {"商店", "home"},
    "陶瓷护甲": {"商店"},
    "魔法护盾": {"魔法所"},
    "AT力场": {"军事基地"},
}

# ── 信源归并：LOCATION_ITEMS 逆映射自 ITEM_LOCATIONS ──────────────────
# LOCATIONS 直接来自顶部 import（engine.action_tables 同一对象），此处不再重复绑定。
# 顺序为 AI 行为敏感（开发优先级），故硬编码；通过 assert 与引擎信源保持一致
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

# ── 模块加载时一致性断言：LOCATION_ITEMS ⊆ ITEM_LOCATIONS + 已知补充 ──
# 别名表（快捷名 → 引擎规范名），公开导出供测试复用
AI_TO_ENGINE_NAME = {"通行证": "办理通行证"}
# AI 层独有（非交互物品，或特殊操作），公开导出供测试复用
AI_ONLY_ITEMS = {"释放病毒"}
for _loc, _items in LOCATION_ITEMS.items():
    for _item in _items:
        if _item in AI_ONLY_ITEMS:
            continue
        _engine_item = AI_TO_ENGINE_NAME.get(_item, _item)
        _engine_locs = _ENGINE_ITEM_LOCATIONS.get(_engine_item)
        assert _engine_locs is not None, \
            f"LOCATION_ITEMS 中的 '{_item}' 在 engine.action_tables.ITEM_LOCATIONS 中不存在"
        assert _loc in _engine_locs, \
            f"LOCATION_ITEMS 声称 '{_item}' 在 '{_loc}'，但 engine 记录为 {_engine_locs}"

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
    "whetstone": [
        ("商店", "磨刀石", "voucher"),
    ],
}
# 不可消耗的被动物品（消耗后会失去关键能力）
PROTECTED_ITEMS = {"防毒面具", "隐身衣", "热成像仪", "隐形涂层", "雷达", "探测魔法"}

# EFFECTIVE_AGAINST → 已归并：from utils.attribute import EFFECTIVE_AGAINST（上文）
# SPELL_PREREQUISITES → 已归并：从 engine.action_tables 推导（上文）
# 警察相关常量
POLICE_AOE_WEAPONS = {"地震", "地动山摇", "电磁步枪", "天星"}

# 人格 → 需求优先级列表（按重要性排序）
# 每个元素是 (need_key, condition_fn_name)
# condition_fn_name 是 None 表示无条件需要，否则是检查方法名
PERSONALITY_NEEDS = {
    "aggressive": [
        "voucher",           # 先拿凭证
        "weapon",            # 拿武器
        "outer_armor",       # 拿1件外甲
        "second_weapon",     # 拿第2件不同属性武器（新增）
        "second_outer_armor",# 拿第2件外甲（新增）
        "inner_armor",       # aggressive 也需要内甲来提升续航，虽然优先级较低
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