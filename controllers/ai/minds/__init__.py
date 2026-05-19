"""AI Minds —— 可独立测试的策略模块（组合优于继承）

每个 Mind 是一个自包含的类，接收明确参数，返回明确结果。
不依赖 BasicAIController 的隐式状态。
"""

from controllers.ai.minds.base import BaseMind, MindAssessment
from controllers.ai.minds.police_mind import PoliceMind, PoliceSituation, PoliceStance
from controllers.ai.minds.threat_mind import ThreatMind
from controllers.ai.minds.develop_mind import DevelopMind
from controllers.ai.minds.combat_mind import CombatMind

__all__ = [
    "BaseMind", "MindAssessment",
    "PoliceMind", "PoliceSituation", "PoliceStance",
    "ThreatMind", "DevelopMind", "CombatMind",
]
