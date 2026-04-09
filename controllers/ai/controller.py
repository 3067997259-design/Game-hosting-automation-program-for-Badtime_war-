"""
BasicAIController —— 基础AI控制器（v2.0 Mixin 重构版）
═══════════════════════════════════════════════════════
原 controllers/ai_basic.py 拆分为 Mixin 模块后的主入口。
"""
from typing import List, Dict, Optional, Any
import random

from controllers.base import PlayerController
from controllers.ai.constants import (
    NEED_PROVIDERS,
    debug_ai_basic, debug_ai_detailed, debug_ai_candidate_commands,
    debug_ai_combat_state, debug_ai_development_plan,
)
from controllers.ai.helpers_mixin import HelpersMixin
from controllers.ai.hoshino_mixin import HoshinoMixin
from controllers.ai.evaluation_mixin import EvaluationMixin
from controllers.ai.choose_mixin import ChooseMixin
from controllers.ai.combat_mixin import CombatMixin
from controllers.ai.develop_mixin import DevelopMixin
from controllers.ai.police_mixin import PoliceMixin
from controllers.ai.events_mixin import EventsMixin



class BasicAIController(
    HoshinoMixin, # type: ignore
    HelpersMixin, # type: ignore
    EvaluationMixin, # type: ignore
    ChooseMixin, # type: ignore
    CombatMixin, # type: ignore
    DevelopMixin, # type: ignore
    PoliceMixin, # type: ignore
    EventsMixin, # type: ignore
    PlayerController,
):
    """
    基础AI：按阶段判定 + 候选命令优先级 + validate 过滤。
    6种人格: balanced, aggressive, defensive, political, assassin, builder
    """

    # ════════════════════════════════════════════════════════
    #  __init__ (原 lines 199-246)
    # ════════════════════════════════════════════════════════

    def __init__(self, personality: str = "balanced"):
        super().__init__()
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
        self._danger_mode = False

        # 导弹相关
        self._missile_cooldown = 0

        # 引用缓存
        self._player = None
        self._game_state = None

        # 警察状态缓存
        self._police_cache: Optional[Dict] = None
        self._current_phase: str = "development"
        self._last_commands: List[str] = []
        self._should_become_captain_flag: bool = False

        self._virus_active: bool = False
        self._virus_location: Optional[str] = None

        # 警察发育状态追踪（political 队长用）
        self._police_dev_assignments: Dict[str, Dict] = {}
        self._police_dev_initialized = False

        self._low_threat_streak: Dict[str, int] = {}
        self._players_who_attacked: set = set()

        self._last_combat_location = None
        self._combat_just_ended_at = None

        # 病毒预防标记（每局一次）
        self._virus_prevention_done: bool = False
        # 行动标记（轮次内）
        self._action_used: bool = False
        # EMR蓄力标记（全息影像发动前）
        self._emr_needs_charge_before_hologram: bool = False

        # 星野战术宏队列
        self._hoshino_macro_queue: Optional[list] = None

    # ════════════════════════════════════════════════════════
    #  接口实现：get_command (原 lines 282-308)
    # ════════════════════════════════════════════════════════

    def get_command(
        self, player: Any, game_state: Any,
        available_actions: List[str], context: Optional[Dict] = None
    ) -> str:
        self.player_name = player.name
        self._my_id = player.player_id
        attempt = context.get("attempt", 1) if context else 1
        situation = (context or {}).get("situation", "")

        # 星野战术宏输入：从预生成队列逐条弹出
        situation = (context or {}).get("situation", "")
        if situation == "hoshino_tactical_input":
            return self._hoshino_get_tactical_command(player, game_state, available_actions)

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
    #  核心：候选命令生成 (原 lines 858-1154)
    # ════════════════════════════════════════════════════════

    def _generate_candidates(self, player, state, available_actions: List[str]) -> List[str]:
        self._my_id = player.player_id
        self.player_name = player.name
        self._player = player
        self._game_state = state

        current_round = getattr(state, 'current_round', 0)
        if current_round > self._round_number:
            self._round_number = current_round

        self._update_threat_scores(player, state)
        self._read_police_state(state)
        self._update_combat_status(player, state)
        self._cleanup_dead_players(state)

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

        # ===== 星野 Terror 状态 =====
        if self._has_hoshino_talent(player) and self._hoshino_is_terror(player):
            debug_ai_basic(player.name, "Terror 状态：全图攻击")
            return self._hoshino_terror_command(player, state, available_actions)

        # ===== 星野战术指令已解锁：优先使用 special Hoshino =====
        if (self._has_hoshino_talent(player)
                and self._hoshino_tactical_unlocked(player)
                and not self._hoshino_is_terror(player)):
            # 有目标时使用战术宏
            target = self._hoshino_find_target(player, state)
            if target and "special" in available_actions:
                # 清空旧队列，下次 get_command 时会重新生成
                self._hoshino_macro_queue = []
                debug_ai_basic(player.name, f"星野战术宏：目标 {target.name}")
                return ["special Hoshino", "forfeit"]

        # ===== 队长指挥 =====
        if getattr(player, 'is_captain', False) and "police_command" in available_actions:
            debug_ai_basic(player.name, "作为队长，生成警察指挥命令")
            captain_cmds = self._cmd_captain(player, state, available_actions)
            if captain_cmds:
                candidates.append(captain_cmds[0])

        # ===== 病毒应急 =====                          # ★ 改动：病毒应急提前到全息影像之前
        if self._needs_virus_cure(player, state):
            debug_ai_basic(player.name, "进入病毒应急模式")
            candidates.extend(self._cmd_virus(player, state, available_actions))
            if candidates:
                candidates.append("forfeit")
                return candidates

        # ===== G2 EMR蓄力准备：即将发动全息影像但EMR未蓄力 =====
        if (getattr(self, '_emr_needs_charge_before_hologram', False)
                and player.talent and hasattr(player.talent, 'name')
                and player.talent.name == "请一直，注视着我"
                and not getattr(player.talent, 'active', False)):  # 影像还没激活
            emr = next((w for w in player.weapons if w and w.name == "电磁步枪"), None)
            if emr and not getattr(emr, 'is_charged', False) and "special" in available_actions:
                debug_ai_basic(player.name, "G2准备发动：先蓄力电磁步枪")
                candidates.insert(0, "special 蓄力电磁步枪")
                candidates.append("forfeit")
                self._emr_needs_charge_before_hologram = False  # 清除标记
                return candidates
            else:
                self._emr_needs_charge_before_hologram = False  # EMR已蓄力或无法蓄力，清除标记

        # ===== 全息影像激活中：留在影像区域用AOE扫场 =====  # ★ 改动：从 line 326 提前到此处
        if (player.talent and hasattr(player.talent, 'name')
                and player.talent.name == "请一直，注视着我"
                and getattr(player.talent, 'active', False)):
            hologram = player.talent
            my_loc = self._get_location_str(player)
            raw_hologram_loc = getattr(hologram, 'location', None)
            hologram_loc = str(raw_hologram_loc) if raw_hologram_loc is not None else None

            if my_loc == hologram_loc:
                # 在影像区域内：优先用AOE攻击被拉入的目标
                debug_ai_basic(player.name, "全息影像激活中：AOE扫场模式")
                same_loc = self._get_same_location_targets(player, state)
                if same_loc:
                    # 检查是否应该先蓄力EMR再攻击
                    emr = next((w for w in player.weapons if w and w.name == "电磁步枪"), None)
                    if emr and not getattr(emr, 'is_charged', False) and "special" in available_actions:
                        # 陶瓷护甲只免疫电流眩晕，不免疫伤害，EMR的0.5 AOE伤害始终有效
                        debug_ai_basic(player.name, "全息影像中：蓄力电磁步枪")
                        candidates.insert(0, "special 蓄力电磁步枪")
                        candidates.append("forfeit")
                        return candidates
                    attack_cmds = self._cmd_attack(player, state, available_actions)
                    if attack_cmds:
                        candidates.extend(attack_cmds)
                        candidates.append("forfeit")
                        return candidates
                # 同地点没有目标（都跑了）→ 拿当前地点的装备
                if "interact" in available_actions:
                    dev_cmds = self._cmd_develop_hologram(player, state, available_actions)
                    if dev_cmds:
                        candidates.extend(dev_cmds)
                candidates.append("forfeit")
                return candidates
            else:
                # 不在影像区域：移动回去
                if "move" in available_actions and hologram_loc:
                    candidates.insert(0, f"move {hologram_loc}")
                    candidates.append("forfeit")
                    return candidates
                # hologram_loc 为 None（异常情况）：兜底 forfeit，避免 fallthrough
                candidates.append("forfeit")
                return candidates

        # ===== 救世主状态 =====
        if self._is_in_savior_state(player) and self._get_effective_hp(player) > 0.5:
            debug_ai_basic(player.name, "救世主状态激活，优先攻击")
            last_attacker = self._get_last_attacker(player, state)
            if last_attacker:
                attack_cmds = self._cmd_attack(player, state, available_actions, last_attacker)
                if attack_cmds:
                    candidates.extend(attack_cmds)
                    candidates.append("forfeit")
                    return candidates
            attack_cmds = self._cmd_attack(player, state, available_actions)
            if attack_cmds:
                candidates.extend(attack_cmds)
                candidates.append("forfeit")
                return candidates

        # ===== 病毒预防 =====
        if not self._virus_prevention_done and not self._has_virus_immunity(player):
            if self._someone_has_virus_immunity(state):
                self._virus_prevention_done = True
                debug_ai_basic(player.name, "检测到有人持有病毒免疫，主动预防")
                prevention_cmds = self._cmd_virus(player, state, available_actions)
                if prevention_cmds:
                    candidates.extend(prevention_cmds)
                    candidates.append("forfeit")
                    return candidates

        # ===== Assassin 主动放毒 =====
        if self._should_release_virus(player, state) and "special" in available_actions:
            debug_ai_basic(player.name, "Assassin 在医院放毒！")
            candidates.append("special 释放病毒")

        # ===== 危险情况 =====
        if self._is_critical(player, state):
            self._danger_mode = True

        if self._danger_mode:
            if self._is_danger_resolved(player):
                debug_ai_basic(player.name, "危险解除，退出危险模式")
                self._danger_mode = False
            else:
                debug_ai_basic(player.name, "处于危险模式")
                if self._is_pursued_by_police(player, state):
                    if self._can_fight_police(player, state):
                        fight_cmds = self._cmd_fight_police(player, state, available_actions)
                        if fight_cmds:
                            candidates.extend(fight_cmds)
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

        # ===== Political 非队长 =====
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
                seen = set()
                deduped = []
                for cmd in candidates:
                    if cmd not in seen:
                        seen.add(cmd)
                        deduped.append(cmd)
                return deduped
            debug_ai_basic(player.name, "political fallback 激活：采用 balanced 行动策略")

        if self._political_develop_only and self._in_combat:
            self._in_combat = False
            self._combat_target = None

        # ===== 救世主紧急集火 =====
        if not self._in_combat:
            for pid in state.player_order:
                if pid == player.player_id:
                    continue
                t = state.get_player(pid)
                if t and t.is_alive() and self._is_in_savior_state(t):
                    # 有远程武器 → 立刻进入战斗
                    # 自己也是救世主就算了
                    has_ranged = not self._is_in_savior_state(player) and any(
                        self._get_weapon_range(w) == "ranged"
                        for w in getattr(player, 'weapons', []) if w
                    )
                    if has_ranged:
                        debug_ai_basic(player.name, f"紧急：发现救世主 {t.name}，用远程武器集火")
                        self._in_combat = True
                        self._combat_target = t
                        break
        # ===== 超新星紧急分散 =====
        if (not self._has_firefly_talent(player)
                and self._firefly_supernova_threat(player, state)):
            my_loc = self._get_location_str(player)
            same_loc_count = len(self._get_same_location_targets(player, state))
            if same_loc_count >= 2 and "move" in available_actions:
                # 同地点有2+个其他玩家，紧急分散
                # 选择没有其他玩家的地点
                empty_locs = []
                for loc in ["home", "商店", "医院", "魔法所", "军事基地", "警察局"]:
                    if loc == my_loc:
                        continue
                    enemies = self._count_enemies_at(loc, player, state)
                    if enemies == 0:
                        empty_locs.append(loc)
                if empty_locs:
                    import random
                    dest = random.choice(empty_locs)
                    candidates.insert(0, f"move {dest}")
                    # 不直接return，让后续逻辑也生成备选命令
        # ===== 星野 Terror / 自我怀疑集火 =====
        if not self._has_hoshino_talent(player):
            for pid in state.player_order:
                if pid == player.player_id:
                    continue
                t = state.get_player(pid)
                if not t or not t.is_alive():
                    continue
                t_talent = getattr(t, 'talent', None)
                if not t_talent:
                    continue
                # Terror 存在 → 最高优先级集火
                if getattr(t_talent, 'is_terror', False):
                    debug_ai_basic(player.name, f"Terror 存在！集火 {t.name}")
                    attack_cmds = self._cmd_attack(player, state, available_actions, t)
                    if attack_cmds:
                        candidates.extend(attack_cmds)
                        candidates.append("forfeit")
                        return candidates
                # 自我怀疑 → 紧急集火（下回合变 Terror）
                if getattr(t_talent, 'self_doubt_pending', False):
                    debug_ai_basic(player.name, f"星野自我怀疑！紧急集火 {t.name}")
                    attack_cmds = self._cmd_attack(player, state, available_actions, t)
                    if attack_cmds:
                        candidates.extend(attack_cmds)
                        candidates.append("forfeit")
                        return candidates

        # ===== 星野发育路径（战术宏入口已在上方处理）=====
        if self._has_hoshino_talent(player):
            if not self._is_development_complete(player, state):
                dev = self._cmd_develop_hoshino(player, state, available_actions)
                if dev:
                    candidates.extend(dev)
                    candidates.append("forfeit")
                    return candidates

        # ===== 战斗状态 =====                           # ★ 改动：删除 hologram pass-through
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
                if target_ref and self._all_weapons_countered(player, target_ref):
                    debug_ai_basic(player.name, "所有武器被克制，寻找新武器")
                    rearm_cmds = self._cmd_rearm(player, state, available_actions)
                    if rearm_cmds:
                        candidates.extend(rearm_cmds)
                        candidates.append("forfeit")
                        return candidates

        # ===== 火萤专用逻辑 =====                       # ★ 改动：此处不再有全息影像块（已提前）
        if self._has_firefly_talent(player):
            # 超新星优先：有超新星就用
            if self._has_supernova(player) and "move" in available_actions:
                best_loc = self._pick_supernova_target(player, state)
                if best_loc:
                    debug_ai_basic(player.name, f"火萤：超新星过载，目标地点={best_loc}")
                    candidates.insert(0, f"move {best_loc}")
                    candidates.append("forfeit")
                    return candidates

            # Phase 1（debuff 前）：拿到刀就冲
            if not self._firefly_debuff_active(player):
                has_knife = any(w.name == "小刀" for w in player.weapons if w)
                if has_knife:
                    # 有刀就攻击，不等发育完成
                    debug_ai_basic(player.name, "火萤Phase1：有刀就冲")
                    attack_cmds = self._cmd_attack(player, state, available_actions)
                    if attack_cmds:
                        candidates.extend(attack_cmds)
                        # 备用发育（只拿护甲，不拿更多武器）
                        dev = self._cmd_develop_firefly_minimal(player, state, available_actions)
                        candidates.extend(dev)
                        candidates.append("forfeit")
                        return candidates

            # Phase 2/3（debuff 后）：攻击优先于发育
            if self._firefly_debuff_active(player):
                debug_ai_basic(player.name, "火萤Phase2/3：debuff已生效，攻击优先")
                attack_cmds = self._cmd_attack(player, state, available_actions)
                if attack_cmds:
                    candidates.extend(attack_cmds)
                # 发育作为备选
                dev = self._cmd_develop(player, state, available_actions)
                for cmd in dev:
                    if cmd not in candidates:
                        candidates.append(cmd)
                candidates.append("forfeit")
                return candidates

        # ===== 火萤击杀机会 =====
        if (self._has_firefly_talent(player)
            and not self._political_develop_only
            and self._has_firefly_kill_opportunity(player, state)):
            debug_ai_basic(player.name, "火萤发现击杀机会，打断发育！")
            kill_cmds = self._cmd_attack(player, state, available_actions)
            if kill_cmds:
                candidates.extend(kill_cmds)
                dev = self._cmd_develop(player, state, available_actions)
                if dev:
                    candidates.append(dev[0])
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

        # 发育受阻：develop 为空但发育未完成
        if not develop and not self._is_development_complete(player, state):
            if self.personality in ("aggressive", "assassin", "balanced") or self._political_in_balanced_fallback:
                debug_ai_basic(player.name, "发育受阻，转为进攻冲散人群")
                attack_cmds = self._cmd_attack(player, state, available_actions)
                for cmd in attack_cmds:
                    if cmd not in candidates:
                        candidates.append(cmd)
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

        # ===== 所有目标受警察保护且无AOE =====
        if self._is_stuck_by_police(player, state):
            debug_ai_basic(player.name, "所有目标受警察保护且无法穿透，去获取有效武器")
            aoe_cmds = self._cmd_fight_police(player, state, available_actions)
            for cmd in aoe_cmds:
                if cmd not in candidates:
                    candidates.insert(0, cmd)

        # ===== 政治型补充 =====
        if self.personality == "political":
            political = self._cmd_police_political(player, state, available_actions)
            candidates.extend(political)

        # ===== 常规攻击补充 =====
        is_political_no_attack = self._political_develop_only or (
            self.personality == "political" and self._political_fallback_level == "none"
        )
        if not is_political_no_attack and (
            "attack" in available_actions or "find" in available_actions
            or "lock" in available_actions
        ):
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

    def _has_supernova(self, player) -> bool:
        """检查火萤IV型是否有超新星可用"""
        talent = getattr(player, 'talent', None)
        if not talent:
            return False
        return getattr(talent, 'has_supernova', False)

    def _pick_supernova_target(self, player, state) -> Optional[str]:
        """选择敌人最多的地点作为超新星目标（包含当前位置）"""
        my_loc = self._get_location_str(player)
        best_loc = None
        best_count = 0

        # 包含当前位置（超新星允许同地点移动）
        all_locations = ["home", "商店", "医院", "魔法所", "军事基地", "警察局"]
        if my_loc not in all_locations:
            all_locations.append(my_loc)

        for loc in all_locations:
            count = self._count_enemies_at(loc, player, state)
            if count > best_count:
                best_count = count
                best_loc = loc

        # 必须有敌人才使用超新星
        if best_loc and best_count > 0:
            return best_loc
        return None  # 没有敌人，不浪费超新星

# ════════════════════════════════════════════════════════════════
#  工厂函数 (原 lines 4460-4502)
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
    """
    valid_personalities = [
        "aggressive", "defensive", "assassin",
        "balanced", "builder", "political"
    ]
    if personality not in valid_personalities:
        debug_ai_basic(player_name,
            f"未知人格类型 '{personality}'，使用 'balanced'")
        personality = "balanced"

    controller = BasicAIController(personality=personality) # type: ignore
    debug_ai_basic(player_name,
        f"创建AI控制器: personality={personality}")
    return controller


def create_random_ai_controller(player_name: str = "", **kwargs) -> BasicAIController:
    import random as _rand
    personalities = [
        "aggressive", "defensive", "assassin",
        "balanced", "builder", "political"
    ]
    personality = _rand.choice(personalities)
    return create_ai_controller(personality=personality, player_name=player_name, **kwargs)