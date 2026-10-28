"""惧怖的完整行动回合计时、状态免疫与独立伤害结算。"""

from __future__ import annotations

from dataclasses import replace
from collections.abc import Mapping
from typing import Any

import pytest

from engine.ga.actions import GrantKind
from engine.game_state import GameState
from engine.ga.combat import resolve_batch
from engine.ga.contracts import (
    ActionRequest, ContractError, DamageKind, EffectKind, ResolutionRecord, TargetScope, TurnStage,
)
from engine.ga.gameplay import build_options, execute_registered_step, validate_request
from engine.ga.runtime import _play_grant
from engine.ga.state import ActionGrant, next_id
from engine.ga.status import expire_r4, put_status
from tests.ga_i2_helpers import Decisions, ready
from tests.test_ga_combat import packet


def fear(state: GameState) -> tuple[ResolutionRecord, ...]:
    """纯状态包不制造伤害，不绕过正式免疫与事件链。"""
    effect = replace(packet(targets=("b",), actor="a", amount=0),
                     kind=EffectKind.STATUS, damage_kind=DamageKind.NONE,
                     parameters={"statuses": ("FEAR",)})
    return resolve_batch(state, (effect,))


def test_fear_applies_without_damage_and_replay_is_idempotent() -> None:
    state = ready()
    before = len(state.ga_rng.draws)
    records = fear(state)
    assert records[0].applied_statuses == ("FEAR",)
    assert records[0].hp_loss == 0 and not records[0].armor_changes
    assert fear(state) == records
    assert len(state.ga_rng.draws) == before
    assert not state.ga_crimes and state.players["b"].hp == 20


@pytest.mark.parametrize("immune", ["status", "absolute"])
def test_fear_immunity_prevents_both_components(immune: str) -> None:
    state = ready()
    if immune == "status":
        state.players["b"].status_immunities = frozenset({"FEAR"})
    else:
        state.ga_afterlife.temporary_immunity["b"] = state.current_round
    assert not fear(state)[0].applied_statuses
    grant = ActionGrant(next_id(state, "b"), "b", GrantKind.STANDARD)
    _play_grant(state, state.ga_controllers, grant)
    assert not grant.movement_blocked


def test_fear_vulnerability_ends_at_next_full_slot_not_r4_or_reaction() -> None:
    state = ready()
    fear(state)
    state.current_round += 2
    expire_r4(state)
    first = resolve_batch(state, (packet("hit1", targets=("b",), actor="a"),))[0]
    assert first.hp_loss == 5
    reaction = ActionGrant(next_id(state, "b-reaction"), "b", GrantKind.REACTION)
    _play_grant(state, state.ga_controllers, reaction)
    assert "FEAR" in state.players["b"].statuses
    full = ActionGrant(next_id(state, "b-extra"), "b", GrantKind.EXTRA_TURN)
    _play_grant(state, state.ga_controllers, full)
    assert "FEAR" not in state.players["b"].statuses and full.movement_blocked
    second = resolve_batch(state, (replace(packet("hit2", targets=("b",), actor="a"),
                                          scope=TargetScope.RANGED_SINGLE),))[0]
    assert second.hp_loss == 4


def test_fear_blocks_move_in_current_slot_only_and_keeps_other_actions() -> None:
    class Inspect(Decisions):
        """控制器在正式调度给出的候选上检查禁令。"""

        def choose(
            self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...], stage: TurnStage,
        ) -> ActionRequest | None:
            """检查调度器已经消费惧怖，再返回合法选择。"""
            if stage is TurnStage.T1:
                assert not any(a.operation == "move" for a in actions)
                assert any(a.operation == "attack" for a in actions)
            return super().choose(view, actions, stage)

    state = ready()
    fear(state)
    state.ga_controllers["b"] = Inspect()
    current = ActionGrant(next_id(state, "b-full"), "b", GrantKind.STANDARD)
    stale = next(o.request for o in build_options(state, current, TurnStage.T1)
                 if o.request.operation == "move")
    _play_grant(state, state.ga_controllers, current)
    assert not validate_request(state, stale).accepted
    next_turn = ActionGrant(next_id(state, "b-next"), "b", GrantKind.EXTRA_TURN)
    assert any(o.request.operation == "move" for o in build_options(
        state, next_turn, TurnStage.T1))


