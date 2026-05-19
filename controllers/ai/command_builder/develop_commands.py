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
        personality: str = "balanced",
        talent_hooks: Optional[Dict] = None,
        controller_ref: Any = None,
    ) -> List[str]:
        """通用发育命令（复制自 DevelopMixin._cmd_develop）。

        controller_ref: 为了兼容天赋专属子路径中对 controller 内部方法的调用，
        这里传入旧 controller 引用。Phase 3+ 会逐步消除这个依赖。
        """
        Q = self._query
        commands: List[str] = []
        loc = Q.get_location_str(player)
        weapons = getattr(player, 'weapons', [])
        has_weapon = any(w for w in weapons if w and getattr(w, 'name', '') != "拳击")
        outer = Q.count_outer_armor(player)
        inner = Q.count_inner_armor(player)
        vouchers = getattr(player, 'vouchers', 0)
        has_pass = getattr(player, 'has_military_pass', False)
        has_detection = getattr(player, 'has_detection', False)

        # 磨刀优先
        if "special" in available:
            has_stone = any(getattr(i, 'name', '') == "磨刀石"
                           for i in getattr(player, 'items', []))
            has_unsharpened = any(w.name == "小刀" and w.base_damage < 2
                                 for w in player.weapons if w)
            if has_stone and has_unsharpened:
                commands.append("special 磨刀")
                return commands

        debug_ai_development_plan(player.name,
            f"状态: loc={loc} vouchers={vouchers} weapon={has_weapon} "
            f"outer={outer} inner={inner} pass={has_pass} detect={has_detection}")

        # 天赋专用发育路径（委托给 controller 的旧路径）
        if controller_ref is not None:
            if Q.has_firefly_talent(player):
                return controller_ref._cmd_develop_firefly(player, state, available)
            talent = getattr(player, 'talent', None)
            if talent and hasattr(talent, 'name') and talent.name == "请一直，注视着我":
                hologram_exhausted = (getattr(talent, 'used', False)
                                      and getattr(talent, 'max_uses', 0) <= 0
                                      and not getattr(talent, 'active', False))
                if hologram_exhausted:
                    post_cmds = controller_ref._cmd_develop_hologram_post(player, state, available)
                    if post_cmds:
                        return post_cmds
                else:
                    holo_cmds = controller_ref._cmd_develop_hologram(player, state, available)
                    if holo_cmds:
                        return holo_cmds
            if Q.has_hoshino_talent(player):
                return controller_ref._cmd_develop_hoshino(player, state, available)

        # Political 特殊处理
        if personality == "political" and controller_ref is not None:
            fallback = getattr(controller_ref, '_political_fallback_level', 'none')
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

        # 通用 interact 逻辑
        if "interact" in available:
            cmds = self._general_interact(
                player, state, loc, has_weapon, outer, inner,
                vouchers, has_pass, has_detection, personality,
                controller_ref,
            )
            commands.extend(cmds)

        # 蓄力
        if "special" in available and not commands:
            emr = next((w for w in weapons if w and getattr(w, 'name', '') == "电磁步枪"), None)
            if emr and not getattr(emr, 'is_charged', False):
                commands.append("special 蓄力电磁步枪")
            if not commands:
                gauss = next((w for w in weapons
                              if w and getattr(w, 'name', '') == "高斯步枪"), None)
                if gauss and not getattr(gauss, 'is_charged', False):
                    commands.append("special 蓄力高斯步枪")

        # 移动
        if "move" in available and not commands:
            next_loc = self.pick_destination(player, state, strategy, personality, controller_ref)
            if next_loc and next_loc != loc:
                if not (next_loc == "home" and Q.is_at_home(player)):
                    commands.append(f"move {next_loc}")

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
        controller_ref: Any = None,
    ) -> Optional[str]:
        """动态需求驱动的目的地选择（复制自 _pick_ideal_destination）。"""
        Q = self._query
        unmet_needs = self._get_unmet_needs(player, state, personality, controller_ref)
        if not unmet_needs:
            if personality in ("aggressive", "assassin", "balanced"):
                return Q.find_nearest_enemy_location(player, state)
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
        # Political 特殊路径
        if personality == "political" and controller_ref is not None:
            result = self._political_destination(player, state, unmet_needs, controller_ref)
            if result is not None:
                return result
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
                                            vouchers, has_pass, personality, controller_ref)
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
        safe_loc = Q.find_safe_location(player, state)
        loc = Q.get_location_str(player)
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
                enemies = Q.count_enemies_at(player, state, dest)
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

    def _general_interact(
        self, player, state, loc, has_weapon, outer, inner,
        vouchers, has_pass, has_detection, personality, controller_ref,
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

    def _get_unmet_needs(self, player, state, personality, controller_ref) -> list:
        """返回当前未满足的需求列表。"""
        Q = self._query
        effective_personality = personality
        if controller_ref and getattr(controller_ref, '_political_in_balanced_fallback', False):
            effective_personality = "balanced"
        needs_order = PERSONALITY_NEEDS.get(effective_personality, PERSONALITY_NEEDS["balanced"])
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
        personality, controller_ref,
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
        enemies = Q.count_enemies_at(player, state, dest)
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

    def _political_destination(self, player, state, unmet_needs, controller_ref) -> Optional[str]:
        """Political 人格的特殊目的地逻辑。"""
        Q = self._query
        fallback = getattr(controller_ref, '_political_fallback_level', 'none')
        if fallback in ("full_balanced", "develop_only"):
            return None
        is_police = getattr(player, 'is_police', False)
        is_captain = getattr(player, 'is_captain', False)
        loc = Q.get_location_str(player)
        if not is_police:
            has_basic = (any(w for w in player.weapons if w and w.name != "拳击")
                         and Q.count_outer_armor(player) > 0)
            if has_basic:
                if loc != "警察局":
                    return "警察局"
            else:
                return None
        if is_police and not is_captain:
            if loc != "警察局":
                return "警察局"
            return None
        if is_captain:
            assignments = getattr(controller_ref, '_police_dev_assignments', {})
            all_deployed = all(
                a.get("phase") in ("stationed", "stationed_default", None)
                for a in assignments.values()
            ) if assignments else False
            if not all_deployed:
                if loc != "警察局":
                    return "警察局"
                return None
            return None
        return None
