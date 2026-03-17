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
from utils.attribute import Attribute
from typing import List, Dict, Optional, Any, Set, Tuple
from controllers.base import PlayerController
import random

EQUIPMENT_LOCATION = {  
    "警棍": {"警察局"},  
    "高斯步枪": {"军事基地"},  
    "地震": {"魔法所"},  
    "地动山摇": {"魔法所"},  
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
            return options[0]
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
                    if target and self._same_location(self._player, target) and target.hp >= 2.0:  
                        for opt in options:  
                            if "发动" in opt:  
                                return opt  
                for opt in options:  
                    if "不发动" in opt or "正常" in opt:  
                        return opt  
                return options[-1]  
              
            # 请一直，注视着我（全息影像）：被攻击或同地点有多个敌人时发动  
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
              
            # 天星/六爻/往世的涟漪：默认发动（get_t0_option已做前置检查）  
            for opt in options:  
                if "发动" in opt:  
                    return opt  
            return options[0]

        # ---- 加入警察 ----
        if situation in ("recruit_pick_1", "recruit_pick_2"):
            if self.personality == "aggressive":
                priority = ["警棍", "盾牌", "凭证"]
            elif self.personality == "defensive":
                priority = ["盾牌", "警棍", "凭证"]
            elif self.personality == "political":
                priority = ["凭证", "警棍", "盾牌"]
            else:
                priority = ["盾牌", "凭证", "警棍"]
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
            for opt in options:
                if "锚定" in opt:
                    return opt
            return options[0]
        if situation == "resurrection_pick_target":
            return options[0]
        if situation == "ripple_anchor_type":
            for opt in options:
                if "击杀" in opt:
                    return opt
            return options[0]
        if situation in ("ripple_anchor_kill_target", "ripple_anchor_armor_target", "ripple_poem_target"):
            player_opts = [o for o in options if o != "取消"]
            if player_opts:
                return max(player_opts, key=lambda name: self._threat_scores.get(name, 0))
            return options[0]
        if situation == "ripple_anchor_armor_pick":
            non_cancel = [o for o in options if o != "取消"]
            return non_cancel[0] if non_cancel else options[0]
        if situation == "ripple_anchor_acquire_item":
            priority = ["高斯步枪", "AT力场", "导弹", "远程魔法弹幕", "陶瓷护甲", "魔法护盾", "电磁步枪"]
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
        if not context:
            return False
        situation = context.get("phase", "")
        if situation == "response_window":
            talent_name = context.get("talent_name", "")
            action_type = context.get("action_type", "")
            # 你给路打油：在被攻击/特殊操作时确认（对自己有利）
        if talent_name == "你给路打油" and action_type in ("attack", "special"):  
                # 只在真正危险时触发，保留使用次数  
                if self._player:  
                    hp = self._player.hp  
                    outer = self._count_outer_armor(self._player)  
                    if hp <= 1.0:  
                        return True  
                    if outer == 0 and hp <= 1.5:  
                        return True  
                    return False  
                return True  # 无法判断，保守触发
            # 其他天赋的响应窗口，默认不确认（防止对自己不利）
        return False

    # ════════════════════════════════════════════════════════
    #  接口实现：on_event
    # ════════════════════════════════════════════════════════

    def on_event(self, event: Dict) -> None:
        self.event_log.append(event)
        event_type = event.get("type", "")
        target = event.get("target")
        attacker = event.get("attacker", "")
        if event_type == "attack" and self.player_name is not None:
            if target == self.player_name:
                self._been_attacked_by.add(attacker)
                self._threat_scores[attacker] = self._threat_scores.get(attacker, 0) + 20
        if event_type == "death":
            killer = event.get("killer", "")
            if killer:
                self._threat_scores[killer] = self._threat_scores.get(killer, 0) + 30

    # ════════════════════════════════════════════════════════
    #  核心：候选命令生成
    # ════════════════════════════════════════════════════════

    def _generate_candidates(self, player, state, available_actions: List[str]) -> List[str]:  
        self._my_id = player.player_id  
        self.player_name = player.name  
        self._player = player  
        self._game_state = state  
    
        # 只在正常 T1 阶段递增轮次（避免特殊调用路径重复递增）  
        # 通过检查 state.current_round 来同步，而非自增  
        current_round = getattr(state, 'current_round', 0)  
        if current_round > self._round_number:  
            self._round_number = current_round  


        self._update_threat_scores(player, state)
        self._read_police_state(state)
        self._update_combat_status(player, state)
        self._cleanup_dead_players(state)  # Bug18

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

        # ===== 极危险情况 =====
        if self._is_critical(player, state):
            debug_ai_basic(player.name, "进入极危模式")
            candidates.extend(self._cmd_survival(player, state, available_actions))
            if candidates:
                candidates.append("forfeit")
                return candidates

        # ===== 病毒应急 =====
        if self._needs_virus_cure(player, state):
            debug_ai_basic(player.name, "进入病毒应急模式")
            candidates.extend(self._cmd_virus(player, state, available_actions))
            if candidates:
                candidates.append("forfeit")
                return candidates

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
                self._combat_target = None

        # ===== 击杀机会 =====
        if self._has_kill_opportunity(player, state):
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

        # ===== 发育完成后主动进攻 =====
        if self._is_development_complete(player, state):
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
        if "attack" in available_actions or "find" in available_actions or "lock" in available_actions:
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
                "is_alive": alive,
                "is_active": active,
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

    def _is_critical(self, player, state) -> bool:  
        if player.hp <= 0.5:  
            return True  
        if player.hp <= 1.0 and self._count_outer_armor(player) == 0:  
            return True
        # 被警察围攻（Bug修复：只检查 "dispatched"，不检查不存在的 "enforcing"）
        pc = self._police_cache or {}
        if pc.get("report_target") == player.player_id:
            phase = pc.get("report_phase", "idle")
            if phase == "dispatched":
                return True
        # 被多人锁定
        locked_count = self._count_locked_by(player, state)  
        if locked_count >= 2:  
            return True  
        if locked_count >= 1 and player.hp <= 1.0:  
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

    def _should_continue_combat(self, player, target) -> bool:
        if not target or not target.is_alive():
            return False
        if player.hp <= 0.5 and self.personality != "aggressive":
            return False
        if self._is_at_disadvantage(player, target) and self.personality == "defensive":
            return False
        return True

    def _is_development_complete(self, player, state) -> bool:  
        """判断发育是否完成"""  
        real_weapons = [w for w in player.weapons if w and w.name != "拳击"]  
        has_real_weapon = len(real_weapons) > 0  
    
        if self.personality == "aggressive":  
            has_armor = self._count_outer_armor(player) > 0  
            return has_real_weapon and has_armor  
    
        elif self.personality == "defensive":  
            has_armor = self._count_outer_armor(player) >= 2  
            has_inner = self._count_inner_armor(player) >= 1  
            return has_real_weapon and has_armor and has_inner  
    
        elif self.personality == "assassin":  
            return has_real_weapon and self._has_stealth(player)  
    
        elif self.personality == "builder":  
            has_armor = self._count_outer_armor(player) >= 2  
            has_inner = self._count_inner_armor(player) >= 1  
            has_pass = getattr(player, 'has_military_pass', False)  
            return has_real_weapon and has_armor and has_inner and has_pass  
    
        elif self.personality == "political":  
            is_police = getattr(player, 'is_police', False)  
            return has_real_weapon and is_police  
    
        else:  # balanced  
            has_armor = self._count_outer_armor(player) >= 1  
            return has_real_weapon and has_armor

    # ════════════════════════════════════════════════════════
    #  命令生成器：起床
    # ════════════════════════════════════════════════════════

    def _cmd_wake(self) -> List[str]:
        return ["wake"]

    # ════════════════════════════════════════════════════════
    #  命令生成器：发育
    # ════════════════════════════════════════════════════════

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

        # 替换为简单状态日志：  
        debug_ai_development_plan(player.name,  
            f"状态: loc={loc} vouchers={vouchers} weapon={has_weapon} "  
            f"outer={outer} inner={inner} pass={has_pass} detect={has_detection}")

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
                if vouchers >= 2 and not has_detection:
                    commands.append("interact 热成像仪")
                if vouchers >= 1 and outer < 2 and not self._has_armor_by_name(player, "陶瓷护甲"):
                    commands.append("interact 陶瓷护甲")
                if self.personality == "assassin" and vouchers >= 2:
                    commands.append("interact 隐身衣")
                if has_weapon and self._has_melee_only(player):
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
                    if "封闭" not in learned:
                        commands.append("interact 封闭")


            elif loc == "医院":
                if inner == 0:
                    if self.personality == "builder":
                        commands.append("interact 晶化皮肤手术")
                        commands.append("interact 额外心脏手术")
                    else:
                        commands.append("interact 晶化皮肤手术")
                elif inner < 2 and self.personality in ("builder", "defensive"):
                    commands.append("interact 额外心脏手术")
                if not self._has_virus_immunity(player):
                    commands.append("interact 防毒面具")
                if vouchers < 1:
                    commands.append("interact 打工")

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
                    if not getattr(player, 'is_police', False):
                        if "recruit" in available:
                            commands.append("recruit")
                    elif getattr(player, 'is_police', False) and not getattr(player, 'is_captain', False):
                        if "election" in available:
                            commands.append("election")

        # ---- 移动到目标地点 ----
        if "move" in available and not commands:
            next_loc = self._pick_develop_destination(player, state)
            if next_loc and next_loc != loc:
                commands.append(f"move {next_loc}")

        return commands

    def _pick_develop_destination(self, player, state) -> Optional[str]:  
        """选择下一个发育目标地点（考虑敌人位置）"""  
        ideal = self._pick_ideal_destination(player, state)  
        if ideal is None:  
            return None  
    
        # 进攻型人格不做安全过滤  
        if self.personality in ("aggressive", "assassin"):  
            return ideal  
    
        # 非进攻型：检查目标地点敌人数量  
        enemies = self._count_enemies_at(ideal, player, state)  
        if enemies >= 2:  
            alt = self._find_safer_alternative(ideal, player, state)  
            if alt is not None:  
                return alt  
        return ideal  
    
    def _pick_ideal_destination(self, player, state) -> Optional[str]:  
        """纯需求驱动的目标地点选择（不考虑敌人）"""  
        weapons = getattr(player, 'weapons', [])  
        has_weapon = any(w for w in weapons if w and getattr(w, 'name', '') != "拳击")  
        outer = self._count_outer_armor(player)  
        inner = self._count_inner_armor(player)  
        vouchers = getattr(player, 'vouchers', 0)  
        has_pass = getattr(player, 'has_military_pass', False)  
        has_detection = getattr(player, 'has_detection', False)  
        loc = self._get_location_str(player)  
    
        if self.personality == "aggressive":  
            if vouchers < 1 and loc != "home":  
                return "home"  
            if not has_weapon and loc != "商店":  
                return "商店"  
            if outer < 1 and loc != "home":  
                return "home"  
            return self._find_nearest_enemy_location(player, state)  
    
        elif self.personality == "defensive":  
            if vouchers < 1 and loc != "home":  
                return "home"  
            if outer < 1 and loc != "home":  
                return "home"  
            if outer < 2 and loc != "商店":  
                return "商店"  
            if not has_weapon and loc != "商店":  
                return "商店"  
            if not has_detection and loc != "商店":  
                return "商店"  
            if inner < 1 and loc != "医院":  
                return "医院"  
            return None  
    
        elif self.personality == "assassin":  
            if vouchers < 1 and loc != "home":  
                return "home"  
            if not has_weapon and loc != "商店":  
                return "商店"  
            if not self._has_stealth(player) and loc != "商店":  
                return "商店"  
            return self._find_nearest_enemy_location(player, state)  
    
        elif self.personality == "political":  
            if vouchers < 1 and loc != "home":  
                return "home"  
            if not has_weapon and loc != "home":  
                return "home"  
            if not getattr(player, 'is_police', False) and loc != "警察局":  
                return "警察局"  
            if getattr(player, 'is_police', False):  
                return "警察局"  
            return "商店"  
    
        elif self.personality == "builder":  
            if vouchers < 1 and loc != "home":  
                return "home"  
            if outer < 1 and loc != "home":  
                return "home"  
            if not has_weapon and loc != "商店":  
                return "商店"  
            if outer < 2 and loc != "商店":  
                return "商店"  
            if inner < 1 and loc != "医院":  
                return "医院"  
            if not has_pass and loc != "军事基地":  
                return "军事基地"  
            if has_pass and loc != "军事基地":  
                return "军事基地"  
            return None  
    
        else:  # balanced  
            if vouchers < 1 and loc != "home":  
                return "home"  
            if outer < 1 and loc != "home":  
                return "home"  
            if not has_weapon and loc != "商店":  
                return "商店"  
            if not has_detection and vouchers >= 2 and loc != "商店":  
                return "商店"  
            if outer < 2 and loc != "商店":  
                return "商店"  
            return self._find_nearest_enemy_location(player, state)  
    
    def _count_enemies_at(self, location: str, player, state) -> int:  
        """统计某地点的存活敌人数量"""  
        count = 0  
        for pid in state.player_order:  
            if pid == player.player_id:  
                continue  
            target = state.get_player(pid)  
            if target and target.is_alive():  
                if self._get_location_str(target) == location:  
                    count += 1  
        return count  
    
    def _find_safer_alternative(self, original: str, player, state) -> Optional[str]:  
        """为非进攻型人格寻找更安全的替代发育地点  
        
        替代逻辑：  
        - 商店 ↔ 魔法所（都能获得武器、护甲、探测）  
        - 医院 → 无直接替代，但可以先去商店/魔法所做其他发育  
        - home → 不替代（每个人的家是独立的，一般不会有敌人）  
        """  
        # 替代映射：原目标 → 可替代地点列表  
        alternatives_map = {  
            "商店": ["魔法所"],  
            "魔法所": ["商店"],  
            "医院": ["商店", "魔法所"],  
            "军事基地": ["商店", "魔法所"],  
        }  
        candidates = alternatives_map.get(original, [])  
        loc = self._get_location_str(player)  
    
        best = None  
        best_enemies = 999  
        for alt_loc in candidates:  
            if alt_loc == loc:  
                continue  
            enemies = self._count_enemies_at(alt_loc, player, state)  
            if enemies < best_enemies:  
                best_enemies = enemies  
                best = alt_loc  
    
        # 只有替代地点确实更安全时才替代  
        original_enemies = self._count_enemies_at(original, player, state)  
        if best is not None and best_enemies < original_enemies:  
            return best  
        return None

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
            # 没武器，尝试近战（拳头？）
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

        if weapon_range == "melee":
            # 近战：需要先 find（建立ENGAGED_WITH）
            is_engaged = False
            if markers and hasattr(markers, 'has_relation'):
                is_engaged = markers.has_relation(
                    player.player_id, "ENGAGED_WITH", target.player_id)

            if not is_engaged:
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
                    player.player_id, "LOCKED_BY", target.player_id)

            if not is_locked:
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
            # 区域武器：需要同地点有目标
            if "attack" in available:
                same_loc_targets = self._get_same_location_targets(player, state)
                if same_loc_targets:
                    layer, attr = self._pick_attack_layer(player, target, weapon)  
                    if layer and attr:  
                        commands.append(f"attack {target.name} {weapon.name} {layer} {attr}")  
                    else:  
                        commands.append(f"attack {target.name} {weapon.name}")
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

    # ════════════════════════════════════════════════════════
    #  命令生成器：队长指挥（Bug8修复）
    # ════════════════════════════════════════════════════════

    def _cmd_captain(self, player, state, available: List[str]) -> List[str]:
        """
        Bug8修复：检查 "police_command" in available（不是 "police attack"）
        生成格式："police move police1 商店" 等
        """
        commands = []

        if "police_command" not in available:
            return commands

        pc = self._police_cache or {}
        if not pc.get("is_captain"):
            return commands

        units = pc.get("units", [])  
        active_units = [u for u in units if u.get("is_alive") and u.get("is_active", True)]  
        alive_units = [u for u in units if u.get("is_alive")]

        if not alive_units:
            debug_ai_basic(player.name, "无存活警察单位，跳过指挥")
            return commands
        # 2.5) 威信低时优先研究性学习  
        authority = pc.get("authority", 0)  
        if authority <= 2 and "study" in available:  
            loc = self._get_location_str(player)  
            if loc == "警察局":  
                return ["study"]  
            else:  
                return [f"move 警察局"]

        # 策略：根据情况指挥
        # 1) 如果有举报目标且已派遣，指挥攻击
        report_target = pc.get("report_target")  
        if report_target and pc.get("report_phase") == "dispatched":  
            target_player = state.get_player(report_target)  
            if not target_player or not target_player.is_alive():  
                # Target died — look for new criminal targets instead of being stuck  
                pass  # Fall through to criminal search below  
            elif target_player.is_alive(): 
                # 威信检查：不攻击无辜者（威信低时）  
                authority = pc.get("authority", 0)  
                target_is_criminal = getattr(target_player, 'is_criminal', False)  
                if not target_is_criminal:  
                    police_obj = getattr(state, 'police', None)  
                    if police_obj and hasattr(police_obj, 'is_criminal'):  
                        target_is_criminal = police_obj.is_criminal(target_player.player_id)  
                if not target_is_criminal and authority <= 1:  
                    loc = self._get_location_str(player)  
                    if loc == "警察局" and "study" in available:  
                        return ["study"]  
                    elif loc != "警察局":  
                        return [f"move 警察局"] 
                # existing attack logic, but only for ONE unit 
                target_loc = self._get_location_str(target_player)
                for unit in active_units:  
                    uid = unit["id"]  
                    unit_loc = unit.get("location")  
                    if unit_loc != target_loc:  
                        return [f"police move {uid} {target_loc}"]  
                    else:  
                        return [f"police attack {uid} {target_player.name}"]
                if commands:
                    return commands

        # 2) 如果没有目标，巡逻或寻找犯罪者
        criminal_players = []
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            p = state.get_player(pid)
            if p and p.is_alive():
                if getattr(p, 'is_criminal', False):
                    criminal_players.append(p)

        if criminal_players:
            primary = max(criminal_players,
                         key=lambda p: self._threat_scores.get(p.name, 0))
            target_loc = self._get_location_str(primary)
            for unit in alive_units:
                uid = unit["id"]
                unit_loc = unit.get("location")
                if unit_loc != target_loc:
                    commands.append(f"police move {uid} {target_loc}")
                else:
                    commands.append(f"police attack {uid} {primary.name}")
            if commands:
                return commands

        # 3) 给警察装备
        EQUIP_LOCATION_MAP = {  
            "高斯步枪": "军事基地",  
            "地震": "魔法所",  
            "地动山摇": "魔法所",  
        }  
        for unit in active_units:  
            uid = unit["id"]  
            current_weapon = unit.get("weapon", "警棍")  
            if current_weapon == "警棍":  
                target_equip = "高斯步枪"  
                required_loc = EQUIP_LOCATION_MAP.get(target_equip, "军事基地")  
                unit_loc = unit.get("location")  
                if unit_loc != required_loc:  
                    return [f"police move {uid} {required_loc}"]  
                else:  
                    return [f"police equip {uid} {target_equip}"]

        # 4) 分散巡逻
        if not commands:
            patrol_locs = ["商店", "医院", "魔法所", "军事基地"]
            for i, unit in enumerate(alive_units):
                uid = unit["id"]
                target_loc = patrol_locs[i % len(patrol_locs)]
                if unit.get("location") != target_loc:
                    commands.append(f"police move {uid} {target_loc}")

        return commands

    # ════════════════════════════════════════════════════════
    #  命令生成器：政治行动
    # ════════════════════════════════════════════════════════

    def _cmd_police_political(self, player, state, available: List[str]) -> List[str]:
        commands = []
        loc = self._get_location_str(player)
        is_police = getattr(player, 'is_police', False)
        is_captain = getattr(player, 'is_captain', False)

