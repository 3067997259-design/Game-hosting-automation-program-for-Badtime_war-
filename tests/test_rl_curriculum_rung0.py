"""RL 课程梯级 Rung 0（dummy bot）的可用性与"可赢性"守卫。

为什么需要 Rung 0：shoot 失明期最低一级对手是 archer/turtle，RL 胜率恒 5%，
但这个数字无法区分两件完全不同的事——"策略没学会" 和 "那条指令物理上发不出"。
dummy 把所有干扰变量拿掉（不发育/不移动/不还手），于是：
    在 Rung 0 都赢不了 ⇒ 一定是动作空间/奖励/探索出了问题，不是对手太强。

因此本测试守的是这条判据本身成立：
1. dummy 已注册进 BOT_REGISTRY（RL env 的 opponent_bots 按名取用）；
2. dummy 恒 forfeit（哪怕手里有武器、身边有敌人）；
3. 它确实能被一条最朴素的进攻脚本（rush）在轮数上限内打死——
   若哪天引擎改动让 dummy 变成打不死的（如被动回血/免伤），Rung 0 就失去
   判据资格，这里必须先红。
"""

from __future__ import annotations

import contextlib
import io
import unittest

import numpy as np

from engine import experiments


class TestRung0DummyBot(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        experiments.set_profile("m9-rfc")

    @classmethod
    def tearDownClass(cls):
        experiments.reset()

    def test_registered_for_rl_curriculum(self):
        from controllers.bots import BOT_REGISTRY, DummyBotController
        self.assertIs(BOT_REGISTRY.get("dummy"), DummyBotController,
                      "RL env 通过 opponent_bots=['dummy'] 按名取用，必须在注册表内")

    def test_always_forfeits_even_when_armed(self):
        from controllers.bots import DummyBotController
        from models.equipment import make_weapon
        from models.player import Player

        bot = DummyBotController()
        me = Player("p1", "Dummy")
        me.weapons.append(make_weapon("小刀"))
        me.is_awake = True
        # game_state=None：dummy 的决策不得依赖任何局势信息
        self.assertEqual(bot.decide(me, None), "forfeit")
        # 未起床时基类先起床（不在图上就不产生可学习的交互）
        me.is_awake = False
        self.assertEqual(bot.get_command(me, None, []), "wake")

    def test_combat_core_contract_excludes_m9_side_systems(self):
        from rl.action_space import action_family_from_idx, idx_to_command
        from rl.env import BadtimeWarEnv

        env = BadtimeWarEnv(
            num_opponents=1, max_rounds=8, enable_talents=False, rl_talent=0,
            opponent_bots=["dummy"], action_contract="combat_core",
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                env.reset(seed=17)
            families = {
                action_family_from_idx(int(idx))
                for idx in np.flatnonzero(env.action_masks())
            }
            self.assertLessEqual(
                families,
                {"forfeit", "wake", "move", "interact", "lock", "find", "attack", "shoot"},
            )
            self.assertFalse({"special", "report", "designate"} & families)
            interact_commands = {
                idx_to_command(int(idx), env._rl_player, env._state)
                for idx in np.flatnonzero(env.action_masks())
                if action_family_from_idx(int(idx)) == "interact"
            }
            self.assertEqual(interact_commands, {"interact 小刀"})
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                env.close()

    def test_beatable_by_naive_aggression(self):
        """rush（最朴素的进攻脚本）应稳定杀死 dummy —— Rung 0 有可赢信号。"""
        import stats_runner

        wins = 0
        rounds: list[int] = []
        with contextlib.redirect_stdout(io.StringIO()):
            for _ in range(8):
                res = stats_runner.run_single_game(
                    2, lineup=["rush", "dummy"], no_talents=True)
                rush_pids = {row["pid"] for row in res["players"]
                             if row["personality"] == "rush"}
                dummy_alive = [row["alive"] for row in res["players"]
                               if row["personality"] == "dummy"]
                if res.get("survival_winner") in rush_pids and not any(dummy_alive):
                    wins += 1
                rounds.append(int(res.get("rounds", 0) or 0))
        self.assertEqual(wins, 8,
                         f"rush 应 8/8 击杀 dummy（实得 {wins}）；Rung 0 若不可赢，"
                         f"就无法用它区分『策略没学会』与『指令发不出』")
        self.assertLess(max(rounds), 30,
                        f"击杀应在轮数上限内明确完成（实测最长 {max(rounds)} 轮）")

    def test_resume_eval_schedule_is_strictly_future(self):
        """续训不得在首批采样时补跑所有历史评估点。"""
        from tools.m9_rl_train import _next_eval_step

        self.assertEqual(_next_eval_step(0, 25_000), 25_000)
        self.assertEqual(_next_eval_step(200_000, 25_000), 225_000)
        self.assertEqual(_next_eval_step(212_345, 25_000), 225_000)
        with self.assertRaises(ValueError):
            _next_eval_step(100, 0)

    def test_one_vs_one_worker_pool_supports_weighted_replay(self):
        from tools.m9_rl_train import _opponents_for_worker

        pool = ["archer", "archer", "archer", "dummy"]
        assigned = [_opponents_for_worker(pool, 1, i) for i in range(8)]
        self.assertEqual(
            assigned,
            [["archer"], ["archer"], ["archer"], ["dummy"]] * 2,
        )
        # 多人局仍按座位解释，不把 lineup 错当随机池。
        self.assertIs(_opponents_for_worker(pool, 2, 3), pool)

    def test_multiplayer_personality_lineups_cycle_with_random_replay(self):
        from tools.m9_rl_train import _personality_lineup_for_worker

        lineups = [
            ["political", "political"],
            ["assassin", "political"],
            ["random"],
        ]
        assigned = [
            _personality_lineup_for_worker(lineups, 2, i)
            for i in range(6)
        ]
        self.assertEqual(
            assigned,
            [
                ["political", "political"],
                ["assassin", "political"],
                None,
            ] * 2,
        )
        with self.assertRaises(ValueError):
            _personality_lineup_for_worker([["political"]], 2, 0)

    def test_full_basic_ai_personalities_can_be_fixed_per_seat(self):
        from rl.env import BadtimeWarEnv

        env = BadtimeWarEnv(
            num_opponents=2, max_rounds=4,
            enable_talents=False, rl_talent=0,
            opponent_personalities=["political", "assassin"],
            action_contract="combat_economy",
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                env.reset(seed=23)
            self.assertEqual(
                env._state.get_player("ai_0").controller.personality,
                "political",
            )
            self.assertEqual(
                env._state.get_player("ai_1").controller.personality,
                "assassin",
            )
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                env.close()

        with self.assertRaisesRegex(ValueError, "未知 BasicAI 人格"):
            BadtimeWarEnv(opponent_personalities=["not-a-personality"])

    def test_assassin_pressure_course_starts_after_detector_handoff(self):
        from controllers.ai.controller import BasicAIController
        from models.equipment import make_armor
        from rl.action_space import idx_to_command
        from rl.env import BadtimeWarEnv
        from rl.reward_m9 import M9RewardTracker
        from tools.m9_recurrent_bc import _scenario_success_kills
        from tools.rung0_chain_probe import (
            _assassin_opening_prep_needed,
            _assassin_opening_prep_skill_mask,
        )

        env = BadtimeWarEnv(
            num_opponents=2, max_rounds=40,
            enable_talents=False, rl_talent=0,
            opponent_personalities=["assassin", "assassin"],
            action_contract="combat_economy",
            course_scenario="combat_assassin_pressure",
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                env.reset(seed=29)
            self.assertIn("魔法", env._rl_player.detection_attrs)
            self.assertEqual(env._rl_player.hp, 20)
            self.assertEqual(env._rl_player.location, "魔法所")
            self.assertEqual((env._rl_player.credits, env._rl_player.arrows), (0, 0))
            self.assertTrue(env._rl_player.has_weapon("小刀"))
            self.assertTrue(all(
                isinstance(env._state.get_player(f"ai_{idx}").controller,
                           BasicAIController)
                for idx in range(2)
            ))
            self.assertTrue(all(
                env._state.get_player(f"ai_{idx}").is_invisible
                and "魔法" in env._state.get_player(f"ai_{idx}").stealth_attrs
                and bool(env._state.get_player(f"ai_{idx}").armor.get_all_active())
                for idx in range(2)
            ))
            self.assertEqual(
                M9RewardTracker._terminal_reward(env._state, "rl_0"), -100.0,
            )
            env._rl_player.kill_count = 1
            self.assertEqual(
                M9RewardTracker._terminal_reward(env._state, "rl_0"), 100.0,
            )
            self.assertEqual(
                _scenario_success_kills("combat_assassin_pressure"), 1,
            )
            self.assertEqual(_scenario_success_kills("combat_retarget"), 2)
            env._rl_player.kill_count = 0
            self.assertTrue(_assassin_opening_prep_needed(
                env._rl_player, env._state,
            ))
            focused, kind, done = _assassin_opening_prep_skill_mask(
                env.action_masks(), env._rl_player, env._state,
            )
            self.assertEqual((kind, done), ("move_shop", False))
            self.assertEqual(
                idx_to_command(
                    int(next(iter(np.flatnonzero(focused)))),
                    env._rl_player, env._state,
                ),
                "move 商店",
            )
            env._rl_player.location = "商店"
            env._rl_player.credits = 3
            focused, kind, done = _assassin_opening_prep_skill_mask(
                env.action_masks(), env._rl_player, env._state,
            )
            self.assertEqual((kind, done), ("buy_armor", False))
            self.assertEqual(
                idx_to_command(
                    int(next(iter(np.flatnonzero(focused)))),
                    env._rl_player, env._state,
                ),
                "interact 陶瓷护甲",
            )
            supply_only, kind, done = _assassin_opening_prep_skill_mask(
                env.action_masks(), env._rl_player, env._state,
                require_armor=False,
            )
            self.assertEqual((kind, done), ("supply", False))
            self.assertEqual(
                idx_to_command(
                    int(next(iter(np.flatnonzero(supply_only)))),
                    env._rl_player, env._state,
                ),
                "interact 箭矢补给",
            )
            armor = make_armor("陶瓷护甲")
            self.assertIsNotNone(armor)
            env._rl_player.add_armor(armor)
            env._rl_player.credits = 1
            focused, kind, done = _assassin_opening_prep_skill_mask(
                env.action_masks(), env._rl_player, env._state,
            )
            self.assertEqual((kind, done), ("supply", False))
            self.assertEqual(
                idx_to_command(
                    int(next(iter(np.flatnonzero(focused)))),
                    env._rl_player, env._state,
                ),
                "interact 箭矢补给",
            )
            env._rl_player.arrows = 2
            self.assertFalse(_assassin_opening_prep_needed(
                env._rl_player, env._state,
            ))
            self.assertTrue(all(
                env._state.get_player(f"ai_{idx}").controller.personality
                == "assassin"
                for idx in range(2)
            ))
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                env.close()

    def test_multiplayer_explicit_basic_ai_sentinel_builds_full_controller(self):
        from controllers.ai.controller import BasicAIController
        from controllers.bots.dummy_bot import DummyBotController
        from rl.env import BadtimeWarEnv

        env = BadtimeWarEnv(
            num_opponents=2, max_rounds=4,
            enable_talents=False, rl_talent=0,
            opponent_bots=["basic_ai", "dummy"],
            action_contract="combat_core",
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                env.reset(seed=19)
            self.assertIsInstance(
                env._state.get_player("ai_0").controller, BasicAIController,
            )
            self.assertIsInstance(
                env._state.get_player("ai_1").controller, DummyBotController,
            )
            self.assertEqual(
                env._opponent_model_stems,
                {"ai_0": "basic_ai", "ai_1": "dummy"},
            )
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                env.close()

    def test_one_vs_one_worker_pool_can_mix_real_basic_ai(self):
        from tools.m9_rl_train import _opponents_for_worker

        pool = ["basic_ai", "basic_ai", "basic_ai", "rush"]
        assigned = [_opponents_for_worker(pool, 1, i) for i in range(8)]
        self.assertEqual(
            assigned,
            [None, None, None, ["rush"]] * 2,
        )

    def test_mixed_full_game_can_isolate_only_combat_scenario_workers(self):
        from tools.m9_rl_train import _action_contract_for_scenario

        self.assertIsNone(_action_contract_for_scenario(None, None, True))
        self.assertEqual(
            _action_contract_for_scenario(None, "combat_retarget", True),
            "combat_core",
        )
        self.assertEqual(
            _action_contract_for_scenario(None, "combat_retarget_static", True),
            "combat_core",
        )
        self.assertIsNone(
            _action_contract_for_scenario(None, "combat_retarget", False),
        )

    def test_retarget_course_keeps_first_and_second_kill_in_one_episode(self):
        from rl.action_space import idx_to_command
        from rl.env import BadtimeWarEnv

        env = BadtimeWarEnv(
            num_opponents=2, max_rounds=25,
            enable_talents=False, rl_talent=0,
            action_contract="combat_core", course_scenario="combat_retarget",
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                env.reset(seed=23)
            self.assertTrue(env._state.get_player("ai_0").is_alive())
            self.assertEqual(env._state.get_player("ai_0").hp, 1)
            self.assertTrue(env._state.get_player("ai_1").is_alive())
            self.assertEqual(env._rl_player.kill_count, 0)
            self.assertFalse(env._rl_player.is_criminal)
            self.assertEqual(
                env._reward_tracker._event_cursor, len(env._state.event_log),
            )
            commands = {
                idx_to_command(int(idx), env._rl_player, env._state)
                for idx in np.flatnonzero(env.action_masks())
            }
            self.assertIn("attack AI_0 小刀", commands)
            self.assertNotIn("move home_ai_0", commands)
            self.assertFalse(any(cmd.startswith("special ") for cmd in commands))
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                env.close()

    def test_static_retarget_keeps_sequence_but_freezes_second_opponent(self):
        from controllers.bots import DummyBotController
        from rl.env import BadtimeWarEnv

        env = BadtimeWarEnv(
            num_opponents=2, max_rounds=25,
            enable_talents=False, rl_talent=0,
            action_contract="combat_core",
            course_scenario="combat_retarget_static",
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                env.reset(seed=24)
            first = env._state.get_player("ai_0")
            second = env._state.get_player("ai_1")
            self.assertEqual(first.hp, 1)
            self.assertEqual(first.location, env._rl_player.location)
            self.assertIsInstance(second.controller, DummyBotController)
            self.assertEqual(second.location, "home_ai_1")
            self.assertEqual(env._rl_player.kill_count, 0)
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                env.close()

    def test_multi_low_hp_courses_keep_normal_opening_and_full_basic_ai(self):
        from controllers.ai.controller import BasicAIController
        from rl.env import BadtimeWarEnv

        for scenario, expected_hp in (
            ("combat_multi_quarter_hp", 5),
            ("combat_multi_half_hp", 10),
            ("combat_multi_full_hp", 20),
        ):
            env = BadtimeWarEnv(
                num_opponents=2, max_rounds=40,
                enable_talents=False, rl_talent=0,
                action_contract="combat_core",
                course_scenario=scenario,
            )
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    env.reset(seed=25)
                opponents = [env._state.get_player("ai_0"), env._state.get_player("ai_1")]
                self.assertEqual([opp.hp for opp in opponents], [expected_hp, expected_hp])
                self.assertTrue(all(isinstance(opp.controller, BasicAIController)
                                    for opp in opponents))
                self.assertNotEqual(env._rl_player.location, "home_ai_0")
                self.assertFalse(env._state.markers.has_relation(
                    "rl_0", "ENGAGED_WITH", "ai_0",
                ))
                self.assertEqual(env._rl_player.kill_count, 0)
            finally:
                with contextlib.redirect_stdout(io.StringIO()):
                    env.close()

    def test_multitarget_oracle_does_not_forfeit_when_second_target_is_lockable(self):
        """slot0 已锁定不得让 oracle 忽略 slot1 的可生产动作。"""
        from rl.action_space import IDX_FORFEIT, IDX_LOCK_BASE
        from rl.env import BadtimeWarEnv
        from tools.rung0_chain_probe import _oracle_action

        env = BadtimeWarEnv(
            num_opponents=2, max_rounds=10,
            enable_talents=False, rl_talent=0,
            action_contract="combat_core",
            course_scenario="combat_multi_half_hp",
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                env.reset(seed=26)
            env._state.markers.add_relation(
                "ai_0", "LOCKED_BY", env._rl_player.player_id,
            )
            mask = np.zeros(env.action_space.n, dtype=bool)
            mask[IDX_FORFEIT] = True
            mask[IDX_LOCK_BASE + 1] = True
            self.assertEqual(
                _oracle_action(mask, env._rl_player, env._state),
                IDX_LOCK_BASE + 1,
            )
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                env.close()

    def test_phase_gate_switches_only_after_configured_kill_count(self):
        from tools.rung0_chain_probe import _use_continuation_policy

        self.assertFalse(_use_continuation_policy(0, 1))
        self.assertTrue(_use_continuation_policy(1, 1))
        self.assertFalse(_use_continuation_policy(1, 2))
        self.assertTrue(_use_continuation_policy(2, 2))
        with self.assertRaises(ValueError):
            _use_continuation_policy(0, 0)

    def test_target_commitment_mask_keeps_only_live_target_progress(self):
        from rl.action_space import idx_to_command
        from rl.env import BadtimeWarEnv
        from tools.rung0_chain_probe import _target_commitment_mask

        env = BadtimeWarEnv(
            num_opponents=2, max_rounds=25,
            enable_talents=False, rl_talent=0,
            action_contract="combat_core", course_scenario="combat_retarget",
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                env.reset(seed=27)
            target_mask, target_name = _target_commitment_mask(
                env.action_masks(), env._rl_player, env._state,
            )
            commands = {
                idx_to_command(int(idx), env._rl_player, env._state)
                for idx in np.flatnonzero(target_mask)
            }
            self.assertEqual(target_name, "AI_0")
            self.assertTrue(commands)
            self.assertTrue(all("AI_0" in cmd for cmd in commands))
            self.assertFalse(any("AI_1" in cmd for cmd in commands))

            env._state.markers.add_relation(
                "ai_0", "LOCKED_BY", env._rl_player.player_id,
            )
            locked_mask, locked_target = _target_commitment_mask(
                env.action_masks(), env._rl_player, env._state,
            )
            locked_commands = {
                idx_to_command(int(idx), env._rl_player, env._state)
                for idx in np.flatnonzero(locked_mask)
            }
            self.assertEqual(locked_target, "AI_0")
            self.assertNotIn("lock AI_0", locked_commands)
            self.assertTrue(locked_commands)
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                env.close()

    def test_recurrent_bc_sequence_weights_must_be_positive_and_aligned(self):
        from tools.m9_recurrent_bc import EpisodeSequence

        observations = np.zeros((2, 3), dtype=np.float32)
        actions = np.asarray([0, 1], dtype=np.int64)
        masks = np.ones((2, 2), dtype=bool)
        valid = EpisodeSequence(
            observations, actions, masks,
            weights=np.asarray([1.0, 12.0], dtype=np.float32),
        )
        self.assertEqual(valid.weights.tolist(), [1.0, 12.0])
        with self.assertRaises(ValueError):
            EpisodeSequence(
                observations, actions, masks,
                weights=np.asarray([1.0], dtype=np.float32),
            )
        with self.assertRaises(ValueError):
            EpisodeSequence(
                observations, actions, masks,
                weights=np.asarray([1.0, 0.0], dtype=np.float32),
            )

    def test_bow_bc_balances_cases_and_sanitizes_old_policy_input(self):
        from rl.obs_builder_m9 import (
            M9_BOW_CONTEXT_BASE, M9_BOW_CONTEXT_DIM, OBS_DIM_M9_TOTAL,
        )
        from tools.m9_recurrent_bc import (
            _bow_loadout_quotas, _source_policy_observation,
        )

        quotas = _bow_loadout_quotas(14)
        self.assertEqual(sum(quotas.values()), 14)
        self.assertLessEqual(max(quotas.values()) - min(quotas.values()), 1)

        obs = np.ones(OBS_DIM_M9_TOTAL, dtype=np.float32)
        sanitized = _source_policy_observation(obs, "equipment_bow_loadout")
        start = M9_BOW_CONTEXT_BASE
        end = start + M9_BOW_CONTEXT_DIM
        self.assertTrue(np.all(sanitized[start:end] == 0.0))
        self.assertTrue(np.all(sanitized[:start] == 1.0))
        self.assertTrue(np.all(obs == 1.0), "sanitizer must not mutate training obs")
        self.assertIs(
            _source_policy_observation(obs, "combat_retarget"), obs,
        )

    def test_soft_commitment_prior_preserves_both_distributions(self):
        from tools.rung0_chain_probe import _blend_commitment_probabilities

        full = np.asarray([0.8, 0.2], dtype=np.float32)
        focused = np.asarray([0.0, 1.0], dtype=np.float32)
        np.testing.assert_allclose(
            _blend_commitment_probabilities(full, focused, 0.0), full,
        )
        np.testing.assert_allclose(
            _blend_commitment_probabilities(full, focused, 1.0), focused,
        )
        blended = _blend_commitment_probabilities(full, focused, 0.6)
        self.assertGreater(blended[1], blended[0])
        with self.assertRaises(ValueError):
            _blend_commitment_probabilities(full, focused, 1.1)

    def test_economy_bow_supply_course_closes_income_to_damage_loop(self):
        from rl.action_space import command_to_action_idx, idx_to_command
        from rl.env import BadtimeWarEnv

        env = BadtimeWarEnv(
            num_opponents=1, max_rounds=12,
            enable_talents=False, rl_talent=0,
            action_contract="economy_course",
            course_scenario="economy_bow_supply",
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                env.reset(seed=28)
            player = env._rl_player
            target = env._state.get_player("ai_0")
            self.assertEqual(player.location, "商店")
            self.assertEqual((player.credits, player.arrows), (0, 0))
            self.assertTrue(player.has_weapon("弓"))
            self.assertEqual(target.hp, 6)

            commands = {
                idx_to_command(int(idx), player, env._state)
                for idx in np.flatnonzero(env.action_masks())
            }
            self.assertIn("interact 打工", commands)
            self.assertNotIn("interact 隐身衣", commands)
            self.assertNotIn("interact 箭矢补给", commands)
            self.assertNotIn("shoot AI_0", commands)

            def step(command: str):
                action = command_to_action_idx(command, player, env._state)
                self.assertGreaterEqual(action, 0, command)
                self.assertTrue(env.action_masks()[action], command)
                with contextlib.redirect_stdout(io.StringIO()):
                    return env.step(action)

            step("interact 打工")
            self.assertEqual(player.credits, 2)
            self.assertIn(
                "interact 箭矢补给",
                {
                    idx_to_command(int(idx), player, env._state)
                    for idx in np.flatnonzero(env.action_masks())
                },
            )
            step("interact 箭矢补给")
            self.assertEqual((player.credits, player.arrows), (1, 2))
            step("shoot AI_0")
            _obs, _reward, terminated, truncated, _info = step("shoot AI_0")
            self.assertTrue(terminated or truncated)
            self.assertEqual(player.kill_count, 1)
            self.assertFalse(target.is_alive())
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                env.close()

    def test_economy_pursuit_course_starts_after_kill_with_narrow_mechanics(self):
        from rl.action_space import idx_to_command
        from rl.env import BadtimeWarEnv

        env = BadtimeWarEnv(
            num_opponents=2, max_rounds=20,
            enable_talents=False, rl_talent=0,
            action_contract="economy_course",
            course_scenario="economy_pursuit_supply",
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                env.reset(seed=31)
            player = env._rl_player
            dead = env._state.get_player("ai_0")
            moving = env._state.get_player("ai_1")
            self.assertEqual(player.kill_count, 1)
            self.assertEqual((player.credits, player.arrows), (0, 0))
            self.assertTrue(player.has_weapon("弓"))
            self.assertFalse(dead.is_alive())
            self.assertEqual(dead.location, None)
            self.assertTrue(moving.is_alive())
            self.assertEqual((moving.hp, moving.max_hp), (6, 6))
            self.assertEqual(moving.weapons, [])
            self.assertTrue(player.has_detection)
            self.assertEqual(player.detection_attrs, {"普通", "魔法", "科技"})
            self.assertEqual(
                env._opponent_model_stems[moving.player_id], "basic_ai",
            )

            commands = {
                idx_to_command(int(idx), player, env._state)
                for idx in np.flatnonzero(env.action_masks())
            }
            self.assertIn("move 商店", commands)
            self.assertNotIn("interact 打工", commands)
            self.assertNotIn("interact 小刀", commands)
            self.assertFalse(any(command.startswith("attack ") for command in commands))
            self.assertFalse(any(command.startswith("special ") for command in commands))

            reward_tracker = env._reward_tracker
            self.assertEqual(
                reward_tracker._terminal_reward(env._state, "rl_0"), -100.0,
            )
            player.kill_count = 2
            self.assertEqual(
                reward_tracker._terminal_reward(env._state, "rl_0"), 100.0,
            )
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                env.close()

    def test_armored_pursuit_bridge_matches_postkill_loadout(self):
        from rl.env import BadtimeWarEnv

        env = BadtimeWarEnv(
            num_opponents=2, max_rounds=30,
            enable_talents=False, rl_talent=0,
            action_contract="economy_course",
            course_scenario="economy_armored_pursuit_supply",
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                env.reset(seed=32)
            player = env._rl_player
            target = env._state.get_player("ai_1")
            self.assertEqual((player.kill_count, player.hp), (1, 12))
            self.assertEqual((player.credits, player.arrows), (0, 3))
            self.assertTrue(player.has_weapon("小刀"))
            self.assertTrue(player.has_weapon("弓"))
            self.assertEqual(
                [piece.name for piece in target.armor.get_all_active()],
                ["盾牌"],
            )
            self.assertEqual((target.hp, target.max_hp), (20, 20))
            self.assertEqual(
                env._opponent_model_stems[target.player_id], "basic_ai",
            )
            self.assertEqual(
                env._reward_tracker._terminal_reward(env._state, "rl_0"),
                -100.0,
            )
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                env.close()

    def test_armored_pursuit_bridge_mirrors_survivor_slot(self):
        from rl.env import BadtimeWarEnv

        env = BadtimeWarEnv(
            num_opponents=2, max_rounds=40,
            enable_talents=False, rl_talent=0,
            action_contract="economy_integration_course",
            course_scenario="economy_armored_pursuit_supply_slot0",
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                env.reset(seed=146)
            survivor = env._state.get_player("ai_0")
            dead = env._state.get_player("ai_1")
            self.assertEqual(
                (env._rl_player.kill_count, env._rl_player.hp), (1, 12),
            )
            self.assertEqual((survivor.hp, survivor.max_hp), (20, 20))
            self.assertEqual(dead.hp, 0)
            self.assertIsNone(dead.location)
            self.assertEqual(env._rl_player.location, "home_ai_1")
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                env.close()

    def test_fire_antiarmor_course_exposes_module_and_requires_second_kill(self):
        from rl.action_space import idx_to_command
        from rl.env import BadtimeWarEnv
        from tools.rung0_chain_probe import _fire_antiarmor_oracle_action

        env = BadtimeWarEnv(
            num_opponents=2, max_rounds=40,
            enable_talents=False, rl_talent=0,
            action_contract="equipment_course",
            course_scenario="equipment_fire_antiarmor",
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                env.reset(seed=150)
            player = env._rl_player
            target = env._state.get_player("ai_1")
            mask = env.action_masks()
            commands = {
                idx_to_command(int(idx), player, env._state)
                for idx in np.flatnonzero(mask)
            }
            self.assertEqual((player.kill_count, player.hp), (1, 16))
            self.assertEqual(player.location, "魔法所")
            self.assertEqual(
                [piece.name for piece in target.armor.get_all_active()],
                ["陶瓷护甲"],
            )
            self.assertIn("interact 火矢模块", commands)
            self.assertNotIn("interact 魔导模块", commands)
            action = _fire_antiarmor_oracle_action(mask, player, env._state)
            self.assertEqual(
                idx_to_command(action, player, env._state),
                "interact 火矢模块",
            )
            self.assertEqual(
                env._reward_tracker._terminal_reward(env._state, "rl_0"),
                -100.0,
            )
            player.kill_count = 2
            self.assertEqual(
                env._reward_tracker._terminal_reward(env._state, "rl_0"),
                100.0,
            )
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                env.close()

    def test_fire_antiarmor_router_moves_then_installs_only_in_safe_stratum(self):
        from rl.action_space import idx_to_command
        from rl.env import BadtimeWarEnv
        from tools.rung0_chain_probe import _fire_antiarmor_skill_mask

        env = BadtimeWarEnv(
            num_opponents=2, max_rounds=40,
            enable_talents=False, rl_talent=0,
            action_contract=None,
            course_scenario="equipment_fire_antiarmor",
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                env.reset(seed=151)
            player = env._rl_player
            target = env._state.get_player("ai_1")

            focused, routed, kind = _fire_antiarmor_skill_mask(
                env.action_masks(), player, env._state,
            )
            self.assertEqual((routed, kind), ("ai_1", "install"))
            self.assertEqual(
                idx_to_command(int(focused.nonzero()[0][0]), player, env._state),
                "interact 火矢模块",
            )

            player.location = "商店"
            focused, routed, kind = _fire_antiarmor_skill_mask(
                env.action_masks(), player, env._state,
            )
            self.assertEqual((routed, kind), ("ai_1", "move"))
            self.assertEqual(
                idx_to_command(int(focused.nonzero()[0][0]), player, env._state),
                "move 魔法所",
            )

            player.hp = 10
            player.arrows = 1
            focused, routed, kind = _fire_antiarmor_skill_mask(
                env.action_masks(), player, env._state,
            )
            self.assertIsNone(routed)
            self.assertIsNone(kind)
            focused, routed, kind = _fire_antiarmor_skill_mask(
                env.action_masks(), player, env._state, min_hp=6,
                require_lowhp_arrows=True,
            )
            self.assertEqual((routed, kind), ("ai_1", "move"))

            player.arrows = 0
            focused, routed, kind = _fire_antiarmor_skill_mask(
                env.action_masks(), player, env._state, min_hp=6,
                require_lowhp_arrows=True,
            )
            self.assertIsNone(routed)
            self.assertIsNone(kind)

            target.hp = 7
            focused, routed, kind = _fire_antiarmor_skill_mask(
                env.action_masks(), player, env._state,
            )
            self.assertIsNone(routed)
            self.assertIsNone(kind)
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                env.close()

    def test_postkill_entry_signature_captures_route_prerequisites(self):
        from rl.env import BadtimeWarEnv
        from tools.rung0_chain_probe import _postkill_entry_signature

        env = BadtimeWarEnv(
            num_opponents=2, max_rounds=40,
            enable_talents=False, rl_talent=0,
            action_contract="economy_course",
            course_scenario="economy_armored_pursuit_supply",
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                env.reset(seed=147)
            signature = _postkill_entry_signature(
                env.action_masks(), env._rl_player, env._state,
            )
            self.assertIn("bow=1;knife=1;armor=0", signature)
            self.assertIn("attack=0", signature)
            self.assertIn("armor_buy=0", signature)
            self.assertIn("arrows=3+;credits=0;hp=11-15", signature)
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                env.close()

    def test_postkill_recovery_budget_closes_after_completion_or_limit(self):
        from tools.rung0_chain_probe import _postkill_recovery_budget_open

        self.assertTrue(_postkill_recovery_budget_open(True, False, 0, 4))
        self.assertTrue(_postkill_recovery_budget_open(True, False, 3, 4))
        self.assertFalse(_postkill_recovery_budget_open(True, False, 4, 4))
        self.assertFalse(_postkill_recovery_budget_open(True, True, 1, 4))
        self.assertFalse(_postkill_recovery_budget_open(False, False, 0, 4))
        self.assertTrue(_postkill_recovery_budget_open(True, False, 99, 0))

    def test_postkill_armor_recovery_starts_with_work_then_buys(self):
        from rl.env import BadtimeWarEnv
        from tools.rung0_chain_probe import _postkill_armor_recovery_skill_mask
        from rl.action_space import idx_to_command

        env = BadtimeWarEnv(
            num_opponents=2, max_rounds=40,
            enable_talents=False, rl_talent=0,
            action_contract=None,
            course_scenario="economy_armored_pursuit_supply",
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                env.reset(seed=148)
            player = env._rl_player
            player.location = "商店"
            player.credits = 0
            focused, kind = _postkill_armor_recovery_skill_mask(
                env.action_masks(), player, env._state,
            )
            self.assertEqual(kind, "work")
            self.assertEqual(
                idx_to_command(int(focused.nonzero()[0][0]), player, env._state),
                "interact 打工",
            )
            player.credits = 3
            focused, kind = _postkill_armor_recovery_skill_mask(
                env.action_masks(), player, env._state,
            )
            self.assertEqual(kind, "buy")
            self.assertEqual(
                idx_to_command(int(focused.nonzero()[0][0]), player, env._state),
                "interact 陶瓷护甲",
            )
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                env.close()

    def test_postkill_funded_armor_only_buys_when_already_affordable(self):
        from rl.action_space import idx_to_command
        from rl.env import BadtimeWarEnv
        from tools.rung0_chain_probe import _postkill_funded_armor_skill_mask

        env = BadtimeWarEnv(
            num_opponents=2, max_rounds=40,
            enable_talents=False, rl_talent=0,
            action_contract=None,
            course_scenario="economy_armored_pursuit_supply",
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                env.reset(seed=149)
            player = env._rl_player
            player.location = "商店"
            player.credits = 3
            player.hp = 4
            player.arrows = 0
            mask = env.action_masks()
            focused, kind = _postkill_funded_armor_skill_mask(
                mask, player, env._state,
            )
            self.assertEqual(kind, "postkill_funded_armor")
            self.assertEqual(
                idx_to_command(int(focused.nonzero()[0][0]), player, env._state),
                "interact 陶瓷护甲",
            )

            player.credits = 0
            mask = env.action_masks()
            focused, kind = _postkill_funded_armor_skill_mask(
                mask, player, env._state,
            )
            self.assertIsNone(kind)
            self.assertTrue(np.array_equal(focused, mask))

            player.credits = 3
            player.kill_count = 0
            mask = env.action_masks()
            focused, kind = _postkill_funded_armor_skill_mask(
                mask, player, env._state,
            )
            self.assertIsNone(kind)
            self.assertTrue(np.array_equal(focused, mask))

            player.kill_count = 1
            player.hp = 6
            mask = env.action_masks()
            focused, kind = _postkill_funded_armor_skill_mask(
                mask, player, env._state,
            )
            self.assertIsNone(kind)
            self.assertTrue(np.array_equal(focused, mask))
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                env.close()

    def test_opening_armor_recovery_works_then_buys_without_hiding_damage(self):
        from models.equipment import make_armor
        from rl.action_space import idx_to_command
        from rl.env import BadtimeWarEnv
        from tools.rung0_chain_probe import _opening_armor_recovery_skill_mask

        env = BadtimeWarEnv(
            num_opponents=2, max_rounds=40,
            enable_talents=False, rl_talent=0,
            action_contract=None,
            course_scenario="economy_armored_pursuit_supply",
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                env.reset(seed=150)
            player = env._rl_player
            player.kill_count = 0
            player.location = "商店"
            player.hp = 8
            player.arrows = 0
            player.credits = 0

            focused, kind = _opening_armor_recovery_skill_mask(
                env.action_masks(), player, env._state,
            )
            self.assertEqual(kind, "opening_armor_work")
            self.assertEqual(
                idx_to_command(int(focused.nonzero()[0][0]), player, env._state),
                "interact 打工",
            )

            player.credits = 3
            focused, kind = _opening_armor_recovery_skill_mask(
                env.action_masks(), player, env._state,
            )
            self.assertEqual(kind, "opening_armor_buy")
            self.assertEqual(
                idx_to_command(int(focused.nonzero()[0][0]), player, env._state),
                "interact 陶瓷护甲",
            )

            player.arrows = 1
            focused, kind = _opening_armor_recovery_skill_mask(
                env.action_masks(), player, env._state,
            )
            self.assertIsNone(kind)

            player.arrows = 0
            player.add_armor(make_armor("陶瓷护甲"))
            focused, kind = _opening_armor_recovery_skill_mask(
                env.action_masks(), player, env._state,
            )
            self.assertIsNone(kind)
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                env.close()

    def test_opening_arrow_supply_recalls_one_step_only_when_no_damage_exists(self):
        from rl.action_space import idx_to_command
        from rl.env import BadtimeWarEnv
        from tools.rung0_chain_probe import _opening_arrow_supply_skill_mask

        env = BadtimeWarEnv(
            num_opponents=2, max_rounds=40,
            enable_talents=False, rl_talent=0,
            action_contract=None,
            course_scenario="economy_armored_pursuit_supply",
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                env.reset(seed=151)
            player = env._rl_player
            player.kill_count = 0
            player.location = "商店"
            player.credits = 1
            player.arrows = 0
            focused, kind = _opening_arrow_supply_skill_mask(
                env.action_masks(), player, env._state,
            )
            self.assertEqual(kind, "opening_arrow_supply")
            self.assertEqual(
                idx_to_command(int(focused.nonzero()[0][0]), player, env._state),
                "interact 箭矢补给",
            )

            player.arrows = 1
            focused, kind = _opening_arrow_supply_skill_mask(
                env.action_masks(), player, env._state,
            )
            self.assertIsNone(kind)

            player.arrows = 0
            target = env._state.get_player("ai_1")
            target.location = "商店"
            env._state.markers.add_relation(
                player.player_id, "ENGAGED_WITH", target.player_id,
            )
            focused, kind = _opening_arrow_supply_skill_mask(
                env.action_masks(), player, env._state,
            )
            self.assertIsNone(kind)
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                env.close()

    def test_precombat_police_election_only_takes_an_empty_candidate_window(self):
        from rl.action_space import idx_to_command
        from rl.env import BadtimeWarEnv
        from tools.rung0_chain_probe import _police_skill_mask

        env = BadtimeWarEnv(
            num_opponents=2, max_rounds=40,
            enable_talents=False, rl_talent=0,
            action_contract=None,
            opponent_personalities=["political", "political"],
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                env.reset(seed=152)
            player = env._rl_player
            player.is_awake = True
            env._state.markers.on_player_wake_up(player.player_id)
            # reset 会先推进排在 RL 前面的 BasicAI；本测试显式还原“RL
            # 是首个候选者”的窗口，另一分支再验证已有候选时不抢占。
            env._state.m9_police._candidates.clear()
            for candidate in env._state.players.values():
                if candidate is not player:
                    candidate.acted_this_round = False
            mask = env.action_masks()

            untouched, kind = _police_skill_mask(
                mask, player, env._state, proactive_election=False,
            )
            self.assertIsNone(kind)
            np.testing.assert_array_equal(untouched, mask)

            focused, kind = _police_skill_mask(
                mask, player, env._state, proactive_election=True,
            )
            self.assertEqual(kind, "precombat_election")
            self.assertEqual(
                idx_to_command(int(focused.nonzero()[0][0]), player, env._state),
                "special 竞选队长",
            )

            env._state.get_player("ai_0").acted_this_round = True
            focused, kind = _police_skill_mask(
                env.action_masks(), player, env._state,
                proactive_election=True,
            )
            self.assertIsNone(kind)
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                env.close()

    def test_captain_target_commitment_uses_public_captain_identity(self):
        from rl.action_space import idx_to_command
        from rl.env import BadtimeWarEnv
        from tools.rung0_chain_probe import _captain_target_commitment_mask

        env = BadtimeWarEnv(
            num_opponents=2, max_rounds=40,
            enable_talents=False, rl_talent=0,
            action_contract=None,
            course_scenario="combat_retarget",
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                env.reset(seed=153)
            env._state.m9_police.captain_id = "ai_1"
            focused, target_id = _captain_target_commitment_mask(
                env.action_masks(), env._rl_player, env._state,
            )
            self.assertEqual(target_id, "ai_1")
            commands = {
                idx_to_command(int(idx), env._rl_player, env._state)
                for idx in np.flatnonzero(focused)
            }
            target = env._state.get_player("ai_1")
            self.assertTrue(commands)
            self.assertTrue(all(
                "AI_1" in command or command == f"move {target.location}"
                for command in commands
            ))

            env._state.m9_police.captain_id = "rl_0"
            original = env.action_masks()
            focused, target_id = _captain_target_commitment_mask(
                original, env._rl_player, env._state,
            )
            self.assertIsNone(target_id)
            np.testing.assert_array_equal(focused, original)
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                env.close()

    def test_combat_economy_contract_excludes_police_and_special_actions(self):
        from rl.action_space import (
            ACTION_COUNT_M9_FIXED,
            IDX_INTERACT_BASE,
            IDX_INTERACT_M9_BASE,
            IDX_SPECIAL_M9_BASE,
            INTERACT_ITEMS,
            M9_INTERACT_ITEMS_EXTRA,
            apply_action_contract,
        )

        mask = np.ones(ACTION_COUNT_M9_FIXED, dtype=bool)
        filtered = apply_action_contract(mask, "combat_economy")
        self.assertTrue(filtered[
            IDX_INTERACT_BASE + INTERACT_ITEMS.index("打工")
        ])
        self.assertTrue(filtered[
            IDX_INTERACT_M9_BASE + M9_INTERACT_ITEMS_EXTRA.index("箭矢补给")
        ])
        self.assertFalse(filtered[
            IDX_INTERACT_M9_BASE + M9_INTERACT_ITEMS_EXTRA.index("举报")
        ])
        self.assertFalse(filtered[IDX_SPECIAL_M9_BASE])

    def test_pursuit_finish_only_adds_melee_to_narrow_economy_contract(self):
        from rl.action_space import (
            ACTION_COUNT_M9_FIXED,
            IDX_ATTACK_BASE,
            IDX_INTERACT_BASE,
            IDX_INTERACT_M9_BASE,
            IDX_SPECIAL_M9_BASE,
            INTERACT_ITEMS,
            M9_INTERACT_ITEMS_EXTRA,
            apply_action_contract,
        )

        mask = np.ones(ACTION_COUNT_M9_FIXED, dtype=bool)
        pursuit = apply_action_contract(mask, "economy_pursuit_drill")
        finish = apply_action_contract(mask, "economy_pursuit_finish")
        self.assertFalse(pursuit[IDX_ATTACK_BASE])
        self.assertTrue(finish[IDX_ATTACK_BASE])
        self.assertTrue(finish[
            IDX_INTERACT_BASE + INTERACT_ITEMS.index("打工")
        ])
        self.assertTrue(finish[
            IDX_INTERACT_M9_BASE + M9_INTERACT_ITEMS_EXTRA.index("箭矢补给")
        ])
        self.assertFalse(finish[IDX_SPECIAL_M9_BASE])
        difference = np.flatnonzero(finish & ~pursuit)
        self.assertTrue(len(difference) > 0)
        self.assertTrue(all(index >= IDX_ATTACK_BASE for index in difference))

    def test_pursuit_finish_oracle_uses_knife_only_after_melee_closes(self):
        from rl.action_space import (
            apply_action_contract, command_to_action_idx, idx_to_command,
        )
        from rl.env import BadtimeWarEnv
        from tools.rung0_chain_probe import (
            _economy_pursuit_finish_oracle_action, _postkill_melee_skill_mask,
        )

        env = BadtimeWarEnv(
            num_opponents=2, max_rounds=40,
            enable_talents=False, rl_talent=0,
            action_contract=None,
            course_scenario="economy_armored_pursuit_supply",
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                env.reset(seed=149)
            player = env._rl_player
            target = env._state.get_player("ai_1")
            player.location = "商店"
            player.hp = 12
            target.location = "医院"
            target.hp = 8
            mask = apply_action_contract(
                env.action_masks(), "economy_pursuit_finish",
            )
            action = _economy_pursuit_finish_oracle_action(
                mask, player, env._state,
            )
            self.assertEqual(
                idx_to_command(action, player, env._state), "move 医院",
            )

            player.location = target.location
            mask = apply_action_contract(
                env.action_masks(), "economy_pursuit_finish",
            )
            focused, target_id, kind = _postkill_melee_skill_mask(
                mask, player, env._state,
            )
            self.assertEqual((target_id, kind), ("ai_1", "find"))
            self.assertEqual(
                idx_to_command(int(focused.nonzero()[0][0]), player, env._state),
                "find AI_1",
            )
            env._state.markers.add_relation(
                player.player_id, "ENGAGED_WITH", target.player_id,
            )
            env._state.markers.add_relation(
                target.player_id, "ENGAGED_WITH", player.player_id,
            )
            mask = apply_action_contract(
                env.action_masks(), "economy_pursuit_finish",
            )
            action = _economy_pursuit_finish_oracle_action(
                mask, player, env._state,
            )
            self.assertEqual(
                idx_to_command(action, player, env._state),
                "attack AI_1 小刀",
            )
            self.assertTrue(mask[command_to_action_idx(
                "attack AI_1 小刀", player, env._state,
            )])
            focused, target_id, kind = _postkill_melee_skill_mask(
                mask, player, env._state,
            )
            self.assertEqual((target_id, kind), ("ai_1", "melee"))
            self.assertEqual(
                idx_to_command(int(focused.nonzero()[0][0]), player, env._state),
                "attack AI_1 小刀",
            )
            player.hp = 1
            focused, target_id, kind = _postkill_melee_skill_mask(
                mask, player, env._state,
            )
            self.assertIsNone(target_id)
            self.assertIsNone(kind)
            np.testing.assert_array_equal(focused, mask)
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                env.close()

    def test_pp_damage_course_requires_modifier_then_closes_lethal_hit(self):
        from rl.action_space import command_to_action_idx, idx_to_command
        from rl.env import BadtimeWarEnv

        env = BadtimeWarEnv(
            num_opponents=1, max_rounds=8,
            enable_talents=False, rl_talent=0,
            action_contract="pp_course",
            course_scenario="pp_damage_finish",
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                env.reset(seed=29)
            player = env._rl_player
            target = env._state.get_player("ai_0")
            self.assertEqual(env._state.m9_pp.balance("rl_0"), 1)
            self.assertEqual(target.hp, 5)

            def commands():
                return {
                    idx_to_command(int(idx), player, env._state)
                    for idx in np.flatnonzero(env.action_masks())
                }

            self.assertIn("special PP加伤", commands())
            self.assertNotIn("attack AI_0 小刀", commands())
            self.assertFalse(any(cmd.startswith("special 交易") for cmd in commands()))

            def step(command: str):
                action = command_to_action_idx(command, player, env._state)
                self.assertGreaterEqual(action, 0, command)
                self.assertTrue(env.action_masks()[action], command)
                with contextlib.redirect_stdout(io.StringIO()):
                    return env.step(action)

            step("special PP加伤")
            self.assertEqual(env._state.m9_pp.balance("rl_0"), 0)
            self.assertEqual(player._m9_pp_damage_bonus, 1)
            self.assertIn("attack AI_0 小刀", commands())
            self.assertNotIn("attack AI_0 拳击", commands())
            _obs, _reward, terminated, truncated, _info = step(
                "attack AI_0 小刀",
            )
            self.assertTrue(terminated or truncated)
            self.assertEqual(player.kill_count, 1)
            self.assertFalse(target.is_alive())
            self.assertEqual(getattr(player, "_m9_pp_damage_bonus", 0), 0)
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                env.close()

    def test_economy_integration_contract_keeps_full_opening_and_narrow_pursuit(self):
        from rl.action_space import apply_action_contract, build_action_mask
        from rl.env import BadtimeWarEnv

        for scenario, expected_contract in (
            (None, "combat_economy"),
            ("economy_armored_pursuit_supply", "economy_pursuit_drill"),
            ("economy_armored_pursuit_supply_slot0", "economy_pursuit_drill"),
        ):
            with self.subTest(scenario=scenario):
                env = BadtimeWarEnv(
                    num_opponents=2, max_rounds=40,
                    enable_talents=False, rl_talent=0,
                    action_contract="economy_integration_course",
                    course_scenario=scenario,
                )
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        env.reset(seed=48)
                    raw = build_action_mask(
                        env._rl_player, env._state, "rl_0",
                        choose_mode=env._choose_mode,
                        choose_situation=env._choose_context.get("situation", ""),
                        choose_options=(
                            env._choose_options if env._choose_mode else None
                        ),
                    )
                    expected = apply_action_contract(
                        raw, expected_contract, choose_mode=env._choose_mode,
                    )
                    np.testing.assert_array_equal(env.action_masks(), expected)
                finally:
                    with contextlib.redirect_stdout(io.StringIO()):
                        env.close()

    def test_pp_damage_choice_makes_unbuffed_hit_lose_and_buffed_hit_win(self):
        from rl.action_space import command_to_action_idx, idx_to_command
        from rl.env import BadtimeWarEnv

        def make_env():
            return BadtimeWarEnv(
                num_opponents=1, max_rounds=4,
                enable_talents=False, rl_talent=0,
                action_contract="pp_course",
                course_scenario="pp_damage_choice",
            )

        def step(env, command: str):
            action = command_to_action_idx(
                command, env._rl_player, env._state,
            )
            self.assertGreaterEqual(action, 0, command)
            self.assertTrue(env.action_masks()[action], command)
            with contextlib.redirect_stdout(io.StringIO()):
                return env.step(action)

        losing = make_env()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                losing.reset(seed=30)
            commands = {
                idx_to_command(int(idx), losing._rl_player, losing._state)
                for idx in np.flatnonzero(losing.action_masks())
            }
            self.assertIn("special PP加伤", commands)
            self.assertIn("attack AI_0 小刀", commands)
            _obs, _reward, terminated, truncated, _info = step(
                losing, "attack AI_0 小刀",
            )
            self.assertTrue(terminated or truncated)
            self.assertFalse(losing._rl_player.is_alive())
            self.assertTrue(losing._state.get_player("ai_0").is_alive())
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                losing.close()

        winning = make_env()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                winning.reset(seed=31)
            step(winning, "special PP加伤")
            _obs, _reward, terminated, truncated, _info = step(
                winning, "attack AI_0 小刀",
            )
            self.assertTrue(terminated or truncated)
            self.assertTrue(winning._rl_player.is_alive())
            self.assertEqual(winning._rl_player.kill_count, 1)
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                winning.close()

    def test_pp_skill_router_only_focuses_unambiguous_lethal_window(self):
        from rl.action_space import command_to_action_idx, idx_to_command
        from rl.env import BadtimeWarEnv
        from tools.rung0_chain_probe import _pp_damage_skill_mask

        env = BadtimeWarEnv(
            num_opponents=1, max_rounds=4,
            enable_talents=False, rl_talent=0,
            action_contract=None, course_scenario="pp_damage_choice",
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                env.reset(seed=32)
            target_player = env._state.get_player("ai_0")
            target_player.inner_defense = {"普通": 1}
            original = env.action_masks()
            focused, target = _pp_damage_skill_mask(
                original, env._rl_player, env._state,
            )
            self.assertIsNone(target)
            np.testing.assert_array_equal(focused, original)
            target_player.inner_defense = {}

            focused, target = _pp_damage_skill_mask(
                env.action_masks(), env._rl_player, env._state,
            )
            commands = {
                idx_to_command(int(idx), env._rl_player, env._state)
                for idx in np.flatnonzero(focused)
            }
            self.assertEqual(target, "ai_0")
            self.assertEqual(commands, {"special PP加伤"})

            action = command_to_action_idx(
                "special PP加伤", env._rl_player, env._state,
            )
            with contextlib.redirect_stdout(io.StringIO()):
                env.step(action)
            focused, target = _pp_damage_skill_mask(
                env.action_masks(), env._rl_player, env._state,
            )
            commands = {
                idx_to_command(int(idx), env._rl_player, env._state)
                for idx in np.flatnonzero(focused)
            }
            self.assertEqual(target, "ai_0")
            self.assertEqual(commands, {"attack AI_0 小刀"})
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                env.close()

    def test_survival_heal_course_requires_heal_then_two_kills(self):
        from rl.action_space import command_to_action_idx, idx_to_command
        from rl.env import BadtimeWarEnv
        from tools.rung0_chain_probe import _oracle_action

        def make_env():
            return BadtimeWarEnv(
                num_opponents=2, max_rounds=14,
                enable_talents=False, rl_talent=0,
                action_contract="survival_course",
                course_scenario="survival_heal_counterattack",
            )

        def step_command(env, command: str):
            action = command_to_action_idx(
                command, env._rl_player, env._state,
            )
            self.assertGreaterEqual(action, 0, command)
            self.assertTrue(env.action_masks()[action], command)
            with contextlib.redirect_stdout(io.StringIO()):
                return env.step(action)

        losing = make_env()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                losing.reset(seed=33)
            commands = {
                idx_to_command(int(idx), losing._rl_player, losing._state)
                for idx in np.flatnonzero(losing.action_masks())
            }
            self.assertIn("interact 治疗", commands)
            self.assertIn("attack AI_0 小刀", commands)
            _obs, _reward, terminated, truncated, _info = step_command(
                losing, "attack AI_0 小刀",
            )
            self.assertTrue(terminated or truncated)
            self.assertFalse(losing._rl_player.is_alive())
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                losing.close()

        winning = make_env()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                obs, _ = winning.reset(seed=34)
            step_command(winning, "interact 治疗")
            self.assertEqual((winning._rl_player.hp, winning._rl_player.credits), (6, 0))
            done = False
            info = {}
            for _ in range(20):
                action = _oracle_action(
                    winning.action_masks(), winning._rl_player, winning._state,
                )
                with contextlib.redirect_stdout(io.StringIO()):
                    obs, _reward, terminated, truncated, info = winning.step(action)
                done = terminated or truncated
                if done:
                    break
            self.assertTrue(done)
            self.assertEqual(winning._rl_player.kill_count, 2)
            self.assertTrue(winning._rl_player.is_alive())
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                winning.close()

    def test_survival_router_only_focuses_a_lethal_but_healable_hit(self):
        from rl.action_space import idx_to_command
        from rl.env import BadtimeWarEnv
        from tools.rung0_chain_probe import _survival_heal_skill_mask

        env = BadtimeWarEnv(
            num_opponents=2, max_rounds=14,
            enable_talents=False, rl_talent=0,
            action_contract="survival_course",
            course_scenario="survival_heal_counterattack",
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                env.reset(seed=35)
            original = env.action_masks()
            focused, threat = _survival_heal_skill_mask(
                original, env._rl_player, env._state,
            )
            commands = {
                idx_to_command(int(idx), env._rl_player, env._state)
                for idx in np.flatnonzero(focused)
            }
            self.assertEqual(threat, "ai_0")
            self.assertEqual(commands, {"interact 治疗"})

            env._rl_player.hp = 5
            unfocused, threat = _survival_heal_skill_mask(
                original, env._rl_player, env._state,
            )
            self.assertIsNone(threat)
            np.testing.assert_array_equal(unfocused, original)
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                env.close()

    def test_postkill_recovery_router_sequences_work_and_heal(self):
        from rl.action_space import idx_to_command
        from rl.env import BadtimeWarEnv
        from tools.rung0_chain_probe import _postkill_recovery_skill_mask

        env = BadtimeWarEnv(
            num_opponents=2, max_rounds=30,
            enable_talents=False, rl_talent=0,
            action_contract=None,
            course_scenario="economy_pursuit_supply",
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                env.reset(seed=36)
            player = env._rl_player
            player.hp = 6

            def focused_command():
                focused, reason = _postkill_recovery_skill_mask(
                    env.action_masks(), player, env._state,
                )
                commands = {
                    idx_to_command(int(idx), player, env._state)
                    for idx in np.flatnonzero(focused)
                }
                return commands, reason

            self.assertEqual(focused_command(), ({"move 商店"}, "postkill_recovery"))
            player.location = "商店"
            self.assertEqual(focused_command(), ({"interact 打工"}, "postkill_recovery"))
            player.credits = 2
            self.assertEqual(focused_command(), ({"move 医院"}, "postkill_recovery"))
            target = env._state.get_player("ai_1")
            target.location = "医院"
            focused, reason = _postkill_recovery_skill_mask(
                env.action_masks(), player, env._state,
            )
            self.assertIsNone(reason)
            np.testing.assert_array_equal(focused, env.action_masks())
            target.location = f"home_{target.player_id}"
            player.location = "医院"
            self.assertEqual(focused_command(), ({"interact 治疗"}, "postkill_recovery"))
            player.hp = 11
            focused, reason = _postkill_recovery_skill_mask(
                env.action_masks(), player, env._state,
            )
            self.assertIsNone(reason)
            np.testing.assert_array_equal(focused, env.action_masks())
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                env.close()

    def test_survival_armor_course_converts_lethal_hit_then_two_kills(self):
        from rl.action_space import command_to_action_idx, idx_to_command
        from rl.env import BadtimeWarEnv
        from tools.rung0_chain_probe import (
            _oracle_action, _survival_armor_skill_mask,
        )

        def make_env():
            return BadtimeWarEnv(
                num_opponents=2, max_rounds=14,
                enable_talents=False, rl_talent=0,
                action_contract="survival_course",
                course_scenario="survival_armor_counterattack",
            )

        def step(env, command: str):
            action = command_to_action_idx(
                command, env._rl_player, env._state,
            )
            self.assertGreaterEqual(action, 0, command)
            self.assertTrue(env.action_masks()[action], command)
            with contextlib.redirect_stdout(io.StringIO()):
                return env.step(action)

        losing = make_env()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                losing.reset(seed=36)
            _obs, _reward, terminated, truncated, _info = step(
                losing, "attack AI_0 小刀",
            )
            self.assertTrue(terminated or truncated)
            self.assertFalse(losing._rl_player.is_alive())
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                losing.close()

        winning = make_env()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                winning.reset(seed=37)
            focused, threat = _survival_armor_skill_mask(
                winning.action_masks(), winning._rl_player, winning._state,
            )
            commands = {
                idx_to_command(int(idx), winning._rl_player, winning._state)
                for idx in np.flatnonzero(focused)
            }
            self.assertEqual(threat, "ai_0")
            self.assertEqual(commands, {"interact 陶瓷护甲"})
            step(winning, "interact 陶瓷护甲")
            self.assertTrue(winning._rl_player.is_alive())
            self.assertEqual(winning._rl_player.credits, 0)
            done = False
            for _ in range(20):
                action = _oracle_action(
                    winning.action_masks(), winning._rl_player, winning._state,
                )
                with contextlib.redirect_stdout(io.StringIO()):
                    _obs, _reward, terminated, truncated, _info = winning.step(action)
                done = terminated or truncated
                if done:
                    break
            self.assertTrue(done)
            self.assertEqual(winning._rl_player.kill_count, 2)
            self.assertTrue(winning._rl_player.is_alive())
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                winning.close()

    def test_survival_repair_course_requires_maintenance_for_two_kills(self):
        from rl.action_space import command_to_action_idx, idx_to_command
        from rl.env import BadtimeWarEnv
        from tools.rung0_chain_probe import (
            _oracle_action, _survival_repair_skill_mask,
        )

        def make_env():
            return BadtimeWarEnv(
                num_opponents=2, max_rounds=16,
                enable_talents=False, rl_talent=0,
                action_contract="survival_course",
                course_scenario="survival_armor_repair",
            )

        def step(env, command: str):
            action = command_to_action_idx(command, env._rl_player, env._state)
            self.assertGreaterEqual(action, 0, command)
            self.assertTrue(env.action_masks()[action], command)
            with contextlib.redirect_stdout(io.StringIO()):
                return env.step(action)

        losing = make_env()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                losing.reset(seed=38)
            step(losing, "attack AI_0 小刀")
            _obs, _reward, terminated, truncated, _info = step(
                losing, "attack AI_0 小刀",
            )
            self.assertTrue(terminated or truncated)
            self.assertFalse(losing._rl_player.is_alive())
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                losing.close()

        winning = make_env()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                winning.reset(seed=39)
            original = winning.action_masks()
            focused, threat = _survival_repair_skill_mask(
                original, winning._rl_player, winning._state,
            )
            commands = {
                idx_to_command(int(idx), winning._rl_player, winning._state)
                for idx in np.flatnonzero(focused)
            }
            self.assertEqual(threat, "ai_0")
            self.assertEqual(commands, {"interact 修理陶瓷护甲"})

            ceramic = next(
                piece for piece in winning._rl_player.armor.get_all_active()
                if piece.name == "陶瓷护甲"
            )
            ceramic.durability = 7
            unfocused, threat = _survival_repair_skill_mask(
                original, winning._rl_player, winning._state,
            )
            self.assertIsNone(threat)
            np.testing.assert_array_equal(unfocused, original)
            ceramic.durability = 1

            step(winning, "interact 修理陶瓷护甲")
            self.assertEqual((ceramic.durability, winning._rl_player.credits), (4, 0))
            done = False
            for _ in range(24):
                action = _oracle_action(
                    winning.action_masks(), winning._rl_player, winning._state,
                )
                with contextlib.redirect_stdout(io.StringIO()):
                    _obs, _reward, terminated, truncated, _info = winning.step(action)
                done = terminated or truncated
                if done:
                    break
            self.assertTrue(done)
            self.assertEqual(winning._rl_player.kill_count, 2)
            self.assertTrue(winning._rl_player.is_alive())
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                winning.close()

    def test_armor_repair_increases_potential_by_restored_durability(self):
        from rl.env import BadtimeWarEnv
        from rl.reward import ARMOR_DURABILITY_VALUE, potential

        env = BadtimeWarEnv(
            num_opponents=2, max_rounds=16,
            enable_talents=False, rl_talent=0,
            action_contract="survival_course",
            course_scenario="survival_armor_repair",
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                env.reset(seed=40)
            ceramic = next(
                piece for piece in env._rl_player.armor.get_all_active()
                if piece.name == "陶瓷护甲"
            )
            before = potential(env._rl_player, env._state)
            ceramic.durability += 6
            after = potential(env._rl_player, env._state)
            self.assertEqual(after - before, 6 * ARMOR_DURABILITY_VALUE)
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                env.close()

    def test_sharpen_course_amortizes_upgrade_across_two_targets(self):
        from rl.env import BadtimeWarEnv
        from rl.action_space import idx_to_command
        from tools.rung0_chain_probe import (
            _oracle_action, _sharpen_oracle_action, _sharpen_skill_mask,
        )

        def run(policy):
            env = BadtimeWarEnv(
                num_opponents=2, max_rounds=9,
                enable_talents=False, rl_talent=0,
                action_contract="equipment_course",
                course_scenario="equipment_sharpen_chain",
            )
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    env.reset(seed=41)
                for _ in range(16):
                    action = policy(env.action_masks(), env._rl_player, env._state)
                    with contextlib.redirect_stdout(io.StringIO()):
                        _obs, _reward, terminated, truncated, _info = env.step(action)
                    if terminated or truncated:
                        break
                knife = next(
                    weapon for weapon in env._rl_player.weapons
                    if weapon.name == "小刀"
                )
                return env._rl_player.kill_count, knife.base_damage, env._state.winner
            finally:
                with contextlib.redirect_stdout(io.StringIO()):
                    env.close()

        plain_kills, plain_damage, plain_winner = run(_oracle_action)
        sharp_kills, sharp_damage, sharp_winner = run(_sharpen_oracle_action)
        self.assertLess(plain_kills, 2)
        self.assertEqual(plain_damage, 4)
        self.assertNotEqual(plain_winner, "rl_0")
        self.assertEqual((sharp_kills, sharp_damage), (2, 7))
        self.assertEqual(sharp_winner, "rl_0")

        env = BadtimeWarEnv(
            num_opponents=2, max_rounds=9,
            enable_talents=False, rl_talent=0,
            action_contract="equipment_course",
            course_scenario="equipment_sharpen_chain",
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                env.reset(seed=42)
            base_mask = env.action_masks()
            focused, kind = _sharpen_skill_mask(
                base_mask, env._rl_player, env._state,
            )
            self.assertEqual(kind, "buy")
            buy_idx = int(next(iter(np.flatnonzero(focused))))
            self.assertEqual(
                idx_to_command(buy_idx, env._rl_player, env._state),
                "interact 磨刀石",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                env.step(buy_idx)
            sharpen_mask, kind = _sharpen_skill_mask(
                env.action_masks(), env._rl_player, env._state,
            )
            self.assertEqual(kind, "sharpen")
            sharpen_idx = int(next(iter(np.flatnonzero(sharpen_mask))))
            self.assertEqual(
                idx_to_command(sharpen_idx, env._rl_player, env._state),
                "special 磨刀",
            )
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                env.close()

        one_target = BadtimeWarEnv(
            num_opponents=2, max_rounds=9,
            enable_talents=False, rl_talent=0,
            action_contract="equipment_course",
            course_scenario="equipment_sharpen_chain",
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                one_target.reset(seed=43)
            one_target._state.get_player("ai_1").hp = 0
            original = one_target.action_masks()
            focused, kind = _sharpen_skill_mask(
                original, one_target._rl_player, one_target._state,
            )
            self.assertIsNone(kind)
            np.testing.assert_array_equal(focused, original)
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                one_target.close()

    def test_thermal_course_unlocks_hidden_target_without_false_positive(self):
        from rl.action_space import idx_to_command
        from rl.env import BadtimeWarEnv
        from tools.rung0_chain_probe import (
            _oracle_action, _thermal_oracle_action, _thermal_skill_mask,
        )

        def run(policy):
            env = BadtimeWarEnv(
                num_opponents=2, max_rounds=8,
                enable_talents=False, rl_talent=0,
                action_contract="equipment_course",
                course_scenario="visibility_thermal_chain",
            )
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    env.reset(seed=44)
                first_mask = env.action_masks()
                focused, target_id = _thermal_skill_mask(
                    first_mask, env._rl_player, env._state,
                )
                self.assertEqual(target_id, "ai_0")
                thermal_idx = int(next(iter(np.flatnonzero(focused))))
                self.assertEqual(
                    idx_to_command(thermal_idx, env._rl_player, env._state),
                    "interact 热成像仪",
                )
                for _ in range(12):
                    action = policy(env.action_masks(), env._rl_player, env._state)
                    with contextlib.redirect_stdout(io.StringIO()):
                        _obs, _reward, terminated, truncated, _info = env.step(action)
                    if terminated or truncated:
                        break
                return env._rl_player.kill_count
            finally:
                with contextlib.redirect_stdout(io.StringIO()):
                    env.close()

        self.assertLess(run(_oracle_action), 2)
        self.assertEqual(run(_thermal_oracle_action), 2)

        clear = BadtimeWarEnv(
            num_opponents=2, max_rounds=8,
            enable_talents=False, rl_talent=0,
            action_contract="equipment_course",
            course_scenario="visibility_thermal_chain",
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                clear.reset(seed=45)
            target = clear._state.get_player("ai_0")
            target.is_invisible = False
            target.stealth_attrs.clear()
            clear._state.markers.on_player_lose_invisible(target.player_id)
            original = clear.action_masks()
            focused, target_id = _thermal_skill_mask(
                original, clear._rl_player, clear._state,
            )
            self.assertIsNone(target_id)
            np.testing.assert_array_equal(focused, original)
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                clear.close()

    def test_magic_and_tech_detection_courses_use_matching_detectors(self):
        from rl.action_space import idx_to_command
        from rl.env import BadtimeWarEnv
        from tools.rung0_chain_probe import (
            _detection_oracle_action, _detection_skill_mask,
        )

        cases = {
            "visibility_magic_chain": (
                "魔法", "interact 探测魔法", "move 魔法所",
            ),
            "visibility_tech_chain": (
                "科技", "interact 雷达", "move 军事基地",
            ),
        }
        for scenario, (
            expected_attr, expected_command, expected_move,
        ) in cases.items():
            with self.subTest(scenario=scenario):
                env = BadtimeWarEnv(
                    num_opponents=2, max_rounds=8,
                    enable_talents=False, rl_talent=0,
                    action_contract="equipment_course",
                    course_scenario=scenario,
                )
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        env.reset(seed=46)
                    focused, target_id, attr = _detection_skill_mask(
                        env.action_masks(), env._rl_player, env._state,
                    )
                    self.assertEqual((target_id, attr), ("ai_0", expected_attr))
                    action = int(next(iter(np.flatnonzero(focused))))
                    self.assertEqual(
                        idx_to_command(action, env._rl_player, env._state),
                        expected_command,
                    )
                    original_kills = env._rl_player.kill_count
                    env._rl_player.kill_count = 1
                    env._rl_player.location = "商店"
                    focused, target_id, attr = _detection_skill_mask(
                        env.action_masks(), env._rl_player, env._state,
                    )
                    self.assertEqual((target_id, attr), ("ai_0", expected_attr))
                    action = int(next(iter(np.flatnonzero(focused))))
                    self.assertEqual(
                        idx_to_command(action, env._rl_player, env._state),
                        expected_move,
                    )
                    env._rl_player.location = (
                        "魔法所" if expected_attr == "魔法" else "军事基地"
                    )
                    env._rl_player.kill_count = original_kills
                    for _ in range(12):
                        action = _detection_oracle_action(
                            env.action_masks(), env._rl_player, env._state,
                        )
                        with contextlib.redirect_stdout(io.StringIO()):
                            _obs, _reward, terminated, truncated, _info = env.step(action)
                        if terminated or truncated:
                            break
                    self.assertEqual(env._rl_player.kill_count, 2)
                    self.assertIn(expected_attr, env._rl_player.detection_attrs)
                finally:
                    with contextlib.redirect_stdout(io.StringIO()):
                        env.close()

    def test_detection_router_unlocks_opening_only_without_visible_attack(self):
        from rl.action_space import idx_to_command
        from rl.env import BadtimeWarEnv
        from tools.rung0_chain_probe import _detection_skill_mask

        env = BadtimeWarEnv(
            num_opponents=2, max_rounds=8,
            enable_talents=False, rl_talent=0,
            action_contract="combat_economy",
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                env.reset(seed=47)
            player = env._rl_player
            player.location = "商店"
            for target in (
                env._state.get_player("ai_0"),
                env._state.get_player("ai_1"),
            ):
                target.is_invisible = True
                target.grant_visibility_item("隐身术")
                env._state.markers.on_player_go_invisible(
                    target.player_id, list(env._state.players.values()),
                )

            focused, target_id, attr = _detection_skill_mask(
                env.action_masks(), player, env._state,
            )
            self.assertIsNotNone(target_id)
            self.assertEqual(attr, "魔法")
            action = int(next(iter(np.flatnonzero(focused))))
            self.assertEqual(
                idx_to_command(action, player, env._state), "move 魔法所",
            )

            visible = env._state.get_player("ai_1")
            visible.is_invisible = False
            visible.stealth_attrs.clear()
            env._state.markers.on_player_lose_invisible(visible.player_id)
            original = env.action_masks()
            self.assertTrue(any(
                idx_to_command(int(idx), player, env._state).startswith("shoot ")
                for idx in np.flatnonzero(original)
            ))
            focused, target_id, attr = _detection_skill_mask(
                original, player, env._state,
            )
            self.assertIsNone(target_id)
            self.assertIsNone(attr)
            np.testing.assert_array_equal(focused, original)
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                env.close()

    def test_attribute_armor_course_leaves_weapon_choice_to_policy(self):
        from rl.action_space import idx_to_command
        from rl.env import BadtimeWarEnv
        from tools.rung0_chain_probe import (
            _attribute_armor_oracle_action, _attribute_armor_skill_mask,
        )

        expected = {
            "attribute_magic_armor_choice": "attack AI_0 高斯步枪",
            "attribute_tech_armor_choice": "attack AI_0 魔法弹幕",
        }
        for scenario, expected_command in expected.items():
            with self.subTest(scenario=scenario):
                env = BadtimeWarEnv(
                    num_opponents=1, max_rounds=4,
                    enable_talents=False, rl_talent=0,
                    action_contract="equipment_course",
                    course_scenario=scenario,
                )
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        env.reset(seed=47)
                    original = env.action_masks()
                    focused, target_id = _attribute_armor_skill_mask(
                        original, env._rl_player, env._state,
                    )
                    self.assertEqual(target_id, "ai_0")
                    focused_commands = {
                        idx_to_command(int(idx), env._rl_player, env._state)
                        for idx in np.flatnonzero(focused)
                    }
                    self.assertEqual(focused_commands, {
                        "attack AI_0 魔法弹幕", "attack AI_0 高斯步枪",
                    })
                    action = _attribute_armor_oracle_action(
                        focused, env._rl_player, env._state,
                    )
                    self.assertEqual(
                        idx_to_command(action, env._rl_player, env._state),
                        expected_command,
                    )
                    with contextlib.redirect_stdout(io.StringIO()):
                        env.step(action)
                    self.assertEqual(env._rl_player.kill_count, 1)
                finally:
                    with contextlib.redirect_stdout(io.StringIO()):
                        env.close()

    def test_unified_bow_loadout_course_covers_six_visible_module_needs(self):
        from controllers.ai_basic import BasicAIController
        from engine.bow_modules import module_specs
        from rl.action_space import idx_to_command
        from rl.env import BadtimeWarEnv
        from rl.obs_builder_m9 import M9_BOW_CONTEXT_BASE, M9_BOW_CONTEXT_DIM
        from tools.rung0_chain_probe import _bow_loadout_oracle_action

        # These seeds are a compact deterministic cover after player-order
        # shuffling has consumed the first RNG draws.
        seed_by_module = {
            "火矢": 0, "力量": 1, "无限": 4,
            "穿甲": 6, "冲击": 11, "魔导": 19,
        }
        observations = {}
        for expected_module, seed in seed_by_module.items():
            with self.subTest(module=expected_module):
                env = BadtimeWarEnv(
                    num_opponents=2, max_rounds=40,
                    enable_talents=False, rl_talent=0,
                    action_contract="equipment_course",
                    course_scenario="equipment_bow_loadout",
                )
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        obs, _ = env.reset(seed=seed)
                    player = env._rl_player
                    survivor = env._state.get_player("ai_1")
                    self.assertEqual(
                        env._state.rl_bow_loadout_target_module,
                        expected_module,
                    )
                    self.assertEqual(player.kill_count, 1)
                    self.assertIsInstance(survivor.controller, BasicAIController)
                    bow_slice = obs[
                        M9_BOW_CONTEXT_BASE:
                        M9_BOW_CONTEXT_BASE + M9_BOW_CONTEXT_DIM
                    ]
                    self.assertTrue(np.any(bow_slice != 0.0))
                    observations[expected_module] = obs.copy()

                    location = module_specs()[expected_module]["location"]
                    self.assertNotEqual(player.location, location)
                    action = _bow_loadout_oracle_action(
                        env.action_masks(), player, env._state,
                    )
                    self.assertEqual(
                        idx_to_command(action, player, env._state),
                        f"move {location}",
                    )

                    # At the facility, both paired alternatives remain legal;
                    # the teacher selects the state-conditioned answer rather
                    # than receiving a one-action mask that leaks the label.
                    player.location = location
                    legal_commands = {
                        idx_to_command(int(idx), player, env._state)
                        for idx in np.flatnonzero(env.action_masks())
                    }
                    action = _bow_loadout_oracle_action(
                        env.action_masks(), player, env._state,
                    )
                    self.assertIn(f"interact {expected_module}模块", legal_commands)
                    self.assertEqual(
                        idx_to_command(action, player, env._state),
                        f"interact {expected_module}模块",
                    )
                    self.assertEqual(
                        env._reward_tracker._terminal_reward(env._state, "rl_0"),
                        -100.0,
                    )
                finally:
                    with contextlib.redirect_stdout(io.StringIO()):
                        env.close()

        self.assertEqual(set(observations), set(seed_by_module))
        reference = observations["力量"]
        for module_name, obs in observations.items():
            if module_name != "力量":
                self.assertFalse(np.array_equal(reference, obs), module_name)

    def test_natural_bow_router_uses_observable_postkill_needs_only(self):
        from engine.bow_modules import module_specs
        from rl.action_space import idx_to_command
        from rl.env import BadtimeWarEnv
        from tools.rung0_chain_probe import _natural_bow_loadout_skill_mask

        seed_by_module = {"火矢": 0, "无限": 4, "穿甲": 6}
        for expected_module, seed in seed_by_module.items():
            with self.subTest(module=expected_module):
                env = BadtimeWarEnv(
                    num_opponents=2, max_rounds=40,
                    enable_talents=False, rl_talent=0,
                    action_contract="equipment_course",
                    course_scenario="equipment_bow_loadout",
                )
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        env.reset(seed=seed)
                    player = env._rl_player
                    state = env._state
                    # Prove the natural router does not consult the curriculum
                    # answer used to construct this compact deterministic state.
                    delattr(state, "rl_bow_loadout_target_module")
                    original = env.action_masks()
                    focused, target_id, module_name = (
                        _natural_bow_loadout_skill_mask(
                            original, player, state,
                        )
                    )
                    self.assertEqual((target_id, module_name),
                                     ("ai_1", expected_module))
                    commands = {
                        idx_to_command(int(idx), player, state)
                        for idx in np.flatnonzero(focused)
                    }
                    # Ownership is broad: the expert, not the router, must
                    # choose the correct facility from several legal moves.
                    destination = module_specs()[expected_module]["location"]
                    expected_command = (
                        f"interact {expected_module}模块"
                        if player.location == destination
                        else f"move {destination}"
                    )
                    self.assertIn(expected_command, commands)
                    self.assertGreaterEqual(len(commands), 4)

                    player.kill_count = 0
                    _, target_id, module_name = (
                        _natural_bow_loadout_skill_mask(
                            original, player, state,
                        )
                    )
                    self.assertIsNone(target_id)
                    self.assertIsNone(module_name)
                finally:
                    with contextlib.redirect_stdout(io.StringIO()):
                        env.close()

        # Piercing is not reachable without the observable military pass.
        env = BadtimeWarEnv(
            num_opponents=2, max_rounds=40,
            enable_talents=False, rl_talent=0,
            action_contract="equipment_course",
            course_scenario="equipment_bow_loadout",
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                env.reset(seed=6)
            delattr(env._state, "rl_bow_loadout_target_module")
            env._rl_player.has_military_pass = False
            _, target_id, module_name = _natural_bow_loadout_skill_mask(
                env.action_masks(), env._rl_player, env._state,
            )
            self.assertIsNone(target_id)
            self.assertIsNone(module_name)
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                env.close()


    def test_combat_core_side_choices_use_neutral_defaults(self):
        from rl.env import _combat_core_default_choice

        self.assertEqual(_combat_core_default_choice(["押注", "不押注"]), "不押注")
        self.assertEqual(
            _combat_core_default_choice(["报名公演", "保留"]), "保留",
        )
        self.assertEqual(_combat_core_default_choice(["是", "否"]), "否")
        self.assertEqual(_combat_core_default_choice(["AI_0", "AI_1"]), "AI_0")


if __name__ == "__main__":
    unittest.main()
