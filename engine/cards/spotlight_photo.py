"""聚光合影 ×2 — 通用"""
from engine.cards.base import BaseCard

class SpotlightPhoto(BaseCard):
    name = "聚光合影"; count = 2
    desc = "邀请一名观众单位移动到你的座位（需对方同意）。你的回合结束后插入其额外行动回合。"

    def play(self, player, ish, turn_mgr):
        pid = player.player_id; seat = player.location
        targets = [turn_mgr.state.get_player(p) for p in ish.participants
                    if p != pid and turn_mgr.state.get_player(p) and turn_mgr.state.get_player(p).is_alive()]
        targets += [c for c in ish.chorus_list if c.is_alive() and c.player_id != pid]
        if not targets: return
        chosen = player.controller.choose("聚光合影：邀请谁到你的座位？",
            [t.name for t in targets], context={"phase":"T0","situation":"g2_card_spotlight_photo"})
        target = next((t for t in targets if t.name == chosen), targets[0])
        if not self._consent(player, target): return
        if target.location != seat:
            target.location = seat
            for p2_id in ish.participants:
                p2 = turn_mgr.state.get_player(p2_id)
                if p2 and p2.is_alive() and p2.location == seat and p2.player_id != target.player_id:
                    turn_mgr.state.markers.set_engaged(target.player_id, p2.player_id)
            for c in ish.chorus_list:
                if c.is_alive() and c.location == seat and c.player_id != target.player_id:
                    turn_mgr.state.markers.set_engaged(target.player_id, c.player_id)
        player._photo_invitee_id = target.player_id

    @staticmethod
    def _consent(player, target) -> bool:
        if getattr(target, 'is_chorus', False): return True
        from controllers.human import HumanController
        if not isinstance(target.controller, HumanController):
            return getattr(player, 'emotion', None) == getattr(target, 'emotion', None)
        return target.controller.confirm(
            f"{player.name} 邀请你到 {player.location} 合影。接受？",
            context={"phase":"T0","situation":"g2_photo_invite"})
