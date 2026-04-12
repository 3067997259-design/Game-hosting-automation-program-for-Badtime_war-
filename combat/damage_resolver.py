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
    检查目标是否拥有能免疫电流武器眩晕的护甲（陶瓷护甲 immune_electric tag）。
    现在陶瓷护甲只抵抗震荡，不再免疫电磁步枪的伤害。
    返回：(bool, ArmorPiece or None)
    """
    all_active = target.armor.get_all_active()
    for armor in all_active:
        if "immune_electric" in armor.special_tags and not armor.is_broken:
            return True, armor
    return False, None

def _check_love_wish_immunity(attacker, target, game_state):
    """检查目标是否是G5持有者且攻击者持有爱愿，使伤害无效"""
    if not attacker or not target or not game_state:
        return False
    if not target.talent or not hasattr(target.talent, 'has_love_wish'):
        return False
    return target.talent.has_love_wish(attacker.player_id)


def _check_hoshino_color_10(target, result):
    """星野色彩≥10：本体HP受伤未死 → 不眩晕 + 恢复破碎护甲 + 自我怀疑。
    在 HP 扣减后、眩晕/死亡判定前调用。
    返回 True 表示触发了色彩10效果（调用方应跳过眩晕判定）。"""
    if not target.talent or not hasattr(target.talent, '_check_color_10_on_hp_damage'):
        return False
    if getattr(target, '_mythland_talent_suppressed', False):
        return False
    hp_damage = result.get("hp_damage", 0)
    if hp_damage <= 0:
        return False  # 本体HP未受伤，不触发
    return target.talent._check_color_10_on_hp_damage(target, hp_damage)


def _record_hoshino_armor_break(target, armor_name):
    """记录星野穿戴过的已破碎护甲名（供色彩10恢复用）"""
    if (target.talent and hasattr(target.talent, 'broken_armors_history')
            and not getattr(target, '_mythland_talent_suppressed', False)):
        target.talent.broken_armors_history.add(armor_name)


def _resolve_weaponless_damage(attacker, target, game_state, result,
                                raw_damage, damage_attribute_str,
                                is_talent_attack=False,
                                is_love_poem=False):
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
    if _check_love_wish_immunity(attacker, target, game_state):
        result["final_damage"] = 0
        result["success"] = False
        result["reason"] = "爱愿免疫"
        result["details"].append(f"💝 「爱愿」生效：{attacker.name} 无法对 {target.name} 造成伤害")
        return result
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
    # ---- 星野架盾：正面伤害过滤（无武器路径） ----
    if (target.talent and hasattr(target.talent, 'shield_mode')
        and target.talent.shield_mode == "架盾"
        and not getattr(target, '_mythland_talent_suppressed', False)):
        talent = target.talent
        attacker_id = attacker.player_id if attacker else None
        if attacker_id and talent.is_front(attacker_id):
            # is_love_poem 由调用方通过参数传入
            if not is_love_poem:
                threshold = talent.shield_snapshot_hp
                if raw <= threshold:
                    result["final_damage"] = 0
                    result["success"] = False
                    result["reason"] = "架盾正面伤害过滤"
                    shield_immune = prompt_manager.get_prompt(
                        "talent", "g7hoshino.shield_filter_immune",
                        raw=raw, threshold=threshold)
                    result["details"].append(shield_immune)
                    return result
                else:
                    talent.iron_horus_hp = max(0, talent.iron_horus_hp - 1)
                    result["final_damage"] = 0
                    result["success"] = False
                    result["reason"] = "架盾正面伤害过滤（溢出）"
                    shield_overflow = prompt_manager.get_prompt(
                        "talent", "g7hoshino.shield_filter_overflow",
                        raw=raw, threshold=threshold, remaining_hp=talent.iron_horus_hp)
                    result["details"].append(shield_overflow)
                    if talent.iron_horus_hp <= 0:
                        player_obj = game_state.get_player(talent.player_id) if game_state else None
                        if player_obj:
                            talent._end_shield_mode(player_obj)
                        result["details"].append(
                            prompt_manager.get_prompt("talent", "g7hoshino.shield_horus_zero"))
                    return result

    # ---- 星野持盾：铁之荷鲁斯伤害减免（增强版） ----
    if (target.talent and hasattr(target.talent, 'shield_mode')
        and target.talent.shield_mode == "持盾"
        and not getattr(target, '_mythland_talent_suppressed', False)):
        talent = target.talent
        attacker_id = attacker.player_id if attacker else None
        is_found_or_locked = False
        if attacker_id and game_state:
            is_found_or_locked = (
                game_state.markers.has_relation(target.player_id, "ENGAGED_WITH", attacker_id)
                or game_state.markers.has_relation(target.player_id, "LOCKED_ON", attacker_id)
            )
        # 持盾保护：所有入射伤害降低50%（包括范围攻击如天星）
        if talent.iron_horus_hp > 0:
            raw = raw * 0.5
            absorbed = min(raw, talent.iron_horus_hp)
            talent.iron_horus_hp -= absorbed
            raw -= absorbed
            hold_absorb = prompt_manager.get_prompt(
                "talent", "g7hoshino.hold_absorb",
                absorbed=absorbed, remaining_hp=talent.iron_horus_hp)
            result["details"].append(hold_absorb)
            if talent.iron_horus_hp <= 0:
                # 破损时吸收所有溢出伤害
                raw = 0
                result["details"].append(
                    prompt_manager.get_prompt("talent", "g7hoshino.hold_broken_absorb"))
                player_obj = game_state.get_player(talent.player_id) if game_state else None
                if player_obj:
                    talent._end_shield_mode(player_obj)
            if raw <= 0:
                result["final_damage"] = 0
                result["success"] = False
                result["reason"] = "持盾伤害减免"
                return result

    # ---- 星野被动保护：非持盾/架盾时，铁之荷鲁斯作为无属性外层护甲 ----
    if (target.talent and hasattr(target.talent, 'iron_horus_hp')
        and getattr(target.talent, 'shield_mode', None) is None  # 非持盾/架盾
        and target.talent.iron_horus_hp > 0
        and getattr(target.talent, 'fusion_shield_done', False)  # 已完成融合
        and not getattr(target, '_mythland_talent_suppressed', False)):
        talent = target.talent
        # 无属性：所有伤害类型都有效，不做属性克制检查
        # 被动模式下吸收伤害，破碎时吸收所有溢出（与持盾一致）
        absorbed = min(raw, talent.iron_horus_hp)
        talent.iron_horus_hp -= absorbed
        raw -= absorbed
        result["details"].append(
            prompt_manager.get_prompt("talent", "g7hoshino.passive_absorb",
                absorbed=absorbed, remaining_hp=talent.iron_horus_hp))
        if talent.iron_horus_hp <= 0:
            # 破碎时吸收所有溢出伤害（与持盾一致）
            raw = 0
            result["details"].append(
                prompt_manager.get_prompt("talent", "g7hoshino.passive_broken"))
        # 被动模式下破碎时也吸收所有溢出伤害（与持盾行为一致）
        if raw <= 0:
            result["final_damage"] = 0
            result["success"] = False
            result["reason"] = "铁之荷鲁斯被动保护"
            return result
        # raw > 0 时继续走后续的天赋减伤和护甲结算

    # ---- 天赋受伤减免（如火萤IV型 -50%）----
    if (target.talent and hasattr(target.talent, 'modify_incoming_damage')
        and not getattr(target, '_mythland_talent_suppressed', False)):
        original_raw = raw
        raw = target.talent.modify_incoming_damage(target, attacker, None, raw)
        if raw != original_raw:
            damage_reduced_text = prompt_manager.get_prompt(
                "combat", "damage_reduced",
                default="受伤减免后：{damage}"
            )
            result["details"].append(damage_reduced_text.format(damage=raw))

    # ---- 全息影像：目标在影像内额外+hologram_bonus ----
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
        if (target.talent and hasattr(target.talent, 'receive_damage_to_temp_hp')
        and not getattr(target, '_mythland_talent_suppressed', False)):
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

    # ---- 星野色彩10：本体HP受伤未死 → 不眩晕 + 恢复护甲 + 自我怀疑 ----
    color_10_triggered = _check_hoshino_color_10(target, result)

    # ---- 石化被攻击自动解除 ----
    # README: 被攻击时石化自动解除（+0.5伤害）
    if target.is_alive() and getattr(target, 'is_petrified', False):
        # 检查涟漪增强豁免：天星持有者的 ripple_petrify_lock 为 True 时跳过
        skip_auto_remove = False
        # 查找是否有天星持有者设置了 ripple_petrify_lock
        if game_state:
            for pid in game_state.player_order:
                p = game_state.get_player(pid)
                if p and p.talent and getattr(p.talent, 'ripple_petrify_lock', False):
                    skip_auto_remove = True
                    break

        if not skip_auto_remove:
            target.is_petrified = False
            if game_state:
                game_state.markers.on_petrify_recover(target.player_id)
            # 解除石化额外0.5伤害（先让临时HP吸收）
            petrify_remaining = 0.5
            if (target.talent and hasattr(target.talent, 'receive_damage_to_temp_hp')
                    and not getattr(target, '_mythland_talent_suppressed', False)):
                petrify_remaining = target.talent.receive_damage_to_temp_hp(petrify_remaining)
            if petrify_remaining > 0:
                target.hp = round(max(0, target.hp - petrify_remaining), 2)
            absorbed = round(0.5 - petrify_remaining, 2)
            actual = round(0.5 - absorbed, 2)
            if absorbed > 0:
                result["details"].append(f"🗿→✨ {target.name} 石化被攻击自动解除！额外受{actual}伤害（临时HP吸收{absorbed}） → HP: {target.hp}")
            else:
                result["details"].append(f"🗿→✨ {target.name} 石化被攻击自动解除！额外受0.5伤害 → HP: {target.hp}")
            result["target_hp"] = target.hp

    if target.hp <= 0:
        if getattr(target, '_mythland_talent_suppressed', False):
            prevented = False
        else:
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
            if (attacker and attacker.talent and hasattr(attacker.talent, 'on_kill')
                    and not getattr(attacker, '_cutaway_suppress_attacker_hooks', False)
                    and not getattr(attacker, '_mythland_talent_suppressed', False)):
                attacker.talent.on_kill(attacker, target)
            if target.talent and hasattr(target.talent, 'on_player_death_check'):
                target.talent.on_player_death_check(target)

    elif target.hp <= 0.5 and not target.is_stunned and not color_10_triggered:
        prevent = False
        if (target.talent and hasattr(target.talent, 'prevent_stun')
                and not getattr(target, '_mythland_talent_suppressed', False)):
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

    # ---- 愿负世：被攻击时积累火种 ----
    if (target.talent and hasattr(target.talent, 'on_being_attacked') and attacker
            and not getattr(target, '_mythland_talent_suppressed', False)):
        is_limited = is_talent_attack and _is_limited_use_talent(attacker.talent)
        target.talent.on_being_attacked(attacker, None, is_limited)

    # 插入式笑话中借用来源玩家执行时跳过（不应破除来源玩家的爱愿）
    if (attacker and attacker.talent and hasattr(attacker.talent, 'break_love_wish')
            and not getattr(attacker, '_cutaway_suppress_attacker_hooks', False)):
        attacker.talent.break_love_wish(target.player_id)

    return result


def resolve_damage(attacker, target, weapon, game_state,
                   target_layer=None, target_armor_attr=None,
                   ignore_element=False, damage_multiplier=1.0,
                   bonus_damage=0.0,
                   ignore_counter=False,
                   ignore_last_inner_absorb=False,
                   raw_damage_override=None,
                   damage_attribute_override=None,
                   is_talent_attack=False,
                   is_love_poem=False):
    """
    完整伤害结算。
    新增参数（Phase 4）：
      ignore_counter: 无视属性克制（一刀缭断）
      ignore_last_inner_absorb: 最后内层不吸收溢出（一刀缭断）
    新增参数（Phase 5 涟漪）：
      raw_damage_override: 无武器时的原始伤害值
      damage_attribute_override: 无武器时的伤害属性（字符串）
      is_love_poem: 是否为爱与记忆之诗伤害（穿透架盾）
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
    # 爱愿免疫：持有爱愿的攻击者无法对G5持有者造成伤害
    if _check_love_wish_immunity(attacker, target, game_state):
        result["final_damage"] = 0
        result["success"] = False
        result["reason"] = "爱愿免疫"
        result["details"].append(f"💝 「爱愿」生效：{attacker.name} 无法对 {target.name} 造成伤害")
        return result

    # ======== 无武器模式（爱与记忆之诗等外部伤害源） ========
    if weapon is None:
        # 六爻·元亨利贞：免疫伤害（无武器路径）
        dmg_attr_str = damage_attribute_override or "普通"
        if (target.talent and hasattr(target.talent, 'is_immune_to_damage')
                and not getattr(target, '_mythland_talent_suppressed', False)):
            if target.talent.is_immune_to_damage(dmg_attr_str):
                result["final_damage"] = 0
                result["success"] = False
                result["reason"] = "元亨利贞免疫"
                result["details"].append(f"☯️ 「元亨利贞」免疫了此次伤害！")
                return result

        # ---- 天赋：修改输出伤害（如火萤IV型 +100%）----
        effective_raw = raw_damage_override or 1.0
        if attacker and attacker.talent and hasattr(attacker.talent, 'modify_outgoing_damage'):
            if not getattr(attacker, '_mythland_talent_suppressed', False):
                mod = attacker.talent.modify_outgoing_damage(attacker, target, None, effective_raw)
                if mod:
                    if "damage_multiplier_override" in mod:
                        effective_raw = effective_raw * mod["damage_multiplier_override"]
                    if "bonus_damage" in mod:
                        effective_raw += mod["bonus_damage"]

        return _resolve_weaponless_damage(
            attacker, target, game_state, result,
            effective_raw,
            dmg_attr_str,
            is_talent_attack=is_talent_attack,
            is_love_poem=is_love_poem,
        )

    # ======== 六爻·元亨利贞：免疫伤害（有武器路径） ========
    if (target.talent and hasattr(target.talent, 'is_immune_to_damage')
        and not getattr(target, '_mythland_talent_suppressed', False)):
        dmg_attr = damage_attribute_override or (getattr(weapon, 'attribute', '普通') if weapon else '普通')
        if target.talent.is_immune_to_damage(dmg_attr):
            result["final_damage"] = 0
            result["success"] = False
            result["reason"] = "元亨利贞免疫"
            result["details"].append(f"☯️ 「元亨利贞」免疫了此次伤害！")
            return result

    # ======== 陶瓷护甲免疫电流眩晕检查 ========
    # 陶瓷护甲免疫电流武器的眩晕效果，但不免疫伤害
    # 伤害正常走属性三角结算（科技 vs 普通 = 有效）

    electric_stun_immune = False
    if weapon.is_electric:
        immune, immune_armor = _check_electric_immunity(target)
        if immune and immune_armor:
            electric_stun_immune = True
            result["details"].append(
                f"🛡️ {target.name} 的「{immune_armor.name}」绝缘：免疫电流眩晕，但伤害正常结算"
            )

    # ---- 天赋：修改输出伤害 ----
    if attacker and attacker.talent and not getattr(attacker, '_mythland_talent_suppressed', False):
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

    # ---- 星野架盾：正面伤害过滤 ----
    if (target.talent and hasattr(target.talent, 'shield_mode')
        and target.talent.shield_mode == "架盾"
        and not getattr(target, '_mythland_talent_suppressed', False)):
        talent = target.talent
        # 检查攻击者是否在正面
        attacker_id = attacker.player_id if attacker else None
        if attacker_id and talent.is_front(attacker_id):
            # 免疫除爱与记忆之诗外所有伤害低于快照护甲值的正面伤害
            # is_love_poem 由调用方通过参数传入
            if not is_love_poem:
                threshold = talent.shield_snapshot_hp
                if raw <= threshold:
                    # 完全免疫，铁之荷鲁斯不受伤害
                    result["final_damage"] = 0
                    result["success"] = False
                    result["reason"] = "架盾正面伤害过滤"
                    shield_immune = prompt_manager.get_prompt(
                        "talent", "g7hoshino.shield_filter_immune",
                        raw=raw, threshold=threshold)
                    result["details"].append(shield_immune)
                    return result
                else:
                    # 溢出：荷鲁斯损耗1点护甲值，无效化剩余
                    talent.iron_horus_hp = max(0, talent.iron_horus_hp - 1)
                    result["final_damage"] = 0
                    result["success"] = False
                    result["reason"] = "架盾正面伤害过滤（溢出）"
                    shield_overflow = prompt_manager.get_prompt(
                        "talent", "g7hoshino.shield_filter_overflow",
                        raw=raw, threshold=threshold, remaining_hp=talent.iron_horus_hp)
                    result["details"].append(shield_overflow)
                    if talent.iron_horus_hp <= 0:
                        player_obj = game_state.get_player(talent.player_id) if game_state else None
                        if player_obj:
                            talent._end_shield_mode(player_obj)
                        result["details"].append(
                            prompt_manager.get_prompt("talent", "g7hoshino.shield_horus_zero"))
                    return result

    # ---- 警察保护阈值减免（非AOE） ----
    if weapon and weapon.weapon_range != WeaponRange.AREA and game_state:
        pe = getattr(game_state, 'police_engine', None)
        if pe:
            threshold = pe.get_protection_threshold(target.player_id)
            if threshold > 0 and raw <= threshold:
                result["details"].append(f"🚔 警察保护：伤害 {raw} ≤ 阈值 {threshold}，完全无效化")
                result["final_damage"] = 0
                result["success"] = False
                result["reason"] = "警察保护无效化"
                return result
            elif threshold > 0 and raw > threshold:
                absorbed = threshold
                raw -= absorbed
                result["details"].append(f"🚔 警察保护：吸收 {absorbed}，剩余 {raw}")

    # ---- 星野持盾：铁之荷鲁斯伤害减免（增强版） ----
    if (target.talent and hasattr(target.talent, 'shield_mode')
        and target.talent.shield_mode == "持盾"
        and not getattr(target, '_mythland_talent_suppressed', False)):
        talent = target.talent
        attacker_id = attacker.player_id if attacker else None
        is_found_or_locked = False
        if attacker_id and game_state:
            is_found_or_locked = (
                game_state.markers.has_relation(target.player_id, "ENGAGED_WITH", attacker_id)
                or game_state.markers.has_relation(target.player_id, "LOCKED_ON", attacker_id)
            )
        if talent.iron_horus_hp > 0:
            raw = raw * 0.5  # 持盾减伤50%
            absorbed = min(raw, talent.iron_horus_hp)
            talent.iron_horus_hp -= absorbed
            raw -= absorbed
            hold_absorb = prompt_manager.get_prompt(
                "talent", "g7hoshino.hold_absorb",
                absorbed=absorbed, remaining_hp=talent.iron_horus_hp)
            result["details"].append(hold_absorb)
            if talent.iron_horus_hp <= 0:
                raw = 0
                result["details"].append(
                    prompt_manager.get_prompt("talent", "g7hoshino.hold_broken_absorb"))
                player_obj = game_state.get_player(talent.player_id) if game_state else None
                if player_obj:
                    talent._end_shield_mode(player_obj)
            if raw <= 0:
                result["final_damage"] = 0
                result["success"] = False
                result["reason"] = "持盾伤害减免"
                return result

    # ---- 星野被动保护：非持盾/架盾时，铁之荷鲁斯作为无属性外层护甲 ----
    if (target.talent and hasattr(target.talent, 'iron_horus_hp')
        and getattr(target.talent, 'shield_mode', None) is None  # 非持盾/架盾
        and target.talent.iron_horus_hp > 0
        and getattr(target.talent, 'fusion_shield_done', False)  # 已完成融合
        and not getattr(target, '_mythland_talent_suppressed', False)):
        talent = target.talent
        # 无属性：所有伤害类型都有效，不做属性克制检查
        # 被动模式下吸收伤害，破碎时吸收所有溢出（与持盾一致）
        absorbed = min(raw, talent.iron_horus_hp)
        talent.iron_horus_hp -= absorbed
        raw -= absorbed
        result["details"].append(
            prompt_manager.get_prompt("talent", "g7hoshino.passive_absorb",
                absorbed=absorbed, remaining_hp=talent.iron_horus_hp))
        if talent.iron_horus_hp <= 0:
            raw = 0  # 破碎时吸收所有溢出
            result["details"].append(
                prompt_manager.get_prompt("talent", "g7hoshino.passive_broken"))
        # 被动模式下破碎时也吸收所有溢出伤害（与持盾行为一致）
        if raw <= 0:
            result["final_damage"] = 0
            result["success"] = False
            result["reason"] = "铁之荷鲁斯被动保护"
            return result
        # raw > 0 时继续走后续的天赋减伤和护甲结算

    # ---- 萤火受伤减免 ----
    if (target.talent and hasattr(target.talent, 'modify_incoming_damage')
        and not getattr(target, '_mythland_talent_suppressed', False)):
        raw = target.talent.modify_incoming_damage(target, attacker, weapon, raw)
        if raw != result["raw_damage"]:
            damage_reduced_text = prompt_manager.get_prompt(
                "combat", "damage_reduced",
                default="受伤减免后：{damage}"
            )
            result["details"].append(damage_reduced_text.format(damage=raw))

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
            None if (ignore_counter or ignore_element) else weapon.attribute
        )

    # ---- 全息影像：额外无视属性克制伤害（护甲结算后独立施加） ----
    if hologram_bonus > 0:
        # 无视属性克制：直接对最外层护甲造成伤害
        hologram_remaining = hologram_bonus
        hologram_armor = _select_armor_target(target, None, None)
        if hologram_armor is not None:
            hologram_remaining = _apply_damage_to_armor(
                target, hologram_armor, hologram_remaining,
                False, result,
                None  # weapon_attribute=None → 无视属性克制
            )
        remaining += hologram_remaining  # 溢出伤害加到总剩余中

    if remaining > 0:
        if (target.talent and hasattr(target.talent, 'receive_damage_to_temp_hp')
        and not getattr(target, '_mythland_talent_suppressed', False)):
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
    # （必须在色彩10检查之前，因为色彩10会清除 is_stunned）
    pre_attack_stunned = getattr(target, 'is_stunned', False)
    pre_attack_shocked = getattr(target, 'is_shocked', False)

    # ---- 星野色彩10：本体HP受伤未死 → 不眩晕 + 恢复护甲 + 自我怀疑 ----
    color_10_triggered = _check_hoshino_color_10(target, result)

    # ---- 石化被攻击自动解除 ----
    # README: 被攻击时石化自动解除（+0.5伤害）
    if target.is_alive() and getattr(target, 'is_petrified', False):
        # 检查涟漪增强豁免：天星持有者的 ripple_petrify_lock 为 True 时跳过
        skip_auto_remove = False
        # 查找是否有天星持有者设置了 ripple_petrify_lock
        if game_state:
            for pid in game_state.player_order:
                p = game_state.get_player(pid)
                if p and p.talent and getattr(p.talent, 'ripple_petrify_lock', False):
                    skip_auto_remove = True
                    break

        if not skip_auto_remove:
            target.is_petrified = False
            if game_state:
                game_state.markers.on_petrify_recover(target.player_id)
            # 解除石化额外0.5伤害（先让临时HP吸收）
            petrify_remaining = 0.5
            if (target.talent and hasattr(target.talent, 'receive_damage_to_temp_hp')
                    and not getattr(target, '_mythland_talent_suppressed', False)):
                petrify_remaining = target.talent.receive_damage_to_temp_hp(petrify_remaining)
            if petrify_remaining > 0:
                target.hp = round(max(0, target.hp - petrify_remaining), 2)
            absorbed = round(0.5 - petrify_remaining, 2)
            actual = round(0.5 - absorbed, 2)
            if absorbed > 0:
                result["details"].append(f"🗿→✨ {target.name} 石化被攻击自动解除！额外受{actual}伤害（临时HP吸收{absorbed}） → HP: {target.hp}")
            else:
                result["details"].append(f"🗿→✨ {target.name} 石化被攻击自动解除！额外受0.5伤害 → HP: {target.hp}")
            result["target_hp"] = target.hp

    # ---- 第6步：眩晕/死亡判定 ----
    if target.hp <= 0:
        if getattr(target, '_mythland_talent_suppressed', False):
            prevented = False
        else:
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
            if (attacker and attacker.talent and hasattr(attacker.talent, 'on_kill')
                    and not getattr(attacker, '_cutaway_suppress_attacker_hooks', False)
                    and not getattr(attacker, '_mythland_talent_suppressed', False)):
                attacker.talent.on_kill(attacker, target)
            if target.talent and hasattr(target.talent, 'on_player_death_check'):
                target.talent.on_player_death_check(target)

    elif target.hp <= 0.5 and not target.is_stunned and not color_10_triggered:
        prevent = False
        if (target.talent and hasattr(target.talent, 'prevent_stun')
                and not getattr(target, '_mythland_talent_suppressed', False)):
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
                default="🔥 {target_name} 还没有倒下"
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
            prevent_shock = electric_stun_immune
            if (not prevent_shock
                    and target.talent and hasattr(target.talent, 'prevent_stun')
                    and not getattr(target, '_mythland_talent_suppressed', False)):
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
    # 插入式笑话中借用来源玩家执行时跳过（不应暴露来源玩家）
    if (attacker and game_state
                and not getattr(attacker, '_cutaway_skip_stealth_suppress', False)
                and not (attacker.talent
                        and hasattr(attacker.talent, 'stealth_on_zero_kills')
                        and attacker.talent.stealth_on_zero_kills
                        and getattr(attacker, 'kill_count', 0) == 0)
            and weapon.weapon_range == WeaponRange.MELEE
            and result["success"] and result.get("final_damage", 0) > 0
            and game_state.markers.has(attacker.player_id, "INVISIBLE")
            and game_state.markers.has_relation(
                attacker.player_id, "ENGAGED_WITH", target.player_id)):
        game_state.markers.on_engaged_melee_attack_by_invisible(
            attacker.player_id, target.player_id)
        result["stealth_suppressed"] = True

    # ---- 愿负世：被攻击时积累火种 ----
    if (target.talent and hasattr(target.talent, 'on_being_attacked') and attacker
            and not getattr(target, '_mythland_talent_suppressed', False)):
        is_limited = is_talent_attack and _is_limited_use_talent(attacker.talent)
        target.talent.on_being_attacked(attacker, weapon, is_limited)

    # 插入式笑话中借用来源玩家执行时跳过（不应破除来源玩家的爱愿）
    if (attacker and attacker.talent and hasattr(attacker.talent, 'break_love_wish')
            and not getattr(attacker, '_cutaway_suppress_attacker_hooks', False)):
        attacker.talent.break_love_wish(target.player_id)

    # ---- 剪刀手一突：攻击回盾（每2次成功攻击，第2次若对护甲造成伤害则回盾） ----
    # 所有成功攻击都推进计数器；只有偶数次且命中护甲时才触发回盾效果
    # 不在无武器路径加是因为这个天赋应该没有造成无武器伤害的路径
    if (attacker and attacker.talent
            and hasattr(attacker.talent, 'on_attack_shield_recovery')
            and not getattr(attacker, '_mythland_talent_suppressed', False)
            and not getattr(attacker, '_cutaway_suppress_attacker_hooks', False)
            and result["success"]):
        # 每次成功攻击都递增计数器
        attacker.talent.attack_count += 1
        # 偶数次攻击时检查是否命中护甲，命中则触发回盾
        if attacker.talent.attack_count % 2 == 0:
            if result.get("armor_hit"):
                armor_name = result["armor_hit"]
                # 找到被命中的护甲对象以获取属性信息
                hit_piece = None
                for layer in [ArmorLayer.OUTER, ArmorLayer.INNER]:
                    for piece in target.armor._get_layer_list(layer):
                        if piece.name == armor_name:
                            hit_piece = piece
                            break
                    if hit_piece:
                        break
                if hit_piece:
                    attacker.talent.on_attack_shield_recovery(attacker, hit_piece)

    return result


