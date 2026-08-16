"""M9 T2 剪刀手一突机制单测（阶段 5）：伤人未杀免罪、攻击回盾、零击杀隐身、
追猎反应（全局一次 + 地火诗免费通道）、即演/公演核心攻击、警觉关注、退役字段、
M9 禁用回退、G6 借用核心。注册表对 T2 保持 BLOCKED，一律直接 import ScissorRush9。"""

import unittest
from types import SimpleNamespace

from engine import experiments
from engine.game_state import GameState
from models.player import Player
from controllers.forfeit_controller import ForfeitController

from engine.m9.gate import ensure_state_mechanisms
from engine.m9.talents.t2 import ScissorRush9


class _InstantController(ForfeitController):
    def choose(self, prompt, options, context=None):
        if (context or {}).get("situation") == "t2_performance_mode":
            return "即演（1 SP）"
        return super().choose(prompt, options, context)


def _enable(*flags):
    experiments.reset()
    for f in flags:
        experiments.enable(f)


def _make():
    """标准 M9 场景：p1 携带 ScissorRush9，位于商店。"""
    state = GameState()
    ensure_state_mechanisms(state)
    p = Player("p1", "T2", controller=ForfeitController())
    state.add_player(p)
    t = ScissorRush9("p1", state)
    p.talent = t
    p.location = "商店"
    return state, p, t


def _add_opponent(state, pid="p2", name="P2", location="商店"):
    opp = Player(pid, name, controller=ForfeitController())
    state.add_player(opp)
    opp.location = location
    opp.is_awake = True
    return opp


def _give_weapon(player, name="小刀", damage=2, weapon_range=None):
    from models.equipment import Weapon, WeaponRange
    from utils.attribute import Attribute

    player.weapons.append(
        Weapon(name, Attribute.ORDINARY, damage, weapon_range or WeaponRange.MELEE)
    )


def _armor(name, durability=4, layer=None, attribute=None):
    """构造满耐久护甲；ArmorPiece 构造时 max_durability == durability，
    测试中如需损耗件，自行下调 piece.durability。"""
    from models.equipment import ArmorLayer, ArmorPiece
    from utils.attribute import Attribute

    return ArmorPiece(
        name,
        attribute or Attribute.MAGIC,
        layer or ArmorLayer.OUTER,
        1.0,
        durability=durability,
    )


def _engage(state, a_id, b_id):
    state.markers.add_relation(a_id, "ENGAGED_WITH", b_id)
    state.markers.add_relation(b_id, "ENGAGED_WITH", a_id)


def _lock(state, locker_id, target_id):
    state.markers.add_relation(target_id, "LOCKED_BY", locker_id)


class CrimeImmunityTest(unittest.TestCase):
    """伤人未杀免罪（M9）：非击杀攻击免罪，击杀正常记罪，无 extra_turn 键。"""

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_non_killing_attack_is_immune(self) -> None:
        state, p, t = _make()
        state.log_event("attack", attacker="p1", result={"killed": False})
        result = t.on_crime_check("p1", "伤害玩家")
        self.assertEqual(result, {"immune": True})
        self.assertNotIn("extra_turn", result)

    def test_killing_attack_records_normally(self) -> None:
        state, p, t = _make()
        state.log_event("attack", attacker="p1", result={"killed": True})
        result = t.on_crime_check("p1", "伤害玩家")
        # 击杀 → 正常记罪（None 即放行）；绝不携带 extra_turn
        self.assertIsNone(result)

    def test_other_player_not_affected(self) -> None:
        state, p, t = _make()
        state.log_event("attack", attacker="p2", result={"killed": False})
        self.assertIsNone(t.on_crime_check("p2", "伤害玩家"))

    def test_non_attack_crime_never_grants_extra_turn(self) -> None:
        state, p, t = _make()
        self.assertIsNone(t.on_crime_check("p1", "进入军事基地"))


