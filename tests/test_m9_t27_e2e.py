"""M9 T2/T7 批次 2 验收：真实 setup → round → turn → talent E2E + 强制 CLI。

T2：即演/公演核心在真实槽结算；T7：挂载在真实槽结算、保险全局唯一。
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
from models.equipment import make_weapon
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


@pytest.mark.parametrize("slot", ("T2", "T7"))
def test_slot_is_implemented_in_registry(slot: str) -> None:
    reg = M9_TALENT_REGISTRY[slot]
    assert reg.availability is TalentAvailability.IMPLEMENTED
    assert reg.m9_class_path is not None
    assert reg.is_selectable


@pytest.mark.parametrize("slot", ("T2", "T7"))
def test_forced_cli_selects_slot(slot: str) -> None:
    from main import setup_game_cli

    state = setup_game_cli(SimpleNamespace(
        mode="all_ai", players=2, humans=0, ai=[], force_talent=slot, seed=1))
    assert state.profile == "m9-rfc"
    assigned = {p.talent_slot_id for p in (
        state.get_player(pid) for pid in state.player_order)}
    assert slot in assigned


def test_t2_round_turn_talent_e2e() -> None:
    """T2 即演：已锁定目标在真实槽上被斩击。"""
    from importlib import import_module
    from engine.m9.talents.t2 import ScissorRush9

    state = GameState()
    ensure_state_mechanisms(state)
    performer = _player("p1")
    other = _player("p2")
    state.add_player(performer)
    state.add_player(other)
    performer.talent = ScissorRush9(performer.player_id, state)
    other.hp = 20
    other.max_hp = 20
    performer.weapons.append(make_weapon("小刀"))
    # p2 被 p1 锁定（方向：目标 LOCKED_BY → 执行者；旧方向是 vacuous 伪阳性）
    state.markers.add_relation("p2", "LOCKED_BY", "p1")
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
    # 真实效果断言：T2 核心斩击必须实际造成伤害（仅 slot_resolved 不足以证明）
    assert other.hp < 20


def test_t7_round_turn_talent_e2e() -> None:
    """T7 挂载在真实槽上结算：保险挂载 + SP 消费。"""
    from engine.m9.talents.t7 import Resurrection9

    state = GameState()
    ensure_state_mechanisms(state)
    performer = _player("p1")
    other = _player("p2")
    state.add_player(performer)
    state.add_player(other)
    performer.talent = Resurrection9(performer.player_id, state)
    state.m9_system.set_sp("p1", 1)
    manager = RoundManager(state)
    state.current_round = 1

    manager._phase_r0()
    manager._phase_r1()
    manager._phase_r3()

    grant = next(g for g in state.m9_round_grants if g.actor_id == "p1")
    outcome = state.m9_system.outcome(grant.grant_id)
    assert outcome.slot_resolved
    assert state.m9_insurance.is_mounted()
    assert state.m9_system.get_sp("p1") <= 1


def test_t2_legacy_regression_path_still_works() -> None:
    from talents.t2_scissor_rush import ScissorRush

    experiments.reset()
    experiments.set_profile("v2exp")
    state = GameState()
    p = _player("p1")
    state.add_player(p)
    t = ScissorRush("p1", state)
    p.talent = t
    assert t.attack_count == 0
    assert t.response_uses_remaining == 1  # 旧响应窗口只在 v2exp 保留


def test_t7_legacy_regression_path_still_works() -> None:
    from talents.t7_resurrection import Resurrection

    experiments.reset()
    experiments.set_profile("v2exp")
    state = GameState()
    p = _player("p1")
    state.add_player(p)
    t = Resurrection("p1", state)
    p.talent = t
    assert not t.learned  # 旧学习前置只在 v2exp 保留
    assert t.mounted_on is None


@pytest.mark.parametrize("slot", ("T2", "T7"))
def test_all_ai_short_game_with_forced_talent_does_not_crash(slot: str) -> None:
    """BasicAI 通用控制器下，强制该天赋的双方局至少能跑完 2 轮不崩溃。"""
    from main import setup_game_cli

    state = setup_game_cli(SimpleNamespace(
        mode="all_ai", players=2, humans=0, ai=[],
        force_talent=slot, seed=1))
    manager = RoundManager(state)
    for _ in range(2):
        manager.run_one_round()
        if state.check_victory():
            break
    assert True
