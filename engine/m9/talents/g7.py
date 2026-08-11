"""M9 G7 战术压制天赋（profile: m9-rfc，G7 合同 v0.3）。

继承 v2exp Hoshino（复用融合/光环/战术宏/正面背面/Terror mixin 的完整机制），
覆写 M9 合同差异点：
- 起床：临战-Archer 不再获得额外行动回合 → 同槽受限追演（wake_followup）；
- 连续射击计数：只在「非射击攻击」或「换形态」时重置（结束盾牌不再重置）；
- Terror 攻击：DIRECT_DAMAGE + absolute_death 身份（T7 不赔付），同一批次
  A=terror_attack_damage，全灭免扣 cost；
- R0 回满：即演豁免（失却汇流成泉 −1 撤销）与 adrenaline_cap 键（后置修正）；
- 数值一律读 `m9_talents_extended.g7.*`（[待风洞]）。
"""
from __future__ import annotations

from typing import Any, Optional

from engine.balance import get as bget
from talents.g7.hoshino import Hoshino


def _g7(key: str, default):
    return bget("m9_talents_extended", "g7", key, default=default)


class Hoshino9(Hoshino):
    """M9 G7（m9-rfc 实例化；与 v2exp 类同名 name 保字符串引用兼容）。"""

    name = "大叔我啊，剪短发了"

    def __init__(self, player_id: str, game_state: Any) -> None:
        super().__init__(player_id, game_state)
        self.wake_followup_available = False
        self._m9_improvise_exempt_next_r0 = False

    # ════════════════════════════════════════════════════════
    #  起床：同槽受限追演（合同 §五 / talent_action §八）
    # ════════════════════════════════════════════════════════

    def on_wakeup(self, player, game_state):
        """M9：临战-Archer 不设额外行动回合（合同删除项），改为受限追演标记；
        水着获盾 / 临战-Shielder 恢复光环照旧。"""
        if player.vouchers < 1:
            player.vouchers = max(player.vouchers, 1)
        if self.form == "水着-shielder":
            from models.equipment import make_armor
            armor = make_armor("盾牌")
            if armor:
                player.add_armor(armor)
        elif self.form == "临战-Archer":
            self.wake_followup_available = True
        elif self.form == "临战-shielder":
            self._halo_restore_one()
        return None

    def m9_wake_followup(self, player: Any, turn_mgr: Any) -> Optional[str]:
        """同槽受限追演：move / interact / find / lock / 结束。

        不是 ActionGrant、不重进 T0；执行的是本槽唯一 root 行动；
        纯 wake + 结束 → root_action_performed=False（round_manager 槽收尾读取
        `_m9_last_slot_wake_followup` 标记写 resolution_kind=wake_followup）。
        """
        if not self.wake_followup_available:
            return None
        self.wake_followup_available = False
        menu = ["结束", "move", "interact", "find", "lock"]
        ctrl = getattr(player, "controller", None)
        try:
            choice = ctrl.choose("起床受限追演：", menu)
        except Exception:
            choice = "结束"
        if choice == "结束" or choice not in menu:
            player._m9_last_slot_wake_followup = False
            return "wake"
        from engine.m9.executor import execute_category
        msg, ok = execute_category(player, turn_mgr.state, choice)
        if ok:
            player._m9_last_slot_wake_followup = True
            return choice
        player._m9_last_slot_wake_followup = False
        return "wake"

    # ════════════════════════════════════════════════════════
    #  连续射击计数（合同 §2.4b：仅非射击攻击/换形态重置）
    # ════════════════════════════════════════════════════════

    def _end_shield_mode(self, player):
        """M9：结束盾牌不再重置连续射击计数（v2exp 在 1013 行重置）。"""
        self.shield_mode = None
        self.shield_snapshot_hp = 0
        self._clear_facing()

    def m9_reset_shoot_streak(self) -> None:
        """非射击攻击或换形态时重置计数（由引擎挂点调用）。"""
        self.shoot_streak = 0

    # ════════════════════════════════════════════════════════
    #  Terror 攻击：DIRECT_DAMAGE + absolute_death（合同 §2.7）
    # ════════════════════════════════════════════════════════

    def _terror_attack(self, player):
        """M9：同一批次 DIRECT_DAMAGE（A=terror_attack_damage）攻击所有玩家单位
        （除自己）；绝对死亡标签 → T7/免死不赔付（结算层分流）；全灭免扣 cost。"""
        from engine.m9.combat import resolve_damage
        from cli import display
        from engine.prompt_manager import prompt_manager

        attack_damage = int(_g7("terror_attack_damage", 4))
        header = prompt_manager.get_prompt("talent", "g7hoshino.terror_attack_header")
        lines = [header]

        for pid in self.state.player_order:
            t = self.state.get_player(pid)
            if not t or not t.is_alive() or t.player_id == player.player_id:
                continue
            r = resolve_damage(
                player, t, weapon=None, game_state=self.state,
                raw_damage_override=attack_damage,
                damage_attribute_override="__无视__",
                source_kind="g7_terror",
            )
            for detail in r.get("details", []):
                lines.append(f"  [{t.name}] {detail}")
            if r.get("killed"):
                self.state.markers.on_player_death(t.player_id)
                if self.state.police_engine:
                    self.state.police_engine.on_player_death(t.player_id)
                player.kill_count += 1
                from engine.round_manager import RoundManager
                RoundManager.notify_all_talents_of_death(
                    self.state, t.player_id, killer_id=player.player_id)

        all_others_dead = all(
            (pid == player.player_id
             or not (self.state.get_player(pid) and
                     self.state.get_player(pid).is_alive()))
            for pid in self.state.player_order)

        if not all_others_dead:
            attack_cost = int(_g7("terror_attack_cost", 6))
            self.terror_extra_hp = round(
                max(0, self.terror_extra_hp - attack_cost), 2)
            lines.append(prompt_manager.get_prompt(
                "talent", "g7hoshino.terror_extra_hp_status",
                terror_extra_hp=self.terror_extra_hp))
        else:
            lines.append("💀 全歼！星野-Terror 胜利。")

        if self.terror_extra_hp <= 0:
            lines.append(prompt_manager.get_prompt(
                "talent", "g7hoshino.terror_extra_hp_zero"))
        return "\n".join(lines)

    # ════════════════════════════════════════════════════════
    #  R0 回满（合同 §2.3：即演豁免撤销失却汇流成泉 −1）
    # ════════════════════════════════════════════════════════

    def on_round_start(self, round_num):
        """M9：super() 走 v2exp 回满+失却之痛；若上轮即演豁免 → 撤销 −1。"""
        exempt = self._m9_improvise_exempt_next_r0
        self._m9_improvise_exempt_next_r0 = False
        super().on_round_start(round_num)
        if exempt and not self.is_terror and not getattr(
                self, "_adrenaline_next_round", False):
            cap = int(_g7("cost_base_cap", 5))
            self.cost = min(self.cost + 1, cap)

    def m9_mark_improvise_exempt(self) -> None:
        """即演（1 SP）后：下个 R0 豁免失却汇流成泉（合同 §四）。"""
        self._m9_improvise_exempt_next_r0 = True
