"""行动回合调度器（Phase 4 完整版 + Controller 接入）：T0天赋+石化+完整行动分发"""

import copy
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
                    remaining = 0.5
                    # 让天赋的临时HP（光环、炽愿等）先吸收
                    if (player.talent and hasattr(player.talent, 'receive_damage_to_temp_hp')
                            and not getattr(player, '_mythland_talent_suppressed', False)):
                        remaining = player.talent.receive_damage_to_temp_hp(remaining)
                    if remaining > 0:
                        player.hp = round(max(0, player.hp - remaining), 2)
                    absorbed = round(0.5 - remaining, 2)
                    actual = round(0.5 - absorbed, 2)
                    if absorbed > 0:
                        display.show_info(f"🗿→✨ {player.name} 解除石化！受{actual}伤害（临时HP吸收{absorbed}） → HP: {player.hp}")
                    else:
                        display.show_info(f"🗿→✨ {player.name} 解除石化！受0.5伤害 → HP: {player.hp}")
                    # 死亡判定
                    if player.hp <= 0:
                        self.state.markers.on_player_death(player.player_id)
                        display.show_death(player.name, "石化解除伤害")
                        return "petrify_death"
                    # 眩晕判定
                    if player.hp <= 0.5 and not player.is_stunned:
                        player.is_stunned = True
                        self.state.markers.add(player.player_id, "STUNNED")
                        display.show_info(f"💫 {player.name} 进入眩晕！")
                        return "petrify_stun"
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
        # ---- 插入式笑话检测 ----
        # 标记设置从 on_turn_start 移到此处，确保只在 T0 正常完成后才生效
        if (player.talent and hasattr(player.talent, 'cutaway_charges')
                and player.talent.cutaway_charges > 0
                and not getattr(player, '_in_cutaway_joke', False)):
            player._in_cutaway_joke = True
            player.talent._d4_force = False
            player.talent._d6_force = False
            display.show_info(f"🎭 {player.name} 的「插入式笑话」发动！可执行其他玩家的合法行动！")
        if getattr(player, '_in_cutaway_joke', False):
            return self._phase_t1_cutaway(player)
        result = self._get_available_actions(player)
        action_names = result[0]
        action_display = result[1]

        # 展示（Human 看屏幕；AI 忽略）
        display.show_available_actions(action_display)

        max_retries = 10
        attempts = 0

        from engine.filtered_state import FilteredGameState
        is_blinded = getattr(player, '_hoshino_blinded', False)
        # 兜底：致盲已过期但未被清理（如 Hoshino 死亡导致 on_round_start 未执行）
        if is_blinded:
            expire_round = getattr(player, '_hoshino_blind_expire_round', -1)
            if self.state.current_round > expire_round:
                player._hoshino_blinded = False
                for attr in ('_hoshino_blind_snapshot', '_hoshino_blind_markers_simple',
                             '_hoshino_blind_markers_relations', '_hoshino_blind_expire_round'):
                    if hasattr(player, attr):
                        delattr(player, attr)
                is_blinded = False
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
                display.show_player_status(player, observable)
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
            result = self._execute_action(parsed, player)
            msg, action_type, success = result[0], result[1], result[2]
            consumes_turn = result[3] if len(result) > 3 else success
            display.show_result(msg)
            if not success:
                display.show_info("⚠️ 行动执行失败，请重新选择行动。")
                continue
            if not consumes_turn:
                attempts -= 1  # 不消耗回合的成功操作不计入重试次数
                # 刷新可用行动列表（状态可能已变化，如取消盾牌后 move 解锁）
                action_names, action_display = self._get_available_actions(player)
                display.show_available_actions(action_display)
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
    #  T1（插入式笑话）：收集其他玩家的层次1行动并执行
    # ================================================================
    def _phase_t1_cutaway(self, player):
        """插入式笑话专用 T1：收集所有其他存活玩家的层次1标准行动，
        按行动类型分别校验执行。"""

        # ---- 只允许复制的 special 白名单 ----
        CUTAWAY_ALLOWED_SPECIALS = {"释放病毒"}

        # ---- 收集所有其他玩家的可用行动 ----
        collected_actions = []
        seen_keys = set()

        for pid in self.state.player_order:
            if pid == player.player_id:
                continue
            other = self.state.get_player(pid)
            if not other or not other.is_alive() or not other.is_awake:
                continue

            actions = action_registry.get_available_actions(other, self.state)
            for a in actions:
                name = a["name"]
                if name in ("放弃", "起床"):
                    continue

                # 特殊操作：只保留白名单内的
                if name == "特殊操作":
                    specials = special_op.get_available_specials(other, self.state)
                    for s in specials:
                        sname = s["name"]
                        if sname not in CUTAWAY_ALLOWED_SPECIALS:
                            continue
                        key = (f"special_{sname}", other.player_id)
                        if key not in seen_keys:
                            seen_keys.add(key)
                            collected_actions.append({
                                "display": f"特殊操作: {sname} — {s['description']}",
                                "usage": f"special {sname}",
                                "source_pid": other.player_id,
                            })
                    continue

                key = (name, other.player_id)
                if key not in seen_keys:
                    seen_keys.add(key)
                    collected_actions.append({
                        "display": f"{name} — {a.get('description', '')}",
                        "usage": a.get("usage", name),
                        "source_pid": other.player_id,
                    })

        if not collected_actions:
            display.show_info("🎭 插入式笑话：没有可用的行动！自动放弃。")
            from actions import forfeit
            msg = forfeit.execute(player, self.state)
            display.show_result(msg)
            return "forfeit"

        # ---- 展示可用行动 ----
        display.show_info("🎭 ═══ 插入式笑话：可选行动 ═══")
        for i, ca in enumerate(collected_actions, 1):
            source = self.state.get_player(ca["source_pid"])
            source_name = source.name if source else "?"
            display.show_info(f"  {i}. [{source_name}] {ca['display']}")

        # ---- 构建给 controller 的可用行动列表 ----
        action_names = []
        for ca in collected_actions:
            action_type = ca["usage"].split()[0]
            if action_type not in action_names:
                action_names.append(action_type)
        action_names.append("forfeit")

        # 构建 source_pid 查找表：action_type -> [pid1, pid2, ...]
        source_lookup = {}
        for ca in collected_actions:
            action_type = ca["usage"].split()[0]
            source_lookup.setdefault(action_type, [])
            if ca["source_pid"] not in source_lookup[action_type]:
                source_lookup[action_type].append(ca["source_pid"])

        # ---- 获取玩家/AI 输入并执行 ----
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
                    "cutaway_joke": True,
                }
            )

            # 查看类指令
            raw_lower = raw.strip().lower()
            if raw_lower in ("help", "status", "allstatus", "police"):
                if raw_lower == "help":
                    display.show_help()
                elif raw_lower == "status":
                    display.show_player_status(player, observable)
                elif raw_lower == "allstatus":
                    display.show_all_players_status(observable)
                elif raw_lower == "police":
                    display.show_police_status(self.state)
                continue

            # 解析
            from cli.parser import parse
            parsed = parse(raw, player.player_id)
            if parsed is None:
                display.show_info(f"⚠️ 无法解析指令: {raw}")
                continue

            action = parsed.get("action")

            # forfeit 直接执行
            if action == "forfeit":
                from actions import forfeit
                msg = forfeit.execute(player, self.state)
                display.show_result(msg)
                from utils.pacing import action_pause
                action_pause(self.state, label=f"{player.name} → forfeit (插入式笑话)")
                return "forfeit"

            # 找到对应的来源玩家列表
            source_pids = source_lookup.get(action, [])
            if not source_pids:
                display.show_info(f"⚠️ 插入式笑话中不可用的行动类型: {action}")
                continue

            # ---- 按行动类型分发校验和执行 ----
            result = None
            if action == "interact":
                result = self._cutaway_interact(player, parsed, source_pids)
            elif action == "move":
                result = self._cutaway_move(player, parsed)
            elif action == "find":
                result = self._cutaway_find(player, parsed, source_pids)
            elif action == "lock":
                result = self._cutaway_lock(player, parsed, source_pids)
            elif action == "attack":
                result = self._cutaway_attack(player, parsed, source_pids)
            elif action == "special":
                result = self._cutaway_special(player, parsed, source_pids)
            elif action in ("report", "assemble", "track_guide", "recruit",
                            "election", "designate", "study", "police_command"):
                result = self._cutaway_police(player, parsed, action, source_pids)
            else:
                display.show_info(f"⚠️ 插入式笑话中不支持的行动类型: {action}")
                continue

            if result is None:
                # 校验/执行失败，重试
                continue

            from utils.pacing import action_pause
            action_pause(self.state, label=f"{player.name} → {result} (插入式笑话)")
            return result

        # 重试耗尽
        display.show_info(f"[{player.name}] 插入式笑话重试耗尽，自动放弃。")
        from actions import forfeit as forfeit_mod
        msg = forfeit_mod.execute(player, self.state)
        display.show_result(msg)
        return "forfeit"

    # ================================================================
    #  插入式笑话：interact
    #  策略：G6 玩家 + 临时替换 location 为来源玩家的位置
    #  个人状态（凭证、物品、武器等）保留 G6 自己的
    #  前置法术：临时注入，学完后移除注入的部分
    #  多回合学习：直接完成（跳过进度系统）
    # ================================================================
    def _cutaway_interact(self, player, parsed, source_pids):
        from cli.validator import validate
        item_name = parsed.get("item")

        original_location = player.location
        original_spells = set(player.learned_spells)

        source_player = None
        last_reason = ""

        for sp_id in source_pids:
            sp = self.state.get_player(sp_id)
            if not sp:
                continue

            # 临时替换位置
            player.location = sp.location

            # 注入前置法术（如果需要）
            injected_prereqs = set()
            from locations.magic_institute import PREREQUISITES
            if item_name in PREREQUISITES:
                prereq = PREREQUISITES[item_name]
                if prereq not in player.learned_spells:
                    player.learned_spells.add(prereq)
                    injected_prereqs.add(prereq)

            valid, reason = validate(parsed, player, self.state)

            if valid:
                source_player = sp
                break

            # 校验失败，清理注入的前置
            player.learned_spells -= injected_prereqs
            last_reason = reason

        if not source_player:
            player.location = original_location
            player.learned_spells = original_spells
            display.show_info(f"⚠️ 指令不合法（所有来源校验失败）: {last_reason}")
            return None

        # ---- 执行 interact ----
        # 多回合学习：直接设置进度为完成状态
        from locations.magic_institute import LEARN_TURNS
        old_progress = None
        progress_key = None
        if item_name in LEARN_TURNS:
            required = LEARN_TURNS[item_name]
            if required > 1:
                progress_key = f"learn_{item_name}"
                old_progress = player.progress.get(progress_key)
                player.progress[progress_key] = required - 1  # do_interact 会 +1 达到 required

        success = False
        try:
            from actions import interact as interact_mod
            msg = interact_mod.execute(player, item_name, self.state)
            display.show_result(msg)

            success = not msg.startswith("❌")
        finally:
            # ---- 清理 ----
            # 恢复位置
            player.location = original_location

            # 清理注入的前置法术（只移除注入的，不移除本次学到的）
            # 本次学到的法术保留在 player.learned_spells 中
            newly_learned = player.learned_spells - original_spells - injected_prereqs
            player.learned_spells = original_spells | newly_learned

            # 失败时恢复人为设置的 progress
            if not success and progress_key is not None:
                if old_progress is None:
                    player.progress.pop(progress_key, None)
                else:
                    player.progress[progress_key] = old_progress

        if not success:
            display.show_info("⚠️ 行动执行失败，请重新选择。")
            return None

        return "interact"

    # ================================================================
    #  插入式笑话：move
    #  策略：直接用 G6 自己校验和执行，不需要来源玩家的状态
    #  G6 从自己的实际位置移动到目标位置
    # ================================================================
    def _cutaway_move(self, player, parsed):
        from cli.validator import validate
        valid, reason = validate(parsed, player, self.state)
        if not valid:
            display.show_info(f"⚠️ 指令不合法: {reason}")
            return None

        result = self._execute_action(parsed, player)
        msg, action_type, success = result[0], result[1], result[2]
        display.show_result(msg)

        if not success:
            display.show_info("⚠️ 行动执行失败，请重新选择。")
            return None

        return "move"

    # ================================================================
    #  插入式笑话：find
    #  策略：只要某个来源玩家能 find 目标，就把 G6 传送过去执行 find
    #  自定义校验：不能 find 来源玩家自己
    # ================================================================
    def _cutaway_find(self, player, parsed, source_pids):
        from cli.parser import resolve_player_target
        from cli.validator import _check_not_disabled

        target_str = parsed.get("target")
        if not target_str:
            display.show_info("⚠️ 请指定目标。")
            return None

        # G6 自身 debuff 检查
        ok, reason = _check_not_disabled(player, self.state)
        if not ok:
            display.show_info(f"⚠️ {reason}")
            return None

        target_id = resolve_player_target(target_str, self.state)
        if not target_id:
            display.show_info(f"⚠️ 找不到玩家「{target_str}」")
            return None
        target = self.state.get_player(target_id)
        if not target or not target.is_alive():
            display.show_info(f"⚠️ {target_str} 已死亡")
            return None
        if target_id == player.player_id:
            display.show_info("⚠️ 不能对自己使用找到")
            return None

        # 检查 G6 是否已经和目标面对面
        already = self.state.markers.has_relation(
            player.player_id, "ENGAGED_WITH", target_id)
        if already:
            display.show_info(f"⚠️ 你已经和 {target.name} 面对面了")
            return None

        # 全息影像禁止检查（用 G6 的 player_id）
        from cli.validator import _check_hologram_lock_find
        hologram_block = _check_hologram_lock_find(player, self.state)
        if hologram_block:
            display.show_info(f"⚠️ {hologram_block}")
            return None

        # 遍历来源玩家，找一个能 find 目标的
        found_source = None
        last_reason = ""
        for sp_id in source_pids:
            sp = self.state.get_player(sp_id)
            if not sp:
                continue
            # 不能通过来源玩家 find 来源玩家自己
            if target_id == sp_id:
                last_reason = f"不能通过 {sp.name} 找到 {sp.name} 自己"
                continue
            # 来源玩家必须和目标同地点
            if sp.location != target.location:
                last_reason = f"{target.name} 不在 {sp.name} 的位置"
                continue
            # 来源玩家必须能看到目标
            visible = self.state.markers.is_visible_to(
                target_id, sp.player_id, sp.has_detection)
            if not visible:
                last_reason = f"{target.name} 对 {sp.name} 不可见"
                continue
            # 烟雾检查（用来源玩家的位置）
            from cli.validator import _is_smoke_active, _is_hoshino_player
            if _is_smoke_active(self.state, sp.location):
                if not _is_hoshino_player(sp):
                    last_reason = "烟雾中无法执行 find"
                    continue
            found_source = sp
            break

        if not found_source:
            display.show_info(f"⚠️ 指令不合法（所有来源校验失败）: {last_reason}")
            return None

        # ---- 执行：传送 G6 到目标位置，然后 find ----
        original_location = player.location
        player.location = target.location

        from actions import find_target
        msg = find_target.execute(player, target_id, self.state)
        display.show_result(msg)

        if msg.startswith("❌"):
            player.location = original_location
            display.show_info("⚠️ 行动执行失败，请重新选择。")
            return None

        # 位置保持在目标位置（传送效果）
        # 不恢复 original_location
        return "find"

    # ================================================================
    #  插入式笑话：lock
    #  策略：来源玩家能看到目标 + G6 自己有远程武器 → 允许 lock
    # ================================================================
    def _cutaway_lock(self, player, parsed, source_pids):
        from cli.parser import resolve_player_target
        from cli.validator import _check_not_disabled
        from models.equipment import WeaponRange

        target_str = parsed.get("target")
        if not target_str:
            display.show_info("⚠️ 请指定锁定目标。")
            return None

        # G6 自身 debuff 检查
        ok, reason = _check_not_disabled(player, self.state)
        if not ok:
            display.show_info(f"⚠️ {reason}")
            return None

        target_id = resolve_player_target(target_str, self.state)
        if not target_id:
            display.show_info(f"⚠️ 找不到玩家「{target_str}」")
            return None
        target = self.state.get_player(target_id)
        if not target or not target.is_alive():
            display.show_info(f"⚠️ {target_str} 已死亡或不存在")
            return None
        if not target.is_on_map():
            display.show_info(f"⚠️ {target.name} 不在地图上")
            return None
        if target_id == player.player_id:
            display.show_info("⚠️ 不能锁定自己")
            return None

        # G6 必须持有远程武器
        has_ranged = any(
            getattr(w, 'weapon_range', None) == WeaponRange.RANGED
            for w in (player.weapons or []) if w
        )
        if not has_ranged:
            display.show_info("⚠️ 锁定是远程攻击前置，你没有远程武器")
            return None

        # G6 是否已经锁定了目标
        already = self.state.markers.has_relation(
            target_id, "LOCKED_BY", player.player_id)
        if already:
            display.show_info(f"⚠️ 你已经锁定了 {target.name}")
            return None

        # 全息影像禁止检查（用 G6 的 player_id）
        from cli.validator import _check_hologram_lock_find
        hologram_block = _check_hologram_lock_find(player, self.state)
        if hologram_block:
            display.show_info(f"⚠️ {hologram_block}")
            return None

        # 遍历来源玩家，找一个能看到目标的
        found_source = None
        last_reason = ""
        for sp_id in source_pids:
            sp = self.state.get_player(sp_id)
            if not sp:
                continue
            # 烟雾检查（用来源玩家看目标的位置）
            from cli.validator import _is_smoke_active, _is_hoshino_player
            target_loc = target.location
            if _is_smoke_active(self.state, target_loc):
                if not _is_hoshino_player(sp):
                    last_reason = "目标在烟雾区域中，无法锁定"
                    continue
            # 来源玩家必须能看到目标
            visible = self.state.markers.is_visible_to(
                target_id, sp.player_id, sp.has_detection)
            if not visible:
                last_reason = f"{target.name} 对 {sp.name} 不可见"
                continue
            found_source = sp
            break

        if not found_source:
            display.show_info(f"⚠️ 指令不合法（所有来源校验失败）: {last_reason}")
            return None

        # ---- 执行：用 G6 的 player_id 建立锁定标记 ----
        from actions import lock_target
        msg = lock_target.execute(player, target_id, self.state)
        display.show_result(msg)
        if msg.startswith("❌"):
            display.show_info("⚠️ 行动执行失败，请重新选择。")
            return None
        return "lock"

    # ================================================================
    #  插入式笑话：attack
    #  策略：直接用来源玩家校验和执行，击杀计数归 G6
    #  补检爱愿（用 G6 的 player_id）
    # ================================================================
    def _cutaway_attack(self, player, parsed, source_pids):
        from cli.validator import validate, _check_love_wish_block
        from cli.parser import resolve_player_target

        # 补检爱愿（用 G6 的 player_id）
        target_str = parsed.get("target")
        if target_str and not target_str.lower().startswith("police"):
            target_id = resolve_player_target(target_str, self.state)
            if target_id and _check_love_wish_block(
                    player.player_id, target_id, self.state):
                target_p = self.state.get_player(target_id)
                tname = target_p.name if target_p else target_str
                display.show_info(f"⚠️ 💝「爱愿」生效中：你无法攻击 {tname}")
                return None

        # 遍历来源玩家，找第一个通过校验的
        source_player = None
        last_reason = ""
        for sp_id in source_pids:
            sp = self.state.get_player(sp_id)
            if not sp:
                continue
            valid, reason = validate(parsed, sp, self.state)
            if valid:
                source_player = sp
                break
            last_reason = reason

        if not source_player:
            display.show_info(f"⚠️ 指令不合法（所有来源校验失败）: {last_reason}")
            return None

        # ---- 执行：用来源玩家执行攻击，击杀归属 G6 ----
        # 快照来源玩家的蓄力/导弹状态，执行后恢复
        weapon_name = parsed.get("weapon")
        src_weapon = source_player.get_weapon(weapon_name) if weapon_name else None
        src_charged_before = src_weapon.is_charged if (src_weapon and src_weapon.requires_charge) else None
        src_had_missile_ctrl = self.state.markers.has(source_player.player_id, "MISSILE_CTRL")

        # 临时禁用隐身暴露和攻击者天赋钩子（on_kill/break_love_wish）
        source_player._cutaway_skip_stealth_suppress = True
        source_player._cutaway_suppress_attacker_hooks = True

        try:
            result = self._execute_attack(parsed, source_player, override_killer=player)
        finally:
            # 恢复临时标记
            if hasattr(source_player, '_cutaway_skip_stealth_suppress'):
                del source_player._cutaway_skip_stealth_suppress
            if hasattr(source_player, '_cutaway_suppress_attacker_hooks'):
                del source_player._cutaway_suppress_attacker_hooks

            # 恢复蓄力状态
            if src_charged_before is not None and src_weapon:
                src_weapon.is_charged = src_charged_before

            # 恢复导弹控制权标记
            if src_had_missile_ctrl and not self.state.markers.has(
                    source_player.player_id, "MISSILE_CTRL"):
                self.state.markers.add(source_player.player_id, "MISSILE_CTRL")

        msg, action_type, success = result[0], result[1], result[2]
        display.show_result(msg)

        if not success:
            display.show_info("⚠️ 行动执行失败，请重新选择。")
            return None

        return "attack"

    # ================================================================
    #  插入式笑话：special（仅释放病毒）
    #  策略：用来源玩家的位置校验，G6 执行
    # ================================================================
    def _cutaway_special(self, player, parsed, source_pids):
        op_name = parsed.get("operation")
        if op_name != "释放病毒":
            display.show_info(f"⚠️ 插入式笑话中只能执行「释放病毒」，不能执行「{op_name}」")
            return None

        # 找一个在医院的来源玩家
        source_player = None
        for sp_id in source_pids:
            sp = self.state.get_player(sp_id)
            if sp and sp.location == "医院":
                source_player = sp
                break

        if not source_player:
            display.show_info("⚠️ 没有来源玩家在医院，无法释放病毒")
            return None

        if self.state.virus.is_active:
            display.show_info("⚠️ 病毒已经在活跃状态了")
            return None

        # 执行释放病毒（全局效果，用 G6 执行，临时设置位置）
        original_location = player.location
        player.location = "医院"
        try:
            msg, consumes = special_op.execute(player, "释放病毒", self.state)
            display.show_result(msg)
        finally:
            player.location = original_location

        if msg.startswith("❌"):
            display.show_info("⚠️ 行动执行失败，请重新选择。")
            return None

        return "special"

    # ================================================================
    #  插入式笑话：police 系列
    #  策略：
    #    - report/assemble/track_guide 依赖 player_id 身份检查
    #      （reporter_id / event_log），直接用来源玩家执行，成果归 G6
    #    - 其他（recruit/election/designate/study/police_command）
    #      临时借用来源玩家状态让 G6 执行
    # ================================================================
    # 需要以来源玩家身份执行的行动（内部用 player_id 做身份匹配）
    _POLICE_IDENTITY_ACTIONS = {"report", "assemble", "track_guide"}

    def _cutaway_police(self, player, parsed, action, source_pids):
        from cli.validator import validate

        source_player = None
        last_reason = ""
        for sp_id in source_pids:
            sp = self.state.get_player(sp_id)
            if not sp:
                continue
            valid, reason = validate(parsed, sp, self.state)
            if valid:
                source_player = sp
                break
            last_reason = reason

        if not source_player:
            display.show_info(f"⚠️ 指令不合法（所有来源校验失败）: {last_reason}")
            return None

        # ---- 身份依赖行动：直接用来源玩家执行，成功后转移归属到 G6 ----
        if action in self._POLICE_IDENTITY_ACTIONS:
            result = self._execute_action(parsed, source_player)
            msg, action_type, success = result[0], result[1], result[2]
            display.show_result(msg)

            if not success:
                display.show_info("⚠️ 行动执行失败，请重新选择。")
                return None

            # 转移归属：将 reporter_id 从来源玩家改为 G6
            # 使后续 assemble/track_guide 的身份检查指向 G6
            pe = self.state.police_engine
            if pe and hasattr(self.state, 'police') and self.state.police:
                if action == "report":
                    self.state.police.reporter_id = player.player_id
                elif action == "assemble":
                    # 警察保护从来源玩家转移到 G6
                    self.state.police.reporter_id = player.player_id
                    if getattr(source_player, 'has_police_protection', False):
                        source_player.has_police_protection = False
                        self.state.markers.remove(source_player.player_id, "POLICE_PROTECT")
                        player.has_police_protection = True
                        self.state.markers.add(player.player_id, "POLICE_PROTECT")
                elif action == "track_guide":
                    self.state.police.reporter_id = player.player_id

            return action_type

        # ---- 其他警察行动：临时借用来源玩家的警察相关状态 ----
        orig_location = player.location
        orig_is_police = player.is_police
        orig_is_captain = player.is_captain
        orig_is_criminal = player.is_criminal

        player.location = source_player.location
        player.is_police = source_player.is_police
        player.is_captain = source_player.is_captain
        player.is_criminal = source_player.is_criminal

        result = None
        try:
            result = self._execute_action(parsed, player)
        finally:
            # 恢复位置和犯罪状态（不因借用而改变）
            player.location = orig_location
            player.is_criminal = orig_is_criminal
            # is_police / is_captain：始终先恢复为原值，
            # 再根据操作是否真正改变了身份来重新设置。
            # 仅凭 result[2] 判断会误判（如 election 递增进度也算成功）。
            post_is_police = player.is_police
            post_is_captain = player.is_captain
            player.is_police = orig_is_police
            player.is_captain = orig_is_captain

            if result is not None and result[2]:
                # recruit 成功：do_recruit 会设 player.is_police = True
                if action == "recruit" and post_is_police:
                    player.is_police = True
                # election 完成（成为队长）：_make_captain 会设 is_captain = True
                if action == "election" and post_is_captain and not orig_is_captain:
                    player.is_captain = True

        msg, action_type, success = result[0], result[1], result[2]
        display.show_result(msg)

        if not success:
            display.show_info("⚠️ 行动执行失败，请重新选择。")
            return None

        return action_type

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
            msg, consumes = special_op.execute(player, op, self.state)
            is_ok = not msg.startswith("❌")
            return msg, "special", is_ok, consumes

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
    def _execute_attack(self, parsed, player, override_killer=None):
        """override_killer: 插入式笑话中传入 G6 玩家，
        使击杀归属、死亡显示、天赋通知都用 G6 的身份。"""
        killer = override_killer or player
        target_id = resolve_player_target(parsed["target"], self.state)
        weapon_name = parsed["weapon"]
        layer_str = parsed.get("layer")
        attr_str = parsed.get("attr")
        weapon = player.get_weapon(weapon_name)

        from models.equipment import WeaponRange
        if weapon.weapon_range == WeaponRange.AREA:
            return self._execute_area_attack(player, weapon, override_killer=override_killer)

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
                killer.kill_count += 1
                self.state.markers.on_player_death(target_id)
                if self.state.police_engine:
                    self.state.police_engine.on_player_death(target_id)
                display.show_death(target.name, f"被 {killer.name} 的 {weapon_name} 击杀")
                # 新增：通知所有天赋（星野色彩计数等）
                from engine.round_manager import RoundManager
                RoundManager.notify_all_talents_of_death(
                    self.state, target_id, killer_id=killer.player_id)

        return msg, "attack", not is_failure                   # CHANGED

    # ================================================================
    #  范围攻击执行
    # ================================================================
    def _execute_area_attack(self, player, weapon, override_killer=None):
        killer = override_killer or player
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
                selected = killer.controller.choose_multi(
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
                        and t.talent.has_love_wish(killer.player_id)):
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
                killer.kill_count += 1
                self.state.markers.on_player_death(t.player_id)
                if self.state.police_engine:
                    self.state.police_engine.on_player_death(t.player_id)
                display.show_death(t.name, f"被 {killer.name} 的 {weapon.name} 击杀")
                # 通知所有天赋（星野色彩计数等）
                from engine.round_manager import RoundManager
                RoundManager.notify_all_talents_of_death(
                    self.state, t.player_id, killer_id=killer.player_id)

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
                unit.last_attacker_id = killer.player_id
                if old_hp > 0 and unit.hp <= 0:
                    killed_any_police = True
            if police_at_loc:
                # 攻击警察视为犯法（犯罪记录归 killer，插入式笑话中归 G6）
                pe.check_and_record_crime(killer.player_id, "攻击警察")
                if killed_any_police and not self.state.police.has_captain():
                    self.state.police.clear_crimes(killer.player_id)
                    killer.is_criminal = False
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