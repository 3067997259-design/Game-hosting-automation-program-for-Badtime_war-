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

    return f"👊 {player.name} 找到了 {target.name}！双方进入面对面关系。"
