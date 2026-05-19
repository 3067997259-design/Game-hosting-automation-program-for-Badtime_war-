"""HexagramAIHook —— T4「六爻」天赋AI钩子"""

from __future__ import annotations
from typing import Dict, List, Optional, Any
import random
from controllers.ai.talents.base_hook import BaseTalentAIHook
from controllers.ai.game_query import GameQuery

HEXAGRAM_OUTCOME_MAP = {
    "石头": ["steal_armor", "disarm", "escape"],
    "剪刀": ["disarm", "thunder", "extra_turn"],
    "布":   ["escape", "extra_turn", "immunity"],
}


class HexagramAIHook(BaseTalentAIHook):
    talent_name = "六爻"

    def __init__(self, controller: Any):
        self._ctrl = controller

    def handle_choose(
        self, player: Any, state: Any, situation: str,
        options: List[str], context: Dict,
    ) -> Optional[str]:
        threat_scores = context.get("threat_scores", {})

        if situation == "hexagram_my_choice":
            return self._hexagram_pick_caster(player, state, options)
        if situation == "hexagram_opp_choice":
            caster = self._find_hexagram_caster(state) if state else None
            if caster and state:
                return self._hexagram_pick_opponent(caster, state, options)
            return random.choice(options)

        if situation == "hexagram_thunder_target":
            return max(options, key=lambda name: threat_scores.get(name, 0), default=options[0])
        if situation == "hexagram_pick_armor":
            armor_priority = ["AT力场", "陶瓷护甲", "魔法护盾", "盾牌", "晶化皮肤", "不老泉", "额外心脏"]
            for preferred in armor_priority:
                if preferred in options:
                    return preferred
            return options[0]
        if situation == "hexagram_pick_opponent":
            return max(options, key=lambda name: threat_scores.get(name, 0), default=options[0])
        if situation == "hexagram_steal_target":
            return max(options, key=lambda name: threat_scores.get(name, 0), default=options[0])
        if situation == "hexagram_disarm_target":
            return max(options, key=lambda name: threat_scores.get(name, 0), default=options[0])
        if situation == "hexagram_free_target":
            return max(options, key=lambda name: threat_scores.get(name, 0), default=options[0])
        if situation == "hexagram_steal_pick":
            armor_priority = ["AT力场", "陶瓷护甲", "魔法护盾", "盾牌", "晶化皮肤"]
            for preferred in armor_priority:
                for opt in options:
                    if preferred in opt:
                        return opt
            return options[0]

        if situation == "talent_t0":
            talent_name = context.get("talent_name", "")
            if "六爻" not in talent_name:
                return None
            for opt in options:
                if "发动" in opt:
                    return opt
            return options[0]

        return None

    # ── 猜拳辅助 ──

    def _hexagram_pick_caster(self, player, state, options) -> str:
        if not player or not state:
            return random.choice(options)
        scores = self._score_hexagram_effects(player, state)
        best_key = max(scores, key=scores.get)  # type: ignore
        rps_map = {
            "steal_armor": "石头",
            "disarm": "剪刀",
            "thunder": "剪刀",
            "escape": "布",
            "extra_turn": "布",
            "immunity": "布",
        }
        best_rps = rps_map.get(best_key, "石头")
        if best_rps in options:
            return best_rps
        return random.choice(options)

    def _hexagram_pick_opponent(self, caster, state, options) -> str:
        if not caster or not state:
            return random.choice(options)
        scores = self._score_hexagram_effects(caster, state)
        worst_key = min(scores, key=scores.get)  # type: ignore
        rps_map = {
            "steal_armor": "石头",
            "disarm": "剪刀",
            "thunder": "剪刀",
            "escape": "布",
            "extra_turn": "布",
            "immunity": "布",
        }
        counter_map = {"石头": "布", "剪刀": "石头", "布": "剪刀"}
        worst_rps = rps_map.get(worst_key, "石头")
        counter = counter_map.get(worst_rps, "石头")
        if counter in options:
            return counter
        return random.choice(options)

    @staticmethod
    def _find_hexagram_caster(state) -> Optional[Any]:
        if not state:
            return None
        for pid in state.player_order:
            p = state.get_player(pid)
            if p and p.is_alive():
                t = getattr(p, 'talent', None)
                if t and '六爻' in getattr(t, 'name', ''):
                    return p
        return None

    def _score_hexagram_effects(self, player, state) -> Dict[str, float]:
        scores: Dict[str, float] = {
            "steal_armor": 0,
            "disarm": 0,
            "thunder": 0,
            "escape": 0,
            "extra_turn": 0,
            "immunity": 0,
        }
        nearby = GameQuery.get_same_location_targets(player, state)
        if not nearby:
            scores["escape"] = 10
            return scores

        for t in nearby:
            outer = GameQuery.count_outer_armor(t)
            inner = GameQuery.count_inner_armor(t)
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
