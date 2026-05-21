"""建设型人格策略"""
from typing import List
from controllers.ai.strategies.base_strategy import BasePersonalityStrategy, DecisionPhase


class BuilderStrategy(BasePersonalityStrategy):
    personality_name = "builder"

    # ── 阶段顺序：发育最优先 ──
    def get_phase_order(self) -> List[DecisionPhase]:
        return [
            DecisionPhase.EMERGENCY_VIRUS,
            DecisionPhase.EMERGENCY_SUPERNOVA,
            DecisionPhase.EMERGENCY_TERROR,
            DecisionPhase.SURVIVAL,
            DecisionPhase.DEVELOP,         # ★ 发育提到战斗前
            DecisionPhase.CAPTAIN,
            DecisionPhase.SPECIAL_TALENT,
            DecisionPhase.COMBAT,
            DecisionPhase.KILL_OPPORTUNITY,
            DecisionPhase.FALLBACK,
        ]

    def get_development_needs_order(self) -> List[str]:
        return [
            "voucher", "outer_armor", "weapon",
            "second_outer_armor", "inner_armor", "military_pass",
        ]

    def is_development_complete(self, player, state,
                                count_outer_armor, count_inner_armor,
                                has_real_weapon, has_pass, has_stealth, real_weapon_count):
        return (has_real_weapon
                and count_outer_armor(player) >= 2
                and count_inner_armor(player) >= 1
                and has_pass)

    def should_attack_when_develop_blocked(self) -> bool:
        return False

