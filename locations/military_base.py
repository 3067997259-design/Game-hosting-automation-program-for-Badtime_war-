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
}

# 需要通行证才能交互的项目（办理通行证本身不需要）
NEED_PASS = {"AT力场", "电磁步枪", "高斯步枪", "导弹控制权", "雷达", "隐形涂层"}


def get_menu():
    return dict(MILITARY_MENU)


def can_interact(player, item_name, game_state=None):
    if item_name not in MILITARY_MENU:
        return False, f"军事基地没有「{item_name}」"

    # 办理通行证不需要已有通行证
    if item_name == "办理通行证":
        if player.has_military_pass:
            return False, "你已经有通行证了"
        return True, ""

    # 其他项目需要通行证
    if item_name in NEED_PASS and not player.has_military_pass:
        return False, "你需要先办理通行证或强买通行证才能使用军事基地设施"
    
    if item_name == "AT力场":  
        from models.equipment import ArmorPiece, ArmorLayer  
        from utils.attribute import Attribute  
        test_armor = ArmorPiece("AT力场", Attribute.TECH, ArmorLayer.OUTER, 1.0, can_regen=True)  
        can_equip, equip_reason = player.armor.check_can_equip(test_armor)  
        if not can_equip:  
            return False, f"无法装备AT力场：{equip_reason}"
    
    return True, ""


def do_interact(player, item_name, game_state=None):
    """执行军事基地交互"""

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
        return f"📡 {player.name} 获得了雷达并改造完成！获得探测能力。"

    elif item_name == "隐形涂层":
        player.is_invisible = True
        if game_state:
            game_state.markers.on_player_go_invisible(
                player.player_id, list(game_state.players.values()))
        return f"🫥 {player.name} 使用了隐形涂层，进入隐身状态！"

    return "❌ 未知项目"


def try_force_entry(player, game_state):
    """
    强买通行证：消耗所有凭证立刻获得通行证。
    不消耗行动回合（在移动到军事基地时触发）。
    返回 (成功bool, 消息str)
    """
    if player.has_military_pass:
        return True, "已有通行证"
    if player.vouchers < 1:
        return False, "你没有购买凭证，无法强买通行证。请先花1回合办理通行证。"
    old = player.vouchers
    player.clear_all_vouchers()
    player.has_military_pass = True
    if game_state:
        game_state.log_event("military_pass", player=player.player_id,
                             method="force_buy", vouchers_spent=old)
    return True, f"🪪 {player.name} 消耗了所有购买凭证（{old}张），强买通行证！"
