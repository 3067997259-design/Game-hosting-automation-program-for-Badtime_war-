"""特殊操作（Phase 3 完整版）：新增释放病毒"""

from models.equipment import make_weapon, make_armor
from engine.prompt_manager import prompt_manager


def get_available_specials(player, game_state):
    """获取当前可用的特殊操作列表"""
    specials = []

    # M4 拆卸弓模块（1 行动随处可做，拍板 §13-16）
    from engine.economy import m4_enabled
    if m4_enabled() and getattr(player, 'bow_modules', None):
        for mod in dict.fromkeys(player.bow_modules):
            specials.append({"name": f"拆卸{mod}",
                             "description": f"拆下弓模块「{mod}」（回流市场）"})

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
            # 六爻封印的武器不可蓄力
            if getattr(w, '_hexagram_disabled', False):
                continue
            specials.append({
                "name": f"蓄力{w.name}",
                "description": f"为「{w.name}」蓄力"
            })

    # 释放病毒（在医院时）
    if player.location == "医院" and not game_state.virus.is_active:
        specials.append({"name": "释放病毒", "description": "🦠 释放病毒，全体感染！"})

    # 星野取消架盾/持盾（不消耗回合）
    if (player.talent and hasattr(player.talent, 'shield_mode')
            and player.talent.shield_mode in ("架盾", "持盾")):
        specials.append({"name": "取消盾牌", "description": "🛡️ 取消当前架盾/持盾状态"})

    # 更衣（Hoshino 形态切换，需在自己家中）
    if player.talent and hasattr(player.talent, 'form'):
        if player.location == f"home_{player.player_id}":
            valid_forms = {"水着-shielder", "临战-Archer", "临战-shielder"}
            for form in valid_forms:
                if form != player.talent.form:
                    specials.append({
                        "name": f"更衣{form}",
                        "description": f"更换形态为「{form}」"
                    })
    # 星野战术指令宏
    if (player.talent and hasattr(player.talent, 'tactical_unlocked')
        and player.talent.tactical_unlocked and not getattr(player.talent, 'is_terror', False)
        and (getattr(player.talent, 'iron_horus_hp', 0) > 0
             or getattr(player.talent, 'eye_of_horus', None))):
        specials.append({"name": "Hoshino", "description": "⚔️ 发动战术指令宏"})

    # 星野修复铁之荷鲁斯
    if (player.talent and hasattr(player.talent, 'iron_horus_hp')
        and player.talent.fusion_shield_done
        and player.talent.iron_horus_hp < player.talent.iron_horus_max_hp):
        specials.append({"name": "修复", "description": "🔧 消耗盾牌/AT力场修复铁之荷鲁斯"})

    # 星野注射肾上腺素（宏外使用）
    if (player.talent and hasattr(player.talent, 'form')
            and hasattr(player.talent, 'adrenaline_used')
            and not player.talent.adrenaline_used
            and "肾上腺素" in getattr(player.talent, 'medicines', [])):
        specials.append({"name": "肾上腺素", "description": "💉 注射肾上腺素（下回合 cost+5 + 光环全恢复 + D4+3行动顺序）"})

    return specials


def execute(player, op_name, game_state):
    """执行特殊操作。
    统一返回 (msg, consumes_turn) 二元组。
    consumes_turn=True 表示消耗行动回合，False 表示不消耗。
    """
    if op_name.startswith("拆卸") and len(op_name) > 2:
        mod = op_name[2:]
        from engine.bow_modules import uninstall
        ok, msg = uninstall(player, mod, game_state)
        return msg, ok

    if op_name == "磨刀":
        return _do_sharpen(player, game_state), True
    elif op_name == "吟唱魔法护盾":
        return _do_regen_magic_shield(player, game_state), True
    elif op_name == "展开AT力场":
        return _do_regen_at_field(player, game_state), True
    elif op_name.startswith("蓄力"):
        weapon_name = op_name[2:]
        return _do_charge(player, weapon_name, game_state), True
    elif op_name == "释放病毒":
        return _do_release_virus(player, game_state), True
    elif op_name == "取消盾牌":
        if (player.talent and hasattr(player.talent, 'shield_mode')
                and player.talent.shield_mode in ("架盾", "持盾")):
            old_mode = player.talent.shield_mode
            player.talent._end_shield_mode(player)
            game_state.log_event("cancel_shield", player=player.player_id, mode=old_mode)
            return f"🛡️ {player.name} 取消了{old_mode}状态", False
        return "❌ 你没有处于架盾/持盾状态", False
    elif op_name == "Hoshino":
        if player.talent and hasattr(player.talent, '_execute_tactical_macro'):
            msg, consumes = player.talent._execute_tactical_macro(player)
            return msg, consumes
        return "❌ 你没有战术指令能力", False
    elif op_name.startswith("更衣"):
        form_name = op_name[2:].strip() if len(op_name) > 2 else ""
        if player.talent and hasattr(player.talent, 'form'):
            if player.location != f"home_{player.player_id}":
                return "❌ 需要在自己家中才能更衣", True
            valid_forms = {"水着-shielder", "临战-Archer", "临战-shielder"}
            if not form_name or form_name not in valid_forms:
                # 让玩家选择
                form_name = player.controller.choose(
                    "选择要更换到的形态：",
                    list(valid_forms),
                    context={"phase": "T1", "situation": "hoshino_change_form"}
                )
            if form_name in valid_forms:
                player.talent.form = form_name
                game_state.log_event("change_form", player=player.player_id, form=form_name)
                return f"👗 {player.name} 更换形态为「{form_name}」！", True
            return f"❌ 无效形态。可选：{', '.join(valid_forms)}", True
        return "❌ 你没有可更换的形态", True
    elif op_name.startswith("修复"):
        sacrifice = op_name[2:].strip() if len(op_name) > 2 else ""
        if player.talent and hasattr(player.talent, '_repair_horus'):
            return player.talent._repair_horus(player, sacrifice), True
        return "❌ 你没有可修复的装备", True
    elif op_name == "肾上腺素":
        if (player.talent and hasattr(player.talent, 'adrenaline_used')
                and not player.talent.adrenaline_used
                and "肾上腺素" in getattr(player.talent, 'medicines', [])):
            player.talent.adrenaline_used = True
            player.talent.medicines.remove("肾上腺素")
            player.talent._adrenaline_next_round = True  # 标记下回合生效
            game_state.log_event("adrenaline", player=player.player_id)
            msg = prompt_manager.get_prompt("talent", "g7hoshino.adrenaline_injected",
                default="💉 {player_name} 注射了肾上腺素！下回合将获得额外 cost 和光环恢复").format(
                player_name=player.name)
            return msg, False  # 不消耗行动回合
        return "❌ 无法使用肾上腺素", False
    else:
        return f"❌ 未知的特殊操作：{op_name}", True


