"""行动类型：起床"""


def execute(player, game_state):
    """
    执行起床。
    效果：玩家出现在自己家中，行动回合结束。
    返回结果描述字符串。
    """
    home_id = f"home_{player.player_id}"
    player.is_awake = True
    # G2 舞台内强制起床：不覆盖已分配的座位
    if "liberamente_vivace" not in getattr(player, 'stage_statuses', set()):
        player.location = home_id
    game_state.markers.on_player_wake_up(player.player_id)
    game_state.log_event("wake_up", player=player.player_id,
                         location=player.location)
    # 天赋起床加成 hook
    if player.talent and hasattr(player.talent, 'on_wakeup'):
        wakeup_msg = player.talent.on_wakeup(player, game_state)
        if wakeup_msg:
            result_msg = f"☀️ {player.name} 起床了！出现在自己家中。\n{wakeup_msg}"
            return result_msg
    return f"☀️ {player.name} 起床了！出现在自己家中。"
