"""十四诗篇通过真实献诗动作验证接受权、有限标记与永久救赎。"""

from dataclasses import replace

import pytest

from engine.game_state import GameState
from engine.ga.contracts import Performance, TurnStage
from engine.ga.extensions import ChoicePending
from engine.ga.gameplay import legal_actions
from engine.ga.poems import POEMS
from engine.ga.runtime import run_round
from engine.ga.state import add_armor
from engine.ga.status import effective_hit, put_status
from tests.ga_i2_helpers import Decisions, execute, ready, turn


def offer(state: GameState, target: str = "b", *, paid_sp: int = 2) -> None:
    """明确前置资源后，仍从实际候选预检支付与执行。"""
    giver = state.ga_roles["a"]
    giver.stage, giver.memory = 2, 12
    state.players["a"].sp = 2
    state.ga_controllers[target] = Decisions(poem_offer=["accept"])
    turn(state, "a")
    execute(state, "g5_poem", targets=(target,), mode=Performance.MAJOR_INSTANT, paid_sp=paid_sp)


@pytest.mark.parametrize("slot", tuple(POEMS))
def test_all_fourteen_poems_have_a_real_paid_action_and_explicit_role_adapter(slot: str) -> None:
    state = ready({"a": "G5", "b": slot}) if slot != "G5" else ready({"a": "G5"})
    offer(state, "a" if slot == "G5" else "b")
    if slot == "G5":
        assert state.ga_roles["a"].arrow_growth == 1
        assert not state.ga_poems.accepted
    else:
        assert ("b", slot) in state.ga_poems.accepted
        assert ("b", "a") in state.ga_poems.love
        context = next(c for c, _ in state.ga_controllers["b"].seen if c["kind"] == "poem_offer")
        assert context["benefit"] and context["permanent_loss"] and context["love"]
    assert not state.ga_public_used


def test_unanswered_poem_is_pending_and_sp_memory_not_refunded_or_autoaccepted() -> None:
    state = ready({"a": "G5", "b": "T1"})
    role = state.ga_roles["a"]
    role.stage, role.memory = 2, 6
    state.players["a"].sp = 1
    state.ga_controllers["b"] = Decisions(poem_offer=[None])
    turn(state, "a")
    with pytest.raises(ChoicePending):
        execute(state, "g5_poem", targets=("b",), mode=Performance.MAJOR_INSTANT, paid_sp=1)
    assert role.memory == 4 and state.players["a"].sp == 0 and not state.ga_roles["b"].ranger
    assert not state.ga_poems.accepted


def test_love_blocks_harm_is_not_a_status_and_giver_attack_breaks_it() -> None:
    state = ready({"a": "G5", "b": "T1"})
    offer(state)
    state.players["b"].statuses.clear()
    turn(state, "b")
    assert not any("a" in a.target_ids and a.verb.value == "attack" for a in legal_actions(state))
    turn(state, "a")
    execute(state, "attack", targets=("b",), weapon="knife")
    assert ("b", "a") not in state.ga_poems.love
    turn(state, "b")
    assert any(a.target_ids == ("a",) and a.verb.value == "attack" for a in legal_actions(state))


def test_love_future_rounds_continue_under_paused_world_clock_and_two_player_exemption() -> None:
    state = ready({"a": "G5", "b": "T1"})
    offer(state)
    state.ga_clock_paused = True
    for _ in range(3):
        run_round(state, state.ga_controllers)
    assert ("b", "a") in state.ga_poems.love
    run_round(state, state.ga_controllers)
    assert not state.ga_poems.love and state.world_tick == 1
    duel = ready({"a": "G5", "b": "T1"}, count=2)
    offer(duel)
    assert not duel.ga_poems.love


def test_firefly_rescues_propagation_permanently_without_armor_penalties_or_future_expiry() -> None:
    state = ready({"a": "G5", "b": "G1"})
    role = state.ga_roles["b"]
    role.form, role.full_opened = "full", True
    state.players["b"].hp = 1
    turn(state, "c")
    execute(state, "attack", targets=("b",), weapon="knife")
    assert role.form == "propagation"
    offer(state)
    assert role.rescued and role.form == "unarmored" and role.form_timer is None
    assert state.players["b"].hp == 12 and role.entropy == 0
    before = state.players["b"].max_hp
    offer(state)
    assert state.players["b"].max_hp == before and state.players["b"].hp == 12
    turn(state, "b", TurnStage.T0)
    assert not any(a.operation in ("g1_sam", "g1_full", "g1_supernova")
                   for a in legal_actions(state))


def test_firefly_safe_window_has_zero_entropy_growth_and_no_forced_expiry_settlement() -> None:
    state = ready({"a": "G5", "b": "G1"})
    offer(state)
    role = state.ga_roles["b"]
    hp, entropy = state.players["b"].hp, role.entropy
    for _ in range(2):
        run_round(state, state.ga_controllers)
    assert role.form == "unarmored" and state.players["b"].hp == hp and role.entropy == entropy


