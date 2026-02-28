"""
地点：商店
需持有购买凭证才能购买（购买不消耗凭证）。
病毒期间商店免费（不需凭证）。
打工可获得凭证。
"""

from models.equipment import make_weapon, make_armor, make_item


SHOP_MENU = {
    "打工":     "获得1张购买凭证",
    "小刀":     "近战武器，普通伤害1（需凭证）",
    "磨刀石":   "购买后再花1回合磨刀，小刀伤害提升至2（需凭证）",
    "隐身衣":   "穿戴后进入隐身（需凭证）",
    "热成像仪": "获得探测能力，可发现隐身目标（需凭证）",
    "陶瓷护甲": "外层护甲，普通护盾1，免疫电流武器（需凭证）",
    "防毒面具": "免疫病毒（免费）",
}

# 不需要凭证的项目
FREE_ITEMS = {"打工", "防毒面具"}


def get_menu():
    return dict(SHOP_MENU)


def _is_virus_active(game_state):
    """检查病毒是否激活（Phase 3 会有virus系统，这里先做兼容）"""
    return hasattr(game_state, 'virus') and hasattr(game_state.virus, 'is_active') and game_state.virus.is_active


def can_interact(player, item_name, game_state=None):
    """
    检查能否交互。
    注意：这个函数签名比home多一个game_state参数，
    interact.py 在调用时会传入。
    """
    if item_name not in SHOP_MENU:
        return False, f"商店没有「{item_name}」"

    # 免费项目直接通过
    if item_name in FREE_ITEMS:
        return True, ""

    # 病毒期间免费
    if game_state and _is_virus_active(game_state):
        return True, ""

    # 需要凭证
    if player.vouchers < 1:
        return False, "你没有购买凭证（山姆会员）！请先获取凭证。"

    return True, ""


def do_interact(player, item_name, game_state=None):
    """执行商店交互"""

    if item_name == "打工":
        player.vouchers += 1
        return f"{player.name} 在商店打工，获得1张购买凭证。当前：{player.vouchers}张"

    elif item_name == "小刀":
        weapon = make_weapon("小刀")
        player.add_weapon(weapon)
        return f"{player.name} 购买了一把小刀。"

    elif item_name == "磨刀石":
        item = make_item("磨刀石")
        player.add_item(item)
        return f"{player.name} 购买了磨刀石。（再花1回合使用「特殊操作-磨刀」来升级小刀）"

    elif item_name == "隐身衣":
        player.items.append(make_item("隐身衣"))
        player.is_invisible = True
        if game_state:
            game_state.markers.add(player.player_id, "INVISIBLE")
        return f"{player.name} 穿上了隐身衣，进入隐身状态！🫥"

    elif item_name == "热成像仪":
        player.items.append(make_item("热成像仪"))
        player.has_detection = True
        return f"{player.name} 获得了热成像仪，可以发现隐身目标！🔍"

    elif item_name == "陶瓷护甲":
        armor = make_armor("陶瓷护甲")
        success, reason = player.add_armor(armor)
        if success:
            return f"{player.name} 装备了陶瓷护甲（外层普通护盾1，免疫电流）。"
        else:
            return f"{player.name} 无法装备陶瓷护甲：{reason}"

    elif item_name == "防毒面具":
        player.items.append(make_item("防毒面具"))
        return f"{player.name} 获得了防毒面具，免疫病毒！😷"

    return "未知项目"
