"""M9 T6 批次 3 验收：真实 setup → round → turn → talent E2E + 强制 CLI。

T6：市民热线作为特殊根行动在真实槽结算；警察管线 R2/R4 在真实轮次中推进。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from controllers.forfeit_controller import ForfeitController
from engine import experiments
from engine.game_state import GameState
from engine.m9.gate import ensure_state_mechanisms
from engine.m9.talent_registry import M9_TALENT_REGISTRY, TalentAvailability
from engine.round_manager import RoundManager
from models.player import Player


@pytest.fixture(autouse=True)
def _m9_profile():
    experiments.reset()
    experiments.set_profile("m9-rfc")
    yield
    experiments.reset()


def _player(player_id: str, controller=None) -> Player:
    player = Player(player_id, player_id.upper(),
                    controller=controller or ForfeitController())
    player.is_awake = True
    player.location = "商店"
    return player


def test_slot_is_implemented_in_registry() -> None:
    reg = M9_TALENT_REGISTRY["T6"]
    assert reg.availability is TalentAvailability.IMPLEMENTED
    assert reg.m9_class_path is not None
    assert reg.is_selectable


def test_forced_cli_selects_slot() -> None:
    from main import setup_game_cli

    state = setup_game_cli(SimpleNamespace(
        mode="all_ai", players=2, humans=0, ai=[], force_talent="T6", seed=1))
    assert state.profile == "m9-rfc"
    assigned = {p.talent_slot_id for p in (
        state.get_player(pid) for pid in state.player_order)}
    assert "T6" in assigned


def test_t6_round_turn_talent_e2e() -> None:
    """T6 联防整备在真实槽结算 + 警察局挂载在真实局。"""
    from engine.m9.talents.t6 import GoodCitizen9

    state = GameState()
    ensure_state_mechanisms(state)
    performer = _player("p1")
    other = _player("p2")
    state.add_player(performer)
    state.add_player(other)
    performer.talent = GoodCitizen9(performer.player_id, state)
    station = state.m9_police
    station.ensure_roster()
    station.units()[0].location = "警察局"
    state.m9_system.set_sp("p1", 2)
    state.m9_system.register_performance("p1", 1)
    manager = RoundManager(state)
    state.current_round = 1

    manager._phase_r0()
    manager._phase_r1()
    manager._phase_r3()

    grant = next(g for g in state.m9_round_grants if g.actor_id == "p1")
    outcome = state.m9_system.outcome(grant.grant_id)
    assert outcome.slot_resolved
    from engine.balance import get as _bget
    expected_roster = int(_bget("m9_system", "police", "fixed_roster", default=3))
    assert state.m9_police.fixed_roster_size() == expected_roster


def test_m9_game_state_builds_fixed_police_roster_at_setup() -> None:
    """真实 GameState 初始化即建立固定编制，不依赖测试夹具补建。"""
    state = GameState()
    units = state.m9_police.units()
    assert len(units) == state.m9_police.fixed_roster_size()
    assert all(unit.location == "警察局" for unit in units)
    RoundManager(state)
    state.current_round = 1
    state.m9_police.r0_tick(state, 1)
    state.m9_police.r2_tick(state, 1)
    from engine.balance import get as _bget
    expected_roster = int(_bget("m9_system", "police", "fixed_roster", default=3))
    assert len(state.m9_police.units()) == expected_roster


def test_hotline_special_op_is_m9_gated() -> None:
    """热线举报只在 M9 profile 出现（v2exp 特殊操作不变）。"""
    from actions.special_op import get_available_specials

    experiments.reset()
    experiments.set_profile("m9-rfc")
    experiments.enable("hp20")
    from engine.m9.talents.t6 import GoodCitizen9
    state = GameState()
    ensure_state_mechanisms(state)
    p = _player("p1")
    other = _player("p2")
    state.add_player(p)
    state.add_player(other)
    p.talent = GoodCitizen9(p.player_id, state)
    state.m9_police.ensure_roster()
    specs = get_available_specials(p, state)
    assert any(s["name"].startswith("热线举报") for s in specs)

    experiments.reset()
    experiments.set_profile("v2exp")
    from talents.t6_good_citizen import GoodCitizen
    state2 = GameState()
    p2 = _player("p1")
    state2.add_player(p2)
    p2.talent = GoodCitizen(p2.player_id, state2)
    specs2 = get_available_specials(p2, state2)
    assert not any(s["name"].startswith("热线举报") for s in specs2)


def test_t6_legacy_regression_path_still_works() -> None:
    from talents.t6_good_citizen import GoodCitizen

    experiments.reset()
    experiments.set_profile("v2exp")
    state = GameState()
    p = _player("p1")
    state.add_player(p)
    t = GoodCitizen("p1", state)
    p.talent = t
    t.on_register()
    assert "进入军事基地" in state.crime_types  # 旧全局犯罪扩展只在 v2exp


def test_all_ai_short_game_with_forced_t6_does_not_crash() -> None:
    """BasicAI 通用控制器下，强制 T6 的双方局至少能跑完 2 轮不崩溃。"""
    from main import setup_game_cli

    state = setup_game_cli(SimpleNamespace(
        mode="all_ai", players=2, humans=0, ai=[],
        force_talent="T6", seed=1))
    manager = RoundManager(state)
    for _ in range(2):
        manager.run_one_round()
        if state.check_victory():
            break
    assert True
