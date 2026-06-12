"""行动类型：射箭（M4 弓系统，experiment: m4_gear，v2.0 §2.8）。

shoot <目标> —— 弓的专属攻击动词：
- 跨地点合法（唯一常规跨地点武器）；锁定不是前置只是命中加成
  （未锁定吃 accuracy.unlocked_ranged_penalty −15）
- 消耗 1 支箭（无限模块豁免）；射出的箭落入目标所在地点的箭堆
  （find 行动免费拾回——风险换弹药）
- 伤害/属性/附带效果按已装模块换算（engine/bow_modules.compute_shot）
"""
from typing import Any, Dict, Tuple

from engine.balance import get as bget


def execute(player: Any, target: Any, game_state: Any) -> Tuple[str, Dict]:
    """执行射箭（validator 已校验：有弓/有箭或无限/目标合法可见）。"""
    from engine.bow_modules import compute_shot
    from combat.damage_resolver import resolve_damage

    shot = compute_shot(player)  # {weapon, infinite, burn_stacks, knockback, ...}
    weapon = shot["weapon"]

    # 弹药消耗与落箭（无限模块豁免且不落箭——能量箭，v2.0 §2.8）
    if not shot["infinite"]:
        player.arrows -= 1
        target_loc = getattr(target, "location", None)
        if target_loc:
            if not hasattr(game_state, "arrow_piles"):
                game_state.arrow_piles = {}
            game_state.arrow_piles[target_loc] = (
                game_state.arrow_piles.get(target_loc, 0) + 1)

    result = resolve_damage(
        attacker=player, target=target, weapon=weapon,
        game_state=game_state)

    lines = [f"🏹 {player.name} 张弓射向 {target.name}！"]
    for detail in result.get("details", []):
        lines.append(f"   {detail}")
    if not shot["infinite"]:
        lines.append(f"   箭矢 {player.arrows}/{bget('bow', 'max_arrows', default=6)}"
                     f"（箭落在 {getattr(target, 'location', '?')}）")

    # 模块附带效果：命中且未被闪避擦伤才触发（零产出禁令同款规则）
    effective_hit = (result.get("success")
                     and not result.get("grazed_by_evasion", False)
                     and not result.get("killed", False))
    if effective_hit and shot["burn_stacks"] > 0 and target.is_alive():
        from engine.bow_modules import apply_burn
        msg = apply_burn(target, shot["burn_stacks"])
        lines.append(f"   {msg}")
    if (effective_hit and shot["knockback"] and target.is_alive()
            and getattr(target, "location", None) == player.location):
        from engine.bow_modules import apply_knockback
        msg = apply_knockback(target, player, game_state,
                              shot["knockback_initiative_penalty"])
        lines.append(f"   {msg}")

    game_state.log_event("shoot", attacker=player.player_id,
                         target=target.player_id, weapon="弓",
                         modules=list(player.bow_modules), result=result)
    return "\n".join(lines), result
