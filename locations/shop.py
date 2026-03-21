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
    "防毒面具": "免疫病毒（本来是免费的，为了针对某不知名RL的毒警体系改了）",
}

# 不需要凭证的项目
FREE_ITEMS = {"打工"}


def get_menu():
    return dict(SHOP_MENU)


def _is_virus_active(game_state):
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

    # 防毒面具：需凭证但不消耗（病毒期间仍免费）；检查是否已有
    if item_name == "防毒面具":
        items = getattr(player, 'items', [])
        if any(getattr(i, 'name', '') == "防毒面具" for i in items):
            return False, "你已经有防毒面具了"
        if game_state and _is_virus_active(game_state):
            return True, ""
        if player.vouchers < 1:
            return False, "防毒面具需要购买凭证（不消耗凭证）。"
        return True, ""

    # 检查重复护甲（在凭证/病毒检查之前，防止重复获取）
    if item_name == "陶瓷护甲":
        from models.equipment import make_armor
        test_armor = make_armor("陶瓷护甲")
        if test_armor:
            can_equip, equip_reason = player.armor.check_can_equip(test_armor)
            if not can_equip:
                return False, f"无法装备陶瓷护甲：{equip_reason}"

    # 检查重复物品（在凭证/病毒检查之前，防止重复获取）
    if item_name == "小刀":
        if player.has_weapon("小刀"):
            return False, "你已经有小刀了"

    if item_name == "磨刀石":
        items = getattr(player, 'items', [])
        # 已有磨刀石
        if any(getattr(i, 'name', '') == "磨刀石" for i in items):
            return False, "你已经有磨刀石了"
        # 小刀已经磨过了（没有未磨的小刀），磨刀石没意义
        has_unsharpened = any(w.name == "小刀" and w.base_damage < 2 for w in player.weapons)
        if not has_unsharpened:
            return False, "你没有需要磨的小刀"

    if item_name == "隐身衣":
        if getattr(player, 'is_invisible', False):
            return False, "你已经处于隐身状态了"
        items = getattr(player, 'items', [])
        if any(getattr(i, 'name', '') == "隐身衣" for i in items):
            return False, "你已经有隐身衣了"

    if item_name == "热成像仪":
        if getattr(player, 'has_detection', False):
            return False, "你已经有探测能力了"

    # 病毒期间免费（跳过凭证检查，但重复物品检查已在上方完成）
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
        player.add_item(make_item("隐身衣"))
        player.is_invisible = True
        if game_state:
            game_state.markers.on_player_go_invisible(
                player.player_id, list(game_state.players.values()))
        return f"{player.name} 穿上了隐身衣，进入隐身状态！🫥"

    elif item_name == "热成像仪":
        player.add_item(make_item("热成像仪"))
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
        player.add_item(make_item("防毒面具"))
        return f"{player.name} 获得了防毒面具，免疫病毒！😷"

    return "❌ 未知项目"
