"""和弦谱 ×2 — Acc 限定"""
from engine.cards.base import BaseCard
from engine.ish_bosheth import ACCAREZZEVOLE


class ChordSheet(BaseCard):
    name = "和弦谱"; count = 2; voice = ACCAREZZEVOLE
    desc = "Acc 限定。累计 ΔRegard +1.5。"

    def play(self, player, ish, turn_mgr):
        ish.cumulative_delta_regard += 1.5
