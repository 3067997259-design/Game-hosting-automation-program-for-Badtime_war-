"""
BasePersonalityStrategy —— 人格策略抽象基类

设计原则：
- 每个策略方法接收明确参数，返回明确结果
- 替代散落在7个文件中的 self.personality == "xxx" 判断
- 默认实现对应 balanced 人格（最中性的行为）
"""

from __future__ import annotations
from enum import IntEnum
from typing import List, Optional, Any


class DecisionPhase(IntEnum):
    """决策阶段枚举，对应旧瀑布流中的优先级层。
    值越大优先级越高。Orchestrator 按降序遍历。
    """
    EMERGENCY_VIRUS = 10       # 病毒应急（需要治疗）
    EMERGENCY_SUPERNOVA = 9    # 超新星威胁（紧急分散）
    EMERGENCY_TERROR = 8       # Terror/自我怀疑（紧急集火）
    SURVIVAL = 7               # 危险模式（逃跑/反制）
    CAPTAIN = 6                # 队长指挥
    SPECIAL_TALENT = 5         # 特殊天赋主动技能（全息影像/救世主/火萤超新星）
    COMBAT = 4                 # 战斗攻击
    KILL_OPPORTUNITY = 3       # 击杀机会
    DEVELOP = 2                # 发育装备
    FALLBACK = 1               # 兜底移动/forfeit