def _do_sharpen(player, game_state):
    from engine import experiments as _exp
    if _exp.is_enabled("hp20"):
        from engine.balance import get as _bget
        sharpened = _bget("weapons", "磨刀小刀", default={}).get("damage", 7)
    else:
        sharpened = 2.0
    stone = None
    for i, item in enumerate(player.items):
        if item.name == "磨刀石":
            stone = i
            break
    if stone is None:
        return "❌ 你没有磨刀石"
    knife = None
    for w in player.weapons:
        if w.name == "小刀" and w.base_damage < sharpened:
            knife = w
            break
    if knife is None:
        return "❌ 你没有可以磨的小刀"
    player.items.pop(stone)
    knife.base_damage = sharpened
    game_state.log_event("sharpen", player=player.player_id)
    return f"🔪 {player.name} 磨了刀！小刀伤害提升至 {sharpened}。"


def _repair_or_recreate(player, armor_name, verb):
    """hp20 修复增量制（v2.0 §2.3）：已持有 → +repair_amount 耐久（不超上限）；
    未持有 → 重新创建。废除 v1「1 行动满血复活」修复平价。"""
    from engine import experiments as _exp
    if _exp.is_enabled("hp20"):
        from engine.balance import get as _bget
        existing = None
        for piece in getattr(player.armor, 'outer', []) or []:
            if piece.name == armor_name:
                existing = piece
                break
        if existing is not None:
            amount = _bget("armor", armor_name, default={}).get("repair_amount", 6)
            if existing.durability >= existing.max_durability:
                return f"❌「{armor_name}」耐久已满（{existing.durability}/{existing.max_durability}）"
            existing.durability = min(existing.max_durability,
                                      existing.durability + amount)
            return (f"🛡️ {player.name} {verb}，「{armor_name}」耐久 +{amount} "
                    f"→ {existing.durability}/{existing.max_durability}")
    armor = make_armor(armor_name)
    if armor is None:
        return "❌ 系统错误"
    success, reason = player.add_armor(armor)
    if success:
        return f"🛡️ {player.name} {verb}了{armor_name}！"
    return f"❌ 无法装备{armor_name}：{reason}"


def _do_regen_magic_shield(player, game_state):
    return _repair_or_recreate(player, "魔法护盾", "重新吟唱")


def _do_regen_at_field(player, game_state):
    return _repair_or_recreate(player, "AT力场", "重新展开")


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
            # 天赋犯罪检查（剪刀手一突等）
            if player.talent and hasattr(player.talent, 'on_crime_check'):
                crime_result = player.talent.on_crime_check(player.player_id, "释放病毒")
                if crime_result and crime_result.get("extra_turn"):
                    msg = crime_result.get("message", "")
                    if msg:
                        from cli import display
                        display.show_info(msg)
                    player.crime_extra_turn = True
            game_state.police_engine.check_and_record_crime(player.player_id, "释放病毒")

    game_state.log_event("release_virus", player=player.player_id)
    return (f"🦠 {player.name} 释放了病毒！全体玩家感染！"
            f"\n   5轮后未获得防毒面具或封闭的玩家将死亡！"
            f"\n   病毒期间商店物品免费！")
