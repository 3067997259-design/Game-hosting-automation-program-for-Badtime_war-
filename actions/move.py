"""行动类型：移动"""

# Phase 1 可用地点列表（后续Phase会扩展）
ALL_LOCATIONS = [
    "商店", "魔法所", "医院", "军事基地", "警察局",
    # 家是动态的：home_玩家id
]


def _talent_crime_hook(player, crime_type):
    """非攻击类犯罪的天赋钩子（犯罪再动等）"""
    if player.talent and hasattr(player.talent, 'on_crime_check'):
        crime_result = player.talent.on_crime_check(player.player_id, crime_type)
        if crime_result and crime_result.get("extra_turn"):
            msg = crime_result.get("message", "")
            if msg:
                from cli import display
                display.show_info(msg)
            player.crime_extra_turn = True


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
    # 星野架盾移动阻碍：正面敌人离开需多花1回合
    # 半进入状态处理：再次 move 同地点 → 突破（正面→背面 + engage 断裂）
    if (destination == old_location
            and getattr(player, '_shield_half_entered', False)
            and getattr(player, '_shield_half_entered_location', None) == destination):
        blocker_id = getattr(player, '_shield_half_entered_blocker', None)
        blocker = game_state.get_player(blocker_id) if blocker_id else None
        # 清除半进入标记
        del player._shield_half_entered
        if hasattr(player, '_shield_half_entered_location'):
            del player._shield_half_entered_location
        if hasattr(player, '_shield_half_entered_blocker'):
            del player._shield_half_entered_blocker
        # 正面→背面
        if blocker and blocker.talent and hasattr(blocker.talent, 'front_players'):
            blocker.talent.front_players.discard(player.player_id)
            blocker.talent.back_players.add(player.player_id)
        # 断裂 engage_with
        if blocker_id:
            game_state.markers.remove_relation(player.player_id, "ENGAGED_WITH", blocker_id)
            game_state.markers.remove_relation(blocker_id, "ENGAGED_WITH", player.player_id)
        blocker_name = blocker.name if blocker else "星野"
        from cli import display
        display.show_info(f"🏃 {player.name} 突破了 {blocker_name} 的封锁，进入背面！")
        old_name = get_location_display_name(old_location, game_state)
        game_state.log_event("move", player=player.player_id,
                             from_loc=old_location, to_loc=destination)
        return f"🏃 {player.name} 完全进入「{old_name}」，脱离正面范围。"

    if destination != old_location:
        # 清理过期的架盾延迟标记（blocker已死亡/不在同地点/不再架盾）
        stale_keys = []
        # 清理过期的架盾移动延迟标记
        for attr_name in list(vars(player)):
            if attr_name.startswith('_shield_move_delayed_'):
                blocker_id = attr_name[len('_shield_move_delayed_'):]
                bp = game_state.get_player(blocker_id)
                if not (bp and bp.is_alive()
                        and bp.talent and hasattr(bp.talent, 'shield_mode')
                        and bp.talent.shield_mode == "架盾"
                        and bp.location == old_location):
                    stale_keys.append(attr_name)
        for key in stale_keys:
            delattr(player, key)

        for pid in game_state.player_order:
            p = game_state.get_player(pid)
            if (p and p.is_alive() and p.player_id != player.player_id
                    and p.talent and hasattr(p.talent, 'shield_mode')
                    and p.talent.shield_mode == "架盾"
                    and p.location == old_location
                    and hasattr(p.talent, 'is_front')
                    and p.talent.is_front(player.player_id)):
                # 检查豁免条件
                is_exempt = False
                # 半进入状态豁免：已经花过回合进入，不再额外阻碍离开
                if getattr(player, '_shield_half_entered', False):
                    is_exempt = True
                # 超新星过载豁免
                if (player.talent and hasattr(player.talent, 'has_supernova')
                        and player.talent.has_supernova):
                    is_exempt = True
                # 插入式笑话豁免（神代6）
                if getattr(player, '_in_cutaway_joke', False):
                    is_exempt = True
                # 原初天赋4强制移动豁免（六爻放逐）
                if getattr(player, '_hexagram_forced_move', False):
                    is_exempt = True
                # 神代天赋5强制位移豁免
                if getattr(player, '_ripple_forced_move', False):
                    is_exempt = True
                # 最后一曲吸引豁免
                if getattr(player, '_hologram_pull', False):
                    is_exempt = True

                if not is_exempt:
                    delay_key = f'_shield_move_delayed_{p.player_id}'
                    if not getattr(player, delay_key, False):
                        setattr(player, delay_key, True)
                        from cli import display
                        display.show_info(
                            f"🛡️ {p.name} 的架盾阻碍了 {player.name} 的移动！"
                            f"（{player.name} 在 {p.name} 的正面）"
                            f"需要多花费1回合才能离开。")
                        game_state.log_event("move_blocked", player=player.player_id,
                                        blocker=p.player_id, reason="架盾移动阻碍")
                        return f"🛡️ {player.name} 被 {p.name} 的架盾阻碍，本回合用于挣脱。"
                    else:
                        # 已经花过一回合，清除标记，允许移动
                        delattr(player, delay_key)
                        break
    player.location = destination

    # 半进入玩家移动到其他地点：清除半进入标记，从正面移除
    if (destination != old_location
            and getattr(player, '_shield_half_entered', False)):
        blocker_id = getattr(player, '_shield_half_entered_blocker', None)
        blocker = game_state.get_player(blocker_id) if blocker_id else None
        if blocker and blocker.talent and hasattr(blocker.talent, 'front_players'):
            blocker.talent.front_players.discard(player.player_id)
            # 不归入背面，因为玩家已经离开了该地点
        if blocker_id:
            game_state.markers.remove_relation(player.player_id, "ENGAGED_WITH", blocker_id)
            game_state.markers.remove_relation(blocker_id, "ENGAGED_WITH", player.player_id)
        del player._shield_half_entered
        if hasattr(player, '_shield_half_entered_location'):
            del player._shield_half_entered_location
        if hasattr(player, '_shield_half_entered_blocker'):
            del player._shield_half_entered_blocker

    # 触发标记联动（原地移动不清除锁定/面对面，如超新星过载原地触发）
    if destination != old_location:
        game_state.markers.on_player_move(player.player_id)
    # 星野架盾：有人进入同地点 → 通知 FacingMixin
    if destination != old_location:
        for pid in game_state.player_order:
            p = game_state.get_player(pid)
            if (p and p.is_alive() and p.player_id != player.player_id
                    and p.talent and hasattr(p.talent, '_on_player_enter_location')):
                p.talent._on_player_enter_location(player.player_id, destination)
    # 星野烟雾弹：进入烟雾区域
    if hasattr(game_state, '_hoshino_smoke_zones'):
        if destination in game_state._hoshino_smoke_zones:
            expire_round = game_state._hoshino_smoke_zones[destination]
            if game_state.current_round <= expire_round:
                # 星野进入烟雾获得隐身
                for pid2 in game_state.player_order:
                    p2 = game_state.get_player(pid2)
                    if (p2 and p2.talent and hasattr(p2.talent, 'name')
                            and p2.talent.name == "大叔我啊，剪短发了"
                            and p2.player_id == player.player_id):
                        player.is_stealthed = True
                        game_state.markers.add(player.player_id, "STEALTH")
                        from cli import display
                        display.show_info(f"💨 {player.name} 进入烟雾，获得隐身！")
                # 其他玩家进入烟雾区域：解除 find/lock
                # 注意：on_player_move 已在上方第86行调用过，此处为幂等操作，保留以明确烟雾语义
                if player.talent is None or not hasattr(player.talent, 'name') or player.talent.name != "大叔我啊，剪短发了":
                    game_state.markers.on_player_move(player.player_id)  # 清除 find/lock

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
                # 天赋犯罪检查（剪刀手一突等）
                _talent_crime_hook(player, "进入他人家")
                game_state.police_engine.check_and_record_crime(player.player_id, "进入他人家")
        # 进入军事基地
        if "进入军事基地" in game_state.crime_types:
            if destination == "军事基地":
                # 天赋犯罪检查（剪刀手一突等）
                _talent_crime_hook(player, "进入军事基地")
                game_state.police_engine.check_and_record_crime(player.player_id, "进入军事基地")

    # Terror 进入时：该地区警察单位逃离到队长所在地点
    if (player.talent and hasattr(player.talent, 'is_terror')
            and player.talent.is_terror and game_state.police_engine):
        pe = game_state.police_engine
        police = game_state.police
        fled_units = []
        for unit in police.active_units():
            if unit.is_alive() and unit.location == destination:
                # 优先移动到队长所在地点
                if police.has_captain():
                    captain = game_state.get_player(police.captain_id)
                    if captain and captain.is_alive():
                        unit.location = captain.location
                        fled_units.append(f"{unit.unit_id}→{captain.location}")
                        continue
                # 无队长：强制要求队长提供location（简化：移动到警察局）
                unit.location = "警察局"
                fled_units.append(f"{unit.unit_id}→警察局")
        if fled_units:
            from cli import display
            display.show_info(f"🚔 Terror 到来！警察单位逃离：{', '.join(fled_units)}")


    old_name = get_location_display_name(old_location, game_state)
    new_name = get_location_display_name(destination, game_state)
    game_state.log_event("move", player=player.player_id,
                         from_loc=old_location, to_loc=destination)
    if (player.talent and hasattr(player.talent, 'has_supernova')
        and player.talent.has_supernova):
        # Check if there are any targets at destination before triggering
        targets_at_dest = [p for p in game_state.players_at_location(destination)
                        if p.player_id != player.player_id and p.is_alive()]
        has_police = False
        if game_state.police_engine and hasattr(game_state.police_engine, 'police'):
            for unit in game_state.police_engine.police.units:
                if unit.is_alive() and unit.location == destination:
                    has_police = True
                    break
        if targets_at_dest or has_police:
            player.talent.trigger_supernova(player, destination, game_state)
        # If no targets, preserve supernova for later (don't trigger)
    return f"🚶 {player.name} 从「{old_name}」移动到「{new_name}」。"



