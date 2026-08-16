"""M9 批次 3：警察管线（PoliceStation 状态机）+ T6 GoodCitizen9 机制单测。

案件/证据/通缉/结案、R2 lead 分配、R4 自动执法、掩体、队长、停机、
G3 挂起、市民热线（根行动不读 SP）、特别线索持久化、联防整备白名单、
无虚构案件补偿。
"""
import unittest

from engine import experiments
from engine.game_state import GameState
from models.player import Player
from controllers.forfeit_controller import ForfeitController

from engine.m9.gate import ensure_state_mechanisms
from engine.m9.police import (
    EVIDENCE_DETECTOR,
    EVIDENCE_T6_CLUE,
    EVIDENCE_VICTIM,
    PoliceStation,
    T6_WEAPON_WHITELIST,
)
from engine.m9.talents.t6 import GoodCitizen9


def _enable(*flags):
    experiments.reset()
    for f in flags:
        experiments.enable(f)


def _make(pids=("p1", "p2")):
    state = GameState()
    ensure_state_mechanisms(state)
    players = {}
    for pid in pids:
        p = Player(pid, pid.upper(), controller=ForfeitController())
        state.add_player(p)
        p.max_hp = 20
        p.hp = 20
        p.location = "商店"
        players[pid] = p
    t = GoodCitizen9("p1", state)
    players["p1"].talent = t
    return state, players, t


class PoliceStationRuntimeTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_fixed_roster_units(self) -> None:
        station = PoliceStation()
        units = station.ensure_roster()
        self.assertEqual(len(units), station.fixed_roster_size())
        self.assertTrue(all(u.is_alive() for u in units))

    def test_case_wanted_flow(self) -> None:
        station = PoliceStation()
        case = station.file_case("r1", "s1", evidence=1,
                                 evidence_kind=EVIDENCE_VICTIM)
        self.assertIsNotNone(case)
        self.assertIsNone(station.open_wanted())       # reported 未通缉
        self.assertTrue(station.verify_case(case.case_id))
        self.assertEqual(station.open_wanted().suspect_id, "s1")
        # 已有通缉时新报案预检失败
        self.assertIsNone(station.file_case("r2", "s2", evidence=1))
        station.close_current_wanted("目标死亡")
        self.assertIsNone(station.open_wanted())

    def test_r2_lead_assignment_and_r4_enforcement(self) -> None:
        state, players, _ = _make()
        station = state.m9_police
        station.ensure_roster()
        station.set_state_ref(state)
        for u in station.units():
            u.location = "警察局"
        state.m9_system.set_sp("p1", 1)
        players["p2"].location = "警察局"
        case = station.file_case("p1", "p2", evidence=1,
                                 evidence_kind=EVIDENCE_VICTIM)
        station.verify_case(case.case_id)
        msgs = station.r2_tick(state, 2)
        self.assertTrue(msgs)
        self.assertIsNotNone(station.lead_id)
        lead = station.get_unit(station.lead_id)
        self.assertEqual(lead.location, "警察局")       # lead 移动到目标地点
        station.r0_tick(state, 2)
        msgs = station.r4_enforcement(state, 2)
        self.assertTrue(msgs)
        self.assertLess(players["p2"].hp, 20)          # 执法造成伤害
        self.assertTrue(lead.acted_this_round)
        self.assertEqual(station.r4_enforcement(state, 2), [])  # 每轮至多一次

    def test_target_moved_skips_enforcement(self) -> None:
        """R2 lead 追踪到目标地点；目标 R3 移动后 R4 不执法（消费自身行动）。"""
        state, players, _ = _make()
        station = state.m9_police
        station.ensure_roster()
        station.set_state_ref(state)
        for u in station.units():
            u.location = "警察局"
        players["p2"].location = "商店"
        case = station.file_case("p1", "p2", evidence=1,
                                 evidence_kind=EVIDENCE_VICTIM)
        station.verify_case(case.case_id)
        station.r2_tick(state, 2)
        lead = station.get_unit(station.lead_id)
        self.assertEqual(lead.location, "商店")          # R2 追踪到新地点
        players["p2"].location = "医院"                  # R3 目标移动
        station.r0_tick(state, 2)
        msgs = station.r4_enforcement(state, 2)
        self.assertEqual(msgs, [])                       # 目标不在 police 地点

    def test_shutdown_stops_everything(self) -> None:
        state, players, _ = _make()
        station = state.m9_police
        station.ensure_roster()
        station.set_state_ref(state)
        for unit in station.units():
            unit.location = "医院"
        station.shut_down("被摧毁")
        self.assertIsNone(station.file_case("p1", "p2", evidence=1))
        self.assertEqual(station.r2_tick(state, 2), [])
        self.assertEqual(station.r4_enforcement(state, 2), [])
        # 停机后警察保留中立 NPC 身份：存活并原地停留，不再执法。
        self.assertTrue(station.alive_units())
        self.assertTrue(all(u.location == "医院" for u in station.units()))

    def test_cover_grant_and_absorb(self) -> None:
        state, players, _ = _make()
        station = state.m9_police
        station.ensure_roster()
        station.set_state_ref(state)
        for u in station.units():
            u.location = "商店"
        station.refresh_cover(state)
        self.assertGreater(station.player_cover("p2"), 0)  # 非通缉同地点获掩体
        # 通缉目标不获掩体；非通缉者仍受保护
        case = station.file_case("p1", "p2", evidence=1,
                                 evidence_kind=EVIDENCE_VICTIM)
        station.verify_case(case.case_id)
        station.refresh_cover(state)
        self.assertEqual(station.player_cover("p2"), 0)
        self.assertGreater(station.player_cover("p1"), 0)

    def test_captain_election_and_command(self) -> None:
        state, players, _ = _make()
        station = state.m9_police
        station.ensure_roster()
        station.set_state_ref(state)
        self.assertTrue(station.apply_captain("p1"))
        self.assertFalse(station.apply_captain("p1"))   # 不重复登记
        msgs = station.r2_tick(state, 2)
        self.assertEqual(station.captain_id, "p1")
        self.assertTrue(players["p1"].is_captain)
        self.assertGreater(station.authority, 0)
        unit = station.units()[0]
        msg = station.captain_command("p1", unit.unit_id, "move", "商店")
        self.assertIn("移动到", msg)
        self.assertEqual(unit.location, "商店")
        self.assertIn("只有队长可以指挥",
                      station.captain_command("p2", unit.unit_id, "move",
                                              "商店"))

    def test_authority_zero_captain_becomes_wanted(self) -> None:
        state, players, _ = _make()
        station = state.m9_police
        station.ensure_roster()
        station.set_state_ref(state)
        station.apply_captain("p1")
        station.r2_tick(state, 2)
        self.assertEqual(station.captain_id, "p1")
        station.reduce_authority(9)
        self.assertIsNone(station.captain_id)
        self.assertFalse(players["p1"].is_captain)
        wanted = station.open_wanted()
        self.assertIsNotNone(wanted)
        self.assertEqual(wanted.suspect_id, "p1")

    def test_g3_suspension_pauses_enforcement(self) -> None:
        state, players, _ = _make()
        station = state.m9_police
        station.ensure_roster()
        station.set_state_ref(state)
        for u in station.units():
            u.location = "警察局"
        players["p2"].location = "警察局"
        case = station.file_case("p1", "p2", evidence=1,
                                 evidence_kind=EVIDENCE_VICTIM)
        station.verify_case(case.case_id)
        station.set_suspended(True)
        self.assertEqual(station.r2_tick(state, 2), [])
        self.assertEqual(station.r4_enforcement(state, 2), [])
        station.set_suspended(False)
        msgs = station.r2_tick(state, 2)
        self.assertTrue(msgs)                            # 恢复后继续

    def test_target_death_closes_wanted(self) -> None:
        state, players, _ = _make()
        station = state.m9_police
        station.ensure_roster()
        station.set_state_ref(state)
        case = station.file_case("p1", "p2", evidence=1,
                                 evidence_kind=EVIDENCE_VICTIM)
        station.verify_case(case.case_id)
        station.on_player_death("p2")
        self.assertIsNone(station.open_wanted())


