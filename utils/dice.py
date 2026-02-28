"""骰子工具"""

import random


def roll_d4() -> int:
    """投掷一个四面骰，返回1~4"""
    return random.randint(1, 4)


def roll_d6() -> int:
    """投掷一个六面骰，返回1~6"""
    return random.randint(1, 6)