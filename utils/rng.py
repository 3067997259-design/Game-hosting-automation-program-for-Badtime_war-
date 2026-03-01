"""
可注入的随机数抽象层（Rng）

用法
----
正常游戏（真实随机）：
    from utils.rng import Rng
    rng = Rng()           # 每次调用 random.randint
    rng.d4()              # 1~4
    rng.d6()              # 1~6

测试（固定序列）：
    rng = Rng(fixed_sequence=[3, 1, 4, 2])
    rng.d4()  # → 3
    rng.d4()  # → 1  （依次消费序列）
    # 序列耗尽后自动回绕，不会抛异常

全局单例（向后兼容 utils/dice.py）：
    from utils.rng import get_rng, set_rng
    set_rng(Rng(fixed_sequence=[2, 2]))  # 测试时替换
    get_rng().d4()                       # → 2
"""

import random
from typing import Optional, List


class Rng:
    """随机数生成器，支持注入固定序列以实现可复现测试。"""

    def __init__(self, fixed_sequence: Optional[List[int]] = None, seed: Optional[int] = None):
        """
        Parameters
        ----------
        fixed_sequence : list[int] | None
            若提供，则按顺序依次返回序列中的值（循环使用）。
            适合单元测试中精确控制骰点结果。
        seed : int | None
            若提供且未提供 fixed_sequence，则以此 seed 初始化伪随机。
            适合需要"可复现但不固定"的场景（如回归测试）。
        """
        self._sequence = fixed_sequence
        self._index = 0
        if seed is not None and fixed_sequence is None:
            random.seed(seed)

    def _next_fixed(self) -> int:
        """从固定序列中取下一个值（循环）。"""
        val = self._sequence[self._index % len(self._sequence)]
        self._index += 1
        return val

    def roll(self, sides: int) -> int:
        """投掷一个 sides 面骰，返回 1~sides 的整数。"""
        if self._sequence is not None:
            return self._next_fixed()
        return random.randint(1, sides)

    def d4(self) -> int:
        """投掷四面骰，返回 1~4。"""
        return self.roll(4)

    def d6(self) -> int:
        """投掷六面骰，返回 1~6。"""
        return self.roll(6)


# ── 全局单例（向后兼容 utils/dice.py 的 roll_d4 / roll_d6 函数） ──

_global_rng: Rng = Rng()


def get_rng() -> Rng:
    """返回当前全局 Rng 单例。"""
    return _global_rng


def set_rng(rng: Rng) -> None:
    """替换全局 Rng 单例。测试 setUp 中调用，tearDown 中还原。"""
    global _global_rng
    _global_rng = rng


def reset_rng() -> None:
    """将全局 Rng 重置为真实随机（测试 tearDown 中调用）。"""
    global _global_rng
    _global_rng = Rng()
