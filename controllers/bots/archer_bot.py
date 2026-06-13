"""archer-bot —— 弓风筝流：买箭跨地点射，被近身就跑（M4 风洞 bot）。

策略轴：home 起步自带弓+3箭 → 跨地点锁定+射击循环 → 箭尽则去商店补给 →
同地点出现敌人就逃。用途：M4 命中体系的头号客户——弓吃未锁定惩罚，
擦伤率应因它显著上升；与 turtle/rush 的矩阵测远程 vs 近战经济。
"""
from typing import Any, List

from controllers.bots.script_bot import ScriptBotController

_PUBLIC_LOCATIONS: List[str] = ["商店", "魔法所", "医院", "军事基地", "警察局"]


class ArcherBotController(ScriptBotController):

    BOT_NAME = "archer"

    def decide(self, player: Any, game_state: Any) -> str:
        # 1. 同地点有敌人 → 逃往无人公共地点（弓手怕近身）
        if self.enemies_here(player, game_state):
            enemies = self.alive_enemies(player, game_state)
            occupied = {p.location for p in enemies}
            for loc in _PUBLIC_LOCATIONS:
                if loc != player.location and loc not in occupied:
                    return f"move {loc}"
            for loc in _PUBLIC_LOCATIONS:
                if loc != player.location:
                    return f"move {loc}"

        # 2. 箭尽 → 去商店补给（有信用点就买，没有就打工）
        if getattr(player, 'arrows', 0) < 1:
            if player.location != "商店":
                return "move 商店"
            if getattr(player, 'credits', 0) < 1:
                return "interact 打工"
            return "interact 箭矢补给"

        # 3. 有箭 → 锁定并射击第一个存活敌人（跨地点）
        enemies = self.alive_enemies(player, game_state)
        if enemies and enemies[0].location:
            target = enemies[0]
            locked = game_state.markers.has_relation(
                target.player_id, "LOCKED_BY", player.player_id)
            if not locked:
                return f"lock {target.name}"
            return f"shoot {target.name}"

        return "forfeit"
