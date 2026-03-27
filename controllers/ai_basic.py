"""
BasicAIController —— 基础AI控制器（v2.0 全面重写版）
═══════════════════════════════════════════════════
v2.0 重写修复清单：
  Bug 1:  choose() 中 self._player/self._game_state 初始化防御
  Bug 2:  _needs_virus_cure 增加 virus 为 None 的防御
  Bug 3:  警察缓存使用 police.units (flat list)，不再引用 teams/individual_units
  Bug 4:  _calculate_combat_power 使用 get_effective_damage() 而非 w.damage
  Bug 5:  发育阶段2 修复 if/elif 链
  Bug 6:  攻击前置检查：近战需 ENGAGED_WITH，远程需 LOCKED_BY
  Bug 7:  aggressive AI 不再无条件攻击警察
  Bug 8:  队长命令：检查 "police_command" in available_actions，生成正确格式
  Bug 9:  默认攻击层处理修正
  Bug 10: confirm() 上下文感知
  Bug 11: location 类型处理统一
  Bug 12: 导弹冷却递减安全
  Bug 13: 警察缓存使用 police.units 正确读取
  Bug 14: _aggressive_survival_strategy 检查 target is None
  Bug 15: 击杀机会判定考虑护甲
  Bug 16: _virus_cure_commands 接收 available_actions
  Bug 17: choose_multi 尊重 min_count
  Bug 18: _been_attacked_by 清理死亡玩家
"""
from models.equipment import make_weapon
from utils.attribute import Attribute
from typing import List, Dict, Optional, Any, Set, Tuple
from controllers.base import PlayerController
import random

EQUIPMENT_LOCATION = {
    "警棍": {"警察局"},
    "高斯步枪": {"军事基地"},
    "魔法弹幕": {"魔法所"},
    "盾牌": {"商店", "home"},
    "陶瓷护甲": {"商店"},
    "魔法护盾": {"魔法所"},
    "AT力场": {"军事基地"},
}

# 导入调试系统
from engine.debug_config import (
    debug_ai, debug_ai_basic, debug_ai_detailed, debug_ai_full,
    debug_ai_combat_state, debug_ai_kill_opportunity,
    debug_ai_missile_attack, debug_ai_candidate_commands,
    debug_ai_attack_generation, debug_ai_development_plan,
    debug_ai_talent_selection, debug_system, debug_warning,
    debug_error, debug_info
)

# ════════════════════════════════════════════════════════
#  SECTION 1: 常量
# ════════════════════════════════════════════════════════

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

# 需求 → 可满足该需求的 (地点, 物品名, 前置条件) 列表
# 前置条件: "voucher" = 需凭证, "pass" = 需通行证, "free" = 免费, "voucher_consume" = 需凭证且消耗所有
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


