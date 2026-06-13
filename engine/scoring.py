"""综合评分制纯函数（experiment: m6_scoring，v2.0 §4）。

终分 = (剧情分 + 喝彩净值 + 战果分) × 存活系数 + 往世分
- 战果分：每击杀 +kill_score；每累计 damage_per_point 有效伤害 +1（上限 damage_cap）
- 存活系数：终局存活 ×survival_alive；中途死亡 ×max(survival_floor, 存活轮/总轮)
- 往世分：星光行动所挣，固定 ×afterlife_mult 折算

"活到最后只是加分项"——存活系数让存活者终分更高，但杀人多的死者仍可凭战果分
+往世分翻盘。胜负 = 终分最高者（round_manager game_over 后重定义）。
"""
from __future__ import annotations
import math
from typing import Any

from engine.balance import get as bget


def _sc(key: str, default: Any) -> Any:
    return bget("scoring", key, default=default)


def battle_score(player: Any) -> int:
    """战果分（§4.1 拍板）：击杀×kill_score + ⌈伤害/damage_per_point⌉（上限 damage_cap）。"""
    kills = getattr(player, "kill_count", 0)
    dmg = getattr(player, "damage_dealt", 0)
    per = _sc("damage_per_point", 20)
    cap = _sc("damage_cap", 5)
    dmg_score = min(cap, dmg // per) if per > 0 else 0
    return kills * _sc("kill_score", 3) + dmg_score


def survival_coefficient(player: Any, total_rounds: int) -> float:
    """存活系数：终局存活 ×survival_alive；死者 ×max(floor, 存活轮/总轮)。

    比例式——自适应轮次变化（M7/M8 后轮次改变也免疫）。
    """
    if player.is_alive():
        return float(_sc("survival_alive", 1.5))
    floor = float(_sc("survival_floor", 0.5))
    death_round = getattr(player, "death_round", 0)
    if total_rounds <= 0 or death_round <= 0:
        return floor
    return max(floor, death_round / total_rounds)


def final_score(player: Any, total_rounds: int) -> float:
    """综合终分。组件：剧情分（完结条）+ 喝彩 + 战果，×存活系数，+ 往世分。"""
    story = getattr(player, "story_score", 0)
    applause = getattr(player, "applause", 0)
    battle = battle_score(player)
    base = (story + applause + battle) * survival_coefficient(player, total_rounds)
    afterlife = getattr(player, "afterlife_score", 0) * float(_sc("afterlife_mult", 0.5))
    return round(base + afterlife, 2)


def compute_all(game_state: Any) -> dict:
    """全玩家终分（终局揭晓）。返回 {pid: score}。"""
    total = getattr(game_state, "current_round", 1)
    scores = {}
    for pid in game_state.player_order:
        p = game_state.get_player(pid)
        if p is not None and not getattr(p, "is_chorus", False):
            scores[pid] = final_score(p, total)
    return scores
