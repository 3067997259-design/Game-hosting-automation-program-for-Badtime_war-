"""玩家手册算例守护：手册里写出的每个数字例子都必须由生产代码算出同样结果。

对应 docs/v2.0-ga/manual_draft_v0.1.md 第五章（伤害与死亡）、第六章（SP 与演出）、第七章（计分）。
抢公演位的时序另由 tests/test_ga_runtime.py 的 GA-003～005 守护。
引擎规则或 N0 数值变化导致本文件失败时，先改手册里的例子，再改这里的期望值。
"""

from __future__ import annotations

import pytest

from engine.ga.combat import attack_output, resolve_batch
from engine.ga.contracts import DamageKind, DefenseKind
from engine.ga.extensions import chapter
from engine.ga.state import add_armor, finish_if_needed, record_death
from tests.ga_i2_helpers import execute, ready, turn
from tests.test_ga_combat import arena, armor, barrier, packet


def test_ch05_sharpened_knife_original_damage() -> None:
    """§5.2：磨过的小刀，基础 4 加磨刀 1，原始伤害 A = 5。"""
    knife = arena().ga_config.get("weapons", "knife")
    assert attack_output(knife["damage"] + knife["sharpen_bonus"], 1, 0, 0) == 5


def test_ch05_finite_barrier_passes_the_rest_through() -> None:
    """§5.1：6 点打 4 点屏障，屏障碎掉，剩 2 点进本体。"""
    state = arena()
    barrier(state, 4, DefenseKind.FINITE_BARRIER)
    record, = resolve_batch(state, (packet("barrier", amount=6),))
    assert record.hp_loss == 2


def test_ch05_breaking_hit_barrier_takes_the_whole_hit() -> None:
    """§5.1：架盾剩 3 挨 6，盾碎、本人扣 0；挨 1 点也扣 1 耐久。"""
    state = arena()
    barrier(state, 3, DefenseKind.BREAKING_HIT_BARRIER)
    record, = resolve_batch(state, (packet("big", amount=6),))
    assert (record.hp_loss, state.players["p1"].defenses["barrier"].durability) == (0, 0)

    state = arena()
    barrier(state, 3, DefenseKind.BREAKING_HIT_BARRIER)
    resolve_batch(state, (packet("small", amount=1),))
    assert state.players["p1"].defenses["barrier"].durability == 2


def test_ch05_floor_does_not_refill_barrier_damage() -> None:
    """§5.2：A=8 打 7 点屏障，只剩 1 点进本体，保底不补成 2。"""
    state = arena()
    barrier(state, 7, DefenseKind.FINITE_BARRIER)
    record, = resolve_batch(state, (packet("floor", amount=8),))
    assert record.hp_loss == 1


def test_ch05_bow_against_n0_shield() -> None:
    """§5.2：弓（4）连射拿着 N0 盾牌（普通 2 / 耐久 6）的人。"""
    state = arena()
    key = add_armor(state, "p1", "shield")
    losses, durability = [], []
    for index in range(4):
        record, = resolve_batch(state, (packet(f"bow{index}", amount=4),))
        losses.append(record.hp_loss)
        durability.append(state.players["p1"].defenses[key].durability)
    assert losses == [2, 2, 2, 4]
    assert durability == [4, 2, 0, 0]


def test_ch05_knife_against_n0_ceramic() -> None:
    """§5.3：小刀（4）连砍 N0 陶瓷护甲（普通 3 / 耐久 10）。"""
    state = arena()
    add_armor(state, "p1", "ceramic")
    losses = [resolve_batch(state, (packet(f"knife{index}", amount=4),))[0].hp_loss
              for index in range(4)]
    assert losses == [1, 1, 1, 3]


@pytest.mark.parametrize(
    ("attack", "defense", "durability", "price", "hp_loss", "wear"),
    [
        (4, 3, 10, 1, 1, 3),
        (4, 3, 1, 1, 3, 1),
        (10, 6, 1, 1, 9, 1),
        (4, 3, 4, 2, 2, 4),
        (4, 3, 3, 2, 2, 3),
        (4, 1, 3, 2, 3, 2),
    ],
)
def test_ch05_single_armor_table(
    attack: int, defense: int, durability: int, price: int, hp_loss: int, wear: int,
) -> None:
    """§5.3 算例表：单件甲，一般价格与双倍磨损。"""
    state = arena()
    armor(state, defense=defense, durability=durability)
    parameters = {"wear_price": price} if price > 1 else {}
    record, = resolve_batch(state, (packet("row", amount=attack, **parameters),))
    assert (record.hp_loss, record.armor_changes[0].ordinary_wear) == (hp_loss, wear)


