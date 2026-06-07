"""火萤IV型(G1) + 全息影像(G2) + 救世主(G4) 天赋AI钩子"""

from __future__ import annotations
from typing import Dict, List, Optional, Any
import random
from controllers.ai.command_builder.develop_commands import DevelopCommandBuilder
from controllers.ai.talents.base_hook import BaseTalentAIHook
from controllers.ai.game_query import GameQuery
from controllers.ai.constants import debug_ai_basic


class FireflyAIHook(BaseTalentAIHook):
    """火萤IV型(G1)天赋AI钩子"""
    talent_name = "火萤IV型-完全燃烧"

    def __init__(self, controller: Any):
        self._ctrl = controller

    def is_development_complete(self, player: Any, state: Any) -> Optional[bool]:
        if not self._is_my_talent(player):
            return None
        real_weapons = [w for w in player.weapons if w and getattr(w, 'name', '') != "拳击"]
        if not GameQuery.firefly_debuff_active(player):
            return len(real_weapons) >= 1
        has_sharpened = any(w.name == "小刀" and getattr(w, 'base_damage', 0) >= 2 for w in real_weapons)
        has_gauss = any(w.name == "高斯步枪" for w in real_weapons)
        return has_sharpened and has_gauss

    def get_develop_commands(
        self, player: Any, state: Any, available: List[str]
    ) -> Optional[List[str]]:
        if not self._is_my_talent(player):
            return None
        commands: List[str] = []
        loc = GameQuery.get_location_str(player)
        weapons = getattr(player, 'weapons', [])
        real_weapons = [w for w in weapons if w and getattr(w, 'name', '') != "拳击"]
        outer = GameQuery.count_outer_armor(player)
        vouchers = getattr(player, 'vouchers', 0)
        has_pass = getattr(player, 'has_military_pass', False)

        sharpen = DevelopCommandBuilder._build_sharpen_command(player, available)
        if sharpen:
            return sharpen

        if GameQuery.firefly_debuff_active(player):
            has_sharpened_knife = any(
                w.name == "小刀" and getattr(w, 'base_damage', 0) >= 2
                for w in real_weapons
            )
            has_gauss = any(w.name == "高斯步枪" for w in real_weapons)
            if "interact" in available:
                if not has_sharpened_knife:
                    has_knife = any(w.name == "小刀" for w in real_weapons)
                    if not has_knife:
                        if GameQuery.is_at_home(player) or loc == "商店":
                            commands.append("interact 小刀")
                    else:
                        has_stone = any(
                            getattr(item, 'name', '') == "磨刀石"
                            for item in getattr(player, 'items', [])
                        )
                        if not has_stone and loc == "商店":
                            commands.append("interact 磨刀石" if vouchers >= 1 else "interact 打工")
                if not has_gauss and loc == "军事基地":
                    commands.append("interact 通行证" if not has_pass else "interact 高斯步枪")
            if has_gauss and "special" in available and not commands:
                gauss = next((w for w in weapons if w and w.name == "高斯步枪"), None)
                if gauss and not getattr(gauss, 'is_charged', False):
                    commands.append("special 蓄力高斯步枪")
            if "move" in available and not commands:
                if not has_sharpened_knife:
                    has_knife = any(w.name == "小刀" for w in real_weapons)
                    if not has_knife and not GameQuery.is_at_home(player):
                        commands.append("move home")
                    elif has_knife and not any(
                        getattr(item, 'name', '') == "磨刀石"
                        for item in getattr(player, 'items', [])
                    ) and loc != "商店":
                        commands.append("move 商店")
                elif not has_gauss and loc != "军事基地":
                    commands.append("move 军事基地")
            return commands

        if "interact" in available:
            if GameQuery.is_at_home(player):
                if vouchers < 1:
                    commands.append("interact 凭证")
                if not any(w.name == "小刀" for w in real_weapons):
                    commands.append("interact 小刀")
                if outer < 1 and not GameQuery.has_armor_by_name(player, "盾牌"):
                    commands.append("interact 盾牌")
            elif loc == "商店":
                if vouchers < 1:
                    commands.append("interact 打工")
                if outer < 1 and not GameQuery.has_armor_by_name(player, "陶瓷护甲"):
                    commands.append("interact 陶瓷护甲")
                has_unsharpened = any(
                    w.name == "小刀" and getattr(w, 'base_damage', 0) < 2
                    for w in weapons if w
                )
                has_stone = any(
                    getattr(item, 'name', '') == "磨刀石"
                    for item in getattr(player, 'items', [])
                )
                if has_unsharpened and not has_stone and vouchers >= 1:
                    commands.append("interact 磨刀石")
            elif loc == "魔法所":
                learned = GameQuery.get_learned_spells(player)
                if "魔法弹幕" not in learned and len(real_weapons) < 2:
                    commands.append("interact 魔法弹幕")
                if "魔法护盾" not in learned and outer < 1:
                    commands.append("interact 魔法护盾")
                if "地震" not in learned:
                    commands.append("interact 地震")
                if "地震" in learned and "地动山摇" not in learned:
                    commands.append("interact 地动山摇")
            elif loc == "军事基地":
                if not has_pass:
                    commands.append("interact 通行证")
                else:
                    if len(real_weapons) < 2:
                        commands.extend(["interact 高斯步枪", "interact 电磁步枪"])
                    if outer < 1 and not GameQuery.has_armor_by_name(player, "AT力场"):
                        commands.append("interact AT力场")
            elif loc == "医院" and vouchers < 1:
                commands.append("interact 打工")

        if "special" in available and not commands:
            for weapon_name in ("高斯步枪", "电磁步枪"):
                weapon = next((w for w in weapons if w and w.name == weapon_name), None)
                if weapon and not getattr(weapon, 'is_charged', False):
                    commands.append(f"special 蓄力{weapon_name}")
                    break

        if "move" in available and not commands:
            next_loc = GameQuery.find_safe_location(player, state)
            if next_loc and GameQuery.normalize_location(next_loc) != GameQuery.normalize_location(loc):
                commands.append(f"move {next_loc}")
        return commands

    def _build_minimal_develop_commands(
        self, player: Any, state: Any, available: List[str]
    ) -> List[str]:
        commands: List[str] = []
        loc = GameQuery.get_location_str(player)
        outer = GameQuery.count_outer_armor(player)
        if "interact" in available:
            if GameQuery.is_at_home(player) and outer < 1:
                if not GameQuery.has_armor_by_name(player, "盾牌"):
                    commands.append("interact 盾牌")
            elif loc == "商店" and outer < 1:
                vouchers = getattr(player, 'vouchers', 0)
                if vouchers >= 1 and not GameQuery.has_armor_by_name(player, "陶瓷护甲"):
                    commands.append("interact 陶瓷护甲")
        return commands

    def modify_target_score(self, target: Any, base_score: float, player: Any) -> float:
        target_name = getattr(target, 'name', '')
        s = base_score
        is_passive = target_name not in getattr(self._ctrl, '_players_who_attacked', set())
        if is_passive:
            s += 70 + self._ctrl._estimate_power(target) * 0.5
        t_talent = getattr(target, 'talent', None)
        if t_talent and getattr(t_talent, 'name', '') == "愿负世，照拂黎明":
            if not getattr(t_talent, 'is_savior', False):
                if self._ctrl._get_divinity(target) <= 6:
                    s += 120
                else:
                    s += 60
            else:
                s += 40
        enemy_best = self._ctrl._best_weapon_damage(target)
        if enemy_best >= 2.0:
            s += 80
        return s

    def should_override_candidates(
        self, player: Any, state: Any, available: List[str]
    ) -> Optional[List[str]]:
        """处理火萤超新星/Phase专用逻辑（替代controller.py L1189-1240）"""
        if not self._is_my_talent(player):
            return None

        candidates = []

        # 超新星优先
        if self._ctrl._has_supernova(player) and "move" in available:
            best_loc = self._pick_supernova_target(player, state)
            if best_loc:
                debug_ai_basic(player.name, f"火萤：超新星过载，目标地点={best_loc}")
                candidates.insert(0, f"move {best_loc}")
                candidates.append("forfeit")
                return candidates

        # Phase 1（debuff前）：拿到刀就冲
        if not GameQuery.firefly_debuff_active(player):
            has_knife = any(w.name == "小刀" for w in player.weapons if w)
            if has_knife:
                debug_ai_basic(player.name, "火萤Phase1：有刀就冲")
                attack_cmds = self._ctrl._cmd_attack(player, state, available)
                if attack_cmds:
                    candidates.extend(attack_cmds)
                    dev = self._build_minimal_develop_commands(player, state, available)
                    candidates.extend(dev)
                    candidates.append("forfeit")
                    return candidates

        # Phase 2/3（debuff后）：攻击优先
        if GameQuery.firefly_debuff_active(player):
            debug_ai_basic(player.name, "火萤Phase2/3：debuff已生效，攻击优先")
            attack_cmds = self._ctrl._cmd_attack(player, state, available)
            if attack_cmds:
                candidates.extend(attack_cmds)
            dev = self.get_develop_commands(player, state, available) or []
            for cmd in dev:
                if cmd not in candidates:
                    candidates.append(cmd)
            if candidates:
                candidates.append("forfeit")
                return candidates
            # 无攻击/发育目标 → 超新星跳脸或追击敌人
            if self._ctrl._has_supernova(player) and "move" in available:
                best_loc = self._pick_supernova_target(player, state)
                if best_loc:
                    debug_ai_basic(player.name,
                        f"火萤Phase2/3：超新星跳脸，目标={best_loc}")
                    return [f"move {best_loc}", "forfeit"]
            my_loc = GameQuery.get_location_str(player)
            enemy_loc = GameQuery.find_nearest_enemy_location(player, state)
            if (enemy_loc and "move" in available
                    and GameQuery.normalize_location(enemy_loc) != GameQuery.normalize_location(my_loc)):
                debug_ai_basic(player.name, f"火萤Phase2/3：追击敌人 → {enemy_loc}")
                return [f"move {enemy_loc}", "forfeit"]
            return None  # 无事可做，不接管

        # 击杀机会
        kill_target = self._ctrl._find_firefly_kill_target(player, state)
        if kill_target:
            debug_ai_basic(player.name, "火萤发现击杀机会，打断发育！")
            kill_cmds = self._ctrl._cmd_attack(player, state, available, forced_target=kill_target)
            if kill_cmds:
                candidates.extend(kill_cmds)
                dev = self.get_develop_commands(player, state, available) or []
                if dev:
                    candidates.append(dev[0])
                candidates.append("forfeit")
                return candidates

        return None  # 不接管，走常规流程

    def _pick_supernova_target(self, player: Any, state: Any) -> Optional[str]:
        """选择敌人最多的地点"""
        my_loc = str(getattr(player, 'location', ''))
        best_loc = None
        best_count = 0
        for loc in ["home", "商店", "医院", "魔法所", "军事基地", "警察局"]:
            count = 0
            for pid in state.player_order:
                if pid == player.player_id:
                    continue
                p = state.get_player(pid)
                if p and p.is_alive() and str(getattr(p, 'location', '')) == loc:
                    count += 1
            if count > best_count:
                best_count = count
                best_loc = loc
        if best_loc and best_count > 0:
            return best_loc
        return None

    def _is_my_talent(self, player: Any) -> bool:
        t = getattr(player, 'talent', None)
        return bool(t and getattr(t, 'name', '') == self.talent_name)


