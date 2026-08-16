"""M9 T4 六爻机制单测：SP 即演/公演门控、六结果（潜龙勿用穿甲 / 飞龙在天夺甲 /
元亨利贞金身 / 亢龙有悔禁武震荡 / 或跃完整额外行动 / 群龙无首遁走）、阴阳诗天机
指定与或跃禁令、G6 借用重掷、legacy 隔离。"""
import unittest
from types import SimpleNamespace

from engine import experiments
from engine.game_state import GameState
from models.equipment import ArmorLayer, ArmorPiece, Weapon, WeaponRange
from models.player import Player
from controllers.base import PlayerController
from utils.attribute import Attribute

from engine.m9.gate import ensure_state_mechanisms
from engine.m9.talents.t4 import Hexagram9


def _enable(*flags):
    experiments.reset()
    for f in flags:
        experiments.enable(f)


class _FixedChoiceController(PlayerController):
    """顺序出拳控制器：按预设序列返回，序列耗尽/非法项回退 options[0]。"""

    def __init__(self, *choices):
        self._choices = list(choices)
        self._i = 0

    def _next(self, options):
        if self._i < len(self._choices):
            c = self._choices[self._i]
            self._i += 1
            return c if c in options else options[0]
        return options[0]

    def get_command(self, player, game_state, available_actions, context=None):
        return "forfeit"

    def choose(self, prompt, options, context=None):
        return self._next(options)

    def choose_multi(self, prompt, options, max_count, min_count=0, context=None):
        return options[:max_count]

    def confirm(self, prompt, context=None):
        return True


def _make(choices=()):
    """M9 场景：p1 持有 Hexagram9，SP=1（add_player 时 register_player 开局值）。"""
    state = GameState()
    ensure_state_mechanisms(state)
    p = Player("p1", "T4", controller=_FixedChoiceController(*choices))
    p.location = "商店"
    state.add_player(p)
    p.max_hp = 20
    p.hp = 20
    t = Hexagram9("p1", state)
    p.talent = t
    return state, p, t


def _add_opponent(state, pid="p2", name="路人", choices=("石头",)):
    """加入对手（默认恒出石头）。"""
    other = Player(pid, name, controller=_FixedChoiceController(*choices))
    other.location = "商店"
    state.add_player(other)
    other.max_hp = 20
    other.hp = 20
    return other


class SPGatingTest(unittest.TestCase):
    """SP 门控与即演/公演消费。"""

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_no_opponent_no_option_no_sp(self) -> None:
        state, p, t = _make()
        self.assertIsNone(t.get_t0_option(p))
        msg, ok = t.execute_t0(p)
        self.assertFalse(ok)
        self.assertEqual(state.m9_system.get_sp("p1"), 1)  # SP 未动

    def test_low_sp_improvise_fails_sp_unchanged(self) -> None:
        state, p, t = _make()
        _add_opponent(state)
        state.m9_system.set_sp("p1", 0)
        self.assertIsNone(t.get_t0_option(p))
        msg, ok = t.execute_t0(p)
        self.assertFalse(ok)
        self.assertEqual(state.m9_system.get_sp("p1"), 0)

    def test_improvise_success_sp0(self) -> None:
        state, p, t = _make(choices=("即演", "路人", "剪刀", "路人"))
        other = _add_opponent(state, choices=("剪刀",))
        state.current_round = 1
        option = t.get_t0_option(p)
        self.assertIsNotNone(option)
        self.assertEqual(option["m9_kind"], "t4_hexagram")
        msg, ok = t.execute_t0(p)
        self.assertFalse(ok)  # 裁决 A：即演不消费回合
        self.assertEqual(state.m9_system.get_sp("p1"), 0)  # 即演 −1
        self.assertLess(other.hp, 20)  # 双剪刀 → 潜龙勿用天雷
        casts = [e for e in state.event_log if e["type"] == "hexagram_cast"]
        self.assertEqual(len(casts), 1)

    def test_public_success_sp0_with_seat(self) -> None:
        state, p, t = _make(choices=("公演", "路人", "石头"))
        other = _add_opponent(state, choices=("布",))  # 石头 vs 布 → 群龙无首
        state.current_round = 1
        state.m9_system.set_sp("p1", 2)
        state.m9_system.register_performance("p1", 1)
        state.m9_system.allocate_public_slot(1)
        msg, ok = t.execute_t0(p)
        self.assertTrue(ok)
        self.assertEqual(state.m9_system.get_sp("p1"), 0)  # 公演 −2
        # 石头 vs 布 → 群龙无首：对手位移 + 自身隐身
        self.assertNotEqual(other.location, "商店")
        self.assertTrue(p.is_invisible)


