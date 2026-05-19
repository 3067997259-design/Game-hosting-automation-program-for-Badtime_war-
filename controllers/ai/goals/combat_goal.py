"""
CombatGoal —— 战斗目标（持久化攻击意图）

覆盖旧架构 L20：战斗状态持续

状态机：APPROACHING → ENGAGING → ATTACKING
当AI决定攻击某人后，这个目标跨轮次持久化，
直到目标死亡或无法抵达。
"""

from __future__ import annotations
from typing import List, Optional, Any

from controllers.ai.goals.base_goal import BaseGoal
from controllers.ai.constants import debug_ai_basic


class CombatGoal(BaseGoal):
    """持久化战斗目标：对target_id执行攻击循环。

    与旧 _in_combat/_combat_target 的区别：
    - 旧系统只靠两个变量追踪，重新进入 generate_candidates 时可能被其他优先级劫持
    - CombatGoal 在GoalStack中按优先级排序，只有被更高优先级目标打断时才暂停
    """

    def __init__(
        self,
        target_id: str,
        target_name: str,
        priority: int = 6,
        debug_name: str = "AI",
    ):
        super().__init__()
        self.target_id = target_id
        self.target_name = target_name
        self.priority = priority
        self.description = f"攻击 {target_name}"
        self._debug_name = debug_name
        self._state: str = "APPROACHING"  # APPROACHING → ENGAGING → ATTACKING
        self._weapon_name: Optional[str] = None
        self._consecutive_failures: int = 0

    def is_expired(self, player: Any, state: Any) -> bool:
        """目标死亡或自己被警察限制时过期"""
        target = state.get_player(self.target_id)
        if not target or not target.is_alive():
            debug_ai_basic(self._debug_name,
                f"CombatGoal: 目标 {self.target_name} 已死亡，目标完成")
            return True
        # 连续失败太多次 → 放弃
        if self._consecutive_failures >= 8:
            debug_ai_basic(self._debug_name,
                f"CombatGoal: 连续失败{self._consecutive_failures}次，放弃")
            return True
        return False

    def is_achieved(self, player: Any, state: Any) -> bool:
        """目标死亡时视为完成"""
        target = state.get_player(self.target_id)
        return not target or not target.is_alive()

    def _get_next_command_internal(
        self, player: Any, state: Any, available: List[str]
    ) -> Optional[str]:
        """生成下一步战斗命令。委托给控制器的 _cmd_attack。

        注意：这个方法需要控制器注入 _cmd_attack 回调。
        在 _generate_candidates 中通过 controller._cmd_attack() 生成命令后，
        再将命令注入candidates，而不是在这里生成。
        
        这里返回 None 表示"目标保持活跃，但不在此生成命令"。
        """
        # CombatGoal 本身不生成命令——命令由 _generate_candidates 的
        # 旧优先级链（L20 战斗状态）生成。CombatGoal 的作用是：
        # 1. 在旧逻辑之前推入目标栈（提高战斗目标的优先级）
        # 2. 跨轮次持久化（防止被其他优先级劫持）
        # 3. 在 L29 作为最后兜底注入命令
        return None  # 命令由外部生成后注入

    def on_command_failed(self) -> None:
        """当生成的命令被validator拒绝时调用"""
        self._consecutive_failures += 1

    def on_command_succeeded(self) -> None:
        """当生成的命令成功执行时调用"""
        self._consecutive_failures = 0
