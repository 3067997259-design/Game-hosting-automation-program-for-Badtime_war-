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

# m4_gear 追加菜单（信用点经济专属服务，get_menu 动态合并）
M4_SHOP_MENU = {
    "修理陶瓷护甲": "修理服务：陶瓷护甲耐久 +6（1 信用点）",
    "箭矢补给":     "补充 2 支箭（1 信用点，上限 6）",
}

# 不需要凭证的项目
FREE_ITEMS = {"打工"}


def get_menu():
    menu = dict(SHOP_MENU)
    from engine.economy import m4_enabled
    if m4_enabled():
        menu.update(M4_SHOP_MENU)
    return menu


def _is_virus_active(game_state):
    return hasattr(game_state, 'virus') and hasattr(game_state.virus, 'is_active') and game_state.virus.is_active


def can_interact(player, item_name, game_state=None):
    """
    检查能否交互。
    注意：这个函数签名比home多一个game_state参数，
    interact.py 在调用时会传入。
    """
    from engine.economy import m4_enabled, can_afford
    _m4 = m4_enabled()

    valid_items = set(SHOP_MENU) | (set(M4_SHOP_MENU) if _m4 else set())
    if item_name not in valid_items:
        return False, f"商店没有「{item_name}」"

    # m4 专属服务的前置
    if item_name == "修理陶瓷护甲":
        ceramic = next((a for a in getattr(player.armor, 'outer', []) or []
                        if a.name == "陶瓷护甲"), None)
        if ceramic is None:
            return False, "你没有装备陶瓷护甲"
        if ceramic.durability >= ceramic.max_durability:
            return False, f"陶瓷护甲耐久已满（{ceramic.durability}/{ceramic.max_durability}）"
    if item_name == "箭矢补给":
        from engine.balance import get as _bget
        max_arrows = _bget("bow", "max_arrows", default=6)
        if getattr(player, 'arrows', 0) >= max_arrows:
            return False, f"箭袋已满（{player.arrows}/{max_arrows}）"

    # 打工：v1 已有凭证时不允许；m4 信用点可累积无此限制
    if item_name == "打工" and not _m4 and player.vouchers >= 1:
        return False, "你已经有购买凭证了，不需要再打工。"

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
        has_unsharpened = any(w.name == "小刀" and w.base_damage < 2 for w in player.weapons if w)
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

    # 病毒期间免费（跳过凭证/付费检查，但重复物品检查已在上方完成）
    if game_state and _is_virus_active(game_state):
        return True, ""

    # m4：信用点真扣费（资格开关退役）
    if _m4:
        return can_afford(player, item_name)

    # v1：需要凭证（资格开关，不消耗）
    if player.vouchers < 1:
        return False, "你没有购买凭证（山姆会员）！请先获取凭证。"

    return True, ""


def do_interact(player, item_name, game_state=None):
    """执行商店交互"""
    from engine.economy import m4_enabled, charge, work_income

    # m4：付费项先扣费（打工不在 sinks 表中 price=0 不扣；病毒期间保持免费语义）
    # 注意必须在 if/elif 链之前——插在链中会断链
    if (m4_enabled() and item_name not in FREE_ITEMS
            and not (game_state and _is_virus_active(game_state))):
        charge(player, item_name)

    if item_name == "打工":
        if m4_enabled():
            income = work_income()
            player.credits += income
            return f"{player.name} 在商店打工，获得 {income} 信用点。当前：{player.credits}"
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
        player.grant_visibility_item("隐身衣")
        if game_state:
            game_state.markers.on_player_go_invisible(
                player.player_id, list(game_state.players.values()))
        return f"{player.name} 穿上了隐身衣，进入隐身状态！🫥"

    elif item_name == "热成像仪":
        player.add_item(make_item("热成像仪"))
        player.has_detection = True
        player.grant_visibility_item("热成像仪")
        return f"{player.name} 获得了热成像仪，可以发现隐身目标！🔍"

    elif item_name == "陶瓷护甲":
        armor = make_armor("陶瓷护甲")
        success, reason = player.add_armor(armor)
        if success:
            return f"{player.name} 装备了陶瓷护甲（外层普通护盾1，免疫电流）。"
        else:
            return f"{player.name} 无法装备陶瓷护甲：{reason}"

    elif item_name == "修理陶瓷护甲":
        # m4 经济战核心回路：防御的经常性开支（费用已在链前扣除）
        ceramic = next((a for a in getattr(player.armor, 'outer', []) or []
                        if a.name == "陶瓷护甲"), None)
        if ceramic is None:
            return "❌ 你没有陶瓷护甲"
        amount = 6  # 与魔法护盾/AT 重吟唱增量一致（[待风洞]）
        ceramic.durability = min(ceramic.max_durability, ceramic.durability + amount)
        return (f"🔧 {player.name} 修理了陶瓷护甲，耐久 +{amount} "
                f"→ {ceramic.durability}/{ceramic.max_durability}")

    elif item_name == "箭矢补给":
        from engine.balance import get as _bget
        amount = _bget("economy", "arrow_supply_amount", default=2)
        max_arrows = _bget("bow", "max_arrows", default=6)
        player.arrows = min(max_arrows, getattr(player, 'arrows', 0) + amount)
        return f"🏹 {player.name} 补充了箭矢 → {player.arrows}/{max_arrows}"

    elif item_name == "防毒面具":
        player.add_item(make_item("防毒面具"))
        return f"{player.name} 获得了防毒面具，免疫病毒！😷"

    return "❌ 未知项目"
