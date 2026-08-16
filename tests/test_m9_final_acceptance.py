"""M9 最终验收：十四活跃槽参数化强制 setup/E2E。

每个活跃槽（T1/T2/T3/T4/T6/T7/G0-G7）都必须：
- 注册表可实例化（fail-closed 之外）；
- 强制选择该天赋的真实 CLI 可建局；
- 强制槽实例必须是对应的具体 adapter（空 stub 会失败）；
- BasicAI 通用控制器下真实跑过 wake→标准行动，且所有 grant 完成收尾；
- T5 字符串仍返回退役错误；编号 5 解析为 G0。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from engine import experiments
from engine.m9.talent_registry import (
    M9_ACTIVE_SLOT_IDS,
    M9_TALENT_REGISTRY,
    M9TalentUnavailableError,
    TalentAvailability,
    resolve_registration,
)
from engine.round_manager import RoundManager


@pytest.fixture(autouse=True)
def _m9_profile():
    experiments.reset()
    experiments.set_profile("m9-rfc")
    yield
    experiments.reset()


ALL_ACTIVE_SLOTS = (
    "T1", "T2", "T3", "T4", "T6", "T7",
    "G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7",
)

EXPECTED_ADAPTERS = {
    "T1": "OneSlash9",
    "T2": "ScissorRush9",
    "T3": "Star9",
    "T4": "Hexagram9",
    "T6": "GoodCitizen9",
    "T7": "Resurrection9",
    "G0": "ShirokoTerror9",
    "G1": "G1MythFire9",
    "G2": "Hologram9",
    "G3": "Mythland9",
    "G4": "Savior9",
    "G5": "Ripple9",
    "G6": "CutawayJoke9",
    "G7": "Hoshino9",
}


def test_active_slots_are_all_implemented() -> None:
    assert tuple(M9_ACTIVE_SLOT_IDS) == ALL_ACTIVE_SLOTS
    for slot in ALL_ACTIVE_SLOTS:
        reg = M9_TALENT_REGISTRY[slot]
        assert reg.availability is TalentAvailability.IMPLEMENTED, slot
        assert reg.is_selectable, slot
        assert reg.m9_class_path is not None, slot


@pytest.mark.parametrize("slot", ALL_ACTIVE_SLOTS)
def test_forced_cli_builds_game_for_each_slot(slot: str) -> None:
    from main import setup_game_cli

    state = setup_game_cli(SimpleNamespace(
        mode="all_ai", players=2, humans=0, ai=[],
        force_talent=slot, seed=1))
    assert state.profile == "m9-rfc"
    assigned = {p.talent_slot_id for p in (
        state.get_player(pid) for pid in state.player_order)}
    assert slot in assigned


@pytest.mark.parametrize("slot", ALL_ACTIVE_SLOTS)
def test_two_round_all_ai_game_per_slot(slot: str) -> None:
    """真实 profile/setup/round/turn：具体 adapter 与标准槽收尾都必须成立。"""
    from main import setup_game_cli

    state = setup_game_cli(SimpleNamespace(
        mode="all_ai", players=2, humans=0, ai=[],
        force_talent=slot, seed=1))
    forced = next(
        state.get_player(pid)
        for pid in state.player_order
        if getattr(state.get_player(pid), "talent_slot_id", None) == slot
    )
    assert type(forced.talent).__name__ == EXPECTED_ADAPTERS[slot]
    assert type(forced.talent).__module__ == f"engine.m9.talents.{slot.lower()}"

    manager = RoundManager(state)
    for _ in range(2):
        manager.run_one_round()
        if state.check_victory():
            break
    assert state.current_round >= 2
    assert state.m9_round_grants
    outcomes = [
        state.m9_system.outcome(grant.grant_id)
        for grant in state.m9_round_grants
    ]
    assert all(outcome.slot_resolved for outcome in outcomes)
    assert all(outcome.resolution_kind in {
        "action_performed", "forfeit", "no_target", "suppressed",
        "wake_followup", "petrified_hold", "aid_rest",
    } for outcome in outcomes)


def test_number_five_maps_to_g0_and_t5_retired() -> None:
    assert resolve_registration("5").slot_id == "G0"
    assert M9_TALENT_REGISTRY["T5"].availability is TalentAvailability.RETIRED
    assert M9_TALENT_REGISTRY["T5"].replacement_slot_id == "G0"


def test_forced_string_t5_rejected() -> None:
    from main import setup_game_cli

    with pytest.raises(M9TalentUnavailableError, match="M9 槽位 T5"):
        setup_game_cli(SimpleNamespace(
            mode="all_ai", players=2, humans=0, ai=[],
            force_talent="T5", seed=1))


def test_registry_has_no_blocked_active_slots() -> None:
    blocked = [slot for slot in M9_ACTIVE_SLOT_IDS
               if M9_TALENT_REGISTRY[slot].availability
               is not TalentAvailability.IMPLEMENTED]
    assert blocked == []


# ════════════════════════════════════════════════════════════
#  机制探针（release-gate 债务修复）：每个活跃槽断言一个真实的、
#  槽位专属的世界状态效果，经真实管线（setup → R0 报名/公演位固化 →
#  R1 标准槽派发 → R3 T0 演出/真实派发）产生，而不是只断言「不抛异常」。
#  实现见 tests/m9_acceptance_probes.py（每个探针标注其可观察量）。
# ════════════════════════════════════════════════════════════

from tests.m9_acceptance_probes import PROBES, probe_world  # noqa: E402


@pytest.mark.parametrize("slot", ALL_ACTIVE_SLOTS)
def test_slot_probe_asserts_real_world_effect(slot, monkeypatch) -> None:
    """槽位机制探针：真实 adapter 经真实管线产生可观察的世界状态效果。"""
    state, p1, p2 = probe_world()
    PROBES[slot](state, p1, p2, monkeypatch)


@pytest.mark.parametrize("slot", ALL_ACTIVE_SLOTS)
def test_empty_stub_swap_fails_slot_probe(slot) -> None:
    """负向控制：把强制槽 adapter 换成空 stub 后，对应槽位 E2E 必须失败。

    - 真实 R0 门 fail-closed：空 stub 无 get_t0_option → 永不获公演位；
    - 真实 T0 通道：_phase_t0 直接 AttributeError（空 stub 必然失败）；
    - 世界状态零效果：SP 未消费、槽位事件/效果不产生。
    """
    from engine.m9.talents.stub import M9TalentStub
    from tests.m9_acceptance_probes import (
        RegistrationController,
        arrange_g0,
        arrange_g3,
        arrange_t6,
    )

    state, p1, p2 = probe_world()
    arrange = {"G0": arrange_g0, "G3": arrange_g3, "T6": arrange_t6}.get(slot)
    if arrange is not None:
        arrange(state, p1, p2)
    if slot == "G3":
        p1.controller = RegistrationController()  # 真实 R0 报名尝试
    p1.talent = M9TalentStub()

    manager = RoundManager(state)
    state.current_round = 1
    manager._phase_r0()  # 真实 R0：报名窗口 + 公演位固化

    m9 = state.m9_system
    if slot == "G3":
        # R0 门 fail-closed：SP=2 也无法被固化公演位
        assert m9.assign_public_slot(1) is None
    # 真实 T0 通道：空 stub 没有 get_t0_option → E2E 失败
    with pytest.raises(AttributeError, match="get_t0_option"):
        manager.turn_manager._phase_t0(p1)
    # 世界状态零效果
    assert m9.get_sp("p1") == (2 if slot in ("G3", "T6") else 1)
    event_types = {e["type"] for e in state.event_log}
    if slot == "G0":
        assert "g0_drone_summon" not in event_types
        assert state.get_actor("g0_drone:p1") is None
    if slot == "G3":
        assert "m9_g3_expand" not in event_types
        assert not getattr(p1.talent, "barrier_active", False)
    if slot == "T6":
        assert "t6_equip" not in event_types
        assert state.m9_police.units()[0].weapon_name == "警棍"  # 未移交
        assert p1.has_weapon("高斯步枪")
        assert state.m9_police.cases == []
