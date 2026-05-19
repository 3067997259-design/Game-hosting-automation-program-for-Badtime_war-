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
            decision = self._ripple_decide_method(player, state, context)
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

        if situation in ("poem_law_extra_action", "poem_law_police_action"):
            return options[0] if options else ""

        return None

    # ── 涟漪辅助方法 ──

    def _ripple_decide_method(self, player, state, context) -> str:
        if not player or not state:
            return "poem"
        if self._ripple_needs_equipment(player):
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
        if player.name in options:
            return player.name
        weakest = self._find_weakest(player, state, options)
        if weakest:
            return weakest
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
        scores: Dict[str, float] = {
            "steal_armor": 0, "disarm": 0, "thunder": 0,
            "escape": 0, "extra_turn": 0, "immunity": 0,
        }
        nearby = GameQuery.get_same_location_targets(player, state)
        if not nearby:
            scores["escape"] = 10
            return scores
        for t in nearby:
            outer = GameQuery.count_outer_armor(t)
            if outer > 0:
                scores["steal_armor"] += outer * 3
            weapons = [w for w in getattr(t, 'weapons', [])
                       if w and getattr(w, 'name', '') != "拳击"]
            if weapons:
                scores["disarm"] += len(weapons) * 2
        scores["thunder"] = len(nearby) * 3
        scores["extra_turn"] = 5
        my_outer = GameQuery.count_outer_armor(player)
        if my_outer == 0:
            scores["immunity"] = 8
        elif player.hp <= 1.0:
            scores["immunity"] = 6
        scores["escape"] = 2
        return scores
