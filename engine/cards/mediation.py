"""调停 ×2 — Ind限定"""
from engine.cards.base import BaseCard
from engine.ish_bosheth import INDIFFERENZA, ACCAREZZEVOLE, STRAPPANDO
from cli import display

class Mediation(BaseCard):
    name = "调停"
    count = 2
    voice = INDIFFERENZA
    desc = "Ind 限定。选 1 Acc + 1 Str，至下个 R4 不能互相 attack。若任一方是 Chorus，摸 1 张牌。"

    def play(self, player, ish, turn_mgr):
        display.show_info(f"🎴 {player.name} 使用 {self.name}")
        pid = player.player_id
        acc_units = [turn_mgr.state.get_player(p) for p in ish.participants
                     if turn_mgr.state.get_player(p) and turn_mgr.state.get_player(p).is_alive()
                     and getattr(turn_mgr.state.get_player(p), 'emotion', None) == ACCAREZZEVOLE]
        str_units = [turn_mgr.state.get_player(p) for p in ish.participants
                     if turn_mgr.state.get_player(p) and turn_mgr.state.get_player(p).is_alive()
                     and getattr(turn_mgr.state.get_player(p), 'emotion', None) == STRAPPANDO]
        if acc_units and str_units:
            acc_chosen = player.controller.choose("调停：选择 Acc 单位", [t.name for t in acc_units],
                context={"phase":"T0","situation":"g2_card_mediation_acc"})
            str_chosen = player.controller.choose("调停：选择 Str 单位", [t.name for t in str_units],
                context={"phase":"T0","situation":"g2_card_mediation_str"})
            acc_target = next((t for t in acc_units if t.name == acc_chosen), acc_units[0])
            str_target = next((t for t in str_units if t.name == str_chosen), str_units[0])
            acc_target._card_no_attack_until_r4 = str_target.player_id
            str_target._card_no_attack_until_r4 = acc_target.player_id
            if getattr(acc_target, 'is_chorus', False) or getattr(str_target, 'is_chorus', False):
                c = ish.deck._draw_one()
                if c: ish.deck.hands.setdefault(pid, []).append(c)
