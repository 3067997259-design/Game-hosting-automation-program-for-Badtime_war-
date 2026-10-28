"""GA实际调度验收：完整对局、原子终局、状态、公演与行动授予。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

import pytest

from engine.game_state import GameState
from engine.ga.actions import ActionDefinition, GrantKind
from engine.ga.combat import resolve_batch
from engine.ga.controllers import HumanController, MinimalController, ScriptController
from engine.ga.contracts import (
    ActionPlan, ActionRequest, ActionVerb, BarrierPolicy, ContractError, DamageAttribute,
    DamageKind, DeathPolicy, DefenseKind, DefenseSpec, EffectKind, EffectSpec, HitPolicy,
    IsolationPolicy, Performance, TargetScope, TerminalReason, TurnStage,
)
from engine.ga.events import EventKind
from engine.ga.gameplay import ActionOption, build_options, legal_actions
from engine.ga.runtime import queue_extra_turn, queue_followup, run_game, run_round
from engine.ga.state import ActionGrant, add_armor, finish_if_needed, touch
from engine.ga.status import add_burns, apply_status


class PassiveController(MinimalController):
    """起床出门后明确放弃，隔离环境/时序机制，不用于平衡评估。"""

    def __init__(self, primary: str | None = None, small: str | None = None) -> None:
        self.primary = primary
        self.small = small
        self.seen: list[tuple[TurnStage, tuple[ActionRequest, ...]]] = []

    def choose(
        self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...], stage: TurnStage,
    ) -> ActionRequest | None:
        self.seen.append((stage, actions))
        if stage is TurnStage.T0:
            return next((a for a in actions if a.operation in ("wake", self.small)), None)
        public = next((a for a in actions if a.performance is Performance.PUBLIC), None)
        return public or next((a for a in actions if a.operation == self.primary), None) or next(
            (a for a in actions if a.operation == "forfeit"), actions[0]
        )

    def escape_petrify(self, view: Mapping[str, Any], attempt: int) -> bool:
        return False


def ready(count: int = 3, *, seed: int = 0) -> GameState:
    """所有人已出门的可运行前置状态，余下流程使用生产调度。"""
    state = GameState.new_ga([chr(97 + i) for i in range(count)], seed=seed)
    for player in state.players.values():
        player.awake = player.home_locked = True
        player.location = "shop"
        player.weapons.update({"knife", "bow"})
        player.ammo["bow"] = 5
    return state


def effect(
    request: ActionRequest, targets: tuple[str, ...], amount: int, *, suffix: str = "hit",
) -> EffectSpec:
    """测试注册动作具备真实伤害/支付语义，不引入生产无天赋演出。"""
    return EffectSpec(f"{request.root_id}/{suffix}", "fixture", request.root_id,
                      f"{request.root_id}/batch/{suffix}", request.actor_id, request.actor_id,
                      request.actor_id, targets, EffectKind.DAMAGE, DamageKind.NORMAL, amount,
                      DamageAttribute.NORMAL,
                      TargetScope.GLOBAL_AREA if len(targets) > 1 else TargetScope.RANGED_SINGLE,
                      HitPolicy.CERTAIN, BarrierPolicy.RESPECT, IsolationPolicy.RESPECT,
                      DeathPolicy.NORMAL)


def option(
    grant: ActionGrant, stage: TurnStage, name: str, *, public: bool = False,
    after: Callable[[GameState, ActionPlan], None] | None = None, free: bool = False,
    targets: tuple[str, ...] = (),
) -> ActionOption:
    """通过同一注册协议提供夹具动作。"""
    mode = Performance.PUBLIC if public else Performance.NONE
    request = ActionRequest(grant.actor_id, grant.grant_id, f"{grant.grant_id}/{stage.value}",
                            stage, ActionVerb.ATTACK if targets else ActionVerb.SPECIAL,
                            name, targets, {}, mode)
    definition = ActionDefinition(name, stage, request.verb, frozenset({mode}), (),
                                  len(targets), len(targets), consumes_stage=not free)
    return ActionOption(request, definition, after=after, hostile=bool(targets))


def public_provider(
    state: GameState, grant: ActionGrant, stage: TurnStage,
) -> tuple[ActionOption, ...]:
    """仅测试用演出注册；正式无天赋菜单不拥有此技能。"""
    targets = tuple(k for k in sorted(state.players) if k != grant.actor_id and
                    state.players[k].alive)
    if stage is not TurnStage.T1 or not targets:
        return ()
    candidate = option(grant, stage, "fixture_public", public=True, targets=(targets[0],))
    return (replace(candidate, effects=(effect(candidate.request, (targets[0],), 1),)),)


@pytest.mark.parametrize("count", [2, 4, 6])
def test_real_no_talent_games_reach_terminal_with_every_actor_accounted(count: int) -> None:
    state = GameState.new_ga([f"p{i}" for i in range(count)], seed=11)
    result = run_game(state, {p: MinimalController() for p in state.players})
    assert result.reason is TerminalReason.LAST_SURVIVOR
    assert len(result.results) == count and result.ruleset == "v2.0-ga"
    assert result.parameter_fingerprint == state.ga_config.fingerprint
    assert state.current_round < 40 and state.winner in state.players
    assert all(p.awake and p.home_locked for p in state.players.values())
    assert not any(r.request.performance is not Performance.NONE
                   for r in state.ga_executed_requests.values())


def test_human_boundary_uses_identical_candidates_and_can_finish_game() -> None:
    state = GameState.new_ga(["a", "b"], seed=8)
    minimal = MinimalController()
    seen = []

    def select(
        view: Mapping[str, Any], actions: tuple[ActionRequest, ...], stage: TurnStage,
    ) -> ActionRequest | None:
        seen.append(actions)
        assert actions == legal_actions(state)
        return minimal.choose(view, actions, stage)

    result = run_game(state, {"a": HumanController(select), "b": MinimalController()})
    assert result.reason is TerminalReason.LAST_SURVIVOR and seen


def test_explicit_scripts_run_all_the_way_to_simultaneous_environment_death() -> None:
    choices = [{"operation": "wake", "choice": "knife"},
               {"operation": "move", "location": "shop"},
               *[item for _ in range(20) for item in (None, {"operation": "forfeit"})]]
    state = GameState.new_ga(["a", "b"], seed=3)
    terminal = run_game(state, {a: ScriptController(choices) for a in state.players})
    assert terminal.reason is TerminalReason.ALL_DEAD and state.current_round == 21
    assert all(result.total_score == 0 for result in terminal.results.values())


def test_script_exhaustion_is_explicit_failure_without_ai_fallback() -> None:
    state = GameState.new_ga(["a", "b"])
    with pytest.raises(ContractError, match="exhausted"):
        run_game(state, {a: ScriptController([]) for a in state.players})
    assert not state.game_over and not state.ga_ledger.events


def test_ga003_round_initial_public_is_r3_first_after_r2() -> None:
    state = ready()
    state.players["b"].sp = 2
    state.ga_action_providers.append(public_provider)
    run_round(state, {p: PassiveController() for p in state.players})
    starts = [e for e in state.ga_trace if e["label"] == "slot_start"]
    assert starts[0]["actor"] == "b"
    labels = [e["label"] for e in state.ga_trace]
    assert labels.index("R2") < labels.index("slot_start")
    receipts = [r for r in state.ga_executed_requests.values()
                if r.request.performance is Performance.PUBLIC]
    assert len(receipts) == 1 and sum(c.amount for c in receipts[0].costs if c.account == "sp") == 2
    assert [e["actor"] for e in starts].count("b") == 1


def test_ga003_invalid_winner_replaced_without_sp_or_standard_slot_loss() -> None:
    state = ready()
    state.players["a"].sp = state.players["b"].sp = 2
    state.ga_action_providers.append(public_provider)

    def suppress_first(s: GameState) -> None:
        apply_status(s, "a", "ACTION_SUPPRESSED", source_id="R2-fixture")

    state.ga_hooks["R2"] = [suppress_first]
    run_round(state, {p: PassiveController() for p in state.players})
    starts = [e for e in state.ga_trace if e["label"] == "slot_start"]
    assert starts[0]["actor"] == "b"
    assert state.players["a"].sp == 2
    assert state.ga_standard_grants["a"].finished
    assert not any(r.request.actor_id == "a" for r in state.ga_executed_requests.values())


def test_ga004_chain_and_followup_finish_before_public_then_extra() -> None:
    state = ready()
    state.ga_action_providers.append(public_provider)

    def after(s: GameState, plan: ActionPlan) -> None:
        assert queue_extra_turn(s, "a", "poem_nightwalk")
        assert queue_extra_turn(s, "a", "t4_or_yue")
        assert queue_followup(s, "a", "fixture-followup")

    def chain(s: GameState, grant: ActionGrant, stage: TurnStage) -> tuple[ActionOption, ...]:
        if grant.actor_id != "a" or stage is not TurnStage.T1 or grant.kind is not (
            GrantKind.STANDARD
        ):
            return ()
        candidate = option(grant, stage, "fixture_chain", after=after, targets=("b",))
        return (replace(candidate, effects=(
            effect(candidate.request, ("b",), 1, suffix="first"),
            effect(candidate.request, ("b",), 1, suffix="second"),
        )),)

    state.ga_action_providers.append(chain)
    run_round(state, {"a": PassiveController("fixture_chain"),
                      "b": PassiveController(), "c": PassiveController()})
    starts = [e for e in state.ga_trace if e["label"] == "slot_start"]
    assert [(e["actor"], e["kind"]) for e in starts[:4]] == [
        ("a", "standard"), ("a", "step_followup"), ("b", "standard"), ("a", "extra_turn")
    ]
    assert starts[3]["source_id"] == "t4_or_yue"
    first_public_index = next(i for i, e in enumerate(state.ga_trace) if
                              e["label"] == "slot_start" and e["actor"] == "b")
    assert sum(e["label"] == "combat_batch" for e in state.ga_trace[:first_public_index]) == 2
    assert [e["actor"] for e in starts if e["kind"] == "standard"].count("b") == 1
    assert not any(e["label"] == "T2" and e["kind"] == "step_followup" for e in state.ga_trace)


def test_ga005_t0_full_sp_can_use_current_t1_without_second_slot() -> None:
    state = ready()
    state.ga_action_providers.append(public_provider)

    def boost(s: GameState, plan: ActionPlan) -> None:
        s.players[plan.request.actor_id].sp = 2
        touch(s)

    def provider(s: GameState, grant: ActionGrant, stage: TurnStage) -> tuple[ActionOption, ...]:
        return (option(grant, stage, "fixture_boost", after=boost),) if (
            grant.actor_id == "a" and stage is TurnStage.T0
        ) else ()

    state.ga_action_providers.append(provider)
    run_round(state, {"a": PassiveController(small="fixture_boost"),
                      "b": PassiveController(), "c": PassiveController()})
    public = [r for r in state.ga_executed_requests.values()
              if r.request.performance is Performance.PUBLIC]
    assert len(public) == 1 and public[0].request.actor_id == "a"
    assert sum(e["label"] == "slot_start" and e["actor"] == "a" for e in state.ga_trace) == 1


def test_free_setup_does_not_consume_t0_and_is_not_repeated() -> None:
    state = ready()
    calls = []

    def declare(s: GameState, plan: ActionPlan) -> None:
        calls.append(plan.request.actor_id)
        touch(s)

    def provider(s: GameState, grant: ActionGrant, stage: TurnStage) -> tuple[ActionOption, ...]:
        return (option(grant, stage, "fixture_setup", free=True, after=declare),) if (
            stage is TurnStage.T0 and grant.actor_id == "a"
        ) else ()

    state.ga_action_providers.append(provider)
    controller = PassiveController(small="fixture_setup")
    original = controller.choose

    def choose(
        view: Mapping[str, Any], actions: tuple[ActionRequest, ...], stage: TurnStage,
    ) -> ActionRequest | None:
        if stage is TurnStage.T0:
            return next((a for a in actions if a.operation == "fixture_setup"), None) or next(
                (a for a in actions if a.operation == "lock"), None
            )
        return original(view, actions, stage)

    controller.choose = choose
    run_round(state, {"a": controller, "b": PassiveController(), "c": PassiveController()})
    assert calls == ["a"]
    assert state.ga_standard_grants["a"].occupied == {TurnStage.T0, TurnStage.T1}


def test_extra_cannot_recurse_and_followup_has_no_t0_or_performance() -> None:
    state = ready()
    results = []

    def t2(s: GameState) -> None:
        grant = s.current_grant
        if grant.actor_id == "a":
            results.append((grant.kind, queue_extra_turn(s, "a", "poem_nightwalk")))
            queue_followup(s, "a", "fixture")

    state.ga_hooks["T2"] = [t2]
    state.ga_action_providers.append(public_provider)
    controllers = {p: PassiveController() for p in state.players}
    run_round(state, controllers)
    assert results == [(GrantKind.STANDARD, True), (GrantKind.EXTRA_TURN, False)]
    assert sum(e["label"] == "slot_start" and e["kind"] == "extra_turn"
               for e in state.ga_trace) == 1
    extra = ActionGrant("extra-fixture", "a", GrantKind.EXTRA_TURN)
    followup = ActionGrant("followup-fixture", "a", GrantKind.STEP_FOLLOWUP)
    state.players["a"].sp = 2
    assert not build_options(state, followup, TurnStage.T0)
    for grant in (extra, followup):
        assert not any(o.request.performance is Performance.PUBLIC
                       for o in build_options(state, grant, TurnStage.T1))


def test_unregistered_extra_source_fails_explicitly() -> None:
    state = ready()
    with pytest.raises(ContractError, match="unregistered"):
        queue_extra_turn(state, "a", "retired_g4_extra")


def test_ga007_later_segment_failure_keeps_first_payment_and_result() -> None:
    state = ready()
    seen = []

    def after(s: GameState, plan: ActionPlan) -> None:
        first_hp = s.players["b"].hp
        from engine.ga.gameplay import validate_request
        bad = replace(plan.request, operation="retired_segment")
        rejected = validate_request(s, bad)
        seen.append((first_hp, rejected.accepted, s.players["a"].ammo["bow"]))

    def provider(s: GameState, grant: ActionGrant, stage: TurnStage) -> tuple[ActionOption, ...]:
        if grant.actor_id != "a" or stage is not TurnStage.T1:
            return ()
        candidate = option(grant, stage, "fixture_partial", after=after, targets=("b",))
        return (replace(candidate, effects=(effect(candidate.request, ("b",), 3),)),)

    state.ga_action_providers.append(provider)
    run_round(state, {"a": PassiveController("fixture_partial"),
                      "b": PassiveController(), "c": PassiveController()})
    assert seen == [(17, False, 5)]


def test_ga016_control_priority_one_slot_and_disorientation_not_spent_when_skipped() -> None:
    state = ready()
    state.players["a"].inventory["cloak"] = 1

    def r2(s: GameState) -> None:
        apply_status(s, "a", "SHOCKED", source_id="shock")
        apply_status(s, "a", "PETRIFIED", source_id="stone")
        assert not apply_status(s, "a", "SHOCKED", source_id="weak")
        apply_status(s, "a", "ACTION_SUPPRESSED", source_id="strong")
        apply_status(s, "a", "DISORIENTED", source_id="confused")

    state.ga_hooks["R2"] = [r2]
    controls = {p: PassiveController() for p in state.players}
    run_round(state, controls)
    assert "control" not in state.players["a"].statuses
    assert "DISORIENTED" in state.players["a"].statuses
    state.ga_hooks.clear()
    run_round(state, controls)
    assert state.ga_standard_grants["a"].t0_disabled
    assert "DISORIENTED" not in state.players["a"].statuses
    assert not any(a.operation == "hide" for stage, actions in controls["a"].seen
                   if stage is TurnStage.T1 for a in actions)


def test_petrify_escape_costs_sp_without_t0_and_at_most_two_draws() -> None:
    state = ready(seed=1)

    def stone(s: GameState) -> None:
        apply_status(s, "a", "PETRIFIED", source_id="stone")
        s.players["a"].sp = 2

    state.ga_hooks["R2"] = [stone]
    controls = {p: MinimalController() if p == "a" else PassiveController() for p in state.players}
    run_round(state, controls)
    escapes = [d for d in state.ga_rng.draws if "/escape/" in d[0]]
    assert 1 <= len(escapes) <= 2
    if "control" not in state.players["a"].statuses:
        assert TurnStage.T0 in state.ga_standard_grants["a"].occupied


def test_burn_creation_round_then_two_actual_r4s_even_when_clock_paused() -> None:
    state = ready()
    state.ga_clock_paused = True

    def burn(s: GameState) -> None:
        add_burns(s.players["b"], s.ga_config, s.current_round, 1,
                  source_id="fixture", actor_id="a", score_owner_id="a", crime_owner_id="a")

    state.ga_hooks["R2"] = [burn]
    controls = {p: PassiveController() for p in state.players}
    run_round(state, controls)
    assert state.players["b"].hp == 20
    state.ga_hooks.clear()
    run_round(state, controls)
    assert state.players["b"].hp == 19 and len(state.players["b"].burns) == 1
    run_round(state, controls)
    assert state.players["b"].hp == 18 and not state.players["b"].burns
    assert state.world_tick == 1 and state.current_round == 3
    assert state.players["a"].hp_damage == 2


def test_r4_order_and_idle_heal_respects_blocked_hostile_engagement() -> None:
    state = ready()
    state.players["b"].surgery = "life"
    state.players["b"].hp = 10
    controls = {p: PassiveController() for p in state.players}
    run_round(state, controls)
    assert state.players["b"].hp == 12
    assert [e["label"] for e in state.ga_trace if e["label"].startswith("R4.")] == [
        "R4.police", "R4.environment", "R4.dot_maintenance", "R4.recovery", "R4.expiry"
    ]
    controls["a"] = PassiveController("attack")
    state.players["b"].defenses["fixture-wall"] = DefenseSpec(
        "fixture-wall", DefenseKind.FINITE_BARRIER, 100, 100,
        frozenset({DamageKind.NORMAL}), frozenset(TargetScope),
    )
    run_round(state, controls)
    assert state.players["b"].engaged and state.players["b"].hp == 12


def test_ga019_acceleration_changes_tick_not_slots_or_state_age() -> None:
    state = ready()
    state.current_round = state.world_tick = 10
    state.ga_clock_accelerated = True
    run_round(state, {p: PassiveController() for p in state.players})
    assert (state.current_round, state.world_tick) == (11, 12)
    assert sum(e["label"] == "slot_start" for e in state.ga_trace) == 3


def test_environment_batch_terminal_stops_later_recovery_and_dot() -> None:
    state = ready(2)
    state.current_round = state.world_tick = 15
    for player in state.players.values():
        player.hp = 1
        player.surgery = "life"
    terminal = run_game(state, {p: PassiveController() for p in state.players})
    assert terminal.reason is TerminalReason.ALL_DEAD
    assert not any(e["label"] == "R4.recovery" for e in state.ga_trace)
    assert all(p.hp == 0 for p in state.players.values())


def test_ga037_actual_pp_after_spending_and_two_independent_winners() -> None:
    state = ready(2)
    state.current_round = state.world_tick = 1
    state.players["a"].pp = 10
    state.players["a"].chapters.update({"first", "second", "third"})
    state.players["a"].pp -= 3
    state.players["a"].hp = 0
    state.players["b"].kills = 1
    finish_if_needed(state)
    assert state.winner == "b" and state.score_winners == ("a",)
    assert state.terminal_snapshot.results["a"].total_score == 19
    assert state.terminal_snapshot.results["a"].pp_balance == 7


def test_40_round_fallback_is_flagged_pace_anomaly_no_crown() -> None:
    state = ready(2)
    state.ga_clock_paused = True
    result = run_game(state, {p: PassiveController() for p in state.players})
    assert result.reason is TerminalReason.ROUND_LIMIT and state.current_round == 40
    assert state.ga_trace[-1]["pace_anomaly"] is True
    assert all(r.total_score == 0 for r in result.results.values())


def test_ga040_same_seed_and_inputs_reproduce_full_facts() -> None:
    games = []
    for _ in range(2):
        state = GameState.new_ga(["a", "b", "c", "d"], seed=17)
        result = run_game(state, {p: MinimalController() for p in state.players})
        games.append((result.to_dict(), state.ga_rng.draws,
                      [e.to_dict() for e in state.ga_ledger.events], state.ga_trace))
    assert games[0] == games[1]


def test_declared_timeout_is_failure_not_round_limit_success() -> None:
    state = ready()
    with pytest.raises(TimeoutError):
        run_game(state, {p: PassiveController() for p in state.players}, timeout_seconds=0)
    assert not state.game_over and not state.ga_running


def test_missing_registered_hook_fails_instead_of_old_talent_fallback() -> None:
    state = ready()

    def bad(s: GameState, grant: ActionGrant, stage: TurnStage) -> tuple[ActionOption, ...]:
        return (option(grant, stage, "missing_handler"),)

    state.ga_action_providers.append(bad)
    with pytest.raises(ContractError, match="no executable hook"):
        run_round(state, {p: PassiveController() for p in state.players})


def test_terminal_segment_stops_remaining_segments_and_t2() -> None:
    state = ready(2)
    state.players["b"].hp = 1
    after_called = []

    def after(s: GameState, plan: ActionPlan) -> None:
        after_called.append(True)

    def provider(s: GameState, grant: ActionGrant, stage: TurnStage) -> tuple[ActionOption, ...]:
        if grant.actor_id != "a" or stage is not TurnStage.T1:
            return ()
        candidate = option(grant, stage, "fixture_finisher", after=after, targets=("b",))
        return (replace(candidate, effects=(
            effect(candidate.request, ("b",), 1, suffix="kill"),
            effect(candidate.request, ("a",), 99, suffix="must-not-run"),
        )),)

    state.ga_action_providers.append(provider)
    run_round(state, {"a": PassiveController("fixture_finisher"), "b": PassiveController()})
    assert state.winner == "a" and state.players["a"].hp == 20
    assert not after_called and not any(e["label"] == "T2" for e in state.ga_trace)
    assert len(state.ga_resolution_batches) == 1


def test_overclock_offers_unused_t0_only_after_this_slots_move() -> None:
    state = ready()
    state.players["a"].surgery = "overclock"
    controller = PassiveController("move")

    def choose(
        view: Mapping[str, Any], actions: tuple[ActionRequest, ...], stage: TurnStage,
    ) -> ActionRequest | None:
        if stage is TurnStage.T0:
            if view["players"]["a"]["location"] == "shop":
                return None
            return next(a for a in actions if a.operation == "lock")
        return next(a for a in actions if a.operation == "move")

    controller.choose = choose
    run_round(state, {"a": controller, "b": PassiveController(), "c": PassiveController()})
    actions = [e for e in state.ga_trace if e["label"] == "action" and e["actor"] == "a"]
    assert [e["operation"] for e in actions] == ["move", "lock"]


def test_hook_waits_two_subsequent_standard_slots() -> None:
    state = ready()
    state.players["a"].weapons = {"fist", "hook"}
    controller = PassiveController()

    def choose(
        view: Mapping[str, Any], actions: tuple[ActionRequest, ...], stage: TurnStage,
    ) -> ActionRequest | None:
        if stage is TurnStage.T0:
            return None
        return next((a for a in actions if a.operation == "attack" and
                     a.parameters["weapon"] == "hook"), None) or next(
            a for a in actions if a.operation == "forfeit"
        )

    controller.choose = choose
    controls = {"a": controller, "b": PassiveController(), "c": PassiveController()}
    hp = []
    for _ in range(4):
        run_round(state, controls)
        hp.append(state.players["b"].hp)
    assert hp == [18, 18, 18, 16]


def test_shock_applied_during_t2_is_reserved_for_next_slot() -> None:
    state = ready()

    def shock(s: GameState) -> None:
        if s.current_grant.actor_id == "a":
            apply_status(s, "a", "SHOCKED", source_id="new-shock")

    state.ga_hooks["T2"] = [shock]
    controls = {p: PassiveController() for p in state.players}
    run_round(state, controls)
    assert state.players["a"].statuses["control"].name == "SHOCKED"
    state.ga_hooks.clear()
    run_round(state, controls)
    assert state.ga_standard_grants["a"].shocked
    assert "control" not in state.players["a"].statuses


def test_furnace_cleanse_shock_allows_normal_t1() -> None:
    state = ready()
    state.players["a"].surgery = "furnace"

    def shock(s: GameState) -> None:
        apply_status(s, "a", "SHOCKED", source_id="fixture")

    state.ga_hooks["R2"] = [shock]
    controller = PassiveController("attack")
    original = controller.choose

    def choose(
        view: Mapping[str, Any], actions: tuple[ActionRequest, ...], stage: TurnStage,
    ) -> ActionRequest | None:
        if stage is TurnStage.T0:
            return next(a for a in actions if a.operation == "furnace" and
                        a.parameters["mode"] == "cleanse")
        return original(view, actions, stage)

    controller.choose = choose
    run_round(state, {"a": controller, "b": PassiveController(), "c": PassiveController()})
    assert state.players["a"].hp == 17 and not state.ga_standard_grants["a"].shocked
    assert state.players["b"].hp < 20


def test_ordinary_weapon_progress_against_repair_work_and_finite_healing() -> None:
    state = ready(2)
    state.players["a"].weapons = {"fist", "knife"}
    defender = state.players["b"]
    defender.surgery = "life"
    defender.hp = defender.max_hp = 28
    defender.inventory["syringe"] = 1
    defender.syringe_purchases = 1
    add_armor(state, "b", "ceramic")
    permanent_states = []
    attacker = MinimalController()
    defend = PassiveController()

    def defense_choice(
        view: Mapping[str, Any], actions: tuple[ActionRequest, ...], stage: TurnStage,
    ) -> ActionRequest | None:
        if stage is TurnStage.T0:
            return next((a for a in actions if a.operation == "syringe"), None)
        return next((a for a in actions if a.operation == "repair"), None) or next(
            (a for a in actions if a.operation == "buy_armor" and
             a.parameters["kind"] == "ceramic" and
             not any(d.durability for d in defender.defenses.values())), None
        ) or next(a for a in actions if a.operation == "work")

    def record(s: GameState) -> None:
        permanent_states.append((
            defender.hp, tuple(d.durability for d in defender.defenses.values()),
            defender.credits, tuple(sorted(defender.inventory.items())),
            defender.hospital_heals, defender.syringe_purchases,
        ))

    defend.choose = defense_choice
    state.ga_hooks["R4.expiry"] = [record]
    result = run_game(state, {"a": attacker, "b": defend})
    assert result.reason is TerminalReason.LAST_SURVIVOR and state.winner == "a"
    assert state.players["a"].kills == 1 and state.players["a"].hp_damage >= 28
    assert len(permanent_states) == len(set(permanent_states))
    assert state.current_round < 16


def test_pp_reroll_happens_once_before_ordering_without_natural_pp_growth() -> None:
    state = ready()
    state.players["a"].pp = 3
    controller = PassiveController()
    controller.reroll_initiative = lambda view, value: True
    run_round(state, {"a": controller, "b": PassiveController(), "c": PassiveController()})
    assert state.players["a"].pp == 2
    assert sum(key.endswith("/reroll") for key, _ in state.ga_rng.draws) == 1
    assert all(state.ga_initiative[state.ga_round_order[i]] >=
               state.ga_initiative[state.ga_round_order[i + 1]] for i in range(2))
