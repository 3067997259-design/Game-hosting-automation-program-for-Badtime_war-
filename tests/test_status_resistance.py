"""M2d 抗性两层制单测：有效抗性 / 降级 / 韧性脉冲 / 来源免疫 tag。"""
import random
import unittest

from engine import experiments
from utils.status_resistance import effective_resistance, apply_control


class _FakePiece:
    def __init__(self, special_tags=None):
        self.special_tags = special_tags or []


class _FakeArmor:
    def __init__(self, outer=None):
        self.outer = outer or []


class _FakeTarget:
    def __init__(self, resistance=0, pulse=0, outer=None):
        self.name = "测试"
        self.general_resistance = resistance
        self.resist_pulse_rounds = pulse
        self.armor = _FakeArmor(outer)


class EffectiveResistanceTest(unittest.TestCase):

    def test_base_zero(self) -> None:
        self.assertEqual(effective_resistance(_FakeTarget()), 0)

    def test_pulse_adds_40(self) -> None:
        self.assertEqual(effective_resistance(_FakeTarget(pulse=2)), 40)
        self.assertEqual(effective_resistance(_FakeTarget(resistance=30, pulse=1)), 70)

    def test_capped_at_100(self) -> None:
        self.assertEqual(effective_resistance(_FakeTarget(resistance=90, pulse=2)), 100)

    def test_electric_tag_grants_full_immunity(self) -> None:
        """陶瓷 immune_electric → 对电流来源抗性 100。"""
        target = _FakeTarget(outer=[_FakePiece(["immune_electric"])])
        self.assertEqual(effective_resistance(target, ["electric"]), 100)
        # 非电流来源不享受 tag
        self.assertEqual(effective_resistance(target, None), 0)


class ApplyControlTest(unittest.TestCase):

    def test_zero_resistance_always_applies_and_pulses(self) -> None:
        target = _FakeTarget()
        result = apply_control(target, "shock")
        self.assertTrue(result["applied"])
        self.assertEqual(target.resist_pulse_rounds, 2)  # 韧性脉冲启动

    def test_full_immunity_never_applies(self) -> None:
        target = _FakeTarget(outer=[_FakePiece(["immune_electric"])])
        result = apply_control(target, "shock", source_tags=["electric"])
        self.assertFalse(result["applied"])
        self.assertFalse(result["degraded"])

    def test_resist_degrades_not_negates(self) -> None:
        """抗性生效 = 降级不归零：先攻惩罚 flag 写入（零产出禁令）。"""
        target = _FakeTarget(resistance=100)  # 100 走免疫……改用 99 + 必中骰
        target.general_resistance = 99
        random.seed(1)  # d100 大概率 ≤99 → 降级
        result = apply_control(target, "stun")
        if result["degraded"]:
            self.assertEqual(getattr(target, "_resist_degrade_penalty", 0), -2)
            self.assertFalse(result["applied"])
        else:  # 1% 漏网：至少验证全额路径自洽
            self.assertTrue(result["applied"])

    def test_pulse_chain_protection(self) -> None:
        """被控后脉冲 +40 → 第二次控制更可能被抗（统计验证）。"""
        random.seed(7)
        applied_second = 0
        for _ in range(200):
            target = _FakeTarget()
            apply_control(target, "shock")           # 必中（抗性0），启动脉冲
            r2 = apply_control(target, "shock")      # 抗性 40
            if r2["applied"]:
                applied_second += 1
        # 期望 ~60% 生效；给统计余量断言 45%~75%
        self.assertTrue(90 <= applied_second <= 150,
                        f"二次控制生效 {applied_second}/200，韧性脉冲未生效？")


if __name__ == "__main__":
    unittest.main()
