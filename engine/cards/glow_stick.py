"""荧光棒 ×2 — 通用（Acc倾向）"""
from engine.cards.base import BaseCard
from engine.ish_bosheth import STRAPPANDO

class GlowStick(BaseCard):
    name = "荧光棒"
    count = 2
    desc = "本回合下一次 attack 伤害 +0.5。若目标是 Strappando，改为 +1.0。"

    def play(self, player, ish, turn_mgr):
        player._card_damage_bonus = 0.5
        player._card_damage_bonus_voice_filter = STRAPPANDO
