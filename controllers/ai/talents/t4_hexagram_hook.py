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
        best_choice = None
        best_worst = -999
        for choice in options:
            outcomes = HEXAGRAM_OUTCOME_MAP.get(choice, [])
            if not outcomes:
                continue
            worst = min(scores.get(effect, 0) for effect in outcomes)
            if worst > best_worst:
                best_worst = worst
                best_choice = choice
        return best_choice or random.choice(options)

    def _hexagram_pick_opponent(self, caster, state, options) -> str:
        if not caster or not state:
            return random.choice(options)
        scores = self._score_hexagram_effects(caster, state)
        best_choice = None
        best_min_max = 999
        rps_order = ["石头", "剪刀", "布"]
        for opp_choice in options:
            if opp_choice not in rps_order:
                continue
            opp_idx = rps_order.index(opp_choice)
            caster_best = -999
            for outcomes in HEXAGRAM_OUTCOME_MAP.values():
                effect = outcomes[opp_idx]
                caster_best = max(caster_best, scores.get(effect, 0))
            if caster_best < best_min_max:
                best_min_max = caster_best
                best_choice = opp_choice
        return best_choice or random.choice(options)

    def _find_hexagram_caster(self, state) -> Optional[Any]:
        if not state:
            return None
        my_id = getattr(self._ctrl, '_my_id', None)
        for pid in state.player_order:
            if pid == my_id:
                continue
            p = state.get_player(pid)
            if p and p.is_alive():
                t = getattr(p, 'talent', None)
                if t and '六爻' in getattr(t, 'name', ''):
                    return p
        return None

    def _score_hexagram_effects(self, player, state) -> Dict[str, float]:
        scores: Dict[str, float] = {}
        hp = player.hp
        my_outer = GameQuery.count_outer_armor(player)
        is_critical = self._ctrl._is_critical(player, state)
        dev_complete = self._ctrl._is_development_complete(player, state)
        has_kill = self._ctrl._has_kill_opportunity(player, state)

        markers_obj = getattr(state, 'markers', None)
        engaged_enemies = []
        locked_by_enemies = []
        if markers_obj:
            locked_list = (
                markers_obj.get_related(player.player_id, "LOCKED_BY")
                if hasattr(markers_obj, 'get_related') else set()
            )
            for pid in state.player_order:
                if pid == player.player_id:
                    continue
                target = state.get_player(pid)
                if target and target.is_alive():
                    if (hasattr(markers_obj, 'has_relation')
                            and markers_obj.has_relation(player.player_id, 'ENGAGED_WITH', pid)):
                        engaged_enemies.append(target)
                    if pid in locked_list:
                        locked_by_enemies.append(target)

        in_combat = len(engaged_enemies) > 0
        losing = in_combat and (hp <= 1.0 or is_critical)
        if losing:
            situation = "D"
        elif in_combat:
            situation = "C"
        elif dev_complete or has_kill:
            situation = "B"
        else:
            situation = "A"

        if situation == "B":
            best_kill = False
            best_armor_break = False
            for pid in state.player_order:
                if pid == player.player_id:
                    continue
                target = state.get_player(pid)
                if target and target.is_alive():
                    if target.hp <= 1.0 and GameQuery.count_outer_armor(target) == 0:
                        best_kill = True
                    if GameQuery.count_outer_armor(target) > 0:
                        best_armor_break = True
            scores["thunder"] = 10 if best_kill else (9 if best_armor_break else 7)
        elif situation == "C":
            combat_target_killable = any(
                enemy.hp <= 1.0 and GameQuery.count_outer_armor(enemy) == 0
                for enemy in engaged_enemies
            )
            scores["thunder"] = 8 if combat_target_killable else 6
        else:
            scores["thunder"] = 3

        enemy_has_armor = any(
            GameQuery.count_outer_armor(state.get_player(pid)) > 0
            for pid in state.player_order
            if pid != player.player_id
            and state.get_player(pid) and state.get_player(pid).is_alive()
        )
        if situation == "A":
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
        else:
            scores["steal_armor"] = 2

        if situation == "D":
            scores["immunity"] = 10
        elif situation == "C":
            if hp <= 1.0:
                scores["immunity"] = 9
            elif hp <= 1.5:
                scores["immunity"] = 7
            else:
                scores["immunity"] = 6
        else:
            scores["immunity"] = 2

        if situation in ("C", "D"):
            best_disarm = 0
            targets_to_check = []
            seen_target_ids = set()
            for target in engaged_enemies + locked_by_enemies:
                target_id = getattr(target, 'player_id', id(target))
                if target_id not in seen_target_ids:
                    seen_target_ids.add(target_id)
                    targets_to_check.append(target)
            if not targets_to_check:
                targets_to_check = [
                    state.get_player(pid) for pid in state.player_order
                    if pid != player.player_id
                    and state.get_player(pid) and state.get_player(pid).is_alive()
                ]
            for target in targets_to_check:
                real_weapons = [
                    w for w in getattr(target, 'weapons', [])
                    if w and getattr(w, 'name', '') != "拳击"
                    and not getattr(w, '_hexagram_disabled', False)
                ]
                if len(real_weapons) == 1:
                    best_disarm = max(best_disarm, 9)
                elif len(real_weapons) > 1:
                    best_disarm = max(best_disarm, 7)
            scores["disarm"] = best_disarm if best_disarm > 0 else 4
            if situation == "D":
                scores["disarm"] = min(scores["disarm"], 6)
        elif situation == "B":
            scores["disarm"] = 5
        else:
            scores["disarm"] = 2

        if situation == "A":
            scores["extra_turn"] = 9
        elif situation in ("B", "C"):
            scores["extra_turn"] = 8
        else:
            scores["extra_turn"] = 5

        is_locked = len(locked_by_enemies) > 0
        if situation == "D":
            scores["escape"] = 10 if is_locked else 9
        elif situation == "C":
            scores["escape"] = 5 if is_locked else 3
        else:
            scores["escape"] = 2
        return scores
