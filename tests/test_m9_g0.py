"""M9 G0 砂狼白子*Terror 天赋适配器单测（G0 RFC v0.3）。

覆盖：AR 替换弓与箭矢转化、即演召唤无人机（SP/HP 代价、唯一性、R4 倒计时
建立轮不 tick）、协同攻击神秘属性（三属性试算取最高 H + 无人机科技追加伤害、
探针耐久快照还原）、十字炮火（DIRECT_DAMAGE 全员含自身、护甲跳过、无人机消失）、
遗物支援技（摧毁/保留无人机、T1 无视护甲×mult、T7 临时复活、G4 余烬护甲、
G5 追忆池与简化诗篇）、调整呼吸（致死触发/免疫/到期退场/absolute_death 直死）、
撤退语义（PP 冻结、遗留登记、不可行动）、无人机被攻击（非犯罪 + 高影响关注）、
T6 遗物临时演出警察、双管线（legacy Combo 在 v2exp 下仍可用）。

G0 已在 registry 中启用；单测直接构造 adapter 以隔离 setup 随机性，槽级接入另由
``tests/test_m9_g0_e2e.py`` 覆盖。
"""
import unittest
from types import SimpleNamespace

from controllers.base import PlayerController

from engine import experiments
from engine.balance import get as bget
from engine.game_state import GameState
from models.equipment import (
    ArmorLayer, ArmorPiece, Item, WeaponRange,
)
from models.player import Player
from utils.attribute import Attribute

from engine.m9.gate import ensure_state_mechanisms
from engine.m9.talents.g0 import AR_WEAPON_NAME, ShirokoTerror9


def _enable(*flags):
    experiments.reset()
    for f in flags:
        experiments.enable(f)


def _g0(key, default):
    return bget("m9_talents_extended", "g0", key, default=default)


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
    """创建 state + 玩家（hp20）+ G0 天赋；pids[0] 为 G0。"""
    state = GameState()
    ensure_state_mechanisms(state)
    state.current_round = 1
    g0 = None
    others = []
    for i, pid in enumerate(pids):
        p = Player(pid, f"玩家{i}", controller=_RecordingController())
        state.add_player(p)
        p.max_hp = 20
        p.hp = 20
        p.location = "公园"
        if i == 0:
            g0 = p
        else:
            others.append(p)
    t = ShirokoTerror9(g0.player_id, state)
    g0.talent = t
    return state, g0, t, others


def _set_sp(state, pid, value):
    state.m9_system.set_sp(pid, value)


def _seat(state, pid, round_num=1):
    """R0 公演位：SP=2 + 报名 + 固化本轮唯一公演位（与 round_manager R0 同序）。"""
    m9 = state.m9_system
    m9.set_sp(pid, 2)
    m9.register_performance(pid, round_num)
    m9.allocate_public_slot(round_num)


def _summon(state, t, g0):
    """即演召唤无人机（SP=1）。"""
    _set_sp(state, g0.player_id, 1)
    msg, ok = t.execute_t0(g0)
    assert ok, msg
    return msg


def _relic_support(state, t, g0, item_name, slot, *choices):
    """标记遗物 + 遗物支援公演（需先召唤无人机）。返回 (msg, ok)。"""
    t.mark_relic(item_name, slot)
    g0.items.append(Item(item_name, "weapon"))
    g0.controller = _RecordingController("遗物支援技", *choices)
    _seat(state, g0.player_id)
    return t.execute_t0(g0)


def _tech_armor():
    """科技甲：科技防御高、魔法防御低（神秘属性应选魔法）。"""
    return ArmorPiece("科技甲", Attribute.TECH, ArmorLayer.OUTER, 5,
                      priority=100,
                      defense_map={"科技": 5, "魔法": 1, "普通": 3},
                      durability=10)


def _events(state, event_type):
    return [e for e in state.event_log if e["type"] == event_type]


class ARAndWeaponTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_ar_starts_as_g0_weapon(self) -> None:
        """AR 替换弓：BLACK FANG 465 起始武器，初始弹匣，箭矢转化记事件。"""
        state, g0, t, _ = _make("p1")
        names = [w.name for w in g0.weapons if w]
        self.assertIn(AR_WEAPON_NAME, names)
        self.assertNotIn("弓", names)
        ar = g0.get_weapon(AR_WEAPON_NAME)
        self.assertEqual(ar.base_damage, int(_g0("ar_base_damage", 3)))
        self.assertEqual(ar.weapon_range, WeaponRange.RANGED)
        self.assertEqual(ar.attribute, Attribute.TECH)
        self.assertEqual(t.magazine, int(_g0("ar_magazine", 30)))
        # 箭矢→子弹：1 箭 = 3 弹（v1 仅记事件）
        self.assertEqual(t.convert_arrow_gain(2),
                         2 * int(_g0("arrow_to_bullet_ratio", 3)))
        self.assertTrue(_events(state, "g0_arrow_to_bullet"))
        # 世界援助状态机随 adapter 挂载
        self.assertIsNotNone(t.world_aid)


class DroneSummonTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_summon_cost_and_singleton(self) -> None:
        state, g0, t, _ = _make("p1")
        option = t.get_t0_option(g0)
        self.assertEqual(option["m9_kind"], "g0_drone_summon")
        msg = _summon(state, t, g0)
        self.assertTrue(t.drone is not None)
        self.assertIsNotNone(t.drone)
        self.assertEqual(t.drone["hp"], int(_g0("drone_hp", 5)))
        self.assertEqual(t.drone["duration_left"],
                         int(_g0("drone_duration", 3)))
        self.assertEqual(t.drone["established_round"], 1)
        # HP 代价 = max(1, half_up(当前 HP × cost%))（读 balance）
        pct = int(_g0("drone_hp_cost", 20))
        cost = max(1, int(round(20 * pct / 100 + 1e-9)))
        self.assertEqual(g0.hp, 20 - cost)
        self.assertEqual(state.m9_system.get_sp("p1"), 0)
        # 第二次召唤（已有无人机）→ 失败，无人机不变
        _set_sp(state, "p1", 1)
        msg, ok = t.execute_t0(g0)
        self.assertFalse(ok)
        self.assertIn("无人机", msg)
        self.assertIsNotNone(t.drone)
        # 已有无人机且 SP<2：无 T0 选项
        self.assertIsNone(t.get_t0_option(g0))

    def test_drone_duration_ticks_establishment_no_tick(self) -> None:
        from engine.balance import get as _bget
        dur = int(_bget("m9_talents_extended", "g0", "drone_duration", default=3))
        state, g0, t, _ = _make("p1")
        _summon(state, t, g0)
        state.current_round = 1
        t.on_round_end(1)          # 建立轮 R4 不 tick
        self.assertEqual(t.drone["duration_left"], dur)
        state.current_round = 2
        t.on_round_end(2)
        self.assertEqual(t.drone["duration_left"], dur - 1)
        state.current_round = 3
        t.on_round_end(3)          # 归零 → 消失
        self.assertIsNone(t.drone)
        self.assertTrue(_events(state, "g0_drone_vanish"))

    def test_drone_follows_g0_location(self) -> None:
        state, g0, t, _ = _make("p1")
        _summon(state, t, g0)
        self.assertEqual(t.drone_location(), "公园")
        g0.location = "医院"
        self.assertEqual(t.drone_location(), "医院")


class CooperativeAttackTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def _attack(self, state, g0, target, weapon=None):
        from combat.damage_resolver import resolve_damage
        return resolve_damage(g0, target, weapon, state)

    def test_cooperative_attack_mystery_attribute_and_drone_bonus(self) -> None:
        """无人机在场：三属性试算取最高 H（魔法）；护甲耐久按所选属性结算一次；
        无人机科技追加伤害独立结算。"""
        dmg = int(_g0("ar_base_damage", 3))
        state, g0, t, (p2,) = _make("p1", "p2")
        _summon(state, t, g0)
        piece = _tech_armor()
        p2.add_armor(piece)
        ar = g0.get_weapon(AR_WEAPON_NAME)
        self._attack(state, g0, p2, ar)
        # R19 数值：ar_base_damage=1 → 三属性 H 均为 1（并列取科技）；
        # drone_bonus_damage=0 → 无追加伤害
        magic_h = max(dmg - 1, 1)
        self.assertEqual(p2.hp, 20 - magic_h)
        self.assertEqual(piece.durability, 10)  # 吸收 0，探针还原后不磨损
        coop = _events(state, "g0_cooperative_attack")
        self.assertEqual(len(coop), 1)
        self.assertEqual(coop[0]["attribute"], "科技")

    def test_plain_ar_attack_without_drone(self) -> None:
        """无无人机：普通 AR 科技属性攻击，无神秘试算、无追加伤害。"""
        dmg = int(_g0("ar_base_damage", 3))
        state, g0, t, (p2,) = _make("p1", "p2")
        piece = _tech_armor()
        p2.add_armor(piece)
        ar = g0.get_weapon(AR_WEAPON_NAME)
        self._attack(state, g0, p2, ar)
        # 科技防御 5 → max(dmg-5, 1)；护甲吸收 = dmg - H 磨耐久
        h = max(dmg - 5, 1)
        self.assertEqual(p2.hp, 20 - h)
        self.assertEqual(piece.durability, 10 - (dmg - h))
        self.assertFalse(_events(state, "g0_cooperative_attack"))

    def test_mystery_probes_restore_armor_durability(self) -> None:
        """三属性探针不磨损护甲耐久（快照还原）。"""
        state, g0, t, (p2,) = _make("p1", "p2")
        _summon(state, t, g0)
        piece = _tech_armor()
        p2.add_armor(piece)
        ar = g0.get_weapon(AR_WEAPON_NAME)
        self._attack(state, g0, p2, ar)
        self.assertEqual(piece.durability, 10)  # R19：AR=1，吸收 0


class CrossfireTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_crossfire_direct_damage_all_units_including_self(self) -> None:
        """十字炮火：DIRECT_DAMAGE 跳过护甲防御，地点全员（含 G0）受全额伤，
        警察单位也受伤，无人机消失，SP 扣 2。"""
        state, g0, t, (p2,) = _make("p1", "p2")
        _summon(state, t, g0)
        shield = ArmorPiece("盾牌", Attribute.ORDINARY, ArmorLayer.OUTER, 2,
                            priority=100, defense_map={"普通": 2}, durability=8)
        p2.add_armor(shield)
        station = state.m9_police
        roster = station.ensure_roster()
        roster[0].location = "公园"
        _seat(state, "p1")
        msg, ok = t.execute_t0(g0)   # 默认选择：十字炮火
        self.assertTrue(ok)
        dmg = int(_g0("crossfire_damage", 3))
        self.assertEqual(p2.hp, 19)                  # 盾牌防 2：1→1（R7 起护甲可减免）
        self.assertEqual(shield.durability, 8)       # 吸收 0，耐久不动
        # G0：即演 HP 代价 → 十字炮火代价（对当前 HP）→ 自伤 dmg
        d_pct = int(_g0("drone_hp_cost", 20))
        c_pct = int(_g0("crossfire_hp_cost", 20))
        after_drone = 20 - max(1, int(round(20 * d_pct / 100 + 1e-9)))
        cross_cost = max(1, int(round(after_drone * c_pct / 100 + 1e-9)))
        self.assertEqual(g0.hp, after_drone - cross_cost - dmg)
        unit_hp = int(bget("m9_system", "police", "unit_hp", default=12))
        self.assertEqual(roster[0].hp, unit_hp - dmg)  # 警察单位受波及
        self.assertIsNone(t.drone)                  # 释放后消失
        self.assertEqual(state.m9_system.get_sp("p1"), 0)
        self.assertTrue(_events(state, "g0_crossfire"))


class RelicSupportTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_relic_support_destroys_relic_keeps_drone(self) -> None:
        """遗物支援：摧毁遗物（从 items/relics 移除）、付 20% HP、无人机保留。"""
        state, g0, t, _ = _make("p1")
        _summon(state, t, g0)
        msg, ok = _relic_support(state, t, g0, "遗物小刀", "T1",
                                 "遗物小刀（T1）")
        self.assertTrue(ok)
        self.assertEqual(t.relics, [])
        self.assertFalse(any(it.name == "遗物小刀" for it in g0.items))
        # HP 代价按 balance：即演（对 20）→ 遗物支援（对当前 HP）
        d_pct = int(_g0("drone_hp_cost", 20))
        r_pct = int(_g0("relic_support_hp_cost", 20))
        after_drone = 20 - max(1, int(round(20 * d_pct / 100 + 1e-9)))
        relic_cost = max(1, int(round(after_drone * r_pct / 100 + 1e-9)))
        self.assertEqual(g0.hp, after_drone - relic_cost)
        self.assertIsNotNone(t.drone)   # 无人机保留
        self.assertTrue(t.t1_relic_armed)
        self.assertTrue(_events(state, "g0_relic_destroyed"))

    def test_t1_relic_next_ar_attack_ignores_armor_xmult(self) -> None:
        """T1 遗物：下次 AR 攻击无视全部护甲，伤害 ×relic_t1_mult。"""
        state, g0, t, (p2,) = _make("p1", "p2")
        _summon(state, t, g0)
        piece = _tech_armor()
        p2.add_armor(piece)
        _relic_support(state, t, g0, "遗物小刀", "T1", "遗物小刀（T1）")
        t._vanish_drone("test")   # 本次只验证纯 T1 强化（无无人机协同）
        from combat.damage_resolver import resolve_damage
        ar = g0.get_weapon(AR_WEAPON_NAME)
        raw = ar.base_damage
        expected = max(1, int(raw * float(_g0("relic_t1_mult", 1.5)) + 0.5))
        resolve_damage(g0, p2, ar, state)
        self.assertEqual(p2.hp, 20 - expected)   # 全额伤害，无视科技防御 5
        self.assertEqual(piece.durability, 10)   # 护甲未吸收
        self.assertFalse(t.t1_relic_armed)       # 一次性消耗

    def test_t7_relic_revive_keeps_1_hp(self) -> None:
        """T7 遗物：下次致死伤害保留 1 HP（一次性）。"""
        state, g0, t, (p2,) = _make("p1", "p2")
        _summon(state, t, g0)
        _relic_support(state, t, g0, "圣物", "T7", "圣物（T7）")
        self.assertTrue(t.t7_revive)
        from combat.damage_resolver import resolve_damage
        r = resolve_damage(p2, g0, weapon=None, game_state=state,
                           raw_damage_override=999,
                           damage_attribute_override="普通")
        self.assertEqual(g0.hp, 1)
        self.assertFalse(r.get("killed"))
        self.assertFalse(t.t7_revive)            # 标记消耗
        self.assertEqual(t.breath_uses_left, 1)  # 不消耗调整呼吸

    def test_g4_relic_ash_layers_absorb(self) -> None:
        """G4 遗物：余烬护甲每层吸收 1 点整数伤害后消耗。"""
        state, g0, t, (p2,) = _make("p1", "p2")
        _summon(state, t, g0)
        _relic_support(state, t, g0, "圣物", "G4", "圣物（G4）")
        self.assertEqual(t.g4_ash_layers, int(_g0("relic_g4_stacks", 4)))
        d_pct = int(_g0("drone_hp_cost", 20))
        r_pct = int(_g0("relic_support_hp_cost", 20))
        after_drone = 20 - max(1, int(round(20 * d_pct / 100 + 1e-9)))
        relic_cost = max(1, int(round(after_drone * r_pct / 100 + 1e-9)))
        hp_after = after_drone - relic_cost
        self.assertEqual(g0.hp, hp_after)
        from engine.m9.combat import resolve_damage
        resolve_damage(p2, g0, weapon=None, game_state=state,
                       raw_damage_override=3, damage_attribute_override="普通")
        self.assertEqual(g0.hp, hp_after)          # 3 伤全部吸收（3 层）
        self.assertEqual(t.g4_ash_layers, 1)
        resolve_damage(p2, g0, weapon=None, game_state=state,
                       raw_damage_override=3, damage_attribute_override="普通")
        self.assertEqual(g0.hp, hp_after - 2)      # 最后一层吸收 1，剩余 2
        self.assertEqual(t.g4_ash_layers, 0)

    def test_g5_relic_memory_cap_and_reduced_poem(self) -> None:
        """G5 遗物：追忆 +6（cap 12）；满 12 公演可花费全部追忆兑换简化诗篇
        （游侠 → ranger_chase 标记），不摧毁遗物。"""
        state, g0, t, _ = _make("p1")
        _summon(state, t, g0)
        _relic_support(state, t, g0, "涟漪一", "G5", "涟漪一（G5）")
        self.assertEqual(t.relic_memory, int(_g0("relic_g5_memory", 6)))
        _relic_support(state, t, g0, "涟漪二", "G5", "涟漪二（G5）")
        self.assertEqual(t.relic_memory, int(_g0("relic_memory_cap", 12)))
        # 追忆已满：本次公演选择兑换诗篇（不摧毁第三件遗物）
        msg, ok = _relic_support(state, t, g0, "备用遗物", "T1",
                                 "兑换简化诗篇（12 追忆）", "游侠")
        self.assertTrue(ok)
        self.assertEqual(t.m9_poem_markers.get("simplified:ranger_chase"), True)
        self.assertEqual(t.relic_memory, 0)
        self.assertTrue(any(r["name"] == "备用遗物" for r in t.relics))
        self.assertIsNotNone(t.drone)

    def test_relic_support_requires_relic(self) -> None:
        state, g0, t, _ = _make("p1")
        _summon(state, t, g0)
        g0.controller = _RecordingController("遗物支援技")
        _seat(state, "p1")
        msg, ok = t.execute_t0(g0)
        self.assertFalse(ok)
        self.assertIn("遗物", msg)


class BreathAndRetreatTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def _lethal(self, state, p2, g0, source_kind=None):
        from engine.m9.combat import resolve_damage
        return resolve_damage(p2, g0, weapon=None, game_state=state,
                              raw_damage_override=999,
                              damage_attribute_override="普通"
                              if source_kind is None else "__无视__",
                              source_kind=source_kind)

    def test_breath_on_lethal_then_immune_then_retreat(self) -> None:
        """呼吸重设计：致死→免疫2轮→触发4轮后 HP≤50% → 立刻退场。"""
        state, g0, t, (p2,) = _make("p1", "p2")
        r = self._lethal(state, p2, g0)
        self.assertEqual(r.get("m9_kind"), "g0_breath")
        self.assertFalse(r.get("killed"))
        self.assertTrue(t.breath_active)
        self.assertEqual(g0.hp, int(_g0("breath_min_hp", 1)))
        self.assertEqual(t.breath_uses_left, 0)
        # 调整呼吸：免疫后续普通攻击伤害
        r2 = self._lethal(state, p2, g0)
        self.assertEqual(r2["hp_damage"], 0)
        self.assertEqual(g0.hp, 1)
        # 免疫期 2 轮（建立轮 R4 不 tick）到期：不退场
        duration = int(_g0("breath_duration", 2))
        t.on_round_end(1)
        for round_num in range(2, 1 + duration):
            t.on_round_end(round_num)
            self.assertTrue(t.breath_active)
        t.on_round_end(1 + duration)
        self.assertFalse(t.breath_active)
        self.assertFalse(t.is_retreated())          # 呼吸结束不再立即退场
        # 触发 4 轮后（T+4）HP 仍 ≤50% → 立刻退场
        t.on_round_end(2 + duration)
        self.assertFalse(t.is_retreated())
        t.on_round_end(5)                          # T=1 → deadline=5
        self.assertTrue(t.is_retreated())
        self.assertTrue(state.m9_pp.is_frozen("p1"))
        self.assertIsNone(t.get_t0_option(g0))
        self.assertFalse(t.is_drone_present())
        # 撤退：装备作为遗留登记，无击杀关系
        leavings = _events(state, "g0_retreat_leavings")
        self.assertEqual(len(leavings), 1)
        self.assertIn(AR_WEAPON_NAME, leavings[0]["weapons"])
        retreat = _events(state, "g0_retreat")
        self.assertEqual(retreat[0]["no_kill_credit"], True)

    def test_breath_forfeit_heals_and_above_threshold_survives(self) -> None:
        """免疫期内 forfeit 回血 breath_forfeit_heal：两次跨过 40% 止损线 → 不退场。"""
        from engine.balance import get as bget
        heal = int(bget("m9_talents_extended", "g0",
                        "breath_forfeit_heal", default=4))
        threshold_pct = float(bget(
            "m9_talents_extended", "g0",
            "breath_recovery_threshold_pct", default=40))
        state, g0, t, (p2,) = _make("p1", "p2")
        self._lethal(state, p2, g0)
        self.assertEqual(g0.hp, 1)
        msg = t.m9_on_forfeit(g0)
        self.assertIn("恢复", msg)
        self.assertEqual(g0.hp, 1 + heal)
        self.assertTrue(_events(state, "g0_breath_forfeit_heal"))
        # 呼吸期外 forfeit 不回血
        t.breath_active = False
        self.assertEqual(t.m9_on_forfeit(g0), "")
        self.assertEqual(g0.hp, 1 + heal)
        t.breath_active = True
        # 第二次 forfeit：两次回血后按 T+4 阈值判定
        t.m9_on_forfeit(g0)
        final_hp = min(20, 1 + 2 * heal)
        self.assertEqual(g0.hp, final_hp)
        for round_num in (1, 2, 3, 4, 5):
            t.on_round_end(round_num)
        should_retreat = final_hp <= 20 * threshold_pct / 100.0
        self.assertEqual(t.is_retreated(), should_retreat)
        if should_retreat:
            self.assertTrue(_events(state, "g0_retreat"))
        else:
            self.assertFalse(t.is_retreated())
            self.assertTrue(_events(state, "g0_breath_recovered"))

    def test_breath_blocks_direct_damage(self) -> None:
        """免疫全部伤害：DIRECT_DAMAGE 也在呼吸期内归零（终焉真伤除外）。"""
        from types import SimpleNamespace
        state, g0, t, (p2,) = _make("p1", "p2")
        t.breath_active = True
        hit = SimpleNamespace(damage=5.0, direct_damage=True)
        t.m9_modify_incoming(hit)
        self.assertEqual(hit.damage, 0)
        t.breath_active = False
        hit2 = SimpleNamespace(damage=5.0, direct_damage=True)
        t.m9_modify_incoming(hit2)
        self.assertEqual(hit2.damage, 5.0)

    def test_breath_ends_full_hp_continues(self) -> None:
        state, g0, t, (p2,) = _make("p1", "p2")
        self._lethal(state, p2, g0)
        self.assertTrue(t.breath_active)
        g0.hp = g0.max_hp           # 呼吸期内回满
        duration = int(_g0("breath_duration", 2))
        for round_num in (1, 2, 3, 4, 5):
            t.on_round_end(round_num)
        self.assertFalse(t.breath_active)
        self.assertFalse(t.is_retreated())
        self.assertEqual(g0.hp, g0.max_hp)
        self.assertTrue(_events(state, "g0_breath_end"))

    def test_absolute_death_goes_straight_to_dead(self) -> None:
        state, g0, t, (p2,) = _make("p1", "p2")
        r = self._lethal(state, p2, g0, source_kind="g7_terror")
        self.assertTrue(r.get("killed"))
        self.assertTrue(r.get("absolute_dead"))
        self.assertEqual(g0.hp, 0)
        self.assertFalse(t.breath_active)        # 未进入调整呼吸
        self.assertEqual(t.breath_uses_left, 1)  # 不消耗次数

    def test_retreated_g0_not_targetable_and_no_actions(self) -> None:
        state, g0, t, (p2,) = _make("p1", "p2")
        self._lethal(state, p2, g0)
        for round_num in (1, 2, 3, 4, 5):        # 呼吸到期 + 4 轮止损
            t.on_round_end(round_num)
        self.assertTrue(t.is_retreated())
        msg, ok = t.execute_t0(g0)
        self.assertFalse(ok)
        self.assertIn("退场", msg)
        # 目标枚举排除已退场 G0
        units = t._units_at_location("公园")
        self.assertNotIn(g0, units)


