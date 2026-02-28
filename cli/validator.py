"""合法性校验器（Phase 3 完整版）"""

from actions.move import get_all_valid_locations
from actions.interact import get_location_module
from actions.special_op import get_available_specials
from cli.parser import resolve_player_target
from models.equipment import WeaponRange

def _is_stealth_blocked_by_hologram(player_id, game_state):
    """检查全息影像是否破除目标的隐身"""
    for pid in game_state.player_order:
        p = game_state.get_player(pid)
        if p and p.talent and hasattr(p.talent, 'is_stealth_blocked'):
            if p.talent.is_stealth_blocked(player_id):
                return True
    return False

def _is_stealth_blocked(player_id, game_state):
    """检查隐身是否被任何效果破除（全息影像/结界）"""
    # 全息影像
    if _is_stealth_blocked_by_hologram(player_id, game_state):
        return True
    # 结界
    if hasattr(game_state, 'active_barrier') and game_state.active_barrier:
        if game_state.active_barrier.is_stealth_blocked_in_barrier(player_id):
            return True
    return False

def _check_hologram_lock_find(player, game_state):
    """检查玩家是否被全息影像禁止锁定/找到"""
    for pid in game_state.player_order:
        p = game_state.get_player(pid)
        if p and p.talent and hasattr(p.talent, 'can_lock_or_find'):
            allowed, reason = p.talent.can_lock_or_find(player.player_id)
            if not allowed:
                return reason
    return None

def _check_barrier_block(player, action_type, game_state):
    """检查结界是否禁止该行动"""
    if not hasattr(game_state, 'active_barrier') or not game_state.active_barrier:
        return None
    barrier = game_state.active_barrier
    if not barrier.is_in_barrier(player.player_id):
        return None
    blocked, reason = barrier.is_action_blocked(action_type)
    if blocked:
        return reason
    return None

def validate(parsed, player, game_state):
    action = parsed.get("action")

    if action == "wake":
        return validate_wake(player)
    elif action == "move":
        return validate_move(player, parsed.get("destination"), game_state)
    elif action == "interact":
        return validate_interact(player, parsed.get("item"), game_state)
    elif action == "lock":
        return validate_lock(player, parsed.get("target"), game_state)
    elif action == "find":
        return validate_find(player, parsed.get("target"), game_state)
    elif action == "attack":
        return validate_attack(player, parsed, game_state)
    elif action == "special":
        return validate_special(player, parsed.get("operation"), game_state)
    elif action == "report":
        return validate_report(player, parsed.get("target"), game_state)
    elif action == "assemble":
        return validate_assemble(player, game_state)
    elif action == "track_guide":
        return validate_track_guide(player, game_state)
    elif action == "recruit":
        return validate_recruit(player, game_state)
    elif action == "election":
        return validate_election(player, game_state)
    elif action == "designate":
        return validate_designate(player, parsed.get("target"), game_state)
    elif action == "split":
        return validate_split(player, game_state)
    elif action == "study":
        return validate_study(player, game_state)
    elif action == "forfeit":
        return True, ""
    elif action in ("status", "allstatus", "help", "police_status"):
        return True, ""
    else:
        return False, f"未知的行动类型：{action}"


# ============================================
#  Phase 1/2 校验（保留不变）
# ============================================

def validate_wake(player):
    if player.is_awake:
        return False, "你已经起床了！"
    return True, ""


def validate_move(player, destination, game_state):
    if not player.is_awake:
        return False, "你还没起床！"
    if destination is None:
        return False, "请指定目的地。用法：move <地点名>"
    # 结界限制
    barrier_msg = _check_barrier_block(player, "move", game_state)
    if barrier_msg:
        return False, barrier_msg
    valid = get_all_valid_locations(game_state)
    if destination not in valid:
        return False, f"「{destination}」不是有效地点。可用：{', '.join(valid)}"
    if destination == player.location:
        return False, "你已经在这里了！"
    return True, ""


