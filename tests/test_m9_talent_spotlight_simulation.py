"""M9 十四天赋抽象聚光推演的结构回归测试。"""

from __future__ import annotations

import unittest

from tools.simulate_m9_talent_spotlights import (
    G7_COMMAND_COSTS,
    TALENT_ORDER,
    aggregate,
    aggregate_mixed,
    enumerate_g7_macros,
    resolve_t5_chart,
    terror_attack_capacity,
)


class TalentSpotlightSimulationTest(unittest.TestCase):
    """验证纸面模型不会重新产生发动饥荒或演出爆量。"""

    def test_six_player_recurring_frequency_stays_bounded(self) -> None:
        """六人三十轮的可重复演出应落在结构目标内。"""

        one_shots = {"T7", "G2", "G4"}
        for index, talent in enumerate(TALENT_ORDER):
            result = aggregate(
                talent=talent,
                players=6,
                rounds=30,
                trials=250,
                event_probability=0.5,
                seed=20260805 + index * 10_000,
            )
            if talent in one_shots:
                continue
            self.assertGreaterEqual(result.uses_mean, 2.0, talent)
            self.assertLessEqual(result.uses_mean, 6.0, talent)
            self.assertLessEqual(result.impact_mean, 9.0, talent)

    def test_one_shot_shapes_have_expected_windows(self) -> None:
        """G2、T7 和 G4 应在长短局中呈现不同兑现概率。"""

        g2 = aggregate("G2", 6, 30, 500, 0.5, 20260805)
        t7_short = aggregate("T7", 6, 15, 500, 0.5, 20260805)
        t7_main = aggregate("T7", 6, 30, 500, 0.5, 20260805)
        g4_short = aggregate("G4", 6, 15, 500, 0.5, 20260805)
        g4_main = aggregate("G4", 6, 30, 500, 0.5, 20260805)

        self.assertGreaterEqual(g2.stage_start_mean, 18.0)
        self.assertLessEqual(g2.stage_start_mean, 22.0)
        self.assertGreaterEqual(t7_short.payoff_probability, 0.30)
        self.assertLessEqual(t7_short.payoff_probability, 0.65)
        self.assertGreaterEqual(t7_main.payoff_probability, 0.75)
        self.assertGreaterEqual(g4_short.payoff_probability, 0.20)
        self.assertLessEqual(g4_short.payoff_probability, 0.55)
        self.assertGreaterEqual(g4_main.payoff_probability, 0.70)

    def test_g5_player_count_scaling_preserves_short_duel_payoff(self) -> None:
        """二人短局应有机会完成一次完整涟漪，六人主窗仍不爆量。"""

        duel_without_applause = aggregate(
            "G5", 2, 15, 800, 0.5, 20260805, applause_probability=0.0
        )
        duel_regular = aggregate(
            "G5", 2, 15, 800, 0.5, 20260805, applause_probability=0.18
        )
        duel_saturated = aggregate(
            "G5", 2, 15, 800, 0.5, 20260805, applause_probability=1.0
        )
        main_saturated = aggregate(
            "G5", 6, 30, 800, 0.5, 20260805, applause_probability=1.0
        )

        self.assertGreaterEqual(duel_without_applause.payoff_probability, 0.60)
        self.assertLessEqual(duel_without_applause.payoff_probability, 0.85)
        self.assertGreaterEqual(duel_regular.payoff_probability, 0.90)
        self.assertLessEqual(
            duel_saturated.uses_mean - duel_regular.uses_mean,
            0.25,
        )
        self.assertGreaterEqual(main_saturated.payoff_probability, 0.90)
        self.assertLessEqual(main_saturated.uses_mean, 2.75)

    def test_three_candidate_handoff_repairs_high_yield_roster(self) -> None:
        """三人顺延应显著降低高让位阵容的全局空窗。"""

        roster = ("T1", "T3", "G2", "G3", "G5", "T6")
        fixed = aggregate_mixed(
            roster,
            30,
            700,
            0.5,
            20260805,
            r0_handoff=False,
        )
        handoff = aggregate_mixed(
            roster,
            30,
            700,
            0.5,
            20260805,
            r0_handoff=True,
            handoff_limit=3,
        )

        self.assertLess(fixed.utilization, 0.65)
        self.assertGreater(handoff.utilization, 0.75)
        self.assertGreater(handoff.utilization, fixed.utilization + 0.20)
        self.assertLessEqual(handoff.max_dry_streak_p95, 4.0)

    def test_g7_macro_and_terror_have_hard_output_caps(self) -> None:
        """临时 Cost 不能变成多次攻击，Terror 也必须以生命换波数。"""

        macros = enumerate_g7_macros(budget=4)
        self.assertTrue(macros)
        self.assertLessEqual(max(value for _, value in macros), 2.20)
        for commands, _ in macros:
            self.assertLessEqual(len(commands), 4)
            self.assertLessEqual(
                sum(G7_COMMAND_COSTS[command] for command in commands),
                4,
            )
            self.assertLessEqual(
                sum(command in {"shoot", "throw"} for command in commands),
                1,
            )

        self.assertEqual(terror_attack_capacity(4.5), 3)
        self.assertEqual(terror_attack_capacity(7.5), 5)
        self.assertEqual(terror_attack_capacity(7.5, 1.0), 3)

    def test_t5_spotlight_can_repair_clear_but_never_create_fc(self) -> None:
        """Miss 救为 Good 只改变 Clear 路径，FC 仍要求全 Perfect。"""

        self.assertEqual(resolve_t5_chart(("P", "P"), True), "fc")
        self.assertEqual(resolve_t5_chart(("P", "G"), True), "clear")
        self.assertEqual(resolve_t5_chart(("P", "M"), True), "clear")
        self.assertEqual(resolve_t5_chart(("P", "M", "M"), True), "partial")
        self.assertNotEqual(resolve_t5_chart(("P", "M"), True), "fc")

        duel = aggregate("T5", 2, 15, 800, 0.5, 20260805)
        self.assertLessEqual(duel.uses_mean, 4.20)

    def test_extended_windows_scale_linearly_without_runaway(self) -> None:
        """长局可以更密集，但不能出现越发动越快的资源回路。"""

        one_shots = {"T7", "G2", "G4"}
        for index, talent in enumerate(TALENT_ORDER):
            if talent in one_shots:
                continue
            six_player_long = aggregate(
                talent,
                6,
                45,
                120,
                0.5,
                20260805 + index * 10_000,
            )
            duel_long = aggregate(
                talent,
                2,
                30,
                120,
                0.5,
                20260805 + index * 10_000,
            )
            self.assertLessEqual(six_player_long.uses_mean, 7.5, talent)
            self.assertLessEqual(six_player_long.impact_mean, 12.0, talent)
            self.assertLessEqual(duel_long.uses_mean, 10.0, talent)
            self.assertLessEqual(duel_long.impact_mean, 18.0, talent)


if __name__ == "__main__":
    unittest.main()