class HologramAIHook(BaseTalentAIHook):
    """G2 v0.6 AI钩子：舞台演唱策略 + 声部选择 + 卡牌决策"""
    talent_name = "请一直，注视着我"

    def __init__(self, controller: Any):
        self._ctrl = controller

    # ════════════════════════════════════════════════════════════════
    #  发育
    # ════════════════════════════════════════════════════════════════

    def is_development_complete(self, player: Any, state: Any) -> Optional[bool]:
        if not self._is_my_talent(player):
            return None
        # v0.6 G2 在舞台内不攻击 → 发育只需武器+护甲保生存
        real_weapons = [w for w in player.weapons if w and getattr(w, 'name', '') != "拳击"]
        has_weapon = len(real_weapons) >= 1
        return has_weapon and GameQuery.count_outer_armor(player) >= 2

    def get_develop_commands(
        self, player: Any, state: Any, available: List[str]
    ) -> Optional[List[str]]:
        if not self._is_my_talent(player):
            return None
        if state and getattr(state, 'ish_bosheth', None) is not None:
            return None  # 已激活，走演唱逻辑
        return self._get_basic_develop_commands(player, state, available)

    def _get_basic_develop_commands(
        self, player: Any, state: Any, available: List[str]
    ) -> List[str]:
        """v0.6 简化发育：武器+护甲+凭证"""
        commands: List[str] = []
        loc = GameQuery.get_location_str(player)
        outer = GameQuery.count_outer_armor(player)
        vouchers = getattr(player, 'vouchers', 0)
        has_real_weapon = any(w.name != "拳击" for w in player.weapons if w)

        if "interact" in available:
            if GameQuery.is_at_home(player):
                if vouchers < 1:
                    commands.append("interact 凭证")
                if outer < 1:
                    commands.append("interact 盾牌")
                if not has_real_weapon:
                    commands.append("interact 小刀")
            elif loc == "商店" and vouchers >= 1:
                if not has_real_weapon:
                    commands.append("interact 小刀")
                if outer < 2:
                    commands.append("interact 陶瓷护甲")
            elif loc == "军事基地":
                has_pass = getattr(player, 'has_military_pass', False)
                if not has_pass:
                    commands.append("interact 通行证")
                elif not has_real_weapon:
                    commands.append("interact 高斯步枪")
                if outer < 2 and not GameQuery.has_armor_by_name(player, "AT力场"):
                    commands.append("interact AT力场")

        if "move" in available and not commands:
            if outer < 2 and loc != "军事基地":
                commands.append("move 军事基地")
            elif not has_real_weapon and loc != "军事基地":
                commands.append("move 军事基地")
            else:
                enemy_loc = GameQuery.find_nearest_enemy_location(player, state, {})
                if enemy_loc and enemy_loc != loc:
                    commands.append(f"move {enemy_loc}")
        return commands

    # ════════════════════════════════════════════════════════════════
    #  T0 激活判断
    # ════════════════════════════════════════════════════════════════

    def handle_choose(
        self, player: Any, state: Any, situation: str,
        options: List[str], context: Dict,
    ) -> Optional[str]:
        if not options:
            return None

        # ── T0 天赋激活 ──
        if situation == "talent_t0":
            return self._choose_t0_activation(player, state, options, context)

        # ── v0.6 G2 演唱决策 ──
        if situation == "g2_sing_song":
            return self._choose_song(player, state, options)
        if situation == "g2_sing_rhythm":
            return self._choose_rhythm(player, state, options)
        if situation == "g2_sing_target":
            return self._choose_sing_target(player, state, options, context)
        if situation == "g2_melody_seat":
            return self._choose_melody_seat(player, state, options)

        # ── v0.6 初始声部选择 ──
        if situation == "g2_voice_choice":
            return self._choose_initial_voice(player, state, options)

        # ── v0.6 物料牌决策 ──
        if situation == "g2_play_card":
            return self._choose_card_to_play(player, state, options)
        if situation == "g2_discard":
            return self._choose_card_to_discard(player, state, options)
        if situation == "g2_pickup_floor":
            return self._choose_floor_pickup(player, state, options)

        # ── 卡牌子决策 ──
        if situation in ("g2_card_front_row", "g2_card_spotlight_photo",
                         "g2_card_support_cheer", "g2_card_boo",
                         "g2_card_bouquet", "g2_card_mediation_acc",
                         "g2_card_mediation_str", "g2_card_program_tidy"):
            return self._pick_random_option(options)
        if situation == "g2_card_blank_stub":
            return self._choose_blank_stub(player, options)
        if situation == "g2_card_exchange_give":
            return self._pick_random_option(options)
        if situation == "g2_card_exchange_target":
            return self._pick_random_option(options)
        if situation == "g2_card_program_tidy_discard":
            return self._pick_random_option(options)
        if situation == "g2_card_program_tidy_pick":
            return self._pick_random_option(options)

        # ── v0.6 G2 Chorus 指挥 ──
        if situation == "g2_command_chorus":
            return self._pick_random_option(options)

        # ── v2.0 G2×G5 duet 决策 ──
        if situation == "duet_vote":
            # G2 倾向于接受（TE 比 BE 好）
            for opt in options:
                if "赞成" in opt:
                    return opt
            return options[0]

        if situation == "displacement_choose":
            # 位移选择：优先选对 G2 有利的座位（离按钮远）
            return options[-1] if len(options) > 1 else options[0]

        if situation == "embrace":
            # 按玩家人格：aggressive 拥抱 G2（攻），cautious 拥抱 G5（防）
            personality = context.get("personality", "balanced")
            if personality == "aggressive":
                for opt in options:
                    if "G2" in opt:
                        return opt
            elif personality in ("cautious", "passive"):
                for opt in options:
                    if "G5" in opt:
                        return opt
            return options[0] if options else ""

        if situation == "pick_location":
            # 按角色选择地点偏好
            personality = context.get("personality", "balanced")
            if personality == "aggressive":
                for loc in ["军事基地", "警察局", "魔法所"]:
                    if loc in options:
                        return loc
            return options[0] if options else ""

        if situation == "pick_item":
            return options[0] if options else ""

        return None

    # ── T0 激活 ──────────────────────────────────────────────────

    def _choose_t0_activation(self, player, state, options, context) -> Optional[str]:
        """判断是否激活 G2。"""
        talent_name = context.get("talent_name", "")
        if "注视" not in talent_name:
            return None

        if not player or not state:
            return self._pick_reject_option(options)

        outer = GameQuery.count_outer_armor(player)
        dev_ok = self.is_development_complete(player, state)
        alive_count = len(state.alive_players())
        been_attacked = bool(context.get("been_attacked_by", set()))

        # v0.6 激活条件：发育完成 + （有护甲 或 被攻击）
        # 2 人局降低门槛：发育完成即可
        if alive_count <= 2:
            should_activate = dev_ok
        else:
            should_activate = dev_ok and (outer >= 1 or been_attacked)

        # HP 低 + 被攻击 → 防御性激活
        if not should_activate and player.hp <= 1.0 and been_attacked:
            should_activate = True

        if should_activate:
            for opt in options:
                if "发动" in opt:
                    return opt
        return self._pick_reject_option(options)

    # ── 演唱决策核心 ──────────────────────────────────────────────

    def _choose_song(self, player, state, options) -> Optional[str]:
        """v0.6 G2 选曲策略。"""
        ish = getattr(state, 'ish_bosheth', None)
        if not ish:
            return self._pick_last_or_forfeit(options)

        result = None

        # 1. 旋律优先（免费，一次性）
        for opt in options:
            if "第三间章" in opt:
                result = opt
        if not result:
            for opt in options:
                if "第二间章" in opt:
                    result = opt

        # 2. 统计声部分布
        if not result:
            str_real = self._count_voice_real(state, ish, "strappando")
            acc_real = self._count_voice_real(state, ish, "accarezzevole")
            regard = ish.regard

            # 3. 高危 Strappando → Sognando（if regard ≥ 2）
            if str_real >= 1 and regard >= 2:
                for opt in options:
                    if "Sognando" in opt or "追寻那道光" in opt:
                        result = opt

            # 4. 多个 Acc + 有敌意 → Before light Dolente
            if not result and acc_real >= 2 and regard >= 2:
                for opt in options:
                    if "Dolente" in opt or "Before light" in opt:
                        result = opt

            # 5. 有盟友 → Soave
            if not result and regard >= 1:
                for opt in options:
                    if "Soave" in opt or "追寻那道光" in opt:
                        result = opt

            # 6. 困住敌人 → Placido/Zeffiroso
            if not result and regard >= 1:
                for opt in options:
                    if "拼接遗憾" in opt:
                        result = opt

        # 7. 保 regard → forfeit
        if not result:
            for opt in options:
                if "放弃" in opt:
                    result = opt
        if not result:
            result = self._pick_last_or_forfeit(options)

        phase = getattr(ish, 'phase', 'active') if ish else '?'
        debug_ai_basic(player.name, f"G2 选曲 → {result} (phase={phase}, Regard={getattr(ish, 'regard', '?')})")
        return result

    def _choose_rhythm(self, player, state, options) -> Optional[str]:
        """选节奏：优先低成本（保 Regard）。"""
        for opt in options:
            if "Soave" in opt or "温柔" in opt:
                return opt
        for opt in options:
            if "Placido" in opt or "平静" in opt:
                return opt
        for opt in options:
            if "Riposato" in opt or "休息" in opt:
                return opt
        # 预算够 → 高费
        ish = getattr(state, 'ish_bosheth', None)
        if ish and ish.regard >= 4:
            for opt in options:
                if "Sognando" in opt or "Dolente" in opt or "Zeffiroso" in opt:
                    return opt
        return options[0] if options else None

    def _choose_sing_target(self, player, state, options, context) -> Optional[str]:
        """选听者：优先高威胁目标。"""
        threat_scores = getattr(self._ctrl, '_threat_scores', {})
        ish = getattr(state, 'ish_bosheth', None)

        best_opt = None
        best_score = -999
        for opt in options:
            # 从 options 中找匹配的玩家名
            target = self._find_target_by_name_in_options(state, ish, opt)
            score = threat_scores.get(target.name if target else opt, 0)
            if score > best_score:
                best_score = score
                best_opt = opt
        return best_opt or self._pick_random_option(options)

    def _choose_melody_seat(self, player, state, options) -> Optional[str]:
        """选旋律座位：优先 Strappando 最多的座位。"""
        ish = getattr(state, 'ish_bosheth', None)
        if not ish:
            return self._pick_random_option(options)

        best_seat = None
        best_str = -1
        for seat_name in options:
            str_count = self._count_str_at_seat(state, ish, seat_name)
            if str_count > best_str:
                best_str = str_count
                best_seat = seat_name
        return best_seat or self._pick_random_option(options)

    # ── 声部选择 ──────────────────────────────────────────────────

    def _choose_initial_voice(self, player, state, options) -> Optional[str]:
        """AI 初始声部选择策略。"""
        threat_scores = getattr(self._ctrl, '_threat_scores', {})
        hp = player.hp
        outer = GameQuery.count_outer_armor(player)

        # 高HP + 有护甲 + 有敌人想杀 → Accarezzevole（积极攻击）
        if hp >= 1.5 and outer >= 1 and self._has_threatening_enemy(state, threat_scores):
            for opt in options:
                if "Accarezzevole" in opt or "入戏" in opt:
                    return opt

        # 低HP 或 无护甲 → Strappando（需要减伤/离场）
        if hp <= 0.5 or outer == 0:
            for opt in options:
                if "Strappando" in opt or "反抗" in opt:
                    return opt

        # 默认 → Indifferenza（观望）
        for opt in options:
            if "Indifferenza" in opt or "抽离" in opt:
                return opt
        return self._pick_random_option(options)

    # ── 物料牌决策 ────────────────────────────────────────────────

    def _choose_card_to_play(self, player, state, options) -> Optional[str]:
        """选择打出哪张物料牌。优先战斗牌 > 防御牌 > 通用牌。"""
        if "不打" in options and len(options) <= 2:
            return "不打"

        # 战斗中优先
        combat_cards = ["荧光棒", "聚光合影", "后台通行证", "撕票", "倒彩"]
        defense_cards = ["耳塞", "花束"]
        utility_cards = ["前排票", "小卡交换", "空白票根", "场刊整理"]

        for card in combat_cards:
            if card in options:
                return card
        for card in defense_cards:
            if card in options:
                return card
        for card in utility_cards:
            if card in options:
                return card
        for opt in options:
            if opt != "不打":
                return opt
        return "不打"

    def _choose_card_to_discard(self, player, state, options) -> Optional[str]:
        """选择弃置哪张牌：优先无用的。"""
        low_priority = ["场刊整理", "空白票根", "前排票", "小卡交换"]
        for card in low_priority:
            if card in options:
                return card
        return self._pick_random_option(options)

    def _choose_floor_pickup(self, player, state, options) -> Optional[str]:
        """选择拾取哪张掉落牌。"""
        if "不拾取" in options and len(options) <= 2:
            return "不拾取"
        return self._pick_random_option(options)

    def _choose_blank_stub(self, player, options) -> Optional[str]:
        """空白票根效果选择。"""
        if getattr(player, 'encore_layers', 0) > 0:
            for opt in options:
                if "安可" in opt:
                    return opt
        for opt in options:
            if "牵连" in opt and getattr(player, 'stage_entangle', []):
                return opt
        for opt in options:
            if "摸" in opt:
                return opt
        return self._pick_random_option(options)

    # ════════════════════════════════════════════════════════════════
    #  候选覆盖（v0.6：舞台激活中 = 必须 special）
    # ════════════════════════════════════════════════════════════════

    def should_override_candidates(
        self, player: Any, state: Any, available: List[str]
    ) -> Optional[List[str]]:
        """v0.6: 舞台激活中（含 duet），G2 只能 sing。"""
        ish = getattr(state, 'ish_bosheth', None)
        if not ish or ish.phase not in ("active", "duet"):
            return None
        if not self._is_my_talent(player):
            return None
        if player.player_id != ish.g2_owner_id:
            return None

        if "special" in available and ish.regard > 0:
            return ["special", "forfeit"]
        return ["forfeit"]

    # ════════════════════════════════════════════════════════════════
    #  辅助方法
    # ════════════════════════════════════════════════════════════════

    def _is_my_talent(self, player: Any) -> bool:
        t = getattr(player, 'talent', None)
        return bool(t and getattr(t, 'name', '') == self.talent_name)

    @property
    def state(self):
        return getattr(self._ctrl, '_game_state', None)

    @staticmethod
    def _count_voice_real(state, ish, voice: str) -> int:
        count = 0
        for pid in ish.participants:
            p = state.get_player(pid)
            if (p and p.is_alive()
                    and getattr(p, 'emotion', None) == voice):
                count += 1
        return count

    @staticmethod
    def _count_str_at_seat(state, ish, seat_name: str) -> int:
        count = 0
        for pid in ish.participants:
            p = state.get_player(pid)
            if (p and p.is_alive() and p.location == seat_name
                    and getattr(p, 'emotion', None) == "strappando"):
                count += 1
        for c in ish.chorus_list:
            if (c.is_alive() and c.location == seat_name
                    and getattr(c, 'emotion', None) == "strappando"):
                count += 1
        return count

    @staticmethod
    def _find_target_by_name_in_options(state, ish, option: str):
        for pid in ish.participants:
            p = state.get_player(pid)
            if p and p.name in option:
                return p
        for c in ish.chorus_list:
            if c.is_alive() and c.name in option:
                return c
        return None

    @staticmethod
    def _has_threatening_enemy(state, threat_scores) -> bool:
        for pid in state.player_order:
            p = state.get_player(pid)
            if p and p.is_alive() and threat_scores.get(p.name, 0) > 30:
                return True
        return False

    @staticmethod
    def _pick_random_option(options: List[str]) -> Optional[str]:
        import random
        return random.choice(options) if options else None

    @staticmethod
    def _pick_last_or_forfeit(options: List[str]) -> Optional[str]:
        for opt in options:
            if "放弃" in opt:
                return opt
        return options[-1] if options else None

    @staticmethod
    def _pick_reject_option(options: List[str]) -> Optional[str]:
        for opt in options:
            if "不发动" in opt or "正常" in opt:
                return opt
        return options[-1] if options else None


