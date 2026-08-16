"""M9 天赋注册边界：完整槽位账本、fail-closed 与双管线隔离。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from controllers.forfeit_controller import ForfeitController
from engine import experiments
from engine.game_setup import (
    TALENT_TABLE,
    assign_talent_entry,
    find_talent_entry,
    talent_table_for_current_profile,
)
from engine.game_state import GameState
from engine.m9.gate import instantiate_talent
from engine.m9.talent_registry import (
    M9_ACTIVE_SLOT_IDS,
    M9_TALENT_REGISTRY,
    M9TalentUnavailableError,
    TalentAvailability,
    active_registrations,
    selectable_legacy_numbers,
)
from engine.m9.talents.g2 import Hologram9
from models.player import Player
from talents.g2_hologram import Hologram
from talents.t5_combo import Combo


@pytest.fixture(autouse=True)
def _reset_profile():
    experiments.reset()
    yield
    experiments.reset()


def _state(profile: str) -> GameState:
    experiments.set_profile(profile)
    return GameState()


def test_registry_accounts_for_every_active_slot_and_retires_t5() -> None:
    assert len(M9_ACTIVE_SLOT_IDS) == 14
    assert {item.slot_id for item in active_registrations()} == set(
        M9_ACTIVE_SLOT_IDS)
    assert set(M9_TALENT_REGISTRY) == set(M9_ACTIVE_SLOT_IDS) | {"T5"}
    assert M9_TALENT_REGISTRY["T5"].availability is TalentAvailability.RETIRED
    assert M9_TALENT_REGISTRY["T5"].replacement_slot_id == "G0"


def test_m9_selection_pool_contains_only_real_adapters() -> None:
    state = _state("m9-rfc")
    table = talent_table_for_current_profile(state)

    assert selectable_legacy_numbers() == frozenset(
        {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14})
    assert {entry[0] for entry in table} == set(selectable_legacy_numbers())
    assert {item.slot_id for item in active_registrations() if item.is_selectable} == {
        "T1", "T2", "T3", "T4", "T6", "T7",
        "G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7",
    }


@pytest.mark.parametrize(
    ("query", "slot"),
    (
        ("T5", "T5"),
    ),
)
def test_unmigrated_or_retired_slots_are_rejected_explicitly(
        query: str | int, slot: str) -> None:
    state = _state("m9-rfc")

    with pytest.raises(M9TalentUnavailableError, match=rf"M9 槽位 {slot}"):
        find_talent_entry(
            query, talent_table_for_current_profile(state), game_state=state,
        )


@pytest.mark.parametrize("profile", ("legacy", "v2exp"))
def test_old_profiles_keep_all_legacy_entries(profile: str) -> None:
    state = _state(profile)
    table = talent_table_for_current_profile(state)
    player = Player("p1", "P1", controller=ForfeitController())
    state.add_player(player)

    assert table == TALENT_TABLE
    assert len(table) == 14
    assert type(instantiate_talent(state, Hologram, "p1")) is Hologram
    assert not hasattr(state, "m9_system")


@pytest.mark.parametrize("profile", ("legacy", "v2exp"))
def test_old_profiles_keep_number_five_as_combo(profile: str) -> None:
    state = _state(profile)
    player = Player("p1", "P1", controller=ForfeitController())
    state.add_player(player)
    entry = find_talent_entry(5, game_state=state)

    assert entry is not None
    assert entry[2] is Combo
    talent = assign_talent_entry(state, player, entry)
    assert type(talent) is Combo
    assert player.talent_slot_id == "T5"


def test_state_freezes_m9_profile_when_global_profile_changes() -> None:
    state = _state("m9-rfc")
    player = Player("p1", "P1", controller=ForfeitController())
    state.add_player(player)
    experiments.set_profile("legacy")

    talent = instantiate_talent(state, Hologram, "p1")

    assert state.profile == "m9-rfc"
    assert isinstance(talent, Hologram9)
    assert len(talent_table_for_current_profile(state)) == 14
    assert state.m9_system.get_sp("p1") == 1


def test_state_freezes_legacy_profile_when_global_profile_changes() -> None:
    state = _state("legacy")
    experiments.set_profile("m9-rfc")

    talent = instantiate_talent(state, Hologram, "p1")

    assert state.profile == "legacy"
    assert type(talent) is Hologram
    assert len(talent_table_for_current_profile(state)) == 14
    assert not hasattr(state, "m9_system")


def test_cli_force_rejects_blocked_slot_with_registry_reason() -> None:
    experiments.set_profile("m9-rfc")
    from main import setup_game_cli

    with pytest.raises(M9TalentUnavailableError, match="M9 槽位 T5"):
        setup_game_cli(SimpleNamespace(
            mode="all_ai", players=2, humans=0, ai=[], force_talent="T5",
        ))


def test_assignment_persists_stable_slot_identity() -> None:
    state = _state("m9-rfc")
    player = Player("p1", "P1", controller=ForfeitController())
    state.add_player(player)
    entry = find_talent_entry("G2", game_state=state)
    assert entry is not None
    talent = assign_talent_entry(state, player, entry)

    assert talent.slot_id == "G2"
    assert player.talent_slot_id == "G2"


def test_stats_uses_stable_g0_slot_instead_of_legacy_display_name() -> None:
    state = _state("m9-rfc")
    player = Player("p1", "P1", controller=ForfeitController())
    state.add_player(player)
    entry = find_talent_entry("G0", game_state=state)
    assert entry is not None
    assign_talent_entry(state, player, entry)

    from stats_runner import _refresh_talent_lookup, _talent_num_for_player
    _refresh_talent_lookup(state)

    assert _talent_num_for_player(player) == 5


def test_stats_rl_numeric_path_propagates_blocked_slot() -> None:
    state = _state("m9-rfc")
    del state
    from stats_runner import run_single_game

    class DummyRL(ForfeitController):
        def reset_game_state(self):
            return None

        def set_player_ref(self, player, game_state):
            return None

    with pytest.raises(M9TalentUnavailableError, match="M9 槽位 T5"):
        run_single_game(2, rl_controller=DummyRL(), rl_talent_mode="T5")


def test_rl_env_propagates_blocked_slot_from_game_thread() -> None:
    pytest.importorskip("gymnasium")
    pytest.importorskip("numpy")
    pytest.importorskip("torch")
    experiments.set_profile("m9-rfc")
    from rl.env import BadtimeWarEnv

    env = BadtimeWarEnv(
        num_opponents=1, max_rounds=1, n_stack=1, rl_talent="T5",
    )
    try:
        with pytest.raises(M9TalentUnavailableError, match="M9 槽位 T5"):
            env.reset(seed=1)
    finally:
        env.close()
