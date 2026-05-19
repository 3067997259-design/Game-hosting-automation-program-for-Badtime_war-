"""激进型人格策略"""
from typing import List
from controllers.ai.strategies.base_strategy import BasePersonalityStrategy, DecisionPhase


class AggressiveStrategy(BasePersonalityStrategy):
    personality_name = "aggressive"

    # ── 阶段顺序：战斗提前到发育前面 ──
    def get_phase_order(self) -> List[DecisionPhase]:
        return [
            DecisionPhase.EMERGENCY_VIRUS,
            DecisionPhase.EMERGENCY_SUPERNOVA,
            DecisionPhase.EMERGENCY_TERROR,
            DecisionPhase.SURVIVAL,
            DecisionPhase.SPECIAL_TALENT,
            DecisionPhase.CAPTAIN,
            DecisionPhase.COMBAT,          # ★ 战斗提到发育前面
            DecisionPhase.KILL_OPPORTUNITY,
            DecisionPhase.DEVELOP,
            DecisionPhase.FALLBACK,
        ]

    def is_terminal_phase(self, phase: DecisionPhase) -> bool:
        # 激进型：战斗不终止，允许追发育和击杀
        return phase.value >= DecisionPhase.KILL_OPPORTUNITY.value

    # ── 发育：2武器 + 2外甲 + 1内甲 ──
    def get_development_needs_order(self) -> List[str]:
        return [
            "voucher", "weapon", "outer_armor",
            "second_weapon", "second_outer_armor", "inner_armor",
        ]

    def is_development_complete(self, player, state,
                                count_outer_armor, count_inner_armor,
                                has_real_weapon, has_pass, has_stealth, real_weapon_count):
        return (real_weapon_count >= 2
                and count_outer_armor(player) >= 2
                and count_inner_armor(player) >= 1)

    # ── 战斗：偏好发育型目标（不主动攻击的玩家）──
    def modify_target_score(self, target, base_score, player,
                            players_who_attacked, is_passive, target_power):
        if is_passive:
            return base_score + 30 + target_power * 0.3
        return base_score

    def get_combat_response_preference(self, options: List[str]) -> str:
        if "counter" in options:
            return "counter"
        return options[0] if options else ""

    # ── 警察：警棍优先 ──
    def get_police_build_priority(self) -> List[str]:
        return ["警棍", "盾牌", "购买凭证"]

    # ── 天赋偏好 ──
    def get_hoshino_form_preference(self) -> str:
        return "临战-Archer"

    def get_anchor_fail_preference(self) -> str:
        return "留在当下"

    # ── 特殊 ──
    def should_agree_military_pass(self) -> bool:
        return True  # 激进型需要科技武器
