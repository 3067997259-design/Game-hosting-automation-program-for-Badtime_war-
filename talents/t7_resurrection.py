"""
天赋7：死者苏生（原初）
需要学习：在魔法所花2个行动回合学习。
学完后花1行动回合挂载到目标玩家。
目标死亡时（在免死效果之后结算）：
  保留物品 + 在家重生 + 不用起床。
全场只能挂1人。全局1次。
"""

from talents.base_talent import BaseTalent
from cli import display


class Resurrection(BaseTalent):
    name = "死者苏生"
    description = "学习后挂载给目标玩家。目标死亡时在家重生。"
    tier = "原初"

    def __init__(self, player_id, game_state):
        super().__init__(player_id, game_state)
        self.learned = False
        self.learn_progress = 0     # 学习进度（需2回合）
        self.mounted_on = None      # 挂载目标player_id
        self.used = False           # 是否已触发过

    def get_t0_option(self, player):
        """T0可用选项：学习 或 挂载"""
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
                return (f"📖 {player.name} 正在学习「死者苏生」"
                        f"（进度：{self.learn_progress}/2）"), True

            # 学习完成
            self.learned = True
            return (f"✨ {player.name} 学会了「死者苏生」！"
                    f"\n   下次行动回合可挂载到目标玩家身上。"), True

        # 挂载阶段
        if self.mounted_on is None:
            targets = [p for p in self.state.alive_players()]
            if not targets:
                return "❌ 没有可挂载的目标", False

            names = [p.name for p in targets]
            choice = display.prompt_choice("选择挂载「死者苏生」的目标：", names)
            target = next(p for p in targets if p.name == choice)

            self.mounted_on = target.player_id
            return (f"🔮 {player.name} 将「死者苏生」挂载到 {target.name} 身上！"
                    f"\n   当 {target.name} 死亡时，将在家中重生。"), True

        return "❌ 死者苏生已挂载", False

    def on_death_check(self, dying_player, damage_source):
        """
        死亡判定钩子（优先级最低——在所有免死效果之后）。
        只对挂载目标生效。
        """
        if self.used:
            return None
        if self.mounted_on is None:
            return None
        if dying_player.player_id != self.mounted_on:
            return None

        # 触发复活
        self.used = True
        home_id = f"home_{dying_player.player_id}"

        # 保留物品（不包括已破碎护盾——破碎的已经被移除了）
        # 重置位置
        dying_player.location = home_id
        dying_player.is_awake = True

        display.show_info(
            f"🌟 死者苏生触发！{dying_player.name} 在家中重生！"
            f"\n   保留所有物品，无需起床。")

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