class HexagramResultsTest(unittest.TestCase):
    """六结果（直接调用对应 _xxx 方法，确定性断言）。"""

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_qianlong_pierce_damage_with_armor(self) -> None:
        """潜龙勿用：穿甲（防御按半计）→ 带甲目标受 qianlong−round(4×0.5) 伤；
        天雷目标可与猜拳对手不同。"""
        from engine.balance import get as bget
        dmg = int(bget("m9_talents_extended", "t4", "qianlong_pierce_damage",
                       default=6))
        state, p, t = _make(choices=("即演", "路人", "剪刀", "第三个"))
        other = _add_opponent(state, choices=("剪刀",))
        third = _add_opponent(state, pid="p3", name="第三个", choices=("石头",))
        third.armor.outer.append(ArmorPiece(
            "盾牌", Attribute.ORDINARY, ArmorLayer.OUTER, 1.0,
            defense_map={"普通": 4}, durability=20))
        state.m9_system.set_sp("p1", 1)
        msg, ok = t.execute_t0(p)
        self.assertFalse(ok)  # 裁决 A：即演不消费回合
        half_def = round(4 * 0.5)
        self.assertEqual(third.hp, 20 - (dmg - half_def))
        self.assertEqual(third.armor.outer[0].durability, 20 - half_def)
        self.assertEqual(other.hp, 20)  # 猜拳对手未被选为天雷目标

    def test_hojump_dispatches_single_full_extra(self) -> None:
        state, p, t = _make()
        other = _add_opponent(state)
        state.current_round = 1
        msg = t._scissors_paper(p, other)
        grants = state.m9_system.drain_pending_full_extra()
        self.assertEqual(len(grants), 1)
        g = grants[0]
        self.assertEqual(g.source_id, "t4_hexagram_hojump")
        self.assertEqual(g.kind, "full_extra")
        self.assertTrue(g.allow_instant)
        self.assertFalse(g.allow_public)
        self.assertEqual(getattr(p, "hexagram_extra_turn", 0), 0)  # 绝不写该属性
        # 同轮第二次 → 每轮每人上限 1，grant None
        msg2 = t._scissors_paper(p, other)
        self.assertIn("已满", msg2)
        self.assertEqual(state.m9_system.drain_pending_full_extra(), [])

    def test_kanglong_disables_weapon_for_rounds(self) -> None:
        from engine.balance import get as bget
        rounds = int(bget("m9_talents_extended", "t4", "weapon_disable_rounds",
                          default=2))
        state, p, t = _make()
        other = _add_opponent(state)
        other.weapons.append(
            Weapon("小刀", Attribute.ORDINARY, 2, WeaponRange.MELEE))
        state.current_round = 3
        msg = t._scissors_rock(p, other)
        knife = next(w for w in other.weapons if w.name == "小刀")
        self.assertTrue(knife._hexagram_disabled)
        self.assertEqual(t.disabled_weapons, [("p2", "小刀", 3 + rounds)])
        t.on_round_start(4)  # 禁用中
        self.assertTrue(knife._hexagram_disabled)
        t.on_round_start(3 + rounds)  # 第 3+rounds 轮 R0 解禁
        self.assertFalse(knife._hexagram_disabled)
        self.assertEqual(t.disabled_weapons, [])

    def test_kanglong_only_fist_shocks(self) -> None:
        state, p, t = _make()
        other = _add_opponent(state)  # 只有默认拳击
        msg = t._scissors_rock(p, other)
        self.assertTrue(other.is_shocked)
        self.assertTrue(state.markers.has("p2", "SHOCKED"))
        self.assertFalse(other.is_stunned)  # 眩晕已退役

    def test_kanglong_golden_body_blocks_shock(self) -> None:
        state, p, t = _make()
        other = _add_opponent(state)
        other.talent = SimpleNamespace(
            is_immune_to_debuff=lambda kind: kind == "shock")
        msg = t._scissors_rock(p, other)
        self.assertIn("免疫", msg)
        self.assertFalse(other.is_shocked)

    def test_golden_body_immunity_and_lethal_prevention(self) -> None:
        state, p, t = _make()
        state.current_round = 1
        t._both_paper(p, None)
        self.assertTrue(t.immunity_active)
        self.assertEqual(t.immunity_expire_round, 2)
        self.assertTrue(t.is_immune_to_debuff("stun"))
        hit = SimpleNamespace(attribute="普通", damage=10)
        t.m9_modify_incoming(hit)
        self.assertEqual(hit.damage, 0)  # 非无视属性归零
        hit2 = SimpleNamespace(attribute="__无视__", damage=10)
        t.m9_modify_incoming(hit2)
        self.assertEqual(hit2.damage, 10)  # 无视属性可穿透金身
        p.hp = 0
        kind = t.m9_on_lethal(p, None, "normal")
        self.assertEqual(kind, "t4_hexagram_golden_body")
        self.assertEqual(p.hp, 1.0)  # 致死预防
        # 下轮 R0 失效
        state.current_round = 2
        t.on_round_start(2)
        self.assertFalse(t.immunity_active)
        hit3 = SimpleNamespace(attribute="普通", damage=10)
        t.m9_modify_incoming(hit3)
        self.assertEqual(hit3.damage, 10)

    def test_feilong_copies_armor_target_untouched(self) -> None:
        state, p, t = _make()
        other = _add_opponent(state)
        piece = ArmorPiece("魔法护盾", Attribute.MAGIC, ArmorLayer.OUTER, 1.0,
                           can_regen=True)
        other.armor.outer.append(piece)
        msg = t._both_rock(p, other)
        copied = [a for a in p.armor.outer if a.name == "魔法护盾"]
        self.assertEqual(len(copied), 1)
        self.assertEqual(copied[0].attribute, Attribute.MAGIC)
        self.assertEqual(copied[0].layer, ArmorLayer.OUTER)
        # 目标护甲未动
        self.assertEqual(len(other.armor.get_active(ArmorLayer.OUTER)), 1)
        self.assertFalse(piece.is_broken)

    def test_qunlong_clears_relations_and_displaces(self) -> None:
        state, p, t = _make()
        other = _add_opponent(state)
        other.location = "医院"
        state.current_round = 1
        state.markers.add_relation("p1", "LOCKED_BY", "p2")
        state.markers.add_relation("p1", "DETECTED_BY", "p3")
        msg = t._rock_paper(p, other)
        self.assertEqual(state.markers.get_related("p1", "LOCKED_BY"), set())
        self.assertEqual(state.markers.get_related("p1", "DETECTED_BY"), set())
        self.assertTrue(p.is_invisible)
        self.assertTrue(state.markers.has("p1", "INVISIBLE"))
        allowed = {"商店", "魔法所", "医院", "军事基地", "警察局",
                   "home_p1", "home_p2"} - {"医院"}
        self.assertNotEqual(other.location, "医院")
        self.assertIn(other.location, allowed)

    def test_qunlong_shield_mode_immunizes_exile(self) -> None:
        state, p, t = _make()
        other = _add_opponent(state)
        other.location = "商店"
        other.talent = SimpleNamespace(shield_mode="架盾")
        msg = t._rock_paper(p, other)
        self.assertEqual(other.location, "商店")  # 强制放逐被免疫
        self.assertNotIn("传送", msg)
        self.assertTrue(p.is_invisible)