def validate_interact(player, item_name, game_state):
    if not player.is_awake:
        return False, "你还没起床！"
    if item_name is None:
        return False, "请指定交互项目。"
    # 结界限制
    barrier_msg = _check_barrier_block(player, "interact", game_state)
    if barrier_msg:
        return False, barrier_msg
    loc_module = get_location_module(player.location)
    if loc_module is None:
        return False, f"当前位置没有可交互的项目。"
    try:
        can, reason = loc_module.can_interact(player, item_name, game_state)
    except TypeError:
        can, reason = loc_module.can_interact(player, item_name)
    if not can:
        return False, reason
    return True, ""


def validate_lock(player, target_str, game_state):
    if not player.is_awake:
        return False, "你还没起床！"
    if target_str is None:
        return False, "请指定锁定目标。"
    ok, reason = _check_not_disabled(player, game_state)
    if not ok:
        return False, reason
    target_id = resolve_player_target(target_str, game_state)
    if not target_id:
        return False, f"找不到玩家「{target_str}」"
    target = game_state.get_player(target_id)
    if not target or not target.is_alive():
        return False, f"{target_str} 已死亡或不存在"
    if not target.is_on_map():
        return False, f"{target.name} 不在地图上"
    if target_id == player.player_id:
        return False, "不能锁定自己"

    # 全息影像/结界：破除隐身（必须在可见性检查之前）
    if _is_stealth_blocked(target_id, game_state):
        if game_state.markers.has(target_id, "INVISIBLE"):
            game_state.markers.remove(target_id, "INVISIBLE")

    visible = game_state.markers.is_visible_to(
        target_id, player.player_id, player.has_detection)
    if not visible:
        return False, f"{target.name} 对你不可见"
    already = game_state.markers.has_relation(
        target_id, "LOCKED_BY", player.player_id)
    if already:
        return False, f"你已经锁定了 {target.name}"

    # 全息影像：禁止非发动者锁定/找到
    hologram_block = _check_hologram_lock_find(player, game_state)
    if hologram_block:
        return False, hologram_block

    return True, ""


def validate_find(player, target_str, game_state):
    if not player.is_awake:
        return False, "你还没起床！"
    if target_str is None:
        return False, "请指定目标。"
    ok, reason = _check_not_disabled(player, game_state)
    if not ok:
        return False, reason
    target_id = resolve_player_target(target_str, game_state)
    if not target_id:
        return False, f"找不到玩家「{target_str}」"
    target = game_state.get_player(target_id)
    if not target or not target.is_alive():
        return False, f"{target_str} 已死亡"
    if target_id == player.player_id:
        return False, "不能对自己使用找到"
    if target.location != player.location:
        return False, f"{target.name} 不在你的位置"

    # 全息影像/结界：破除隐身（必须在可见性检查之前）
    if _is_stealth_blocked(target_id, game_state):
        if game_state.markers.has(target_id, "INVISIBLE"):
            game_state.markers.remove(target_id, "INVISIBLE")

    visible = game_state.markers.is_visible_to(
        target_id, player.player_id, player.has_detection)
    if not visible:
        return False, f"{target.name} 对你不可见"
    already = game_state.markers.has_relation(
        player.player_id, "ENGAGED_WITH", target_id)
    if already:
        return False, f"你已经和 {target.name} 面对面了"

    # 全息影像：禁止非发动者锁定/找到
    hologram_block = _check_hologram_lock_find(player, game_state)
    if hologram_block:
        return False, hologram_block

    return True, ""


