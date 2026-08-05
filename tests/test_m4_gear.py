"""M4 消耗层测试：经济 / 弓 / 模块 / 灼烧 / 钩索 / 退役 / v1 回归。"""
import random
import unittest

from engine import experiments
from engine.game_state import GameState
from models.player import Player
from controllers.forfeit_controller import ForfeitController


def _enable_m4():
    for f in ("k_initiative", "hp20", "m3_accuracy", "m4_gear"):
        experiments.enable(f)


def _player(pid, state, loc="商店"):
    p = Player(pid, f"玩家{pid}", controller=ForfeitController())
    p.is_awake = True
    p.location = loc
    state.add_player(p)
    return p


class EconomyTest(unittest.TestCase):

    def setUp(self):
        experiments.reset()
        _enable_m4()
        self.state = GameState()
        self.p = _player("p1", self.state)

    def tearDown(self):
        experiments.reset()

    def test_work_accumulates(self):
        from locations import shop
        shop.do_interact(self.p, "打工", self.state)
        shop.do_interact(self.p, "打工", self.state)
        self.assertEqual(self.p.credits, 4)

    def test_purchase_charges(self):
        from locations import shop
        self.p.credits = 5
        shop.do_interact(self.p, "小刀", self.state)
        self.assertEqual(self.p.credits, 4)  # 小刀 1cr
        self.assertTrue(self.p.has_weapon("小刀"))

    def test_surgery_property_tax(self):
        from locations import hospital
        self.p.location = "医院"
        self.p.credits = 6
        hospital.do_interact(self.p, "不老泉手术", self.state)
        self.assertEqual(self.p.credits, 0)  # 全部信用点
        self.assertEqual(self.p.regen_per_round, 1)

    def test_home_no_income(self):
        from locations import home
        self.p.location = "home_p1"
        ok, _ = home.can_interact(self.p, "凭证", self.state)
        self.assertFalse(ok)

    def test_ceramic_repair(self):
        from locations import shop
        from models.equipment import make_armor
        ceramic = make_armor("陶瓷护甲")
        ceramic.durability = 5
        self.p.armor.outer.append(ceramic)
        self.p.credits = 5
        shop.do_interact(self.p, "修理陶瓷护甲", self.state)
        self.assertEqual(ceramic.durability, 11)  # +6
        self.assertEqual(self.p.credits, 4)  # 1cr


class BowTest(unittest.TestCase):

    def setUp(self):
        experiments.reset()
        _enable_m4()
        self.state = GameState()
        self.a = _player("p1", self.state, "商店")
        self.t = _player("p2", self.state, "医院")

    def tearDown(self):
        experiments.reset()

    def test_bow_is_starting_gear(self):
        self.assertTrue(self.a.has_weapon("弓"))
        self.assertEqual(self.a.arrows, 3)

    def test_shoot_consumes_arrow_and_drops(self):
        from actions.shoot import execute
        random.seed(1)
        execute(self.a, self.t, self.state)
        self.assertEqual(self.a.arrows, 2)
        self.assertEqual(self.state.arrow_piles.get("医院"), 1)

    def test_cannot_shoot_without_arrows(self):
        from cli.validator import validate_shoot
        self.a.arrows = 0
        ok, _ = validate_shoot(self.a, "玩家p2", self.state)
        self.assertFalse(ok)

    def test_cross_location_unlocked_penalty(self):
        from combat.accuracy import compute_hit_chance
        from models.equipment import make_bow
        chance, _ = compute_hit_chance(self.a, self.t, make_bow(), self.state)
        self.assertEqual(chance, 85)  # 跨地点未锁定 -15

    def test_find_picks_up_arrows(self):
        from actions.find_target import execute as find_exec
        self.state.arrow_piles = {"商店": 2}
        self.a.arrows = 1
        find_exec(self.a, "p2", self.state)  # 同地点需 p2 在商店
        # p2 在医院不影响拾箭——拾箭只看本地点箭堆
        self.assertEqual(self.a.arrows, 3)
        self.assertEqual(self.state.arrow_piles["商店"], 0)


