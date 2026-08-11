"""M9 引擎机制核心单测（B-3，profile: m9-rfc 机制层）。

覆盖：动作系统（SP/槽位/ActionGrant/三源/公演队列）、分辨率合同（A/H/
DIRECT_DAMAGE/absolute_dead/援助休整）、G3 连续投影、G0 世界援助、
PP/投注/魂援/评分。v2exp 不 import 本包 → 不污染 v2exp 回归。
"""
import unittest

from engine import experiments
from engine.m9 import action_system, g0_world_poem, g3_chain, pp, police, resolution
from engine.m9.action_system import (
    ActionSystem, GrantLedger, PublicPerformanceQueue,
    RESOLUTION_KINDS, SP_PUBLIC_COST,
)
from engine.m9.g3_chain import ChainConfig, ProjectionChain
from engine.m9.g0_world_poem import WORLD_RULE_SOURCE_ID, WORLD_RULE_SOURCE_KIND
from engine.m9.pp import PPLedger, ScoringEngine


def _enable_m9():
    experiments.reset()
    experiments.set_profile("m9-rfc")


class ActionSystemTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable_m9()

    def tearDown(self) -> None:
        experiments.reset()

    def test_sp_spend_prechecks_before_consume(self) -> None:
        asys = ActionSystem()
        asys.set_sp("a", 1)
        self.assertFalse(asys.spend_sp("a", SP_PUBLIC_COST))
        self.assertEqual(asys.get_sp("a"), 1)
        self.assertTrue(asys.spend_sp("a", 1))
        self.assertEqual(asys.get_sp("a"), 0)

    def test_full_extra_cap_one_per_round_per_player(self) -> None:
        asys = ActionSystem()
        g1 = asys.dispatch_full_extra("a", 1, "t4_hexagram_hojump")
        self.assertIsNotNone(g1)
        g2 = asys.dispatch_full_extra("a", 1, "g4_savior_active_burn")
        self.assertIsNone(g2)  # 落选候选整体丢弃
        g3 = asys.dispatch_full_extra("b", 1, "g4_savior_active_burn")
        self.assertIsNotNone(g3)  # 不同 actor 不受限

    def test_full_extra_whitelist_and_depth(self) -> None:
        asys = ActionSystem()
        self.assertIsNone(asys.dispatch_full_extra("a", 1, "not_in_whitelist"))
        parent = asys.dispatch_full_extra("a", 1, "t4_hexagram_hojump")
        child = asys.dispatch_full_extra("a", 1, "g5_poem_earthfire", parent=parent)
        self.assertIsNone(child)  # 同轮同 actor 已占满
        # 深度闸：隔轮新父链递归
        parent2 = asys.dispatch_full_extra("c", 2, "t4_hexagram_hojump")
        child2 = asys.dispatch_full_extra("c", 2, "g5_poem_earthfire",
                                          parent=parent2)
        self.assertIsNone(child2)  # 同轮已占满

    def test_three_source_arbitration_priority(self) -> None:
        asys = ActionSystem()
        picked = asys.pick_full_extra_candidate(
            ["g4_savior_active_burn", "t4_hexagram_hojump", "g5_poem_earthfire"])
        self.assertEqual(picked, "t4_hexagram_hojump")

    def test_public_queue_head_invalid_no_fill(self) -> None:
        q = PublicPerformanceQueue()
        q.enqueue("a")
        q.enqueue("b")
        asys = ActionSystem()
        asys.queue = q
        asys.set_sp("a", 0)  # 队首失效（SP<2）
        asys.set_sp("b", 2)
        self.assertEqual(asys.assign_public_slot(1), "b")
        self.assertFalse(q.is_in_queue("a"))  # 永久移除
        q.reenqueue_from_tail("a")
        self.assertEqual(q.head(), "b")  # 重报从队尾

    def test_improvise_and_public_dispatch(self) -> None:
        asys = ActionSystem()
        asys.set_sp("a", 2)
        asys.register_performance("a", 1)
        asys.set_sp("a", 2)
        pub = asys.dispatch_public("a", 1)
        self.assertIsNotNone(pub)
        self.assertTrue(pub.allow_public)
        self.assertEqual(asys.get_sp("a"), 0)
        # 即演：SP 不足不派发
        self.assertIsNone(asys.dispatch_improvise("a", 1))

    def test_slot_finalization_kinds(self) -> None:
        asys = ActionSystem()
        sid = asys.assign_slot("a")
        asys.resolve_slot(sid, root_action=True)
        out = asys.outcome(sid)
        self.assertTrue(out.slot_resolved)
        self.assertEqual(out.resolution_kind, "action_performed")
        self.assertTrue(out.root_action_performed)
        sid2 = asys.assign_slot("a")
        asys.resolve_slot(sid2, kind="suppressed", suppressed=True)
        self.assertEqual(asys.outcome(sid2).resolution_kind, "suppressed")


class ResolutionTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable_m9()

    def tearDown(self) -> None:
        experiments.reset()

    def _target(self, defense_map=None, durability=8, hp=20):
        from models.equipment import ArmorPiece, ArmorLayer
        from utils.attribute import Attribute
        piece = ArmorPiece("盾牌", Attribute.ORDINARY, ArmorLayer.OUTER, 1.0,
                           defense_map=defense_map or {"普通": 2},
                           durability=durability)
        armor = type("A", (), {"outer": [piece]})()
        t = type("T", (), {"armor": armor, "inner_defense": {}, "hp": hp})()
        return t, piece

    def test_a_h_two_phase_accounting(self) -> None:
        target, piece = self._target({"普通": 2}, durability=8)
        hit = resolution.resolve_attack(target, 5, "普通")
        self.assertEqual(hit.damage, 3)          # H 阶段
        self.assertEqual(hit.a_phase_absorbed, 2)  # A 阶段
        self.assertEqual(piece.durability, 6)
        self.assertTrue(hit.effective_hit)

    def test_direct_damage_identity_still_normal_death_flow(self) -> None:
        hit = resolution.resolve_attack(type("T", (), {"armor": None,
                                                       "inner_defense": {},
                                                       "hp": 20})(), 5, "普通",
                                        direct_damage=True)
        self.assertTrue(hit.direct_damage)
        self.assertFalse(hit.absolute_death)
        self.assertFalse(resolution.would_skip_revive("direct_damage"))

    def test_absolute_death_skips_revive(self) -> None:
        self.assertTrue(resolution.would_skip_revive("absolute_death"))
        self.assertTrue(resolution.is_absolute_dead_death("absolute_death"))
        self.assertFalse(resolution.would_skip_revive("terror"))

    def test_aid_rest_tracker(self) -> None:
        tr = resolution.AidRestTracker()
        tr.mark("a")
        self.assertTrue(tr.pending("a"))
        self.assertTrue(tr.consume("a"))
        self.assertFalse(tr.pending("a"))

    def test_control_priority(self) -> None:
        self.assertTrue(resolution.ControlRegistry.higher_or_equal(
            "suppressed", "petrified"))
        self.assertFalse(resolution.ControlRegistry.higher_or_equal(
            "shocked", "petrified"))
        self.assertTrue(resolution.ControlRegistry.is_control("stunned"))
        self.assertFalse(resolution.ControlRegistry.is_control("aid_rest"))


class ProjectionChainTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable_m9()

    def tearDown(self) -> None:
        experiments.reset()

    def _chain(self, inside=True, budget=20):
        chain = ProjectionChain(ChainConfig(), inside_barrier=inside,
                                weapon_name="螺旋剑（伪）")
        chain.magic_budget = budget
        return chain

    def test_whitelist_outside_barrier(self) -> None:
        chain = self._chain(inside=False)
        self.assertFalse(chain.can_chain())
        self.assertIsNone(chain.next_segment_cost())

    def test_escalating_cost_and_max_repeats(self) -> None:
        chain = self._chain(budget=20)
        self.assertEqual(chain.next_segment_cost(), 2)   # 首发
        seg = chain.pay("t1")
        self.assertIsNotNone(seg)
        self.assertEqual(seg.index, 1)
        self.assertEqual(chain.next_segment_cost(), 4)   # 第 2 段 spiral+step
        chain.pay("t2")
        chain.pay("t3")
        self.assertEqual(len(chain.segments), 3)         # 单根至多 3 发
        self.assertIsNone(chain.next_segment_cost())

    def test_precheck_failure_no_payment(self) -> None:
        chain = self._chain(budget=3)
        self.assertEqual(chain.next_segment_cost(), 2)
        self.assertTrue(chain.pay("t1"))
        self.assertIsNone(chain.pay("t2"))  # 成本 4 > 剩余 1 → 不支付、连射结束
        self.assertEqual(chain.cumulative_magic, 2)

    def test_gale_threshold_and_frequency_gate(self) -> None:
        chain = self._chain(budget=20)
        self.assertEqual(chain.next_segment_cost(), 2)
        self.assertTrue(chain.pay("t1"))   # 累计 2
        self.assertTrue(chain.pay("t2"))   # 累计 2+4=6 ≥ 阈值 → 触发
        self.assertTrue(chain.should_apply_gale("p1"))
        self.assertFalse(chain.should_apply_gale("p1"))  # 频率闸同 player_id 一次
        self.assertTrue(chain.should_apply_gale("p2"))

    def test_terminal_collapse_requires_min_magic(self) -> None:
        chain = self._chain(budget=3)
        # budget=3, 未支付任何段 → 剩余 3 ≥ 2 → 可终段
        self.assertTrue(chain.can_terminal_collapse(True))
        spent = chain.terminal_collapse()
        self.assertEqual(spent, 3)
        self.assertEqual(chain.magic_budget, 0)
        chain2 = self._chain(budget=2)
        chain2.pay("t1")  # 花 2 → 剩余 0 < 2
        self.assertFalse(chain2.can_terminal_collapse(True))


class WorldPoemAidTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable_m9()

    def tearDown(self) -> None:
        experiments.reset()

    def test_activation_threshold_and_snapshot(self) -> None:
        ledger = PPLedger()
        aid = g0_world_poem.WorldPoemAid(has_g0_in_pool=True, pp=ledger)
        aid.recompute(1, ["p1", "p2"], ["d1"])
        self.assertFalse(aid.activated)  # 无押注 → 不激活
        self.assertFalse(aid.should_followup_attack("p1", 2))
        # 死者 d1 押注生者 p1 → 激活；p1 被押非黑马，p2 是黑马
        ledger.earn("d1", 5)
        self.assertTrue(ledger.place_bet("d1", "p1"))
        aid.recompute(2, ["p1", "p2"], ["d1"])
        self.assertTrue(aid.activated)
        self.assertFalse(aid.pp.is_blackhorse("p1"))  # 被押 → 非黑马
        self.assertTrue(aid.pp.is_blackhorse("p2"))

    def test_followup_once_per_blackhorse_per_round(self) -> None:
        ledger = PPLedger()
        ledger.earn("d1", 5)
        ledger.place_bet("d1", "p1")   # 死者押 p1 → p2 是黑马
        aid = g0_world_poem.WorldPoemAid(True, ledger)
        aid.recompute(1, ["p1", "p2", "p3"], ["d1"])
        self.assertTrue(aid.should_followup_attack("p2", 3))
        self.assertFalse(aid.should_followup_attack("p2", 3))  # 同轮一次
        self.assertTrue(aid.should_followup_attack("p2", 4))   # 次轮恢复

    def test_r4_heal_once_per_location(self) -> None:
        ledger = PPLedger()
        aid = g0_world_poem.WorldPoemAid(True, ledger)
        self.assertTrue(aid.can_heal_location(4, "商店"))
        self.assertFalse(aid.can_heal_location(4, "商店"))
        self.assertTrue(aid.can_heal_location(5, "商店"))

    def test_world_rule_source_tag(self) -> None:
        ledger = PPLedger()
        aid = g0_world_poem.WorldPoemAid(True, ledger)
        kind, sid = aid.source_tag()
        self.assertEqual(kind, WORLD_RULE_SOURCE_KIND)
        self.assertEqual(sid, WORLD_RULE_SOURCE_ID)


class PPScoringTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable_m9()

    def tearDown(self) -> None:
        experiments.reset()

    def test_earn_freeze_decay(self) -> None:
        ledger = PPLedger()
        ledger.earn("a", 3)
        self.assertEqual(ledger.balance("a"), 3)
        ledger.freeze("a")
        ledger.earn("a", 5)
        self.assertEqual(ledger.balance("a"), 3)  # 冻结不收入
        ledger.decay("a")
        self.assertEqual(ledger.balance("a"), 3)  # 死者免衰减
        ledger.decay("b")
        self.assertEqual(ledger.balance("b"), 0)

    def test_betting_and_blackhorse(self) -> None:
        ledger = PPLedger()
        ledger.earn("d1", 5)
        self.assertTrue(ledger.place_bet("d1", "p1"))  # 死者押生者
        self.assertEqual(ledger.balance("d1"), 3)      # transfer_fee=2
        self.assertTrue(ledger.has_active_bet())
        ledger.recompute_blackhorse(["p1", "p2"], ["d1"])
        self.assertTrue(ledger.is_blackhorse("p2"))
        self.assertFalse(ledger.is_blackhorse("p1"))

    def test_scoring_four_step_settle(self) -> None:
        ledger = PPLedger()
        engine = ScoringEngine(ledger)
        engine.add_arc("p1", 3)
        engine.add_arc("p2", 1)
        ledger.recompute_blackhorse(["p1", "p2"], [])
        results = engine.settle(["p1", "p2"], [])
        self.assertTrue(results["p1"].is_winner)
        self.assertFalse(results["p2"].is_winner)
        base = results["p1"].base_final_score
        self.assertEqual(results["p1"].display_final_score, base + 10)  # 黑马加成

    def test_retreat_uses_alive_formula_half(self) -> None:
        ledger = PPLedger()
        engine = ScoringEngine(ledger)
        engine.add_arc("g0", 2)
        engine.mark_retreat("g0")
        score = engine.score("g0", alive=False)
        self.assertEqual(score.base_final_score, 0.5 * (2 + 0))


class PoliceStationTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable_m9()

    def tearDown(self) -> None:
        experiments.reset()

    def test_case_filing_and_verification(self) -> None:
        station = police.PoliceStation()
        case = station.file_case("r1", "s1", evidence=1)
        self.assertIsNotNone(case)
        self.assertTrue(station.verify_case(case.case_id))
        station.close_case(case.case_id)
        self.assertEqual(station.open_cases(), [])

    def test_shutdown_disables_enforcement(self) -> None:
        station = police.PoliceStation()
        station.shut_down()
        self.assertTrue(station.is_disabled())
        self.assertIsNone(station.file_case("r1", "s1", evidence=1))

    def test_fixed_roster(self) -> None:
        station = police.PoliceStation()
        self.assertEqual(len(station.ensure_roster()), station.fixed_roster_size())

    def test_cover_absorbs_a_phase(self) -> None:
        cover = police.CoverSystem()
        cover.grant("u1", 3)
        self.assertEqual(cover.absorb("u1", 2), 0)
        self.assertEqual(cover.durability("u1"), 1)
        self.assertEqual(cover.absorb("u1", 5), 4)  # 溢出进 H 阶段


if __name__ == "__main__":
    unittest.main()
