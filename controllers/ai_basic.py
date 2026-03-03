"""
BasicAIController —— 基础AI控制器（修复版：战斗指令生成）
═══════════════════════════════════════════════════
修复致命问题：AI能识别击杀机会和进入战斗模式，但无法生成进攻候选指令

问题分析：
1. _pick_best_weapon_against 可能返回None，导致攻击命令生成失败
2. _pick_attack_layer 对无护甲目标处理不当
3. 战斗状态下的移动/攻击优先级逻辑有问题
4. 武器属性检测不完整
"""

from typing import List, Optional, Dict, Any
from controllers.base import PlayerController
import random

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
#  常量：地点名、交互项目、克制关系
# ════════════════════════════════════════════════════════

LOCATIONS = ["home", "商店", "魔法所", "医院", "军事基地", "警察局"]

# 各地点可交互的项目（用于生成 interact 命令）
LOCATION_ITEMS = {
    "home": ["凭证", "小刀", "盾牌"],
    "商店": ["打工", "小刀", "磨刀石", "隐身衣", "热成像仪", "陶瓷护甲", "防毒面具"],
    "魔法所": ["魔法护盾", "魔法弹幕", "远程魔法弹幕", "封闭",
              "地震", "地动山摇", "隐身术", "探测魔法"],
    "医院": ["打工", "晶化皮肤手术", "额外心脏手术", "不老泉手术",
            "防毒面具", "释放病毒"],
    "军事基地": ["通行证", "AT力场", "电磁步枪", "导弹", "高斯步枪",
               "雷达", "隐形涂层"],
    "警察局": [],  # 警察局的行动走 recruit/election/report 等专用命令
}

# 属性克制：attacker_attr → 能有效打的 armor_attr 集合
EFFECTIVE_AGAINST = {
    "普通": {"魔法", "普通"},
    "魔法": {"科技", "魔法"},
    "科技": {"普通", "科技"},
}

# 护甲层标识（用于生成 attack 命令的层+属性参数）
ARMOR_LAYERS = [
    ("外层", "普通"), ("外层", "魔法"), ("外层", "科技"),
    ("内层", "普通"), ("内层", "魔法"), ("内层", "科技"),
]


