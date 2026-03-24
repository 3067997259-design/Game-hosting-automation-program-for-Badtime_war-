"""伤害结算管线（Phase 4 完整版）：支持天赋参数、电磁步枪/陶瓷护甲特效"""

from utils.attribute import Attribute, is_effective
from models.equipment import ArmorLayer, WeaponRange
from engine.prompt_manager import prompt_manager

def _get_hologram_bonus(target, game_state):
    """检查所有玩家天赋，看目标是否在全息影像中"""
    if not game_state:
        return 0
    for pid in game_state.player_order:
        p = game_state.get_player(pid)
        if p and p.talent and hasattr(p.talent, 'get_bonus_damage'):
            bonus = p.talent.get_bonus_damage(target.player_id)
            if bonus > 0:
                return bonus
    return 0

def quantize_damage(damage):
    """伤害量化规则：
    - damage <= 0 → 0
    - 整数伤害 → 不变
    - 有小数部分 → int_part + 0.5（即 <0.5 补足为0.5，>=0.5 截为0.5）
    """
    if damage <= 0:
        return 0
    int_part = int(damage)
    frac_part = damage - int_part
    if abs(frac_part) < 1e-9:
        return float(int_part)
    return int_part + 0.5


def _check_electric_immunity(target):
    """
    检查目标是否拥有能免疫电流武器的护甲（陶瓷护甲 immune_electric tag）。
    根据README：陶瓷护甲：外层护甲，普通护盾1，免疫电流武器伤害与眩晕
    返回：(bool, ArmorPiece or None)
    """
    all_active = target.armor.get_all_active()
    for armor in all_active:
        if "immune_electric" in armor.special_tags and not armor.is_broken:
            return True, armor
    return False, None


