"""
天赋3：天星（原初）
主动，1次使用，消耗行动回合。
T0启动：对同地点所有其他单位造成1点伤害（无视属性克制）+ 全体石化。
石化：下回合二选一（解除受0.5伤害 / 保持）。
被攻击时石化自动解除（+0.5伤害）。
非玩家单位不能自行解除，需其他玩家花1回合解除。
"""

from talents.base_talent import BaseTalent
from cli import display


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
            return "❌ 天星已用完", False

        self.uses_remaining -= 1

        targets = [p for p in self.state.players_at_location(player.location)
                   if p.player_id != player.player_id and p.is_alive()]

        # 同地点的警察也算目标（非玩家单位）
        police_at_loc = []
        if hasattr(self.state, 'police'):
            police_at_loc = self.state.police.all_teams_at(player.location)

        lines = [f"⭐ {player.name} 发动「天星」！天幕降落！"]

        # 对每个玩家造成1点伤害（无视克制）
        for t in targets:
            old_hp = t.hp
            t.hp = max(0, t.hp - 1.0)
            lines.append(f"   → {t.name} 受到 1.0 点伤害（无视克制）"
                         f"HP: {old_hp} → {t.hp}")

            # 石化
            self.state.markers.add(t.player_id, "PETRIFIED")
            t.is_petrified = True
            lines.append(f"   → {t.name} 进入石化状态 🗿")

            # 死亡检查
            if t.hp <= 0:
                # 先检查免死天赋
                prevented = False
                if t.talent:
                    death_result = t.talent.on_death_check(t, player)
                    if death_result and death_result.get("prevent_death"):
                        t.hp = death_result.get("new_hp", 0)
                        prevented = True
                        lines.append(f"   → {t.name} 的死亡被天赋阻止！")

                if not prevented and t.hp <= 0:
                    player.kill_count += 1
                    self.state.markers.on_player_death(t.player_id)
                    lines.append(f"   💀 {t.name} 被天星击杀！")
            elif t.hp <= 0.5 and not t.is_stunned:
                t.is_stunned = True
                self.state.markers.add(t.player_id, "STUNNED")
                lines.append(f"   💫 {t.name} 进入眩晕状态！")

        # 对警察造成伤害+石化
        for team in police_at_loc:
            for cop in team.get_active_members():
                cop.hp = max(0, cop.hp - 1.0)
                if cop.hp <= 0:
                    lines.append(f"   → 警察{cop.unit_id} 被天星击杀！")
                else:
                    cop.is_stunned = True
                    lines.append(f"   → 警察{cop.unit_id} 受到1.0伤害+石化")
                # 警察石化标记（简化处理：设为眩晕+石化标记）

        msg = "\n".join(lines)
        self.state.log_event("star", player=player.player_id)
        return msg, True  # 消耗行动回合

    def describe_status(self):
        return f"剩余次数：{self.uses_remaining}"
