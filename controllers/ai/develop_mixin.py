"""DevelopMixin —— 发育命令、目的地选择、病毒应急"""
from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional, Any, Dict
from controllers.ai.constants import (
    NEED_PROVIDERS, PERSONALITY_NEEDS,
    debug_ai_basic, debug_ai_development_plan
)

if TYPE_CHECKING:
    from controllers.ai.controller import BasicAIController

_Base = BasicAIController if TYPE_CHECKING else object


class DevelopMixin(_Base):

    # ════════════════════════════════════════════════════════
    #  发育完成判定
    # ════════════════════════════════════════════════════════

    def _is_development_complete(self, player, state) -> bool:
        """判断发育是否完成"""
        real_weapons = [w for w in player.weapons if w and getattr(w, 'name', '') != "拳击"]
        has_real_weapon = len(real_weapons) > 0
        # 死者苏生：未学习或未挂载时，发育未完成
        if (player.talent
            and hasattr(player.talent, 'name')
            and player.talent.name == "死者苏生"):
            if hasattr(player.talent, 'learned') and not player.talent.learned:
                return False
            if hasattr(player.talent, 'mounted_on') and player.talent.mounted_on is None:
                return False
        # 火萤IV型：天赋感知的发育标准
        if self._has_firefly_talent(player):
            real_weapons = [w for w in player.weapons if w and getattr(w, 'name', '') != "拳击"]
            # Phase 1（debuff 前）：有 1 把武器就算完成
            if not self._firefly_debuff_active(player):
                return len(real_weapons) >= 1
            # Phase 2（debuff 后）：需要磨过的小刀 + 高斯步枪
            has_sharpened_knife = any(
                w.name == "小刀" and getattr(w, 'base_damage', 0) >= 2
                for w in real_weapons
            )
            has_gauss = any(w.name == "高斯步枪" for w in real_weapons)
            return has_sharpened_knife and has_gauss

        if self.personality == "aggressive":
            has_armor = self._count_outer_armor(player) >= 2
            has_two_weapons = len(real_weapons) >= 2
            return has_two_weapons and has_armor
        elif self.personality == "defensive":
            has_armor = self._count_outer_armor(player) >= 2
            has_inner = self._count_inner_armor(player) >= 1
            return has_real_weapon and has_armor and has_inner
        elif self.personality == "assassin":
            has_armor = self._count_outer_armor(player) >= 2
            has_two_weapons = len(real_weapons) >= 2
            return has_two_weapons and self._has_stealth(player) and has_armor
        elif self.personality == "builder":
            has_armor = self._count_outer_armor(player) >= 2
            has_inner = self._count_inner_armor(player) >= 1
            has_pass = getattr(player, 'has_military_pass', False)
            return has_real_weapon and has_armor and has_inner and has_pass
        elif self.personality == "political":
            fallback = self._political_fallback_level
            if fallback in ("full_balanced", "develop_only"):
                # fallback 时使用 balanced 完成标准（2外甲+1内甲+1武器）
                has_outer = self._count_outer_armor(player) >= 2
                has_inner = self._count_inner_armor(player) >= 1
                return has_real_weapon and has_outer and has_inner
            else:
                is_captain = getattr(player, 'is_captain', False)
                if not is_captain:
                    return False
            has_armor = self._count_outer_armor(player) >= 1
            # 检查警察是否全部部署
            all_deployed = all(
                a.get("phase") in ("stationed", "stationed_default", None)
                for a in self._police_dev_assignments.values()
            ) if self._police_dev_assignments else False
            return has_real_weapon and has_armor and all_deployed
        else:  # balanced
            has_outer = self._count_outer_armor(player) >= 2
            has_inner = self._count_inner_armor(player) >= 1
            return has_real_weapon and has_outer and has_inner

    def _cmd_wake(self) -> List[str]:
        return ["wake"]
    # ════════════════════════════════════════════════════════
    #  命令生成器：发育
    # ════════════════════════════════════════════════════════
    def _cmd_develop_firefly(self, player, state, available: List[str]) -> List[str]:
        """火萤IV型专用发育路径"""
        commands = []
        loc = self._get_location_str(player)
        weapons = getattr(player, 'weapons', [])
        real_weapons = [w for w in weapons if w and getattr(w, 'name', '') != "拳击"]
        outer = self._count_outer_armor(player)
        vouchers = getattr(player, 'vouchers', 0)
        has_pass = getattr(player, 'has_military_pass', False)
        # 磨刀优先（与通用逻辑一致）
        if "special" in available:
            has_stone = any(getattr(i, 'name', '') == "磨刀石" for i in getattr(player, 'items', []))
            has_unsharpened = any(w.name == "小刀" and w.base_damage < 2 for w in player.weapons if w)
            if has_stone and has_unsharpened:
                commands.append("special 磨刀")
                return commands
        debuff_active = self._firefly_debuff_active(player)
        if debuff_active:
            # === debuff 已生效：不拿护甲，专注高级武器 ===
            has_sharpened_knife = any(
                w.name == "小刀" and getattr(w, 'base_damage', 0) >= 2
                for w in real_weapons
            )
            has_gauss = any(w.name == "高斯步枪" for w in real_weapons)
            if "interact" in available:
                # 磨过的小刀路线：home 拿小刀 → 商店拿磨刀石 → 磨刀
                if not has_sharpened_knife:
                    has_knife = any(w.name == "小刀" for w in real_weapons)
                    if not has_knife:
                        if loc == "home" or self._is_at_home(player):
                            commands.append("interact 小刀")
                        elif loc == "商店":
                            commands.append("interact 小刀")  # 商店也有小刀
                    else:
                        # 有小刀但没磨，去商店拿磨刀石
                        has_stone = any(getattr(i, 'name', '') == "磨刀石" for i in getattr(player, 'items', []))
                        if not has_stone and loc == "商店":
                            if vouchers >= 1:
                                commands.append("interact 磨刀石")
                            else:
                                commands.append("interact 打工")
                # 高斯步枪路线：军事基地
                if not has_gauss:
                    if loc == "军事基地":
                        if not has_pass:
                            commands.append("interact 通行证")
                        else:
                            commands.append("interact 高斯步枪")
            # 蓄力高斯步枪（与 interact 块同级，避免 interact 不可用时跳过蓄力）
            if has_gauss and "special" in available and not commands:
                gauss = next((w for w in weapons if w and w.name == "高斯步枪"), None)
                if gauss and not getattr(gauss, 'is_charged', False):
                    commands.append("special 蓄力高斯步枪")
            # 移动到需要的地点
            if "move" in available and not commands:
                if not has_sharpened_knife:
                    has_knife = any(w.name == "小刀" for w in real_weapons)
                    if not has_knife:
                        if loc != "home" and not self._is_at_home(player):
                            commands.append("move home")
                    else:
                        has_stone = any(getattr(i, 'name', '') == "磨刀石" for i in getattr(player, 'items', []))
                        if not has_stone:
                            if loc != "商店":
                                commands.append("move 商店")
                elif not has_gauss:
                    if loc != "军事基地":
                        commands.append("move 军事基地")
        else:
            # === debuff 未生效：2武器 + 1外甲，不拿隐身/探测 ===
            if "interact" in available:
                if loc == "home" or self._is_at_home(player):
                    if vouchers < 1:
                        commands.append("interact 凭证")
                    if not any(w.name == "小刀" for w in real_weapons):
                        commands.append("interact 小刀")
                    if outer < 1 and not self._has_armor_by_name(player, "盾牌"):
                        commands.append("interact 盾牌")
                elif loc == "商店":
                    if vouchers < 1:
                        commands.append("interact 打工")
                    if outer < 1 and not self._has_armor_by_name(player, "陶瓷护甲"):
                        commands.append("interact 陶瓷护甲")
                    # 磨刀石（如果有未磨小刀）
                    has_unsharpened = any(w.name == "小刀" and w.base_damage < 2 for w in player.weapons if w)
                    has_stone = any(getattr(i, 'name', '') == "磨刀石" for i in getattr(player, 'items', []))
                    if has_unsharpened and not has_stone and vouchers >= 1:
                        commands.append("interact 磨刀石")
                elif loc == "魔法所":
                    learned = self._get_learned_spells(player)
                    if "魔法弹幕" not in learned and len(real_weapons) < 2:
                        commands.append("interact 魔法弹幕")
                    if "魔法护盾" not in learned and outer < 1:
                        commands.append("interact 魔法护盾")
                    # 不拿探测魔法、隐身术
                    if "地震" not in learned:
                        commands.append("interact 地震")
                    if "地震" in learned and "地动山摇" not in learned:
                        commands.append("interact 地动山摇")
                elif loc == "军事基地":
                    if not has_pass:
                        commands.append("interact 通行证")
                    elif has_pass:
                        if len(real_weapons) < 2:
                            commands.append("interact 高斯步枪")
                            commands.append("interact 电磁步枪")
                        if outer < 1 and not self._has_armor_by_name(player, "AT力场"):
                            commands.append("interact AT力场")
                        # 不拿雷达、隐形涂层
                elif loc == "医院":
                    # 火萤不主动去医院拿内甲（debuff 未生效时也不需要）
                    if vouchers < 1:
                        commands.append("interact 打工")
            # 蓄力
            if "special" in available and not commands:
                gauss = next((w for w in weapons if w and w.name == "高斯步枪"), None)
                if gauss and not getattr(gauss, 'is_charged', False):
                    commands.append("special 蓄力高斯步枪")
                emr = next((w for w in weapons if w and w.name == "电磁步枪"), None)
                if emr and not getattr(emr, 'is_charged', False):
                    commands.append("special 蓄力电磁步枪")
            # 移动
            if "move" in available and not commands:
                next_loc = self._pick_ideal_destination(player, state)
                if next_loc and next_loc != loc:
                    if not (next_loc == "home" and self._is_at_home(player)):
                        commands.append(f"move {next_loc}")
        return commands

    def _cmd_develop_firefly_minimal(self, player, state, available: List[str]) -> List[str]:
        """火萤Phase1最小发育：只拿护甲，不拿更多武器"""
        commands = []
        loc = self._get_location_str(player)
        outer = self._count_outer_armor(player)

        if "interact" in available:
            if (loc == "home" or self._is_at_home(player)) and outer < 1:
                if not self._has_armor_by_name(player, "盾牌"):
                    commands.append("interact 盾牌")
            elif loc == "商店" and outer < 1:
                vouchers = getattr(player, 'vouchers', 0)
                if vouchers >= 1 and not self._has_armor_by_name(player, "陶瓷护甲"):
                    commands.append("interact 陶瓷护甲")

        return commands
    # ════════════════════════════════════════════════════════
    #  通用发育命令
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
        # ---- 磨刀优先：有磨刀石+未磨小刀 → 立即磨刀 ----
        if "special" in available:
            has_stone = any(getattr(i, 'name', '') == "磨刀石" for i in getattr(player, 'items', []))
            has_unsharpened = any(w.name == "小刀" and w.base_damage < 2 for w in player.weapons if w)
            if has_stone and has_unsharpened:
                commands.append("special 磨刀")
                return commands  # 磨刀最高优先级，不生成其他命令
        debug_ai_development_plan(player.name,
            f"状态: loc={loc} vouchers={vouchers} weapon={has_weapon} "
            f"outer={outer} inner={inner} pass={has_pass} detect={has_detection}")
        # 火萤IV型：专用发育路径
        if self._has_firefly_talent(player):
            return self._cmd_develop_firefly(player, state, available)
        # Political 特殊处理：基本需求满足后，跳过通用发育，直奔警察局
        if (self.personality == "political"
            and self._political_fallback_level == "none"
            and not getattr(player, 'is_captain', False)
            and outer >= 1):
            debug_ai_development_plan(player.name, "political 基本需求已满足，直奔警察局路线")
            if loc == "警察局":
                if not getattr(player, 'is_police', False) and "recruit" in available:
                    commands.append("recruit")
                elif getattr(player, 'is_police', False) and "election" in available:
                    commands.append("election")
            elif "move" in available:
                commands.append("move 警察局")
            # 如果在警察局但 recruit/election 都不可用，不返回空列表，
            # fall through 到通用发育逻辑
            if commands:
                return commands
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
                if vouchers >= 1 and not has_detection:
                    commands.append("interact 热成像仪")
                if vouchers >= 1 and outer < 2 and not self._has_armor_by_name(player, "陶瓷护甲"):
                    commands.append("interact 陶瓷护甲")
                if self.personality == "assassin" and vouchers >= 1:
                    commands.append("interact 隐身衣")
                if has_weapon and self._has_melee_only(player):
                    has_stone = any(getattr(i, 'name', '') == "磨刀石" for i in getattr(player, 'items', []))
                    has_unsharpened = any(w.name == "小刀" and w.base_damage < 2 for w in player.weapons if w)
                    # 只在没有磨刀石且有未磨的小刀时才买
                    if not has_stone and has_unsharpened:
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
                    if "地震" in learned and "地动山摇" not in learned:
                        commands.append("interact 地动山摇")
                    if "封闭" not in learned:
                        commands.append("interact 封闭")
                    # 死者苏生：在魔法所学习（通过T0系统，不需要interact命令）
                    # 如果有死者苏生天赋且未学习，留在魔法所等待T0触发
                    if (player.talent
                        and hasattr(player.talent, 'learned')
                        and not player.talent.learned
                        and hasattr(player.talent, 'name')
                        and player.talent.name == "死者苏生"):
                        # 返回一个forfeit让AI留在魔法所，T0会在下一轮触发学习
                        if not commands:
                            commands.append("forfeit")
            elif loc == "医院":
                if inner == 0:
                    if self.personality == "builder":
                        commands.append("interact 晶化皮肤手术")
                        commands.append("interact 额外心脏手术")
                    else:
                        commands.append("interact 晶化皮肤手术")
                elif inner < 2 and self.personality in ("builder", "defensive"):
                    commands.append("interact 额外心脏手术")
                if not self._has_virus_immunity(player) and vouchers >= 1:
                    commands.append("interact 防毒面具")
                if vouchers < 1:
                    commands.append("interact 打工")
                # assassin 在医院顺手放毒
                if self._should_release_virus(player, state) and "special" in available:
                    commands.insert(0, "special 释放病毒")  # 插到最前面，优先放毒
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
                    # 集结优先于一切
                    police = getattr(state, 'police', None)
                    if police and police.report_phase == "reported" and police.reporter_id == player.player_id:
                        commands.append("assemble")
                        return commands
                    if not getattr(player, 'is_police', False):
                        if "recruit" in available:
                            commands.append("recruit")
                    elif getattr(player, 'is_police', False) and not getattr(player, 'is_captain', False):
                        if "election" in available:
                            commands.append("election")
        # ---- 蓄力：interact 之后、move 之前 ----
        if "special" in available and not commands:
            emr = next((w for w in weapons if w and getattr(w, 'name', '') == "电磁步枪"), None)
            if emr and not getattr(emr, 'is_charged', False):
                commands.append("special 蓄力电磁步枪")
            if not commands:
                gauss = next((w for w in weapons if w and getattr(w, 'name', '') == "高斯步枪"), None)
                if gauss and not getattr(gauss, 'is_charged', False):
                    commands.append("special 蓄力高斯步枪")
        # ---- 移动到目标地点 ----
        if "move" in available and not commands:
            next_loc = self._pick_ideal_destination(player, state)
            if next_loc and next_loc != loc:
                if not (next_loc == "home" and self._is_at_home(player)):
                    commands.append(f"move {next_loc}")
        return commands
    # ════════════════════════════════════════════════════════
    #  目的地选择与需求评估
    # ════════════════════════════════════════════════════════

    def _pick_ideal_destination(self, player, state) -> Optional[str]:
        """动态需求驱动的目标地点选择"""
        # 1. 收集当前未满足的需求
        unmet_needs = self._get_unmet_needs(player, state)
        if not unmet_needs:
            # 发育完成
            if self.personality in ("aggressive", "assassin", "balanced") or getattr(self, '_political_in_balanced_fallback', False):
                return self._find_nearest_enemy_location(player, state)
            return None
        # ---- 死者苏生：未学习时优先去魔法所 ----
        if (player.talent
            and hasattr(player.talent, 'learned')
            and not player.talent.learned
            and hasattr(player.talent, 'name')
            and player.talent.name == "死者苏生"):
            if self._get_location_str(player) != "魔法所":
                return "魔法所"
            # 已在魔法所 → 不需要移动，T0会自动触发学习
            return None
        # 2. political 特殊路径（警察局逻辑保留）
        if self.personality == "political":
            result = self._political_destination(player, state, unmet_needs)
            if result is not None:
                return result
        # 3. 对每个候选地点评分
        loc = self._get_location_str(player)
        vouchers = getattr(player, 'vouchers', 0)
        has_pass = getattr(player, 'has_military_pass', False)
        # 候选地点（排除当前位置和警察局）
        candidate_locs = ["home", "商店", "魔法所", "医院", "军事基地"]
        best_loc = None
        best_score = -999
        for dest in candidate_locs:
            if dest == loc:
                continue
            if dest == "home" and self._is_at_home(player):
                continue
            score = self._score_destination(dest, unmet_needs, player, state, vouchers, has_pass)
            if score > best_score:
                best_score = score
                best_loc = dest
        # 发育受阻判断：所有有用地点都被敌人压制
        if best_score <= 0 and unmet_needs:
            return None
        return best_loc
    def _get_unmet_needs(self, player, state) -> list:
        """返回当前未满足的需求列表（按人格优先级排序）"""
        # political fallback 时使用 balanced 的需求列表（6项而非3项）
        effective_personality = self.personality
        if getattr(self, '_political_in_balanced_fallback', False):
            effective_personality = "balanced"
        needs_order = PERSONALITY_NEEDS.get(effective_personality, PERSONALITY_NEEDS["balanced"])
        weapons = [w for w in player.weapons if w and getattr(w, 'name', '') != "拳击"]
        has_weapon = len(weapons) > 0
        weapon_attrs = set(self._get_weapon_attr(w) for w in weapons)
        outer = self._count_outer_armor(player)
        inner = self._count_inner_armor(player)
        vouchers = getattr(player, 'vouchers', 0)
        has_detection = getattr(player, 'has_detection', False)
        has_stealth = self._has_stealth(player)
        unmet = []
        for need in needs_order:
            if need == "voucher" and vouchers < 1:
                unmet.append(("voucher", 3))  # (need_key, priority_weight)
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
                # 需要至少2件真实武器（与 _is_development_complete 一致）
                unmet.append(("second_weapon", 3))
        if self.personality == "builder":
            has_pass = getattr(player, 'has_military_pass', False)
            if not has_pass:
                unmet.append(("military_pass", 4))  # 需要通行证
        return unmet
    def _score_destination(self, dest, unmet_needs, player, state, vouchers, has_pass) -> float:
        """对一个候选地点评分"""
        score = 0.0
        # 1. 能满足多少需求（按优先级加权）
        for need_key, priority in unmet_needs:
            providers = NEED_PROVIDERS.get(need_key, [])
            for (ploc, item_name, prereq) in providers:
                if ploc != dest:
                    continue
                # 检查前置条件
                if prereq == "voucher" and vouchers < 1:
                    score += priority * 0.3  # 没凭证但可以打工，打折
                    continue
                if prereq == "pass" and not has_pass:
                    if vouchers >= 1:
                        score += priority * 0.5  # 可以强买通行证，打折
                    else:
                        score += priority * 0.1  # 需要先拿凭证再强买，大打折
                    continue
                if prereq == "voucher_consume" and vouchers < 1:
                    score += priority * 0.2  # 需要先拿凭证
                    continue
                # 检查是否已有该物品（避免重复获取）
                if self._already_has_item(player, item_name):
                    continue
                score += priority  # 完全满足
                break  # 每个需求只计一次
        enemies = self._count_enemies_at(dest, player, state)
        if self.personality in ("aggressive", "assassin"):
            score -= enemies * 0.5
        else:
            if enemies == 1:
                score -= 0.5
            elif enemies == 2:
                score -= 2.5
            elif enemies >= 3:
                score -= enemies * 2 + 3
        # 3. 效率加分：一个地方能同时满足多个需求
        satisfiable_count = 0
        for need_key, _ in unmet_needs:
            providers = NEED_PROVIDERS.get(need_key, [])
            for (ploc, item_name, _) in providers:
                if ploc == dest and not self._already_has_item(player, item_name):
                    satisfiable_count += 1
                    break
        if satisfiable_count >= 2:
            score += 3  # 一站式加分
        if satisfiable_count >= 3:
            score += 3  # 更多加分
        return score
    def _already_has_item(self, player, item_name) -> bool:
        """检查玩家是否已拥有某物品/装备/法术"""
        # 武器（非法术类）
        if item_name in ("小刀", "高斯步枪", "电磁步枪"):
            return any(w.name == item_name for w in player.weapons if w)
        # 法术（魔法所的东西都是法术，包括魔法弹幕）
        learned = self._get_learned_spells(player)
        if item_name in ("魔法护盾", "魔法弹幕", "远程魔法弹幕", "封闭", "地震", "地动山摇", "隐身术", "探测魔法"):
            # 魔法弹幕既是法术也会变成武器，两者都检查
            if item_name == "魔法弹幕":
                return (item_name in learned
                        or any(w.name == item_name for w in player.weapons if w))
            return item_name in learned
        # 护甲
        if item_name in ("盾牌", "陶瓷护甲", "AT力场"):
            return self._has_armor_by_name(player, item_name)
        # 手术（内甲）：按具体手术名检查对应的护甲片
        surgery_armor_map = {
            "晶化皮肤手术": "晶化皮肤",
            "额外心脏手术": "额外心脏",
            "不老泉手术": "不老泉",
        }
        if item_name in surgery_armor_map:
            return self._has_armor_by_name(player, surgery_armor_map[item_name])
        # 物品
        if item_name in ("热成像仪", "隐身衣", "隐形涂层", "雷达"):
            if item_name == "热成像仪" or item_name == "雷达":
                return getattr(player, 'has_detection', False)
            if item_name in ("隐身衣", "隐形涂层", "隐身术"):
                return self._has_stealth(player)
        if item_name == "通行证":
            return getattr(player, 'has_military_pass', False)
        if item_name == "凭证":
            return getattr(player, 'vouchers', 0) >= 1
        if item_name == "打工":
            return getattr(player, 'vouchers', 0) >= 1  # 有凭证时游戏引擎禁止打工
        return False
    def _political_destination(self, player, state, unmet_needs) -> Optional[str]:
        """political 人格的特殊目的地逻辑（警察局相关）"""
        fallback = self._political_fallback_level
        if fallback in ("full_balanced", "develop_only"):
            return None  # 返回 None 让通用评分逻辑处理
        is_police = getattr(player, 'is_police', False)
        is_captain = getattr(player, 'is_captain', False)
        loc = self._get_location_str(player)
        # 还没加入警察 → 先满足基本需求再去警察局
        if not is_police:
            # 如果还有武器或外甲需求，先满足
            has_basic = any(w for w in player.weapons if w and w.name != "拳击") and self._count_outer_armor(player) > 0
            if has_basic:
                if loc != "警察局":
                    return "警察局"
            else:
                return None  # 让通用逻辑处理基本需求
        # 已加入但还没当队长 → 去警察局竞选
        if is_police and not is_captain:
            if loc != "警察局":
                return "警察局"
            return None  # 已在警察局
        # 已是队长 → 检查警察部署，然后让通用逻辑处理自身发育
        if is_captain:
            all_deployed = all(
                a.get("phase") in ("stationed", "stationed_default", None)
                for a in self._police_dev_assignments.values()
            ) if self._police_dev_assignments else False
            if not all_deployed:
                if loc != "警察局":
                    return "警察局"
                return None
            # 警察部署完毕，让通用逻辑处理
            return None
        return None
    # ════════════════════════════════════════════════════════
    #  病毒相关
    # ════════════════════════════════════════════════════════

    def _someone_has_virus_immunity(self, state) -> bool:
        """检查局内是否有其他玩家持有防毒面具或封闭"""
        for pid in state.player_order:
            if pid == self._my_id:
                continue
            p = state.get_player(pid)
            if not p or not p.is_alive():
                continue
            # 检查防毒面具
            items = getattr(p, 'items', [])
            for item in items:
                if getattr(item, 'name', '') == "防毒面具":
                    return True
            # 检查封闭
            if getattr(p, 'has_seal', False):
                return True
            if "封闭" in getattr(p, 'learned_spells', set()):
                return True
        return False
    def _count_opponents_without_immunity(self, player, state) -> int:
        """统计没有病毒免疫的存活对手数量"""
        count = 0
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            p = state.get_player(pid)
            if not p or not p.is_alive():
                continue
            if not self._has_virus_immunity(p):
                count += 1
        return count
    def _should_release_virus(self, player, state) -> bool:
        """判断 assassin 是否应该在医院释放病毒"""
        # 仅 assassin 人格
        if self.personality != "assassin":
            return False
        # 必须在医院
        if self._get_location_str(player) != "医院":
            return False
        # 病毒已激活则不放
        virus = getattr(state, 'virus', None)
        if virus and getattr(virus, 'is_active', False):
            return False
        # 自己必须有病毒免疫
        if not self._has_virus_immunity(player):
            return False
        # 警察成员（非队长）不能放毒（游戏规则阻止）
        if getattr(player, 'is_police', False) and not getattr(player, 'is_captain', False):
            return False
        # 对手免疫人数检查
        alive_count = len([p for p in state.players.values() if p.is_alive()])
        vulnerable = self._count_opponents_without_immunity(player, state)
        if alive_count >= 4:
            return vulnerable >= 2
        else:
            return vulnerable >= 1
    def _cmd_virus(self, player, state, available: List[str]) -> List[str]:
        commands = []
        loc = self._get_location_str(player)
        vouchers = getattr(player, 'vouchers', 0)
        # 路径 1：当前在商店/医院 → 直接拿面具
        # 商店：病毒期间免费，否则需凭证；医院：始终需凭证
        virus = getattr(state, 'virus', None)
        virus_active = getattr(virus, 'is_active', False) if virus else False
        if loc == "商店" and "interact" in available and (vouchers >= 1 or virus_active):
            commands.append("interact 防毒面具")
        elif loc == "医院" and "interact" in available and vouchers >= 1:
            commands.append("interact 防毒面具")
        # 路径 2：当前在商店/医院，没凭证 → 先打工
        elif loc in ("商店", "医院") and "interact" in available and vouchers < 1:
            commands.append("interact 打工")
        # 路径 3：当前在魔法所 → 学封闭（不需要凭证，2 回合）
        elif loc == "魔法所" and "interact" in available:
            learned = self._get_learned_spells(player)
            if "封闭" not in learned:
                commands.append("interact 封闭")
        # 路径 4：不在上述地点 → 选人少的地方去
        elif "move" in available:
            # 优先去有凭证能直接拿面具的地方，否则去能打工的地方
            candidates = []
            for dest in ["商店", "医院", "魔法所"]:
                if dest == loc:
                    continue
                enemies = self._count_enemies_at(dest, player, state)
                candidates.append((dest, enemies))
            candidates.sort(key=lambda x: x[1])
            if candidates:
                commands.append(f"move {candidates[0][0]}")
            else:
                commands.append("move 商店")
        return commands