"""Chorus 单位数据模型（G2 Reset: ish-bosheth 舞台观众）

Chorus 是临时生成的 NPC 观众，用于填补舞台席位至 6 人。
兼容 combat/damage_resolver.py 对 target 接口的要求。
"""

import random
from typing import Optional
from models.equipment import (
    Weapon, ArmorPiece, ArmorLayer, make_weapon, make_armor,
)
from utils.attribute import Attribute


# 提案 §4.5 d3 生成表 ──────────────────────────────────────────────
_CHORUS_WEAPON_POOL = ["小刀", "魔法弹幕", "高斯步枪"]
_CHORUS_OUTER_ARMOR_POOL = ["盾牌", "魔法护盾", "AT力场"]
_CHORUS_INNER_ARMOR_POOL = ["额外心脏", "不老泉", "晶化皮肤"]

_chorus_counter = 0


def _next_chorus_id() -> str:
    global _chorus_counter
    _chorus_counter += 1
    return f"chorus_{_chorus_counter}"


class _ChorusArmorSlots:
    """最小化护甲槽位，兼容 damage_resolver 的 target.armor 访问。"""

    def __init__(self):
        self.outer: list[ArmorPiece] = []
        self.inner: list[ArmorPiece] = []

    def get_active(self, layer: ArmorLayer) -> list[ArmorPiece]:
        lst = self.outer if layer == ArmorLayer.OUTER else self.inner
        return [a for a in lst if not a.is_broken]

    def has_any_outer_active(self) -> bool:
        return len(self.get_active(ArmorLayer.OUTER)) > 0

    def get_all_active(self) -> list[ArmorPiece]:
        return self.get_active(ArmorLayer.OUTER) + self.get_active(ArmorLayer.INNER)

    def get_piece(self, layer, attr):
        active = self.get_active(layer)
        for piece in active:
            if piece.attribute == attr:
                return piece
        return None

    def remove_piece(self, piece: ArmorPiece):
        piece.is_broken = True
        piece.current_hp = 0

    def is_last_inner(self, piece: ArmorPiece) -> bool:
        if piece.layer != ArmorLayer.INNER:
            return False
        inner_active = self.get_active(ArmorLayer.INNER)
        return len(inner_active) == 1 and inner_active[0] is piece

    def describe(self) -> str:
        parts = []
        for a in self.get_active(ArmorLayer.OUTER):
            parts.append(f"[外]{a.name}({a.attribute.value}{a.current_hp}/{a.max_hp})")
        for a in self.get_active(ArmorLayer.INNER):
            parts.append(f"[内]{a.name}({a.attribute.value}{a.current_hp}/{a.max_hp})")
        return " ".join(parts) if parts else "无"


class ChorusUnit:
    """临时观众单位。

    属性兼容 Player / PoliceUnit，使 resolve_damage 等无需特殊分支。
    """

    is_chorus = True

    def __init__(self, name: Optional[str] = None):
        self.player_id: str = _next_chorus_id()
        self.name: str = name or f"Chorus_{self.player_id.split('_')[1]}"

        # 基础属性
        self.hp: float = 1.0
        self.max_hp: float = 1.0
        self.base_attack: float = 0.5
        self.location: Optional[str] = None

        # 装备（d3 随机各一件）
        self.weapons: list[Weapon] = []
        self.armor = _ChorusArmorSlots()
        self.items: list = []

        self._roll_equipment()

        # 舞台状态
        self.emotion: Optional[str] = None
        self.stage_statuses: set = set()
        self.encore_layers: int = 0
        self.stage_entangle: list = []
        self.temp_hp_g2: float = 0.0
        self.temp_atk_g2: float = 0.0
        # G2 v0.6 物料牌临时效果
        self._card_damage_bonus: float = 0.0
        self._card_damage_bonus_target: Optional[str] = None
        self._card_debuff_damage_taken: float = 0.0
        self._card_no_attack_until_r4: Optional[str] = None
        self._card_temp_hp_until_r4: float = 0.0

        # 兼容字段
        self.is_awake: bool = True
        self.is_stunned: bool = False
        self.is_shocked: bool = False
        self.is_invisible: bool = False
        self.is_petrified: bool = False
        self.is_police: bool = False
        self.is_captain: bool = False
        self.is_criminal: bool = False
        self.has_police_protection: bool = False
        self.has_detection: bool = False
        self.has_seal: bool = False
        self.has_military_pass: bool = False
        self.talent = None
        self.talent_name = None
        self.controller = None
        self.vouchers: int = 0
        self._armor_gained_this_round: bool = False
        # 行动统计（round_manager R3 需要）
        self.last_action_type = None
        self.acted_this_round: bool = False
        self.no_action_streak: int = 0
        self.total_action_turns: int = 0

    # ── 装备随机生成 ───────────────────────────────────────────────
    def _roll_equipment(self):
        w_name = random.choice(_CHORUS_WEAPON_POOL)
        w = make_weapon(w_name)
        if w:
            self.weapons = [w]

        oa_name = random.choice(_CHORUS_OUTER_ARMOR_POOL)
        oa = make_armor(oa_name)
        if oa:
            self.armor.outer.append(oa)

        ia_name = random.choice(_CHORUS_INNER_ARMOR_POOL)
        ia = make_armor(ia_name)
        if ia:
            self.armor.inner.append(ia)

    # ── 查询 ──────────────────────────────────────────────────────
    def get_weapon(self, weapon_name):
        for w in self.weapons:
            if w and getattr(w, 'name', None) == weapon_name:
                return w
        return None

    def is_alive(self) -> bool:
        return self.hp > 0

    def is_on_map(self) -> bool:
        return self.is_awake and self.location is not None

    def can_be_targeted(self) -> bool:
        return self.is_alive() and self.is_on_map()
