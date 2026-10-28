"""① 防失明回归：引擎 choose situation 必须在 RL 决策/启发式/显式登记之一。

背景：RLController.choose 默认接管所有非 _HEURISTIC_CHOOSE 的 choose（选项掩码通用分支
恒给 16 项），因此"choose 失明"的结构风险在于——引擎新增战略性 situation 而我们从未
分类它。本测试从引擎源码提取全部具名 situation，强制其 ∈ STRATEGIC|HEURISTIC|KNOWN。

另：RLController.choose 对非启发式 situation 且 options>16 会打 warning（截断守卫），
其合理性由 tests/ 运行时行为覆盖，此处只守"分类漂移"。
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from rl.action_space import (
    HEURISTIC_CHOOSE_SITUATIONS,
    KNOWN_CHOOSE_UNMAPPED,
    STRATEGIC_CHOOSE_SITUATIONS,
    STRATEGIC_SITUATIONS,
)
from rl.obs_builder import _CHOOSE_SITUATION_MAP

ROOT = Path(__file__).resolve().parents[1]

ENGINE_NAMED_CHOOSE: set[str] = set()
for f in (ROOT / "engine").rglob("*.py"):
    ENGINE_NAMED_CHOOSE |= set(
        re.findall(r'"situation"\s*:\s*"([^"]+)"', f.read_text(encoding="utf-8"))
    )

CLASSIFIED = (STRATEGIC_CHOOSE_SITUATIONS | HEURISTIC_CHOOSE_SITUATIONS
              | KNOWN_CHOOSE_UNMAPPED)

# STRATEGIC 中引擎未以 context.situation 标记、由未打标 choose 触发（走默认 RL 路径）——
# 登记为意图清单，防"改引擎后 STRATEGIC 与实况失配"误报。
STRATEGIC_UNTAGGED_ALLOWED: set[str] = {
    "captain_election", "cutaway_borrow_target", "g2_emotion_choice",
    "g2_melody_propagate", "g2_melody_target", "g2_sing_rhythm", "g2_sing_song",
    "g2_sing_target", "hexagram_pick_target", "hoshino_form_choice",
    "hoshino_self_doubt_choice", "mythland_pick_target", "ripple_anchor_target",
    "ripple_anchor_type", "ripple_poem_target", "savior_activate", "talent_pick",
}


def test_every_engine_choose_situation_is_classified() -> None:
    missing = ENGINE_NAMED_CHOOSE - CLASSIFIED
    assert not missing, (
        "引擎新增 choose situation 未登记（要么进 STRATEGIC/HEURISTIC，"
        f"要么进 KNOWN_CHOOSE_UNMAPPED 说明为何 RL 不参与）：{sorted(missing)}"
    )


def test_strategic_entries_exist_or_untagged() -> None:
    absent = (STRATEGIC_CHOOSE_SITUATIONS - ENGINE_NAMED_CHOOSE
              - STRATEGIC_UNTAGGED_ALLOWED)
    assert not absent, (
        f"STRATEGIC 声明了引擎既不产生也未登记为未打标触发的 situation：{sorted(absent)}"
    )


def test_known_unmapped_disjoint_with_strategic() -> None:
    overlap = KNOWN_CHOOSE_UNMAPPED & (STRATEGIC_CHOOSE_SITUATIONS | HEURISTIC_CHOOSE_SITUATIONS)
    assert not overlap, f"KNOWN 与 STRATEGIC/HEURISTIC 重叠（归类冲突）：{sorted(overlap)}"


def test_every_strategic_choose_has_distinct_observation_id() -> None:
    missing = STRATEGIC_CHOOSE_SITUATIONS - set(_CHOOSE_SITUATION_MAP)
    assert not missing, f"战略 choose 被 RL 接管但 observation 没有 situation ID：{sorted(missing)}"
    ids = [_CHOOSE_SITUATION_MAP[name] for name in STRATEGIC_CHOOSE_SITUATIONS]
    assert len(ids) == len(set(ids)), "战略 choose situation ID 必须一一对应，禁止观测混叠"


def test_choose_registry_has_single_authoritative_set() -> None:
    assert STRATEGIC_SITUATIONS is STRATEGIC_CHOOSE_SITUATIONS


def test_m9_talent_identity_uses_stable_slot_id() -> None:
    from rl.obs_builder import M9_TALENT_SLOT_TO_IDX, _build_talent_id

    assert len(M9_TALENT_SLOT_TO_IDX) == 14
    for slot_id, expected_idx in M9_TALENT_SLOT_TO_IDX.items():
        player = SimpleNamespace(
            talent=SimpleNamespace(),
            talent_slot_id=slot_id,
        )
        encoded = _build_talent_id(player)
        assert np.flatnonzero(encoded).tolist() == [expected_idx]
        assert float(encoded.sum()) == 1.0


def test_choose_options_expose_target_and_function_not_just_ordinal() -> None:
    from rl.obs_builder_m9 import encode_choose_options

    me = SimpleNamespace(player_id="p0", name="Me")
    alice = SimpleNamespace(player_id="p1", name="Alice")
    state = SimpleNamespace(
        player_order=["p0", "p1"],
        get_player=lambda pid: {"p0": me, "p1": alice}.get(pid),
    )
    options = [
        "跳过", "攻击 Alice", "防御", "前往医院",
        "高斯步枪", "即演模式", "消耗 10 PP",
    ]
    encoded = encode_choose_options(options, me, state)

    assert encoded.shape == (16, 16)
    assert encoded[0, 8] == 1.0                 # decline/skip
    assert encoded[1, 2] == 1.0                 # opponent slot 0
    assert encoded[1, 9] == 1.0                 # offense
    assert encoded[2, 10] == 1.0                # defense/support
    assert encoded[3, 11] == 1.0                # movement/location
    assert encoded[4, 12] == 1.0                # weapon/item
    assert encoded[5, 13] == 1.0                # mode/form
    assert encoded[6, 14] == 0.5                # numeric value / 20
    assert not encoded[7:].any()


def test_choose_option_order_changes_candidate_blocks_but_not_header() -> None:
    from rl.obs_builder_m9 import (
        M9_CHOOSE_HEADER_DIM, M9_CHOOSE_OBS_DIM, M9_TALENT_BLOCK_DIM,
        OBS_DIM_M9_TOTAL, fill_choose_obs_m9,
    )

    first = np.zeros(OBS_DIM_M9_TOTAL, dtype=np.float32)
    second = np.zeros_like(first)
    kwargs = dict(
        choose_mode=True, situation="g4_strike_pick_weapon", n_options=2,
    )
    fill_choose_obs_m9(first, options=["高斯步枪", "跳过"], **kwargs)
    fill_choose_obs_m9(second, options=["跳过", "高斯步枪"], **kwargs)

    # choose 契约区固定在 v5 核心 [569..828)，不随尾部追加（P2 天赋块）移动
    base = OBS_DIM_M9_TOTAL - M9_TALENT_BLOCK_DIM - M9_CHOOSE_OBS_DIM
    assert base == 569
    assert np.array_equal(
        first[base:base + M9_CHOOSE_HEADER_DIM],
        second[base:base + M9_CHOOSE_HEADER_DIM],
    )
    assert not np.array_equal(
        first[base + M9_CHOOSE_HEADER_DIM:base + M9_CHOOSE_OBS_DIM],
        second[base + M9_CHOOSE_HEADER_DIM:base + M9_CHOOSE_OBS_DIM],
    )


def test_m9_resource_and_visibility_observation_is_checkpoint_width_compatible() -> None:
    """复用旧槽位表达现役信用点和属性来源，不扩大 828 维 checkpoint。"""
    from engine import experiments
    from engine.game_state import GameState
    from models.player import Player
    from rl.obs_builder import OBS_DIM
    from rl.obs_builder_m9 import OBS_DIM_M9_TOTAL, build_obs_profile

    previous_profile = experiments.current_profile()
    experiments.set_profile("m9-rfc")
    try:
        state = GameState()
        player = Player("rl_0", "RL")
        opponent = Player("ai_0", "AI")
        state.add_player(player)
        state.add_player(opponent)
        player.credits = 7
        player.vouchers = 3  # 若错误读取退役凭证，idx=2 会变成 1.0
        opponent.credits = 11
        opponent.vouchers = 0
        player.has_detection = True
        player.detection_attrs = {"魔法"}
        opponent.is_invisible = True
        opponent.stealth_attrs = {"科技"}

        obs = build_obs_profile(player, state, player.player_id)
        assert obs.shape == (OBS_DIM_M9_TOTAL,)
        assert np.isclose(float(obs[2]), 7 / 20)
        assert float(obs[19]) == 0.875  # magic code=2
        assert float(obs[44 + 31]) == 0.625  # tech code=4
        assert np.isclose(float(obs[44 + 32]), 11 / 20)
        # 维度钉：P2 起天赋块追加在 v5 核心 828 之后（核心宽度冻结见
        # OBS_DIM_M9_V5_CORE，旧 checkpoint 经 LegacyObsDimWrapper 服役）
        assert OBS_DIM_M9_TOTAL == 918 and OBS_DIM == 539
    finally:
        experiments.set_profile(previous_profile)


def test_m9_bow_context_reuses_non_choose_tail_without_changing_checkpoint_width() -> None:
    from combat.numeric_v2 import compute_defense
    from engine import experiments
    from engine.game_state import GameState
    from models.equipment import make_armor
    from models.player import Player
    from rl.action_space import M9_BOW_MODULE_NAMES
    from rl.obs_builder_m9 import (
        M9_BOW_CONTEXT_BASE,
        M9_BOW_CONTEXT_DIM,
        OBS_DIM_M9_TOTAL,
        build_obs_profile,
    )

    previous_profile = experiments.current_profile()
    experiments.set_profile("m9-rfc")
    try:
        state = GameState()
        player = Player("rl_0", "RL")
        opponent = Player("ai_0", "AI")
        state.add_player(player)
        state.add_player(opponent)
        player.bow_modules = ["火矢", "火矢"]
        player.arrows = 3
        state.module_supply["火矢"] = 0
        opponent.burn_stacks = 2
        opponent.moved_this_round = True
        ceramic = make_armor("陶瓷护甲")
        assert ceramic is not None
        added, reason = opponent.add_armor(ceramic)
        assert added, reason

        plain = build_obs_profile(player, state, player.player_id)
        encoded = build_obs_profile(
            player, state, player.player_id, bow_context=True,
        )
        assert plain.shape == encoded.shape == (OBS_DIM_M9_TOTAL,)
        assert not plain[M9_BOW_CONTEXT_BASE:
                         M9_BOW_CONTEXT_BASE + M9_BOW_CONTEXT_DIM].any()

        fire_idx = M9_BOW_MODULE_NAMES.index("火矢")
        assert encoded[M9_BOW_CONTEXT_BASE + fire_idx] == 1.0
        assert encoded[M9_BOW_CONTEXT_BASE + 6 + fire_idx] == 0.0
        assert encoded[M9_BOW_CONTEXT_BASE + 12] == 0.5
        assert encoded[M9_BOW_CONTEXT_BASE + 13] == 0.0
        player.arrows = 0
        empty_quiver = build_obs_profile(
            player, state, player.player_id, bow_context=True,
        )
        assert empty_quiver[M9_BOW_CONTEXT_BASE + 12] == 0.0
        assert empty_quiver[M9_BOW_CONTEXT_BASE + 13] == 1.0
        target_base = M9_BOW_CONTEXT_BASE + 14
        expected_defense, _ = compute_defense(opponent, "普通")
        assert np.isclose(encoded[target_base], expected_defense / 12.0)
        assert encoded[target_base + 4] == 2 / 3
        assert encoded[target_base + 5] == 1.0
    finally:
        experiments.set_profile(previous_profile)


def test_m9_choose_contract_overrides_bow_context_overlay() -> None:
    from engine import experiments
    from engine.game_state import GameState
    from models.player import Player
    from rl.obs_builder_m9 import M9_BOW_CONTEXT_BASE, build_obs_profile

    previous_profile = experiments.current_profile()
    experiments.set_profile("m9-rfc")
    try:
        state = GameState()
        player = Player("rl_0", "RL")
        opponent = Player("ai_0", "AI")
        state.add_player(player)
        state.add_player(opponent)
        player.bow_modules = ["火矢", "火矢"]

        obs = build_obs_profile(
            player,
            state,
            player.player_id,
            bow_context=True,
            choose_mode=True,
            choose_situation="g4_strike_pick_weapon",
            choose_n_options=1,
            choose_options=["攻击 AI"],
        )
        # bow-context 起点与第一个 choose option 的 present 位复用；choose 优先。
        assert obs[M9_BOW_CONTEXT_BASE] == 1.0
        assert obs[M9_BOW_CONTEXT_BASE + 1] == 0.0
    finally:
        experiments.set_profile(previous_profile)


def test_m9_long_term_economy_advantage_uses_credits_not_vouchers() -> None:
    from engine import experiments
    from engine.game_state import GameState
    from models.player import Player
    from rl.env import BadtimeWarEnv

    previous_profile = experiments.current_profile()
    experiments.set_profile("m9-rfc")
    try:
        state = GameState()
        state.max_rounds = 40
        player = Player("rl_0", "RL")
        opponent = Player("ai_0", "AI")
        state.add_player(player)
        state.add_player(opponent)
        player.credits, opponent.credits = 9, 1
        player.vouchers, opponent.vouchers = 0, 3

        env = object.__new__(BadtimeWarEnv)
        env._rl_player = player
        env._state = state
        env._lt_initial_hp = player.hp
        env._lt_last_kill_round = 0
        env._lt_last_kill_count = 0
        env._lt_attack_actions = 0
        env._lt_total_actions = 0
        env.num_opponents = 1

        stats = env._build_long_term_stats()
        assert stats["economy_advantage"] == (9 - 1) / 20
    finally:
        experiments.set_profile(previous_profile)


def test_m9_detectors_stack_by_attribute_while_v2exp_keeps_old_gate() -> None:
    from engine import experiments
    from engine.game_state import GameState
    from locations import military_base, shop
    from models.player import Player

    previous_profile = experiments.current_profile()
    try:
        experiments.set_profile("m9-rfc")
        state = GameState()
        player = Player("rl_0", "RL")
        state.add_player(player)
        player.has_detection = True
        player.detection_attrs = {"魔法"}
        player.credits = 20
        player.has_military_pass = True

        assert shop.can_interact(player, "热成像仪", state)[0]
        assert military_base.can_interact(player, "雷达", state)[0]

        player.detection_attrs.add("普通")
        assert not shop.can_interact(player, "热成像仪", state)[0]
        player.detection_attrs.add("科技")
        assert not military_base.can_interact(player, "雷达", state)[0]

        experiments.set_profile("v2exp")
        legacy_state = GameState()
        legacy_player = Player("rl_0", "RL")
        legacy_state.add_player(legacy_player)
        legacy_player.has_detection = True
        legacy_player.detection_attrs = {"魔法"}
        legacy_player.credits = 20
        legacy_player.vouchers = 1
        legacy_player.has_military_pass = True
        assert not shop.can_interact(legacy_player, "热成像仪", legacy_state)[0]
        assert not military_base.can_interact(legacy_player, "雷达", legacy_state)[0]

        from rl.obs_builder_m9 import build_obs_profile
        legacy_obs = build_obs_profile(
            legacy_player, legacy_state, legacy_player.player_id,
        )
        assert legacy_obs.shape == (539,)
        assert np.isclose(float(legacy_obs[2]), 1 / 3)
        assert float(legacy_obs[19]) == 1.0
    finally:
        experiments.set_profile(previous_profile)


def test_m9_betting_choose_contexts_are_distinct_and_checkpoint_compatible() -> None:
    expected = {
        "m9_betting_action", "m9_betting_target", "m9_betting_amount",
        "m9_betting_transfer_out", "m9_betting_transfer_in",
    }
    ids = [_CHOOSE_SITUATION_MAP[name] for name in sorted(expected)]
    assert len(ids) == len(set(ids))
    assert all(-5 <= value <= -1 for value in ids)
    # 旧 checkpoint 已学习的 situation 值不得因新增类别被重新缩放。
    assert _CHOOSE_SITUATION_MAP["talent_t0_retry"] == 54


def test_m9_betting_targets_use_stable_opponent_slots() -> None:
    from rl.action_space import (
        ACTION_COUNT_M9_FIXED, IDX_TALENT_T0_TARGET_BASE, build_action_mask,
        idx_to_choose_option,
    )

    me = SimpleNamespace(player_id="rl_0", name="RL")
    first = SimpleNamespace(player_id="ai_0", name="AI_0")
    second = SimpleNamespace(player_id="ai_1", name="AI_1")
    state = SimpleNamespace(
        m9_enabled=True,
        player_order=["ai_1", "rl_0", "ai_0"],
        get_player=lambda pid: {
            "rl_0": me, "ai_0": first, "ai_1": second,
        }.get(pid),
    )
    options = ["AI_1", "AI_0"]  # 故意与稳定 player_id slot 顺序相反
    mask = build_action_mask(
        me, state, me.player_id,
        choose_mode=True,
        choose_situation="m9_betting_target",
        choose_options=options,
    )
    enabled = np.flatnonzero(mask).tolist()
    assert enabled == [
        IDX_TALENT_T0_TARGET_BASE,
        IDX_TALENT_T0_TARGET_BASE + 1,
    ]
    assert idx_to_choose_option(
        IDX_TALENT_T0_TARGET_BASE, options, "m9_betting_target", me, state,
    ) == "AI_0"
    assert mask.shape == (ACTION_COUNT_M9_FIXED,)
