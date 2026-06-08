"""duet_mode.py — G2×G5 双人演出模式 AI 行为决策

负责：自适应互惠（合作/竞争/混合三姿态）、按钮/PvP/move 优先级、
      位移目标选择。MVP 暂留 TODO 的函数：投票/Embrace/安可选择。
"""

from __future__ import annotations
import random
from typing import TYPE_CHECKING, List, Optional

from controllers.ai.stage.target_filter import (
    get_opponents,
    pick_best_weapon,
    _DEFAULT_DMG,
)

if TYPE_CHECKING:
    from models.player import Player
    from engine.ish_bosheth import IshBosheth


def assess_duet_stance(player, ish: IshBosheth, game_state) -> str:
    """评估当前轮次 AI 应采取的博弈姿态。

    输入信号（全部公开状态）：
      - 各声部当轮热力增量（累计 - 上轮累计）
      - 总当轮热力 vs 预期（2 按钮 × ~3.0 均伤 = ~6.0）
      - 本声部热力排名
      - Regard 剩余
      - G5 追忆预算（安可预期）

    Returns: "cooperate" | "compete" | "mixed"
    """
    voice = getattr(player, 'emotion', None)

    # 当轮增量（_duet_prev_heat 在 enter_duet 初始化为全0，首轮 total_round=0，无背叛风险）
    prev = getattr(ish, '_duet_prev_heat', {})
    total_round = sum(ish.duet_heat.get(v, 0) - prev.get(v, 0)
                      for v in ish.duet_heat)
    regard = ish.regard
    duet_round = ish.duet_round

    # 本声部排名
    ranking = sorted(ish.duet_heat.items(), key=lambda x: x[1], reverse=True)
    my_rank = next((i for i, (v, _) in enumerate(ranking) if v == voice), 2)

    # G5 安可预期
    g5 = game_state.get_player(ish.duet_g5_pid) if ish.duet_g5_pid else None
    encore_possible = False
    if g5 and g5.talent:
        encore_possible = getattr(g5.talent, 'reminiscence_budget', 0) >= 12.0

    # ── 阈值判定 ──
    high_coop = 4.0   # 大于此 → 大家都在合作
    low_coop = 2.0    # 小于此 → 背叛蔓延

    # 安可修正：合作阈值降低
    if encore_possible:
        high_coop = 2.8
        low_coop = 1.4

    if total_round >= high_coop:
        return "cooperate"
    if total_round < low_coop and regard > 3:
        return "compete"
    if total_round < low_coop and regard <= 3:
        return "cooperate"  # 先保命

    # 中等热度 → 按排名决策
    if my_rank == 0:
        result = "cooperate"
    elif my_rank == 2:
        result = "mixed"
    else:
        result = "cooperate"

    # L2: 姿态评估
    from controllers.ai.stage.stage_ai import StageAI
    encore_str = "可能" if encore_possible else "否"
    StageAI._dbg(2, player,
        f"姿态={result} (当轮热力={total_round:.1f} 排名=#{my_rank+1}/3 Regard={regard:.1f} 安可={encore_str})")
    # L3: 热力完整数据
    StageAI._dbg(3, player,
        f"热力: Acc={ish.duet_heat.get('accarezzevole',0):.1f} "
        f"Ind={ish.duet_heat.get('indifferenza',0):.1f} "
        f"Str={ish.duet_heat.get('strappando',0):.1f}")

    return result


# ================================================================
#  行动决策
# ================================================================

def decide_duet_action(
    player,
    ish: IshBosheth,
    game_state,
    available_actions: List[str],
    threat_scores: Optional[dict] = None,
    assessment: Optional[dict] = None,
) -> str:
    """Duet 模式下的单轮行动决策。

    五级优先级（按 stance 变化）：
      合作: 按钮 > move(去按钮) > forfeit
      竞争: 按钮 > PvP(对立打手) > move > forfeit
      混合: 好武器→按钮, 坏武器→PvP
    """
    if assessment is None:
        stance = assess_duet_stance(player, ish, game_state)
        weapons = getattr(player, 'weapons', [])
    else:
        stance = assessment.get("stance", "cooperate")
        weapons = assessment.get("weapons", [])
    my_seat = getattr(player, 'location', None)
    best_dmg = max((getattr(w, 'get_effective_damage', lambda: _DEFAULT_DMG)() for w in weapons),
                   default=_DEFAULT_DMG)

    # ── 按钮位置 ──
    button_seats = {b.location for b in ish.duet_buttons if hasattr(b, 'is_alive') and b.is_alive()}
    at_button = my_seat in button_seats
    has_button = bool(button_seats)

    # L3: 按钮检查
    from controllers.ai.stage.stage_ai import StageAI
    if button_seats:
        StageAI._dbg(3, player,
            f"按钮: {len(button_seats)}个 座位={button_seats} 在旁={at_button} "
            f"武器伤害={best_dmg:.1f}")

    # ── 合作态 ──
    if stance == "cooperate":
        if at_button and "attack" in available_actions and weapons:
            btn = next(b for b in ish.duet_buttons if b.location == my_seat)
            wname = pick_best_weapon(player)
            return f"attack {btn.name} {wname}" if wname else f"attack {btn.name}"
        if has_button and "move" in available_actions:
            return f"move {random.choice(list(button_seats))}"
        return "forfeit"

    # ── 竞争态 ──
    if stance == "compete":
        if at_button and "attack" in available_actions and weapons:
            btn = next(b for b in ish.duet_buttons if b.location == my_seat)
            wname = pick_best_weapon(player)
            return f"attack {btn.name} {wname}" if wname else f"attack {btn.name}"
        # PvP: 攻击对立声部中在按钮旁的单位
        if "attack" in available_actions and weapons:
            pvp_target = _pick_pvp_target(player, ish, game_state, button_seats)
            if pvp_target:
                wname = pick_best_weapon(player)
                tname = getattr(pvp_target, 'name', str(pvp_target))
                return f"attack {tname} {wname}" if wname else f"attack {tname}"
        # fallback
        if has_button and "move" in available_actions:
            return f"move {random.choice(list(button_seats))}"
        return "forfeit"

    # ── 混合态：在按钮旁优先打按钮（任何热力 > 低伤害 PvP）──
    if at_button and "attack" in available_actions and weapons:
        btn = next(b for b in ish.duet_buttons if b.location == my_seat)
        wname = pick_best_weapon(player)
        return f"attack {btn.name} {wname}" if wname else f"attack {btn.name}"
    if "attack" in available_actions and weapons:
        # 不在按钮旁 → PvP 位移对立声部（好武器主动压制 / 弱武器退而求其次）
        pvp_target = _pick_pvp_target(player, ish, game_state, button_seats)
        if pvp_target:
            wname = pick_best_weapon(player)
            tname = getattr(pvp_target, 'name', str(pvp_target))
            return f"attack {tname} {wname}" if wname else f"attack {tname}"
    if has_button and "move" in available_actions:
        return f"move {random.choice(list(button_seats))}"
    return "forfeit"


