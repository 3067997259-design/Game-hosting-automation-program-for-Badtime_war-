"""喝彩系统·获取（experiment: m6_scoring，v2.0 §4.2）。

喝彩是两用资源：可消耗（M6d）/ 余额计入终分（M6b 已接 final_score）。
获取走机检事件（首杀/重伤反杀/破满配甲/终焉击杀/最后一箭击杀）。

反合谋三闸（§4.2）：
1. 每类每局限 1 次（_applause_events_used 去重）
2. 对抗性事件要求目标"非自愿"（本局对你 damage_relations 有伤害）
3. 合谋 bot 风洞实测（刷得动就改规则）——M6g 探针
"""
from __future__ import annotations
from typing import Any, Optional

from engine import experiments
from engine.balance import get as bget

# 对抗性事件（要求目标本局对受奖者造成过伤害——防友好刷分）
_ADVERSARIAL = {"severe_revenge", "break_full_armor"}


def award(game_state: Any, player: Any, event_key: str,
          target: Optional[Any] = None) -> bool:
    """给玩家发喝彩点。返回是否实际发放（去重/反合谋拦截则 False）。"""
    if not experiments.is_enabled("m6_scoring"):
        return False
    # M9：喝彩语义被 B4 v0.4 的 PP 取代（PP 合并旧喝彩与星光），m9-rfc 不发喝彩。
    if experiments.is_enabled("m9_rfc"):
        return False
    if player is None or not hasattr(player, "player_id"):
        return False

    used = getattr(game_state, "_applause_events_used", None)
    if used is None:
        return False
    # 闸 1：每类每局限 1 次（去重键含 player——每人各自首杀等独立）
    dedup_key = (player.player_id, event_key)
    if dedup_key in used:
        return False

    # 闸 2：对抗性事件要求目标本局对受奖者有伤害
    if event_key in _ADVERSARIAL and target is not None:
        rel = getattr(game_state, "damage_relations", {})
        attackers_of_player = rel.get(player.player_id, set())
        if getattr(target, "player_id", None) not in attackers_of_player:
            return False  # 目标没打过你 → 疑似合谋，不发

    points = bget("applause", event_key, default=0)
    if points <= 0:
        return False

    # M6 往世层·加冕：下一个喝彩事件分值 ×2（自消耗）
    if getattr(player, "_coronation_active", False):
        player._coronation_active = False
        points *= 2

    used.add(dedup_key)
    player.applause = getattr(player, "applause", 0) + points
    _record_applause_location(game_state, player)
    game_state.log_event("applause", player=player.player_id,
                         event=event_key, points=points)
    return True


def _record_applause_location(game_state: Any, player: Any) -> None:
    """记录喝彩发生的(轮次,地点)到侧表（供 G5 追忆水源筛地点）。

    不写进 event_log —— golden 摘要只采 event_log，侧表不污染回放基线（m6 冻结）。
    """
    rec = getattr(game_state, "_round_applause", None)
    if rec is None:
        rec = []
        game_state._round_applause = rec
    rec.append((getattr(game_state, "current_round", 0),
                getattr(player, "location", None)))


def check_kill_applause(game_state: Any, killer: Any, victim: Any,
                        cause: Optional[str] = None,
                        weapon_arrows_left: Optional[int] = None) -> None:
    """击杀时的喝彩机检集合（死亡总线/攻击善后调用）。"""
    if not experiments.is_enabled("m6_scoring") or killer is None:
        return
    # M9：喝彩被 B4 v0.4 的 PP 取代，m9-rfc 不机检喝彩事件。
    if experiments.is_enabled("m9_rfc"):
        return
    from combat.numeric_v2 import is_severely_injured

    # 首杀：全局第一个击杀（去重键全局唯一，用特殊 player=None 标记）
    used = getattr(game_state, "_applause_events_used", None)
    if used is not None and ("__global__", "first_kill") not in used:
        used.add(("__global__", "first_kill"))
        pts = bget("applause", "first_kill", default=2)
        if pts > 0:
            killer.applause = getattr(killer, "applause", 0) + pts
            _record_applause_location(game_state, killer)
            game_state.log_event("applause", player=killer.player_id,
                                 event="first_kill", points=pts)

    # 重伤反杀：击杀时自己处于重伤（HP≤5）
    if is_severely_injured(killer):
        award(game_state, killer, "severe_revenge", target=victim)

    # 终焉击杀
    if cause == "apocalypse_phase" or _is_apocalypse(game_state):
        award(game_state, killer, "apocalypse_kill", target=victim)

    # 最后一箭击杀
    if weapon_arrows_left == 0:
        award(game_state, killer, "last_arrow_kill", target=victim)


def _is_apocalypse(game_state: Any) -> bool:
    if not experiments.is_enabled("m5_clock"):
        return False
    from engine import world_clock
    return world_clock.is_phase(game_state, world_clock.APOCALYPSE)
