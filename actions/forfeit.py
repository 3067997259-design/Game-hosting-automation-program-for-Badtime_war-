"""行动类型：放弃行动"""


def execute(player, game_state):
    """
    执行放弃行动。
    消耗行动回合，视为本轮执行过行动回合——清零未行动保底计数。
    但不视为带有效果的行动类型。
    """
    game_state.log_event("forfeit", player=player.player_id)
    return f"💤 {player.name} 选择放弃行动。（视为已行动，保底清零）"