def _pick_pvp_target(player, ish: IshBosheth, game_state, button_seats: set):
    """Duet PvP 位移：选择对立声部中可达的、威胁最大的单位。

    远程武器可跨座攻击，近战需同座。过滤不可达目标避免生成无效 attack 指令。
    """
    opponents = get_opponents(player, ish, game_state)
    my_seat = getattr(player, 'location', None)
    # 检查是否有远程武器
    weapons = getattr(player, 'weapons', [])
    from models.equipment import WeaponRange
    has_ranged = any(
        getattr(w, 'weapon_range', None) == WeaponRange.RANGED
        for w in weapons
    )
    # 过滤可达目标（远程可跨座，近战需同座）
    reachable = [o for o in opponents
                 if has_ranged or getattr(o, 'location', None) == my_seat]
    if not reachable:
        return None
    # 优先选在按钮旁的
    at_btn = [o for o in reachable if getattr(o, 'location', None) in button_seats]
    return max(at_btn or reachable, key=lambda o: max(
        (getattr(w, 'get_effective_damage', lambda: 0)() for w in getattr(o, 'weapons', [])),
        default=0))




# ================================================================
#  辅助
# ================================================================

# ================================================================
#  MVP TODO 占位 — 未来迭代
# ================================================================

def vote_duet_entry(player, ish: IshBosheth, game_state, options: List[str]) -> str:
    """Duet 入口投票决策。

    TODO: 未来根据声部优势/HP 安全/安可预期决定。
    当前 MVP: 总是赞成。
    """
    for opt in options:
        if "赞成" in opt:
            return opt
    return options[0] if options else ""


def vote_song(player, ish: IshBosheth, game_state, options: List[str]) -> str:
    """Duet 歌曲投票决策。

    TODO: 未来根据声部排名/安可预期/博弈姿态选择最优节奏。
    当前 MVP: 返回第一个选项。
    """
    return options[0] if options else ""


def decide_embrace(player, ish: IshBosheth, game_state, options: List[str]) -> str:
    """Embrace 选择决策。

    TODO: 未来根据攻防需求（HP/护甲/武器状态）决定。
    当前 MVP: 缺攻→G2, 缺防→G5。简单启发式。
    """
    hp = getattr(player, 'hp', 1.0)
    has_armor = bool(getattr(player, 'armor', None))
    if hp <= 0.5 and not has_armor:
        # 急需防御 → G5（免伤）
        for opt in options:
            if "G5" in opt:
                return opt
    # 默认 → G2（攻击 buff）
    for opt in options:
        if "G2" in opt:
            return opt
    return options[0] if options else ""


def choose_displacement_target(
    player, ish: IshBosheth, game_state, options: List[str]
) -> str:
    """位移目标选择：优先选对立声部中威胁最大的单位所在的座位（推远）。
    当前 MVP: 返回最后一个选项（最远座位）。
    """
    return options[-1] if len(options) > 1 else (options[0] if options else "")


# ================================================================
#  T0 物料牌决策（duet 模式）
# ================================================================

def decide_t0_duet(player, ish: IshBosheth, assessment: dict,
                   playable: list[str]) -> Optional[str]:
    """基于 duet assessment 选择最优牌。

    优先级：
      1. 前排票 → 若不在按钮旁，T0 move 到按钮座，T1 直接 attack
      2. 后台通行证 → Regard 告急时加速谢幕
      3. 荧光棒/聚光合影 → 在按钮旁时预加伤害
      4. 花束 → 同声部队友受伤时回血
      5. 否则不出（保留手牌等下一轮）
    """
    at_button = assessment.get("at_button", False)
    regard = ish.regard

    # 优先级 1: 前排票 → 移动到按钮旁
    if "前排票" in playable and not at_button:
        return "前排票"

    # 优先级 2: 后台通行证 → Regard 低时加速
    if "后台通行证" in playable and regard <= 3:
        return "后台通行证"

    # 优先级 3: 荧光棒 → 在按钮旁时预加伤害
    if "荧光棒" in playable and at_button:
        return "荧光棒"
    if "聚光合影" in playable and at_button:
        return "聚光合影"

    # 优先级 4: 花束 → 同声部队友受伤
    if "花束" in playable:
        from controllers.ai.stage.target_filter import get_teammates
        teammates = get_teammates(player, ish, game_state)
        for t in teammates:
            if getattr(t, 'is_alive', lambda: True)() and getattr(t, 'hp', 0) < 1.0:
                return "花束"

    return None
