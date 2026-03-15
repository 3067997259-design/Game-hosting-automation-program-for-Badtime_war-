"""行动注册表（Phase 3 完整版）：新增警察相关行动"""

from actions import move, interact, special_op
from models.equipment import WeaponRange


def get_available_actions(player, game_state):
    actions = []

    if not player.is_awake:
        actions.append({
            "name": "起床", "usage": "wake",
            "description": "起床，出现在自己家中",
        })
        return actions

    # 1. 移动
    actions.append({
        "name": "移动", "usage": "move <地点名>",
        "description": "移动到任意地点",
    })

    # 2. 交互
    menu = interact.get_menu_for_location(player, game_state)
    if menu:
        items_list = " / ".join(menu.keys())
        actions.append({
            "name": "交互", "usage": "interact <项目名>",
            "description": f"可选：{items_list}",
        })

    # 3. 锁定
    lockable = _get_lockable_targets(player, game_state)
    if lockable:
        names = ", ".join(lockable.values())
        actions.append({
            "name": "锁定", "usage": "lock <玩家名>",
            "description": f"可锁定：{names}",
        })

    # 4. 找到
    findable = _get_findable_targets(player, game_state)
    if findable:
        names = ", ".join(findable.values())
        actions.append({
            "name": "找到", "usage": "find <玩家名>",
            "description": f"可找到：{names}",
        })

    # 5. 攻击
    attackable = _get_attackable_info(player, game_state)
    if attackable:
        actions.append({
            "name": "攻击", "usage": "attack <目标> <武器> [层 属性]",
            "description": attackable,
        })

    # 6. 特殊操作
    specials = special_op.get_available_specials(player, game_state)
    if specials:
        sp_list = " / ".join(s["name"] for s in specials)
        actions.append({
            "name": "特殊操作", "usage": "special <操作名>",
            "description": f"可用：{sp_list}",
        })

    # 7. 警察相关行动
    police_actions = _get_police_actions(player, game_state)
    actions.extend(police_actions)

    # 放弃
    actions.append({
        "name": "放弃", "usage": "forfeit",
        "description": "放弃本次行动",
    })

    return actions


def _get_police_actions(player, game_state):
    """获取当前可用的警察相关行动"""
    actions = []
    pe = game_state.police_engine
    if not pe:
        return actions

    police = game_state.police

    # 举报（在警察局、无犯罪记录、无队长、有违法者）
    if (not police.has_captain()
            and not police.is_criminal(player.player_id)
            and police.report_phase == "idle"):
        criminals = [p for p in game_state.alive_players()
                     if police.is_criminal(p.player_id)
                     and p.player_id != player.player_id]
        if criminals and player.location == "警察局":
            names = ", ".join(p.name for p in criminals)
            actions.append({
                "name": "举报", "usage": "report <玩家名>",
                "description": f"可举报：{names}",
            })

    # 集结（举报者、已举报未集结）
    if (police.report_phase == "reported"
            and police.reporter_id == player.player_id):
        actions.append({
            "name": "集结", "usage": "assemble",
            "description": "集结警察出动",
        })

    # 追踪指引（举报者、有警队在追踪）
    if police.reporter_id == player.player_id:
        tracking = any(unit.is_tracking for unit in police.units if unit.is_alive())
        if tracking:
            actions.append({
                "name": "追踪指引", "usage": "track",
                "description": "指引警察追踪目标（立刻到达）",
            })

    # 加入警察
    if (not player.is_police
            and not police.is_criminal(player.player_id)
            and player.location == "警察局"):
        actions.append({
            "name": "加入警察", "usage": "recruit",
            "description": "加入警队，三选二奖励",
        })

    # 竞选队长
    if (player.is_police
            and not police.has_captain()
            and player.location == "警察局"):
        progress = player.progress.get("captain_election", 0)
        actions.append({
            "name": "竞选队长", "usage": "election",
            "description": f"竞选进度：{progress}/3",
        })

    # 队长专属
    if player.is_captain:
        actions.append({
            "name": "指定目标", "usage": "designate <玩家名>",
            "description": "指定警察执法目标",
        })


        if player.location == "警察局":
            actions.append({
                "name": "研究性学习", "usage": "study",
                "description": f"威信+1（当前：{police.authority}）",
            })

    return actions


def _get_lockable_targets(player, game_state):
    targets = {}
    for p in game_state.alive_players():
        if p.player_id == player.player_id:
            continue
        if not p.is_on_map():
            continue
        visible = game_state.markers.is_visible_to(
            p.player_id, player.player_id, player.has_detection)
        if visible:
            already = game_state.markers.has_relation(
                p.player_id, "LOCKED_BY", player.player_id)
            if not already:
                targets[p.player_id] = p.name
    return targets


def _get_findable_targets(player, game_state):
    targets = {}
    for p in game_state.alive_players():
        if p.player_id == player.player_id:
            continue
        if p.location != player.location:
            continue
        visible = game_state.markers.is_visible_to(
            p.player_id, player.player_id, player.has_detection)
        if not visible:
            continue
        already = game_state.markers.has_relation(
            player.player_id, "ENGAGED_WITH", p.player_id)
        if not already:
            targets[p.player_id] = p.name
    return targets


def _get_attackable_info(player, game_state):
    parts = []
    engaged = game_state.markers.get_related(player.player_id, "ENGAGED_WITH")
    melee_weapons = [w for w in player.weapons if w.weapon_range == WeaponRange.MELEE]
    if engaged and melee_weapons:
        for eid in engaged:
            ep = game_state.get_player(eid)
            if ep and ep.is_alive() and ep.location == player.location:
                parts.append(f"近战->{ep.name}")
    ranged_weapons = [w for w in player.weapons if w.weapon_range == WeaponRange.RANGED]
    if ranged_weapons:
        for p in game_state.alive_players():
            if p.player_id == player.player_id:
                continue
            locked = game_state.markers.has_relation(
                p.player_id, "LOCKED_BY", player.player_id)
            if not locked:
                continue
            visible = game_state.markers.is_visible_to(
                p.player_id, player.player_id, player.has_detection)
            if visible:
                parts.append(f"远程->{p.name}")
    area_weapons = [w for w in player.weapons if w.weapon_range == WeaponRange.AREA]
    if area_weapons:
        others = [p for p in game_state.players_at_location(player.location)
                  if p.player_id != player.player_id]
        if others:
            parts.append(f"范围->同地点所有人")
    return " | ".join(parts)


def get_available_move_targets(player, game_state):
    all_locs = move.get_all_valid_locations(game_state)
    return [loc for loc in all_locs if loc != player.location]
