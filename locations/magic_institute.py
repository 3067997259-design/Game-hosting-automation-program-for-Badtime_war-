"""
地点：魔法所
法术免费，部分需多回合学习。
使用进度系统（6.1）。
"""

from models.equipment import make_weapon, make_armor


MAGIC_MENU = {
    "魔法护盾":     "学习1回合，外层魔法护盾1（可重新吟唱恢复）",
    "魔法弹幕":     "学习1回合，近战法术伤害1",
    "远程魔法弹幕": "需先学会魔法弹幕，再学1回合，远程法术伤害1",
    "封闭":         "学习2回合，免疫病毒",
    "地震":         "学习1回合，范围法术伤害0.5",
    "地动山摇":     "需先学会地震，再学1回合，范围法术伤害0.5+震荡2目标",
    "隐身术":       "学习1回合，永久隐身",
    "探测魔法":     "学习1回合，获得探测能力",
}

# 学习所需回合数
LEARN_TURNS = {
    "魔法护盾": 1,
    "魔法弹幕": 1,
    "远程魔法弹幕": 1,
    "封闭": 2,
    "地震": 1,
    "地动山摇": 1,
    "隐身术": 1,
    "探测魔法": 1,
}

# 前置依赖
PREREQUISITES = {
    "远程魔法弹幕": "魔法弹幕",
    "地动山摇": "地震",
}


def get_menu():
    return dict(MAGIC_MENU)


def can_interact(player, item_name, game_state=None):
    if item_name not in MAGIC_MENU:
        return False, f"魔法所没有「{item_name}」"

    # 检查是否已学会
    if item_name in player.learned_spells:
        return False, f"你已经学会「{item_name}」了"

    # 检查前置
    prereq = PREREQUISITES.get(item_name)
    if prereq and prereq not in player.learned_spells:
        return False, f"需要先学会「{prereq}」才能学习「{item_name}」"

    return True, ""


def do_interact(player, item_name, game_state=None):
    """执行学习。使用进度系统。"""
    required = LEARN_TURNS.get(item_name, 1)
    progress_key = f"learn_{item_name}"

    # 推进进度
    current = player.progress.get(progress_key, 0)
    current += 1
    player.progress[progress_key] = current

    if current < required:
        return f"📖 {player.name} 正在学习「{item_name}」... 进度：{current}/{required}"

    # 学习完成
    player.learned_spells.add(item_name)
    del player.progress[progress_key]

    return _apply_learned_spell(player, item_name, game_state)


def _apply_learned_spell(player, spell_name, game_state):
    """学习完成后应用效果"""
    from utils.attribute import Attribute
    from models.equipment import Weapon, WeaponRange, ArmorPiece, ArmorLayer

    if spell_name == "魔法护盾":
        armor = ArmorPiece("魔法护盾", Attribute.MAGIC, ArmorLayer.OUTER, 1.0, can_regen=True)
        success, reason = player.add_armor(armor)
        if success:
            return f"✨ {player.name} 学会了魔法护盾并展开！（外层魔法护盾1）"
        else:
            return f"✨ {player.name} 学会了魔法护盾！但装备失败：{reason}（可以之后吟唱展开）"

    elif spell_name == "魔法弹幕":
        w = Weapon("魔法弹幕", Attribute.MAGIC, 1.0, WeaponRange.MELEE)
        player.add_weapon(w)
        return f"✨ {player.name} 学会了魔法弹幕！（近战法术伤害1）"

    elif spell_name == "远程魔法弹幕":
        w = Weapon("远程魔法弹幕", Attribute.MAGIC, 1.0, WeaponRange.RANGED)
        player.add_weapon(w)
        return f"✨ {player.name} 学会了远程魔法弹幕！（远程法术伤害1）"

    elif spell_name == "封闭":
        player.has_seal = True
        return f"✨ {player.name} 学会了封闭！免疫病毒。"

    elif spell_name == "地震":
        w = Weapon("地震", Attribute.MAGIC, 0.5, WeaponRange.AREA)
        player.add_weapon(w)
        return f"✨ {player.name} 学会了地震！（范围法术伤害0.5）"

    elif spell_name == "地动山摇":
        w = Weapon("地动山摇", Attribute.MAGIC, 0.5, WeaponRange.AREA,
                   special_tags=["shock_2_targets"])
        player.add_weapon(w)
        return f"✨ {player.name} 学会了地动山摇！（范围法术伤害0.5 + 眩晕2目标）"

    elif spell_name == "隐身术":
        player.is_invisible = True
        if game_state:
            game_state.markers.on_player_go_invisible(
                player.player_id, list(game_state.players.values()))
        return f"✨ {player.name} 学会了隐身术！永久隐身。🫥"

    elif spell_name == "探测魔法":
        player.has_detection = True
        return f"✨ {player.name} 学会了探测魔法！可以发现隐身目标。🔍"

    return f"✨ {player.name} 学会了「{spell_name}」！"
