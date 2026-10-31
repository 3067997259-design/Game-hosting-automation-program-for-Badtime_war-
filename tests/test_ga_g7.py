"""I3 G7真实装备、宏逐步支付、残盾与Terror结构预警。"""

from collections import deque
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import pytest

from engine.game_state import GameState
from engine.ga.contracts import ChoiceWindow, Performance, RoundPhase, TurnStage
from engine.ga.gameplay import legal_actions
from engine.ga.runtime import run_round
from engine.ga.state import run_hooks
from engine.ga.talents.g7 import FORMS
from tests.ga_i2_helpers import Decisions, execute, ready, turn


def woken(form: str) -> GameState:
    """与 ready 相同的真实起床出门流程，只是 G7 起床时自选初始形态。"""
    ids = ["a", "b", "c"]
    state = GameState.new_ga(ids, seed=0, talents={"a": "G7"})
    state.current_round = state.world_tick = 1
    state.current_phase = RoundPhase.R3
    state.ga_controllers = {actor: Decisions() for actor in ids}
    state.ga_initiative = {actor: len(ids) - index for index, actor in enumerate(ids)}
    state.ga_round_order = tuple(ids)
    for actor in ids:
        turn(state, actor, TurnStage.T0)
        if actor == "a":
            assert {a.parameters["form"] for a in legal_actions(state)} == set(FORMS)
        execute(state, "wake", choice="knife", **({"form": form} if actor == "a" else {}))
        state.current_stage = TurnStage.T1
        execute(state, "move", location="shop")
    return state


def fused() -> GameState:
    """经真实地点菜单购买组件，两次融合自动触发，不直接塞融合装备。"""
    state = ready({"a": "G7"})
    state.players["a"].credits = 30
    state.players["a"].location = "military"
    turn(state, "a")
    execute(state, "buy_armor", kind="at_field")
    turn(state, "a")
    execute(state, "buy_weapon", weapon="electromagnetic_rifle")
    turn(state, "a")
    execute(state, "buy_weapon", weapon="gauss_rifle")
    state.players["a"].location = "shop"
    return state


class Steps(Decisions):
    """选择引擎传入的实际子步骤，绝不自行拼造宏动作。"""

    def __init__(self, steps: list[str]) -> None:
        super().__init__()
        self.steps = deque(steps)

    def respond(
        self, view: Mapping[str, Any], window: ChoiceWindow, context: Mapping[str, Any],
    ) -> str | None:
        if context["kind"] == "hoshino_step":
            if not self.steps:
                return "cancel"
            operation = self.steps.popleft()
            return next(str(i) for i, step in enumerate(context["steps"]) if
                        step["operation"] == operation)
        return super().respond(view, window, context)


@pytest.mark.parametrize(("form", "shields", "halo"), [
    ("swimsuit", 1, 2), ("archer", 0, 2), ("shielder", 0, 4),
])
def test_wake_picks_starting_form_and_grants_only_that_forms_bonus(
    form: str, shields: int, halo: int,
) -> None:
    state = woken(form)
    role = state.ga_roles["a"]
    assert role.form == role.old_form == form and role.changes == 0
    assert list(state.players["a"].armor_kinds.values()).count("shield") == shields
    assert role.halo_value() == halo


def test_ex_without_intact_iron_horus_keeps_shield_down_and_movement_open() -> None:
    state = ready({"a": "G7"})
    role = state.ga_roles["a"]
    state.players["a"].credits = 30
    state.players["a"].location = "military"
    turn(state, "a")
    execute(state, "buy_weapon", weapon="electromagnetic_rifle")
    turn(state, "a")
    execute(state, "buy_weapon", weapon="gauss_rifle")
    state.players["a"].location = "shop"
    assert role.gun_fused and not role.shield_fused and role.magazine
    state.players["a"].sp = 2
    turn(state, "a")
    execute(state, "g7_ex", targets=("b",), mode=Performance.PUBLIC, location="shop")
    assert role.pose == "passive"
    turn(state, "a")
    assert any(a.verb.value == "move" for a in legal_actions(state))