class ModuleTest(unittest.TestCase):

    def setUp(self):
        experiments.reset()
        _enable_m4()
        self.state = GameState()
        self.p = _player("p1", self.state)
        self.p.credits = 20

    def tearDown(self):
        experiments.reset()

    def test_install_and_double(self):
        from engine.bow_modules import compute_shot
        from locations import shop
        shop.do_interact(self.p, "力量模块", self.state)
        self.assertEqual(compute_shot(self.p)["weapon"].base_damage, 5)
        shop.do_interact(self.p, "力量模块", self.state)
        self.assertEqual(compute_shot(self.p)["weapon"].base_damage, 7)

    def test_supply_depletes(self):
        from engine.bow_modules import supply_left
        from locations import shop
        shop.do_interact(self.p, "力量模块", self.state)
        shop.do_interact(self.p, "力量模块", self.state)
        self.assertEqual(supply_left(self.state, "力量"), 0)
        ok, _ = shop.can_interact(self.p, "力量模块", self.state)
        self.assertFalse(ok)

    def test_infinite_exclusive(self):
        from engine.bow_modules import can_install
        self.p.location = "医院"
        from locations import hospital
        hospital.do_interact(self.p, "无限模块", self.state)
        ok, _ = can_install(self.p, "力量", self.state)
        self.assertFalse(ok)  # 无限独占双槽

    def test_death_recycle(self):
        from engine.bow_modules import release_on_death, supply_left
        from locations import shop
        shop.do_interact(self.p, "力量模块", self.state)
        self.assertEqual(supply_left(self.state, "力量"), 1)
        release_on_death(self.p, self.state)
        self.assertEqual(supply_left(self.state, "力量"), 2)
        self.assertEqual(self.p.bow_modules, [])


class BurnTest(unittest.TestCase):

    def setUp(self):
        experiments.reset()
        _enable_m4()
        self.state = GameState()
        self.p = _player("p1", self.state)

    def tearDown(self):
        experiments.reset()

    def test_burn_cap_and_damage(self):
        from engine.bow_modules import apply_burn
        from engine.round_manager import RoundManager
        apply_burn(self.p, 5)
        self.assertEqual(self.p.burn_stacks, 3)  # 上限 3
        rm = RoundManager(self.state)
        rm._process_burn_stacks_m4()
        self.assertEqual(self.p.hp, 17)  # 3 层 3 伤

    def test_armor_gain_extinguishes(self):
        from engine.bow_modules import apply_burn
        from engine.round_manager import RoundManager
        apply_burn(self.p, 2)
        self.p._armor_gained_this_round = True
        RoundManager(self.state)._process_burn_stacks_m4()
        self.assertEqual(self.p.burn_stacks, 1)  # 扑灭 1 层


class HookTest(unittest.TestCase):

    def setUp(self):
        experiments.reset()
        _enable_m4()
        self.state = GameState()
        self.state.current_round = 5
        self.a = _player("p1", self.state, "军事基地")
        self.t = _player("p2", self.state, "医院")
        from models.equipment import Item
        self.a.add_item(Item("钩索", "tool"))

    def tearDown(self):
        experiments.reset()

    def test_pull_target_relocates(self):
        from actions.hook import execute
        random.seed(2)
        execute(self.a, {"mode": "pull", "target": "玩家p2"}, self.state)
        self.assertEqual(self.t.location, "军事基地")

    def test_shared_cooldown(self):
        from actions.hook import execute
        from cli.validator import validate_hook
        from cli.parser import parse
        random.seed(2)
        execute(self.a, {"mode": "pull", "target": "玩家p2"}, self.state)
        ok, _ = validate_hook(self.a, parse("hook self 商店", "p1"), self.state)
        self.assertFalse(ok)  # 拉人后拉己同冷却

    def test_pull_self_no_aoo(self):
        """拉己直接位移，不触发借机攻击（不走 move.execute）。"""
        from actions.hook import execute
        # 建立交战
        self.t.location = "军事基地"
        self.state.markers.add_relation("p1", "ENGAGED_WITH", "p2")
        self.state.markers.add_relation("p2", "ENGAGED_WITH", "p1")
        execute(self.a, {"mode": "self", "destination": "商店"}, self.state)
        aoo = [e for e in self.state.event_log
               if e.get("type") == "opportunity_attack"]
        self.assertEqual(len(aoo), 0)


class RetirementTest(unittest.TestCase):

    def setUp(self):
        experiments.reset()
        _enable_m4()
        self.state = GameState()

    def tearDown(self):
        experiments.reset()

    def test_retired_weapons_blocked(self):
        from locations import magic_institute, military_base
        p = _player("p1", self.state, "魔法所")
        ok, _ = magic_institute.can_interact(p, "远程魔法弹幕", self.state)
        self.assertFalse(ok)
        ok2, _ = magic_institute.can_interact(p, "地动山摇", self.state)
        self.assertFalse(ok2)
        p.location = "军事基地"
        ok3, _ = military_base.can_interact(p, "导弹控制权", self.state)
        self.assertFalse(ok3)


class V1RegressionTest(unittest.TestCase):
    """m4 关闭：凭证经济与无弓（防 m4 分支泄漏）。"""

    def setUp(self):
        experiments.reset()

    def test_v1_no_bow_no_credits(self):
        state = GameState()
        p = _player("p1", state)
        self.assertFalse(p.has_weapon("弓"))
        self.assertEqual(p.arrows, 0)
        # v1 打工给凭证
        from locations import shop
        shop.do_interact(p, "打工", state)
        self.assertEqual(p.vouchers, 1)
        self.assertEqual(p.credits, 0)


if __name__ == "__main__":
    unittest.main()
