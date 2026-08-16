"""
地点：军事基地
进入需要通行证（花1回合办理 或 消耗所有凭证强买）。
装备免费。
导弹三步流程全部需要在基地执行。
"""

from utils.attribute import Attribute
from models.equipment import Weapon, ArmorPiece, ArmorLayer, WeaponRange, make_item


MILITARY_MENU = {
    "办理通行证":   "花1回合获得通行证（免费）",
    "AT力场":       "外层科技护盾1（可重新展开）",
    "电磁步枪":     "电流武器，蓄力1回合，对已发现你的目标0.5科技伤害+眩晕",
    "高斯步枪":     "近战科技伤害1（蓄力后伤害2）",
    "导弹控制权":   "取得导弹控制权（导弹三步流程第1步）",
    "雷达":         "花1回合改造，使自己或导弹获得探测能力",
    "隐形涂层":     "使用后进入隐身",
    # 星野战术道具（需习得战术动作）
    "破片手雷":     "⚔️ 战术道具：0.5伤害+脆弱debuff（需习得战术动作）",
    "震撼弹":       "⚔️ 战术道具：AOE震荡（含警察）（需习得战术动作）",
    "闪光弹":       "⚔️ 战术道具：致盲（需习得战术动作）",
    "烟雾弹":       "⚔️ 战术道具：区域烟雾（需习得战术动作）",
    "燃烧瓶":       "⚔️ 战术道具：2层灼烧（需习得战术动作）",
}

# 星野战术道具（需习得战术动作）
HOSHINO_TACTICAL = {"破片手雷", "震撼弹", "闪光弹", "烟雾弹", "燃烧瓶"}

# 需要通行证才能交互的项目（办理通行证本身不需要）
NEED_PASS = {"AT力场", "电磁步枪", "高斯步枪", "导弹控制权", "雷达", "隐形涂层"} | HOSHINO_TACTICAL


# m4 退役：导弹生态位由弓继承，攻城/反龟缩职责移交钩索（v2.0 §2.2 处决名单）
_M4_RETIRED = {"导弹控制权"}


def _m9_active(game_state) -> bool:
    try:
        from engine.m9.gate import m9_enabled
        return m9_enabled(game_state)
    except Exception:
        return False


def get_menu():
    menu = dict(MILITARY_MENU)
    from engine.economy import m4_enabled
    if m4_enabled():
        for r in _M4_RETIRED:
            menu.pop(r, None)
        from engine.bow_modules import menu_entries
        menu.update(menu_entries("军事基地"))
        menu["钩索"] = "神器·钩索（通行证+信用点，全图唯一）：拉人/拉己位移"
    return menu


def can_interact(player, item_name, game_state=None):
    from engine.economy import m4_enabled
    _m4 = m4_enabled()

    # m4 弓模块（穿甲/冲击，军基走通行证权限，免信用点）
    if _m4:
        from engine.bow_modules import is_module_item, check_purchase, base_name
        if is_module_item(item_name):
            if not player.has_military_pass:
                return False, "军事基地的弓模块需要通行证"
            return check_purchase(player, base_name(item_name), game_state)
        # 钩索（神器×1）
        if item_name == "钩索":
            if not player.has_military_pass:
                return False, "钩索需要通行证"
            if getattr(game_state, 'hook_taken', False):
                return False, "钩索是全图唯一神器，已被取走"
            if any(getattr(i, 'name', '') == "钩索" for i in getattr(player, 'items', [])):
                return False, "你已经持有钩索"
            from engine.economy import can_afford
            return can_afford(player, "钩索")

    if item_name not in MILITARY_MENU:
        return False, f"军事基地没有「{item_name}」"

    # m4 退役武器：断新增（已持有者不剥夺）
    if _m4 and item_name in _M4_RETIRED:
        return False, f"「{item_name}」已退役（远程交给弓，攻城交给钩索）"

    # 办理通行证不需要已有通行证
    if item_name == "办理通行证":
        if player.has_military_pass:
            return False, "你已经有通行证了"
        if _m9_active(game_state):
            from engine.economy import can_afford
            return can_afford(player, "办理通行证")
        return True, ""

    # 其他项目需要通行证
    if item_name in NEED_PASS and not player.has_military_pass:
        return False, "你需要先办理通行证或强买通行证才能使用军事基地设施"

    # 检查重复武器
    if item_name == "电磁步枪":
        if player.has_weapon("电磁步枪"):
            return False, "你已经有电磁步枪了"

    if item_name == "高斯步枪":
        if player.has_weapon("高斯步枪"):
            return False, "你已经有高斯步枪了"

    if item_name == "雷达":
        if getattr(player, 'has_detection', False):
            return False, "你已经有探测能力了"

    if item_name == "隐形涂层":
        if getattr(player, 'is_invisible', False):
            return False, "你已经处于隐身状态了"

    # 导弹控制权：检查是否已有控制权标记
    if item_name == "导弹控制权":
        if game_state and game_state.markers.has(player.player_id, "MISSILE_CTRL"):
            return False, "你已经有导弹控制权了"

    if item_name == "AT力场":
        from models.equipment import ArmorPiece, ArmorLayer
        from utils.attribute import Attribute
        test_armor = ArmorPiece("AT力场", Attribute.TECH, ArmorLayer.OUTER, 1.0, can_regen=True)
        can_equip, equip_reason = player.armor.check_can_equip(test_armor)
        if not can_equip:
            return False, f"无法装备AT力场：{equip_reason}"

    # 星野战术道具：需习得战术动作，最多持有2样
    if item_name in HOSHINO_TACTICAL:
        if not (player.talent and hasattr(player.talent, 'tactical_unlocked')
                and player.talent.tactical_unlocked):
            return False, "你需要先习得战术动作才能获取战术道具"
        if hasattr(player.talent, 'tactical_items') and len(player.talent.tactical_items) >= 2:
            return False, "你最多同时持有2样战术道具"
        if _m9_active(game_state):
            from engine.economy import can_afford
            return can_afford(player, item_name)
        return True, ""

    # M9 机制刀（用户批准）：军基通行证/装备不再免费，走信用点价格表。
    if _m9_active(game_state):
        from engine.economy import can_afford
        return can_afford(player, item_name)

    return True, ""


