"""DevelopCommandBuilder —— 发育指令、目的地选择、病毒应急

从 develop_mixin.py 复制，所有 self._xxx 属性访问改为通过 GameQuery / ctx 参数。
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from controllers.ai.constants import (
    NEED_PROVIDERS, PERSONALITY_NEEDS,
    debug_ai_basic, debug_ai_development_plan,
)

if TYPE_CHECKING:
    from controllers.ai.game_query import GameQuery
    from controllers.ai.context import OrchestratorContext


class DevelopCommandBuilder:
    """发育指令构建器。"""

    def __init__(self, query: "GameQuery"):
        self._query = query

    # ════════════════════════════════════════════════════════
    #  通用发育入口
    # ════════════════════════════════════════════════════════

    def build_develop(
        self,
        player: Any,
        state: Any,
        strategy: Any,
        available: List[str],
        ctx: "OrchestratorContext",
        develop_assessment: Any = None,
        combat_assessment: Any = None,
        talent_hooks: Optional[Dict] = None,
        combat_builder: Any = None,
    ) -> List[str]:
        """将 DevelopMind 的评估结果转换为发育命令。"""
        Q = self._query
        commands: List[str] = []
        loc = Q.get_location_str(player)
        personality = getattr(ctx, 'personality', None) or getattr(
            strategy, 'personality_name', 'balanced')
        develop_data = getattr(develop_assessment, 'data', {}) if develop_assessment else {}
        if develop_data.get("development_complete"):
            return []

        talent_cmds = self._get_talent_develop_commands(
            player, state, available, talent_hooks)
        if talent_cmds is not None:
            return talent_cmds

        weapons = getattr(player, 'weapons', [])
        outer = Q.count_outer_armor(player)
        vouchers = getattr(player, 'vouchers', 0)

        # 磨刀优先
        sharpen = self._build_sharpen_command(player, available)
        if sharpen:
            return sharpen

        debug_ai_development_plan(player.name,
            f"状态: loc={loc} vouchers={vouchers} "
            f"outer={outer} needs={develop_data.get('needs', [])}")

        # Political 特殊处理
        if personality == "political":
            fallback = getattr(ctx, 'political_fallback_level', 'none')
            if (fallback == "none"
                    and not getattr(player, 'is_captain', False)
                    and outer >= 1):
                if loc == "警察局":
                    if not getattr(player, 'is_police', False) and "recruit" in available:
                        commands.append("recruit")
                    elif getattr(player, 'is_police', False) and "election" in available:
                        commands.append("election")
                elif "move" in available:
                    commands.append("move 警察局")
                if commands:
                    return commands

        # 当前地点可交互：直接消费 DevelopMind 的 current_location_actions
        current_interact = develop_data.get("current_location_actions", [])
        if current_interact and "interact" in available:
            commands.extend(current_interact)

        # 蓄力：与当前地点交互同级，交给最终选择逻辑排序。
        if "special" in available:
            if combat_builder is not None:
                charge_cmds = combat_builder.build_charge(player, available)
                for cmd in charge_cmds:
                    if cmd not in commands:
                        commands.append(cmd)
            else:
                for weapon_name in ("电磁步枪", "高斯步枪"):
                    weapon = next((w for w in weapons
                                   if w and getattr(w, 'name', '') == weapon_name), None)
                    if weapon and not getattr(weapon, 'is_charged', False):
                        cmd = f"special 蓄力{weapon_name}"
                        if cmd not in commands:
                            commands.append(cmd)

        # 移动到 DevelopMind 选出的最优地点
        if "move" in available and not commands:
            best_move = develop_data.get("best_move")
            if best_move:
                dest = best_move.replace("move ", "", 1)
                if Q.normalize_location(dest) != Q.normalize_location(loc):
                    commands.append(best_move)

        # 发育受阻：攻击型人格可转进攻；否则移动到安全兜底地点。
        if not commands:
            if personality in ("aggressive", "assassin", "balanced"):
                combat_data = getattr(combat_assessment, 'data', {}) if combat_assessment else {}
                target = combat_data.get("best_target") if combat_data.get("combat_ready") else None
                if target and combat_builder is not None:
                    commands.extend(combat_builder.build_attack(
                        player, state, strategy, available, ctx,
                        forced_target=target,
                    ))
                    if commands:
                        return commands
            fallback = Q.find_safe_location(player, state)
            if fallback and "move" in available:
                if Q.normalize_location(fallback) != Q.normalize_location(loc):
                    commands.append(f"move {fallback}")

        return commands

    # ════════════════════════════════════════════════════════
    #  唤醒
    # ════════════════════════════════════════════════════════

    def build_wake(
        self, player: Any, state: Any,
        strategy: Any, available: List[str],
        ctx: "OrchestratorContext",
    ) -> List[str]:
        """唤醒命令（简单 wrapper）。"""
        commands: List[str] = []
        if "special" in available:
            commands.append("special 唤醒")
        return commands

    # ════════════════════════════════════════════════════════
    #  目的地选择
    # ════════════════════════════════════════════════════════

    def pick_destination(
        self, player: Any, state: Any,
        strategy: Any, personality: str = "balanced",
    ) -> Optional[str]:
        """动态需求驱动的目的地选择（复制自 _pick_ideal_destination）。"""
        Q = self._query
        unmet_needs = self._get_unmet_needs(player, state, personality)
        if not unmet_needs:
            if personality in ("aggressive", "assassin", "balanced"):
                return Q.find_nearest_enemy_location(player, state, {})
            return None
        # 死者苏生
        if (player.talent
                and hasattr(player.talent, 'learned')
                and not player.talent.learned
                and hasattr(player.talent, 'name')
                and player.talent.name == "死者苏生"):
            if Q.get_location_str(player) != "魔法所":
                return "魔法所"
            return None
        # 评分
        loc = Q.get_location_str(player)
        vouchers = getattr(player, 'vouchers', 0)
        has_pass = getattr(player, 'has_military_pass', False)
        candidate_locs = ["home", "商店", "魔法所", "医院", "军事基地"]
        best_loc = None
        best_score = -999.0
        for dest in candidate_locs:
            if dest == loc:
                continue
            if dest == "home" and Q.is_at_home(player):
                continue
            score = self._score_destination(dest, unmet_needs, player, state,
                                            vouchers, has_pass, personality)
            if score > best_score:
                best_score = score
                best_loc = dest
        if best_score <= 0 and unmet_needs:
            return None
        return best_loc

    # ════════════════════════════════════════════════════════
    #  危险发育
    # ════════════════════════════════════════════════════════

    def build_danger_develop(
        self, player: Any, state: Any,
        strategy: Any, available: List[str],
        ctx: "OrchestratorContext",
    ) -> List[str]:
        """危险模式下的发育指令。"""
        Q = self._query
        commands: List[str] = []
        loc = Q.get_location_str(player)
        outer = Q.count_outer_armor(player)
        inner = Q.count_inner_armor(player)
        vouchers = getattr(player, 'vouchers', 0)

        if "interact" in available:
            if loc == "home" or Q.is_at_home(player):
                if outer == 0 and not Q.has_armor_by_name(player, "盾牌"):
                    commands.append("interact 盾牌")
            elif loc == "商店":
                if (not Q.has_virus_immunity(player)
                        and getattr(state, 'virus', None)
                        and getattr(state.virus, 'is_active', False)):
                    commands.insert(0, "interact 防毒面具")
                if vouchers >= 1 and outer < 2 and not Q.has_armor_by_name(player, "陶瓷护甲"):
                    commands.append("interact 陶瓷护甲")
                if vouchers < 1:
                    commands.append("interact 打工")
            elif loc == "魔法所":
                learned = Q.get_learned_spells(player)
                if "魔法护盾" not in learned and outer < 2:
                    commands.append("interact 魔法护盾")
            elif loc == "医院":
                if (not Q.has_virus_immunity(player)
                        and getattr(state, 'virus', None)
                        and getattr(state.virus, 'is_active', False)):
                    if vouchers >= 1:
                        commands.insert(0, "interact 防毒面具")
                    else:
                        commands.insert(0, "interact 打工")
                if inner == 0:
                    commands.append("interact 晶化皮肤手术")
                if vouchers < 1:
                    commands.append("interact 打工")
            elif loc == "军事基地":
                has_pass = getattr(player, 'has_military_pass', False)
                if has_pass and outer < 2 and not Q.has_armor_by_name(player, "AT力场"):
                    commands.append("interact AT力场")

        safe_loc = Q.find_safe_location(player, state)
        if safe_loc and safe_loc != loc and "move" in available:
            commands.append(f"move {safe_loc}")
        return commands

    # ════════════════════════════════════════════════════════
    #  病毒应急
    # ════════════════════════════════════════════════════════

    def build_virus(
        self, player: Any, state: Any,
        strategy: Any, available: List[str],
        ctx: "OrchestratorContext",
    ) -> List[str]:
        """病毒应急命令（复制自 DevelopMixin._cmd_virus）。"""
        Q = self._query
        commands: List[str] = []
        loc = Q.get_location_str(player)
        vouchers = getattr(player, 'vouchers', 0)
        virus = getattr(state, 'virus', None)
        virus_active = getattr(virus, 'is_active', False) if virus else False
        if loc == "商店" and "interact" in available and (vouchers >= 1 or virus_active):
            commands.append("interact 防毒面具")
        elif loc == "医院" and "interact" in available and vouchers >= 1:
            commands.append("interact 防毒面具")
        elif loc in ("商店", "医院") and "interact" in available and vouchers < 1:
            commands.append("interact 打工")
        elif loc == "魔法所" and "interact" in available:
            learned = Q.get_learned_spells(player)
            if "封闭" not in learned:
                commands.append("interact 封闭")
        elif "move" in available:
            candidates = []
            for dest in ["商店", "医院", "魔法所"]:
                if dest == loc:
                    continue
                enemies = Q.count_enemies_at(dest, player, state)
                candidates.append((dest, enemies))
            candidates.sort(key=lambda x: x[1])
            if candidates:
                commands.append(f"move {candidates[0][0]}")
            else:
                commands.append("move 商店")
        return commands

    # ════════════════════════════════════════════════════════
    #  内部辅助
    # ════════════════════════════════════════════════════════

    def _get_talent_develop_commands(
        self, player: Any, state: Any, available: List[str],
        talent_hooks: Optional[Dict],
    ) -> Optional[List[str]]:
        if not talent_hooks:
            return None
        talent_name = getattr(getattr(player, 'talent', None), 'name', '')
        hook = talent_hooks.get(talent_name) if talent_name else None
        if not hook or not hasattr(hook, 'get_develop_commands'):
            return None
        return hook.get_develop_commands(player, state, available)

    @staticmethod
    def _build_sharpen_command(player: Any, available: List[str]) -> List[str]:
        if "special" not in available:
            return []
        has_stone = any(
            getattr(item, 'name', '') == "磨刀石"
            for item in getattr(player, 'items', [])
        )
        has_unsharpened = any(
            weapon.name == "小刀" and getattr(weapon, 'base_damage', 0) < 2
            for weapon in getattr(player, 'weapons', []) if weapon
        )
        return ["special 磨刀"] if has_stone and has_unsharpened else []

    def _general_interact(
        self, player, state, loc, has_weapon, outer, inner,
        vouchers, has_pass, has_detection, personality,
    ) -> List[str]:
        """通用地点 interact 逻辑（复制自 _cmd_develop 的 interact 段落）。"""
        Q = self._query
        commands: List[str] = []
        if loc == "home" or Q.is_at_home(player):
            if outer == 0 and not Q.has_armor_by_name(player, "盾牌"):
                commands.append("interact 盾牌")
            if not has_weapon:
                commands.append("interact 小刀")
            if vouchers < 1:
                commands.append("interact 凭证")
        elif loc == "商店":
            if not has_weapon:
                commands.append("interact 小刀")
            if vouchers >= 1 and not has_detection:
                commands.append("interact 热成像仪")
            if vouchers >= 1 and outer < 2 and not Q.has_armor_by_name(player, "陶瓷护甲"):
                commands.append("interact 陶瓷护甲")
            if personality == "assassin" and vouchers >= 1:
                commands.append("interact 隐身衣")
            if has_weapon and Q.has_melee_only(player):
                has_stone = any(getattr(i, 'name', '') == "磨刀石"
                               for i in getattr(player, 'items', []))
                has_unsharpened = any(w.name == "小刀" and w.base_damage < 2
                                     for w in player.weapons if w)
                if not has_stone and has_unsharpened:
                    commands.append("interact 磨刀石")
            if vouchers < 1:
                commands.append("interact 打工")
        elif loc == "魔法所":
            learned = Q.get_learned_spells(player)
            if "魔法弹幕" not in learned and not has_weapon:
                commands.append("interact 魔法弹幕")
            if "魔法弹幕" in learned and "远程魔法弹幕" not in learned:
                commands.append("interact 远程魔法弹幕")
            if "魔法护盾" not in learned and outer < 2:
                commands.append("interact 魔法护盾")
            if "探测魔法" not in learned and not has_detection:
                commands.append("interact 探测魔法")
            if "隐身术" not in learned and personality == "assassin":
                commands.append("interact 隐身术")
            if "地震" not in learned:
                commands.append("interact 地震")
            if "地震" in learned and "地动山摇" not in learned:
                commands.append("interact 地动山摇")
            if "封闭" not in learned:
                commands.append("interact 封闭")
            if (player.talent
                    and hasattr(player.talent, 'learned')
                    and not player.talent.learned
                    and hasattr(player.talent, 'name')
                    and player.talent.name == "死者苏生"):
                if not commands:
                    commands.append("forfeit")
        elif loc == "医院":
            if inner == 0:
                if personality == "builder":
                    commands.append("interact 晶化皮肤手术")
                    commands.append("interact 额外心脏手术")
                else:
                    commands.append("interact 晶化皮肤手术")
            elif inner < 2 and personality in ("builder", "defensive"):
                commands.append("interact 额外心脏手术")
            if not Q.has_virus_immunity(player) and vouchers >= 1:
                commands.append("interact 防毒面具")
            if vouchers < 1:
                commands.append("interact 打工")
        elif loc == "军事基地":
            if not has_pass:
                commands.append("interact 通行证")
            elif has_pass:
                if not has_weapon or personality in ("aggressive", "balanced"):
                    commands.append("interact 电磁步枪")
                    commands.append("interact 高斯步枪")
                if outer < 2 and not Q.has_armor_by_name(player, "AT力场"):
                    commands.append("interact AT力场")
                if not has_detection:
                    commands.append("interact 雷达")
                if personality == "assassin":
                    commands.append("interact 隐形涂层")
        elif loc == "警察局":
            if personality == "political":
                police = getattr(state, 'police', None)
                if (police and police.report_phase == "reported"
                        and police.reporter_id == player.player_id):
                    commands.append("assemble")
                    return commands
                if not getattr(player, 'is_police', False):
                    commands.append("recruit")
                elif (getattr(player, 'is_police', False)
                      and not getattr(player, 'is_captain', False)):
                    commands.append("election")
        return commands

    def _get_unmet_needs(self, player, state, personality) -> list:
        """返回当前未满足的需求列表。"""
        Q = self._query
        needs_order = PERSONALITY_NEEDS.get(personality, PERSONALITY_NEEDS["balanced"])
        weapons = [w for w in player.weapons if w and getattr(w, 'name', '') != "拳击"]
        has_weapon = len(weapons) > 0
        outer = Q.count_outer_armor(player)
        inner = Q.count_inner_armor(player)
        vouchers = getattr(player, 'vouchers', 0)
        has_detection = getattr(player, 'has_detection', False)
        has_stealth = Q.has_stealth(player)
        unmet = []
        for need in needs_order:
            if need == "voucher" and vouchers < 1:
                unmet.append(("voucher", 3))
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
                unmet.append(("second_weapon", 3))
        if personality == "builder":
            has_pass = getattr(player, 'has_military_pass', False)
            if not has_pass:
                unmet.append(("military_pass", 4))
        return unmet

    def _score_destination(
        self, dest, unmet_needs, player, state, vouchers, has_pass,
        personality,
    ) -> float:
        """对候选地点评分。"""
        Q = self._query
        score = 0.0
        for need_key, priority in unmet_needs:
            providers = NEED_PROVIDERS.get(need_key, [])
            for (ploc, item_name, prereq) in providers:
                if ploc != dest:
                    continue
                if prereq == "voucher" and vouchers < 1:
                    score += priority * 0.3
                    continue
                if prereq == "pass" and not has_pass:
                    if vouchers >= 1:
                        score += priority * 0.5
                    else:
                        score += priority * 0.1
                    continue
                if prereq == "voucher_consume" and vouchers < 1:
                    score += priority * 0.2
                    continue
                if self._already_has_item(player, item_name):
                    continue
                score += priority
                break
        enemies = Q.count_enemies_at(dest, player, state)
        if personality in ("aggressive", "assassin"):
            score -= enemies * 0.5
        else:
            if enemies == 1:
                score -= 0.5
            elif enemies == 2:
                score -= 2.5
            elif enemies >= 3:
                score -= enemies * 2 + 3
        satisfiable_count = 0
        for need_key, _ in unmet_needs:
            providers = NEED_PROVIDERS.get(need_key, [])
            for (ploc, item_name, _) in providers:
                if ploc == dest and not self._already_has_item(player, item_name):
                    satisfiable_count += 1
                    break
        if satisfiable_count >= 2:
            score += 3
        if satisfiable_count >= 3:
            score += 3
        return score

    def _already_has_item(self, player, item_name) -> bool:
        """检查玩家是否已拥有某物品/装备/法术。"""
        Q = self._query
        if item_name in ("小刀", "高斯步枪", "电磁步枪"):
            return any(w.name == item_name for w in player.weapons if w)
        learned = Q.get_learned_spells(player)
        if item_name in ("魔法护盾", "魔法弹幕", "远程魔法弹幕", "封闭",
                         "地震", "地动山摇", "隐身术", "探测魔法"):
            if item_name == "魔法弹幕":
                return (item_name in learned
                        or any(w.name == item_name for w in player.weapons if w))
            return item_name in learned
        if item_name in ("盾牌", "陶瓷护甲", "AT力场"):
            return Q.has_armor_by_name(player, item_name)
        surgery_armor_map = {
            "晶化皮肤手术": "晶化皮肤",
            "额外心脏手术": "额外心脏",
            "不老泉手术": "不老泉",
        }
        if item_name in surgery_armor_map:
            return Q.has_armor_by_name(player, surgery_armor_map[item_name])
        if item_name in ("热成像仪", "隐身衣", "隐形涂层", "雷达"):
            if item_name in ("热成像仪", "雷达"):
                return getattr(player, 'has_detection', False)
            if item_name in ("隐身衣", "隐形涂层", "隐身术"):
                return Q.has_stealth(player)
        if item_name == "通行证":
            return getattr(player, 'has_military_pass', False)
        if item_name == "凭证":
            return getattr(player, 'vouchers', 0) >= 1
        if item_name == "打工":
            return getattr(player, 'vouchers', 0) >= 1
        return False