def validate_attack(player, parsed, game_state):
    if not player.is_awake:
        return False, "你还没起床！"
    ok, reason = _check_not_disabled(player, game_state)
    if not ok:
        return False, reason
    target_str = parsed.get("target")
    weapon_name = parsed.get("weapon")
    if not target_str:
        return False, "请指定攻击目标。"
    if not weapon_name:
        if len(player.weapons) == 1:
            weapon_name = player.weapons[0].name
            parsed["weapon"] = weapon_name
        else:
            available = ", ".join(w.name for w in player.weapons)
            return False, f"你有多把武器，请指定使用哪一把：{available}"
    target_id = resolve_player_target(target_str, game_state)
    if not target_id:
        return False, f"找不到玩家「{target_str}」"
    target = game_state.get_player(target_id)
    if not target or not target.is_alive():
        return False, f"{target_str} 已死亡"
    if target_id == player.player_id:
        return False, "不能攻击自己"
    weapon = player.get_weapon(weapon_name)
    if not weapon:
        available = ", ".join(w.name for w in player.weapons)
        return False, f"你没有武器「{weapon_name}」。你持有：{available}"
    if weapon.requires_charge and not weapon.is_charged:
        return False, f"「{weapon_name}」需要先蓄力！"

    # 愿负世：救世主状态禁用远程
    if weapon.weapon_range == WeaponRange.RANGED:
        if player.talent and hasattr(player.talent, 'is_remote_disabled'):
            if player.talent.is_remote_disabled():
                return False, "救世主状态下禁用远程攻击。"

    # 警察保护检查
    if weapon.weapon_range != WeaponRange.AREA:
        if game_state.police_engine and game_state.police_engine.is_protected_by_police(target_id):
            return False, f"{target.name} 受警察保护，免疫单体伤害！（范围攻击仍有效）"

    if weapon.weapon_range == WeaponRange.MELEE:
        return _validate_melee(player, target, target_id, game_state)
    elif weapon.weapon_range == WeaponRange.RANGED:
        return _validate_ranged(player, target, target_id, weapon, game_state)
    elif weapon.weapon_range == WeaponRange.AREA:
        return _validate_area(player, target, game_state)
    return True, ""


def validate_special(player, op_name, game_state):
    if not player.is_awake:
        return False, "你还没起床！"
    if not op_name:
        return False, "请指定操作名。"
    ok, reason = _check_not_disabled(player, game_state)
    if not ok:
        return False, reason
    available = get_available_specials(player, game_state)
    available_names = [s["name"] for s in available]
    if op_name not in available_names:
        if available_names:
            return False, f"当前不可执行「{op_name}」。可用：{', '.join(available_names)}"
        return False, f"当前没有可执行的特殊操作"
    return True, ""


# ============================================
#  警察相关校验（全部加结界拦截）
# ============================================

def validate_report(player, target_str, game_state):
    if not player.is_awake:
        return False, "你还没起床！"
    ok, reason = _check_not_disabled(player, game_state)
    if not ok:
        return False, reason
    # 结界限制
    barrier_msg = _check_barrier_block(player, "report", game_state)
    if barrier_msg:
        return False, barrier_msg
    if target_str is None:
        return False, "请指定举报目标。用法：report <玩家名或ID>"
    target_id = resolve_player_target(target_str, game_state)
    if not target_id:
        return False, f"找不到玩家「{target_str}」"
    if target_id == player.player_id:
        return False, "不能举报自己"
    if not game_state.police_engine:
        return False, "警察系统未初始化"
    can, reason = game_state.police_engine.can_report(player.player_id, target_id)
    if not can:
        return False, reason
    return True, ""


def validate_assemble(player, game_state):
    if not player.is_awake:
        return False, "你还没起床！"
    ok, reason = _check_not_disabled(player, game_state)
    if not ok:
        return False, reason
    # 结界限制
    barrier_msg = _check_barrier_block(player, "assemble", game_state)
    if barrier_msg:
        return False, barrier_msg
    if not game_state.police_engine:
        return False, "警察系统未初始化"
    can, reason = game_state.police_engine.can_assemble(player.player_id)
    if not can:
        return False, reason
    return True, ""


def validate_track_guide(player, game_state):
    if not player.is_awake:
        return False, "你还没起床！"
    ok, reason = _check_not_disabled(player, game_state)
    if not ok:
        return False, reason
    # 结界限制
    barrier_msg = _check_barrier_block(player, "track_guide", game_state)
    if barrier_msg:
        return False, barrier_msg
    if not game_state.police_engine:
        return False, "警察系统未初始化"
    police = game_state.police
    if police.reporter_id != player.player_id:
        return False, "只有举报者才能指引追踪"
    tracking = any(t.is_tracking for t in police.teams if not t.is_eliminated())
    if not tracking:
        return False, "当前没有警队在追踪中"
    return True, ""


