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
from combat.damage_resolver import resolve_location_damage


class Star(BaseTalent):
    name = "天星"
    description = "对同地点所有单位造成动态无视克制伤害+石化。"
    tier = "原初"

    def __init__(self, player_id, game_state):
        super().__init__(player_id, game_state)
        self.uses_remaining = 2

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

        # 对警察造成伤害+石化（ver1.9：直接遍历PoliceUnit扁平列表）
        for unit in police_at_loc:
            if not unit.is_alive():
                continue

            # [Issue 1] 走护甲结算（天星无视属性克制）
            old_hp = unit.hp
            pe = getattr(self.state, 'police_engine', None)
            if pe:
                pe._resolve_attack_on_police(
                    weapon=None, unit=unit,
                    raw_damage_override=damage_per_target, ignore_counter=True,
                    attacker=player
                )
                unit.last_attacker_id = player.player_id
            else:
                unit.take_damage(1.0, attacker_id=player.player_id)

            if unit.hp <= 0:
                police_death_msg = prompt_manager.get_prompt(
                    "talent", "t3star.police_death",
                    default="   → 警察{unit_id} 受到{damage:.1f}伤害+石化 HP: {old_hp} → {new_hp}",
                    unit_id=unit.unit_id, damage=damage_per_target, old_hp=old_hp, new_hp=unit.hp)

                lines.append(police_death_msg)
            else:
                # 存活则施加石化
                unit.is_petrified = True
                police_damage_msg = prompt_manager.get_prompt(
                    "talent", "t3star.police_damage",
                    default="   \u2192 警察{unit_id} 受到{damage:.1f}伤害+石化 HP: {old_hp} \u2192 {new_hp}",
                    unit_id=unit.unit_id, damage=damage_per_target, old_hp=old_hp, new_hp=unit.hp)
                lines.append(police_damage_msg)

        msg = "\n".join(lines)
        self.state.log_event("star", player=player.player_id)
        return msg, True  # 消耗行动回合

    def describe_status(self):
        return f"剩余次数：{self.uses_remaining}"
