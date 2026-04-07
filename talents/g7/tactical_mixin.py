"""战术指令宏系统 Mixin — 神代天赋7核心"""

import random
from cli import display
from talents.g7.items import TACTICAL_ITEMS, MEDICINES


from typing import Any, TYPE_CHECKING

class TacticalMixin:
    """战术指令宏系统 Mixin"""

    # 类型声明（运行时由 Hoshino.__init__ 初始化）
    state: Any
    player_id: str
    tactical_unlocked: bool
    is_terror: bool
    iron_horus_hp: int
    iron_horus_max_hp: int
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
            "reorder": "排弹", "排列": "换弹",
        }
        action = aliases.get(cmd, cmd)
        if action not in self.TACTICAL_COST:
            return None
        return (action, args)

    def _execute_tactical_macro(self, player):
        """战术指令宏主入口。返回 (消息str, 是否消耗回合bool)"""
        if not self.tactical_unlocked:
            return "❌ 战术指令尚未解锁（需同时持有铁之荷鲁斯和荷鲁斯之眼）", False
        if self.is_terror:
            return "❌ Terror 状态下无法使用战术指令", False
        if self.iron_horus_hp <= 0 and not self.eye_of_horus:
            return "❌ 铁之荷鲁斯已破损且荷鲁斯之眼不可用", False

        display.show_info("⚔️ 进入战术指令宏模式。依次输入战术动作，输入 terminal 结束。")
        display.show_info(f"   当前 Cost: {self.cost}/{self.max_cost}")
        display.show_info(f"   可用战术：架盾(2) 射击(2) 重新装填(0) 持盾(1) 投掷(1) 服药(0) 冲刺(1) 取消(0) find(1) lock(1) 转向(0) 排弹(0)")

        # 收集指令
        commands = []
        dash_count = 0
        reorder_count = 0
        while True:
            raw = player.controller.get_command(
                player=player,
                game_state=self.state,
                available_actions=list(self.TACTICAL_COST.keys()) + ["terminal"],
                context={"phase": "T0", "situation": "hoshino_tactical_input"}
            )
            raw_stripped = raw.strip()
            raw_lower = raw_stripped.lower()
            if raw_lower == "terminal":
                break

            # 查看类指令（不消耗战术动作，不退出战术模式）
            if raw_lower == "allstatus":
                display.show_all_players_status(self.state)
                continue
            if raw_lower == "status":
                me = self.state.get_player(self.player_id)
                if me:
                    display.show_player_status(me, self.state)
                continue
            if raw_lower == "help":
                display.show_info(
                    "⚔️ 战术指令宏帮助：\n"
                    "  架盾(2) 射击(2) 重新装填(0) 持盾(1) 投掷(1)\n"
                    "  服药(0) 冲刺(1) 取消(0) find(1) lock(1) 转向(0) 排弹(0)\n"
                    "  terminal — 结束输入\n"
                    "  allstatus / status / police — 查看状态（不消耗动作）"
                )
                continue
            if raw_lower == "police":
                display.show_police_status(self.state)
                continue

            parsed = self._parse_tactical_command(raw_stripped)
            if parsed is None:
                display.show_info(f"⚠️ 无法识别的战术指令: {raw_stripped}")
                continue
            action_name, args = parsed
            # 冲刺每宏最多1次
            if action_name == "冲刺":
                dash_count += 1
                if dash_count > 1:
                    display.show_info("⚠️ 每个战术指令宏最多包含1次冲刺")
                    continue
            if action_name == "排弹":
                reorder_count += 1
                if reorder_count > 1:
                    display.show_info("⚠️ 每个战术指令宏最多包含1次排弹")
                    continue
            commands.append((action_name, args))
            cost = self.TACTICAL_COST[action_name]
            display.show_info(f"   ✓ {action_name} {''.join(args)} (cost: {cost})")

        if not commands:
            return "❌ 战术指令宏为空，取消。", False

        # 计算总 cost
        total_cost = sum(self.TACTICAL_COST[cmd] for cmd, _ in commands)
        if total_cost > self.cost:
            display.show_info(f"❌ Cost 不足！需要 {total_cost}，当前 {self.cost}。战术指令宏不执行，返还回合。")
            return f"❌ Cost 不足（需要{total_cost}，当前{self.cost}），战术指令宏取消", False

        # 扣除 cost 并依次执行
        lines = [f"⚔️ 战术指令宏开始执行（总 Cost: {total_cost}）"]
        prev_action = None  # 追踪上一个动作
        for i, (action_name, args) in enumerate(commands):
            cost = self.TACTICAL_COST[action_name]
            self.cost -= cost
            is_last = (i == len(commands) - 1)
            result = self._dispatch_tactical(player, action_name, args, is_last, prev_action=prev_action)
            lines.append(f"  [{i+1}] {action_name}: {result} (剩余Cost: {self.cost})")
            # 架盾/持盾结束检查
            if self.shield_mode and self._should_end_shield(player):
                self._end_shield_mode(player)
                lines.append(f"  ⚠️ 架盾/持盾状态被强制结束")
            prev_action = action_name

        lines.append(f"⚔️ 战术指令宏执行完毕。剩余 Cost: {self.cost}/{self.max_cost}")
        return "\n".join(lines), True  # 消耗回合

    def _dispatch_tactical(self, player, action_name, args, is_last, prev_action=None):
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
            return self._tac_find(player, target_name, prev_action=prev_action)
        elif action_name == "lock":
            target_name = args[0] if args else None
            return self._tac_lock(player, target_name)
        elif action_name == "转向":
            return self._tac_flip(player)
        elif action_name == "排弹":
            return self._tac_reorder(player)
        return "❌ 未知战术动作"

    # ---- 架盾 ----
    def _tac_deploy_shield(self, player):
        if self.iron_horus_hp <= 0:
            return "❌ 铁之荷鲁斯已破损，无法架盾"
        if self.shield_mode == "架盾":
            return "❌ 已处于架盾状态"
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
        return (f"🛡️ 架盾！铁之荷鲁斯护甲值快照: {self.shield_snapshot_hp}\n"
                f"   正面({len(self.front_players)}): {', '.join(front_names) or '无'}\n"
                f"   背面({len(self.back_players)}): {', '.join(back_names) or '无'}")

    # ---- 射击 ----
    def _tac_shoot(self, player, target_name):
        if not self.ammo:
            return "❌ 荷鲁斯之眼没有子弹"
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

        # 解析目标
        from cli.parser import resolve_player_target
        target_id = resolve_player_target(target_name, self.state) if target_name else None
        target = self.state.get_player(target_id) if target_id else None

        if not target or not target.is_alive():
            return "❌ 无效的射击目标"

        # 弹丸分配逻辑（每发3颗弹丸，每颗0.5伤害）
        pellet_damage = 0.5
        results = []

        if mode == "持盾射击":
            # 3颗全部命中 find 的目标（独头弹）
            for _ in range(3):
                r = self._apply_pellet_damage(player, target, pellet_damage, bullet_attr)
                results.append(r)
        elif mode == "架盾射击":
            # 目标至少1颗，剩余2颗随机分配给正面单位
            r = self._apply_pellet_damage(player, target, pellet_damage, bullet_attr)
            results.append(f"{target.name}: {r}")
            front_targets = [self.state.get_player(pid) for pid in self.front_players
                           if pid != target.player_id and self.state.get_player(pid) and self.state.get_player(pid).is_alive()]
            all_front = front_targets + [target]  # target also in front
            for _ in range(2):
                if all_front:
                    t = random.choice(all_front)
                    r = self._apply_pellet_damage(player, t, pellet_damage, bullet_attr)
                    results.append(f"{t.name}: {r}")
        else:
            # 普通射击：目标至少2颗，剩余1颗随机分配给 engaged 单位
            for _ in range(2):
                r = self._apply_pellet_damage(player, target, pellet_damage, bullet_attr)
                results.append(f"{target.name}: {r}")
            # 剩余1颗随机分配
            engaged = [self.state.get_player(pid) for pid in self.state.player_order
                      if pid != player.player_id and self.state.get_player(pid) and self.state.get_player(pid).is_alive()
                      and self.state.markers.has_relation(player.player_id, "ENGAGED_WITH", pid)]
            if engaged:
                t = random.choice(engaged)
                r = self._apply_pellet_damage(player, t, pellet_damage, bullet_attr)
                results.append(f"{t.name}: {r}")
            else:
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
                    extra_msg += " 💀击杀！"
            else:
                for _ in range(3):
                    self._apply_pellet_damage(player, target, pellet_damage, bullet_attr)

        return f"🔫 {mode}（{bullet_attr}属性）→ {'; '.join(results)}{extra_msg}"

    def _apply_pellet_damage(self, player, target, damage, attribute_str):
        """对单个目标施加一颗弹丸伤害"""
        # 警察保护简化：若保护阈值 < 1.5（一发子弹总伤害），忽略保护
        pe = getattr(self.state, 'police_engine', None)
        if pe:
            threshold = pe.get_protection_threshold(target.player_id)
            if threshold > 0 and threshold >= 1.5:
                return f"🚔 警察保护过滤（阈值{threshold}≥1.5）"

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
                    detail_lines.append("    💀 击杀！")
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
            detail_lines.append("    💀 击杀！")
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
                return "❌ 没有可消耗的有属性物品、护甲或武器"
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
            return f"❌ 找不到可消耗的「{item_name}」"

        # 检查弹药容量（在消耗物品之前）
        current_total = len(self.ammo)
        new_bullets = min(4, self.max_ammo - current_total)
        if new_bullets <= 0:
            return f"❌ 弹药已满（{current_total}/{self.max_ammo}），无法装填"

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
        total_after = sum(1 for _ in self.ammo)
        msg = f"🔄 消耗「{item_name}」→ 装填{new_bullets}发{attr_str}子弹（{total_after}/{self.max_ammo}）"
        if overflow > 0:
            msg += f"，{overflow}发溢出弃去"
        return msg

    # ---- 持盾 ----
    def _tac_hold_shield(self, player):
        if self.iron_horus_hp <= 0:
            return "❌ 铁之荷鲁斯已破损，无法持盾"
        if self.shield_mode == "持盾":
            return "❌ 已处于持盾状态"
        self.shield_mode = "持盾"
        # 持盾模式下铁之荷鲁斯作为 priority=100 最外层护甲
        # 实际的伤害减免在 damage_resolver 中通过 modify_incoming_damage 钩子实现
        return f"🛡️ 持盾！铁之荷鲁斯展开（护甲值: {self.iron_horus_hp}）"

    # ---- 投掷 ----
    def _tac_throw(self, player, item_name, location):
        if not self.tactical_items:
            return "❌ 没有战术道具"
        if item_name is None:
            names = [it for it in self.tactical_items]
            item_name = player.controller.choose(
                "选择投掷的道具：", names,
                context={"phase": "T0", "situation": "hoshino_throw_item"}
            )
        if item_name not in self.tactical_items:
            return f"❌ 你没有「{item_name}」"
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

        lines = [f"💣 投掷「{item_name}」→ {location}"]

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
                lines.append(f"  → {t.name}: HP→{r.get('target_hp', '?')} + 脆弱{detail_str}")
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
                lines.append(f"  → {t.name}: ⚡震荡")
            # 警察也受影响
            pe = getattr(self.state, 'police_engine', None)
            if pe and hasattr(self.state, 'police') and self.state.police:
                for unit in self.state.police.units_at(location):
                    if unit.is_alive():
                        unit.is_shocked = True
                        unit.is_stunned = True
                        lines.append(f"  → {unit.unit_id}: ⚡震荡")

        elif effect == "blind":
            # 闪光弹：致盲（持续到下轮R4）
            for t in targets:
                t._hoshino_blinded = True
                t._hoshino_blind_expire_round = self.state.current_round + 1
                lines.append(f"  → {t.name}: 👁️致盲")

        elif effect == "smoke":
            # 烟雾弹：区域烟雾
            if not hasattr(self.state, '_hoshino_smoke_zones'):
                self.state._hoshino_smoke_zones = {}
            self.state._hoshino_smoke_zones[location] = self.state.current_round + 1
            lines.append(f"  → {location} 展开烟雾（持续到下轮R4）")

        elif effect == "burn":
            # 燃烧瓶：2层灼烧（复用g1灼烧逻辑）
            for t in targets:
                if t.talent and hasattr(t.talent, 'apply_burn'):
                    t.talent.apply_burn(2, 0.5)
                else:
                    # 直接设置灼烧属性
                    t._burn_stacks = getattr(t, '_burn_stacks', 0) + 2
                    t._burn_damage_per_stack = 0.5
                lines.append(f"  → {t.name}: 🔥+2层灼烧")

        return "\n".join(lines)

    # ---- 服药 ----
    def _tac_medicine(self, player, med_name):
        if not self.medicines:
            return "❌ 没有药物"
        if med_name is None:
            med_name = player.controller.choose(
                "选择服用的药物：", self.medicines,
                context={"phase": "T0", "situation": "hoshino_medicine"}
            )
        if med_name not in self.medicines:
            return f"❌ 你没有「{med_name}」"

        med_data = MEDICINES.get(med_name, {})
        effect = med_data.get("effect", "")

        if effect == "full_restore" and self.adrenaline_used:
            return "❌ 肾上腺素全局仅能使用1次"

        self.medicines.remove(med_name)

        if effect == "cost_plus_1":
            self.cost = min(self.cost + 1, self.max_cost + 1)  # EPO可以超过max
            return f"💊 EPO！Cost+1 → {self.cost}"
        elif effect == "restore_halo":
            restored = self._halo_restore_one()
            return f"🍫 海豚巧克力！{'恢复1层光环' if restored else '光环已满'}"
        elif effect == "full_restore":
            self.adrenaline_used = True
            self.cost = self.max_cost
            for h in self.halos:
                h['active'] = True
                h['recovering'] = False
                h['cooldown_remaining'] = 0
            return f"💉 肾上腺素！Cost和光环全部回满"
        return f"💊 服用了{med_name}"

    # ---- 冲刺 ----
    def _tac_dash(self, player, dest, is_last):
            """冲刺：消耗1cost，持盾状态下的战术移动"""
            if self.shield_mode != "持盾":
                return "❌ 冲刺需要在持盾状态下"
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
            msg = f"🏃 冲刺到 {dest}"

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
                    msg += f"\n   💥 冲击 {impact_target.name}！⚡震荡"

                    # 自动进入架盾模式
                    self.shield_mode = "架盾"
                    self.shield_snapshot_hp = self.iron_horus_hp
                    self._init_facing(player)  # FacingMixin
                    msg += f"\n   🛡️ 自动架盾！"

                    # 该轮R4不扣cost
                    self.dash_free_shield_cost = True

            return msg

    def _tac_cancel(self, player):
        """取消架盾或持盾状态"""
        if not self.shield_mode:
            return "❌ 当前没有架盾或持盾状态"
        old_mode = self.shield_mode
        self._end_shield_mode(player)
        return f"🔓 取消{old_mode}状态"

    def _tac_find(self, player, target_name, prev_action=None):
        """战术指令宏内的 find"""
        from cli.parser import resolve_player_target
        target_id = resolve_player_target(target_name, self.state) if target_name else None
        if not target_id:
            return "❌ 无效的目标"
        from actions import find_target
        result = find_target.execute(player, target_id, self.state)

        # 通知 FacingMixin（架盾模式下 find 的人归入正面）
        if self.shield_mode == "架盾":
            self._on_find_target(target_id)

        # "肘开23，迎接24"：持盾状态下，冲刺后紧接 find 成功 → 自动冲击+震荡
        if (prev_action == "冲刺" and self.shield_mode == "持盾"
                and "找到了" in result):  # find 成功的标志
            target = self.state.get_player(target_id)
            if target and target.is_alive():
                target.is_shocked = True
                target.is_stunned = True
                self.state.markers.add(target.player_id, "SHOCKED")
                self.state.markers.add(target.player_id, "STUNNED")
                result += f"\n   💥 肘开23，迎接24！冲击 {target.name}！⚡震荡"
                # 注意：这里不需要额外添加 engage_with，因为 find 已经建立了

        return result

    def _tac_lock(self, player, target_name):
        """战术指令宏内的 lock"""
        from cli.parser import resolve_player_target
        target_id = resolve_player_target(target_name, self.state) if target_name else None
        if not target_id:
            return "❌ 无效的目标"
        from actions import lock_target
        result = lock_target.execute(player, target_id, self.state)
        return result

    def _tac_flip(self, player):
        """转向：正面↔背面互换"""
        if self.shield_mode != "架盾":
            return "❌ 转向需要在架盾状态下"
        self._flip_facing()  # FacingMixin
        return f"🔄 转向！正面{len(self.front_players)}人，背面{len(self.back_players)}人"

    def _tac_reorder(self, player):
        """排弹：重新排列弹匣内子弹顺序（每宏限1次）"""
        if not self.ammo:
            return "❌ 弹匣为空，无需排弹"
        if len(self.ammo) == 1:
            return "❌ 弹匣只有1发子弹，无需排弹"

        # 显示当前弹匣
        current_display = " ".join(
            f"[{i+1}]{b.get('attribute', '普通')}" for i, b in enumerate(self.ammo)
        )
        display.show_info(f"  当前弹匣: {current_display}")

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
            return "❌ 输入格式错误，请输入数字序列（如: 2 1 3 4）"

        # 验证：必须是当前弹匣长度的完整排列
        n = len(self.ammo)
        if sorted(indices) != list(range(n)):
            return f"❌ 请输入 1~{n} 的完整排列（如: {' '.join(str(i+1) for i in range(n))}）"

        # 执行重排
        new_ammo = [self.ammo[i] for i in indices]
        self.ammo = new_ammo

        new_display = " ".join(
            f"[{i+1}]{b.get('attribute', '普通')}" for i, b in enumerate(self.ammo)
        )
        return f"🔄 弹匣重排: {new_display}"

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
        # 水着-shielder：架盾cost降为1
        deduct = 1 if self.form == "水着-shielder" else 2
        if self.cost >= deduct:
            self.cost -= deduct
        else:
            # cost不足，架盾立刻结束
            player = self.state.get_player(self.player_id)
            if player:
                self._end_shield_mode(player)
                from cli import display
                display.show_info(f"⚠️ {player.name} Cost不足，架盾状态结束")

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

