"""行动类型：起床"""


def execute(player, game_state):
    """
    执行起床。
    效果：玩家出现在自己家中，行动回合结束。
    返回结果描述字符串。
    """
    home_id = f"home_{player.player_id}"
    player.is_awake = True
    player.location = home_id
    game_state.markers.on_player_wake_up(player.player_id)
    game_state.log_event("wake_up", player=player.player_id, location=home_id)
    return f"☀️ {player.name} 起床了！出现在自己家中。"