def resolve_area_damage(attacker, weapon, location, game_state,
                        ignore_element=False, damage_multiplier=1.0,
                        bonus_damage=0.0, exclude_self=True):
    """范围伤害结算"""
    # V1.92: AOE天赋加成（如救世主状态下的额外伤害）
    aoe_bonus = 0.0
    if attacker and attacker.talent and hasattr(attacker.talent, 'modify_aoe_damage'):
        mod = attacker.talent.modify_aoe_damage(
            attacker, None, weapon,
            weapon.get_effective_damage() if weapon else 0)
        if mod and "bonus_damage" in mod:
            aoe_bonus = mod["bonus_damage"]

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
            bonus_damage=bonus_damage + aoe_bonus,
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

def resolve_location_damage(attacker, location, game_state,
                            raw_damage=1.0, ignore_counter=True,
                            exclude_self=True,
                            damage_attribute_override=None,
                            is_talent_attack=False):
    """对指定地点的所有单位（玩家 + 警察 + 未来的其他单位）造成伤害。

    参数：
      attacker: 攻击者玩家对象（可为None）
      location: 目标地点
      game_state: 游戏状态
      raw_damage: 原始伤害值
      ignore_counter: 是否无视属性克制
      exclude_self: 是否排除攻击者自身
      damage_attribute_override: 伤害属性字符串（如"无视属性克制"），
                                 传递给 resolve_damage 的 damage_attribute_override 参数
    """
    results = {"players": [], "police": [], "other": []}

    # 1. 玩家
    for t in game_state.players_at_location(location):
        if exclude_self and attacker and t.player_id == attacker.player_id:
            continue
        if not t.is_alive():
            continue
        r = resolve_damage(
            attacker, t, weapon=None, game_state=game_state,
            raw_damage_override=raw_damage,
            ignore_counter=ignore_counter,
            damage_attribute_override=damage_attribute_override,
            is_talent_attack=is_talent_attack,
        )
        results["players"].append({"target": t, "result": r})

    # 2. 警察
    pe = getattr(game_state, 'police_engine', None)
    if pe and hasattr(game_state, 'police') and game_state.police:
        for unit in game_state.police.units_at(location):
            if not unit.is_alive():
                continue
            pe._resolve_attack_on_police(
                weapon=None, unit=unit,
                raw_damage_override=raw_damage,
                ignore_counter=ignore_counter,
                attacker=attacker,
            )
            unit.last_attacker_id = attacker.player_id if attacker else None
            results["police"].append(unit)
        game_state.police.check_all_dead()

    # 3. 未来扩展点：其他非玩家单位
    # for npc in game_state.npcs_at_location(location): ...


    return results

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

        # 记录星野穿戴过的已破碎护甲名（供色彩10恢复用）
        _record_hoshino_armor_break(target, armor_piece.name)

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
    # Terror 状态：无视任何条件死亡（包括复活、免死等）
    if (target.talent and hasattr(target.talent, 'is_terror')
            and target.talent.is_terror):
        return False

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

