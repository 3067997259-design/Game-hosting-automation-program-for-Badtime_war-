"""ChooseMixin —— choose/choose_multi/confirm 接口实现"""
from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional, Dict, Any
import random
from controllers.ai.constants import debug_ai_basic

if TYPE_CHECKING:
    from controllers.ai.controller import BasicAIController

_Base = BasicAIController if TYPE_CHECKING else object

HEXAGRAM_OUTCOME_MAP = {
    "石头": ["steal_armor", "disarm", "escape"],      # 飞龙在天 / 亢龙有悔 / 群龙无首
    "剪刀": ["disarm", "thunder", "extra_turn"],       # 亢龙有悔 / 潜龙勿用 / 或跃在渊
    "布":   ["escape", "extra_turn", "immunity"],      # 群龙无首 / 或跃在渊 / 元亨利贞
}


class ChooseMixin(_Base):

    # ════════════════════════════════════════════════════════
    #  choose：单选决策
    # ════════════════════════════════════════════════════════

    def choose(
        self, prompt: str, options: List[str],
        context: Optional[Dict] = None
    ) -> str:
        situation = (context or {}).get("situation", "")
        # ---- 猜拳 ----
        if situation == "hexagram_my_choice":
            if self._player and self._game_state:
                return self._hexagram_pick_caster(self._player, self._game_state, options)
            return random.choice(options)
        if situation == "hexagram_opp_choice":
            # 对手视角：需要找到发动者（六爻的 caster）
            # context 里没有 caster 信息，但可以从 game_state 找持有六爻天赋的玩家
            caster = self._find_hexagram_caster(self._game_state) if self._game_state else None
            if caster and self._game_state:
                return self._hexagram_pick_opponent(caster, self._game_state, options)
            return random.choice(options)
        if situation == "mythland_rps":
            return random.choice(options)  # 幻想乡猜拳仍然随机

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
        if situation == "oneslash_pick_weapon":
            # 一刀缭断武器选择：磨过的小刀优先于蓄好力的高斯步枪
            if self._player:
                best_name = None
                best_dmg = -1
                best_is_sharpened_knife = False
                for w in getattr(self._player, 'weapons', []):
                    if w and w.name in options:
                        dmg = self._get_weapon_damage(w)
                        is_sharpened_knife = (
                            w.name == "小刀"
                            and getattr(w, 'base_damage', 0) >= 2
                        )
                        # 优先选磨过的小刀；同优先级下选伤害最高的
                        if (is_sharpened_knife and not best_is_sharpened_knife) or \
                           (is_sharpened_knife == best_is_sharpened_knife and dmg > best_dmg):
                            best_dmg = dmg
                            best_name = w.name
                            best_is_sharpened_knife = is_sharpened_knife
                if best_name:
                    return best_name
            return options[0]
        if situation == "oneslash_pick_target":
            return max(options, key=lambda name: self._threat_scores.get(name, 0), default=options[0])
        # ---- 天赋T0 ----
        if situation == "talent_t0":
            talent_name = (context or {}).get("talent_name", "")
            # 愿负世（主动发动）：只在火种足够高时发动
            if "愿负世" in talent_name:
                talent = getattr(self._player, 'talent', None) if self._player else None
                divinity = getattr(talent, 'divinity', 0) if talent else 0
                if divinity >= 8:
                    for opt in options:
                        if "发动" in opt:
                            return opt
                elif self._player and self._player.hp <= 1.0 and divinity >= 4:
                    nearby = self._get_same_location_targets(self._player, self._game_state) if self._game_state else []
                    if nearby:
                        for opt in options:
                            if "发动" in opt:
                                return opt
                # Not worth activating — save for passive trigger (+2 bonus divinity)
                for opt in options:
                    if "不发动" in opt or "正常" in opt:
                        return opt
                return options[-1]
            # 一刀缭断：满足任一条件即发动（前提：面对面）
            if talent_name == "一刀缭断":
                if self._player and self._game_state:
                    state = self._game_state
                    player = self._player
                    markers = getattr(state, 'markers', None)
                    # Find a face-to-face target
                    engaged_target = None
                    if markers:
                        for pid in state.player_order:
                            if pid == player.player_id:
                                continue
                            t = state.get_player(pid)
                            if t and t.is_alive() and markers.has_relation(
                                    player.player_id, "ENGAGED_WITH", pid):
                                engaged_target = t
                                break
                    if engaged_target:
                        should_activate = False
                        # Condition 1: In combat AND all weapons countered by target's armor
                        # (一刀缭断 ignores element countering, so it's the perfect counter)
                        if self._in_combat and self._all_weapons_countered(player, engaged_target):
                            should_activate = True
                        # Condition 2: Target's effective HP + total armor count >= 3
                        # (target is tanky enough to warrant the burst)
                        if not should_activate:
                            eff_hp = self._get_effective_hp(engaged_target)
                            total_armor = (self._count_outer_armor(engaged_target)
                                         + self._count_inner_armor(engaged_target))
                            if eff_hp + total_armor >= 3:
                                should_activate = True
                        if should_activate:
                            for opt in options:
                                if "发动" in opt:
                                    return opt
                for opt in options:
                    if "不发动" in opt or "正常" in opt:
                        return opt
                return options[-1]
            # 请一直，注视着我（全息影像）：保守发动逻辑
            if "注视" in talent_name:
                should_activate = False
                if self._player and self._game_state:
                    my_loc = self._get_location_str(self._player)
                    pc = self._police_cache or {}
                    outer = self._count_outer_armor(self._player)
                    inner = self._count_inner_armor(self._player)
                    total_armor = outer + inner

                    # --- 辅助：计算同地点的敌人+警察总数 ---
                    nearby_players = self._get_same_location_targets(
                        self._player, self._game_state)
                    nearby_police_count = 0
                    for unit in pc.get("units", []):
                        if (unit.get("is_alive")
                                and unit.get("location")
                                and unit["location"] == my_loc):
                            nearby_police_count += 1
                    nearby_total = len(nearby_players) + nearby_police_count

                    has_two_aoe = self._count_distinct_aoe_attrs(self._player) >= 2

                    # --- 条件1（主动进攻）：发育完成 + 至少2件护甲 + 同地点敌人>=1 ---
                    if (not should_activate
                            and self._is_development_complete(self._player, self._game_state)
                            and total_armor >= 2
                            and nearby_total >= 1):
                        should_activate = True

                    # --- 条件2（保命逃跑）：HP <= 1.0 且被攻击过 且攻击者在同地点 ---
                    # 保命用：交技能震荡攻击者，额外行动回合用来逃跑
                    if not should_activate and self._player.hp <= 1.0 and self._been_attacked_by:
                        for attacker_name in self._been_attacked_by:
                            for pid in self._game_state.player_order:
                                atk = self._game_state.get_player(pid)
                                if (atk and atk.is_alive()
                                        and atk.name == attacker_name
                                        and self._same_location(self._player, atk)):
                                    should_activate = True
                                    break
                            if should_activate:
                                break

                    # --- 条件3（反警察）：有2种AOE + 队长在任（3个警察已召唤） ---
                    if not should_activate and has_two_aoe:
                        has_captain = pc.get("captain_id") is not None
                        if has_captain:
                            should_activate = True

                if should_activate:
                    for opt in options:
                        if "发动" in opt:
                            return opt
                # 不满足任何条件 → 不发动
                for opt in options:
                    if "不发动" in opt or "正常" in opt:
                        return opt
                return options[-1]
            # 遗世独立的幻想乡/神话之外：发育完成且有目标时发动
            if "幻想乡" in talent_name or "神话之外" in talent_name:
                if self._player and self._game_state:
                    if self._is_development_complete(self._player, self._game_state):
                        nearby = self._get_same_location_targets(self._player, self._game_state)
                        if nearby:
                            for opt in options:
                                if "发动" in opt:
                                    return opt
                for opt in options:
                    if "不发动" in opt or "正常" in opt:
                        return opt
                return options[-1]
            # 天星：被攻击或同地点有多个敌人时发动（与全息影像一致）
            if talent_name == "天星":
                talent = getattr(self._player, 'talent', None) if self._player else None
                uses = getattr(talent, 'uses_remaining', 0) if talent else 0
                if uses >= 2:
                    # 有2次，更积极发动
                    if self._player and self._game_state:
                        nearby = self._get_same_location_targets(self._player, self._game_state)
                        if len(nearby) >= 1:  # 原来是 >= 2
                            for opt in options:
                                if "发动" in opt:
                                    return opt
                # uses == 1 时保留原有的发动条件（被攻击或同地点有多个敌人）
                if self._player and self._game_state:
                    attackers = len(self._been_attacked_by)
                    if attackers >= 1:
                        for opt in options:
                            if "发动" in opt:
                                return opt
                    nearby = self._get_same_location_targets(self._player, self._game_state)
                    if len(nearby) >= 2:
                        for opt in options:
                            if "发动" in opt:
                                return opt
                for opt in options:
                    if "不发动" in opt or "正常" in opt:
                        return opt
                return options[-1]
            # 六爻/往世的涟漪：默认发动（get_t0_option已做前置检查）
            for opt in options:
                if "发动" in opt:
                    return opt
            return options[0]
        # ---- 加入警察 ----
        if situation in ("recruit_pick_1", "recruit_pick_2"):
            if self.personality == "aggressive":
                priority = ["警棍", "盾牌", "购买凭证"]
            elif self.personality == "defensive":
                priority = ["盾牌", "警棍", "购买凭证"]
            elif self.personality == "political":
                priority = ["购买凭证", "警棍", "盾牌"]
            else:
                priority = ["盾牌", "购买凭证", "警棍"]
            for preferred in priority:
                if preferred in options:
                    return preferred
            return options[0]
        # ---- 竞选队长（Bug1修复：安全引用 self._player/self._game_state）----
        if situation == "captain_election":
            should = False
            if self._player is not None and self._game_state is not None:
                should = self._should_become_captain(self._player, self._game_state)
            else:
                # 没有缓存时，political 默认竞选
                should = (self.personality == "political")
            if should:
                for opt in options:
                    if "竞选" in opt:
                        return opt
            else:
                for opt in options:
                    if "不竞选" in opt or "放弃" in opt:
                        return opt
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
        if situation == "hexagram_steal_target":
            # 飞龙在天: pick target with best outer armor
            return max(options, key=lambda name: self._threat_scores.get(name, 0), default=options[0])

        if situation == "hexagram_disarm_target":
            # 亢龙有悔: pick target with fewest weapons (most impactful to disable)
            return max(options, key=lambda name: self._threat_scores.get(name, 0), default=options[0])

        if situation == "hexagram_free_target":
            # 涟漪自由选择: pick highest threat target
            return max(options, key=lambda name: self._threat_scores.get(name, 0), default=options[0])

        if situation == "hexagram_steal_pick":
            # 飞龙在天: pick which armor to steal - prefer AT力场 > 陶瓷 > 魔法护盾 > 盾牌
            armor_priority = ["AT力场", "陶瓷护甲", "魔法护盾", "盾牌", "晶化皮肤"]
            for preferred in armor_priority:
                for opt in options:
                    if preferred in opt:
                        return opt
            return options[0]
        # ---- 涟漪 ----
        if situation == "ripple_choose_method":
            # 单人模式下方式二（献诗）收益更高
            for opt in options:
                if "献诗" in opt:
                    return opt
            return options[0]
        if situation == "resurrection_pick_target":
            # 单人模式下挂自己收益最大
            if self._player and self._player.name in options:
                return self._player.name
            return options[0]
        if situation == "ripple_anchor_type":
            for opt in options:
                if "击杀" in opt:
                    return opt
            return options[0]
        if situation == "ripple_poem_target":
            # 献诗选自己（触发爱与记忆之诗，4发伤害）
            if self._player and self._player.name in options:
                return self._player.name
            player_opts = [o for o in options if o != "取消"]
            return player_opts[0] if player_opts else options[0]
        if situation in ("ripple_anchor_kill_target", "ripple_anchor_armor_target"):
            player_opts = [o for o in options if o != "取消"]
            if player_opts:
                return max(player_opts, key=lambda name: self._threat_scores.get(name, 0))
            return options[0]
        if situation == "ripple_anchor_armor_pick":
            non_cancel = [o for o in options if o != "取消"]
            return non_cancel[0] if non_cancel else options[0]
        if situation == "ripple_anchor_acquire_item":
            priority = ["高斯步枪", "AT力场", "导弹控制权", "远程魔法弹幕", "陶瓷护甲", "魔法护盾", "电磁步枪"]
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
            if self._player and self._game_state:
                scores = self._score_hexagram_effects(self._player, self._game_state)
                # Map display names to effect keys
                best_key = max(scores, key=scores.get)
                name_map = {
                    "thunder": "潜龙勿用",
                    "steal_armor": "飞龙在天",
                    "immunity": "元亨利贞",
                    "disarm": "亢龙有悔",
                    "extra_turn": "或跃在渊",
                    "escape": "群龙无首",
                }
                best_name = name_map.get(best_key, "")
                for opt in options:
                    if best_name in opt:
                        return opt
            # Fallback to thunder
            for opt in options:
                if "天雷" in opt or "潜龙" in opt:
                    return opt
            return options[0]
        # ---- 献予律法之诗：额外行动 ----
        if situation in ("poem_law_extra_action", "poem_law_police_action"):
            return options[0] if options else ""
        # ---- 默认 ----
        return options[0]

    # ════════════════════════════════════════════════════════
    #  choose_multi：多选决策
    # ════════════════════════════════════════════════════════

    def choose_multi(
        self, prompt: str, options: List[str],
        max_count: int, min_count: int = 0,
        context: Optional[Dict] = None
    ) -> List[str]:
        if not options:
            return []
        sorted_opts = sorted(
            options, key=lambda name: self._threat_scores.get(name, 0), reverse=True
        )
        # 取 min_count 和 max_count 之间的合理数量
        count = max(min_count, min(max_count, len(sorted_opts)))
        return sorted_opts[:count]

    # ════════════════════════════════════════════════════════
    #  confirm：确认决策
    # ════════════════════════════════════════════════════════

    def confirm(self, prompt: str, context: Optional[Dict] = None) -> bool:
        # 强买通行证：当prompt包含"强买通行证"且AI需要去军事基地时同意
        if "强买通行证" in prompt:
            # 如果AI手上所有武器都是普通属性，需要去军事基地拿科技武器
            if self._player and not self._has_non_ordinary_weapon(self._player):
                return True
            # 其他情况（如builder正常发育路线）也可以同意
            if self.personality == "builder":
                return True
            return False
        if not context:
            return False
        situation = context.get("phase", "")
        if situation == "response_window":
            talent_name = context.get("talent_name", "")
            action_type = context.get("action_type", "")
            if talent_name == "你给路打油" and action_type in ("attack", "special"):
                if self._player:
                    hp = self._player.hp
                    outer = self._count_outer_armor(self._player)
                    if hp <= 1.0:
                        return True
                    if outer == 0 and hp <= 1.5:
                        return True
                    return False
                return True
        return False

    def _score_hexagram_effects(self, player, state) -> dict:
        """为六爻6种效果评分 — 基于当前战术处境动态调整"""
        scores = {}

        # ---- Step 1: Determine current situation ----
        hp = player.hp
        my_outer = self._count_outer_armor(player)
        is_critical = self._is_critical(player, state)
        dev_complete = self._is_development_complete(player, state)
        has_kill = self._has_kill_opportunity(player, state)

        # Check combat state from markers (not self._in_combat which belongs to this AI instance)
        markers_obj = getattr(state, 'markers', None)
        engaged_enemies = []
        locked_by_enemies = []
        if markers_obj:
            for pid in state.player_order:
                if pid == player.player_id:
                    continue
                t = state.get_player(pid)
                if t and t.is_alive():
                    if hasattr(markers_obj, 'has_relation') and markers_obj.has_relation(
                            player.player_id, 'ENGAGED_WITH', pid):
                        engaged_enemies.append(t)
                    locked_list = markers_obj.get_related(player.player_id, "LOCKED_BY") if hasattr(markers_obj, 'get_related') else set()
                    if pid in locked_list:
                        locked_by_enemies.append(t)

        in_combat = len(engaged_enemies) > 0 or self._in_combat
        losing = in_combat and (hp <= 1.0 or is_critical)

        # Classify situation
        if losing:
            situation = "D"  # Critical/losing
        elif in_combat:
            situation = "C"  # Active combat
        elif dev_complete or has_kill:
            situation = "B"  # Ready to attack
        else:
            situation = "A"  # Safe development

        # ---- Step 2: Score each effect based on situation ----

        # === thunder (潜龙勿用: 1 damage + break 1 armor) ===
        # Offensive effect — high when attacking, low when defending/developing
        if situation == "B":
            # Check for kill opportunities or armor to break
            best_kill = False
            best_armor_break = False
            for pid in state.player_order:
                if pid == player.player_id:
                    continue
                t = state.get_player(pid)
                if t and t.is_alive():
                    if t.hp <= 1.0 and self._count_outer_armor(t) == 0:
                        best_kill = True
                    if self._count_outer_armor(t) > 0:
                        best_armor_break = True
            scores["thunder"] = 10 if best_kill else (9 if best_armor_break else 7)
        elif situation == "C":
            # In combat: thunder is decent (damage the person you're fighting)
            combat_target_killable = False
            for e in engaged_enemies:
                if e.hp <= 1.0 and self._count_outer_armor(e) == 0:
                    combat_target_killable = True
            scores["thunder"] = 8 if combat_target_killable else 6
        else:
            # Safe or losing: thunder is low priority
            scores["thunder"] = 3

        # === steal_armor (飞龙在天: steal 1 outer armor from target) ===
        # Development effect — high when safe and need armor, low in combat
        enemy_has_armor = any(
            self._count_outer_armor(state.get_player(pid)) > 0
            for pid in state.player_order
            if pid != player.player_id and state.get_player(pid) and state.get_player(pid).is_alive()
        )
        if situation == "A":
            # Safe development: stealing armor is great
            if my_outer == 0 and enemy_has_armor:
                scores["steal_armor"] = 9
            elif my_outer < 2 and enemy_has_armor:
                scores["steal_armor"] = 7
            else:
                scores["steal_armor"] = 4
        elif situation == "B":
            scores["steal_armor"] = 5 if enemy_has_armor else 2
        elif situation == "C":
            scores["steal_armor"] = 4 if (my_outer == 0 and enemy_has_armor) else 3
        else:  # D: losing
            scores["steal_armor"] = 2

        # === immunity (元亨利贞: immune to all damage/debuff for 1 round) ===
        # Defensive effect — high when in danger, low when safe
        if situation == "D":
            scores["immunity"] = 10  # Top priority when losing
        elif situation == "C":
            if hp <= 1.0:
                scores["immunity"] = 9
            elif hp <= 1.5:
                scores["immunity"] = 7
            else:
                scores["immunity"] = 6
        else:
            scores["immunity"] = 2  # Not useful when safe

        # === disarm (亢龙有悔: disable 1 weapon for 2 rounds) ===
        # Combat effect — high when fighting someone, low when not in combat
        if situation in ("C", "D"):
            # Check the specific enemies we're fighting
            best_disarm = 0
            targets_to_check = engaged_enemies if engaged_enemies else []
            # Also check locked_by enemies (ranged attackers targeting us)
            targets_to_check = list(set(targets_to_check + locked_by_enemies))
            if not targets_to_check:
                # Fallback: check all alive enemies
                for pid in state.player_order:
                    if pid == player.player_id:
                        continue
                    t = state.get_player(pid)
                    if t and t.is_alive():
                        targets_to_check.append(t)
            for t in targets_to_check:
                real_weapons = [w for w in getattr(t, 'weapons', [])
                            if w and getattr(w, 'name', '') != "拳击"
                            and not getattr(w, '_hexagram_disabled', False)]
                if len(real_weapons) == 1:
                    best_disarm = max(best_disarm, 9)
                elif len(real_weapons) > 1:
                    best_disarm = max(best_disarm, 7)
            scores["disarm"] = best_disarm if best_disarm > 0 else 4
            # If losing, disarm is less useful than immunity/escape
            if situation == "D":
                scores["disarm"] = min(scores["disarm"], 6)
        elif situation == "B":
            # Preparing to attack: disarm is moderately useful (weaken target before engaging)
            scores["disarm"] = 5
        else:
            # Safe development: disarm is nearly useless
            scores["disarm"] = 2

        # === extra_turn (或跃在渊: 2 extra action turns) ===
        # Versatile — always decent, but context changes priority
        if situation == "A":
            scores["extra_turn"] = 9  # Great for accelerating development
        elif situation == "B":
            scores["extra_turn"] = 8  # Good for attacking (2 extra attacks)
        elif situation == "C":
            scores["extra_turn"] = 8  # Good in combat (2 extra attacks)
        else:  # D: losing
            scores["extra_turn"] = 5  # Less useful when you need to survive, not act more

        # === escape (群龙无首: stealth + teleport enemy away) ===
        # Escape effect — high when losing/trapped, low when safe
        is_locked = len(locked_by_enemies) > 0
        if situation == "D":
            scores["escape"] = 10 if is_locked else 9  # Top priority: run away
        elif situation == "C":
            # In combat but not losing: escape is moderate (can disengage)
            scores["escape"] = 5 if is_locked else 3
        else:
            # Safe or attacking: escape is low priority
            scores["escape"] = 2

        return scores


    def _hexagram_pick_caster(self, player, state, options) -> str:
        """发动者出拳：maximin（最差情况收益最高）"""
        scores = self._score_hexagram_effects(player, state)
        best_choice = None
        best_worst = -999
        for choice in options:
            outcomes = HEXAGRAM_OUTCOME_MAP.get(choice, [])
            if not outcomes:
                continue
            worst = min(scores.get(e, 0) for e in outcomes)
            if worst > best_worst:
                best_worst = worst
                best_choice = choice
        return best_choice or random.choice(options)

    def _hexagram_pick_opponent(self, caster, state, options) -> str:
        """对手出拳：minimax（让发动者最好情况收益最低）"""
        scores = self._score_hexagram_effects(caster, state)
        best_choice = None
        best_min_max = 999
        for opp_choice in options:
            # 对手出 opp_choice 时，发动者出每种拳的结果
            caster_best = -999
            for caster_choice, outcomes in HEXAGRAM_OUTCOME_MAP.items():
                # outcomes[i] 对应对手出石头/剪刀/布
                opp_idx = ["石头", "剪刀", "布"].index(opp_choice)
                effect = outcomes[opp_idx]
                val = scores.get(effect, 0)
                if val > caster_best:
                    caster_best = val
            if caster_best < best_min_max:
                best_min_max = caster_best
                best_choice = opp_choice
        return best_choice or random.choice(options)

    def _find_hexagram_caster(self, state) -> Optional[Any]:
        """找到当前持有六爻天赋的玩家（用于对手出拳时评估）"""
        if not state:
            return None
        for pid in state.player_order:
            if pid == self._my_id:
                continue  # 自己是对手，跳过
            p = state.get_player(pid)
            if p and p.is_alive() and p.talent:
                if hasattr(p.talent, 'name') and p.talent.name == "六爻":
                    return p
        return None