def test_ch05_one_armor_per_hit() -> None:
    """§5.3 多件甲：只用实际挡得最多的一件，碎了也不接力。"""
    state = arena()
    first = armor(state, defense=3, durability=10)
    second = armor(state, defense=2, durability=10)
    record, = resolve_batch(state, (packet("pair", amount=8),))
    defenses = state.players["p1"].defenses
    assert (record.hp_loss, defenses[first].durability, defenses[second].durability) == (5, 7, 10)

    state = arena()
    brittle = armor(state, defense=6, durability=2)
    sturdy = armor(state, defense=1, durability=10)
    record, = resolve_batch(state, (packet("brittle", amount=8),))
    defenses = state.players["p1"].defenses
    assert (record.hp_loss, defenses[brittle].durability, defenses[sturdy].durability) == (6, 0, 10)


@pytest.mark.parametrize(("reduction", "hp_loss", "wear"), [(4, 2, 2), (6, 2, 0)])
def test_ch05_flat_reduction_before_armor(reduction: int, hp_loss: int, wear: int) -> None:
    """§5.3：固定减伤先扣，外甲只为剩下的部分付耐久。"""
    state = arena()
    armor(state, defense=4, durability=10)
    state.players["p1"].flat_reduction = reduction
    record, = resolve_batch(state, (packet("reduced", amount=8),))
    paid = record.armor_changes[0].ordinary_wear if record.armor_changes else 0
    assert (record.hp_loss, paid) == (hp_loss, wear)


def test_ch05_attached_wear_after_the_hit() -> None:
    """§5.3：附加磨甲在本击之后扣同一件甲，不增加本击扣血。"""
    state = arena()
    armor(state, defense=3, durability=4)
    record, = resolve_batch(state, (packet("auto", amount=6, attached_wear=2),))
    change = record.armor_changes[0]
    assert (record.hp_loss, change.ordinary_wear, change.attached_wear, change.after) == (3, 3, 1, 0)


def test_ch05_pure_wear() -> None:
    """§5.3：纯磨甲不扣血、余量作废；有能挡它的屏障时先磨屏障。"""
    state = arena()
    key = armor(state, defense=3, durability=1)
    record, = resolve_batch(state, (packet("wear", amount=2, kind=DamageKind.PURE_WEAR),))
    assert (record.hp_loss, state.players["p1"].defenses[key].durability) == (0, 0)

    state = arena()
    key = armor(state, defense=3, durability=5)
    barrier(state, 1, DefenseKind.FINITE_BARRIER)
    resolve_batch(state, (packet("shielded", amount=2, kind=DamageKind.PURE_WEAR),))
    assert state.players["p1"].defenses[key].durability == 5


def test_ch05_direct_damage_skips_armor() -> None:
    """§5.4：直达伤害跳过外甲。"""
    state = arena()
    armor(state, defense=3, durability=10)
    record, = resolve_batch(state, (packet("direct", amount=4, kind=DamageKind.DIRECT),))
    assert record.hp_loss == 4


def test_ch05_effective_damage_boundaries() -> None:
    """§5.7：屏障整个挡下且没碎、纯磨甲没打碎，都不算有效伤害。"""
    state = arena()
    barrier(state, 5, DefenseKind.FINITE_BARRIER)
    record, = resolve_batch(state, (packet("blocked", amount=4),))
    assert not record.effective_hit

    state = arena()
    armor(state, defense=3, durability=5)
    record, = resolve_batch(state, (packet("scratch", amount=2, kind=DamageKind.PURE_WEAR),))
    assert not record.effective_hit


