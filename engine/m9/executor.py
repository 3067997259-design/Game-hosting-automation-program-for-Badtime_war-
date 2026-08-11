"""M9 命令执行助手（profile: m9-rfc）——G6 重演/借用核心的执行路径。

用 G6 自身状态执行类别命令（move/attack/interact/find/lock），目标/物品经
controller.choose 选择（无选择能力时取默认）。只调 actions.* 模块，不改 v2exp 逻辑。
"""
from __future__ import annotations

from typing import Any, List, Optional, Tuple


def _pick(controller: Any, prompt: str, options: List[str]) -> Optional[str]:
    """选择器：choose 失败/返回非法项时取默认（首个）。"""
    if not options:
        return None
    try:
        choice = controller.choose(prompt, list(options))
        if choice in options:
            return choice
    except Exception:
        pass
    return options[0]


def execute_category(player: Any, game_state: Any, category: str) -> Tuple[str, bool]:
    """以 G6 自身状态重演一个类别模板。返回 (消息, 是否成功)。"""
    ctrl = getattr(player, "controller", None)
    if category == "move":
        from actions.move import ALL_LOCATIONS
        current = getattr(player, "location", None)
        candidates = [loc for loc in ALL_LOCATIONS if loc != current] or ALL_LOCATIONS
        dest = _pick(ctrl, "重演移动：选择地点", candidates)
        if dest is None:
            return "❌ 无可用移动地点", False
        from actions import move as _move
        return _move.execute(player, dest, game_state), True

    if category == "interact":
        from actions.interact import get_location_menu
        menu = get_location_menu(player.location)
        names = [k for k in menu.keys() if not k.startswith("_")]
        item = _pick(ctrl, "重演交互：选择物品", names)
        if item is None:
            return "❌ 当前位置无可交互物品", False
        from actions import interact as _interact
        return _interact.execute(player, item, game_state), True

    if category == "find":
        ids = [p.player_id for p in game_state.player_order
               if p != player.player_id
               and game_state.get_player(p).is_alive()]
        target = _pick(ctrl, "重演搜索：选择目标", ids)
        if target is None:
            return "❌ 无搜索目标", False
        from actions import find_target as _find
        return _find.execute(player, target, game_state), True

    if category == "lock":
        ids = [p.player_id for p in game_state.player_order
               if p != player.player_id
               and game_state.get_player(p).is_alive()]
        target = _pick(ctrl, "重演锁定：选择目标", ids)
        if target is None:
            return "❌ 无锁定目标", False
        from actions import lock_target as _lock
        return _lock.execute(player, target, game_state), True

    if category == "attack":
        weapons = [w.name for w in getattr(player, "weapons", []) if w]
        targets = [p.player_id for p in game_state.player_order
                   if p != player.player_id
                   and game_state.get_player(p).is_alive()]
        weapon = _pick(ctrl, "重演攻击：选择武器", weapons)
        target = _pick(ctrl, "重演攻击：选择目标", targets)
        if weapon is None or target is None:
            return "❌ 无武器或无目标", False
        from actions import attack as _attack
        return _attack.execute(player, target, weapon, game_state), True

    return f"❌ 未知类别 {category}", False
