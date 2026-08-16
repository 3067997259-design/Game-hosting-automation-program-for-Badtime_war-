"""特殊操作（Phase 3 完整版）：新增释放病毒"""

from models.equipment import (
    make_weapon, make_armor, has_unsharpened_knife, knife_sharpened_damage,
)
from engine.prompt_manager import prompt_manager
from engine.m9.text import m9_text


def _m9_pp_bonus(game_state) -> int:
    """PP 加伤的当前数值：M9 读 balance，legacy 保持 +2。"""
    from engine.balance import get as _bget
    from engine.m9.gate import m9_enabled
    if m9_enabled(game_state):
        return int(_bget("m9_system", "pp", "bonus_damage", default=1))
    return 2


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
    if has_stone and has_unsharpened_knife(player):
        target_damage = knife_sharpened_damage()
        specials.append({"name": "磨刀",
                         "description": f"消耗磨刀石，小刀伤害提升至{target_damage:g}"})

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

    # 释放病毒（在医院时；M9-rfc 不包含病毒机制，不展示）
    from engine.m9.gate import m9_enabled as _m9_virus
    if (not _m9_virus(game_state)
            and player.location == "医院" and not game_state.virus.is_active):
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

    # ── M9 警察/T6（profile: m9-rfc；v2exp 不出现）──
    from engine.m9.gate import m9_enabled
    if m9_enabled(game_state):
        _append_m9_specials(player, game_state, specials)

    return specials


def _append_m9_specials(player, game_state, specials):
    """M9 警察/T6 特殊操作：市民热线（T6 根行动）、队长竞选/指挥。"""
    station = getattr(game_state, "m9_police", None)
    talent = getattr(player, "talent", None)
    # A trapped unit always has a public break-barrier root, independent of
    # police availability.
    from engine.m9.talents.g3 import active_barrier
    barrier = active_barrier(game_state)
    if barrier is not None and barrier._is_trapped(player) \
            and getattr(player, "player_id", None) != barrier.player_id:
        specials.append({"name": "破界",
                         "description": m9_text("special_op.desc_barrier_break")})
        if any(w for w in getattr(player, "weapons", []) if w):
            specials.append({
                "name": "武器破界",
                "description": m9_text("special_op.desc_barrier_break_weapon")})
    if station is None or station.is_disabled():
        return
    station.set_state_ref(game_state)
    # 市民热线：朝阳好市民的标准根行动（不读 SP）
    if talent is not None and getattr(talent, "name", "") == "朝阳好市民":
        suspects = [p for p in game_state.player_order
                    if p != player.player_id
                    and game_state.get_player(p).is_alive()]
        for pid in suspects:
            p = game_state.get_player(pid)
            specials.append({
                "name": f"热线举报{p.name}",
                "description": m9_text("special_op.desc_hotline_report", name=p.name),
            })
    # 队长候选/指挥
    if station.captain_id is None and not getattr(talent, "is_terror", False):
        if player.player_id not in station.candidates():
            specials.append({"name": "竞选队长",
                             "description": m9_text("special_op.desc_captain_candidate")})
    elif station.captain_id == player.player_id:
        wanted = station.open_wanted()
        suspect = None
        if wanted is not None:
            suspect = game_state.get_player(wanted.suspect_id)
        for u in station.alive_units():
            # 指挥强化（裁决）：通缉目标与警员同地点时开放立即攻击——
            # 警察线的真输出入口（此前引擎支持 attack 命令但无 special 暴露）
            if (suspect is not None and suspect.is_alive()
                    and getattr(u, "location", None)
                    == getattr(suspect, "location", None)):
                specials.append({
                    "name": f"指挥{u.unit_id}攻击",
                    "description": m9_text("special_op.desc_command_attack",
                                           unit_id=u.unit_id),
                })
            specials.append({
                "name": f"指挥{u.unit_id}移动",
                "description": m9_text("special_op.desc_command_move"),
            })
    # M9 PP 生前消耗（B4 §3.3）：重掷/加伤/偷看先攻/抵消犯罪（旧喝彩 4 用途迁入 PP）
    pp = getattr(game_state, "m9_pp", None)
    if pp is not None and not getattr(talent, "is_terror", False):
        bal = pp.balance(player.player_id)
        if bal >= 1:
            specials.append({"name": "PP重掷先攻",
                             "description": m9_text("special_op.desc_pp_reroll")})
            specials.append({"name": "PP加伤",
                             "description": m9_text(
                                 "special_op.desc_pp_damage",
                                 bonus=_m9_pp_bonus(game_state))})
        if bal >= 2:
            specials.append({"name": "PP偷看先攻",
                             "description": m9_text("special_op.desc_pp_peek")})
            specials.append({"name": "PP抵消犯罪",
                             "description": m9_text("special_op.desc_pp_crime_clear")})
    # G1 卸甲免费 find（§2.1）：每轮一次、不占行动槽（玩家主动发起）
    if (talent is not None and hasattr(talent, "free_find_available")
            and talent.free_find_available(getattr(game_state, "current_round", 1))):
        specials.append({"name": "卸甲免费find",
                         "description": m9_text("special_op.desc_free_find")})
    # M9 交易（B4 §五）：向任一玩家转移 PP（金额执行时输入）
    if pp is not None:
        for pid in game_state.player_order:
            if pid == player.player_id:
                continue
            other = game_state.get_player(pid)
            if other is not None:
                specials.append({
                    "name": f"交易{other.name}",
                    "description": m9_text("special_op.desc_trade", name=other.name),
                })


