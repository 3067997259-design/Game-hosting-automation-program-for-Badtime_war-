"""M9 G3「神话之外」天赋适配器单测（固有结界 RFC v0.2 + 连续投影 RFC v0.1）。

覆盖：魔力账本（初始/R0 恢复/上限）、结界外投影（螺旋剑/双刀攻守/七重圆环/复制武器）、
结界展开（2 SP 公演/捕捉/主目标/警察挂起/临时魔力）、结界内螺旋剑连发（ProjectionChain
接线/赤原猎风/段数上限）、剑阵、理想燃烧+幻想崩坏公式、R4 维持（建立轮不 tick/不足强制
解除/硬上限）、破界、G3 死亡清理、跨边界、无内部授予/不免疫控制、双管线隔离、
G6 借用螺旋剑。

G3 在 registry 中 BLOCKED：直接 import engine.m9.talents.g3.Mythland9，不走注册表。
"""
import unittest

from controllers.base import PlayerController

from engine import experiments
from engine.balance import get as bget
from engine.game_state import GameState
from models.equipment import make_weapon
from models.player import Player

from engine.m9.gate import ensure_state_mechanisms
from engine.m9.talents.g3 import COPY_WEAPON_CLOSED_LIST, Mythland9


def _enable(*flags):
    experiments.reset()
    for f in flags:
        experiments.enable(f)


def _g3(key, default):
    return bget("m9_talents_extended", "g3", key, default=default)


class _RecordingController(PlayerController):
    """记录 choose 调用，返回预设选择序列（耗尽后回退首个选项）。"""

    def __init__(self, *choices):
        super().__init__()
        self.calls = []
        self._choices = list(choices)

    def choose(self, prompt, options, context=None):
        self.calls.append((prompt, list(options)))
        if self._choices:
            choice = self._choices.pop(0)
            return choice if choice in options else options[0]
        return options[0]

    def get_command(self, player, game_state, available_actions, context=None):
        return "forfeit"

    def choose_multi(self, prompt, options, max_count, min_count=0, context=None):
        return list(options)[:max_count]

    def confirm(self, prompt, context=None):
        return True


def _make(*pids):
    """创建 state + 玩家（hp20）+ G3 天赋；pids[0] 为 G3。"""
    state = GameState()
    ensure_state_mechanisms(state)
    state.current_round = 1
    g3 = None
    others = []
    for i, pid in enumerate(pids):
        p = Player(pid, f"玩家{i}", controller=_RecordingController())
        state.add_player(p)
        p.max_hp = 20
        p.hp = 20
        p.location = "公园"
        if i == 0:
            g3 = p
        else:
            others.append(p)
    t = Mythland9(g3.player_id, state)
    g3.talent = t
    return state, g3, t, others


def _set_sp(state, pid, value):
    state.m9_system.set_sp(pid, value)


def _lock(state, attacker_id, target_id):
    state.markers.add_relation(target_id, "LOCKED_BY", attacker_id)


def _engage(state, attacker_id, target_id):
    state.markers.add_relation(attacker_id, "ENGAGED_WITH", target_id)
    state.markers.add_relation(target_id, "ENGAGED_WITH", attacker_id)


def _seat(state, pid, round_num=1):
    """R0 公演位：SP=2 + 报名 + 固化本轮唯一公演位（与 round_manager R0 同序）。"""
    m9 = state.m9_system
    m9.set_sp(pid, 2)
    m9.register_performance(pid, round_num)
    m9.allocate_public_slot(round_num)


class MagicLedgerTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_initial_magic_and_cap(self) -> None:
        state, g3, t, _ = _make("p1")
        self.assertEqual(t.magic, int(_g3("magic_initial", 6)))
        self.assertEqual(t.magic_cap, int(_g3("magic_cap", 8)))

    def test_r0_recovery_outside_barrier(self) -> None:
        state, g3, t, _ = _make("p1")
        t.magic = 5
        t.on_round_start(2)
        self.assertEqual(t.magic, 6)          # 恢复 1
        t.on_round_start(2)                   # 同轮重复调用不双恢复
        self.assertEqual(t.magic, 6)
        t.on_round_start(3)
        self.assertEqual(t.magic, 7)
        # on_round_end 路径同样按轮恢复
        t.on_round_end(4)
        self.assertEqual(t.magic, 8)

    def test_cap_not_exceeded(self) -> None:
        state, g3, t, _ = _make("p1")
        t.magic = 8
        t.on_round_start(2)
        self.assertEqual(t.magic, 8)          # 封顶

    def test_no_recovery_inside_barrier(self) -> None:
        state, g3, t, others = _make("p1", "p2")
        _seat(state, "p1")
        msg, ok = t.execute_t0(g3)
        self.assertTrue(ok)
        t.magic = 3
        t.on_round_start(2)                   # 结界内不自然恢复
        self.assertEqual(t.magic, 3)


class OutsideProjectionTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_spiral_attack_root_no_persistent(self) -> None:
        state, g3, t, others = _make("p1", "p2")
        p2 = others[0]
        p2.hp = 50
        _lock(state, "p1", "p2")
        _set_sp(state, "p1", 1)
        msg, ok = t.execute_t0(g3)            # 默认首项：螺旋剑（伪）
        self.assertTrue(ok)
        self.assertEqual(p2.hp, 50 - t.spiral_damage)
        # 魔力已扣（初始 6 − 2）
        self.assertEqual(t.magic, 4)
        # 不占持续通道
        self.assertFalse(t.defense_marker)
        self.assertIsNone(t.rho_aias)
        self.assertIsNone(t.copy_weapon)

    def test_spiral_insufficient_magic(self) -> None:
        state, g3, t, others = _make("p1", "p2")
        _lock(state, "p1", "p2")
        t.magic = 1
        _set_sp(state, "p1", 1)
        msg, ok = t.execute_t0(g3)
        self.assertFalse(ok)
        self.assertIn("魔力不足", msg)

    def test_dual_blade_offense_bonus(self) -> None:
        state, g3, t, others = _make("p1", "p2")
        p2 = others[0]
        p2.hp = 50
        _engage(state, "p1", "p2")
        _set_sp(state, "p1", 1)
        g3.controller = _RecordingController("双刀·攻势")
        msg, ok = t.execute_t0(g3)
        self.assertTrue(ok)
        # 拳击基础 2 + 双刀攻击方加值 2 = 4
        base = int(round(float(make_weapon("拳击").get_effective_damage())))
        self.assertEqual(p2.hp, 50 - base - t.dual_blade_attack_bonus)
        self.assertEqual(t.magic, 5)

    def test_dual_blade_defense_marker_reduced_and_consumed(self) -> None:
        state, g3, t, others = _make("p1", "p2")
        p2 = others[0]
        _set_sp(state, "p1", 1)
        g3.controller = _RecordingController("双刀·守势")
        msg, ok = t.execute_t0(g3)
        self.assertTrue(ok)
        self.assertTrue(t.defense_marker)
        self.assertEqual(t.magic, 5)
        # 下一起来袭攻击：目标侧固定减伤（25% 下限约束）
        from engine.m9.combat import resolve_damage
        knife = make_weapon("小刀")
        result = resolve_damage(p2, g3, weapon=knife, game_state=state)
        raw = int(round(float(knife.get_effective_damage())))
        expected = max(1, raw - t.dual_blade_reduction)
        self.assertEqual(result["hp_damage"], expected)
        self.assertFalse(t.defense_marker)    # 标记已消耗

    def test_dual_blade_defense_marker_reduces_regular_crossfire(self) -> None:
        state, g3, t, others = _make("p1", "p2")
        p2 = others[0]
        t.defense_marker = True
        from engine.m9.combat import resolve_damage
        result = resolve_damage(p2, g3, weapon=None, game_state=state,
                                raw_damage_override=4,
                                damage_attribute_override="普通",
                                source_kind="g0_crossfire")
        self.assertEqual(result["hp_damage"], 2)   # R7 起普通管线：4−2 减伤
        self.assertFalse(t.defense_marker)         # 标记消耗

    def test_rho_aias_cover_absorbs_and_no_overflow(self) -> None:
        state, g3, t, others = _make("p1", "p2")
        p2 = others[0]
        _set_sp(state, "p1", 1)
        g3.controller = _RecordingController("七重圆环")
        msg, ok = t.execute_t0(g3)
        self.assertTrue(ok)
        self.assertIsNotNone(t.rho_aias)
        self.assertEqual(t.rho_aias["durability"], t.rho_aias_durability)
        self.assertEqual(t.magic, 4)
        # 远程直接攻击先命中圆环：小伤只磨耐久
        self.assertEqual(t.defend_ranged(p2, 3), 0)
        self.assertEqual(t.rho_aias["durability"], t.rho_aias_durability - 3)
        # 击破圆环的那次攻击不向 G3 溢出
        self.assertEqual(t.defend_ranged(p2, 99), 0)
        self.assertIsNone(t.rho_aias)
        # 圆环消失后 G3 正常承受
        self.assertEqual(t.defend_ranged(p2, 4), 4)

    def test_rho_aias_lost_on_leave_location(self) -> None:
        state, g3, t, others = _make("p1", "p2")
        p2 = others[0]
        t.rho_aias = {"durability": 8, "location": "公园"}
        g3.location = "医院"
        self.assertEqual(t.defend_ranged(p2, 3), 3)  # 离开地点 → 圆环失去
        self.assertIsNone(t.rho_aias)

    def test_copy_weapon_outside_ratio_half_up(self) -> None:
        state, g3, t, others = _make("p1", "p2")
        p2 = others[0]
        p2.hp = 50
        _set_sp(state, "p1", 1)
        g3.controller = _RecordingController("复制武器")
        msg, ok = t.execute_t0(g3)
        self.assertTrue(ok)
        self.assertIsNotNone(t.copy_weapon)
        self.assertEqual(t.copy_weapon["name"], COPY_WEAPON_CLOSED_LIST[0])
        self.assertEqual(t.magic, 5)
        # 结界外 ×outside_copy_ratio（half-up）
        _engage(state, "p1", "p2")
        result = t.use_copy_attack(g3, p2)
        self.assertIsNotNone(result)
        base = int(t.copy_weapon["base_damage"])
        expected = int(_g3("outside_copy_ratio", 0.75) * base + 0.5)
        self.assertEqual(result["hp_damage"], expected)
        self.assertEqual(p2.hp, 50 - expected)

    def test_copy_weapon_witness_priority(self) -> None:
        state, g3, t, others = _make("p1", "p2")
        p2 = others[0]
        p2.hp = 50
        state.event_log.append({"type": "attack", "weapon": "魔法弹幕",
                                "round": 1})
        _set_sp(state, "p1", 1)
        g3.controller = _RecordingController("复制武器")
        msg, ok = t.execute_t0(g3)
        self.assertTrue(ok)
        self.assertEqual(t.copy_weapon["name"], "魔法弹幕")
        # 5 × 0.75 = 3.75 → half-up 4
        _engage(state, "p1", "p2")
        result = t.use_copy_attack(g3, p2)
        self.assertEqual(result["hp_damage"], 4)

    def test_outside_single_persistent_channel_replaced(self) -> None:
        state, g3, t, others = _make("p1", "p2")
        _set_sp(state, "p1", 1)
        g3.controller = _RecordingController("双刀·守势")
        t.execute_t0(g3)
        self.assertTrue(t.defense_marker)
        # 新持续投影替换旧：圆环创建清除防御标记
        g3.controller = _RecordingController("七重圆环")
        t.execute_t0(g3)
        self.assertFalse(t.defense_marker)
        self.assertIsNotNone(t.rho_aias)


class BarrierExpansionTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_expansion_captures_and_designates(self) -> None:
        state, g3, t, others = _make("p1", "p2")
        p2 = others[0]
        _seat(state, "p1")
        msg, ok = t.execute_t0(g3)            # 默认首项：展开固有结界
        self.assertTrue(ok)
        self.assertTrue(t.barrier_active)
        self.assertEqual(t.barrier_location, "公园")
        self.assertEqual(t.captured, ["p2"])
        self.assertEqual(t.main_target, "p2")
        self.assertEqual(t.barrier_anchor_durability, t.barrier_anchor_max)
        self.assertEqual(t.temp_magic, t.public_temp_magic)   # 2 SP → 临时魔力
        self.assertEqual(state.m9_system.get_sp("p1"), 0)     # 公演 SP 归零
        # 无当前通缉时结界不能全局冻结无关警务。
        self.assertFalse(state.m9_police.suspended)
        self.assertEqual(p2.location, "公园")

    def test_expansion_without_seat_fails_clean(self) -> None:
        state, g3, t, others = _make("p1", "p2")
        # 公演位被 p2 占住：G3 无位 → 展开取消且不改状态
        _seat(state, "p2", 1)
        _set_sp(state, "p1", 2)
        msg, ok = t.execute_t0(g3)
        self.assertFalse(ok)
        self.assertFalse(t.barrier_active)
        self.assertEqual(state.m9_system.get_sp("p1"), 2)  # 预检失败不扣 SP

    def test_expansion_requires_same_location_player(self) -> None:
        """裁决：同地点没有其他存活玩家时不可展开无限剑制（空结界无意义）。"""
        state, g3, t, _ = _make("p1")
        _seat(state, "p1")
        # 孤身一人 → 拒绝，不扣 SP、不开结界
        msg, ok = t.execute_t0(g3)
        self.assertFalse(ok)
        self.assertIn("同地点没有其他玩家", msg)
        self.assertFalse(t.barrier_active)
        self.assertEqual(state.m9_system.get_sp("p1"), 2)
        # 异地点对手 → 仍拒绝
        p2 = Player("p2", "远方", controller=_RecordingController())
        state.add_player(p2)
        p2.hp = 20
        p2.max_hp = 20
        p2.location = "医院"
        msg, ok = t.execute_t0(g3)
        self.assertFalse(ok)
        self.assertFalse(t.barrier_active)
        # 同地点对手 → 可展开
        p2.location = "公园"
        msg, ok = t.execute_t0(g3)
        self.assertTrue(ok)
        self.assertTrue(t.barrier_active)
        self.assertEqual(t.captured, ["p2"])

    def test_cross_boundary_both_directions(self) -> None:
        state, g3, t, others = _make("p1", "p2")
        _seat(state, "p1")
        t.execute_t0(g3)
        # 展开后新到原地点的单位仍在外部普通地点，不补入结界
        p3 = Player("p3", "局外人", controller=_RecordingController())
        state.add_player(p3)
        p3.location = "公园"
        p3.hp = 20
        self.assertNotIn("p3", t.captured)
        self.assertTrue(t.cross_boundary(p3, others[0]))   # 外→内
        self.assertTrue(t.cross_boundary(others[0], p3))   # 内→外
        self.assertFalse(t.cross_boundary(g3, others[0]))  # 内→内
        self.assertFalse(t.cross_boundary(p3, p3))
        # G3 自身攻击目标合法性排除结界外单位
        _lock(state, "p1", "p2")
        ids = [a.player_id for a in t._legal_targets(g3, ranged=True)]
        self.assertEqual(ids, ["p2"])

    def test_inside_actor_cannot_enumerate_or_submit_lock_on_outsider(self) -> None:
        from cli.parser import parse
        from cli.validator import validate
        from engine.action_enumerator import build_action_options

        state, g3, t, others = _make("p1", "p2")
        g3.is_awake = True
        others[0].is_awake = True
        _seat(state, "p1")
        t.execute_t0(g3)
        g3.weapons.append(make_weapon("远程魔法弹幕"))
        outsider = Player("p3", "局外人", controller=_RecordingController())
        state.add_player(outsider)
        outsider.location = "公园"
        outsider.hp = 20
        outsider.is_awake = True

        options = build_action_options(g3, state, ["lock"])
        self.assertNotIn("lock 局外人", options.get("lock", []))
        valid, reason = validate(parse("lock 局外人", "p1"), g3, state)
        self.assertFalse(valid)
        self.assertIn("无限剑制", reason)

        valid, reason = validate(parse("lock 玩家1", "p1"), g3, state)
        self.assertTrue(valid, reason)

    def test_no_internal_grants_and_not_control_immune(self) -> None:
        state, g3, t, others = _make("p1", "p2")
        g3.is_petrified = True               # 石化不受结界豁免
        _seat(state, "p1")
        t.execute_t0(g3)
        self.assertTrue(g3.is_petrified)     # 控制不自动解除/不免疫
        grants = list(state.m9_system.ledger._grants.values())
        full_extra = [g for g in grants if g.kind == "full_extra"]
        self.assertEqual(full_extra, [])     # 无内部完整额外行动
        for g in grants:
            self.assertEqual(g.actor_id, "p1")  # 只授予 G3 自身公演槽

    def test_free_initial_config_sword_array(self) -> None:
        state, g3, t, others = _make("p1", "p2")
        _seat(state, "p1")
        g3.controller = _RecordingController("剑阵", "剑阵", "弹道校正")
        msg, ok = t.execute_t0(g3)
        self.assertTrue(ok)
        self.assertIsNotNone(t.sword_array)
        self.assertEqual(t.sword_array["function"], "ballistic")
        self.assertEqual(t.magic, 6)         # 免费初始配置不扣魔力


class InsideChainTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def _expanded(self):
        state, g3, t, others = _make("p1", "p2")
        _seat(state, "p1")
        msg, ok = t.execute_t0(g3)
        assert ok, msg
        _lock(state, "p1", "p2")
        return state, g3, t, others

    def test_chain_segments_gale_and_budget(self) -> None:
        state, g3, t, others = self._expanded()
        p2 = others[0]
        p2.hp = 100
        t.magic = 20                          # 预算 20 + 4 临时 = 24
        # 赤原猎风目标：SP=2 且在公演队列
        _set_sp(state, "p2", 2)
        state.m9_system.register_performance("p2", 1)
        msg, ok = t.execute_t0(g3)            # 默认首项：螺旋剑连发
        self.assertTrue(ok)
        chain = t.chain
        self.assertIsNotNone(chain)
        # 单根至多 3 发（max_repeats=2）；累计耗魔 ≤ 预算（finish_root 清零，
        # 以记账字段断言）
        self.assertEqual(len(chain.segments), 3)
        self.assertEqual(t.last_chain_spent, 12)
        self.assertEqual(sum(s.magic_paid for s in chain.segments), 12)
        self.assertLessEqual(t.last_chain_spent, 24)
        self.assertEqual(p2.hp, 100 - 3 * t.spiral_damage)
        # 赤原猎风：SP −1 + 永久移出公演队列
        self.assertEqual(state.m9_system.get_sp("p2"), 1)
        self.assertFalse(state.m9_system.queue.is_in_queue("p2"))
        # 账本：只扣 12 普通魔力
        self.assertEqual(t.magic, 8)
        self.assertEqual(t.temp_magic, 4)

    def test_chain_precheck_stops_at_budget(self) -> None:
        state, g3, t, others = self._expanded()
        p2 = others[0]
        p2.hp = 100
        t.magic = 2                           # 预算 2 + 4 = 6 → 只够两段
        msg, ok = t.execute_t0(g3)
        self.assertTrue(ok)
        self.assertEqual(len(t.chain.segments), 2)
        self.assertEqual(t.last_chain_spent, 6)
        self.assertEqual(t.magic, 0)          # 普通 2 先扣
        self.assertEqual(t.temp_magic, 0)     # 临时 4 后扣


class SwordArrayAndIdealBurnTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def _expanded(self, config="兵装（螺旋剑）"):
        state, g3, t, others = _make("p1", "p2")
        _seat(state, "p1")
        g3.controller = _RecordingController(config)
        msg, ok = t.execute_t0(g3)
        assert ok, msg
        return state, g3, t, others

    def test_sword_array_creation_costs_and_ballistic_flag(self) -> None:
        state, g3, t, others = self._expanded()
        g3.controller = _RecordingController("剑阵", "弹道校正")
        msg, ok = t.execute_t0(g3)
        self.assertTrue(ok)
        self.assertEqual(t.magic, 4)          # 创建扣 sword_array_cost=2
        self.assertEqual(t.sword_array["function"], "ballistic")
        # 弹道校正：命中加值不得混入伤害 A。
        self.assertEqual(t.m9_modify_outgoing(g3, others[0], None, 10.0),
                         10.0)
        self.assertEqual(
            t.m9_accuracy_bonus(g3, others[0], None, "normal"),
            t.sword_array_hit_bonus)
        # 非 G3 攻击不受影响
        self.assertEqual(t.m9_modify_outgoing(others[0], g3, None, 10.0), 10.0)

    def test_sword_array_intercept_first_ranged_attack(self) -> None:
        state, g3, t, others = self._expanded()
        g3.controller = _RecordingController("剑阵", "拦截")
        t.execute_t0(g3)
        p2 = others[0]
        self.assertEqual(t.defend_ranged(p2, 3), 0)   # 首次远程直接攻击被拦截
        self.assertEqual(t.sword_array["durability"], t.sword_array_durability - 3)
        self.assertTrue(t.sword_array["intercept_used"])
        # 本轮后续远程攻击不再拦截（若圆环存在则由圆环承接）
        t.rho_aias = {"durability": 8, "location": "公园"}
        self.assertEqual(t.defend_ranged(p2, 2), 0)   # 圆环承接
        self.assertEqual(t.rho_aias["durability"], 6)
        # R0 额度重置
        t.on_round_start(2)
        self.assertFalse(t.sword_array["intercept_used"])

    def test_ideal_burn_unlock_and_collapse_formula(self) -> None:
        state, g3, t, others = self._expanded()
        p2 = others[0]
        p2.hp = 100
        t.magic = 20
        # 式样 1：剑阵（创建根完成）
        g3.controller = _RecordingController("剑阵", "弹道校正")
        msg, ok = t.execute_t0(g3)
        self.assertTrue(ok)
        # 式样 2：七重圆环（创建根完成）
        g3.controller = _RecordingController("投影创建", "七重圆环")
        msg, ok = t.execute_t0(g3)
        self.assertTrue(ok)
        # 式样 3：双刀·守势（创建根完成）→ 达到 3 种解锁理想燃烧
        g3.controller = _RecordingController("投影创建", "双刀·守势")
        msg, ok = t.execute_t0(g3)
        self.assertTrue(ok)
        self.assertTrue(t.ideal_burn)
        self.assertEqual(t.ideal_styles,
                         {"剑阵", "七重圆环", "双刀·守势"})
        # 幻想崩坏：5 + 2×min(3, 5) = 11；弹道校正只影响命中。
        g3.controller = _RecordingController("幻想崩坏")
        msg, ok = t.execute_t0(g3)
        self.assertTrue(ok)
        expected = 11
        self.assertEqual(p2.hp, 100 - expected)
        self.assertFalse(t.barrier_active)    # 结算后无条件解除
        self.assertEqual(t.magic, 0)          # 消耗全部剩余魔力
        self.assertEqual(t.temp_magic, 0)
        self.assertFalse(state.m9_police.suspended)

    def test_collapse_requires_ideal_burn_and_min_magic(self) -> None:
        state, g3, t, others = self._expanded()
        self.assertFalse(t._collapse_legal())  # 未理想燃烧
        t.ideal_burn = True
        t.magic = 1
        t.temp_magic = 0
        self.assertFalse(t._collapse_legal())  # 剩余魔力 < 最低下限
        t.magic = 2
        self.assertTrue(t._collapse_legal())

    def test_ideal_burn_cost_reduction(self) -> None:
        state, g3, t, others = self._expanded()
        t.ideal_burn = True
        self.assertEqual(t._proj_cost("sword_array_cost"),
                         max(0, t.sword_array_cost - t.ideal_burn_cost_reduction))
        g3.controller = _RecordingController("剑阵", "弹道校正")
        msg, ok = t.execute_t0(g3)
        self.assertTrue(ok)
        self.assertEqual(t.magic, 6 - (t.sword_array_cost
                                       - t.ideal_burn_cost_reduction))


class UpkeepTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def _expanded(self):
        state, g3, t, others = _make("p1", "p2")
        _seat(state, "p1")
        msg, ok = t.execute_t0(g3)
        assert ok, msg
        return state, g3, t, others

    def test_establishment_r4_no_tick(self) -> None:
        state, g3, t, _ = self._expanded()
        self.assertEqual(t.established_round, 1)
        t.on_round_end(1)                     # 建立轮 R4 不收费不 tick
        self.assertEqual(t.barrier_rounds, 0)
        self.assertEqual(t.magic, 6)
        self.assertTrue(t.barrier_active)

    def test_upkeep_insufficient_forces_dismiss_no_partial_payment(self) -> None:
        state, g3, t, others = self._expanded()
        t.on_round_end(1)                     # 建立轮
        t.magic = 1
        t.temp_magic = 0
        # 维持 = 1 + 1×1 = 2 > 1 → 不部分支付、不扣任何魔力、强制解除
        t.on_round_end(2)
        self.assertFalse(t.barrier_active)
        self.assertEqual(t.magic, 1)          # 魔力未扣
        self.assertEqual(t.temp_magic, 0)
        self.assertEqual(others[0].location, "公园")  # 单位返回原地点
        self.assertFalse(state.m9_police.suspended)   # 警察恢复
        self.assertIsNone(t.main_target)

    def test_max_barrier_rounds_dismiss(self) -> None:
        state, g3, t, _ = self._expanded()
        t.magic = 20
        t.on_round_end(1)                     # 建立轮不 tick
        for r in (2, 3, 4, 5):
            t.on_round_end(r)
            self.assertTrue(t.barrier_active)
            self.assertEqual(t.barrier_rounds, r - 1)
        t.on_round_end(6)                     # 第 5 tick = 硬上限 → 强制解除
        self.assertFalse(t.barrier_active)

    def test_upkeep_includes_wall_and_array(self) -> None:
        state, g3, t, _ = self._expanded()
        t.rho_aias = {"durability": 8, "location": "公园"}
        t.sword_array = {"function": "ballistic", "durability": 6}
        t.magic = 20
        t.on_round_end(2)
        expected = (t.barrier_base_upkeep + t.barrier_per_unit_upkeep
                    + t.barrier_wall_upkeep + t.barrier_array_upkeep)
        self.assertEqual(t.magic, 20 - expected)


class BreakTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_weapon_attack_anchor_reduces_durability(self) -> None:
        state, g3, t, others = _make("p1", "p2")
        _seat(state, "p1")
        t.execute_t0(g3)
        p2 = others[0]
        knife = make_weapon("小刀")
        msg, ok = t.weapon_attack_anchor(p2, knife)
        self.assertTrue(ok)
        self.assertEqual(t.barrier_anchor_durability,
                         t.barrier_anchor_max - 4)

    def test_break_barrier_fixed_power_and_zero_dismisses(self) -> None:
        state, g3, t, others = _make("p1", "p2")
        _seat(state, "p1")
        t.execute_t0(g3)
        p2 = others[0]
        msg, ok = t.break_barrier(p2)
        self.assertTrue(ok)
        self.assertEqual(t.barrier_anchor_durability,
                         t.barrier_anchor_max - t.break_action_power)
        t.barrier_anchor_durability = 1
        msg, ok = t.break_barrier(p2)         # 归零 → 先收尾再强制解除
        self.assertTrue(ok)
        self.assertFalse(t.barrier_active)
        self.assertEqual(p2.location, "公园")

    def test_anchor_attack_no_damage_to_units(self) -> None:
        state, g3, t, others = _make("p1", "p2")
        _seat(state, "p1")
        t.execute_t0(g3)
        p2 = others[0]
        hp_before = p2.hp
        t.weapon_attack_anchor(p2, make_weapon("小刀"))
        self.assertEqual(p2.hp, hp_before)    # 无伤害单位/击杀


class DeathCleanupTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_g3_death_dismisses_barrier(self) -> None:
        state, g3, t, others = _make("p1", "p2")
        p2 = others[0]
        _seat(state, "p1")
        t.execute_t0(g3)
        g3.hp = 0
        t.on_round_end(2)                     # 惰性死亡清理路径
        self.assertFalse(t.barrier_active)
        self.assertEqual(p2.location, "公园")
        self.assertFalse(state.m9_police.suspended)
        self.assertEqual(t.temp_magic, 0)
        # cleanup_on_death / force_dismiss 幂等
        t.cleanup_on_death()
        t.force_dismiss()
        self.assertFalse(t.barrier_active)

    def test_death_clears_projections(self) -> None:
        state, g3, t, others = _make("p1", "p2")
        _seat(state, "p1")
        t.execute_t0(g3)
        t.rho_aias = {"durability": 8, "location": "公园"}
        t.sword_array = {"function": "ballistic", "durability": 6}
        t.defense_marker = True
        t.copy_weapon = {"name": "小刀", "base_damage": 4, "attribute": "普通"}
        t.cleanup_on_death()
        self.assertIsNone(t.rho_aias)
        self.assertIsNone(t.sword_array)
        self.assertIsNone(t.copy_weapon)
        self.assertFalse(t.defense_marker)


class IsolationAndBorrowTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_legacy_mythland_untouched(self) -> None:
        from talents.g3_mythland import Mythland
        legacy = Mythland("p1", GameState())
        self.assertEqual(legacy.uses_remaining, 2)      # v2exp 独立次数保持
        self.assertEqual(legacy.name, "神话之外")

    def test_g6_borrow_simple_projection(self) -> None:
        state, g3, t, others = _make("p1", "p2")
        p2 = others[0]
        p2.hp = 50
        _lock(state, "p1", "p2")
        msg = Mythland9.borrow_simple_projection(g3, state)
        self.assertIsInstance(msg, str)
        self.assertIn("螺旋剑", msg)
        self.assertEqual(p2.hp, 50 - t.spiral_damage)
        # 借用不读 G3 账本
        self.assertEqual(t.magic, 6)
        # simple_projection 同路径
        p2.hp = 50
        msg2 = Mythland9.simple_projection(g3, state)
        self.assertIn("螺旋剑", msg2)
        self.assertEqual(p2.hp, 50 - t.spiral_damage)


class ArmamentPoolTest(unittest.TestCase):
    """兵装池 + 超限灌注（RFC v0.3 §7.2）。"""

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def _expanded(self, initial="兵装（螺旋剑）"):
        """展开结界（免费初始配置按给定选择），返回 (state, g3, t, others)。"""
        state, g3, t, others = _make("p1", "p2")
        g3.controller = _RecordingController(initial)
        _seat(state, "p1")
        msg, ok = t.execute_t0(g3)
        self.assertTrue(ok, msg)
        self.assertTrue(t.barrier_active)
        return state, g3, t, others

    def test_dual_blade_enters_pool_and_attacks(self) -> None:
        state, g3, t, others = self._expanded()
        p2 = others[0]
        # 结界内投影创建双刀·守势 → 入池
        g3.controller = _RecordingController("投影创建", "双刀·守势")
        msg, ok = t.execute_t0(g3)
        self.assertTrue(ok, msg)
        self.assertIn("双刀", t.armament_pool)
        # 兵装攻击：双刀近战 → 目标 p2，基础伤害 dual_blade_base_damage
        _engage(state, "p1", "p2")
        g3.controller = _RecordingController("兵装攻击", "双刀", "不灌注")
        before = p2.hp
        msg, ok = t.execute_t0(g3)
        self.assertTrue(ok, msg)
        self.assertEqual(p2.hp, before - _g3("dual_blade_base_damage", 3))
        self.assertIn("双刀", t.armament_pool)  # 结算后兵装保留

    def test_copy_weapon_enters_pool_and_attacks(self) -> None:
        state, g3, t, others = self._expanded()
        p2 = others[0]
        g3.controller = _RecordingController("投影创建", "复制武器", "小刀")
        msg, ok = t.execute_t0(g3)
        self.assertTrue(ok, msg)
        member = "复制武器:小刀"
        self.assertIn(member, t.armament_pool)
        _engage(state, "p1", "p2")
        g3.controller = _RecordingController("兵装攻击", member, "不灌注")
        before = p2.hp
        msg, ok = t.execute_t0(g3)
        self.assertTrue(ok, msg)
        self.assertEqual(p2.hp, before - t.copy_weapon["base_damage"])

    def test_overload_consumes_temp_magic_and_bonus(self) -> None:
        state, g3, t, others = self._expanded()
        p2 = others[0]
        g3.controller = _RecordingController("投影创建", "双刀·守势")
        t.execute_t0(g3)
        temp_before = t.temp_magic
        _engage(state, "p1", "p2")
        g3.controller = _RecordingController("兵装攻击", "双刀", "灌注")
        before = p2.hp
        msg, ok = t.execute_t0(g3)
        self.assertTrue(ok, msg)
        cost = _g3("armament_overload_cost", 2)
        bonus = _g3("armament_overload_bonus", 2)
        self.assertEqual(t.temp_magic, temp_before - cost)
        self.assertEqual(p2.hp, before - (_g3("dual_blade_base_damage", 3)
                                          + bonus))
        self.assertIn("超限灌注", msg)

    def test_attack_requires_pool_and_pool_cleared_on_dismiss(self) -> None:
        state, g3, t, others = self._expanded()
        msg, ok = t._armament_attack(g3)
        self.assertFalse(ok)
        self.assertIn("兵装池为空", msg)
        # 创建后强制解除 → 池清空
        g3.controller = _RecordingController("投影创建", "双刀·守势")
        t.execute_t0(g3)
        self.assertTrue(t.armament_pool)
        t.force_dismiss("test")
        self.assertEqual(t.armament_pool, [])


if __name__ == "__main__":
    unittest.main()
