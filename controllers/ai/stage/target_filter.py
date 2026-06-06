"""target_filter.py — 声部合法目标过滤（纯函数，零依赖）

G2 舞台内所有非 G2 攻击者的目标选择规则，被 normal_mode / duet_mode 共同引用。
"""

from __future__ import annotations
from typing import TYPE_CHECKING, List, Union

if TYPE_CHECKING:
    from models.player import Player
    from models.chorus import ChorusUnit
    from engine.ish_bosheth import ButtonDummy, IshBosheth

# 声部常量（统一来源）
from engine.ish_bosheth import ACCAREZZEVOLE, INDIFFERENZA, STRAPPANDO


def get_legal_normal_targets(
    player, ish: IshBosheth, game_state
) -> List[Union[Player, ChorusUnit]]:
    """正常模式（G2 独唱）下的合法攻击目标。

    Acc → Str 真实 + Str Chorus + 持牌 Chorus
    Str → Acc 真实 + Acc Chorus + G2 + G2 投影
    Ind → 持牌 Chorus（禁止攻击真实玩家）
    """
    voice = getattr(player, 'emotion', None)
    targets: list = []
    g2_owner_id = ish.g2_owner_id
    my_id = player.player_id

    # ── 真实玩家 ──
    for pid in ish.participants:
        p = game_state.get_player(pid)
        if not p or not p.is_alive() or not p.is_on_map():
            continue
        if p.player_id == my_id:
            continue
        p_voice = getattr(p, 'emotion', None)

        if voice == ACCAREZZEVOLE:
            if p_voice == STRAPPANDO:
                targets.append(p)
        elif voice == STRAPPANDO:
            if p_voice == ACCAREZZEVOLE:
                targets.append(p)
        elif voice == INDIFFERENZA:
            pass  # Ind 不攻击真实玩家
        else:
            if p.player_id != g2_owner_id:
                targets.append(p)

    # ── Str 可攻击 G2 ──
    if voice == STRAPPANDO:
        g2p = game_state.get_player(g2_owner_id)
        if g2p and g2p.is_alive() and g2p.is_on_map():
            targets.append(g2p)

    # ── Chorus ──
    for c in ish.chorus_list:
        if not c.is_alive() or c.player_id == my_id:
            continue
        c_voice = getattr(c, 'emotion', None)

        if voice == ACCAREZZEVOLE:
            if c_voice == STRAPPANDO:
                targets.append(c)
            elif _holds_card(c, ish):
                targets.append(c)
        elif voice == STRAPPANDO:
            if c_voice == ACCAREZZEVOLE:
                targets.append(c)
        elif voice == INDIFFERENZA:
            if _holds_card(c, ish):
                targets.append(c)
        else:
            targets.append(c)

    return targets


def get_legal_duet_targets(
    player, ish: IshBosheth, game_state
) -> List[Union[Player, ChorusUnit]]:
    """Duet 模式下的合法目标列表。

    按钮优先（get_command 层处理），此函数返回 PvP 位移的合法目标：
    对立声部的所有真实玩家和 Chorus。
    """
    voice = getattr(player, 'emotion', None)
    if voice == ACCAREZZEVOLE:
        opponent_voice = STRAPPANDO
    elif voice == STRAPPANDO:
        opponent_voice = ACCAREZZEVOLE
    else:
        opponent_voice = None  # Ind: 只能打按钮

    targets: list = []
    my_id = player.player_id
    g2_owner_id = ish.g2_owner_id

    for pid in ish.participants:
        p = game_state.get_player(pid)
        if not p or not p.is_alive() or not p.is_on_map():
            continue
        if p.player_id == my_id:
            continue
        if getattr(p, 'emotion', None) == opponent_voice:
            targets.append(p)

    for c in ish.chorus_list:
        if not c.is_alive() or c.player_id == my_id:
            continue
        if getattr(c, 'emotion', None) == opponent_voice:
            targets.append(c)

    return targets


def get_teammates(
    player, ish: IshBosheth, game_state
) -> List[Union[Player, ChorusUnit]]:
    """获取同声部所有存活单位。"""
    voice = getattr(player, 'emotion', None)
    if not voice:
        return []
    result: list = []
    my_id = player.player_id

    for pid in ish.participants:
        p = game_state.get_player(pid)
        if p and p.is_alive() and p.player_id != my_id:
            if getattr(p, 'emotion', None) == voice:
                result.append(p)
    for c in ish.chorus_list:
        if c.is_alive() and c.player_id != my_id:
            if getattr(c, 'emotion', None) == voice:
                result.append(c)
    return result


def get_opponents(
    player, ish: IshBosheth, game_state
) -> List[Union[Player, ChorusUnit]]:
    """获取对立声部所有存活单位。"""
    voice = getattr(player, 'emotion', None)
    if voice == ACCAREZZEVOLE:
        opp = STRAPPANDO
    elif voice == STRAPPANDO:
        opp = ACCAREZZEVOLE
    else:
        return []  # Ind 无对立声部

    result: list = []
    my_id = player.player_id
    for pid in ish.participants:
        p = game_state.get_player(pid)
        if p and p.is_alive() and p.player_id != my_id:
            if getattr(p, 'emotion', None) == opp:
                result.append(p)
    for c in ish.chorus_list:
        if c.is_alive() and c.player_id != my_id:
            if getattr(c, 'emotion', None) == opp:
                result.append(c)
    return result


def _holds_card(unit, ish: IshBosheth) -> bool:
    """检查单位是否持有物料牌（用于 Ind 的攻击筛选）。"""
    if ish.deck is None:
        return False
    if getattr(unit, 'is_chorus', False):
        return bool(ish.deck.chorus_slots.get(unit.player_id))
    return bool(ish.deck.hands.get(unit.player_id))