def test_ch06_attention_once_per_round() -> None:
    """§6.2 例子：攻击双方各 +1；同一轮再挨打不再涨；下一轮能再涨。"""
    state = ready(count=3)
    for actor in ("a", "b", "c"):
        state.players[actor].sp = 0
    turn(state, "a")
    execute(state, "attack", targets=("b",), weapon="knife")
    assert (state.players["a"].sp, state.players["b"].sp) == (1, 1)
    turn(state, "c")
    execute(state, "attack", targets=("b",), weapon="knife")
    assert (state.players["c"].sp, state.players["b"].sp) == (1, 1)
    state.current_round += 1
    turn(state, "a")
    execute(state, "attack", targets=("b",), weapon="knife")
    assert state.players["b"].sp == 2


def test_ch06_full_sp_does_not_use_the_round_quota() -> None:
    """§6.2：SP 已满时挨打不占当轮额度，花掉之后同一轮再挨打还能涨。"""
    state = ready(count=3)
    state.players["b"].sp = 2
    turn(state, "a")
    execute(state, "attack", targets=("b",), weapon="knife")
    assert state.players["b"].sp == 2
    state.players["b"].sp = 0
    turn(state, "c")
    execute(state, "attack", targets=("b",), weapon="knife")
    assert state.players["b"].sp == 1


def test_ch06_kill_pp_is_not_capped() -> None:
    """§6.6 / §7.3：一次范围攻击杀两人，击杀 PP 拿两份。"""
    state = arena(3)
    for target in ("p1", "p2"):
        state.players[target].hp = 1
    resolve_batch(state, (packet("aoe", amount=4, targets=("p1", "p2")),))
    assert (state.players["p0"].kills, state.players["p0"].pp) == (2, 4)


def test_ch07_chapter_gives_one_pp_once() -> None:
    """§7.4：完成一章拿 1 PP，同一章不重复。"""
    state = arena()
    assert chapter(state, "p0", 1) and not chapter(state, "p0", 1)
    assert state.players["p0"].pp == 1


def test_ch07_score_card_example() -> None:
    """§7.1 例子：2 杀、23 伤害、2 破甲、2 章、3 PP、最后存活者 = 36。"""
    state = arena()
    me = state.players["p0"]
    me.kills, me.hp_damage, me.armor_breaks, me.pp = 2, 23, 2, 3
    me.chapters = {"chapter/1", "chapter/2"}
    record_death(state, "p1", absolute=False)
    assert finish_if_needed(state)
    assert state.terminal_snapshot.results["p0"].total_score == 36


def test_ch07_crown_needs_a_kill_or_two_chapters() -> None:
    """§7.1：0 击杀、只有 1 章的最后存活者没有王冠。"""
    state = arena()
    state.players["p0"].chapters = {"chapter/1"}
    record_death(state, "p1", absolute=False)
    finish_if_needed(state)
    assert state.terminal_snapshot.results["p0"].total_score == 4


def test_ch07_no_crown_when_several_survive() -> None:
    """§7.1 / §7.5：兜底结束时好几个人活着，没有王冠，也没有最后存活者。"""
    state = arena()
    state.players["p0"].kills = 1
    finish_if_needed(state, round_limit=True)
    assert state.terminal_snapshot.results["p0"].total_score == 6
    assert state.winner is None


def _system(state, name: str):
    """取已安装的公共系统实例（警察、往世层），只用于读取和触发其钩子。"""
    hooks = {"Police": "R4.police", "Afterlife": "R0"}
    return next(h.__self__ for h in state.ga_hooks[hooks[name]]
                if type(getattr(h, "__self__", None)).__name__ == name)


def test_ch08_hurting_a_player_opens_a_case_with_witnesses() -> None:
    """§8.1 例子：在商店砍乙一刀，目击者是在场的你、乙、丙。"""
    state = ready(count=3)
    state.players["b"].moved = False
    turn(state, "a")
    execute(state, "attack", targets=("b",), weapon="knife")
    assert state.players["b"].hp == 16
    case = state.ga_crimes[-1]
    assert (case["actor_id"], case["victim_id"], case["witness_ids"]) == ("a", "b", ["a", "b", "c"])


