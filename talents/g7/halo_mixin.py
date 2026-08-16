from typing import Any
class HaloMixin:
    """光环系统 Mixin"""

    # 类型声明（运行时由 Hoshino.__init__ 初始化）
    state: Any
    player_id: str
    halos: list

    def _halo_full_value(self):
        """m7：每层光环可再生护体池满值（§7.4=3）；v1 无池概念返回 0。"""
        from talents.talent_balance import talent_num
        return talent_num("g7", "halo_value", v1=0.0)

    def _halo_activate(self, halo):
        """点亮一层光环（统一入口）：m7 下同时充满护体池。"""
        halo['active'] = True
        halo['recovering'] = False
        halo['cooldown_remaining'] = 0
        halo['value'] = self._halo_full_value()

    def _halo_dim(self, halo):
        """熄灭指定光环并按需启动恢复链（供 m7 护体池耗尽时用）。"""
        halo['active'] = False
        halo['value'] = 0.0
        halo['cooldown_remaining'] = 0
        halo['recovering'] = False
        if not any(h['recovering'] for h in self.halos):
            cd = self._halo_cooldown_time()
            halo['recovering'] = True
            halo['cooldown_remaining'] = cd

    def _halo_drain(self, remaining):
        """用活跃光环吸收伤害，返回剩余。m7 按护体池扣（每层 3 点），v1 每层 0.5。"""
        from talents.talent_balance import m7_enabled
        if m7_enabled():
            for halo in self.halos:
                if remaining <= 0:
                    break
                if halo['active'] and halo.get('value', 0) > 0:
                    absorb = min(remaining, halo['value'])
                    halo['value'] -= absorb
                    remaining -= absorb
                    if halo['value'] <= 0:
                        self._halo_dim(halo)
            return remaining
        # v1：每层吸收 0.5
        while remaining > 0 and any(h['active'] for h in self.halos):
            absorb = min(remaining, 0.5)
            remaining -= absorb
            self._halo_consume_one()
        return remaining

    def _halo_cooldown_time(self, alive_count=None):
        """计算光环冷却时间（加速版）"""
        if alive_count is None:
            alive_count = len([pid for pid in self.state.player_order
                            if self.state.get_player(pid) and self.state.get_player(pid).is_alive()])
        # 原公式: max(12 - alive_count * 2, 3)
        # 加速版: max(10 - alive_count * 2, 2)
        return max(10 - alive_count * 2, 2)

    def _check_first_all_lit(self):
        """首次全亮检测 → 授予战斗续行免死（统一入口）"""
        if not getattr(self, '_all_halos_first_lit', False):
            if all(h['active'] for h in self.halos):
                self._all_halos_first_lit = True
                self._combat_continuation_immunity = True
                from cli import display
                from engine.prompt_manager import prompt_manager
                display.show_info(prompt_manager.get_prompt("talent", "g7hoshino.combat_continuation_ready",
                    default="✨ 三层光环全部点亮！「战斗续行」：获得一次免死机会"))

    def _halo_tick(self):
        """R0调用：推进恢复中光环的冷却"""
        alive_count = len([pid for pid in self.state.player_order
                        if self.state.get_player(pid) and self.state.get_player(pid).is_alive()])
        cooldown_time = self._halo_cooldown_time(alive_count)

        for halo in self.halos:
            if halo['recovering']:
                halo['cooldown_remaining'] -= 1
                if halo['cooldown_remaining'] <= 0:
                    self._halo_activate(halo)
                    self._start_next_recovery(cooldown_time)
                    break

        self._check_first_all_lit()

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
        """直接恢复1层光环（临战-shielder起床 / 海豚巧克力），并维护恢复链"""
        for halo in self.halos:
            if not halo['active']:
                self._halo_activate(halo)
                # 维护恢复链：如果没有其他光环在恢复中，启动下一个
                if not any(h['recovering'] for h in self.halos):
                    cooldown_time = self._halo_cooldown_time()
                    self._start_next_recovery(cooldown_time)
                # 首次全亮检测
                self._check_first_all_lit()
                return True
        return False