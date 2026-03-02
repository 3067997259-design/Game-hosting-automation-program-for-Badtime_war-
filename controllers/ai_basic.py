"""
BasicAIController —— 基础AI控制器（完整策略版）
═══════════════════════════════════════════════════
决策框架：
  1. 判断当前阶段（生存/发育/进攻/政治/应急）
  2. 按阶段生成候选命令列表（优先级从高到低）
  3. 候选命令交给 parse+validate 过滤，第一个合法的执行
  4. choose/confirm 等选择也按场景评分

设计原则：
  - AI 只生产"命令字符串"，规则裁决全部由 parse→validate→execute 完成
  - AI 不可能绕过规则作弊
  - 不合法的候选会被主循环 retry，最终兜底 forfeit
"""

from typing import List, Optional, Dict, Any
from controllers.base import PlayerController
import random

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
        "陶瓷护甲": ("外层", "科技"),
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
            print(f"\n🤖 [{player.name}] 候选命令列表（共{len(self._candidates)}条）：")
            for i, cmd in enumerate(self._candidates, 1):
                print(f"   {i}. {cmd}")
        else:
            self._attempt_index += 1

        if self._attempt_index < len(self._candidates):
            cmd = self._candidates[self._attempt_index]
            print(f"🤖 [{player.name}] 尝试第{attempt}条：{cmd}")
        else:
            cmd = "forfeit"
            print(f"🤖 [{player.name}] 候选耗尽，兜底forfeit")
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
    #  核心：候选命令生成
    # ════════════════════════════════════════════════════════



    def _generate_candidates(self, player, state, available_actions: List[str]) -> List[str]:
        self._my_id = player.player_id
        self.player_name = player.name
        self._update_threat_scores(player, state)
        
        # 更新战斗状态
        self._update_combat_status(player, state)
        
        candidates = []
        
        # 如果未起床，返回wake
        if not player.is_awake:
            return ["wake"]
        
        # 战斗状态优先级最高（除了导弹攻击）
        if self._in_combat and self._combat_target:
            # 检查是否需要导弹攻击（战斗中的远程攻击）
            if self._needs_missile_in_combat(player, state, self._combat_target):
                missile_cmds = self._missile_attack_commands(player, state, available_actions, self._combat_target)
                if missile_cmds:
                    return missile_cmds + ["forfeit"]
        
        # 生成战斗攻击命令
        combat_cmds = self._combat_attack_commands(player, state, available_actions, self._combat_target)
        if combat_cmds:
            # 战斗状态下，攻击优先级高于发育
            candidates.extend(combat_cmds)
            # 只添加一个发育命令作为后备
            develop = self._develop_commands(player, state, available_actions)
            if develop:
                candidates.append(develop[0])  # 只取第一个发育命令
            candidates.append("forfeit")
            return candidates
        
        # 检查是否需要导弹攻击（高优先级）
        if self._needs_missile_attack(player, state):
            print(f"🤖 [{player.name}] 需要导弹攻击")
            missile_cmds = self._missile_attack_commands(player, state, available_actions)
            if missile_cmds:
                candidates.extend(missile_cmds)
                print(f"🤖 [{player.name}] 导弹攻击候选：{missile_cmds}")
                # 导弹攻击是紧急任务，如果有了就直接返回
                candidates.append("forfeit")
                seen = set()
                deduped = [cmd for cmd in candidates if not (cmd in seen or seen.add(cmd))]
                return deduped
        
        if self._is_critical(player, state):
            print(f"🤖 [{player.name}] 进入极危模式")
            candidates.extend(self._survival_commands(player, state, available_actions))
            print(f"🤖 [{player.name}] 极危候选：{candidates}")
            if candidates:
                return candidates
        
        if self._needs_virus_cure(player, state):
            print(f"🤖 [{player.name}] 进入病毒应急模式")
            candidates.extend(self._virus_cure_commands(player, state))
            print(f"🤖 [{player.name}] 病毒候选：{candidates}")
            if candidates:
                return candidates
        
        # 优先处理击杀机会
        if self._has_kill_opportunity(player, state):
            print(f"🤖 [{player.name}] 有击杀机会")
            kill_attack_cmds = self._attack_commands(player, state, available_actions)
            if kill_attack_cmds:
                candidates.extend(kill_attack_cmds)
                print(f"🤖 [{player.name}] 击杀攻击候选：{kill_attack_cmds}")
        
        # 生成发育命令
        print(f"🤖 [{player.name}] 进入发育模式")
        develop = self._develop_commands(player, state, available_actions)
        print(f"🤖 [{player.name}] 发育候选：{develop}")
        candidates.extend(develop)
        
        # 检查发育是否完成，如果完成则优先攻击
        if self._is_development_complete(player):
            print(f"🤖 [{player.name}] 发育完成，尝试生成find/攻击命令")
            
            # 检查同地点是否有其他玩家
            same_location_targets = []
            for pid in state.player_order:
                if pid == player.player_id:
                    continue
                target = state.get_player(pid)
                if target and target.is_alive() and target.location == player.location:
                    same_location_targets.append(target)
            
            if same_location_targets:
                print(f"🤖 [{player.name}] 同地点发现玩家: {[t.name for t in same_location_targets]}")
                
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
                        print(f"🤖 [{player.name}] 插入优先find命令: {find_cmd}")
                elif "attack" in available_actions:
                    # 如果已经有find关系，直接攻击
                    attack_cmds = self._attack_commands(player, state, available_actions)
                    if attack_cmds and attack_cmds[0] not in candidates:
                        # 将攻击命令插入到发育命令之前
                        candidates.insert(0, attack_cmds[0])
                        print(f"🤖 [{player.name}] 插入优先攻击命令: {attack_cmds[0]}")
        
        if self.personality == "political":
            print(f"🤖 [{player.name}] 进入政治模式")
            political = self._political_commands(player, state, available_actions)
            print(f"🤖 [{player.name}] 政治候选：{political}")
            candidates.extend(political)
        
        # 常规攻击命令（如果前面没有处理过攻击）
        if "attack" in available_actions:
            print(f"🤖 [{player.name}] 生成常规进攻")
            attack = self._attack_commands(player, state, available_actions)
            print(f"🤖 [{player.name}] 进攻候选：{attack}")
            # 避免重复添加相同的攻击命令
            for cmd in attack:
                if cmd not in candidates:
                    candidates.append(cmd)
        
        candidates.append("forfeit")
        seen = set()
        deduped = [cmd for cmd in candidates if not (cmd in seen or seen.add(cmd))]
        print(f"🤖 [{player.name}] 最终候选（去重后）：{deduped}")
        return deduped

    # ════════════════════════════════════════════════════════
    #  阶段判定
    # ════════════════════════════════════════════════════════

    def _is_critical(self, player, state) -> bool:
        if player.hp <= 0.5:
            return True
        if (state.police_engine
                and hasattr(state.police, 'reported_target_id')
                and state.police.reported_target_id == player.player_id
                and hasattr(state.police, 'report_phase')
                and state.police.report_phase in ("dispatched", "enforcing")):
            return True
        lock_count = self._count_locked_by(player, state)
        if lock_count >= 2:
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
            
            # 政治型玩家例外：挨打就跑
            if self.personality == "political":
                # 检查自己是否处于劣势
                if self._is_at_disadvantage(player, current_combat_target):
                    self._in_combat = False  # 退出战斗状态
                    self._combat_target = None
        else:
            # 没有战斗关系，退出战斗状态
            self._in_combat = False
            self._combat_target = None

    def _combat_attack_commands(self, player, state, available_actions, target):
        """生成战斗攻击命令"""
        cmds = []
        
        # 如果不在同一地点，需要移动或使用远程武器
        if player.location != target.location:
            # 检查是否有远程武器
            has_ranged = self._has_ranged_weapon(player)
            if has_ranged and "attack" in available_actions:
                # 使用远程武器攻击
                attack_cmd = self._generate_attack_command(player, target, available_actions)
                if attack_cmd:
                    cmds.append(attack_cmd)
            else:
                # 移动到目标地点
                cmds.append(f"move {target.location}")
        elif "attack" in available_actions:
            # 在同一地点，直接攻击
            attack_cmd = self._generate_attack_command(player, target, available_actions)
            if attack_cmd:
                cmds.append(attack_cmd)
        
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
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if not target or not target.is_alive():
                continue
            if target.hp <= best_weapon_dmg and self._has_attack_prerequisite(player, target, state):
                return True
        return False

    # ════════════════════════════════════════════════════════
    #  生存模式命令
    # ════════════════════════════════════════════════════════

    def _survival_commands(self, player, state, available) -> List[str]:
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
    #  进攻模式命令
    # ════════════════════════════════════════════════════════

    def _attack_commands(self, player, state, available) -> List[str]:
        cmds = []
        target = self._pick_best_target(player, state)
        if not target:
            return cmds
        target_name = target.name
        weapon = self._pick_best_weapon_against(player, target)
        if not weapon:
            return cmds
        weapon_name = weapon.name
        if "attack" in available:
            layer_attr = self._pick_attack_layer(weapon, target)
            if layer_attr:
                layer_str, attr_str = layer_attr
                cmds.append(f"attack {target_name} {weapon_name} {layer_str} {attr_str}")
        if "find" in available and target.location == player.location:
            cmds.append(f"find {target_name}")
        if "lock" in available:
            cmds.append(f"lock {target_name}")
        if "move" in available and target.location != player.location and target.location:
            cmds.append(f"move {target.location}")
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
        has_military_pass = getattr(player, 'has_military_pass', False)  # 新增
        print(f"  📊 [{player.name}] has_credential={has_credential} has_outer={has_outer_armor} "
              f"has_weapon={has_good_weapon}(dmg={self._best_weapon_damage(player)}) "
              f"has_detect={has_detection} has_inner={has_inner_armor} "
              f"has_pass={has_military_pass}")  # 加上pass日志
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
            if loc == "医院":
                plan.append("interact 晶化皮肤手术")
            else:
                plan.append("move 医院")

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
            if target.hp <= my_dmg and self._has_attack_prerequisite(player, target, state):
                score += 100
            if target.location == player.location:
                score += 20
            if target.hp <= 0.5:
                score += 30
            if score > best_score:
                best_score = score
                best_target = target
        return best_target

    # ════════════════════════════════════════════════════════
    #  辅助方法：武器与护甲
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
        max_dmg = 0.5
        for w in getattr(player, 'weapons', []):
            dmg = getattr(w, 'base_damage', 0)
            if isinstance(dmg, (int, float)) and dmg > max_dmg:
                max_dmg = dmg
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
            return self._count_outer_armor(player) == 0  # 或更精确的检查
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
        return self._best_weapon_damage(target)  # 复用同一套逻辑

    def _pick_best_weapon_against(self, player, target):
        if not player.weapons:
            return None
        best = None
        best_score = -1
        for w in player.weapons:
            if not hasattr(w, 'damage') or not hasattr(w, 'damage_type'):
                continue
            w_attr = getattr(w, 'damage_type', '普通')
            effective_set = EFFECTIVE_AGAINST.get(w_attr, set())
            score = w.damage
            if self._can_damage_any_armor(w_attr, target):
                score += 5
            score += w.damage * 10
            if score > best_score:
                best_score = score
                best = w
        return best

    def _pick_attack_layer(self, weapon, target):
        w_attr = getattr(weapon, 'damage_type', '普通')
        effective_set = EFFECTIVE_AGAINST.get(w_attr, set())
        if hasattr(target, 'armor'):
            for attr in ["普通", "魔法", "科技"]:
                if attr in effective_set and self._target_has_armor_layer(target, "外层", attr):
                    return ("外层", attr)
            if not self._target_has_any_outer(target):
                for attr in ["普通", "魔法", "科技"]:
                    if attr in effective_set and self._target_has_armor_layer(target, "内层", attr):
                        return ("内层", attr)
        return ("外层", w_attr)

    def _can_damage_any_armor(self, weapon_attr: str, target) -> bool:
        effective_set = EFFECTIVE_AGAINST.get(weapon_attr, set())
        if not hasattr(target, 'armor'):
            return True
        for layer_type in ["外层", "内层"]:
            for attr in ["普通", "魔法", "科技"]:
                if attr in effective_set and self._target_has_armor_layer(target, layer_type, attr):
                    return True
        return True

    # ========== 增强的护甲检测 ==========
    def _target_has_armor_layer(self, target, layer_type: str, attribute: str) -> bool:
        if not hasattr(target, 'armor'):
            return False
        armor = target.armor
        # 直接读 outer/inner 字典
        if layer_type == "外层" and hasattr(armor, 'outer') and isinstance(armor.outer, dict):
            for key, val in armor.outer.items():
                if val is not None and attribute in str(key):
                    return True
        elif layer_type == "内层" and hasattr(armor, 'inner') and isinstance(armor.inner, dict):
            for key, val in armor.inner.items():
                if val is not None and attribute in str(key):
                    return True
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
        # 优先使用 get_all_active 获取所有未破损护甲层（根据提供的 Player 模型）
        if hasattr(armor, 'get_all_active') and callable(armor.get_all_active):
            active_pieces = armor.get_all_active()
            if isinstance(active_pieces, (list, tuple)):
                for piece in active_pieces:
                    piece_name = getattr(piece, 'name', str(piece))
                    if piece_name == item_name:
                        return True
        # 备选方案：尝试其他常见接口
        else:
            layers: list = []
            if hasattr(armor, 'get_all_layers') and callable(armor.get_all_layers):
                result = armor.get_all_layers()
                if isinstance(result, (list, tuple)):
                    layers = list(result)
                elif isinstance(result, dict):
                    layers = list(result.values())
            elif hasattr(armor, 'layers') and isinstance(armor.layers, (list, dict)):
                if isinstance(armor.layers, dict):
                    layers = list(armor.layers.values())
                else:
                    layers = list(armor.layers)
            elif hasattr(armor, 'outer') and hasattr(armor, 'inner'):
                if isinstance(armor.outer, list):
                    layers.extend(armor.outer)
                if isinstance(armor.inner, list):
                    layers.extend(armor.inner)
            for layer in layers:
                layer_name = getattr(layer, 'name', str(layer))
                if item_name == layer_name:
                    return True
                if hasattr(layer, 'item') and hasattr(layer.item, 'name') and layer.item.name == item_name:
                    return True
        return False

    def _has_item(self, player, item_name: str) -> bool:
        for item in getattr(player, 'items', []):
            name = getattr(item, 'name', str(item))
            if item_name == name:
                return True
        return False

    def _can_take_item(self, player, item_name: str) -> bool:
        """判断玩家是否能够获取指定物品（考虑护甲层和物品重复）"""
        # 优先检查同名护甲层
        if self._has_armor_by_name(player, item_name):
            return False
        # 再检查护甲层属性
        if item_name in self._ARMOR_ITEM_MAP:
            layer_type, attr = self._ARMOR_ITEM_MAP[item_name]
            if self._target_has_armor_layer(player, layer_type, attr):
                return False
        # 检查物品列表
        if self._has_item(player, item_name):
            return False
        return True
    # ====================================

    # ════════════════════════════════════════════════════════
    #  辅助方法：状态查询
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
        if not hasattr(player, 'armor'):
            return 0
        armor = player.armor
        if hasattr(armor, 'outer') and isinstance(armor.outer, dict):
            return sum(1 for v in armor.outer.values() if v is not None)
        return 0
    def _count_inner_armor(self, player) -> int:
        if not hasattr(player, 'armor'):
            return 0
        armor = player.armor
        if hasattr(armor, 'inner') and isinstance(armor.inner, dict):
            return sum(1 for v in armor.inner.values() if v is not None)
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
        # 如果在同一地点，可以直接攻击（游戏规则允许）
        if player.location == target.location:
            return True
    
        # 远程攻击：需要已锁定且目标可见
        markers = state.markers
        if hasattr(markers, 'has_relation') and markers.has_relation(player.player_id, "LOCKED", target.player_id):
            has_detection = getattr(player, 'has_detection', False)
            if hasattr(markers, 'is_visible_to'):
                return markers.is_visible_to(target.player_id, player.player_id, has_detection)
    
        return False


    def _extra_develop_commands(self, player, state):
        cmds = []
        loc = player.location

        if loc == "魔法所":
            # 按优先级学习，跳过已有的
            for spell in ["魔法弹幕", "远程魔法弹幕", "地震", "地动山摇"]:#因为有bug，暂时禁止了AI学习探测魔法
                if self._can_learn(player, spell):
                    cmds.append(f"interact {spell}")
                    break  # 一次只学一个
            if not cmds:
                cmds.append("move 军事基地")

        elif loc == "军事基地":
            if not getattr(player, 'has_military_pass', False):
                cmds.append("interact 办理通行证")
            else:
                for weapon in ["高斯步枪", "导弹控制权", "电磁步枪"]:
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
            # 内层护甲已有就离开
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
            # 必须在警察局才能举报
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
            # 只有看到犯罪行为才举报
            if getattr(p, 'crime_record', False) or getattr(p, 'wanted', False):
                return p
        return None  # 没人犯法就不举报
    

    def _is_development_complete(self, player) -> bool:
        """根据AI人格判断发育是否完成"""
        # 基础能力检查
        has_credential = self._has_credential(player)
        has_outer_armor = self._count_outer_armor(player) > 0
        has_good_weapon = self._best_weapon_damage(player) >= 1.0
        has_detection = getattr(player, 'has_detection', False)
        has_inner_armor = self._count_inner_armor(player) > 0
        
        if self.personality == "aggressive":  # 进攻型
            # 拿到好武器就完成（伤害≥1.0）
            print(f"🤖 [{player.name}] 进攻型发育检查：武器伤害={self._best_weapon_damage(player)}，完成={has_good_weapon}")
            return has_good_weapon
        elif self.personality == "balanced":  # 均衡型
            # 完成初始发育：凭证+外层护甲+武器
            result = has_credential and has_outer_armor and has_good_weapon
            print(f"🤖 [{player.name}] 均衡型发育检查：凭证={has_credential}，外层={has_outer_armor}，武器={has_good_weapon}，完成={result}")
            return result
        elif self.personality == "defensive" or self.personality == "builder":  # 防守型/发育型
            # 完成所有发育：凭证+外层护甲+武器+探测+内层护甲
            result = (has_credential and has_outer_armor and has_good_weapon and 
                    has_detection and has_inner_armor)
            print(f"🤖 [{player.name}] 发育型检查：凭证={has_credential}，外层={has_outer_armor}，武器={has_good_weapon}，探测={has_detection}，内层={has_inner_armor}，完成={result}")
            return result
        else:  # 政治型等
            # 政治型不需要发育完成
            print(f"🤖 [{player.name}] 政治型，不需要发育完成")
            return False

    def _needs_missile_attack(self, player, state):
        """判断是否需要使用导弹攻击（更严格的条件）"""
        # 如果已经在战斗状态，使用_needs_missile_in_combat
        if self._in_combat and self._combat_target:
            return self._needs_missile_in_combat(player, state, self._combat_target)
        
        # 非战斗状态下的导弹需求
        has_missile_control = self._has_weapon_named(player, "导弹控制权")
        
        # 如果不在军事基地且没有导弹控制权，不需要导弹攻击
        if player.location != "军事基地" and not has_missile_control:
            return False
        
        # 找到最高威胁的远程目标
        best_target = None
        best_threat = -1
        
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if not target or not target.is_alive():
                continue
            
            # 目标必须在不同地点才有远程攻击的意义
            if target.location == player.location:
                continue
            
            # 检查目标是否隐身
            is_invisible = getattr(target, 'is_invisible', False)
            if is_invisible:
                # 检查自己是否有探测能力
                has_detection = getattr(player, 'has_detection', False)
                if not has_detection:
                    continue  # 无法探测隐身目标
            
            # 计算威胁分数
            threat_score = self._threat_scores.get(target.name, 0)
            
            # 提高威胁阈值到60，避免轻易使用导弹
            if threat_score > 60 and threat_score > best_threat:
                best_threat = threat_score
                best_target = target
        
        # 如果有高威胁远程目标，则需要导弹攻击
        if best_target and best_threat > 60:
            print(f"🤖 [{player.name}] 检测到高威胁远程目标 {best_target.name}，威胁分数 {best_threat}")
            return True
        
        return False

    def _needs_missile_in_combat(self, player, state, combat_target):
        """战斗状态下是否需要导弹攻击"""
        # 如果目标在同一地点，不需要导弹
        if player.location == combat_target.location:
            return False
        
        # 检查是否有导弹控制权
        has_missile_control = self._has_weapon_named(player, "导弹控制权")
        
        # 如果没有导弹控制权，只在军事基地时才考虑获取
        if not has_missile_control:
            return player.location == "军事基地"
        
        # 有导弹控制权，检查目标是否值得用导弹
        # 目标威胁分数需要很高（>70）才使用导弹
        threat_score = self._threat_scores.get(combat_target.name, 0)
        return threat_score > 70

    def _missile_attack_commands(self, player, state, available_actions, specific_target=None):
        """生成导弹攻击命令序列"""
        cmds = []
        
        # 确定目标
        target = specific_target
        if not target:
            # 找到最高威胁的远程目标
            target = self._find_best_missile_target(player, state)
            if not target:
                return cmds
        
        target_name = target.name
        has_missile_control = self._has_weapon_named(player, "导弹控制权")
        
        print(f"🤖 [{player.name}] 导弹攻击目标：{target_name}，已有控制权：{has_missile_control}")
        
        # 检查目标是否隐身
        is_invisible = getattr(target, 'is_invisible', False)
        if is_invisible:
            # 检查自己是否有探测能力
            has_detection = getattr(player, 'has_detection', False)
            if not has_detection:
                print(f"🤖 [{player.name}] 目标隐身且无探测能力，无法使用导弹")
                return cmds
        
        # 1. 如果没有导弹控制权，先获取
        if not has_missile_control:
            if player.location == "军事基地" and "interact" in available_actions:
                # 检查是否已经有其他玩家在尝试获取导弹
                # 避免多个AI同时尝试获取导弹
                if not self._too_many_players_in_military_base(player, state):
                    cmds.append("interact 导弹控制权")
                    print(f"🤖 [{player.name}] 需要获取导弹控制权")
                else:
                    print(f"🤖 [{player.name}] 太多玩家在军事基地，暂不获取导弹")
                    return []
            else:
                # 如果不在军事基地，先去军事基地
                cmds.append("move 军事基地")
                print(f"🤖 [{player.name}] 前往军事基地获取导弹")
            return cmds
        
        # 2. 检查锁定状态
        markers = state.markers
        is_locked = False
        if hasattr(markers, 'has_relation'):
            is_locked = markers.has_relation(player.player_id, "LOCKED", target.player_id)
        
        # 3. 如果没有锁定，先锁定
        if not is_locked and "lock" in available_actions:
            cmds.append(f"lock {target_name}")
            print(f"🤖 [{player.name}] 需要锁定目标 {target_name}")
            return cmds
        
        # 4. 如果已锁定，发射导弹
        if "attack" in available_actions:
            # 找到导弹武器
            missile_weapon = None
            for w in getattr(player, 'weapons', []):
                if hasattr(w, 'name') and "导弹" in w.name:
                    missile_weapon = w
                    break
            
            if missile_weapon:
                # 选择攻击层和属性
                layer_attr = self._pick_attack_layer(missile_weapon, target)
                if layer_attr:
                    layer_str, attr_str = layer_attr
                    attack_cmd = f"attack {target_name} {missile_weapon.name} {layer_str} {attr_str}"
                    cmds.append(attack_cmd)
                    print(f"🤖 [{player.name}] 发射导弹攻击 {target_name}")
                else:
                    # 如果无法确定攻击层，使用默认值
                    attack_cmd = f"attack {target_name} {missile_weapon.name} 外层 普通"
                    cmds.append(attack_cmd)
                    print(f"🤖 [{player.name}] 使用默认参数发射导弹")
        
        return cmds

    def _too_many_players_in_military_base(self, player, state):
        """检查军事基地是否玩家过多"""
        count = 0
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            other = state.get_player(pid)
            if other and other.is_alive() and other.location == "军事基地":
                count += 1
        return count >= 2  # 如果已经有2个或更多其他玩家在军事基地
    
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
        return my_power < target_power * 0.7  # 自己战力低于对方70%算劣势

    def _calculate_combat_power(self, player):
        """计算战斗战力"""
        power = 0
        
        # 武器伤害
        if hasattr(player, 'weapons'):
            for w in player.weapons:
                if hasattr(w, 'damage'):
                    power += w.damage * 10
        
        # 护甲加成
        armor_health = self._calculate_armor_health(player)
        power *= (1 + armor_health)
        
        return power