class YinyangTianjiTest(unittest.TestCase):
    """阴阳诗天机：公演指定非或跃结果；或跃被禁回退；归零移除。"""

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def _public_scene(self, choices):
        state, p, t = _make(choices=choices)
        other = _add_opponent(state)
        state.current_round = 1
        state.m9_system.set_sp("p1", 2)
        state.m9_system.register_performance("p1", 1)
        state.m9_system.allocate_public_slot(1)
        return state, p, t, other

    def test_specify_qianlong_deterministic(self) -> None:
        from engine.balance import get as bget
        dmg = int(bget("m9_talents_extended", "t4", "qianlong_pierce_damage",
                       default=6))
        state, p, t, other = self._public_scene(
            ("公演", "路人", "指定卦象", "潜龙勿用", "路人"))
        t.m9_poem_markers["yin_yang_tianji"] = 2
        msg, ok = t.execute_t0(p)
        self.assertTrue(ok)
        self.assertEqual(t.m9_poem_markers["yin_yang_tianji"], 1)  # −1
        self.assertEqual(other.hp, 20 - dmg)  # 指定潜龙勿用：穿甲，无需出拳
        casts = [e for e in state.event_log if e["type"] == "hexagram_cast"]
        self.assertEqual(len(casts), 1)

    def test_specify_hojump_banned_falls_back_to_roll(self) -> None:
        state, p, t, other = self._public_scene(
            ("公演", "路人", "指定卦象", "或跃在渊", "剪刀", "石头"))
        t.m9_poem_markers["yin_yang_tianji"] = 1
        msg, ok = t.execute_t0(p)
        self.assertTrue(ok)
        # 或跃被禁 → 回退正常出拳：剪刀 vs 石头 = 亢龙有悔 → 仅有拳击 → 震荡
        self.assertTrue(other.is_shocked)
        self.assertTrue(state.markers.has("p2", "SHOCKED"))
        self.assertEqual(t.m9_poem_markers["yin_yang_tianji"], 1)  # 未消耗

    def test_tianji_consumed_at_zero(self) -> None:
        state, p, t, other = self._public_scene(
            ("公演", "路人", "指定卦象", "亢龙有悔"))
        t.m9_poem_markers["yin_yang_tianji"] = 1
        msg, ok = t.execute_t0(p)
        self.assertTrue(ok)
        self.assertNotIn("yin_yang_tianji", t.m9_poem_markers)  # 0 → 移除
        self.assertTrue(other.is_shocked)  # 指定亢龙有悔 → 仅有拳击 → 震荡


