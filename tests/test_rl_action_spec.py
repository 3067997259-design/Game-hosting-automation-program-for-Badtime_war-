"""ActionSpec、固定动作 id 与 BC 反向映射必须共享同一语义。"""

from __future__ import annotations

import contextlib
import io

from engine import experiments


def _env():
    from rl.env import BadtimeWarEnv

    with contextlib.redirect_stdout(io.StringIO()):
        env = BadtimeWarEnv(
            num_opponents=1, max_rounds=4, opponent_bots=["dummy"],
            enable_talents=False, rl_talent=0,
        )
        env.reset(seed=41)
    return env


def test_all_legal_command_indices_roundtrip_through_action_spec() -> None:
    from rl.action_space import (
        action_spec_from_idx, build_action_mask, command_to_action_idx,
    )

    experiments.set_profile("m9-rfc")
    env = _env()
    try:
        mask = build_action_mask(env._rl_player, env._state, "rl_0")
        for idx, legal in enumerate(mask):
            if not legal:
                continue
            spec = action_spec_from_idx(idx, env._rl_player, env._state)
            assert spec.version == "m9-action-spec-v1"
            assert spec.stable_key
            assert command_to_action_idx(spec.raw, env._rl_player, env._state) == idx
    finally:
        env.close()
        experiments.reset()


def test_m9_dynamic_interact_has_stable_semantic_key_and_bc_mapping() -> None:
    from rl.action_space import command_to_action_idx
    from rl.action_spec import command_action_spec
    from rl.bc_collector import BCCollectorController

    experiments.set_profile("m9-rfc")
    env = _env()
    try:
        command = "interact EPO"
        idx = command_to_action_idx(command, env._rl_player, env._state)
        assert idx >= 160
        spec = command_action_spec(command)
        assert spec.stable_key.endswith("|EPO")

        controller = BCCollectorController(collector=None)
        assert controller._command_to_action_idx(
            command, env._rl_player, env._state, "",
        ) == idx
    finally:
        env.close()
        experiments.reset()


def test_choose_spec_uses_situation_option_and_relative_target_slot() -> None:
    from rl.action_spec import choose_action_spec

    spec = choose_action_spec(
        "攻击 Alice", ["跳过", "攻击 Alice"], "g4_strike_pick_target",
        target_slots={"Alice": 2},
    )
    assert spec.kind == "choose"
    assert spec.option_index == 1
    assert spec.target_slot == 2
    assert "g4_strike_pick_target|1|slot:2" in spec.stable_key


def test_parameterized_m9_commands_keep_target_and_object_semantics() -> None:
    from rl.action_space import command_to_action_idx, idx_to_command

    experiments.set_profile("m9-rfc")
    env = _env()
    try:
        opponent = next(
            env._state.get_player(pid) for pid in env._state.player_order
            if pid != env._rl_player.player_id
        )
        commands = [
            f"report {opponent.name}",
            f"designate {opponent.name}",
            f"special 热线举报{opponent.name}",
            f"special 交易{opponent.name}",
            "special 拆卸力量",
            "special 指挥unit1移动",
            "special 指挥unit1攻击",
        ]
        indices = [
            command_to_action_idx(command, env._rl_player, env._state)
            for command in commands
        ]
        assert min(indices) >= 185
        assert len(set(indices)) == len(indices)
        assert [
            idx_to_command(idx, env._rl_player, env._state) for idx in indices
        ] == commands
    finally:
        env.close()
        experiments.reset()


def test_parameterized_special_mask_never_enables_ambiguous_family_slot() -> None:
    from unittest.mock import patch

    import numpy as np

    from rl.action_space import (
        IDX_SPECIAL_M9_BASE, M9_PLAYER_SPECIAL_FAMILIES,
        SPECIAL_OPS_M9, SPECIAL_OPS_M9_TEMPLATE, _fill_special_mask,
        command_to_action_idx, rl_action_count,
    )

    experiments.set_profile("m9-rfc")
    env = _env()
    try:
        opponent = next(
            env._state.get_player(pid) for pid in env._state.player_order
            if pid != env._rl_player.player_id
        )
        concrete = [
            f"热线举报{opponent.name}", f"交易{opponent.name}",
            "拆卸力量", "指挥unit1移动",
        ]
        mask = np.zeros(rl_action_count(env._state), dtype=bool)
        with patch(
            "actions.special_op.get_available_specials",
            return_value=[{"name": name} for name in concrete],
        ):
            _fill_special_mask(mask, env._rl_player, env._state)

        for name in concrete:
            idx = command_to_action_idx(
                f"special {name}", env._rl_player, env._state,
            )
            assert idx >= 185 and mask[idx]
        for family in M9_PLAYER_SPECIAL_FAMILIES + ("指挥", "拆卸"):
            old_family_idx = (
                IDX_SPECIAL_M9_BASE + len(SPECIAL_OPS_M9)
                + SPECIAL_OPS_M9_TEMPLATE.index(family)
            )
            assert not mask[old_family_idx]
    finally:
        env.close()
        experiments.reset()


