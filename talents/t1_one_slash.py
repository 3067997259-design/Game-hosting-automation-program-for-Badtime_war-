"""
天赋1：一刀缭断（原初）+ Controller 接入
主动，2次使用，消耗行动回合。
T0启动，选任意近战武器+面对面目标。
本次攻击：伤害+100%，无视属性克制，最后内层不吸收溢出。
"""

from talents.base_talent import BaseTalent, PromptLevel
from engine.prompt_manager import prompt_manager


class OneSlash(BaseTalent):
    name = "一刀缭断"
    description = "发动近战攻击：伤害×2，无视属性克制。共2次。"
    tier = "原初"

    def __init__(self, player_id, game_state):
        super().__init__(player_id, game_state)
        self.max_uses = 2
        self.uses_remaining = self.max_uses

    @property
    def uses_left(self):
        return self.uses_remaining

    @uses_left.setter
    def uses_left(self, value):
        self.uses_remaining = value

    def get_t0_option(self, player):
        if self.uses_remaining <= 0:
            return None
        engaged = self.state.markers.get_related(player.player_id, "ENGAGED_WITH")
        if not engaged:
            return None
        from models.equipment import WeaponRange
        melee = [w for w in player.weapons if w.weapon_range == WeaponRange.MELEE]
        if not melee:
            return None
        return {
            "name": "一刀缭断",
            "description": f"发动一次NB的近战攻击（伤害×2+无视克制）。剩余{self.uses_remaining}次",
        }

    def execute_t0(self, player):
        if self.uses_remaining <= 0:
            return prompt_manager.get_prompt("error", "action_failed",
                                           reason="一刀缭断已用完", default="❌ 一刀缭断已用完"), False

        # 选择近战武器
        from models.equipment import WeaponRange
        melee = [w for w in player.weapons if w.weapon_range == WeaponRange.MELEE]
        if not melee:
            return prompt_manager.get_prompt("error", "action_failed",
                                           reason="没有近战武器", default="❌ 你没有近战武器"), False

        if len(melee) == 1:
            weapon = melee[0]
        else:
            # ══ CONTROLLER 改动 1：选武器 ══
            names = [w.name for w in melee]
            choice = player.controller.choose(
                "选择使用的近战武器：", names,
                context={"phase": "T0", "situation": "oneslash_pick_weapon"}
            )
            weapon = next(w for w in melee if w.name == choice)
            # ══ CONTROLLER 改动 1 结束 ══

        # 选择面对面目标
        engaged = self.state.markers.get_related(player.player_id, "ENGAGED_WITH")
        valid_targets = []
        for eid in engaged:
            ep = self.state.get_player(eid)
            if ep and ep.is_alive() and ep.location == player.location:
                valid_targets.append(ep)

        if not valid_targets:
            return prompt_manager.get_prompt("error", "action_failed",
                                           reason="没有可攻击的面对面目标",
                                           default="❌ 没有可攻击的面对面目标"), False

        if len(valid_targets) == 1:
            target = valid_targets[0]
        else:
            # ══ CONTROLLER 改动 2：选目标 ══
            names = [t.name for t in valid_targets]
            choice = player.controller.choose(
                "选择攻击目标：", names,
                context={"phase": "T0", "situation": "oneslash_pick_target"}
            )
            target = next(t for t in valid_targets if t.name == choice)
            # ══ CONTROLLER 改动 2 结束 ══

        # 执行特殊攻击
        self.uses_remaining -= 1

        # 显示激活提示
        self.show_activation(player.name)

        from combat.damage_resolver import resolve_damage
        result = resolve_damage(
            attacker=player,
            target=target,
            weapon=weapon,
            game_state=self.state,
            damage_multiplier=2.0,
            ignore_counter=True,
            ignore_last_inner_absorb=True,
            is_talent_attack=True,
        )

        # ── RL 事件日志 ──
        self.state.log_event("oneslash_attack", player=player.player_id,
                             target=target.player_id,
                             weapon=weapon.name,
                             damage=result.get("final_damage", 0),
                             killed=result.get("killed", False),
                             uses_remaining=self.uses_remaining)

        # 构建攻击结果消息
        attack_msg = prompt_manager.get_prompt("talent", "t1oneslash.attack",
                                              default="⚔️ 一刀缭断！{player_name} 用「{weapon_name}」斩向 {target_name}！",
                                              player_name=player.name,
                                              weapon_name=weapon.name,
                                              target_name=target.name)

        effect_msg = prompt_manager.get_prompt("talent", "t1oneslash.effect",
                                              default="   （伤害×2 + 无视属性克制 + 无视最后内层吸收）")

        lines = [attack_msg, effect_msg]
        for detail in result.get("details", []):
            lines.append(f"   {detail}")

        if result.get("killed"):
            player.kill_count += 1
            self.state.markers.on_player_death(target.player_id)
            # 使用游戏中的死亡提示
            prompt_manager.show("game", "death",
                               player_name=target.name,
                               cause=f"被 {player.name} 的一刀缭断击杀",
                               level=PromptLevel.CRITICAL)

        msg = "\n".join(lines)
        return msg, True

    def describe_status(self):
        return f"剩余次数：{self.uses_remaining}/{self.max_uses}"