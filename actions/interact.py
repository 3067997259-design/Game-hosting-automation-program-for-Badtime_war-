"""行动类型：交互（Phase 3 完整版，支持警察局）"""

import locations.home as home_loc
import locations.shop as shop_loc
import locations.magic_institute as magic_loc
import locations.hospital as hospital_loc
import locations.military_base as military_loc
import locations.police_station as police_loc


def get_location_module(location_id):
    if location_id.startswith("home_"):
        return home_loc
    mapping = {
        "商店": shop_loc,
        "魔法所": magic_loc,
        "医院": hospital_loc,
        "军事基地": military_loc,
        "警察局": police_loc,
    }
    return mapping.get(location_id)


def get_menu_for_location(player, game_state):
    loc_module = get_location_module(player.location)
    if loc_module is None:
        return {}
    return loc_module.get_menu()


def execute(player, item_name, game_state):
    loc_module = get_location_module(player.location)
    if loc_module is None:
        return f"当前位置「{player.location}」没有可交互的项目。"

    try:
        can, reason = loc_module.can_interact(player, item_name, game_state)
    except TypeError:
        can, reason = loc_module.can_interact(player, item_name)

    if not can:
        return f"❌ 无法交互：{reason}"

    try:
        result = loc_module.do_interact(player, item_name, game_state)
    except TypeError:
        result = loc_module.do_interact(player, item_name)

    game_state.log_event("interact", player=player.player_id,
                         location=player.location, item=item_name)
    return result
