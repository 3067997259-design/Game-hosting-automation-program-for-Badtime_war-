"""24K钛合金狗牌 ×2 — Acc限定"""
from engine.cards.base import BaseCard
from engine.ish_bosheth import ACCAREZZEVOLE

class DogTag(BaseCard):
    name = "24K钛合金狗牌"; count = 2; voice = ACCAREZZEVOLE
    desc = "Acc 限定。本行动轮次内你的所有攻击无视属性克制。"
    def play(self, player, ish, turn_mgr):
        player._dog_tag_active = True