def test_ch08_volley_example() -> None:
    """§8.2 例子：满编 5 点、少 1 名特警 4 点；每轮只打一次。"""
    for dead_officers, expected in ((0, 15), (1, 16)):
        state = ready(count=2)
        police = _system(state, "Police")
        target = state.players["b"]
        target.location, target.moved = "police", False
        for index in range(dead_officers):
            state.ga_units[f"npc/police/{index}"].hp = 0
        state.ga_police.wanted.append("b")
        police.r4(state)
        assert target.hp == expected
        police.r4(state)
        assert target.hp == expected


def test_ch09_bet_split_example() -> None:
    """§9.2 例子：两人押中各 1；三人押中先给前两人，下次从第三人接着轮。"""
    state = bare_state(5)
    afterlife = _system(state, "Afterlife")
    for ghost in ("p1", "p2", "p3"):
        record_death(state, ghost, absolute=False)
    afterlife_state = state.ga_afterlife
    afterlife_state.bets.update({"p1": "p0", "p2": "p0"})
    afterlife.distribute(state, "kill-a", "p0", 2)
    assert [state.players[g].pp for g in ("p1", "p2")] == [1, 1]

    state = bare_state(5)
    afterlife = _system(state, "Afterlife")
    for ghost in ("p1", "p2", "p3"):
        record_death(state, ghost, absolute=False)
    state.ga_afterlife.bets.update({"p1": "p0", "p2": "p0", "p3": "p0"})
    afterlife.distribute(state, "kill-b", "p0", 2)
    assert [state.players[g].pp for g in ("p1", "p2", "p3")] == [1, 1, 0]
    afterlife.distribute(state, "kill-c", "p0", 2)
    assert [state.players[g].pp for g in ("p1", "p2", "p3")] == [2, 1, 1]


def test_ch09_blessing_barrier_lasts_until_next_round_r4() -> None:
    """§9.3：赐福的 3 点屏障撑过当轮 R4，到下一轮 R4 才消失。"""
    from engine.ga.actions import GrantKind
    from engine.ga.contracts import RoundPhase, TurnStage
    from engine.ga.status import expire_r4

    state = ready(count=3)
    record_death(state, "c", absolute=False)
    state.players["c"].pp = 2
    state.current_round += 1
    state.current_phase = RoundPhase.R0
    turn(state, "c", TurnStage.T0, kind=GrantKind.AFTERLIFE)
    execute(state, "afterlife_glow", targets=("a",), kind="barrier", payer="c")
    key = "afterlife/glow/a"
    assert state.players["a"].defenses[key].durability == 3
    assert state.players["c"].pp == 0
    expire_r4(state)
    assert key in state.players["a"].defenses
    state.current_round += 1
    expire_r4(state)
    assert key not in state.players["a"].defenses


@pytest.mark.parametrize(("accelerated", "paused", "hp"), [(False, False, 17), (True, False, 14),
                                                          (False, True, 20)])
def test_ch10_apocalypse_damage_example(accelerated: bool, paused: bool, hp: int) -> None:
    """§10.2 例子：刻度 18 时终焉伤害 3 点，加速后 6 点，倒流期间 0 点。"""
    from engine.ga.runtime import _r4

    state = arena()
    state.world_tick = 18
    state.ga_clock_accelerated, state.ga_clock_paused = accelerated, paused
    _r4(state)
    assert [state.players[p].hp for p in ("p0", "p1")] == [hp, hp]


def test_ch10_clock_steps() -> None:
    """§10.2：第 1 轮刻度 1，之后每轮 +1；加速后 +2；倒流期间不动，实际轮数照常 +1。"""
    from engine.ga.controllers import MinimalController
    from engine.ga.runtime import _round_start

    state = bare_state(2)
    controllers = {actor: MinimalController() for actor in state.players}
    ticks = []
    for accelerated, paused in ((False, False), (False, False), (True, False), (True, True)):
        state.ga_clock_accelerated, state.ga_clock_paused = accelerated, paused
        _round_start(state, controllers)
        ticks.append((state.current_round, state.world_tick))
    assert ticks == [(1, 1), (2, 2), (3, 4), (4, 4)]


def bare_state(count: int):
    """不经起床出门的最小正式状态，只用于直接调用公共系统。"""
    from engine.game_state import GameState

    state = GameState.new_ga([f"p{i}" for i in range(count)], seed=0)
    for player in state.players.values():
        player.awake = player.home_locked = True
        player.location = "shop"
    return state


