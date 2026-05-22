"""政治型人格策略（最复杂的人格）"""
from typing import List, Optional
from controllers.ai.strategies.base_strategy import BasePersonalityStrategy, DecisionPhase


class PoliticalStrategy(BasePersonalityStrategy):
    personality_name = "political"

    # ── 阶段顺序：队长指挥最优先 ──
    def get_phase_order(self) -> List[DecisionPhase]:
        return [
            DecisionPhase.EMERGENCY_VIRUS,
            DecisionPhase.EMERGENCY_SUPERNOVA,
            DecisionPhase.EMERGENCY_TERROR,
            DecisionPhase.SURVIVAL,
            DecisionPhase.CAPTAIN,         # ★ 队长指挥提前
            DecisionPhase.SPECIAL_TALENT,
            DecisionPhase.DEVELOP,
            DecisionPhase.KILL_OPPORTUNITY,
            DecisionPhase.COMBAT,
            DecisionPhase.FALLBACK,
        ]

    def get_development_needs_order(self) -> List[str]:
        # political 只需要最基本的装备，然后直奔警察局
        return ["voucher", "weapon", "outer_armor"]

    def is_development_complete(self, player, state,
                                count_outer_armor, count_inner_armor,
                                has_real_weapon, has_pass, has_stealth, real_weapon_count):
        """political的发育完成取决于是否当上队长+警察是否部署完毕。
        此方法由外部政治降级逻辑配合使用，这里只做基本判断。"""
        # 非队长时永不满足（由 controller 层的 fallback 逻辑处理）
        if not getattr(player, 'is_captain', False):
            return False
        return has_real_weapon and count_outer_armor(player) >= 1

    def get_police_build_priority(self) -> List[str]:
        return ["购买凭证", "警棍", "盾牌"]

    def should_support_report(self, target_name: str = "", threat_score: float = 0.0) -> bool:
        return True

    def should_continue_combat(self, player, target, is_at_disadvantage):
        # political 非队长时不战斗
        if not getattr(player, 'is_captain', False):
            return False
        return None

    def get_police_stance(self, player, state) -> str:
        """political: 警察系统可用时BUILD, 犯罪时RESIST, 否则IGNORE"""
        # 有犯罪记录 → resist
        if self._is_criminal(player, state):
            return "resist"
        # 警察系统不可用 → ignore
        police = getattr(state, 'police', None)
        if not police or getattr(police, 'permanently_disabled', False):
            return "ignore"
        # 已有队长（不是自己）→ ignore（无法再当队长）
        if police.has_captain() and police.captain_id != player.player_id:
            return "ignore"
        # 已有其他警察成员 → ignore（一局只能一个警察）
        pe = getattr(state, 'police_engine', None)
        if pe:
            existing = pe.get_current_police_member_id()
            if existing is not None and existing != player.player_id:
                return "ignore"
        # 可以build
        return "build"

    def supports_political_fallback(self) -> bool:
        return True

    def should_attack_when_develop_blocked(self) -> bool:
        return False

    def should_prioritize_police_wake(self) -> bool:
        return True

    def is_terminal_phase(self, phase: DecisionPhase) -> bool:
        """political: CAPTAIN 仍然是 terminal——队长事务最高优先。"""
        return phase.value >= DecisionPhase.SPECIAL_TALENT.value