def _is_limited_use_talent(talent):
    """判断天赋是否属于「限定使用次数」（README 12 定义）

    仅覆盖会发起攻击型天赋动作的类型：
      - uses_remaining: 一刀缭断、天星等
      - charges + max_charges: 六爻（充能制）
    """
    if talent is None:
        return False
    # 一刀缭断、天星、你给路打油、神话之外等
    if hasattr(talent, 'uses_remaining'):
        return True
    # 六爻（充能制）
    if hasattr(talent, 'charges') and hasattr(talent, 'max_charges'):
        return True
    return False

def notify_positive_talent_effect(source_player, target_player):
    """当正面天赋效果作用于目标时，通知目标天赋（愿负世火种积累）"""
    if source_player is None or target_player is None:
        return
    if source_player.player_id == target_player.player_id:
        return  # 自己对自己不算
    if not target_player.talent or not hasattr(target_player.talent, 'on_positive_talent_used'):
        return
    is_limited = _is_limited_use_talent(source_player.talent)
    target_player.talent.on_positive_talent_used(source_player, is_limited)

def resolve_terror_damage(attacker, target, game_state, raw_damage=1.0):
    """
    Terror 全图伤害结算。
    走正常护甲→临时HP→HP管线，但：
    - 不享受加成和减伤（跳过 modify_outgoing/incoming_damage、hologram bonus）
    - 单体保护不过滤（跳过警察保护阈值）
    - 无视属性克制（damage_attr=None）
    - 不无视元亨利贞金身
    - 死亡判定：无视死者苏生和g4人形态免死，不无视救世主免死
    """
    result = {
        "success": False,
        "reason": "",
        "raw_damage": raw_damage,
        "final_damage": 0,
        "armor_hit": None,
        "armor_broken": False,
        "hp_damage": 0,
        "target_hp": target.hp,
        "stunned": False,
        "killed": False,
        "details": [],
    }

    # ---- 元亨利贞金身检查（不无视） ----
    if (target.talent and hasattr(target.talent, 'is_immune_to_damage')
            and not getattr(target, '_mythland_talent_suppressed', False)):
        if target.talent.is_immune_to_damage(None):
            result["final_damage"] = 0
            result["success"] = False
            result["reason"] = "元亨利贞免疫"
            gold_body = prompt_manager.get_prompt("talent", "g7hoshino.terror_gold_body_immune")
            result["details"].append(gold_body)
            return result

    # ---- 不享受加成和减伤：跳过 modify_outgoing_damage、modify_incoming_damage、hologram ----
    # ---- 单体保护不过滤：跳过警察保护阈值 ----

    # ---- 伤害量化 ----
    final_damage = quantize_damage(raw_damage)
    result["final_damage"] = final_damage
    terror_damage = prompt_manager.get_prompt("talent", "g7hoshino.terror_damage_detail",
                                        damage=final_damage)
    result["details"].append(terror_damage)

    remaining = final_damage
    result["success"] = True

    # ---- 护甲结算（无视属性克制：weapon_attribute=None） ----
    armor_piece = _select_armor_target(target, None, None)
    if armor_piece is not None:
        result["details"].append(f"攻击目标护甲：{armor_piece}")
        remaining = _apply_damage_to_armor(
            target, armor_piece, remaining,
            False, result,
            None  # weapon_attribute=None → 无视属性克制，不会被护甲克制
        )

    # ---- 临时生命值结算 ----
    if remaining > 0:
        if (target.talent and hasattr(target.talent, 'receive_damage_to_temp_hp')
                and not getattr(target, '_mythland_talent_suppressed', False)):
            remaining = target.talent.receive_damage_to_temp_hp(remaining)
        if remaining > 0:
            result["hp_damage"] = remaining
            target.hp = round(max(0, target.hp - remaining), 2)
            hp_damage = prompt_manager.get_prompt("talent", "g7hoshino.terror_hp_damage",
                                            damage=remaining,
                                            current_hp=target.hp, max_hp=target.max_hp)
            result["details"].append(hp_damage)

    result["target_hp"] = target.hp

    # ---- 星野色彩10：本体HP受伤未死 → 不眩晕 + 恢复护甲 + 自我怀疑 ----
    color_10_triggered = _check_hoshino_color_10(target, result)

    # ---- 石化被攻击自动解除 ----
    if target.is_alive() and getattr(target, 'is_petrified', False):
        skip_auto_remove = False
        if game_state:
            for pid in game_state.player_order:
                p = game_state.get_player(pid)
                if p and p.talent and getattr(p.talent, 'ripple_petrify_lock', False):
                    skip_auto_remove = True
                    break
        if not skip_auto_remove:
            target.is_petrified = False
            if game_state:
                game_state.markers.on_petrify_recover(target.player_id)
            # 解除石化额外0.5伤害（先让临时HP吸收）
            petrify_remaining = 0.5
            if (target.talent and hasattr(target.talent, 'receive_damage_to_temp_hp')
                    and not getattr(target, '_mythland_talent_suppressed', False)):
                petrify_remaining = target.talent.receive_damage_to_temp_hp(petrify_remaining)
            if petrify_remaining > 0:
                target.hp = round(max(0, target.hp - petrify_remaining), 2)
            absorbed = round(0.5 - petrify_remaining, 2)
            actual = round(0.5 - absorbed, 2)
            if absorbed > 0:
                result["details"].append(f"🗿→✨ {target.name} 石化被攻击自动解除！额外受{actual}伤害（临时HP吸收{absorbed}） → HP: {target.hp}")
            else:
                result["details"].append(f"🗿→✨ {target.name} 石化被攻击自动解除！额外受0.5伤害 → HP: {target.hp}")
            result["target_hp"] = target.hp

    # ---- 死亡判定（自定义：无视死者苏生和g4人形态免死，不无视救世主免死） ----
    if target.hp <= 0:
        prevented = False
        # 只允许救世主（Savior）的免死生效
        if (target.talent and not getattr(target, '_mythland_talent_suppressed', False)):
            if hasattr(target.talent, 'name') and target.talent.name == "愿负世，照拂黎明":
                if getattr(target.talent, 'is_savior', False):
                    # 已在救世主状态 → Terror 不无视救世主免死
                    # 阻止死亡，设 HP 为 0.5，退出救世主状态
                    target.hp = 0.5
                    prevented = True
                    if hasattr(target.talent, '_exit_savior_state'):
                        target.talent._exit_savior_state()
                    result["details"].append(
                        prompt_manager.get_prompt("talent", "g7hoshino.terror_savior_survive", hp=target.hp))
                # else: 人形态 → Terror 无视人形态免死 → 不调用 on_death_check，直接跳过
        # 其他玩家的救世主天赋检查（当前 on_death_check 仅对自身生效，实际不会触发跨玩家保护）
        if not prevented and game_state:
            for pid in game_state.player_order:
                p = game_state.get_player(pid)
                if (p and p.talent and p.player_id != target.player_id
                        and hasattr(p.talent, 'name')
                        and p.talent.name == "愿负世，照拂黎明"):
                    dr = p.talent.on_death_check(target, attacker)
                    if dr and dr.get("prevent_death"):
                        target.hp = dr.get("new_hp", 0.5)
                        prevented = True
                        savior_survive = prompt_manager.get_prompt("talent", "g7hoshino.terror_savior_survive",
                                                          hp=target.hp)
                        result["details"].append(savior_survive)
                        break
        # 注意：死者苏生、g4人形态免死在这里被跳过（不检查）

        if prevented:
            result["killed"] = False
            result["target_hp"] = target.hp
        else:
            result["killed"] = True
            terror_kill = prompt_manager.get_prompt("talent", "g7hoshino.terror_kill",
                                          target_name=target.name)
            result["details"].append(terror_kill)
            if (attacker and attacker.talent and hasattr(attacker.talent, 'on_kill')
                and not getattr(attacker, '_mythland_talent_suppressed', False)):
                attacker.talent.on_kill(attacker, target)

    elif target.hp <= 0.5 and not target.is_stunned and not color_10_triggered:
        prevent = False
        if (target.talent and hasattr(target.talent, 'prevent_stun')
                and not getattr(target, '_mythland_talent_suppressed', False)):
            prevent = target.talent.prevent_stun(target)
        if not prevent:
            result["stunned"] = True
            target.is_stunned = True
            if game_state:
                game_state.markers.add(target.player_id, "STUNNED")
            terror_stun = prompt_manager.get_prompt("talent", "g7hoshino.terror_stun",
                                          target_name=target.name)
            result["details"].append(terror_stun)

    # ---- 愿负世：被攻击时积累火种 ----
    if (target.talent and hasattr(target.talent, 'on_being_attacked') and attacker
            and not getattr(target, '_mythland_talent_suppressed', False)):
        target.talent.on_being_attacked(attacker, None, False)

    return result