def test_every_schema_index_has_known_action_family() -> None:
    from rl.action_space import ACTION_COUNT_M9_FIXED, action_family_from_idx

    families = [action_family_from_idx(idx) for idx in range(ACTION_COUNT_M9_FIXED)]
    assert "invalid" not in families
    assert {"move", "interact", "attack", "special", "choose"} <= set(families)


def test_m9_opponent_slots_are_independent_of_random_turn_order() -> None:
    from rl.action_space import get_opponent_slots
    from rl.env import BadtimeWarEnv

    experiments.set_profile("m9-rfc")
    with contextlib.redirect_stdout(io.StringIO()):
        env = BadtimeWarEnv(
            num_opponents=2, max_rounds=4,
            enable_talents=False, rl_talent=0,
        )
        env.reset(seed=42)
    try:
        before = [
            opp.player_id for opp in get_opponent_slots(
                env._rl_player, env._state,
            ) if opp is not None
        ]
        env._state.player_order.reverse()
        after = [
            opp.player_id for opp in get_opponent_slots(
                env._rl_player, env._state,
            ) if opp is not None
        ]
        assert before == ["ai_0", "ai_1"]
        assert after == before
    finally:
        with contextlib.redirect_stdout(io.StringIO()):
            env.close()
        experiments.reset()


def test_m9_home_move_indices_are_independent_of_random_turn_order() -> None:
    from rl.action_space import _cmd_to_move_idx, _rl_move_destinations
    from rl.env import BadtimeWarEnv

    experiments.set_profile("m9-rfc")
    with contextlib.redirect_stdout(io.StringIO()):
        env = BadtimeWarEnv(
            num_opponents=2, max_rounds=4,
            enable_talents=False, rl_talent=0,
        )
        env.reset(seed=43)
    try:
        before = _rl_move_destinations(env._state)
        indices_before = {
            pid: _cmd_to_move_idx(f"move home_{pid}", env._state)
            for pid in ("ai_0", "ai_1", "rl_0")
        }
        env._state.player_order.reverse()
        after = _rl_move_destinations(env._state)
        indices_after = {
            pid: _cmd_to_move_idx(f"move home_{pid}", env._state)
            for pid in ("ai_0", "ai_1", "rl_0")
        }
        assert before[-3:] == ["home_ai_0", "home_ai_1", "home_rl_0"]
        assert after == before
        assert indices_after == indices_before
    finally:
        with contextlib.redirect_stdout(io.StringIO()):
            env.close()
        experiments.reset()


def test_combat_slot_symmetry_only_copies_equivalent_action_rows() -> None:
    import torch

    from rl.action_space import ACTION_COUNT_M9_FIXED, IDX_LOCK_BASE
    from tools.m9_symmetrize_checkpoint import (
        combat_slot_row_groups, symmetrize_combat_action_rows,
    )

    class _Policy:
        def __init__(self):
            self.action_net = torch.nn.Linear(3, ACTION_COUNT_M9_FIXED)
            self.optimizer = torch.optim.Adam(self.action_net.parameters())

    policy = _Policy()
    before_forfeit = policy.action_net.weight[0].detach().clone()
    with torch.no_grad():
        policy.action_net.weight[IDX_LOCK_BASE].fill_(7.0)
        policy.action_net.bias[IDX_LOCK_BASE].fill_(3.0)

    report = symmetrize_combat_action_rows(policy, merge_random_home=False)

    assert report["combat_groups"] == 13
    assert torch.equal(policy.action_net.weight[0], before_forfeit)
    for row in combat_slot_row_groups()[0]:
        assert torch.all(policy.action_net.weight[row] == 7.0)
        assert float(policy.action_net.bias[row]) == 3.0
