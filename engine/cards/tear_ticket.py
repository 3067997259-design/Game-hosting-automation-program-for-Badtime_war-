"""撕票 ×1 — Str限定"""
from engine.cards.base import BaseCard
from engine.ish_bosheth import STRAPPANDO

class TearTicket(BaseCard):
    name = "撕票"
    count = 1
    voice = STRAPPANDO
    desc = "Str 限定。Regard -0.5。若本回合击杀 Acc 单位，额外 Regard -0.5。"
    def play(self, player, ish, turn_mgr):
        ish.adjust_regard(-0.5)
        player._card_tear_ticket_active = True
