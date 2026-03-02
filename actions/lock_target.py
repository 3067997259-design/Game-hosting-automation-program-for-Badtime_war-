"""行动类型：锁定玩家（远程攻击前置）"""


def execute(player, target_id, game_state):
    """
    对目标执行锁定，放置「被你锁定」标记。
    返回结果描述字符串。
    """
    target = game_state.get_player(target_id)
    if not target:
        return f"❌ 找不到玩家 {target_id}"

    # 放置锁定标记
    game_state.markers.add_relation(target_id, "LOCKED_BY", player.player_id)
    game_state.log_event("lock", player=player.player_id, target=target_id)
    return f"🎯 {player.name} 锁定了 {target.name}！（可进行远程攻击）"
