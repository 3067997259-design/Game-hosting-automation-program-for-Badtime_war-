from typing import Any
class HaloMixin:
    """光环系统 Mixin"""

    # 类型声明（运行时由 Hoshino.__init__ 初始化）
    state: Any
    player_id: str
    halos: list

    def _halo_init(self):
        """游戏开始时初始化光环（全部熄灭，依次恢复）"""
        alive_count = len([pid for pid in self.state.player_order
                        if self.state.get_player(pid) and self.state.get_player(pid).is_alive()])
        cooldown_time = self._halo_cooldown_time(alive_count)
        self.halos = [
            {"active": False, "cooldown_remaining": 0, "recovering": False}
            for _ in range(3)
        ]
        # 第一个光环立刻开始恢复
        self.halos[0]['recovering'] = True
        self.halos[0]['cooldown_remaining'] = cooldown_time

    def _halo_cooldown_time(self, alive_count=None):
        """计算光环冷却时间（加速版）"""
        if alive_count is None:
            alive_count = len([pid for pid in self.state.player_order
                            if self.state.get_player(pid) and self.state.get_player(pid).is_alive()])
        # 原公式: max(12 - alive_count * 2, 3)
        # 加速版: max(10 - alive_count * 2, 2)
        return max(10 - alive_count * 2, 2)

    def _halo_tick(self):
        """R0调用：推进恢复中光环的冷却"""
        alive_count = len([pid for pid in self.state.player_order
                        if self.state.get_player(pid) and self.state.get_player(pid).is_alive()])
        cooldown_time = self._halo_cooldown_time(alive_count)

        for halo in self.halos:
            if halo['recovering']:
                halo['cooldown_remaining'] -= 1
                if halo['cooldown_remaining'] <= 0:
                    halo['active'] = True
                    halo['recovering'] = False
                    halo['cooldown_remaining'] = 0
                    self._start_next_recovery(cooldown_time)
                    break

        # 首次全亮检测 → 授予战斗续行免死
        if not getattr(self, '_all_halos_first_lit', False):
            if all(h['active'] for h in self.halos):
                self._all_halos_first_lit = True
                self._combat_continuation_immunity = True
                from cli import display
                from engine.prompt_manager import prompt_manager
                display.show_info(prompt_manager.get_prompt("talent", "g7hoshino.combat_continuation_ready",
                    default="✨ 三层光环全部点亮！「战斗续行」：获得一次免死机会"))

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
        cooldown_time = self._halo_cooldown_time(alive_count)

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
        for halo in self.halos:
            if not halo['active']:
                halo['active'] = True
                halo['recovering'] = False
                halo['cooldown_remaining'] = 0
                # 首次全亮检测
                if not getattr(self, '_all_halos_first_lit', False):
                    if all(h['active'] for h in self.halos):
                        self._all_halos_first_lit = True
                        self._combat_continuation_immunity = True
                        from cli import display
                        from engine.prompt_manager import prompt_manager
                        display.show_info(prompt_manager.get_prompt("talent", "g7hoshino.combat_continuation_ready",
                            default="✨ 三层光环全部点亮！「战斗续行」：获得一次免死机会"))
                return True
        return False