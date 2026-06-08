"""normal_mode.py — G2 独唱模式下的 AI 行为决策

负责：目标威胁排序、武器选择、移动判断、forfeit 条件、简单出牌倾向。
由 StageAI 分发，Chorus 和 BasicAI 共用。
"""

from __future__ import annotations
import random
from typing import TYPE_CHECKING, List, Optional, Tuple, Union

from controllers.ai.stage.target_filter import (
    get_legal_normal_targets, get_teammates, get_hand, pick_best_weapon,
    _DEFAULT_DMG,
)

if TYPE_CHECKING:
    from models.player import Player
    from models.chorus import ChorusUnit
    from engine.ish_bosheth import IshBosheth

# 声部常量
from engine.ish_bosheth import ACCAREZZEVOLE, INDIFFERENZA, STRAPPANDO


# ================================================================
#  目标评分
# ================================================================

def rank_targets(
    player,
    legal_targets: list,
    ish: IshBosheth,
    game_state,
    threat_scores: Optional[dict] = None,
) -> List[Tuple[Union[Player, ChorusUnit], float]]:
    """对合法目标按优先级打分排序。

    评分维度（每项 0-50，总分上限 ~150）：
      - threat: 目标威胁度（来自 BasicAI 或武器伤害估算）
      - killable: 当前武器能否在本回合击杀
      - proximity: 是否同地点（不需要 move）
      - break_bonus: Str 攻击 G2 的破幕加成
      - retaliation: 目标 D4 排序惩罚（先手风险）
    """
    scored: list = []
    weapons = getattr(player, 'weapons', [])
    best_dmg = max((getattr(w, 'get_effective_damage', lambda: _DEFAULT_DMG)() for w in weapons),
                   default=_DEFAULT_DMG)
    my_seat = getattr(player, 'location', None)
    voice = getattr(player, 'emotion', None)

    for target in legal_targets:
        score = 0.0

        # 1. 威胁分 (0-50)
        tname = getattr(target, 'name', '')
        tpid = getattr(target, 'player_id', '')
        threat = 0.0
        if threat_scores:
            threat = threat_scores.get(tname, threat_scores.get(tpid, 0))
        else:
            tw = getattr(target, 'weapons', [])
            threat = max((getattr(w, 'get_effective_damage', lambda: 0)() for w in tw),
                         default=0)
        score += min(threat * 15, 50)

        # 2. 可击杀性 (0-40)
        t_hp = getattr(target, 'hp', 0) + getattr(target, 'max_hp', 0) * 0.1  # 轻量估算
        if best_dmg >= t_hp:
            score += 40

        # 3. 同地点 (0-20)
        t_seat = getattr(target, 'location', None)
        if my_seat and t_seat and my_seat == t_seat:
            score += 20

        # 4. 破幕加成 (0-50, 仅 Str→G2)
        if voice == STRAPPANDO and getattr(target, 'player_id', None) == ish.g2_owner_id:
            score += 50

        # 5. 反击风险 (0-10, 目标在 D4 排序中越靠前风险越高)
        winners = getattr(game_state, 'round_winners', [])
        if winners:
            tpos = next((i for i, pid in enumerate(winners) if pid == tpid), len(winners))
            score -= min(tpos, 10)

        scored.append((target, score))

    scored.sort(key=lambda x: x[1], reverse=True)

    # L2: 前 3 名评分明细
    if scored:
        from controllers.ai.stage.stage_ai import StageAI
        top = scored[:3]
        details = " / ".join(
            f"{getattr(t, 'name', str(t))}:{s:.0f}" for t, s in top
        )
        StageAI._dbg(2, player, f"目标排序: {details}")
        # L3: 全部候选
        if len(scored) > 3:
            all_details = ", ".join(
                f"{getattr(t, 'name', str(t))}:{s:.0f}" for t, s in scored
            )
            StageAI._dbg(3, player, f"候选{len(scored)}人: [{all_details}]")

    return scored


# ================================================================
#  行动决策
# ================================================================

