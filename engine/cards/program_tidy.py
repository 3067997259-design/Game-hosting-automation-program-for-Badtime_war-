"""场刊整理 ×2 — 通用（Ind倾向）"""
from engine.cards.base import BaseCard
from cli import display

class ProgramTidy(BaseCard):
    name = "场刊整理"
    count = 2
    desc = "Ind 倾向。选一名观众（含 Chorus），双方各摸 1 张。若声部不同，可令其中一人弃 1 张。"

    def play(self, player, ish, turn_mgr):
        display.show_info(f"🎴 {player.name} 使用 {self.name}")
        pid = player.player_id
        targets = [turn_mgr.state.get_player(p) for p in ish.participants
                    if p != pid and turn_mgr.state.get_player(p) and turn_mgr.state.get_player(p).is_alive()]
        targets += [c for c in ish.chorus_list if c.is_alive()]
        if targets:
            chosen = player.controller.choose("场刊整理：选择一名观众", [t.name for t in targets],
                context={"phase":"T0","situation":"g2_card_program_tidy"})
            target = next((t for t in targets if t.name == chosen), targets[0])
            for person in [player, target]:
                c = ish.deck._draw_one()
                if c:
                    if getattr(person, 'is_chorus', False):
                        if not ish.deck.chorus_slots.get(person.player_id):
                            ish.deck.chorus_slots[person.player_id] = c
                    else:
                        ish.deck.hands.setdefault(person.player_id, []).append(c)
            if not getattr(target, 'is_chorus', False) and getattr(player, 'emotion', None) != getattr(target, 'emotion', None):
                victim = player.controller.choose("场刊整理：令谁弃 1 张？", [player.name, target.name],
                    context={"phase":"T0","situation":"g2_card_program_tidy_discard"})
                vic = player if victim == player.name else target
                vhand = ish.deck.hands.get(vic.player_id, [])
                if vhand:
                    dc = player.controller.choose(f"选择 {vic.name} 弃置的牌", vhand,
                        context={"phase":"T0","situation":"g2_card_program_tidy_pick"})
                    ish.deck.discard_from_hand(vic.player_id, dc)
