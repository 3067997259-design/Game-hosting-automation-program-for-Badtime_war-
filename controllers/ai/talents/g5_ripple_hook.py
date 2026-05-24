"""RippleAIHook —— G5「往世的涟漪」天赋AI钩子"""

from __future__ import annotations
from typing import Dict, List, Optional, Any
import random
from controllers.ai.talents.base_hook import BaseTalentAIHook
from controllers.ai.game_query import GameQuery


class RippleAIHook(BaseTalentAIHook):
    talent_name = "往世的涟漪"

    def __init__(self, controller: Any):
        self._ctrl = controller

    def handle_choose(
        self, player: Any, state: Any, situation: str,
        options: List[str], context: Dict,
    ) -> Optional[str]:
        threat_scores = context.get("threat_scores", {})
        personality = context.get("personality", "balanced")

        if situation == "talent_t0":
            talent_name = context.get("talent_name", "")
            if "涟漪" not in talent_name:
                return None
            for opt in options:
                if "发动" in opt:
                    return opt
            return options[0]

        if situation == "ripple_choose_method":
            return self._ripple_choose_method(player, state, options, context)

        if situation == "resurrection_pick_target":
            if player and player.name in options:
                return player.name
            return options[0]

        if situation == "ripple_anchor_type":
            anchor_decision = self._ripple_decide_anchor_type(player)
            for opt in options:
                if anchor_decision in opt:
                    return opt
            return options[0]

        if situation == "ripple_poem_target":
            return self._ripple_decide_poem_target(player, state, options, context)

        if situation in ("ripple_anchor_kill_target", "ripple_anchor_armor_target"):
            player_opts = [o for o in options if o != "取消"]
            if player_opts:
                return max(player_opts, key=lambda name: threat_scores.get(name, 0))
            return options[0]

        if situation == "ripple_anchor_armor_pick":
            non_cancel = [o for o in options if o != "取消"]
            return non_cancel[0] if non_cancel else options[0]

        if situation == "ripple_anchor_acquire_item":
            return self._ripple_decide_acquire_item(player, options)

        if situation == "ripple_anchor_arrive_loc":
            non_cancel = [o for o in options if o != "取消"]
            if non_cancel:
                return random.choice(non_cancel)
            return options[0]

        if situation == "ripple_anchor_fail":
            if personality == "aggressive":
                for opt in options:
                    if "留在当下" in opt:
                        return opt
            for opt in options:
                if "回到过去" in opt:
                    return opt
            return options[0]

        if situation == "ripple_destiny_damage":
            return self._ripple_decide_destiny_target(player, state, options, threat_scores, context)

        if situation == "ripple_hexagram_free_choice":
            if player and state:
                scores = self._score_hexagram_effects(player, state)
                best_key = max(scores, key=scores.get)  # type: ignore
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
            for opt in options:
                if "天雷" in opt or "潜龙" in opt:
                    return opt
            return options[0]

        if situation == "poem_nightwatch_choice":
            talent = getattr(player, 'talent', None)
            if talent and getattr(talent, 'is_terror', False):
                for opt in options:
                    if "接受" in opt:
                        return opt
            for opt in options:
                if "拒绝" in opt:
                    return opt
            return options[-1]

        if situation == "poem_law_extra_action":
            # 选择与战斗目标同地点的警察单位
            combat_target = context.get("combat_target")
            police_cache = context.get("police_cache") or {}
            if combat_target:
                from controllers.ai.game_query import GameQuery
                result = GameQuery.select_police_unit_at_target(combat_target, police_cache, options)
                if result:
                    return result
            return options[0] if options else ""
        if situation == "poem_law_police_action":
            return options[0] if options else ""

        return None

    # ── 涟漪辅助方法 ──

    def _ripple_decide_method(self, player, state, context) -> str:
        if not player or not state:
            return "poem"
        result = self._ripple_choose_method(
            player, state, ["方式一：锚定命运", "方式二：献诗"], context)
        if "锚定" in result or "方式一" in result:
            return "anchor"
        return "poem"

    def _ripple_decide_anchor_type(self, player) -> str:
        if not player:
            return "获取"
        if self._ripple_needs_equipment(player):
            return "获取"
        return "击杀"

    def _ripple_decide_poem_target(self, player, state, options, context) -> str:
        if not player or not state:
            if player and player.name in options:
                return player.name
            return options[0]

        reason = getattr(self, '_ripple_priority_reason', '')
        hint = getattr(self, '_ripple_poem_target_hint', None)

        if reason in ("斩首队长", "确定击杀", "反杀追杀者", "通用输出", "斩首队长（被警察追杀）", "1v1输出"):
            if player.name in options:
                return player.name

        if hint:
            hint_player = state.get_player(hint)
            if hint_player and hint_player.name in options:
                return hint_player.name

        if reason == "危急保命":
            chaser = self._ripple_find_chaser(player, state, context)
            if chaser and chaser != "police" and chaser.name in options:
                return chaser.name

        if reason == "被警察追杀保命":
            for name in options:
                if name == player.name:
                    continue
                for pid in state.player_order:
                    target = state.get_player(pid)
                    if target and target.name == name and not self._ripple_has_love_wish(player, target):
                        return name

        weakest = self._ripple_find_weakest_without_love_wish(player, state)
        if weakest and weakest.name in options:
            return weakest.name
        if player.name in options:
            return player.name
        return options[0] if options else "取消"

    def _ripple_decide_acquire_item(self, player, options) -> str:
        if not player:
            non_cancel = [o for o in options if o != "取消"]
            return non_cancel[0] if non_cancel else options[0]

        weapons = getattr(player, 'weapons', [])
        real_weapons = [w for w in weapons if w and getattr(w, 'name', '') != "拳击"
                        and not getattr(w, '_hexagram_disabled', False)]
        outer = GameQuery.count_outer_armor(player)
        inner = GameQuery.count_inner_armor(player)

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

        luxury_priority = ["AT力场", "高斯步枪", "导弹控制权", "隐身衣", "热成像仪"]
        for item in luxury_priority:
            if item in options:
                return item

        non_cancel = [o for o in options if o != "取消"]
        return non_cancel[0] if non_cancel else options[0]

    def _ripple_decide_destiny_target(self, player, state, options, threat_scores, context) -> str:
        if not state or not player:
            return max(options, key=lambda name: threat_scores.get(name, 0), default=options[0])

        hint = getattr(self, '_ripple_destiny_target_hint', None)
        if hint:
            hint_player = state.get_player(hint)
            if hint_player and hint_player.is_alive() and hint_player.name in options:
                return hint_player.name

        best_target = None
        best_score = 999
        for name in options:
            p = next((pl for pl in state.alive_players() if pl.name == name), None)
            if not p or p.player_id == player.player_id:
                continue
            eff = p.hp + GameQuery.count_outer_armor(p) + GameQuery.count_inner_armor(p) * 0.5
            if hasattr(p, 'talent') and p.talent and p.talent.name == "死者苏生":
                if hasattr(p.talent, 'used') and not p.talent.used:
                    eff += 10
            if hasattr(p, 'talent') and p.talent and hasattr(p.talent, 'divinity'):
                if getattr(p.talent, 'divinity', 0) >= 8:
                    eff += 5
            if eff < best_score:
                best_score = eff
                best_target = name

        return best_target or max(options, key=lambda name: threat_scores.get(name, 0), default=options[0])

    def _ripple_needs_equipment(self, player) -> bool:
        weapons = getattr(player, 'weapons', [])
        real_weapons = [w for w in weapons if w and getattr(w, 'name', '') != "拳击"
                        and not getattr(w, '_hexagram_disabled', False)]
        outer = GameQuery.count_outer_armor(player)
        return len(real_weapons) == 0 or outer < 1

    def _ripple_choose_method(self, player, state, options, context) -> str:
        """涟漪发动方式选择：恢复旧架构的 9 级优先级与 hint 系统。"""
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

        # ★ 1v1 终局：只放爱与记忆之诗或守夜人之诗，放不起就锚定
        alive_enemies = [p for p in state.players.values()
                        if p.is_alive() and p.player_id != player.player_id]
        if len(alive_enemies) == 1:
            enemy = alive_enemies[0]
            talent = getattr(player, 'talent', None)
            if talent:
                cost = getattr(talent, 'get_destiny_cost', lambda: 12)()
                if getattr(talent, 'reminiscence', 0) >= cost and poem_opt:
                    self._ripple_priority_reason = "1v1输出"
                    return poem_opt  # target = self (爱与记忆之诗)
                enemy_talent = getattr(enemy, 'talent', None)
                if (enemy_talent
                        and getattr(enemy_talent, 'name', '') == "大叔我啊，剪短发了"
                        and not getattr(enemy_talent, 'is_terror', False)
                        and poem_opt):
                    self._ripple_priority_reason = "1v1守夜人"
                    self._ripple_poem_target_hint = enemy.player_id
                    return poem_opt
            if anchor_opt:
                self._ripple_priority_reason = "1v1锚定"
                return anchor_opt
            return options[0]

        pc = self._ripple_police_cache(context)
        captain_id = pc.get("captain_id")
        if captain_id:
            captain = state.get_player(captain_id)
            if captain and captain.is_alive() and self._ripple_can_kill_with_destiny(player, captain, state):
                if poem_opt:
                    self._ripple_priority_reason = "斩首队长"
                    self._ripple_destiny_target_hint = captain_id
                    return poem_opt

        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if not target or not target.is_alive():
                continue
            talent = getattr(target, 'talent', None)
            if talent:
                if hasattr(talent, 'used') and not talent.used and hasattr(talent, 'name') and '苏生' in talent.name:
                    continue
                if hasattr(talent, 'divinity') and getattr(talent, 'divinity', 0) >= 8:
                    continue
            if self._ripple_can_kill_with_destiny(player, target, state):
                if poem_opt:
                    self._ripple_priority_reason = "确定击杀"
                    self._ripple_destiny_target_hint = pid
                    return poem_opt

        chaser = self._ripple_find_chaser(player, state, context)
        if chaser and chaser != "police":
            if self._ripple_can_kill_with_destiny(player, chaser, state):
                if poem_opt:
                    self._ripple_priority_reason = "反杀追杀者"
                    self._ripple_destiny_target_hint = chaser.player_id
                    return poem_opt
            if player.hp <= 0.5 or GameQuery.count_outer_armor(player) == 0:
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
            weaker, _stronger = tiger_wolf
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

    def _ripple_police_cache(self, context) -> Dict:
        return context.get("police_cache") or getattr(self._ctrl, '_police_cache', None) or {}

    def _ripple_get_destiny_stages(self, talent, state) -> int:
        initial_count = len(getattr(state, 'player_order', []) or []) or 6
        base_n = min(4, max(2, initial_count // 2 + 1))
        extra = max(0, getattr(talent, 'destiny_use_count', 0))
        return base_n + extra

    def _ripple_estimate_effective_stages(self, talent, target, state) -> int:
        total = self._ripple_get_destiny_stages(talent, state)
        outer = GameQuery.count_outer_armor(target)
        return max(1, total - outer)

    def _ripple_can_kill_with_destiny(self, player, target, state) -> bool:
        talent = getattr(player, 'talent', None)
        if not talent:
            return False
        if hasattr(talent, 'get_destiny_cost'):
            cost = talent.get_destiny_cost()
            if getattr(talent, 'reminiscence', 0) < cost:
                return False
        effective = self._ripple_estimate_effective_stages(talent, target, state)
        total_hp = target.hp + GameQuery.count_outer_armor(target) + GameQuery.count_inner_armor(target) * 0.5
        return effective >= total_hp

    def _ripple_effective_hp(self, player) -> float:
        hp = player.hp
        talent = getattr(player, 'talent', None)
        if talent:
            hp += max(0.0, getattr(talent, 'temp_hp', 0.0))
            hp += max(0, getattr(talent, 'ardent_wish_charges', 0)) * 0.5
        return hp

    def _ripple_combat_strength(self, player) -> float:
        score = self._ripple_effective_hp(player) * 3
        score += GameQuery.count_outer_armor(player) * 2
        score += GameQuery.count_inner_armor(player) * 3
        weapons = [w for w in getattr(player, 'weapons', [])
                   if w and getattr(w, 'name', '') != '拳击'
                   and not getattr(w, '_hexagram_disabled', False)]
        score += len(weapons) * 2
        talent = getattr(player, 'talent', None)
        if talent:
            if hasattr(talent, 'divinity') and getattr(talent, 'divinity', 0) >= 6:
                score += 5
            if hasattr(talent, 'is_savior') and talent.is_savior:
                score += 8
            if hasattr(talent, 'charges') and hasattr(talent, 'name') and '六爻' in talent.name:
                score += talent.charges * 2
        return score

    def _ripple_find_chaser(self, player, state, context):
        markers = getattr(state, 'markers', None)
        if not markers:
            return None
        my_pid = player.player_id
        engaged = markers.get_related(my_pid, "ENGAGED_WITH")
        locked_by = markers.get_related(my_pid, "LOCKED_BY")
        chasers = set(engaged) | set(locked_by)
        if not chasers:
            pc = self._ripple_police_cache(context)
            if pc.get("report_target") == my_pid and pc.get("report_phase") == "dispatched":
                return "police"
            return None
        best = None
        best_threat = -1
        for pid in chasers:
            target = state.get_player(pid)
            if target and target.is_alive():
                threat = self._ripple_combat_strength(target)
                if threat > best_threat:
                    best_threat = threat
                    best = target
        return best

    def _ripple_has_love_wish(self, player, target) -> bool:
        talent = getattr(player, 'talent', None)
        if not talent or not hasattr(talent, 'has_love_wish'):
            return False
        return talent.has_love_wish(target.player_id)

    def _ripple_find_weakest_without_love_wish(self, player, state):
        best = None
        best_strength = 999
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if not target or not target.is_alive():
                continue
            if self._ripple_has_love_wish(player, target):
                continue
            strength = self._ripple_combat_strength(target)
            if strength < best_strength:
                best_strength = strength
                best = target
        return best

    def _ripple_find_tiger_wolf_fight(self, player, state):
        markers = getattr(state, 'markers', None)
        if not markers:
            return None
        my_pid = player.player_id
        best_pair = None
        best_strength_diff = 0
        alive_players = [
            state.get_player(pid) for pid in state.player_order
            if pid != my_pid and state.get_player(pid) and state.get_player(pid).is_alive()
        ]
        for i, first in enumerate(alive_players):
            for second in alive_players[i + 1:]:
                engaged = markers.get_related(first.player_id, "ENGAGED_WITH")
                if second.player_id not in engaged:
                    continue
                my_engaged = markers.get_related(my_pid, "ENGAGED_WITH")
                if first.player_id in my_engaged or second.player_id in my_engaged:
                    continue
                first_strength = self._ripple_combat_strength(first)
                second_strength = self._ripple_combat_strength(second)
                if first_strength == second_strength:
                    continue
                stronger = first if first_strength > second_strength else second
                weaker = second if first_strength > second_strength else first
                if self._ripple_has_love_wish(player, weaker):
                    continue
                diff = abs(first_strength - second_strength)
                if diff > best_strength_diff:
                    best_strength_diff = diff
                    best_pair = (weaker, stronger)
        return best_pair

    def _ripple_should_anchor_kill(self, player, state) -> bool:
        if self._ripple_needs_equipment(player):
            return False
        for pid in state.player_order:
            if pid == player.player_id:
                continue
            target = state.get_player(pid)
            if not target or not target.is_alive():
                continue
            talent = getattr(target, 'talent', None)
            if not talent:
                continue
            if getattr(talent, 'divinity', 0) >= 8:
                return True
            if hasattr(talent, 'used') and not talent.used and hasattr(talent, 'name') and '苏生' in talent.name:
                return True
            if getattr(talent, 'immunity_active', False):
                return True
        return False

    def _find_weakest(self, player, state, options) -> Optional[str]:
        best = None
        best_strength = 999.0
        for name in options:
            if name == "取消":
                continue
            p = next((pl for pl in state.alive_players() if pl.name == name), None)
            if not p or p.player_id == player.player_id:
                continue
            strength = p.hp + GameQuery.count_outer_armor(p) + GameQuery.count_inner_armor(p)
            if strength < best_strength:
                best_strength = strength
                best = name
        return best

    def _score_hexagram_effects(self, player, state) -> Dict[str, float]:
        if hasattr(self._ctrl, '_score_hexagram_effects'):
            return self._ctrl._score_hexagram_effects(player, state)
        return {"thunder": 3, "steal_armor": 4, "immunity": 2,
                "disarm": 2, "extra_turn": 9, "escape": 2}
