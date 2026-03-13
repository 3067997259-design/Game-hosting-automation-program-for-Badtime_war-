"""
天赋3：天星（原初）
主动，1次使用，消耗行动回合。
T0启动：对同地点所有其他单位造成1点伤害（无视属性克制）+ 全体石化。
石化：下回合二选一（解除受0.5伤害 / 保持）。
被攻击时石化自动解除（+0.5伤害）。
非玩家单位不能自行解除，需其他玩家花1回合解除。
"""

from talents.base_talent import BaseTalent, PromptLevel
from engine.prompt_manager import prompt_manager
from combat.damage_resolver import resolve_damage


class Star(BaseTalent):
    name = "天星"
    description = "对同地点所有单位造成1点无视克制伤害+石化。"
    tier = "原初"

    def __init__(self, player_id, game_state):
        super().__init__(player_id, game_state)
        self.uses_remaining = 1

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
            "description": f"对同地点所有单位造成1点伤害+石化。剩余{self.uses_remaining}次",
        }

    def execute_t0(self, player):
        if self.uses_remaining <= 0:
            return prompt_manager.get_prompt("error", "action_failed",
                                           reason="天星已用完",
                                           default="❌ 天星已用完"), False

        self.uses_remaining -= 1

        targets = [p for p in self.state.players_at_location(player.location)
                   if p.player_id != player.player_id and p.is_alive()]

        # 同地点的警察也算目标（非玩家单位）
        police_at_loc = []
        if hasattr(self.state, 'police'):
            police_at_loc = self.state.police.all_teams_at(player.location)

        # 显示激活提示
        self.show_activation(player.name)

        lines = [prompt_manager.get_prompt("talent", "t3star.activate",
                                          default="⭐ {player_name} 发动「天星」！天幕降落！",
                                          player_name=player.name)]

        # 对每个玩家造成1点伤害（无视克制）- 使用damage_resolver
        for t in targets:
            old_hp = t.hp
            
            # 使用resolve_damage处理伤害
            result = resolve_damage(
                attacker=player,
                target=t,
                weapon=None,  # 无武器
                game_state=self.state,
                raw_damage_override=1.0,
                damage_attribute_override="无视属性克制",  # 对应伤害类型
                ignore_counter=True,  # 无视属性克制
            )
            
            damage_msg = prompt_manager.get_prompt("talent", "t3star.damage",
                                                  default="   → {target_name} 受到 1.0 点伤害（无视克制） HP: {old_hp} → {new_hp}",
                                                  target_name=t.name, old_hp=old_hp, new_hp=t.hp)
            lines.append(damage_msg)
            
            # 添加伤害结算详情
            for detail in result.get("details", []):
                lines.append(f"      {detail}")

            # 石化
            self.state.markers.add(t.player_id, "PETRIFIED")
            t.is_petrified = True
            
            petrify_msg = prompt_manager.get_prompt("talent", "t3star.petrify",
                                                   default="   → {target_name} 进入石化状态 🗿",
                                                   target_name=t.name)
            lines.append(petrify_msg)

            # 检查击杀和眩晕（通过result获取）
            if result.get("killed", False):
                player.kill_count += 1
                self.state.markers.on_player_death(t.player_id)
                death_msg = prompt_manager.get_prompt("talent", "t3star.death",
                                                     default="   💀 {target_name} 被天星击杀！",
                                                     target_name=t.name)
                lines.append(death_msg)
            elif result.get("stunned", False):
                stun_msg = prompt_manager.get_prompt("talent", "t3star.stun",
                                                    default="   💫 {target_name} 进入眩晕状态！",
                                                    target_name=t.name)
                lines.append(stun_msg)

        # 对警察造成伤害+石化（警察使用简化处理）
        for team in police_at_loc:
            for cop in team.get_active_members():
                cop.hp = max(0, cop.hp - 1.0)
                if cop.hp <= 0:
                    police_death_msg = prompt_manager.get_prompt("talent", "t3star.police_death",
                                                                default="   → 警察{cop_id} 被天星击杀！",
                                                                cop_id=cop.unit_id)
                    lines.append(police_death_msg)
                else:
                    cop.is_stunned = True
                    police_damage_msg = prompt_manager.get_prompt("talent", "t3star.police_damage",
                                                                 default="   → 警察{cop_id} 受到1.0伤害+石化",
                                                                 cop_id=cop.unit_id)
                    lines.append(police_damage_msg)
                # 警察石化标记（简化处理：设为眩晕+石化标记）

        msg = "\n".join(lines)
        self.state.log_event("star", player=player.player_id)
        return msg, True  # 消耗行动回合

    def describe_status(self):
        return f"剩余次数：{self.uses_remaining}"