def decide_normal_action(
    player,
    ish: IshBosheth,
    game_state,
    available_actions: List[str],
    threat_scores: Optional[dict] = None,
) -> str:
    """正常模式下的单轮行动决策。

    决策链：
      1. 声部过滤 → 合法目标
      2. 有合法目标 → 威胁排序 → 选最优 → 最佳武器 → attack
      3. 无合法目标 → 尝试移动（向队友/猎物聚集）
      4. 否则 → forfeit
    """
    legal = get_legal_normal_targets(player, ish, game_state)

    # 物料牌加成定向：荧光棒等 → 限定目标优先
    card_bonus_target = getattr(player, '_card_damage_bonus_target_id', None)
    card_bonus_voice = getattr(player, '_card_damage_bonus_voice_filter', None)
    if card_bonus_target or card_bonus_voice:
        filtered = []
        for t in legal:
            tpid = getattr(t, 'player_id', None)
            tvoice = getattr(t, 'emotion', None)
            if card_bonus_target and tpid == card_bonus_target:
                filtered.append(t)
            elif card_bonus_voice and tvoice == card_bonus_voice:
                filtered.append(t)
        if filtered:
            legal = filtered

    if legal and "attack" in available_actions:
        ranked = rank_targets(player, legal, ish, game_state, threat_scores)
        best_target, _ = ranked[0]
        my_seat = getattr(player, 'location', None)
        t_seat = getattr(best_target, 'location', None)
        # 检查射程：远程可跨座，近战需同座；不可达则改为 move
        from models.equipment import WeaponRange
        weapons = getattr(player, 'weapons', [])
        has_ranged = any(
            getattr(w, 'weapon_range', None) == WeaponRange.RANGED for w in weapons
        )
        if has_ranged or t_seat == my_seat:
            weapon = pick_best_weapon(player)
            tname = getattr(best_target, 'name', str(best_target))
            if weapon:
                return f"attack {tname} {weapon}"
            return f"attack {tname}"
        # 近战不可达 → 移动到目标座位
        if t_seat and "move" in available_actions:
            return f"move {t_seat}"

    # 无合法目标：移动决策
    if "move" in available_actions:
        move_cmd = _decide_normal_move(player, ish, game_state)
        if move_cmd:
            return move_cmd

    return "forfeit"


def _decide_normal_move(player, ish: IshBosheth, game_state) -> Optional[str]:
    """正常模式下选择移动目标座位。

    优先级: 队友聚集 > 猎物聚集 > 随机座位。
    """
    my_seat = getattr(player, 'location', None)
    available_seats = sorted(ish.SEATS)
    if my_seat in available_seats:
        available_seats.remove(my_seat)
    if not available_seats:
        return None

    # 统计各座位队友/猎物密度
    seat_score = {s: 0 for s in available_seats}
    teammates = get_teammates(player, ish, game_state)
    opponents = get_legal_normal_targets(player, ish, game_state)

    for unit in teammates:
        loc = getattr(unit, 'location', None)
        if loc in seat_score:
            seat_score[loc] += 3  # 队友权重高
    for unit in opponents:
        loc = getattr(unit, 'location', None)
        if loc in seat_score:
            seat_score[loc] += 1

    best_seat = max(seat_score, key=seat_score.get)
    if seat_score[best_seat] > 0:
        return f"move {best_seat}"

    # 无目标 → 随机移动
    return f"move {random.choice(available_seats)}"


# ================================================================
#  出牌判断
# ================================================================

def decide_t0_normal(player, ish: IshBosheth, game_state,
                     assessment: dict, playable: list[str]) -> Optional[str]:
    """正常模式 T0：基于 assessment 选择最优物料牌。

    优先级：
      1. 荧光棒 → 有同座合法目标
      2. 前排票 → 有猎物不在同座
      3. 花束 → 同声部 Chorus 队友受伤
    """
    my_seat = getattr(player, 'location', None)
    if assessment.get("legal_targets"):
        same_seat = [t for t in assessment["legal_targets"]
                     if getattr(t, 'location', None) == my_seat]
        if "荧光棒" in playable and same_seat:
            return "荧光棒"
        if "前排票" in playable and not same_seat:
            return "前排票"

    if "花束" in playable:
        for c in ish.chorus_list:
            if (c.is_alive() and getattr(c, 'emotion', None) == getattr(player, 'emotion', None)
                    and getattr(c, 'hp', 0) < 1.0):
                return "花束"

    return None