class BasicAIController(PlayerController):
    """
    基础AI：按阶段判定 + 候选命令优先级 + validate 过滤。
    """

    # 物品名 → (层类型, 属性) 映射（仅限护甲类物品）
    _ARMOR_ITEM_MAP = {
        "盾牌": ("外层", "普通"),
        "陶瓷护甲": ("外层", "普通"),  # 修正：陶瓷护甲是普通属性，不是科技属性
        "魔法护盾": ("外层", "魔法"),
        "AT力场": ("外层", "科技"),
        "晶化皮肤手术": ("内层", "普通"),   # 假设手术提供内层普通护甲
        "额外心脏手术": ("内层", "普通"),
        "不老泉手术": ("内层", "普通"),
        # 可根据实际游戏扩展
    }

    def __init__(self, personality: str = "balanced"):
        """
        personality: 人格参数，影响各阶段权重。
          "balanced"    - 均衡
          "aggressive"  - 偏进攻
          "defensive"   - 偏防守
          "political"   - 偏政治（警察/举报）
          "assassin"    - 偏隐身暗杀
          "builder"     - 偏长线发育
        """
        self.personality = personality
        self.event_log: List[Dict] = []

        # ── 内部记忆 ──
        self._threat_scores: Dict[str, float] = {}   # 玩家名 → 威胁分
        self._been_attacked_by: set = set()           # 打过我的人
        self._my_kills: int = 0
        self._consecutive_forfeits: int = 0           # 连续放弃计数（防摆烂）
        self._last_action: Optional[str] = None
        self._develop_plan: List[str] = []            # 当前发育计划缓存
        self._attempt_index: int = 0                  # 当前 get_command 重试时的候选索引
        self.player_name: Optional[str] = None        # 当前玩家名，用于事件比较
        self._my_id: Optional[str] = None              # 当前玩家ID（备用）
        self._combat_target = None  # 当前战斗目标
        self._in_combat = False  # 是否处于战斗状态
        self._last_combat_turn = -1  # 上次战斗的回合数
        
        # 导弹相关状态（新增）
        self._missile_cooldown = 0  # 导弹冷却回合数
        self._last_missile_turn = -1  # 上次使用导弹的回合
        self._missile_attempt_failed = False  # 导弹尝试是否失败
        
        # 探测装备列表
        self._detection_items = ["热成像仪", "雷达", "探测魔法"]

    # ════════════════════════════════════════════════════════
    #  接口实现：get_command
    # ════════════════════════════════════════════════════════

    def get_command(
        self,
        player: Any,
        game_state: Any,
        available_actions: List[str],
        context: Optional[Dict] = None
    ) -> str:
        self.player_name = player.name
        self._my_id = player.player_id
        attempt = context.get("attempt", 1) if context else 1

        if attempt == 1:
            self._candidates = self._generate_candidates(
                player, game_state, available_actions
            )
            self._attempt_index = 0
            debug_ai_candidate_commands(player.name, f"候选命令列表（共{len(self._candidates)}条）")
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
        self,
        prompt: str,
        options: List[str],
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
            for opt in options:
                if "发动" in opt:
                    return opt
            return options[0]
        # ---- 加入警察 ----
        if situation in ("recruit_pick_1", "recruit_pick_2"):
            priority = ["盾牌", "凭证", "警棍"]
            if self.personality == "aggressive":
                priority = ["警棍", "盾牌", "凭证"]
            for preferred in priority:
                if preferred in options:
                    return preferred
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
        # ---- 默认 ----
        return options[0]

    # ════════════════════════════════════════════════════════
    #  接口实现：choose_multi
    # ════════════════════════════════════════════════════════

    def choose_multi(
        self,
        prompt: str,
        options: List[str],
        max_count: int,
        min_count: int = 0,
        context: Optional[Dict] = None
    ) -> List[str]:
        if not options:
            return []
        sorted_opts = sorted(options, key=lambda name: self._threat_scores.get(name, 0), reverse=True)
        return sorted_opts[:max_count]

    # ════════════════════════════════════════════════════════
    #  接口实现：confirm
    # ════════════════════════════════════════════════════════

    def confirm(self, prompt: str, context: Optional[Dict] = None) -> bool:
        if not context:
            return False
        situation = context.get("phase", "")
        if situation == "response_window":
            talent_name = context.get("talent_name", "")
            action_type = context.get("action_type", "")
            if talent_name == "你给路打油" and action_type in ("attack", "special"):
                return True
            return True
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
    #  核心：候选命令生成（修复版：重点修复战斗指令生成）
    # ════════════════════════════════════════════════════════

    def _generate_candidates(self, player, state, available_actions: List[str]) -> List[str]:
        self._my_id = player.player_id
        self.player_name = player.name
        self._update_threat_scores(player, state)
        
        # 更新战斗状态
        self._update_combat_status(player, state)
        
        # 更新导弹冷却
        if self._missile_cooldown > 0:
            self._missile_cooldown -= 1
        
        candidates = []
        
        # 如果未起床，返回wake
        if not player.is_awake:
            return ["wake"]
        
        # ========== 战斗状态优先级最高 ==========
        if self._in_combat and self._combat_target:
            debug_ai_combat_state(player.name, f"处于战斗状态，目标: {self._combat_target.name}")
            
            # 检查是否应该继续战斗
            if not self._should_continue_combat(player, self._combat_target):
                debug_ai_basic(player.name, "需要撤退，退出战斗状态")
                self._in_combat = False
                self._combat_target = None
            else:
                # 生成战斗攻击命令（重点修复）
                combat_cmds = self._combat_attack_commands(player, state, available_actions, self._combat_target)
                if combat_cmds:
                    debug_ai_attack_generation(player.name, "战斗攻击命令生成", str(combat_cmds))
                    # 战斗状态下，攻击优先级最高
                    candidates.extend(combat_cmds)
                    
                    # 如果不在同一地点，检查是否有远程武器
                    if player.location != self._combat_target.location:
                        # 如果有远程武器，可以尝试远程攻击而不是移动
                        if self._has_ranged_weapon(player):
                            debug_ai_basic(player.name, "有远程武器，优先远程攻击而不是移动")
                        else:
                            # 没有远程武器，添加移动命令
                            if f"move {self._combat_target.location}" not in candidates:
                                candidates.append(f"move {self._combat_target.location}")
                    
                    # 只添加一个紧急发育命令作为后备
                    if len(candidates) < 2:  # 如果攻击命令不够，添加发育命令
                        develop = self._develop_commands(player, state, available_actions)
                        if develop:
                            candidates.append(develop[0])
                    
                    candidates.append("forfeit")
                    return candidates
        
        # ========== 导弹攻击逻辑优化 ==========
        # 检查导弹冷却
        if self._missile_cooldown <= 0:
            if self._needs_missile_attack(player, state):
                debug_ai_missile_attack(player.name, "需要导弹攻击")
                missile_cmds = self._missile_attack_commands(player, state, available_actions)
                if missile_cmds:
                    candidates.extend(missile_cmds)
                    debug_ai_missile_attack(player.name, f"导弹攻击候选：{missile_cmds}")
                    # 设置导弹冷却
                    self._missile_cooldown = 3
                    candidates.append("forfeit")
                    return candidates
        
        # ========== 极危险情况处理（增强版）==========
        if self._is_critical(player, state):
            danger_type = self._get_danger_type(player, state)
            debug_ai_basic(player.name, f"进入极危模式，危险类型: {danger_type}")
            
            # 根据不同危险类型采取不同策略
            if danger_type == "police_surrounded":
                debug_ai_basic(player.name, "被警察围攻，寻求群攻手段")
                candidates.extend(self._police_survival_commands(player, state, available_actions))
            elif danger_type == "anchored":
                debug_ai_basic(player.name, "被锚定，采取破坏性行动")
                candidates.extend(self._anchor_survival_commands(player, state, available_actions))
            else:
                # 通用生存命令
                candidates.extend(self._survival_commands(player, state, available_actions))
            
            debug_ai_detailed(player.name, f"极危候选：{candidates}")
            if candidates:
                return candidates
        
        if self._needs_virus_cure(player, state):
            debug_ai_basic(player.name, "进入病毒应急模式")
            candidates.extend(self._virus_cure_commands(player, state))
            debug_ai_detailed(player.name, f"病毒候选：{candidates}")
            if candidates:
                return candidates
        
        # ========== 修复：优先处理击杀机会 ==========
        # 这里特别重要：即使不在战斗状态，有击杀机会也要攻击
        if self._has_kill_opportunity(player, state):
            debug_ai_basic(player.name, f"有击杀机会！")
            kill_attack_cmds = self._attack_commands(player, state, available_actions)
            if kill_attack_cmds:
                candidates.extend(kill_attack_cmds)
                debug_ai_attack_generation(player.name, "击杀攻击命令生成", "击杀机会目标")
                # 击杀机会优先级很高，直接返回
                if kill_attack_cmds:
                    # 添加发育命令作为后备
                    develop = self._develop_commands(player, state, available_actions)
                    if develop:
                        candidates.append(develop[0])
                    candidates.append("forfeit")
                    return candidates
        
        # 生成发育命令
        debug_ai_development_plan(player.name, "进入发育模式")
        develop = self._develop_commands(player, state, available_actions)
        debug_ai_development_plan(player.name, f"发育候选：{develop}")
        candidates.extend(develop)
        
        # 检查发育是否完成，如果完成则优先攻击
        if self._is_development_complete(player):
            debug_ai_basic(player.name, "发育完成，尝试生成find/攻击命令")
            
            # 检查同地点是否有其他玩家
            same_location_targets = []
            for pid in state.player_order:
                if pid == player.player_id:
                    continue
                target = state.get_player(pid)
                if target and target.is_alive() and target.location == player.location:
                    same_location_targets.append(target)
            
            if same_location_targets:
                debug_ai_detailed(player.name, f"同地点发现玩家: {[t.name for t in same_location_targets]}")
                
                # 选择威胁最大的目标
                best_target = max(same_location_targets, 
                                key=lambda t: self._threat_scores.get(t.name, 0))
                
                # 如果还没有建立find关系，先find
                markers = state.markers
                needs_find = False
                if hasattr(markers, 'has_relation'):
                    needs_find = not markers.has_relation(player.player_id, "ENGAGED_WITH", best_target.player_id)
                else:
                    # 保守估计需要find
                    needs_find = True
                
                if needs_find and "find" in available_actions:
                    find_cmd = f"find {best_target.name}"
                    if find_cmd not in candidates:
                        candidates.insert(0, find_cmd)
                        debug_ai_basic(player.name, f"插入优先find命令: {find_cmd}")
                elif "attack" in available_actions:
                    # 如果已经有find关系，直接攻击
                    attack_cmds = self._attack_commands(player, state, available_actions)
                    if attack_cmds and attack_cmds[0] not in candidates:
                        # 将攻击命令插入到发育命令之前
                        candidates.insert(0, attack_cmds[0])
                        debug_ai_basic(player.name, f"插入优先攻击命令: {attack_cmds[0]}")
        
        if self.personality == "political":
            debug_ai_basic(player.name, "进入政治模式")
            political = self._political_commands(player, state, available_actions)
            debug_ai_detailed(player.name, f"政治候选：{political}")
            candidates.extend(political)
        
        # ========== 修复：常规攻击命令 ==========
        # 确保总是尝试生成攻击命令
        if "attack" in available_actions:
            debug_ai_attack_generation(player.name, "常规进攻命令", "未指定目标")
            attack = self._attack_commands(player, state, available_actions)
            debug_ai_detailed(player.name, f"进攻候选：{attack}")
            # 避免重复添加相同的攻击命令
            for cmd in attack:
                if cmd not in candidates:
                    candidates.append(cmd)
        
        # 如果没有攻击命令，尝试生成find命令
        if "find" in available_actions and not any("attack" in cmd for cmd in candidates):
            target = self._pick_best_target(player, state)
            if target and target.location == player.location:
                find_cmd = f"find {target.name}"
                if find_cmd not in candidates:
                    candidates.append(find_cmd)
                    debug_ai_basic(player.name, f"添加find命令: {find_cmd}")
        
        candidates.append("forfeit")
        seen = set()
        deduped = [cmd for cmd in candidates if not (cmd in seen or seen.add(cmd))]
        debug_ai_candidate_commands(player.name, f"最终候选（去重后）：{deduped}")
        return deduped

    # ════════════════════════════════════════════════════════
    #  阶段判定（增强版）
    # ════════════════════════════════════════════════════════

    def _is_critical(self, player, state) -> bool:
        """判断是否处于极危险情况"""
        # 血量极低
        if player.hp <= 0.5:
            return True
        # 被警察围攻
        if (state.police_engine
                and hasattr(state.police, 'reported_target_id')
                and state.police.reported_target_id == player.player_id
                and hasattr(state.police, 'report_phase')
                and state.police.report_phase in ("dispatched", "enforcing")):
            return True
        # 被多人锁定
        lock_count = self._count_locked_by(player, state)
        if lock_count >= 2:
            return True
        # 被锚定（检查涟漪锚定标记）
        if self._is_anchored(player, state):
            return True
        return False
    
    def _get_danger_type(self, player, state) -> str:
        """获取具体的危险类型"""
        # 检查是否被警察围攻
        if (state.police_engine
                and hasattr(state.police, 'reported_target_id')
                and state.police.reported_target_id == player.player_id
                and hasattr(state.police, 'report_phase')
                and state.police.report_phase in ("dispatched", "enforcing")):
            return "police_surrounded"
        # 检查是否被锚定
        if self._is_anchored(player, state):
            return "anchored"
        # 默认危险
        return "general"
    
    def _is_anchored(self, player, state) -> bool:
        """检查是否被涟漪天赋锚定"""
        # 方法1：检查涟漪天赋的锚定标记
        markers = getattr(state, 'markers', None)
        if markers:
            # 检查是否有ANCHORED_BY关系
            if hasattr(markers, 'has_relation'):
                # 查找所有与玩家有ANCHORED_BY关系的玩家
                for pid in state.player_order:
                    if pid == player.player_id:
                        continue
                    if markers.has_relation(player.player_id, "ANCHORED_BY", pid):
                        return True
            # 方法2：检查涟漪天赋的锚定状态
            for pid in state.player_order:
                if pid == player.player_id:
                    continue
                target = state.get_player(pid)
                if target and target.talent and hasattr(target.talent, 'is_anchoring'):
                    if target.talent.is_anchoring(player):
                        return True
        return False
    
    def _update_combat_status(self, player, state):
        """更新战斗状态"""
        markers = state.markers if hasattr(state, 'markers') else None
        
        # 检查是否有战斗关系
        current_combat_target = None
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if target and target.is_alive():
                # 检查是否有ENGAGED_WITH关系
                if markers and hasattr(markers, 'has_relation'):
                    if markers.has_relation(player.player_id, "ENGAGED_WITH", pid):
                        current_combat_target = target
                        break
        
        if current_combat_target:
            # 进入或保持战斗状态
            self._in_combat = True
            self._combat_target = current_combat_target
            debug_ai_combat_state(player.name, f"与 {current_combat_target.name} 进入战斗状态")
        else:
            # 没有战斗关系，退出战斗状态
            if self._in_combat:
                debug_ai_basic(player.name, "退出战斗状态")
            self._in_combat = False
            self._combat_target = None

    def _should_continue_combat(self, player, target):
        """判断是否应该继续战斗"""
        if not target or not target.is_alive():
            return False
        
        # 政治型玩家：挨打就跑
        if self.personality == "political":
            # 检查自己是否处于劣势
            if self._is_at_disadvantage(player, target):
                return False
            return True
        
        # 其他类型玩家：检查护甲和血量
        my_armor_health = self._calculate_armor_health(player)
        target_armor_health = self._calculate_armor_health(target)
        
        # 如果自己护甲严重不足（低于20%）且目标护甲还很多，考虑撤退
        if my_armor_health < 0.2 and target_armor_health > 0.5:
            return False
        
        # 如果自己血量很低，撤退
        if player.hp < 0.3:
            return False
        
        return True

    # ════════════════════════════════════════════════════════
    #  核心修复：战斗攻击命令生成
    # ════════════════════════════════════════════════════════

    def _combat_attack_commands(self, player, state, available_actions, target):
        """生成战斗攻击命令（重点修复版本）"""
        cmds = []
        
        if not target:
            debug_warning(f"[{player.name}] 战斗攻击命令：目标无效")
            return cmds
        
        debug_ai_attack_generation(player.name, "战斗攻击命令", target.name)
        
        # 检查攻击前提条件
        if not self._has_attack_prerequisite(player, target, state):
            debug_ai_basic(player.name, f"无法攻击 {target.name}，不满足攻击前提条件")
            # 如果不满足攻击条件，尝试建立关系
            if "find" in available_actions and player.location == target.location:
                cmds.append(f"find {target.name}")
                debug_ai_basic(player.name, "添加find命令建立战斗关系")
            return cmds
        
        # 检查是否在同一地点
        if player.location != target.location:
            debug_ai_detailed(player.name, f"与目标 {target.name} 不在同一地点 ({player.location} vs {target.location})")
            
            # 检查是否有远程武器
            if self._has_ranged_weapon(player):
                debug_ai_basic(player.name, "拥有远程武器，尝试远程攻击")
                # 尝试生成远程攻击命令
                attack_cmd = self._generate_attack_command(player, target, available_actions)
                if attack_cmd:
                    cmds.append(attack_cmd)
                    debug_ai_attack_generation(player.name, "远程攻击命令生成", target.name)
                else:
                    debug_ai_basic(player.name, "无法生成远程攻击命令")
                    # 无法远程攻击，移动到目标地点
                    if "move" in available_actions:
                        cmds.append(f"move {target.location}")
                        debug_ai_basic(player.name, f"添加移动命令到 {target.location}")
            else:
                debug_ai_basic(player.name, "没有远程武器，移动到目标地点")
                # 没有远程武器，移动到目标地点
                if "move" in available_actions:
                    cmds.append(f"move {target.location}")
                    debug_ai_basic(player.name, f"添加移动命令到 {target.location}")
        else:
            # 在同一地点，直接攻击
            debug_ai_detailed(player.name, f"与目标 {target.name} 在同一地点，直接攻击")
            
            if "attack" in available_actions:
                attack_cmd = self._generate_attack_command(player, target, available_actions)
                if attack_cmd:
                    cmds.append(attack_cmd)
                    debug_ai_attack_generation(player.name, "近战攻击命令生成", target.name)
                else:
                    debug_ai_basic(player.name, "无法生成攻击命令")
                    # 如果无法生成攻击命令，尝试find
                    if "find" in available_actions:
                        markers = getattr(state, 'markers', None)
                        needs_find = True
                        if markers and hasattr(markers, 'has_relation'):
                            needs_find = not markers.has_relation(player.player_id, "ENGAGED_WITH", target.player_id)
                        
                        if needs_find:
                            cmds.append(f"find {target.name}")
                            debug_ai_basic(player.name, "添加find命令")
            elif "find" in available_actions:
                # 如果没有攻击选项，尝试find
                markers = getattr(state, 'markers', None)
                needs_find = True
                if markers and hasattr(markers, 'has_relation'):
                    needs_find = not markers.has_relation(player.player_id, "ENGAGED_WITH", target.player_id)
                
                if needs_find:
                    cmds.append(f"find {target.name}")
                    debug_ai_basic(player.name, "添加find命令")
        
        return cmds

    def _needs_virus_cure(self, player, state) -> bool:
        if not state.virus.is_active:
            return False
        if self._has_virus_immunity(player):
            return False
        return True

    def _has_kill_opportunity(self, player, state) -> bool:
        best_weapon_dmg = self._best_weapon_damage(player)
        if best_weapon_dmg <= 0:
            return False
        
        debug_ai_basic(player.name, f"检查击杀机会，最佳武器伤害: {best_weapon_dmg}")
        
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if not target or not target.is_alive():
                continue
            
            debug_ai_kill_opportunity(player.name, target.name, target.hp)
            
            if target.hp <= best_weapon_dmg:
                debug_ai_basic(player.name, f"目标 {target.name} 血量({target.hp}) <= 武器伤害({best_weapon_dmg})")
                if self._has_attack_prerequisite(player, target, state):
                    debug_ai_basic(player.name, "满足攻击前提条件，有击杀机会！")
                    return True
                else:
                    debug_ai_basic(player.name, f"目标 {target.name} 血量低但不满足攻击前提条件")
        
        return False

    # ════════════════════════════════════════════════════════
    #  生存模式命令（增强版：针对不同危险类型）
    # ════════════════════════════════════════════════════════

    def _survival_commands(self, player, state, available) -> List[str]:
        """通用生存命令：获取护甲、隐身、回血等"""
        cmds = []
        loc = player.location
        if "move" in available:
            safe_loc = self._find_safest_location(player, state)
            if safe_loc and safe_loc != loc:
                cmds.append(f"move {safe_loc}")
        if loc == "home" and "interact" in available:
            if self._can_take_item(player, "盾牌"):
                cmds.append("interact 盾牌")
        if "interact" in available and loc in LOCATION_ITEMS:
            for item in LOCATION_ITEMS.get(loc, []):
                if item in ("盾牌", "魔法护盾", "AT力场", "陶瓷护甲"):
                    if self._can_take_item(player, item):
                        cmds.append(f"interact {item}")
                if item in ("隐身衣", "隐身术", "隐形涂层"):
                    if self._can_take_item(player, item):
                        cmds.append(f"interact {item}")
                if item == "防毒面具" and self._needs_virus_cure(player, state):
                    if self._can_take_item(player, item):
                        cmds.append(f"interact {item}")
        if "special" in available:
            for item in player.items:
                if hasattr(item, 'name') and "隐身" in item.name:
                    cmds.append(f"special {item.name}")
        return cmds

    def _police_survival_commands(self, player, state, available) -> List[str]:
        """警察围攻生存命令：获取群攻手段"""
        cmds = []
        loc = player.location
        
        # 首先尝试获取群攻法术
        if loc == "魔法所":
            # 检查是否可以学习地震或地动山摇
            if self._can_learn(player, "地震"):
                cmds.append("interact 地震")
            elif self._can_learn(player, "地动山摇"):
                cmds.append("interact 地动山摇")
            else:
                # 如果已经学会，尝试获取其他生存物品
                if self._can_take_item(player, "魔法护盾"):
                    cmds.append("interact 魔法护盾")
                elif "move" in available:
                    cmds.append("move 商店")
        else:
            # 前往魔法所学习群攻法术
            if "move" in available:
                cmds.append("move 魔法所")
        
        # 如果没有其他命令，添加通用生存命令
        if not cmds:
            cmds.extend(self._survival_commands(player, state, available))
        
        return cmds

    def _anchor_survival_commands(self, player, state, available) -> List[str]:
        """被锚定生存命令：获取护甲或尝试击杀发动者"""
        cmds = []
        loc = player.location
        
        # 1. 尝试找到锚定发动者
        anchor_caster = None
        markers = getattr(state, 'markers', None)
        if markers and hasattr(markers, 'has_relation'):
            for pid in state.player_order:
                if pid == player.player_id:
                    continue
                if markers.has_relation(player.player_id, "ANCHORED_BY", pid):
                    anchor_caster = state.get_player(pid)
                    break
        
        # 2. 如果找到发动者且护甲足够，尝试击杀
        if anchor_caster and anchor_caster.is_alive():
            # 检查护甲是否足够（至少1件外层护甲）
            if self._count_outer_armor(player) > 0:
                debug_ai_basic(player.name, f"护甲足够，尝试反击锚定发动者 {anchor_caster.name}")
                # 生成攻击命令
                attack_cmds = self._attack_commands(player, state, available)
                if attack_cmds:
                    cmds.extend(attack_cmds)
                    return cmds
        
        # 3. 否则，获取护甲（优先去发动者不在的地方）
        safe_locations = ["商店", "魔法所", "军事基地", "医院"]
        if anchor_caster:
            # 避免去发动者所在的地点
            safe_locations = [loc for loc in safe_locations if loc != anchor_caster.location]
        
        if loc in safe_locations:
            # 在当前安全地点获取护甲
            if "interact" in available:
                for item in LOCATION_ITEMS.get(loc, []):
                    if item in ("盾牌", "陶瓷护甲", "魔法护盾", "AT力场"):
                        if self._can_take_item(player, item):
                            cmds.append(f"interact {item}")
                            break
                if not cmds and "move" in available and safe_locations:
                    # 如果当前地点没有护甲，移动到其他安全地点
                    target_loc = next((l for l in safe_locations if l != loc), safe_locations[0])
                    cmds.append(f"move {target_loc}")
        elif "move" in available and safe_locations:
            # 移动到安全地点
            target_loc = safe_locations[0]
            cmds.append(f"move {target_loc}")
        
        # 4. 如果没有其他命令，添加通用生存命令
        if not cmds:
            cmds.extend(self._survival_commands(player, state, available))
        
        return cmds

    # ════════════════════════════════════════════════════════
    #  病毒应急命令
    # ════════════════════════════════════════════════════════

    def _virus_cure_commands(self, player, state) -> List[str]:
        cmds = []
        loc = player.location
        if loc == "商店" or loc == "医院":
            if self._can_take_item(player, "防毒面具"):
                cmds.append("interact 防毒面具")
        if loc != "商店":
            cmds.append("move 商店")
        if loc != "医院":
            cmds.append("move 医院")
        if loc == "魔法所" and not self._has_virus_immunity(player):
            cmds.append("interact 封闭")
        return cmds

    # ════════════════════════════════════════════════════════
    #  进攻模式命令（常规攻击）
    # ════════════════════════════════════════════════════════

    def _attack_commands(self, player, state, available) -> List[str]:
        """生成常规攻击命令（重点修复）"""
        cmds = []
        
        # 选择最佳目标
        target = self._pick_best_target(player, state)
        if not target:
            debug_ai_basic(player.name, "攻击命令：没有找到合适的目标")
            return cmds
        
        target_name = target.name
        debug_ai_attack_generation(player.name, "选择目标", target_name)
        
        # 选择最佳武器（重点修复）
        weapon = self._pick_best_weapon_against(player, target)
        if not weapon:
            debug_ai_basic(player.name, f"攻击命令：无法选择武器攻击 {target_name}")
            # 即使没有最佳武器，也尝试使用默认武器
            if player.weapons and len(player.weapons) > 0:
                weapon = player.weapons[0]
                debug_ai_basic(player.name, f"使用默认武器: {weapon.name}")
            else:
                return cmds
        
        weapon_name = weapon.name
        debug_ai_attack_generation(player.name, weapon_name, target_name)
        
        # 生成攻击命令
        if "attack" in available:
            layer_attr = self._pick_attack_layer(weapon, target)
            if layer_attr:
                layer_str, attr_str = layer_attr
                attack_cmd = f"attack {target_name} {weapon_name} {layer_str} {attr_str}"
                cmds.append(attack_cmd)
                debug_ai_attack_generation(player.name, "生成攻击命令", target_name)
            else:
                # 如果无法确定攻击层，使用默认值
                attack_cmd = f"attack {target_name} {weapon_name} 外层 普通"
                cmds.append(attack_cmd)
                debug_ai_basic(player.name, f"使用默认攻击命令: {attack_cmd}")
        
        # 附加命令：find, lock, move
        if "find" in available and target.location == player.location:
            cmds.append(f"find {target_name}")
            debug_ai_basic(player.name, "添加find命令")
        
        if "lock" in available:
            cmds.append(f"lock {target_name}")
            debug_ai_basic(player.name, "添加lock命令")
        
        if "move" in available and target.location != player.location and target.location:
            cmds.append(f"move {target.location}")
            debug_ai_basic(player.name, f"添加move命令到 {target.location}")
        
        return cmds

    # ════════════════════════════════════════════════════════
    #  发育模式命令
    # ════════════════════════════════════════════════════════

    def _develop_commands(self, player, state, available) -> List[str]:
        cmds = []
        loc = player.location
        if "interact" not in available and "move" not in available:
            return cmds

        has_credential = self._has_credential(player)
        has_outer_armor = self._count_outer_armor(player) > 0
        has_good_weapon = self._best_weapon_damage(player) >= 1.0
        has_detection = getattr(player, 'has_detection', False)
        has_inner_armor = self._count_inner_armor(player) > 0
        has_military_pass = getattr(player, 'has_military_pass', False)
        
        debug_ai_development_plan(player.name, 
            f"has_credential={has_credential} has_outer={has_outer_armor} "
            f"has_weapon={has_good_weapon}(dmg={self._best_weapon_damage(player)}) "
            f"has_detect={has_detection} has_inner={has_inner_armor} "
            f"has_pass={has_military_pass}")
        
        plan: list = []

        def is_at(location_name):
            return loc == location_name or loc == f"home_{player.player_id}"

        # ══════ 阶段1：拿凭证 ══════
        if not has_credential:
            if is_at("home"):
                plan.append("interact 凭证")
            elif loc == "商店":
                plan.append("interact 打工")
            elif loc == "医院":
                plan.append("interact 打工")
            else:
                plan.append("move 商店")

        # ══════ 阶段2：拿护甲 ══════
        elif not has_outer_armor:
            if is_at("home"):
                plan.append("interact 盾牌")
            if loc == "商店":
                plan.append("interact 陶瓷护甲")
            elif loc == "魔法所":
                plan.append("interact 魔法护盾")
            elif loc == "军事基地":
                if not has_military_pass:
                    plan.append("interact 办理通行证")
                else:
                    plan.append("interact AT力场")
            else:
                plan.append("move 商店")

        # ══════ 阶段3：拿武器 ══════
        elif not has_good_weapon:
            if is_at("home"):
                if not self._has_weapon_named(player, "小刀"):
                    plan.append("interact 小刀")
                else:
                    plan.append("move 魔法所")
            elif loc == "商店":
                if not self._has_weapon_named(player, "小刀"):
                    plan.append("interact 小刀")
                else:
                    plan.append("move 魔法所")
            elif loc == "魔法所":
                if self._can_learn(player, "魔法弹幕"):
                    plan.append("interact 魔法弹幕")
                elif self._can_learn(player, "远程魔法弹幕"):
                    plan.append("interact 远程魔法弹幕")
                else:
                    plan.append("move 军事基地")
            elif loc == "军事基地":
                if not has_military_pass:
                    plan.append("interact 办理通行证")
                elif not self._has_weapon_named(player, "高斯步枪"):
                    plan.append("interact 高斯步枪")
                else:
                    plan.append("move 魔法所")
            else:
                plan.append("move 商店")

        # ══════ 阶段4：拿探测 ══════
        elif not has_detection:
            if loc == "商店":
                if not self._has_item_named(player, "热成像仪"):
                    plan.append("interact 热成像仪")
                else:
                    plan.append("move 魔法所")
            elif loc == "魔法所":
                if self._can_learn(player, "探测魔法"):
                    plan.append("interact 探测魔法")
                else:
                    plan.append("move 军事基地")
            elif loc == "军事基地":
                if not has_military_pass:
                    plan.append("interact 办理通行证")
                elif not self._has_item_named(player, "雷达"):
                    plan.append("interact 雷达")
                else:
                    plan.append("move 商店")
            else:
                plan.append("move 魔法所")

        # ══════ 阶段5：内层护甲 ══════
        elif not has_inner_armor:
            # 如果在魔法所，优先学习其他有用的魔法
            if loc == "魔法所":
                # 检查是否有其他重要魔法可以学习
                other_spells = ["魔法弹幕", "远程魔法弹幕", "地震", "地动山摇", "魔法护盾", "封闭"]
                spell_learned = False
                for spell in other_spells:
                    if self._can_learn(player, spell):
                        plan.append(f"interact {spell}")
                        spell_learned = True
                        debug_ai_development_plan(player.name, f"在魔法所，优先学习 {spell}")
                        break
                if not spell_learned:
                    # 没有其他魔法可学，去医院（但需要凭证）
                    if has_credential:  # 有凭证才去医院做手术
                        plan.append("move 医院")
                        debug_ai_development_plan(player.name, "有凭证，前往医院做手术")
                    else:
                        # 没有凭证，继续发育其他项目
                        plan.append("move 商店")
                        debug_ai_development_plan(player.name, "没有凭证，不去医院，去商店发育")
            elif loc == "医院":
                if has_credential:  # 有凭证才做手术
                    plan.append("interact 晶化皮肤手术")
                    debug_ai_development_plan(player.name, "在医院且有凭证，进行手术")
                else:
                    # 没有凭证，离开医院
                    plan.append("move 商店")
                    debug_ai_development_plan(player.name, "在医院但没有凭证，离开医院")
            else:
                # 不在魔法所或医院，检查是否有凭证
                if has_credential:
                    plan.append("move 医院")
                    debug_ai_development_plan(player.name, "有凭证，前往医院")
                else:
                    # 没有凭证，先去获取凭证或发育
                    plan.append("move 商店")
                    debug_ai_development_plan(player.name, "没有凭证，先不去医院")

        # ══════ 阶段6：额外发育 ══════
        else:
            extras = self._extra_develop_commands(player, state)
            plan.extend(extras)

        cmds.extend(plan)

        if not cmds and "move" in available:
            cmds.append("move 军事基地")

        return cmds

    # ════════════════════════════════════════════════════════
    #  辅助方法：威胁评估
    # ════════════════════════════════════════════════════════

    def _update_threat_scores(self, player, state):
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if not target or not target.is_alive():
                if target:
                    self._threat_scores.pop(target.name, None)
                continue
            score = self._threat_scores.get(target.name, 0)
            t_dmg = self._best_weapon_damage_of(target)
            score = max(score, t_dmg * 10)
            score += target.hp * 5
            score += self._count_outer_armor_of(target) * 5
            score += self._count_inner_armor_of(target) * 5
            if target.location == player.location:
                score += 15
            self._threat_scores[target.name] = score

    def _pick_best_target(self, player, state):
        """选择最佳攻击目标（修复版）"""
        best_target = None
        best_score = -1
        
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if not target or not target.is_alive():
                continue
            
            score = self._threat_scores.get(target.name, 0)
            my_dmg = self._best_weapon_damage(player)
            
            # 击杀机会优先
            if target.hp <= my_dmg and self._has_attack_prerequisite(player, target, state):
                score += 100
                debug_ai_basic(player.name, f"目标 {target.name} 有击杀机会，分数+100")
            
            # 同地点优先
            if target.location == player.location:
                score += 20
                debug_ai_basic(player.name, f"目标 {target.name} 同地点，分数+20")
            
            # 低血量优先
            if target.hp <= 0.5:
                score += 30
                debug_ai_basic(player.name, f"目标 {target.name} 低血量，分数+30")
            
            if score > best_score:
                best_score = score
                best_target = target
        
        if best_target:
            debug_ai_attack_generation(player.name, "选择最佳目标", best_target.name)
        else:
            debug_ai_basic(player.name, "没有找到合适的目标")
        
        return best_target

    # ════════════════════════════════════════════════════════
    #  核心修复：武器与攻击层选择
    # ════════════════════════════════════════════════════════

    def _has_weapon_named(self, player, name: str) -> bool:
        """检查玩家是否已拥有指定名称的武器"""
        for w in getattr(player, 'weapons', []):
            if getattr(w, 'name', '') == name:
                return True
        return False

    def _has_item_named(self, player, name: str) -> bool:
        """检查玩家是否已拥有指定名称的物品"""
        for item in getattr(player, 'items', []):
            if getattr(item, 'name', '') == name:
                return True
        return False

    def _best_weapon_damage(self, player) -> float:
        """获取最佳武器伤害（修复版）"""
        max_dmg = 0.0
        for w in getattr(player, 'weapons', []):
            # 尝试多种可能的伤害属性名
            dmg = 0
            for attr_name in ['damage', 'base_damage', 'dmg']:
                if hasattr(w, attr_name):
                    attr_value = getattr(w, attr_name)
                    if isinstance(attr_value, (int, float)):
                        dmg = max(dmg, attr_value)
            
            # 如果没有找到伤害属性，尝试从名称推断
            if dmg <= 0 and hasattr(w, 'name'):
                weapon_name = w.name.lower()
                if "小刀" in weapon_name:
                    dmg = 0.5
                elif "魔法弹幕" in weapon_name:
                    dmg = 1.0
                elif "高斯步枪" in weapon_name or "电磁步枪" in weapon_name:
                    dmg = 1.5
                elif "导弹" in weapon_name:
                    dmg = 2.0
            
            if dmg > max_dmg:
                max_dmg = dmg
        
        # 默认最小伤害
        if max_dmg <= 0:
            max_dmg = 0.5
        
        debug_ai_full(player.name, f"最佳武器伤害: {max_dmg}")
        return max_dmg
    
    # 类属性：前置条件
    SPELL_PREREQUISITES = {
        "远程魔法弹幕": ["魔法弹幕"],
        "地动山摇": ["地震"],
        "地震": ["魔法弹幕"],
        # 无前置
        "魔法弹幕": [],
        "探测魔法": [],
        "魔法护盾": [],
    }

    def _can_learn(self, player, spell_name: str) -> bool:
        """检查：没学过 + 满足前置"""
        # 武器类法术
        if self._has_weapon_named(player, spell_name):
            return False
        # 护甲类（魔法护盾）
        if spell_name == "魔法护盾":
            return self._count_outer_armor(player) == 0
        # 检查前置
        prereqs = self.SPELL_PREREQUISITES.get(spell_name, [])
        for prereq in prereqs:
            if not self._has_weapon_named(player, prereq):
                return False
        return True

    def _has_detection(self, player) -> bool:
        """检查是否有任何探测能力"""
        detect_items = {"热成像仪", "雷达", "探测魔法"}
        for item in getattr(player, 'items', []):
            name = getattr(item, 'name', '')
            if name in detect_items:
                return True
            effect = getattr(item, 'effect', {})
            if isinstance(effect, dict) and effect.get('grant') == 'detect':
                return True
        return False
        
    def _best_weapon_damage_of(self, target) -> float:
        return self._best_weapon_damage(target)

    def _pick_best_weapon_against(self, player, target):
        """选择最佳武器攻击目标（重点修复）"""
        if not hasattr(player, 'weapons') or not player.weapons:
            debug_ai_basic(player.name, "没有武器")
            return None
        
        debug_ai_attack_generation(player.name, "选择武器攻击目标", target.name)
        
        best = None
        best_score = -1
        
        for w in player.weapons:
            debug_ai_full(player.name, f"检查武器: {getattr(w, 'name', '未知')}")
            
            # 获取武器伤害
            damage = 0
            for attr_name in ['damage', 'base_damage', 'dmg']:
                if hasattr(w, attr_name):
                    attr_value = getattr(w, attr_name)
                    if isinstance(attr_value, (int, float)):
                        damage = max(damage, attr_value)
            
            # 如果找不到伤害属性，使用默认值
            if damage <= 0:
                weapon_name = getattr(w, 'name', '').lower()
                if "小刀" in weapon_name:
                    damage = 0.5
                elif "魔法弹幕" in weapon_name:
                    damage = 1.0
                elif "高斯步枪" in weapon_name or "电磁步枪" in weapon_name:
                    damage = 1.5
                elif "导弹" in weapon_name:
                    damage = 2.0
                else:
                    damage = 0.5  # 默认值
            
            # 获取武器属性
            weapon_attr = "普通"
            for attr_name in ['damage_type', 'attribute', 'type']:
                if hasattr(w, attr_name):
                    attr_value = getattr(w, attr_name)
                    if attr_value in ["普通", "魔法", "科技"]:
                        weapon_attr = attr_value
                        break
            
            debug_ai_full(player.name, f"武器 {getattr(w, 'name', '未知')} 伤害: {damage}, 属性: {weapon_attr}")
            
            # 计算分数
            score = damage * 10  # 基础分数
            
            # 属性克制加分
            if self._can_damage_any_armor(weapon_attr, target):
                score += 15
                debug_ai_full(player.name, "武器属性克制，分数+15")
            
            # 高伤害武器额外加分
            if damage >= 1.0:
                score += 10
                debug_ai_full(player.name, "高伤害武器，分数+10")
            
            # 远程武器对远处目标加分
            if player.location != target.location:
                weapon_name = getattr(w, 'name', '').lower()
                if "远程" in weapon_name or "导弹" in weapon_name or "弹幕" in weapon_name:
                    score += 20
                    debug_ai_full(player.name, "远程武器对远处目标，分数+20")
            
            debug_ai_full(player.name, f"武器 {getattr(w, 'name', '未知')} 最终分数: {score}")
            
            if score > best_score:
                best_score = score
                best = w
        
        if best:
            debug_ai_attack_generation(player.name, "选择武器", getattr(best, 'name', '未知'))
        else:
            debug_ai_basic(player.name, "没有找到合适的武器")
        
        return best

    def _pick_attack_layer(self, weapon, target):
        """选择攻击层和属性（修复版）"""
        # 获取武器属性
        weapon_attr = "普通"
        for attr_name in ['damage_type', 'attribute', 'type']:
            if hasattr(weapon, attr_name):
                attr_value = getattr(weapon, attr_name)
                if attr_value in ["普通", "魔法", "科技"]:
                    weapon_attr = attr_value
                    break
        
        debug_ai_full(self.player_name, f"选择攻击层，武器属性: {weapon_attr}")
        
        effective_set = EFFECTIVE_AGAINST.get(weapon_attr, set())
        
        # 检查目标是否有护甲
        if hasattr(target, 'armor'):
            debug_ai_full(self.player_name, "目标有护甲，检查有效攻击层")
            
            # 优先攻击有效的护甲层
            for attr in ["普通", "魔法", "科技"]:
                if attr in effective_set and self._target_has_armor_layer(target, "外层", attr):
                    debug_ai_full(self.player_name, f"选择外层 {attr} 护甲")
                    return ("外层", attr)
            
            # 如果没有有效的护甲层，检查是否有外层护甲
            if not self._target_has_any_outer(target):
                debug_ai_full(self.player_name, "目标没有外层护甲，检查内层")
                for attr in ["普通", "魔法", "科技"]:
                    if attr in effective_set and self._target_has_armor_layer(target, "内层", attr):
                        debug_ai_full(self.player_name, f"选择内层 {attr} 护甲")
                        return ("内层", attr)
        
        debug_ai_basic(self.player_name, f"使用默认攻击层: 外层 {weapon_attr}")
        return ("外层", weapon_attr)

    def _can_damage_any_armor(self, weapon_attr: str, target) -> bool:
        """检查武器是否能伤害目标的任何护甲"""
        effective_set = EFFECTIVE_AGAINST.get(weapon_attr, set())
        
        if not hasattr(target, 'armor'):
            debug_ai_full(self.player_name, "目标没有护甲，任何武器都能伤害")
            return True
        
        # 检查是否有任何有效的护甲层
        for layer_type in ["外层", "内层"]:
            for attr in ["普通", "魔法", "科技"]:
                if attr in effective_set and self._target_has_armor_layer(target, layer_type, attr):
                    debug_ai_full(self.player_name, f"武器属性 {weapon_attr} 能伤害 {layer_type} {attr} 护甲")
                    return True
        
        debug_ai_full(self.player_name, f"武器属性 {weapon_attr} 不能伤害目标的任何护甲")
        return False

    # ========== 增强的护甲检测（更新版：支持新的ArmorSlots列表结构） ==========
    def _target_has_armor_layer(self, target, layer_type: str, attribute: str) -> bool:
        if not hasattr(target, 'armor'):
            return False
        
        armor = target.armor
        debug_ai_full(self.player_name, f"检查目标 {target.name} 的 {layer_type} {attribute} 护甲")
        
        # 获取对应层的列表
        layer_list = armor.outer if layer_type == "外层" else armor.inner
        
        # 遍历列表中的护甲
        for piece in layer_list:
            if piece is not None and not piece.is_broken:
                piece_attr = str(piece.attribute).lower()
                target_attr = attribute.lower()
                
                # 检查属性匹配
                if target_attr in piece_attr or (attribute == "普通" and "普通" in piece_attr):
                    debug_ai_full(self.player_name, f"找到 {layer_type} {attribute} 护甲: {piece.name}")
                    return True
        
        debug_ai_full(self.player_name, f"没有找到 {layer_type} {attribute} 护甲")
        return False
        
    def _target_has_any_outer(self, target) -> bool:
        for attr in ["普通", "魔法", "科技"]:
            if self._target_has_armor_layer(target, "外层", attr):
                return True
        return False

    def _has_armor_by_name(self, player, item_name: str) -> bool:
        """检查玩家护甲中是否有指定名称的护甲层（精确匹配）"""
        if not hasattr(player, 'armor'):
            return False
        armor = player.armor
        
        # 检查外层和内层
        for piece in armor.outer + armor.inner:
            if piece is not None and not piece.is_broken:
                piece_name = getattr(piece, 'name', str(piece))
                if piece_name == item_name:
                    return True
        
        return False

    def _has_item(self, player, item_name: str) -> bool:
        for item in getattr(player, 'items', []):
            name = getattr(item, 'name', str(item))
            if item_name == name:
                return True
        return False

    def _can_take_item(self, player, item_name: str) -> bool:
        """判断玩家是否能够获取指定物品"""
        # 只检查同名护甲层，不检查属性匹配
        if self._has_armor_by_name(player, item_name):
            return False
        # 检查物品列表
        if self._has_item(player, item_name):
            return False
        return True

    # ====================================

    # ════════════════════════════════════════════════════════
    #  辅助方法：状态查询（更新版：支持新的ArmorSlots列表结构）
    # ════════════════════════════════════════════════════════

    def _has_credential(self, player) -> bool:
        vouchers = getattr(player, 'vouchers', 0)
        if vouchers > 0:
            return True
        credentials = getattr(player, 'credentials', 0)
        if credentials > 0:
            return True
        if hasattr(player, 'has_credential') and callable(player.has_credential):
            return bool(player.has_credential())
        for item in getattr(player, 'items', []):
            name = getattr(item, 'name', str(item))
            if '凭证' in name or '山姆' in name:
                return True
        return False

    def _has_virus_immunity(self, player) -> bool:
        if hasattr(player, 'virus_immune') and player.virus_immune:
            return True
        for item in getattr(player, 'items', []):
            name = getattr(item, 'name', str(item))
            if '防毒' in name or '封闭' in name:
                return True
        return False

    def _count_outer_armor(self, player) -> int:
        """计算外层护甲数量（更新版：支持列表结构）"""
        if not hasattr(player, 'armor'):
            return 0
        armor = player.armor
        
        # 如果outer是字典（旧结构）
        if hasattr(armor, 'outer') and isinstance(armor.outer, dict):
            return sum(1 for v in armor.outer.values() if v is not None)
        # 如果outer是列表（新结构）
        elif hasattr(armor, 'outer') and isinstance(armor.outer, list):
            return sum(1 for piece in armor.outer if piece is not None and not piece.is_broken)
        return 0
        
    def _count_inner_armor(self, player) -> int:
        """计算内层护甲数量（更新版：支持列表结构）"""
        if not hasattr(player, 'armor'):
            return 0
        armor = player.armor
        
        # 如果inner是字典（旧结构）
        if hasattr(armor, 'inner') and isinstance(armor.inner, dict):
            return sum(1 for v in armor.inner.values() if v is not None)
        # 如果inner是列表（新结构）
        elif hasattr(armor, 'inner') and isinstance(armor.inner, list):
            return sum(1 for piece in armor.inner if piece is not None and not piece.is_broken)
        return 0
    
    def _count_outer_armor_of(self, target) -> int:
        return self._count_outer_armor(target)

    def _count_inner_armor_of(self, target) -> int:
        return self._count_inner_armor(target)

    def _count_locked_by(self, player, state) -> int:
        count = 0
        if hasattr(state.markers, 'get_all_relations'):
            relations = state.markers.get_all_relations(player.player_id)
            for rel in relations:
                if 'LOCKED' in str(rel).upper():
                    count += 1
        elif hasattr(state.markers, 'count_locked_by'):
            count = state.markers.count_locked_by(player.player_id)
        return count

    def _has_attack_prerequisite(self, player, target, state) -> bool:
        """判断是否可以攻击目标"""
        # 如果在同一地点，可以直接攻击
        if player.location == target.location:
            debug_ai_basic(player.name, f"与目标 {target.name} 在同一地点，可以攻击")
            return True
    
        # 远程攻击：需要已锁定且目标可见
        markers = state.markers
        if hasattr(markers, 'has_relation'):
            is_locked = markers.has_relation(player.player_id, "LOCKED", target.player_id)
            if is_locked:
                has_detection = getattr(player, 'has_detection', False)
                if hasattr(markers, 'is_visible_to'):
                    is_visible = markers.is_visible_to(target.player_id, player.player_id, has_detection)
                    debug_ai_basic(player.name, f"目标 {target.name} 已锁定，可见性: {is_visible}")
                    return is_visible
                else:
                    debug_ai_basic(player.name, f"目标 {target.name} 已锁定，默认可见")
                    return True
        
        debug_ai_basic(player.name, f"无法攻击目标 {target.name}，不满足攻击前提条件")
        return False

    def _extra_develop_commands(self, player, state):
        cmds = []
        loc = player.location

        # 检查是否需要导弹控制权
        has_missile_control = self._has_weapon_named(player, "导弹控制权")
        
        # 检查是否有高威胁远程目标
        needs_missile_for_target = False
        if not has_missile_control:
            for pid in state.player_order:
                if pid == player.player_id:
                    continue
                target = state.get_player(pid)
                if not target or not target.is_alive() or target.location == player.location:
                    continue
                threat_score = self._threat_scores.get(target.name, 0)
                if threat_score > 70:
                    needs_missile_for_target = True
                    break

        if loc == "魔法所":
            for spell in ["魔法弹幕", "远程魔法弹幕", "地震", "地动山摇"]:
                if self._can_learn(player, spell):
                    cmds.append(f"interact {spell}")
                    break
            if not cmds:
                cmds.append("move 军事基地")

        elif loc == "军事基地":
            if not getattr(player, 'has_military_pass', False):
                cmds.append("interact 办理通行证")
            else:
                weapon_priority = ["高斯步枪", "电磁步枪"]
                if needs_missile_for_target:
                    weapon_priority.insert(0, "导弹控制权")
                
                for weapon in weapon_priority:
                    if not self._has_weapon_named(player, weapon):
                        cmds.append(f"interact {weapon}")
                        break
                if not cmds:
                    cmds.append("move 商店")

        elif loc == "商店":
            for item in ["磨刀石", "隐身衣", "防毒面具"]:
                if not self._has_item_named(player, item):
                    cmds.append(f"interact {item}")
                    break
            if not cmds:
                cmds.append("move 魔法所")

        elif loc == "医院":
            cmds.append("move 魔法所")

        else:
            cmds.append("move 魔法所")

        return cmds

    def _find_safest_location(self, player, state) -> Optional[str]:
        best_loc = None
        min_enemies = 999
        all_locs = list(LOCATIONS)
        if player.location not in all_locs:
            all_locs.append(player.location)
        for loc in all_locs:
            enemy_count = 0
            for pid in state.player_order:
                if pid == player.player_id:
                    continue
                p = state.get_player(pid)
                if p and p.is_alive() and p.location == loc:
                    enemy_count += 1
            if enemy_count < min_enemies:
                min_enemies = enemy_count
                best_loc = loc
        return best_loc
    
    def _political_commands(self, player, state, available) -> List[str]:
        cmds = []
        loc = player.location.name if hasattr(player.location, 'name') else str(player.location)

        if "recruit" in available:
            cmds.append("recruit")

        if "report" in available:
            if loc == "警察局":
                target = self._pick_report_target(player, state)
                if target:
                    cmds.append(f"report {target.name}")

        if "vote" in available:
            target = self._pick_best_target(player, state)
            if target:
                cmds.append(f"vote {target.name}")

        return cmds

    def _pick_report_target(self, player, state):
        """只举报确实有犯罪记录的人"""
        for p in state.players.values():
            if p.player_id == player.player_id:
                continue
            if getattr(p, 'crime_record', False) or getattr(p, 'wanted', False):
                return p
        return None
    
    def _is_development_complete(self, player) -> bool:
        """根据AI人格判断发育是否完成"""
        has_credential = self._has_credential(player)
        has_outer_armor = self._count_outer_armor(player) > 0
        has_good_weapon = self._best_weapon_damage(player) >= 1.0
        has_detection = getattr(player, 'has_detection', False)
        has_inner_armor = self._count_inner_armor(player) > 0
        
        if self.personality == "aggressive":
            debug_ai_development_plan(player.name, f"进攻型发育检查：武器伤害={self._best_weapon_damage(player)}，完成={has_good_weapon}")
            return has_good_weapon
        elif self.personality == "balanced":
            result = has_credential and has_outer_armor and has_good_weapon
            debug_ai_development_plan(player.name, f"均衡型发育检查：凭证={has_credential}，外层={has_outer_armor}，武器={has_good_weapon}，完成={result}")
            return result
        elif self.personality == "defensive" or self.personality == "builder":
            result = (has_credential and has_outer_armor and has_good_weapon and 
                    has_detection and has_inner_armor)
            debug_ai_development_plan(player.name, f"发育型检查：凭证={has_credential}，外层={has_outer_armor}，武器={has_good_weapon}，探测={has_detection}，内层={has_inner_armor}，完成={result}")
            return result
        else:
            debug_ai_development_plan(player.name, "政治型，不需要发育完成")
            return False

    # ========== 导弹攻击逻辑优化 ==========
    
    def _needs_missile_attack(self, player, state):
        """判断是否需要使用导弹攻击"""
        if self._in_combat and self._combat_target:
            return self._needs_missile_in_combat(player, state, self._combat_target)
        
        has_missile_control = self._has_weapon_named(player, "导弹控制权")
        
        if player.location != "军事基地" and not has_missile_control:
            return False
        
        best_target = None
        best_threat = -1
        
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            
            target = state.get_player(pid)
            if not target or not target.is_alive():
                continue
            
            if target.location == player.location:
                continue
            
            is_invisible = getattr(target, 'is_invisible', False)
            if is_invisible:
                if not self._has_detection_capability(player):
                    continue
            
            threat_score = self._threat_scores.get(target.name, 0)
            
            if threat_score > 80 and threat_score > best_threat:
                best_threat = threat_score
                best_target = target
        
        if best_target and best_threat > 80:
            debug_ai_missile_attack(player.name, f"检测到高威胁远程目标 {best_target.name}，威胁分数 {best_threat}")
            return True
        
        return False

    def _needs_missile_in_combat(self, player, state, combat_target):
        """战斗状态下是否需要导弹攻击"""
        if player.location == combat_target.location:
            return False
        
        has_missile_control = self._has_weapon_named(player, "导弹控制权")
        
        if not has_missile_control:
            return player.location == "军事基地"
        
        threat_score = self._threat_scores.get(combat_target.name, 0)
        
        is_invisible = getattr(combat_target, 'is_invisible', False)
        if is_invisible and not self._has_detection_capability(player):
            return False
            
        return threat_score > 80

    def _missile_attack_commands(self, player, state, available_actions, specific_target=None):
        """生成导弹攻击命令序列"""
        cmds = []
        
        target = specific_target
        if not target:
            target = self._find_best_missile_target(player, state)
            if not target:
                return cmds
        
        target_name = target.name
        has_missile_control = self._has_weapon_named(player, "导弹控制权")
        
        debug_ai_missile_attack(player.name, f"导弹攻击目标：{target_name}，已有控制权：{has_missile_control}")
        
        is_invisible = getattr(target, 'is_invisible', False)
        if is_invisible:
            if not self._has_detection_capability(player):
                debug_ai_missile_attack(player.name, "目标隐身且无探测能力，无法使用导弹")
                return cmds
        
        if not has_missile_control:
            if player.location == "军事基地" and "interact" in available_actions:
                if not self._should_avoid_military_base(player, state):
                    cmds.append("interact 导弹控制权")
                    debug_ai_missile_attack(player.name, "需要获取导弹控制权")
                else:
                    debug_ai_missile_attack(player.name, "太多玩家在军事基地，暂不获取导弹")
                    cmds.append("move 商店")
            else:
                cmds.append("move 军事基地")
                debug_ai_missile_attack(player.name, "前往军事基地获取导弹")
            return cmds
        
        markers = state.markers
        is_locked = False
        if hasattr(markers, 'has_relation'):
            is_locked = markers.has_relation(player.player_id, "LOCKED", target.player_id)
        
        if not is_locked and "lock" in available_actions:
            cmds.append(f"lock {target_name}")
            debug_ai_missile_attack(player.name, f"需要锁定目标 {target_name}")
            return cmds
        
        if "attack" in available_actions:
            missile_weapon = None
            for w in getattr(player, 'weapons', []):
                if hasattr(w, 'name') and "导弹" in w.name:
                    missile_weapon = w
                    break
            
            if missile_weapon:
                layer_attr = self._pick_attack_layer(missile_weapon, target)
                if layer_attr:
                    layer_str, attr_str = layer_attr
                    attack_cmd = f"attack {target_name} {missile_weapon.name} {layer_str} {attr_str}"
                    cmds.append(attack_cmd)
                    debug_ai_missile_attack(player.name, f"发射导弹攻击 {target_name}")
                else:
                    attack_cmd = f"attack {target_name} {missile_weapon.name} 外层 普通"
                    cmds.append(attack_cmd)
                    debug_ai_missile_attack(player.name, "使用默认参数发射导弹")
        
        return cmds

    def _should_avoid_military_base(self, player, state):
        """判断是否应该避免去军事基地"""
        count = 0
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            other = state.get_player(pid)
            if other and other.is_alive() and other.location == "军事基地":
                count += 1
                if self._has_weapon_named(other, "导弹控制权"):
                    return True
        return count >= 2

    def _has_detection_capability(self, player):
        """检查是否有探测能力"""
        for item_name in self._detection_items:
            if self._has_item_named(player, item_name):
                return True
        
        if self._has_weapon_named(player, "探测魔法"):
            return True
        
        if getattr(player, 'has_detection', False):
            return True
        
        return False
    
    def _has_ranged_weapon(self, player):
        """检查是否有远程武器"""
        if not hasattr(player, 'weapons'):
            return False
        
        ranged_keywords = ["导弹", "远程", "弹幕", "步枪"]
        for w in player.weapons:
            if hasattr(w, 'name'):
                weapon_name = w.name.lower()
                for keyword in ranged_keywords:
                    if keyword in weapon_name:
                        return True
        return False

    def _is_at_disadvantage(self, player, target):
        """判断是否处于劣势"""
        my_power = self._calculate_combat_power(player)
        target_power = self._calculate_combat_power(target)
        return my_power < target_power * 0.7

    def _calculate_combat_power(self, player):
        """计算战斗战力"""
        power = 0
        
        if hasattr(player, 'weapons'):
            for w in player.weapons:
                if hasattr(w, 'damage'):
                    power += w.damage * 10
        
        armor_health = self._calculate_armor_health(player)
        power *= (1 + armor_health)
        
        return power
    
    def _generate_attack_command(self, player, target, available_actions):
        """生成攻击命令字符串"""
        weapon = self._pick_best_weapon_against(player, target)
        if not weapon:
            return None
        
        weapon_name = weapon.name
        target_name = target.name
        
        layer_attr = self._pick_attack_layer(weapon, target)
        if not layer_attr:
            return None
        
        layer_str, attr_str = layer_attr
        return f"attack {target_name} {weapon_name} {layer_str} {attr_str}"

    def _find_best_missile_target(self, player, state):
        """找到最佳的导弹攻击目标"""
        best_target = None
        best_threat = -1
        
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            
            target = state.get_player(pid)
            if not target or not target.is_alive():
                continue
            
            if target.location == player.location:
                continue
            
            is_invisible = getattr(target, 'is_invisible', False)
            if is_invisible:
                if not self._has_detection_capability(player):
                    continue
            
            threat_score = self._threat_scores.get(target.name, 0)
            
            if threat_score > best_threat:
                best_threat = threat_score
                best_target = target
        
        return best_target

    def _calculate_armor_health(self, player):
        """计算护甲健康度（0-1）"""
        if not hasattr(player, 'armor'):
            return 0.0
        
        try:
            armor = player.armor
            total_pieces = 0
            active_pieces = 0
            
            # 检查外层护甲
            if hasattr(armor, 'outer'):
                outer_list = armor.outer
                if isinstance(outer_list, list):
                    for piece in outer_list:
                        if piece is not None:
                            total_pieces += 1
                            if not piece.is_broken:
                                active_pieces += 1
                elif isinstance(outer_list, dict):
                    for key, val in outer_list.items():
                        total_pieces += 1
                        if val is not None and not val.is_broken:
                            active_pieces += 1
            
            # 检查内层护甲
            if hasattr(armor, 'inner'):
                inner_list = armor.inner
                if isinstance(inner_list, list):
                    for piece in inner_list:
                        if piece is not None:
                            total_pieces += 1
                            if not piece.is_broken:
                                active_pieces += 1
                elif isinstance(inner_list, dict):
                    for key, val in inner_list.items():
                        total_pieces += 1
                        if val is not None and not val.is_broken:
                            active_pieces += 1
            
            if total_pieces == 0:
                return 0.0
            return active_pieces / total_pieces
        except Exception as e:
            debug_error(f"计算护甲健康度时出错: {e}")
            return 0.0