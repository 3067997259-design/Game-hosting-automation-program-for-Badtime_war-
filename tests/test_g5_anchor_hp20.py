"""G5 锚定核心 hp20 测试（M7 第二阶段 5/5，§7.5）。

G5a 部分：信源统一评估器 simulate_path —— 命数对拍 numeric_v2 手算
（减法防御 + 耐久磨损 + 破甲后伤害上升 + 蓄力 + 多武器贪心 + horizon 截断）。
后续 G5b/c 的模板/窗口/破坏性/善见天 v3 测试在本文件追加。
"""
import unittest

from engine import experiments
from models.player import Player
from models.equipment import make_armor, Weapon, WeaponRange
from utils.attribute import Attribute
from controllers.forfeit_controller import ForfeitController
from engine import anchor_eval


def _target(hp=20, with_shield=True):
    experiments.enable("hp20")
    p = Player("t", "T", controller=ForfeitController())
    p.hp = hp
    p.max_hp = hp
    if with_shield:
        p.armor.outer.append(make_armor("盾牌"))   # hp20: 普通防御2, 耐久8
    return p


def _knife():
    return Weapon("小刀", Attribute.ORDINARY, 4, WeaponRange.MELEE)


class SimulatePathTest(unittest.TestCase):
    def tearDown(self):
        experiments.reset()

    def test_kill_through_armor(self):
        # 盾牌 普通防御2/耐久8：raw4→伤2/吸2，破甲4轮(8/2)→裸伤4，剩12HP/4=3轮 → 共7
        r = anchor_eval.simulate_path(_target(), [_knife()],
                                      [("attack", None)] * 15, goal="kill")
        self.assertTrue(r.achieved)
        self.assertEqual(r.rounds, 7)

    def test_kill_no_armor(self):
        # 裸目标 20HP，raw4→伤4 → ceil(20/4)=5
        r = anchor_eval.simulate_path(_target(with_shield=False), [_knife()],
                                      [("attack", None)] * 15, goal="kill")
        self.assertTrue(r.achieved)
        self.assertEqual(r.rounds, 5)

    def test_break_armor(self):
        r = anchor_eval.simulate_path(_target(), [_knife()],
                                      [("attack", None)] * 15,
                                      goal="break_armor", break_piece="盾牌")
        self.assertTrue(r.achieved)
        self.assertEqual(r.rounds, 4)   # 8/2

    def test_horizon_infeasible(self):
        r = anchor_eval.simulate_path(_target(), [_knife()],
                                      [("attack", None)] * 5, goal="kill", horizon=5)
        self.assertFalse(r.achieved)
        self.assertEqual(r.rounds, 5)

    def test_charge_weapon(self):
        g = Weapon("高斯步枪", Attribute.TECH, 6, WeaponRange.RANGED,
                   requires_charge=True, charged_damage=8)
        g.charge_mandatory = True
        naked = _target(with_shield=False)
        seq = [("charge", g)] + [("attack", g)] * 5
        r = anchor_eval.simulate_path(naked, [g], seq, goal="kill")
        self.assertTrue(r.achieved)
        self.assertEqual(r.rounds, 4)   # 蓄力1 + ceil(20/8)=3

    def test_mandatory_charge_cannot_fire_uncharged(self):
        g = Weapon("高斯步枪", Attribute.TECH, 6, WeaponRange.RANGED,
                   requires_charge=True, charged_damage=8)
        g.charge_mandatory = True
        r = anchor_eval.simulate_path(_target(with_shield=False), [g],
                                      [("attack", g)] * 5, goal="kill", horizon=5)
        self.assertFalse(r.achieved)   # 没蓄力，强制蓄力武器打不出

    def test_greedy_picks_higher_net(self):
        # 两把武器：弱普通 vs 强科技；裸目标下贪心选 raw 高者
        weak = Weapon("小刀", Attribute.ORDINARY, 3, WeaponRange.MELEE)
        strong = Weapon("电磁步枪", Attribute.TECH, 7, WeaponRange.RANGED)
        r = anchor_eval.simulate_path(_target(with_shield=False), [weak, strong],
                                      [("attack", None)] * 15, goal="kill")
        self.assertTrue(r.achieved)
        self.assertEqual(r.rounds, 3)   # ceil(20/7)=3，证明选了 strong

    def test_no_weapon_not_achieved(self):
        r = anchor_eval.simulate_path(_target(), [],
                                      [("attack", None)] * 15, goal="kill", horizon=15)
        self.assertFalse(r.achieved)