def _resolve_weaponless_damage(attacker, target, game_state, result,
                                raw_damage, damage_attribute_str):
    """
    无武器伤害结算（爱与记忆之诗等外部伤害源）。
    走护甲结算但不涉及武器天赋修正。
    """
    from utils.attribute import Attribute

    ATTR_MAP = {
        "科技": Attribute.TECH,
        "普通": Attribute.ORDINARY,
        "魔法": Attribute.MAGIC,
        "无视属性克制": None,
    }
    damage_attr = ATTR_MAP.get(damage_attribute_str)

    raw = raw_damage
    result["raw_damage"] = raw

    external_damage_text = prompt_manager.get_prompt(
        "combat", "external_damage_source",
        default="外部伤害源：{damage}（{attribute}）"
    )
    result["details"].append(external_damage_text.format(
        damage=raw, attribute=damage_attribute_str
    ))

    # ---- 全息影像：目标在影像内额外+0.5 ----
    hologram_bonus = _get_hologram_bonus(target, game_state)
    if hologram_bonus > 0:
        raw += hologram_bonus
        hologram_text = prompt_manager.get_prompt(
            "combat", "hologram_vulnerability",
            default="✨全息影像易伤：+{hologram_bonus}"
        )
        result["details"].append(hologram_text.format(
            hologram_bonus=hologram_bonus
        ))

    # ---- 天赋受伤减免（如火萤IV型 -50%）----  <-- 新增
    if target.talent and hasattr(target.talent, 'modify_incoming_damage'):
        original_raw = raw
        raw = target.talent.modify_incoming_damage(target, attacker, None, raw)
        if raw != original_raw:
            damage_reduced_text = prompt_manager.get_prompt(
                "combat", "damage_reduced",
                default="受伤减免后：{damage}"
            )
            result["details"].append(damage_reduced_text.format(damage=raw))

    final_damage = quantize_damage(raw)
    result["final_damage"] = final_damage
    remaining = final_damage

    armor_piece = _select_armor_target(target, None, None)
    if armor_piece is not None:
        if damage_attr is not None:
            if not is_effective(damage_attr, armor_piece.attribute):
                weapon_countered_text = prompt_manager.get_prompt(
                    "combat", "weapon_countered",
                    default="伤害属性「{weapon_attr}」被护甲「{armor_name}({armor_attr})」克制，无效！"
                )
                result["reason"] = weapon_countered_text.format(
                    weapon_attr=damage_attribute_str,
                    armor_name=armor_piece.name,
                    armor_attr=armor_piece.attribute.value
                )
                result["details"].append(result["reason"])
                result["success"] = False
                result["final_damage"] = 0
                return result

        attack_target_text = prompt_manager.get_prompt(
            "combat", "attack_target_armor",
            default="攻击目标护甲：{armor_piece}"
        )
        result["details"].append(attack_target_text.format(
            armor_piece=armor_piece
        ))

    result["success"] = True

    if armor_piece is not None:
        remaining = _apply_damage_to_armor(
            target, armor_piece, remaining,
            False, result,
            damage_attr
        )

    if remaining > 0:
        if target.talent and hasattr(target.talent, 'receive_damage_to_temp_hp'):
            remaining = target.talent.receive_damage_to_temp_hp(remaining)
        if remaining > 0:
            result["hp_damage"] = remaining
            target.hp = round(max(0, target.hp - remaining), 2)

            hp_damage_text = prompt_manager.get_prompt(
                "combat", "hp_damage_detailed",
                default="生命受到 {damage} 伤害 → HP: {current_hp}/{max_hp}"
            )
            result["details"].append(hp_damage_text.format(
                damage=remaining,
                current_hp=target.hp,
                max_hp=target.max_hp
            ))

    result["target_hp"] = target.hp

    if target.hp <= 0:
        prevented = _talent_death_check(target, attacker, game_state)
        if prevented:
            result["killed"] = False
            result["target_hp"] = target.hp
            death_prevented_text = prompt_manager.get_prompt(
                "combat", "death_prevented",
                default="💫 记忆令毁灭的骄阳愈发明亮，不会落下…… HP → {target_hp}"
            )
            result["details"].append(death_prevented_text.format(
                target_hp=target.hp
            ))
        else:
            result["killed"] = True
            killed_text = prompt_manager.get_prompt(
                "combat", "killed",
                default="💀 {target_name} 被击杀！"
            )
            result["details"].append(killed_text.format(
                target_name=target.name
            ))
            if attacker and attacker.talent and hasattr(attacker.talent, 'on_kill'):
                attacker.talent.on_kill(attacker, target)
            if target.talent and hasattr(target.talent, 'on_player_death_check'):
                target.talent.on_player_death_check(target)

    elif target.hp <= 0.5 and not target.is_stunned:
        prevent = False
        if target.talent and hasattr(target.talent, 'prevent_stun'):
            prevent = target.talent.prevent_stun(target)
        if not prevent:
            result["stunned"] = True
            target.is_stunned = True
            if game_state:
                game_state.markers.add(target.player_id, "STUNNED")
            stunned_text = prompt_manager.get_prompt(
                "combat", "stunned_full",
                default="💫 {target_name} 进入眩晕状态！"
            )
            result["details"].append(stunned_text.format(
                target_name=target.name
            ))
        else:
            stun_prevented_text = prompt_manager.get_prompt(
                "combat", "stun_prevented",
                default="🔥 {target_name} 从不因为孱弱的攻击而倒下！"
            )
            result["details"].append(stun_prevented_text.format(
                target_name=target.name
            ))

    # ---- 愿负世：被攻击时积累神性 ----
    if target.talent and hasattr(target.talent, 'on_being_attacked') and attacker:
        is_limited = False
        if attacker and attacker.talent and hasattr(attacker.talent, 'uses_remaining'):
            is_limited = True
        target.talent.on_being_attacked(attacker, None, is_limited)

    return result


