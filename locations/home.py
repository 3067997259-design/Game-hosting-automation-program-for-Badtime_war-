"""
地点：家（每位玩家独有）
可交互项目：拿购买凭证 / 拿小刀 / 拿盾牌
全部1回合完成，无限量。
"""

from models.equipment import make_weapon, make_armor


# 家的交互菜单：名称 -> 描述
HOME_MENU = {
    "凭证": "获得1张购买凭证（山姆会员）",
    "小刀":   "获得1把小刀（近战，普通伤害1）",
    "盾牌":   "获得1面盾牌（外层普通护盾1，优先消耗）",
}


def get_menu():
    """返回家的交互菜单字典 {项目名: 描述}"""
    return dict(HOME_MENU)


def can_interact(player, item_name, game_state=None):
    if item_name not in HOME_MENU:
        return False, f"家中没有「{item_name}」这个项目"

    # 盾牌：检查是否已有同名外层护甲
    if item_name == "盾牌":
        from models.equipment import make_armor
        test_armor = make_armor("盾牌")
        if test_armor:
            can_equip, equip_reason = player.armor.check_can_equip(test_armor)
            if not can_equip:
                return False, f"无法装备盾牌：{equip_reason}"

    # 小刀：检查是否已有小刀
    if item_name == "小刀":
        if player.has_weapon("小刀"):
            return False, "你已经有小刀了"

    return True, ""


def do_interact(player, item_name, game_state=None):
    """
    执行家中交互。
    返回结果描述字符串。
    """
    if item_name == "凭证":
        player.vouchers += 1
        return f"{player.name} 获得了1张购买凭证。当前持有：{player.vouchers}张"

    elif item_name == "小刀":
        weapon = make_weapon("小刀")
        player.add_weapon(weapon)
        return f"{player.name} 获得了一把小刀。"

    elif item_name == "盾牌":
        armor = make_armor("盾牌")
        success, reason = player.add_armor(armor)
        if success:
            return f"{player.name} 获得了一面盾牌。"
        else:
            return f"❌ {player.name} 无法装备盾牌：{reason}"

    return "❌ 未知项目"