class G6BorrowCoreTest(unittest.TestCase):
    """G6 借用核心：hexagram_cast 或跃重掷至非或跃，绝不派发完整额外行动。"""

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_hexagram_cast_rerolls_hojump(self) -> None:
        state, p, t = _make()
        other = _add_opponent(state, choices=("布", "石头"))
        p.controller = _FixedChoiceController("剪刀", "石头")
        state.current_round = 1
        msg = t.hexagram_cast(other)
        self.assertIn("借六爻", msg)
        self.assertIn("石头 vs 石头", msg)  # 或跃 → 重掷 → 飞龙在天
        self.assertIn("both_rock", msg)
        self.assertEqual(state.m9_system.drain_pending_full_extra(), [])
        self.assertEqual(getattr(p, "hexagram_extra_turn", 0), 0)


class LegacyIsolationTest(unittest.TestCase):
    """Profile 隔离：m9 关闭时 Hexagram9 完整回退 legacy 路径。"""

    def setUp(self) -> None:
        _enable("hp20")  # 不含 m9_rfc

    def tearDown(self) -> None:
        experiments.reset()

    def test_legacy_path_still_functional(self) -> None:
        state = GameState()
        p = Player("p1", "T4",
                   controller=_FixedChoiceController("路人", "剪刀", "剪刀", "路人"))
        state.add_player(p)
        p.max_hp = 20
        p.hp = 20
        t = Hexagram9("p1", state)
        p.talent = t
        other = Player("p2", "路人", controller=_FixedChoiceController("剪刀"))
        other.location = "商店"
        state.add_player(other)
        t.charges = 1
        option = t.get_t0_option(p)
        self.assertIsNotNone(option)
        msg, ok = t.execute_t0(p)
        self.assertTrue(ok)
        self.assertEqual(t.charges, 0)  # legacy 充能消费路径
        self.assertFalse(hasattr(state, "m9_system"))  # M9 机制未挂载

    def test_m9_instance_retires_charge_fields(self) -> None:
        _enable("m9_rfc", "hp20")
        state, _p, talent = _make()
        self.assertIsNotNone(state.m9_system)
        for field in ("charges", "max_charges", "round_counter"):
            self.assertFalse(hasattr(talent, field), field)


if __name__ == "__main__":
    unittest.main()