def validate_recruit(player, game_state):
    if not player.is_awake:
        return False, "你还没起床！"
    ok, reason = _check_not_disabled(player, game_state)
    if not ok:
        return False, reason
    # 结界限制
    barrier_msg = _check_barrier_block(player, "recruit", game_state)
    if barrier_msg:
        return False, barrier_msg
    if not game_state.police_engine:
        return False, "警察系统未初始化"
    can, reason = game_state.police_engine.can_join_police(player.player_id)
    if not can:
        return False, reason
    return True, ""


def validate_election(player, game_state):
    if not player.is_awake:
        return False, "你还没起床！"
    ok, reason = _check_not_disabled(player, game_state)
    if not ok:
        return False, reason
    # 结界限制
    barrier_msg = _check_barrier_block(player, "election", game_state)
    if barrier_msg:
        return False, barrier_msg
    if not game_state.police_engine:
        return False, "警察系统未初始化"
    can, reason = game_state.police_engine.can_start_election(player.player_id)
    if not can:
        return False, reason
    return True, ""


def validate_designate(player, target_str, game_state):
    if not player.is_awake:
        return False, "你还没起床！"
    if not player.is_captain:
        return False, "只有队长才能指定执法目标"
    # 结界限制
    barrier_msg = _check_barrier_block(player, "designate", game_state)
    if barrier_msg:
        return False, barrier_msg
    if target_str is None:
        return False, "请指定目标。用法：designate <玩家名或ID>"
    target_id = resolve_player_target(target_str, game_state)
    if not target_id:
        return False, f"找不到玩家「{target_str}」"
    target = game_state.get_player(target_id)
    if not target or not target.is_alive():
        return False, f"{target_str} 已死亡"
    return True, ""


def validate_split(player, game_state):
    if not player.is_awake:
        return False, "你还没起床！"
    if not player.is_captain:
        return False, "只有队长才能拆分警队"
    # 结界限制
    barrier_msg = _check_barrier_block(player, "split", game_state)
    if barrier_msg:
        return False, barrier_msg
    police = game_state.police
    active = [t for t in police.teams if not t.is_eliminated()]
    if len(active) >= police.max_teams:
        return False, f"警队数量已达上限（{police.max_teams}支）"
    if police.splits_this_round >= 1:
        return False, "本轮已拆分过一次"
    return True, ""


def validate_study(player, game_state):
    if not player.is_awake:
        return False, "你还没起床！"
    if not player.is_captain:
        return False, "只有队长才能进行研究性学习"
    # 结界限制
    barrier_msg = _check_barrier_block(player, "study", game_state)
    if barrier_msg:
        return False, barrier_msg
    if player.location != "警察局":
        return False, "需要在警察局才能研究性学习"
    return True, ""


# ============================================
#  内部辅助
# ============================================

def _validate_melee(player, target, target_id, game_state):
    if target.location != player.location:
        return False, f"近战需要同地点（{target.name}在{target.location}）"
    engaged = game_state.markers.has_relation(
        player.player_id, "ENGAGED_WITH", target_id)
    if not engaged:
        return False, f"近战需要先找到{target.name}（建立面对面）"
    return True, ""


def _validate_ranged(player, target, target_id, weapon, game_state):
    if "missile" in weapon.special_tags:
        if player.location != "军事基地":
            return False, "导弹发射必须在军事基地"
        if not game_state.markers.has(player.player_id, "MISSILE_CTRL"):
            return False, "你没有导弹控制权"
    locked = game_state.markers.has_relation(
        target_id, "LOCKED_BY", player.player_id)
    if not locked:
        return False, f"远程攻击需要先锁定{target.name}"
    visible = game_state.markers.is_visible_to(
        target_id, player.player_id, player.has_detection)
    if not visible:
        return False, f"{target.name} 对你不可见"
    return True, ""


def _validate_area(player, target, game_state):
    others = [p for p in game_state.players_at_location(player.location)
              if p.player_id != player.player_id and p.is_alive()]
    if not others:
        return False, "同地点没有其他目标"
    return True, ""


def _check_not_disabled(player, game_state):
    if game_state.markers.has(player.player_id, "STUNNED"):
        return False, "你处于眩晕状态"
    if game_state.markers.has(player.player_id, "SHOCKED"):
        return False, "你处于震荡状态"
    if game_state.markers.has(player.player_id, "PETRIFIED"):
        return False, "你处于石化状态"
    return True, ""
