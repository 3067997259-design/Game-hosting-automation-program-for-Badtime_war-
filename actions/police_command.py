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
        # 支持同时装备武器和护甲，格式：equip <警察ID> <武器> [护甲]
        # 或者分别指定：equip <警察ID> weapon <武器名> 和 equip <警察ID> armor <护甲名>
        equipment = parsed.get("equipment")
        equipment_type = parsed.get("equipment_type")  # weapon 或 armor
        
        if equipment_type:
            # 分别指定武器或护甲
            if equipment_type == "weapon":
                if not equipment:
                    return "❌ 请指定武器名称", {}
                result = police_engine.captain_control_police(
                    captain_id=player.player_id,
                    police_id=police_id,
                    command="equip",
                    weapon=equipment
                )
                return result, {}
            elif equipment_type == "armor":
                if not equipment:
                    return "❌ 请指定护甲名称", {}
                result = police_engine.captain_control_police(
                    captain_id=player.player_id,
                    police_id=police_id,
                    command="equip",
                    armor=equipment
                )
                return result, {}
            else:
                return f"❌ 未知的装备类型：{equipment_type}", {}
        else:
            # 传统模式：只指定一个装备（可能是武器或护甲）
            if not equipment:
                return "❌ 请指定装备名称", {}
            # 先尝试作为武器，警察引擎内部会验证
            result = police_engine.captain_control_police(
                captain_id=player.player_id,
                police_id=police_id,
                command="equip",
                weapon=equipment
            )
            # 如果装备被拒绝，尝试作为护甲
            if "❌ 警察不能装备" in result:
                result = police_engine.captain_control_police(
                    captain_id=player.player_id,
                    police_id=police_id,
                    command="equip",
                    armor=equipment
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