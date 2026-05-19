"""CommandBuilder 模块 —— 根据 Mind 分析 + Strategy → 生成指令字符串列表

各 Builder 不做态势分析、人格判断，只负责将决策转化为合法的游戏指令。
"""
from controllers.ai.command_builder.combat_commands import CombatCommandBuilder
from controllers.ai.command_builder.develop_commands import DevelopCommandBuilder
from controllers.ai.command_builder.police_commands import PoliceCommandBuilder

__all__ = [
    "CombatCommandBuilder",
    "DevelopCommandBuilder",
    "PoliceCommandBuilder",
]
