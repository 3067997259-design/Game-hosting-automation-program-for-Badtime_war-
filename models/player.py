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

        # 基础属性（hp20 实验开关下为 20 整数量纲，v2.0 §2.1）
        from engine import experiments as _exp
        if _exp.is_enabled("hp20"):
            from engine.balance import get as _bget
            self.hp = _bget("hp20", "player_max_hp", default=20)
            self.max_hp = self.hp
        else:
            self.hp = 1.0
            self.max_hp = 1.0
        self.base_attack = 0.5

        # HP20 数值模型字段（v1 路径下保持空值无副作用）
        self.inner_defense: dict = {}      # 晶化皮肤等永久身体改造（属性名→防御）
        self.regen_per_round: int = 0      # 不老泉每轮再生
        self.surgeries_done: set = set()   # 终身一次手术记录（hp20 下无内甲 piece 可查重）
        self.general_resistance: int = 0   # 负面效果通用抗性 0-100（§2.5.1）
        self.resist_pulse_rounds: int = 0  # 韧性脉冲剩余轮数

        # M3 命中/闪避与属性分轨字段（v1 路径下空值零副作用，v2.0 §2.7）
        self.stealth_attrs: set = set()    # 隐身覆盖的属性（隐身衣=普/隐身术=魔/隐形涂层=科）
        self.detection_attrs: set = set()  # 探测覆盖的属性（热成像=普/探测魔法=魔/雷达=科）
        self.moved_this_round: bool = False  # 本轮主动移动（移动闪避来源，每轮重置）

        # M4 消耗层字段（experiment: m4_gear，v1 路径下零副作用，v2.0 §2.8/§6）
        self.credits: int = 0              # 信用点（凭证退役后的经济通货，占位名）
        self.arrows: int = 0               # 箭矢（弓弹药，上限见 balance bow.max_arrows）
        self.bow_modules: list = []        # 已安装弓模块名（双槽）
        self.burn_stacks: int = 0          # 灼烧层数（R4 每层 1 伤，获甲扑灭 1 层）
        self._last_hook_round: int = -99   # 钩索共享冷却（拉人/拉己同钟）
        self.is_suspect: bool = False      # M5 白昼首攻嫌疑（不记罪但留痕）

        # M6 评分制字段（experiment: m6_scoring，v1/无 m6 零副作用，v2.0 §4/§5）
        self.damage_dealt: int = 0         # 累计造成的有效伤害（战果分）
        self.death_round: int = 0          # 死于第几轮（0=未死，存活系数用）
        self.applause: int = 0             # 喝彩点（两用资源：可消耗/计入终分）
        self.is_star: bool = False         # 往世层：死后成星
        self.starlight: int = 0            # 星光（每轮+1上限3，做星光行动）
        self.afterlife_score: int = 0      # 往世分（×0.5 计入终分）
        self.story_score: int = 0          # 剧情分（完结条，M7 天赋接入）

        # 位置
        self.location = None

        # 装备
        self.weapons: list[Weapon | None] = [make_weapon("拳击")]
        self.armor = ArmorSlots()
        self.items = []
        self._armor_gained_this_round = False
        if _exp.is_enabled("m4_gear"):
            # 弓是起始装备（人手一份的跨地点武器，v2.0 §2.8）
            from models.equipment import make_bow
            from engine.balance import get as _bget_bow
            self.weapons.append(make_bow())
            self.arrows = _bget_bow("bow", "initial_arrows", default=3)

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
        # K 模式借机攻击：本轮已使用的轮次号（每轮限 1 次，v2.0 §1.3）
        self._aoo_used_round = 0
        self.acted_this_round = False
        # 通用追加回合通道（round_manager R3 消费；天赋经 BaseTalent.grant_extra_turn 置位）
        # 默认 0 时通道惰性、不产生任何行为，保持 v1 字节不变
        self.pending_extra_turns: int = 0

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
        # hp20 重伤状态：先攻惩罚（经 get_initiative_bonus 同时覆盖 K 模式）
        from engine import experiments as _exp
        if _exp.is_enabled("hp20"):
            from combat.numeric_v2 import is_severely_injured
            from engine.balance import get as _bget
            if is_severely_injured(self):
                bonus += _bget("hp20", "severe_injury_initiative_penalty", default=-2)
            # 抗性降级的先攻惩罚（自消耗 flag，M2 临时映射，M3 改命中）
            degrade = getattr(self, '_resist_degrade_penalty', 0)
            if degrade:
                self._resist_degrade_penalty = 0
                bonus += degrade
        return bonus

    def grant_visibility_item(self, item_name):
        """M3 属性分轨：隐身/探测道具按 balance visibility 表写入属性集。

        仅 m3_accuracy 开关下生效（v1 的 is_invisible/has_detection 布尔
        机制由调用方照旧维护，两套并行互不干扰）。
        """
        from engine import experiments as _exp
        if not _exp.is_enabled("m3_accuracy"):
            return
        from engine.balance import get as _bget
        stealth_map = _bget("visibility", "stealth_items", default={}) or {}
        detect_map = _bget("visibility", "detection_items", default={}) or {}
        if item_name in stealth_map:
            self.stealth_attrs.add(stealth_map[item_name])
        if item_name in detect_map:
            self.detection_attrs.add(detect_map[item_name])

    def get_initiative_bonus(self):
        """先攻修正（K 常量行动制，experiment: k_initiative）。

        = D4 加成 + D6 加成之和：天赋的 on_d4_bonus/on_d6_bonus 钩子语义不变，
        统一折算为先攻修正（v2.0 §1.4）。K 模式下 no_action_streak 恒为 0
        （未行动保底已退役），故 get_d4_bonus 的 streak 分支不会掺入。
        注意：两个方法内的自消耗 flag（duet/curtain 等）每轮只应读取一次。
        """
        return self.get_d4_bonus() + self.get_d6_bonus()

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
