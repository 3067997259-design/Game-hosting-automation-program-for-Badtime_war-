"""行动类型：交互（Phase 2 完整版，支持所有地点）"""

import locations.home as home_loc
import locations.shop as shop_loc
import locations.magic_institute as magic_loc
import locations.hospital as hospital_loc
import locations.military_base as military_loc


def get_location_module(location_id):
    """根据地点ID获取对应的地点模块"""
    if location_id.startswith("home_"):
        return home_loc
    mapping = {
        "商店": shop_loc,
        "魔法所": magic_loc,
        "医院": hospital_loc,
        "军事基地": military_loc,
        # Phase 3+: "警察局": police_station_loc
    }
    return mapping.get(location_id)


def get_menu_for_location(player, game_state):
    """获取玩家当前位置的交互菜单"""
    loc_module = get_location_module(player.location)
    if loc_module is None:
        return {}
    return loc_module.get_menu()


def execute(player, item_name, game_state):
    """执行交互"""
    loc_module = get_location_module(player.location)
    if loc_module is None:
        return f"当前位置「{player.location}」没有可交互的项目。"

    # 不同地点的 can_interact 签名可能不同（有些接受game_state）
    try:
        can, reason = loc_module.can_interact(player, item_name)
    except TypeError:
        # home_loc 的 can_interact 只接受 player, item_name
        can, reason = loc_module.can_interact(player, item_name)

    if not can:
        return f"❌ 无法交互：{reason}"

    try:
        result = loc_module.do_interact(player, item_name)
    except TypeError:
        result = loc_module.do_interact(player, item_name)

    game_state.log_event("interact", player=player.player_id,
                         location=player.location, item=item_name)
    return result