def _options(state, actor: str, operation: str, stage=None):
    """读取生产候选里的某个演出选项，不执行。"""
    from engine.ga.contracts import TurnStage
    from engine.ga.gameplay import build_options

    stage = stage or TurnStage.T1
    turn(state, actor, stage)
    return [o for o in build_options(state, state.current_grant, stage)
            if o.request.operation == operation]


def test_ch11_t1_slash_damage_and_public_bypass() -> None:
    """§11.1：小刀 8、磨过的小刀 9、拳头 4；双倍磨损；公演无视屏障。"""
    from engine.ga.contracts import BarrierPolicy, Performance

    state = ready({"a": "T1"})
    state.players["a"].sp = 2
    amounts = {(o.request.parameters["weapon"], o.request.performance): o.effects[0]
               for o in _options(state, "a", "t1_slash")
               if o.request.target_ids == ("b",)}
    knife = amounts[("knife", Performance.INSTANT)]
    assert (knife.amount, knife.parameters["wear_price"]) == (8, 2)
    assert amounts[("fist", Performance.INSTANT)].amount == 4
    assert amounts[("knife", Performance.PUBLIC)].barrier_policy is BarrierPolicy.BYPASS_NUMERIC
    state.players["a"].sharpened = True
    sharpened = [o.effects[0].amount for o in _options(state, "a", "t1_slash")
                 if o.request.parameters["weapon"] == "knife"]
    assert set(sharpened) == {9}


def test_ch11_t2_performance_bonus() -> None:
    """§11.2：偷袭用小刀是 4 + 1 = 5。"""
    from engine.ga.contracts import Performance

    state = ready({"a": "T2"})
    strikes = [o.effects[0].amount for o in _options(state, "a", "t2_strike")
               if o.request.parameters["weapon"] == "knife"
               and o.request.performance is Performance.INSTANT]
    assert strikes and set(strikes) == {5}


def test_ch11_t3_starfall_and_jade() -> None:
    """§11.3：天星 4 点、外甲防御当 0 且不磨甲、目标石化；玉璋 3 点屏障。"""
    from engine.ga.contracts import Performance, TurnStage

    state = ready({"a": "T3"})
    shield = add_armor(state, "b", "shield")
    state.players["a"].sp = 2
    turn(state, "a")
    execute(state, "t3_starfall", mode=Performance.PUBLIC)
    target = state.players["b"]
    assert (target.hp, target.defenses[shield].durability) == (16, 6)
    assert target.statuses["control"].name == "PETRIFIED"

    state = ready({"a": "T3"})
    turn(state, "a", TurnStage.T0)
    execute(state, "t3_jade", mode=Performance.INSTANT)
    assert state.players["a"].defenses["t3/jade/a"].durability == 3


def test_ch11_t4_double_scissors_pierce() -> None:
    """§11.4：双剪刀「潜龙勿用」6 点，外甲防御当 0、不磨甲。"""
    from engine.ga.contracts import Performance
    from tests.ga_i2_helpers import Decisions

    state = ready({"a": "T4"})
    shield = add_armor(state, "b", "shield")
    state.players["b"].moved = False
    state.ga_controllers["a"] = Decisions(hexagram=["scissors"])
    state.ga_controllers["b"] = Decisions(hexagram=["scissors"])
    turn(state, "a")
    execute(state, "t4_hexagram", targets=("b",), mode=Performance.INSTANT)
    target = state.players["b"]
    assert (target.hp, target.defenses[shield].durability) == (14, 6)


def test_ch11_t7_insurance_and_prayer() -> None:
    """§11.6：保险救回来 HP = 最大 HP 的一半（10）、SP 0；祝祷回 3 HP。"""
    from engine.ga.contracts import Performance

    state = ready({"a": "T7"})
    turn(state, "a")
    execute(state, "t7_insure", targets=("b",), mode=Performance.INSTANT)
    target = state.players["b"]
    target.sp = 2
    resolve_batch(state, (packet("lethal", amount=30, targets=("b",), actor="c"),))
    assert (target.alive, target.hp, target.sp) == (True, 10, 0)
    state.players["a"].sp = 1
    turn(state, "a")
    execute(state, "t7_prayer", targets=("b",), mode=Performance.INSTANT)
    assert target.hp == 13
