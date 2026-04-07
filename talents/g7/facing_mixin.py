from typing import Any

class FacingMixin:
    """正面/背面空间系统 Mixin"""

    # 类型声明（运行时由 Hoshino.__init__ 初始化）
    state: Any
    player_id: str
    shield_mode: str | None
    shield_guard_mode: str  # "block_leaving" / "block_entering"
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
        """架盾期间有人进入同地点"""
        me = self.state.get_player(self.player_id)
        if self.shield_mode == "架盾" and me and me.location == location:
            if player_id != self.player_id:
                if self.shield_guard_mode == "block_entering":
                    # 守点模式：进入者归入正面 + 建立 engage_with + 半进入标记
                    self.front_players.add(player_id)
                    self.back_players.discard(player_id)
                    # 建立 engage_with
                    self.state.markers.add_relation(self.player_id, "ENGAGED_WITH", player_id)
                    self.state.markers.add_relation(player_id, "ENGAGED_WITH", self.player_id)
                    # 设置半进入标记
                    entering_player = self.state.get_player(player_id)
                    if entering_player:
                        entering_player._shield_half_entered = True
                        entering_player._shield_half_entered_location = location
                        entering_player._shield_half_entered_blocker = self.player_id
                else:
                    # 默认模式：进入者归入背面
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
        """转向：正面↔背面互换 + 切换守点模式"""
        self.front_players, self.back_players = self.back_players, self.front_players
        # toggle 守点模式
        if self.shield_guard_mode == "block_leaving":
            self.shield_guard_mode = "block_entering"
        else:
            self.shield_guard_mode = "block_leaving"
        # 切换模式时清除所有半进入标记
        self._clear_half_entered_marks()

    def _clear_facing(self):
        """架盾/持盾结束时清空"""
        self._clear_half_entered_marks()
        self.front_players.clear()
        self.back_players.clear()
        self.shield_guard_mode = "block_leaving"  # 重置

    def _clear_half_entered_marks(self):
        """清除所有玩家的半进入标记"""
        for pid in self.state.player_order:
            p = self.state.get_player(pid)
            if p and hasattr(p, '_shield_half_entered'):
                if getattr(p, '_shield_half_entered_blocker', None) == self.player_id:
                    del p._shield_half_entered
                    if hasattr(p, '_shield_half_entered_location'):
                        del p._shield_half_entered_location
                    if hasattr(p, '_shield_half_entered_blocker'):
                        del p._shield_half_entered_blocker

    def is_front(self, pid):
        return pid in self.front_players

    def is_back(self, pid):
        return pid in self.back_players