"""
StrategyRegistry —— 人格策略注册表
"""

from typing import Dict, Type
from controllers.ai.strategies.base_strategy import BasePersonalityStrategy, DecisionPhase
from controllers.ai.strategies.aggressive_strategy import AggressiveStrategy
from controllers.ai.strategies.defensive_strategy import DefensiveStrategy
from controllers.ai.strategies.assassin_strategy import AssassinStrategy
from controllers.ai.strategies.builder_strategy import BuilderStrategy
from controllers.ai.strategies.political_strategy import PoliticalStrategy


# 注册表：人格名 → 策略类
STRATEGY_REGISTRY: Dict[str, Type[BasePersonalityStrategy]] = {
    "balanced":   BasePersonalityStrategy,
    "aggressive": AggressiveStrategy,
    "defensive":  DefensiveStrategy,
    "assassin":   AssassinStrategy,
    "builder":    BuilderStrategy,
    "political":  PoliticalStrategy,
}


def create_strategy(personality: str) -> BasePersonalityStrategy:
    """工厂函数：根据人格名创建策略实例。"""
    cls = STRATEGY_REGISTRY.get(personality, BasePersonalityStrategy)
    return cls()
