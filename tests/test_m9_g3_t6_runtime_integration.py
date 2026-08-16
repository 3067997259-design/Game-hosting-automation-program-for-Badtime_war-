"""Production-entry regressions for the M9 G3/T6/police runtime wiring."""

from __future__ import annotations

import pytest

from actions import attack, special_op
from controllers.base import PlayerController
from engine import experiments
from engine.action_enumerator import build_action_options
from engine.game_state import GameState
from engine.m9.talents.g3 import Mythland9
from engine.m9.talents.t6 import GoodCitizen9
from models.equipment import make_armor, make_weapon
from models.player import Player


class ChoiceController(PlayerController):
    def __init__(self, *choices: str) -> None:
        super().__init__()
        self.choices = list(choices)

    def choose(self, prompt, options, context=None):
        if self.choices:
            choice = self.choices.pop(0)
            if choice in options:
                return choice
        return options[0]

    def get_command(self, player, game_state, available_actions, context=None):
        return "forfeit"

    def choose_multi(self, prompt, options, max_count, min_count=0, context=None):
        return list(options)[:min_count]

    def confirm(self, prompt, context=None):
        return True


@pytest.fixture(autouse=True)
def _m9_profile():
    experiments.reset()
    experiments.set_profile("m9-rfc")
    yield
    experiments.reset()


def _player(pid: str, *, controller=None, location: str = "商店") -> Player:
    player = Player(pid, pid.upper(), controller=controller or ChoiceController())
    player.hp = player.max_hp = 20
    player.is_awake = True
    player.location = location
    return player


def _state(*players: Player) -> GameState:
    state = GameState()
    for player in players:
        state.add_player(player)
    return state


def _expand_barrier(state: GameState, owner: Player, other: Player) -> Mythland9:
    owner.controller = ChoiceController("展开固有结界", "兵装（螺旋剑）")
    talent = Mythland9(owner.player_id, state)
    owner.talent = talent
    state.current_round = 1
    state.m9_system.set_sp(owner.player_id, 2)
    state.m9_system.register_performance(owner.player_id, 1)
    state.m9_system.allocate_public_slot(1)
    message, ok = talent.execute_t0(owner)
    assert ok, message
    assert other.player_id in talent.captured
    return talent


def test_normal_attack_cannot_cross_g3_barrier_boundary() -> None:
    g3 = _player("g3", location="公园")
    trapped = _player("inside", location="公园")
    outside = _player("outside", location="医院")
    outside.weapons.append(make_weapon("小刀"))
    state = _state(g3, trapped, outside)
    _expand_barrier(state, g3, trapped)

    before = trapped.hp
    message, result = attack.execute(outside, trapped.player_id, "小刀", state)

    assert message.startswith("❌")
    assert result == {}
    assert trapped.hp == before


def test_normal_ranged_attack_flows_through_g3_wall() -> None:
    g3 = _player("g3", location="公园")
    attacker = _player("attacker", location="公园")
    attacker.weapons.append(make_weapon("远程魔法弹幕"))
    state = _state(g3, attacker)
    talent = Mythland9(g3.player_id, state)
    g3.talent = talent
    talent.rho_aias = {"durability": 8, "location": "公园"}

    before = g3.hp
    message, result = attack.execute(
        attacker, g3.player_id, "远程魔法弹幕", state)

    assert not message.startswith("❌")
    assert result["hp_damage"] == 0
    assert g3.hp == before
    assert talent.rho_aias["durability"] < 8


def test_trapped_actor_has_real_break_barrier_root() -> None:
    g3 = _player("g3", location="公园")
    trapped = _player("inside", location="公园")
    state = _state(g3, trapped)
    talent = _expand_barrier(state, g3, trapped)
    before = talent.barrier_anchor_durability

    names = [entry["name"] for entry in special_op.get_available_specials(
        trapped, state)]
    assert "破界" in names
    message, consumes = special_op.execute(trapped, "破界", state)

    assert consumes
    assert "锚点耐久" in message
    assert talent.barrier_anchor_durability == before - talent.break_action_power


def test_police_is_targetable_and_armor_changes_real_damage() -> None:
    attacker = _player("p1", location="警察局")
    attacker.weapons.append(make_weapon("高斯步枪"))
    state = _state(attacker)
    unit = state.m9_police.ensure_roster()[0]
    unit.location = "警察局"
    unit.armor = make_armor("陶瓷护甲")

    options = build_action_options(attacker, state, ["attack"])["attack"]
    assert any(unit.name in option for option in options)
    before = unit.hp
    message, result = attack.execute(
        attacker, unit.player_id, "高斯步枪", state)

    assert not message.startswith("❌")
    assert 0 < result["hp_damage"] < 6
    assert unit.hp == before - result["hp_damage"]


