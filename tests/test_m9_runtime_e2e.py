"""M9 profile 的真实 setup → round → turn → talent 纵向验收。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from controllers.forfeit_controller import ForfeitController
from engine import experiments
from engine.game_state import GameState
from engine.m9.combat import resolve_damage
from engine.m9.gate import instantiate_talent
from engine.m9.talents.g1 import G1MythFire9
from engine.m9.talents.g2 import Hologram9
from engine.m9.talents.g4 import Savior9
from engine.m9.talents.g5 import Ripple9
from engine.m9.talents.g6 import CutawayJoke9
from engine.m9.talents.g7 import Hoshino9
from engine.m9.talents.stub import M9TalentStub
from engine.m9.talents.t2 import ScissorRush9
from engine.round_manager import RoundManager
from models.equipment import ArmorLayer, ArmorPiece, Weapon, WeaponRange
from models.player import Player
from talents.g2_hologram import Hologram
from utils.attribute import Attribute


@pytest.fixture(autouse=True)
def _m9_profile():
    experiments.reset()
    experiments.set_profile("m9-rfc")
    yield
    experiments.reset()


def _player(player_id: str, controller=None) -> Player:
    player = Player(
        player_id,
        player_id.upper(),
        controller=controller or ForfeitController(),
    )
    player.is_awake = True
    player.location = "商店"
    return player


class _RegistrationController(ForfeitController):
    def choose(self, prompt, options, context=None):
        if context and context.get("phase") == "M9_PUBLIC_REGISTRATION":
            return "报名公演"
        return super().choose(prompt, options, context)


class _GrantingTalent(M9TalentStub):
    name = "测试用地火授予者"

    def __init__(self, state: GameState, target_id: str) -> None:
        self.state = state
        self.target_id = target_id
        self.issued = False

    def get_t0_option(self, player):
        return None

    def on_turn_end(self, player, action_type):
        if self.issued:
            return
        self.issued = True
        self.state.m9_system.dispatch_full_extra(
            self.target_id,
            self.state.current_round,
            "g5_poem_earthfire",
        )


class _OneCommandController(ForfeitController):
    def __init__(self, command: str) -> None:
        self.command = command
        self.used = False

    def get_command(self, player, game_state, available_actions, context=None):
        if not self.used:
            self.used = True
            return self.command
        return "forfeit"


@pytest.mark.parametrize(
    ("talent_number", "expected_cls"),
    (
        (8, G1MythFire9),
        (9, Hologram9),
        (11, Savior9),
        (12, Ripple9),
        (13, CutawayJoke9),
        (14, Hoshino9),
    ),
)
def test_profile_setup_seeds_sp_and_maps_talent(
        talent_number: int, expected_cls: type) -> None:
    from engine.game_setup import TALENT_TABLE

    state = GameState()
    player = _player("p1")
    state.add_player(player)
    legacy_cls = next(
        cls for number, _, cls, _ in TALENT_TABLE
        if number == talent_number)

    talent = instantiate_talent(state, legacy_cls, player.player_id)

    assert state.m9_system.get_sp(player.player_id) == 1
    assert isinstance(talent, expected_cls)


def test_real_cli_setup_uses_m9_talent_factory() -> None:
    from main import setup_game_cli

    state = setup_game_cli(SimpleNamespace(
        mode="all_ai",
        players=2,
        humans=0,
        ai=[],
        force_talent="G2",
    ))

    assert isinstance(state.get_player("p1").talent, Hologram9)
    assert state.m9_system.get_sp("p1") == 1
    assert state.m9_system.get_sp("p2") == 1


@pytest.mark.parametrize("profile", ("legacy", "v2exp"))
def test_older_profiles_keep_their_original_runtime(profile: str) -> None:
    experiments.reset()
    experiments.set_profile(profile)
    state = GameState()
    player = _player("p1")
    state.add_player(player)

    talent = instantiate_talent(state, Hologram, player.player_id)

    assert not hasattr(state, "m9_system")
    assert type(talent) is Hologram


def test_r1_to_r3_gives_every_player_one_standard_grant(monkeypatch) -> None:
    rolls = iter((6, 6, 4, 4, 2, 2))
    monkeypatch.setattr("engine.round_manager.roll_d6", lambda: next(rolls))
    state = GameState()
    for index in range(3):
        state.add_player(_player(f"p{index + 1}"))
    state.current_round = 1
    manager = RoundManager(state)

    manager._phase_r1()
    manager._phase_r3()

    assert state.round_winners == ["p1", "p2", "p3"]
    assert [grant.actor_id for grant in state.m9_round_grants] == [
        "p1", "p2", "p3"]
    assert all(state.get_player(pid).total_action_turns == 1
               for pid in state.player_order)
    assert all(state.m9_system.outcome(grant.grant_id).slot_resolved
               for grant in state.m9_round_grants)
    assert all(state.m9_system.outcome(grant.grant_id).voluntary_forfeit
               for grant in state.m9_round_grants)
    assert all(not state.m9_system.outcome(
        grant.grant_id).root_action_performed
        for grant in state.m9_round_grants)


def test_m9_runtime_flag_alone_still_selects_standard_grant_scheduler(
        monkeypatch) -> None:
    experiments.reset()
    experiments.enable("m9_rfc")
    monkeypatch.setattr("engine.round_manager.roll_d6", lambda: 3)
    state = GameState()
    state.add_player(_player("p1"))
    state.add_player(_player("p2"))
    state.current_round = 1
    manager = RoundManager(state)

    manager._phase_r1()
    manager._phase_r3()

    assert len(state.m9_round_grants) == 2
    assert all(state.m9_system.outcome(grant.grant_id).slot_resolved
               for grant in state.m9_round_grants)


def test_shadow_joins_next_round_as_independent_actor(monkeypatch) -> None:
    monkeypatch.setattr("engine.round_manager.roll_d6", lambda: 3)
    state = GameState()
    owner = _player("p1")
    other = _player("p2")
    state.add_player(owner)
    state.add_player(other)
    owner.talent = Hologram9(owner.player_id, state)
    state.current_round = 1
    shadow = owner.talent._create_shadow(owner)
    manager = RoundManager(state)

    manager._phase_r1()
    assert shadow.player_id not in state.round_winners

    state.current_round = 2
    manager._phase_r1()
    manager._phase_r3()

    assert set(state.round_winners) == {"p1", "p2", shadow.player_id}
    assert shadow.total_action_turns == 1
    assert state.get_actor(shadow.player_id) is shadow


def test_talent_performance_is_recorded_on_the_real_grant(monkeypatch) -> None:
    monkeypatch.setattr("engine.round_manager.roll_d6", lambda: 3)
    state = GameState()
    performer = _player("p1")
    other = _player("p2")
    state.add_player(performer)
    state.add_player(other)
    performer.talent = Hologram9(performer.player_id, state)
    state.current_round = 1
    manager = RoundManager(state)

    manager._phase_r1()
    manager._phase_r3()

    grant = next(g for g in state.m9_round_grants if g.actor_id == "p1")
    outcome = state.m9_system.outcome(grant.grant_id)
    assert outcome.slot_resolved
    assert outcome.root_action_performed
    assert outcome.performance_performed
    assert outcome.resolution_kind == "action_performed"


def test_attention_is_per_player_and_public_slot_starts_next_round() -> None:
    state = GameState()
    attacker = _player("p1")
    focus = _player("p2", _RegistrationController())
    state.add_player(attacker)
    state.add_player(focus)
    focus.talent = Hologram9(focus.player_id, state)
    state.current_round = 1
    manager = RoundManager(state)
    state.m9_system.begin_round(1)
    state.m9_system.allocate_public_slot(1, manager._m9_public_eligible)

    state.log_event("attack", attacker="p1", target="p2")
    state.log_event("attack", attacker="p1", target="p2")
    ready = state.m9_system.drain_ready_to_register()
    manager._m9_offer_performance_registration(ready)

    assert ready == ["p2", "p1"]
    assert state.m9_system.get_sp("p1") == 2
    assert state.m9_system.get_sp("p2") == 2
    assert state.m9_system.queue.is_in_queue("p2")
    assert state.m9_system.assign_public_slot(1) is None

    state.current_round = 2
    manager._phase_r0()
    assert state.m9_system.assign_public_slot(2) == "p2"


def test_full_extra_grant_runs_immediately_after_parent(monkeypatch) -> None:
    rolls = iter((6, 6, 1, 1))
    monkeypatch.setattr("engine.round_manager.roll_d6", lambda: next(rolls))
    state = GameState()
    source = _player("p1")
    target = _player("p2")
    state.add_player(source)
    state.add_player(target)
    source.talent = _GrantingTalent(state, target.player_id)
    state.current_round = 1
    manager = RoundManager(state)

    manager._phase_r1()
    manager._phase_r3()

    assert source.total_action_turns == 1
    assert target.total_action_turns == 2
    full_extra = next(
        grant for grant in state.m9_system.ledger._grants.values()
        if grant.kind == "full_extra"
    )
    assert full_extra.grant_id
    assert full_extra.parent_grant_id == state.m9_round_grants[0].grant_id
    assert state.m9_system.outcome(full_extra.grant_id).slot_resolved


def test_earthfire_full_extra_find_triggers_free_hunt_in_grant(monkeypatch) -> None:
    rolls = iter((6, 6, 4, 4, 2, 2))
    monkeypatch.setattr("engine.round_manager.roll_d6", lambda: next(rolls))
    state = GameState()
    source = _player("p1")
    hunter = _player("p2", _OneCommandController("find p3"))
    target = _player("p3")
    state.add_player(source)
    state.add_player(hunter)
    state.add_player(target)
    source.talent = _GrantingTalent(state, hunter.player_id)
    hunter.talent = ScissorRush9(hunter.player_id, state)
    hunter.weapons.append(Weapon(
        "远程魔法弹幕", Attribute.MAGIC, 1, WeaponRange.RANGED))
    state.current_round = 1

    RoundManager(state)._phase_r1()
    RoundManager(state)._phase_r3()

    assert state.markers.has_relation("p2", "ENGAGED_WITH", "p3")
    assert state.markers.has_relation("p3", "LOCKED_BY", "p2")
    assert not hunter.talent._hunt_used


def test_direct_damage_skips_armor_but_uses_normal_death_pipeline() -> None:
    state = GameState()
    attacker = _player("p1")
    target = _player("p2")
    state.add_player(attacker)
    state.add_player(target)
    target.hp = 4
    shield = ArmorPiece(
        "盾牌",
        Attribute.ORDINARY,
        ArmorLayer.OUTER,
        1.0,
        defense_map={"普通": 5},
        durability=8,
    )
    target.armor.outer.append(shield)

    result = resolve_damage(
        attacker,
        target,
        weapon=None,
        game_state=state,
        raw_damage_override=4,
        damage_attribute_override="普通",
        source_kind="g4_counter",
    )

    assert result["final_damage"] == 4
    assert result["killed"]
    assert not result["absolute_dead"]
    assert shield.durability == 8


def test_real_attack_death_clears_runtime_identity() -> None:
    state = GameState()
    attacker = _player("p1")
    target = _player("p2")
    state.add_player(attacker)
    state.add_player(target)
    target.hp = 1
    state.m9_system.set_sp(target.player_id, 2)
    state.m9_system.register_performance(target.player_id, 1)
    manager = RoundManager(state)

    _, action_type, success = manager.turn_manager._execute_attack(
        {"target": target.player_id, "weapon": "拳击"}, attacker)

    assert success
    assert action_type == "attack"
    assert not target.is_alive()
    assert attacker.kill_count == 1
    assert state.m9_system.get_sp(target.player_id) == 0
    assert not state.m9_system.queue.is_in_queue(target.player_id)


def test_shared_secondary_death_runs_cleanup_and_kill_attribution() -> None:
    state = GameState()
    owner = _player("p1")
    focus = _player("p2")
    secondary = _player("p3")
    attacker = _player("p4")
    attacker.location = "医院"
    secondary.hp = 1
    for player in (owner, focus, secondary, attacker):
        state.add_player(player)
    owner.talent = Hologram9(owner.player_id, state)
    shadow = owner.talent._create_shadow(owner)
    owner.talent._commit_terminal(owner, shadow)

    resolve_damage(
        attacker,
        focus,
        weapon=None,
        game_state=state,
        raw_damage_override=20,  # R19 ratio=0.2：4 成员共享 4，p3 必死
        damage_attribute_override="普通",
    )

    assert secondary.hp == 0
    assert state.m9_system.get_sp(secondary.player_id) == 0
    assert attacker.kill_count == 1
    assert any(event["type"] == "death" and event["player"] == "p3"
               for event in state.event_log)
