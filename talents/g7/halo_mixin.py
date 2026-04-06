from typing import Any
class HaloMixin:
    """光环系统 Mixin"""

    # 类型声明（运行时由 Hoshino.__init__ 初始化）
    state: Any
    player_id: str
    halos: list

    def _halo_init(self):
        """游戏开始时初始化光环"""
        self.halos = [
            {"active": True, "cooldown_remaining": 0, "recovering": False}
            for _ in range(3)
        ]

    def _halo_tick(self):
        """R0调用：推进恢复中光环的冷却"""
        alive_count = len([pid for pid in self.state.player_order
                          if self.state.get_player(pid) and self.state.get_player(pid).is_alive()])
        cooldown_time = max(12 - alive_count * 2, 3)

        for halo in self.halos:
            if halo['recovering']:
                halo['cooldown_remaining'] -= 1
                if halo['cooldown_remaining'] <= 0:
                    halo['active'] = True
                    halo['recovering'] = False
                    halo['cooldown_remaining'] = 0
                    # 开始恢复下一个黯淡的光环
                    self._start_next_recovery(cooldown_time)
                    break  # 一次只恢复一个

    def _start_next_recovery(self, cooldown_time):
        """找到下一个黯淡但未恢复的光环，开始恢复"""
        for halo in self.halos:
            if not halo['active'] and not halo['recovering']:
                halo['recovering'] = True
                halo['cooldown_remaining'] = cooldown_time
                break

    def _halo_consume_one(self):
        """消耗1层活跃光环 → 进入黯淡 → 如果没有正在恢复的，开始恢复"""
        alive_count = len([pid for pid in self.state.player_order
                          if self.state.get_player(pid) and self.state.get_player(pid).is_alive()])
        cooldown_time = max(12 - alive_count * 2, 3)

        for halo in self.halos:
            if halo['active']:
                halo['active'] = False
                halo['cooldown_remaining'] = 0
                halo['recovering'] = False
                # 如果没有正在恢复的光环，这个开始恢复
                any_recovering = any(h['recovering'] for h in self.halos)
                if not any_recovering:
                    halo['recovering'] = True
                    halo['cooldown_remaining'] = cooldown_time
                break

    def _halo_restore_one(self):
        """直接恢复1层光环（临战-shielder起床加成 / 海豚巧克力）"""
        for halo in self.halos:
            if not halo['active']:
                halo['active'] = True
                halo['recovering'] = False
                halo['cooldown_remaining'] = 0
                return True
        return False