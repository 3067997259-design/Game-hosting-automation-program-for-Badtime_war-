"""
BaseMind —— 态势分析器抽象基类 + MindAssessment 统一输出结构

设计原则：
1. 每个 Mind 是纯函数分析器：接收 state + strategy，输出结构化评估
2. MindAssessment 是所有 Mind 的统一输出格式
3. Mind 不保持任何跨轮次状态（状态由 GoalStack 管理）
4. 每个 Mind 可独立单元测试
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from controllers.ai.strategies.base_strategy import DecisionPhase


@dataclass
class MindAssessment:
    """一个 Mind 对当前态势的判断结果。

    Orchestrator 收集所有 Mind 的评估后，按 DecisionPhase 分发到对应阶段。
    """

    # 哪个 Mind 产出的（"threat", "police", "combat", "develop"）
    mind_name: str

    # 紧急程度 0-10，越大越需要优先处理
    urgency: int = 0

    # 这个评估属于哪个决策阶段
    phase: DecisionPhase = DecisionPhase.FALLBACK

    # 人类可读的态势描述（用于调试日志）
    summary: str = ""

    # 阶段相关的结构化数据，不同 Mind 填充不同字段：
    #   ThreatMind: threat_scores, danger, terror_target, supernova_threat, virus_emergency
    #   PoliceMind: police_situation, aoe_weapons, protected_targets
    #   CombatMind: viable_targets, best_weapon, kill_targets, combat_ready
    #   DevelopMind: needs, best_location, development_complete
    data: Dict[str, Any] = field(default_factory=dict)

    # 如果该评估建议建立一个持久化目标（由 Orchestrator 决定是否推入 GoalStack）
    recommended_goal: Optional[Any] = None


class BaseMind(ABC):
    """态势分析器抽象基类。

    使用方式：
        mind = ThreatMind(debug_name="AI_张三")
        assessment = mind.assess(player, state, strategy)
        if assessment.data.get("danger"):
            # 进入危险处理逻辑
    """

    def __init__(self, debug_name: str = "AI", query: Any = None):
        self._debug_name = debug_name
        self._query = query

    @abstractmethod
    def assess(self, player: Any, state: Any, strategy: Any, **kwargs) -> MindAssessment:
        """分析当前态势，返回结构化评估。

        Args:
            player: 当前 AI 控制的玩家对象
            state: 游戏状态（GameState）
            strategy: 人格策略实例（BasePersonalityStrategy）

        Returns:
            MindAssessment: 结构化的态势评估结果
        """
        ...
