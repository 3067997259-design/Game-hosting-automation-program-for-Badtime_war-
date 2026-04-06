from typing import Any

class FacingMixin:
    """正面/背面空间系统 Mixin"""

    # 类型声明（运行时由 Hoshino.__init__ 初始化）
    state: Any
    player_id: str
    shield_mode: str | None
    front_players: set
    back_players: set

    def _init_facing(self, player):
        """架盾时初始化：当前地点已有玩家 → 正面，之后进入的 → 背面"""
        self.front_players = set()
        self.back_players = set()
        location = player.location
        for pid in self.state.player_order:
            p = self.state.get_player(pid)
            if p and p.is_alive() and p.player_id != player.player_id and p.location == location:
                self.front_players.add(pid)
        # 同地点警察也视作正面
        # (police integration handled separately)

    def _on_player_enter_location(self, player_id, location):
        """架盾期间有人进入同地点 → 归入背面"""
        me = self.state.get_player(self.player_id)
        if self.shield_mode == "架盾" and me and me.location == location:
            if player_id != self.player_id:
                self.back_players.add(player_id)
                self.front_players.discard(player_id)

    def _on_find_target(self, target_id):
        """find 成功 → 额外归入正面"""
        self.front_players.add(target_id)
        self.back_players.discard(target_id)

    def _on_disengage(self, target_id):
        """断开 engage_with → 归入背面"""
        self.front_players.discard(target_id)
        self.back_players.add(target_id)

    def _flip_facing(self):
        """转向：正面↔背面互换"""
        self.front_players, self.back_players = self.back_players, self.front_players

    def _clear_facing(self):
        """架盾/持盾结束时清空"""
        self.front_players.clear()
        self.back_players.clear()

    def is_front(self, pid):
        return pid in self.front_players

    def is_back(self, pid):
        return pid in self.back_players