class DualPipelineTest(unittest.TestCase):

    def tearDown(self) -> None:
        experiments.reset()

    def test_legacy_combo_still_functional_under_v2exp(self) -> None:
        """双管线：legacy Combo（talents/t5_combo.py）在 v2exp 下仍可导入/实例化，
        G0 为独立类（直接 import 不依赖注册表）。"""
        from talents.t5_combo import Combo
        _enable("v2exp")
        state = GameState()
        combo = Combo("p9", state)
        self.assertEqual(combo.player_id, "p9")
        self.assertEqual(combo.consecutive_actions, 0)  # legacy 状态保留
        self.assertNotEqual(Combo, ShirokoTerror9)       # 未移植、未继承
        self.assertNotIsInstance(ShirokoTerror9("p1", state), Combo)
        # 注册表 G0 已 IMPLEMENTED（fail-closed 由 T5 退役记录承担）
        from engine.m9.talent_registry import (
            registration_for_slot, require_selectable,
        )
        reg = registration_for_slot("G0")
        self.assertIsNotNone(reg)
        self.assertTrue(reg.is_selectable)
        require_selectable(reg)  # 不抛错


class DroneAttackTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_drone_attack_not_crime_attention_registered(self) -> None:
        state, g0, t, (p2,) = _make("p1", "p2")
        _summon(state, t, g0)
        attacker = SimpleNamespace(player_id="p2", name="玩家2")
        r = t.attack_drone(attacker, 2)
        self.assertTrue(r["success"])
        self.assertFalse(r["destroyed"])
        self.assertTrue(r["attention"])     # 高影响关注登记（SP 推进）
        self.assertEqual(t.drone["hp"], int(_g0("drone_hp", 5)) - 2)
        self.assertEqual(state.m9_system.get_sp("p1"), 1)  # 0 → 1（关注推进 SP）
        # 非犯罪：无警察犯罪记录
        self.assertFalse(getattr(state.police, "crime_records", {}))
        attacked = _events(state, "g0_drone_attacked")
        self.assertEqual(attacked[0]["not_a_crime"], True)
        # R19：drone_hp=3 → 第二次 1 伤摧毁；关注额度已用，不再登记
        r2 = t.attack_drone(attacker, 1)
        self.assertTrue(r2["destroyed"])
        self.assertFalse(r2["attention"])
        self.assertIsNone(t.drone)
        r3 = t.attack_drone(attacker, 999)
        self.assertFalse(r3["success"])
        d_pct = int(_g0("drone_hp_cost", 20))
        after_drone = 20 - max(1, int(round(20 * d_pct / 100 + 1e-9)))
        self.assertEqual(g0.hp, after_drone)  # 仅即演 HP 代价，无人机毁伤不传导


class TempPoliceTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_t6_relic_temp_police_enforces_current_wanted(self) -> None:
        """T6 遗物：临时演出警察只对当前通缉（同地点）执法一次；不结案。"""
        state, g0, t, (p2, p3) = _make("p1", "p2", "p3")
        p3.location = "公园"
        station = state.m9_police
        case = station.file_case("p2", "p3", evidence=2)
        self.assertIsNotNone(case)
        station.verify_case(case.case_id, lead_ok=True)
        _summon(state, t, g0)
        msg, ok = _relic_support(state, t, g0, "遗物警棍", "T6",
                                 "遗物警棍（T6）")
        self.assertTrue(ok)
        dmg = int(bget("m9_system", "police", "baton_damage", default=4))
        self.assertEqual(p3.hp, 20 - dmg)         # 执法一次
        self.assertTrue(_events(state, "g0_temp_police"))
        self.assertTrue(_events(state, "g0_temp_police_enforcement"))
        self.assertEqual(t.relics, [])            # 遗物已摧毁
        self.assertIsNotNone(station.open_wanted())  # 临时警察不推进/不结案

    def test_t6_relic_temp_police_skips_wanted_elsewhere(self) -> None:
        """通缉目标不在 G0 地点：临时警察不执行执法（仅记事件）。"""
        state, g0, t, (p2, p3) = _make("p1", "p2", "p3")
        p3.location = "医院"
        station = state.m9_police
        case = station.file_case("p2", "p3", evidence=2)
        station.verify_case(case.case_id, lead_ok=True)
        _summon(state, t, g0)
        msg, ok = _relic_support(state, t, g0, "遗物警棍", "T6",
                                 "遗物警棍（T6）")
        self.assertTrue(ok)
        self.assertEqual(p3.hp, 20)
        self.assertTrue(_events(state, "g0_temp_police"))
        self.assertFalse(_events(state, "g0_temp_police_enforcement"))


if __name__ == "__main__":
    unittest.main()
