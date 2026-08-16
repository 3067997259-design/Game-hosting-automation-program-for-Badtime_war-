"""rush-bot —— 纯莽夫：最小武装后直奔最近目标，永不二次发育。

策略轴：home 拿小刀 → 锁定第一个存活敌人 → 追到同地点 → 小刀循环攻击。
用途：测攻击路线的下限收益（TTK 实测值、先手价值）；与 turtle 的 1v1
矩阵是"攻击 vs 防御经济"的最直接对照实验。
"""
from typing import Any

from controllers.bots.script_bot import ScriptBotController


class RushBotController(ScriptBotController):

    BOT_NAME = "rush"

    def decide(self, player: Any, game_state: Any) -> str:
        # 1. 最小武装：小刀（home 免费）
        if not self.has_weapon(player, "小刀"):
            home = self.my_home(player)
            if player.location != home:
                return f"move {home}"
            return "interact 小刀"

        # 2. 同地点有敌人 → 先 find 建立面对面，再开打（按 player_order 取第一个）
        here = self.enemies_here(player, game_state)
        if here:
            target = here[0]
            if not self.is_engaged(player, target, game_state):
                return f"find {target.name}"
            return f"attack {target.name} 小刀"

        # 3. 追击第一个存活敌人
        enemies = self.alive_enemies(player, game_state)
        if enemies and enemies[0].location:
            return f"move {enemies[0].location}"

        return "forfeit"
