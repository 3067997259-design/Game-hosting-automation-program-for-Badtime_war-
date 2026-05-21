"""刺客型人格策略"""
from typing import List
from controllers.ai.strategies.base_strategy import BasePersonalityStrategy, DecisionPhase


class AssassinStrategy(BasePersonalityStrategy):
    personality_name = "assassin"

    # ── 阶段顺序：击杀机会提前 ──
    def get_phase_order(self) -> List[DecisionPhase]:
        return [
            DecisionPhase.EMERGENCY_VIRUS,
            DecisionPhase.EMERGENCY_SUPERNOVA,
            DecisionPhase.EMERGENCY_TERROR,
            DecisionPhase.SURVIVAL,
            DecisionPhase.SPECIAL_TALENT,
            DecisionPhase.KILL_OPPORTUNITY,  # ★ 击杀机会提前
            DecisionPhase.CAPTAIN,
            DecisionPhase.COMBAT,
            DecisionPhase.DEVELOP,
            DecisionPhase.FALLBACK,
        ]

    def is_terminal_phase(self, phase: DecisionPhase) -> bool:
        # 刺客型：更激进，KILL_OPPORTUNITY 产出指令后不再继续
        return phase.value >= DecisionPhase.KILL_OPPORTUNITY.value

    def get_development_needs_order(self) -> List[str]:
        return [
            "voucher", "weapon", "outer_armor",
            "stealth", "second_weapon", "second_outer_armor",
        ]

    def is_development_complete(self, player, state,
                                count_outer_armor, count_inner_armor,
                                has_real_weapon, has_pass, has_stealth, real_weapon_count):
        return (real_weapon_count >= 2
                and has_stealth
                and count_outer_armor(player) >= 2)

    # ── 战斗：偏好低HP/无甲目标 ──
    def modify_target_score(self, target, base_score, player,
                            players_who_attacked, is_passive, target_power):
        # 计算有效HP：基础HP + 天赋额外HP
        hp = getattr(target, 'hp', 2.0)
        talent = getattr(target, 'talent', None)
        if talent:
            temp_hp = getattr(talent, 'temp_hp', 0.0)
            if temp_hp > 0:
                hp += temp_hp
            charges = getattr(talent, 'ardent_wish_charges', 0)
            if charges > 0:
                hp += charges * 0.5
        score = base_score + max(0, 3 - hp) * 20
        # 无外甲时额外加分
        armor = getattr(target, 'armor', None)
        if armor and hasattr(armor, 'get_active'):
            from models.equipment import ArmorLayer
            if not armor.get_active(ArmorLayer.OUTER):
                score += 40
            if not armor.get_active(ArmorLayer.INNER):
                score += 20
        return score

    def get_combat_response_preference(self, options: List[str]) -> str:
        if "dodge" in options:
            return "dodge"
        return options[0] if options else ""

