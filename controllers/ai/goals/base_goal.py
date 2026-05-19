"""
BaseGoal —— 目标基类

设计原则：
1. 目标是跨轮次持久化的——解决AI"忘记自己在做什么"
2. 每个目标是一个小型状态机，自己管理生命周期
3. 目标可以被打断（危险模式），打断后自动恢复
4. 优先级决定目标栈中的顺序
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import List, Optional, Any


class BaseGoal(ABC):
    """持久化目标基类。

    子类只需实现 is_expired / is_achieved / _get_next_command_internal。
    优先级由 priority 属性决定，数值越大越优先。
    """

    # 目标优先级（越大越优先）
    # 参考值：10=逃跑/应急，8=警察对抗，6=战斗，4=关键发育，2=一般发育
    priority: int = 5

    # 可读的目标描述（用于调试日志）
    description: str = "未命名目标"

    def __init__(self):
        self._created_round: int = 0
        self._interrupted: bool = False

    def set_round(self, round_num: int) -> None:
        """由控制器在创建目标时调用，记录创建轮次"""
        self._created_round = round_num

    @abstractmethod
    def is_expired(self, player: Any, state: Any) -> bool:
        """目标是否已过期（目标死亡/物品被抢/位置不可达等）。
        过期目标会被从栈中移除。"""
        ...

    @abstractmethod
    def is_achieved(self, player: Any, state: Any) -> bool:
        """目标是否已完成。
        已完成目标会被从栈中移除。"""
        ...

    def get_next_command(
        self, player: Any, state: Any, available: List[str]
    ) -> Optional[str]:
        """获取下一步指令。返回 None 表示本回合无法推进（但目标保留）。"""
        if self._interrupted:
            return None  # 被中断时不输出命令，但目标保留在栈中
        return self._get_next_command_internal(player, state, available)

    @abstractmethod
    def _get_next_command_internal(
        self, player: Any, state: Any, available: List[str]
    ) -> Optional[str]:
        """子类实现：生成下一步指令"""
        ...

    def interrupt(self) -> None:
        """标记目标被打断（如进入危险模式）。目标保留在栈中，但暂停输出命令。"""
        self._interrupted = True

    def resume(self) -> None:
        """恢复被打断的目标。"""
        self._interrupted = False

    def __repr__(self) -> str:
        status = "⏸" if self._interrupted else "→"
        return f"{status} {self.description} (pri={self.priority})"


class GoalStack:
    """目标栈：管理多个持久化目标。

    特性：
    - 按优先级排序（高优先级在前）
    - 自动清理过期/已完成的目标
    - 支持打断/恢复
    """

    def __init__(self, max_goals: int = 5):
        self._goals: List[BaseGoal] = []
        self._max_goals = max_goals

    def push(self, goal: BaseGoal) -> None:
        """添加目标。如果已存在同类目标，只用同级或更高优先级目标替换。"""
        # 替换同类目标（基于类型），但不能让低优先级目标覆盖高优先级意图
        goal_type = type(goal)
        same_type_goals = [g for g in self._goals if isinstance(g, goal_type)]
        if any(g.priority > goal.priority for g in same_type_goals):
            return
        self._goals = [g for g in self._goals if not isinstance(g, goal_type)]
        self._goals.append(goal)
        # 按优先级降序排列
        self._goals.sort(key=lambda g: g.priority, reverse=True)
        # 超出上限则移除低优先级目标
        if len(self._goals) > self._max_goals:
            self._goals = self._goals[:self._max_goals]

    def top(self) -> Optional[BaseGoal]:
        """获取栈顶目标（最高优先级）。"""
        return self._goals[0] if self._goals else None

    def pop_expired(self, player: Any, state: Any) -> List[BaseGoal]:
        """移除并返回所有过期/已完成的目标。"""
        removed = []
        kept = []
        for g in self._goals:
            if g.is_expired(player, state) or g.is_achieved(player, state):
                removed.append(g)
            else:
                kept.append(g)
        self._goals = kept
        return removed

    def interrupt_all(self) -> None:
        """打断所有目标（如进入危险模式）。"""
        for g in self._goals:
            g.interrupt()

    def resume_all(self) -> None:
        """恢复所有被打断的目标。"""
        for g in self._goals:
            g.resume()

    def clear(self) -> None:
        """清空所有目标。"""
        self._goals.clear()

    @property
    def is_empty(self) -> bool:
        return len(self._goals) == 0

    @property
    def all_goals(self) -> List[BaseGoal]:
        return list(self._goals)

    def __repr__(self) -> str:
        if not self._goals:
            return "GoalStack(空)"
        lines = ["GoalStack:"]
        for g in self._goals:
            lines.append(f"  {g}")
        return "\n".join(lines)