class ResolverEvalTest(unittest.TestCase):
    """G5b：anchor_resolver m7 分支走评估器 + 人类自传序列判断 + v1 回退。"""

    def tearDown(self):
        experiments.reset()

    def _setup(self):
        from engine.game_state import GameState
        st = GameState()
        c = Player("c", "C", controller=ForfeitController())
        c.hp = 20; c.max_hp = 20; c.location = "商店"
        c.weapons.append(Weapon("小刀", Attribute.ORDINARY, 4, WeaponRange.MELEE))
        st.add_player(c)
        t = Player("t", "T", controller=ForfeitController())
        t.hp = 20; t.max_hp = 20; t.location = "商店"
        t.armor.outer.append(make_armor("盾牌"))
        st.add_player(t)
        return st, c, t

    def test_m7_auto_floor_feasible(self):
        experiments.enable("hp20"); experiments.enable("m7_talents")
        from engine.anchor_resolver import AnchorVerifier
        st, c, t = self._setup()
        r = AnchorVerifier(st).verify_kill(c, t)
        # 同地点近战：find 1 + 杀 7 = 命数 8（horizon 8 → 可行）
        self.assertTrue(r.feasible)
        self.assertEqual(r.fate, 8)

    def test_m7_supplied_sequence_too_short(self):
        experiments.enable("hp20"); experiments.enable("m7_talents")
        from engine.anchor_resolver import AnchorVerifier
        st, c, t = self._setup()
        seq = [("find",)] + [("attack", None)] * 3
        r = AnchorVerifier(st).verify_sequence(c, t, "kill", seq)
        self.assertFalse(r.feasible)   # 4 轮不够

    def test_m7_supplied_sequence_enough(self):
        experiments.enable("hp20"); experiments.enable("m7_talents")
        from engine.anchor_resolver import AnchorVerifier
        st, c, t = self._setup()
        seq = [("find",)] + [("attack", None)] * 7
        r = AnchorVerifier(st).verify_sequence(c, t, "kill", seq)
        self.assertTrue(r.feasible)
        self.assertEqual(r.fate, 8)

    def test_variance_clamp_formula(self):
        # window=8, variance_base=2：命数8→变数2；命数越短变数越大、封顶 K-1=7
        experiments.enable("hp20"); experiments.enable("m7_talents")
        from engine.anchor_resolver import AnchorVerifier
        st, c, t = self._setup()
        r = AnchorVerifier(st).verify_kill(c, t)   # 命数8（find1+杀7）
        self.assertEqual(r.fate, 8)
        self.assertEqual(r.variance, 2)            # clamp(2+(8-8),0,7)=2
        # 自传短路径（命数3：3连击直接，假设够杀的裸目标）
        bare = Player("b", "B", controller=ForfeitController())
        bare.hp = 6; bare.max_hp = 6; bare.location = "商店"
        st.add_player(bare)
        # 小刀 raw4：裸 6HP → 2 击；同地点需 find1 → 命数3
        r2 = AnchorVerifier(st).verify_sequence(
            c, bare, "kill", [("find",), ("attack", None), ("attack", None)])
        self.assertTrue(r2.feasible)
        self.assertEqual(r2.variance, max(0, min(2 + (8 - r2.fate), 7)))

    def test_v1_falls_through_to_old_formula(self):
        # m7 关：走旧闭式公式（命数 = prep + ceil(总HP/裸伤)），不经评估器
        experiments.reset()
        from engine.anchor_resolver import AnchorVerifier
        st, c, t = self._setup()
        r = AnchorVerifier(st).verify_kill(c, t)
        self.assertIsNotNone(r)   # 旧公式仍可调用（feasible 取决于旧 cap 5）


