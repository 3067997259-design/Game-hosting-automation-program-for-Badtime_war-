"""PoliceCommandBuilder —— 队长指挥、政治行动、警察反击

从 police_mixin.py 复制，所有 self._xxx 属性访问改为通过 GameQuery / ctx 参数。
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

from controllers.ai.constants import (
    EFFECTIVE_AGAINST, POLICE_AOE_WEAPONS,
    debug_ai_basic, debug_ai_detailed,
    make_weapon,
)

if TYPE_CHECKING:
    from controllers.ai.game_query import GameQuery
    from controllers.ai.context import OrchestratorContext


class PoliceCommandBuilder:
    """警察指令构建器。"""

    def __init__(self, query: "GameQuery"):
        self._query = query

    # ════════════════════════════════════════════════════════
    #  队长指挥
    # ════════════════════════════════════════════════════════

    def build_captain(
        self,
        player: Any,
        state: Any,
        strategy: Any,
        available: List[str],
        ctx: "OrchestratorContext",
    ) -> List[str]:
        """队长指挥命令（复制自 PoliceMixin._cmd_captain）。"""
        Q = self._query
        commands: List[str] = []
        if "police_command" not in available:
            return commands
        pc = ctx.police_cache or {}
        if not pc.get("is_captain"):
            return commands
        units = pc.get("units", [])
        alive_units = [u for u in units if u.get("is_alive")]
        active_units = [u for u in units if u.get("is_alive") and u.get("is_active", True)]
        disabled_units = [u for u in units if u.get("is_alive") and not u.get("is_active", True)]
        if not alive_units:
            return commands

        # study 优先
        authority = pc.get("authority", 0)
        if authority <= 1 and "study" in available:
            loc = Q.get_location_str(player)
            if loc == "警察局":
                return ["study"]

        # 初始化发育计划
        assignments = self._get_assignments(ctx)
        criminal_target = self._find_criminal_target(player, state, ctx)
        if criminal_target:
            new_target_id = criminal_target.player_id
            if ctx.ai_state and ctx.last_criminal_target_id != new_target_id:
                ctx.ai_state.police_dev_initialized = False
                ctx.police_dev_initialized = False
            if ctx.ai_state:
                ctx.ai_state.last_criminal_target_id = new_target_id
            ctx.last_criminal_target_id = new_target_id
        if not ctx.police_dev_initialized:
            self._init_police_dev_plan(alive_units, player, state, ctx)
            if ctx.ai_state:
                ctx.ai_state.police_dev_initialized = True
            ctx.police_dev_initialized = True

        personality = ctx.personality

        # political 优先唤醒
        if personality == "political" and disabled_units:
            wake_cmd = self._police_wake_step(disabled_units, state)
            if wake_cmd:
                return [wake_cmd]

        # 攻击犯罪目标
        if criminal_target:
            attack_cmd = self._police_attack_criminal(
                criminal_target, active_units, state, ctx)
            if attack_cmd:
                return [attack_cmd]
        # 重置 combat phase
        assignments = self._get_assignments(ctx)
        for _uid, _assign in assignments.items():
            if _assign.get("phase") == "combat":
                _assign["phase"] = "stationed"

        # 唤醒
        if disabled_units:
            wake_cmd = self._police_wake_step(disabled_units, state)
            if wake_cmd:
                return [wake_cmd]

        # 发育
        dev_cmd = self._police_develop_step(active_units, ctx)
        if dev_cmd:
            return [dev_cmd]

        # 部署
        deploy_cmd = self._police_deploy_step(alive_units, ctx)
        if deploy_cmd:
            return [deploy_cmd]

        return commands

    # ════════════════════════════════════════════════════════
    #  政治行动
    # ════════════════════════════════════════════════════════

    def build_police_political(
        self,
        player: Any,
        state: Any,
        strategy: Any,
        available: List[str],
        ctx: "OrchestratorContext",
    ) -> List[str]:
        """政治行动命令（复制自 PoliceMixin._cmd_police_political）。"""
        Q = self._query
        fallback = ctx.political_fallback_level
        if fallback in ("full_balanced", "develop_only"):
            return []
        commands: List[str] = []
        loc = Q.get_location_str(player)
        is_police = getattr(player, 'is_police', False)
        is_captain = getattr(player, 'is_captain', False)

        # 集结
        if "assemble" in available:
            police = getattr(state, 'police', None)
            if police and police.report_phase == "reported" and police.reporter_id == player.player_id:
                commands.append("assemble")
                return commands

        # 追踪指引
        if "track_guide" in available:
            police = getattr(state, 'police', None)
            if police and police.reporter_id == player.player_id:
                pe = getattr(state, 'police_engine', None)
                if pe:
                    can_track, _ = pe.can_track_guide(player.player_id)
                    if can_track:
                        commands.append("track")
                        return commands

        # 举报
        police_data = getattr(state, 'police', None)
        report_phase = getattr(police_data, 'report_phase', 'idle') if police_data else 'idle'
        has_captain = police_data.has_captain() if police_data and hasattr(police_data, 'has_captain') else False
        is_self_criminal = (police_data.is_criminal(player.player_id)
                            if police_data and hasattr(police_data, 'is_criminal') else False)
        if ("report" in available
                and is_police
                and report_phase == "idle"
                and not has_captain
                and not is_self_criminal):
            can_remote = False
            talent = getattr(player, 'talent', None)
            if talent and hasattr(talent, 'can_remote_report'):
                can_remote = talent.can_remote_report()
            if loc != "警察局" and not can_remote:
                pass
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
        has_captain_flag = police_data.has_captain() if police_data and hasattr(police_data, 'has_captain') else False
        if ("election" in available
                and is_police
                and not is_captain
                and not has_captain_flag
                and loc == "警察局"):
            commands.append("election")

        # 指定执法目标
        if "designate" in available and is_captain:
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
                    score = ctx.threat_scores.get(target.name, 0)
                    if target_is_criminal:
                        score += 100
                    if score > best_score:
                        best_score = score
                        best_target = target
            if best_target:
                commands.append(f"designate {best_target.name}")

        # 移动到警察局
        if "move" in available and not commands and loc != "警察局":
            if not is_police:
                if Q.count_outer_armor(player) >= 1:
                    commands.append("move 警察局")
            elif is_police and not is_captain:
                commands.append("move 警察局")

        return commands

    # ════════════════════════════════════════════════════════
    #  警察反击
    # ════════════════════════════════════════════════════════

    def build_fight_police(
        self,
        player: Any,
        state: Any,
        strategy: Any,
        available: List[str],
        ctx: "OrchestratorContext",
    ) -> List[str]:
        """反击警察命令（复制自 PoliceMixin._cmd_fight_police）。"""
        Q = self._query
        commands: List[str] = []
        loc = Q.get_location_str(player)

        # 找受保护目标的护甲属性
        pe = getattr(state, 'police_engine', None)
        target_armor_attrs: set = set()
        if pe:
            for pid in state.player_order:
                if pid == player.player_id:
                    continue
                t = state.get_player(pid)
                if t and t.is_alive() and pe.is_protected_by_police(t.player_id):
                    attrs = Q.get_outer_armor_attr(t)
                    if not attrs:
                        attrs = Q.get_inner_armor_attr(t)
                    target_armor_attrs.update(attrs)

        # 判断是否有有效 AOE
        has_effective_aoe = False
        if Q.has_aoe_weapon(player):
            for pid in state.player_order:
                if pid == player.player_id:
                    continue
                t = state.get_player(pid)
                if t and t.is_alive():
                    if Q.has_effective_aoe_against(player, t):
                        has_effective_aoe = True
                        break

        pc = ctx.police_cache or {}
        if has_effective_aoe:
            for unit in pc.get("units", []):
                if unit.get("is_alive") and unit.get("location"):
                    unit_loc = unit["location"]
                    aoe_name = Q.get_aoe_weapon_name(player)
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
            from utils.attribute import Attribute
            need_tech_aoe = any(a == Attribute.ORDINARY for a in target_armor_attrs)
            if need_tech_aoe:
                has_emr = any(w.name == "电磁步枪" for w in getattr(player, 'weapons', []) if w)
                if has_emr:
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
                enemies_magic = Q.count_enemies_at("魔法所", player, state)
                enemies_military = Q.count_enemies_at("军事基地", player, state)
                if enemies_magic <= enemies_military:
                    if loc == "魔法所" and "interact" in available:
                        learned = Q.get_learned_spells(player)
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

    # ════════════════════════════════════════════════════════
    #  内部辅助
    # ════════════════════════════════════════════════════════

    @staticmethod
    def _get_assignments(ctx) -> Dict:
        return getattr(ctx, 'police_dev_assignments', {}) or {}

    def _find_criminal_target(self, player, state, ctx):
        """找到最高威胁的犯罪目标。"""
        pc = ctx.police_cache or {}
        report_target = pc.get("report_target")
        if report_target and pc.get("report_phase") == "dispatched":
            target_player = state.get_player(report_target)
            if target_player and target_player.is_alive():
                return target_player
        best = None
        best_score = -1.0
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
                    score = ctx.threat_scores.get(p.name, 0)
                    if score > best_score:
                        best_score = score
                        best = p
        return best

    def _init_police_dev_plan(self, alive_units, player, state, ctx):
        """初始化警察发育计划。"""
        Q = self._query
        sorted_units = sorted(alive_units, key=lambda u: u["id"])
        criminal_target = self._find_criminal_target(
            player, state,
            ctx)
        target_armor_attrs: set = set()
        if criminal_target:
            outer = Q.get_outer_armor_attr(criminal_target)
            if outer:
                target_armor_attrs = set(outer)
            else:
                inner = Q.get_inner_armor_attr(criminal_target)
                if inner:
                    target_armor_attrs = set(inner)
        from utils.attribute import Attribute
        if target_armor_attrs:
            needs_magic = False
            needs_tech = False
            for attr in target_armor_attrs:
                if attr == Attribute.TECH:
                    needs_magic = True
                elif attr == Attribute.ORDINARY:
                    needs_tech = True
                elif attr == Attribute.MAGIC:
                    needs_magic = True
            if needs_magic and not needs_tech:
                first_dest, first_weapon, first_armor, first_station = "魔法所", "魔法弹幕", "魔法护盾", "军事基地"
                second_dest, second_weapon, second_armor, second_station = "军事基地", "高斯步枪", "AT力场", "商店"
            elif needs_tech and not needs_magic:
                first_dest, first_weapon, first_armor, first_station = "军事基地", "高斯步枪", "AT力场", "商店"
                second_dest, second_weapon, second_armor, second_station = "魔法所", "魔法弹幕", "魔法护盾", "军事基地"
            else:
                enemies_magic = Q.count_enemies_at("魔法所", player, state)
                enemies_military = Q.count_enemies_at("军事基地", player, state)
                if enemies_magic <= enemies_military:
                    first_dest, first_weapon, first_armor, first_station = "魔法所", "魔法弹幕", "魔法护盾", "军事基地"
                    second_dest, second_weapon, second_armor, second_station = "军事基地", "高斯步枪", "AT力场", "商店"
                else:
                    first_dest, first_weapon, first_armor, first_station = "军事基地", "高斯步枪", "AT力场", "商店"
                    second_dest, second_weapon, second_armor, second_station = "魔法所", "魔法弹幕", "魔法护盾", "军事基地"
        else:
            enemies_magic = Q.count_enemies_at("魔法所", player, state)
            enemies_military = Q.count_enemies_at("军事基地", player, state)
            if enemies_magic <= enemies_military:
                first_dest, first_weapon, first_armor, first_station = "魔法所", "魔法弹幕", "魔法护盾", "军事基地"
                second_dest, second_weapon, second_armor, second_station = "军事基地", "高斯步枪", "AT力场", "商店"
            else:
                first_dest, first_weapon, first_armor, first_station = "军事基地", "高斯步枪", "AT力场", "商店"
                second_dest, second_weapon, second_armor, second_station = "魔法所", "魔法弹幕", "魔法护盾", "军事基地"
        assignments = {}
        if len(sorted_units) >= 1:
            assignments[sorted_units[0]["id"]] = {
                "dest": first_dest, "target_weapon": first_weapon,
                "target_armor": first_armor, "station": first_station, "phase": "pending",
            }
        if len(sorted_units) >= 2:
            assignments[sorted_units[1]["id"]] = {
                "dest": second_dest, "target_weapon": second_weapon,
                "target_armor": second_armor, "station": second_station, "phase": "pending",
            }
        if len(sorted_units) >= 3:
            assignments[sorted_units[2]["id"]] = {
                "dest": None, "target_weapon": None,
                "target_armor": None, "station": "魔法所", "phase": "stationed_default",
            }
        ctx.police_dev_assignments.clear(); ctx.police_dev_assignments.update(assignments)

    def _police_develop_step(self, active_units, ctx) -> Optional[str]:
        """执行一步警察发育。"""
        assignments = self._get_assignments(ctx)
        for unit in active_units:
            uid = unit["id"]
            assignment = assignments.get(uid)
            if not assignment:
                continue
            phase = assignment.get("phase", "pending")
            if phase == "combat":
                continue
            unit_loc = unit.get("location")
            dest = assignment.get("dest")
            if phase == "pending":
                if dest and unit_loc != dest:
                    assignment["phase"] = "moving"
                    return f"police move {uid} {dest}"
                elif dest and unit_loc == dest:
                    assignment["phase"] = "equip_weapon"
                    phase = "equip_weapon"
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
        return None

    def _police_deploy_step(self, alive_units, ctx) -> Optional[str]:
        """部署警察到驻扎位置。"""
        assignments = self._get_assignments(ctx)
        for unit in alive_units:
            uid = unit["id"]
            assignment = assignments.get(uid)
            if not assignment:
                continue
            phase = assignment.get("phase", "pending")
            if phase == "combat":
                continue
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
        return None

    @staticmethod
    def _police_wake_step(disabled_units, state) -> Optional[str]:
        """唤醒处于 debuff 的警察单位。"""
        pe = getattr(state, 'police_engine', None)
        for unit in disabled_units:
            uid = unit["id"]
            if unit.get("is_submerged", False):
                unit_loc = unit.get("location")
                if pe and unit_loc and pe._is_in_hologram_range(unit_loc):
                    continue
            return f"police wake {uid}"
        return None

    def _police_attack_criminal(self, target, active_units, state, ctx):
        """选择武器属性能有效打击目标的警察单位进行攻击。"""
        Q = self._query
        from utils.attribute import Attribute
        target_player = target
        target_loc = Q.get_location_str(target_player)
        target_armor_attrs = Q.get_outer_armor_attr(target_player)
        if not target_armor_attrs:
            target_armor_attrs = Q.get_inner_armor_attr(target_player)
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
            if target_armor_attrs:
                effective_set = EFFECTIVE_AGAINST.get(w_attr, set())
                can_be_effective = any(a in effective_set for a in target_armor_attrs)
                if can_be_effective:
                    score += 100
                else:
                    score -= 200
            else:
                score += 50
            if unit_loc == target_loc:
                score += 20
            if score > best_score:
                best_score = score
                best_unit = unit
        if not best_unit:
            return None
        uid = best_unit["id"]
        unit_loc = self._get_police_unit_location_from_state(state, uid)
        if unit_loc is None:
            unit_loc = best_unit.get("location")
        assignments = self._get_assignments(ctx)
        if unit_loc != target_loc:
            if uid in assignments:
                assignments[uid]["phase"] = "combat"
            return f"police move {uid} {target_loc}"
        else:
            weapon_name = best_unit.get("weapon", "警棍")
            weapon = make_weapon(weapon_name) if weapon_name else make_weapon("警棍")
            w_attr = weapon.attribute if weapon else Attribute.ORDINARY
            if target_armor_attrs:
                effective_set = EFFECTIVE_AGAINST.get(w_attr, set())
                can_hit = any(a in effective_set for a in target_armor_attrs)
                if not can_hit:
                    for other_unit in active_units:
                        if other_unit["id"] == uid:
                            continue
                        other_weapon = make_weapon(other_unit.get("weapon", "警棍"))
                        if other_weapon:
                            other_attr = other_weapon.attribute
                            other_effective = EFFECTIVE_AGAINST.get(other_attr, set())
                            if any(a in other_effective for a in target_armor_attrs):
                                other_id = other_unit['id']
                                if other_id in assignments:
                                    assignments[other_id]["phase"] = "combat"
                                return f"police move {other_id} {target_loc}"
                    return None
            if uid in assignments:
                assignments[uid]["phase"] = "combat"
            return f"police attack {uid} {target_player.player_id}"

    @staticmethod
    def _get_police_unit_location_from_state(state, unit_id: str) -> Optional[str]:
        """从 live game state 读取警察单位的实际位置。"""
        police = getattr(state, 'police', None)
        if not police:
            return None
        for unit in getattr(police, 'units', []):
            if getattr(unit, 'unit_id', '') == unit_id:
                loc = getattr(unit, 'location', None)
                return str(loc) if loc else None
        return None
