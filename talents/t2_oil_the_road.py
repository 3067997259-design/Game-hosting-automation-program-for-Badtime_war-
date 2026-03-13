"""
天赋2：你给路打油（原初）
全局最多2次。
R3期间，当你认为某玩家行动可能导致你死亡时声明。
不消耗行动回合，立刻获得1个额外行动回合（插入在下一胜者之前）。
同地点最多触发1次。
"""

from talents.base_talent import BaseTalent, PromptLevel
from engine.prompt_manager import prompt_manager


class OilTheRoad(BaseTalent):
    name = "你给路打油"
    description = "R3期间可声明获得额外行动回合。全局2次，每地点1次。"
    tier = "原初"

    def __init__(self, player_id, game_state):
        super().__init__(player_id, game_state)
        self.uses_remaining = 2
        self.triggered_locations = set()  # 已触发过的地点

    def check_response_window(self, actor, action_type):
        """R3期间每个胜者行动后检查"""
        if self.uses_remaining <= 0:
            return False

        me = self.state.get_player(self.player_id)
        if not me or not me.is_alive() or not me.is_on_map():
            return False

        # 不响应自己的行动
        if actor.player_id == self.player_id:
            return False

        # 同地点限制
        if me.location in self.triggered_locations:
            return False

        return True

    def execute_response(self, player):
        """执行：获得额外行动回合"""
        self.uses_remaining -= 1
        self.triggered_locations.add(player.location)

        self.state.log_event("oil_the_road", player=self.player_id,
                             location=player.location,
                             remaining=self.uses_remaining)

        # 使用提示管理器显示响应信息
        response_msg = prompt_manager.get_prompt("talent", "t2oiltheroad.response",
                                                default="🛢️ {player_name} 使用「你给路打油」！\n    获得1个额外行动回合！（剩余{remaining}次）",
                                                player_name=player.name,
                                                remaining=self.uses_remaining)
        
        return response_msg

    def describe_status(self):
        locs = ", ".join(self.triggered_locations) if self.triggered_locations else "无"
        return f"剩余次数：{self.uses_remaining}/2，已触发地点：{locs}"