"""行动类型：找到玩家（近战攻击前置）"""



def execute(player, target_id, game_state):
    """
    对目标执行找到，建立面对面关系。
    返回结果描述字符串。
    """
    target = game_state.get_player(target_id)
    if not target:
        return f"❌ 找不到玩家 {target_id}"

    # 建立双向面对面标记
    game_state.markers.add_relation(player.player_id, "ENGAGED_WITH", target_id)
    game_state.markers.add_relation(target_id, "ENGAGED_WITH", player.player_id)
    game_state.log_event("find", player=player.player_id, target=target_id)

    # ---- 剪刀手一突·警觉：find 钩子 ----
    # 主动找到他人
    if (player.talent
            and hasattr(player.talent, 'on_find_someone')
            and not getattr(player, '_mythland_talent_suppressed', False)):
        player.talent.on_find_someone(player, target_id)
    # 被他人找到
    if (target.talent
            and hasattr(target.talent, 'on_found_by_someone')
            and not getattr(target, '_mythland_talent_suppressed', False)):
        target.talent.on_found_by_someone(target, player.player_id)

    msg = f"👊 {player.name} 找到了 {target.name}！双方进入面对面关系。"

    # ---- M4：find 顺带拾取本地点箭堆（风险换弹药，v2.0 §2.8） ----
    from engine.economy import m4_enabled
    if m4_enabled():
        piles = getattr(game_state, 'arrow_piles', {})
        ground = piles.get(player.location, 0)
        if ground > 0 and player.has_weapon("弓"):
            from engine.balance import get as _bget
            max_arrows = _bget("bow", "max_arrows", default=6)
            take = min(ground, max_arrows - player.arrows)
            if take > 0:
                player.arrows += take
                piles[player.location] = ground - take
                msg += f"\n   🏹 顺带拾起地上的 {take} 支箭（{player.arrows}/{max_arrows}）"

        # ---- M5：find 顺带拾取本地点击杀掉落（钱包/箭/装备/物品，v2.0 §6.4） ----
        loot = getattr(game_state, 'ground_loot', {}).get(player.location)
        if loot:
            picked = _pick_up_loot(player, loot, game_state)
            if picked:
                msg += "\n   💰 拾取掉落：" + "、".join(picked)

    return msg


def _pick_up_loot(player, loot, game_state):
    """从本地点掉落堆拾取（1 行动 = find 顺带）。返回拾取项描述列表。"""
    picked = []
    if loot.get("credits", 0) > 0:
        player.credits = getattr(player, "credits", 0) + loot["credits"]
        picked.append(f"{loot['credits']} 信用点")
        loot["credits"] = 0
    if loot.get("arrows", 0) > 0 and player.has_weapon("弓"):
        from engine.balance import get as _bget
        max_arrows = _bget("bow", "max_arrows", default=6)
        take = min(loot["arrows"], max_arrows - player.arrows)
        if take > 0:
            player.arrows += take
            loot["arrows"] -= take
            picked.append(f"{take} 支箭")
    from models.equipment import make_weapon, make_armor, make_item
    for wname in list(loot.get("weapons", [])):
        w = make_weapon(wname)
        if w and not player.has_weapon(wname):
            player.add_weapon(w)
            loot["weapons"].remove(wname)
            picked.append(wname)
    for iname in list(loot.get("items", [])):
        it = make_item(iname)
        if it:
            player.add_item(it)
            loot["items"].remove(iname)
            picked.append(iname)
    return picked