class SaviorAIHook(BaseTalentAIHook):
    """愿负世(G4)天赋AI钩子"""
    talent_name = "愿负世，照拂黎明"

    def __init__(self, controller: Any):
        self._ctrl = controller

    def modify_target_score(self, target: Any, base_score: float, player: Any) -> float:
        t_talent = getattr(target, 'talent', None)
        if not t_talent or getattr(t_talent, 'name', '') != self.talent_name:
            return base_score
        if getattr(t_talent, 'is_savior', False):
            s = base_score + 200
            temp_hp = getattr(t_talent, 'temp_hp', 0)
            s += temp_hp * 20
            duration = getattr(t_talent, 'savior_duration', 0)
            if duration <= 3:
                s += 100
            return s
        divinity = getattr(t_talent, 'divinity', 0)
        if divinity >= 8:
            if self._ctrl._has_firefly_talent(player):
                return base_score + 120
            return base_score - 40
        if divinity <= 4:
            return base_score + 30
        return base_score

    def handle_choose(
        self, player: Any, state: Any, situation: str,
        options: List[str], context: Dict,
    ) -> Optional[str]:
        if situation == "talent_t0":
            talent_name = context.get("talent_name", "")
            if "愿负世" not in talent_name:
                return None
            talent = getattr(player, 'talent', None)
            divinity = getattr(talent, 'divinity', 0) if talent else 0
            if divinity >= 8:
                for opt in options:
                    if "发动" in opt:
                        return opt
            elif player and player.hp <= 1.0 and divinity >= 4:
                nearby = GameQuery.get_same_location_targets(player, state) if state else []
                if nearby:
                    for opt in options:
                        if "发动" in opt:
                            return opt
            for opt in options:
                if "不发动" in opt or "正常" in opt:
                    return opt
            return options[-1]
        return None

    def should_override_candidates(
        self, player: Any, state: Any, available: List[str]
    ) -> Optional[List[str]]:
        """救世主状态：优先攻击"""
        if not self._ctrl._is_in_savior_state(player) or self._ctrl._get_effective_hp(player) <= 0.5:
            return None
        debug_ai_basic(player.name, "救世主状态激活，优先攻击")
        last_attacker = self._ctrl._get_last_attacker(player, state)
        if last_attacker:
            attack_cmds = self._ctrl._cmd_attack(player, state, available, last_attacker)
            if attack_cmds:
                return [*attack_cmds, "forfeit"]
        attack_cmds = self._ctrl._cmd_attack(player, state, available)
        if attack_cmds:
            return [*attack_cmds, "forfeit"]
        return None