class PioneerIncrementTest(unittest.TestCase):
    """G5c-3：开拓增量公式（防御 Σn_X + 移动 + 致残 + 治疗；删隐身）。"""

    def tearDown(self):
        experiments.reset()

    def _setup(self, attr_counts):
        experiments.enable("hp20"); experiments.enable("m7_talents")
        from engine.game_state import GameState
        from talents.g5.ripple import Ripple
        st = GameState()
        caster = Player("g5", "G5", controller=ForfeitController())
        caster.hp = 20; caster.max_hp = 20; caster.location = "商店"
        st.add_player(caster)
        t = Player("t", "T", controller=ForfeitController())
        t.hp = 20; t.max_hp = 20; t.location = "商店"
        st.add_player(t)
        g5 = Ripple("g5", st)
        caster.talent = g5
        g5.anchor_active = True
        g5.anchor_target_id = "t"
        g5.anchor_attr_counts = dict(attr_counts)
        g5._anchor_save_round_snapshots()   # 轮初快照
        return st, caster, t, g5

    def _add_armor(self, target, defense_map):
        from models.equipment import ArmorPiece, ArmorLayer
        target.armor.outer.append(
            ArmorPiece("X", None, ArmorLayer.OUTER, 10, defense_map=defense_map, durability=10))

    def test_defense_magic_plus_n(self):
        st, c, t, g5 = self._setup({"魔法": 2, "科技": 1})
        self._add_armor(t, {"魔法": 3})       # 抬高魔法防御
        self.assertEqual(g5._compute_pioneer_increment(t, c), 2)   # +n_魔

    def test_defense_tech_plus_one(self):
        st, c, t, g5 = self._setup({"魔法": 2, "科技": 1})
        self._add_armor(t, {"科技": 3})
        self.assertEqual(g5._compute_pioneer_increment(t, c), 1)   # +n_科

    def test_defense_normal_zero_when_not_in_path(self):
        st, c, t, g5 = self._setup({"魔法": 2})   # 路径无普通
        self._add_armor(t, {"普通": 3})           # 抬高普通防御
        self.assertEqual(g5._compute_pioneer_increment(t, c), 0)   # n_普=0 → +0

    def test_normal_counts_when_in_path(self):
        st, c, t, g5 = self._setup({"普通": 2})   # 路径有普通（普通非特例）
        self._add_armor(t, {"普通": 3})
        self.assertEqual(g5._compute_pioneer_increment(t, c), 2)   # 普通也算

    def test_move_and_heal_and_stun(self):
        st, c, t, g5 = self._setup({"魔法": 1})
        t.location = "魔法所"          # 脱离 G5（商店）
        t.hp = t.hp + 1               # 治疗
        c.is_stunned = True           # 致残发动者
        # 移动+1, 治疗+1, 致残+1 = 3（未抬防御 → 防御 0）
        self.assertEqual(g5._compute_pioneer_increment(t, c), 3)

    def test_no_pioneer_when_idle(self):
        st, c, t, g5 = self._setup({"魔法": 2})
        self.assertEqual(g5._compute_pioneer_increment(t, c), 0)


