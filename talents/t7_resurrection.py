"""
天赋7：死者苏生（原初）+ Controller 接入
需要学习：在魔法所花2个行动回合学习。
学完后花1行动回合挂载到目标玩家。
目标死亡时（在免死效果之后结算）：
  保留物品 + 在家重生 + 不用起床。
全场只能挂1人。全局1次。
"""

from talents.base_talent import BaseTalent
from cli import display
from engine.prompt_manager import prompt_manager


class Resurrection(BaseTalent):
    name = "死者苏生"
    description = "学习后挂载给目标玩家。目标死亡时在家重生。"
    tier = "原初"

    def __init__(self, player_id, game_state):
        super().__init__(player_id, game_state)
        self.learned = False
        self.learn_progress = 0
        self.mounted_on = None
        self.used = False

    def get_t0_option(self, player):
        if self.used:
            return None

        if not self.learned:
            if player.location == "魔法所":
                return {
                    "name": "学习死者苏生",
                    "description": f"在魔法所学习（进度：{self.learn_progress}/2）",
                }
            return None

        if self.mounted_on is None:
            return {
                "name": "挂载死者苏生",
                "description": "选择一名玩家挂载死者苏生效果",
            }

        return None

    def execute_t0(self, player):
        if self.used:
            return "❌ 死者苏生已使用", False

        # 学习阶段
        if not self.learned:
            if player.location != "魔法所":
                return "❌ 需要在魔法所学习", False

            self.learn_progress += 1
            if self.learn_progress < 2:
                return (prompt_manager.get_prompt(
                    "talent", "t7resurrection.learning_progress",
                    default="📖 {player_name} 正在学习「死者苏生」（进度：{progress}/2）"
                ).format(player_name=player.name, progress=self.learn_progress)), True

            self.learned = True
            return (prompt_manager.get_prompt(
                "talent", "t7resurrection.learning_complete",
                default="✨ {player_name} 学会了「死者苏生」！\n   下次行动回合可挂载到目标玩家身上。"
            ).format(player_name=player.name)), True

        # 挂载阶段
        if self.mounted_on is None:
            targets = [p for p in self.state.alive_players()]
            if not targets:
                return "❌ 没有可挂载的目标", False

            # ══ CONTROLLER 改动：选挂载目标 ══
            names = [p.name for p in targets]
            choice = player.controller.choose(
                "选择挂载「死者苏生」的目标：", names,
                context={"phase": "T0", "situation": "resurrection_pick_target"}
            )
            target = next(p for p in targets if p.name == choice)
            # ══ CONTROLLER 改动结束 ══

            self.mounted_on = target.player_id
            return (prompt_manager.get_prompt(
                "talent", "t7resurrection.mount_success",
                default="🔮 {player_name} 将「死者苏生」挂载到 {target_name} 身上！\n   当 {target_name} 死亡时，将在家中重生。"
            ).format(player_name=player.name, target_name=target.name)), True

        return "❌ 死者苏生已挂载", False

    def on_death_check(self, dying_player, damage_source):
        if self.used:
            return None
        if self.mounted_on is None:
            return None
        if dying_player.player_id != self.mounted_on:
            return None

        self.used = True
        home_id = f"home_{dying_player.player_id}"

        dying_player.location = home_id
        dying_player.is_awake = True

        # 清除所有markers关系（类似死亡清理，但玩家实际未死亡）
        # 清除LOCKED_BY、ENGAGED_WITH等状态
        self.state.markers.on_player_death(dying_player.player_id)

        resurrection_msg = prompt_manager.get_prompt(
            "talent", "t7resurrection.resurrection_trigger",
            default="🌟 死者苏生触发！{player_name} 在家中重生！\n   保留所有物品，无需起床。"
        ).format(player_name=dying_player.name)
        display.show_info(resurrection_msg)

        return {"prevent_death": True, "new_hp": 1.0}

    def describe_status(self):
        if self.used:
            return "已使用（永久失效）"
        if not self.learned:
            return f"未学习（进度：{self.learn_progress}/2，需在魔法所）"
        if self.mounted_on is None:
            return "已学会，未挂载"
        target = self.state.get_player(self.mounted_on)
        name = target.name if target else self.mounted_on
        return f"已挂载到 {name}"