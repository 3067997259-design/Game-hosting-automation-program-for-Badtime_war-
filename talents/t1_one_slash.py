"""
天赋1：一刀缭断（原初）
主动，1次使用，消耗行动回合。
T0启动，选任意近战武器+面对面目标。
本次攻击：伤害+100%，无视属性克制，最后内层不吸收溢出。
"""

from talents.base_talent import BaseTalent
from cli import display


class OneSlash(BaseTalent):
    name = "一刀缭断"
    description = "发动一次近战攻击：伤害×2，无视属性克制。"
    tier = "原初"

    def __init__(self, player_id, game_state):
        super().__init__(player_id, game_state)
        self.uses_remaining = 1

    def get_t0_option(self, player):
        if self.uses_remaining <= 0:
            return None
        # 检查是否有面对面目标
        engaged = self.state.markers.get_related(player.player_id, "ENGAGED_WITH")
        if not engaged:
            return None
        # 检查是否有近战武器
        from models.equipment import WeaponRange
        melee = [w for w in player.weapons if w.weapon_range == WeaponRange.MELEE]
        if not melee:
            return None
        return {
            "name": "一刀缭断",
            "description": f"发动一次NB的近战攻击（伤害×2+无视克制）。剩余{self.uses_remaining}次",
        }

    def execute_t0(self, player):
        """执行一刀缭断"""
        if self.uses_remaining <= 0:
            return "❌ 一刀缭断已用完", False

        # 选择近战武器
        from models.equipment import WeaponRange
        melee = [w for w in player.weapons if w.weapon_range == WeaponRange.MELEE]
        if not melee:
            return "❌ 你没有近战武器", False

        if len(melee) == 1:
            weapon = melee[0]
        else:
            names = [w.name for w in melee]
            choice = display.prompt_choice("选择使用的近战武器：", names)
            weapon = next(w for w in melee if w.name == choice)

        # 选择面对面目标
        engaged = self.state.markers.get_related(player.player_id, "ENGAGED_WITH")
        valid_targets = []
        for eid in engaged:
            ep = self.state.get_player(eid)
            if ep and ep.is_alive() and ep.location == player.location:
                valid_targets.append(ep)

        if not valid_targets:
            return "❌ 没有可攻击的面对面目标", False

        if len(valid_targets) == 1:
            target = valid_targets[0]
        else:
            names = [t.name for t in valid_targets]
            choice = display.prompt_choice("选择攻击目标：", names)
            target = next(t for t in valid_targets if t.name == choice)

        # 执行特殊攻击
        self.uses_remaining -= 1

        from combat.damage_resolver import resolve_damage
        result = resolve_damage(
            attacker=player,
            target=target,
            weapon=weapon,
            game_state=self.state,
            damage_multiplier=2.0,
            ignore_counter=True,
            ignore_last_inner_absorb=True,
        )

        lines = [f"⚔️ 一刀缭断！{player.name} 用「{weapon.name}」斩向 {target.name}！"]
        lines.append(f"   （伤害×2 + 无视属性克制 + 无视最后内层吸收）")
        for detail in result.get("details", []):
            lines.append(f"   {detail}")

        if result.get("killed"):
            player.kill_count += 1
            self.state.markers.on_player_death(target.player_id)
            display.show_death(target.name, f"被 {player.name} 的一刀缭断击杀")

        msg = "\n".join(lines)
        return msg, True  # 消耗行动回合

    def describe_status(self):
        return f"剩余次数：{self.uses_remaining}"
