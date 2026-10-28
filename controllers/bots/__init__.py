"""风洞 bot 包 —— 极端单一策略的脚本控制器（M0 风洞基础设施）。

用途：经济/数值实验的可控对照组（stats_runner --lineup turtle,rush,...）。
它们不是 AI——没有评估、没有人格、没有天赋，只有一条写死的策略轴。
"""
from controllers.bots.script_bot import ScriptBotController
from controllers.bots.turtle_bot import TurtleBotController
from controllers.bots.rush_bot import RushBotController
from controllers.bots.dodge_bot import DodgeBotController
from controllers.bots.archer_bot import ArcherBotController
from controllers.bots.dummy_bot import DummyBotController

# stats_runner --lineup / RL 课程 opponent_bots 名称注册表
BOT_REGISTRY = {
    "dummy": DummyBotController,     # Rung 0：不作为的活靶子（RL 课程最低一级）
    "turtle": TurtleBotController,
    "rush": RushBotController,
    "dodge": DodgeBotController,
    "archer": ArcherBotController,
}

__all__ = [
    "ScriptBotController", "DummyBotController", "TurtleBotController",
    "RushBotController", "DodgeBotController", "ArcherBotController",
    "BOT_REGISTRY",
]
