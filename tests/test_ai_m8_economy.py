"""M8.1 步骤 4：经济 credits 接地（D3）——_score_destination m8_ai 分叉。

- on 路径：按 player.credits + balance.economy 价格决策（免费/付费/财产税三档）。
- off 路径：voucher 模型逐字不变（凭证/通行证语义）。
"""
import unittest
from types import SimpleNamespace

from engine import experiments
from controllers.ai.develop_mixin import DevelopMixin


def _enable(*flags):
    experiments.reset()
    for f in flags:
        experiments.enable(f)


def _player(credits=0, vouchers=0, has_pass=False):
    return SimpleNamespace(credits=credits, vouchers=vouchers,
                           has_military_pass=has_pass)


class _StubMixin(DevelopMixin):
    def __init__(self, personality="balanced"):
        self.personality = personality

    def _count_enemies_at(self, dest, player, state):
        return 0

    def _already_has_item(self, player, item_name):
        return False


class ScoreDestinationGatedTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable("m8_ai")
        self.m = _StubMixin()

    def tearDown(self) -> None:
        experiments.reset()

    def test_on_affordable_shop_full_priority(self) -> None:
        p = _player(credits=5)
        score = self.m._score_destination("商店", [("weapon", 5)], p, None, 0, False)
        self.assertEqual(score, 5.0)

    def test_on_poor_shop_discounted(self) -> None:
        p = _player(credits=0)
        score = self.m._score_destination("商店", [("weapon", 5)], p, None, 0, False)
        self.assertEqual(score, 5 * 0.3)

    def test_on_free_provider_always_full(self) -> None:
        p = _player(credits=0)
        score = self.m._score_destination("魔法所", [("weapon", 5)], p, None, 0, False)
        self.assertEqual(score, 5.0)

    def test_on_military_pass_tiers(self) -> None:
        with_pass = _player(credits=0, has_pass=True)
        self.assertEqual(
            self.m._score_destination("军事基地", [("weapon", 5)], with_pass, None, 0, True),
            5.0)
        rich = _player(credits=5, has_pass=False)
        self.assertEqual(
            self.m._score_destination("军事基地", [("weapon", 5)], rich, None, 0, False),
            5 * 0.5)
        poor = _player(credits=1, has_pass=False)
        self.assertEqual(
            self.m._score_destination("军事基地", [("weapon", 5)], poor, None, 0, False),
            5 * 0.1)

    def test_on_surgery_tiers(self) -> None:
        rich = _player(credits=6)
        self.assertEqual(
            self.m._score_destination("医院", [("inner_armor", 2)], rich, None, 0, False),
            2.0)
        poor = _player(credits=3)
        # 3 个手术提供者各 0.2×（逐条打折，与 off 路径逐字一致）
        self.assertEqual(
            self.m._score_destination("医院", [("inner_armor", 2)], poor, None, 0, False),
            2 * 0.2 * 3)


class ScoreDestinationOffVerbatimTest(unittest.TestCase):

    def setUp(self) -> None:
        _enable()
        self.m = _StubMixin()

    def tearDown(self) -> None:
        experiments.reset()

    def test_off_voucher_shop_tiers(self) -> None:
        with_voucher = _player(vouchers=1)
        self.assertEqual(
            self.m._score_destination("商店", [("weapon", 5)], with_voucher, None, 1, False),
            5.0)
        no_voucher = _player(vouchers=0)
        self.assertEqual(
            self.m._score_destination("商店", [("weapon", 5)], no_voucher, None, 0, False),
            5 * 0.3)

    def test_off_pass_tiers(self) -> None:
        with_pass = _player(has_pass=True)
        self.assertEqual(
            self.m._score_destination("军事基地", [("weapon", 5)], with_pass, None, 0, True),
            5.0)
        has_voucher = _player(vouchers=1)
        self.assertEqual(
            self.m._score_destination("军事基地", [("weapon", 5)], has_voucher, None, 1, False),
            5 * 0.5)
        no_voucher = _player(vouchers=0)
        self.assertEqual(
            self.m._score_destination("军事基地", [("weapon", 5)], no_voucher, None, 0, False),
            5 * 0.1)

    def test_off_surgery_tiers(self) -> None:
        with_voucher = _player(vouchers=1)
        self.assertEqual(
            self.m._score_destination("医院", [("inner_armor", 2)], with_voucher, None, 1, False),
            2.0)
        no_voucher = _player(vouchers=0)
        self.assertEqual(
            self.m._score_destination("医院", [("inner_armor", 2)], no_voucher, None, 0, False),
            2 * 0.2 * 3)


if __name__ == "__main__":
    unittest.main()
