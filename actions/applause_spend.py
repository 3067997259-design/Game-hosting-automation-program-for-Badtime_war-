"""喝彩消耗（M6d，experiment: m6_scoring，v2.0 §4.2）。

喝彩的"两用资源"消耗面——4 个用途：
- 伤害+2（1点）：下次攻击伤害 +2（buff flag，仿 _card_damage_bonus）
- 重掷先攻（1点）：下轮 R1 先攻骰重掷
- 偷看先攻（2点）：揭示下轮先攻顺序（信息）
- 抵消犯罪（2点）：清除自己一条犯罪记录

balance applause_spend 分区给成本。AI/bot 不会用（M8 适配），主要热座人类用。
"""
from typing import Any, Tuple

from engine.balance import get as bget

# 用途 → 成本
_USES = {
    "伤害加成": 1,
    "重掷先攻": 1,
    "偷看先攻": 2,
    "抵消犯罪": 2,
}


def cost_of(use: str) -> int:
    return _USES.get(use, 0)


def available_uses(player: Any) -> list:
    """玩家当前喝彩点够用的用途列表（供 special_op 注入选项）。"""
    bal = getattr(player, "applause", 0)
    return [u for u, c in _USES.items() if bal >= c]


def execute(player: Any, use: str, game_state: Any) -> Tuple[str, bool]:
    """执行喝彩消耗。返回 (消息, 是否消耗回合)。多数不消耗回合（即时增益）。"""
    cost = cost_of(use)
    if cost <= 0:
        return f"❌ 未知喝彩用途「{use}」", False
    if getattr(player, "applause", 0) < cost:
        return f"❌ 喝彩点不足（需 {cost}，有 {player.applause}）", False

    player.applause -= cost

    if use == "伤害加成":
        player._applause_damage_bonus = getattr(player, "_applause_damage_bonus", 0) + 2
        return f"👏 {player.name} 消耗 {cost} 喝彩：下次攻击伤害 +2", False
    if use == "重掷先攻":
        player._applause_reroll_initiative = True
        return f"👏 {player.name} 消耗 {cost} 喝彩：下轮先攻将重掷", False
    if use == "偷看先攻":
        # 揭示信息——置 flag，R1 时对该玩家展示（M6d 简化：记 flag）
        player._applause_peek_initiative = True
        return f"👏 {player.name} 消耗 {cost} 喝彩：下轮先攻顺序将对你揭示", False
    if use == "抵消犯罪":
        pe = getattr(game_state, "police_engine", None)
        if pe and hasattr(pe, "police"):
            crimes = getattr(pe.police, "crimes", None)
            if crimes and player.player_id in crimes and crimes[player.player_id]:
                crimes[player.player_id].pop()
                if not crimes[player.player_id]:
                    player.is_criminal = False
                return f"👏 {player.name} 消耗 {cost} 喝彩：抵消一条犯罪记录", False
        player.applause += cost  # 没有犯罪可抵消，退款
        return f"❌ {player.name} 没有犯罪记录可抵消（喝彩已退还）", False

    return f"❌ 未知喝彩用途「{use}」", False
