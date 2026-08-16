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
        g0_talent = getattr(player, "talent", None)
        if (ground > 0 and g0_talent is not None
                and hasattr(g0_talent, "receive_arrows")):
            res = g0_talent.receive_arrows(ground, source="find_arrow_pile")
            consumed = res["arrows_consumed"]
            loaded = res["bullets_loaded"]
            if consumed > 0:
                # 只扣除实际转化的箭数（弹匣空间不足时保留剩余箭堆）
                piles[player.location] = ground - consumed
                msg += f"\n   🔫 将 {consumed} 支箭转化并装填 {loaded} 发子弹"
            else:
                msg += "\n   🔫 弹匣已满，箭堆未动"
        elif ground > 0 and player.has_weapon("弓"):
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
    g0_talent = getattr(player, "talent", None)
    if (loot.get("arrows", 0) > 0 and g0_talent is not None
            and hasattr(g0_talent, "receive_arrows")):
        arrows = loot["arrows"]
        res = g0_talent.receive_arrows(arrows, source="ground_loot")
        consumed = res["arrows_consumed"]
        loaded = res["bullets_loaded"]
        if consumed > 0:
            # 只扣除实际转化的箭数（弹匣空间不足时保留剩余掉落箭）
            loot["arrows"] = arrows - consumed
            picked.append(f"{consumed} 支箭→{loaded} 发子弹")
        else:
            picked.append("弹匣已满（箭未动）")
    elif loot.get("arrows", 0) > 0 and player.has_weapon("弓"):
        from engine.balance import get as _bget
        max_arrows = _bget("bow", "max_arrows", default=6)
        take = min(loot["arrows"], max_arrows - player.arrows)
        if take > 0:
            player.arrows += take
            loot["arrows"] -= take
            picked.append(f"{take} 支箭")
    from models.equipment import make_armor, make_item, make_weapon

    def unpack(entry, kind):
        if isinstance(entry, dict):
            return (entry.get("name", ""), entry.get("object"),
                    entry.get("source_slot", ""))
        factory = {
            "weapon": make_weapon,
            "armor": make_armor,
            "item": make_item,
        }[kind]
        return entry, factory(entry), ""

    def mark_relic(name, source_slot, kind):
        if (source_slot and g0_talent is not None
                and hasattr(g0_talent, "mark_relic")):
            g0_talent.mark_relic(name, source_slot, kind=kind)

    for entry in list(loot.get("weapons", [])):
        wname, w, source_slot = unpack(entry, "weapon")
        if w and not player.has_weapon(wname):
            player.add_weapon(w)
            loot["weapons"].remove(entry)
            picked.append(wname)
            mark_relic(wname, source_slot, "weapon")
    for entry in list(loot.get("armor", [])):
        aname, piece, source_slot = unpack(entry, "armor")
        if piece:
            ok, _ = player.add_armor(piece)
            if ok:
                loot["armor"].remove(entry)
                picked.append(aname)
                mark_relic(aname, source_slot, "armor")
    for entry in list(loot.get("items", [])):
        iname, it, source_slot = unpack(entry, "item")
        if it:
            player.add_item(it)
            loot["items"].remove(entry)
            picked.append(iname)
            mark_relic(iname, source_slot, "item")
    return picked
