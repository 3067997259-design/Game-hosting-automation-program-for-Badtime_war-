"""行动类型：移动"""

# Phase 1 可用地点列表（后续Phase会扩展）
ALL_LOCATIONS = [
    "商店", "魔法所", "医院", "军事基地", "警察局",
    # 家是动态的：home_玩家id
]


def get_all_valid_locations(game_state):
    """获取所有合法地点名称列表（包含各玩家的家）"""
    locations = list(ALL_LOCATIONS)
    for pid in game_state.player_order:
        locations.append(f"home_{pid}")
    return locations


def get_location_display_name(loc_id, game_state):
    """将地点ID转为显示名"""
    if loc_id.startswith("home_"):
        pid = loc_id[5:]
        p = game_state.get_player(pid)
        if p:
            return f"{p.name}的家"
        return f"玩家{pid}的家"
    return loc_id


def execute(player, destination, game_state):
    """
    执行移动。
    效果：玩家从当前地点移动到目标地点。
    触发标记联动（清锁定/清面对面）。
    如果目标是军事基地且玩家有凭证但无通行证，提供强买选项。
    返回结果描述字符串。
    """
    old_location = player.location
    player.location = destination

    # 触发标记联动
    game_state.markers.on_player_move(player.player_id)

    # 军事基地：到达时提供强买通行证选项
    if destination == "军事基地" and not player.has_military_pass and player.vouchers >= 1:
        from locations.military_base import try_force_entry
        if hasattr(player, 'controller') and player.controller:
            do_force = player.controller.confirm(
                f"你没有军事基地通行证，但拥有{player.vouchers}张凭证。"
                f"是否消耗所有凭证强买通行证？（不消耗行动回合）"
            )
            if do_force:
                success, msg = try_force_entry(player, game_state)
                if success:
                    from cli import display
                    display.show_info(msg)

    # 全息影像：检查是否进入影像区域
    for pid in game_state.player_order:
        p = game_state.get_player(pid)
        if p and p.talent and hasattr(p.talent, 'on_player_move_to'):
            enter_lines = p.talent.on_player_move_to(player, destination)
            if enter_lines:
                from cli import display
                for line in enter_lines:
                    display.show_info(line)

    # 犯罪检查（朝阳好市民扩展时生效）
    if game_state.police_engine:
        # 进入他人家
        if "进入他人家" in game_state.crime_types:
            if destination.startswith("home_") and destination != f"home_{player.player_id}":
                game_state.police_engine.check_and_record_crime(player.player_id, "进入他人家")
        # 进入军事基地
        if "进入军事基地" in game_state.crime_types:
            if destination == "军事基地":
                game_state.police_engine.check_and_record_crime(player.player_id, "进入军事基地")


    old_name = get_location_display_name(old_location, game_state)
    new_name = get_location_display_name(destination, game_state)
    game_state.log_event("move", player=player.player_id,
                         from_loc=old_location, to_loc=destination)
    return f"🚶 {player.name} 从「{old_name}」移动到「{new_name}」。"
