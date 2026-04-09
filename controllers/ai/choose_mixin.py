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

        # ---- 星野形态选择 ----
        if situation == "hoshino_form_choice":
            if self.personality == "aggressive":
                priority = ["临战-Archer", "临战-shielder", "水着-shielder"]
            elif self.personality == "defensive":
                priority = ["水着-shielder", "临战-shielder", "临战-Archer"]
            else:  # balance/builder/political
                priority = ["水着-shielder", "临战-Archer", "临战-shielder"]
            for form in priority:
                if form in options:
                    return form
            return options[0]

        # ---- 星野自我怀疑 ----
        if situation == "hoshino_self_doubt":
            # 场上人越少，选 Terror 概率越高
            alive_count = 0
            if self._game_state:
                for pid in self._game_state.player_order:
                    p = self._game_state.get_player(pid)
                    if p and p.is_alive():
                        alive_count += 1
            # 2人或以下：选 Terror；3人以上：拒绝
            if alive_count <= 2:
                for opt in options:
                    if "接受" in opt or "terror" in opt.lower():
                        return opt
            for opt in options:
                if "拒绝" in opt or "抵抗" in opt:
                    return opt
            return options[-1]

        # ---- 星野解锁配发选择 ----
        if situation == "hoshino_tactical_equip":
            talent = getattr(self._player, 'talent', None) if self._player else None
            owned_items = getattr(talent, 'tactical_items', []) if talent else []
            owned_meds = getattr(talent, 'medicines', []) if talent else []
            # 优先拿没有的道具/药物
            # 优先拿没有的道具/药物（闪光弹 > 烟雾弹 > 破片手雷 > 震撼弹）
            priority_items = ["闪光弹", "烟雾弹", "破片手雷", "震撼弹"]
            for item_name in priority_items:
                for opt in options:
                    if item_name in opt and item_name not in owned_items:
                        return opt
            for opt in options:
                if "肾上腺素" in opt and "肾上腺素" not in owned_meds:
                    return opt
            # 都有了就拿子弹
            for opt in options:
                if "子弹" in opt:
                    return opt
            return options[0]

        # ---- 星野修复材料选择 ----
        if situation == "hoshino_repair_material":
            # 优先消耗盾牌（比 AT 力场便宜）
            for opt in options:
                if "盾牌" in opt:
                    return opt
            return options[0]

        if situation == "hoshino_throw_item":
            # 默认优先级：闪光弹 > 烟雾弹 > 破片手雷 > 震撼弹
            priority = ["闪光弹", "烟雾弹", "破片手雷", "震撼弹", "燃烧瓶"]
            for item in priority:
                if item in options:
                    return item
            return options[0]

        # ---- 星野服药选择 ----
        if situation == "hoshino_medicine":
            # 优先 EPO（cost+1），巧克力次之
            for opt in options:
                if "EPO" in opt:
                    return opt
            for opt in options:
                if "巧克力" in opt:
                    return opt
            return options[0] if options else ""

        # ---- 星野冲刺目标选择 ----
        if situation == "hoshino_dash_target":
            # 选威胁最高的目标所在地点
            if not options:
                return ""
            return max(options, key=lambda name: self._threat_scores.get(name, 0))

        # ---- 星野射击目标选择 ----
        if situation == "hoshino_shoot_target":
            if not options:
                return ""
            return max(options, key=lambda name: self._threat_scores.get(name, 0))

        # ---- 星野 find 目标选择 ----
        if situation == "hoshino_find_target":
            if not options:
                return ""
            return max(options, key=lambda name: self._threat_scores.get(name, 0))

        # ---- 守夜人之诗选择（被 G5 献诗时） ----
        if situation == "poem_nightwatch_choice":
            talent = getattr(self._player, 'talent', None) if self._player else None
            if talent and getattr(talent, 'is_terror', False):
                # Terror 状态下接受（解除 Terror）
                for opt in options:
                    if "接受" in opt:
                        return opt
            # 非 Terror：拒绝
            for opt in options:
                if "拒绝" in opt:
                    return opt
            return options[-1]

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
                            and total_armor >= 1
                            and nearby_total >= 1):
                        emr = next((w for w in self._player.weapons if w and w.name == "电磁步枪"), None)
                        if emr and not getattr(emr, 'is_charged', False):
                            # EMR未蓄力，检查是否有其他可用AOE武器
                            other_aoe = [w for w in self._player.weapons
                                        if w and w.name != "电磁步枪" and w.name != "拳击"
                                        and self._get_weapon_range(w) == "area"]
                            if other_aoe:
                                should_activate = True  # 有地震/地动山摇等可用AOE，正常发动
                            else:
                                # 只有EMR且未蓄力：标记需要蓄力，本回合蓄力，下回合发动
                                self._emr_needs_charge_before_hologram = True
                        else:
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
                    # --- 条件4（反火萤）：场上有火萤持超新星 + 自己有AOE + 火萤在同地点或可能被拉来 ---
                    if not should_activate and has_two_aoe:
                        for pid in self._game_state.player_order:
                            if pid == self._player.player_id:
                                continue
                            t = self._game_state.get_player(pid)
                            if (t and t.is_alive() and t.talent
                                    and getattr(t.talent, 'has_supernova', False)):
                                # 火萤有超新星，紧急发动全息影像
                                # 全息影像的D6拉人有50%概率把火萤拉过来
                                # 即使火萤不在同地点，拉过来后震荡可以阻止超新星
                                # 全息影像的易伤加成不受火萤减伤影响，是击杀火萤的最佳时机
                                should_activate = True
                                break
                    # --- 条件5（战斗中激活）：正在和人打架 + 有AOE武器 ---
                    # 全息影像在战斗中发动价值极高：震荡硬控 + 易伤 + 额外行动
                    # 注意：choose() 在 T0 阶段调用，self._in_combat 是上一轮缓存，
                    # 必须直接从 markers 实时检查 ENGAGED_WITH 关系。
                    if not should_activate:
                        markers = getattr(self._game_state, 'markers', None)
                        engaged_target = None
                        if markers and hasattr(markers, 'has_relation'):
                            for pid in self._game_state.player_order:
                                if pid == self._player.player_id:
                                    continue
                                t = self._game_state.get_player(pid)
                                if t and t.is_alive() and markers.has_relation(
                                        self._player.player_id, "ENGAGED_WITH", pid):
                                    engaged_target = t
                                    break
                        if engaged_target and self._same_location(self._player, engaged_target):
                            # 只要有至少1种AOE武器就值得发动
                            if self._count_distinct_aoe_attrs(self._player) >= 1:
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
            decision = self._ripple_decide_method()
            if decision == "anchor":
                for opt in options:
                    if "锚定" in opt:
                        return opt
            else:
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
            anchor_decision = self._ripple_decide_anchor_type()
            for opt in options:
                if anchor_decision in opt:
                    return opt
            return options[0]
        if situation == "ripple_poem_target":
            target_name = self._ripple_decide_poem_target(options)
            return target_name
        if situation in ("ripple_anchor_kill_target", "ripple_anchor_armor_target"):
            player_opts = [o for o in options if o != "取消"]
            if player_opts:
                return max(player_opts, key=lambda name: self._threat_scores.get(name, 0))
            return options[0]
        if situation == "ripple_anchor_armor_pick":
            non_cancel = [o for o in options if o != "取消"]
            return non_cancel[0] if non_cancel else options[0]
        if situation == "ripple_anchor_acquire_item":
            return self._ripple_decide_acquire_item(options)
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
            return self._ripple_decide_destiny_target(options, context)
        if situation == "ripple_hexagram_free_choice":
            if self._player and self._game_state:
                scores = self._score_hexagram_effects(self._player, self._game_state)
                # Map display names to effect keys
                best_key = max(scores, key=scores.get) # type: ignore
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
        # ---- 星野（神代天赋7）备用形态选择 ----
        # 注意：hoshino_form 和 hoshino_form_choice 是不同的 situation key，
        # 由游戏引擎在不同上下文中发出。hoshino_form_choice（上方）按精确形态名匹配，
        # hoshino_form（此处）按模糊关键字匹配作为兜底。
        if situation == "hoshino_form":
            if self.personality in ("balanced", "defensive"):
                for opt in options:
                    if "水着" in opt:
                        return opt
            elif self.personality == "aggressive":
                for opt in options:
                    if "Archer" in opt:
                        return opt
            else:
                for opt in options:
                    if "临战-shielder" in opt or "shielder" in opt:
                        return opt
            return options[0]

        if situation == "hoshino_self_doubt_choice":
            # 场上存活人数 <= 2 时选择进入 Terror
            alive_count = 0
            if self._game_state:
                for pid in self._game_state.player_order:
                    p = self._game_state.get_player(pid)
                    if p and p.is_alive():
                        alive_count += 1
            if alive_count <= 2:
                return options[0]  # "是因为我……"（进入自我怀疑）
            return options[1] if len(options) > 1 else options[0]  # "不，不是这样的"

        if situation == "hoshino_throw_location":
            # 选目标所在地点（如果有战斗目标）
            if self._combat_target:
                target_loc = self._get_location_str(self._combat_target)
                if target_loc in options:
                    return target_loc
            return options[0]

        if situation == "hoshino_reorder_ammo":
            # 不排弹，返回当前顺序
            return " ".join(str(i+1) for i in range(len(options)))
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
    #  G5 涟漪 AI 决策系统
    # ════════════════════════════════════════════════════════

    def _ripple_decide_method(self) -> str:
        """决定用锚定还是献诗。返回 "anchor" 或 "poem"。
        委托给 _ripple_choose_method 做完整的 9 级优先级判断。"""
        player = self._player
        state = self._game_state
        if not player or not state:
            return "poem"

        # 构造选项列表供 _ripple_choose_method 解析
        options = ["方式一：锚定命运", "方式二：献诗"]
        result = self._ripple_choose_method(player, state, options)
        if "锚定" in result or "方式一" in result:
            return "anchor"
        return "poem"


    def _ripple_decide_anchor_type(self) -> str:
        """决定锚定类型。返回 "获取" 或 "击杀"。"""
        player = self._player
        if not player:
            return "获取"

        if self._ripple_needs_equipment(player):
            return "获取"

        return "击杀"


    def _ripple_decide_poem_target(self, options) -> str:
        """决定献诗目标。委托给 _ripple_choose_poem_target。"""
        player = self._player
        state = self._game_state

        if not player or not state:
            if player and player.name in options:
                return player.name
            return options[0]

        return self._ripple_choose_poem_target(player, state, options)


    def _ripple_decide_acquire_item(self, options) -> str:
        """决定锚定获取什么物品。根据当前缺什么来选。"""
        player = self._player
        if not player:
            non_cancel = [o for o in options if o != "取消"]
            return non_cancel[0] if non_cancel else options[0]

        weapons = getattr(player, 'weapons', [])
        real_weapons = [w for w in weapons if w and getattr(w, 'name', '') != "拳击"
                        and not getattr(w, '_hexagram_disabled', False)]
        outer = self._count_outer_armor(player)
        inner = self._count_inner_armor(player)

        # 优先级：缺武器 > 缺外甲 > 缺内甲 > 其他
        if len(real_weapons) == 0:
            weapon_priority = ["高斯步枪", "电磁步枪", "小刀", "远程魔法弹幕"]
            for item in weapon_priority:
                if item in options:
                    return item
        if outer < 2:
            armor_priority = ["AT力场", "陶瓷护甲", "魔法护盾", "盾牌"]
            for item in armor_priority:
                if item in options:
                    return item
        if inner == 0:
            inner_priority = ["额外心脏", "不老泉", "晶化皮肤"]
            for item in inner_priority:
                if item in options:
                    return item

        # 有装备了，拿高价值物品
        luxury_priority = ["AT力场", "高斯步枪", "导弹控制权", "隐身衣", "热成像仪"]
        for item in luxury_priority:
            if item in options:
                return item

        non_cancel = [o for o in options if o != "取消"]
        return non_cancel[0] if non_cancel else options[0]


    def _ripple_decide_destiny_target(self, options, context=None) -> str:
        """爱与记忆之诗伤害目标选择：集中火力打同一个目标。"""
        state = self._game_state
        player = self._player
        if not state or not player:
            return max(options, key=lambda name: self._threat_scores.get(name, 0), default=options[0])

        # 优先读取 hint（由 _ripple_choose_method 设置的目标）
        hint = getattr(self, '_ripple_destiny_target_hint', None)
        if hint:
            hint_player = state.get_player(hint)
            if hint_player and hint_player.is_alive() and hint_player.name in options:
                return hint_player.name

        # 否则集中打最容易击杀的目标（HP+甲最低的）
        best_target = None
        best_score = 999
        for name in options:
            p = next((pl for pl in state.alive_players() if pl.name == name), None)
            if not p or p.player_id == player.player_id:
                continue
            # 有效HP = HP + 外甲数 + 内甲数×0.5
            eff = p.hp + self._count_outer_armor(p) + self._count_inner_armor(p) * 0.5
            # 有死者苏生未触发 → 不优先打（打死会复活）
            if hasattr(p, 'talent') and p.talent and p.talent.name == "死者苏生":
                if hasattr(p.talent, 'used') and not p.talent.used:
                    eff += 10  # 大幅降低优先级
            # 有愿负世且火种高 → 不优先打（打到濒死触发救世主）
            if hasattr(p, 'talent') and p.talent and hasattr(p.talent, 'divinity'):
                if getattr(p.talent, 'divinity', 0) >= 8:
                    eff += 5
            if eff < best_score:
                best_score = eff
                best_target = name

        return best_target or max(options, key=lambda name: self._threat_scores.get(name, 0), default=options[0])

    # ════════════════════════════════════════════════════════
    #  G5 涟漪 AI 辅助判定方法
    # ════════════════════════════════════════════════════════

    def _ripple_get_destiny_stages(self, talent) -> int:
        """计算下一次爱与记忆之诗的伤害段数"""
        initial_count = len(self._game_state.player_order) if self._game_state else 6
        base_n = min(4, max(2, initial_count // 2 + 1))
        extra = max(0, getattr(talent, 'destiny_use_count', 0))  # 已用次数=额外段数
        return base_n + extra


    def _ripple_estimate_effective_stages(self, talent, target) -> int:
        """估算对目标的有效伤害段数（扣除被护甲克制的段数）"""
        total = self._ripple_get_destiny_stages(talent)
        # 简化估算：无视克制段一定命中，其他段有概率被甲挡
        outer = self._count_outer_armor(target)
        # 至少有1段无视克制（第4段起），每段打掉1层甲或1HP
        # 保守估计：有效段数 = 总段数 - 外甲数（每层甲挡1段）
        effective = max(1, total - outer)
        return effective


    def _ripple_get_chaser(self, player, state):
        """获取正在追杀涟漪的玩家（面对面/锁定/同地点攻击者）"""
        markers = getattr(state, 'markers', None)
        if not markers:
            return None
        # 检查 ENGAGED_WITH（面对面）
        engaged = markers.get_related(player.player_id, "ENGAGED_WITH")
        for eid in engaged:
            enemy = state.get_player(eid)
            if enemy and enemy.is_alive():
                return enemy
        # 检查 LOCKED_BY
        locked = markers.get_related(player.player_id, "LOCKED_BY")
        for lid in locked:
            enemy = state.get_player(lid)
            if enemy and enemy.is_alive():
                return enemy
        return None


    def _ripple_is_critical(self, player, state) -> bool:
        """涟漪是否处于危急状态"""
        if player.hp <= 0.5:
            return True
        if player.hp <= 1.0 and self._count_outer_armor(player) == 0:
            chaser = self._ripple_get_chaser(player, state)
            if chaser:
                return True
        return False

    def _ripple_needs_equipment(self, player) -> bool:
        """涟漪是否缺装备"""
        weapons = getattr(player, 'weapons', [])
        real_weapons = [w for w in weapons if w and getattr(w, 'name', '') != "拳击"
                        and not getattr(w, '_hexagram_disabled', False)]
        outer = self._count_outer_armor(player)
        # 缺武器或缺外甲
        return len(real_weapons) == 0 or outer < 1

    def _ripple_combat_strength(self, p) -> float:
        """评估玩家的战斗力"""
        score = 0
        # HP
        score += self._get_effective_hp(p) * 3
        # 护甲
        score += self._count_outer_armor(p) * 2
        score += self._count_inner_armor(p) * 3
        # 武器
        weapons = [w for w in getattr(p, 'weapons', []) if w and getattr(w, 'name', '') != '拳击'
                and not getattr(w, '_hexagram_disabled', False)]
        score += len(weapons) * 2
        # 特殊天赋加成
        if p.talent:
            if hasattr(p.talent, 'divinity') and getattr(p.talent, 'divinity', 0) >= 6:
                score += 5
            if hasattr(p.talent, 'is_savior') and p.talent.is_savior:
                score += 8
            if hasattr(p.talent, 'charges') and hasattr(p.talent, 'name') and '六爻' in p.talent.name:
                score += p.talent.charges * 2
        return score


    def _ripple_should_anchor_kill(self, player, state) -> bool:
        """是否应该用锚定击杀（针对常规手段无法击杀的目标）"""
        # 需要有装备来维护命运（至少有武器+甲）
        if self._ripple_needs_equipment(player):
            return False
        # 检查是否有"难以常规击杀"的目标
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            t = state.get_player(pid)
            if not t or not t.is_alive():
                continue
            # 愿负世（高火种）或死者苏生（未触发）= 难以常规击杀
            if t.talent:
                # 愿负世：火种≥8，常规击杀会触发救世主
                divinity = getattr(t.talent, 'divinity', 0)
                if divinity >= 8:
                    return True
                # 死者苏生：未使用过，常规击杀会复活
                if hasattr(t.talent, 'used') and not t.talent.used and hasattr(t.talent, 'name') and '苏生' in t.talent.name:
                    return True
                # 六爻金身生效中
                if getattr(t.talent, 'immunity_active', False):
                    return True
        return False

    def _ripple_has_love_wish(self, target, state) -> bool:
        """检查目标是否已对涟漪持有者持有爱愿"""
        player = self._player
        if not player:
            return False
        talent = getattr(player, 'talent', None)
        if not talent or not hasattr(talent, 'has_love_wish'):
            return False
        return talent.has_love_wish(target.player_id)

    def _ripple_find_weakest_without_love_wish(self, player, state):
        """找到没有爱愿的最弱玩家（用于献诗-扶弱）"""
        my_pid = player.player_id
        best = None
        best_strength = 999
        for pid in state.player_order:
            if pid == my_pid:
                continue
            t = state.get_player(pid)
            if not t or not t.is_alive():
                continue
            if self._ripple_has_love_wish(t, state):
                continue
            strength = self._ripple_combat_strength(t)
            if strength < best_strength:
                best_strength = strength
                best = t
        return best

    def _ripple_find_tiger_wolf_fight(self, player, state):
        """
        寻找驱虎吞狼机会：找到两个正在交战的玩家，返回 (weaker, stronger)。
        条件：
        1. 两人互相 ENGAGED_WITH（面对面）
        2. 涟漪持有者不在这场战斗中
        3. 弱者尚未持有爱愿
        返回 (weaker_player, stronger_player) 或 None
        """
        markers = getattr(state, 'markers', None)
        if not markers:
            return None

        my_pid = player.player_id
        best_pair = None
        best_strength_diff = 0

        alive_players = [state.get_player(pid) for pid in state.player_order
                        if pid != my_pid and state.get_player(pid) and state.get_player(pid).is_alive()]

        for i, p1 in enumerate(alive_players):
            for p2 in alive_players[i+1:]:
                # 检查是否互相面对面
                engaged = markers.get_related(p1.player_id, "ENGAGED_WITH")
                if p2.player_id not in engaged:
                    continue
                # 涟漪持有者不能卷入这场战斗
                my_engaged = markers.get_related(my_pid, "ENGAGED_WITH")
                if p1.player_id in my_engaged or p2.player_id in my_engaged:
                    continue
                # 计算强弱
                s1 = self._ripple_combat_strength(p1)
                s2 = self._ripple_combat_strength(p2)
                if s1 == s2:
                    continue
                stronger = p1 if s1 > s2 else p2
                weaker = p2 if s1 > s2 else p1
                # 弱者不能已有爱愿
                if self._ripple_has_love_wish(weaker, state):
                    continue
                diff = abs(s1 - s2)
                if diff > best_strength_diff:
                    best_strength_diff = diff
                    best_pair = (weaker, stronger)

        return best_pair

    def _ripple_find_chaser(self, player, state):
        """找到正在追杀涟漪持有者的玩家"""
        markers = getattr(state, 'markers', None)
        if not markers:
            return None
        my_pid = player.player_id
        engaged = markers.get_related(my_pid, "ENGAGED_WITH")
        locked_by = markers.get_related(my_pid, "LOCKED_BY")
        chasers = set(engaged) | set(locked_by)
        if not chasers:
            pc = self._police_cache or {}
            if pc.get("report_target") == my_pid and pc.get("report_phase") == "dispatched":
                return "police"
            return None
        best = None
        best_threat = -1
        for pid in chasers:
            t = state.get_player(pid)
            if t and t.is_alive():
                threat = self._ripple_combat_strength(t)
                if threat > best_threat:
                    best_threat = threat
                    best = t
        return best

    def _ripple_can_kill_with_destiny(self, player, target, state) -> bool:
        """判断爱与记忆之诗能否确定击杀目标。
        先检查追忆是否足够支付递增费用，再复用
        _ripple_estimate_effective_stages 保持与伤害分配阶段一致的保守估算。"""
        talent = player.talent
        if not talent:
            return False
        # 费用检查：爱与记忆之诗费用递增 min(24, 12 + 3 × 已使用次数)
        if hasattr(talent, 'get_destiny_cost'):
            cost = talent.get_destiny_cost()
            if talent.reminiscence < cost:
                return False
        effective = self._ripple_estimate_effective_stages(talent, target)
        total_hp = target.hp + self._count_outer_armor(target) + self._count_inner_armor(target) * 0.5
        return effective >= total_hp

    def _ripple_choose_method(self, player, state, options) -> str:
        """涟漪发动时选择方式（9级优先级，带 hint 系统）"""
        self._ripple_priority_reason = ''
        self._ripple_destiny_target_hint = None
        self._ripple_poem_target_hint = None
        poem_opt = None
        anchor_opt = None
        for opt in options:
            if "献诗" in opt or "方式二" in opt:
                poem_opt = opt
            if "锚定" in opt or "方式一" in opt:
                anchor_opt = opt

        pc = self._police_cache or {}
        captain_id = pc.get("captain_id")
        if captain_id:
            captain = state.get_player(captain_id)
            if captain and captain.is_alive():
                if self._ripple_can_kill_with_destiny(player, captain, state):
                    if poem_opt:
                        self._ripple_priority_reason = "斩首队长"
                        self._ripple_destiny_target_hint = captain_id
                        return poem_opt

        for pid in state.player_order:
            if pid == player.player_id:
                continue
            t = state.get_player(pid)
            if not t or not t.is_alive():
                continue
            if t.talent:
                if hasattr(t.talent, 'used') and not t.talent.used and hasattr(t.talent, 'name') and '苏生' in t.talent.name:
                    continue
                if hasattr(t.talent, 'divinity') and getattr(t.talent, 'divinity', 0) >= 8:
                    continue
            if self._ripple_can_kill_with_destiny(player, t, state):
                if poem_opt:
                    self._ripple_priority_reason = "确定击杀"
                    self._ripple_destiny_target_hint = pid
                    return poem_opt

        chaser = self._ripple_find_chaser(player, state)
        if chaser and chaser != "police":
            if self._ripple_can_kill_with_destiny(player, chaser, state):
                if poem_opt:
                    self._ripple_priority_reason = "反杀追杀者"
                    self._ripple_destiny_target_hint = chaser.player_id
                    return poem_opt
            if player.hp <= 0.5 or self._count_outer_armor(player) == 0:
                if poem_opt:
                    self._ripple_priority_reason = "危急保命"
                    self._ripple_poem_target_hint = chaser.player_id
                    return poem_opt
        elif chaser == "police":
            if captain_id:
                captain = state.get_player(captain_id)
                if captain and captain.is_alive() and self._ripple_can_kill_with_destiny(player, captain, state):
                    if poem_opt:
                        self._ripple_priority_reason = "斩首队长（被警察追杀）"
                        self._ripple_destiny_target_hint = captain_id
                        return poem_opt
            if poem_opt:
                self._ripple_priority_reason = "被警察追杀保命"
                return poem_opt

        if self._ripple_needs_equipment(player) and anchor_opt:
            self._ripple_priority_reason = "锚定获取装备"
            return anchor_opt

        tiger_wolf = self._ripple_find_tiger_wolf_fight(player, state)
        if tiger_wolf:
            weaker, stronger = tiger_wolf
            if poem_opt:
                self._ripple_priority_reason = "驱虎吞狼"
                self._ripple_poem_target_hint = weaker.player_id
                return poem_opt

        if self._ripple_should_anchor_kill(player, state) and anchor_opt:
            self._ripple_priority_reason = "锚定击杀"
            return anchor_opt

        weakest = self._ripple_find_weakest_without_love_wish(player, state)
        if weakest and poem_opt:
            self._ripple_priority_reason = "扶弱"
            self._ripple_poem_target_hint = weakest.player_id
            return poem_opt

        if poem_opt:
            self._ripple_priority_reason = "通用输出"
            return poem_opt

        return options[0]

    def _ripple_choose_poem_target(self, player, state, options) -> str:
        """献诗目标选择（基于 hint 系统）"""
        reason = getattr(self, '_ripple_priority_reason', '')
        hint = getattr(self, '_ripple_poem_target_hint', None)

        # 爱与记忆相关的 reason → 选自己（触发爱与记忆之诗）
        if reason in ("斩首队长", "确定击杀", "反杀追杀者", "通用输出", "斩首队长（被警察追杀）"):
            if player.name in options:
                return player.name

        if hint:
            hint_player = state.get_player(hint)
            if hint_player and hint_player.name in options:
                return hint_player.name

        if reason == "危急保命":
            chaser = self._ripple_find_chaser(player, state)
            if chaser and chaser != "police":
                if chaser.name in options:
                    return chaser.name

        if reason == "被警察追杀保命":
            for name in options:
                if name == player.name:
                    continue
                for pid in state.player_order:
                    t = state.get_player(pid)
                    if t and t.name == name and not self._ripple_has_love_wish(t, state):
                        return name

        weakest = self._ripple_find_weakest_without_love_wish(player, state)
        if weakest and weakest.name in options:
            return weakest.name

        if player.name in options:
            return player.name
        return options[0] if options else "取消"

    # ════════════════════════════════════════════════════════
    #  confirm：确认决策
    # ════════════════════════════════════════════════════════

    def confirm(self, prompt: str, context: Optional[Dict] = None) -> bool:
        # 强买通行证：当prompt包含"强买通行证"且AI需要去军事基地时同意
        if "强买通行证" in prompt:
            # 星野必须去军事基地
            if self._player and self._has_hoshino_talent(self._player):
                return True

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

        in_combat = len(engaged_enemies) > 0
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