def resolve_damage(attacker, target, weapon, game_state,
                   target_layer=None, target_armor_attr=None,
                   ignore_element=False, damage_multiplier=1.0,
                   bonus_damage=0.0,
                   ignore_counter=False,
                   ignore_last_inner_absorb=False,
                   raw_damage_override=None,
                   damage_attribute_override=None):
    """
    完整伤害结算。
    新增参数（Phase 4）：
      ignore_counter: 无视属性克制（一刀缭断）
      ignore_last_inner_absorb: 最后内层不吸收溢出（一刀缭断）
    新增参数（Phase 5 涟漪）：
      raw_damage_override: 无武器时的原始伤害值
      damage_attribute_override: 无武器时的伤害属性（字符串）
    """
    result = {
        "success": False,
        "reason": "",
        "raw_damage": 0,
        "final_damage": 0,
        "armor_hit": None,
        "armor_broken": False,
        "hp_damage": 0,
        "target_hp": target.hp,
        "stunned": False,
        "shocked": False,
        "killed": False,
        "details": [],
    }

    # ======== 无武器模式（爱与记忆之诗等外部伤害源） ========
    if weapon is None:
        return _resolve_weaponless_damage(
            attacker, target, game_state, result,
            raw_damage_override or 1.0,
            damage_attribute_override or "普通"
        )

    # ======== 陶瓷护甲免疫电流武器检查 ========
    # 根据README：陶瓷护甲 免疫电流武器伤害与眩晕
    # 如果武器是电流武器(is_electric)且目标有未破碎的immune_electric护甲，
    # 则整个攻击无效（包括伤害和后续的震荡效果）
    if weapon.is_electric:
        immune, immune_armor = _check_electric_immunity(target)
        if immune and immune_armor:
            electric_immune_text = prompt_manager.get_prompt(
                "combat", "electric_immunity",
                default="🛡️ {target_name} 的「{armor_name}」免疫电流武器伤害与眩晕！攻击无效。"
            )
            result["reason"] = electric_immune_text.format(
                target_name=target.name,
                armor_name=immune_armor.name
            )
            result["details"].append(result["reason"])
            return result

    # ---- 天赋：修改输出伤害 ----
    if attacker and attacker.talent:
        mod = attacker.talent.modify_outgoing_damage(attacker, target, weapon, weapon.get_effective_damage())
        if mod:
            if "damage_multiplier_override" in mod:
                damage_multiplier = mod["damage_multiplier_override"]
            if mod.get("ignore_counter"):
                ignore_counter = True
            if mod.get("ignore_last_inner_absorb"):
                ignore_last_inner_absorb = True
            if "bonus_damage" in mod:
                bonus_damage += mod["bonus_damage"]

    # ---- 第1步：计算原始伤害 ----
    raw = weapon.get_effective_damage()
    raw = raw * damage_multiplier + bonus_damage
    result["raw_damage"] = raw

    raw_damage_text = prompt_manager.get_prompt(
        "combat", "raw_damage",
        default="原始伤害：{damage}"
    )
    result["details"].append(raw_damage_text.format(damage=raw))

    # ---- 萤火受伤减免 ----
    if target.talent and hasattr(target.talent, 'modify_incoming_damage'):
        raw = target.talent.modify_incoming_damage(target, attacker, weapon, raw)
        if raw != result["raw_damage"]:
            damage_reduced_text = prompt_manager.get_prompt(
                "combat", "damage_reduced",
                default="受伤减免后：{damage}"
            )
            result["details"].append(damage_reduced_text.format(damage=raw))

    # ---- 全息影像：目标在影像内额外+0.5 ----
    hologram_bonus = _get_hologram_bonus(target, game_state)
    if hologram_bonus > 0:
        raw += hologram_bonus
        hologram_text = prompt_manager.get_prompt(
            "combat", "hologram_vulnerability",
            default="✨全息影像易伤：+{hologram_bonus}"
        )
        result["details"].append(hologram_text.format(
            hologram_bonus=hologram_bonus
        ))

    # ---- 第2步：选择攻击目标层和属性 ----
    armor_piece = _select_armor_target(target, target_layer, target_armor_attr)

    # ---- 第3步：克制判定 ----
    if armor_piece is not None:
        if not ignore_counter and not ignore_element:
            if not is_effective(weapon.attribute, armor_piece.attribute):
                weapon_countered_text = prompt_manager.get_prompt(
                    "combat", "weapon_countered",
                    default="武器「{weapon_attr}」被护甲「{armor_name}({armor_attr})」克制，无效！"
                )
                result["reason"] = weapon_countered_text.format(
                    weapon_attr=weapon.attribute.value,
                    armor_name=armor_piece.name,
                    armor_attr=armor_piece.attribute.value
                )
                result["details"].append(result["reason"])
                return result

        attack_target_text = prompt_manager.get_prompt(
            "combat", "attack_target_armor",
            default="攻击目标护甲：{armor_piece}"
        )
        result["details"].append(attack_target_text.format(
            armor_piece=armor_piece
        ))

    # ---- 第4步：伤害量化 ----
    final_damage = quantize_damage(raw)
    result["final_damage"] = final_damage

    quantized_text = prompt_manager.get_prompt(
        "combat", "quantized_damage",
        default="量化后伤害：{damage}"
    )
    result["details"].append(quantized_text.format(damage=final_damage))

    # ---- 第5步：扣减护甲/生命 ----
    remaining = final_damage
    result["success"] = True

    if armor_piece is not None:
        remaining = _apply_damage_to_armor(
            target, armor_piece, remaining,
            ignore_last_inner_absorb, result,
            weapon.attribute
        )

    if remaining > 0:
        if target.talent and hasattr(target.talent, 'receive_damage_to_temp_hp'):
            remaining = target.talent.receive_damage_to_temp_hp(remaining)
        if remaining > 0:
            result["hp_damage"] = remaining
            target.hp = round(max(0, target.hp - remaining), 2)
            hp_damage_text = prompt_manager.get_prompt(
                "combat", "hp_damage_detailed",
                default="生命受到 {damage} 伤害 → HP: {current_hp}/{max_hp}"
            )
            result["details"].append(hp_damage_text.format(
                damage=remaining,
                current_hp=target.hp,
                max_hp=target.max_hp
            ))

    result["target_hp"] = target.hp

    # 记录攻击前的CC状态，用于后续电磁步枪震荡判定
    pre_attack_stunned = getattr(target, 'is_stunned', False)
    pre_attack_shocked = getattr(target, 'is_shocked', False)

    # ---- 第6步：眩晕/死亡判定 ----
    if target.hp <= 0:
        prevented = _talent_death_check(target, attacker, game_state)
        if prevented:
            result["killed"] = False
            result["target_hp"] = target.hp
            death_prevented_text = prompt_manager.get_prompt(
                "combat", "death_prevented",
                default="💫 记忆令毁灭的骄阳愈发明亮，不会落下…… HP → {target_hp}"
            )
            result["details"].append(death_prevented_text.format(
                target_hp=target.hp
            ))
        else:
            result["killed"] = True
            killed_text = prompt_manager.get_prompt(
                "combat", "killed",
                default="💀 {target_name} 被击杀！"
            )
            result["details"].append(killed_text.format(
                target_name=target.name
            ))
            if attacker and attacker.talent and hasattr(attacker.talent, 'on_kill'):
                attacker.talent.on_kill(attacker, target)
            if target.talent and hasattr(target.talent, 'on_player_death_check'):
                target.talent.on_player_death_check(target)

    elif target.hp <= 0.5 and not target.is_stunned:
        prevent = False
        if target.talent and hasattr(target.talent, 'prevent_stun'):
            prevent = target.talent.prevent_stun(target)
        if not prevent:
            result["stunned"] = True
            target.is_stunned = True
            if game_state:
                game_state.markers.add(target.player_id, "STUNNED")
            stunned_text = prompt_manager.get_prompt(
                "combat", "stunned_full",
                default="💫 {target_name} 进入眩晕状态！"
            )
            result["details"].append(stunned_text.format(
                target_name=target.name
            ))
        else:
            stun_prevented_text = prompt_manager.get_prompt(
                "combat", "stun_prevented",
                default="🔥 {target_name} 从不因为孱弱的攻击而倒下！"
            )
            result["details"].append(stun_prevented_text.format(
                target_name=target.name
            ))

    # ---- 第6.5步：电磁步枪命中震荡（stun_on_hit） ----
    # 根据README：电磁步枪发射时对已发现你的所有目标造成0.5科技伤害+眩晕
    # 震荡（Shocked）：下一个行动回合只能选择「苏醒」，消耗一个行动回合
    # 震荡和眩晕不能叠加，其中一个被解除，另一个随即解除
    # 注意：只有攻击成功且目标未死亡时才施加震荡
    if (weapon.special_tags and "stun_on_hit" in weapon.special_tags
            and result["success"] and not result["killed"]):
        already_cc = pre_attack_stunned or pre_attack_shocked
        if not already_cc:
            prevent_shock = False
            if target.talent and hasattr(target.talent, 'prevent_stun'):
                prevent_shock = target.talent.prevent_stun(target)

            if not prevent_shock:
                result["shocked"] = True
                target.is_shocked = True
                target.is_stunned = True
                if game_state:
                    game_state.markers.add(target.player_id, "SHOCKED")
                    game_state.markers.add(target.player_id, "STUNNED")

                shocked_text = prompt_manager.get_prompt(
                    "combat", "shocked_by_electric",
                    default="⚡ {target_name} 被电磁步枪击中，进入震荡状态！"
                )
                result["details"].append(shocked_text.format(
                    target_name=target.name
                ))
            else:
                shock_prevented_text = prompt_manager.get_prompt(
                    "combat", "shock_prevented",
                    default="🔥 {target_name} 抵抗了电磁步枪的震荡效果！"
                )
                result["details"].append(shock_prevented_text.format(
                    target_name=target.name
                ))

    # ---- 近战攻击造成伤害后：隐身临时失效（README 9.3.3） ----
    # 面对面关系解除前隐身失效，解除后恢复
    if (attacker and game_state
            and weapon.weapon_range == WeaponRange.MELEE
            and result["success"] and result.get("final_damage", 0) > 0
            and game_state.markers.has(attacker.player_id, "INVISIBLE")
            and game_state.markers.has_relation(
                attacker.player_id, "ENGAGED_WITH", target.player_id)):
        game_state.markers.on_engaged_melee_attack_by_invisible(
            attacker.player_id, target.player_id)
        result["stealth_suppressed"] = True

    # ---- 愿负世：被攻击时积累神性 ----
    if target.talent and hasattr(target.talent, 'on_being_attacked') and attacker:
        is_limited = False
        if attacker.talent and hasattr(attacker.talent, 'uses_remaining'):
            is_limited = True
        target.talent.on_being_attacked(attacker, weapon, is_limited)

    return result


