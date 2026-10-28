"""I4.0公开观察、正式预检说明与同源计分验收。"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from engine.ga.contracts import TurnStage, thaw_json
from engine.ga.gameplay import build_options, validate_request
from engine.ga.observation import public_value
from engine.ga.scoring import score_components
from engine.ga.setup import TALENTS
from engine.ga.state import add_armor, finish_if_needed, observe
from engine.ga.status import put_status
from tests.ga_i2_helpers import ready, turn


@pytest.mark.parametrize("slot", tuple(TALENTS))
@pytest.mark.parametrize("stage", (TurnStage.T0, TurnStage.T1))
def test_all_talents_use_same_pure_candidate_quotes(slot: str, stage: TurnStage) -> None:
    state = ready({"a": slot})
    state.players["a"].sp = 2
    turn(state, "a", stage)
    grant = state.current_grant
    options = build_options(state, grant, stage)
    before = (state.ga_revision, state.ga_id_counter, len(state.ga_rng.draws),
              len(state.ga_ledger.events), public_value(state.players["a"]))
    view = observe(state, "a", options=options)
    for option, description in zip(options, view["candidates"], strict=True):
        plan = validate_request(state, option.request)
        assert plan.accepted
        assert thaw_json(description["request"]) == option.request.to_dict()
        assert thaw_json(description["costs"]) == public_value(plan.costs)
        assert description["phase_claim"] == public_value(plan.phase_claim)
        assert description["effect_scope"] == "registered_before_runtime_hooks"
    assert before == (state.ga_revision, state.ga_id_counter, len(state.ga_rng.draws),
                      len(state.ga_ledger.events), public_value(state.players["a"]))
    json.dumps(thaw_json(view))


def test_snapshot_keeps_real_timers_defense_and_finite_service_counters() -> None:
    state = ready()
    armor = add_armor(state, "a", "shield")
    player = state.players["a"]
    put_status(player, state.ga_config, state.current_round, "PETRIFIED", source_id="test")
    player.hospital_heals = 2
    view = observe(state, "b")
    body = view["players"]["a"]
    assert body["statuses"]["control"]["timer"]["created_round"] == state.current_round
    assert body["defense_details"][armor]["durability"] == player.defenses[armor].durability
    assert body["hospital_heals"] == 2
    with pytest.raises(TypeError):
        body["statuses"]["control"]["timer"]["remaining"] = 100
    player.defenses[armor] = replace(player.defenses[armor], durability=1)
    player.hospital_heals = 3
    assert body["defense_details"][armor]["durability"] != 1
    assert body["hospital_heals"] == 2


def test_score_progress_does_not_award_crown_and_tracks_live_pp() -> None:
    state = ready()
    player = state.players["a"]
    player.kills, player.hp_damage, player.armor_breaks, player.pp = 1, 7, 2, 9
    player.chapters.add("chapter/1")
    snapshot = observe(state, "a")["players"]["a"]["score"]
    assert snapshot["components"]["total"] == 22
    assert snapshot["components"]["crown"] == 0 and snapshot["crown_eligible"]
    player.pp -= 3
    assert score_components(player, state.ga_config)["total"] == 19
    for key in ("b", "c"):
        state.players[key].hp = 0
    assert finish_if_needed(state)
    assert state.terminal_snapshot.results["a"].total_score == 25


def test_stale_round_order_is_not_presented_as_current_initiative() -> None:
    state = ready()
    state.ga_round_order = ("c", "b", "a")
    state.ga_initiative = {"a": 1, "b": 2, "c": 3}
    state.ga_order_round = state.current_round - 1
    assert not observe(state, "a")["schedule"]["order_ready"]
    assert not observe(state, "a")["schedule"]["initiative"]
    state.ga_order_round = state.current_round
    assert observe(state, "a")["schedule"]["round_order"] == ("c", "b", "a")


def test_poem_quote_uses_actual_sp_override_and_memory_cost() -> None:
    state = ready({"a": "G5", "b": "T1"})
    state.ga_roles["a"].stage, state.ga_roles["a"].memory = 2, 6
    state.players["a"].sp = 1
    turn(state, "a")
    options = build_options(state, state.current_grant, TurnStage.T1)
    view = observe(state, "a", options=options)
    poem = next(c for c in view["candidates"] if c["request"]["operation"] == "g5_poem" and
                c["request"]["target_ids"] == ("b",) and
                c["request"]["parameters"]["paid_sp"] == 1)
    costs = {c["account"]: c["amount"] for c in poem["costs"]}
    assert costs == {"sp": 1, "resource:memory": 2}
    assert poem["has_continuation"]
