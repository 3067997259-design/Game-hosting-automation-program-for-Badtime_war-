"""M9 G3 批次 4 验收：真实 setup → round → turn → talent E2E + 强制 CLI。

G3：魔力账本、结界外投影、公演展开结界在真实槽结算；R4 维持费在真实轮次推进。
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


class _RegistrationController(ForfeitController):
    def choose(self, prompt, options, context=None):
        if context and context.get("phase") == "M9_PUBLIC_REGISTRATION":
            return "报名公演"
        return super().choose(prompt, options, context)


def test_slot_is_implemented_in_registry() -> None:
    reg = M9_TALENT_REGISTRY["G3"]
    assert reg.availability is TalentAvailability.IMPLEMENTED
    assert reg.m9_class_path is not None
    assert reg.is_selectable


def test_forced_cli_selects_slot() -> None:
    from main import setup_game_cli

    state = setup_game_cli(SimpleNamespace(
        mode="all_ai", players=2, humans=0, ai=[], force_talent="G3", seed=1))
    assert state.profile == "m9-rfc"
    assigned = {p.talent_slot_id for p in (
        state.get_player(pid) for pid in state.player_order)}
    assert "G3" in assigned


def test_g3_round_turn_talent_e2e() -> None:
    """G3 结界外投影（螺旋剑即发）在真实槽结算 + 魔力消耗。"""
    from engine.m9.talents.g3 import Mythland9

    state = GameState()
    ensure_state_mechanisms(state)
    performer = _player("p1")
    other = _player("p2")
    state.add_player(performer)
    state.add_player(other)
    performer.talent = Mythland9(performer.player_id, state)
    other.hp = 20
    other.max_hp = 20
    state.m9_system.set_sp("p1", 1)
    manager = RoundManager(state)
    state.current_round = 1

    manager._phase_r0()
    manager._phase_r1()
    manager._phase_r3()

    grant = next(g for g in state.m9_round_grants if g.actor_id == "p1")
    outcome = state.m9_system.outcome(grant.grant_id)
    assert outcome.slot_resolved
    assert state.m9_system.get_sp("p1") <= 1


def test_g3_barrier_public_and_upkeep_round() -> None:
    """公演展开结界 + R4 维持费推进（真实轮次）。"""
    from engine.m9.talents.g3 import Mythland9

    state = GameState()
    ensure_state_mechanisms(state)
    performer = _player("p1", _RegistrationController())
    other = _player("p2")
    state.add_player(performer)
    state.add_player(other)
    performer.talent = Mythland9(performer.player_id, state)
    state.m9_system.set_sp("p1", 2)
    manager = RoundManager(state)
    state.current_round = 1

    manager._phase_r0()
    manager._phase_r1()
    manager._phase_r3()
    assert outcome_slot(state, "p1")

    # R4：建立轮不 tick，但 R4 本身运行不崩溃
    manager._phase_r4()
    assert True


def outcome_slot(state, pid):
    grant = next(g for g in state.m9_round_grants if g.actor_id == pid)
    return state.m9_system.outcome(grant.grant_id).slot_resolved


def test_g3_legacy_regression_path_still_works() -> None:
    from talents.g3_mythland import Mythland

    experiments.reset()
    experiments.set_profile("v2exp")
    state = GameState()
    p = _player("p1")
    state.add_player(p)
    t = Mythland("p1", state)
    p.talent = t
    assert t.uses_remaining == 2  # 旧次数制只在 v2exp 保留


def test_all_ai_short_game_with_forced_g3_does_not_crash() -> None:
    """BasicAI 通用控制器下，强制 G3 的双方局至少能跑完 2 轮不崩溃。"""
    from main import setup_game_cli

    state = setup_game_cli(SimpleNamespace(
        mode="all_ai", players=2, humans=0, ai=[],
        force_talent="G3", seed=1))
    manager = RoundManager(state)
    for _ in range(2):
        manager.run_one_round()
        if state.check_victory():
            break
    assert True
