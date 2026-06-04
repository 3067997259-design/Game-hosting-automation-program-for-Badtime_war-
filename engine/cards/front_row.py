"""前排票 ×2 — 通用"""
from engine.cards.base import BaseCard
from cli import display


class FrontRowTicket(BaseCard):
    name = "前排票"
    count = 2
    desc = "移动到任意观众座位，与该座位 1 名单位建立 engage。不能用于离场。"

    def play(self, player, ish, turn_mgr):
        display.show_info(f"🎴 {player.name} 使用 {self.name}")
        seat = player.location
        available = sorted(ish.SEATS - {seat})
        dest = player.controller.choose(
            "前排票：选择目标座位", available,
            context={"phase": "T0", "situation": "g2_card_front_row"})
        if dest in available:
            player.location = dest
            for p2_id in ish.participants:
                p2 = turn_mgr.state.get_player(p2_id)
                if p2 and p2.is_alive() and p2.location == dest and p2.player_id != player.player_id:
                    turn_mgr.state.markers.set_engaged(player.player_id, p2.player_id)
                    break
