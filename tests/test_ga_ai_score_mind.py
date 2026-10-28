"""收局估计与正式往世分红/终局快照对照，保留已死亡的评分竞争者。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from engine.ga.ai.forecast import estimate_option
from engine.ga.ai.minds import ScoreMind
from engine.ga.combat import resolve_batch
from engine.ga.contracts import DamageKind, thaw_json
from engine.ga.observation import public_value
from engine.ga.state import add_armor, observe
from tests.ga_i2_helpers import ready
from tests.test_ga_combat import packet


def endgame(*, count: int = 3, cursor: int = 0) -> Any:
    """只构造合法押注前置与残局血量；真正死亡和分红仍由正式解析器执行。"""
    state = ready(count=count)
    state.players["b"].hp = 1
    for key in sorted(state.players)[2:]:
        state.players[key].hp = 0
        state.players[key].pp = 12
        state.ga_afterlife.bets[key] = "a"
    state.ga_afterlife.remainder_cursor = cursor
    return state


def compare_terminal(state: Any, *, break_armor: bool = False, actor: str = "a") -> float:
    """真实声明效果先估计，再正式结算；终分分差必须一致。"""
    if break_armor:
        key = add_armor(state, "b", "shield")
        state.players["b"].defenses[key] = replace(state.players["b"].defenses[key], durability=1)
    effect = packet(actor=actor, targets=("b",), amount=4)
    view = thaw_json(observe(state, actor))
    # 允许本测试在观察字段接线之前独立验证；内容来自同一公开账本。
    view["afterlife"]["break_rounds"] = public_value(state.ga_afterlife.break_rounds)
    quote = estimate_option(view, {"declared_effects": [public_value(effect)]}, state.ga_config)
    assert quote.kill_probability == 1 and quote.expected_breaks.is_integer()
    predicted = ScoreMind.terminal_margin(view, actor, "b", quote.score_gain, state.ga_config,
                                          breaks=int(quote.expected_breaks))
    resolve_batch(state, (effect,))
    assert state.terminal_snapshot is not None
    scores = state.terminal_snapshot.results
    actual = scores[actor].total_score - max(result.total_score for key, result in scores.items()
                                            if key != actor)
    assert predicted == actual
    return actual


def test_kill_pool_can_turn_a_predicted_tie_into_a_real_loss() -> None:
    state = endgame()
    assert compare_terminal(state) == -2
    assert state.terminal_snapshot.results["a"].total_score == 14
    assert state.terminal_snapshot.results["c"].total_score == 16
    assert state.players["b"].pp == 0  # 本击新死者没有既存押注，不加入分红者。


@pytest.mark.parametrize("cursor", (0, 1, 2))
@pytest.mark.parametrize("break_armor", (False, True))
@pytest.mark.parametrize("break_used", (False, True))
def test_pool_order_and_shared_remainder_cursor_match_terminal(
    cursor: int, break_armor: bool, break_used: bool,
) -> None:
    state = endgame(count=5, cursor=cursor)
    # 不同原有钱包让余数发给谁影响最高对手，而非只比较总池守恒。
    state.players["c"].pp = 12
    state.players["d"].pp = 13
    state.players["e"].pp = 11
    if break_used:
        state.ga_afterlife.break_rounds.add(("a", state.current_round))
    compare_terminal(state, break_armor=break_armor)


def test_absolute_dead_and_exited_bets_do_not_receive_the_pools() -> None:
    state = endgame(count=5)
    state.players["c"].absolute_dead = True
    state.players["d"].exited = True
    compare_terminal(state)
    assert state.players["c"].pp == state.players["d"].pp == 12
    assert state.players["e"].pp == 16


def test_bets_for_the_dying_target_do_not_receive_the_winners_kill_or_crown() -> None:
    state = endgame()
    state.ga_afterlife.bets["c"] = "b"
    assert compare_terminal(state) == 2
    assert state.players["c"].pp == 12


def test_dead_damage_owner_cannot_award_a_crown_to_an_unqualified_survivor() -> None:
    state = endgame()
    state.ga_afterlife.bets.clear()
    # 已死c的持续伤害击杀b；a既无击杀也无章节，活到最后仍没有王冠。
    state.players["c"].pp = 0
    assert compare_terminal(state, actor="c") == 8
    assert state.terminal_snapshot.results["a"].total_score == 0


def test_qualified_survivor_crown_pool_and_dead_owner_kill_pool_are_separate() -> None:
    state = endgame(count=4)
    state.players["a"].kills = 1
    state.players["c"].pp = 0
    state.ga_afterlife.bets["c"] = "a"
    state.ga_afterlife.bets["d"] = "c"  # 已死目标不是合法新押注，模拟旧押注遗留。
    compare_terminal(state, actor="c")
    assert state.players["c"].pp == 4  # 击杀自身PP2，押中a合格王冠2。
    assert state.players["d"].pp == 14  # 押中c的后续归属击杀池2。


def test_nonterminal_or_invalid_kill_has_no_terminal_bonus() -> None:
    state = ready()
    view = observe(state, "a")
    assert ScoreMind.terminal_margin(view, "a", "b", 8, state.ga_config) is None
    assert ScoreMind.terminal_margin(view, "a", "a", 8, state.ga_config) is None
    assert ScoreMind.terminal_margin(view, "a", "unknown", 8, state.ga_config) is None


def test_current_score_lead_includes_dead_competitors() -> None:
    state = endgame()
    situation = ScoreMind.assess(observe(state, "a"), state.ga_config)
    assert situation.score_lead == -12 and situation.enemies == ("b",)
