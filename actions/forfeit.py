"""行动类型：放弃行动"""


def execute(player, game_state):
    """
    执行放弃行动。
    消耗行动回合，但不视为「行动」——不清零未行动保底计数。
    返回结果描述字符串。
    """
    game_state.log_event("forfeit", player=player.player_id)
    return f"💤 {player.name} 选择放弃行动。（不影响未行动保底加成）"
