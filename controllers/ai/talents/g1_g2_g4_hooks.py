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
            candidates.append("forfeit")
            return candidates

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
    """全息影像(G2)天赋AI钩子"""
    talent_name = "请一直，注视着我"

    def __init__(self, controller: Any):
        self._ctrl = controller

    def is_development_complete(self, player: Any, state: Any) -> Optional[bool]:
        if not self._is_my_talent(player):
            return None
        talent = player.talent
        exhausted = (getattr(talent, 'used', False) and getattr(talent, 'max_uses', 0) <= 0
                     and not getattr(talent, 'active', False))
        if exhausted:
            real_weapons = [w for w in player.weapons if w and getattr(w, 'name', '') != "拳击"]
            has_ranged = any(w.name in ("魔法弹幕", "远程魔法弹幕", "高斯步枪") for w in real_weapons)
            return has_ranged and GameQuery.count_outer_armor(player) >= 2
        has_two_aoe = GameQuery.count_distinct_aoe_attrs(player) >= 2
        return has_two_aoe and GameQuery.count_outer_armor(player) >= 1

    def get_develop_commands(
        self, player: Any, state: Any, available: List[str]
    ) -> Optional[List[str]]:
        if not self._is_my_talent(player):
            return None
        talent = player.talent
        exhausted = (getattr(talent, 'used', False)
                     and getattr(talent, 'max_uses', 0) <= 0
                     and not getattr(talent, 'active', False))
        if exhausted:
            post_cmds = self._get_post_hologram_commands(player, state, available)
            return post_cmds if post_cmds else None
        return self._get_hologram_commands(player, state, available)

    def _get_hologram_commands(
        self, player: Any, state: Any, available: List[str]
    ) -> List[str]:
        commands: List[str] = []
        loc = GameQuery.get_location_str(player)
        outer = GameQuery.count_outer_armor(player)
        vouchers = getattr(player, 'vouchers', 0)
        has_pass = getattr(player, 'has_military_pass', False)
        learned = GameQuery.get_learned_spells(player)
        has_magic_aoe = "地震" in learned or "地动山摇" in learned
        has_tech_aoe = any(w.name == "电磁步枪" for w in player.weapons if w)

        if "interact" in available:
            if GameQuery.is_at_home(player):
                if vouchers < 1:
                    commands.append("interact 凭证")
                if outer < 1 and not GameQuery.has_armor_by_name(player, "盾牌"):
                    commands.append("interact 盾牌")
                if not any(w.name == "小刀" for w in player.weapons if w):
                    commands.append("interact 小刀")
            elif loc == "魔法所":
                if "地震" not in learned:
                    commands.append("interact 地震")
                elif "地动山摇" not in learned:
                    commands.append("interact 地动山摇")
                if "魔法护盾" not in learned and outer < 2:
                    commands.append("interact 魔法护盾")
            elif loc == "军事基地":
                if not has_pass:
                    commands.append("interact 通行证")
                elif not has_tech_aoe:
                    commands.append("interact 电磁步枪")
                elif not any(
                    w.name in ("小刀", "高斯步枪")
                    for w in player.weapons if w and w.name != "拳击"
                ):
                    commands.append("interact 高斯步枪")
                if outer < 2 and not GameQuery.has_armor_by_name(player, "AT力场"):
                    commands.append("interact AT力场")
            elif loc == "商店":
                if vouchers >= 1 and not any(w.name == "小刀" for w in player.weapons if w):
                    commands.append("interact 小刀")
                if vouchers >= 1 and outer < 2 and not GameQuery.has_armor_by_name(player, "陶瓷护甲"):
                    commands.append("interact 陶瓷护甲")
                if vouchers < 1:
                    commands.append("interact 打工")

        if "interact" in available and not commands:
            has_real_melee = any(
                w.name != "拳击" and GameQuery.get_weapon_range(w) == "melee"
                for w in player.weapons if w
            )
            if not has_real_melee:
                if GameQuery.is_at_home(player):
                    if not any(w.name == "小刀" for w in player.weapons if w):
                        commands.append("interact 小刀")
                elif loc == "商店" and vouchers >= 1:
                    if not any(w.name == "小刀" for w in player.weapons if w):
                        commands.append("interact 小刀")

        if "special" in available:
            emr = next((w for w in player.weapons if w and w.name == "电磁步枪"), None)
            if emr and not getattr(emr, 'is_charged', False):
                return ["special 蓄力电磁步枪", *commands]

        if "move" in available and not commands:
            if not has_magic_aoe and loc != "魔法所":
                commands.append("move 魔法所")
            elif not has_tech_aoe and loc != "军事基地":
                commands.append("move 军事基地")
            elif outer < 2:
                next_loc = GameQuery.find_safe_location(player, state)
                if next_loc and GameQuery.normalize_location(next_loc) != GameQuery.normalize_location(loc):
                    commands.append(f"move {next_loc}")
            else:
                enemy_loc = GameQuery.find_nearest_enemy_location(player, state, {})
                if enemy_loc and enemy_loc != loc:
                    commands.append(f"move {enemy_loc}")
        return commands

    def _get_post_hologram_commands(
        self, player: Any, state: Any, available: List[str]
    ) -> List[str]:
        commands: List[str] = []
        loc = GameQuery.get_location_str(player)
        learned = GameQuery.get_learned_spells(player)
        real_weapons = [w for w in player.weapons if w and getattr(w, 'name', '') != "拳击"]
        has_ranged = any(w.name in ("魔法弹幕", "远程魔法弹幕", "高斯步枪") for w in real_weapons)
        if has_ranged:
            return []
        if "interact" in available:
            if loc == "魔法所":
                if "魔法弹幕" not in learned:
                    commands.append("interact 魔法弹幕")
                elif "远程魔法弹幕" not in learned:
                    commands.append("interact 远程魔法弹幕")
            elif loc == "军事基地":
                has_pass = getattr(player, 'has_military_pass', False)
                if not has_pass:
                    commands.append("interact 通行证")
                elif not any(w.name == "高斯步枪" for w in real_weapons):
                    commands.append("interact 高斯步枪")
        if "move" in available and not commands:
            if loc != "魔法所" and "魔法弹幕" not in learned:
                commands.append("move 魔法所")
            elif loc != "军事基地":
                commands.append("move 军事基地")
        return commands

    def handle_choose(
        self, player: Any, state: Any, situation: str,
        options: List[str], context: Dict,
    ) -> Optional[str]:
        if situation == "talent_t0":
            talent_name = context.get("talent_name", "")
            if "注视" not in talent_name:
                return None
            should_activate = False
            if player and state:
                my_loc = GameQuery.get_location_str(player)
                pc = context.get("police_cache") or {}
                outer = GameQuery.count_outer_armor(player)
                inner = GameQuery.count_inner_armor(player)
                total_armor = outer + inner
                nearby_players = GameQuery.get_same_location_targets(player, state)
                nearby_police_count = 0
                for unit in pc.get("units", []):
                    if (unit.get("is_alive")
                            and unit.get("location")
                            and unit["location"] == my_loc):
                        nearby_police_count += 1
                nearby_total = len(nearby_players) + nearby_police_count
                has_two_aoe = GameQuery.count_distinct_aoe_attrs(player) >= 2
                been_attacked_by = context.get("been_attacked_by", set())

                if (not should_activate
                        and self.is_development_complete(player, state)
                        and total_armor >= 1
                        and nearby_total >= 1):
                    emr = next((w for w in player.weapons if w and w.name == "电磁步枪"), None)
                    if emr and not getattr(emr, 'is_charged', False):
                        other_aoe = [w for w in player.weapons
                                     if w and w.name != "电磁步枪" and w.name != "拳击"
                                     and GameQuery.get_weapon_range(w) == "area"]
                        if other_aoe:
                            should_activate = True
                        else:
                            self._ctrl._emr_needs_charge_before_hologram = True
                    else:
                        should_activate = True

                if not should_activate and player.hp <= 1.0 and been_attacked_by:
                    for attacker_name in been_attacked_by:
                        for pid in state.player_order:
                            atk = state.get_player(pid)
                            if (atk and atk.is_alive()
                                    and atk.name == attacker_name
                                    and GameQuery.same_location(player, atk)):
                                should_activate = True
                                break
                        if should_activate:
                            break

                if not should_activate and has_two_aoe:
                    has_captain = pc.get("captain_id") is not None
                    if has_captain:
                        should_activate = True

                if not should_activate and has_two_aoe:
                    for pid in state.player_order:
                        if pid == player.player_id:
                            continue
                        t = state.get_player(pid)
                        if (t and t.is_alive() and t.talent
                                and getattr(t.talent, 'has_supernova', False)):
                            should_activate = True
                            break

                if not should_activate:
                    markers = getattr(state, 'markers', None)
                    if markers and hasattr(markers, 'has_relation'):
                        for pid in state.player_order:
                            if pid == player.player_id:
                                continue
                            t = state.get_player(pid)
                            if t and t.is_alive() and markers.has_relation(
                                    player.player_id, "ENGAGED_WITH", pid):
                                if GameQuery.same_location(player, t):
                                    if GameQuery.count_distinct_aoe_attrs(player) >= 1:
                                        should_activate = True
                                break

            if should_activate:
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
        """全息影像激活中的AOE扫场模式"""
        if not self._is_my_talent(player) or not getattr(player.talent, 'active', False):
            return None
        my_loc = str(getattr(player, 'location', ''))
        hologram_loc = str(getattr(player.talent, 'location', ''))
        if my_loc != hologram_loc:
            if "move" in available:
                return [f"move {hologram_loc}", "forfeit"]
            return ["forfeit"]
        # 在影像区域内：AOE扫场
        same_loc = self._ctrl._get_same_location_targets(player, state)
        if same_loc:
            emr = next((w for w in player.weapons if w and w.name == "电磁步枪"), None)
            if emr and not getattr(emr, 'is_charged', False) and "special" in available:
                return ["special 蓄力电磁步枪", "forfeit"]
            attack_cmds = self._ctrl._cmd_attack(player, state, available)
            if attack_cmds:
                return [*attack_cmds, "forfeit"]
        return None  # 走常规流程

    def _is_my_talent(self, player: Any) -> bool:
        t = getattr(player, 'talent', None)
        return bool(t and getattr(t, 'name', '') == self.talent_name)


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
