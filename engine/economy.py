"""信用点经济工具层（experiment: m4_gear，v2.0 §6）。

凭证（资格开关）→ 信用点（真扣费通货）的统一收口：各地点的 m4 分支
只调用本模块，价格全部来自 balance.json economy 分区。

收支三原则（§6.2）：
1. 禁止被动收入、禁止 home 产出——收入必须出门打工；
2. 战斗 = 对手的支出（修甲收费在商店服务，见 locations/shop.py）；
3. 手术/强买 = 财产税（全部信用点，含下限）。
"""
from __future__ import annotations
from typing import Any, Tuple

from engine import experiments
from engine.balance import get as bget


def m4_enabled() -> bool:
    return experiments.is_enabled("m4_gear")


def price(item_name: str) -> int:
    """物品售价（信用点）。未定价 = 0（免费）。"""
    return int(bget("economy", "sinks", item_name, default=0))


def work_income() -> int:
    return int(bget("economy", "faucets", "打工", default=2))


def can_afford(player: Any, item_name: str) -> Tuple[bool, str]:
    cost = price(item_name)
    if cost <= 0:
        return True, ""
    if getattr(player, "credits", 0) < cost:
        return False, f"信用点不足：「{item_name}」需要 {cost}，你有 {player.credits}"
    return True, ""


def charge(player: Any, item_name: str) -> int:
    """扣费并返回实付金额（调用方先过 can_afford）。"""
    cost = price(item_name)
    if cost > 0:
        player.credits -= cost
    return cost


def pay_all(player: Any, min_cost_key: str) -> Tuple[bool, int]:
    """财产税：清空全部信用点（含下限检查）。

    min_cost_key: balance economy 分区的下限键（surgery_min_cost 等）。
    返回 (是否支付成功, 实付金额)。
    """
    min_cost = int(bget("economy", min_cost_key, default=0))
    held = getattr(player, "credits", 0)
    if held < min_cost:
        return False, min_cost
    player.credits = 0
    return True, held
