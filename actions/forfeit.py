"""行动类型：放弃行动"""


def execute(player, game_state):
    """
    执行放弃行动。
    消耗行动回合，视为本轮执行过行动回合——清零未行动保底计数。
    但不视为带有效果的行动类型。
    """
    game_state.log_event("forfeit", player=player.player_id)
    # 天赋侧 forfeit 钩子（G0 调整呼吸：免疫期内 forfeit 回血，hasattr 协议，
    # 不要求 BaseTalent 签名变更）
    talent = getattr(player, "talent", None)
    if talent is not None and hasattr(talent, "m9_on_forfeit"):
        try:
            extra = talent.m9_on_forfeit(player)
            if extra:
                return (f"💤 {player.name} 选择放弃行动。（视为已行动，保底清零）"
                        f"{extra}")
        except Exception:
            pass
    return f"💤 {player.name} 选择放弃行动。（视为已行动，保底清零）"
