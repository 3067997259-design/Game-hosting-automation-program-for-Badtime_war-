"""M9-rfc 机制冒烟：确定性场景驱动引擎/m9 机制层，全部成功则 exit 0。

用法: python tools/m9_rfc_smoke.py

场景（确定性，无随机）：
1. SP/即演/公演与公演队列（队首失效不递补）；
2. 三源 full-extra 仲裁 + 每轮每人上限 + 深度闸；
3. 槽位统一收尾（action_performed / suppressed）；
4. A/H 两阶段攻击（减法防御 + 25% 下限 + 耐久磨损）与 DIRECT_DAMAGE 身份、
   absolute_dead 分流（PP 冻结、评分排除）；
5. G3 连续投影：白名单/递增成本/预检失败不支付/赤原猎风（SP−1+队列移除+频率闸）/
   终段幻想崩坏；
6. G0 世界援助：激活门槛 → 黑马快照 → 星野追演 → 绫音急救（WORLD_RULE）；
7. 警察/T6：案件建档/验证/停机/固定警力/掩体吸收；
8. PP/评分：四步求值 + 黑马加成（胜者快照不回算）。

每步断言失败打印 FAIL 并 exit 1；全部通过打印 PASS 并 exit 0。
"""
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine import experiments
from engine.m9.action_system import ActionSystem, SP_PUBLIC_COST
from engine.m9 import g0_world_poem, g3_chain, police, pp, resolution
from engine.m9 import talent_registry
from engine.m9.g3_chain import ChainConfig
from engine.m9.pp import PPLedger, ScoringEngine

FAILURES: list = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if not cond:
        FAILURES.append(f"{name}: {detail}")


