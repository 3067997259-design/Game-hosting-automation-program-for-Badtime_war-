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
        # 深挖：political 每存活轮攻击率全场 1.49× 但装备差（interact 只有
        # 全场一半）——凭证是警察线道具，对战斗无用；武器/护甲优先
        # （R42 修正：voucher 由首位降至末位）。
        return ["weapon", "outer_armor", "voucher"]

    def is_development_complete(self, player, state,
                                count_outer_armor, count_inner_armor,
                                has_real_weapon, has_pass, has_stealth, real_weapon_count):
        """political 的发育完成取决于队长身份与警察体系状态。

        M9：队长身份在 `m9_police.captain_id`（r2_tick 上任时回写
        `player.is_captain`）。自己当上队长 → 基础装备齐即完成；
        队长被他人占据或警务停机 → 政治路线无回报，按 balanced 战斗
        路线放行（基础装备齐即完成），否则 political 会永远卡在
        发育未完成、永不进入主战斗入口。
        """
        if getattr(state, "m9_enabled", False):
            station = getattr(state, "m9_police", None)
            captain = station.captain_id if station is not None else None
            is_captain = captain == getattr(player, "player_id", None)
            if is_captain:
                return has_real_weapon and count_outer_armor(player) >= 1
            # 他人队长 / 警务停机 / 非 T6 竞选窗口（R5）过后仍无队长 →
            # 政治路线无回报，按基础装备放行战斗。T6（朝阳好市民）的
            # 胜利路径就是警察线，永不降级（R35 实测：降级对 T6 是
            # 负优化，8.2→6.9）。
            talent = getattr(player, "talent", None)
            is_t6 = False
            if talent is not None:
                try:
                    from controllers.ai.decision.snapshot import _slot_id_for
                    is_t6 = _slot_id_for(talent) == "T6"
                except Exception:
                    is_t6 = "朝阳" in str(getattr(talent, "name", ""))
            round_num = int(getattr(state, "current_round", 0) or 0)
            if (captain is not None
                    or (station is not None and station.is_disabled())
                    or (not is_t6 and round_num >= 5)):
                return has_real_weapon and count_outer_armor(player) >= 1
            return False
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
        """political: 警察系统可用时BUILD, 犯罪且有外部队长时RESIST, 否则IGNORE"""
        station = getattr(state, "m9_police", None)
        if getattr(state, "m9_enabled", False) and station is not None:
            if station.is_disabled():
                return "ignore"
            captain_id = getattr(station, "captain_id", None)
            wanted = station.open_wanted()
            if (wanted is not None
                    and getattr(wanted, "suspect_id", None)
                    == getattr(player, "player_id", None)):
                return "resist" if captain_id not in (
                    None, getattr(player, "player_id", None)) else "ignore"
            if captain_id not in (None, getattr(player, "player_id", None)):
                return "ignore"
            return "build"
        if self._is_criminal(player, state):
            police = getattr(state, 'police', None)
            if police and police.has_captain() and police.captain_id != player.player_id:
                return "resist"
            return "ignore"
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

    # ── 战斗：补刀偏好（深挖发现：political 每存活轮攻击率 1.49× 全场，
    #    但击杀转化只有一半——装备弱，靠收割残血/无甲目标把攻击变击杀。
    #    R40 半强度版验证有效（T6 6.9→8.8）；R41 加满刺客强度反而回退
    #    （T6 8.2）——满档让 political 过度追杀残血，半强度为甜点）──
    def modify_target_score(self, target, base_score, player,
                            players_who_attacked, is_passive, target_power):
        hp = getattr(target, 'hp', 2.0)
        score = base_score + max(0, 3 - hp) * 10
        armor = getattr(target, 'armor', None)
        if armor and hasattr(armor, 'get_active'):
            from models.equipment import ArmorLayer
            if not armor.get_active(ArmorLayer.OUTER):
                score += 20
        return score

    def should_attack_when_develop_blocked(self) -> bool:
        return False

    def should_prioritize_police_wake(self) -> bool:
        return True

    def is_terminal_phase(self, phase: DecisionPhase) -> bool:
        """political: CAPTAIN 仍然是 terminal——队长事务最高优先。"""
        return phase.value >= DecisionPhase.SPECIAL_TALENT.value
