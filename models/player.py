"""玩家数据模型（Phase 4 完整版）"""

from models.equipment import ArmorLayer, ArmorPiece, Weapon, make_weapon
from utils.attribute import Attribute


class ArmorSlots:
    """护甲槽位管理"""

    def __init__(self):
        self.outer = {Attribute.ORDINARY: None, Attribute.MAGIC: None, Attribute.TECH: None}
        self.inner = {Attribute.ORDINARY: None, Attribute.MAGIC: None, Attribute.TECH: None}

    def _get_layer_dict(self, layer):
        return self.outer if layer == ArmorLayer.OUTER else self.inner

    def get_active(self, layer):
        d = self._get_layer_dict(layer)
        return [a for a in d.values() if a is not None and not a.is_broken]

    def has_any_outer_active(self):
        return len(self.get_active(ArmorLayer.OUTER)) > 0

    def get_piece(self, layer, attr):
        d = self._get_layer_dict(layer)
        piece = d.get(attr)
        if piece and not piece.is_broken:
            return piece
        return None

    def equip(self, piece):
        d = self._get_layer_dict(piece.layer)
        existing = d.get(piece.attribute)
        if existing is not None and not existing.is_broken:
            return False, f"已有同属性{piece.layer.value}护甲：{existing.name}"
        active_count = len(self.get_active(piece.layer))
        if active_count >= 3:
            return False, f"{piece.layer.value}护甲已满3件"
        d[piece.attribute] = piece
        return True, "装备成功"

    def check_can_equip(self, piece):
        """非破坏性检查：是否能装备该护甲（不实际装备）"""
        d = self._get_layer_dict(piece.layer)
        existing = d.get(piece.attribute)
        if existing is not None and not existing.is_broken:
            return False, f"已有同属性护甲"
        active_count = len(self.get_active(piece.layer))
        if active_count >= 3:
            return False, f"该层已满"
        return True, ""

    def remove_piece(self, piece):
        """移除（标记为破碎）一件护甲"""
        piece.is_broken = True
        piece.current_hp = 0

    def get_all_active(self):
        result = []
        result.extend(self.get_active(ArmorLayer.OUTER))
        result.extend(self.get_active(ArmorLayer.INNER))
        return result

    def get_all_pieces(self):
        """get_all_active的别名，兼容"""
        return self.get_all_active()

    def is_last_inner(self, piece):
        """判断某护甲是否是最后一件内层护甲"""
        if piece.layer != ArmorLayer.INNER:
            return False
        inner_active = self.get_active(ArmorLayer.INNER)
        return len(inner_active) == 1 and inner_active[0] is piece

    def describe(self):
        parts = []
        for a in self.get_active(ArmorLayer.OUTER):
            parts.append(f"[外]{a.name}({a.attribute.value}{a.current_hp}/{a.max_hp})")
        for a in self.get_active(ArmorLayer.INNER):
            parts.append(f"[内]{a.name}({a.attribute.value}{a.current_hp}/{a.max_hp})")
        return " ".join(parts) if parts else "无"


class Player:
    def __init__(self, player_id, name):
        self.player_id = player_id
        self.name = name

        # 基础属性
        self.hp = 1.0
        self.max_hp = 1.0
        self.base_attack = 0.5

        # 位置
        self.location = None

        # 装备
        self.weapons = [make_weapon("拳击")]
        self.armor = ArmorSlots()
        self.items = []

        # 经济
        self.vouchers = 0

        # 状态标记
        self.is_awake = False
        self.is_stunned = False
        self.is_shocked = False
        self.is_invisible = False
        self.is_petrified = False
        self.is_police = False
        self.is_captain = False
        self.is_criminal = False
        self.has_police_protection = False
        self.has_detection = False
        self.has_seal = False

        # 天赋（Phase 4）
        self.talent = None
        self.talent_name = None

        # 天赋辅助标记
        self.hexagram_extra_turn = False  # 六爻剪刀vs布的额外回合标记

        # 多回合进度
        self.progress = {}
        self.learned_spells = set()

        # 统计
        self.no_action_streak = 0
        self.total_action_turns = 0
        self.kill_count = 0
        self.last_action_type = None
        self.acted_this_round = False

        # 军事基地
        self.has_military_pass = False

    def is_alive(self):
        return self.hp > 0

    def is_on_map(self):
        return self.is_awake and self.location is not None

    def can_be_targeted(self):
        return self.is_alive() and self.is_on_map()

    def get_d4_bonus(self):
        bonus = 0
        if self.no_action_streak >= 3:
            bonus += min(self.no_action_streak - 2, 3)
        # 天赋加成
        if self.talent:
            talent_bonus = self.talent.on_d4_bonus(self) if hasattr(self.talent, 'on_d4_bonus') else 0
            bonus += talent_bonus
        return bonus

    def has_weapon(self, weapon_name):
        return any(w.name == weapon_name for w in self.weapons)

    def get_weapon(self, weapon_name):
        for w in self.weapons:
            if w.name == weapon_name:
                return w
        return None

    def add_weapon(self, weapon):
        self.weapons.append(weapon)

    def add_armor(self, piece):
        return self.armor.equip(piece)

    def add_item(self, item):
        self.items.append(item)

    def clear_all_vouchers(self):
        self.vouchers = 0

    def describe_status(self):
        lines = []
        lines.append(f"  玩家：{self.name} ({self.player_id})")
        if self.talent_name:
            talent_status = ""
            if self.talent:
                talent_status = self.talent.describe_status()
            lines.append(f"  天赋：{self.talent_name}" +
                         (f" ({talent_status})" if talent_status else ""))
        if not self.is_awake:
            lines.append("  状态：💤 睡眠中")
            return "\n".join(lines)
        lines.append(f"  位置：{self.location}")
        lines.append(f"  HP：{self.hp}/{self.max_hp}")
        lines.append(f"  购买凭证：{self.vouchers}")
        weapon_str = ", ".join(str(w) for w in self.weapons)
        lines.append(f"  武器：{weapon_str}")
        lines.append(f"  护甲：{self.armor.describe()}")
        item_str = ", ".join(str(i) for i in self.items) if self.items else "无"
        lines.append(f"  物品：{item_str}")
        return "\n".join(lines)
