"""拼接遗憾 —— Placido(1费) + Zeffiroso(2费)"""
from __future__ import annotations
import random
from typing import TYPE_CHECKING, Any
from engine.prompt_manager import prompt_manager
from engine.ish_bosheth import ACCAREZZEVOLE, INDIFFERENZA, STRAPPANDO
from talents.g2_songs.base import BaseSong

if TYPE_CHECKING:
    from engine.ish_bosheth import IshBosheth


class Placido(BaseSong):
    name = "拼接遗憾"
    rhythm = "平静 (Placido)"
    cost = 1
    desc = "修补物料与观众"
    _rhythm_key = "Placido"

    def execute(self, g2_player, target, ish, game_state):
        target.temp_hp_g2 = getattr(target, 'temp_hp_g2', 0) + 0.5
        is_chorus = getattr(target, 'is_chorus', False)
        if is_chorus:
            target.temp_hp_g2 += 0.5

        if ish.deck:
            if is_chorus:
                card = ish.deck.chorus_slots.get(target.player_id)
                if card:
                    ish.deck.chorus_slots[target.player_id] = None
                    ish.deck.draw_pile.insert(0, card)
                    ish.deck.chorus_draw(target.player_id)
            else:
                hand = ish.deck.hands.get(target.player_id, [])
                if hand:
                    card = hand.pop(0)
                    ish.deck.draw_pile.insert(0, card)
                    c = ish.deck._draw_one()
                    if c:
                        hand.append(c)

        # v0.7 安定値交互：目标下次旋律 armor_mod ×0.5
        target._stability_armor_mult = getattr(target, '_stability_armor_mult', 1.0) * 0.5
        prompt_manager.show("g2reset", "song.placido", target_name=target.name)
        return f"🎵 {self.name}·{self.rhythm} → {target.name}"


class Zeffiroso(BaseSong):
    name = "拼接遗憾"
    rhythm = "遗憾 (Zeffiroso)"
    cost = 2
    desc = "修补物料与观众"
    needs_target = False
    _rhythm_key = "Zeffiroso"

    @staticmethod
    def is_available(ish):
        return ish.regard >= 2

    def execute(self, g2_player, target, ish, game_state):
        raise RuntimeError("Zeffiroso must use execute_two(), not execute()")

    def execute_two(self, g2_player, t1, t2, ish, game_state) -> str:
        # 交换牌
        if ish.deck:
            self._swap_cards(ish.deck, t1, t2)
        if getattr(t1, 'is_chorus', False) or getattr(t2, 'is_chorus', False):
            ish.adjust_regard(+0.5)

        # 复活死 Chorus
        dead = [c for c in ish.chorus_list if not c.is_alive()]
        if dead:
            c = dead[0]
            c.hp = 1.0
            c.emotion = self._minority_voice(ish, game_state)
            c.location = random.choice(sorted(ish.SEATS))
            prompt_manager.show("g2reset", "song.zeffiroso_revive",
                               chorus_name=c.name, voice=c.emotion)

        # v0.7 安定値交互：选中目标下次旋律 安定値 ×2
        is_c1 = getattr(t1, 'is_chorus', False)
        is_c2 = getattr(t2, 'is_chorus', False)
        if not is_c1:
            t1._stability_armor_mult = getattr(t1, '_stability_armor_mult', 1.0) * 2.0
        if not is_c2:
            t2._stability_armor_mult = getattr(t2, '_stability_armor_mult', 1.0) * 2.0

        prompt_manager.show("g2reset", "song.zeffiroso",
                           t1_name=t1.name, t2_name=t2.name)
        return f"🎵 {self.name}·{self.rhythm} → {t1.name} ↔ {t2.name}"

    @staticmethod
    def _swap_cards(deck, u1, u2):
        is_c1 = getattr(u1, 'is_chorus', False)
        is_c2 = getattr(u2, 'is_chorus', False)
        card1 = deck.chorus_slots.get(u1.player_id) if is_c1 else (
            deck.hands.get(u1.player_id, [None])[0] if deck.hands.get(u1.player_id) else None)
        card2 = deck.chorus_slots.get(u2.player_id) if is_c2 else (
            deck.hands.get(u2.player_id, [None])[0] if deck.hands.get(u2.player_id) else None)
        if card1 and card2:
            if is_c1:
                deck.chorus_slots[u1.player_id] = card2
            else:
                hand = deck.hands[u1.player_id]
                if card1 in hand:
                    hand[hand.index(card1)] = card2
            if is_c2:
                deck.chorus_slots[u2.player_id] = card1
            else:
                hand = deck.hands[u2.player_id]
                if card2 in hand:
                    hand[hand.index(card2)] = card1

    @staticmethod
    def _minority_voice(ish, game_state):
        counts = {ACCAREZZEVOLE: 0, INDIFFERENZA: 0, STRAPPANDO: 0}
        for pid in ish.participants:
            p = game_state.get_player(pid)
            if p and p.is_alive():
                v = getattr(p, 'emotion', None)
                if v in counts:
                    counts[v] += 1
        for c in ish.chorus_list:
            if c.is_alive() and c.emotion in counts:
                counts[c.emotion] += 1
        return min(counts, key=counts.get)