class ShieldRecoveryTest(unittest.TestCase):
    """攻击回盾（M9 hp20 耐久路径）：偶数次命中护甲恢复自身同名外甲耐久。"""

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def _recovery(self) -> float:
        from engine.balance import get as bget
        return float(bget("m9_talents_extended", "t2",
                          "shield_recovery_durability", default=4))

    def test_even_hit_with_broken_recovers_durability(self) -> None:
        state, p, t = _make()
        my_piece = _armor("魔法护盾", durability=4)
        my_piece.durability = 2  # 已损耗 2/4
        p.armor.outer.append(my_piece)
        p2 = _add_opponent(state)
        p2.armor.outer.append(_armor("魔法护盾", durability=4))

        hit1 = SimpleNamespace(broken=[], a_phase_absorbed=0)
        t.m9_on_attack(hit1, p2)  # 第 1 次：奇数不计
        self.assertEqual(p.armor.outer[0].durability, 2)

        hit2 = SimpleNamespace(broken=["魔法护盾"], a_phase_absorbed=3)
        t.m9_on_attack(hit2, p2)  # 第 2 次：偶数 → 回盾
        self.assertEqual(p.armor.outer[0].durability,
                         min(2 + self._recovery(), 4))

    def test_recovery_caps_at_max_durability(self) -> None:
        state, p, t = _make()
        p.armor.outer.append(_armor("魔法护盾", durability=4))  # 已满
        p2 = _add_opponent(state)
        p2.armor.outer.append(_armor("魔法护盾", durability=4))
        t.attack_count = 3
        hit = SimpleNamespace(broken=["魔法护盾"], a_phase_absorbed=3)
        t.m9_on_attack(hit, p2)  # 第 4 次：已满不回溢
        self.assertEqual(p.armor.outer[0].durability, 4)

    def test_absorb_fallback_finds_outer_piece(self) -> None:
        state, p, t = _make()
        my_piece = _armor("魔法护盾", durability=4)
        my_piece.durability = 1
        p.armor.outer.append(my_piece)
        p2 = _add_opponent(state)
        p2.armor.outer.append(_armor("魔法护盾", durability=4))
        t.attack_count = 1
        hit = SimpleNamespace(broken=[], a_phase_absorbed=2)  # 无击碎，走吸收回退
        t.m9_on_attack(hit, p2)
        self.assertEqual(p.armor.outer[0].durability,
                         min(1 + self._recovery(), 4))

    def test_absorb_fallback_finds_inner_piece(self) -> None:
        from models.equipment import ArmorLayer

        state, p, t = _make()
        my_piece = _armor("魔法护盾", durability=4)
        my_piece.durability = 1
        p.armor.outer.append(my_piece)
        p2 = _add_opponent(state)
        p2.armor.inner.append(_armor("魔法护盾", durability=4, layer=ArmorLayer.INNER))
        t.attack_count = 1
        hit = SimpleNamespace(broken=[], a_phase_absorbed=2)
        t.m9_on_attack(hit, p2)
        self.assertEqual(p.armor.outer[0].durability,
                         min(1 + self._recovery(), 4))

    def test_iron_horus_excluded(self) -> None:
        state, p, t = _make()
        my_piece = _armor("铁之荷鲁斯", durability=4)
        my_piece.durability = 1
        p.armor.outer.append(my_piece)
        p2 = _add_opponent(state)
        p2.armor.outer.append(_armor("铁之荷鲁斯", durability=4))
        t.attack_count = 1
        hit = SimpleNamespace(broken=["铁之荷鲁斯"], a_phase_absorbed=2)
        t.m9_on_attack(hit, p2)  # 偶数次但护甲被排除
        self.assertEqual(p.armor.outer[0].durability, 1)


class StealthTest(unittest.TestCase):
    """零击杀隐身：stealth_on_zero_kills=True（M9 战斗钩子自动豁免）。"""

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_stealth_on_zero_kills_flag(self) -> None:
        state, p, t = _make()
        self.assertTrue(t.stealth_on_zero_kills)