def do_interact(player, item_name, game_state=None):
    """执行军事基地交互"""
    from engine.economy import m4_enabled
    # m4 弓模块（穿甲/冲击）：委托 do_purchase
    if m4_enabled():
        from engine.bow_modules import is_module_item, do_purchase, base_name
        if is_module_item(item_name):
            return do_purchase(player, base_name(item_name), game_state)
        if item_name == "钩索":
            from engine.economy import charge
            from models.equipment import Item
            charge(player, "钩索")
            player.add_item(Item("钩索", "tool"))
            game_state.hook_taken = True
            return f"🪝 {player.name} 取得了全图唯一神器·钩索！"

    # M9：军基通行证/装备按 economy.sinks 扣信用点（can_interact 已预检）
    if _m9_active(game_state) and item_name in MILITARY_MENU:
        from engine.economy import charge
        charge(player, item_name)

    if item_name == "办理通行证":
        player.has_military_pass = True
        if game_state:
            game_state.log_event("military_pass", player=player.player_id, method="free")
        return f"🪪 {player.name} 办理了军事基地通行证。"

    elif item_name == "AT力场":
        armor = ArmorPiece("AT力场", Attribute.TECH, ArmorLayer.OUTER, 1.0, can_regen=True)
        player.learned_spells.add("AT力场")  # 记录可以重新展开
        success, reason = player.add_armor(armor)
        if success:
            return f"🛡️ {player.name} 获得了AT力场！（外层科技护盾1）"
        else:
            return f"❌ 无法装备AT力场：{reason}"

    elif item_name == "电磁步枪":
        w = Weapon("电磁步枪", Attribute.TECH, 0.5, WeaponRange.AREA,
                   requires_charge=True, is_electric=True,
                   special_tags=["stun_on_hit", "hits_all_detected"])
        player.add_weapon(w)
        return f"⚡ {player.name} 获得了电磁步枪！（需蓄力1回合，对已发现你的目标0.5科技伤害+眩晕）"

    elif item_name == "高斯步枪":
        w = Weapon("高斯步枪", Attribute.TECH, 1.0, WeaponRange.MELEE,
                requires_charge=True, charged_damage=2.0,
                charge_mandatory=False)
        player.add_weapon(w)
        return f"🔫 {player.name} 获得了高斯步枪！（科技伤害1，蓄力后伤害2）"

    elif item_name == "导弹控制权":
        # 导弹三步流程第1步
        if game_state:
            game_state.markers.add(player.player_id, "MISSILE_CTRL")
        w_exists = player.has_weapon("导弹")
        if not w_exists:
            w = Weapon("导弹", Attribute.TECH, 1.0, WeaponRange.RANGED,
                       special_tags=["missile"])
            player.add_weapon(w)
        return f"🚀 {player.name} 取得了导弹控制权！（第1步完成，接下来需要锁定目标→发射）"

    elif item_name == "雷达":
        player.add_item(make_item("雷达"))
        player.has_detection = True
        player.grant_visibility_item("雷达")
        return f"📡 {player.name} 获得了雷达并改造完成！获得探测能力。"

    elif item_name == "隐形涂层":
        player.is_invisible = True
        player.grant_visibility_item("隐形涂层")
        if game_state:
            game_state.markers.on_player_go_invisible(
                player.player_id, list(game_state.players.values()))
        return f"🫥 {player.name} 使用了隐形涂层，进入隐身状态！"

    # 星野战术道具
    elif item_name in HOSHINO_TACTICAL:
        player.talent.tactical_items.append(item_name)
        count = len(player.talent.tactical_items)
        return f"⚔️ {player.name} 获得了战术道具「{item_name}」！（当前持有 {count}/2）"

    return "❌ 未知项目"


def try_force_entry(player, game_state):
    """
    强买通行证：消耗所有凭证立刻获得通行证。
    不消耗行动回合（在移动到军事基地时触发）。
    返回 (成功bool, 消息str)
    """
    if player.has_military_pass:
        return True, "已有通行证"
    from engine.economy import m4_enabled, pay_all
    if m4_enabled():
        # m4 财产税：强买 = 全部信用点（下限 force_pass_min_cost）
        ok, paid = pay_all(player, "force_pass_min_cost")
        if not ok:
            return False, "信用点不足，无法强买通行证。请先花1回合办理通行证。"
        player.has_military_pass = True
        if game_state:
            game_state.log_event("military_pass", player=player.player_id,
                                 method="force_buy", credits_spent=paid)
        return True, f"🪪 {player.name} 支付了全部信用点（{paid}），强买通行证！"
    if player.vouchers < 1:
        return False, "你没有购买凭证，无法强买通行证。请先花1回合办理通行证。"
    old = player.vouchers
    player.clear_all_vouchers()
    player.has_military_pass = True
    if game_state:
        game_state.log_event("military_pass", player=player.player_id,
                             method="force_buy", vouchers_spent=old)
    return True, f"🪪 {player.name} 消耗了所有购买凭证（{old}张），强买通行证！"
