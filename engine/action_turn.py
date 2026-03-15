"""行动回合调度器（Phase 4 完整版 + Controller 接入）：T0天赋+石化+完整行动分发"""

from cli import display
from cli.parser import parse, resolve_player_target
from cli.validator import validate
from actions import (action_registry, wake_up, move, interact,
                     forfeit, lock_target, find_target, attack, special_op)


class ActionTurnManager:
    def __init__(self, game_state):
        self.state = game_state

    # ================================================================
    #  主入口
    # ================================================================
    def execute_action_turn(self, player):
        display.show_action_turn_header(player.name)
        skip = self._phase_t0(player)
        if skip:
            from utils.pacing import action_pause
            action_pause(self.state, f"{player.name} → {skip}")
            return skip
        if not player.is_awake:
            result_msg = wake_up.execute(player, self.state)
            display.show_result(result_msg)
            return "wake"
        action_type = self._phase_t1(player)
        self._phase_t2(player, action_type)
        return action_type

    # ================================================================
    #  T0：眩晕苏醒 → 天赋被动T0 → 震荡 → 石化 → 天赋T0选项
    # ================================================================
    def _phase_t0(self, player):

        # ---- 眩晕苏醒 ----
        if player.is_stunned and not self.state.markers.has(player.player_id, "SHOCKED"):
            player.is_stunned = False
            player.hp = min(1.0, player.max_hp)
            self.state.markers.on_stun_recover(player.player_id)
            display.show_info(f"{player.name} 从眩晕中苏醒！HP恢复至 {player.hp}")

        # ---- 天赋被动T0（如萤火0.5血自愈）----
        if player.talent and hasattr(player.talent, 'on_turn_start'):
            player.talent.on_turn_start(player)

        # ---- 震荡处理 ----
        if self.state.markers.has(player.player_id, "SHOCKED"):
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

        # ---- 石化处理 ----
        if self.state.markers.has(player.player_id, "PETRIFIED"):
            if (self.state.active_barrier
                    and self.state.active_barrier.is_caster_immune_to_control(player.player_id)):
                self.state.markers.on_petrify_recover(player.player_id)
                player.is_petrified = False
                display.show_info(f"🌀 {player.name} 在结界内免疫石化，自动解除！")
            else:
                display.show_info(f"🗿 {player.name} 处于石化状态！")

                # ══ CONTROLLER 改动：石化选择走 controller ══
                choice = player.controller.choose(
                    "选择处理方式：",
                    ["解除石化（受0.5伤害）", "保持石化（本回合跳过）"],
                    context={"phase": "T0", "situation": "petrified"}
                )
                # ══ CONTROLLER 改动结束 ══

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
                        player.is_stunned = True
                        self.state.markers.add(player.player_id, "STUNNED")
                        display.show_info(f"💫 {player.name} 进入眩晕！")
                else:
                    display.show_info(f"🗿 {player.name} 选择保持石化，跳过本回合。")
                    return "petrify_skip"

        # ---- 天赋T0选项 ----
        # ══ BUG FIX：choice 变量未定义问题修复 ══
        if player.talent and player.is_awake:
            t0_option = player.talent.get_t0_option(player)
            if t0_option:
                # 防御性兼容：字符串→字典
                if isinstance(t0_option, str):
                    t0_option = {
                        "name": player.talent.name,
                        "description": t0_option
                    }
                elif not isinstance(t0_option, dict):
                    t0_option = {
                        "name": player.talent.name,
                        "description": str(t0_option)
                    }

                # 六爻额外回合中禁用六爻
                if (player.talent.name == "六爻"
                        and hasattr(player, 'hexagram_extra_turn')
                        and player.hexagram_extra_turn):
                    t0_option = None

            # ══ BUG FIX：把 choice 判断移入 t0_option 非 None 的分支内 ══
            if t0_option:
                display.show_info(
                    f"🌟 天赋可用：【{t0_option['name']}】{t0_option['description']}")

                # ══ CONTROLLER 改动：天赋T0是否发动走 controller ══
                choice = player.controller.choose(
                    "是否在本回合开始时发动天赋？",
                    ["发动天赋", "不发动，正常行动"],
                    context={
                        "phase": "T0",
                        "situation": "talent_t0",
                        "talent_name": t0_option["name"],
                        "talent_desc": t0_option["description"],
                    }
                )
                # ══ CONTROLLER 改动结束 ══

                if choice == "发动天赋":
                    msg, consumes_turn = player.talent.execute_t0(player)
                    display.show_result(msg)
                    if consumes_turn:
                        return "talent_t0"
            # ══ BUG FIX 结束：如果 t0_option 为 None，直接跳过，不会访问未定义的 choice ══

        return None

    # ================================================================
    #  辅助：可用行动类型列表
    # ================================================================
    def _get_available_actions(self, player) -> tuple[list[str], list[dict[str, str]]]:
        """
        返回两个列表：
        - action_names: ["move", "interact", ...] → 给 controller
        - action_display: [{"usage": ..., "description": ...}, ...] → 给 display
        """
        if not player.is_awake:
            names = ["wake"]
            descs = [{"usage": "wake", "description": "起床"}]
            return names, descs

        names = ["move", "interact", "forfeit"]
        descs = [
            {"usage": "move <地点>", "description": "移动到其他地点"},
            {"usage": "interact <项目名>", "description": "与当前地点交互"},
            {"usage": "forfeit", "description": "放弃行动"},
        ]

        # 用 get_player 遍历 player_order，避免依赖 alive_players() 的签名
        others_alive = []
        for pid in self.state.player_order:
            if pid == player.player_id:
                continue
            p = self.state.get_player(pid)
            if p and p.is_alive():
                others_alive.append(p)

        if others_alive:
            names.extend(["lock", "find", "attack"])
            descs.extend([
                {"usage": "lock <玩家名>", "description": "锁定目标（远程前置）"},
                {"usage": "find <玩家名>", "description": "找到目标（近战前置）"},
                {"usage": "attack <目标> <武器> [层 属性]", "description": "攻击目标"},
            ])

        if player.weapons or player.items:
            names.append("special")
            descs.append(
                {"usage": "special <操作名>", "description": "特殊操作"}
            )

        if self.state.police_engine:
            police_actions = [
                ("report",      "report <玩家名>",    "举报违法者"),
                ("assemble",    "assemble",            "集结警察"),
                ("track_guide", "track",               "追踪指引"),
                ("recruit",     "recruit",             "加入警察"),
                ("election",    "election",            "竞选队长"),
                ("designate",   "designate <玩家名>",  "指定执法目标"),
                ("split",       "split <警队ID>",      "拆分警队"),
                ("study",       "study",               "研究性学习（威信+1）"),
            ]
            for name, usage, desc in police_actions:
                names.append(name)
                descs.append({"usage": usage, "description": desc})
            
            # 队长操控警察命令（仅队长可见）
            if player.is_captain:
                names.append("police_command")
                descs.append({
                    "usage": "police move/equip/attack <警察ID> <参数>", 
                    "description": "队长操控警察移动/装备/攻击"
                })

        return names, descs

    # ================================================================
    #  T1：选择行动类型并执行
    # ================================================================
    def _phase_t1(self, player):
        """
        T1：从 controller 获取命令 → parse → validate → execute。
        """
        result = self._get_available_actions(player)
        action_names = result[0]
        action_display = result[1]

        # 展示（Human 看屏幕；AI 忽略）
        display.show_available_actions(action_display)

        max_retries = 10
        attempts = 0

        while attempts < max_retries:
            attempts += 1

            # ══ CONTROLLER：输入来源 ══
            raw = player.controller.get_command(
                player=player,
                game_state=self.state,
                available_actions=action_names,
                context={
                    "phase": "T1",
                    "round": self.state.current_round,
                    "attempt": attempts,
                }
            )
            # ══ CONTROLLER 结束 ══

            # ---- 查看类指令（不消耗行动）----
            raw_lower = raw.strip().lower()
            if raw_lower == "help":
                display.show_help()
                continue
            if raw_lower == "status":
                display.show_player_status(player, self.state)
                continue
            if raw_lower == "allstatus":
                display.show_all_players_status(self.state)
                continue
            if raw_lower == "police":
                display.show_police_status(self.state)
                continue

            # 解析
            parsed = parse(raw, player.player_id)
            if parsed is None:
                display.show_info(f"⚠️ 无法解析指令: {raw}")
                continue

            # 校验
            valid, reason = validate(parsed, player, self.state)
            if not valid:
                display.show_info(f"⚠️ 指令不合法: {reason}")
                continue

            # 执行
            msg, action_type = self._execute_action(parsed, player)
            display.show_result(msg)
            from utils.pacing import action_pause
            action_pause(self.state, label=f"{player.name} → {action_type}")
            return action_type

        # 重试耗尽 → 强制 forfeit
        display.show_info(f"[{player.name}] 重试次数耗尽，自动放弃行动。")
        msg = forfeit.execute(player, self.state)
        display.show_result(msg)
        return "forfeit"

    # ================================================================
    #  T2：回合结束触发
    # ================================================================
    def _phase_t2(self, player, action_type):
        if player.talent:
            player.talent.on_turn_end(player, action_type)

    # ================================================================
    #  行动执行分发
    # ================================================================
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
                    and self.state.police.report_phase == "dispatched"):
                msg += f"\n   🚔 警察将在轮末自动追踪！"
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
            msg = self.state.police_engine.do_track_guide(player.player_id)
            return msg, "track_guide"

        elif action == "recruit":
            msg, rewards = self.state.police_engine.do_recruit(player.player_id)
            if rewards:
                # ══ CONTROLLER 改动：加入警察三选二走 controller ══
                display.show_info("加入警察！请从以下三项中选择两项奖励：")
                choice1 = player.controller.choose(
                    "选择第1项：", rewards,
                    context={"phase": "T1", "situation": "recruit_pick_1"}
                )
                remaining = [o for o in rewards if o != choice1]
                choice2 = player.controller.choose(
                    "选择第2项：", remaining,
                    context={"phase": "T1", "situation": "recruit_pick_2"}
                )
                # ══ CONTROLLER 改动结束 ══
                # 实际发放奖励
                from models.equipment import make_weapon as _mw, make_armor as _ma
                for choice in [choice1, choice2]:
                    if choice == "购买凭证":
                        player.vouchers += 1
                    elif choice == "警棍":
                        w = _mw("警棍")
                        if w:
                            player.add_weapon(w)
                    elif choice == "盾牌":
                        a = _ma("盾牌")
                        if a:
                            player.add_armor(a)
                msg += f"\n选择了：{choice1}、{choice2}"
            return msg, "recruit"

        elif action == "election":
            msg = self.state.police_engine.do_election(player.player_id)
            return msg, "election"

        elif action == "designate":
            target_id = resolve_player_target(parsed["target"], self.state)
            msg = self.state.police_engine.captain_designate_target(
                player.player_id, target_id)
            return msg, "designate"

        elif action == "split":
            return "❌ 拆分功能已在v1.9中移除（警察现在是独立单位）", "split"

        elif action == "study":
            msg = self.state.police_engine.do_study(player.player_id)
            return msg, "study"

        elif action == "police_command":
            # 队长操控警察
            from actions.police_command import execute as police_command_execute
            msg = police_command_execute(player, parsed, self.state)
            return msg, "police_command"

        elif action == "forfeit":
            msg = forfeit.execute(player, self.state)
            return msg, "forfeit"
        

        return "未知行动", "unknown"

    # ================================================================
    #  攻击执行
    # ================================================================
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
            player.kill_count += 1
            self.state.markers.on_player_death(target_id)
            display.show_death(target.name, f"被 {player.name} 的 {weapon_name} 击杀")

        return msg, "attack"

    # ================================================================
    #  范围攻击执行
    # ================================================================
    def _execute_area_attack(self, player, weapon):
        from combat.damage_resolver import resolve_area_damage

        results = resolve_area_damage(
            attacker=player, weapon=weapon,
            location=player.location, game_state=self.state,
        )

        lines = [f"🌍 {player.name} 使用「{weapon.name}」发动范围攻击！"]

        # ---- 地动山摇震荡选择 ----
        shock_targets = []
        if "shock_2_targets" in weapon.special_tags and results:
            alive_hit = [r for r in results
                         if r["target"].is_alive() and r["result"]["success"]]
            if alive_hit:
                names = [r["target"].name for r in alive_hit]
                lines.append(f"   可选震荡目标（最多2个）：{', '.join(names)}")

                # ══ CONTROLLER 改动：震荡目标选择走 controller ══
                selected = player.controller.choose_multi(
                    "选择震荡目标（最多2个）：",
                    names,
                    max_count=min(2, len(alive_hit)),
                    min_count=0,
                    context={"phase": "T1", "situation": "shock_target_select"}
                )
                # ══ CONTROLLER 改动结束 ══
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

    # ================================================================
    #  结界内简化行动回合
    # ================================================================
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