def execute(player, op_name, game_state):
    """执行特殊操作。
    统一返回 (msg, consumes_turn) 二元组。
    consumes_turn=True 表示消耗行动回合，False 表示不消耗。
    """
    # 释放病毒：M9-rfc 禁用（该机制不属于 M9 当前规则）。
    if op_name == "释放病毒":
        from engine.m9.gate import m9_enabled
        if m9_enabled(game_state):
            return m9_text("special_op.err_virus_rejected"), False
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
    elif op_name.startswith("热线举报"):
        target_name = op_name[len("热线举报"):]
        target = next((p for p in game_state.player_order
                       if game_state.get_player(p).name == target_name), None)
        if target is None:
            return m9_text("special_op.err_report_target_invalid"), False
        if player.talent and hasattr(player.talent, "hotline_report"):
            msg = player.talent.hotline_report(target)
            return msg, not msg.startswith("❌")
        return m9_text("special_op.err_no_hotline_ability"), False
    elif op_name in ("破界", "武器破界"):
        from engine.m9.talents.g3 import active_barrier
        barrier = active_barrier(game_state)
        if barrier is None:
            return m9_text("special_op.err_no_barrier"), False
        if op_name == "破界":
            return barrier.break_barrier(player)
        weapons = [w for w in getattr(player, "weapons", [])
                   if w is not None and not getattr(w, "_hexagram_disabled", False)]
        if not weapons:
            return m9_text("special_op.err_no_weapon"), False
        names = [w.name for w in weapons]
        try:
            name = player.controller.choose(
                m9_text("special_op.choose_break_weapon"), names)
        except Exception:
            name = names[0]
        weapon = next((w for w in weapons if w.name == name), weapons[0])
        return barrier.weapon_attack_anchor(player, weapon)
    elif op_name == "竞选队长":
        station = getattr(game_state, "m9_police", None)
        if station is None or station.captain_id is not None:
            return m9_text("special_op.err_captain_seat_occupied"), True
        if station.apply_captain(player.player_id):
            return m9_text("special_op.msg_captain_registered"), True
        return m9_text("special_op.err_already_candidate"), True
    elif op_name.startswith("指挥") and op_name.endswith("移动"):
        unit_id = op_name[len("指挥"):-len("移动")]
        station = getattr(game_state, "m9_police", None)
        if station is None:
            return m9_text("special_op.err_police_not_mounted"), True
        # 指挥强化（裁决）：有通缉目标时移动到其地点（为指挥攻击接敌），
        # 否则回警察局（此前硬编码回局，警员永远够不到通缉目标）。
        dest = "警察局"
        wanted = station.open_wanted()
        if wanted is not None:
            suspect = game_state.get_player(wanted.suspect_id)
            if suspect is not None and suspect.is_alive():
                dest = getattr(suspect, "location", None) or "警察局"
        msg = station.captain_command(player.player_id, unit_id, "move", dest)
        return msg, True
    elif op_name.startswith("指挥") and op_name.endswith("攻击"):
        unit_id = op_name[len("指挥"):-len("攻击")]
        station = getattr(game_state, "m9_police", None)
        if station is None:
            return m9_text("special_op.err_police_not_mounted"), True
        wanted = station.open_wanted()
        if wanted is None:
            return m9_text("special_op.err_no_wanted"), True
        msg = station.captain_command(
            player.player_id, unit_id, "attack", wanted.suspect_id)
        return msg, True
    elif op_name in ("PP重掷先攻", "PP加伤", "PP偷看先攻", "PP抵消犯罪"):
        return _m9_pp_use(player, game_state, op_name)
    elif op_name == "卸甲免费find":
        talent = getattr(player, "talent", None)
        if talent is None or not hasattr(talent, "do_free_find"):
            return m9_text("special_op.err_no_free_find_ability"), False
        return talent.do_free_find(player)
    elif op_name.startswith("交易"):
        return _m9_trade(player, game_state, op_name[len("交易"):])
    else:
        return f"❌ 未知的特殊操作：{op_name}", True