class BasicAIController(PlayerController):
    """
    基础AI：按阶段判定 + 候选命令优先级 + validate 过滤。
    6种人格: balanced, aggressive, defensive, political, assassin, builder
    """

    def __init__(self, personality: str = "balanced"):
        self.personality = personality
        self.event_log: List[Dict] = []
        self._round_number = 0

        # 内部记忆
        self._threat_scores: Dict[str, float] = {}
        self._been_attacked_by: set = set()
        self._my_kills: int = 0
        self._consecutive_forfeits: int = 0
        self._last_action: Optional[str] = None
        self._develop_plan: List[str] = []
        self._attempt_index: int = 0
        self.player_name: Optional[str] = None
        self._my_id: Optional[str] = None
        self._combat_target = None
        self._in_combat = False
        # 危险状态持久化
        self._danger_mode = False  # 是否处于持续危险模式

        # 导弹相关
        self._missile_cooldown = 0

        # 引用缓存（在 _generate_candidates 中赋值，用于 choose）
        self._player = None
        self._game_state = None

        # 警察状态缓存
        self._police_cache: Optional[Dict] = None
        self._current_phase: str = "development"   # 当前阶段：development / combat / endgame
        self._last_commands: List[str] = []         # 最近执行的命令历史
        self._should_become_captain_flag: bool = False  # 是否应该竞选队长

        self._virus_active: bool = False
        self._virus_location: Optional[str] = None
        # 警察发育状态追踪（political 队长用）
        # 发育计划：3个警察单位的目标配置
        # {unit_id: {"target_weapon": str, "target_armor": str,
        # "station": str, "phase": "pending"|"moving"|"equip_weapon"|"equip_armor"|"returning"|"stationed"}}
        self._police_dev_assignments: Dict[str, Dict] = {}
        self._police_dev_initialized = False

        self._low_threat_streak: Dict[str, int] = {} # 注意安静的人——他们可能在发育
        self._players_who_attacked: set = set() # 记录攻击过自己的玩家，识别潜在威胁和发育者

    # ════════════════════════════════════════════════════════
    #  安全工具方法
    # ════════════════════════════════════════════════════════

    def _pname(self) -> str:
        return self.player_name or "AI"

    @staticmethod
    def _safe_float(value) -> float:
        if value is None:
            return 0.0
        try:
            return float(value)
        except (ValueError, TypeError):
            return 0.0

    def _has_firefly_talent(self, player) -> bool:
        """检查玩家是否持有火萤IV型天赋"""
        talent = getattr(player, 'talent', None)
        if talent and hasattr(talent, 'name') and talent.name == "火萤IV型-完全燃烧":
            return True
        return False

    def _firefly_debuff_active(self, player) -> bool:
        """检查火萤IV型的 debuff 是否已生效"""
        talent = getattr(player, 'talent', None)
        if talent and hasattr(talent, 'debuff_started'):
            return talent.debuff_started
        return False


    # ════════════════════════════════════════════════════════
    #  接口实现：get_command
    # ════════════════════════════════════════════════════════

    def get_command(
        self, player: Any, game_state: Any,
        available_actions: List[str], context: Optional[Dict] = None
    ) -> str:
        self.player_name = player.name
        self._my_id = player.player_id
        attempt = context.get("attempt", 1) if context else 1

        if attempt == 1:
            self._candidates = self._generate_candidates(
                player, game_state, available_actions
            )
            self._attempt_index = 0
            debug_ai_candidate_commands(self._pname(),
                [f"候选命令列表（共{len(self._candidates)}条）"])
            for i, cmd in enumerate(self._candidates, 1):
                debug_ai_detailed(player.name, f"   {i}. {cmd}")
        else:
            self._attempt_index += 1

        if self._attempt_index < len(self._candidates):
            cmd = self._candidates[self._attempt_index]
            debug_ai_basic(player.name, f"尝试第{attempt}条：{cmd}")
        else:
            cmd = "forfeit"
            debug_ai_basic(player.name, "候选耗尽，兜底forfeit")
        return cmd

    # ════════════════════════════════════════════════════════
    #  接口实现：choose
    # ════════════════════════════════════════════════════════

    def choose(
        self, prompt: str, options: List[str],
        context: Optional[Dict] = None
    ) -> str:
        situation = (context or {}).get("situation", "")

        # ---- 猜拳 ----
        if situation in ("hexagram_my_choice", "hexagram_opp_choice", "mythland_rps"):
            return random.choice(options)

        # ---- 结界选目标 ----
        if situation == "mythland_pick_target":
            player_opts = [o for o in options if o != "不拉人"]
            if player_opts:
                return max(player_opts, key=lambda name: self._threat_scores.get(name, 0))
            return "不拉人"

        # ---- 石化 ----
        if situation == "petrified":
            for opt in options:
                if "解除" in opt:
                    return opt
            return options[0]

        # ---- 一刀缭断 ----
        if situation == "oneslash_pick_weapon":
            # 选伤害最高的近战武器
            if self._player:
                best_name = None
                best_dmg = -1
                for w in getattr(self._player, 'weapons', []):
                    if w and w.name in options:
                        dmg = self._get_weapon_damage(w)
                        if dmg > best_dmg:
                            best_dmg = dmg
                            best_name = w.name
                if best_name:
                    return best_name
            return options[0]  # fallback
        if situation == "oneslash_pick_target":
            return max(options, key=lambda name: self._threat_scores.get(name, 0), default=options[0])

        # ---- 天赋T0 ----
        if situation == "talent_t0":
            talent_name = (context or {}).get("talent_name", "")

            # 愿负世（主动发动）：只在神性足够高时发动
            if "愿负世" in talent_name:
                talent = getattr(self._player, 'talent', None) if self._player else None
                divinity = getattr(talent, 'divinity', 0) if talent else 0
                if divinity >= 8:
                    for opt in options:
                        if "发动" in opt:
                            return opt
                elif self._player and self._player.hp <= 1.0 and divinity >= 4:
                    nearby = self._get_same_location_targets(self._player, self._game_state) if self._game_state else []
                    if nearby:
                        for opt in options:
                            if "发动" in opt:
                                return opt
                # Not worth activating — save for passive trigger (+2 bonus divinity)
                for opt in options:
                    if "不发动" in opt or "正常" in opt:
                        return opt
                return options[-1]

            # 一刀缭断：只在有面对面的高血量目标时发动
            if talent_name == "一刀缭断":
                if self._player and self._game_state:
                    target = self._pick_target(self._player, self._game_state)
                    if (self._is_development_complete(self._player, self._game_state)
                    and target and self._same_location(self._player, target) and target.hp >= 2.0):
                        for opt in options:
                            if "发动" in opt:
                                return opt
                for opt in options:
                    if "不发动" in opt or "正常" in opt:
                        return opt
                return options[-1]

            # 请一直，注视着我（全息影像）：被攻击或同地点有多个敌人或者对警察单位起了杀心时发动
            if "注视" in talent_name:
                attackers = len(self._been_attacked_by)
                if attackers >= 1:
                    for opt in options:
                        if "发动" in opt:
                            return opt
                if self._player and self._game_state:
                    nearby = self._get_same_location_targets(self._player, self._game_state)
                    if len(nearby) >= 2:
                        for opt in options:
                            if "发动" in opt:
                                return opt
                    # 新增：有AOE武器且地图上有不在自己位置的警察单位 → 发动全息影像把警察拉过来
                    if self._has_aoe_weapon(self._player):
                        pc = self._police_cache or {}
                        units = pc.get("units", [])
                        has_remote_police = False
                        for unit in units:
                            if (unit.get("is_alive")
                                    and unit.get("location")
                                    and unit["location"] != self._get_location_str(self._player)):
                                has_remote_police = True
                                break
                        if has_remote_police:
                            for opt in options:
                                if "发动" in opt:
                                    return opt
                for opt in options:
                    if "不发动" in opt or "正常" in opt:
                        return opt
                return options[-1]

            # 遗世独立的幻想乡/神话之外：发育完成且有目标时发动
            if "幻想乡" in talent_name or "神话之外" in talent_name:
                if self._player and self._game_state:
                    if self._is_development_complete(self._player, self._game_state):
                        nearby = self._get_same_location_targets(self._player, self._game_state)
                        if nearby:
                            for opt in options:
                                if "发动" in opt:
                                    return opt
                for opt in options:
                    if "不发动" in opt or "正常" in opt:
                        return opt
                return options[-1]

            # 天星：被攻击或同地点有多个敌人时发动（与全息影像一致）
            if talent_name == "天星":
                attackers = len(self._been_attacked_by)
                if attackers >= 1:
                    for opt in options:
                        if "发动" in opt:
                            return opt
                if self._player and self._game_state:
                    nearby = self._get_same_location_targets(self._player, self._game_state)
                    if len(nearby) >= 2:
                        for opt in options:
                            if "发动" in opt:
                                return opt
                for opt in options:
                    if "不发动" in opt or "正常" in opt:
                        return opt
                return options[-1]

            # 六爻/往世的涟漪：默认发动（get_t0_option已做前置检查）
            for opt in options:
                if "发动" in opt:
                    return opt
            return options[0]

        # ---- 加入警察 ----
        if situation in ("recruit_pick_1", "recruit_pick_2"):
            if self.personality == "aggressive":
                priority = ["警棍", "盾牌", "购买凭证"]
            elif self.personality == "defensive":
                priority = ["盾牌", "警棍", "购买凭证"]
            elif self.personality == "political":
                priority = ["购买凭证", "警棍", "盾牌"]
            else:
                priority = ["盾牌", "购买凭证", "警棍"]
            for preferred in priority:
                if preferred in options:
                    return preferred
            return options[0]

        # ---- 竞选队长（Bug1修复：安全引用 self._player/self._game_state）----
        if situation == "captain_election":
            should = False
            if self._player is not None and self._game_state is not None:
                should = self._should_become_captain(self._player, self._game_state)
            else:
                # 没有缓存时，political 默认竞选
                should = (self.personality == "political")
            if should:
                for opt in options:
                    if "竞选" in opt:
                        return opt
            else:
                for opt in options:
                    if "不竞选" in opt or "放弃" in opt:
                        return opt
            return options[0]

        # ---- 六爻 ----
        if situation == "hexagram_thunder_target":
            return max(options, key=lambda name: self._threat_scores.get(name, 0), default=options[0])
        if situation == "hexagram_pick_armor":
            armor_priority = ["AT力场", "陶瓷护甲", "魔法护盾", "盾牌", "晶化皮肤", "不老泉", "额外心脏"]
            for preferred in armor_priority:
                if preferred in options:
                    return preferred
            return options[0]
        if situation == "hexagram_pick_opponent":
            return max(options, key=lambda name: self._threat_scores.get(name, 0), default=options[0])

        # ---- 涟漪 ----
        if situation == "ripple_choose_method":
            # 单人模式下方式二（献诗）收益更高
            for opt in options:
                if "献诗" in opt:
                    return opt
            return options[0]
        if situation == "resurrection_pick_target":
            # 单人模式下挂自己收益最大
            if self._player and self._player.name in options:
                return self._player.name
            return options[0]
        if situation == "ripple_anchor_type":
            for opt in options:
                if "击杀" in opt:
                    return opt
            return options[0]
        if situation == "ripple_poem_target":
            # 献诗选自己（触发爱与记忆之诗，4发伤害）
            if self._player and self._player.name in options:
                return self._player.name
            player_opts = [o for o in options if o != "取消"]
            return player_opts[0] if player_opts else options[0]

        if situation in ("ripple_anchor_kill_target", "ripple_anchor_armor_target"):
            player_opts = [o for o in options if o != "取消"]
            if player_opts:
                return max(player_opts, key=lambda name: self._threat_scores.get(name, 0))
            return options[0]
        if situation == "ripple_anchor_armor_pick":
            non_cancel = [o for o in options if o != "取消"]
            return non_cancel[0] if non_cancel else options[0]
        if situation == "ripple_anchor_acquire_item":
            priority = ["高斯步枪", "AT力场", "导弹控制权", "远程魔法弹幕", "陶瓷护甲", "魔法护盾", "电磁步枪"]
            for item in priority:
                if item in options:
                    return item
            non_cancel = [o for o in options if o != "取消"]
            return non_cancel[0] if non_cancel else options[0]
        if situation == "ripple_anchor_arrive_loc":
            non_cancel = [o for o in options if o != "取消"]
            if non_cancel:
                return random.choice(non_cancel)
            return options[0]
        if situation == "ripple_anchor_fail":
            if self.personality == "aggressive":
                for opt in options:
                    if "留在当下" in opt:
                        return opt
            for opt in options:
                if "回到过去" in opt:
                    return opt
            return options[0]
        if situation == "ripple_destiny_damage":
            return max(options, key=lambda name: self._threat_scores.get(name, 0), default=options[0])
        if situation == "ripple_hexagram_free_choice":
            for opt in options:
                if "天雷" in opt:
                    return opt
            return options[0]

        # ---- 献予律法之诗：额外行动 ----
        if situation in ("poem_law_extra_action", "poem_law_police_action"):
            return options[0] if options else ""

        # ---- 默认 ----
        return options[0]

    # ════════════════════════════════════════════════════════
    #  接口实现：choose_multi  （Bug17修复：尊重 min_count）
    # ════════════════════════════════════════════════════════

    def choose_multi(
        self, prompt: str, options: List[str],
        max_count: int, min_count: int = 0,
        context: Optional[Dict] = None
    ) -> List[str]:
        if not options:
            return []
        sorted_opts = sorted(
            options, key=lambda name: self._threat_scores.get(name, 0), reverse=True
        )
        # 取 min_count 和 max_count 之间的合理数量
        count = max(min_count, min(max_count, len(sorted_opts)))
        return sorted_opts[:count]

    # ════════════════════════════════════════════════════════
    #  接口实现：confirm  （Bug10修复：上下文感知）
    # ════════════════════════════════════════════════════════

    def confirm(self, prompt: str, context: Optional[Dict] = None) -> bool:
        # 强买通行证：当prompt包含"强买通行证"且AI需要去军事基地时同意
        if "强买通行证" in prompt:
            # 如果AI手上所有武器都是普通属性，需要去军事基地拿科技武器
            if self._player and not self._has_non_ordinary_weapon(self._player):
                return True
            # 其他情况（如builder正常发育路线）也可以同意
            if self.personality == "builder":
                return True
            return False

        if not context:
            return False
        situation = context.get("phase", "")
        if situation == "response_window":
            talent_name = context.get("talent_name", "")
            action_type = context.get("action_type", "")
            if talent_name == "你给路打油" and action_type in ("attack", "special"):
                if self._player:
                    hp = self._player.hp
                    outer = self._count_outer_armor(self._player)
                    if hp <= 1.0:
                        return True
                    if outer == 0 and hp <= 1.5:
                        return True
                    return False
                return True
        return False

    def _is_in_savior_state(self, player) -> bool:
        """检查玩家是否处于救世主状态"""
        talent = getattr(player, 'talent', None)
        if talent and hasattr(talent, 'is_savior'):
            return talent.is_savior
        return False
    # ════════════════════════════════════════════════════════
    #  接口实现：on_event
    # ════════════════════════════════════════════════════════

    def on_event(self, event: Dict) -> None:
        self.event_log.append(event)
        event_type = event.get("type", "")
        target = event.get("target")
        attacker = event.get("attacker", "")

        # 被攻击
        if event_type == "attack" and self.player_name is not None:
            if target == self.player_name:
                self._been_attacked_by.add(attacker)
                self._threat_scores[attacker] = self._threat_scores.get(attacker, 0) + 20
            # 记录所有发起过攻击的玩家（用于识别发育者）
            self._players_who_attacked.add(attacker)

        # 被找到（find 事件用 "player" 字段表示发起者，"target" 是 player_id）
        if event_type == "find" and self._my_id is not None:
            finder = event.get("player", "")
            if target == self._my_id:
                # 需要把 player_id 转换为 name
                finder_name = self._pid_to_name(finder)
                if finder_name:
                    self._threat_scores[finder_name] = self._threat_scores.get(finder_name, 0) + 10

        # 被锁定
        if event_type == "lock" and self._my_id is not None:
            locker = event.get("player", "")
            if target == self._my_id:
                locker_name = self._pid_to_name(locker)
                if locker_name:
                    self._threat_scores[locker_name] = self._threat_scores.get(locker_name, 0) + 15

        # 有人放毒
        if event_type == "release_virus":
            releaser_pid = event.get("player", "")
            releaser_name = self._pid_to_name(releaser_pid)
            if releaser_name and releaser_name != self.player_name:
                self._threat_scores[releaser_name] = self._threat_scores.get(releaser_name, 0) + 20

        # 有人死亡
        if event_type == "death":
            killer = event.get("killer", "")
            if killer:
                self._threat_scores[killer] = self._threat_scores.get(killer, 0) + 30

        # 有人竞选队长
        if event_type == "election":
            candidate_pid = event.get("player", "")
            candidate_name = self._pid_to_name(candidate_pid)
            if candidate_name and candidate_name != self.player_name:
                self._threat_scores[candidate_name] = self._threat_scores.get(candidate_name, 0) + 10

        # 有人当上队长
        if event_type == "captain_elected":
            captain_pid = event.get("captain", "")
            captain_name = self._pid_to_name(captain_pid)
            if captain_name and captain_name != self.player_name:
                self._threat_scores[captain_name] = self._threat_scores.get(captain_name, 0) + 30



    def _get_last_attacker(self, player, state) -> Optional[Any]:
        """获取最后一个攻击自己的存活玩家"""
        # 从 event_log 倒序查找最近的攻击事件
        for event in reversed(self.event_log):
            if event.get("type") == "attack" and event.get("target") == player.name:
                attacker_name = event.get("attacker", "")
                # 找到对应的存活玩家
                for pid in state.player_order:
                    target = state.get_player(pid)
                    if target and target.is_alive() and target.name == attacker_name:
                        return target
        return None

    def _pid_to_name(self, player_id: str) -> Optional[str]:
        """将 player_id 转换为 player.name"""
        if not self._game_state:
            return None
        p = self._game_state.get_player(player_id)
        return p.name if p else None


    def _someone_has_virus_immunity(self, state) -> bool:
        """检查局内是否有其他玩家持有防毒面具或封闭"""
        for pid in state.player_order:
            if pid == self._my_id:
                continue
            p = state.get_player(pid)
            if not p or not p.is_alive():
                continue
            # 检查防毒面具
            items = getattr(p, 'items', [])
            for item in items:
                if getattr(item, 'name', '') == "防毒面具":
                    return True
            # 检查封闭
            if getattr(p, 'has_seal', False):
                return True
            if "封闭" in getattr(p, 'learned_spells', set()):
                return True
        return False

    def _count_opponents_without_immunity(self, player, state) -> int:
        """统计没有病毒免疫的存活对手数量"""
        count = 0
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            p = state.get_player(pid)
            if not p or not p.is_alive():
                continue
            if not self._has_virus_immunity(p):
                count += 1
        return count

    def _should_release_virus(self, player, state) -> bool:
        """判断 assassin 是否应该在医院释放病毒"""
        # 仅 assassin 人格
        if self.personality != "assassin":
            return False
        # 必须在医院
        if self._get_location_str(player) != "医院":
            return False
        # 病毒已激活则不放
        virus = getattr(state, 'virus', None)
        if virus and getattr(virus, 'is_active', False):
            return False
        # 自己必须有病毒免疫
        if not self._has_virus_immunity(player):
            return False
        # 警察成员（非队长）不能放毒（游戏规则阻止）
        if getattr(player, 'is_police', False) and not getattr(player, 'is_captain', False):
            return False
        # 对手免疫人数检查
        alive_count = len([p for p in state.players.values() if p.is_alive()])
        vulnerable = self._count_opponents_without_immunity(player, state)
        if alive_count >= 4:
            return vulnerable >= 2
        else:
            return vulnerable >= 1

    # ════════════════════════════════════════════════════════
    #  核心：候选命令生成
    # ════════════════════════════════════════════════════════

    def _generate_candidates(self, player, state, available_actions: List[str]) -> List[str]:
        self._my_id = player.player_id
        self.player_name = player.name
        self._player = player
        self._game_state = state
        if not hasattr(self, '_virus_prevention_done'):
            self._virus_prevention_done = False  # 每局只触发一次预防性拿面具

        # 只在正常 T1 阶段递增轮次（避免特殊调用路径重复递增）
        # 通过检查 state.current_round 来同步，而非自增
        current_round = getattr(state, 'current_round', 0)
        if current_round > self._round_number:
            self._round_number = current_round


        self._update_threat_scores(player, state)
        self._read_police_state(state)
        self._update_combat_status(player, state)
        self._cleanup_dead_players(state)  # Bug18
        # political fallback 判定（每轮重新计算，不持久化）
        # 当已有队长但不是自己、或警察全灭时，采用 balanced 策略
        if self.personality == "political":
            self._political_fallback_level = self._political_should_fallback(player, state)
            self._political_in_balanced_fallback = (self._political_fallback_level == "full_balanced")
            self._political_develop_only = (self._political_fallback_level == "develop_only")
        else:
            self._political_fallback_level = "none"
            self._political_in_balanced_fallback = False
            self._political_develop_only = False

        candidates = []

        # 未起床
        if not player.is_awake:
            return ["wake"]

        # ===== 队长指挥（高优先但不独占） =====
        if getattr(player, 'is_captain', False) and "police_command" in available_actions:
            debug_ai_basic(player.name, "作为队长，生成警察指挥命令")
            captain_cmds = self._cmd_captain(player, state, available_actions)
            if captain_cmds:
                # 只取第1条警察命令（每回合只能执行1条）
                candidates.append(captain_cmds[0])
            # 不再 return，继续生成其他候选命令

        # ===== 救世主状态：最高攻击优先级 =====
        if self._is_in_savior_state(player) and player.hp > 0.5:
            debug_ai_basic(player.name, "救世主状态激活，优先攻击")
            # 优先攻击最后一个攻击自己的人
            last_attacker = self._get_last_attacker(player, state)
            if last_attacker:
                attack_cmds = self._cmd_attack(player, state, available_actions, last_attacker)
                if attack_cmds:
                    candidates.extend(attack_cmds)
                    candidates.append("forfeit")
                    return candidates
            # 没有明确的攻击者，攻击威胁最高的目标
            attack_cmds = self._cmd_attack(player, state, available_actions)
            if attack_cmds:
                candidates.extend(attack_cmds)
                candidates.append("forfeit")
                return candidates

        # ===== 病毒预防（每局一次）=====
        if not self._virus_prevention_done and not self._has_virus_immunity(player):
            if self._someone_has_virus_immunity(state):
                self._virus_prevention_done = True
                debug_ai_basic(player.name, "检测到有人持有病毒免疫，主动预防")
                prevention_cmds = self._cmd_virus(player, state, available_actions)
                if prevention_cmds:
                    candidates.extend(prevention_cmds)
                    candidates.append("forfeit")
                    return candidates

        # L530-549 整段替换为：
        # ===== 病毒应急 =====
        if self._needs_virus_cure(player, state):
            debug_ai_basic(player.name, "进入病毒应急模式")
            candidates.extend(self._cmd_virus(player, state, available_actions))
            if candidates:
                candidates.append("forfeit")
                return candidates
        # ===== Assassin 主动放毒 =====
        if self._should_release_virus(player, state) and "special" in available_actions:
            debug_ai_basic(player.name, "Assassin 在医院放毒！")
            candidates.append("special 释放病毒")
            # 不 return —— 放毒是"顺手"行为，继续生成其他候选命令
            # 放毒命令会排在候选列表前面，优先被尝试

        # ===== 危险情况 / 持续危险模式 =====
        if self._is_critical(player, state):
            self._danger_mode = True

        if self._danger_mode:
            if self._is_danger_resolved(player):
                debug_ai_basic(player.name, "危险解除，退出危险模式")
                self._danger_mode = False
                # 不 return，fall through 到下面的正常逻辑
            else:
                debug_ai_basic(player.name, "处于危险模式")
                if self._is_pursued_by_police(player, state):
                    if self._can_fight_police(player, state):
                        fight_cmds = self._cmd_fight_police(player, state, available_actions)
                        if fight_cmds:
                            candidates.extend(fight_cmds)
                            # 保留 danger_develop 作为 fallback（fight 命令可能被引擎拒绝）
                            danger_fallback = self._cmd_danger_develop(player, state, available_actions)
                            for cmd in danger_fallback:
                                if cmd not in candidates:
                                    candidates.append(cmd)
                            candidates.append("forfeit")
                            return candidates

                danger_cmds = self._cmd_danger_develop(player, state, available_actions)
                candidates.extend(danger_cmds)
                if candidates:
                    candidates.append("forfeit")
                    return candidates
        # ===== Political 非队长：优先政治路径（含未入警阶段） =====
        if (not getattr(player, 'is_captain', False)
            and self.personality == "political"):
            if not self._political_in_balanced_fallback:
                political = self._cmd_police_political(player, state, available_actions)
                candidates.extend(political)
                develop = self._cmd_develop(player, state, available_actions)
                for cmd in develop:
                    if cmd not in candidates:
                        candidates.append(cmd)
                candidates.append("forfeit")
                # 去重后返回（早返回路径也需要去重）
                seen = set()
                deduped = []
                for cmd in candidates:
                    if cmd not in seen:
                        seen.add(cmd)
                        deduped.append(cmd)
                return deduped
            debug_ai_basic(player.name, "political fallback 激活：采用 balanced 行动策略")

        # political develop_only 模式：不进入/不维持战斗
        if self._political_develop_only and self._in_combat:
            self._in_combat = False
            self._combat_target = None

        # ===== 战斗状态 =====
        if self._in_combat and self._combat_target:
            if self._should_continue_combat(player, self._combat_target):
                debug_ai_combat_state(player.name, f"战斗目标: {self._combat_target.name}")
                combat_cmds = self._cmd_attack(player, state, available_actions, self._combat_target)
                if combat_cmds:
                    candidates.extend(combat_cmds)
                    candidates.append("forfeit")
                    return candidates
            else:
                debug_ai_basic(player.name, "退出战斗状态")
                self._in_combat = False
                target_ref = self._combat_target
                self._combat_target = None
                # 如果是因为武器被克制退出，优先去拿新武器
                if target_ref and self._all_weapons_countered(player, target_ref):
                    debug_ai_basic(player.name, "所有武器被克制，寻找新武器")
                    rearm_cmds = self._cmd_rearm(player, state, available_actions)
                    if rearm_cmds:
                        candidates.extend(rearm_cmds)
                        candidates.append("forfeit")
                        return candidates

        # ===== 击杀机会 =====
        if self._has_kill_opportunity(player, state) and not self._political_develop_only:
            debug_ai_basic(player.name, "发现击杀机会！")
            kill_cmds = self._cmd_attack(player, state, available_actions)
            if kill_cmds:
                candidates.extend(kill_cmds)
                # 备用发育
                dev = self._cmd_develop(player, state, available_actions)
                if dev:
                    candidates.append(dev[0])
                candidates.append("forfeit")
                return candidates

        # ===== 发育 =====
        debug_ai_development_plan(player.name, "进入发育模式")
        develop = self._cmd_develop(player, state, available_actions)
        candidates.extend(develop)

        # ===== 发育受阻：develop 为空但发育未完成 =====
        if not develop and not self._is_development_complete(player, state):
            if self.personality in ("aggressive", "assassin", "balanced") or self._political_in_balanced_fallback:
                debug_ai_basic(player.name, "发育受阻，转为进攻冲散人群")
                attack_cmds = self._cmd_attack(player, state, available_actions)
                for cmd in attack_cmds:
                    if cmd not in candidates:
                        candidates.append(cmd)
            # 兜底：去敌人最少的有用地点
            if not candidates:
                fallback_loc = self._pick_fallback_destination(player, state)
                if fallback_loc:
                    candidates.append(f"move {fallback_loc}")

        # ===== 发育完成后主动进攻 =====
        if self._is_development_complete(player, state) and not self._political_develop_only:
            debug_ai_basic(player.name, "发育完成，尝试进攻")
            attack_cmds = self._cmd_attack(player, state, available_actions)
            for cmd in attack_cmds:
                if cmd not in candidates:
                    candidates.insert(0, cmd)

        # ===== 政治型 =====
        if self.personality == "political":
            political = self._cmd_police_political(player, state, available_actions)
            candidates.extend(political)

        # ===== 常规攻击补充 =====
        is_political_no_attack = self._political_develop_only or (self.personality == "political" and self._political_fallback_level == "none")
        if not is_political_no_attack and ("attack" in available_actions or "find" in available_actions or "lock" in available_actions):
            attack = self._cmd_attack(player, state, available_actions)
            for cmd in attack:
                if cmd not in candidates:
                    candidates.append(cmd)

        candidates.append("forfeit")
        # 去重
        seen = set()
        deduped = []
        for cmd in candidates:
            if cmd not in seen:
                seen.add(cmd)
                deduped.append(cmd)
        return deduped



    def _pick_fallback_destination(self, player, state) -> Optional[str]:
        """发育受阻时的兜底：在能满足需求的地点中选敌人最少的"""
        unmet_needs = self._get_unmet_needs(player, state)
        if not unmet_needs:
            return self._find_nearest_enemy_location(player, state)

        loc = self._get_location_str(player)
        # 收集所有能满足至少一个需求的地点
        useful_locs = set()
        for need_key, _ in unmet_needs:
            for (ploc, item_name, _) in NEED_PROVIDERS.get(need_key, []):
                if not self._already_has_item(player, item_name):
                    useful_locs.add(ploc)

        # 排除当前位置和已在的 home
        useful_locs.discard(loc)
        if self._is_at_home(player):
            useful_locs.discard("home")

        if not useful_locs:
            return self._find_nearest_enemy_location(player, state)

        # 按敌人数排序，选最少的
        scored = []
        for dest in useful_locs:
            enemies = self._count_enemies_at(dest, player, state)
            scored.append((dest, enemies))
        scored.sort(key=lambda x: x[1])
        return scored[0][0]

    # ════════════════════════════════════════════════════════
    #  警察缓存读取（Bug3/Bug13修复：正确使用 police.units）
    # ════════════════════════════════════════════════════════

    def _read_police_state(self, state) -> Dict:
        """读取警察系统状态，使用 ver1.9 的 PoliceData.units"""
        cache = {
            "has_police": False,
            "captain_id": None,
            "is_captain": False,
            "authority": 0,
            "report_phase": "idle",
            "report_target": None,
            "units": [],  # [{id, location, hp, weapon, is_active, is_alive}]
            "alive_count": 0,
            "active_count": 0,
        }

        if not hasattr(state, 'police') or not state.police:
            self._police_cache = cache
            return cache

        police = state.police
        cache["has_police"] = True

        # 队长
        cache["captain_id"] = getattr(police, 'captain_id', None)
        cache["is_captain"] = (cache["captain_id"] == self._my_id)

        # 威信（Bug3修复：用 police.authority 而非 captain_authority）
        cache["authority"] = getattr(police, 'authority', 0)

        # 举报
        cache["report_phase"] = getattr(police, 'report_phase', "idle")
        cache["report_target"] = getattr(police, 'reported_target_id', None)

        # 警察单位（Bug3/Bug13修复：直接读 police.units 扁平列表）
        units_info = []
        alive_count = 0
        active_count = 0
        for unit in getattr(police, 'units', []):
            alive = unit.is_alive() if hasattr(unit, 'is_alive') else False
            active = unit.is_active() if hasattr(unit, 'is_active') else False
            info = {
                "id": getattr(unit, 'unit_id', 'unknown'),
                "location": getattr(unit, 'location', None),
                "hp": getattr(unit, 'hp', 0),
                "weapon": getattr(unit, 'weapon_name', '警棍'),
                "outer_armor": getattr(unit, 'outer_armor_name', '盾牌'),
                "is_alive": alive,
                "is_active": active,
                "is_submerged": getattr(unit, 'is_submerged', False),
            }
            units_info.append(info)
            if alive:
                alive_count += 1
            if active:
                active_count += 1

        cache["units"] = units_info
        cache["alive_count"] = alive_count
        cache["active_count"] = active_count

        self._police_cache = cache
        debug_ai_detailed(self._pname(), f"警察缓存: alive={alive_count} active={active_count}")
        return cache

    # ════════════════════════════════════════════════════════
    #  阶段评估
    # ════════════════════════════════════════════════════════
    def _is_critical_firefly(self, player, state) -> bool:
        """火萤IV型的危险判定：更激进，不轻易进入危险模式"""
        # 被警察围攻仍然算危险
        pc = self._police_cache or {}
        if pc.get("report_target") == player.player_id:
            phase = pc.get("report_phase", "idle")
            if phase == "dispatched":
                return True
        # 被锚定仍然算危险
        if self._is_anchored(player, state):
            return True

        if self._firefly_debuff_active(player):
            # debuff 已生效：不再因为没有护甲而陷入危险
            # 只有 hp <= 0.5 时才算危险（但火萤 0.5 不眩晕，T0 自愈到 1）
            # 所以实际上几乎不会进入危险模式
            return False
        else:
            # debuff 未生效：
            # 条件1：无护甲 + 对方（engaged_with 的人）有伤害>1的武器
            # 条件2：无护甲 + 被>1人锁定
            outer = self._count_outer_armor(player)
            if outer > 0:
                return False  # 有护甲就不危险

            # 无护甲时检查条件1：engaged_with 的对手有伤害>1武器
            markers = getattr(state, 'markers', None)
            if markers:
                engaged = markers.get_related(player.player_id, "ENGAGED_WITH")
                for eid in engaged:
                    enemy = state.get_player(eid)
                    if enemy and enemy.is_alive():
                        enemy_best_dmg = self._best_weapon_damage(enemy)
                        if enemy_best_dmg > 1.0:
                            return True

            # 无护甲时检查条件2：被>1人锁定
            locked_count = self._count_locked_by(player, state)
            if locked_count > 1:
                return True

            return False

    def _is_critical(self, player, state) -> bool:
        # 火萤IV型：自定义危险判定
        if self._has_firefly_talent(player):
            return self._is_critical_firefly(player, state)

        if player.hp <= 0.5:
                return True
        if player.hp <= 1.0 and self._count_outer_armor(player) == 0:
            return True
        # 被警察围攻
        pc = self._police_cache or {}
        if pc.get("report_target") == player.player_id:
            phase = pc.get("report_phase", "idle")
            if phase == "dispatched":
                return True
        # 被锁定且完全没有护甲
        locked_count = self._count_locked_by(player, state)
        if locked_count >= 1:
            total_armor = self._count_outer_armor(player) + self._count_inner_armor(player)
            if total_armor <= 1:
                return True
        # 被锚定
        if self._is_anchored(player, state):
            return True
        return False

    def _is_anchored(self, player, state) -> bool:
        markers = getattr(state, 'markers', None)
        if not markers or not hasattr(markers, 'has_relation'):
            return False
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            if markers.has_relation(player.player_id, "ANCHORED_BY", pid):
                return True
        # 备用检查
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if target and target.talent and hasattr(target.talent, 'is_anchoring'):
                if target.talent.is_anchoring(player):
                    return True
        return False

    def _needs_virus_cure(self, player, state) -> bool:
        """Bug2修复：防御 state.virus 为 None"""
        virus = getattr(state, 'virus', None)
        if virus is None:
            return False
        if not getattr(virus, 'is_active', False):
            return False
        if self._has_virus_immunity(player):
            return False
        return True

    def _has_kill_opportunity(self, player, state) -> bool:
        """Bug15修复：击杀机会需考虑护甲"""
        best_dmg = self._best_weapon_damage(player)
        if best_dmg <= 0:
            return False

        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if not target or not target.is_alive():
                continue

            # Bug15修复：必须考虑护甲
            outer_count = self._count_outer_armor(target)
            inner_count = self._count_inner_armor(target)

            # 只有在无护甲时，才比较 hp vs damage
            if outer_count == 0 and inner_count == 0:
                if target.hp <= best_dmg:
                    if self._can_attack_target(player, target, state):
                        debug_ai_basic(player.name,
                            f"击杀机会: {target.name} HP={target.hp} 无护甲 dmg={best_dmg}")
                        return True
            # 有护甲时，需要更高伤害穿透
            elif outer_count == 0 and inner_count > 0:
                # 外层清了只剩内层，如果伤害足够打破最后内层+hp
                if target.hp <= 0.5 and best_dmg >= 1.0:
                    if self._can_attack_target(player, target, state):
                        return True

        return False

    def _update_combat_status(self, player, state):
        markers = getattr(state, 'markers', None)
        current_target = None
        if markers and hasattr(markers, 'has_relation'):
            for pid in state.player_order:
                if pid == player.player_id:
                    continue
                target = state.get_player(pid)
                if target and target.is_alive():
                    if markers.has_relation(player.player_id, "ENGAGED_WITH", pid):
                        current_target = target
                        break

        if current_target:
            self._in_combat = True
            self._combat_target = current_target
        else:
            if self._in_combat:
                debug_ai_basic(player.name, "退出战斗状态（目标丢失）")
            self._in_combat = False
            self._combat_target = None



    def _is_development_complete(self, player, state) -> bool:
        """判断发育是否完成"""
        real_weapons = [w for w in player.weapons if w and getattr(w, 'name', '') != "拳击"]
        has_real_weapon = len(real_weapons) > 0

        # 死者苏生：未学习或未挂载时，发育未完成
        if (player.talent
            and hasattr(player.talent, 'name')
            and player.talent.name == "死者苏生"):
            if hasattr(player.talent, 'learned') and not player.talent.learned:
                return False
            if hasattr(player.talent, 'mounted_on') and player.talent.mounted_on is None:
                return False

        # 火萤IV型：天赋感知的发育标准
        if self._has_firefly_talent(player):
            real_weapons = [w for w in player.weapons if w and getattr(w, 'name', '') != "拳击"]
            if self._firefly_debuff_active(player):
                # debuff 已生效：需要磨过的小刀 + 高斯步枪
                has_sharpened_knife = any(
                    w.name == "小刀" and getattr(w, 'base_damage', 0) >= 2
                    for w in real_weapons
                )
                has_gauss = any(w.name == "高斯步枪" for w in real_weapons)
                return has_sharpened_knife and has_gauss
            else:
                # debuff 未生效：2武器 + 1外甲
                has_two_weapons = len(real_weapons) >= 2
                has_outer = self._count_outer_armor(player) >= 1
                return has_two_weapons and has_outer

        if self.personality == "aggressive":
            has_armor = self._count_outer_armor(player) >= 2
            has_two_weapons = len(real_weapons) >= 2
            return has_two_weapons and has_armor

        elif self.personality == "defensive":
            has_armor = self._count_outer_armor(player) >= 2
            has_inner = self._count_inner_armor(player) >= 1
            return has_real_weapon and has_armor and has_inner

        elif self.personality == "assassin":
            has_armor = self._count_outer_armor(player) >= 2
            has_two_weapons = len(real_weapons) >= 2
            return has_two_weapons and self._has_stealth(player) and has_armor

        elif self.personality == "builder":
            has_armor = self._count_outer_armor(player) >= 2
            has_inner = self._count_inner_armor(player) >= 1
            has_pass = getattr(player, 'has_military_pass', False)
            return has_real_weapon and has_armor and has_inner and has_pass

        elif self.personality == "political":
            fallback = self._political_fallback_level
            if fallback in ("full_balanced", "develop_only"):
                # fallback 时使用 balanced 完成标准（2外甲+1内甲+1武器）
                has_outer = self._count_outer_armor(player) >= 2
                has_inner = self._count_inner_armor(player) >= 1
                return has_real_weapon and has_outer and has_inner
            else:
                is_captain = getattr(player, 'is_captain', False)
                if not is_captain:
                    return False
            has_armor = self._count_outer_armor(player) >= 1
            # 检查警察是否全部部署
            all_deployed = all(
                a.get("phase") in ("stationed", "stationed_default", None)
                for a in self._police_dev_assignments.values()
            ) if self._police_dev_assignments else False
            return has_real_weapon and has_armor and all_deployed

        else:  # balanced
            has_outer = self._count_outer_armor(player) >= 2
            has_inner = self._count_inner_armor(player) >= 1
            return has_real_weapon and has_outer and has_inner

    # ════════════════════════════════════════════════════════
    #  命令生成器：起床
    # ════════════════════════════════════════════════════════

    def _cmd_wake(self) -> List[str]:
        return ["wake"]

    # ════════════════════════════════════════════════════════
    #  命令生成器：发育
    # ════════════════════════════════════════════════════════
    def _cmd_develop_firefly(self, player, state, available: List[str]) -> List[str]:
        """火萤IV型专用发育路径"""
        commands = []
        loc = self._get_location_str(player)
        weapons = getattr(player, 'weapons', [])
        real_weapons = [w for w in weapons if w and getattr(w, 'name', '') != "拳击"]
        outer = self._count_outer_armor(player)
        vouchers = getattr(player, 'vouchers', 0)
        has_pass = getattr(player, 'has_military_pass', False)

        # 磨刀优先（与通用逻辑一致）
        if "special" in available:
            has_stone = any(getattr(i, 'name', '') == "磨刀石" for i in getattr(player, 'items', []))
            has_unsharpened = any(w.name == "小刀" and w.base_damage < 2 for w in player.weapons if w)
            if has_stone and has_unsharpened:
                commands.append("special 磨刀")
                return commands

        debuff_active = self._firefly_debuff_active(player)

        if debuff_active:
            # === debuff 已生效：不拿护甲，专注高级武器 ===
            has_sharpened_knife = any(
                w.name == "小刀" and getattr(w, 'base_damage', 0) >= 2
                for w in real_weapons
            )
            has_gauss = any(w.name == "高斯步枪" for w in real_weapons)

            if "interact" in available:
                # 磨过的小刀路线：home 拿小刀 → 商店拿磨刀石 → 磨刀
                if not has_sharpened_knife:
                    has_knife = any(w.name == "小刀" for w in real_weapons)
                    if not has_knife:
                        if loc == "home" or self._is_at_home(player):
                            commands.append("interact 小刀")
                        elif loc == "商店":
                            commands.append("interact 小刀")  # 商店也有小刀
                    else:
                        # 有小刀但没磨，去商店拿磨刀石
                        has_stone = any(getattr(i, 'name', '') == "磨刀石" for i in getattr(player, 'items', []))
                        if not has_stone and loc == "商店":
                            if vouchers >= 1:
                                commands.append("interact 磨刀石")
                            else:
                                commands.append("interact 打工")

                # 高斯步枪路线：军事基地
                if not has_gauss:
                    if loc == "军事基地":
                        if not has_pass:
                            commands.append("interact 通行证")
                        else:
                            commands.append("interact 高斯步枪")

            # 蓄力高斯步枪（与 interact 块同级，避免 interact 不可用时跳过蓄力）
            if has_gauss and "special" in available and not commands:
                gauss = next((w for w in weapons if w and w.name == "高斯步枪"), None)
                if gauss and not getattr(gauss, 'is_charged', False):
                    commands.append("special 蓄力高斯步枪")

            # 移动到需要的地点
            if "move" in available and not commands:
                if not has_sharpened_knife:
                    has_knife = any(w.name == "小刀" for w in real_weapons)
                    if not has_knife:
                        if loc != "home" and not self._is_at_home(player):
                            commands.append("move home")
                    else:
                        has_stone = any(getattr(i, 'name', '') == "磨刀石" for i in getattr(player, 'items', []))
                        if not has_stone:
                            if loc != "商店":
                                commands.append("move 商店")
                elif not has_gauss:
                    if loc != "军事基地":
                        commands.append("move 军事基地")
        else:
            # === debuff 未生效：2武器 + 1外甲，不拿隐身/探测 ===
            if "interact" in available:
                if loc == "home" or self._is_at_home(player):
                    if vouchers < 1:
                        commands.append("interact 凭证")
                    if not any(w.name == "小刀" for w in real_weapons):
                        commands.append("interact 小刀")
                    if outer < 1 and not self._has_armor_by_name(player, "盾牌"):
                        commands.append("interact 盾牌")
                elif loc == "商店":
                    if vouchers < 1:
                        commands.append("interact 打工")
                    if outer < 1 and not self._has_armor_by_name(player, "陶瓷护甲"):
                        commands.append("interact 陶瓷护甲")
                    # 磨刀石（如果有未磨小刀）
                    has_unsharpened = any(w.name == "小刀" and w.base_damage < 2 for w in player.weapons if w)
                    has_stone = any(getattr(i, 'name', '') == "磨刀石" for i in getattr(player, 'items', []))
                    if has_unsharpened and not has_stone and vouchers >= 1:
                        commands.append("interact 磨刀石")
                elif loc == "魔法所":
                    learned = self._get_learned_spells(player)
                    if "魔法弹幕" not in learned and len(real_weapons) < 2:
                        commands.append("interact 魔法弹幕")
                    if "魔法护盾" not in learned and outer < 1:
                        commands.append("interact 魔法护盾")
                    # 不拿探测魔法、隐身术
                    if "地震" not in learned:
                        commands.append("interact 地震")
                    if "地震" in learned and "地动山摇" not in learned:
                        commands.append("interact 地动山摇")
                elif loc == "军事基地":
                    if not has_pass:
                        commands.append("interact 通行证")
                    elif has_pass:
                        if len(real_weapons) < 2:
                            commands.append("interact 高斯步枪")
                            commands.append("interact 电磁步枪")
                        if outer < 1 and not self._has_armor_by_name(player, "AT力场"):
                            commands.append("interact AT力场")
                        # 不拿雷达、隐形涂层
                elif loc == "医院":
                    # 火萤不主动去医院拿内甲（debuff 未生效时也不需要）
                    if vouchers < 1:
                        commands.append("interact 打工")

            # 蓄力
            if "special" in available and not commands:
                gauss = next((w for w in weapons if w and w.name == "高斯步枪"), None)
                if gauss and not getattr(gauss, 'is_charged', False):
                    commands.append("special 蓄力高斯步枪")
                emr = next((w for w in weapons if w and w.name == "电磁步枪"), None)
                if emr and not getattr(emr, 'is_charged', False):
                    commands.append("special 蓄力电磁步枪")

            # 移动
            if "move" in available and not commands:
                next_loc = self._pick_ideal_destination(player, state)
                if next_loc and next_loc != loc:
                    if not (next_loc == "home" and self._is_at_home(player)):
                        commands.append(f"move {next_loc}")

        return commands

    def _cmd_develop(self, player, state, available: List[str]) -> List[str]:
        commands = []
        loc = self._get_location_str(player)
        weapons = getattr(player, 'weapons', [])
        has_weapon = any(w for w in weapons if w and getattr(w, 'name', '') != "拳击")
        outer = self._count_outer_armor(player)
        inner = self._count_inner_armor(player)
        vouchers = getattr(player, 'vouchers', 0)
        has_pass = getattr(player, 'has_military_pass', False)
        has_detection = getattr(player, 'has_detection', False)
        # ---- 磨刀优先：有磨刀石+未磨小刀 → 立即磨刀 ----
        if "special" in available:
            has_stone = any(getattr(i, 'name', '') == "磨刀石" for i in getattr(player, 'items', []))
            has_unsharpened = any(w.name == "小刀" and w.base_damage < 2 for w in player.weapons)
            if has_stone and has_unsharpened:
                commands.append("special 磨刀")
                return commands  # 磨刀最高优先级，不生成其他命令

        debug_ai_development_plan(player.name,
            f"状态: loc={loc} vouchers={vouchers} weapon={has_weapon} "
            f"outer={outer} inner={inner} pass={has_pass} detect={has_detection}")

        # 火萤IV型：专用发育路径
        if self._has_firefly_talent(player):
            return self._cmd_develop_firefly(player, state, available)

        # Political 特殊处理：基本需求满足后，跳过通用发育，直奔警察局
        if (self.personality == "political"
            and self._political_fallback_level == "none"
            and not getattr(player, 'is_captain', False)
            and outer >= 1):
            debug_ai_development_plan(player.name, "political 基本需求已满足，直奔警察局路线")
            if loc == "警察局":
                if not getattr(player, 'is_police', False) and "recruit" in available:
                    commands.append("recruit")
                elif getattr(player, 'is_police', False) and "election" in available:
                    commands.append("election")
            elif "move" in available:
                commands.append("move 警察局")
            # 如果在警察局但 recruit/election 都不可用，不返回空列表，
            # fall through 到通用发育逻辑
            if commands:
                return commands

        if "interact" in available:
            # ---- 阶段1：在home拿凭证/盾牌 ----
            if loc == "home" or self._is_at_home(player):
                if outer == 0 and not self._has_armor_by_name(player, "盾牌"):
                    commands.append("interact 盾牌")
                if not has_weapon:
                    commands.append("interact 小刀")
                if vouchers < 1:
                    commands.append("interact 凭证")

            # ---- Bug5修复：用 elif 确保不重复 ----
            elif loc == "商店":
                if not has_weapon:
                    commands.append("interact 小刀")
                if vouchers >= 1 and not has_detection:
                    commands.append("interact 热成像仪")
                if vouchers >= 1 and outer < 2 and not self._has_armor_by_name(player, "陶瓷护甲"):
                    commands.append("interact 陶瓷护甲")
                if self.personality == "assassin" and vouchers >= 1:
                    commands.append("interact 隐身衣")
                if has_weapon and self._has_melee_only(player):
                    has_stone = any(getattr(i, 'name', '') == "磨刀石" for i in getattr(player, 'items', []))
                    has_unsharpened = any(w.name == "小刀" and w.base_damage < 2 for w in player.weapons)
                    # 只在没有磨刀石且有未磨的小刀时才买
                    if not has_stone and has_unsharpened:
                        commands.append("interact 磨刀石")
                if vouchers < 1:
                    commands.append("interact 打工")

            elif loc == "魔法所":
                if "interact" in available:
                    # 学法术（通过交互）
                    learned = self._get_learned_spells(player)
                    if "魔法弹幕" not in learned and not has_weapon:
                        commands.append("interact 魔法弹幕")
                    if "魔法弹幕" in learned and "远程魔法弹幕" not in learned:
                        commands.append("interact 远程魔法弹幕")
                    if "魔法护盾" not in learned and outer< 2:
                        commands.append("interact 魔法护盾")
                    if "探测魔法" not in learned and not has_detection:
                        commands.append("interact 探测魔法")
                    if "隐身术" not in learned and self.personality == "assassin":
                        commands.append("interact 隐身术")
                    if "地震" not in learned:
                        commands.append("interact 地震")
                    if "地震" in learned and "地动山摇" not in learned:
                        commands.append("interact 地动山摇")
                    if "封闭" not in learned:
                        commands.append("interact 封闭")
                    # 死者苏生：在魔法所学习（通过T0系统，不需要interact命令）
                    # 如果有死者苏生天赋且未学习，留在魔法所等待T0触发
                    if (player.talent
                        and hasattr(player.talent, 'learned')
                        and not player.talent.learned
                        and hasattr(player.talent, 'name')
                        and player.talent.name == "死者苏生"):
                        # 返回一个forfeit让AI留在魔法所，T0会在下一轮触发学习
                        if not commands:
                            commands.append("forfeit")


            elif loc == "医院":
                if inner == 0:
                    if self.personality == "builder":
                        commands.append("interact 晶化皮肤手术")
                        commands.append("interact 额外心脏手术")
                    else:
                        commands.append("interact 晶化皮肤手术")
                elif inner < 2 and self.personality in ("builder", "defensive"):
                    commands.append("interact 额外心脏手术")
                if not self._has_virus_immunity(player) and vouchers >= 1:
                    commands.append("interact 防毒面具")
                if vouchers < 1:
                    commands.append("interact 打工")
                # assassin 在医院顺手放毒
                if self._should_release_virus(player, state) and "special" in available:
                    commands.insert(0, "special 释放病毒")  # 插到最前面，优先放毒

            elif loc == "军事基地":
                if not has_pass:
                    commands.append("interact 通行证")
                elif has_pass:
                    if not has_weapon or self.personality in ("aggressive", "balanced"):
                        commands.append("interact 电磁步枪")
                        commands.append("interact 高斯步枪")
                    if outer < 2 and not self._has_armor_by_name(player, "AT力场"):
                        commands.append("interact AT力场")
                    if not has_detection:
                        commands.append("interact 雷达")
                    if self.personality == "assassin":
                        commands.append("interact 隐形涂层")
                    # 导弹
                    if self._missile_cooldown <= 0 and self.personality in ("aggressive", "balanced"):
                        commands.append("interact 导弹控制权")

            elif loc == "警察局":
                if self.personality == "political":
                    # 集结优先于一切
                    police = getattr(state, 'police', None)
                    if police and police.report_phase == "reported" and police.reporter_id == player.player_id:
                        commands.append("assemble")
                        return commands
                    if not getattr(player, 'is_police', False):
                        if "recruit" in available:
                            commands.append("recruit")
                    elif getattr(player, 'is_police', False) and not getattr(player, 'is_captain', False):
                        if "election" in available:
                            commands.append("election")

        # ---- 蓄力：interact 之后、move 之前 ----
        if "special" in available and not commands:
            emr = next((w for w in weapons if w and getattr(w, 'name', '') == "电磁步枪"), None)
            if emr and not getattr(emr, 'is_charged', False):
                commands.append("special 蓄力电磁步枪")
            if not commands:
                gauss = next((w for w in weapons if w and getattr(w, 'name', '') == "高斯步枪"), None)
                if gauss and not getattr(gauss, 'is_charged', False):
                    commands.append("special 蓄力高斯步枪")


        # ---- 移动到目标地点 ----
        if "move" in available and not commands:
            next_loc = self._pick_ideal_destination(player, state)
            if next_loc and next_loc != loc:
                if not (next_loc == "home" and self._is_at_home(player)):
                    commands.append(f"move {next_loc}")

        return commands


    def _pick_ideal_destination(self, player, state) -> Optional[str]:
        """动态需求驱动的目标地点选择"""
        # 1. 收集当前未满足的需求
        unmet_needs = self._get_unmet_needs(player, state)
        if not unmet_needs:
            # 发育完成
            if self.personality in ("aggressive", "assassin", "balanced") or getattr(self, '_political_in_balanced_fallback', False):
                return self._find_nearest_enemy_location(player, state)
            return None

        # ---- 死者苏生：未学习时优先去魔法所 ----
        if (player.talent
            and hasattr(player.talent, 'learned')
            and not player.talent.learned
            and hasattr(player.talent, 'name')
            and player.talent.name == "死者苏生"):
            if self._get_location_str(player) != "魔法所":
                return "魔法所"
            # 已在魔法所 → 不需要移动，T0会自动触发学习
            return None

        # 2. political 特殊路径（警察局逻辑保留）
        if self.personality == "political":
            result = self._political_destination(player, state, unmet_needs)
            if result is not None:
                return result

        # 3. 对每个候选地点评分
        loc = self._get_location_str(player)
        vouchers = getattr(player, 'vouchers', 0)
        has_pass = getattr(player, 'has_military_pass', False)

        # 候选地点（排除当前位置和警察局）
        candidate_locs = ["home", "商店", "魔法所", "医院", "军事基地"]

        best_loc = None
        best_score = -999

        for dest in candidate_locs:
            if dest == loc:
                continue
            if dest == "home" and self._is_at_home(player):
                continue
            score = self._score_destination(dest, unmet_needs, player, state, vouchers, has_pass)
            if score > best_score:
                best_score = score
                best_loc = dest

        # 发育受阻判断：所有有用地点都被敌人压制
        if best_score <= 0 and unmet_needs:
            return None

        return best_loc

    def _get_unmet_needs(self, player, state) -> list:
        """返回当前未满足的需求列表（按人格优先级排序）"""
        # political fallback 时使用 balanced 的需求列表（6项而非3项）
        effective_personality = self.personality
        if getattr(self, '_political_in_balanced_fallback', False):
            effective_personality = "balanced"
        needs_order = PERSONALITY_NEEDS.get(effective_personality, PERSONALITY_NEEDS["balanced"])

        weapons = [w for w in player.weapons if w and getattr(w, 'name', '') != "拳击"]
        has_weapon = len(weapons) > 0
        weapon_attrs = set(self._get_weapon_attr(w) for w in weapons)
        outer = self._count_outer_armor(player)
        inner = self._count_inner_armor(player)
        vouchers = getattr(player, 'vouchers', 0)
        has_detection = getattr(player, 'has_detection', False)
        has_stealth = self._has_stealth(player)

        unmet = []
        for need in needs_order:
            if need == "voucher" and vouchers < 1:
                unmet.append(("voucher", 3))  # (need_key, priority_weight)
            elif need == "weapon" and not has_weapon:
                unmet.append(("weapon", 5))
            elif need == "outer_armor" and outer < 1:
                unmet.append(("outer_armor", 4))
            elif need == "second_outer_armor" and outer < 2:
                unmet.append(("second_outer_armor", 3))
            elif need == "inner_armor" and inner < 1:
                unmet.append(("inner_armor", 2))
            elif need == "detection" and not has_detection:
                unmet.append(("detection", 2))
            elif need == "stealth" and not has_stealth:
                unmet.append(("stealth", 3))
            elif need == "second_weapon" and len(weapons) < 2:
                # 需要至少2件真实武器（与 _is_development_complete 一致）
                unmet.append(("second_weapon", 3))
        if self.personality == "builder":
            has_pass = getattr(player, 'has_military_pass', False)
            if not has_pass:
                unmet.append(("military_pass", 4))  # 需要通行证

        return unmet

    def _score_destination(self, dest, unmet_needs, player, state, vouchers, has_pass) -> float:
        """对一个候选地点评分"""
        score = 0.0

        # 1. 能满足多少需求（按优先级加权）
        for need_key, priority in unmet_needs:
            providers = NEED_PROVIDERS.get(need_key, [])
            for (ploc, item_name, prereq) in providers:
                if ploc != dest:
                    continue
                # 检查前置条件
                if prereq == "voucher" and vouchers < 1:
                    score += priority * 0.3  # 没凭证但可以打工，打折
                    continue
                if prereq == "pass" and not has_pass:
                    if vouchers >= 1:
                        score += priority * 0.5  # 可以强买通行证，打折
                    else:
                        score += priority * 0.1  # 需要先拿凭证再强买，大打折
                    continue
                if prereq == "voucher_consume" and vouchers < 1:
                    score += priority * 0.2  # 需要先拿凭证
                    continue
                # 检查是否已有该物品（避免重复获取）
                if self._already_has_item(player, item_name):
                    continue
                score += priority  # 完全满足
                break  # 每个需求只计一次

        enemies = self._count_enemies_at(dest, player, state)
        if self.personality in ("aggressive", "assassin"):
            score -= enemies * 0.5
        else:
            if enemies == 1:
                score -= 0.5
            elif enemies == 2:
                score -= 2.5
            elif enemies >= 3:
                score -= enemies * 2 + 3

        # 3. 效率加分：一个地方能同时满足多个需求
        satisfiable_count = 0
        for need_key, _ in unmet_needs:
            providers = NEED_PROVIDERS.get(need_key, [])
            for (ploc, item_name, _) in providers:
                if ploc == dest and not self._already_has_item(player, item_name):
                    satisfiable_count += 1
                    break
        if satisfiable_count >= 2:
            score += 3  # 一站式加分
        if satisfiable_count >= 3:
            score += 3  # 更多加分

        return score

    def _already_has_item(self, player, item_name) -> bool:
        """检查玩家是否已拥有某物品/装备/法术"""
        # 武器（非法术类）
        if item_name in ("小刀", "高斯步枪", "电磁步枪"):
            return any(w.name == item_name for w in player.weapons if w)
        # 法术（魔法所的东西都是法术，包括魔法弹幕）
        learned = self._get_learned_spells(player)
        if item_name in ("魔法护盾", "魔法弹幕", "远程魔法弹幕", "封闭", "地震", "地动山摇", "隐身术", "探测魔法"):
            # 魔法弹幕既是法术也会变成武器，两者都检查
            if item_name == "魔法弹幕":
                return (item_name in learned
                        or any(w.name == item_name for w in player.weapons if w))
            return item_name in learned
        # 护甲
        if item_name in ("盾牌", "陶瓷护甲", "AT力场"):
            return self._has_armor_by_name(player, item_name)
        # 手术（内甲）：按具体手术名检查对应的护甲片
        surgery_armor_map = {
            "晶化皮肤手术": "晶化皮肤",
            "额外心脏手术": "额外心脏",
            "不老泉手术": "不老泉",
        }
        if item_name in surgery_armor_map:
            return self._has_armor_by_name(player, surgery_armor_map[item_name])
        # 物品
        if item_name in ("热成像仪", "隐身衣", "隐形涂层", "雷达"):
            if item_name == "热成像仪" or item_name == "雷达":
                return getattr(player, 'has_detection', False)
            if item_name in ("隐身衣", "隐形涂层", "隐身术"):
                return self._has_stealth(player)
        if item_name == "通行证":
            return getattr(player, 'has_military_pass', False)
        if item_name == "凭证":
            return getattr(player, 'vouchers', 0) >= 1
        if item_name == "打工":
            return getattr(player, 'vouchers', 0) >= 1  # 有凭证时游戏引擎禁止打工
        return False

    def _political_destination(self, player, state, unmet_needs) -> Optional[str]:
        """political 人格的特殊目的地逻辑（警察局相关）"""
        fallback = self._political_fallback_level
        if fallback in ("full_balanced", "develop_only"):
            return None  # 返回 None 让通用评分逻辑处理

        is_police = getattr(player, 'is_police', False)
        is_captain = getattr(player, 'is_captain', False)
        loc = self._get_location_str(player)

        # 还没加入警察 → 先满足基本需求再去警察局
        if not is_police:
            # 如果还有武器或外甲需求，先满足
            has_basic = any(w for w in player.weapons if w and w.name != "拳击") and self._count_outer_armor(player) > 0
            if has_basic:
                if loc != "警察局":
                    return "警察局"
            else:
                return None  # 让通用逻辑处理基本需求

        # 已加入但还没当队长 → 去警察局竞选
        if is_police and not is_captain:
            if loc != "警察局":
                return "警察局"
            return None  # 已在警察局

        # 已是队长 → 检查警察部署，然后让通用逻辑处理自身发育
        if is_captain:
            all_deployed = all(
                a.get("phase") in ("stationed", "stationed_default", None)
                for a in self._police_dev_assignments.values()
            ) if self._police_dev_assignments else False
            if not all_deployed:
                if loc != "警察局":
                    return "警察局"
                return None
            # 警察部署完毕，让通用逻辑处理
            return None

        return None



    def _count_enemies_at(self, location: str, player, state) -> int:
        count = 0
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if target and target.is_alive():
                target_loc = self._get_location_str(target)
                if target_loc == location:
                    count += 1
                elif location == "home" and target_loc == f"home_{player.player_id}":
                    count += 1
        return count


    # ════════════════════════════════════════════════════════
    #  命令生成器：攻击（Bug6修复：检查 ENGAGED_WITH/LOCKED_BY）
    # ════════════════════════════════════════════════════════

    def _cmd_attack(self, player, state, available: List[str],
                    forced_target=None) -> List[str]:
        commands = []
        target = forced_target or self._pick_target(player, state)
        if not target:
            return commands

        weapon = self._pick_weapon(player, target)
        if not weapon:
            return commands

        # 检查武器是否被目标护甲克制
        if self._all_weapons_countered(player, target):
            # 所有武器都被克制，不生成攻击命令
            debug_ai_attack_generation(player.name,
                weapon.name, f"所有武器被目标 {target.name} 护甲克制，跳过攻击")
            return commands

        cmds = self._build_attack_cmd(player, target, weapon, state, available)
        commands.extend(cmds)

        debug_ai_attack_generation(player.name,
            weapon.name, f"攻击命令: {commands} (目标={target.name})")
        return commands

    def _build_attack_cmd(self, player, target, weapon, state,
                          available: List[str]) -> List[str]:
        """
        Bug6修复核心：根据武器类型检查前置条件
        - 近战：需要 ENGAGED_WITH → 没有则先find
        - 远程：需要 LOCKED_BY → 没有则先 lock
        - 区域：检查同地点有目标
        """
        commands = []
        markers = getattr(state, 'markers', None)
        weapon_range = self._get_weapon_range(weapon)
        # 救世主状态：远程武器不应该到这里（_pick_weapon 已过滤），但防御性处理
        if self._is_in_savior_state(player) and weapon_range == "ranged":
            weapon_range = "melee"  # 降级为近战路径

        if weapon_range == "melee":
            # 近战：需要先 find（建立ENGAGED_WITH）
            is_engaged = False
            if markers and hasattr(markers, 'has_relation'):
                is_engaged = markers.has_relation(
                    player.player_id, "ENGAGED_WITH", target.player_id)

            if not is_engaged:
                # 检查目标是否对自己可见
                markers_obj = getattr(state, 'markers', None)
                target_visible = True
                if markers_obj and hasattr(markers_obj, 'is_visible_to'):
                    target_visible = markers_obj.is_visible_to(
                        target.player_id, player.player_id,
                        getattr(player, 'has_detection', False))

                if not target_visible:
                    # 目标隐身且自己没有探测 → 不生成 find，改为获取探测手段
                    detection_cmds = self._cmd_get_detection(player, state, available)
                    commands.extend(detection_cmds)
                    return commands

                if "find" in available:
                    # 先确认在同一地点
                    if self._same_location(player, target):
                        commands.append(f"find {target.name}")
                        return commands
                    else:
                        # 需要先移动
                        target_loc = self._get_location_str(target)
                        if target_loc and "move" in available:
                            commands.append(f"move {target_loc}")
                        commands.append(f"find {target.name}")
                        return commands
                else:
                    return commands# find 不可用

            # 已ENGAGED_WITH，可以攻击
            if "attack" in available:
                layer, attr = self._pick_attack_layer(player, target, weapon)
                if layer and attr:
                    commands.append(f"attack {target.name} {weapon.name} {layer} {attr}")
                else:
                    commands.append(f"attack {target.name} {weapon.name}")

        elif weapon_range == "ranged":
            # 远程：需要先 lock（建立 LOCKED_BY）
            is_locked = False
            if markers and hasattr(markers, 'has_relation'):
                is_locked = markers.has_relation(
                    target.player_id, "LOCKED_BY", player.player_id)

            if not is_locked:
                # 检查目标是否对自己可见
                markers_obj = getattr(state, 'markers', None)
                target_visible = True
                if markers_obj and hasattr(markers_obj, 'is_visible_to'):
                    target_visible = markers_obj.is_visible_to(
                        target.player_id, player.player_id,
                        getattr(player, 'has_detection', False))

                if not target_visible:
                    # 目标隐身且自己没有探测 → 不生成 lock，改为获取探测手段
                    detection_cmds = self._cmd_get_detection(player, state, available)
                    commands.extend(detection_cmds)
                    return commands


                if "lock" in available:
                    commands.append(f"lock {target.name}")
                    return commands
                else:
                    return commands  # lock 不可用

            # 已 LOCKED_BY，可以攻击
            if "attack" in available:
                layer, attr = self._pick_attack_layer(player, target, weapon)
                if layer and attr:
                    commands.append(f"attack {target.name} {weapon.name} {layer} {attr}")
                else:
                    commands.append(f"attack {target.name} {weapon.name}")

        elif weapon_range == "area":
            if "attack" in available:
                same_loc_targets = self._get_same_location_targets(player, state)
                if same_loc_targets:
                    layer, attr = self._pick_attack_layer(player, target, weapon)
                    if layer and attr:
                        commands.append(f"attack {target.name} {weapon.name} {layer} {attr}")
                    else:
                        commands.append(f"attack {target.name} {weapon.name}")
                else:
                    # area 武器 move 兜底：先移动到目标位置
                    target_loc = self._get_location_str(target)
                    if target_loc and "move" in available:
                        commands.append(f"move {target_loc}")
            return commands

        else:
            # 未知类型，按近战处理
            if "attack" in available:
                layer, attr = self._pick_attack_layer(player, target, weapon)
                if layer and attr:
                    commands.append(f"attack {target.name} {weapon.name} {layer} {attr}")
                else:
                    commands.append(f"attack {target.name} {weapon.name}")
            return commands

        return commands

    def _cmd_get_detection(self, player, state, available: List[str]) -> List[str]:
        """生成获取探测手段的命令（当目标隐身且自己没有探测时）"""
        commands = []
        loc = self._get_location_str(player)
        has_detection = getattr(player, 'has_detection', False)
        if has_detection:
            return commands  # 已有探测，不需要

        vouchers = getattr(player, 'vouchers', 0)
        has_pass = getattr(player, 'has_military_pass', False)

        # 当前位置能拿探测手段就直接拿
        if "interact" in available:
            if loc == "商店" and vouchers >= 1:
                commands.append("interact 热成像仪")
                return commands
            if loc == "魔法所":
                learned = self._get_learned_spells(player)
                if "探测魔法" not in learned:
                    commands.append("interact 探测魔法")
                    return commands
            if loc == "军事基地" and has_pass:
                commands.append("interact 雷达")
                return commands

        # 不在能拿探测的地方 → 移动过去
        if "move" in available:
            # 优先去魔法所（免费），其次商店（需凭证），最后军事基地（需通行证）
            if loc != "魔法所":
                commands.append("move 魔法所")
            elif vouchers >= 1 and loc != "商店":
                commands.append("move 商店")
            elif has_pass and loc != "军事基地":
                commands.append("move 军事基地")
            else:
                # 没凭证也没通行证 → 去魔法所学探测魔法（免费）
                if loc != "魔法所":
                    commands.append("move 魔法所")

        return commands

    # ════════════════════════════════════════════════════════
    #  命令生成器：队长指挥（Bug8修复）
    # ════════════════════════════════════════════════════════

    def _cmd_captain(self, player, state, available: List[str]) -> List[str]:
        commands = []

        if "police_command" not in available:
            return commands

        pc = self._police_cache or {}
        if not pc.get("is_captain"):
            return commands

        units = pc.get("units", [])
        alive_units = [u for u in units if u.get("is_alive")]
        active_units = [u for u in units if u.get("is_alive") and u.get("is_active", True)]
        disabled_units = [u for u in units if u.get("is_alive") and not u.get("is_active", True)]  # 新增

        if not alive_units:
            return commands

        # study：威信 <= 1 时优先研究性学习
        authority = pc.get("authority", 0)
        if authority <= 1 and "study" in available:
            loc = self._get_location_str(player)
            if loc == "警察局":
                return ["study"]

        # ===== 初始化发育计划 =====
        criminal_target = self._find_criminal_target(player, state)

        # 新增：如果犯罪目标变了，重新初始化发育计划
        if criminal_target:
            new_target_id = criminal_target.player_id
            if hasattr(self, '_last_criminal_target_id') and self._last_criminal_target_id != new_target_id:
                self._police_dev_initialized = False
            self._last_criminal_target_id = new_target_id

        if not self._police_dev_initialized:
            self._init_police_dev_plan(alive_units, player, state)
            self._police_dev_initialized = True

        # ===== political 队长优先唤醒：debuff 后大概率被杀，必须抢救 =====
        if self.personality == "political" and disabled_units:
            wake_cmd = self._police_wake_step(disabled_units, state)
            if wake_cmd:
                return [wake_cmd]

        # ===== 检查犯罪目标 =====
        criminal_target = self._find_criminal_target(player, state)


        # ===== 阶段3：有犯罪目标且至少一个警察已完成换装 → 攻击 =====
        if criminal_target:
            attack_cmd = self._police_attack_criminal(criminal_target, active_units, state)
            if attack_cmd:
                return [attack_cmd]

        # ===== 新增：唤醒处于debuff的警察 =====
        # 优先级：在攻击之后、发育之前
        # 如果有犯罪目标但没有active单位可攻击，唤醒最重要
        # 如果没有犯罪目标，唤醒也比发育/部署重要（恢复战力）
        if disabled_units:
            wake_cmd = self._police_wake_step(disabled_units, state)
            if wake_cmd:
                return [wake_cmd]

        # ===== 阶段1：发育（换装） =====
        dev_cmd = self._police_develop_step(active_units)
        if dev_cmd:
            return [dev_cmd]

        # ===== 阶段2：部署到驻扎位置 =====
        deploy_cmd = self._police_deploy_step(active_units)  # 注意：下面也要修复
        if deploy_cmd:
            return [deploy_cmd]

        return commands

    def _init_police_dev_plan(self, alive_units, player, state):
        """初始化警察发育计划：根据犯罪目标护甲属性分配3个单位的目标配置"""
        sorted_units = sorted(alive_units, key=lambda u: u["id"])

        # ===== 第一步：检查犯罪目标的护甲属性 =====
        criminal_target = self._find_criminal_target(player, state)
        target_armor_attrs = set()
        if criminal_target:
            # 先看外层（攻击时先打外层）
            outer = self._get_outer_armor_attr(criminal_target)
            if outer:
                target_armor_attrs = set(outer)
            else:
                # 无外层看内层
                inner = self._get_inner_armor_attr(criminal_target)
                if inner:
                    target_armor_attrs = set(inner)

        # ===== 第二步：根据目标护甲决定优先武器 =====
        # 默认路线（无目标或目标无甲）：按敌人分布选
        # 有目标有甲：优先能克制目标护甲的武器
        from utils.attribute import Attribute

        if target_armor_attrs:
            needs_magic = False  # 需要魔法弹幕
            needs_tech = False   # 需要高斯步枪

            for attr in target_armor_attrs:
                if attr == Attribute.TECH:
                    needs_magic = True   # 科技甲 → 魔法武器克制
                elif attr == Attribute.ORDINARY:
                    needs_tech = True    # 普通甲 → 科技武器克制
                elif attr == Attribute.MAGIC:
                    # 魔法甲 → 普通武器克制（但警棍伤害太低）
                    # 魔法弹幕（魔法）对魔法甲有效（MAGIC ∈ EFFECTIVE_AGAINST[MAGIC]）
                    # 高斯步枪（科技）对魔法甲无效（MAGIC ∉ EFFECTIVE_AGAINST[TECH]）
                    # 所以魔法甲目标：优先魔法弹幕
                    needs_magic = True

            if needs_magic and not needs_tech:
                # 优先魔法弹幕路线
                first_dest = "魔法所"
                first_weapon = "魔法弹幕"
                first_armor = "魔法护盾"
                first_station = "军事基地"
                second_dest = "军事基地"
                second_weapon = "高斯步枪"
                second_armor = "AT力场"
                second_station = "商店"
            elif needs_tech and not needs_magic:
                # 优先高斯步枪路线
                first_dest = "军事基地"
                first_weapon = "高斯步枪"
                first_armor = "AT力场"
                first_station = "商店"
                second_dest = "魔法所"
                second_weapon = "魔法弹幕"
                second_armor = "魔法护盾"
                second_station = "军事基地"
            else:
                # 两种都需要（目标有多层不同属性甲）或都不特别需要
                # 回退到原有逻辑：按敌人分布选
                enemies_magic = self._count_enemies_at("魔法所", player, state)
                enemies_military = self._count_enemies_at("军事基地", player, state)
                if enemies_magic <= enemies_military:
                    first_dest, first_weapon, first_armor, first_station = "魔法所", "魔法弹幕", "魔法护盾", "军事基地"
                    second_dest, second_weapon, second_armor, second_station = "军事基地", "高斯步枪", "AT力场", "商店"
                else:
                    first_dest, first_weapon, first_armor, first_station = "军事基地", "高斯步枪", "AT力场", "商店"
                    second_dest, second_weapon, second_armor, second_station = "魔法所", "魔法弹幕", "魔法护盾", "军事基地"
        else:
            # 无目标或目标无甲 → 原有逻辑
            enemies_magic = self._count_enemies_at("魔法所", player, state)
            enemies_military = self._count_enemies_at("军事基地", player, state)
            if enemies_magic <= enemies_military:
                first_dest, first_weapon, first_armor, first_station = "魔法所", "魔法弹幕", "魔法护盾", "军事基地"
                second_dest, second_weapon, second_armor, second_station = "军事基地", "高斯步枪", "AT力场", "商店"
            else:
                first_dest, first_weapon, first_armor, first_station = "军事基地", "高斯步枪", "AT力场", "商店"
                second_dest, second_weapon, second_armor, second_station = "魔法所", "魔法弹幕", "魔法护盾", "军事基地"

        # ===== 第三步：分配（和原来一样） =====
        assignments = {}
        if len(sorted_units) >= 1:
            assignments[sorted_units[0]["id"]] = {
                "dest": first_dest,
                "target_weapon": first_weapon,
                "target_armor": first_armor,
                "station": first_station,
                "phase": "pending",
            }
        if len(sorted_units) >= 2:
            assignments[sorted_units[1]["id"]] = {
                "dest": second_dest,
                "target_weapon": second_weapon,
                "target_armor": second_armor,
                "station": second_station,
                "phase": "pending",
            }
        if len(sorted_units) >= 3:
            assignments[sorted_units[2]["id"]] = {
                "dest": None,
                "target_weapon": None,
                "target_armor": None,
                "station": "魔法所",
                "phase": "stationed_default",
            }

        self._police_dev_assignments = assignments

    def _police_develop_step(self, active_units) -> Optional[str]:
        """执行一步警察发育：移动→换武器→换护甲，每回合1条命令"""
        for unit in active_units:
            uid = unit["id"]
            assignment = self._police_dev_assignments.get(uid)
            if not assignment:
                continue

            phase = assignment.get("phase", "pending")
            unit_loc = unit.get("location")
            dest = assignment.get("dest")

            if phase == "pending":
                # 需要移动到目标地点
                if dest and unit_loc != dest:
                    assignment["phase"] = "moving"
                    return f"police move {uid} {dest}"
                elif dest and unit_loc == dest:
                    # 已经在目标地点
                    assignment["phase"] = "equip_weapon"
                    phase = "equip_weapon"# fall through

            if phase == "moving":
                if unit_loc == dest:
                    assignment["phase"] = "equip_weapon"
                    phase = "equip_weapon"
                else:
                    return f"police move {uid} {dest}"

            if phase == "equip_weapon":
                target_weapon = assignment.get("target_weapon")
                current_weapon = unit.get("weapon", "警棍")
                if target_weapon and current_weapon != target_weapon:
                    assignment["phase"] = "equip_armor"
                    return f"police equip {uid} {target_weapon}"
                else:
                    assignment["phase"] = "equip_armor"
                    phase = "equip_armor"

            if phase == "equip_armor":
                target_armor = assignment.get("target_armor")
                current_armor = unit.get("outer_armor", "盾牌")
                if target_armor and current_armor != target_armor:
                    assignment["phase"] = "ready_to_deploy"
                    return f"police equip {uid} {target_armor}"
                else:
                    assignment["phase"] = "ready_to_deploy"

            # stationed_default 和 ready_to_deploy 不需要发育步骤

        return None  # 所有单位发育完成


    def _police_deploy_step(self, alive_units) -> Optional[str]:
        """部署警察到驻扎位置"""
        for unit in alive_units:
            uid = unit["id"]
            assignment = self._police_dev_assignments.get(uid)
            if not assignment:
                continue

            phase = assignment.get("phase", "pending")
            station = assignment.get("station")
            unit_loc = unit.get("location")

            if phase in ("ready_to_deploy", "stationed_default"):
                if station and unit_loc != station:
                    assignment["phase"] = "deploying"
                    return f"police move {uid} {station}"
                else:
                    assignment["phase"] = "stationed"

            if phase == "deploying":
                if unit_loc == station:
                    assignment["phase"] = "stationed"
                else:
                    return f"police move {uid} {station}"

        return None  # 所有单位已部署


    def _police_wake_step(self, disabled_units, state) -> Optional[str]:
        """唤醒处于debuff的警察单位（队长远程唤醒）"""
        pe = getattr(state, 'police_engine', None)

        for unit in disabled_units:
            uid = unit["id"]

            # 沉沦+全息影像 → 无法唤醒，跳过
            if unit.get("is_submerged", False):
                unit_loc = unit.get("location")
                if pe and unit_loc and pe._is_in_hologram_range(unit_loc):
                    continue

            # 生成唤醒命令
            return f"police wake {uid}"

        return None  # 没有可唤醒的单位


    def _find_criminal_target(self, player, state):
        """找到最高威胁的犯罪目标"""
        # 先检查举报目标
        pc = self._police_cache or {}
        report_target = pc.get("report_target")
        if report_target and pc.get("report_phase") == "dispatched":
            target_player = state.get_player(report_target)
            if target_player and target_player.is_alive():
                return target_player

        # 再找其他犯罪者
        best = None
        best_score = -1
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            p = state.get_player(pid)
            if p and p.is_alive():
                is_criminal = getattr(p, 'is_criminal', False)
                if not is_criminal:
                    police = getattr(state, 'police', None)
                    if police and hasattr(police, 'is_criminal'):
                        is_criminal = police.is_criminal(pid)
                if is_criminal:
                    score = self._threat_scores.get(p.name, 0)
                    if score > best_score:
                        best_score = score
                        best = p
        return best


    def _police_attack_criminal(self, target, active_units, state) -> Optional[str]:
        """
        方案B：选择武器属性能有效打击目标护甲的警察单位
        - 有外层护甲 → 检查外层属性
        - 无外层有内层 → 检查内层属性
        - 无护甲 → 任意警察
        """
        from utils.attribute import Attribute, is_effective

        target_player = target
        target_loc = self._get_location_str(target_player)

        # 获取目标护甲属性
        target_armor_attrs = self._get_outer_armor_attr(target_player)
        if not target_armor_attrs:
            target_armor_attrs = self._get_inner_armor_attr(target_player)

        # 为每个活跃警察评分
        best_unit = None
        best_score = -1
        for unit in active_units:
            uid = unit["id"]
            weapon_name = unit.get("weapon", "警棍")
            weapon = make_weapon(weapon_name) if weapon_name else None
            if not weapon:
                weapon = make_weapon("警棍")

            w_attr = weapon.attribute if weapon else Attribute.ORDINARY
            unit_loc = unit.get("location")

            score = 0
            can_be_effective = False
            if target_armor_attrs:
                effective_set = EFFECTIVE_AGAINST.get(w_attr, set())
                for armor_attr in target_armor_attrs:
                    if armor_attr in effective_set:
                        score += 100  # 能有效打击是最重要的条件
                        can_be_effective = True
                        break
                if not can_be_effective:
                    score -= 200  # 打不动的大幅降分，确保不会被位置加分覆盖
            else:
                score += 50  # 无甲，任何武器都行

            # 已在目标位置的加分（省一步 move）
            if unit_loc == target_loc:
                score += 20

            if score > best_score:
                best_score = score
                best_unit = unit

        if not best_unit:
            return None

        uid = best_unit["id"]
        unit_loc = best_unit.get("location")

        # 如果最佳警察不在目标位置，移动过去（交换机制会自动处理）
        if unit_loc != target_loc:
            return f"police move {uid} {target_loc}"
        else:
            # 检查是否会无效攻击（避免重复浪费）
            weapon_name = best_unit.get("weapon", "警棍")
            weapon = make_weapon(weapon_name) if weapon_name else make_weapon("警棍")
            w_attr = weapon.attribute if weapon else Attribute.ORDINARY

            if target_armor_attrs:
                effective_set = EFFECTIVE_AGAINST.get(w_attr, set())
                can_hit = any(a in effective_set for a in target_armor_attrs)
                if not can_hit:
                    # 当前警察打不动，尝试换一个能打的过来
                    for other_unit in active_units:
                        if other_unit["id"] == uid:
                            continue
                        other_weapon = make_weapon(other_unit.get("weapon", "警棍"))
                        if other_weapon:
                            other_attr = other_weapon.attribute
                            other_effective = EFFECTIVE_AGAINST.get(other_attr, set())
                            if any(a in other_effective for a in target_armor_attrs):
                                # 把这个警察 move 到目标位置，会和当前警察交换
                                return f"police move {other_unit['id']} {target_loc}"
                    # 没有能打的警察 → 返回 None，让 _cmd_captain 继续尝试其他操作
                    return None

            return f"police attack {uid} {target_player.player_id}"
    # ════════════════════════════════════════════════════════
    #  命令生成器：政治行动
    # ════════════════════════════════════════════════════════

    def _cmd_police_political(self, player, state, available: List[str]) -> List[str]:
        # ---- 降级检查：队长被占 / 警察系统不可用 / 有犯罪记录 → 不生成任何政治命令 ----
        fallback = self._political_fallback_level
        if fallback in ("full_balanced", "develop_only"):
            return []   # 不生成任何警察/政治相关命令

        commands = []
        loc = self._get_location_str(player)
        is_police = getattr(player, 'is_police', False)
        is_captain = getattr(player, 'is_captain', False)

        # 集结（最高优先级！举报后必须先集结才能做其他事）
        if "assemble" in available:
            police = getattr(state, 'police', None)
            if police and police.report_phase == "reported" and police.reporter_id == player.player_id:
                commands.append("assemble")
                return commands  # 集结是最高优先级，立即返回

        # 追踪指引（集结后的后续操作）
        if "track_guide" in available:
            police = getattr(state, 'police', None)
            if police and police.reporter_id == player.player_id:
                pe = getattr(state, 'police_engine', None)
                if pe:
                    can_track, _ = pe.can_track_guide(player.player_id)
                    if can_track:
                        commands.append("track")
                        return commands  # 追踪指引也是高优先级

        # 举报犯罪者（需要在警察局，除非有远程举报天赋）
        # 前置检查：report_phase 必须为 idle 才能举报
        # （available_actions 无条件包含 "report"，不能仅靠 "report" in available 判断）
        police_data = getattr(state, 'police', None)
        report_phase = getattr(police_data, 'report_phase', 'idle') if police_data else 'idle'
        has_captain = police_data.has_captain() if police_data and hasattr(police_data, 'has_captain') else False
        is_self_criminal = police_data.is_criminal(player.player_id) if police_data and hasattr(police_data, 'is_criminal') else False

        if ("report" in available
                and is_police
                and report_phase == "idle"       # 没有正在处理的举报
                and not has_captain              # 没有队长时才能举报
                and not is_self_criminal):       # 自己没有犯罪记录
            can_remote = False
            talent = getattr(player, 'talent', None)
            if talent and hasattr(talent, 'can_remote_report'):
                can_remote = talent.can_remote_report()
            if loc != "警察局" and not can_remote:
                pass  # Skip report — not at police station
            else:
                for pid in state.player_order:
                    if pid == player.player_id:
                        continue
                    target = state.get_player(pid)
                    if target and target.is_alive():
                        target_is_criminal = getattr(target, 'is_criminal', False)
                        if not target_is_criminal:
                            if police_data and hasattr(police_data, 'is_criminal'):
                                target_is_criminal = police_data.is_criminal(target.player_id)
                        if target_is_criminal:
                            commands.append(f"report {target.name}")
                            break

        # 加入警察
        if "recruit" in available and not is_police and loc == "警察局":
            commands.append("recruit")

        # 竞选队长
        if ("election" in available
                and is_police
                and not is_captain
                and not has_captain          # 新增：系统中没有队长才能竞选
                and loc == "警察局"):
            commands.append("election")

        # 指定执法目标
        if "designate" in available and is_captain:
            # 找威胁最高的犯罪者或敌人
            best_target = None
            best_score = -1
            for pid in state.player_order:
                if pid == player.player_id:
                    continue
                target = state.get_player(pid)
                if target and target.is_alive():
                    target_is_criminal = getattr(target, 'is_criminal', False)
                    if not target_is_criminal:
                        police = getattr(state, 'police', None)
                        if police and hasattr(police, 'is_criminal'):
                            target_is_criminal = police.is_criminal(target.player_id)
                    score = self._threat_scores.get(target.name, 0)
                    if target_is_criminal:
                        score += 100  # 优先犯罪者
                    if score > best_score:
                        best_score = score
                        best_target = target
            if best_target:
                commands.append(f"designate {best_target.name}")

        # 移动到警察局
        # 移动到警察局（仅在需要 recruit 或 election 时）
        if "move" in available and not commands and loc != "警察局":
            if not is_police:
                # 还没加入警察，有基本装备后去
                if self._count_outer_armor(player) >= 1:
                    commands.append("move 警察局")
            elif is_police and not is_captain:
                # 已加入但还没当队长，去竞选
                commands.append("move 警察局")
            # 队长不需要回警察局

        return commands

    # ════════════════════════════════════════════════════════
    #  命令生成器：病毒应急（Bug16修复：接收 available_actions）
    # ════════════════════════════════════════════════════════

    def _cmd_virus(self, player, state, available: List[str]) -> List[str]:
        commands = []
        loc = self._get_location_str(player)
        vouchers = getattr(player, 'vouchers', 0)

        # 路径 1：当前在商店/医院 → 直接拿面具
        # 商店：病毒期间免费，否则需凭证；医院：始终需凭证
        virus = getattr(state, 'virus', None)
        virus_active = getattr(virus, 'is_active', False) if virus else False
        if loc == "商店" and "interact" in available and (vouchers >= 1 or virus_active):
            commands.append("interact 防毒面具")
        elif loc == "医院" and "interact" in available and vouchers >= 1:
            commands.append("interact 防毒面具")

        # 路径 2：当前在商店/医院，没凭证 → 先打工
        elif loc in ("商店", "医院") and "interact" in available and vouchers < 1:
            commands.append("interact 打工")

        # 路径 3：当前在魔法所 → 学封闭（不需要凭证，2 回合）
        elif loc == "魔法所" and "interact" in available:
            learned = self._get_learned_spells(player)
            if "封闭" not in learned:
                commands.append("interact 封闭")

        # 路径 4：不在上述地点 → 选人少的地方去
        elif "move" in available:
            # 优先去有凭证能直接拿面具的地方，否则去能打工的地方
            candidates = []
            for dest in ["商店", "医院", "魔法所"]:
                if dest == loc:
                    continue
                enemies = self._count_enemies_at(dest, player, state)
                candidates.append((dest, enemies))
            candidates.sort(key=lambda x: x[1])
            if candidates:
                commands.append(f"move {candidates[0][0]}")
            else:
                commands.append("move 商店")

        return commands

    # ════════════════════════════════════════════════════════
    #  目标选择
    # ════════════════════════════════════════════════════════
    def _political_should_fallback(self, player, state):
        """Check if political AI should fall back.
        Called every turn - purely reads current state, no persistent flags.
        Returns:
            "none"         — 正常政治路径（去警察局、recruit、竞选）
            "develop_only" — 只发育不攻击（有犯罪记录但队长位空，还有机会洗白）
            "full_balanced" — 完全 balanced 策略含攻击（已有其他队长/警察全灭，政治路径无望）
        """
        police = getattr(state, 'police', None)
        if not police:
            return "full_balanced"

        # 警察系统永久禁用 → 完全 balanced
        if police.permanently_disabled:
            return "full_balanced"

        # 自己已是队长 → 正常（队长有自己的指挥逻辑）
        if police.captain_id == player.player_id:
            return "none"

        # 有其他队长 → 完全 balanced（解禁攻击，需要战斗求生）
        if police.has_captain():
            return "full_balanced"

        # 无队长，检查自己是否有犯罪记录
        is_criminal = getattr(player, 'is_criminal', False)
        if not is_criminal:
            # 也检查 police.is_criminal（双重保险）
            is_criminal = police.is_criminal(player.player_id)

        if is_criminal:
            # 有犯罪记录但队长位空 → 只发育不攻击
            # （犯罪记录可能被献予律法之诗清除，所以每轮重新检查）
            return "develop_only"

        # 无队长、无犯罪 → 正常政治路径
        return "none"

    def _should_become_captain(self, player, state) -> bool:
        """判断是否应该竞选警察队长"""
        if not getattr(player, 'is_police', False):
            return False
        police = getattr(state, 'police', None)
        if not police:
            return False
        # 检查是否已有队长
        captain_id = getattr(police, 'captain_id', None)
        if captain_id is not None:
            return False
        # 检查警察系统是否永久禁用
        if getattr(police, 'permanently_disabled', False):
            return False
        return True

    def _update_threat_scores(self, player, state):
        """更新威胁分数（_update_threat_assessment的别名）"""
        self._update_threat_assessment(player, state)

    def _cleanup_dead_players(self, state):
        """清理已死亡玩家的相关数据"""
        dead_names = []
        for pid in state.player_order:
            target = state.get_player(pid)
            if target and not target.is_alive():
                dead_names.append(target.name)
        for name in dead_names:
            if name in self._threat_scores:
                del self._threat_scores[name]
            self._been_attacked_by.discard(name)

    def _count_locked_by(self, player, state) -> int:
        """计算有多少人锁定了自己"""
        count = 0
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if target and target.is_alive():
                locked = getattr(target, 'locked_target', None)
                if locked and (locked == player.name or locked == player.player_id):
                    count += 1
        return count

    def _estimate_talent_adjusted_damage(self, player, weapon=None) -> float:
        """估算考虑天赋修正后的实际伤害（用于评估其他玩家的威胁）

        如果 weapon 为 None，则返回该玩家最强武器的天赋修正后伤害。
        """
        if weapon is not None:
            base_dmg = self._get_weapon_damage(weapon)
        else:
            # 找最强武器
            weapons = getattr(player, 'weapons', [])
            if not weapons:
                return 0.0
            base_dmg = max((self._get_weapon_damage(w) for w in weapons if w), default=0.0)

        talent = getattr(player, 'talent', None)
        if not talent:
            return base_dmg

        # 火萤IV型：所有伤害×2
        if hasattr(talent, 'name') and talent.name == "火萤IV型-完全燃烧":
            return base_dmg * 2.0

        # 救世主状态：近战+temp_attack_bonus
        if hasattr(talent, 'is_savior') and talent.is_savior:
            bonus = getattr(talent, 'temp_attack_bonus', 0.0)
            # 只有近战武器才有加成，但估算时按最大值算
            return base_dmg + bonus

        # 一刀缭断：有使用次数时近战×2（但只有一次，影响有限）
        # 不在这里加成，因为是一次性的

        return base_dmg

    def _best_weapon_damage(self, player) -> float:
        """获取玩家最强武器的伤害值"""
        weapons = getattr(player, 'weapons', [])
        if not weapons:
            return 0.0
        best =0.0
        for w in weapons:
            dmg = self._estimate_talent_adjusted_damage(player, w)
            if dmg > best:
                best = dmg
        return best

    def _pick_target(self, player, state) -> Optional[Any]:
        """选择最佳攻击目标"""
        candidates = []
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if not target or not target.is_alive():
                continue
            # 警察成员不攻击普通玩家（Bug修复：警察犯罪限制）
            if getattr(player, 'is_police', False) and not getattr(player, 'is_captain', False):
                if not getattr(target, 'is_criminal', False):
                    continue
            candidates.append(target)

        if not candidates:
            return None

                # 评分
        # 预计算全场最强候选的 power
        max_power = max(
            (self._estimate_power(c) for c in candidates),
            default=0
        )

        def score(t):
            s = 0
            s += self._threat_scores.get(t.name, 0) * 2
            if t.name in self._been_attacked_by:
                s += 50
            if self._same_location(player, t):
                s += 30
            s += max(0, 5 - t.hp) * 10
            s -= self._count_outer_armor(t) * 15
            s -= self._count_inner_armor(t) * 10
            if self.personality == "assassin":
                s += max(0, 3 - t.hp) * 20
                if self._count_outer_armor(t) == 0:
                    s += 40
                if self._count_inner_armor(t) == 0:
                    s += 20
            if self.personality == "aggressive":
                target_name = getattr(t, 'name', '')
                target_pid = getattr(t, 'player_id', '')
                is_passive = (target_name not in self._players_who_attacked
                            and target_pid not in self._players_who_attacked)
                if is_passive:
                    target_power = self._estimate_power(t)
                    s += 30 + target_power * 0.3  # 越肉的发育者越危险
            # 武器有效性
            if self._all_weapons_countered(player, t):
                s -= 200
            # 隐身且无探测 → 大幅降分（打不到）
            if getattr(t, 'is_invisible', False) and not getattr(player, 'has_detection', False):
                markers_obj = getattr(state, 'markers', None)
                if markers_obj and hasattr(markers_obj, 'is_visible_to'):
                    if not markers_obj.is_visible_to(t.player_id, player.player_id, player.has_detection):
                        s -= 300  # 看不到的目标大幅降分
            # 队长保护
            if getattr(t, 'is_captain', False):
                has_aoe = self._has_aoe_weapon(player)
                if not has_aoe and self._captain_has_police_escort(t, state):
                    s -= 500
            # 全场最强玩家额外加分
            if self._estimate_power(t) >= max_power:
                s += 40
            # 火萤 debuff 生效后的目标偏好
            if self._has_firefly_talent(player) and self._firefly_debuff_active(player):
                # 优先攻击没有伤害>=2武器的玩家
                enemy_best_dmg = self._best_weapon_damage(t)
                if enemy_best_dmg < 2.0:
                    s += 60  # 大幅加分：优先打弱者
                else:
                    # 所有人都有高伤害武器时，优先打 hp+护盾总值低的
                    total_effective_hp = t.hp + self._count_outer_armor(t) + self._count_inner_armor(t)
                    s += max(0, 10 - total_effective_hp) * 15  # 总值越低分越高
            return s

        candidates.sort(key=score, reverse=True)
        return candidates[0]


    def _can_attack_target(self, player, target, state) -> bool:
        """检查是否可能攻击目标（考虑距离和武器）"""
        weapons = getattr(player, 'weapons', [])
        if not weapons:
            return False

        for w in weapons:
            wr = self._get_weapon_range(w)
            if wr == "area":
                if self._same_location(player, target):
                    return True
            elif wr == "ranged":
                # 救世主状态禁用远程
                if self._is_in_savior_state(player):
                    continue
                return True  # 远程不需要同地点
            elif wr == "melee":
                if self._same_location(player, target):
                    return True
        return False

    # ════════════════════════════════════════════════════════
    #  武器选择
    # ════════════════════════════════════════════════════════

    def _pick_weapon(self, player, target) -> Optional[Any]:
        """选择最佳武器"""
        weapons = getattr(player, 'weapons', [])
        if not weapons:
            return None

        # 过滤掉 None，让所有武器（含拳击）参与评分，由 weapon_score 决定优劣
        pool = [w for w in weapons if w]
        # 救世主状态：过滤掉远程武器（validator 会拒绝，避免浪费重试）
        if self._is_in_savior_state(player):
            melee_and_area = [w for w in pool if self._get_weapon_range(w) != "ranged"]
            if melee_and_area:
                pool = melee_and_area
        if not pool:
            return None

        target_outer_attrs = self._get_outer_armor_attr(target)
        if not target_outer_attrs:
            target_outer_attrs = self._get_inner_armor_attr(target)

        def weapon_score(w):
            s = 0
            dmg = self._get_weapon_damage(w)
            # 救世主状态：近战武器加上临时攻击力加成
            if self._is_in_savior_state(player) and self._get_weapon_range(w) == "melee":
                talent = getattr(player, 'talent', None)
                if talent and hasattr(talent, 'temp_attack_bonus'):
                    dmg += talent.temp_attack_bonus
            s += dmg * 10

            # 蓄力必须但未蓄力 → 打不出去，大幅扣分
            if (getattr(w, 'requires_charge', False)
                    and getattr(w, 'charge_mandatory', True)
                    and not getattr(w, 'is_charged', False)):
                s -= 200

            w_attr = self._get_weapon_attr(w)
            if target_outer_attrs and w_attr in EFFECTIVE_AGAINST:
                effective_set = EFFECTIVE_AGAINST[w_attr]
                has_effective = False
                for armor_attr in target_outer_attrs:
                    if armor_attr in effective_set:
                        has_effective = True
                        s += 20
                        break
                if not has_effective:
                    s -= 50

            # 射程适配
            wr = self._get_weapon_range(w)
            if self._same_location(player, target):
                if wr == "melee":
                    s += 10
                elif wr == "area":
                    s += 5  # area 同地点也能打
            else:
                if wr == "ranged":
                    s += 15
                elif wr == "melee":
                    s -= 20

            # 控制效果加分（同地点时更有价值）
            tags = getattr(w, 'special_tags', []) or []
            has_control = any(t in tags for t in ("shock_2_targets", "stun_on_hit"))
            if has_control and self._same_location(player, target):
                s += 15

            return s

        sorted_weapons = sorted(pool, key=weapon_score, reverse=True)
        return sorted_weapons[0]

    def _pick_attack_layer(self, player, target, weapon) -> tuple:
        """选择攻击层和属性，返回 (layer_str, armor_attr_str)

        layer_str: "外层" / "内层" / None（无甲直接打HP）
        armor_attr_str: 目标护甲的属性字符串（如 "魔法"），用于指定攻击哪件护甲
        """
        from models.equipment import ArmorLayer
        from utils.attribute import Attribute

        outer_active = []
        inner_active = []
        armor = getattr(target, 'armor', None)
        if armor and hasattr(armor, 'get_active'):
            outer_active = armor.get_active(ArmorLayer.OUTER)
            inner_active = armor.get_active(ArmorLayer.INNER)

        w_attr = weapon.attribute if weapon else Attribute.ORDINARY

        if outer_active:
            # 优先攻击能被武器克制的外甲
            best_piece = self._pick_best_armor_target(outer_active, w_attr)
            armor_attr_str = best_piece.attribute.value if hasattr(best_piece.attribute, 'value') else str(best_piece.attribute)
            return ("外层", armor_attr_str)
        elif inner_active:
            best_piece = self._pick_best_armor_target(inner_active, w_attr)
            armor_attr_str = best_piece.attribute.value if hasattr(best_piece.attribute, 'value') else str(best_piece.attribute)
            return ("内层", armor_attr_str)
        else:
            # 无甲，不指定层和属性
            return (None, None)
    def _pick_best_armor_target(self, armor_pieces, weapon_attr) -> Any:
        """从护甲列表中选择最佳攻击目标：优先选能被武器克制的"""
        effective_set = EFFECTIVE_AGAINST.get(weapon_attr, set())
        # 优先选能被克制的护甲
        for piece in armor_pieces:
            if piece.attribute in effective_set:
                return piece
        # 没有可克制的，选第一个
        return armor_pieces[0]

    def _pick_counter_attr(self, target_armor_attr) -> 'Attribute':
        """根据目标护甲属性，选择克制它的武器属性"""
        from utils.attribute import Attribute
        counter_map = {
            Attribute.ORDINARY: Attribute.TECH,    # 科技克普通
            Attribute.MAGIC: Attribute.ORDINARY,    # 普通克魔法
            Attribute.TECH: Attribute.MAGIC,        # 魔法克科技
        }
        return counter_map.get(target_armor_attr, Attribute.ORDINARY)

    # ════════════════════════════════════════════════════════
    #  天赋相关决策 已经被完全转移
    # ════════════════════════════════════════════════════════



    # ════════════════════════════════════════════════════════
    #  辅助方法：装备计数与查询
    # ════════════════════════════════════════════════════════

    def _is_danger_resolved(self, player) -> bool:
        """判断危险状态是否已解除"""
        if self._is_critical(player, self._game_state):
            return False
        # 火萤 debuff 生效后不要求护甲来解除危险
        if self._has_firefly_talent(player) and self._firefly_debuff_active(player):
            return True
        total_armor = self._count_outer_armor(player) + self._count_inner_armor(player)
        return total_armor >= 2


    def _has_armor_by_name(self, player, armor_name: str) -> bool:
        """检查玩家是否已有指定名称的活跃护甲"""
        armor = getattr(player, 'armor', None)
        if armor and hasattr(armor, 'get_all_active'):
            for piece in armor.get_all_active():
                if piece.name == armor_name:
                    return True
        return False

    def _count_outer_armor(self, player) -> int:
        """统计玩家活跃的外层护甲数量"""
        armor = getattr(player, 'armor', None)
        if armor and hasattr(armor, 'get_active'):
            from models.equipment import ArmorLayer
            return len(armor.get_active(ArmorLayer.OUTER))
        return 0

    def _count_inner_armor(self, player) -> int:
        """统计玩家活跃的内层护甲数量"""
        armor = getattr(player, 'armor', None)
        if armor and hasattr(armor, 'get_active'):
            from models.equipment import ArmorLayer
            return len(armor.get_active(ArmorLayer.INNER))
        return 0

    def _get_outer_armor_attr(self, player) -> list:
        """获取玩家所有活跃外层护甲的属性列表"""
        armor = getattr(player, 'armor', None)
        if armor and hasattr(armor, 'get_active'):
            from models.equipment import ArmorLayer
            return [a.attribute for a in armor.get_active(ArmorLayer.OUTER)]
        return []

    def _get_inner_armor_attr(self, player) -> list:
        """获取玩家所有活跃内层护甲的属性列表"""
        armor = getattr(player, 'armor', None)
        if armor and hasattr(armor, 'get_active'):
            from models.equipment import ArmorLayer
            return [a.attribute for a in armor.get_active(ArmorLayer.INNER)]
        return []

    def _get_weapon_damage(self, weapon) -> float:
        """获取武器有效伤害"""
        if not weapon:
            return 0.0
        if hasattr(weapon, 'get_effective_damage'):
            return weapon.get_effective_damage()
        return getattr(weapon, 'base_damage', 1.0)

    def _get_weapon_range(self, weapon) -> str:
        """获取武器的射程类型"""
        from models.equipment import WeaponRange
        if not weapon:
            return "melee"
        wr = getattr(weapon, 'weapon_range', None)
        if wr == WeaponRange.MELEE:
            return "melee"
        elif wr == WeaponRange.RANGED:
            return "ranged"
        elif wr == WeaponRange.AREA:
            return "area"
        return "melee"



    def _captain_has_police_escort(self, captain, state) -> bool:
        """检查队长所在地点是否有活跃的警察单位"""
        police = getattr(state, 'police', None)
        if not police:
            return False
        captain_loc = self._get_location_str(captain)
        for unit in getattr(police, 'units', []):
            if (unit.is_alive() and not unit.is_disabled()
                    and getattr(unit, 'location', None) == captain_loc):
                return True
        return False

    def _get_weapon_attr(self, weapon):
        """获取武器属性（返回 Attribute 枚举）"""
        from utils.attribute import Attribute
        attr = getattr(weapon, 'attribute', None)
        if isinstance(attr, Attribute):
            return attr
        return Attribute.ORDINARY

    def _has_melee_only(self, player) -> bool:
        """是否只有近战武器"""
        weapons = getattr(player, 'weapons', [])
        for w in weapons:
            if self._get_weapon_range(w) != "melee":
                return False
        return True

    def _has_stealth(self, player) -> bool:
        """检查玩家是否有隐身能力"""
        # 检查隐身状态
        if getattr(player, 'is_invisible', False):
            return True
        # 检查物品（player.items 列表）
        items = getattr(player, 'items', [])
        for item in items:
            name = getattr(item, 'name', '')
            if name in ("隐身衣", "隐形涂层"):
                return True
        # 检查已学法术（player.learned_spells 是set）
        learned = getattr(player, 'learned_spells', set())
        if "隐身术" in learned:
            return True
        return False

    def _has_virus_immunity(self, player) -> bool:
        """检查玩家是否有病毒免疫"""
        # 检查物品
        items = getattr(player, 'items', [])
        for item in items:
            name = getattr(item, 'name', '')
            if name == "防毒面具":
                return True
        # 检查已学法术
        learned = getattr(player, 'learned_spells', set())
        if "封闭" in learned:
            return True
        # 检查 has_seal 标记
        if getattr(player, 'has_seal', False):
            return True
        return False

    def _get_learned_spells(self, player) -> set:
        """获取玩家已学法术集合"""
        return getattr(player, 'learned_spells', set())
    # ════════════════════════════════════════════════════════
    #  辅助方法：位置相关
    # ════════════════════════════════════════════════════════

    def _get_location_str(self, player) -> str:
        """获取玩家位置字符串"""
        loc = getattr(player, 'location', None)
        if loc is None:
            return "unknown"
        if isinstance(loc, str):
            return loc
        if hasattr(loc, 'name'):
            return loc.name
        return str(loc)

    def _is_at_home(self, player) -> bool:
        """是否在自己家"""
        loc = self._get_location_str(player)
        pid = getattr(player, 'player_id', '')
        return loc == "home" or loc == f"home_{pid}" or "家" in loc

    def _same_location(self, player1, player2) -> bool:
        """两个玩家是否在同一地点"""
        loc1 = self._get_location_str(player1)
        loc2 = self._get_location_str(player2)
        return loc1 == loc2 and loc1 != "unknown"

    def _get_same_location_targets(self, player, state) -> List[Any]:
        """获取同地点的敌方玩家"""
        result = []
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if target and target.is_alive() and self._same_location(player, target):
                result.append(target)
        return result

    def _find_nearest_enemy_location(self, player, state) -> Optional[str]:
            """找到威胁度最大的敌人所在位置（因为这游戏没有距离概念啦）
            aggressive 人格会优先去发育者（从未攻击过任何人的玩家）所在位置
            """
            # 预计算全场最强玩家的 power（避免在循环内重复计算）
            max_power = max(
                (self._estimate_power(state.get_player(other_pid))
                 for other_pid in state.player_order
                 if other_pid != player.player_id
                 and state.get_player(other_pid) and state.get_player(other_pid).is_alive()),
                default=0
            )

            candidates = []
            for pid in state.player_order:
                if pid == player.player_id:
                    continue
                target = state.get_player(pid)
                if target and target.is_alive():
                    target_loc = self._get_location_str(target)
                    threat = self._threat_scores.get(target.name, 0)
                    target_power = self._estimate_power(target)

                    # aggressive 优先骚扰发育者
                    if self.personality == "aggressive":
                        target_name = getattr(target, 'name', '')
                        target_pid = getattr(target, 'player_id', '')
                        is_passive = (target_name not in self._players_who_attacked
                                    and target_pid not in self._players_who_attacked)
                        if is_passive:
                            threat += 30 + target_power * 0.3  # 越肉的发育者越危险

                    # 全场最强玩家额外加分
                    # 甲最多的人是最大的后期威胁，所有人都应该优先针对
                    if target_power >= max_power:
                        threat += 40  # 最强玩家额外 +40 优先级

                    candidates.append((target_loc, threat))

            if not candidates:
                return None

            # 按威胁分排序
            candidates.sort(key=lambda x: x[1], reverse=True)
            return candidates[0][0]

    def _find_safe_location(self, player, state) -> Optional[str]:
        """找到最安全的位置（按敌人数量排序）"""
        loc = self._get_location_str(player)
        enemies_here = len(self._get_same_location_targets(player, state))

        if enemies_here == 0:
            return None  # 当前已安全

        # 收集所有候选地点及其敌人数
        candidates = []
        all_locations = ["home", "商店", "医院", "魔法所", "军事基地", "警察局"]
        for test_loc in all_locations:
            if test_loc == loc:
                continue
            enemies_at = self._count_enemies_at(test_loc, player, state)
            candidates.append((test_loc, enemies_at))

        # 按敌人数升序排序，优先去没人的地方
        candidates.sort(key=lambda x: x[1])

        if candidates:
            return candidates[0][0]
        return "home"

    # ════════════════════════════════════════════════════════
    #  辅助方法：威胁评估
    # ════════════════════════════════════════════════════════

    def _cmd_rearm(self, player, state, available: List[str]) -> List[str]:
        """近战中所有武器被克制后的换武器逻辑

        1. 检查当前地点能否interact到非普通属性武器 → 直接拿
        2. 不能 → move到魔法所或军事基地
        - 有凭证且缺科技和魔法武器 → 选人少的
        - 选军事基地时强买通行证（通过confirm机制）
        - 没凭证 → 去魔法所
        """
        commands = []
        loc = self._get_location_str(player)

        # 1) 当前地点能拿到非普通武器吗？
        if "interact" in available:
            interact_cmd = self._get_counter_weapon_interact_cmd(player)
            if interact_cmd:
                commands.append(interact_cmd)
                return commands

        # 2) 当前地点拿不到，需要移动
        if "move" in available:
            dest = self._pick_counter_weapon_destination(player, state)
            if dest and dest != loc:
                commands.append(f"move {dest}")

        return commands

    def _all_weapons_countered(self, player, target) -> bool:
        """检查玩家所有武器是否都被目标护甲克制（检查所有层）"""
        weapons = getattr(player, 'weapons', [])
        if not weapons:
            return True  # 没武器视为被克制

        target_outer_attrs = self._get_outer_armor_attr(target)
        target_inner_attrs = self._get_inner_armor_attr(target)

        if not target_outer_attrs and not target_inner_attrs:
            return False  # 目标无甲，任何武器都有效

        # 需要检查的护甲层：如果有外层就检查外层，否则检查内层
        # （因为攻击时先打外层，外层打完才打内层）
        check_attrs = target_outer_attrs if target_outer_attrs else target_inner_attrs

        for w in weapons:
            w_attr = self._get_weapon_attr(w)
            effective_set = EFFECTIVE_AGAINST.get(w_attr, set())
            for armor_attr in check_attrs:
                if armor_attr in effective_set:
                    return False  # 至少有一把武器能打当前层
        return True

    def _has_non_ordinary_weapon(self, player) -> bool:
        """检查玩家是否拥有非普通属性的武器（魔法或科技）"""
        for w in player.weapons:
            attr = self._get_weapon_attr(w)
            if attr in (Attribute.MAGIC, Attribute.TECH):
                return True
        return False

    def _get_counter_weapon_interact_cmd(self, player) -> Optional[str]:
        """在当前地点寻找可以interact获取的非普通属性武器，返回interact命令或None"""
        loc = self._get_location_str(player)
        learned = self._get_learned_spells(player)
        has_pass = getattr(player, 'has_military_pass', False)

        if loc == "魔法所":
            if "魔法弹幕" not in learned:
                return "interact 魔法弹幕"
            if "远程魔法弹幕" not in learned:
                return "interact 远程魔法弹幕"
            if "地震" not in learned:
                return "interact 地震"
            if "地动山摇" not in learned:
                return "interact 地动山摇"
            return None

        elif loc == "军事基地" and has_pass:
            # 高斯步枪（近战科技）、电磁步枪（范围科技）
            has_gauss = any(w.name == "高斯步枪" for w in player.weapons)
            has_emr = any(w.name == "电磁步枪" for w in player.weapons)
            if not has_gauss:
                return "interact 高斯步枪"
            if not has_emr:
                return "interact 电磁步枪"
            return None

        # home、商店、医院、警察局都没有非普通属性武器
        return None

    def _pick_counter_weapon_destination(self, player, state) -> str:
        """选择去哪里获取非普通属性武器

        规则：
        - 有凭证且缺科技和魔法武器 → 选魔法所或军事基地中人少的
        - 如果选军事基地，到达时会触发强买通行证
        - 没凭证 → 去魔法所（免费）
        """
        vouchers = getattr(player, 'vouchers', 0)
        has_magic_weapon = any(
            self._get_weapon_attr(w) == Attribute.MAGIC
            for w in player.weapons
        )
        has_tech_weapon = any(
            self._get_weapon_attr(w) == Attribute.TECH
            for w in player.weapons
        )

        if vouchers < 1:
            # 没凭证，只能去魔法所（免费学法术）
            return "魔法所"

        # 有凭证，两个地方都可以去
        candidates = []
        if not has_magic_weapon:
            candidates.append("魔法所")
        if not has_tech_weapon:
            candidates.append("军事基地")

        if not candidates:
            # 两种都有了但还是被克制？理论上不应该发生，保底去魔法所
            return "魔法所"

        if len(candidates) == 1:
            return candidates[0]

        # 两个都可以，选人少的
        enemies_magic = self._count_enemies_at("魔法所", player, state)
        enemies_military = self._count_enemies_at("军事基地", player, state)
        if enemies_military <= enemies_magic:
            return "军事基地"
        else:
            return "魔法所"

    def _should_continue_combat(self, player, target) -> bool:
        if not target or not target.is_alive():
            return False
        # aggressive：只有被打到无甲才撤退
        if self.personality == "aggressive":
            total_armor = self._count_outer_armor(player) + self._count_inner_armor(player)
            if total_armor == 0:
                return False
        else:
            # 其他人格：HP <= 0.5 时退出
            if player.hp <= 0.5:
                return False
        if self._is_at_disadvantage(player, target) and self.personality == "defensive":
            return False
        # 所有武器被目标护甲克制 → 退出近战
        if self._all_weapons_countered(player, target):
            return False
        # political 非 full_balanced 时不继续战斗（避免犯法），队长除外
        if (self.personality == "political"
                and not self._political_in_balanced_fallback
                and not getattr(player, 'is_captain', False)):
            return False
        return True

    def _is_at_disadvantage(self, player, target) -> bool:
        """是否处于劣势"""
        my_power = self._estimate_power(player)
        enemy_power = self._estimate_power(target)
        return my_power < enemy_power * 0.7

    def _estimate_power(self, player) -> float:
        """估算玩家战力"""
        power = 0.0
        power += player.hp * 10

        weapons = getattr(player, 'weapons', [])
        for w in weapons:
            power += self._estimate_talent_adjusted_damage(player, w) * 15 if w else 0

        outer = self._count_outer_armor(player)
        inner = self._count_inner_armor(player)
        power += outer * 20
        power += inner * 15

        if self._has_stealth(player):
            power += 10

        if getattr(player, 'has_detection', False):
            power += 5

        return power

    def _cmd_danger_develop(self, player, state, available: List[str]) -> List[str]:
        """危险模式下的发育：在当前地点拿护甲，然后移动到远离当前位置的安全地点"""
        commands = []
        loc = self._get_location_str(player)
        outer = self._count_outer_armor(player)
        inner = self._count_inner_armor(player)
        vouchers = getattr(player, 'vouchers', 0)

        # 1) 当前地点能拿到护甲就拿
        if "interact" in available:
            if loc == "home" or self._is_at_home(player):
                if outer == 0 and not self._has_armor_by_name(player, "盾牌"):
                    commands.append("interact 盾牌")
            elif loc == "商店":
                if vouchers >= 1 and outer < 2 and not self._has_armor_by_name(player, "陶瓷护甲"):
                    commands.append("interact 陶瓷护甲")
                if vouchers < 1:
                    commands.append("interact 打工")
                if not self._has_virus_immunity(player) and getattr(state, 'virus', None) and getattr(state.virus, 'is_active', False):
                    commands.insert(0, "interact 防毒面具")  # 插到最前面
            elif loc == "魔法所":
                learned = self._get_learned_spells(player)
                if "魔法护盾" not in learned and outer < 2:
                    commands.append("interact 魔法护盾")
            elif loc == "医院":
                if inner == 0:
                    commands.append("interact 晶化皮肤手术")
                if not self._has_virus_immunity(player) and getattr(state, 'virus', None) and getattr(state.virus, 'is_active', False):
                    if vouchers >= 1:
                        commands.insert(0, "interact 防毒面具")
                    else:
                        commands.insert(0, "interact 打工")  # 先打工拿凭证
                if vouchers < 1:
                    commands.append("interact 打工")
            elif loc == "军事基地":
                has_pass = getattr(player, 'has_military_pass', False)
                if has_pass and outer < 2 and not self._has_armor_by_name(player, "AT力场"):
                    commands.append("interact AT力场")

        # 2) 移动到安全且能拿护甲的地方（优先远离当前位置）
        if "move" in available:
            dest = self._pick_safe_armor_destination(player, state)
            if dest and dest != loc:
                commands.append(f"move {dest}")

        return commands

    def _pick_safe_armor_destination(self, player, state) -> Optional[str]:
        """危险模式下选择目的地：安全 + 能拿护甲"""
        loc = self._get_location_str(player)
        outer = self._count_outer_armor(player)
        inner = self._count_inner_armor(player)
        vouchers = getattr(player, 'vouchers', 0)
        has_pass = getattr(player, 'has_military_pass', False)

        # 候选地点：能拿到护甲的地方
        armor_locations = []
        if outer < 1 and loc != "home":
            armor_locations.append("home")  # 盾牌
        if outer < 2 and loc != "商店":
            armor_locations.append("商店")  # 陶瓷护甲
        if outer < 2 and loc != "魔法所":
            armor_locations.append("魔法所")  # 魔法护盾
        if inner < 1 and loc != "医院":
            armor_locations.append("医院")  # 手术
        if has_pass and outer < 2 and loc != "军事基地":
            armor_locations.append("军事基地")  # AT力场

        if not armor_locations:
            # 没有需要护甲的地方，找最安全的地方
            return self._find_safe_location(player, state)

        # 按敌人数排序，选最安全的
        scored = []
        for dest in armor_locations:
            enemies = self._count_enemies_at(dest, player, state)
            scored.append((dest, enemies))
        scored.sort(key=lambda x: x[1])

        return scored[0][0]

    # ════════════════════════════════════════════════════════
    #  轮次事件处理
    # ════════════════════════════════════════════════════════

    def on_round_start(self, player, state, round_number: int):
        """轮次开始时调用"""
        self._round_number = round_number
        self._action_used = False
        self._missile_cooldown = max(0, self._missile_cooldown - 1)

        # 更新威胁评估
        self._update_threat_assessment(player, state)

        # 更新缓存
        self._update_caches(player, state)

        debug_ai_basic(player.name,
            f"轮次{round_number}开始，人格={self.personality}，"
            f"阶段={self._current_phase}")

    def on_round_end(self, player, state, round_number: int):
        """轮次结束时调用"""
        self._been_attacked_by.clear()

    def on_damaged(self, player, attacker_name: str, damage: float):
        """被攻击时调用"""
        self._been_attacked_by.add(attacker_name)
        # 增加攻击者威胁
        self._threat_scores[attacker_name] = \
            self._threat_scores.get(attacker_name, 0) + damage * 10
        debug_ai_basic(player.name,
            f"被 {attacker_name} 攻击，伤害={damage}")

    def on_player_killed(self, player, killed_name: str, killer_name: str):
        """有玩家被杀时调用"""
        if killed_name in self._threat_scores:
            del self._threat_scores[killed_name]
        if killer_name and killer_name != player.name:
            self._threat_scores[killer_name] = \
                self._threat_scores.get(killer_name, 0) + 30
        debug_ai_basic(player.name,
            f"玩家 {killed_name} 被 {killer_name} 杀死")

    def _update_threat_assessment(self, player, state):
        """更新威胁评估"""
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if not target or not target.is_alive():
                if target and target.name in self._threat_scores:
                    del self._threat_scores[target.name]
                continue
            power = self._estimate_power(target)
            existing = self._threat_scores.get(target.name, 0)
            # 衰减历史威胁 + 新威胁
            self._threat_scores[target.name] = existing * 0.8 + power * 0.2

        # 检测安静发育者：连续多轮处于最低威胁的玩家
        alive_threats = {
            name: score for name, score in self._threat_scores.items()
            if any(
                state.get_player(p) and state.get_player(p).is_alive()
                and state.get_player(p).name == name
                for p in state.player_order
            )
        }
        if len(alive_threats) >= 2:
            min_threat = min(alive_threats.values())
            for name, score in alive_threats.items():
                if score <= min_threat + 1.0:
                    self._low_threat_streak[name] = self._low_threat_streak.get(name, 0) + 1
                else:
                    self._low_threat_streak[name] = 0
                if self._low_threat_streak.get(name, 0) >= 5:
                    self._threat_scores[name] = self._threat_scores.get(name, 0) + 15.0

        # 清理死亡玩家
        for name in list(self._low_threat_streak.keys()):
            if name not in alive_threats:
                del self._low_threat_streak[name]

    def _update_caches(self, player, state):
        """更新缓存信息"""
        # 更新警察缓存（复用 _read_police_state 保持字段一致）
        self._read_police_state(state)

        # 更新病毒缓存
        virus = getattr(state, 'virus', None)
        if virus:
            self._virus_active = getattr(virus, 'is_active', False)
            self._virus_location = self._get_location_str(virus) if hasattr(virus, 'location') else None
        else:
            self._virus_active = False
            self._virus_location = None

    # ════════════════════════════════════════════════════════
    #  响应窗口方法
    # ════════════════════════════════════════════════════════

    def respond_to_event(self, player, state, event_type: str,
                         event_data: dict) -> Optional[str]:
        """
        响应窗口事件处理
        Bug12修复：确保返回合法响应
        """
        debug_ai_basic(player.name,
            f"响应事件: type={event_type}, data={event_data}")

        if event_type == "被攻击":
            return self._respond_attacked(player, state, event_data)
        elif event_type == "举报":
            return self._respond_report(player, state, event_data)
        elif event_type == "天赋触发":
            return self._respond_talent(player, state, event_data)
        elif event_type == "投票":
            return self._respond_vote(player, state, event_data)
        elif event_type == "警察行动":
            return self._respond_police_action(player, state, event_data)
        elif event_type == "病毒":
            return self._respond_virus(player, state, event_data)
        else:
            debug_ai_basic(player.name, f"未知事件类型: {event_type}")
            return None

    def _respond_attacked(self, player, state, data) -> Optional[str]:
        """被攻击时的响应"""
        attacker = data.get("attacker")
        if not attacker:
            return None

        # 记录被攻击
        self._been_attacked_by.add(attacker)

        # 检查是否有防御选项
        options = data.get("options", [])
        if "dodge" in options and self.personality in ("assassin", "defensive"):
            return "dodge"
        if "block" in options:
            return "block"
        if "counter" in options and self.personality == "aggressive":
            return "counter"

        return options[0] if options else None

    def _respond_report(self, player, state, data) -> Optional[str]:
        """举报事件响应"""
        reporter = data.get("reporter")
        target = data.get("target")
        options = data.get("options", [])

        if target == player.name:
            # 自己被举报
            if "deny" in options:
                return "deny"
            return options[0] if options else None

        # 别人被举报，投票
        if "support" in options and "oppose" in options:
            target_player = None
            for pid in state.player_order:
                p = state.get_player(pid)
                if p and p.name == target:
                    target_player = p
                    break

            if target_player:
                threat = self._threat_scores.get(target, 0)
                if threat > 30:
                    return "support"
                elif self.personality == "political":
                    return "support"
                else:
                    return "oppose"

        return options[0] if options else None

    def _respond_talent(self, player, state, data) -> Optional[str]:
        """天赋触发响应"""
        options = data.get("options", [])
        talent_type = data.get("talent_type", "")

        # 默认接受
        if "accept" in options:
            return "accept"
        if "activate" in options:
            return "activate"

        return options[0] if options else None

    def _respond_vote(self, player, state, data) -> Optional[str]:
        """投票响应"""
        options = data.get("options", [])
        candidate = data.get("candidate")

        if candidate:
            # 如果候选人是自己的盟友
            threat = self._threat_scores.get(candidate, 0)
            if threat < 20 and "support" in options:
                return "support"
            elif "oppose" in options:
                return "oppose"

        return options[0] if options else None

    def _respond_police_action(self, player, state, data) -> Optional[str]:
        """警察行动响应"""
        options = data.get("options", [])
        action = data.get("action", "")

        if action == "arrest" and player.name == data.get("target"):
            if "resist" in options and self.personality == "aggressive":
                return "resist"
            if "surrender" in options:
                return "surrender"

        return options[0] if options else None

    def _respond_virus(self, player, state, data) -> Optional[str]:
        """病毒事件响应"""
        options = data.get("options", [])
        if "use_mask" in options:
            return "use_mask"
        if "flee" in options:
            return "flee"
        return options[0] if options else None

    def _is_pursued_by_police(self, player, state) -> bool:
        """检查是否正在被警察追击"""
        pc = self._police_cache or {}
        if pc.get("report_target") == player.player_id:
            phase = pc.get("report_phase", "idle")
            if phase in ("reported", "dispatched"):
                return True
        return False

    def _can_fight_police(self, player, state) -> bool:
        """判断是否有能力反击警察：内甲+外甲>=2，或有克制警察武器的护甲"""
        outer = self._count_outer_armor(player)
        inner = self._count_inner_armor(player)
        if outer + inner >= 2:
            return True
        # 检查是否有护甲克制同地点警察的武器
        pc = self._police_cache or {}
        for unit in pc.get("units", []):
            if not unit.get("is_alive"):
                continue
            weapon_name = unit.get("weapon", "警棍")
            # 检查玩家护甲是否克制该武器属性
            if self._armor_counters_weapon(player, weapon_name):
                return True
        return False

    def _cmd_fight_police(self, player, state, available) -> List[str]:
        """反击警察：去拿AOE武器，然后攻击同地点的警察"""
        commands = []
        loc = self._get_location_str(player)

        # 检查是否已有AOE武器
        has_aoe = self._has_aoe_weapon(player)

        if has_aoe:
            pc = self._police_cache or {}
            for unit in pc.get("units", []):
                if unit.get("is_alive") and unit.get("location"):
                    unit_loc = unit["location"]
                    aoe_name = self._get_aoe_weapon_name(player)
                    if aoe_name:
                        # 检查是否需要蓄力
                        aoe_w = next((w for w in player.weapons
                                    if w and w.name == aoe_name), None)
                        if (aoe_w
                                and getattr(aoe_w, 'requires_charge', False)
                                and not getattr(aoe_w, 'is_charged', False)):
                            if "special" in available:
                                commands.append(f"special 蓄力{aoe_name}")
                            return commands

                        if loc == unit_loc:
                            commands.append(f"attack {unit['id']} {aoe_name}")
                        else:
                            commands.append(f"move {unit_loc}")
                    return commands
        else:
            # 没有AOE武器，去拿一个
            # 优先去人少的地方：魔法所（地震）或军事基地（电磁步枪）
            enemies_magic = self._count_enemies_at("魔法所", player, state)
            enemies_military = self._count_enemies_at("军事基地", player, state)

            if enemies_magic <= enemies_military:
                if loc == "魔法所" and "interact" in available:
                    learned = self._get_learned_spells(player)
                    if "地震" in learned and "地动山摇" not in learned:
                        commands.append("interact 地动山摇")
                    elif "地震" not in learned:
                        commands.append("interact 地震")
                else:
                    commands.append("move 魔法所")
            else:
                if loc == "军事基地" and "interact" in available:
                    commands.append("interact 电磁步枪")
                else:
                    commands.append("move 军事基地")
        return commands

    def _has_aoe_weapon(self, player) -> bool:
        for w in getattr(player, 'weapons', []):
            name = w.name if hasattr(w, 'name') else str(w)
            if name in POLICE_AOE_WEAPONS:
                return True
        learned = getattr(player, 'learned_spells', set())
        if "地震" in learned or "地动山摇" in learned:
            return True
        return False

    def _get_aoe_weapon_name(self, player) -> Optional[str]:
        for w in getattr(player, 'weapons', []):
            name = w.name if hasattr(w, 'name') else str(w)
            if name in POLICE_AOE_WEAPONS:
                return name
        learned = getattr(player, 'learned_spells', set())
        if "地动山摇" in learned:
            return "地动山摇"
        if "地震" in learned:
            return "地震"
        return None

    def _armor_counters_weapon(self, player, weapon_name) -> bool:
        """检查玩家的护甲是否克制指定武器"""
        from utils.attribute import Attribute, is_effective
        from models.equipment import make_weapon, ArmorLayer
        w = make_weapon(weapon_name)
        if not w:
            return False
        armor = getattr(player, 'armor', None)
        if not armor or not hasattr(armor, 'get_active'):
            return False
        for piece in armor.get_active(ArmorLayer.OUTER):
            if hasattr(piece, 'attribute') and not piece.is_broken:
                if not is_effective(w.attribute, piece.attribute):
                    return True
        return False

    # ════════════════════════════════════════════════════════
    #  命令格式化与验证
    # ════════════════════════════════════════════════════════

    def _format_command(self, raw_cmd: str) -> str:
        """格式化命令"""
        return raw_cmd.strip()

    def _validate_command(self, cmd: str, available: List[str]) -> bool:
        """验证命令是否合法"""
        if not cmd:
            return False
        parts = cmd.split()
        if not parts:
            return False

        action = parts[0]
        # 检查行动类型是否可用
        if action in available:
            return True
        # 特殊命令
        if action in ("police", "talent_activate", "special"):
            return True
        return False

    def _fallback_command(self, player, state, available: List[str]) -> str:
        """
        Bug15修复：所有策略都失败时的兜底命令
        优先选择安全的行动
        """
        debug_ai_basic(player.name, "使用兜底命令")

        # 1) forfeit（放弃行动）永远合法
        if "forfeit" in available:
            return "forfeit"

        # 2) 移动到安全位置
        if "move" in available:
            safe = self._find_safe_location(player, state)
            if safe:
                return f"move {safe}"
            return "move home"

        # 3) 起床
        if "wake" in available:
            return "wake"

        # 4) 交互
        if "interact" in available:
            return "interact"

        # 5) 实在没办法
        return "forfeit"

    # ════════════════════════════════════════════════════════
    #  调试输出
    # ════════════════════════════════════════════════════════

    def get_debug_info(self, player) -> dict:
        """获取AI调试信息"""
        # _is_development_complete 需要 state，这里用缓存的 _game_state
        dev_complete = False
        if player and self._game_state:
            dev_complete = self._is_development_complete(player, self._game_state)

        return {
            "personality": self.personality,
            "phase": self._current_phase,
            "round": self._round_number,
            "threat_scores": dict(self._threat_scores),
            "in_combat": self._in_combat,
            "combat_target": self._combat_target,
            "been_attacked_by": list(self._been_attacked_by),
            "virus_active": getattr(self, '_virus_active', False),
            "last_commands": self._last_commands[:],
            "police_cache": self._police_cache,
            "development_complete": dev_complete,}

    def __repr__(self) -> str:
        return (f"BasicAIController(personality={self.personality}, "
                f"phase={self._current_phase}, round={self._round_number})")


