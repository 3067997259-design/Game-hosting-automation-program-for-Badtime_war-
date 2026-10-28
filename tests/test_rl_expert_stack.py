"""tests/test_rl_expert_stack.py
──────────────────────────
专家栈 P0 门禁（docs/m9/rl/m9_rl_next_steps.md §3）：

- 注册表 9+1 专家齐全、路径与本仓库 checkpoint 对应（存在性不强求，
  交由 load_experts 给出可操作错误）；
- route() 表驱动单测：每条规则有正例与反例，多规则命中时按
  ROUTE_PRIORITY 先命中先赢；
- ExpertStackModel：每专家独立递归状态、episode 边界统一重置、
  按局路由统计（二杀/续战/切换口径）与 routing_log 可归因。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import numpy as np

from rl.expert_stack import (
    EXPERT_CHECKPOINTS,
    EXPERT_DECISION_BUDGETS,
    ROUTE_PRIORITY,
    EpisodeSummary,
    ExpertStackModel,
    GameFacts,
    OpponentFacts,
    route,
    route_with_budgets,
)


def _opp(
    hp: int = 20,
    invisible: bool = False,
    ceramic: bool = False,
) -> OpponentFacts:
    return OpponentFacts(
        alive=hp > 0, hp=hp, invisible=invisible, has_ceramic_armor=ceramic,
    )


def _facts(**overrides) -> GameFacts:
    defaults = dict(
        kill_count=0,
        hp=20,
        max_hp=20,
        pp=0,
        choose_mode=False,
        situation="",
        has_bow=False,
        best_weapon_damage=4,
        has_unsharpened_knife=False,
        has_detection=False,
        detection_attrs=frozenset(),
        own_armor_min_ratio=1.0,
        opponents=(_opp(), _opp()),
    )
    defaults.update(overrides)
    return GameFacts(**defaults)


class _FakeExpert:
    """返回固定动作并记录递归状态传递的最小专家桩。"""

    def __init__(self, label: int) -> None:
        self.label = label
        self.calls = []

        class _Space:
            shape = (828,)
            n = 217

        self.observation_space = _Space()
        self.action_space = _Space()

    def predict(self, obs, state=None, episode_start=None,
                action_masks=None, deterministic=True):
        self.calls.append({
            "state_in": state,
            "episode_start": bool(np.any(episode_start)) if episode_start is not None else False,
        })
        return np.array([self.label], dtype=np.int64), f"s{self.label}"


def _fake_experts() -> dict:
    return {key: _FakeExpert(i) for i, key in enumerate(
        ["opening", "postkill", "pp_finish", "survival_heal",
         "armor_repair", "sharpen", "detection", "attribute",
         "fire_antiarmor", "police"],
    )}


class TestExpertRegistry(unittest.TestCase):
    """注册表完整性。"""

    def test_registry_contains_all_roles(self):
        expected = {
            "opening", "postkill", "pp_finish", "survival_heal",
            "armor_repair", "sharpen", "detection", "attribute",
            "fire_antiarmor", "police",
        }
        self.assertEqual(set(EXPERT_CHECKPOINTS), expected)

    def test_priority_covers_all_non_default_experts(self):
        self.assertEqual(
            set(ROUTE_PRIORITY),
            set(EXPERT_CHECKPOINTS) - {"opening", "postkill"},
        )
        # 无重复、有全序
        self.assertEqual(len(ROUTE_PRIORITY), len(set(ROUTE_PRIORITY)))


class TestRoutingTable(unittest.TestCase):
    """route() 表驱动正反例。"""

    def test_default_opening_and_postkill(self):
        self.assertEqual(route(_facts()).expert, "opening")
        self.assertEqual(
            route(_facts(kill_count=1)).expert, "postkill",
        )

    def test_police_rule(self):
        hit = route(_facts(choose_mode=True, situation="police_election"))
        self.assertEqual(hit.expert, "police")
        # 引擎实际使用英文 situation 名（全量采集实证）：
        self.assertEqual(
            route(_facts(choose_mode=True, situation="captain_election")).expert,
            "police",
        )
        self.assertIn("警察族", hit.reason)
        miss = route(_facts(choose_mode=True, situation="weapon_choice"))
        self.assertNotEqual(miss.expert, "police")
        # 非 choose 模式不触发
        miss2 = route(_facts(situation="police_election"))
        self.assertNotEqual(miss2.expert, "police")

    def test_pp_finish_rule(self):
        # 5 HP 对手 vs 4 伤武器 → margin=1 命中（课程事实）
        hit = route(_facts(pp=1, opponents=(_opp(hp=5), _opp(hp=20))))
        self.assertEqual(hit.expert, "pp_finish")
        # margin=2 不命中
        miss = route(_facts(pp=1, opponents=(_opp(hp=6), _opp(hp=20))))
        self.assertNotEqual(miss.expert, "pp_finish")
        # 无 PP 不命中
        miss2 = route(_facts(pp=0, opponents=(_opp(hp=5),)))
        self.assertNotEqual(miss2.expert, "pp_finish")

    def test_fire_antiarmor_rule(self):
        hit = route(_facts(has_bow=True,
                           opponents=(_opp(ceramic=True), _opp())))
        self.assertEqual(hit.expert, "fire_antiarmor")
        miss = route(_facts(has_bow=False,
                            opponents=(_opp(ceramic=True),)))
        self.assertNotEqual(miss.expert, "fire_antiarmor")

    def test_survival_heal_rule(self):
        hit = route(_facts(hp=4, location="医院"))
        self.assertEqual(hit.expert, "survival_heal")
        # v1.1：不在可执行治疗/购甲的地点不接管（防低血锁死）
        miss = route(_facts(hp=4, location="魔法所"))
        self.assertNotEqual(miss.expert, "survival_heal")
        miss_hp = route(_facts(hp=12, location="医院"))
        self.assertNotEqual(miss_hp.expert, "survival_heal")
        # 无存活对手不触发
        miss2 = route(_facts(
            hp=4, location="医院", opponents=(_opp(hp=0), _opp(hp=0)),
        ))
        self.assertNotEqual(miss2.expert, "survival_heal")

    def test_armor_repair_rule(self):
        hit = route(_facts(own_armor_min_ratio=0.25))
        self.assertEqual(hit.expert, "armor_repair")
        miss = route(_facts(own_armor_min_ratio=0.8))
        self.assertNotEqual(miss.expert, "armor_repair")

    def test_sharpen_rule(self):
        hit = route(_facts(kill_count=1, has_unsharpened_knife=True))
        self.assertEqual(hit.expert, "sharpen")
        # 首杀前不磨刀，避免劫持开局
        miss = route(_facts(kill_count=0, has_unsharpened_knife=True))
        self.assertNotEqual(miss.expert, "sharpen")

    def test_detection_rule(self):
        hit = route(_facts(opponents=(_opp(invisible=True), _opp())))
        self.assertEqual(hit.expert, "detection")
        miss = route(_facts(has_detection=True,
                            opponents=(_opp(invisible=True), _opp())))
        self.assertNotEqual(miss.expert, "detection")

    def test_detection_budget(self):
        # v1.1：detection 有每局决策预算，耗尽后回落默认分流
        budget = EXPERT_DECISION_BUDGETS["detection"]
        self.assertGreater(budget, 0)
        facts = _facts(opponents=(_opp(invisible=True), _opp()))
        under = route_with_budgets(facts, {"detection": budget - 1})
        self.assertEqual(under.expert, "detection")
        exhausted = route_with_budgets(facts, {"detection": budget})
        self.assertNotEqual(exhausted.expert, "detection")
        # 无预算视图（route）不受影响
        self.assertEqual(route(facts).expert, "detection")

    def test_pp_finish_sticky_within_episode(self):
        # v1.2：PP 花掉后条件消失，但本局已启用 pp_finish → 粘性接管
        trigger = _facts(
            pp=1, opponents=(_opp(hp=5), _opp()),
        )
        self.assertEqual(route(trigger).expert, "pp_finish")
        after_spend = _facts(
            pp=0, hp=1, opponents=(_opp(hp=5), _opp()),
        )
        # 无 spent 时按常规优先级（低血在医院才会触发 survival）
        self.assertNotEqual(route(after_spend).expert, "pp_finish")
        sticky = route_with_budgets(
            after_spend, {"pp_finish": 1}, {"pp_finish": 0},
        )
        self.assertEqual(sticky.expert, "pp_finish")
        self.assertIn("粘性", sticky.reason)
        # 粘性优先于其他专项规则
        overridden = route_with_budgets(
            _facts(pp=0, hp=4, location="医院",
                   opponents=(_opp(hp=5), _opp())),
            {"pp_finish": 1}, {"pp_finish": 0},
        )
        self.assertEqual(overridden.expert, "pp_finish")

    def test_pp_finish_sticky_releases_on_kill(self):
        # P4.1：击杀数变化即解除粘性——首杀后交回 postkill 追第二杀
        released = route_with_budgets(
            _facts(kill_count=1, pp=0, opponents=(_opp(hp=8), _opp())),
            {"pp_finish": 3}, {"pp_finish": 0},
        )
        self.assertEqual(released.expert, "postkill")

    def test_attribute_rule(self):
        hit = route(_facts(has_detection=True,
                           detection_attrs=frozenset({"普通"})))
        self.assertEqual(hit.expert, "attribute")
        full = route(_facts(
            has_detection=True,
            detection_attrs=frozenset({"普通", "魔法", "科技"}),
        ))
        self.assertNotEqual(full.expert, "attribute")

    def test_priority_pp_over_fire(self):
        decision = route(_facts(
            pp=1,
            has_bow=True,
            opponents=(_opp(hp=5, ceramic=True), _opp()),
        ))
        self.assertEqual(decision.expert, "pp_finish")

    def test_priority_police_over_all(self):
        decision = route(_facts(
            choose_mode=True,
            situation="队长竞选",
            pp=1,
            has_bow=True,
            hp=4,
            opponents=(_opp(hp=5, ceramic=True), _opp()),
        ))
        self.assertEqual(decision.expert, "police")

    def test_every_decision_has_reason(self):
        for facts in (
            _facts(), _facts(kill_count=2),
            _facts(choose_mode=True, situation="police_command"),
            _facts(hp=1, pp=3, opponents=(_opp(hp=5, ceramic=True),)),
        ):
            decision = route(facts)
            self.assertTrue(decision.reason.strip())


class TestExpertStackModel(unittest.TestCase):
    """predict 侧路由器的状态管理与统计。"""

    def _stack_with(self, facts_sequence):
        experts = _fake_experts()
        seq = list(facts_sequence)
        fallback = _facts()

        def facts_fn():
            return seq.pop(0) if seq else fallback

        stack = ExpertStackModel(experts, facts_fn)
        return stack, experts

    def test_routes_to_chosen_expert_action(self):
        facts_seq = [_facts(), _facts()]
        stack, experts = self._stack_with(facts_seq)
        action, state = stack.predict(
            np.zeros(828, dtype=np.float32), episode_start=np.array([True]),
            action_masks=np.ones(217, dtype=bool),
        )
        self.assertEqual(int(action[0]), experts["opening"].label)
        self.assertIsNotNone(state)  # 非 None 哨兵（供边界检测回传）

    def test_episode_boundary_via_state_sentinel(self):
        # winrate_eval 的真实调用模式：每局首拍 state=None 且
        # episode_start=[False]（局末 [True] 被外层循环丢弃）。
        # 依赖 state 哨兵识别边界，否则整个 seed 的局会合并成一个汇总。
        facts_seq = [_facts(kill_count=1)] * 6
        stack, _ = self._stack_with(facts_seq)
        mask = np.ones(217, dtype=bool)
        obs = np.zeros(828, dtype=np.float32)
        for _ in range(3):
            _, state = stack.predict(
                obs, state=None, episode_start=np.array([False]),
                action_masks=mask,
            )
            stack.predict(
                obs, state=state, episode_start=np.array([False]),
                action_masks=mask,
            )
        stack.flush()
        self.assertEqual(len(stack.episode_summaries), 3)
        self.assertTrue(all(s.decisions == 2 for s in stack.episode_summaries))

    def test_per_expert_state_isolation(self):
        # 开局 → 首杀后 → 再回开局条件（模拟规则反复）时，
        # 各专家拿回自己的状态，互不串扰
        facts_seq = [
            _facts(),
            _facts(kill_count=1),
            _facts(kill_count=1),
            _facts(),
            _facts(),
        ]
        stack, experts = self._stack_with(facts_seq)
        mask = np.ones(217, dtype=bool)
        obs = np.zeros(828, dtype=np.float32)
        state = None
        for _ in range(5):
            _, state = stack.predict(
                obs, state=state, episode_start=np.array([False]),
                action_masks=mask,
            )
        opening_calls = experts["opening"].calls
        postkill_calls = experts["postkill"].calls
        self.assertEqual(len(opening_calls), 3)
        self.assertEqual(len(postkill_calls), 2)
        # 第 2/3 次调用各自拿回上一次返回的状态（postkill 桩 label=1）
        self.assertEqual(opening_calls[1]["state_in"], "s0")
        self.assertEqual(opening_calls[2]["state_in"], "s0")
        self.assertEqual(postkill_calls[0]["state_in"], None)
        self.assertEqual(postkill_calls[1]["state_in"], "s1")

    def test_episode_boundary_resets_states_and_summarizes(self):
        facts_seq = [_facts(kill_count=1), _facts(kill_count=2)]
        stack, experts = self._stack_with(facts_seq)
        mask = np.ones(217, dtype=bool)
        obs = np.zeros(828, dtype=np.float32)
        stack.predict(obs, episode_start=np.array([True]), action_masks=mask)
        # 新 episode：状态清空 + 上一局汇总入 episode_summaries
        stack.predict(obs, episode_start=np.array([True]), action_masks=mask)
        self.assertEqual(len(stack.episode_summaries), 1)
        summary = stack.episode_summaries[0]
        self.assertIsInstance(summary, EpisodeSummary)
        self.assertEqual(summary.final_kill_count, 2)
        self.assertEqual(summary.decisions, 1)
        # 清空后 postkill 的第一个调用应拿到 None 状态
        self.assertEqual(experts["postkill"].calls[-1]["state_in"], None)

    def test_routing_log_and_switch_counts(self):
        facts_seq = [
            _facts(),
            _facts(kill_count=1),
            _facts(kill_count=1, hp=4, location="医院"),
            _facts(kill_count=1),
        ]
        stack, _ = self._stack_with(facts_seq)
        mask = np.ones(217, dtype=bool)
        obs = np.zeros(828, dtype=np.float32)
        state = None
        for _ in range(4):
            _, state = stack.predict(
                obs, state=state, episode_start=np.array([False]),
                action_masks=mask,
            )
        stack.flush()  # 评估收口：未关闭的当期局计入统计
        experts_used = [entry[1] for entry in stack.routing_log]
        self.assertEqual(
            experts_used,
            ["opening", "postkill", "survival_heal", "postkill"],
        )
        self.assertTrue(
            all(reason.strip() for _, _, reason in stack.routing_log),
        )
        # 开局→续战→生存→续战 = 3 次切换
        self.assertEqual(stack.episode_summaries[0].switches, 3)

    def test_budget_integration_through_stack(self):
        # 隐身对手持续存在（条件不清除）时，detection 只接管预算内的拍数，
        # 之后回落默认分流——v1 基线"锁死"病理的回归测试。
        budget = EXPERT_DECISION_BUDGETS["detection"]
        facts_seq = [
            _facts(opponents=(_opp(invisible=True), _opp()))
            for _ in range(budget + 3)
        ]
        stack, _ = self._stack_with(facts_seq)
        mask = np.ones(217, dtype=bool)
        obs = np.zeros(828, dtype=np.float32)
        state = None
        for _ in range(budget + 3):
            _, state = stack.predict(
                obs, state=state, episode_start=np.array([False]),
                action_masks=mask,
            )
        stack.flush()
        summary = stack.episode_summaries[0]
        self.assertEqual(
            summary.routing_counts.get("detection", 0), budget,
        )
        self.assertEqual(
            summary.routing_counts.get("opening", 0), 3,
        )

    def test_missing_expert_rejected(self):
        with self.assertRaises(ValueError):
            ExpertStackModel({"opening": _FakeExpert(0)}, lambda: _facts())

    def test_spaces_exposed_for_schema_validation(self):
        stack, _ = self._stack_with([_facts()])
        self.assertEqual(stack.observation_space.shape, (828,))
        self.assertEqual(stack.action_space.n, 217)


if __name__ == "__main__":
    unittest.main()