def test_equipped_police_weapon_drives_enforcement_and_captain_cannot_double_attack() -> None:
    captain = _player("p1", location="商店")
    target = _player("p2", location="商店")
    state = _state(captain, target)
    station = state.m9_police
    station.set_state_ref(state)
    unit = station.ensure_roster()[0]
    unit.location = "商店"
    unit.weapon_name = "高斯步枪"
    station.captain_id = captain.player_id

    before = target.hp
    first = station.captain_command(
        captain.player_id, unit.unit_id, "attack", target.player_id)
    second = station.captain_command(
        captain.player_id, unit.unit_id, "attack", target.player_id)

    assert "立即攻击" in first
    assert target.hp == before - 6
    assert "本轮已经攻击" in second


def test_terror_captain_cannot_move_or_attack_police() -> None:
    captain = _player("p1")
    target = _player("p2")
    state = _state(captain, target)
    # The command contract is identity-based; a lightweight Terror marker is enough.
    captain.talent = type("Terror", (), {"is_terror": True})()
    station = state.m9_police
    station.set_state_ref(state)
    unit = station.ensure_roster()[0]
    unit.location = "商店"
    station.captain_id = captain.player_id

    assert "Terror" in station.captain_command(
        captain.player_id, unit.unit_id, "move", "医院")
    assert "Terror" in station.captain_command(
        captain.player_id, unit.unit_id, "attack", target.player_id)


def test_shutdown_keeps_surviving_police_in_place() -> None:
    state = _state(_player("p1"))
    unit = state.m9_police.ensure_roster()[0]
    unit.location = "医院"

    state.m9_police.shut_down("destroyed")

    assert unit.is_alive()
    assert unit.location == "医院"


def test_hotline_requires_event_bound_witness_and_failed_preflight_is_free() -> None:
    reporter = _player("p1", location="商店")
    suspect = _player("p2", location="医院")
    bystander = _player("p3", location="商店")
    state = _state(reporter, suspect, bystander)
    reporter.talent = GoodCitizen9(reporter.player_id, state)
    state.m9_police.ensure_roster()

    message, consumes = special_op.execute(
        reporter, f"热线举报{suspect.name}", state)
    assert message.startswith("❌")
    assert consumes is False

    state.log_event(
        "attack", attacker=suspect.player_id, target=bystander.player_id,
        witnesses=[reporter.player_id], location="商店")
    message, consumes = special_op.execute(
        reporter, f"热线举报{suspect.name}", state)
    assert "通缉" in message
    assert consumes is True


def test_t6_improvise_transfers_real_equipment_and_public_uses_same_core() -> None:
    reporter = _player(
        "p1", location="警察局",
        controller=ChoiceController("即演", "unit1", "武器", "警棍"),
    )
    state = _state(reporter)
    reporter.talent = GoodCitizen9(reporter.player_id, state)
    reporter.weapons.append(make_weapon("警棍"))
    unit = state.m9_police.ensure_roster()[0]
    unit.location = "警察局"
    state.m9_system.set_sp(reporter.player_id, 1)

    message, ok = reporter.talent.execute_t0(reporter)

    assert ok, message
    assert state.m9_system.get_sp(reporter.player_id) == 0
    assert unit.weapon_name == "警棍"
    assert all(weapon.name != "警棍" for weapon in reporter.weapons)


def test_g3_chain_checks_terminal_before_finish_root() -> None:
    g3 = _player(
        "g3", location="公园",
        controller=ChoiceController("停止", "是"),
    )
    target = _player("p2", location="公园")
    state = _state(g3, target)
    talent = Mythland9(g3.player_id, state)
    g3.talent = talent
    talent.barrier_active = True
    talent.barrier_location = "公园"
    talent.captured = [target.player_id]
    talent.main_target = target.player_id
    talent.ideal_burn = True
    talent.magic = talent.spiral_cost
    talent.temp_magic = 0

    message, ok = talent._spiral_chain(g3)

    assert ok
    assert "终段幻想崩坏" not in message
    assert talent.barrier_active


def test_g3_outside_projection_is_not_sp_gated() -> None:
    g3 = _player("g3", location="公园", controller=ChoiceController("双刀·守势"))
    state = _state(g3)
    talent = Mythland9(g3.player_id, state)
    g3.talent = talent
    state.m9_system.set_sp(g3.player_id, 0)

    option = talent.get_t0_option(g3)
    message, ok = talent.execute_t0(g3)

    assert option["m9_kind"] == "g3_projection"
    assert ok, message
    assert talent.defense_marker
    assert state.m9_system.get_sp(g3.player_id) == 0
