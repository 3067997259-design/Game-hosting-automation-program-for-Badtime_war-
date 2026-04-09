"""色彩反转 + Terror 状态 Mixin — 神代天赋7"""

import math
from cli import display
from engine.prompt_manager import prompt_manager
from typing import TYPE_CHECKING, Any


class TerrorMixin:
    """色彩反转 + Terror 状态 Mixin"""

    # 类型声明（运行时由 Hoshino.__init__ 初始化）
    state: Any
    player_id: str
    color: int
    color_is_null: bool
    is_terror: bool
    self_doubt_pending: bool
    terror_extra_hp: float
    broken_armors_history: set
    tactical_unlocked: bool
    tactical_items: list
    medicines: list
    iron_horus_hp: int
    iron_horus_max_hp: int
    fusion_shield_done: bool
    halos: list
    shield_mode: str | None
    cost: int
    if TYPE_CHECKING:
        def _clear_facing(self) -> None: ...


    def _on_any_player_death(self, victim_id, killer_id=None):
        """色彩计数：每有玩家出局 +2，自己击杀额外 +2"""
        if self.color_is_null:
            return
        if victim_id == self.player_id:
            return  # 自己死了不加
        self.color += 2
        if killer_id == self.player_id:
            self.color += 2  # 自己击杀额外 +2

    def _check_color_6_choice(self, player):
        """
        T0: 色彩≥6 → 提供选择是否进入自我怀疑。
        返回 skip reason string 或 None。
        """
        if self.color_is_null or self.is_terror or self.self_doubt_pending:
            return None
        if self.color >= 6:
            choice = player.controller.choose(
                f"是因为你在，她才会死在沙漠里的。她的死都是你的错……你还在试图相信那个可笑的自己吗？",
                ["是因为我……一切都是因为我……", "不，不是这样的……"],
                context={"phase": "T0", "situation": "hoshino_self_doubt_choice"}
            )
            if choice == "是因为我……一切都是因为我……":
                self.self_doubt_pending = True
                msg = prompt_manager.get_prompt("talent", "g7hoshino.self_doubt_next_skip",
                                              player_name=player.name)
                display.show_info(msg)
        return None

    def _process_self_doubt(self, player):
        """
        T0: 自我怀疑 → 跳过回合 → 反转为 Terror。
        返回 skip reason string 或 None。
        """
        if not self.self_doubt_pending:
            return None
        self.self_doubt_pending = False
        msg = prompt_manager.get_prompt("talent", "g7hoshino.self_doubt_skip", player_name=player.name)
        display.show_info(msg)
        # 反转为 Terror
        self._enter_terror(player)
        return "self_doubt_terror"

    def _check_color_10_on_hp_damage(self, player, damage):
        """
        色彩≥10 且本体HP受伤未死 → 不眩晕 + 恢复所有破碎护甲 → 自我怀疑。
        在 receive_damage_to_temp_hp 之后、HP扣减之后调用。
        返回 True 如果触发了色彩10效果。
        """
        if self.color_is_null or self.is_terror:
            return False
        if self.color < 10:
            return False
        if player.hp <= 0:
            return False  # 已死亡，不触发
        # 本体HP受到伤害但没有死亡
        # 不陷入眩晕
        player.is_stunned = False
        self.state.markers.remove(player.player_id, "STUNNED")
        # 恢复所有穿戴过的已破碎护甲（不允许同名重叠）
        restored_names = set()
        for armor_name in self.broken_armors_history:
            if armor_name not in restored_names:
                from models.equipment import make_armor
                armor = make_armor(armor_name)
                if armor:
                    success, _ = player.add_armor(armor)
                    if success:
                        restored_names.add(armor_name)
        if restored_names:
            msg = prompt_manager.get_prompt("talent", "g7hoshino.color_10_armor_restore",
                                        restored_names=', '.join(restored_names))
            display.show_info(msg)
        # 进入自我怀疑
        self.self_doubt_pending = True
        msg = prompt_manager.get_prompt("talent", "g7hoshino.color_10_self_doubt",
                                    player_name=player.name)
        display.show_info(msg)
        return True

    def _enter_terror(self, player):
        """进入 Terror 状态"""
        banner_top = prompt_manager.get_prompt("talent", "g7hoshino.terror_banner_top")
        banner_mid = prompt_manager.get_prompt("talent", "g7hoshino.terror_banner_mid",
                                           player_name=player.name)
        banner_bot = prompt_manager.get_prompt("talent", "g7hoshino.terror_banner_bot")
        display.show_info(f"\n{banner_top}")
        display.show_info(banner_mid)
        display.show_info(f"{banner_bot}\n")

        self.is_terror = True

        # ID 覆盖
        player.name = "星野-Terror"

        # 失去所有战术指令、战术物品和药物
        self.tactical_unlocked = False
        self.tactical_items.clear()
        self.medicines.clear()

        # 失去铁之荷鲁斯 → 每点护甲值折算1额外生命值
        original_horus_hp = self.iron_horus_hp
        horus_extra = self.iron_horus_hp * 1.0
        self.iron_horus_hp = 0

        # 失去所有光环 → 每层折算1额外生命值
        halo_extra = sum(1.0 for h in self.halos if h['active'])
        for h in self.halos:
            h['active'] = False
            h['recovering'] = False
            h['cooldown_remaining'] = 0

        # 失去所有护甲 → 每层1额外生命值
        armor_extra = 0.0
        all_armor = list(player.armor.get_all_active())
        for armor in all_armor:
            if not armor.is_broken:
                armor_extra += 1.0
            player.armor.remove_piece(armor)

        self.terror_extra_hp = horus_extra + halo_extra + armor_extra
        hp_calc = prompt_manager.get_prompt("talent", "g7hoshino.terror_hp_calc",
                                         original_horus_hp=original_horus_hp,
                                         horus_extra=horus_extra,
                                         halo_extra=halo_extra,
                                         armor_extra=armor_extra)
        display.show_info(hp_calc)
        extra_hp_msg = prompt_manager.get_prompt("talent", "g7hoshino.terror_extra_hp",
                                              terror_extra_hp=self.terror_extra_hp)
        display.show_info(extra_hp_msg)

        # 结束架盾/持盾
        self.shield_mode = None
        self._clear_facing()

        # cost 显示为 Null
        self.cost = 0

    def _terror_attack(self, player):
            """
            Terror 攻击：对全图除自己外所有单位造成1点无视克制伤害。
            走正常护甲管线，但不享受加成和减伤，单体保护不过滤。
            消耗1额外HP（伤害结算后扣除，不同归于尽）。
            """
            from combat.damage_resolver import resolve_terror_damage
            from cli import display

            header = prompt_manager.get_prompt("talent", "g7hoshino.terror_attack_header")
            lines = [header]

            for pid in self.state.player_order:
                t = self.state.get_player(pid)
                if not t or not t.is_alive() or t.player_id == player.player_id:
                    continue

                r = resolve_terror_damage(player, t, self.state, raw_damage=1.0)

                # 收集结算详情
                for detail in r.get("details", []):
                    lines.append(f"  [{t.name}] {detail}")

                if r.get("killed"):
                    self.state.markers.on_player_death(t.player_id)
                    if self.state.police_engine:
                        self.state.police_engine.on_player_death(t.player_id)
                    player.kill_count += 1
                    # 通知所有天赋（包括自身的色彩计数 _on_any_player_death）
                    from engine.round_manager import RoundManager
                    RoundManager.notify_all_talents_of_death(
                        self.state, t.player_id, killer_id=player.player_id)

            # 伤害结算后扣除额外HP（不同归于尽）
            self.terror_extra_hp = round(max(0, self.terror_extra_hp - 1.5), 2)
            extra_hp_msg = prompt_manager.get_prompt("talent", "g7hoshino.terror_extra_hp_status",
                                                 terror_extra_hp=self.terror_extra_hp)
            lines.append(extra_hp_msg)

            if self.terror_extra_hp <= 0:
                zero_msg = prompt_manager.get_prompt("talent", "g7hoshino.terror_extra_hp_zero")
                lines.append(zero_msg)

            return "\n".join(lines)

    def _terror_move(self, player, destination):
        """
        Terror 移动：消耗0.5额外HP（行动后扣，可致死）。
        返回消息字符串。
        """
        from actions import move
        msg = move.execute(player, destination, self.state)

        # 行动完成后扣除
        self.terror_extra_hp = round(max(0, self.terror_extra_hp - 0.5), 2)
        extra_hp_msg = prompt_manager.get_prompt("talent", "g7hoshino.terror_extra_hp_status",
                                          terror_extra_hp=self.terror_extra_hp)
        msg += f"\n{extra_hp_msg}"

        # 警察逃离逻辑（在 police_system 中处理）

        if self.terror_extra_hp <= 0:
            death_msg = prompt_manager.get_prompt("talent", "g7hoshino.terror_move_hp_zero")
            msg += f"\n{death_msg}"
            player.hp = 0

        return msg

    def _terror_on_death_check(self, player, damage_source):
        """
        Terror 下的死亡判定：
        - 被挂载的复活不生效
        - HP归零 → 无视任何条件死亡
        返回 None（不阻止死亡）
        """
        if self.is_terror:
            # 无视任何条件死亡
            return None
        return None

    def _poem_nightwatch_effect(self, caster, target):
        """
        献予「守夜人」之诗效果（由 g5/poem_mixin.py 调用）。
        需承受者选择是否接受。
        """
        player = target
        talent = target.talent
        if not hasattr(talent, 'color_is_null'):
            return prompt_manager.get_prompt("talent", "g7hoshino.target_not_hoshino")

        # 需承受者选择是否接受
        choice = player.controller.choose(
            "「向走向过去的少女说出你的愿望吧，她的未来，就是你的过去」\n是否接受献予「守夜人」之诗？",
            ["接受", "拒绝"],
            context={"phase": "T0", "situation": "poem_nightwatch_choice"}
        )
        if choice == "拒绝":
            return prompt_manager.get_prompt("talent", "g7hoshino.poem_reject")

        # 色彩值永久赋为null
        talent.color_is_null = True
        talent.color = 0

        msg_parts = [prompt_manager.get_prompt("talent", "g7hoshino.poem_accept")]

        if talent.is_terror:
            # Terror 解除
            talent.is_terror = False
            # 强制锁定ID解除（恢复原名需要外部记录，这里用通用方式）
            # 每1.5点剩余额外生命值转化为1点永久额外生命值（向下取整）
            permanent_extra = math.floor(talent.terror_extra_hp / 1.5)
            talent.terror_extra_hp = 0
            # 额外扣除3点（不致死，不足的话有多少扣多少）
            deduct = min(permanent_extra, 3) if permanent_extra >= 2 else permanent_extra
            permanent_extra -= deduct
            # 恢复铁之荷鲁斯（护甲值3）
            talent.iron_horus_hp = 3
            talent.iron_horus_max_hp = 3
            talent.fusion_shield_done = True
            # 恢复战术可用性
            talent.tactical_unlocked = True
            terror_cancel = prompt_manager.get_prompt("talent", "g7hoshino.poem_terror_cancel",
                                                 permanent_extra=permanent_extra)
            msg_parts.append(terror_cancel)
            msg_parts.append(prompt_manager.get_prompt("talent", "g7hoshino.poem_horus_restore"))
            msg_parts.append(prompt_manager.get_prompt("talent", "g7hoshino.poem_tactical_restore"))

        return "\n".join(msg_parts)