class GoodCitizen9Test(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20")

    def tearDown(self) -> None:
        experiments.reset()

    def test_on_register_does_not_mutate_crime_types(self) -> None:
        state, players, t = _make()
        before = set(state.crime_types)
        t.on_register()
        self.assertEqual(set(state.crime_types), before)

    def test_hotline_remote_report_no_sp(self) -> None:
        state, players, t = _make()
        state.m9_police.ensure_roster()
        sp_before = state.m9_system.get_sp("p1")
        t.record_special_clue("p2", "进入军事基地")
        msg = t.hotline_report("p2")
        self.assertIn("通缉", msg)
        self.assertEqual(state.m9_system.get_sp("p1"), sp_before)  # 不读 SP
        wanted = state.m9_police.open_wanted()
        self.assertIsNotNone(wanted)
        self.assertEqual(wanted.suspect_id, "p2")
        self.assertEqual(wanted.evidence_kind, EVIDENCE_T6_CLUE)

    def test_hotline_precheck_failure_no_case_no_slot(self) -> None:
        state, players, t = _make()
        state.m9_police.ensure_roster()
        players["p2"].location = "医院"                # 异地 + 无证据/线索
        msg = t.hotline_report("p2")
        self.assertIn("无合法证据", msg)
        self.assertEqual(state.m9_police.open_cases(), [])
        self.assertFalse(state.m9_police.has_open_wanted())

    def test_special_clue_persists_after_t6_death(self) -> None:
        state, players, t = _make()
        t.record_special_clue("p2", "释放病毒")
        players["p1"].hp = 0
        self.assertEqual(t.special_clues_for("p2"), [])   # 死亡 T6 不可用
        self.assertTrue(any(
            e.get("type") == "special_clue"
            for e in state.event_log))                    # 线索仍在日志

    def test_witness_evidence_at_same_location(self) -> None:
        state, players, t = _make(pids=("p1", "p2", "p3"))
        state.m9_police.ensure_roster()
        players["p2"].location = "医院"                 # 被举报者异地
        players["p3"].location = "商店"                 # 同地点目击者
        state.log_event("attack", attacker="p2", target="p3",
                        location="商店", witnesses=["p1"])
        msg = t.hotline_report("p2")
        self.assertIn("通缉", msg)

    def test_joint_defense_equip_public(self) -> None:
        state, players, t = _make()
        station = state.m9_police
        station.ensure_roster()
        unit = station.units()[0]
        unit.location = "警察局"
        players["p1"].location = "警察局"
        from models.equipment import make_weapon
        players["p1"].weapons.append(make_weapon("警棍"))
        old_choose = players["p1"].controller.choose
        players["p1"].controller.choose = lambda prompt, options, context=None: (
            "公演" if "公演" in options else old_choose(prompt, options, context))
        m9 = state.m9_system
        m9.set_sp("p1", 2)
        m9.register_performance("p1", 1)
        m9.assign_public_slot(1)
        self.assertIsNotNone(t.get_t0_option(players["p1"]))
        msg, ok = t.execute_t0(players["p1"])
        self.assertTrue(ok, msg)
        self.assertEqual(m9.get_sp("p1"), 0)
        self.assertIn("整备", msg)
        self.assertIn(unit.weapon_name, T6_WEAPON_WHITELIST)

    def test_joint_defense_cancels_without_living_police(self) -> None:
        state, players, t = _make()
        state.m9_police.ensure_roster()
        for u in state.m9_police.units():
            u.hp = 0                                     # 全员阵亡
        m9 = state.m9_system
        m9.set_sp("p1", 2)
        msg, ok = t.execute_t0(players["p1"])
        self.assertFalse(ok)
        self.assertEqual(m9.get_sp("p1"), 2)             # 消费前取消

    def test_no_fabricated_cases_in_peace(self) -> None:
        state, players, t = _make()
        state.m9_police.ensure_roster()
        self.assertFalse(state.m9_police.has_open_wanted())
        self.assertEqual(state.m9_police.open_cases(), [])
        self.assertIsNone(t.get_t0_option(players["p1"]))  # SP=0 无整备入口


if __name__ == "__main__":
    unittest.main()
