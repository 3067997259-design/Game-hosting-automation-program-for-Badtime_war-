"""dodge-bot —— 纯逃跑：最小发育，受到关注立刻换点。

策略轴：home 拿小刀+盾 → 一旦被攻击/同地点出现敌人就移动到无人地点 → 否则蹲点。
用途：测逃跑收益（v0.2 §9 "追逃平均轮数"指标的对照腿）；v2.0 命中/
闪避/借机攻击落地后，同一 bot 重跑即得逃跑收益变化。
"""
from typing import Any, List

from controllers.bots.script_bot import ScriptBotController

# 公共地点池（不含 home：躲进自己家是 turtle 的事，dodge 测的是游走）
_PUBLIC_LOCATIONS: List[str] = ["商店", "魔法所", "医院", "军事基地", "警察局"]


class DodgeBotController(ScriptBotController):

    BOT_NAME = "dodge"

    def decide(self, player: Any, game_state: Any) -> str:
        home = self.my_home(player)

        # 1. 最小发育：小刀 + 盾牌（home 免费）
        if not self.has_weapon(player, "小刀") or not self.has_armor_named(player, "盾牌"):
            if player.location != home:
                return f"move {home}"
            if not self.has_weapon(player, "小刀"):
                return "interact 小刀"
            return "interact 盾牌"

        # 2. 同地点有敌人 → 逃往第一个无人公共地点（顺序固定，确定性稳定）
        if self.enemies_here(player, game_state):
            enemies = self.alive_enemies(player, game_state)
            occupied = {p.location for p in enemies}
            for loc in _PUBLIC_LOCATIONS:
                if loc != player.location and loc not in occupied:
                    return f"move {loc}"
            # 无处可逃：换到任意一个不同地点
            for loc in _PUBLIC_LOCATIONS:
                if loc != player.location:
                    return f"move {loc}"

        # 3. 无人打扰：蹲点观望
        return "forfeit"
