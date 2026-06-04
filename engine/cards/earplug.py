"""耳塞 ×2 — 通用"""
from engine.cards.base import BaseCard
from cli import display

class Earplug(BaseCard):
    name = "耳塞"
    count = 2
    desc = "至下个 R4：下一次旋律命中或 Before light 效果无视。清除 1 条舞台牵连。"

    def play(self, player, ish, turn_mgr):
        display.show_info(f"🎴 {player.name} 使用 {self.name}")
        player._card_earplug = True
        ent = getattr(player, 'stage_entangle', [])
        if ent: ent.pop()
