"""追寻那道光 —— Soave(1费) + Sognando(2费)"""
from __future__ import annotations
from typing import TYPE_CHECKING, Any
from engine.prompt_manager import prompt_manager
from engine.ish_bosheth import ACCAREZZEVOLE, INDIFFERENZA, STRAPPANDO
from talents.g2_songs.base import BaseSong

if TYPE_CHECKING:
    from engine.ish_bosheth import IshBosheth


class Soave(BaseSong):
    name = "追寻那道光"
    rhythm = "温柔 (Soave)"
    cost = 1
    desc = "选择演员：聚光灯+摸牌"
    _rhythm_key = "Soave"

    def execute(self, g2_player, target, ish, game_state):
        target.stage_statuses = getattr(target, 'stage_statuses', set())
        target.stage_statuses.add("spotlight")
        target._spotlight_granted_r4 = ish.r4_count
        is_real = not getattr(target, 'is_chorus', False)

        if ish.deck:
            if is_real:
                c = ish.deck._draw_one()
                if c:
                    hand = ish.deck.hands.setdefault(target.player_id, [])
                    if len(hand) < 3:
                        hand.append(c)
            else:
                ish.deck.chorus_draw(target.player_id)
        if is_real:
            target._card_extra_play = True

        voice = getattr(target, 'emotion', None)
        if voice == ACCAREZZEVOLE:
            target.temp_atk_g2 = getattr(target, 'temp_atk_g2', 0) + 0.5
        elif voice == INDIFFERENZA:
            if is_real and ish.deck:
                ish.deck.traded_this_round.discard(target.player_id)
        prompt_manager.show("g2reset", "song.soave", target_name=target.name)

        g2_player._g2_spotlight_target_id = target.player_id
        return f"🎵 {self.name}·{self.rhythm} → {target.name}"


class Sognando(BaseSong):
    name = "追寻那道光"
    rhythm = "追寻 (Sognando)"
    cost = 2
    desc = "选择演员：聚光灯+摸2+Chorus指挥"
    _rhythm_key = "Sognando"

    @staticmethod
    def is_available(ish):
        return ish.regard >= 2

    def execute(self, g2_player, target, ish, game_state):
        target.stage_statuses = getattr(target, 'stage_statuses', set())
        target.stage_statuses.add("spotlight")
        target._spotlight_granted_r4 = ish.r4_count
        is_real = not getattr(target, 'is_chorus', False)

        if ish.deck:
            if is_real:
                for _ in range(2):
                    c = ish.deck._draw_one()
                    if c:
                        ish.deck.hands.setdefault(target.player_id, []).append(c)
                hand = ish.deck.hands.get(target.player_id, [])
                while len(hand) > 3:
                    hand.pop()
            else:
                ish.deck.chorus_draw(target.player_id)
        if is_real:
            target._card_extra_play = True

        if not is_real:
            legal = [t for t in self.get_legal_targets(ish, game_state)
                     if getattr(t, 'player_id', None) != target.player_id]
            if legal:
                chosen_name = g2_player.controller.choose(
                    "指定 Chorus 攻击目标：", [t.name for t in legal],
                    context={"situation": "g2_command_chorus"})
                chosen = next((t for t in legal if t.name == chosen_name), None)
                if chosen:
                    target._g2_commanded_target_id = chosen.player_id

        g2_player._g2_spotlight_target_id = target.player_id
        prompt_manager.show("g2reset", "song.sognando", target_name=target.name)
        return f"🎵 {self.name}·{self.rhythm} → {target.name}"
