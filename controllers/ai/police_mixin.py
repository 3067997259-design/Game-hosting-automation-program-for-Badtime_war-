"""PoliceMixin —— 警察系统相关：缓存、队长、政治、反击"""
from typing import List, Dict, Optional, Any
from controllers.ai.constants import (
    EFFECTIVE_AGAINST, POLICE_AOE_WEAPONS,
    debug_ai_basic, debug_ai_detailed,
    make_weapon
)
class PoliceMixin:
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
        for _uid, _assign in self._police_dev_assignments.items():
            if _assign.get("phase") == "combat":
                _assign["phase"] = "stationed"
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
        """反击警察：获取有效AOE武器，然后攻击同地点的警察"""
        commands = []
        loc = self._get_location_str(player)
        # 找出受保护的目标，确定其护甲属性
        pe = getattr(state, 'police_engine', None)
        target_armor_attrs = set()
        if pe:
            for pid in state.player_order:
                if pid == player.player_id:
                    continue
                t = state.get_player(pid)
                if t and t.is_alive() and pe.is_protected_by_police(t.player_id):
                    attrs = self._get_outer_armor_attr(t)
                    if not attrs:
                        attrs = self._get_inner_armor_attr(t)
                    target_armor_attrs.update(attrs)
        # 判断是否有"有效的"AOE（能克制目标护甲）
        has_effective_aoe = False
        if self._has_aoe_weapon(player):
            for pid in state.player_order:
                if pid == player.player_id:
                    continue
                t = state.get_player(pid)
                if t and t.is_alive():
                    if self._has_effective_aoe_against(player, t):
                        has_effective_aoe = True
                        break
        if has_effective_aoe:
            # 已有有效AOE → 去打警察（保留原有逻辑）
            pc = self._police_cache or {}
            for unit in pc.get("units", []):
                if unit.get("is_alive") and unit.get("location"):
                    unit_loc = unit["location"]
                    aoe_name = self._get_aoe_weapon_name(player)
                    if aoe_name:
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
            # 没有有效AOE → 根据目标护甲属性决定去哪拿
            # 关键：如果目标有ORDINARY护甲（盾牌），必须拿TECH AOE（电磁步枪）
            # 因为 TECH 克制 ORDINARY，而 MAGIC（地震）不克制 ORDINARY
            from models.equipment import Attribute
            need_tech_aoe = any(a == Attribute.ORDINARY for a in target_armor_attrs)
            if need_tech_aoe:
                # 目标有普通属性护甲 → 必须去军事基地拿电磁步枪
                has_emr = any(w.name == "电磁步枪" for w in getattr(player, 'weapons', []) if w)
                if has_emr:
                    # 已有电磁步枪但未蓄力
                    emr = next((w for w in player.weapons if w and w.name == "电磁步枪"), None)
                    if emr and not getattr(emr, 'is_charged', False):
                        if "special" in available:
                            commands.append("special 蓄力电磁步枪")
                        return commands
                else:
                    has_pass = getattr(player, 'has_military_pass', False)
                    if loc == "军事基地" and "interact" in available:
                        if not has_pass:
                            commands.append("interact 通行证")
                        else:
                            commands.append("interact 电磁步枪")
                    else:
                        commands.append("move 军事基地")
            else:
                # 目标没有普通属性护甲 → 魔法AOE也行，去人少的地方
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
    def _has_effective_aoe_against(self, player, target) -> bool:
        """检查是否拥有能克制目标护甲属性的AOE武器"""
        target_armor_attrs = self._get_outer_armor_attr(target)
        if not target_armor_attrs:
            target_armor_attrs = self._get_inner_armor_attr(target)
        if not target_armor_attrs:
            return True  # 目标无甲，任何AOE都有效
        aoe_names = self._get_all_aoe_weapon_names(player)
        for aoe_name in aoe_names:
            aoe_weapon = next((w for w in getattr(player, 'weapons', [])
                            if w and w.name == aoe_name), None)
            if not aoe_weapon:
                from models.equipment import make_weapon
                aoe_weapon = make_weapon(aoe_name)
            if not aoe_weapon:
                continue
            # 跳过需要蓄力但未蓄力的
            if (getattr(aoe_weapon, 'requires_charge', False)
                    and getattr(aoe_weapon, 'charge_mandatory', True)
                    and not getattr(aoe_weapon, 'is_charged', False)):
                continue
            w_attr = self._get_weapon_attr(aoe_weapon)
            effective_set = EFFECTIVE_AGAINST.get(w_attr, set())
            if any(a in effective_set for a in target_armor_attrs):
                return True
        return False
    def _is_stuck_by_police(self, player, state) -> bool:
        """检查是否所有存活目标都受警察保护，且自己没有有效的AOE武器
        两种情况都算 stuck:
        1. 完全没有AOE武器
        2. 有AOE武器但属性全部被目标护甲克制（如只有地震打盾牌）
        """
        pe = getattr(state, 'police_engine', None)
        if not pe:
            return False
        alive_targets = []
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            p = state.get_player(pid)
            if p and p.is_alive():
                alive_targets.append(p)
        if not alive_targets:
            return False
        # 找出所有受警察保护的目标
        protected_targets = [t for t in alive_targets if pe.is_protected_by_police(t.player_id)]
        if not protected_targets:
            return False
        # 如果有不受保护的目标，不算stuck（可以打别人）
        if len(protected_targets) < len(alive_targets):
            return False
        # 所有目标都受保护 → 检查是否有有效AOE
        if not self._has_aoe_weapon(player):
            return True  # 完全没有AOE
        # 有AOE但检查是否对所有受保护目标都无效
        for t in protected_targets:
            if self._has_effective_aoe_against(player, t):
                return False  # 至少有一个目标能打穿
        return True  # 有AOE但全部无效
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
    def _get_all_aoe_weapon_names(self, player) -> List[str]:
        """返回玩家拥有的所有AOE武器名列表（武器列表优先，然后是已学法术）"""
        names = []
        seen = set()
        for w in getattr(player, 'weapons', []):
            name = w.name if hasattr(w, 'name') else str(w)
            if name in POLICE_AOE_WEAPONS and name not in seen:
                names.append(name)
                seen.add(name)
        learned = getattr(player, 'learned_spells', set())
        if "地动山摇" in learned and "地动山摇" not in seen:
            names.append("地动山摇")
            seen.add("地动山摇")
        if "地震" in learned and "地震" not in seen:
            names.append("地震")
            seen.add("地震")
        return names
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

    
