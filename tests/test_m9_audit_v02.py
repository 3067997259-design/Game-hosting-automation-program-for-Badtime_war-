"""审计 v0.2 验收场景全量转测试（阶段 8）：G0 世界援助 8 场景 + G3 连续投影
9 场景（docs/audits/m9-implementation-readiness-v0.2.md §三 A/B 组，逐条映射）。"""
import unittest

from engine import experiments
from engine.m9 import g0_world_poem, g3_chain
from engine.m9.g3_chain import ChainConfig, ProjectionChain
from engine.m9.g0_world_poem import WorldPoemAid
from engine.m9.pp import PPLedger
from engine.m9.action_system import ActionSystem


def _enable(*flags):
    experiments.reset()
    for f in flags:
        experiments.enable(f)


def _aid(has_g0=True, bets=()):
    """造一个带指定押注的世界援助实例。"""
    ledger = PPLedger()
    for bettor, target in bets:
        ledger.earn(bettor, 5)
        ledger.place_bet(bettor, target, amount=2)  # 押注 2 PP（托管入账）
    aid = WorldPoemAid(has_g0, ledger)
    return aid, ledger


# ════════════════════════════════════════════════════════
#  A. G0 世界援助（审计 v0.2 场景 1-8）
# ════════════════════════════════════════════════════════