def test_fear_and_vulnerability_share_one_normal_damage_modifier() -> None:
    state = ready()
    fear(state)
    put_status(state.players["b"], state.ga_config, state.current_round,
               "VULNERABLE", source_id="other")
    hit = resolve_batch(state, (packet("stack", targets=("b",), actor="a"),))[0]
    assert hit.hp_loss == 5
    direct = packet("direct", targets=("b",), actor="a", kind=DamageKind.DIRECT)
    assert resolve_batch(state, (direct,))[0].hp_loss == 4


def test_fear_consumes_on_skipped_slot_without_spilling_into_next_turn() -> None:
    state = ready()
    fear(state)
    put_status(state.players["b"], state.ga_config, state.current_round,
               "ACTION_SUPPRESSED", source_id="suppressed")
    skipped = ActionGrant(next_id(state, "b-skip"), "b", GrantKind.STANDARD)
    _play_grant(state, state.ga_controllers, skipped)
    assert skipped.finished and skipped.movement_blocked
    assert "FEAR" not in state.players["b"].statuses
    next_turn = ActionGrant(next_id(state, "b-next"), "b", GrantKind.EXTRA_TURN)
    _play_grant(state, state.ga_controllers, next_turn)
    assert not next_turn.movement_blocked


def test_composite_move_cannot_bypass_parent_slot_prohibition() -> None:
    state = ready()
    grant = ActionGrant(next_id(state, "root"), "b", GrantKind.STANDARD)
    candidate = next(o for o in build_options(state, grant, TurnStage.T1)
                     if o.request.operation == "move")
    state.current_grant, state.current_stage = grant, TurnStage.T1
    grant.movement_blocked = True
    before = state.players["b"].location
    with pytest.raises(ContractError):
        execute_registered_step(state, candidate)
    assert state.players["b"].location == before


def test_followup_inherits_move_prohibition_but_does_not_consume_new_fear() -> None:
    from engine.ga.runtime import queue_followup

    state = ready()
    fear(state)
    seen = []

    def follow(state: GameState) -> None:
        """完整回合收尾中新施加的惧怖留给下一完整回合。"""
        if state.current_grant.actor_id == "b":
            queue_followup(state, "b", "g1_full_burn")
            put_status(state.players["b"], state.ga_config, state.current_round,
                       "FEAR", source_id="renewed")

    class Inspect(Decisions):
        """追击只有攻击仍可执行，不能靠重新授予恢复移动。"""

        def choose(
            self, view: Mapping[str, Any], actions: tuple[ActionRequest, ...], stage: TurnStage,
        ) -> ActionRequest | None:
            """检查实际给控制器的追击候选。"""
            if stage is TurnStage.T1:
                assert not any(a.operation == "move" for a in actions)
                seen.append(state.current_grant.kind)
            return super().choose(view, actions, stage)

    state.ga_controllers["b"] = Inspect()
    state.ga_hooks["T2"] = [follow]
    _play_grant(state, state.ga_controllers,
                ActionGrant(next_id(state, "b-full"), "b", GrantKind.STANDARD))
    assert GrantKind.STEP_FOLLOWUP in seen
    assert "FEAR" in state.players["b"].statuses


def test_g7_macro_does_not_offer_a_forbidden_dash_before_payment() -> None:
    from tests.test_ga_g7 import fused

    state = fused()
    role = state.ga_roles["a"]
    state.current_grant.movement_blocked = True
    sub = ActionGrant(next_id(state, "macro-step"), "a", GrantKind.ROOT_STEP)
    options = role.macro_options(state, sub, epo_used=False, settings=set())
    assert options and not any(o.request.operation == "g7_macro_dash" for o in options)


def test_g2_forced_move_has_explicit_forfeit_when_fear_blocks_movement() -> None:
    state = ready({"b": "G2"})
    grant = ActionGrant(next_id(state, "b-fear"), "b", GrantKind.STANDARD)
    grant.movement_blocked = True
    state.ga_roles["b"].forced_move = grant.grant_id
    options = build_options(state, grant, TurnStage.T1)
    assert [o.request.operation for o in options] == ["forfeit"]
