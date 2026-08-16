"""M9 G5 轮回锚定机制单测（阶段 7）：四形态/归家非死亡/转世/追忆封存/
AnchorScript 投影器/逐槽监控（自然实现/再投影强制/因果改写）/窄回溯/完结条。"""
import unittest
from types import SimpleNamespace

from engine import experiments
from engine.game_state import GameState
from models.equipment import Weapon, WeaponRange
from models.player import Player
from controllers.forfeit_controller import ForfeitController
from utils.attribute import Attribute

from engine.m9.gate import ensure_state_mechanisms
from engine.m9.talents.g5 import (
    AnchorScriptProjector, EventCandidate, FORM_CYRENE, FORM_DEMIURGE,
    FORM_HOME, FORM_PAST, Ripple9,
)


def _enable(*flags):
    experiments.reset()
    for f in flags:
        experiments.enable(f)


def _make():
    state = GameState()
    ensure_state_mechanisms(state)
    p = Player("p1", "G5", controller=ForfeitController())
    state.add_player(p)
    p.max_hp = 20
    p.hp = 20
    t = Ripple9("p1", state)
    p.talent = t
    state.m9_system.set_sp("p1", 2)
    return state, p, t


class FormLifecycleTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_homecoming_not_death(self) -> None:
        state, p, t = _make()
        self._add_opponents(state)
        t.form = FORM_CYRENE
        p.hp = 0
        kind = t.m9_on_lethal(p, None, "normal")
        self.assertEqual(kind, "g5_homecoming")
        self.assertEqual(t.form, FORM_HOME)

    def test_registration_uses_first_cyrene_body_stats(self) -> None:
        from engine.balance import get as bget
        state, p, t = _make()
        t.on_register()
        self.assertEqual(t.incarnations, 1)
        cyrene_hp = int(bget("m9_talents_extended", "g5",
                             "cyrene_hp", default=8))
        self.assertEqual(p.max_hp, cyrene_hp)
        self.assertEqual(p.hp, cyrene_hp)
        # 开局仍走全员共用的起床槽，不因天赋额外获得行动。
        self.assertFalse(p.is_awake)
        self.assertIsNone(p.location)

    def test_four_future_r4_ticks_trigger_homecoming(self) -> None:
        from engine.balance import get as bget
        state, p, t = _make()
        self._add_opponents(state)
        t.on_register()
        p.is_awake = True
        p.location = "商店"
        life_ticks = int(bget("m9_talents_extended", "g5",
                              "cyrene_life_ticks", default=4))
        for round_num in range(1, life_ticks):
            t.on_round_end(round_num)
            self.assertEqual(t.form, FORM_CYRENE)
        t.on_round_end(life_ticks)
        self.assertEqual(t.form, FORM_HOME)
        self.assertIsNone(p.location)
        self.assertIsNone(state.get_actor("p1"))

    def test_homecoming_drops_world_items_but_keeps_credits(self) -> None:
        state, p, t = _make()
        t.on_register()
        p.is_awake = True
        p.location = "商店"
        p.credits = 7
        p.arrows = 3
        p.weapons.append(Weapon(
            "小刀", Attribute.ORDINARY, 4, WeaponRange.MELEE))
        t._homecoming(p)
        pile = state.ground_loot["商店"]
        self.assertEqual(p.credits, 7)
        self.assertEqual(p.arrows, 0)
        self.assertEqual(pile["arrows"], 3)
        self.assertIn("小刀", [entry["name"] for entry in pile["weapons"]])

    def test_absolute_death_kills_cyrene(self) -> None:
        state, p, t = _make()
        t.form = FORM_CYRENE
        self.assertIsNone(t.m9_on_lethal(p, None, "g7_terror"))

    def test_reincarnation_at_r0(self) -> None:
        state, p, t = _make()
        t.form = FORM_HOME
        t.incarnations = 1
        t.sealed_reminiscence = 2.0
        t.on_round_start(2)
        self.assertEqual(t.form, FORM_CYRENE)
        self.assertEqual(t.incarnations, 2)

    def test_demiurge_birth_at_threshold(self) -> None:
        state, p, t = _make()
        t.form = FORM_HOME
        t.sealed_reminiscence = 20.0  # ≥ birth_threshold(12)
        t.on_round_start(2)
        self.assertEqual(t.form, FORM_DEMIURGE)

    def _add_opponents(self, state, count=2) -> None:
        for index in range(count):
            player = Player(
                f"p{index + 2}", f"对手{index + 1}",
                controller=ForfeitController())
            player.hp = 20
            player.location = "商店"
            state.add_player(player)

    def test_dusk_lethal_forces_immediate_demiurge(self) -> None:
        experiments.enable("m5_clock")
        state, p, t = _make()
        self._add_opponents(state)
        t.on_register()
        state.current_round = (6 + len(state.player_order)) * 2 + 1
        p.location = "商店"
        p.hp = 0

        kind = t.m9_on_lethal(p, None, "normal")

        self.assertEqual(kind, "g5_demiurge_birth")
        self.assertEqual(t.form, FORM_DEMIURGE)
        self.assertEqual(p.hp, p.max_hp)
        self.assertEqual(p.location, "home_p1")

    def test_apocalypse_lethal_uses_normal_death_pipeline(self) -> None:
        experiments.enable("m5_clock")
        state, p, t = _make()
        self._add_opponents(state)
        t.on_register()
        state.current_round = (6 + len(state.player_order)) * 3 + 1
        p.hp = 0

        self.assertIsNone(t.m9_on_lethal(p, None, "normal"))
        self.assertEqual(t.form, FORM_CYRENE)

    def test_two_player_endgame_lethal_uses_normal_death_pipeline(self) -> None:
        state, p, t = _make()
        self._add_opponents(state, count=1)
        t.on_register()
        state.current_round = 1
        p.hp = 0

        self.assertIsNone(t.m9_on_lethal(p, None, "normal"))
        self.assertEqual(t.form, FORM_CYRENE)

    def test_dusk_life_expiry_forces_demiurge(self) -> None:
        experiments.enable("m5_clock")
        state, p, t = _make()
        self._add_opponents(state)
        t.on_register()
        state.current_round = (6 + len(state.player_order)) * 2 + 1
        t._cyrene_established_round = 0
        t.life_ticks = 1

        t.on_round_end(state.current_round)

        self.assertEqual(t.form, FORM_DEMIURGE)
        self.assertGreater(p.hp, 0)

    def test_apocalypse_life_expiry_finalizes_normal_death(self) -> None:
        experiments.enable("m5_clock")
        state, p, t = _make()
        self._add_opponents(state)
        t.on_register()
        state.current_round = (6 + len(state.player_order)) * 3 + 1
        t._cyrene_established_round = 0
        t.life_ticks = 1

        t.on_round_end(state.current_round)

        self.assertEqual(p.hp, 0)
        self.assertTrue(getattr(p, "_m9_death_finalized", False))
        deaths = [event for event in state.event_log
                  if event["type"] == "death" and event["player"] == "p1"]
        self.assertEqual(len(deaths), 1)

    def test_home_crossing_into_dusk_forces_demiurge_at_r0(self) -> None:
        experiments.enable("m5_clock")
        state, p, t = _make()
        self._add_opponents(state)
        t.on_register()
        t.form = FORM_HOME
        state.current_round = (6 + len(state.player_order)) * 2 + 1

        t.on_round_start(state.current_round)

        self.assertEqual(t.form, FORM_DEMIURGE)

    def test_past_closure_freezes_pp(self) -> None:
        state, p, t = _make()
        t.form = FORM_DEMIURGE
        t.sealed_reminiscence = 1.0  # < anchor_min_k(3)
        state.m9_pp.earn("p1", 5)
        t.on_round_start(2)
        self.assertEqual(t.form, FORM_PAST)
        self.assertTrue(state.m9_pp.is_frozen("p1"))

    def test_reminiscence_sealed_capped(self) -> None:
        state, p, t = _make()
        t.form = FORM_CYRENE
        t.m9_on_combat_event("combat", personal=True)
        self.assertEqual(t.sealed_reminiscence, 2.0)
        for _ in range(30):
            t.m9_on_combat_event("idle")
        self.assertLessEqual(t.sealed_reminiscence, 24.0)


class ProjectorTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_validate_illegal_first_slot(self) -> None:
        state, p, t = _make()
        proj = AnchorScriptProjector(state, t)
        self.assertEqual(proj.validate_script([("forfeit", "p2")]), 0)
        self.assertEqual(proj.validate_script([("attack", "p2")]), 0)  # 缺武器
        self.assertIsNone(proj.validate_script([("attack", "p2", "小刀"),
                                                ("move", "商店")]))

    def test_project_defeat_candidate(self) -> None:
        state, p, t = _make()
        target = Player("p2", "目标", controller=ForfeitController())
        target.location = "商店"
        target.hp = 2
        state.add_player(target)
        p.weapons.append(Weapon("小刀", Attribute.ORDINARY, 4, WeaponRange.MELEE))
        proj = AnchorScriptProjector(state, t)
        cands = proj.project([("attack", "p2", "小刀")], 1)
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0].kind, "DEFEAT")
        self.assertEqual(cands[0].subject_id, "p2")

    def test_precheck_rejects_pure_move_future(self) -> None:
        """张力规则：纯 move 脚本（全 RELOCATE，恒可强制实现）→ 拒绝且不消费。"""
        state, p, t = _make()
        t.form = FORM_DEMIURGE
        t.sealed_reminiscence = 10
        script = [("move", "医院"), ("move", "公园"),
                  ("move", "军事基地"), ("move", "魔法所")]
        msg, ok = t.execute_anchor(p, script)
        self.assertFalse(ok)
        self.assertIn("毫无意义", msg)
        self.assertEqual(state.m9_system.get_sp("p1"), 2)  # 未消费
        self.assertEqual(t.sealed_reminiscence, 10)
        self.assertFalse(t.active_anchor)


class AnchorLifecycleTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_anchor_commit_and_monitor_rewind(self) -> None:
        """锚定建立 → 逐槽监控（目标已死 = 自然实现）→ 未来闭合 + 窄回溯。"""
        state, p, t = _make()
        target = Player("p2", "目标", controller=ForfeitController())
        target.location = "商店"
        target.hp = 2
        state.add_player(target)
        p.weapons.append(Weapon("小刀", Attribute.ORDINARY, 4, WeaponRange.MELEE))
        t.form = FORM_DEMIURGE
        t.sealed_reminiscence = 10
        p.hp = 5
        state.m9_system.register_performance("p1", state.current_round)
        state.m9_system.allocate_public_slot(state.current_round)
        script = [("attack", "p2", "小刀"), ("move", "医院"),
                  ("attack", "p2", "小刀"), ("move", "商店")]
        msg, ok = t.execute_anchor(p, script)
        self.assertTrue(ok)
        self.assertTrue(t.active_anchor)
        self.assertEqual(t.sealed_reminiscence, 6)  # K=4 扣 4
        self.assertEqual(state.m9_system.get_sp("p1"), 0)  # 公演扣 2
        # 目标先被打死 → 槽 0 DEFEAT 自然实现；槽 1 RELOCATE（p1 去医院）；
        # 槽 2 DEFEAT（p2 已死）自然实现；槽 3 RELOCATE → 未来闭合
        target.hp = 0
        state.current_round = 6
        t.on_round_end(6)  # 槽 0
        t.on_round_end(7)  # 槽 1
        t.on_round_end(8)  # 槽 2
        t.on_round_end(9)  # 槽 3 → 收尾
        self.assertEqual(t.anchor_results, ["未来闭合"])
        self.assertFalse(t.active_anchor)
        self.assertEqual(p.hp, 5)  # 窄回溯恢复快照 HP

    def test_consecutive_closures_have_independent_results_and_flowers(self) -> None:
        state, p, t = _make()
        t.form = FORM_DEMIURGE
        for expected in (1, 2):
            t.anchor_results = []
            t.active_anchor = True
            t.anchor_snapshot = {"hp": p.hp, "location": p.location}
            t._finish_anchor()
            self.assertEqual(t.anchor_results, ["未来闭合"])
            self.assertEqual(t.total_closures, expected)
            self.assertEqual(t.flowers_granted, expected)
        # 三章制完结条（arc RFC v0.1）：事件只登记事实；登台后扫描 →
        # 登台1 + 水晶花(高光)1 + 双锚(谢幕)1 = 3，且只颁发一次。
        self.assertEqual(state.m9_scoring.arc_count("p1"), 0)
        state.m9_arc.on_public_performance("p1", 1)
        state.m9_arc.scan(state)
        self.assertEqual(state.m9_scoring.arc_count("p1"), 3)

    def test_anchor_rewrite_when_target_unreachable(self) -> None:
        """可达性门：R4 再投影不满足真实攻击合法性 → 因果改写（不击杀）。"""
        state, p, t = _make()
        target = Player("p2", "目标", controller=ForfeitController())
        target.location = "商店"
        target.hp = 2
        state.add_player(target)
        p.weapons.append(Weapon("小刀", Attribute.ORDINARY, 4, WeaponRange.MELEE))
        p.location = "医院"  # 与目标不同地点：近战不可达
        t.form = FORM_DEMIURGE
        t.sealed_reminiscence = 10
        state.m9_system.register_performance("p1", state.current_round)
        state.m9_system.allocate_public_slot(state.current_round)
        script = [("attack", "p2", "小刀")] + [("move", "医院")] * 3
        msg, ok = t.execute_anchor(p, script)
        self.assertTrue(ok)
        t._monitor_slot()
        self.assertIn("因果被改写", t.anchor_results)
        self.assertFalse(t.active_anchor)
        self.assertGreater(target.hp, 0)  # 未被远程处决

    def test_anchor_rewrite_when_target_healed(self) -> None:
        """可达但探针不再致死 → 因果改写。"""
        state, p, t = _make()
        target = Player("p2", "目标", controller=ForfeitController())
        target.location = "商店"
        target.hp = 2
        state.add_player(target)
        p.weapons.append(Weapon("小刀", Attribute.ORDINARY, 4, WeaponRange.MELEE))
        p.location = "商店"
        state.markers.init_player("p1")
        state.markers.add_relation("p1", "ENGAGED_WITH", "p2")
        t.form = FORM_DEMIURGE
        t.sealed_reminiscence = 10
        state.m9_system.register_performance("p1", state.current_round)
        state.m9_system.allocate_public_slot(state.current_round)
        script = [("attack", "p2", "小刀")] + [("move", "医院")] * 3
        msg, ok = t.execute_anchor(p, script)
        self.assertTrue(ok)
        target.hp = 20  # 截止前痊愈：同源探针不再致死
        t._monitor_slot()
        self.assertIn("因果被改写", t.anchor_results)
        self.assertFalse(t.active_anchor)
        self.assertGreater(target.hp, 0)

    def test_defeat_forced_realization_when_reachable(self) -> None:
        """可达且探针致死 → R4 强制差分：绝对死亡击杀（命中掷骰命中时）。"""
        from unittest.mock import patch
        state, p, t = _make()
        target = Player("p2", "目标", controller=ForfeitController())
        target.location = "商店"
        target.hp = 2
        state.add_player(target)
        p.weapons.append(Weapon("小刀", Attribute.ORDINARY, 4, WeaponRange.MELEE))
        p.location = "商店"
        state.markers.init_player("p1")
        state.markers.add_relation("p1", "ENGAGED_WITH", "p2")
        t.form = FORM_DEMIURGE
        t.sealed_reminiscence = 10
        state.m9_system.register_performance("p1", state.current_round)
        state.m9_system.allocate_public_slot(state.current_round)
        script = [("attack", "p2", "小刀")] + [("move", "医院")] * 3
        msg, ok = t.execute_anchor(p, script)
        self.assertTrue(ok)
        with patch("combat.accuracy.roll_hit", return_value=(True, 100)):
            t._monitor_slot()
        self.assertTrue(getattr(target, "_m9_death_finalized", False))
        self.assertFalse(target.is_alive())

    def test_anchor_blocked_by_love_wish(self) -> None:
        state, p, t = _make()
        other = Player("p2", "路人", controller=ForfeitController())
        state.add_player(other)
        other.talent = SimpleNamespace(
            has_love_wish=lambda pid: pid == "p1")
        t.form = FORM_DEMIURGE
        t.sealed_reminiscence = 10
        msg, ok = t.execute_anchor(p, [("move", "医院")])
        self.assertFalse(ok)
        self.assertIn("爱愿", msg)

    def test_natural_realization_window_scoped(self) -> None:
        """窗口期语义：锚定前的事件不算自然实现（T7 复活场景不误锁）。"""
        state, p, t = _make()
        target = Player("p2", "目标", controller=ForfeitController())
        target.location = "商店"
        state.add_player(target)
        state.current_round = 2
        state.log_event("death", player="p2")          # 锚定前的旧死亡事件
        t.anchor_established_round = 5
        cand = EventCandidate("DEFEAT", 0, "p2", ("attack", "p2", "小刀"))
        self.assertFalse(t._naturally_realized(cand))   # 旧事件不锁
        state.current_round = 5
        state.log_event("death", player="p2")          # 窗口内死亡
        self.assertTrue(t._naturally_realized(cand))
        cand2 = EventCandidate("RELOCATE", 1, "p1", ("move", "医院"),
                               before=("loc",), after=("医院",))
        state.current_round = 4
        state.log_event("move", player="p1", to_loc="医院")   # 锚定前
        self.assertFalse(t._naturally_realized(cand2))
        state.current_round = 6
        state.log_event("move", player="p1", to_loc="医院")   # 窗口内
        self.assertTrue(t._naturally_realized(cand2))

    def test_destroy_candidate_forced_realization(self) -> None:
        """DESTROY 候选截止时再投影：同来源动作（武器仍持有）强制摧毁目标护甲件。"""
        from models.equipment import make_armor
        from models.player import ArmorSlots
        state, p, t = _make()
        target = Player("p2", "目标", controller=ForfeitController())
        target.armor = ArmorSlots()
        piece = make_armor("盾牌")
        target.armor.outer.append(piece)
        state.add_player(target)
        p.weapons.append(Weapon("小刀", Attribute.ORDINARY, 4, WeaponRange.MELEE))
        p.location = "商店"
        target.location = "商店"
        state.markers.init_player("p1")
        state.markers.add_relation("p1", "ENGAGED_WITH", "p2")
        piece.durability = 1  # 探针磨损至碎（同源攻击真实伤害足够破盾）
        cand = EventCandidate("DESTROY", 0, "盾牌",
                              ("attack", "p2", "小刀"))
        t.anchor_candidates = [cand]
        t.anchor_k = 1
        t.anchor_slot_index = 0
        t.active_anchor = True
        t._monitor_slot()
        self.assertTrue(piece.is_broken)
        self.assertEqual(target.armor.get_all_active(), [])

    def test_destroy_candidate_naturally_realized(self) -> None:
        """DESTROY 候选自然实现：对象已被第三方摧毁 → 不再强制。"""
        from models.equipment import make_armor
        from models.player import ArmorSlots
        state, p, t = _make()
        target = Player("p2", "目标", controller=ForfeitController())
        target.armor = ArmorSlots()
        target.armor.outer.append(make_armor("盾牌"))
        state.add_player(target)
        cand = EventCandidate("DESTROY", 0, "盾牌",
                              ("attack", "p2", "小刀"))
        self.assertFalse(t._naturally_realized(cand))
        target.armor.remove_piece(target.armor.outer[0])
        self.assertTrue(t._naturally_realized(cand))

    def test_acquire_candidate_forced_realization(self) -> None:
        """ACQUIRE 候选截止时再投影：地面可转移对象强制归 G5 持有。"""
        from models.equipment import make_item
        state, p, t = _make()
        item = make_item("防毒面具")
        state.ground_loot = {"商店": {
            "credits": 0, "arrows": 0, "items": [
                {"name": "防毒面具", "kind": "item",
                 "source_slot": "", "object": item}],
            "weapons": [], "armor": []}}
        cand = EventCandidate("ACQUIRE", 0, "防毒面具",
                              ("interact", "防毒面具", "pickup"))
        t.anchor_candidates = [cand]
        t.anchor_k = 1
        t.anchor_slot_index = 0
        t.active_anchor = True
        t._monitor_slot()
        self.assertIn(item, p.items)
        self.assertEqual(state.ground_loot["商店"]["items"], [])

    def test_acquire_candidate_unreachable_is_rewrite(self) -> None:
        """ACQUIRE 再投影无对象可转移 → 因果改写。"""
        state, p, t = _make()
        state.ground_loot = {}
        cand = EventCandidate("ACQUIRE", 0, "防毒面具",
                              ("interact", "防毒面具", "pickup"))
        t.anchor_candidates = [cand]
        t.anchor_k = 1
        t.anchor_slot_index = 0
        t.active_anchor = True
        t._monitor_slot()
        self.assertIn("因果被改写", t.anchor_results)
        self.assertFalse(t.active_anchor)


if __name__ == "__main__":
    unittest.main()