class HuntReactionTest(unittest.TestCase):
    """追猎反应：公演根行动完成后合法 find/lock 一次；免费通道不受额度限制。"""

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_same_location_triggers_find(self) -> None:
        state, p, t = _make()
        _add_opponent(state, location="商店")
        t.m9_on_public_root_completed("p2")
        self.assertTrue(state.markers.has_relation("p1", "ENGAGED_WITH", "p2"))
        self.assertTrue(state.markers.has_relation("p2", "ENGAGED_WITH", "p1"))

    def test_hunt_consumed_once(self) -> None:
        state, p, t = _make()
        _add_opponent(state, location="商店")
        t.m9_on_public_root_completed("p2")
        self.assertTrue(t._hunt_used)
        # 解除关系后再触发：额度已消耗 → 不再反应
        state.markers.remove_relation("p1", "ENGAGED_WITH", "p2")
        state.markers.remove_relation("p2", "ENGAGED_WITH", "p1")
        t.m9_on_public_root_completed("p2")
        self.assertFalse(state.markers.has_relation("p1", "ENGAGED_WITH", "p2"))

    def test_different_location_no_reaction_without_ranged(self) -> None:
        state, p, t = _make()
        _add_opponent(state, location="医院")
        t.m9_on_public_root_completed("p2")
        self.assertFalse(state.markers.has_relation("p1", "ENGAGED_WITH", "p2"))
        self.assertFalse(state.markers.has_relation("p2", "LOCKED_BY", "p1"))

    def test_remote_performer_with_ranged_triggers_lock(self) -> None:
        from models.equipment import WeaponRange

        state, p, t = _make()
        _give_weapon(p, "远程魔法弹幕", 1, WeaponRange.RANGED)
        _add_opponent(state, location="医院")
        t.m9_on_public_root_completed("p2")
        self.assertTrue(state.markers.has_relation("p2", "LOCKED_BY", "p1"))

    def test_invisible_unseen_performer_does_not_trigger_hunt(self) -> None:
        state, p, t = _make()
        _add_opponent(state, location="商店")
        state.markers.add("p2", "INVISIBLE")

        t.m9_on_public_root_completed("p2")

        self.assertFalse(state.markers.has_relation("p1", "ENGAGED_WITH", "p2"))
        self.assertFalse(t._hunt_used)

    def test_free_hunt_reaction_after_used(self) -> None:
        state, p, t = _make()
        _add_opponent(state, location="商店")
        t.m9_on_public_root_completed("p2")
        self.assertTrue(t._hunt_used)
        # 地火诗免费通道：额度已消耗仍可追猎
        state.markers.remove_relation("p1", "ENGAGED_WITH", "p2")
        state.markers.remove_relation("p2", "ENGAGED_WITH", "p1")
        t.free_hunt_reaction("p2")
        self.assertTrue(state.markers.has_relation("p1", "ENGAGED_WITH", "p2"))
        self.assertTrue(t._hunt_used)  # 免费通道不触碰全局额度


