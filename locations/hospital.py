"""
地点：医院
打工获取凭证。手术需凭证且消耗所有凭证。
释放病毒为特殊操作（Phase 3病毒系统激活后可用）。
防毒面具免费。
"""

from models.equipment import make_item
from utils.attribute import Attribute
from models.equipment import ArmorPiece, ArmorLayer


HOSPITAL_MENU = {
    "打工":         "获得1张购买凭证",
    "晶化皮肤手术": "内层科技护甲1（需凭证，消耗所有凭证）",
    "额外心脏手术": "内层普通护甲1（需凭证，消耗所有凭证）",
    "不老泉手术":   "内层魔法护甲1（需凭证，消耗所有凭证）",
    "防毒面具":     "免疫病毒（本来是免费的，为了针对毒警体系现在不免费了）",
    # "释放病毒" → Phase 3 在 special_op 中处理，不在交互菜单
}

# 不需要凭证的项目
FREE_ITEMS = {"打工"}

# 手术项目
SURGERY_ITEMS = {"晶化皮肤手术", "额外心脏手术", "不老泉手术"}


def get_menu():
    return dict(HOSPITAL_MENU)


def can_interact(player, item_name, game_state=None):
    if item_name not in HOSPITAL_MENU:
        return False, f"医院没有「{item_name}」"

    if item_name in FREE_ITEMS:
        return True, ""

    # 手术需要凭证
    if item_name in SURGERY_ITEMS:
        if player.vouchers < 1:
            return False, "手术需要至少1张购买凭证！（手术会消耗你所有凭证）"
        
    # 防毒面具：需凭证但不消耗  
    if item_name == "防毒面具":  
        if player.vouchers < 1:  
            return False, "防毒面具需要购买凭证（不消耗凭证）。"  
        return True, ""

    return True, ""




def do_interact(player, item_name, game_state=None):
    """执行医院交互"""

    if item_name == "打工":
        player.vouchers += 1
        return f"{player.name} 在医院打工，获得1张购买凭证。当前：{player.vouchers}张"

    elif item_name == "防毒面具":
        player.add_item(make_item("防毒面具"))
        return f"{player.name} 获得了防毒面具，免疫病毒！😷"

    elif item_name == "晶化皮肤手术":
        return _do_surgery(player, "晶化皮肤",
                           ArmorPiece("晶化皮肤", Attribute.TECH, ArmorLayer.INNER, 1.0),
                           game_state)

    elif item_name == "额外心脏手术":
        return _do_surgery(player, "额外心脏",
                           ArmorPiece("额外心脏", Attribute.ORDINARY, ArmorLayer.INNER, 1.0),
                           game_state)

    elif item_name == "不老泉手术":
        return _do_surgery(player, "不老泉",
                           ArmorPiece("不老泉", Attribute.MAGIC, ArmorLayer.INNER, 1.0),
                           game_state)

    return "❌ 未知项目"


def _do_surgery(player, surgery_name, armor_piece, game_state):
    """执行手术：装备内层护甲，消耗所有凭证"""
    old_vouchers = player.vouchers
    player.clear_all_vouchers()

    success, reason = player.add_armor(armor_piece)
    if success:
        if game_state:
            game_state.log_event("surgery", player=player.player_id,
                                 surgery=surgery_name, vouchers_spent=old_vouchers)
        return (f"🏥 {player.name} 完成了{surgery_name}手术！"
                f"（内层{armor_piece.attribute.value}护甲1）"
                f"\n   消耗了所有购买凭证（{old_vouchers}张→0张）")
    else:
        # 手术失败但凭证已消耗（规则如此——手术会消耗所有凭证）
        return (f"❌ {player.name} 的{surgery_name}手术失败：{reason}"
                f"\n   但购买凭证已被消耗（{old_vouchers}张→0张）")