def _m9_pp_use(player, game_state, op_name):
    """B4 §3.3 PP 生前消耗（旧喝彩 4 用途迁入 PP）：重掷/加伤/偷看/抵消犯罪。"""
    from engine.balance import get as _bget
    pp = getattr(game_state, "m9_pp", None)
    if pp is None:
        return m9_text("special_op.err_pp_ledger_missing"), False
    cost_map = {"PP重掷先攻": 1, "PP加伤": 1,
                "PP偷看先攻": 2, "PP抵消犯罪": 2}
    cost = cost_map[op_name]
    if not pp.spend(player.player_id, cost):
        return m9_text("special_op.err_pp_insufficient"), False
    if op_name == "PP重掷先攻":
        player._applause_reroll_initiative = True
        return m9_text("special_op.msg_pp_reroll",
                       name=player.name, cost=cost), False
    if op_name == "PP加伤":
        from engine.m9.gate import m9_enabled
        if m9_enabled(game_state):
            bonus = int(_bget("m9_system", "pp", "bonus_damage", default=1))
            player._m9_pp_damage_bonus = getattr(
                player, "_m9_pp_damage_bonus", 0) + bonus
            return (m9_text("special_op.msg_pp_damage",
                            name=player.name, cost=cost, bonus=bonus),
                    False)
        player._applause_damage_bonus = getattr(
            player, "_applause_damage_bonus", 0) + 2
        return m9_text("special_op.msg_pp_damage",
                       name=player.name, cost=cost, bonus=2), False
    if op_name == "PP偷看先攻":
        player._applause_peek_initiative = True
        return m9_text("special_op.msg_pp_peek",
                       name=player.name, cost=cost), False
    records = getattr(getattr(game_state, "police", None),
                      "crime_records", None)
    if records and player.player_id in records and records[player.player_id]:
        records[player.player_id].pop()
        if not records[player.player_id]:
            player.is_criminal = False
        return m9_text("special_op.msg_pp_crime_clear",
                       name=player.name, cost=cost), False
    pp.earn(player.player_id, cost)  # 无犯罪可抵消，退款
    return m9_text("special_op.err_pp_no_crime"), False


def _m9_trade(player, game_state, target_name):
    """B4 §五 交易系统：向任一玩家转移 PP（金额执行时输入）。"""
    pp = getattr(game_state, "m9_pp", None)
    if pp is None:
        return m9_text("special_op.err_trade_ledger_missing"), False
    target = next((pid for pid in game_state.player_order
                   if game_state.get_player(pid).name == target_name), None)
    if target is None or target == player.player_id:
        return m9_text("special_op.err_trade_target_invalid"), False
    bal = pp.balance(player.player_id)
    if bal < 1:
        return m9_text("special_op.err_trade_insufficient"), False
    try:
        amt = int(player.controller.choose(
            m9_text("special_op.choose_trade_amount",
                    name=target_name, max_pp=bal),
            [str(i) for i in range(1, bal + 1)]))
    except Exception:
        amt = 1
    amt = max(1, min(int(amt), bal))
    if not pp.transfer_pp(player.player_id, target, amt):
        return m9_text("special_op.err_trade_failed"), False
    game_state.log_event("m9_trade", sender=player.player_id,
                         target=target, amount=amt)
    return m9_text("special_op.msg_trade_success",
                   player_name=player.name, name=target_name, amount=amt), False


def _do_sharpen(player, game_state):
    sharpened = knife_sharpened_damage()
    stone = None
    for i, item in enumerate(player.items):
        if item.name == "磨刀石":
            stone = i
            break
    if stone is None:
        return "❌ 你没有磨刀石"
    knife = None
    for w in player.weapons:
        if getattr(w, "name", "") == "小刀" \
                and float(getattr(w, "base_damage", 0) or 0) < sharpened:
            knife = w
            break
    if knife is None:
        return "❌ 你没有可以磨的小刀"
    player.items.pop(stone)
    knife.base_damage = sharpened
    game_state.log_event("sharpen", player=player.player_id)
    return f"🔪 {player.name} 磨了刀！小刀伤害提升至 {sharpened:g}。"


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
