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
        # 星野药物（需习得战术动作，不检查凭证）
    "EPO":          "💊 cost+1（需习得战术动作）",
    "海豚巧克力":   "🍫 回复1层光环（需习得战术动作）",
    "肾上腺素":     "💉 全局仅1次，回满cost和光环（需习得战术动作）",
}

# 不需要凭证的项目
FREE_ITEMS = {"打工"}

# 手术项目
SURGERY_ITEMS = {"晶化皮肤手术", "额外心脏手术", "不老泉手术"}

# 星野药物（需习得战术动作）
HOSHINO_MEDICINES = {"EPO", "海豚巧克力", "肾上腺素"}


def get_menu():
    menu = dict(HOSPITAL_MENU)
    from engine.economy import m4_enabled
    if m4_enabled():
        from engine.bow_modules import menu_entries
        menu.update(menu_entries("医院"))
        menu["治疗"] = "恢复 HP（信用点；黄昏阶段费用翻倍）"
    return menu


def _heal_cost(game_state):
    """治疗信用点费用（黄昏阶段 ×heal_cost_multiplier，v2.0 §3）。"""
    from engine.balance import get as _bget
    cost = _bget("hospital", "heal_cost", default=2)
    from engine import experiments
    if game_state is not None and experiments.is_enabled("m5_clock"):
        from engine import world_clock
        cost = int(cost * world_clock.active_value(
            game_state, "heal_cost_multiplier", default=1))
    return cost


def can_interact(player, item_name, game_state=None):
    from engine.economy import m4_enabled
    from engine.balance import get as _bget
    _m4 = m4_enabled()

    # m4 弓模块（无限）：委托 bow_modules
    if _m4:
        from engine.bow_modules import is_module_item, check_purchase, base_name
        if is_module_item(item_name):
            return check_purchase(player, base_name(item_name), game_state)

    # m4 治疗条目（§2.5，动态菜单不在 HOSPITAL_MENU）
    if _m4 and item_name == "治疗":
        if player.hp >= player.max_hp:
            return False, "你的 HP 已满"
        cost = _heal_cost(game_state)
        if getattr(player, 'credits', 0) < cost:
            return False, f"治疗需要 {cost} 信用点，你只有 {player.credits}"
        return True, ""

    if item_name not in HOSPITAL_MENU:
        return False, f"医院没有「{item_name}」"

    # 打工：v1 已有凭证时不允许；m4 信用点可累积无此限制
    if item_name == "打工" and not _m4 and player.vouchers >= 1:
        return False, "你已经有购买凭证了，不需要再打工。"

    if item_name in FREE_ITEMS:
        return True, ""

    # 手术需要凭证（v1）/ 信用点财产税下限（m4）
    if item_name in SURGERY_ITEMS:
        if _m4:
            min_cost = _bget("economy", "surgery_min_cost", default=4)
            if getattr(player, 'credits', 0) < min_cost:
                return False, (f"手术费 = 你的全部信用点（下限 {min_cost}）。"
                               f"你只有 {player.credits}，不足以支付。")
        elif player.vouchers < 1:
            return False, "手术需要至少1张购买凭证！（手术会消耗你所有凭证）"
        # hp20：手术=永久身体改造，终身一次（无内甲 piece 可查重，走 surgeries_done）
        from engine import experiments as _exp
        if _exp.is_enabled("hp20"):
            surgery_name = item_name.replace("手术", "")
            if surgery_name in getattr(player, 'surgeries_done', set()):
                return False, f"{surgery_name}手术终身只能进行一次。"
            return True, ""
        # v1：检查是否已有该内层护甲（同名护甲不能重复装备）
        armor_name_map = {
            "晶化皮肤手术": "晶化皮肤",
            "额外心脏手术": "额外心脏",
            "不老泉手术": "不老泉",
        }
        armor_name = armor_name_map.get(item_name)
        if armor_name:
            from models.equipment import ArmorPiece, ArmorLayer
            from utils.attribute import Attribute
            attr_map = {"晶化皮肤": Attribute.TECH, "额外心脏": Attribute.ORDINARY, "不老泉": Attribute.MAGIC}
            test_piece = ArmorPiece(armor_name, attr_map[armor_name], ArmorLayer.INNER, 1.0)
            can_equip, equip_reason = player.armor.check_can_equip(test_piece)
            if not can_equip:
                return False, f"无法进行{item_name}：{equip_reason}"

    # 防毒面具：需凭证但不消耗；检查是否已有
    if item_name == "防毒面具":
        # 已有防毒面具则不能再拿
        items = getattr(player, 'items', [])
        if any(getattr(i, 'name', '') == "防毒面具" for i in items):
            return False, "你已经有防毒面具了"
        if _m4:
            from engine.economy import can_afford
            return can_afford(player, "防毒面具")
        if player.vouchers < 1:
            return False, "防毒面具需要购买凭证（不消耗凭证）。"
        return True, ""

    # 星野药物：需习得战术动作，不检查凭证，最多持有2样
    if item_name in HOSHINO_MEDICINES:
        if not (player.talent and hasattr(player.talent, 'tactical_unlocked')
                and player.talent.tactical_unlocked):
            return False, "你需要先习得战术动作才能获取药物"
        if item_name == "肾上腺素" and getattr(player.talent, 'adrenaline_used', False):
            return False, "肾上腺素全局仅能使用1次，已经使用过了"
        if hasattr(player.talent, 'medicines') and len(player.talent.medicines) >= 2:
            return False, "你最多同时持有2样药物"
        return True, ""

    return True, ""