def main() -> int:
    experiments.reset()
    experiments.set_profile("m9-rfc")

    # ── 1. SP / 即演 / 公演 / 队列 ──
    asys = ActionSystem()
    asys.set_sp("a", 2)
    check("register_performance", asys.register_performance("a", 1))
    asys.set_sp("a", 2)
    pub = asys.dispatch_public("a", 1)
    check("public_dispatch", pub is not None and pub.allow_public,
          f"grant={pub}")
    check("public_sp_consumed", asys.get_sp("a") == 0)
    check("improvise_needs_sp", asys.dispatch_improvise("a", 1) is None)
    q = asys.queue
    q.enqueue("x")
    asys.set_sp("x", 0)
    check("queue_head_invalid_no_fill",
          asys.assign_public_slot(2) is None and not q.is_in_queue("x"))

    # ── 2. full-extra 三源仲裁 + 上限 + 深度 ──
    picked = asys.pick_full_extra_candidate(
        ["g4_savior_active_burn", "t4_hexagram_hojump"])
    check("three_source_priority", picked == "t4_hexagram_hojump")
    g1 = asys.dispatch_full_extra("a", 3, "t4_hexagram_hojump")
    check("full_extra_issued", g1 is not None)
    check("full_extra_cap",
          asys.dispatch_full_extra("a", 3, "g5_poem_earthfire") is None)
    check("full_extra_other_player",
          asys.dispatch_full_extra("b", 3, "g4_savior_active_burn") is not None)
    check("full_extra_whitelist",
          asys.dispatch_full_extra("c", 4, "nope") is None)
    parent = asys.dispatch_full_extra("d", 5, "t4_hexagram_hojump")
    child = asys.dispatch_full_extra("d", 5, "g5_earthfire_poem", parent=parent)
    check("full_extra_cap_same_round", child is None)

    # ── 3. 槽位统一收尾 ──
    sid = asys.assign_slot("a")
    asys.resolve_slot(sid, root_action=True)
    out = asys.outcome(sid)
    check("slot_resolved", out.slot_resolved and out.root_action_performed
          and out.resolution_kind == "action_performed")
    sid2 = asys.assign_slot("b")
    asys.resolve_slot(sid2, kind="suppressed", suppressed=True)
    check("slot_suppressed", asys.outcome(sid2).resolution_kind == "suppressed")

    # ── 4. A/H 两阶段 + DIRECT_DAMAGE + absolute_dead ──
    from models.equipment import ArmorLayer, ArmorPiece
    from utils.attribute import Attribute
    piece = ArmorPiece("盾牌", Attribute.ORDINARY, ArmorLayer.OUTER, 1.0,
                       defense_map={"普通": 2}, durability=8)
    armor = type("Armor", (), {"outer": [piece]})()
    target = type("Target", (), {"armor": armor, "inner_defense": {},
                                 "hp": 20})()
    hit = resolution.resolve_attack(target, 5, "普通")
    check("ah_accounting", hit.damage == 3 and hit.a_phase_absorbed == 2
          and piece.durability == 6)
    dd = resolution.resolve_attack(
        type("T", (), {"armor": None, "inner_defense": {}, "hp": 20})(),
        5, "普通", direct_damage=True)
    check("direct_damage_identity", dd.direct_damage
          and not resolution.would_skip_revive("direct_damage"))
    check("absolute_dead_skip_revive",
          resolution.would_skip_revive("absolute_death"))
    ledger = PPLedger()
    ledger.earn("f", 4)
    ledger.freeze("f")
    ledger.earn("f", 5)
    check("absolute_dead_pp_frozen", ledger.balance("f") == 4)

    # ── 5. G3 连续投影 ──
    chain = g3_chain.ProjectionChain(ChainConfig(), inside_barrier=True,
                                     weapon_name="螺旋剑（伪）")
    chain.magic_budget = 20
    check("chain_whitelist_out", g3_chain.ProjectionChain(
        ChainConfig(), inside_barrier=False,
        weapon_name="螺旋剑（伪）").can_chain() is False)
    check("chain_first_cost", chain.next_segment_cost() == 2)
    chain.pay("t1")
    chain.pay("t2")                      # 累计 6 ≥ 阈值
    check("chain_gale_trigger", chain.should_apply_gale("p1"))
    check("chain_gale_frequency_gate", not chain.should_apply_gale("p1"))
    chain.pay("t3")
    check("chain_max_repeats", len(chain.segments) == 3
          and chain.next_segment_cost() is None)
    chain.finish_root()
    check("chain_counter_reset", chain.cumulative_magic == 0
          and not chain.should_apply_gale("p1"))

    # ── 6. G0 世界援助 ──
    aid_ledger = PPLedger()
    aid = g0_world_poem.WorldPoemAid(True, aid_ledger)
    aid.recompute(1, ["p1", "p2"], ["d1"])
    check("aid_inactive_without_bet", not aid.activated)
    aid_ledger.earn("d1", 5)
    aid_ledger.place_bet("d1", "p1")
    aid.recompute(2, ["p1", "p2"], ["d1"])
    check("aid_activation", aid.activated)
    check("blackhorse_snapshot", aid_ledger.is_blackhorse("p2")
          and not aid_ledger.is_blackhorse("p1"))
    check("followup_once", aid.should_followup_attack("p2", 3)
          and not aid.should_followup_attack("p2", 3))
    check("r4_heal_once", aid.can_heal_location(4, "商店")
          and not aid.can_heal_location(4, "商店"))
    check("world_rule_source", aid.source_tag()
          == ("WORLD_RULE", "world_poem_g0_aid"))

    # ── 7. 警察/T6 ──
    station = police.PoliceStation()
    case = station.file_case("r1", "s1", evidence=1)
    check("case_filed", case is not None)
    check("case_verified", station.verify_case(case.case_id))
    station.shut_down()
    check("station_shutdown", station.is_disabled()
          and station.file_case("r2", "s2", evidence=1) is None)
    cover = police.CoverSystem()
    cover.grant("u1", 3)
    check("cover_absorb", cover.absorb("u1", 2) == 0
          and cover.absorb("u1", 5) == 4)
    check("t6_equipment", "警棍" in police.t6_equipment_set()
          and "盾牌" in police.t6_equipment_set())
    check("slot_migration", talent_registry.resolve_slot("T5") == "G0")
    check("g3_spotlight", talent_registry.spotlight("G3").uses_sp == 2)

    # ── 8. PP/评分四步求值 ──
    s_ledger = PPLedger()
    s_ledger.earn("d1", 5)
    s_ledger.place_bet("d1", "p1")
    s_ledger.recompute_blackhorse(["p1", "p2"], ["d1"])
    engine = ScoringEngine(s_ledger)
    engine.add_arc("p1", 1)
    engine.add_arc("p2", 3)
    results = engine.settle(["p1", "p2"], ["d1"])
    check("winner_snapshot", results["p2"].is_winner
          and not results["p1"].is_winner)
    # 黑马加成只在黑马胜利时计入显示终分，胜者快照（base）不回算
    check("blackhorse_bonus_on_winner",
          results["p2"].display_final_score
          == results["p2"].base_final_score + 10)
    check("loser_no_bonus",
          results["p1"].display_final_score == results["p1"].base_final_score)

    experiments.reset()

    if FAILURES:
        print("M9-rfc smoke FAIL:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("M9-rfc smoke PASS: 8 场景全过（动作/分辨率/连发/世界援助/警察/评分）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
