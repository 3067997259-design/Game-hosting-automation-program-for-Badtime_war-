"""M9 T7 死者苏生天赋（profile: m9-rfc，T3/T7 迁移合同 v0.3 §2）。

- 删除魔法所学习前置；即演（1 SP）/公演（2 SP）直接挂载全局唯一「保险伏笔」；
- 保险挂载在系统级 `game_state.m9_insurance`（engine.m9.insurance）：全局唯一、
  不可覆盖、不可重挂；兑现后 T7 永久落幕；
- 保险在 T7 死亡后继续存在；普通死亡可兑现，absolute_death 跳过（由
  DeathAdjudicator 在免死/保险链之前分流）；
- 兑现：家中复活（home 被毁 → 结算时当前位置 → 最后安全地点兜底）、
  revive_hp、保留全部物品、恢复可再生的破碎护盾、清理死亡关系；
  击杀者不得击杀计数（管线 prevented 不置 killed）、不成立犯罪、
  PP/评分视为未死亡；复活后 SP=0；
- G5「彼岸」诗强化：far_shore_watch 标记存在时复活后 SP 置 2 + 复活前可选
  携带一件装备；标记用后消耗；
- 死亡事件、击杀、marker 与装备恢复只结算一次（cash_in 幂等）。

数值一律读 `m9_talents_extended.t7.*`（[待风洞]）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from engine.balance import get as bget
from engine.m9.text import m9_text
from talents.t7_resurrection import Resurrection


def _t7(key: str, default):
    return bget("m9_talents_extended", "t7", key, default=default)


def revive_hp() -> float:
    return float(_t7("revive_hp", 12))


class Resurrection9(Resurrection):
    """M9 T7（m9-rfc 实例化；与 v2exp 类同名 name 保字符串引用兼容）。"""

    name = "死者苏生"

    def __init__(self, player_id: str, game_state: Any) -> None:
        super().__init__(player_id, game_state)
        from engine.m9.gate import m9_enabled
        if m9_enabled(game_state):
            # M9：学习进度、学会状态和 legacy 挂载/次数状态不存在于真实实例。
            for attr in ("learned", "learn_progress", "mounted_on", "used"):
                if hasattr(self, attr):
                    delattr(self, attr)

    # ════════════════════════════════════════════════════════
    #  T0 入口：即演/公演直接挂载
    # ════════════════════════════════════════════════════════

    def get_t0_option(self, player: Any) -> Optional[dict]:
        """仅当存在可挂载目标、保险未挂载且 T7 未落幕时出现。"""
        from engine.m9.gate import m9_enabled
        if not m9_enabled(self.state):
            return super().get_t0_option(player)
        m9 = getattr(self.state, "m9_system", None)
        reg = getattr(self.state, "m9_insurance", None)
        if m9 is None or reg is None:
            return None
        if reg.is_retired() or reg.is_mounted():
            return None
        if not self._mount_targets():
            return None
        sp = m9.get_sp(self.player_id)
        if sp < 1:
            return None
        return {
            "name": m9_text("talents.t7.t0.name"),
            "description": m9_text("talents.t7.t0.description"),
            "m9_kind": "t7_mount",
        }

    def execute_t0(self, player: Any):
        """预检先于 SP 消费：可挂载目标存在 + 保险未挂载 + 未落幕。"""
        from engine.m9.gate import m9_enabled
        if not m9_enabled(self.state):
            return super().execute_t0(player)
        m9 = getattr(self.state, "m9_system", None)
        reg = getattr(self.state, "m9_insurance", None)
        if m9 is None or reg is None:
            return m9_text("talents.t7.err_m9_not_mounted"), False
        if reg.is_retired():
            return m9_text("talents.t7.err_retired"), False
        if reg.is_mounted():
            return m9_text("talents.t7.err_already_mounted"), False
        targets = self._mount_targets()
        if not targets:
            return m9_text("talents.t7.err_no_target"), False
        if m9.get_sp(self.player_id) < 1:
            return m9_text("talents.t7.err_sp_insufficient_cancel"), False
        round_num = getattr(self.state, "current_round", 1)
        # 选择即演或公演（SP 允许的范围）。决策统一交给 controller
        # （BasicAI 走 t0_policy：未登台且持公演位 → 公演；否则即演）。
        public_ready = (m9.get_sp(self.player_id) >= 2
                        and m9.assign_public_slot(round_num) == self.player_id)
        ctrl = getattr(player, "controller", None)
        options = [m9_text("talents.t7.option_public"),
                   m9_text("talents.t7.option_improvise")] if public_ready \
            else [m9_text("talents.t7.option_improvise")]
        try:
            want = ctrl.choose(m9_text("talents.t7.choose_mode_prompt"), options)
        except Exception:
            want = options[0]
        if "公演" in want and m9.get_sp(self.player_id) >= 2:
            grant = self._dispatch_public(player, m9, round_num)
            mode = "公演"
        else:
            grant = m9.dispatch_improvise(self.player_id, round_num,
                                          source_id="t7_mount")
            mode = "即演"
        if grant is None:
            return m9_text("talents.t7.err_sp_or_public_seat"), False
        # 选目标（含自己）
        names = [self.state.get_player(pid).name for pid in targets]
        try:
            choice = player.controller.choose(
                m9_text("talents.t7.choose_target_prompt"), names,
                context={"phase": "T0", "situation": "resurrection_pick_target"})
        except Exception:
            choice = names[0]
        target_pid = next((pid for pid in targets
                           if self.state.get_player(pid).name == choice),
                          targets[0])
        if not reg.mount(self.player_id, target_pid):
            return m9_text("talents.t7.err_mount_failed"), False
        # 双向关注：目标作为焦点先登记，T7 作为发起者后登记（每人每轮 +1 上限）
        if m9.can_attend(round_num, target_pid):
            m9.mark_attention(round_num, target_pid)
        if m9.can_attend(round_num, self.player_id):
            m9.mark_attention(round_num, self.player_id)
        self.state.log_event("resurrection_mount", player=self.player_id,
                             target=target_pid, mode=mode)
        target_name = self.state.get_player(target_pid).name
        return m9_text("talents.t7.mount_success", player=player.name,
                       target=target_name, mode=mode), True

    def _mount_targets(self) -> List[str]:
        """可挂载目标：全部存活玩家（含自己）。"""
        return [pid for pid in self.state.player_order
                if self.state.get_player(pid) is not None
                and self.state.get_player(pid).is_alive()]

    @staticmethod
    def _dispatch_public(player: Any, m9: Any, round_num: int):
        if m9.assign_public_slot(round_num) != player.player_id:
            return None
        return m9.dispatch_public(player.player_id, round_num,
                                  source_id="t7_mount")

    # ════════════════════════════════════════════════════════
    #  死亡兑现（普通死亡；absolute_death 由 DeathAdjudicator 前置分流）
    # ════════════════════════════════════════════════════════

    def on_death_check(self, dying_player: Any, damage_source: Any):
        """保险兑现：全局唯一、只结算一次；兑现后 T7 永久落幕。"""
        from engine.m9.gate import m9_enabled
        if not m9_enabled(self.state):
            return super().on_death_check(dying_player, damage_source)
        reg = getattr(self.state, "m9_insurance", None)
        if reg is None:
            return None
        record = reg.record()
        if record is None or record.cashed_in:
            return None
        if getattr(dying_player, "player_id", "") != record.target_pid:
            return None
        cashed = reg.cash_in()
        if cashed is None:
            return None
        hp = revive_hp()
        dying_player.hp = hp
        dying_player.is_awake = True
        dying_player.location = self._revive_location(dying_player)
        self._restore_regen_shields(dying_player)
        # 清理死亡关系（击杀者无击杀、不成立犯罪由管线 prevented 保证）
        try:
            self.state.markers.on_player_death(dying_player.player_id)
        except Exception:
            pass
        # 复活后 SP=0（普通）；彼岸诗 → SP=2 + 可选携带装备
        m9 = getattr(self.state, "m9_system", None)
        far_shore = self._consume_far_shore_watch(dying_player)
        if m9 is not None:
            m9.set_sp(dying_player.player_id, 2 if far_shore else 0)
        self.state.log_event("resurrection_trigger",
                             player=self.player_id,
                             target=dying_player.player_id,
                             far_shore=far_shore)
        return {"prevent_death": True, "new_hp": hp}

    def _revive_location(self, dying_player: Any) -> str:
        """复活位置：home → home 被毁时结算时当前位置 → 最后安全地点兜底。"""
        pid = dying_player.player_id
        home = f"home_{pid}"
        destroyed = getattr(self.state, "m9_destroyed_locations", set())
        if home not in destroyed:
            return home
        current = getattr(dying_player, "location", None)
        if current and current not in destroyed:
            return current
        from actions.move import ALL_LOCATIONS
        for loc in ALL_LOCATIONS:
            if loc not in destroyed and loc != "home":
                return loc
        return home

    @staticmethod
    def _restore_regen_shields(player: Any) -> List[str]:
        """恢复已破碎且可再生的护盾（魔法护盾/AT力场）；不恢复一次性护甲。"""
        restored = []
        armor = getattr(player, "armor", None)
        if armor is None:
            return restored
        for layer in ("outer", "inner"):
            for piece in getattr(armor, layer, []) or []:
                if piece is None:
                    continue
                if getattr(piece, "is_broken", False) \
                        and getattr(piece, "can_regen", False):
                    piece.is_broken = False
                    piece.current_hp = getattr(piece, "max_hp", 0)
                    restored.append(getattr(piece, "name", "?"))
        return restored

    def _consume_far_shore_watch(self, dying_player: Any) -> bool:
        """彼岸诗：标记存在 → SP2 + 复活前可选携带一件装备；标记用后消耗。"""
        talent = getattr(dying_player, "talent", None)
        markers = getattr(talent, "m9_poem_markers", None) if talent else None
        if not markers or not markers.get("far_shore_watch"):
            return False
        markers["far_shore_watch"] = False
        ctrl = getattr(dying_player, "controller", None)
        try:
            if ctrl is not None:
                from models.equipment import make_armor, make_weapon
                weapon_names = ("小刀", "警棍", "魔法弹幕", "高斯步枪")
                armor_names = ("盾牌", "陶瓷护甲", "魔法护盾", "AT力场")
                options = list(weapon_names + armor_names)
                choice = ctrl.choose(m9_text("talents.t7.far_shore_choose_prompt"),
                                     options)
                if choice in weapon_names:
                    weapon = make_weapon(choice)
                    if weapon is not None:
                        dying_player.weapons.append(weapon)
                elif choice in armor_names:
                    armor = make_armor(choice)
                    if armor is not None:
                        dying_player.add_armor(armor)
                dying_player._m9_far_shore_equipment = choice
        except (AttributeError, TypeError, ValueError):
            pass
        return True

    def describe_status(self) -> str:
        reg = getattr(self.state, "m9_insurance", None)
        if reg is None:
            return m9_text("talents.t7.status_no_registry")
        if reg.is_retired():
            return m9_text("talents.t7.status_retired")
        if reg.is_mounted():
            target = reg.mounted_target()
            name = target
            p = self.state.get_player(target) if target else None
            if p is not None:
                name = p.name
            return m9_text("talents.t7.status_mounted", name=name)
        return m9_text("talents.t7.status_unmounted")
