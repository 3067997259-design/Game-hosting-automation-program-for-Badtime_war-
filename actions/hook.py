"""行动类型：钩索（M4 神器，experiment: m4_gear，v2.0 §2.9）。

位移三件套之「拉」（科技属性）：
- hook <目标>  拉人：把任意地点的目标拽至己方地点 + 2 科伤，走命中骰；
                擦钩（被闪避）= 不位移，但目标本轮全部闪避加成失效（零产出禁令）
- hook self <地点>  拉己：钩向任意地点位移，不触发借机攻击
                    （机械把你拽走，对手刀砍空气）

双模式共享冷却（balance hook.cooldown_rounds）——用钩逃命=放弃开团权，反之亦然。
"""
from typing import Any, Dict, Tuple

from engine.balance import get as bget


def execute(player: Any, parsed: Dict, game_state: Any) -> Tuple[str, bool]:
    """执行钩索（validator 已校验持有/冷却/目标合法）。"""
    mode = parsed.get("mode")
    if mode == "self":
        return _pull_self(player, parsed.get("destination"), game_state)
    return _pull_target(player, parsed.get("target"), game_state)


def _set_cooldown(player: Any, game_state: Any) -> None:
    player._last_hook_round = game_state.current_round


def _pull_self(player: Any, destination: str, game_state: Any) -> Tuple[str, bool]:
    """拉己：直接位移（不走 move.execute → 天然不触发借机攻击）。"""
    old = player.location
    player.location = destination
    player.moved_this_round = True  # 主动位移（移动闪避来源）
    game_state.markers.on_player_move(player.player_id)  # 清锁定/面对面
    _set_cooldown(player, game_state)
    game_state.log_event("hook", player=player.player_id, mode="self",
                         from_loc=old, to_loc=destination)
    return f"🪝 {player.name} 钩向 {destination}，瞬间脱离！", True


def _pull_target(player: Any, target_str: str, game_state: Any) -> Tuple[str, bool]:
    """拉人：拽至己方地点 + 科伤，走命中骰；擦钩则闪避失效。"""
    from cli.validator import resolve_player_target
    target_id = resolve_player_target(target_str, game_state)
    target = game_state.get_player(target_id)
    if not target:
        return "❌ 找不到目标", False

    _set_cooldown(player, game_state)

    # 命中判定（拉拽走命中体系；冷箭/隐身等照常影响）
    from combat.accuracy import compute_hit_chance, roll_hit
    chance, _bd = compute_hit_chance(player, target,
                                     _hook_weapon(), game_state)
    hit, roll = (True, 0) if chance >= 100 else roll_hit(chance)

    if not hit:
        # 擦钩：不位移，但目标本轮闪避加成失效（accuracy 检查此 flag）
        target._hook_no_evasion_round = game_state.current_round
        game_state.log_event("hook", player=player.player_id, mode="pull",
                             target=target_id, grazed=True)
        return (f"🪝 {player.name} 的钩索擦过 {target.name}（掷 {roll} > {chance}）"
                f"——未能拽动，但 {target.name} 本轮闪避全失效！", True)

    # 命中：拽至己方地点 + 科伤
    old = target.location
    target.location = player.location
    game_state.markers.on_player_move(target_id)
    from combat.damage_resolver import resolve_damage
    result = resolve_damage(attacker=player, target=target,
                            weapon=_hook_weapon(), game_state=game_state)
    game_state.log_event("hook", player=player.player_id, mode="pull",
                         target=target_id, from_loc=old,
                         to_loc=player.location, result=result)
    lines = [f"🪝 {player.name} 用钩索将 {target.name} 从 {old} 拽到面前！"]
    for detail in result.get("details", []):
        lines.append(f"   {detail}")
    if result.get("killed"):
        player.kill_count += 1
        game_state.markers.on_player_death(target_id)
        if game_state.police_engine:
            game_state.police_engine.on_player_death(target_id)
        from cli import display
        display.show_death(target.name, f"被 {player.name} 钩索拽杀")
        from engine import applause as _applause
        _applause.check_kill_applause(game_state, player, target)
        from engine.round_manager import RoundManager
        RoundManager.notify_all_talents_of_death(
            game_state, target_id, killer_id=player.player_id)
    return "\n".join(lines), True


def _hook_weapon():
    """钩索的伤害载体（临时 Weapon，科技属性）。"""
    from models.equipment import Weapon, WeaponRange
    from utils.attribute import Attribute
    return Weapon("钩索", Attribute.TECH, bget("hook", "damage", default=2),
                  WeaponRange.MELEE, special_tags=["hook"])