class ImproviseTest(unittest.TestCase):
    """即演（−1 SP）：对已找到/已锁定目标攻击，无免费移动。"""

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_improvise_engaged_target(self) -> None:
        state, p, t = _make()
        _give_weapon(p, "小刀", 2)
        p2 = _add_opponent(state, location="商店")
        _engage(state, "p1", "p2")
        state.m9_system.set_sp("p1", 1)

        option = t.get_t0_option(p)
        self.assertIsNotNone(option)
        self.assertEqual(option["m9_kind"], "t2_improvise")

        msg, ok = t.execute_t0(p)
        self.assertTrue(ok)
        self.assertEqual(state.m9_system.get_sp("p1"), 0)  # −1 SP
        self.assertEqual(p2.hp, 18)  # 小刀 2 伤
        self.assertEqual(p.location, "商店")  # 无移动

    def test_improvise_locked_only_target_legal(self) -> None:
        state, p, t = _make()
        _give_weapon(p, "小刀", 2)
        p2 = _add_opponent(state, location="商店")
        _lock(state, "p1", "p2")  # 只有 LOCKED_BY，无 ENGAGED_WITH
        state.m9_system.set_sp("p1", 1)

        option = t.get_t0_option(p)
        self.assertIsNotNone(option)
        self.assertEqual(option["m9_kind"], "t2_improvise")

        msg, ok = t.execute_t0(p)
        self.assertTrue(ok)
        self.assertEqual(state.m9_system.get_sp("p1"), 0)
        self.assertEqual(p2.hp, 18)
        self.assertEqual(p.location, "商店")

    def test_improvise_no_target_precheck_fails_without_sp_spend(self) -> None:
        state, p, t = _make()
        _give_weapon(p, "小刀", 2)
        state.m9_system.set_sp("p1", 1)
        self.assertIsNone(t.get_t0_option(p))
        msg, ok = t.execute_t0(p)
        self.assertFalse(ok)
        self.assertEqual(state.m9_system.get_sp("p1"), 1)  # 预检先于消费

    def test_locked_shadow_actor_is_a_legal_target(self) -> None:
        state, p, t = _make()
        _give_weapon(p, "小刀", 2)
        shadow = Player("p2@shadow", "影身", controller=ForfeitController())
        shadow.location = "医院"
        shadow.hp = 20
        state.m9_shadows[shadow.player_id] = shadow
        state.markers.init_player(shadow.player_id)
        _lock(state, "p1", shadow.player_id)
        state.m9_system.set_sp("p1", 1)

        _msg, ok = t.execute_t0(p)

        self.assertTrue(ok)
        self.assertLess(shadow.hp, 20)


class PublicTest(unittest.TestCase):
    """公演（−2 SP）：先追演移动到目标地点，再核心攻击。"""

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_public_chase_move_then_attack(self) -> None:
        state, p, t = _make()
        _give_weapon(p, "小刀", 2)
        p2 = _add_opponent(state, location="医院")
        _lock(state, "p1", "p2")  # 锁定目标在另一地点
        state.m9_system.set_sp("p1", 2)
        state.m9_system.register_performance("p1", state.current_round)
        state.m9_system.allocate_public_slot(state.current_round)

        option = t.get_t0_option(p)
        self.assertIsNotNone(option)
        self.assertEqual(option["m9_kind"], "t2_public")

        msg, ok = t.execute_t0(p)
        self.assertTrue(ok)
        self.assertEqual(state.m9_system.get_sp("p1"), 0)  # −2 SP
        self.assertEqual(p.location, "医院")  # 追演已发生
        self.assertEqual(p.location, p2.location)
        self.assertEqual(p2.hp, 18)  # 核心攻击

    def test_public_same_location_no_move(self) -> None:
        state, p, t = _make()
        _give_weapon(p, "小刀", 2)
        p2 = _add_opponent(state, location="商店")
        _engage(state, "p1", "p2")
        state.m9_system.set_sp("p1", 2)
        state.m9_system.register_performance("p1", state.current_round)
        state.m9_system.allocate_public_slot(state.current_round)

        msg, ok = t.execute_t0(p)
        self.assertTrue(ok)
        self.assertEqual(state.m9_system.get_sp("p1"), 0)
        self.assertEqual(p.location, "商店")  # 同地点不追演
        self.assertEqual(p2.hp, 18)

    def test_sp2_can_choose_improvise_without_public_registration(self) -> None:
        state, p, t = _make()
        p.controller = _InstantController()
        _give_weapon(p, "小刀", 2)
        p2 = _add_opponent(state, location="商店")
        _engage(state, "p1", "p2")
        state.m9_system.set_sp("p1", 2)

        _msg, ok = t.execute_t0(p)

        self.assertTrue(ok)
        self.assertEqual(state.m9_system.get_sp("p1"), 1)
        self.assertEqual(state.m9_system.performance_kind, "improvise")
        self.assertFalse(state.m9_system.queue.is_in_queue("p1"))
        self.assertEqual(p2.hp, 18)