class BasePersonalityStrategy:
    """人格策略基类。子类覆盖需要差异化行为的方法。"""

    personality_name: str = "balanced"

    # ════════════════════════════════════════════════════════
    #  发育相关
    # ════════════════════════════════════════════════════════

    def get_development_needs_order(self) -> List[str]:
        """返回发育需求优先级列表。"""
        return [
            "voucher", "weapon", "outer_armor",
            "second_outer_armor", "detection", "inner_armor",
        ]

    def is_development_complete(
        self, player: Any, state: Any,
        count_outer_armor, count_inner_armor, has_real_weapon: bool,
        has_pass: bool, has_stealth: bool, real_weapon_count: int,
    ) -> bool:
        """判断发育是否完成。"""
        outer = count_outer_armor(player)
        inner = count_inner_armor(player)
        return has_real_weapon and outer >= 2 and inner >= 1

    # ════════════════════════════════════════════════════════
    #  战斗相关
    # ════════════════════════════════════════════════════════

    def modify_target_score(
        self, target: Any, base_score: float, player: Any,
        players_who_attacked: set, is_passive: bool, target_power: float,
    ) -> float:
        """调整目标评分。"""
        return base_score

    def should_continue_combat(
        self, player: Any, target: Any, is_at_disadvantage: bool,
    ) -> Optional[bool]:
        """是否继续战斗。返回 None 表示用默认逻辑，True/False 覆盖。"""
        if is_at_disadvantage:
            return False  # 劣势时撤退（defensive 会更早撤退）
        return None  # 使用默认逻辑

    def get_combat_response_preference(self, options: List[str]) -> str:
        """被攻击时的响应偏好。"""
        if "block" in options:
            return "block"
        return options[0] if options else ""

    # ════════════════════════════════════════════════════════
    #  警察相关
    # ════════════════════════════════════════════════════════

    def get_police_build_priority(self) -> List[str]:
        """加入警察时的装备选择优先级。"""
        return ["盾牌", "购买凭证", "警棍"]

    def should_support_report(self) -> bool:
        """被问是否支持举报时默认态度。"""
        return False

    # ════════════════════════════════════════════════════════
    #  天赋相关
    # ════════════════════════════════════════════════════════

    def get_hoshino_form_preference(self) -> str:
        """星野形态选择偏好。"""
        return "水着-shielder"

    def get_anchor_fail_preference(self) -> str:
        """锚定失败时的偏好。"""
        return "回到过去"

    # ════════════════════════════════════════════════════════
    #  特殊地点 / 交互
    # ════════════════════════════════════════════════════════

    def should_agree_military_pass(self) -> bool:
        """是否同意强买通行证。"""
        return False

    def should_buy_stealth_items(self) -> bool:
        """是否购买隐身物品。"""
        return False

    def should_release_virus(self) -> bool:
        """是否应在医院放毒。"""
        return False

    def wants_extra_inner_armor(self) -> bool:
        """是否需要第二个内甲。"""
        return False

    # ════════════════════════════════════════════════════════
    #  DecisionOrchestrator 接口 —— 控制决策阶段顺序与终止条件
    #  这是新架构的"调度层"，替代旧瀑布流的硬编码 if-else 顺序
    # ════════════════════════════════════════════════════════

    def get_phase_order(self) -> List[DecisionPhase]:
        """返回决策阶段优先级顺序。Orchestrator 将按列表顺序遍历。

        不同人格可以重排阶段顺序来改变行为倾向：
        - aggressive 可以把 COMBAT 提到 DEVELOP 前面
        - builder 可以把 DEVELOP 提到 COMBAT 前面
        - political 可以把 CAPTAIN 提到最前面
        """
        return [
            DecisionPhase.EMERGENCY_VIRUS,
            DecisionPhase.EMERGENCY_SUPERNOVA,
            DecisionPhase.EMERGENCY_TERROR,
            DecisionPhase.SURVIVAL,
            DecisionPhase.CAPTAIN,
            DecisionPhase.SPECIAL_TALENT,
            DecisionPhase.COMBAT,
            DecisionPhase.KILL_OPPORTUNITY,
            DecisionPhase.DEVELOP,
            DecisionPhase.FALLBACK,
        ]

    def is_terminal_phase(self, phase: DecisionPhase) -> bool:
        """该阶段产出指令后，是否停止后续所有阶段？

        默认：EMERGENCY_* 和 SURVIVAL 阶段产出指令后立即返回，
        不继续往下遍历（因为紧急情况必须优先处理）。
        COMBAT 和 DEVELOP 阶段不终止，允许叠加（如战斗同时拿装备）。
        """
        return phase.value >= DecisionPhase.SPECIAL_TALENT.value

    # ════════════════════════════════════════════════════════
    #  警察立场 + 队长危险判定（Strategy层核心扩展）
    # ════════════════════════════════════════════════════════

    def get_police_stance(self, player: Any, state: Any) -> str:
        """返回对警察的态度：'build', 'resist', 'ignore'。
        默认逻辑（所有非political人格）：
        - 有犯罪记录 → resist（警察会追我）
        - 无罪 → ignore
        """
        if self._is_criminal(player, state):
            return "resist"
        return "ignore"

    def assess_captain_danger(self, player: Any, state: Any,
                              police_cache: dict, count_outer_armor_fn) -> bool:
        """队长专用危险判定。仅当玩家是队长时调用。
        返回 True 表示需要进入危险模式。
        """
        # 1. HP极低 → 无论如何都危险
        if player.hp <= 0.5:
            return True

        # 2. 没有同地点活跃警察 → 无保护 → 普通标准
        my_loc = str(getattr(player, 'location', ''))
        active_nearby = sum(1 for u in police_cache.get("units", [])
                           if u.get("is_active") and u.get("is_alive")
                           and u.get("location") == my_loc)
        if active_nearby == 0:
            return player.hp <= 1.0 and count_outer_armor_fn(player) == 0

        # 3. 有保护 → 检查是否存在保护穿透威胁
        return self._has_protection_bypass_threat(player, state)

    def _has_protection_bypass_threat(self, player: Any, state: Any) -> bool:
        """同地点是否存在能绕开警察保护的威胁？子类可追加。"""
        my_loc = str(getattr(player, 'location', ''))
        for pid in state.player_order:
            t = state.get_player(pid)
            if not t or not t.is_alive() or t.player_id == player.player_id:
                continue
            if str(getattr(t, 'location', '')) != my_loc:
                continue
            t_talent = getattr(t, 'talent', None)
            if not t_talent:
                continue

            # G3: 未使用的幻想乡 → 随时被拉进结界单挑
            if getattr(t_talent, 'name', '') == "神话之外" and not getattr(t_talent, 'used', False):
                return True

            # G4: 救世主形态 → 无视保护的高伤害近战
            if getattr(t_talent, 'is_savior', False):
                return True

            # G1: 火萤有磨过的小刀 → 2×2=4伤害
            if getattr(t_talent, 'name', '') == "火萤IV型-完全燃烧":
                for w in getattr(t, 'weapons', []):
                    if w and getattr(w, 'name', '') == "小刀" and getattr(w, 'base_damage', 0) >= 2:
                        return True

            # 任意玩家有AOE → 警察保护对AOE无效
            if self._has_aoe_weapon(t):
                return True

        return False

    @staticmethod
    def _is_criminal(player: Any, state: Any) -> bool:
        if getattr(player, 'is_criminal', False):
            return True
        police = getattr(state, 'police', None)
        if police and hasattr(police, 'is_criminal'):
            if police.is_criminal(player.player_id):
                return True
        return False

    @staticmethod
    def _has_aoe_weapon(p: Any) -> bool:
        from controllers.ai.constants import POLICE_AOE_WEAPONS
        for w in getattr(p, 'weapons', []):
            if w and getattr(w, 'name', '') in POLICE_AOE_WEAPONS:
                return True
        learned = getattr(p, 'learned_spells', set())
        return "地震" in learned or "地动山摇" in learned
