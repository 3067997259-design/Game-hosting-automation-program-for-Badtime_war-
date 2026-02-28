"""行动回合调度器（Phase 4 完整版）：T0天赋+石化+完整行动分发"""

from cli import display
from cli.parser import parse, resolve_player_target
from cli.validator import validate
from actions import (action_registry, wake_up, move, interact,
                     forfeit, lock_target, find_target, attack, special_op)


class ActionTurnManager:
    def __init__(self, game_state):
        self.state = game_state

    def execute_action_turn(self, player):
        display.show_action_turn_header(player.name)
        skip = self._phase_t0(player)
        if skip:
            return skip
        if not player.is_awake:
            result_msg = wake_up.execute(player, self.state)
            display.show_result(result_msg)
            return "wake"
        action_type = self._phase_t1(player)
        self._phase_t2(player, action_type)
        return action_type

    def _phase_t0(self, player):
        """T0：眩晕苏醒 → 天赋被动T0 → 震荡处理 → 石化处理 → 天赋T0选项"""

        # 眩晕苏醒
        if player.is_stunned and not self.state.markers.has(player.player_id, "SHOCKED"):
            player.is_stunned = False
            player.hp = min(1.0, player.max_hp)
            self.state.markers.on_stun_recover(player.player_id)
            display.show_info(f"{player.name} 从眩晕中苏醒！HP恢复至 {player.hp}")

        # 天赋被动T0（如血火0.5血自愈）
        if player.talent and hasattr(player.talent, 'on_turn_start'):
            player.talent.on_turn_start(player)

        # 震荡处理
        if self.state.markers.has(player.player_id, "SHOCKED"):
            # 结界发动者免疫
            if (self.state.active_barrier
                    and self.state.active_barrier.is_caster_immune_to_control(player.player_id)):
                player.is_stunned = False
                player.is_shocked = False
                self.state.markers.on_shock_recover(player.player_id)
                display.show_info(f"🌀 {player.name} 在结界内免疫震荡，自动解除！")
            else:
                display.show_info(f"{player.name} 处于震荡状态，本回合用于苏醒。")
                player.is_stunned = False
                player.is_shocked = False
                self.state.markers.on_shock_recover(player.player_id)
                display.show_result(f"⚡ {player.name} 从震荡中苏醒！")
                return "shock_recover"

        # 石化处理
        if self.state.markers.has(player.player_id, "PETRIFIED"):
            # 结界发动者免疫
            if (self.state.active_barrier
                    and self.state.active_barrier.is_caster_immune_to_control(player.player_id)):
                self.state.markers.on_petrify_recover(player.player_id)
                player.is_petrified = False
                display.show_info(f"🌀 {player.name} 在结界内免疫石化，自动解除！")
            else:
                display.show_info(f"🗿 {player.name} 处于石化状态！")
                choice = display.prompt_choice(
                    "选择处理方式：",
                    ["解除石化（受0.5伤害）", "保持石化（本回合跳过）"]
                )
                if choice.startswith("解除"):
                    self.state.markers.on_petrify_recover(player.player_id)
                    player.is_petrified = False
                    player.hp = round(max(0, player.hp - 0.5), 2)
                    display.show_info(
                        f"🗿→✨ {player.name} 解除石化！受0.5伤害 → HP: {player.hp}")
                    if player.hp <= 0:
                        self.state.markers.on_player_death(player.player_id)
                        display.show_death(player.name, "石化解除伤害")
                        return "petrify_death"
                    if player.hp <= 0.5 and not player.is_stunned:
                        # 结界免疫检查（理论上不会走到这，因为上面已处理）
                        player.is_stunned = True
                        self.state.markers.add(player.player_id, "STUNNED")
                        display.show_info(f"💫 {player.name} 进入眩晕！")
                else:
                    display.show_info(f"🗿 {player.name} 选择保持石化，跳过本回合。")
                    return "petrify_skip"

        # 天赋T0选项
        if player.talent and player.is_awake:
            t0_option = player.talent.get_t0_option(player)
            if t0_option:
                # 六爻额外回合中禁用六爻
                if (player.talent.name == "六爻"
                        and hasattr(player, 'hexagram_extra_turn')
                        and player.hexagram_extra_turn):
                    t0_option = None

                if t0_option:
                    display.show_info(
                        f"🌟 天赋可用：【{t0_option['name']}】{t0_option['description']}")
                    choice = display.prompt_choice(
                        "是否在本回合开始时发动天赋？",
                        ["发动天赋", "不发动，正常行动"]
                    )
                    if choice == "发动天赋":
                        msg, consumes_turn = player.talent.execute_t0(player)
                        display.show_result(msg)
                        if consumes_turn:
                            return "talent_t0"

        return None
    def _phase_t1(self, player):
        """T1：选择行动并执行"""
        while True:
            display.show_player_status(player, self.state)
            available = action_registry.get_available_actions(player, self.state)
            display.show_available_actions(available)
            raw = display.prompt_input(player.name)
            parsed = parse(raw, player.player_id)

            if parsed is None:
                display.show_error("无法识别指令。输入 help 查看帮助。")
                continue

            # 非行动指令
            if parsed["action"] == "status":
                display.show_player_status(player, self.state)
                continue
            elif parsed["action"] == "allstatus":
                display.show_all_players_status(self.state)
                continue
            elif parsed["action"] == "help":
                display.show_help()
                continue
            elif parsed["action"] == "police_status":
                display.show_police_status(self.state)
                continue

            is_legal, reason = validate(parsed, player, self.state)
            if not is_legal:
                display.show_error(reason)
                continue

            result_msg, action_type = self._execute_action(parsed, player)
            display.show_result(result_msg)
            return action_type

    def _phase_t2(self, player, action_type):
        """T2：回合结束触发"""
        if player.talent:
            player.talent.on_turn_end(player, action_type)

    def _execute_action(self, parsed, player):
        action = parsed["action"]

        if action == "wake":
            msg = wake_up.execute(player, self.state)
            return msg, "wake"

        elif action == "move":
            dest = parsed["destination"]
            msg = move.execute(player, dest, self.state)
            if (self.state.police_engine
                    and self.state.police.reported_target_id == player.player_id
                    and self.state.police.report_phase in ("dispatched", "enforcing")):
                self.state.police_engine.on_target_moved(player.player_id, dest)
                msg += f"\n   🚔 警察开始追踪！"
            return msg, "move"

        elif action == "interact":
            item = parsed["item"]
            msg = interact.execute(player, item, self.state)
            return msg, "interact"

        elif action == "lock":
            target_id = resolve_player_target(parsed["target"], self.state)
            msg = lock_target.execute(player, target_id, self.state)
            return msg, "lock"

        elif action == "find":
            target_id = resolve_player_target(parsed["target"], self.state)
            msg = find_target.execute(player, target_id, self.state)
            return msg, "find"

        elif action == "attack":
            return self._execute_attack(parsed, player)

        elif action == "special":
            op = parsed["operation"]
            msg = special_op.execute(player, op, self.state)
            return msg, "special"

        elif action == "report":
            target_id = resolve_player_target(parsed["target"], self.state)
            msg = self.state.police_engine.do_report(player.player_id, target_id)
            return msg, "report"

        elif action == "assemble":
            msg = self.state.police_engine.do_assemble(player.player_id)
            return msg, "assemble"

        elif action == "track_guide":
            msg = self.state.police_engine.do_tracking_guide(player.player_id)
            return msg, "track_guide"

        elif action == "recruit":
            options = ["凭证", "警棍", "盾牌"]
            display.show_info("加入警察！请从以下三项中选择两项奖励：")
            choice1 = display.prompt_choice("选择第1项：", options)
            remaining = [o for o in options if o != choice1]
            choice2 = display.prompt_choice("选择第2项：", remaining)
            msg = self.state.police_engine.do_join_police(
                player.player_id, [choice1, choice2])
            return msg, "recruit"

        elif action == "election":
            msg = self.state.police_engine.do_election_progress(player.player_id)
            return msg, "election"

        elif action == "designate":
            target_id = resolve_player_target(parsed["target"], self.state)
            msg = self.state.police_engine.captain_designate_target(
                player.player_id, target_id)
            return msg, "designate"

        elif action == "split":
            team_id = parsed.get("team", "alpha")
            msg = self.state.police_engine.captain_split_team(
                player.player_id, team_id)
            return msg, "split"

        elif action == "study":
            msg = self.state.police_engine.captain_study(player.player_id)
            return msg, "study"

        elif action == "forfeit":
            msg = forfeit.execute(player, self.state)
            return msg, "forfeit"

        return "未知行动", "unknown"

    def _execute_attack(self, parsed, player):
        target_id = resolve_player_target(parsed["target"], self.state)
        weapon_name = parsed["weapon"]
        layer_str = parsed.get("layer")
        attr_str = parsed.get("attr")
        weapon = player.get_weapon(weapon_name)

        from models.equipment import WeaponRange
        if weapon.weapon_range == WeaponRange.AREA:
            return self._execute_area_attack(player, weapon)

        msg, result = attack.execute(
            player, target_id, weapon_name, self.state,
            layer_str=layer_str, attr_str=attr_str
        )

        if weapon.requires_charge and weapon.is_charged:
            weapon.is_charged = False
        if "missile" in weapon.special_tags:
            self.state.markers.remove(player.player_id, "MISSILE_CTRL")

        if (weapon.weapon_range == WeaponRange.MELEE
                and result.get("success") and player.is_invisible):
            engaged = self.state.markers.has_relation(
                player.player_id, "ENGAGED_WITH", target_id)
            if engaged:
                self.state.markers.on_engaged_melee_attack_by_invisible(
                    player.player_id, target_id)
                msg += f"\n   ⚠️ {player.name} 因面对面近战暂时暴露！"

        target = self.state.get_player(target_id)
        if result.get("killed") and target:
            self.state.markers.on_player_death(target_id)
            display.show_death(target.name, f"被 {player.name} 的 {weapon_name} 击杀")

        return msg, "attack"

    def _execute_area_attack(self, player, weapon):
        from combat.damage_resolver import resolve_area_damage

        results = resolve_area_damage(
            attacker=player, weapon=weapon,
            location=player.location, game_state=self.state,
        )

        lines = [f"🌍 {player.name} 使用「{weapon.name}」发动范围攻击！"]

        # 地动山摇震荡选择
        shock_targets = []
        if "shock_2_targets" in weapon.special_tags and results:
            alive_hit = [r for r in results
                         if r["target"].is_alive() and r["result"]["success"]]
            if alive_hit:
                names = [r["target"].name for r in alive_hit]
                lines.append(f"   可选震荡目标（最多2个）：{', '.join(names)}")
                selected = []
                for pick_num in range(min(2, len(alive_hit))):
                    opts = [r["target"].name for r in alive_hit
                            if r["target"].name not in selected]
                    if opts:
                        choice = display.prompt_choice(
                            f"选择第{pick_num+1}个震荡目标（或'跳过'）：",
                            opts + ["跳过"])
                        if choice != "跳过":
                            selected.append(choice)
                shock_targets = selected

        for r in results:
            t = r["target"]
            res = r["result"]
            lines.append(f"\n   → 对 {t.name}:")
            for detail in res.get("details", []):
                lines.append(f"      {detail}")

            if t.name in shock_targets:
                t.is_shocked = True
                t.is_stunned = True
                self.state.markers.on_shock(t.player_id)
                lines.append(f"      ⚡ {t.name} 进入震荡状态！")

            if res.get("killed"):
                player.kill_count += 1
                self.state.markers.on_player_death(t.player_id)
                display.show_death(t.name, f"被 {player.name} 的 {weapon.name} 击杀")

        if weapon.requires_charge and weapon.is_charged:
            weapon.is_charged = False

        return "\n".join(lines), "attack"
    
    def execute_single_action(self, player):
        """
        结界内的简化行动回合。
        无T0天赋选项、无R0/R4。
        仅执行T1（选择行动）+ T2（回合结束触发）。
        """
        display.show_info(f"\n🌀 [{player.name}] 的结界行动回合")
        display.show_info("  ⚠️ 结界内不可与地点交互（拿物品/学法术/手术/举报等）")

        action_type = self._phase_t1(player)
        self._phase_t2(player, action_type)
        return action_type