def resolve_area_damage(attacker, weapon, location, game_state,
                        ignore_element=False, damage_multiplier=1.0,
                        bonus_damage=0.0, exclude_self=True):
    """范围伤害结算"""
    results = []
    targets = game_state.players_at_location(location)
    for t in targets:
        if exclude_self and t.player_id == attacker.player_id:
            continue
        if not t.is_alive():
            continue
        r = resolve_damage(
            attacker, t, weapon, game_state,
            ignore_element=ignore_element,
            damage_multiplier=damage_multiplier,
            bonus_damage=bonus_damage,
        )
        results.append({"target": t, "result": r})
    return results


def _select_armor_target(target, target_layer, target_armor_attr):
    """选择被攻击的护甲"""
    if target_layer is not None and target_armor_attr is not None:
        # 校验：外层未全部击破时，不可选择攻击内层
        if target_layer == ArmorLayer.INNER:
            outer_active = target.armor.get_active(ArmorLayer.OUTER)
            if outer_active:
                # 外层仍有存活护甲，不允许直接攻击内层，回退到自动选择
                pass  # fall through to auto-selection below
            else:
                piece = target.armor.get_piece(target_layer, target_armor_attr)
                if piece:
                    return piece
        else:
            piece = target.armor.get_piece(target_layer, target_armor_attr)
            if piece:
                return piece

    # 自动选择：外层优先
    outer = target.armor.get_active(ArmorLayer.OUTER)
    if outer:
        outer.sort(key=lambda a: a.priority, reverse=True)
        return outer[0]
    inner = target.armor.get_active(ArmorLayer.INNER)
    if inner:
        return inner[0]
    return None