# ════════════════════════════════════════════════════════════════
#  属性克制常量表
# ════════════════════════════════════════════════════════════════





# ════════════════════════════════════════════════════════════════
#  工厂函数
# ════════════════════════════════════════════════════════════════

def create_ai_controller(personality: str = "balanced",
                         player_name: str = "",
                         **kwargs) -> BasicAIController:
    """
    创建AI控制器的工厂函数

    参数:
        personality: AI人格类型
            - "aggressive": 激进型，优先攻击
            - "defensive": 防御型，优先发育和防守
            - "assassin": 刺客型，隐身突袭
            - "balanced": 均衡型，攻守兼备
            - "builder": 建设型，追求全面发育
            - "political": 政治型，利用警察系统
        player_name: 玩家名称（用于调试）
        **kwargs: 其他参数

    返回:
        BasicAIController 实例
    """
    valid_personalities = [
        "aggressive", "defensive", "assassin",
        "balanced", "builder", "political"
    ]
    if personality not in valid_personalities:
        debug_ai_basic(player_name,
            f"未知人格类型 '{personality}'，使用 'balanced'")
        personality = "balanced"

    controller = BasicAIController(personality=personality)
    debug_ai_basic(player_name,
        f"创建AI控制器: personality={personality}")
    return controller


def create_random_ai_controller(player_name: str = "",
                                 **kwargs) -> BasicAIController:
    """创建随机人格的AI控制器"""
    import random
    personalities = [
        "aggressive", "defensive", "assassin",
        "balanced", "builder", "political"
    ]
    # 权重：balanced和aggressive更常见
    weights = [25, 15, 15, 30, 10, 5]
    personality = random.choices(personalities, weights=weights, k=1)[0]
    return create_ai_controller(personality=personality,
                                player_name=player_name, **kwargs)