def test_spotlight_reactor_rescue_does_not_reverse_halving_or_permanent_acceleration() -> None:
    state = ready({"a": "G5", "b": "G2"})
    role = state.ga_roles["b"]
    role.black = 10
    role.fuse(state)
    assert role.form == "reactor" and not state.players["b"].targetable
    offer(state)
    assert role.form == "echo" and state.players["b"].targetable and state.players["b"].healable
    assert state.ga_clock_accelerated and not state.ga_world.rain
    assert state.players["a"].max_hp == state.players["b"].max_hp == 10
    hp = state.players["b"].hp
    run_round(state, state.ga_controllers)
    assert state.players["b"].hp == hp


def test_watchman_converts_remaining_extra_hp_once_and_does_not_invent_unfused_shield() -> None:
    state = ready({"a": "G5", "b": "G7"})
    role = state.ga_roles["b"]
    role.enter_terror(state)
    before = role.extra_hp
    offer(state)
    assert role.color is None and role.form == "swimsuit" and role.extra_hp == 0
    assert state.players["b"].max_hp == 20 + before
    assert not role.shield_fused and role.shield_id not in state.players["b"].defenses
    offer(state)
    assert state.players["b"].max_hp == 20 + before
    assert "terror/b" not in state.ga_police.stopped


def test_ranger_chases_actual_lock_and_independently_refunds_only_once() -> None:
    state = ready({"a": "G5", "b": "T1"})
    offer(state)
    state.players["c"].location = "magic"
    state.players["c"].hp = 1
    state.players["b"].sp = 2
    turn(state, "b", TurnStage.T0)
    execute(state, "lock", targets=("c",))
    state.current_stage = TurnStage.T1
    execute(state, "t1_slash", targets=("c",), mode=Performance.PUBLIC, weapon="knife")
    assert state.players["b"].location == "magic" and state.players["b"].sp == 1
    assert not state.ga_roles["b"].ranger and len(state.ga_roles["b"].refunded) == 1


def test_stars_lock_is_not_released_by_two_hits_and_next_starfall_gets_two_meteors() -> None:
    state = ready({"a": "G5", "b": "T3"}, count=4)
    put_status(state.players["c"], state.ga_config, state.current_round, "PETRIFIED",
               source_id="fixture", actor_id="b")
    offer(state)
    effective_hit(state.players["c"], state.ga_config)
    effective_hit(state.players["c"], state.ga_config)
    assert state.players["c"].statuses["control"].anchored
    state.players["b"].sp = 2
    turn(state, "b")
    execute(state, "t3_starfall", mode=Performance.PUBLIC)
    meteors = [r for _, rs in state.ga_resolution_batches.values() for r in rs if
               r.effect.source_id == "poem_stars"]
    assert len(meteors) == 2 and sum(r.effect.amount for r in meteors) == 4
    assert state.ga_roles["b"].meteors == 0


def test_yin_yang_two_casts_choose_only_non_extra_results_without_opponent_rps() -> None:
    state = ready({"a": "G5", "b": "T4"})
    offer(state)
    state.ga_controllers["b"] = Decisions(poem_hexagram=["pierce", "immune"])
    for _ in range(2):
        state.players["b"].sp = 1
        turn(state, "b")
        execute(state, "t4_hexagram", targets=("c",), mode=Performance.INSTANT)
    assert state.ga_roles["b"].yin_yang == 0 and not state.ga_pending_extras
    assert not any(context["kind"] == "hexagram" for context, _ in state.ga_controllers["c"].seen)


def test_law_rebuilds_one_roster_but_preserves_active_rain_stop() -> None:
    state = ready({"a": "G5", "b": "T6", "c": "G2"})
    state.ga_roles["c"].fuse(state)
    state.destroyed_locations.add("police")
    for npc in state.ga_units.values():
        npc.hp = 0
    offer(state)
    assert "police" not in state.destroyed_locations and len(state.ga_units) == 3
    assert all(p.hp == 6 and p.submerged_sources for p in state.ga_units.values())
    assert state.ga_police.attack_upgrade == state.ga_police.wall_upgrade == 1
    assert "rain" in state.ga_police.stopped


def test_shore_early_boost_and_late_single_rearm_never_produce_a_third_payout() -> None:
    state = ready({"a": "G5", "b": "T7"})
    offer(state)
    state.players["b"].sp = 1
    turn(state, "b")
    execute(state, "t7_insure", targets=("b",), mode=Performance.INSTANT)
    state.players["b"].hp = 1
    turn(state, "c")
    execute(state, "attack", targets=("b",), weapon="knife")
    assert state.players["b"].sp == 2 and state.ga_roles["b"].used == 1
    assert any(d.durability == 6 for d in state.players["b"].defenses.values())
    offer(state)
    role = state.ga_roles["b"]
    assert role.capacity == 2 and state.ga_insurance == {"owner": "b", "target": "b", "full": False}
    state.players["b"].defenses.clear()
    state.players["b"].hp = 1
    turn(state, "c")
    execute(state, "attack", targets=("b",), weapon="knife")
    assert role.used == 2
    offer(state)
    assert role.capacity == 2 and state.ga_insurance is None
