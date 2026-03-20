"""特殊操作（Phase 3 完整版）：新增释放病毒"""

from models.equipment import make_weapon, make_armor


def get_available_specials(player, game_state):
    """获取当前可用的特殊操作列表"""
    specials = []

    # 磨刀
    has_stone = any(i.name == "磨刀石" for i in player.items)
    has_unsharpened = any(w.name == "小刀" and w.base_damage < 2 for w in player.weapons)
    if has_stone and has_unsharpened:
        specials.append({"name": "磨刀", "description": "消耗磨刀石，小刀伤害提升至2"})

    # 吟唱魔法护盾
    if "魔法护盾" in player.learned_spells:
        from utils.attribute import Attribute
        from models.equipment import ArmorLayer
        piece = player.armor.get_piece(ArmorLayer.OUTER, Attribute.MAGIC)
        if piece is None:
            specials.append({"name": "吟唱魔法护盾", "description": "重新展开魔法护盾"})

    # 展开AT力场
    if "AT力场" in player.learned_spells:
        from utils.attribute import Attribute
        from models.equipment import ArmorLayer
        piece = player.armor.get_piece(ArmorLayer.OUTER, Attribute.TECH)
        if piece is None:
            specials.append({"name": "展开AT力场", "description": "重新展开AT力场"})

    # 蓄力武器
    for w in player.weapons:
        if w.requires_charge and not w.is_charged:
            specials.append({
                "name": f"蓄力{w.name}",
                "description": f"为「{w.name}」蓄力"
            })

    # 释放病毒（在医院时）
    if player.location == "医院" and not game_state.virus.is_active:
        specials.append({"name": "释放病毒", "description": "🦠 释放病毒，全体感染！"})

    return specials


def execute(player, op_name, game_state):
    """执行特殊操作"""
    if op_name == "磨刀":
        return _do_sharpen(player, game_state)
    elif op_name == "吟唱魔法护盾":
        return _do_regen_magic_shield(player, game_state)
    elif op_name == "展开AT力场":
        return _do_regen_at_field(player, game_state)
    elif op_name.startswith("蓄力"):
        weapon_name = op_name[2:]
        return _do_charge(player, weapon_name, game_state)
    elif op_name == "释放病毒":
        return _do_release_virus(player, game_state)
    else:
        return f"❌ 未知的特殊操作：{op_name}"


def _do_sharpen(player, game_state):
    stone = None
    for i, item in enumerate(player.items):
        if item.name == "磨刀石":
            stone = i
            break
    if stone is None:
        return "❌ 你没有磨刀石"
    knife = None
    for w in player.weapons:
        if w.name == "小刀" and w.base_damage < 2:
            knife = w
            break
    if knife is None:
        return "❌ 你没有可以磨的小刀"
    player.items.pop(stone)
    knife.base_damage = 2.0
    game_state.log_event("sharpen", player=player.player_id)
    return f"🔪 {player.name} 磨了刀！小刀伤害提升至 2。"


def _do_regen_magic_shield(player, game_state):
    armor = make_armor("魔法护盾")
    if armor is None:
        return "❌ 系统错误"
    success, reason = player.add_armor(armor)
    if success:
        return f"🛡️ {player.name} 重新吟唱了魔法护盾！"
    return f"❌ 无法装备魔法护盾：{reason}"


def _do_regen_at_field(player, game_state):
    armor = make_armor("AT力场")
    if armor is None:
        return "❌ 系统错误"
    success, reason = player.add_armor(armor)
    if success:
        return f"🛡️ {player.name} 重新展开了AT力场！"
    return f"❌ 无法装备AT力场：{reason}"


def _do_charge(player, weapon_name, game_state):
    weapon = player.get_weapon(weapon_name)
    if not weapon:
        return f"❌ 你没有武器「{weapon_name}」"
    if not weapon.requires_charge:
        return f"❌「{weapon_name}」不需要蓄力"
    if weapon.is_charged:
        return f"❌「{weapon_name}」已蓄力完成"
    weapon.is_charged = True
    game_state.log_event("charge", player=player.player_id, weapon=weapon_name)
    return f"⚡ {player.name} 为「{weapon_name}」完成蓄力！"


def _do_release_virus(player, game_state):
    """释放病毒"""
    if game_state.virus.is_active:
        return "❌ 病毒已经在传播中了！"
    if player.location != "医院":
        return "❌ 需要在医院才能释放病毒"

    game_state.virus.release(player.player_id, game_state.current_round)

    # 犯罪检查（基础局不违法，朝阳好市民扩展时违法）
    if "释放病毒" in game_state.crime_types:
        if game_state.police_engine:
            game_state.police_engine.check_and_record_crime(player.player_id, "释放病毒")

    game_state.log_event("release_virus", player=player.player_id)  
    return (f"🦠 {player.name} 释放了病毒！全体玩家感染！"  
            f"\n   5轮后未获得防毒面具或封闭的玩家将死亡！"  
            f"\n   病毒期间商店物品免费！")
