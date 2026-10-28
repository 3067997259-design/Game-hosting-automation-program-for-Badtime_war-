"""M9 RL 天赋首课：T1 单步归因、对手隔离与基础回放守卫。"""

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


def _env(*, scenario="talent_t1_improvise", rl_talent=1):
    from rl.env import BadtimeWarEnv

    return BadtimeWarEnv(
        num_opponents=1, max_rounds=6,
        enable_talents=True, enable_opponent_talents=False,
        rl_talent=rl_talent, opponent_bots=["dummy"],
        action_contract="talent_course", course_scenario=scenario,
    )


def test_t1_drill_starts_at_attributable_t0_choice() -> None:
    from rl.action_space import IDX_CHOOSE_BASE

    env = _env()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            _obs, _info = env.reset(seed=41)
        assert env._choose_mode is True
        assert env._choose_context["situation"] == "talent_t0"
        assert env._rl_player.talent.__class__.__name__ == "OneSlash9"
        assert env._state.get_player("ai_0").talent is None
        assert env._state.get_player("ai_0").hp == 1
        assert env._state.markers.has_relation(
            "rl_0", "ENGAGED_WITH", "ai_0",
        )
        assert set(np.flatnonzero(env.action_masks())) == {
            IDX_CHOOSE_BASE, IDX_CHOOSE_BASE + 1,
        }
    finally:
        with contextlib.redirect_stdout(io.StringIO()):
            env.close()


def test_t1_activation_is_the_only_terminal_damage_path() -> None:
    from rl.action_space import IDX_CHOOSE_BASE

    env = _env()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            env.reset(seed=42)
            _obs, reward, terminated, truncated, info = env.step(IDX_CHOOSE_BASE)
        slash = next(
            event for event in env._state.event_log
            if event.get("type") == "oneslash_attack"
            and event.get("player") == "rl_0"
        )
        assert slash["damage"] >= 1
        assert slash["killed"] is True
        assert terminated is True
        assert truncated is False
        assert reward > 0
        assert "oneslash_attack" in info["reward_ledger"]["event_types"]
    finally:
        with contextlib.redirect_stdout(io.StringIO()):
            env.close()


def test_declining_t1_cannot_bypass_drill_with_normal_attack() -> None:
    from rl.action_space import IDX_CHOOSE_BASE, idx_to_command

    env = _env()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            env.reset(seed=43)
            env.step(IDX_CHOOSE_BASE + 1)
        commands = {
            idx_to_command(int(idx), env._rl_player, env._state)
            for idx in np.flatnonzero(env.action_masks())
        }
        assert commands == {"forfeit"}
        assert not any(
            event.get("type") == "oneslash_attack"
            for event in env._state.event_log
        )
        assert env._state.get_player("ai_0").hp == 1
    finally:
        with contextlib.redirect_stdout(io.StringIO()):
            env.close()


def test_none_worker_keeps_base_replay_without_talent() -> None:
    from rl.action_space import idx_to_command

    env = _env(scenario=None, rl_talent=0)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            env.reset(seed=44)
        assert env._rl_player.talent is None
        assert env._state.get_player("ai_0").talent is None
        commands = {
            idx_to_command(int(idx), env._rl_player, env._state)
            for idx in np.flatnonzero(env.action_masks())
        }
        assert any(
            cmd == "forfeit" or cmd.startswith(("move ", "interact ", "lock ", "find ", "attack "))
            for cmd in commands
        )
        assert not any(cmd.startswith("special ") for cmd in commands)
    finally:
        with contextlib.redirect_stdout(io.StringIO()):
            env.close()


def test_talent_scenario_selects_t1_only_for_specialist_worker() -> None:
    from tools.m9_rl_train import _rl_talent_for_scenario

    assert _rl_talent_for_scenario(0, "talent_t1_improvise") == 1
    assert _rl_talent_for_scenario(0, None) == 0
    assert _rl_talent_for_scenario(None, None) is None


def test_t3_public_drill_requires_registration_then_activation() -> None:
    from rl.action_space import IDX_CHOOSE_BASE

    env = _env(scenario="talent_t3_public", rl_talent=3)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            env.reset(seed=45)
        assert env._rl_player.talent.__class__.__name__ == "Star9"
        assert env._choose_context["phase"] == "M9_PUBLIC_REGISTRATION"
        assert env._choose_options == ["报名公演", "保留"]

        with contextlib.redirect_stdout(io.StringIO()):
            _obs, _reward, terminated, truncated, _info = env.step(
                IDX_CHOOSE_BASE,
            )
        assert not terminated and not truncated
        assert env._choose_context["situation"] == "talent_t0"
        assert env._choose_options[0] == "发动天赋"

        with contextlib.redirect_stdout(io.StringIO()):
            _obs, reward, terminated, truncated, info = env.step(IDX_CHOOSE_BASE)
        star = next(
            event for event in env._state.event_log
            if event.get("type") == "star_attack"
            and event.get("player") == "rl_0"
        )
        assert star["hits"] >= 1
        assert star["kills"] >= 1
        assert terminated is True
        assert truncated is False
        assert reward > 0
        assert "star_attack" in info["reward_ledger"]["event_types"]
    finally:
        with contextlib.redirect_stdout(io.StringIO()):
            env.close()


def test_t3_keep_cannot_reach_starfall_in_the_same_round() -> None:
    from rl.action_space import IDX_CHOOSE_BASE, idx_to_command

    env = _env(scenario="talent_t3_public", rl_talent=3)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            env.reset(seed=46)
            env.step(IDX_CHOOSE_BASE + 1)  # 保留 SP，不报名
        commands = {
            idx_to_command(int(idx), env._rl_player, env._state)
            for idx in np.flatnonzero(env.action_masks())
        }
        assert commands == {"forfeit"}
        assert not any(
            event.get("type") == "star_attack"
            for event in env._state.event_log
        )
    finally:
        with contextlib.redirect_stdout(io.StringIO()):
            env.close()


def test_talent_scenario_selects_t3_for_public_worker() -> None:
    from tools.m9_rl_train import _rl_talent_for_scenario

    assert _rl_talent_for_scenario(0, "talent_t3_public") == 3


def test_public_registration_options_have_functional_semantics() -> None:
    from rl.obs_builder_m9 import encode_choose_options

    encoded = encode_choose_options(["保留", "报名公演"])
    # 7=accept，8=decline：训练应学习功能，不应靠“第二个按钮”猜报名。
    assert encoded[0, 7] == 0.0
    assert encoded[0, 8] == 1.0
    assert encoded[1, 7] == 1.0
    assert encoded[1, 8] == 0.0
