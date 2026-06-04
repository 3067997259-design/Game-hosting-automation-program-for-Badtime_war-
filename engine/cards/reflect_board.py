"""反光板 ×2 — Ind 限定"""
from engine.cards.base import BaseCard
from engine.ish_bosheth import INDIFFERENZA


class ReflectBoard(BaseCard):
    name = "反光板"; count = 2; voice = INDIFFERENZA
    desc = "Ind 限定。选择一名观众，其下次旋律中 decay_factor 强制=1.0。"

    def play(self, player, ish, turn_mgr):
        targets = [turn_mgr.state.get_player(p) for p in ish.participants
                    if turn_mgr.state.get_player(p) and turn_mgr.state.get_player(p).is_alive()]
        targets += [c for c in ish.chorus_list if c.is_alive()]
        if targets:
            chosen = player.controller.choose("反光板：选择目标",
                [t.name for t in targets],
                context={"phase":"T0","situation":"g2_card_reflect_board"})
            t = next((x for x in targets if x.name == chosen), targets[0])
            t._stability_force_decay = 1.0
