"""骰子工具（向后兼容层）

roll_d4 / roll_d6 保持原有公共接口，
内部改用 utils.rng.get_rng()，
从而支持测试时通过 set_rng() 注入固定结果。
"""

from utils.rng import get_rng


def roll_d4() -> int:
    """投掷一个四面骰，返回 1~4。"""
    return get_rng().d4()


def roll_d6() -> int:
    """投掷一个六面骰，返回 1~6。"""
    return get_rng().d6()
