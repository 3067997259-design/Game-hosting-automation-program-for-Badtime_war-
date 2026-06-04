"""耳返 ×2 — 通用"""
from cli import display
from engine.cards.base import BaseCard

class EarMonitor(BaseCard):
    name = "耳返"; count = 2
    desc = "下次旋律中你的 total_defense 在安定値计算时 -2。"

    def play(self, player, ish, turn_mgr):
        player._stability_defense_offset = getattr(player, '_stability_defense_offset', 0.0) - 2.0
        display.show_info(f"🎧 {player.name} 戴上耳返：旋律判定中防御-2")
