"""StarAIHook —— T3「天星」天赋AI钩子"""

from __future__ import annotations
from typing import Dict, List, Optional, Any
from controllers.ai.talents.base_hook import BaseTalentAIHook
from controllers.ai.game_query import GameQuery


class StarAIHook(BaseTalentAIHook):
    talent_name = "天星"

    def __init__(self, controller: Any):
        self._ctrl = controller

    def handle_choose(
        self, player: Any, state: Any, situation: str,
        options: List[str], context: Dict,
    ) -> Optional[str]:
        if situation != "talent_t0":
            return None
        talent_name = context.get("talent_name", "")
        if talent_name != "天星":
            return None

        if not player or not state:
            for opt in options:
                if "不发动" in opt or "正常" in opt:
                    return opt
            return options[-1]

        talent = getattr(player, 'talent', None)
        uses = getattr(talent, 'uses_remaining', 0) if talent else 0
        if uses <= 0:
            for opt in options:
                if "不发动" in opt or "正常" in opt:
                    return opt
            return options[-1]

        nearby = GameQuery.get_same_location_targets(player, state)
        if not nearby:
            for opt in options:
                if "不发动" in opt or "正常" in opt:
                    return opt
            return options[-1]

        police_count = 0
        if hasattr(state, 'police') and state.police:
            police_at_loc = [u for u in state.police.units_at(player.location) if u.is_alive()]
            police_count = len(police_at_loc)
        target_count = len(nearby) + police_count
        damage_per_target = min(1.0 + 0.5 * target_count, 3.0)

        has_executable = False
        for t in nearby:
            outer = GameQuery.count_outer_armor(t)
            if t.hp + outer <= damage_per_target:
                has_executable = True
                break

        has_protected_captain = False
        for t in nearby:
            if getattr(t, 'is_captain', False):
                pe = getattr(state, 'police_engine', None)
                if pe and pe.is_protected_by_police(t.player_id):
                    has_protected_captain = True
                    break

        terror_found = any(
            getattr(getattr(state.get_player(pid), 'talent', None), 'is_terror', False)
            for pid in state.player_order
            if pid != player.player_id
            and state.get_player(pid) and state.get_player(pid).is_alive()
        )

        alive_count = sum(
            1 for pid in state.player_order
            if state.get_player(pid) and state.get_player(pid).is_alive()
        )

        been_attacked_by = context.get("been_attacked_by", set())
        danger_mode = context.get("danger_mode", False)

        should_activate = False

        if has_protected_captain:
            should_activate = True
        if not should_activate and target_count >= 3:
            should_activate = True
        if not should_activate and target_count == 2 and has_executable:
            should_activate = True
        if not should_activate and target_count == 1:
            if has_executable and uses >= 2:
                should_activate = True
        if not should_activate and terror_found:
            for t in nearby:
                t_talent = getattr(t, 'talent', None)
                if t_talent and getattr(t_talent, 'is_terror', False):
                    should_activate = True
                    break
        if not should_activate and alive_count == 2 and has_executable:
            should_activate = True
        if not should_activate and danger_mode:
            if len(been_attacked_by) >= 1:
                should_activate = True

        if should_activate:
            for opt in options:
                if "发动" in opt:
                    return opt

        for opt in options:
            if "不发动" in opt or "正常" in opt:
                return opt
        return options[-1]
