"""空白票根 ×2 — 通用"""
from engine.cards.base import BaseCard
from cli import display

class BlankStub(BaseCard):
    name = "空白票根"
    count = 2
    desc = "选择：摸 1 张牌 / 清除 1 条舞台牵连 / 清除 1 层安可。"

    def play(self, player, ish, turn_mgr):
        display.show_info(f"🎴 {player.name} 使用 {self.name}")
        pid = player.player_id
        opts = ["摸 1 张牌", "清除 1 条舞台牵连", "清除 1 层安可"]
        choice = player.controller.choose("空白票根：选择效果", opts,
            context={"phase":"T0","situation":"g2_card_blank_stub"})
        if "摸" in choice:
            c = ish.deck._draw_one()
            if c: ish.deck.hands.setdefault(pid, []).append(c)
        elif "牵连" in choice:
            ent = getattr(player, 'stage_entangle', [])
            if ent: ent.pop()
        elif "安可" in choice and getattr(player, 'encore_layers', 0) > 0:
            player.encore_layers -= 1
