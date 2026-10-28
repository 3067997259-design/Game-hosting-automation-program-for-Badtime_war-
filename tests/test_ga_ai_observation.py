"""I4.1公开掉落与精确奖励余额；观察不可写且不消费实际奖励额度。"""

from __future__ import annotations

from dataclasses import replace
import json

import pytest

from engine.ga.combat import resolve_batch
from engine.ga.contracts import DamageKind, thaw_json
from engine.ga.state import add_armor, observe, record_death
from tests.test_ga_combat import arena, packet


def test_reward_allowance_tracks_actual_payments_per_actor_and_resets_per_round() -> None:
    state = arena(3)
    config = state.ga_config
    initial = observe(state, "p0")
    assert initial["schema"] == "ga.observation.v2"
    pp_cap = config.get("pp", "armor_break_rewards_per_round")
    sp_cap = config.get("action", "attention_per_round")
    assert all(allowance == {"armor_break_pp": pp_cap, "attention": sp_cap}
               for allowance in initial["reward_allowance"].values())
    for actor in ("p1", "p2"):
        key = add_armor(state, actor, "shield")
        state.players[actor].defenses[key] = replace(
            state.players[actor].defenses[key], durability=1)
    effect = packet("quota", amount=9, targets=("p1", "p2"), kind=DamageKind.PURE_WEAR)
    resolve_batch(state, (effect,))
    after = observe(state, "p0")
    assert after["reward_allowance"]["p0"]["armor_break_pp"] == 0
    assert after["reward_allowance"]["p1"]["armor_break_pp"] == pp_cap
    assert all(allowance["attention"] == 0 for allowance in after["reward_allowance"].values())
    assert initial["reward_allowance"]["p0"]["armor_break_pp"] == pp_cap
    claims = state.ga_ledger.claims
    revision, random_draws = state.ga_revision, tuple(state.ga_rng.draws)
    for _ in range(3):
        assert observe(state, "p1")["reward_allowance"] == after["reward_allowance"]
    assert state.ga_ledger.claims == claims and state.ga_revision == revision
    assert tuple(state.ga_rng.draws) == random_draws
    with pytest.raises(TypeError):
        after["reward_allowance"]["p0"]["attention"] = 99
    state.current_round += 1
    assert observe(state, "p0")["reward_allowance"] == initial["reward_allowance"]


def test_sp_wallet_full_does_not_consume_attention_allowance() -> None:
    state = arena(3)
    state.players["p0"].sp = state.ga_config.get("action", "sp_cap")
    key = add_armor(state, "p1", "shield")
    state.players["p1"].defenses[key] = replace(state.players["p1"].defenses[key], durability=1)
    resolve_batch(state, (packet("full", amount=9, kind=DamageKind.PURE_WEAR),))
    still_available = observe(state, "p0")["reward_allowance"]["p0"]["attention"]
    assert still_available == state.ga_config.get("action", "attention_per_round")
    state.players["p0"].sp -= 1
    key = add_armor(state, "p2", "shield")
    state.players["p2"].defenses[key] = replace(state.players["p2"].defenses[key], durability=1)
    resolve_batch(state, (packet("room", amount=9, targets=("p2",), kind=DamageKind.PURE_WEAR),))
    assert observe(state, "p0")["reward_allowance"]["p0"]["attention"] == still_available - 1


def test_public_death_loot_is_complete_json_serializable_and_recursively_immutable() -> None:
    state = arena(3)
    state.world_tick = state.ga_config.get("clock", "day_start")
    owner = state.players["p1"]
    owner.credits, owner.modules = 3, ("fire",)
    owner.ammo["bow"] = 4
    owner.weapons.update({"bow", "knife"})
    owner.inventory["syringe"] = 1
    key = add_armor(state, "p1", "shield")
    record_death(state, "p1", absolute=False)
    snapshot = observe(state, "p0")
    loot = snapshot["ground_loot"]["shop"][0]
    assert loot["owner_id"] == "p1" and loot["credits"] == 3
    assert loot["ammo"]["bow"] == 4 and loot["modules"] == ("fire",)
    assert set(loot["weapons"]) >= {"bow", "knife"}
    assert loot["defenses"][key]["durability"] == state.ga_config.get(
        "armor", "shield", "durability")
    assert loot["armor_kinds"][key] == "shield"
    json.dumps(thaw_json(snapshot), allow_nan=False)
    with pytest.raises(TypeError):
        loot["ammo"]["bow"] = 0
    with pytest.raises(TypeError):
        loot["defenses"][key]["durability"] = 0
    state.ground_loot["shop"][0]["credits"] = 0
    state.ground_loot["shop"][0]["inventory"]["syringe"] = 0
    assert loot["credits"] == 3 and loot["inventory"]["syringe"] == 1
    assert observe(state, "p2")["ground_loot"]["shop"][0]["credits"] == 0