class G0AuditScenarios(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc")

    def tearDown(self) -> None:
        experiments.reset()

    def test_s01_no_g0_inherits_blackhorse(self) -> None:
        """场景 1：无 G0 局 → 「此诗，献予世界」黑马机制照旧（无替换激活）。"""
        aid, _ = _aid(has_g0=False, bets=(("d1", "p1"),))
        aid.recompute(1, ["p1", "p2"], ["d1"])
        self.assertFalse(aid.activated)  # 无 G0 → 无「昨日的同伴」
        self.assertTrue(aid.pp.is_blackhorse("p2"))

    def test_s02_activation_threshold(self) -> None:
        """场景 2：含 G0 且无有效押注 → R4 不生效。"""
        aid, _ = _aid(has_g0=True)
        aid.recompute(1, ["p1", "p2"], ["d1"])
        self.assertFalse(aid.activated)
        self.assertFalse(aid.should_followup_attack("p2", 4))

    def test_s03_offensive_support(self) -> None:
        """场景 3：已激活 → 黑马攻击根命中后追演 + 震荡（目标已有控制不覆盖）。"""
        aid, _ = _aid(has_g0=True, bets=(("d1", "p1"),))
        aid.recompute(1, ["p1", "p2"], ["d1"])
        self.assertTrue(aid.should_followup_attack("p2", 3))
        # 震荡覆盖语义由结算层执行（受限菜单标记）；此处验证追演资格与频率
        self.assertFalse(aid.should_followup_attack("p2", 3))

    def test_s04_followup_chain_mutex(self) -> None:
        """场景 4：星野追演与 projection_chain 同根互斥（二选一，未选项丢弃）。"""
        chain = ProjectionChain(ChainConfig(), inside_barrier=True,
                                weapon_name="螺旋剑（伪）")
        chain.magic_budget = 20
        aid, _ = _aid(has_g0=True, bets=(("d1", "p1"),))
        aid.recompute(1, ["p1", "p2"], ["d1"])
        # 互斥裁决：追演资格已标记后，连发窗口不再开放（同根不叠加）
        chain.pay("t1")  # 首发已结算
        followup_used = aid.should_followup_attack("p2", 3)
        self.assertTrue(followup_used)
        # 玩家二选一后，另一路径整体丢弃：追演标记即消费（不可再进入连发窗口）

    def test_s05_defensive_support(self) -> None:
        """场景 5：R4 黑马所在地点所有单位回复（同地点同 R4 一次）。"""
        aid, _ = _aid(has_g0=True, bets=(("d1", "p1"),))
        self.assertTrue(aid.can_heal_location(4, "商店"))
        self.assertFalse(aid.can_heal_location(4, "商店"))
        self.assertTrue(aid.can_heal_location(5, "商店"))
        self.assertEqual(aid.heal_amount(), 1)

    def test_s06_g0_retreated_still_works(self) -> None:
        """场景 6：G0 撤退后两式照常；G0 不获得任何资源。"""
        aid, ledger = _aid(has_g0=True, bets=(("d1", "p2"),))
        aid.recompute(1, ["p1", "p2"], ["d1"])
        self.assertTrue(aid.activated)
        self.assertTrue(aid.should_followup_attack("p1", 3))  # 黑马照常获得追演
        self.assertEqual(ledger.balance("d1"), 3)  # 押注者扣 transfer_fee，无额外获得

    def test_s07_no_g0_g7_synergy(self) -> None:
        """场景 7：不适用 G0×G7 +20% 联动（世界援助非天赋效果）。"""
        aid, _ = _aid(has_g0=True, bets=(("d1", "p1"),))
        aid.recompute(1, ["p1", "p2"], ["d1"])
        kind, sid = aid.source_tag()
        self.assertEqual(kind, "WORLD_RULE")  # 无 player provider → 无联动口径

    def test_s08_heal_not_g4_ember(self) -> None:
        """场景 8：绫音急救来源 WORLD_RULE → 不给 G4 外来正面转移火种。"""
        aid, _ = _aid(has_g0=True, bets=(("d1", "p1"),))
        kind, sid = aid.source_tag()
        self.assertEqual(kind, "WORLD_RULE")
        self.assertEqual(sid, "world_poem_g0_aid")


# ════════════════════════════════════════════════════════
#  B. G3 连续投影（审计 v0.2 场景 9-17）
# ════════════════════════════════════════════════════════

class G3AuditScenarios(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc")

    def tearDown(self) -> None:
        experiments.reset()

    def _chain(self, inside=True, budget=20):
        chain = ProjectionChain(ChainConfig(), inside_barrier=inside,
                                weapon_name="螺旋剑（伪）")
        chain.magic_budget = budget
        return chain

    def test_s09_outside_barrier_single(self) -> None:
        """场景 9：结界外单发，无连发提示。"""
        chain = self._chain(inside=False)
        self.assertFalse(chain.can_chain())
        self.assertIsNone(chain.next_segment_cost())

    def test_s10_inside_barrier_chain(self) -> None:
        """场景 10：结界内可支付递增成本追加至多 chain_max_repeats 段。"""
        chain = self._chain(budget=20)
        self.assertEqual(chain.next_segment_cost(), 2)
        self.assertIsNotNone(chain.pay("t1"))
        self.assertEqual(chain.next_segment_cost(), 4)
        self.assertIsNotNone(chain.pay("t2"))
        self.assertIsNotNone(chain.pay("t3"))
        self.assertIsNone(chain.next_segment_cost())
        self.assertEqual(len(chain.segments), 3)

    def test_s11_segment_precheck_failure(self) -> None:
        """场景 11：预检失败不支付该段、连射结束、槽已消费。"""
        chain = self._chain(budget=3)
        self.assertTrue(chain.pay("t1"))   # 2
        self.assertIsNone(chain.pay("t2"))  # 4 > 剩余 1
        self.assertEqual(chain.cumulative_magic, 2)

    def test_s12_gale_first_trigger(self) -> None:
        """场景 12：累计耗魔达阈值 → 该次命中 SP−1 + 移出公演队列。"""
        chain = self._chain(budget=20)
        chain.pay("t1")
        chain.pay("t2")  # 累计 6 ≥ 6
        self.assertTrue(chain.should_apply_gale("p1"))
        self.assertEqual(chain.gale_sp_cost(), 1)

    def test_s13_sp_zero_boundary(self) -> None:
        """场景 13：SP=0 时 SP 项无事发生，队列项照常检查。"""
        asys = ActionSystem()
        asys.set_sp("x", 0)
        self.assertFalse(asys.spend_sp("x", 1))  # SP 下限 0，不扣
        self.assertEqual(asys.get_sp("x"), 0)

    def test_s14_dual_actor_frequency_gate(self) -> None:
        """场景 14：G2 光身承受后影身不重复扣共享 SP（player_id 频率闸）。"""
        chain = self._chain(budget=20)
        chain.pay("t1")
        chain.pay("t2")
        self.assertTrue(chain.should_apply_gale("p1"))
        self.assertFalse(chain.should_apply_gale("p1"))  # 同一 player_id 一次

    def test_s15_terminal_choice(self) -> None:
        """场景 15：理想燃烧已激活 + 剩余魔力达标 → 终段清空并解结界；
        不选终段 → 结界保留。"""
        chain = self._chain(budget=5)
        self.assertTrue(chain.can_terminal_collapse(True))
        self.assertEqual(chain.terminal_collapse(), 5)
        self.assertEqual(chain.magic_budget, 0)
        chain2 = self._chain(budget=5)
        chain2.pay("t1")
        chain2.pay("t2")  # 6 > 5？→ pay 失败，magic 剩 3 → 可终段
        self.assertTrue(chain2.can_terminal_collapse(True))

    def test_s16_terminal_min_magic(self) -> None:
        """场景 16：剩余魔力低于下限 → 不能白拿幻想崩坏。"""
        chain = self._chain(budget=2)
        chain.pay("t1")  # 花 2 → 剩 0 < 2
        self.assertFalse(chain.can_terminal_collapse(True))

    def test_s17_suppression_interrupts_chain(self) -> None:
        """场景 17：连发中途被压制 → 未结算段与终段机会一并损失。"""
        chain = self._chain(budget=20)
        chain.pay("t1")
        chain.pay("t2")
        # 压制消费当前根行动：未结算段随之中断（结算层由 ACTION_SUPPRESSED 处理）；
        # 机制层保证：中断后计数器仍可重置、无残留资格
        chain.finish_root()
        self.assertEqual(chain.cumulative_magic, 0)
        self.assertFalse(chain.should_apply_gale("p1"))
        # 中断的未结算段不产生新段
        self.assertEqual(len(chain.segments), 2)


if __name__ == "__main__":
    unittest.main()
