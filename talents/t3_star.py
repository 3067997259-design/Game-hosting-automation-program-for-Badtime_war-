"""
天赋3：天星（原初）
主动，2次使用，消耗行动回合。
T0启动：对同地点所有其他单位造成动态伤害（无视属性克制）+ 全体石化。
V1.92 动态伤害公式：min(1 + 0.5 * 命中单位数, 3)
石化：下回合二选一（解除受0.5伤害 / 保持）。
被攻击时石化自动解除（+0.5伤害）。
非玩家单位不能自行解除，需其他玩家花1回合解除。
"""

from talents.base_talent import BaseTalent, PromptLevel
from engine.prompt_manager import prompt_manager
from combat.damage_resolver import resolve_location_damage, resolve_damage


class Star(BaseTalent):
    name = "天星"
    description = "对同地点所有单位造成动态无视克制伤害+石化。"
    tier = "原初"

    def __init__(self, player_id, game_state):
        super().__init__(player_id, game_state)
        self.uses_remaining = 2
        self.ripple_enhanced = False  # 涟漪增强标记
        self.ripple_petrify_lock = False # 献诗增强：石化不因被攻击解除

    def get_t0_option(self, player):
        if self.uses_remaining <= 0:
            return None
        # 检查同地点有没有其他单位
        others = [p for p in self.state.players_at_location(player.location)
                  if p.player_id != player.player_id and p.is_alive()]
        if not others:
            return None
        return {
            "name": "天星",
            "description": f"对同地点所有单位造成动态伤害+石化。剩余{self.uses_remaining}次",
        }
    def enhance_by_ripple(self):
        """献予群星之诗：额外2次0.5伤害 + 石化不因被攻击解除"""
        self.ripple_enhanced = True
        self.ripple_petrify_lock = True  # 石化不因被攻击解除

    def execute_t0(self, player):
        if self.uses_remaining <= 0:
            return prompt_manager.get_prompt("error", "action_failed",
                                           reason="天星已用完",
                                           default="\u274c 天星已用完"), False

        self.uses_remaining -= 1

        # 收集目标，用于计算动态伤害
        targets = [p for p in self.state.players_at_location(player.location)
                   if p.player_id != player.player_id and p.is_alive()]
        police_at_loc = []
        if hasattr(self.state, 'police') and self.state.police:
            police_at_loc = [u for u in self.state.police.units_at(player.location)
                             if u.is_alive()]

        # V1.92: 动态伤害公式 = min(1 + 0.5 * 命中单位数, 3)
        target_count = len(targets) + len(police_at_loc)
        damage_per_target = min(1.0 + 0.5 * target_count, 3.0)

        # 记录伤害前的 HP（resolve_location_damage 内部会直接扣血）
        player_old_hp = {}
        for t in targets:
            player_old_hp[t.player_id] = t.hp
        police_old_hp = {}
        for u in police_at_loc:
            police_old_hp[u.unit_id] = u.hp

        # 显示激活提示
        self.show_activation(player.name)

        lines = [prompt_manager.get_prompt("talent", "t3star.activate",
                                          default="\u2b50 {player_name} 发动「天星」！天幕降落！",
                                          player_name=player.name)]

        # 使用 resolve_location_damage 统一处理玩家+警察伤害
        results_dict = resolve_location_damage(
            attacker=player, location=player.location,
            game_state=self.state, raw_damage=damage_per_target,
            ignore_counter=True, exclude_self=True,
            damage_attribute_override="无视属性克制",
            is_talent_attack=True,
        )

        # 处理玩家结果：伤害 + 石化
        for r in results_dict.get("players", []):
            t = r["target"]
            result = r["result"]
            old_hp = player_old_hp.get(t.player_id, 0)

            damage_msg = prompt_manager.get_prompt("talent", "t3star.damage",
                                                   default="   → {target_name} 受到 {damage:.1f} 点伤害（无视克制） HP: {old_hp} → {new_hp}",
                                                  target_name=t.name, damage=damage_per_target, old_hp=old_hp, new_hp=t.hp)
            lines.append(damage_msg)

            for detail in result.get("details", []):
                lines.append(f"      {detail}")

            # 石化（存活单位）
            if not result.get("killed", False):
                # 六爻·元亨利贞：免疫石化
                if (t.talent and hasattr(t.talent, 'is_immune_to_debuff')
                    and not getattr(t, '_mythland_talent_suppressed', False)
                    and t.talent.is_immune_to_debuff("petrify")):
                    lines.append(f"   ☯️ {t.name} 的「元亨利贞」免疫了石化！")
                else:
                    self.state.markers.add(t.player_id, "PETRIFIED")
                    t.is_petrified = True
                    petrify_msg = prompt_manager.get_prompt("talent", "t3star.petrify",
                                                       default="   \u2192 {target_name} 进入石化状态 \U0001f5ff",
                                                       target_name=t.name)
                    lines.append(petrify_msg)

            # 击杀 / 眩晕
            if result.get("killed", False):
                player.kill_count += 1
                self.state.markers.on_player_death(t.player_id)
                death_msg = prompt_manager.get_prompt("talent", "t3star.death",
                                                     default="   \U0001f480 {target_name} 被天星击杀！",
                                                     target_name=t.name)
                lines.append(death_msg)
            elif result.get("stunned", False):
                stun_msg = prompt_manager.get_prompt("talent", "t3star.stun",
                                                    default="   \U0001f4ab {target_name} 进入眩晕状态！",
                                                    target_name=t.name)
                lines.append(stun_msg)

        # 处理警察结果：伤害 + 石化（resolve_location_damage 已完成伤害结算 + check_all_dead）
        for unit in results_dict.get("police", []):
            old_hp = police_old_hp.get(unit.unit_id, 0)

            if unit.hp <= 0:
                police_death_msg = prompt_manager.get_prompt(
                    "talent", "t3star.police_death",
                    default="   \u2192 警察{unit_id} 被天星击杀！HP: {old_hp} \u2192 {new_hp}",
                    unit_id=unit.unit_id, old_hp=old_hp, new_hp=unit.hp)
                lines.append(police_death_msg)
            else:
                # 存活则施加石化
                unit.is_petrified = True
                police_damage_msg = prompt_manager.get_prompt(
                    "talent", "t3star.police_damage",
                    default="   \u2192 警察{unit_id} 受到{damage:.1f}伤害+石化 HP: {old_hp} \u2192 {new_hp}",
                    unit_id=unit.unit_id, damage=damage_per_target, old_hp=old_hp, new_hp=unit.hp)
                lines.append(police_damage_msg)

        # ===== 涟漪增强：额外弹射伤害 =====
        if self.ripple_enhanced:
            bounce_count = getattr(self, 'ripple_bounce_count', 2)
            lines.append(f"\n   ⭐🌊 涟漪增强：额外指定{bounce_count}次目标，各造成0.5无视属性伤害！")

            # 收集同地点存活的可选目标（发动者以外的玩家+警察）
            bounce_player_targets = [p for p in self.state.players_at_location(player.location)
                                     if p.player_id != player.player_id and p.is_alive()]
            bounce_police_targets = []
            if hasattr(self.state, 'police') and self.state.police:
                bounce_police_targets = [u for u in self.state.police.units_at(player.location)
                                         if u.is_alive()]

            all_target_names = [p.name for p in bounce_player_targets]
            all_target_names += [f"警察{u.unit_id}" for u in bounce_police_targets]

            if all_target_names:
                from combat.damage_resolver import resolve_damage
                for i in range(bounce_count):
                    chosen_name = player.controller.choose(
                        f"涟漪弹射第{i+1}次目标：",
                        all_target_names,
                        context={"phase": "T0", "situation": "star_ripple_bounce", "bounce_index": i}
                    )

                    # 找到目标并造成0.5伤害
                    target_obj = None
                    is_police_target = False
                    for p in bounce_player_targets:
                        if p.name == chosen_name:
                            target_obj = p
                            break
                    if not target_obj:
                        for u in bounce_police_targets:
                            if f"警察{u.unit_id}" == chosen_name:
                                target_obj = u
                                is_police_target = True
                                break

                    if target_obj and not is_police_target:
                        old_hp = target_obj.hp
                        result = resolve_damage(
                            attacker=player, target=target_obj, weapon=None,
                            game_state=self.state,
                            raw_damage_override=0.5,
                            damage_attribute_override="无视属性克制",
                            ignore_counter=True,
                            is_talent_attack=True,
                        )
                        lines.append(f"   ⭐🌊 弹射→ {target_obj.name} 受到0.5伤害 HP: {old_hp} → {target_obj.hp}")
                        if result.get("killed", False):
                            player.kill_count += 1
                            self.state.markers.on_player_death(target_obj.player_id)
                            lines.append(f"   💀 {target_obj.name} 被弹射击杀！")
                    elif target_obj and is_police_target:
                        old_hp = target_obj.hp
                        target_obj.take_damage(0.5, attacker_id=player.player_id)
                        lines.append(f"   ⭐🌊 弹射→ 警察{target_obj.unit_id} 受到0.5伤害 HP: {old_hp} → {target_obj.hp}")
                        if target_obj.hp <= 0:
                            self.state.police.check_all_dead()
                            lines.append(f"   💀 警察{target_obj.unit_id} 被弹射击杀！")

        msg = "\n".join(lines)
        self.state.log_event("star_attack", player=player.player_id)
        return msg, True  # 消耗行动回合

    def describe_status(self):
        status = f"剩余次数：{self.uses_remaining}"
        if self.ripple_enhanced:
            status += " | 涟漪增强（弹射+石化锁定）"
        return status
