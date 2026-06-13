"""M6 喝彩测试：获取去重 / 反合谋三闸 / 消耗 4 用途 / 加冕×2 / 往世层。"""
import unittest

from engine import experiments
from engine.game_state import GameState
from engine import applause
from models.player import Player
from controllers.forfeit_controller import ForfeitController


def _player(pid, state, **kw):
    p = Player(pid, f"玩家{pid}", controller=ForfeitController())
    p.is_awake = True
    p.location = "商店"
    for k, v in kw.items():
        setattr(p, k, v)
    state.add_player(p)
    return p


class ApplauseAwardTest(unittest.TestCase):

    def setUp(self):
        experiments.reset()
        for f in ("hp20", "m6_scoring"):
            experiments.enable(f)
        self.state = GameState()

    def tearDown(self):
        experiments.reset()

    def test_first_kill_once_globally(self):
        a = _player("p1", self.state)
        b = _player("p2", self.state)
        victim = _player("p3", self.state)
        applause.check_kill_applause(self.state, a, victim)
        self.assertEqual(a.applause, 2)  # 首杀
        applause.check_kill_applause(self.state, b, victim)
        self.assertEqual(b.applause, 0)  # 首杀已被领走

    def test_anti_collusion_gate(self):
        a = _player("p1", self.state, hp=4)  # 重伤
        target = _player("p2", self.state)
        # target 没打过 a → 反杀奖励被拦
        self.assertFalse(applause.award(self.state, a, "severe_revenge", target=target))
        self.assertEqual(a.applause, 0)
        # target 打过 a → 放行
        self.state.damage_relations.setdefault("p1", set()).add("p2")
        self.assertTrue(applause.award(self.state, a, "severe_revenge", target=target))
        self.assertEqual(a.applause, 3)

    def test_dedup_same_event(self):
        a = _player("p1", self.state)
        self.assertTrue(applause.award(self.state, a, "break_full_armor"))
        self.assertFalse(applause.award(self.state, a, "break_full_armor"))

    def test_coronation_doubles(self):
        a = _player("p1", self.state, _coronation_active=True)
        applause.award(self.state, a, "break_full_armor")
        self.assertEqual(a.applause, 4)  # 2×2


class ApplauseSpendTest(unittest.TestCase):

    def setUp(self):
        experiments.reset()
        for f in ("hp20", "m6_scoring"):
            experiments.enable(f)
        self.state = GameState()

    def tearDown(self):
        experiments.reset()

    def test_damage_bonus(self):
        from actions.applause_spend import execute
        a = _player("p1", self.state, applause=5)
        execute(a, "伤害加成", self.state)
        self.assertEqual(a.applause, 4)
        self.assertEqual(a._applause_damage_bonus, 2)

    def test_insufficient_applause(self):
        from actions.applause_spend import execute
        a = _player("p1", self.state, applause=0)
        msg, _ = execute(a, "偷看先攻", self.state)
        self.assertIn("不足", msg)

    def test_reroll_initiative_flag(self):
        from actions.applause_spend import execute
        a = _player("p1", self.state, applause=2)
        execute(a, "重掷先攻", self.state)
        self.assertTrue(a._applause_reroll_initiative)


if __name__ == "__main__":
    unittest.main()