# 举报犯罪者（需要在警察局，除非有远程举报天赋）  
        if "report" in available and is_police:  
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
                        # Bug修复：使用 player.is_criminal 和 police.is_criminal()
                        target_is_criminal = getattr(target, 'is_criminal', False)
                        if not target_is_criminal:
                            police = getattr(state, 'police', None)
                            if police and hasattr(police, 'is_criminal'):
                                target_is_criminal = police.is_criminal(target.player_id)
                        if target_is_criminal:
                            commands.append(f"report {target.name}")
                            break

        # 加入警察
        if "recruit" in available and not is_police and loc == "警察局":
            commands.append("recruit")

        # 竞选队长
        if "election" in available and is_police and not is_captain and loc == "警察局":
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
        if "move" in available and not commands and loc != "警察局":
            if not is_police or (is_police and not is_captain):
                commands.append("move 警察局")

        return commands

    # ════════════════════════════════════════════════════════
    #  命令生成器：生存
    # ════════════════════════════════════════════════════════

    def _cmd_survival(self, player, state, available: List[str]) -> List[str]:  
        commands = []  
        loc = self._get_location_str(player)  
    
        # 1) 当前地点有免费防御物品时顺手拿  
        if "interact" in available:  
            if loc in ("医院", "商店") and not self._has_virus_immunity(player):  
                commands.append("interact 防毒面具")  
    
        # 2) aggressive 人格：反击  
        if self.personality == "aggressive":  
            aggressive_cmds = self._aggressive_survival_strategy(player, state, available)  
            commands.extend(aggressive_cmds)  
    
        # 3) 逃跑到安全地点  
        if "move" in available:  
            safe_loc = self._find_safe_location(player, state)  
            if safe_loc and safe_loc != loc:  
                commands.append(f"move {safe_loc}")  
    
        return commands

    def _aggressive_survival_strategy(self, player, state, available: List[str]) -> List[str]:
        """Bug14修复：检查 target is None"""
        commands = []
        target = self._pick_target(player, state)
        if target is None:
            return commands

        # 在同一地点，反击
        if self._same_location(player, target):
            attack_cmds = self._cmd_attack(player, state, available, target)
            commands.extend(attack_cmds)
        else:
            # 去找目标
            target_loc = self._get_location_str(target)
            if target_loc and "move" in available:
                commands.append(f"move {target_loc}")

        return commands

    # ════════════════════════════════════════════════════════
    #  命令生成器：病毒应急（Bug16修复：接收 available_actions）
    # ════════════════════════════════════════════════════════

    def _cmd_virus(self, player, state, available: List[str]) -> List[str]:
        commands = []
        loc = self._get_location_str(player)

        # 买防毒面具
        if loc == "商店" and "interact" in available:
            commands.append("interact 防毒面具")
        elif loc == "医院" and "interact" in available:
            commands.append("interact 防毒面具")
        elif "move" in available:
            commands.append("move 商店")

        return commands

    # ════════════════════════════════════════════════════════
    #  目标选择
    # ════════════════════════════════════════════════════════
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

    def _best_weapon_damage(self, player) -> float:
        """获取玩家最强武器的伤害值"""
        weapons = getattr(player, 'weapons', [])
        if not weapons:
            return 0.0
        best =0.0
        for w in weapons:
            dmg = self._get_weapon_damage(w)
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
        def score(t):
            s = 0
            s += self._threat_scores.get(t.name, 0) * 2
            if t.name in self._been_attacked_by:
                s += 50  # 优先反击
            if self._same_location(player, t):
                s += 30
            # 低血优先
            s += max(0, 5 - t.hp) * 10
            # 护甲少优先
            s -= self._count_outer_armor(t) * 15
            s -= self._count_inner_armor(t) * 10
            # assassin偏好低血目标
            if self.personality == "assassin":
                s += max(0, 3 - t.hp) * 20
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
    
        target_outer_attrs = self._get_outer_armor_attr(target)  # List[Attribute]  
    
        def weapon_score(w):  
            s = 0  
            dmg = self._get_weapon_damage(w)  
            s += dmg * 10  
    
            # 属性克制：检查武器属性是否能有效打击目标的任一外甲  
            w_attr = self._get_weapon_attr(w)  
            if target_outer_attrs and w_attr in EFFECTIVE_AGAINST:  
                effective_set = EFFECTIVE_AGAINST[w_attr]  
                for armor_attr in target_outer_attrs:  
                    if armor_attr in effective_set:  
                        s += 20  
                        break  
    
            # 射程适配  
            wr = self._get_weapon_range(w)  
            if self._same_location(player, target):  
                if wr == "melee":  
                    s += 10  
            else:  
                if wr == "ranged":  
                    s += 15  
                elif wr == "melee":  
                    s -= 20  
    
            return s  
    
        # 使用 weapon_score 排序  
        sorted_weapons = sorted(weapons, key=weapon_score, reverse=True)  
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
    #  天赋相关决策
    # ════════════════════════════════════════════════════════

    def _cmd_talent(self, player, state, available: List[str]) -> List[str]:
        """天赋相关的行动命令"""
        commands = []
        talent = getattr(player, 'talent', None)
        if not talent:
            return commands

        talent_name = getattr(talent, 'name', '')

        # 各天赋的特殊行动
        if talent_name == "一刀缭断":
            cmds = self._talent_one_slash(player, state, available)
            commands.extend(cmds)
        elif talent_name == "你给路打油":
            cmds = self._talent_oil(player, state, available)
            commands.extend(cmds)
        elif talent_name == "天星":
            cmds = self._talent_star(player, state, available)
            commands.extend(cmds)
        elif talent_name == "六爻":
            cmds = self._talent_hexagram(player, state, available)
            commands.extend(cmds)
        elif talent_name == "不良少年":
            cmds = self._talent_delinquent(player, state, available)
            commands.extend(cmds)
        elif talent_name == "朝阳好市民":
            cmds = self._talent_good_citizen(player, state, available)
            commands.extend(cmds)
        elif talent_name == "死者苏生":
            cmds = self._talent_resurrection(player, state, available)
            commands.extend(cmds)
        elif talent_name == "火萤IV型-完全燃烧":
            cmds = self._talent_firefly(player, state, available)
            commands.extend(cmds)
        elif talent_name == "请一直，注视着我":
            cmds = self._talent_hologram(player, state, available)
            commands.extend(cmds)
        elif talent_name == "遗世独立的幻想乡":
            cmds = self._talent_mythland(player, state, available)
            commands.extend(cmds)
        elif talent_name == "愿负世，照拂黎明":
            cmds = self._talent_savior(player, state, available)
            commands.extend(cmds)
        elif talent_name == "往世的涟漪":
            cmds = self._talent_ripple(player, state, available)
            commands.extend(cmds)

        return commands

    def _talent_one_slash(self, player, state, available) -> List[str]:
        """一刀缭断：强化近战攻击"""
        commands = []
        if "talent_activate" in available:
            target = self._pick_target(player, state)
            if target and self._same_location(player, target):
                # 判断是否值得使用天赋（目标HP较高时使用）
                if target.hp >= 2.0:
                    commands.append(f"talent_activate {target.name}")
        return commands

    def _talent_oil(self, player, state, available) -> List[str]:
        """你给路打油：设置陷阱"""
        commands = []
        if "talent_activate" in available:
            # 在关键地点设置陷阱
            loc = self._get_location_str(player)
            high_traffic = ["商店", "医院", "魔法所"]
            if loc in high_traffic:
                commands.append("talent_activate")
        return commands

    def _talent_star(self, player, state, available) -> List[str]:
        """天星：远程攻击强化"""
        commands = []
        if "talent_activate" in available:
            target = self._pick_target(player, state)
            if target:
                commands.append(f"talent_activate {target.name}")
        return commands

    def _talent_hexagram(self, player, state, available) -> List[str]:
        """六爻：预知/占卜"""
        commands = []
        if "talent_activate" in available:
            # 在需要情报时使用
            if self._round_number <= 3:
                commands.append("talent_activate")
        return commands

    def _talent_delinquent(self, player, state, available) -> List[str]:
        """不良少年：战斗强化"""
        commands = []
        if "talent_activate" in available:
            target = self._pick_target(player, state)
            if target and self._same_location(player, target):
                commands.append(f"talent_activate {target.name}")
        return commands

    def _talent_good_citizen(self, player, state, available) -> List[str]:
        """朝阳好市民：政治强化"""
        commands = []
        if "talent_activate" in available:
            if getattr(player, 'is_police', False):
                commands.append("talent_activate")
        return commands

    def _talent_resurrection(self, player, state, available) -> List[str]:
        """死者苏生：复活能力"""
        commands = []
        if "talent_activate" in available:
            # 检查是否有已死亡的友方
            for pid in state.player_order:
                if pid == player.player_id:
                    continue
                target = state.get_player(pid)
                if target and not target.is_alive():
                    # 可以复活
                    commands.append(f"talent_activate {target.name}")
                    break
        return commands

    def _talent_firefly(self, player, state, available) -> List[str]:
        """火萤IV型-完全燃烧：自爆/强化攻击"""
        commands = []
        if "talent_activate" in available:
            # 低血量时使用（同归于尽策略）
            if player.hp <= 1.0:
                targets_nearby = self._get_same_location_targets(player, state)
                if targets_nearby:
                    commands.append("talent_activate")
        return commands

    def _talent_hologram(self, player, state, available) -> List[str]:
        """请一直，注视着我：分身/嘲讽"""
        commands = []
        if "talent_activate" in available:
            # 被多人攻击时使用
            attackers = len(self._been_attacked_by)
            if attackers >= 2:
                commands.append("talent_activate")
        return commands

    def _talent_mythland(self, player, state, available) -> List[str]:
        """遗世独立的幻想乡：创建领域"""
        commands = []
        if "talent_activate" in available:
            # 在发育完成后使用
            if self._is_development_complete(player, state):
                commands.append("talent_activate")
        return commands

    def _talent_savior(self, player, state, available) -> List[str]:
        """愿负世，照拂黎明：保护/治疗他人"""
        commands = []
        if "talent_activate" in available:
            # 找到低血量的友方
            for pid in state.player_order:
                if pid == player.player_id:
                    continue
                target = state.get_player(pid)
                if target and target.is_alive() and target.hp <= 1.5:
                    if self._same_location(player, target):
                        commands.append(f"talent_activate {target.name}")
                        break
        return commands

    def _talent_ripple(self, player, state, available) -> List[str]:
        """往世的涟漪：时间回溯"""
        commands = []
        if "talent_activate" in available:
            # 在关键时刻使用（自己或目标即将死亡）
            if player.hp <= 0.5:
                commands.append("talent_activate self")
            else:
                # 检查是否有刚死亡的敌人可以回溯
                target = self._pick_target(player, state)
                if target and target.hp <= 0.5:
                    commands.append(f"talent_activate {target.name}")
        return commands

    # ════════════════════════════════════════════════════════
    #  辅助方法：装备计数与查询
    # ════════════════════════════════════════════════════════

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
        """找到最近的敌人所在位置"""
        candidates = []
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if target and target.is_alive():
                target_loc = self._get_location_str(target)
                threat = self._threat_scores.get(target.name, 0)
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
            power += self._get_weapon_damage(w) * 15

        outer = self._count_outer_armor(player)
        inner = self._count_inner_armor(player)
        power += outer * 20
        power += inner * 15

        if self._has_stealth(player):
            power += 10

        if getattr(player, 'has_detection', False):
            power += 5

        return power

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

    def _update_caches(self, player, state):
        """更新缓存信息"""
        # 更新警察缓存
        police = getattr(state, 'police', None)
        if police:
            is_police = getattr(player, 'is_police', False)
            is_captain = getattr(player, 'is_captain', False)
            units = []
            if hasattr(police, 'units'):
                for u in police.units:  
                    units.append({  
                        "id": getattr(u, 'unit_id', ''),           # was 'id'  
                        "is_alive": u.is_alive() if callable(getattr(u, 'is_alive', None)) else True,  # was getattr(u, 'is_alive', True)  
                        "location": self._get_location_str(u) if hasattr(u, 'location') else None,  
                        "weapon": getattr(u, 'weapon_name', '警棍'),  # was 'weapon'  
                    })  
            report_target = getattr(police, 'reported_target_id', None)  # was 'current_report_target'
            report_phase = getattr(police, 'report_phase', None)
            self._police_cache = {
                "is_police": is_police,
                "is_captain": is_captain,
                "units": units,
                "report_target": report_target,
                "report_phase": report_phase,
            }
        else:
            self._police_cache = {}

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
