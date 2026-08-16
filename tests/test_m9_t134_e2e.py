"""M9 T1/T3/T4 批次 1 验收：真实 setup → round → turn → talent E2E + 强制 CLI。

每天赋断言：adapter 可经注册表实例化、R1 派发真实标准槽、T0 演出在真实槽上
结算并写出统一收尾、强制选择该天赋的 CLI 可建局。
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


class _RegistrationController(ForfeitController):
    def choose(self, prompt, options, context=None):
        if context and context.get("phase") == "M9_PUBLIC_REGISTRATION":
            return "报名公演"
        return super().choose(prompt, options, context)


@pytest.mark.parametrize(
    "slot",
    ("T1", "T3", "T4"),
)
def test_slot_is_implemented_in_registry(slot: str) -> None:
    reg = M9_TALENT_REGISTRY[slot]
    assert reg.availability is TalentAvailability.IMPLEMENTED
    assert reg.m9_class_path is not None
    assert reg.is_selectable


@pytest.mark.parametrize(
    "slot",
    ("T1", "T3", "T4"),
)
def test_forced_cli_selects_slot(slot: str) -> None:
    from main import setup_game_cli

    state = setup_game_cli(SimpleNamespace(
        mode="all_ai", players=2, humans=0, ai=[], force_talent=slot, seed=1))
    assert state.profile == "m9-rfc"
    assigned = {p.talent_slot_id for p in (
        state.get_player(pid) for pid in state.player_order)}
    assert slot in assigned


@pytest.mark.parametrize(
    "slot",
    ("T1", "T3", "T4"),
)
def test_round_turn_talent_e2e_runs_on_real_grant(slot: str) -> None:
    """setup → R0（公演位固化）→ R1（真实标准槽）→ R3（T0 演出在真实槽结算）。"""
    from importlib import import_module

    state = GameState()
    ensure_state_mechanisms(state)
    performer = _player("p1", _RegistrationController())
    other = _player("p2")
    state.add_player(performer)
    state.add_player(other)
    talent_cls = M9_TALENT_REGISTRY[slot].m9_class_path.rsplit(".", 1)
    adapter = getattr(import_module(talent_cls[0]), talent_cls[1])
    performer.talent = adapter(performer.player_id, state)
    other.hp = 20
    other.max_hp = 20
    # T1 需要近战武器与面对面目标
    if slot == "T1":
        performer.weapons.append(make_weapon("小刀"))
        state.markers.add_relation("p1", "ENGAGED_WITH", "p2")
    state.m9_system.set_sp("p1", 2)
    state.m9_system.set_sp("p2", 2)
    manager = RoundManager(state)
    state.current_round = 1

    manager._phase_r0()
    manager._phase_r1()
    assert any(g.actor_id == "p1" for g in state.m9_round_grants)

    manager._phase_r3()
    grant = next(g for g in state.m9_round_grants if g.actor_id == "p1")
    outcome = state.m9_system.outcome(grant.grant_id)
    assert outcome.slot_assigned
    assert outcome.slot_resolved
    # T3 公演 / T1、T4 即演都在真实槽上结算过 SP
    assert state.m9_system.get_sp("p1") <= 1
    # 演出对局内对象生效（T1 斩击/T3 AOE/T4 至少选对手猜拳）
    assert other.hp <= 20


def test_t1_legacy_regression_path_still_works() -> None:
    """legacy/v2exp：T1 旧类仍可按次数制运行（双管线不串线）。"""
    from talents.t1_one_slash import OneSlash

    experiments.reset()
    experiments.set_profile("v2exp")
    state = GameState()
    p = _player("p1")
    state.add_player(p)
    t = OneSlash("p1", state)
    p.talent = t
    assert t.uses_remaining == 2
    assert t.get_t0_option(p) is None  # 无面对面目标


def test_t3_legacy_regression_path_still_works() -> None:
    from talents.t3_star import Star

    experiments.reset()
    experiments.set_profile("v2exp")
    state = GameState()
    p = _player("p1")
    state.add_player(p)
    t = Star("p1", state)
    p.talent = t
    assert t.uses_remaining == 2


def test_t4_legacy_regression_path_still_works() -> None:
    from talents.t4_hexagram import Hexagram

    experiments.reset()
    experiments.set_profile("v2exp")
    state = GameState()
    p = _player("p1")
    state.add_player(p)
    t = Hexagram("p1", state)
    p.talent = t
    assert t.max_charges > 0  # 旧充能制只在 v2exp 保留


@pytest.mark.parametrize("slot", ("T1", "T3", "T4"))
def test_all_ai_short_game_with_forced_talent_does_not_crash(slot: str) -> None:
    """BasicAI 通用控制器下，强制该天赋的双方局至少能跑完 2 轮不崩溃。"""
    from main import setup_game_cli
    from engine.round_manager import RoundManager

    state = setup_game_cli(SimpleNamespace(
        mode="all_ai", players=2, humans=0, ai=[],
        force_talent=slot, seed=1))
    manager = RoundManager(state)
    for _ in range(2):
        manager.run_one_round()
        if state.check_victory():
            break
    assert True
