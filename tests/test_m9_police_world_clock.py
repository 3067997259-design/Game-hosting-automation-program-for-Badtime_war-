"""M9 警察局 × 世界时钟单测（m5_clock：黄昏撤掩体 / 终焉全停）。

合同（镜像 legacy engine/police_system.py:506-514 / :1198-1204 与
data/balance.json 的 world_clock 分区）：
- 黎明 dawn：警察保护/立案/执法全开（balance 黎明节点为空 → 全部默认值）；
- 黄昏 dusk：police_protection=False → 掩体保护撤销（player_cover 归零），
  但立案 / R2 lead / R4 执法照常推进；
- 终焉 apocalypse：police_disabled=True → 警察整体停摆：不立案、R2/R4 不推进、
  不给掩体、不受保护、队长候选拒收；已立案的通缉事实保留（停机只关执法推进，
  不抹去事实）；
- m5_clock 关闭时 world_clock.current_phase 恒回退黎明 → 本文件所有时钟门控
  恒不触发（实验关闭回归保护）。

段长 = segment_base(6) + 初始人数(2) = 8 轮：黎明 1-8 / 白昼 9-16 /
黄昏 17-24 / 终焉 25+。
"""
import unittest

from controllers.forfeit_controller import ForfeitController

from engine import experiments
from engine.game_state import GameState
from engine.m9.gate import ensure_state_mechanisms
from engine.m9.police import EVIDENCE_VICTIM, PoliceStation
from models.player import Player

SEG = 6 + 2                 # segment_base + 2 名玩家（与 world_clock._segment_length 同式）
DAWN_ROUND = 1              # (1-1)//8 = 0 → dawn
DUSK_ROUND = SEG * 2 + 1    # 17： (17-1)//8 = 2 → dusk
APOCALYPSE_ROUND = SEG * 3 + 1  # 25：(25-1)//8 = 3 → apocalypse


def _enable(*flags):
    experiments.reset()
    for f in flags:
        experiments.enable(f)


def _make(pids=("p1", "p2")):
    """创建 state + 玩家（hp20，同 test_m9_t6._make 模式）。"""
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
    return state, players


def _seat_police(state, location="商店"):
    """警察就位：固定编制 + state 引用 + 全员同地点（掩体/执法前置条件）。"""
    station = state.m9_police
    station.ensure_roster()
    station.set_state_ref(state)
    for u in station.units():
        u.location = location
    return station


class PoliceWorldClockTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m9_rfc", "hp20", "m5_clock")

    def tearDown(self) -> None:
        experiments.reset()

    def test_dawn_cover_granted_and_case_files(self) -> None:
        """黎明：同地点非通缉玩家获掩体；立案正常。"""
        state, players = _make()
        state.current_round = DAWN_ROUND
        station = _seat_police(state)
        station.refresh_cover(state)
        self.assertGreater(station.player_cover("p2"), 0)
        case = station.file_case("p1", "p2", evidence=1,
                                 evidence_kind=EVIDENCE_VICTIM)
        self.assertIsNotNone(case)

    def test_dusk_cover_withdrawn_enforcement_continues(self) -> None:
        """黄昏：掩体撤销（player_cover==0），但立案 / R2 / R4 照常。"""
        state, players = _make()
        state.current_round = DUSK_ROUND
        station = _seat_police(state)
        # 掩体撤销：refresh_cover 后保护归零
        station.refresh_cover(state)
        self.assertEqual(station.player_cover("p2"), 0)
        # 立案照常（黄昏只撤保护，不撤执法）
        case = station.file_case("p1", "p2", evidence=1,
                                 evidence_kind=EVIDENCE_VICTIM)
        self.assertIsNotNone(case)
        station.verify_case(case.case_id)
        # R2 照常分配执法 lead
        msgs = station.r2_tick(state, DUSK_ROUND)
        self.assertTrue(msgs)
        self.assertIsNotNone(station.lead_id)
        lead = station.get_unit(station.lead_id)
        self.assertEqual(lead.location, players["p2"].location)
        # R4 照常执法（同地点攻击）
        station.r0_tick(state, DUSK_ROUND)
        msgs = station.r4_enforcement(state, DUSK_ROUND)
        self.assertTrue(msgs)
        self.assertLess(players["p2"].hp, 20)

    def test_apocalypse_police_halted(self) -> None:
        """终焉：不立案、R2/R4 停摆、不给掩体/保护、队长拒收；事实保留。"""
        state, players = _make()
        station = _seat_police(state)
        # 黎明先立案 → 通缉事实在终焉保留（停机只关推进，不抹事实）
        state.current_round = DAWN_ROUND
        case = station.file_case("p1", "p2", evidence=1,
                                 evidence_kind=EVIDENCE_VICTIM)
        self.assertIsNotNone(case)
        station.verify_case(case.case_id)
        wanted = station.open_wanted()
        self.assertIsNotNone(wanted)
        # 进入终焉
        state.current_round = APOCALYPSE_ROUND
        # 不立案
        self.assertIsNone(station.file_case("p1", "p2", evidence=1,
                                            evidence_kind=EVIDENCE_VICTIM))
        # R2/R4 停摆（已有通缉也不推进）
        self.assertEqual(station.r2_tick(state, APOCALYPSE_ROUND), [])
        self.assertEqual(station.r4_enforcement(state, APOCALYPSE_ROUND), [])
        # 不给掩体（refresh/grant 双入口均空转）
        station.refresh_cover(state)
        self.assertEqual(station.player_cover("p2"), 0)
        station.grant_cover("unit1", 5)
        self.assertEqual(station.cover_durability("unit1"), 0)
        station.grant_player_cover("p2", "unit1", 5)
        self.assertEqual(station.player_cover("p2"), 0)
        # 队长候选拒收
        self.assertFalse(station.apply_captain("p1"))
        # 通缉事实未被抹去
        self.assertIsNotNone(station.open_wanted())

    def test_clock_disabled_behaves_like_dawn(self) -> None:
        """m5_clock 关闭：即使回合已进终焉数值段，行为恒同黎明（回归保护）。"""
        experiments.disable("m5_clock")
        state, players = _make()
        state.current_round = APOCALYPSE_ROUND
        station = _seat_police(state)
        station.refresh_cover(state)
        self.assertGreater(station.player_cover("p2"), 0)
        case = station.file_case("p1", "p2", evidence=1,
                                 evidence_kind=EVIDENCE_VICTIM)
        self.assertIsNotNone(case)
        station.verify_case(case.case_id)
        self.assertTrue(station.r2_tick(state, APOCALYPSE_ROUND))

    def test_default_profile_unchanged(self) -> None:
        """默认 profile（legacy，无任何实验）：不操作时钟轮次，行为不变。"""
        experiments.reset()  # 清运行时覆盖 → 回到 config profile=legacy
        state = GameState()
        for pid in ("p1", "p2"):
            p = Player(pid, pid.upper(), controller=ForfeitController())
            state.add_player(p)
            p.max_hp = 20
            p.hp = 20
            p.location = "商店"
        station = PoliceStation()
        station.ensure_roster()
        station.set_state_ref(state)
        for u in station.units():
            u.location = "商店"
        station.refresh_cover(state)
        self.assertGreater(station.player_cover("p2"), 0)
        case = station.file_case("p1", "p2", evidence=1,
                                 evidence_kind=EVIDENCE_VICTIM)
        self.assertIsNotNone(case)


if __name__ == "__main__":
    unittest.main()
