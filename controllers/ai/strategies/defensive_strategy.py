"""防御型人格策略"""
from typing import List, Optional
from controllers.ai.strategies.base_strategy import BasePersonalityStrategy, DecisionPhase


class DefensiveStrategy(BasePersonalityStrategy):
    personality_name = "defensive"

    # ── 阶段顺序：发育优先 ──
    def get_phase_order(self) -> List[DecisionPhase]:
        return [
            DecisionPhase.EMERGENCY_VIRUS,
            DecisionPhase.EMERGENCY_SUPERNOVA,
            DecisionPhase.EMERGENCY_TERROR,
            DecisionPhase.SURVIVAL,
            DecisionPhase.CAPTAIN,
            DecisionPhase.SPECIAL_TALENT,
            DecisionPhase.DEVELOP,         # ★ 发育提前
            DecisionPhase.KILL_OPPORTUNITY,
            DecisionPhase.COMBAT,
            DecisionPhase.FALLBACK,
        ]

    def get_development_needs_order(self) -> List[str]:
        return [
            "voucher", "outer_armor", "second_outer_armor",
            "weapon", "detection", "inner_armor",
        ]

    def is_development_complete(self, player, state,
                                count_outer_armor, count_inner_armor,
                                has_real_weapon, has_pass, has_stealth, real_weapon_count):
        return (has_real_weapon
                and count_outer_armor(player) >= 2
                and count_inner_armor(player) >= 1)

    # ── 战斗：劣势时撤退 ──
    def should_continue_combat(self, player, target, is_at_disadvantage):
        if is_at_disadvantage:
            return False
        return None

    def get_combat_response_preference(self, options: List[str]) -> str:
        if "dodge" in options:
            return "dodge"
        if "block" in options:
            return "block"
        return options[0] if options else ""

    # ── 警察：盾牌优先 ──
    def get_police_build_priority(self) -> List[str]:
        return ["盾牌", "警棍", "购买凭证"]

    def should_attack_when_develop_blocked(self) -> bool:
        return False

