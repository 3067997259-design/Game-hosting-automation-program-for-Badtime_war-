"""行动类型：队长操控警察"""

def execute(player, parsed, game_state):
    """
    执行队长操控警察命令。
    
    参数：
      player: 队长玩家
      parsed: 解析后的命令字典，包含：
        subcommand: "move"/"equip"/"attack"
        police_id: 警察单位ID
        location/equipment/target: 根据子命令不同
      game_state: 游戏状态
    
    返回 (结果消息str, 额外数据dict)
    """
    if not player.is_captain:
        return "❌ 只有队长可以操控警察", {}
    
    if not hasattr(game_state, 'police_engine') or not game_state.police_engine:
        return "❌ 警察系统未初始化", {}
    
    police_engine = game_state.police_engine
    subcommand = parsed.get("subcommand")
    police_id = parsed.get("police_id")
    
    if subcommand == "move":
        location = parsed.get("location")
        if not location:
            return "❌ 请指定目的地", {}
        result = police_engine.captain_control_police(
            captain_id=player.player_id,
            police_id=police_id,
            command="move",
            location=location
        )
        return result, {}
    
    elif subcommand == "equip":
        equipment = parsed.get("equipment")
        if not equipment:
            return "❌ 请指定装备名称", {}
        # 简单判断是武器还是护甲（根据警察引擎内部验证）
        result = police_engine.captain_control_police(
            captain_id=player.player_id,
            police_id=police_id,
            command="equip",
            weapon=equipment  # 警察引擎会判断是武器还是护甲
        )
        return result, {}
    
    elif subcommand == "attack":
        target = parsed.get("target")
        if not target:
            return "❌ 请指定攻击目标", {}
        result = police_engine.captain_control_police(
            captain_id=player.player_id,
            police_id=police_id,
            command="attack",
            target=target
        )
        return result, {}
    
    else:
        return f"❌ 未知的子命令：{subcommand}", {}
