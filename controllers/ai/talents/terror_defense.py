"""
TerrorDefenseAI —— 非星野AI如何应对星野Terror状态

设计原则：
1. 自包含：接收明确参数，不依赖BasicAIController
2. 透明：每个方法的决策逻辑都有明确注释和调试输出
3. 可扩展：新增天赋AI行为只需添加方法，不修改现有逻辑

Terror 机制（来自 G7 星野天赋）：
- 星野「色彩」≥6 时，R0可选择进入"自我怀疑"
- 自我怀疑后下一轮变成 Terror
- Terror 状态下：全图任意攻击、高伤害、免疫病毒
- 对非星野玩家，Terror是最危险的信号——必须集火或逃跑
"""

from __future__ import annotations
from typing import List, Optional, Tuple, Any

from controllers.ai.constants import debug_ai_basic


class TerrorDefenseAI:
    """非星野AI应对Terror的决策模块。

    使用方式：
        terror_ai = TerrorDefenseAI(debug_name="AI_张三")
        threat = terror_ai.find_terror_threat(state)
        if threat:
            cmds = terror_ai.get_emergency_commands(player, state, threat, available)

    该模块不关心"我是谁"（什么天赋），只关心"场上有没有Terror"和"我应该做什么"。
    """

    def __init__(self, debug_name: str = "AI"):
        self._debug_name = debug_name

    # ════════════════════════════════════════════════════════════
    #  威胁检测
    # ════════════════════════════════════════════════════════════

    @staticmethod
    def find_terror_threat(state: Any) -> Optional[Any]:
        """在场上寻找处于 Terror 状态的玩家。

        Returns:
            找到的Terror玩家对象，没有则返回None。
        """
        for pid in state.player_order:
            t = state.get_player(pid)
            if not t or not t.is_alive():
                continue
            talent = getattr(t, 'talent', None)
            if talent and getattr(talent, 'is_terror', False):
                return t
        return None

    @staticmethod
    def find_self_doubt_threat(state: Any) -> Optional[Any]:
        """在场上寻找处于「自我怀疑」状态的玩家（下回合变Terror）。

        Returns:
            找到的自我怀疑玩家对象，没有则返回None。
        """
        for pid in state.player_order:
            t = state.get_player(pid)
            if not t or not t.is_alive():
                continue
            talent = getattr(t, 'talent', None)
            if talent and getattr(talent, 'self_doubt_pending', False):
                return t
        return None

    # ════════════════════════════════════════════════════════════
    #  紧急命令生成
    # ════════════════════════════════════════════════════════════

    def get_emergency_focus_fire_commands(
        self,
        player: Any,
        state: Any,
        target: Any,
        available: List[str],
        generate_attack_cmds,  # callable: (player, state, available, forced_target) -> List[str]
    ) -> Optional[List[str]]:
        """生成紧急集火Terror/自我怀疑目标的命令。

        这是最高优先级的反应——AI应该放下手里的发育/战斗，
        立刻攻击Terror目标。
        """
        threat_type = (
            "Terror" if getattr(getattr(target, 'talent', None), 'is_terror', False)
            else "自我怀疑(即将变Terror)"
        )
        debug_ai_basic(self._debug_name,
            f"TerrorDefense: {threat_type}目标 {target.name}，紧急集火！")

        attack_cmds = generate_attack_cmds(player, state, available, target)
        if attack_cmds:
            return [*attack_cmds, "forfeit"]
        # 打不到？至少尝试移动过去
        target_loc = self._get_location_str(target)
        my_loc = self._get_location_str(player)
        if target_loc and target_loc != my_loc and "move" in available:
            debug_ai_basic(self._debug_name,
                f"TerrorDefense: 无法攻击，移动到 {target_loc}")
            return [f"move {target_loc}", "forfeit"]
        return None

    # ════════════════════════════════════════════════════════════
    #  目标评分调整
    # ════════════════════════════════════════════════════════════

    @staticmethod
    def modify_target_score(target: Any, base_score: float) -> float:
        """调整目标评分——Terror/自我怀疑目标应该获得极高优先级。

        在有Terror的场上，AI不应该因为"目标护甲太厚"或"目标太远"
        而放弃攻击Terror。这个修正确保Terror始终是最高优先级目标。
        """
        talent = getattr(target, 'talent', None)
        if not talent:
            return base_score

        if getattr(talent, 'is_terror', False):
            # Terror 是全场最危险的敌人，集火优先级最高
            return base_score + 500.0

        if getattr(talent, 'self_doubt_pending', False):
            # 即将变Terror，次高优先级
            return base_score + 400.0

        return base_score

    # ════════════════════════════════════════════════════════════
    #  威胁评估调整
    # ════════════════════════════════════════════════════════════

    @staticmethod
    def modify_threat_power(target: Any, base_power: float) -> float:
        """调整威胁评估——Terror玩家的威胁值应该远超正常水平。

        这确保在 _update_threat_assessment 中，Terror目标不会被
        其他玩家的EMA衰减冲淡。
        """
        talent = getattr(target, 'talent', None)
        if not talent:
            return base_power

        if getattr(talent, 'is_terror', False):
            return base_power + 200.0  # Terror 最高优先级威胁
        if getattr(talent, 'self_doubt_pending', False):
            return base_power + 150.0  # 即将变 Terror

        return base_power

    # ════════════════════════════════════════════════════════════
    #  生存建议：当Terror过于强大时
    # ════════════════════════════════════════════════════════════

    def should_flee_from_terror(
        self, player: Any, terror_target: Any, state: Any
    ) -> Tuple[bool, str]:
        """判断是否应该从Terror身边逃跑（而非硬打）。

        条件：
        - 什么条件下都不能跑。Terror是全图攻击，而且全场禁用interact，不能跑也不能拿物品，只能莽。

        Returns:
            (should_flee: bool, reason: str)
        """
        if self._get_location_str(player) != self._get_location_str(terror_target):
            return False, "不同地点，但是Terror的攻击是全图且不可防御的，只能硬拼"

        hp = getattr(player, 'hp', 2.0)
        outer = self._count_outer_armor(player)
        inner = self._count_inner_armor(player)
        total_armor = outer + inner

        if hp <= 0.5:
            return False, f"HP极低({hp})，但是Terror的攻击是全图1点无视属性克制伤害，只能硬拼"
        if hp <= 1.0 and total_armor == 0:
            return False, f"HP低({hp})且无护甲，但是Terror的攻击是全图1点无视属性克制伤害，只能硬拼"

        return False, "有战斗能力"

    @staticmethod
    def _get_location_str(player: Any) -> str:
        loc = getattr(player, 'location', None)
        return str(loc) if loc else ""

    @staticmethod
    def _count_outer_armor(player: Any) -> int:
        armor = getattr(player, 'armor', None)
        if not armor or not hasattr(armor, 'get_active'):
            return 0
        from models.equipment import ArmorLayer
        return len(armor.get_active(ArmorLayer.OUTER))

    @staticmethod
    def _count_inner_armor(player: Any) -> int:
        armor = getattr(player, 'armor', None)
        if not armor or not hasattr(armor, 'get_active'):
            return 0
        from models.equipment import ArmorLayer
        return len(armor.get_active(ArmorLayer.INNER))