def _redirect_overflow_damage(target, broken_armor, overflow,
                                 weapon_attribute, result,
                                 ignore_last_inner_absorb=False):
    """
    将溢出伤害重定向到其他护甲。
    遵循用户指定的规则：
    1. 如果破碎的是外层护甲：先检查其他外层护甲，再检查内层护甲
    2. 如果破碎的是内层护甲：只检查其他内层护甲
    3. 随机选择一个不免疫武器属性的护甲
    4. 如果没有符合条件的护甲，则伤害转移到生命值
    """
    from models.equipment import ArmorLayer
    import random

    # broken_armor 已被标记为 is_broken，get_active() 不会包含它，
    # 直接使用 broken_armor.layer 属性判断所属层
    is_outer = (broken_armor.layer == ArmorLayer.OUTER)
    is_inner = not is_outer

    candidates = []

    if is_outer:
        outer_list = target.armor.get_active(ArmorLayer.OUTER)
        for armor in outer_list:
            candidates.append((armor, "外层"))
        if not candidates:
            inner_list = target.armor.get_active(ArmorLayer.INNER)
            for armor in inner_list:
                candidates.append((armor, "内层"))
    else:
        inner_list = target.armor.get_active(ArmorLayer.INNER)
        for armor in inner_list:
            candidates.append((armor, "内层"))

    effective_candidates = []
    if weapon_attribute is None:
        effective_candidates = candidates
    else:
        for armor, layer_type in candidates:
            if is_effective(weapon_attribute, armor.attribute):
                effective_candidates.append((armor, layer_type))

    if not effective_candidates:
        if candidates:
            # 有护甲但全部免疫 → 伤害被克制，无效化
            immune_names = "、".join(f"「{a.name}({a.attribute.value})」" for a, _ in candidates)
            result["details"].append(
                f"溢出伤害 {overflow} 被剩余护甲 {immune_names} 的属性克制，无效！"
            )
            return 0
        else:
            # 确实没有护甲了 → 伤害转移到生命
            result["details"].append(f"溢出伤害 {overflow} 没有护甲可承受，直接作用到生命")
            return overflow

    selected_armor, selected_layer = random.choice(effective_candidates)

    result["details"].append(
        f"溢出伤害 {overflow} 重定向到 {selected_layer}护甲「{selected_armor.name}」"
    )

    return _apply_damage_to_armor(
        target, selected_armor, overflow,
        ignore_last_inner_absorb, result, weapon_attribute
    )

