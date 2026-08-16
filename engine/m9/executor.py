"""M9 命令执行助手（profile: m9-rfc）——G6 重演/借用核心的执行路径。

用 G6 自身状态执行类别命令（move/attack/interact/find/lock），目标/物品经
controller.choose 选择（无选择能力时取默认）。只调 actions.* 模块，不改 v2exp 逻辑。
"""
from __future__ import annotations

from typing import Any, List, Optional, Tuple

from engine.m9.text import m9_text


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
    actors = list(game_state.iter_actors()) if hasattr(
        game_state, "iter_actors") else [
            game_state.get_player(pid) for pid in game_state.player_order]
    targets = [
        actor for actor in actors
        if actor is not None
        and actor.player_id != player.player_id
        and actor.is_alive()
    ]
    if category == "move":
        from actions.move import ALL_LOCATIONS
        current = getattr(player, "location", None)
        candidates = [loc for loc in ALL_LOCATIONS if loc != current] or ALL_LOCATIONS
        dest = _pick(ctrl, m9_text("executor.replay_move_choose_location"), candidates)
        if dest is None:
            return m9_text("executor.no_move_location"), False
        from actions import move as _move
        return _move.execute(player, dest, game_state), True

    if category == "interact":
        from actions.interact import get_available_items
        names = get_available_items(player, game_state)
        item = _pick(ctrl, m9_text("executor.replay_interact_choose_item"), names)
        if item is None:
            return m9_text("executor.no_interact_item"), False
        from actions import interact as _interact
        return _interact.execute(player, item, game_state), True

    if category == "find":
        ids = [actor.player_id for actor in targets]
        target = _pick(ctrl, m9_text("executor.replay_find_choose_target"), ids)
        if target is None:
            return m9_text("executor.no_find_target"), False
        from actions import find_target as _find
        return _find.execute(player, target, game_state), True

    if category == "lock":
        ids = [actor.player_id for actor in targets]
        target = _pick(ctrl, m9_text("executor.replay_lock_choose_target"), ids)
        if target is None:
            return m9_text("executor.no_lock_target"), False
        from actions import lock_target as _lock
        return _lock.execute(player, target, game_state), True

    if category == "attack":
        # 受限追加/重演也必须消费公共合法动作目录；旧路径直接从所有存活
        # 目标中挑选并调用 attack.execute，会绕过面对面/锁定等攻击前置。
        from engine.action_enumerator import build_action_options
        commands = build_action_options(
            player, game_state, ["attack"]).get("attack", [])
        command = _pick(ctrl, m9_text("executor.replay_attack_choose_command"), commands)
        if command is None:
            return m9_text("executor.no_legal_attack_target"), False
        parts = command.split(maxsplit=2)
        if len(parts) != 3:
            return m9_text("executor.invalid_attack_command"), False
        target_name, weapon = parts[1], parts[2]
        target = next(
            (actor.player_id for actor in targets
             if getattr(actor, "name", "") == target_name), None)
        if target is None:
            return m9_text("executor.no_legal_attack_target"), False
        from actions import attack as _attack
        return _attack.execute(player, target, weapon, game_state), True

    return m9_text("executor.unknown_category", action_category=category), False
