"""行动回合调度器（Phase 4 完整版 + Controller 接入）：T0天赋+石化+完整行动分发"""

from cli import display
from cli.parser import parse, resolve_player_target
from cli.validator import validate
from engine.prompt_manager import prompt_manager
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

        # ---- 天赋被动T0（如萤火0.5血自愈） ----
        if (player.talent and hasattr(player.talent, 'on_turn_start')
            and not getattr(player, '_mythland_talent_suppressed', False)):
            t0_result = player.talent.on_turn_start(player)
            # 天赋可通过返回 {"consume_turn": True} 来跳过本回合（如星野自我怀疑）
            if isinstance(t0_result, dict) and t0_result.get("consume_turn"):
                msg = t0_result.get("message", "talent_turn_consumed")
                display.show_info(msg)
                return msg

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
            # 永恒之诗增强：被拉入者禁用主动天赋
            if getattr(player, '_eternity_blocked', False):
                t0_option = None
            else:
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

        # Terror 状态：只允许 attack 和 move
        if (player.talent and hasattr(player.talent, 'is_terror')
                and player.talent.is_terror):
            names = ["move"]
            descs = [
                {"usage": "move <地点>", "description": prompt_manager.get_prompt(
                    "talent", "g7hoshino.terror_move_desc")},
            ]
            # 检查是否有存活目标可攻击
            others_alive = []
            for pid in self.state.player_order:
                if pid == player.player_id:
                    continue
                p = self.state.get_player(pid)
                if p and p.is_alive():
                    others_alive.append(p)
            if others_alive:
                names.append("attack")
                descs.append(
                    {"usage": "attack", "description": prompt_manager.get_prompt(
                        "talent", "g7hoshino.terror_attack_desc")})
            return names, descs

        names = ["move", "interact", "forfeit"]
        descs = [
            {"usage": "move <地点>", "description": "移动到其他地点"},
            {"usage": "interact <项目名>", "description": "与当前地点交互"},
            {"usage": "forfeit", "description": "放弃行动"},
        ]

        # Terror 存活时：全场禁用 interact
        if self.state.is_terror_alive():
            names = [n for n in names if n != "interact"]
            descs = [d for d in descs if not d["usage"].startswith("interact")]

        # 星野架盾/持盾：过滤不可用行动
        if (player.talent and hasattr(player.talent, 'shield_mode')
                and player.talent.shield_mode):
            shield_mode = player.talent.shield_mode
            if shield_mode == "架盾":
                # 架盾：不能 move 也不能 interact
                names = [n for n in names if n not in ("move", "interact")]
                descs = [d for d in descs if d["usage"].split()[0] not in ("move", "interact")]
            elif shield_mode == "持盾":
                # 持盾：不能 interact
                names = [n for n in names if n != "interact"]
                descs = [d for d in descs if not d["usage"].startswith("interact")]

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

        from engine.filtered_state import FilteredGameState
        is_blinded = getattr(player, '_hoshino_blinded', False)
        observable = (FilteredGameState(self.state, player.player_id)
                      if is_blinded else self.state)

        while attempts < max_retries:
            attempts += 1

            raw = player.controller.get_command(
                player=player,
                game_state=observable,
                available_actions=action_names,
                context={
                    "phase": "T1",
                    "round": self.state.current_round,
                    "attempt": attempts,
                }
            )
            # ══ CONTROLLER 结束 ══

            # ---- 查看类指令（不消耗行动） ----
            raw_lower = raw.strip().lower()
            if raw_lower == "help":
                display.show_help()
                continue
            if raw_lower == "status":
                display.show_player_status(player, self.state)
                continue
            if raw_lower == "allstatus":
                display.show_all_players_status(observable)
                if is_blinded:
                    display.show_info(prompt_manager.get_prompt(
                        "talent", "g7hoshino.blind_info_stale",
                        default="⚠️ [致盲中·以上信息可能已过时]"))
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
            msg, action_type, success = self._execute_action(parsed, player)
            display.show_result(msg)
            if not success:
                display.show_info("⚠️ 行动执行失败，请重新选择行动。")
                continue
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

    def _execute_action(self, parsed, player):
        action = parsed["action"]

        if action == "wake":
            msg = wake_up.execute(player, self.state)
            return msg, "wake", True                          # CHANGED: 永远成功

        elif action == "move":
            dest = parsed["destination"]
            # Terror 移动：额外消耗0.5额外HP
            if (player.talent and hasattr(player.talent, 'is_terror')
                    and player.talent.is_terror):
                msg = player.talent._terror_move(player, dest)
                if player.talent.terror_extra_hp <= 0:
                    player.hp = 0
                    self.state.markers.on_player_death(player.player_id)
                    if self.state.police_engine:
                        self.state.police_engine.on_player_death(player.player_id)
                    terror_death = prompt_manager.get_prompt("talent", "g7hoshino.terror_death")
                    display.show_death(player.name, terror_death)
                    from engine.round_manager import RoundManager
                    RoundManager.notify_all_talents_of_death(
                        self.state, player.player_id, killer_id=None)
                return msg, "move", True
            msg = move.execute(player, dest, self.state)
            if (self.state.police_engine
                    and self.state.police.reported_target_id == player.player_id
                    and self.state.police.report_phase == "dispatched"):
                msg += f"\n   🚔 警察将在轮末自动追踪！"
            return msg, "move", True                          # CHANGED: 永远成功

        elif action == "interact":
            item = parsed["item"]
            msg = interact.execute(player, item, self.state)
            return msg, "interact", not msg.startswith("❌")  # CHANGED

        elif action == "lock":
            target_id = resolve_player_target(parsed["target"], self.state)
            if not target_id:                                  # CHANGED: 新增空检查
                return "❌ 找不到目标玩家", "lock", False
            msg = lock_target.execute(player, target_id, self.state)
            return msg, "lock", not msg.startswith("❌")      # CHANGED

        elif action == "find":
            target_id = resolve_player_target(parsed["target"], self.state)
            if not target_id:                                  # CHANGED: 新增空检查
                return "❌ 找不到目标玩家", "find", False
            msg = find_target.execute(player, target_id, self.state)
            return msg, "find", not msg.startswith("❌")      # CHANGED

        elif action == "attack":
            # Terror 攻击：走特殊逻辑
            if (player.talent and hasattr(player.talent, 'is_terror')
                    and player.talent.is_terror):
                msg = player.talent._terror_attack(player)
                # Terror 攻击后检查额外HP
                if player.talent.terror_extra_hp <= 0:
                    player.hp = 0
                    self.state.markers.on_player_death(player.player_id)
                    if self.state.police_engine:
                        self.state.police_engine.on_player_death(player.player_id)
                    terror_death = prompt_manager.get_prompt("talent", "g7hoshino.terror_death")
                    display.show_death(player.name, terror_death)
                    from engine.round_manager import RoundManager
                    RoundManager.notify_all_talents_of_death(
                        self.state, player.player_id, killer_id=None)
                return msg, "attack", True
            return self._execute_attack(parsed, player)        # 内部已改为三元组

        elif action == "special":
            op = parsed["operation"]
            msg = special_op.execute(player, op, self.state)
            return msg, "special", not msg.startswith("❌")   # CHANGED

        elif action == "report":
            target_id = resolve_player_target(parsed["target"], self.state)
            if not target_id:                                  # CHANGED
                return "❌ 找不到目标玩家", "report", False
            msg = self.state.police_engine.do_report(player.player_id, target_id)
            return msg, "report", not msg.startswith("❌")    # CHANGED

        elif action == "assemble":
            msg = self.state.police_engine.do_assemble(player.player_id)
            return msg, "assemble", not msg.startswith("❌")  # CHANGED

        elif action == "track_guide":
            msg = self.state.police_engine.do_track_guide(player.player_id)
            return msg, "track_guide", not msg.startswith("❌")  # CHANGED

        elif action == "recruit":
            msg, rewards = self.state.police_engine.do_recruit(player.player_id)
            if not rewards:
                return msg, "recruit", not msg.startswith("❌")  # CHANGED: 无奖励=失败
            # ══ CONTROLLER 改动：加入警察三选二走 controller ══
            # 在 choice 之前过滤不可用的奖励
            from models.equipment import make_armor as _ma_check
            filtered_rewards = []
            for r in rewards:
                if r == "盾牌":
                    test_armor = _ma_check("盾牌")
                    if test_armor:
                        can_equip, _ = player.armor.check_can_equip(test_armor)
                        if not can_equip:
                            continue  # 已有盾牌，不提供此选项
                if r == "警棍":
                    if player.has_weapon("警棍"):
                        continue  # 已有警棍，不提供此选项
                filtered_rewards.append(r)

            # 如果过滤后选项不足2个，补充"购买凭证"（可重复选）
            while len(filtered_rewards) < 2:
                filtered_rewards.append("购买凭证")
            display.show_info("加入警察！请从以下三项中选择两项奖励：")
            choice1 = player.controller.choose(
                "选择第1项：", filtered_rewards,
                context={"phase": "T1", "situation": "recruit_pick_1"}
            )
            remaining = [o for o in filtered_rewards if o != choice1]
            choice2 = player.controller.choose(
                "选择第2项：", remaining,
                context={"phase": "T1", "situation": "recruit_pick_2"}
            )
            # ══ CONTROLLER 改动结束 ══
            from models.equipment import make_weapon as _mw, make_armor as _ma
            reward_results = []
            for choice in [choice1, choice2]:
                if choice == "购买凭证":
                    player.vouchers += 1
                    reward_results.append(f"购买凭证（当前：{player.vouchers}张）")
                elif choice == "警棍":
                    w = _mw("警棍")
                    if w:
                        player.add_weapon(w)
                        reward_results.append("警棍")
                elif choice == "盾牌":
                    a = _ma("盾牌")
                    if a:
                        success, reason = player.add_armor(a)
                        if success:
                            reward_results.append("盾牌")
                        else:
                            # 装备失败，改为发放购买凭证作为补偿
                            player.vouchers += 1
                            reward_results.append(f"盾牌装备失败（{reason}），改为获得购买凭证（当前：{player.vouchers}张）")
            msg += f"\n选择了：{'、'.join(reward_results)}"
            return msg, "recruit", True                        # CHANGED

        elif action == "election":
            msg = self.state.police_engine.do_election(player.player_id)
            return msg, "election", not msg.startswith("❌")  # CHANGED

        elif action == "designate":
            target_id = resolve_player_target(parsed["target"], self.state)
            if not target_id:                                  # CHANGED
                return "❌ 找不到目标玩家", "designate", False
            msg = self.state.police_engine.captain_designate_target(
                player.player_id, target_id)
            return msg, "designate", not msg.startswith("❌") # CHANGED

        elif action == "split":
            return "❌ 拆分功能已在v1.9中移除（警察现在是独立单位）", "split", False  # CHANGED

        elif action == "study":
            msg = self.state.police_engine.do_study(player.player_id)
            return msg, "study", not msg.startswith("❌")     # CHANGED

        elif action == "police_command":
            from actions.police_command import execute as police_command_execute
            msg: str
            msg, _ = police_command_execute(player, parsed, self.state)
            return msg, "police_command", not msg.startswith("❌")

        elif action == "forfeit":
              msg = forfeit.execute(player, self.state)
              return msg, "forfeit", True                        # CHANGED: 永远成功

        return "❌ 未知行动", "unknown", False                  # CHANGED: 加❌前缀 + False




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

        # 攻击一旦进入 resolve_damage 就有副作用，不应重试
        # 但如果 attack.execute 返回 ❌（目标不存在/无武器），是纯失败
        is_failure = isinstance(msg, str) and msg.startswith("❌")

        if not is_failure:
            if weapon.requires_charge and weapon.is_charged:
                weapon.is_charged = False
            if "missile" in weapon.special_tags:
                self.state.markers.remove(player.player_id, "MISSILE_CTRL")

            if result.get("stealth_suppressed"):
                msg += f"\n   ⚠️ {player.name} 因面对面近战暂时暴露！"

            target = self.state.get_player(target_id)
            if result.get("killed") and target:
                player.kill_count += 1
                self.state.markers.on_player_death(target_id)
                if self.state.police_engine:
                    self.state.police_engine.on_player_death(target_id)
                display.show_death(target.name, f"被 {player.name} 的 {weapon_name} 击杀")
                # 新增：通知所有天赋（星野色彩计数等）
                from engine.round_manager import RoundManager
                RoundManager.notify_all_talents_of_death(
                    self.state, target_id, killer_id=player.player_id)

        return msg, "attack", not is_failure                   # CHANGED

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
                # 爱愿检查
                if (t.talent and hasattr(t.talent, 'has_love_wish')
                        and t.talent.has_love_wish(player.player_id)):
                    lines.append(f"      💝 「爱愿」保护 {t.name} 免受震荡！")
                # 六爻·元亨利贞：免疫震荡
                elif t.talent and hasattr(t.talent, 'is_immune_to_debuff') and t.talent.is_immune_to_debuff("shock"):
                    lines.append(f"      ☯️ {t.name} 的「元亨利贞」免疫了震荡！")
                else:
                    t.is_shocked = True
                    t.is_stunned = True
                    self.state.markers.on_shock(t.player_id)
                    lines.append(f"      ⚡ {t.name} 进入震荡状态！")

            if res.get("killed"):
                player.kill_count += 1
                self.state.markers.on_player_death(t.player_id)
                if self.state.police_engine:
                    self.state.police_engine.on_player_death(t.player_id)
                display.show_death(t.name, f"被 {player.name} 的 {weapon.name} 击杀")
                # 通知所有天赋（星野色彩计数等）
                from engine.round_manager import RoundManager
                RoundManager.notify_all_talents_of_death(
                    self.state, t.player_id, killer_id=player.player_id)

        # ---- 范围攻击同时波及同地点警察 ----
        pe = self.state.police_engine
        if pe and hasattr(self.state, 'police') and self.state.police:
            police_at_loc = self.state.police.units_at(player.location)
            killed_any_police = False
            for unit in police_at_loc:
                if not unit.is_alive():
                    continue
                old_hp = unit.hp
                atk_result = pe._resolve_attack_on_police(weapon, unit, attacker=player)
                lines.append(f"\n   → 对 警察{unit.unit_id}: {atk_result}")
                unit.last_attacker_id = player.player_id
                if old_hp > 0 and unit.hp <= 0:
                    killed_any_police = True
            if police_at_loc:
                # 攻击警察视为犯法
                pe.check_and_record_crime(player.player_id, "攻击警察")
                if killed_any_police and not self.state.police.has_captain():
                    self.state.police.clear_crimes(player.player_id)
                    player.is_criminal = False
                    lines.append(f"   💪 击杀警察！犯罪记录已清除")
                self.state.police.check_all_dead()

        if weapon.requires_charge and weapon.is_charged:
            weapon.is_charged = False

        return "\n".join(lines), "attack", True                # CHANGED

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