def _apply_damage_to_armor(target, armor_piece, damage,
                           ignore_last_inner_absorb, result,
                           weapon_attribute=None):
    """
    将伤害施加到护甲上（修复版：支持溢出伤害重定向到其他护甲）。
    返回剩余伤害（溢出到生命的部分）。
    weapon_attribute: 武器属性（Attribute枚举），None表示无视属性克制
    """
    result["armor_hit"] = armor_piece.name

    if damage >= armor_piece.current_hp:
        overflow = damage - armor_piece.current_hp
        armor_piece.current_hp = 0
        armor_piece.is_broken = True
        result["armor_broken"] = True

        armor_destroyed_text = prompt_manager.get_prompt(
            "combat", "armor_destroyed_detailed",
            default="护甲「{armor_name}」被击破！溢出：{overflow}"
        )
        result["details"].append(armor_destroyed_text.format(
            armor_name=armor_piece.name,
            overflow=overflow
        ))

        is_last = target.armor.is_last_inner(armor_piece)
        if is_last and not ignore_last_inner_absorb:
            result["details"].append("最后内层护甲吸收所有溢出")
            return 0
        else:
            return _redirect_overflow_damage(
                target, armor_piece, overflow,
                weapon_attribute, result,
                ignore_last_inner_absorb
            )
    else:
        armor_piece.current_hp -= damage
        armor_damaged_text = prompt_manager.get_prompt(
            "combat", "armor_damaged",
            default="护甲「{armor_name}」剩余 {current_hp}/{max_hp}"
        )
        result["details"].append(armor_damaged_text.format(
            armor_name=armor_piece.name,
            current_hp=armor_piece.current_hp,
            max_hp=armor_piece.max_hp
        ))
        return 0


def _talent_death_check(target, attacker, game_state):
    """
    天赋死亡检查。
    优先级：1. 免死效果 → 2. 复活效果（死者苏生）
    返回 True 表示死亡被阻止。
    """
    if target.talent:
        death_result = target.talent.on_death_check(target, attacker)
        if death_result and death_result.get("prevent_death"):
            target.hp = death_result.get("new_hp", 0.5)
            return True

    if game_state:
        for pid in game_state.player_order:
            p = game_state.get_player(pid)
            if not p or not p.talent or p.player_id == target.player_id:
                continue
            death_result = p.talent.on_death_check(target, attacker)
            if death_result and death_result.get("prevent_death"):
                target.hp = death_result.get("new_hp", 0.5)
                return True

    return False