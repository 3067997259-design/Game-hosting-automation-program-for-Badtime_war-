"""色彩反转 + Terror 状态 Mixin — 神代天赋7"""

import math
from cli import display


class TerrorMixin:
    """色彩反转 + Terror 状态 Mixin"""

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
                f"色彩值已达 {self.color}。是否进入「自我怀疑」状态？",
                ["进入自我怀疑", "暂不"],
                context={"phase": "T0", "situation": "hoshino_self_doubt_choice"}
            )
            if choice == "进入自我怀疑":
                self.self_doubt_pending = True
                display.show_info(f"😰 {player.name} 进入「自我怀疑」状态，下一次行动回合将被跳过...")
        return None

    def _process_self_doubt(self, player):
        """
        T0: 自我怀疑 → 跳过回合 → 反转为 Terror。
        返回 skip reason string 或 None。
        """
        if not self.self_doubt_pending:
            return None
        self.self_doubt_pending = False
        display.show_info(f"😰 {player.name} 处于「自我怀疑」状态，本回合被跳过...")
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
            display.show_info(f"🛡️ 色彩反转！恢复护甲：{', '.join(restored_names)}")
        # 进入自我怀疑
        self.self_doubt_pending = True
        display.show_info(f"😰 {player.name} 色彩值达到10，进入「自我怀疑」状态...")
        return True

    def _enter_terror(self, player):
        """进入 Terror 状态"""
        display.show_info(f"\n{'='*60}")
        display.show_info(f"  ⚠️ 色彩反转 — {player.name} → 「星野-Terror」")
        display.show_info(f"{'='*60}\n")

        self.is_terror = True

        # ID 覆盖
        player.name = "星野-Terror"

        # 失去所有战术指令、战术物品和药物
        self.tactical_unlocked = False
        self.tactical_items.clear()
        self.medicines.clear()

        # 失去铁之荷鲁斯 → 按护甲值折算额外生命值
        horus_extra = self.iron_horus_hp * 1.5
        self.iron_horus_hp = 0

        # 失去所有光环 → 每层折算1额外生命值
        halo_extra = sum(1.0 for h in self.halos if h['active'])
        for h in self.halos:
            h['active'] = False
            h['recovering'] = False
            h['cooldown_remaining'] = 0

        # 失去所有护甲 → 每层1.5额外生命值
        armor_extra = 0.0
        all_armor = list(player.armor.get_all_active())
        for armor in all_armor:
            if not armor.is_broken:
                armor_extra += 1.5
            player.armor.remove_piece(armor)

        self.terror_extra_hp = horus_extra + halo_extra + armor_extra
        display.show_info(f"  铁之荷鲁斯({self.iron_horus_hp}→{horus_extra}HP) + "
                         f"光环({halo_extra}HP) + 护甲({armor_extra}HP)")
        display.show_info(f"  Terror 额外生命值: {self.terror_extra_hp}")

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

            lines = ["⚠️ Terror 攻击！"]

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
                    # 色彩 +2（击杀额外）
                    if not self.color_is_null:
                        self.color += 2

            # 伤害结算后扣除额外HP（不同归于尽）
            self.terror_extra_hp = round(max(0, self.terror_extra_hp - 1.0), 2)
            lines.append(f"  Terror 额外HP: {self.terror_extra_hp}")

            if self.terror_extra_hp <= 0:
                lines.append("  ⚠️ Terror 额外HP归零！")

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
        msg += f"\n  Terror 额外HP: {self.terror_extra_hp}"

        # 警察逃离逻辑（在 police_system 中处理）

        if self.terror_extra_hp <= 0:
            msg += f"\n  ⚠️ Terror 额外HP归零，判定死亡！"
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
            return "❌ 目标不是星野"

        # 需承受者选择是否接受
        choice = player.controller.choose(
            "「向走向过去的少女说出你的愿望吧，她的未来，就是你的过去」\n是否接受献予「守夜人」之诗？",
            ["接受", "拒绝"],
            context={"phase": "T0", "situation": "poem_nightwatch_choice"}
        )
        if choice == "拒绝":
            return "🌙 守夜人拒绝了涟漪的馈赠。"

        # 色彩值永久赋为null
        talent.color_is_null = True
        talent.color = 0

        msg_parts = ["🌙 献予「守夜人」之诗生效！色彩值永久归null"]

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
            msg_parts.append(f"Terror 解除！永久额外HP: {permanent_extra}")
            msg_parts.append(f"铁之荷鲁斯恢复（护甲值3）")
            msg_parts.append("战术指令、药物和战术装备可用性恢复（需自己回去拿）")

        return "\n".join(msg_parts)