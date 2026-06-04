"""后台通行证 ×2 — Str限定"""
from engine.cards.base import BaseCard
from engine.ish_bosheth import STRAPPANDO

class BackstagePass(BaseCard):
    name = "后台通行证"; count = 2; voice = STRAPPANDO
    desc = "Str 限定。在当前座位生成 G2 投影，立刻 engage。攻击投影视为攻击 G2，可触发破幕。"
    def play(self, player, ish, turn_mgr):
        pid = player.player_id
        ish.create_projection(player.location)
        turn_mgr.state.markers.set_engaged(pid, ish.g2_owner_id)