def test_fusion_consumes_components_once_and_grants_two_supply_uses_only() -> None:
    state = fused()
    role = state.ga_roles["a"]
    assert role.shield_fused and role.gun_fused and role.supply == 2
    assert state.players["a"].defenses[role.shield_id].durability == 12
    assert not {"electromagnetic_rifle", "gauss_rifle"} & state.players["a"].weapons
    assert len(role.magazine) == state.players["a"].ammo["horus_loaded"] == 4
    turn(state, "a", TurnStage.T0)
    execute(state, "g7_supply", kind="ammo")
    execute(state, "g7_supply", kind="ammo")
    assert role.supply == 0 and len(role.reserve) == 4
    assert state.current_grant.occupied == set()
    assert not any(a.operation == "g7_supply" for a in legal_actions(state))


def test_macro_two_paid_shots_one_free_share_one_root_and_real_two_ammo_cost_four() -> None:
    state = fused()
    role = state.ga_roles["a"]
    turn(state, "a", TurnStage.T0)
    execute(state, "g7_change", form="archer")
    state.players["c"].location = "magic"
    state.ga_controllers["a"] = Steps(["g7_macro_shoot", "g7_macro_shoot"])
    state.current_stage = TurnStage.T1
    receipt = execute(state, "g7_macro")
    assert state.players["a"].resources["cost"] == 2 and len(role.magazine) == 2
    assert state.players["a"].ammo["horus_loaded"] == 2
    records = [r for _, rs in state.ga_resolution_batches.values() for r in rs if
               r.effect.source_id in (
        "g7_shot", "g7_free_shot")]
    assert len(records) == 9 and sum(r.effect.amount for r in records) == 11
    assert {r.effect.root_id for r in records} == {receipt.request.root_id}
    assert [x for x in state.ga_history if x[1] == "a"][-1][2] == "special"
    endings = [e for e in state.ga_ledger.events if e.root_id == receipt.request.root_id and
               e.kind.value == "action_resolved"]
    assert len(endings) == 1 and not state.ga_extra_used


def test_reload_moves_only_real_reserve_and_zero_cost_steps_cannot_mint_ammo() -> None:
    state = fused()
    role = state.ga_roles["a"]
    turn(state, "a", TurnStage.T0)
    execute(state, "g7_supply", kind="ammo")
    before = len(role.magazine) + len(role.reserve)
    state.ga_controllers["a"] = Steps(["g7_macro_reload", "g7_macro_eject"])
    state.current_stage = TurnStage.T1
    execute(state, "g7_macro")
    assert not role.magazine and not role.reserve
    assert state.players["a"].ammo["horus_reserve"] == state.players["a"].ammo["horus_loaded"] == 0
    assert before == 6


def test_active_breaking_shield_protects_full_hit_and_nap_cannot_restore_zero() -> None:
    state = fused()
    role = state.ga_roles["a"]
    shield = state.players["a"].defenses[role.shield_id]
    state.players["a"].defenses[role.shield_id] = replace(shield, durability=1)
    state.ga_controllers["a"] = Steps(["g7_macro_shield"])
    turn(state, "a")
    execute(state, "g7_macro")
    turn(state, "b")
    execute(state, "attack", targets=("a",), weapon="knife")
    assert state.players["a"].hp == 20
    assert state.players["a"].defenses[role.shield_id].durability == 0
    turn(state, "a", TurnStage.T0)
    assert not any(a.operation == "g7_nap" and a.parameters["kind"] == "shield"
                   for a in legal_actions(state))
    assert not any(a.verb.value == "move" for a in legal_actions(state))


def test_component_t1_repairs_zero_shield_but_does_not_redeploy_or_renew_supplies() -> None:
    state = fused()
    role = state.ga_roles["a"]
    shield = state.players["a"].defenses[role.shield_id]
    state.players["a"].defenses[role.shield_id] = replace(shield, durability=0)
    turn(state, "a")
    execute(state, "buy_armor", kind="shield")
    component = next(k for k, v in state.players["a"].armor_kinds.items() if v == "shield")
    turn(state, "a")
    execute(state, "g7_repair", component=component)
    assert state.players["a"].defenses[role.shield_id].durability == 4 and role.pose == "passive"
    assert component not in state.players["a"].defenses and role.supply == 2


