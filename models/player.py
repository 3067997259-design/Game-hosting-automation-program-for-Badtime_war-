"""玩家数据模型（Phase 4 完整版）"""

from typing import Optional
from models.equipment import ArmorLayer, ArmorPiece, Weapon, make_weapon
from utils.attribute import Attribute
# ──── 新增：控制器字段 ────
# 在现有 __init__ 参数列表末尾新增 controller 参数
from controllers.base import PlayerController
from controllers.human import HumanController


class ArmorSlots:
    """护甲槽位管理（修改版：支持同属性多个护甲，只限制同名）"""

    def __init__(self):
        # 改为列表存储，每层最多3件护甲
        self.outer = []  # 存储ArmorPiece对象
        self.inner = []  # 存储ArmorPiece对象

    def _get_layer_list(self, layer):
        return self.outer if layer == ArmorLayer.OUTER else self.inner

    def get_active(self, layer):
        d = self._get_layer_list(layer)
        return [a for a in d if not a.is_broken]

    def has_any_outer_active(self):
        return len(self.get_active(ArmorLayer.OUTER)) > 0

    def get_piece(self, layer, attr):
        """获取指定层和属性的护甲（返回优先级最高的活跃护甲）"""
        active = self.get_active(layer)
        # 按优先级降序排序
        active.sort(key=lambda p: p.priority, reverse=True)
        for piece in active:
            if piece.attribute == attr:
                return piece
        return None

    def equip(self, piece):
        d = self._get_layer_list(piece.layer)

        # 检查是否有同名护甲
        for existing_piece in d:
            if not existing_piece.is_broken and existing_piece.name == piece.name:
                return False, f"已有同名{piece.layer.value}护甲：{existing_piece.name}"

        # 检查层数限制（每层最多3件）
        active_count = len(self.get_active(piece.layer))
        if active_count >= 3:
            return False, f"{piece.layer.value}护甲已满3件"

        # 添加到列表
        d.append(piece)
        return True, "装备成功"

    def check_can_equip(self, piece):
        """非破坏性检查：是否能装备该护甲（不实际装备）"""
        d = self._get_layer_list(piece.layer)

        # 检查是否有同名护甲
        for existing_piece in d:
            if not existing_piece.is_broken and existing_piece.name == piece.name:
                return False, f"已有同名护甲"

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
    def __init__(self, player_id, name, controller: Optional[PlayerController] = None):
        self.player_id = player_id
        self.name = name
        if controller is None:
            controller = HumanController()
        self.controller: PlayerController = controller

        # 基础属性
        self.hp = 1.0
        self.max_hp = 1.0
        self.base_attack = 0.5

        # 位置
        self.location = None

        # 装备
        self.weapons: list[Weapon | None] = [make_weapon("拳击")]
        self.armor = ArmorSlots()
        self.items = []
        self._armor_gained_this_round = False

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
        self.hexagram_extra_turn = 0  # 六爻额外行动回合数（Phase 4）
        self.crime_extra_turn = False     # 犯罪触发的额外行动回合标记
        self.savior_extra_turn = False    # 愿负世主动发动的额外行动回合标记

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

        # G2 舞台状态（ish-bosheth）
        self.emotion: Optional[str] = None
        self.stage_statuses: set = set()
        self.encore_layers: int = 0
        self.stage_entangle: list = []
        self.temp_hp_g2: float = 0.0
        self.temp_atk_g2: float = 0.0
        # G2 v0.6 物料牌临时效果
        self._card_extra_play: bool = False     # 本回合可额外打 1 张牌
        self._card_d6_bonus_rounds: int = 0     # 跨声部小卡交换 D6+1 剩余轮次
        self._card_damage_bonus: float = 0.0    # 本回合 attack +X（荧光棒等）
        self._card_damage_bonus_target_id: Optional[str] = None  # 伤害加成限定目标玩家 ID
        self._card_damage_bonus_voice_filter: Optional[str] = None  # 伤害加成限定声部
        self._card_earplug: bool = False         # 耳塞：下次旋律/光色无视
        self._card_debuff_damage_taken: float = 0.0  # 倒彩等：受到伤害 +X 至下个 R4
        self._card_no_attack_until_r4: Optional[str] = None  # 调停：不能攻击的对方 id
        self._card_temp_hp_until_r4: float = 0.0  # 花束等：临时 HP 至下个 R4
        self._card_tear_ticket_active: bool = False  # 撕票：本回合击杀 Acc 额外 Regard -0.5
        self._dog_tag_active: bool = False       # 24K钛合金狗牌：本回合攻击无视属性克制
        self._photo_invitee_id: Optional[str] = None  # 聚光合影邀请的目标 id
        # v2.0 G2×G5 duet 奖励属性
        self._duet_d4_bonus: bool = False        # duet 谢幕后 D4+1
        self._duet_d6_bonus: bool = False        # duet 谢幕后 D6+1
        self._duet_damage_bonus: float = 0.0     # duet 谢幕后下次伤害+X
        self._embrace_g2_buff: float = 0.0       # 拥抱 G2：下次攻击伤害+X
        self._embrace_g5_buff: float = 0.0       # 拥抱 G5：下次被攻击免伤X

    def is_alive(self):
        return self.hp > 0

    def is_on_map(self):
        return self.is_awake and self.location is not None

    def can_be_targeted(self):
        # TODO: Replace scattered is_alive() + is_on_map() checks with this method
        return self.is_alive() and self.is_on_map()

    def get_d4_bonus(self):
        bonus = 0
        if self.no_action_streak >= 3:
            bonus += min(self.no_action_streak - 2, 3)
        # 天赋加成
        # 预留接口：未来天赋可实现 on_d4_bonus(player) 来修改D4加成
        if self.talent:
            talent_bonus = self.talent.on_d4_bonus(self) if hasattr(self.talent, 'on_d4_bonus') else 0
            bonus += talent_bonus
        # V1.92: 锚定D4加成（由涟漪天赋直接设置在玩家对象上）
        if getattr(self, '_anchor_d4_bonus_rounds', 0) > 0:
            bonus += getattr(self, '_anchor_d4_bonus_amount', 0)
        # G2 舞台法则 D4+1（非 G2 发动者的舞台参与者）
        if 'liberamente_vivace' in getattr(self, 'stage_statuses', set()):
            bonus += 1
        # G2 废墟谢幕 D4+1 奖励
        if getattr(self, '_g2_curtain_d4_bonus', False):
            bonus += 1
            self._g2_curtain_d4_bonus = False
        # v2.0 duet 谢幕 D4+1 奖励
        if self._duet_d4_bonus:
            bonus += 1
            self._duet_d4_bonus = False
        return bonus

    def get_d6_bonus(self):
        bonus = 0
        if self.talent:
            talent_bonus = self.talent.on_d6_bonus(self) if hasattr(self.talent, 'on_d6_bonus') else 0
            bonus += talent_bonus
        # v2.0 duet 谢幕 D6+1 奖励
        if self._duet_d6_bonus:
            bonus += 1
            self._duet_d6_bonus = False
        return bonus

    def has_weapon(self, weapon_name):
        return any(getattr(w, 'name', None) == weapon_name for w in self.weapons)

    def get_weapon(self, weapon_name):
        for w in self.weapons:
            if w and w.name == weapon_name:
                return w
        return None

    def add_weapon(self, weapon):
        self.weapons.append(weapon)

    def add_armor(self, piece):
        success, msg = self.armor.equip(piece)
        if success:
            self._armor_gained_this_round = True
        return success, msg

    def add_item(self, item):
        self.items.append(item)

    def clear_all_vouchers(self):
        self.vouchers = 0

    def _format_stage_status(self) -> str:
        """格式化 G2 ish-bosheth 舞台状态（单行）。"""
        parts = ["🎭 舞台中"]
        if self.emotion:
            emo_labels = {
                "accarezzevole": "入戏", "indifferenza": "抽离", "strappando": "反抗"
            }
            parts.append(f"情绪={emo_labels.get(self.emotion, self.emotion)}")
        ss = getattr(self, 'stage_statuses', set())
        if "spotlight" in ss:
            tmphp = getattr(self, 'temp_hp_g2', 0)
            tmpatk = getattr(self, 'temp_atk_g2', 0)
            parts.append(f"聚光灯(HP+{tmphp}/ATK+{tmpatk})")
        if "imbalance" in ss:
            parts.append("失衡")
        if "moderation_lock" in ss:
            parts.append("节制锁定")
        if "sognando_lock" in ss:
            parts.append("入戏锁定")
        if self.encore_layers > 0:
            parts.append(f"安可×{self.encore_layers}")
        return " | ".join(parts)

    def describe_status(self):
        lines = []
        lines.append(f"  玩家：{self.name} ({self.player_id})")
        if self.talent_name:
            talent_status = ""
            if self.talent:
                talent_status = self.talent.describe_status()
            lines.append(f"  天赋：{self.talent_name}" +
                         (f" ({talent_status})" if talent_status else ""))
        # G2 舞台状态
        if 'liberamente_vivace' in getattr(self, 'stage_statuses', set()):
            lines.append(self._format_stage_status())
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