class RetiredFieldsTest(unittest.TestCase):
    """退役字段：不设任何 extra-turn 标志，legacy 字段从实例上删除。"""

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_legacy_fields_deleted_from_instance(self) -> None:
        state, p, t = _make()
        for attr in (
            "triggered_crime_types",
            "response_uses_remaining",
            "response_triggered_locations",
            "vigilance_uses",
        ):
            self.assertFalse(hasattr(t, attr), attr)

    def test_no_extra_turn_writes_on_player(self) -> None:
        state, p, t = _make()
        _add_opponent(state)
        state.log_event("attack", attacker="p1", result={"killed": True})
        t.on_crime_check("p1", "伤害玩家")
        t.on_find_someone(p, "p2")
        t.on_found_by_someone(p, "p2")
        # crime_extra_turn 是 Player 常量字段（恒 False）：绝不被置 True
        self.assertFalse(p.crime_extra_turn)
        # vigilance_extra_turn 是 legacy 动态字段：M9 下从未被创建
        self.assertFalse(hasattr(p, "vigilance_extra_turn"))
        self.assertEqual(getattr(p, "pending_extra_turns", 0), 0)


class AttentionTest(unittest.TestCase):
    """find/found 警觉：只登记普通关注（mark_attention +1 SP），无额外回合。"""

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_find_attention_once_per_round(self) -> None:
        state, p, t = _make()
        _add_opponent(state)
        state.current_round = 1
        state.m9_system.set_sp("p1", 1)
        t.on_find_someone(p, "p2")
        self.assertEqual(state.m9_system.get_sp("p1"), 2)  # 关注 +1
        t.on_find_someone(p, "p2")  # 同轮重复不叠加
        self.assertEqual(state.m9_system.get_sp("p1"), 2)
        self.assertFalse(hasattr(p, "vigilance_extra_turn"))

    def test_found_attention_once_per_round(self) -> None:
        state, p, t = _make()
        state.current_round = 2
        state.m9_system.set_sp("p1", 1)
        t.on_found_by_someone(p, "p3")
        self.assertEqual(state.m9_system.get_sp("p1"), 2)  # 关注 +1
        t.on_found_by_someone(p, "p3")
        self.assertEqual(state.m9_system.get_sp("p1"), 2)
        self.assertFalse(hasattr(p, "vigilance_extra_turn"))


class LegacyRegressionTest(unittest.TestCase):
    """M9 禁用回归：super() 路径（on_crime_check/describe_status 等）不崩。"""

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_m9_disabled_legacy_paths_survive(self) -> None:
        experiments.reset()  # m9_rfc 关闭
        state = GameState()
        p = Player("p1", "T2", controller=ForfeitController())
        state.add_player(p)
        t = ScissorRush9("p1", state)
        p.talent = t
        # 退役字段保留（m9 禁用时不删除），legacy 描述可用
        self.assertIsInstance(t.describe_status(), str)
        # legacy 犯罪路径：无攻击事件 → 免罪（不崩溃）
        self.assertEqual(t.on_crime_check("p1", "伤害玩家"), {"immune": True})
        # legacy 响应窗口路径
        self.assertFalse(t.check_response_window(None, "attack"))
        # legacy find/found 路径不崩溃
        t.on_find_someone(p, "p2")
        t.on_found_by_someone(p, "p2")
        self.assertTrue(getattr(p, "vigilance_extra_turn", False))


class CoreAttackBorrowTest(unittest.TestCase):
    """G6 借用核心：core_attack 直接攻击（无追演移动、无 find 前置）。"""

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_core_attack_damages_without_hunt_move(self) -> None:
        state, p, t = _make()
        _give_weapon(p, "小刀", 2)
        p2 = _add_opponent(state, location="医院")  # 另一地点、无任何关系
        state.m9_system.set_sp("p1", 2)

        msg, ok = t.core_attack("p2")
        self.assertTrue(ok)
        self.assertEqual(p2.hp, 18)
        self.assertEqual(p.location, "商店")  # 无追演移动
        self.assertEqual(state.m9_system.get_sp("p1"), 2)  # 不消费 SP

    def test_core_attack_dead_target_fails(self) -> None:
        state, p, t = _make()
        p2 = _add_opponent(state)
        p2.hp = 0
        msg, ok = t.core_attack("p2")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
