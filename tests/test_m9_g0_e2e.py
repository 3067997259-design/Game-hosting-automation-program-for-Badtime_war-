"""M9 G0 批次 5 验收：真实 setup → round → turn → talent E2E + 强制 CLI。

G0：编号 5 解析到 G0；字符串 T5 仍返回退役错误；无人机/撤退在真实槽结算。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from controllers.forfeit_controller import ForfeitController
from engine import experiments
from engine.game_state import GameState
from engine.m9.gate import ensure_state_mechanisms
from engine.m9.talent_registry import (
    M9_TALENT_REGISTRY,
    M9TalentUnavailableError,
    TalentAvailability,
    resolve_registration,
)
from engine.round_manager import RoundManager
from models.player import Player
from models.equipment import make_weapon


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
    reg = M9_TALENT_REGISTRY["G0"]
    assert reg.availability is TalentAvailability.IMPLEMENTED
    assert reg.m9_class_path is not None
    assert reg.is_selectable
    assert reg.legacy_number == 5


def test_number_five_resolves_to_g0_and_t5_stays_retired() -> None:
    by_number = resolve_registration("5")
    assert by_number is not None
    assert by_number.slot_id == "G0"
    by_slot = resolve_registration("T5")
    assert by_slot is not None
    assert by_slot.slot_id == "T5"
    assert by_slot.availability is TalentAvailability.RETIRED
    assert by_slot.replacement_slot_id == "G0"


def test_forced_cli_selects_slot() -> None:
    from main import setup_game_cli

    state = setup_game_cli(SimpleNamespace(
        mode="all_ai", players=2, humans=0, ai=[], force_talent="G0", seed=1))
    assert state.profile == "m9-rfc"
    assigned = {p.talent_slot_id for p in (
        state.get_player(pid) for pid in state.player_order)}
    assert "G0" in assigned


def test_forced_cli_number_five_assigns_g0() -> None:
    from main import setup_game_cli

    state = setup_game_cli(SimpleNamespace(
        mode="all_ai", players=2, humans=0, ai=[],
        force_talent="5", seed=1))
    assigned = {p.talent_slot_id for p in (
        state.get_player(pid) for pid in state.player_order)}
    assert "G0" in assigned


def test_forced_cli_string_t5_rejected_as_retired() -> None:
    from main import setup_game_cli

    with pytest.raises(M9TalentUnavailableError, match="M9 槽位 T5"):
        setup_game_cli(SimpleNamespace(
            mode="all_ai", players=2, humans=0, ai=[],
            force_talent="T5", seed=1))


def test_g0_round_turn_talent_e2e() -> None:
    """G0 即演召唤无人机在真实槽结算。"""
    from engine.m9.talents.g0 import ShirokoTerror9

    state = GameState()
    ensure_state_mechanisms(state)
    performer = _player("p1")
    other = _player("p2")
    state.add_player(performer)
    state.add_player(other)
    performer.talent = ShirokoTerror9(performer.player_id, state)
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


def test_g0_legacy_number_five_keeps_combo_in_v2exp() -> None:
    """双管线：v2exp 编号 5 仍是 Combo。"""
    experiments.reset()
    experiments.set_profile("v2exp")
    from engine.game_setup import find_talent_entry
    from talents.t5_combo import Combo

    state = GameState()
    entry = find_talent_entry(5, game_state=state)
    assert entry is not None
    assert entry[2] is Combo


def test_all_ai_short_game_with_forced_g0_does_not_crash() -> None:
    """BasicAI 强制 G0 的真实标准槽完成收尾（召唤门：SP≥2 才召唤，
    短局允许未召唤——不再 SP=1 无脑召唤烧 HP）。"""
    from main import setup_game_cli

    state = setup_game_cli(SimpleNamespace(
        mode="all_ai", players=2, humans=0, ai=[],
        force_talent="G0", seed=1))
    manager = RoundManager(state)
    for _ in range(2):
        manager.run_one_round()
        if state.check_victory():
            break
    assert state.current_round >= 2
    g0 = next(state.get_player(pid) for pid in state.player_order
              if state.get_player(pid).talent_slot_id == "G0")
    if g0.talent.drone is not None:
        assert g0.talent.drone["hp"] > 0
    outcomes = [state.m9_system.outcome(g.grant_id)
                for g in state.m9_round_grants]
    assert outcomes and all(o is not None and o.slot_resolved for o in outcomes)


def test_drone_is_public_target_but_never_gets_action_slot() -> None:
    """无人机进入公共目标解析，但不进入独立 actor/标准槽。"""
    from engine.m9.talents.g0 import ShirokoTerror9

    state = GameState()
    owner = _player("p1")
    attacker = _player("p2")
    state.add_player(owner)
    state.add_player(attacker)
    owner.talent = ShirokoTerror9(owner.player_id, state)
    state.m9_system.set_sp(owner.player_id, 1)
    _, ok = owner.talent.execute_t0(owner)
    assert ok

    target = state.get_actor("g0_drone:p1")
    assert target is not None
    assert target.is_alive()
    assert target.location == owner.location
    assert target in list(state.iter_targetable_actors())
    assert target not in list(state.iter_actors())
    assert target not in list(state.iter_action_actors(2))


def test_public_attack_enumerates_and_destroys_drone_without_crime() -> None:
    """真实 action 枚举/执行可以攻击无人机，并把关注归到 G0 所有者。"""
    from cli.parser import parse, resolve_player_target
    from cli.validator import validate
    from engine.action_enumerator import build_action_options
    from engine.action_turn import ActionTurnManager
    from engine.m9.talents.g0 import ShirokoTerror9

    state = GameState()
    state.current_round = 1
    owner = _player("p1")
    attacker = _player("p2")
    state.add_player(owner)
    state.add_player(attacker)
    owner.talent = ShirokoTerror9(owner.player_id, state)
    attacker.weapons.append(make_weapon("高斯步枪"))
    state.m9_system.set_sp(owner.player_id, 1)
    _, ok = owner.talent.execute_t0(owner)
    assert ok
    owner.talent.drone["hp"] = 1

    options = build_action_options(
        attacker, state, ["attack", "find", "lock"])
    assert any("无人机" in option for option in options["attack"])
    assert not any("无人机" in option for option in options.get("find", []))
    assert not any("无人机" in option for option in options.get("lock", []))
    command = next(option for option in options["attack"] if "无人机" in option)
    parsed = parse(command, attacker.player_id)
    valid, reason = validate(parsed, attacker, state)
    assert valid, reason
    assert resolve_player_target(parsed["target"], state) == owner.talent.drone_target_id
    message, action_type, consumed = ActionTurnManager(state)._execute_attack(
        parsed, attacker)

    assert not message.startswith("❌")
    assert action_type == "attack"
    assert consumed
    assert owner.talent.drone is None
    assert not attacker.is_criminal
    attacks = [e for e in state.event_log if e["type"] == "attack"]
    assert attacks[-1]["target"] == "g0_drone:p1"


def test_common_owner_death_notification_removes_drone() -> None:
    """死亡收尾会通知已死亡的 G0 adapter，无人机不能成为孤儿对象。"""
    from engine.m9.combat import finalize_death
    from engine.m9.talents.g0 import ShirokoTerror9

    state = GameState()
    owner = _player("p1")
    other = _player("p2")
    state.add_player(owner)
    state.add_player(other)
    owner.talent = ShirokoTerror9(owner.player_id, state)
    state.m9_system.set_sp(owner.player_id, 1)
    _, ok = owner.talent.execute_t0(owner)
    assert ok
    assert owner.talent.drone is not None

    owner.hp = 0
    assert finalize_death(state, owner, other, source_kind="test")
    assert owner.talent.drone is None
    assert state.get_actor("g0_drone:p1") is None


def test_ar_multiword_weapon_command_parses_and_validates() -> None:
    """G0 主战回归：带空格的 BLACK FANG 465 必须能过 parser+validator。

    旧 parser 只取第 3 个 token（BLACK），AR 攻击 100% 被 validator 拒收。
    """
    from cli.parser import parse
    from cli.validator import validate
    from engine.m9.talents.g0 import ShirokoTerror9

    state = GameState()
    owner = _player("p1")
    target = _player("p2")
    state.add_player(owner)
    state.add_player(target)
    owner.talent = ShirokoTerror9(owner.player_id, state)
    state.markers.add_relation(target.player_id, "LOCKED_BY", owner.player_id)
    parsed = parse(f"attack {target.name} BLACK FANG 465", owner.player_id)
    assert parsed["weapon"] == "BLACK FANG 465"
    valid, reason = validate(parsed, owner, state)
    assert valid, reason
    assert parsed["weapon"] == "BLACK FANG 465"


def test_ar_attack_spends_magazine_and_find_converts_arrows() -> None:
    """AR 的真实伤害入口扣弹；find 的箭堆进入同一弹匣。"""
    from actions.find_target import execute as find
    from combat.damage_resolver import resolve_damage
    from engine.m9.talents.g0 import AR_WEAPON_NAME, ShirokoTerror9

    state = GameState()
    owner = _player("p1")
    target = _player("p2")
    state.add_player(owner)
    state.add_player(target)
    owner.talent = ShirokoTerror9(owner.player_id, state)
    owner.talent.magazine = 1
    before = target.hp
    resolve_damage(owner, target, owner.get_weapon(AR_WEAPON_NAME), state)
    assert owner.talent.magazine == 0
    assert target.hp < before
    after_first = target.hp
    resolve_damage(owner, target, owner.get_weapon(AR_WEAPON_NAME), state)
    assert target.hp == after_first

    state.arrow_piles = {owner.location: 2}
    find(owner, target.player_id, state)
    assert owner.talent.magazine == 6
    assert state.arrow_piles[owner.location] == 0


def test_public_ar_attack_returns_and_logs_real_cooperative_damage() -> None:
    """公共 attack 的 result/event 必须反映主 hit + 无人机追加，不能返回零伤占位。"""
    from actions import attack
    from engine.balance import get as bget
    from engine.m9.talents.g0 import ShirokoTerror9

    ar_dmg = int(bget("m9_talents_extended", "g0", "ar_base_damage", default=3))
    drone_dmg = int(bget("m9_talents_extended", "g0", "drone_bonus_damage",
                         default=1))
    expected = ar_dmg + drone_dmg  # 无甲目标：神秘属性=AR 伤 + 无人机追加

    state = GameState()
    state.current_round = 1
    owner = _player("p1")
    target = _player("p2")
    state.add_player(owner)
    state.add_player(target)
    owner.talent = ShirokoTerror9(owner.player_id, state)
    state.m9_system.set_sp(owner.player_id, 1)
    _, ok = owner.talent.execute_t0(owner)
    assert ok

    before = target.hp
    message, result = attack.execute(
        owner, target.player_id, "BLACK FANG 465", state)

    assert not message.startswith("❌")
    assert target.hp == before - expected
    assert result["hp_damage"] == expected
    assert result["final_damage"] == expected
    assert not result["killed"]
    event = [e for e in state.event_log if e["type"] == "attack"][-1]
    assert event["result"]["hp_damage"] == expected
    assert event["result"]["final_damage"] == expected


def test_public_ar_cooperative_lethal_reports_kill_once() -> None:
    """无人机追加致死必须聚合到外层 result，且公共死亡只记一次击杀。"""
    from actions import attack
    from engine.m9.talents.g0 import ShirokoTerror9

    state = GameState()
    state.current_round = 1
    owner = _player("p1")
    target = _player("p2")
    state.add_player(owner)
    state.add_player(target)
    owner.talent = ShirokoTerror9(owner.player_id, state)
    state.m9_system.set_sp(owner.player_id, 1)
    _, ok = owner.talent.execute_t0(owner)
    assert ok
    target.hp = 1  # R19 数值：AR 基础 1 + 无人机追加 0 = 1 伤致死

    _, result = attack.execute(
        owner, target.player_id, "BLACK FANG 465", state)

    assert result["hp_damage"] == 1
    assert result["final_damage"] == 1
    assert result["killed"]
    assert owner.kill_count == 1
    deaths = [e for e in state.event_log
              if e["type"] == "death" and e["player"] == target.player_id]
    assert len(deaths) == 1


def test_retreat_removes_g0_from_actor_target_and_drops_equipment() -> None:
    """撤退是真退场：不占胜利/行动/目标池，装备落地且无人机消失。"""
    from engine.m9.talents.g0 import ShirokoTerror9

    state = GameState()
    owner = _player("p1")
    survivor = _player("p2")
    state.add_player(owner)
    state.add_player(survivor)
    owner.talent = ShirokoTerror9(owner.player_id, state)
    owner.talent_slot_id = "G0"
    owner.weapons.append(make_weapon("高斯步枪"))
    state.m9_system.set_sp(owner.player_id, 1)
    _, ok = owner.talent.execute_t0(owner)
    assert ok

    owner.talent._retreat(owner, reason="test")
    assert state.get_actor(owner.player_id) is None
    assert owner not in list(state.iter_actors())
    assert owner not in list(state.iter_action_actors(2))
    assert state.check_victory() == survivor.player_id
    assert state.get_actor("g0_drone:p1") is None
    dropped = state.ground_loot[owner.location]["weapons"]
    assert any(entry["name"] == "高斯步枪" for entry in dropped)


def test_pickup_preserves_owner_slot_and_marks_real_relic() -> None:
    """装备遗留从掉落到 find 拾取均保留原天赋槽，并自动注册遗物。"""
    from actions.find_target import execute as find
    from engine.m9.talents.g0 import ShirokoTerror9

    state = GameState()
    owner = _player("p1")
    donor = _player("p2")
    state.add_player(owner)
    state.add_player(donor)
    owner.talent = ShirokoTerror9(owner.player_id, state)
    donor.talent_slot_id = "T3"
    donor.weapons.append(make_weapon("高斯步枪"))

    state.drop_loot_on_retreat(donor)
    find(owner, donor.player_id, state)

    assert owner.has_weapon("高斯步枪")
    assert {r["slot"] for r in owner.talent.relics} == {"T3"}
    assert owner.talent.relics[0]["kind"] == "weapon"


def test_summon_hp_cost_death_uses_common_finalizer() -> None:
    """显式 HP 成本致死不生成无人机，也不能绕开 SP/队列/死亡清理。"""
    from engine.m9.talents.g0 import ShirokoTerror9

    state = GameState()
    owner = _player("p1")
    other = _player("p2")
    state.add_player(owner)
    state.add_player(other)
    owner.talent = ShirokoTerror9(owner.player_id, state)
    owner.hp = 1
    state.m9_system.set_sp(owner.player_id, 1)

    _, resolved = owner.talent.execute_t0(owner)

    assert resolved
    assert owner.hp == 0
    assert owner.talent.drone is None
    assert state.m9_system.get_sp(owner.player_id) == 0
    assert getattr(owner, "_m9_death_finalized", False)
    deaths = [e for e in state.event_log if e["type"] == "death"]
    assert deaths[-1]["source_kind"] == "g0_drone_hp_cost"


def test_v2exp_ground_loot_keeps_legacy_string_shape() -> None:
    """provenance 只属于 m9-rfc；旧管线继续暴露字符串装备清单。"""
    experiments.reset()
    experiments.set_profile("v2exp")
    state = GameState()
    donor = _player("p1")
    state.add_player(donor)
    donor.weapons.append(make_weapon("高斯步枪"))
    state.current_round = 13
    donor.hp = 0

    state.drop_loot_on_death(donor)

    assert state.ground_loot[donor.location]["weapons"] == ["高斯步枪"]
