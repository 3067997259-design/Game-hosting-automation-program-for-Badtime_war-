"""
MythlandAIHook —— G3「神话之外」天赋AI钩子

覆盖决策：
  get_command（正常回合）:
    - 结界内 → 只允许攻击对方 / forfeit

  choose（选择决策）:
    - talent_t0 → 发育完成+同地点有目标→发动
    - mythland_pick_target → 选威胁最高的目标
    - mythland_rps → 纯随机
"""

from __future__ import annotations
from typing import Dict, List, Optional, Any
import random
from controllers.ai.talents.base_hook import BaseTalentAIHook
from controllers.ai.constants import debug_ai_basic


class MythlandAIHook(BaseTalentAIHook):
    talent_name = "神话之外"

    def __init__(self, controller: Any):
        self._ctrl = controller

    # ════════════════════════════════════════════════════════
    #  get_command：结界内行动覆盖
    # ════════════════════════════════════════════════════════

    def should_override_candidates(
        self, player: Any, state: Any, available: List[str]
    ) -> Optional[List[str]]:
        """结界内：只允许攻击对方 / forfeit"""
        barrier = getattr(state, 'active_barrier', None)
        if not barrier or not self._is_in_barrier(barrier, player.player_id):
            return None  # 不在结界内，不接管

        other = self._get_other_barrier_player(barrier, player, state)
        if not other or not other.is_alive():
            debug_ai_basic(player.name, "G3结界内：对方已死亡，forfeit")
            return ["forfeit"]

        # 选最佳近战武器攻击对方（考虑护甲属性克制）
        weapon = self._pick_best_melee(player, opponent=other)
        if weapon and "attack" in available:
            debug_ai_basic(player.name,
                f"G3结界内：攻击 {other.name} 使用 {weapon.name}")
            return [f"attack {other.name} {weapon.name}", "forfeit"]

        debug_ai_basic(player.name, "G3结界内：无可用的近战武器（全部被克制或无效），forfeit")
        return ["forfeit"]

    # ════════════════════════════════════════════════════════
    #  choose：启动时机 / 目标选择 / 猜拳
    # ════════════════════════════════════════════════════════

    def handle_choose(
        self, player: Any, state: Any, situation: str,
        options: List[str], context: Dict,
    ) -> Optional[str]:
        """处理 G3 相关的 choose 决策。返回 None 表示走默认逻辑。"""

        # ── 结界猜拳：纯随机 ──
        if situation == "mythland_rps":
            return random.choice(options)

        # ── 结界选拉入目标：旧架构按威胁分选择最高目标 ──
        if situation == "mythland_pick_target":
            threat_scores = context.get("threat_scores", {})
            player_opts = [o for o in options if o != "不拉人"]
            if player_opts:
                return max(player_opts, key=lambda name: threat_scores.get(name, 0))
            return "不拉人"

        # ── 天赋T0：是否发动 ──
        if situation == "talent_t0":
            talent_name = context.get("talent_name", "")
            if "幻想乡" not in talent_name and "神话之外" not in talent_name:
                return None
            return self._decide_activation(player, options)

        return None  # 不处理其他情况

    def _decide_activation(self, player: Any, options: List[str]) -> Optional[str]:
        """G3 发动决策：受保护队长优先，否则发育完成且同地点有目标才发动。"""
        state = getattr(self._ctrl, '_game_state', None)
        if not state:
            for opt in options:
                if "不发动" in opt or "正常" in opt:
                    return opt
            return options[-1] if options else None

        same_loc = self._ctrl._get_same_location_targets(player, state)
        if not same_loc:
            for opt in options:
                if "不发动" in opt or "正常" in opt:
                    return opt
            return options[-1] if options else None

        # 同地点有受警察保护的队长 → 拉入结界（警察保护无效）
        for t in same_loc:
            if getattr(t, 'is_captain', False):
                pe = getattr(state, 'police_engine', None)
                if pe and pe.is_protected_by_police(t.player_id):
                    for opt in options:
                        if "发动" in opt:
                            return opt

        if self._ctrl._is_development_complete(player, state):
            for opt in options:
                if "发动" in opt:
                    return opt

        for opt in options:
            if "不发动" in opt or "正常" in opt:
                return opt
        return options[-1] if options else None

    # ════════════════════════════════════════════════════════
    #  工具方法
    # ════════════════════════════════════════════════════════

    @staticmethod
    def _is_in_barrier(barrier, player_id: str) -> bool:
        if hasattr(barrier, 'is_in_barrier'):
            return barrier.is_in_barrier(player_id)
        players = getattr(barrier, 'barrier_players', [])
        return player_id in players

    @staticmethod
    def _get_other_barrier_player(barrier, player, state):
        """获取结界内的另一个玩家对象"""
        if hasattr(barrier, 'barrier_players'):
            for pid in barrier.barrier_players:
                if pid != player.player_id:
                    return state.get_player(pid)
        return None

    @staticmethod
    def _pick_best_melee(player, opponent=None) -> Optional[Any]:
        """选最佳近战武器（结界内只能近战），考虑对手护甲属性克制"""
        weapons = [w for w in getattr(player, 'weapons', [])
                   if w and not getattr(w, '_hexagram_disabled', False)]
        if not weapons:
            return None

        # 获取对手护甲属性（用于判定克制关系）
        from controllers.ai.constants import EFFECTIVE_AGAINST
        opponent_attrs = set()
        if opponent:
            armor = getattr(opponent, 'armor', None)
            if armor and hasattr(armor, 'get_all_active'):
                for piece in armor.get_all_active():
                    attr = getattr(piece, 'attribute', None)
                    if attr:
                        opponent_attrs.add(attr)

        best = None
        best_score = -999
        for w in weapons:
            rng = getattr(w, 'range', 'melee')
            if rng not in ('melee', 'area'):
                continue
            dmg = MythlandAIHook._get_damage(w)
            score = dmg * 10
            # 属性克制判定
            w_attr = getattr(w, 'attribute', None)
            if opponent_attrs:
                effective_set = EFFECTIVE_AGAINST.get(w_attr, set())
                if any(a in effective_set for a in opponent_attrs):
                    score += 50  # 克制对手护甲
                else:
                    score -= 500  # 被对手护甲克制 → 几乎无效
            if score > best_score:
                best_score = score
                best = w
        return best

    @staticmethod
    def _estimate_combat_power(p) -> float:
        """战力评估：HP + 外甲 + 内甲 + 武器伤害，越小越弱"""
        power = float(getattr(p, 'hp', 1.0))
        armor = getattr(p, 'armor', None)
        if armor and hasattr(armor, 'get_active'):
            from models.equipment import ArmorLayer
            power += len(armor.get_active(ArmorLayer.OUTER))
            power += len(armor.get_active(ArmorLayer.INNER))
        for w in getattr(p, 'weapons', []):
            if w:
                dmg = getattr(w, 'base_damage', 0)
                if isinstance(dmg, (int, float)):
                    power += dmg
        return power

    @staticmethod
    def _get_damage(weapon) -> float:
        if not weapon:
            return 0.0
        if hasattr(weapon, 'get_effective_damage'):
            return weapon.get_effective_damage()
        return getattr(weapon, 'base_damage', 1.0)