def test_shop_repair_skips_horus_but_broken_component_still_repairs_it() -> None:
    """DEC-062：商店普通修甲修不了铁之荷鲁斯；已破碎的盾牌照样能拆来修。"""
    state = fused()
    role = state.ga_roles["a"]
    player = state.players["a"]
    player.defenses[role.shield_id] = replace(player.defenses[role.shield_id], durability=5)
    turn(state, "a")
    execute(state, "buy_armor", kind="shield")
    component = next(k for k, v in player.armor_kinds.items() if v == "shield")
    player.defenses[component] = replace(player.defenses[component], durability=2)
    turn(state, "a")
    repairs = {a.parameters["defense_id"] for a in legal_actions(state) if a.operation == "repair"}
    assert repairs == {component}
    player.defenses[component] = replace(player.defenses[component], durability=0)
    turn(state, "a")
    assert not any(a.operation == "repair" for a in legal_actions(state))
    execute(state, "g7_repair", component=component)
    assert player.defenses[role.shield_id].durability == 9
    assert component not in player.defenses


def test_fatigue_once_nap_cancels_and_adrenaline_next_r0_overrides_once() -> None:
    state = fused()
    role = state.ga_roles["a"]
    state.ga_controllers["a"] = Steps([])
    turn(state, "a")
    execute(state, "g7_macro")
    run_round(state, state.ga_controllers)
    assert state.players["a"].resources["cost"] == 5
    state.players["a"].inventory["adrenaline"] = 1
    turn(state, "a", TurnStage.T0)
    execute(state, "g7_medicine", kind="adrenaline")
    run_round(state, state.ga_controllers)
    assert state.players["a"].resources["cost"] == 8 and state.players["a"].initiative_bonus == 3
    run_round(state, state.ga_controllers)
    assert state.players["a"].resources["cost"] == 6 and state.players["a"].initiative_bonus == 0
    assert role.adrenaline_used == 1


def test_halo_idle_is_one_total_point_no_combat_recovery_and_continuation_is_once() -> None:
    state = ready({"a": "G7"})
    role = state.ga_roles["a"]
    assert role.halo_value() == 2
    run_hooks(state, "R4.recovery")
    assert role.halo_value() == 3
    state.players["a"].engaged = True
    state.current_round += 1
    run_hooks(state, "R4.recovery")
    assert role.halo_value() == 3
    role.halo(state, 6)
    state.players["a"].hp = 1
    state.players["a"].defenses.clear()
    turn(state, "b")
    execute(state, "attack", targets=("a",), weapon="knife")
    assert state.players["a"].hp == 6 and role.revive_used == 1 and role.halo_value() == 0


def test_terror_doubt_skips_next_standard_extra_cannot_pay_or_attack_early() -> None:
    state = ready({"a": "G7"})
    role = state.ga_roles["a"]
    role.color = 6
    turn(state, "a", TurnStage.T0)
    execute(state, "g7_doubt")
    state.current_stage = TurnStage.T1
    assert [a.operation for a in legal_actions(state)] == ["forfeit"]
    run_round(state, state.ga_controllers)
    assert role.form == "terror" and not role.terror_ready
    before = state.players["b"].hp
    run_round(state, state.ga_controllers)
    assert role.terror_ready and state.players["b"].hp == before
    extra_before = role.extra_hp
    turn(state, "a")
    execute(state, "g7_terror")
    assert state.players["b"].hp == before - 4 and not state.players["b"].absolute_dead
    assert role.extra_hp == extra_before - 6


def test_terror_lethal_is_absolute_respects_isolation_and_win_skips_self_cost() -> None:
    state = ready({"a": "G7"})
    role = state.ga_roles["a"]
    role.enter_terror(state)
    role.terror_ready = True
    state.players["b"].hp = 3
    state.players["c"].isolation_id = "realm"
    turn(state, "a")
    execute(state, "g7_terror")
    assert state.players["b"].absolute_dead and state.players["c"].hp == 20
    assert role.extra_hp == 6
    state.players["c"].isolation_id = None
    state.players["c"].hp = 3
    turn(state, "a")
    execute(state, "g7_terror")
    assert state.winner == "a" and role.extra_hp == 6


def test_swimsuit_ex_uses_actual_magazine_and_unlocks_final_after_real_active_block() -> None:
    state = fused()
    role = state.ga_roles["a"]
    state.ga_controllers["a"] = Steps(["g7_macro_shield"])
    turn(state, "a")
    execute(state, "g7_macro")
    turn(state, "b")
    execute(state, "attack", targets=("a",), weapon="knife")
    state.players["a"].sp = 2
    turn(state, "a")
    execute(state, "g7_ex", targets=("b",), mode=Performance.PUBLIC, location="shop")
    assert not role.magazine and state.players["a"].ammo["horus_loaded"] == 0
    assert state.players["a"].chapters == {"chapter/1", "chapter/2", "chapter/3"}