def do_interact(player, item_name, game_state=None):
    """执行医院交互"""
    from engine.economy import m4_enabled, charge, work_income

    # m4 弓模块（无限）：委托 do_purchase
    if m4_enabled():
        from engine.bow_modules import is_module_item, do_purchase, base_name
        if is_module_item(item_name):
            return do_purchase(player, base_name(item_name), game_state)

    # m4 治疗（§2.5：+heal_amount HP，黄昏费用×2）
    if m4_enabled() and item_name == "治疗":
        from engine.balance import get as _bget
        amount = _bget("hospital", "heal_amount", default=6)
        cost = _heal_cost(game_state)
        player.credits -= cost
        before = player.hp
        player.hp = min(player.max_hp, player.hp + amount)
        return (f"💉 {player.name} 接受治疗，HP {before}→{player.hp}"
                f"（花费 {cost} 信用点）")

    if item_name == "打工":
        if m4_enabled():
            income = work_income()
            player.credits += income
            return f"{player.name} 在医院打工，获得 {income} 信用点。当前：{player.credits}"
        player.vouchers += 1
        return f"{player.name} 在医院打工，获得1张购买凭证。当前：{player.vouchers}张"

    elif item_name == "防毒面具":
        if m4_enabled():
            charge(player, "防毒面具")
        player.add_item(make_item("防毒面具"))
        return f"{player.name} 获得了防毒面具，免疫病毒！😷"

    elif item_name == "晶化皮肤手术":
        from engine import experiments as _exp
        if _exp.is_enabled("hp20"):
            return _do_surgery_hp20(player, "晶化皮肤", game_state)
        return _do_surgery(player, "晶化皮肤",
                           ArmorPiece("晶化皮肤", Attribute.TECH, ArmorLayer.INNER, 1.0),
                           game_state)

    elif item_name == "额外心脏手术":
        from engine import experiments as _exp
        if _exp.is_enabled("hp20"):
            return _do_surgery_hp20(player, "额外心脏", game_state)
        return _do_surgery(player, "额外心脏",
                           ArmorPiece("额外心脏", Attribute.ORDINARY, ArmorLayer.INNER, 1.0),
                           game_state)

    elif item_name == "不老泉手术":
        from engine import experiments as _exp
        if _exp.is_enabled("hp20"):
            return _do_surgery_hp20(player, "不老泉", game_state)
        return _do_surgery(player, "不老泉",
                           ArmorPiece("不老泉", Attribute.MAGIC, ArmorLayer.INNER, 1.0),
                           game_state)

    # 星野药物
    elif item_name in HOSHINO_MEDICINES:
        player.talent.medicines.append(item_name)
        count = len(player.talent.medicines)
        return f"💊 {player.name} 获得了药物「{item_name}」！（当前持有 {count}/2）"

    return "❌ 未知项目"


def _do_surgery_hp20(player, surgery_name, game_state):
    """HP20 手术：永久身体改造而非内甲 piece（v2.0 §2.4，外层不破不打内层规则随之消失）。"""
    from engine.balance import get as bget

    if surgery_name in player.surgeries_done:
        return f"❌ {surgery_name}手术终身只能进行一次。（凭证未消耗）"

    spec = bget("surgery", surgery_name, default=None)
    if not isinstance(spec, dict):
        return f"❌ 系统错误：手术「{surgery_name}」无数值定义"

    from engine.economy import m4_enabled, pay_all
    log_kwargs = {}
    if m4_enabled():
        # m4 财产税：手术费 = 全部信用点（下限已在 can_interact 拦截）
        ok, paid = pay_all(player, "surgery_min_cost")
        if not ok:
            return f"❌ 信用点不足，无法进行{surgery_name}手术。"
        cost_note = f"\n   手术费：全部信用点（{paid} → 0）"
        log_kwargs["credits_spent"] = paid
    else:
        old_vouchers = player.vouchers
        player.clear_all_vouchers()
        cost_note = f"\n   消耗了所有购买凭证（{old_vouchers}张→0张）"
        log_kwargs["vouchers_spent"] = old_vouchers  # 保持与 hp20 golden 锚点一致
    player.surgeries_done.add(surgery_name)

    if surgery_name == "额外心脏":
        player.max_hp += spec.get("max_hp_bonus", 4)
        player.hp = min(player.hp + spec.get("heal_on_surgery", 4), player.max_hp)
        effect = f"生命上限 +{spec.get('max_hp_bonus', 4)}（当前 {player.hp}/{player.max_hp}）"
    elif surgery_name == "晶化皮肤":
        for attr_name, value in spec.get("inner_defense", {}).items():
            player.inner_defense[attr_name] = (
                player.inner_defense.get(attr_name, 0) + value)
        defs = "/".join(f"{k}-{v}" for k, v in spec.get("inner_defense", {}).items())
        effect = f"永久防御 {defs}（不可破坏）"
    elif surgery_name == "不老泉":
        player.regen_per_round += spec.get("regen_per_round", 1)
        effect = f"每轮再生 {player.regen_per_round} HP"
    else:
        effect = "？"

    if game_state:
        game_state.log_event("surgery", player=player.player_id,
                             surgery=surgery_name, **log_kwargs)
    return f"🏥 {player.name} 完成了{surgery_name}手术！{effect}{cost_note}"


def _do_surgery(player, surgery_name, armor_piece, game_state):
    # 先检查能否装备（不消耗凭证）
    can_equip, reason = player.armor.check_can_equip(armor_piece)
    if not can_equip:
        return f"❌ {player.name} 的{surgery_name}手术失败：{reason}（凭证未消耗）"

    # 检查通过，消耗凭证并装备
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
        # 理论上不应该到这里（can_interact已检查），但保险起见
        return (f"❌ {player.name} 的{surgery_name}手术失败：{reason}"
                f"\n   但购买凭证已被消耗（{old_vouchers}张→0张）")
