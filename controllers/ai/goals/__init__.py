"""AI Goals —— 持久化目标包

已实现:
- DevelopGoal    — 发育（去X地点拿Y物品）
- CombatGoal     — 战斗（对目标执行攻击循环）
- FleeGoal       — 逃跑（危险模式回避难所）
- VirusCureGoal  — 病毒免疫（获取防毒面具/封闭）
- CaptainGoal    — 队长指挥（管理警察单位）
- PoliticalGoal  — 政治行动（加入警察/竞选队长）
"""

from controllers.ai.goals.base_goal import BaseGoal, GoalStack
from controllers.ai.goals.develop_goal import DevelopGoal
from controllers.ai.goals.combat_goal import CombatGoal
from controllers.ai.goals.flee_goal import FleeGoal
from controllers.ai.goals.virus_goal import VirusCureGoal
from controllers.ai.goals.captain_goal import CaptainGoal
from controllers.ai.goals.political_goal import PoliticalGoal

__all__ = [
    "BaseGoal", "GoalStack",
    "DevelopGoal", "CombatGoal", "FleeGoal", "VirusCureGoal",
    "CaptainGoal", "PoliticalGoal",
]
