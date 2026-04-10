"""战术指令宏系统 Mixin — 神代天赋7核心"""

import random
from cli import display
from engine.prompt_manager import prompt_manager
from talents.g7.items import TACTICAL_ITEMS, MEDICINES


from typing import Any, TYPE_CHECKING

class TacticalMixin:
    """战术指令宏系统 Mixin"""

    # 类型声明（运行时由 Hoshino.__init__ 初始化）
    state: Any
    player_id: str
    tactical_unlocked: bool
    is_terror: bool
    iron_horus_hp: float
    iron_horus_max_hp: float
    eye_of_horus: Any
    cost: int
    max_cost: int
    shield_mode: str | None
    shield_snapshot_hp: int
    front_players: set
    back_players: set
    ammo: list
    max_ammo: int
    tactical_items: list
    medicines: list
    adrenaline_used: bool
    halos: list
    form: str | None
    shoot_streak: int
    dash_free_shield_cost: bool

    # 以下 stub 仅供静态类型检查器使用，运行时不定义（避免遮蔽 FacingMixin/HaloMixin 的真实实现）
    if TYPE_CHECKING:
        def _init_facing(self, player) -> None: ...
        def _on_find_target(self, target_id: str) -> None: ...
        def _flip_facing(self) -> None: ...
        def _clear_facing(self) -> None: ...
        def is_front(self, pid: str) -> bool: ...
        def is_back(self, pid: str) -> bool: ...
        def _halo_restore_one(self) -> bool: ...

    TACTICAL_COST = {
        "架盾": 2, "射击": 2, "重新装填": 0, "持盾": 1,
        "投掷": 1, "服药": 0, "冲刺": 1, "取消": 0,
        "find": 1, "lock": 1, "转向": 0, "排弹": 0,
    }

    def _parse_tactical_command(self, raw):
        """解析单条战术指令，返回 (action_name, args_list) 或 None"""
        parts = raw.strip().split()
        if not parts:
            return None
        cmd = parts[0]
        args = parts[1:] if len(parts) > 1 else []
        # 中文别名映射
        aliases = {
            "deploy": "架盾", "shield": "架盾",
            "shoot": "射击", "fire": "射击",
            "reload": "重新装填", "装填": "重新装填",
            "hold": "持盾", "举盾": "持盾",
            "throw": "投掷",
            "medicine": "服药", "med": "服药", "药": "服药",
            "dash": "冲刺", "sprint": "冲刺",
            "cancel": "取消",
            "find": "find", "找": "find", "找到": "find",
            "lock": "lock", "锁定": "lock",
            "turn": "转向", "flip": "转向",
            "reorder": "排弹", "排列": "排弹",
        }
        action = aliases.get(cmd, cmd)
        if action not in self.TACTICAL_COST:
            return None
        return (action, args)

    def _execute_tactical_macro(self, player):
        """战术指令宏主入口。返回 (消息str, 是否消耗回合bool)"""
        if not self.tactical_unlocked:
            return prompt_manager.get_prompt("talent", "g7hoshino.macro_not_unlocked"), False
        if self.is_terror:
            return prompt_manager.get_prompt("talent", "g7hoshino.macro_terror_block"), False
        if self.iron_horus_hp <= 0 and not self.eye_of_horus:
            return prompt_manager.get_prompt("talent", "g7hoshino.macro_broken"), False

        display.show_info(prompt_manager.get_prompt("talent", "g7hoshino.macro_enter"))
        cost_display = prompt_manager.get_prompt("talent", "g7hoshino.macro_cost_display",
                                            cost=self.cost, max_cost=self.max_cost)
        display.show_info(cost_display)
        display.show_info(prompt_manager.get_prompt("talent", "g7hoshino.macro_tactics_list"))

        # 收集指令
        commands = []
        dash_count = 0
        reorder_count = 0
        # 致盲状态下预建过滤视图，避免每次 allstatus 重建泄露实时数据
        _blind_observable = None
        if getattr(player, '_hoshino_blinded', False):
            from engine.filtered_state import FilteredGameState
            _blind_observable = FilteredGameState(self.state, player.player_id)
        while True:
            raw = player.controller.get_command(
                player=player,
                game_state=_blind_observable or self.state,
                available_actions=list(self.TACTICAL_COST.keys()) + ["terminal"],
                context={"phase": "T0", "situation": "hoshino_tactical_input"}
            )
            raw_stripped = raw.strip()
            raw_lower = raw_stripped.lower()
            if raw_lower == "terminal":
                break

            # 查看类指令（不消耗战术动作，不退出战术模式）
            if raw_lower == "allstatus":
                if _blind_observable:
                    display.show_all_players_status(_blind_observable)
                    display.show_info(prompt_manager.get_prompt(
                        "talent", "g7hoshino.blind_info_stale",
                        default="⚠️ [致盲中·以上信息可能已过时]"))
                else:
                    display.show_all_players_status(self.state)
                continue
            if raw_lower == "status":
                me = self.state.get_player(self.player_id)
                if me:
                    display.show_player_status(me, self.state)
                continue
            if raw_lower == "help":
                display.show_info(prompt_manager.get_prompt("talent", "g7hoshino.macro_help"))
                continue
            if raw_lower == "police":
                display.show_police_status(self.state)
                continue

            parsed = self._parse_tactical_command(raw_stripped)
            if parsed is None:
                msg = prompt_manager.get_prompt("talent", "g7hoshino.macro_unknown_cmd",
                                             raw_cmd=raw_stripped)
                display.show_info(msg)
                continue
            action_name, args = parsed
            # 冲刺每宏最多1次
            if action_name == "冲刺":
                dash_count += 1
                if dash_count > 1:
                    display.show_info(prompt_manager.get_prompt("talent", "g7hoshino.macro_dash_limit"))
                    continue
            if action_name == "排弹":
                reorder_count += 1
                if reorder_count > 1:
                    display.show_info(prompt_manager.get_prompt("talent", "g7hoshino.macro_reorder_limit"))
                    continue
            commands.append((action_name, args))
            cost = self.TACTICAL_COST[action_name]
            cmd_added = prompt_manager.get_prompt("talent", "g7hoshino.macro_cmd_added",
                                             action_name=action_name, args=''.join(args), cost=cost)
            display.show_info(cmd_added)

        if not commands:
            return prompt_manager.get_prompt("talent", "g7hoshino.macro_empty"), False

        # 扣除 cost 并依次执行
        start_msg = prompt_manager.get_prompt("talent", "g7hoshino.macro_start",
            default="⚔️ 战术指令宏开始执行（当前 Cost: {current_cost}）", current_cost=self.cost)
        lines = [start_msg]
        has_dashed = False  # 追踪本宏内是否执行过冲刺
        any_executed = False  # 追踪是否有命令实际执行
        for i, (action_name, args) in enumerate(commands):
            cost = self.TACTICAL_COST[action_name]
            if cost > self.cost:
                lines.append(prompt_manager.get_prompt("talent", "g7hoshino.macro_step_cost_insufficient",
                    default="  ❌ Cost不足执行 {action_name}（需要{cost}，当前{current_cost}），宏中断").format(
                    action_name=action_name, cost=cost, current_cost=self.cost))
                break
            self.cost -= cost
            any_executed = True
            is_last = (i == len(commands) - 1)
            result = self._dispatch_tactical(player, action_name, args, is_last, has_dashed=has_dashed)
            step_msg = prompt_manager.get_prompt("talent", "g7hoshino.macro_step",
                                             step=i+1, action_name=action_name, result=result,
                                             remaining_cost=self.cost)
            lines.append(step_msg)
            # 架盾/持盾结束检查
            if self.shield_mode and self._should_end_shield(player):
                self._end_shield_mode(player)
                lines.append(prompt_manager.get_prompt("talent", "g7hoshino.macro_shield_forced_end"))
            if action_name == "冲刺" and not result.startswith("❌"):
                has_dashed = True

        done_msg = prompt_manager.get_prompt("talent", "g7hoshino.macro_done",
                                          cost=self.cost, max_cost=self.max_cost)
        lines.append(done_msg)
        if any_executed:
        self._macro_used_this_round = True
        from cli import display as _display
        _display.show_info(prompt_manager.get_prompt("talent", "g7hoshino.macro_fatigue_set",
            default="⚠️ 失却之痛，流溢成河……（下轮 D4-1, D6-1）"))
        return "\n".join(lines), any_executed  # 仅在有命令实际执行时消耗回合

    def _dispatch_tactical(self, player, action_name, args, is_last, has_dashed=False):
        """分发单个战术动作"""
        if action_name == "架盾":
            return self._tac_deploy_shield(player)
        elif action_name == "射击":
            target_name = args[0] if args else None
            return self._tac_shoot(player, target_name)
        elif action_name == "重新装填":
            item_name = " ".join(args) if args else None
            return self._tac_reload(player, item_name)
        elif action_name == "持盾":
            return self._tac_hold_shield(player)
        elif action_name == "投掷":
            # 投掷 <道具名> <地点>
            item_name = args[0] if len(args) >= 1 else None
            location = args[1] if len(args) >= 2 else None
            return self._tac_throw(player, item_name, location)
        elif action_name == "服药":
            med_name = " ".join(args) if args else None
            return self._tac_medicine(player, med_name)
        elif action_name == "冲刺":
            dest = args[0] if args else None
            return self._tac_dash(player, dest, is_last)
        elif action_name == "取消":
            return self._tac_cancel(player)
        elif action_name == "find":
            target_name = args[0] if args else None
            return self._tac_find(player, target_name, has_dashed=has_dashed)
        elif action_name == "lock":
            target_name = args[0] if args else None
            return self._tac_lock(player, target_name)
        elif action_name == "转向":
            return self._tac_flip(player)
        elif action_name == "排弹":
            return self._tac_reorder(player)
        return prompt_manager.get_prompt("talent", "g7hoshino.macro_unknown_action")

    # ---- 架盾 ----
    def _tac_deploy_shield(self, player):
        if self.iron_horus_hp <= 0:
            return prompt_manager.get_prompt("talent", "g7hoshino.deploy_broken")
        if self.shield_mode == "架盾":
            return prompt_manager.get_prompt("talent", "g7hoshino.deploy_already")
        self.shield_mode = "架盾"
        self.shield_snapshot_hp = self.iron_horus_hp
        self._init_facing(player)
        # 列出正面玩家名字
        front_names = []
        for pid in self.front_players:
            p = self.state.get_player(pid)
            if p:
                front_names.append(p.name)
        back_names = []
        for pid in self.back_players:
            p = self.state.get_player(pid)
            if p:
                back_names.append(p.name)
        deploy_msg = prompt_manager.get_prompt("talent", "g7hoshino.deploy_ok",
                                          snapshot_hp=self.shield_snapshot_hp)
        return (f"{deploy_msg}\n"
                f"   正面({len(self.front_players)}): {', '.join(front_names) or '无'}\n"
                f"   背面({len(self.back_players)}): {', '.join(back_names) or '无'}")

    # ---- 射击 ----
    def _tac_shoot(self, player, target_name):
        if not self.ammo:
            return prompt_manager.get_prompt("talent", "g7hoshino.shoot_no_ammo")
        # 消耗1发子弹
        bullet = self.ammo[0]
        bullet_attr = bullet.get("attribute", "普通")
        self.ammo.pop(0)

        # 确定射击模式
        if self.shield_mode == "架盾":
            mode = "架盾射击"
        elif self.shield_mode == "持盾":
            mode = "持盾射击"
        else:
            mode = "普通射击"

        # 解析目标（架盾射击模式下从正面玩家中选择，不需要 find）
        if mode == "架盾射击":
            # 获取正面存活玩家列表
            front_alive = []
            for pid in self.front_players:
                p = self.state.get_player(pid)
                if p and p.is_alive():
                    front_alive.append(p)
            if not front_alive:
                # 子弹已消耗，但无有效目标
                return prompt_manager.get_prompt("talent", "g7hoshino.shoot_no_front_target")

            if target_name:
                # 尝试匹配玩家输入的名字
                from cli.parser import resolve_player_target
                target_id = resolve_player_target(target_name, self.state)
                target = self.state.get_player(target_id) if target_id else None
                if not target or target.player_id not in self.front_players or not target.is_alive():
                    # 输入的名字不在正面，提示可选目标
                    names = [p.name for p in front_alive]
                    return prompt_manager.get_prompt("talent", "g7hoshino.shoot_wrong_target",
                                                    target_name=target_name, available=', '.join(names))
            else:
                # 未指定目标，交互式选择
                if len(front_alive) == 1:
                    target = front_alive[0]
                else:
                    names = [p.name for p in front_alive]
                    from cli import display
                    front_targets = prompt_manager.get_prompt("talent", "g7hoshino.shoot_front_targets",
                                                       names=', '.join(names))
                    display.show_info(front_targets)
                    choice = player.controller.choose(
                        "选择架盾射击主目标：", names,
                        context={"phase": "T0", "situation": "hoshino_shield_shoot_target"}
                    )
                    target = next((p for p in front_alive if p.name == choice), front_alive[0])
        else:
            # 持盾射击和普通射击：仍需指定目标名
            from cli.parser import resolve_player_target
            target_id = resolve_player_target(target_name, self.state) if target_name else None
            target = self.state.get_player(target_id) if target_id else None
            if not target or not target.is_alive():
                return prompt_manager.get_prompt("talent", "g7hoshino.shoot_invalid_target")

        # 弹丸分配逻辑（每发3颗弹丸，每颗0.5伤害）
        pellet_damage = 0.5
        results = []

        if mode == "持盾射击":
            # 2颗必中
            for _ in range(2):
                r = self._apply_pellet_damage(player, target, pellet_damage, bullet_attr)
                results.append(r)
            # 第3颗 50% 概率飞散
            if random.random() < 0.5:
                r = self._apply_pellet_damage(player, target, pellet_damage, bullet_attr)
                results.append(r)
            else:
                results.append(prompt_manager.get_prompt("talent", "g7hoshino.pellet_miss",
                    default="💨 弹丸飞散！"))
        elif mode == "架盾射击":
            front_targets = [self.state.get_player(pid) for pid in self.front_players
                            if self.state.get_player(pid) and self.state.get_player(pid).is_alive()]
            if len(front_targets) <= 1:
                # 单目标：1必中 + 2颗各50%飞散
                r = self._apply_pellet_damage(player, target, pellet_damage, bullet_attr)
                results.append(f"{target.name}: {r}")
                for _ in range(2):
                    if random.random() < 0.5:
                        r = self._apply_pellet_damage(player, target, pellet_damage, bullet_attr)
                        results.append(f"{target.name}: {r}")
                    else:
                        results.append(prompt_manager.get_prompt("talent", "g7hoshino.pellet_miss",
                            default="💨 弹丸飞散！"))
            else:
                # 多目标：选中目标至少命中1颗，剩余2颗随机分配，不飞散
                r = self._apply_pellet_damage(player, target, pellet_damage, bullet_attr)
                results.append(f"{target.name}: {r}")
                for _ in range(2):
                    t = random.choice(front_targets)
                    r = self._apply_pellet_damage(player, t, pellet_damage, bullet_attr)
                    results.append(f"{t.name}: {r}")
        else:
            # 普通射击：3颗全部命中目标
            for _ in range(3):
                r = self._apply_pellet_damage(player, target, pellet_damage, bullet_attr)
                results.append(f"{target.name}: {r}")

        # 临战-Archer 连续射击计数
        self.shoot_streak += 1
        extra_msg = ""
        if self.form == "临战-Archer" and self.shoot_streak % 2 == 0:
            # 额外执行1次射击（不消耗cost和子弹，20%破甲）
            extra_msg = "\n   🏹 临战-Archer 额外射击！"
            # 基础破甲概率20%，脆弱+20%
            break_chance = 0.2
            if getattr(target, '_hoshino_fragile', False):
                break_chance += 0.2
                extra_msg += "（脆弱加成！）"
            armor_break = random.random() < break_chance
            if armor_break:
                extra_msg += "（破甲！）"
                from combat.damage_resolver import resolve_damage
                result = resolve_damage(player, target, weapon=None, game_state=self.state,
                             raw_damage_override=1.0, damage_attribute_override="无视属性克制",
                             is_talent_attack=True)
                for detail in result.get("details", []):
                    extra_msg += f"\n      {detail}"
                if result.get("killed"):
                    self.state.markers.on_player_death(target.player_id)
                    if self.state.police_engine:
                        self.state.police_engine.on_player_death(target.player_id)
                    player.kill_count += 1
                    from engine.round_manager import RoundManager
                    RoundManager.notify_all_talents_of_death(
                        self.state, target.player_id, killer_id=player.player_id)
                    extra_msg += prompt_manager.get_prompt("talent", "g7hoshino.shoot_kill")
            else:
                for _ in range(3):
                    r = self._apply_pellet_damage(player, target, pellet_damage, bullet_attr)
                    extra_msg += f"\n      {r}"

        return prompt_manager.get_prompt("talent", "g7hoshino.shoot_result",
                                       mode=mode, bullet_attr=bullet_attr,
                                       results='; '.join(results), extra_msg=extra_msg)

    def _apply_pellet_damage(self, player, target, damage, attribute_str):
        """对单个目标施加一颗弹丸伤害"""
        if not target.is_alive():
            return prompt_manager.get_prompt("talent", "g7hoshino.shoot_pellet_dead")
        # 警察保护简化：若保护阈值 < 1.5（一发子弹总伤害），忽略保护
        # 烟雾/致盲等效果已在 is_protected_by_police / get_protection_threshold 内部处理
        pe = getattr(self.state, 'police_engine', None)
        if pe:
            threshold = pe.get_protection_threshold(target.player_id)
            if threshold > 0 and threshold >= 1.5:
                return prompt_manager.get_prompt("talent", "g7hoshino.shoot_pellet_police_filter",
                                            threshold=threshold)

        if getattr(target, '_hoshino_fragile', False):
            armor_break = random.random() < 0.2
            if armor_break:
                from combat.damage_resolver import resolve_damage
                result = resolve_damage(player, target, weapon=None, game_state=self.state,
                            raw_damage_override=1.0, damage_attribute_override="无视属性克制",
                            is_talent_attack=True)
                detail_lines = []
                for detail in result.get("details", []):
                    detail_lines.append(f"    {detail}")
                if result.get("killed"):
                    self.state.markers.on_player_death(target.player_id)
                    if self.state.police_engine:
                        self.state.police_engine.on_player_death(target.player_id)
                    player.kill_count += 1
                    from engine.round_manager import RoundManager
                    RoundManager.notify_all_talents_of_death(
                        self.state, target.player_id, killer_id=player.player_id)
                    detail_lines.append("    " + prompt_manager.get_prompt("talent", "g7hoshino.shoot_kill"))
                summary = f"💥破甲！HP→{target.hp}"
                if detail_lines:
                    return summary + "\n" + "\n".join(detail_lines)
                return summary

        from combat.damage_resolver import resolve_damage
        result = resolve_damage(
            attacker=player, target=target, weapon=None,
            game_state=self.state,
            raw_damage_override=damage,
            damage_attribute_override=attribute_str,
            is_talent_attack=True,
        )
        # 收集结算详情（护甲破坏、溢出、HP变化等）
        detail_lines = []
        for detail in result.get("details", []):
            detail_lines.append(f"    {detail}")

        if result.get("killed"):
            self.state.markers.on_player_death(target.player_id)
            if self.state.police_engine:
                self.state.police_engine.on_player_death(target.player_id)
            player.kill_count += 1
            detail_lines.append("    " + prompt_manager.get_prompt("talent", "g7hoshino.shoot_kill"))
            # 通知所有天赋（星野色彩计数等）
            from engine.round_manager import RoundManager
            RoundManager.notify_all_talents_of_death(
                self.state, target.player_id, killer_id=player.player_id)

        summary = f"HP→{result.get('target_hp', '?')}"
        if detail_lines:
            return summary + "\n" + "\n".join(detail_lines)
        return summary

    # ---- 重新装填 ----
    def _tac_reload(self, player, item_name):
        """摧毁一件有属性的物品、护甲或武器，填充4发对应属性子弹"""
        if not item_name:
            # 让玩家选择
            candidates = []
            for item in player.items:
                if hasattr(item, 'attribute') and item.attribute:
                    candidates.append(("item", item.name, item.attribute.value))
            for armor in player.armor.get_all_active():
                if armor.name not in ("铁之荷鲁斯",):
                    candidates.append(("armor", armor.name, armor.attribute.value))
            for weapon in (player.weapons or []):
                if weapon and weapon.name != "拳击" and hasattr(weapon, 'attribute') and weapon.attribute:
                    candidates.append(("weapon", weapon.name, weapon.attribute.value))
            if not candidates:
                return prompt_manager.get_prompt("talent", "g7hoshino.reload_no_candidates")
            names = [f"{name}({attr})" for _, name, attr in candidates]
            item_name = player.controller.choose(
                "选择要消耗的物品/护甲/武器：", names,
                context={"phase": "T0", "situation": "hoshino_reload"}
            )
            # 解析选择
            for cat, name, attr in candidates:
                if name in item_name:
                    item_name = name
                    break

        # 查找物品/护甲/武器（先不消耗，确认弹药容量后再消耗）
        attr_str = None
        found_item_idx = None
        found_armor = None
        found_weapon_idx = None
        # 先查物品
        for i, item in enumerate(player.items):
            if item.name == item_name:
                attr_str = item.attribute.value if hasattr(item, 'attribute') and item.attribute else "普通"
                found_item_idx = i
                break
        # 再查护甲
        if attr_str is None:
            for armor in player.armor.get_all_active():
                if armor.name == item_name and armor.name != "铁之荷鲁斯":
                    attr_str = armor.attribute.value
                    found_armor = armor
                    break
        # 最后查武器（拳击不可消耗）
        if attr_str is None:
            for i, weapon in enumerate(player.weapons or []):
                if weapon and weapon.name == item_name and weapon.name != "拳击":
                    attr_str = weapon.attribute.value if hasattr(weapon, 'attribute') and weapon.attribute else "普通"
                    found_weapon_idx = i
                    break
        if attr_str is None:
            return prompt_manager.get_prompt("talent", "g7hoshino.reload_not_found",
                                          item_name=item_name)

        # 检查弹药容量（在消耗物品之前）
        current_total = len(self.ammo)
        new_bullets = min(4, self.max_ammo - current_total)
        if new_bullets <= 0:
            return prompt_manager.get_prompt("talent", "g7hoshino.reload_full",
                                          current_total=current_total, max_ammo=self.max_ammo)

        # 容量足够，消耗物品/护甲/武器
        if found_item_idx is not None:
            player.items.pop(found_item_idx)
        elif found_armor is not None:
            player.armor.remove_piece(found_armor)
        elif found_weapon_idx is not None:
            player.weapons.pop(found_weapon_idx)

        # 填充子弹
        for _ in range(new_bullets):
            self.ammo.append({"attribute": attr_str})
        overflow = 4 - new_bullets
        msg = prompt_manager.get_prompt("talent", "g7hoshino.reload_ok",
                                      item_name=item_name, count=new_bullets, attr=attr_str,
                                      total=current_total + new_bullets, max=self.max_ammo)
        if overflow > 0:
            msg += prompt_manager.get_prompt("talent", "g7hoshino.reload_overflow",
                                          excess=overflow, count=current_total + new_bullets, max=self.max_ammo)
        return msg

    # ---- 持盾 ----
    def _tac_hold_shield(self, player):
        if self.iron_horus_hp <= 0:
            return prompt_manager.get_prompt("talent", "g7hoshino.hold_broken")
        if self.shield_mode == "持盾":
            return prompt_manager.get_prompt("talent", "g7hoshino.hold_already")
        self.shield_mode = "持盾"
        # 持盾模式下铁之荷鲁斯作为 priority=100 最外层护甲
        # 实际的伤害减免在 damage_resolver 中通过 modify_incoming_damage 钩子实现
        return prompt_manager.get_prompt("talent", "g7hoshino.hold_ok",
                                       iron_horus_hp=self.iron_horus_hp)

    # ---- 投掷 ----
    def _tac_throw(self, player, item_name, location):
        if not self.tactical_items:
            return prompt_manager.get_prompt("talent", "g7hoshino.throw_no_items")
        if item_name is None:
            names = [it for it in self.tactical_items]
            item_name = player.controller.choose(
                "选择投掷的道具：", names,
                context={"phase": "T0", "situation": "hoshino_throw_item"}
            )
        if item_name not in self.tactical_items:
            return prompt_manager.get_prompt("talent", "g7hoshino.throw_not_owned",
                                            item_name=item_name)
        if location is None:
            from actions.move import get_all_valid_locations
            locs = get_all_valid_locations(self.state)
            location = player.controller.choose(
                "选择投掷目标地点：", locs,
                context={"phase": "T0", "situation": "hoshino_throw_location"}
            )

        self.tactical_items.remove(item_name)
        item_data = TACTICAL_ITEMS.get(item_name, {})
        effect = item_data.get("effect", "")

        # 获取目标地点的所有单位（排除自己）
        targets = [p for p in self.state.players_at_location(location)
                  if p.player_id != player.player_id and p.is_alive()]

        # 架盾状态下只对正面单位生效
        if self.shield_mode == "架盾":
            targets = [t for t in targets if self.is_front(t.player_id)]

        lines = [prompt_manager.get_prompt("talent", "g7hoshino.throw_header",
                                         item_name=item_name, location=location)]

        if effect == "fragile":
            for t in targets:
                from combat.damage_resolver import resolve_damage
                r = resolve_damage(player, t, weapon=None, game_state=self.state,
                                 raw_damage_override=0.5, damage_attribute_override="普通",
                                 is_talent_attack=True)
                t._hoshino_fragile = True
                # 包含护甲详情
                detail_str = ""
                for detail in r.get("details", []):
                    detail_str += f"\n      {detail}"
                lines.append(prompt_manager.get_prompt("talent", "g7hoshino.throw_fragile",
                                                      target_name=t.name,
                                                      target_hp=r.get('target_hp', '?'),
                                                      details=detail_str))
                if r.get("killed"):
                    self.state.markers.on_player_death(t.player_id)
                    if self.state.police_engine:
                        self.state.police_engine.on_player_death(t.player_id)
                    player.kill_count += 1
                    from engine.round_manager import RoundManager
                    RoundManager.notify_all_talents_of_death(
                        self.state, t.player_id, killer_id=player.player_id)

        elif effect == "shock":
            # 震撼弹：AOE震荡（含警察）
            for t in targets:
                t.is_shocked = True
                t.is_stunned = True
                self.state.markers.add(t.player_id, "SHOCKED")
                self.state.markers.add(t.player_id, "STUNNED")
                lines.append(prompt_manager.get_prompt("talent", "g7hoshino.throw_shock",
                                                     target_name=t.name))
            # 警察也受影响
            pe = getattr(self.state, 'police_engine', None)
            if pe and hasattr(self.state, 'police') and self.state.police:
                for unit in self.state.police.units_at(location):
                    if unit.is_alive():
                        unit.is_shocked = True
                        unit.is_stunned = True
                        lines.append(f"  → {unit.unit_id}: ⚡震荡")

        elif effect == "blind":
            from engine.filtered_state import create_snapshot
            for t in targets:
                t._hoshino_blinded = True
                t._hoshino_blind_expire_round = self.state.current_round + 1
                snapshot, frozen_simple, frozen_relations = create_snapshot(self.state, t.player_id)
                t._hoshino_blind_snapshot = snapshot
                t._hoshino_blind_markers_simple = frozen_simple
                t._hoshino_blind_markers_relations = frozen_relations
                lines.append(prompt_manager.get_prompt(
                    "talent", "g7hoshino.throw_blind",
                    target_name=t.name))
            # 闪光弹也影响警察：致盲期间不攻击、不执行命令、不提供保护
            pe = getattr(self.state, 'police_engine', None)
            if pe and hasattr(self.state, 'police') and self.state.police:
                for unit in self.state.police.units_at(location):
                    if unit.is_alive():
                        unit._hoshino_blinded = True
                        unit._hoshino_blind_expire_round = self.state.current_round + 1
                        lines.append(f"  → {unit.unit_id}: 👁️ 致盲")

        elif effect == "smoke":
            if not hasattr(self.state, '_hoshino_smoke_zones'):
                self.state._hoshino_smoke_zones = {}
            self.state._hoshino_smoke_zones[location] = self.state.current_round + 1
            lines.append(prompt_manager.get_prompt(
                "talent", "g7hoshino.throw_smoke", rounds=1))

            # 新增：清除该地点所有非星野玩家的已有 find/lock
            for p in self.state.players_at_location(location):
                if p.player_id != player.player_id:
                    self.state.markers.on_player_move(p.player_id)
                    lines.append(prompt_manager.get_prompt(
                        "talent", "g7hoshino.smoke_clear_relations",
                        name=p.name))

        elif effect == "burn":
            # 燃烧瓶：2层灼烧（复用g1灼烧逻辑）
            for t in targets:
                if t.talent and hasattr(t.talent, 'apply_burn'):
                    t.talent.apply_burn(2, 0.5)
                else:
                    # 直接设置灼烧属性
                    t._burn_stacks = getattr(t, '_burn_stacks', 0) + 2
                    t._burn_damage_per_stack = 0.5
                lines.append(prompt_manager.get_prompt("talent", "g7hoshino.throw_burn",
                                                     target_name=t.name))

        return "\n".join(lines)

    # ---- 服药 ----
    def _tac_medicine(self, player, med_name):
        if not self.medicines:
            return prompt_manager.get_prompt("talent", "g7hoshino.med_no_meds")
        if med_name is None:
            med_name = player.controller.choose(
                "选择服用的药物：", self.medicines,
                context={"phase": "T0", "situation": "hoshino_medicine"}
            )
        if med_name not in self.medicines:
            return prompt_manager.get_prompt("talent", "g7hoshino.med_not_owned",
                                            med_name=med_name)

        med_data = MEDICINES.get(med_name, {})
        effect = med_data.get("effect", "")

        if effect == "full_restore" and self.adrenaline_used:
            return prompt_manager.get_prompt("talent", "g7hoshino.med_adrenaline_used")
        # 新增：肾上腺素不允许在宏内使用
        if effect == "full_restore":
            return prompt_manager.get_prompt("talent", "g7hoshino.med_adrenaline_macro_block",
                default="❌ 肾上腺素不能在战术指令宏中使用，请在宏外通过 special 使用")

        self.medicines.remove(med_name)

        if effect == "cost_plus_1":
            self.cost = min(self.cost + 1, self.max_cost + 1)  # EPO可以超过max
            return prompt_manager.get_prompt("talent", "g7hoshino.med_epo", cost=self.cost)
        elif effect == "restore_halo":
            restored = self._halo_restore_one()
            if restored:
                return prompt_manager.get_prompt("talent", "g7hoshino.med_chocolate_restore")
            else:
                return prompt_manager.get_prompt("talent", "g7hoshino.med_chocolate_full")
        return prompt_manager.get_prompt("talent", "g7hoshino.med_generic", med_name=med_name)

    # ---- 冲刺 ----
    def _tac_dash(self, player, dest, is_last):
        """冲刺：消耗1cost，持盾状态下的战术移动"""
        if self.shield_mode != "持盾":
            return prompt_manager.get_prompt("talent", "g7hoshino.dash_no_hold")
        if dest is None:
            from actions.move import get_all_valid_locations
            locs = get_all_valid_locations(self.state)
            dest = player.controller.choose(
                "选择冲刺目标地点：", locs,
                context={"phase": "T0", "situation": "hoshino_dash"}
            )
        # 执行移动
        from actions import move
        move.execute(player, dest, self.state)
        dash_ok = prompt_manager.get_prompt("talent", "g7hoshino.dash_ok", destination=dest)
        msg = dash_ok

        # 临战-shielder 特殊：冲刺为宏最后一个动作时
        # → 自动锁定冲刺目标地点的一个玩家 → 冲击 → 自动架盾 → 该轮R4不扣cost
        if is_last and self.form == "临战-shielder":
            targets_at_dest = [
                p for p in self.state.players_at_location(dest)
                if p.player_id != player.player_id and p.is_alive()
            ]
            if targets_at_dest:
                import random
                if len(targets_at_dest) == 1:
                    impact_target = targets_at_dest[0]
                else:
                    # 多人时掷骰子，点数最低者吃冲击
                    rolls = {t.player_id: random.randint(1, 6) for t in targets_at_dest}
                    min_roll = min(rolls.values())
                    losers = [t for t in targets_at_dest if rolls[t.player_id] == min_roll]
                    impact_target = random.choice(losers)

                # 冲击：建立面对面关系
                self.state.markers.add_relation(player.player_id, "ENGAGED_WITH", impact_target.player_id)
                self.state.markers.add_relation(impact_target.player_id, "ENGAGED_WITH", player.player_id)

                # 冲击：对目标施加震荡
                impact_target.is_shocked = True
                impact_target.is_stunned = True
                self.state.markers.add(impact_target.player_id, "SHOCKED")
                self.state.markers.add(impact_target.player_id, "STUNNED")
                impact_msg = prompt_manager.get_prompt("talent", "g7hoshino.dash_impact",
                                                  target_name=impact_target.name)
                msg += f"\n{impact_msg}"

                # 自动进入架盾模式
                self.shield_mode = "架盾"
                self.shield_snapshot_hp = self.iron_horus_hp
                self._init_facing(player)  # FacingMixin
                msg += f"\n{prompt_manager.get_prompt('talent', 'g7hoshino.dash_auto_shield')}"

                # 该轮R4不扣cost
                self.dash_free_shield_cost = True

        return msg

    def _tac_cancel(self, player):
        """取消架盾或持盾状态"""
        if not self.shield_mode:
            return prompt_manager.get_prompt("talent", "g7hoshino.cancel_no_shield")
        old_mode = self.shield_mode
        self._end_shield_mode(player)
        return prompt_manager.get_prompt("talent", "g7hoshino.cancel_ok", old_mode=old_mode)

    def _tac_find(self, player, target_name, has_dashed=False):
        """战术指令宏内的 find"""
        from cli.parser import resolve_player_target
        target_id = resolve_player_target(target_name, self.state) if target_name else None
        if not target_id:
            return prompt_manager.get_prompt("talent", "g7hoshino.find_invalid")
        from actions import find_target
        result = find_target.execute(player, target_id, self.state)

        # 通知 FacingMixin（架盾模式下 find 的人归入正面）
        if self.shield_mode == "架盾":
            self._on_find_target(target_id)

        # "肘开23，迎接24"：持盾状态下，本宏内冲刺过且 find 成功 → 自动冲击+震荡
        find_success = self.state.markers.has_relation(player.player_id, "ENGAGED_WITH", target_id)
        if (has_dashed and self.shield_mode == "持盾"
                and find_success):
            target = self.state.get_player(target_id)
            if target and target.is_alive():
                target.is_shocked = True
                target.is_stunned = True
                self.state.markers.add(target.player_id, "SHOCKED")
                self.state.markers.add(target.player_id, "STUNNED")
                dash_impact = prompt_manager.get_prompt("talent", "g7hoshino.dash_find_impact",
                                              target_name=target.name)
                result += f"\n{dash_impact}"
                # 注意：这里不需要额外添加 engage_with，因为 find 已经建立了

        return result

    def _tac_lock(self, player, target_name):
        """战术指令宏内的 lock"""
        from cli.parser import resolve_player_target
        target_id = resolve_player_target(target_name, self.state) if target_name else None
        if not target_id:
            return prompt_manager.get_prompt("talent", "g7hoshino.lock_invalid")
        from actions import lock_target
        result = lock_target.execute(player, target_id, self.state)
        return result

    def _tac_flip(self, player):
        """转向：正面↔背面互换 + 切换守点模式"""
        if self.shield_mode != "架盾":
            return prompt_manager.get_prompt("talent", "g7hoshino.flip_no_shield")
        self._flip_facing()  # FacingMixin（内部会 toggle shield_guard_mode）
        mode_desc = "阻止进入（守点）" if self.shield_guard_mode == "block_entering" else "阻止离开"
        return prompt_manager.get_prompt("talent", "g7hoshino.flip_ok",
                                    front=len(self.front_players),
                                    back=len(self.back_players),
                                    mode=mode_desc)

    def _tac_reorder(self, player):
        """排弹：重新排列弹匣内子弹顺序（每宏限1次）"""
        if not self.ammo:
            return prompt_manager.get_prompt("talent", "g7hoshino.reorder_empty")
        if len(self.ammo) == 1:
            return prompt_manager.get_prompt("talent", "g7hoshino.reorder_single")

        # 显示当前弹匣
        current_display = " ".join(
            f"[{i+1}]{b.get('attribute', '普通')}" for i, b in enumerate(self.ammo)
        )
        current_msg = prompt_manager.get_prompt("talent", "g7hoshino.reorder_current",
                                         current_display=current_display)
        display.show_info(current_msg)

        # 请求新顺序
        raw_order = player.controller.get_command(
            player=player,
            game_state=self.state,
            available_actions=[str(i+1) for i in range(len(self.ammo))],
            context={"phase": "T0", "situation": "hoshino_reorder_ammo",
                     "ammo": [b.get("attribute", "普通") for b in self.ammo]}
        )

        # 解析输入的数字序列
        try:
            indices = [int(x) - 1 for x in raw_order.strip().split()]
        except ValueError:
            return prompt_manager.get_prompt("talent", "g7hoshino.reorder_error")

        # 验证：必须是当前弹匣长度的完整排列
        n = len(self.ammo)
        if sorted(indices) != list(range(n)):
            example = ' '.join(str(i+1) for i in range(n))
            return prompt_manager.get_prompt("talent", "g7hoshino.reorder_invalid",
                                         n=n, example=example)

        # 执行重排
        new_ammo = [self.ammo[i] for i in indices]
        self.ammo = new_ammo

        new_display = " ".join(
            f"[{i+1}]{b.get('attribute', '普通')}" for i, b in enumerate(self.ammo)
        )
        return prompt_manager.get_prompt("talent", "g7hoshino.reorder_ok",
                                      new_display=new_display)

    def _should_end_shield(self, player):
        """检查是否应该强制结束架盾/持盾"""
        # 铁之荷鲁斯护甲值归零
        if self.iron_horus_hp <= 0:
            return True
        # 眩晕/震荡等控制效果
        if getattr(player, 'is_stunned', False) or getattr(player, 'is_shocked', False):
            return True
        return False

    def _end_shield_mode(self, player):
        """结束架盾/持盾状态"""
        self.shield_mode = None
        self.shield_snapshot_hp = 0
        self._clear_facing()  # FacingMixin
        self.shoot_streak = 0  # 重置射击连击

    def _r4_shield_cost_check(self):
        """R4 最后：架盾 cost 扣除（README: "位于R4所有检查之后"）"""
        if self.shield_mode != "架盾":
            return
        # 临战-shielder 冲刺免cost
        if self.dash_free_shield_cost:
            self.dash_free_shield_cost = False
            return
        # 水着-shielder：架盾免cost；其他形态cost-1
        deduct = 0 if self.form == "水着-shielder" else 1
        if self.cost >= deduct:
            self.cost -= deduct
        else:
            # cost不足，架盾立刻结束
            player = self.state.get_player(self.player_id)
            if player:
                self._end_shield_mode(player)
                from cli import display
                msg = prompt_manager.get_prompt("talent", "g7hoshino.r4_cost_end_shield",
                                         player_name=player.name)
                display.show_info(msg)

    def get_move_extra_cost(self, mover_id):
        """
        返回 mover 从星野架盾地点离开需要额外花费的回合数。
        0 = 无阻碍。
        供 actions/move.py 调用。
        """
        if self.shield_mode != "架盾":
            return 0
        if not self.is_front(mover_id):
            return 0
        # 检查豁免：超新星过载、最后一曲、原初4强制移动、g5强制位移、g6插入式笑话
        mover = self.state.get_player(mover_id)
        if mover and mover.talent:
            # 超新星过载
            if hasattr(mover.talent, 'has_supernova') and mover.talent.has_supernova:
                return 0
        return 1  # 需要多花费1回合