class BowAwareEvalTest(unittest.TestCase):
    """G5c-1：评估器弓感知——弓按 compute_shot 解析属性 + 吃 pierce + attr_counts。"""

    def tearDown(self):
        experiments.reset()

    def _caster_with_bow(self, modules):
        experiments.enable("hp20"); experiments.enable("m4_gear")
        c = Player("c", "C", controller=ForfeitController())
        c.hp = 20; c.max_hp = 20; c.location = "商店"
        c.bow_modules = list(modules)
        from models.equipment import Weapon, WeaponRange
        from utils.attribute import Attribute
        c.weapons.append(Weapon("弓", Attribute.ORDINARY, 3, WeaponRange.RANGED,
                                special_tags=["bow", "no_lock_required"]))
        return c

    def test_resolve_weapons_pierce_bow(self):
        c = self._caster_with_bow(["穿甲"])
        eff = anchor_eval.resolve_weapons(c)
        bow = next(w for w in eff if w.name == "弓")   # Player 默认带拳击，弓按名取
        self.assertEqual(bow.attribute.value, "科技")          # 穿甲改属性
        self.assertEqual(getattr(bow, "_pierce_factor", 1.0), 0.5)  # 穿甲 pierce

    def test_attr_counts_records_tech(self):
        c = self._caster_with_bow(["穿甲"])
        t = _target()           # 盾牌 普通防御2/耐久8
        weapons = anchor_eval.resolve_weapons(c)
        # 纯弓连射，attr_counts 应全记"科技"
        r = anchor_eval.simulate_path(t, weapons, [("attack", None)] * 15, goal="kill")
        self.assertTrue(r.achieved)
        self.assertEqual(set(r.attr_counts), {"科技"})
        self.assertEqual(r.attr_counts["科技"], r.rounds)

    def test_pierce_lowers_fate(self):
        # 目标科技外甲：穿甲弓比裸弓命数更低（穿甲真减防 → 伤更高）
        from models.equipment import ArmorPiece, ArmorLayer, Weapon, WeaponRange
        from utils.attribute import Attribute

        def tgt():
            experiments.enable("hp20")
            t = Player("t", "T", controller=ForfeitController())
            t.hp = 20
            # 三属性都防 4，封死拳击(普通)绕路，逼评估器用弓 → 凸显穿甲减防
            t.armor.outer.append(ArmorPiece("全防甲", None, ArmorLayer.OUTER, 30,
                                            defense_map={"科技": 4, "普通": 4, "魔法": 4},
                                            durability=30))
            return t

        pierce_bow = self._caster_with_bow(["穿甲", "力量"])  # 科技 + 伤5 + pierce0.5
        plain_bow = self._caster_with_bow(["力量"])           # 普通 + 伤5
        rp = anchor_eval.simulate_path(tgt(), anchor_eval.resolve_weapons(pierce_bow),
                                       [("attack", None)] * 30, goal="kill")
        rn = anchor_eval.simulate_path(tgt(), anchor_eval.resolve_weapons(plain_bow),
                                       [("attack", None)] * 30, goal="kill")
        self.assertTrue(rp.achieved and rn.achieved)
        self.assertLess(rp.rounds, rn.rounds)   # 穿甲命数更短


class ShinkenV3Test(unittest.TestCase):
    """G5c-4：善见天 v3——监控期目标对锚定者的闪避/隐身失效；替换已死 D4 加成。"""

    def tearDown(self):
        experiments.reset()

    def _pair(self):
        experiments.enable("hp20"); experiments.enable("m3_accuracy")
        experiments.enable("m7_talents")
        from engine.game_state import GameState
        st = GameState(); st.current_round = 3
        a = Player("a", "A", controller=ForfeitController()); a.location = "商店"
        t = Player("t", "T", controller=ForfeitController()); t.location = "商店"
        st.add_player(a); st.add_player(t)
        t.moved_this_round = True   # → move_evasion
        return st, a, t

    def test_anchored_evasion_nullified(self):
        from combat.accuracy import compute_hit_chance
        st, a, t = self._pair()
        base, _ = compute_hit_chance(a, t, None, st)   # 未锚定：吃移动闪避
        t._anchored_by = a.player_id
        anc, _ = compute_hit_chance(a, t, None, st)     # 锚定者：善见天 → 闪避失效
        self.assertGreater(anc, base)
        self.assertEqual(anc, 100)

    def test_non_anchorer_unaffected(self):
        from combat.accuracy import compute_hit_chance
        st, a, t = self._pair()
        other = Player("o", "O", controller=ForfeitController()); other.location = "商店"
        st.add_player(other)
        t._anchored_by = a.player_id           # 被 a 锚定
        c, _ = compute_hit_chance(other, t, None, st)   # other 非锚定者 → 闪避照常
        self.assertLess(c, 100)

    def test_v1_d4_shinken_preserved(self):
        experiments.reset()   # m7 关
        from engine.game_state import GameState
        from talents.g5.ripple import Ripple
        st = GameState(); p = Player("g", "G", controller=ForfeitController())
        st.add_player(p)
        g5 = Ripple("g", st); g5._anchor_d4_bonus_rounds = 5
        self.assertEqual(g5.on_d4_bonus(p), 3)   # v1 善见天 D4 保留

    def test_m7_d4_shinken_dead(self):
        experiments.enable("m7_talents")
        from engine.game_state import GameState
        from talents.g5.ripple import Ripple
        st = GameState(); p = Player("g", "G", controller=ForfeitController())
        st.add_player(p)
        g5 = Ripple("g", st); g5._anchor_d4_bonus_rounds = 5
        self.assertEqual(g5.on_d4_bonus(p), 0)   # m7：D4 退役 → 让位 v3


if __name__ == "__main__":
    unittest.main()
