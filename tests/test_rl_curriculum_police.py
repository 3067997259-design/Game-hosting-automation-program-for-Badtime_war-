"""M9 RL 警察专项：场景隔离、动作合同与结果归因守卫。"""

from __future__ import annotations

import contextlib
import io

import numpy as np
import pytest

from engine import experiments


@pytest.fixture(autouse=True)
def _m9_profile():
    experiments.set_profile("m9-rfc")
    yield
    experiments.reset()


def _env(scenario: str):
    from rl.env import BadtimeWarEnv

    return BadtimeWarEnv(
        num_opponents=1, max_rounds=8, enable_talents=False, rl_talent=0,
        opponent_bots=["dummy"], action_contract="police_course",
        course_scenario=scenario,
    )


def test_command_drill_exposes_only_outcome_bearing_police_actions() -> None:
    from rl.action_space import action_family_from_idx, idx_to_command

    env = _env("police_command")
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            env.reset(seed=31)
        station = env._state.m9_police
        assert station.captain_id == "rl_0"
        assert station.has_open_wanted()
        assert station.automatic_enforcement_enabled is False
        legal = np.flatnonzero(env.action_masks())
        commands = {
            idx_to_command(int(idx), env._rl_player, env._state)
            for idx in legal
        }
        assert any(
            cmd.startswith("special 指挥unit") and cmd.endswith("攻击")
            for cmd in commands
        )
        assert not any(cmd.startswith("attack ") for cmd in commands)
        assert "interact 小刀" not in commands
        assert not any(cmd.startswith("special PP") for cmd in commands)
        assert not any(cmd.startswith("special 交易") for cmd in commands)
        assert not ({"report", "designate"} & {
            action_family_from_idx(int(idx)) for idx in legal
        })
    finally:
        with contextlib.redirect_stdout(io.StringIO()):
            env.close()


def test_command_attack_logs_creditable_damage_but_forfeit_gets_no_auto_hit() -> None:
    from rl.action_space import idx_to_command

    env = _env("police_command")
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            env.reset(seed=32)
            env.step(0)  # 放弃一轮；课程靶场不得由 R4 自动替策略攻击
        assert not any(
            e.get("type") == "m9_police_enforcement"
            for e in env._state.event_log
        )

        attack_idx = next(
            int(idx) for idx in np.flatnonzero(env.action_masks())
            if idx_to_command(int(idx), env._rl_player, env._state).startswith(
                "special 指挥unit"
            ) and idx_to_command(int(idx), env._rl_player, env._state).endswith(
                "攻击"
            )
        )
        before = env._state.get_player("ai_0").hp
        with contextlib.redirect_stdout(io.StringIO()):
            _obs, reward, _term, _trunc, info = env.step(attack_idx)
        event = next(
            e for e in reversed(env._state.event_log)
            if e.get("type") == "m9_police_command_attack"
        )
        assert event["captain"] == "rl_0"
        assert event["damage"] > 0
        assert env._state.get_player("ai_0").hp < before
        assert "m9_police_command_attack" in info["reward_ledger"]["event_types"]
        assert reward > 0
    finally:
        with contextlib.redirect_stdout(io.StringIO()):
            env.close()


def test_election_drill_starts_without_command_authority() -> None:
    from rl.action_space import idx_to_command

    env = _env("police_election")
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            env.reset(seed=33)
        commands = {
            idx_to_command(int(idx), env._rl_player, env._state)
            for idx in np.flatnonzero(env.action_masks())
        }
        assert "special 竞选队长" in commands
        assert not any(cmd.startswith("special 指挥") for cmd in commands)
    finally:
        with contextlib.redirect_stdout(io.StringIO()):
            env.close()


def test_criminal_cannot_reenter_captain_candidate_loop() -> None:
    from actions.special_op import get_available_specials

    env = _env("police_election")
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            env.reset(seed=34)
        state = env._state
        station = state.m9_police
        state.police.add_crime("rl_0", "伤害玩家")

        names = {
            item["name"] for item in get_available_specials(
                env._rl_player, state,
            )
        }
        assert "竞选队长" not in names
        assert station.apply_captain("rl_0", state) is False
    finally:
        with contextlib.redirect_stdout(io.StringIO()):
            env.close()


def test_course_scenarios_support_weighted_worker_replay() -> None:
    from tools.m9_rl_train import _course_scenario_for_worker

    pool = ["police_command", "police_command", "police_election", "none"]
    assert [_course_scenario_for_worker(pool, i) for i in range(8)] == [
        "police_command", "police_command", "police_election", None,
    ] * 2


@pytest.mark.parametrize(
    ("scenario", "expected_kind"),
    [("police_command", "attack"), ("police_election", "election")],
)
def test_natural_police_router_never_exposes_unit_move(
    scenario: str, expected_kind: str,
) -> None:
    from rl.action_space import idx_to_command
    from tools.rung0_chain_probe import _police_skill_mask

    env = _env(scenario)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            env.reset(seed=35)
        focused, kind = _police_skill_mask(
            env.action_masks(), env._rl_player, env._state,
        )
        assert kind == expected_kind
        commands = {
            idx_to_command(int(idx), env._rl_player, env._state)
            for idx in np.flatnonzero(focused)
        }
        assert not any(command.endswith("移动") for command in commands)
        if expected_kind == "attack":
            assert commands
            assert all(command.endswith("攻击") for command in commands)
        else:
            assert commands == {"special 竞选队长"}
    finally:
        with contextlib.redirect_stdout(io.StringIO()):
            env.close()


def test_natural_police_router_defers_to_personal_attack() -> None:
    from models.equipment import make_weapon
    from rl.env import BadtimeWarEnv
    from tools.rung0_chain_probe import _police_skill_mask

    env = BadtimeWarEnv(
        num_opponents=1, max_rounds=8, enable_talents=False, rl_talent=0,
        opponent_bots=["dummy"], action_contract=None,
        course_scenario="police_command",
    )
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            env.reset(seed=36)
        target = env._state.get_player("ai_0")
        target.location = env._rl_player.location
        env._rl_player.weapons = [make_weapon("小刀")]
        env._state.markers.add_relation(
            "rl_0", "ENGAGED_WITH", target.player_id,
        )
        env._state.markers.add_relation(
            target.player_id, "ENGAGED_WITH", "rl_0",
        )
        original = env.action_masks()
        focused, kind = _police_skill_mask(
            original, env._rl_player, env._state,
        )
        assert kind is None
        np.testing.assert_array_equal(focused, original)
    finally:
        with contextlib.redirect_stdout(io.StringIO()):
            env.close()


def test_resume_learning_rate_override_updates_schedule_and_optimizer() -> None:
    from types import SimpleNamespace
    from tools.m9_rl_train import _override_learning_rate

    model = SimpleNamespace(
        learning_rate=3e-4,
        lr_schedule=lambda _progress: 3e-4,
        policy=SimpleNamespace(
            optimizer=SimpleNamespace(param_groups=[{"lr": 3e-4}]),
        ),
    )
    _override_learning_rate(model, 3e-5)
    assert model.learning_rate == 3e-5
    assert model.lr_schedule(0.5) == 3e-5
    assert model.policy.optimizer.param_groups[0]["lr"] == 3e-5
