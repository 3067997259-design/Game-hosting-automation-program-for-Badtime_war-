"""
合法性校验器（Phase 3 完整版 - ver1.9适配）
"""

from actions.move import get_all_valid_locations, has_active_supernova
from actions.interact import get_location_module
from actions.special_op import get_available_specials
from cli.parser import resolve_player_target
from models.equipment import WeaponRange
from engine.prompt_manager import prompt_manager

def _check_love_wish_block(attacker_pid, target_pid, game_state):
    """检查攻击者是否因爱愿无法攻击目标（目标是G5持有者）"""
    if not game_state:
        return False
    target = game_state.get_player(target_pid)
    if not target or not target.talent:
        return False
    # Check if target is G5 (Ripple) holder and attacker has love_wish
    if hasattr(target.talent, 'love_wish') and hasattr(target.talent, 'has_love_wish'):
        if target.talent.has_love_wish(attacker_pid):
            return True
    return False

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

def _is_smoke_active(game_state, location):
    """检查指定地点的烟雾是否仍在有效期内"""
    if not hasattr(game_state, '_hoshino_smoke_zones'):
        return False
    if location not in game_state._hoshino_smoke_zones:
        return False
    return game_state.current_round <= game_state._hoshino_smoke_zones[location]

def _is_hoshino_player(player):
    """检查玩家是否为星野（通过天赋名判断）"""
    return (player.talent and getattr(player.talent, 'name', '') == "大叔我啊，剪短发了")

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


def _is_police_crime_blocked(player, parsed, game_state):
    """[Issue 5] 检查警察成员（非队长）是否因犯罪限制被阻止执行该行动。
    README 10.8.1: 加入警察后不能犯罪，违法条目视为不允许执行。"""
    if not getattr(player, 'is_police', False) or getattr(player, 'is_captain', False):
        return None  # 非警察或队长不受限制

    action = parsed.get("action")

    # 1. 攻击其他玩家 = 犯罪
    if action == "attack":
        target_str = parsed.get("target", "")
        if target_str:
            return "你是警察成员，不能执行违法行为（伤害其他玩家/攻击警察）"

    # 2. 无凭证购物/手术
    if action == "interact":
        item = parsed.get("item", "")
        if getattr(player, 'location', '') == "商店" and not getattr(player, 'has_voucher', False):
            return "你是警察成员，不能执行违法行为（无凭证购物）"
        if getattr(player, 'location', '') == "医院" and item == "手术" and not getattr(player, 'has_voucher', False):
            return "你是警察成员，不能执行违法行为（无凭证手术）"

    # 3. 释放病毒
    if action == "special" and parsed.get("operation") == "释放病毒":
        return "你是警察成员，不能执行违法行为（释放病毒）"

    # 4. 朝阳好市民扩展条目（检查是否有朝阳好市民天赋生效）
    has_citizen_talent = False
    for pid in game_state.player_order:
        p = game_state.get_player(pid)
        if p and p.talent and getattr(p.talent, 'name', '') == '朝阳好市民':
            has_citizen_talent = True
            break

    if has_citizen_talent:
        if action == "move":
            dest = parsed.get("destination", "")
            # 进入其他玩家的家
            for p in game_state.players.values():                    # 修复: .values()
                if p.player_id != player.player_id:
                    home = f"home_{p.player_id}"                     # 修复: 匹配 parser 格式
                    if dest == home:
                        return "你是警察成员，不能执行违法行为（进入他人住宅）"
            # 进入军事基地
            if dest == "军事基地":
                return "你是警察成员，不能执行违法行为（进入军事基地）"

        # 释放病毒


    return None


def validate(parsed, player, game_state):
    action = parsed.get("action")

    # [Issue 5] 警察成员犯罪限制前置检查
    crime_block = _is_police_crime_blocked(player, parsed, game_state)
    if crime_block:
        return False, crime_block

    # M9：legacy 警察引擎指令与喝彩消耗在 m9-rfc 下退役（commands.md 附录已
    # 标注"设计已取代、实现未拆除"）。M9 警务走 m9_police 状态机，入口为
    # `special 热线举报<目标>` / `special 竞选队长` / `special 指挥<警员>移动`；
    # 喝彩被 B4 v0.4 的 PP 取代。这里在入口统一拒绝，避免退役语义泄漏进 m9-rfc。
    _M9_RETIRED_ACTIONS = frozenset({
        "wake_police", "report", "assemble", "track_guide", "recruit",
        "election", "designate", "study", "police_command", "applause_spend",
    })
    if action in _M9_RETIRED_ACTIONS:
        from engine import experiments
        if experiments.is_enabled("m9_rfc"):
            hint = ("警务请用 `special 热线举报<玩家名>` / `special 竞选队长` / "
                    "`special 指挥<警员id>移动`；喝彩已被 PP 取代")
            return False, f"❌ 该指令在 M9 规则下已退役（{hint}）"

    if action == "wake":
        return validate_wake(player)
    elif action == "wake_police":
        return validate_wake_police(player, parsed.get("police_id"), game_state)
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
    elif action == "applause_spend":
        return validate_applause_spend(player, parsed.get("use"), game_state)
    elif action == "shoot":
        return validate_shoot(player, parsed.get("target"), game_state)
    elif action == "hook":
        return validate_hook(player, parsed, game_state)
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
    elif action == "study":
        return validate_study(player, game_state)
    elif action == "police_command":
        return validate_police_command(player, parsed, game_state)
    elif action == "forfeit":
        return True, ""
    elif action in ("status", "allstatus", "help", "police_status"):
        return True, ""
    else:
        return False, f"未知的行动类型：{action}"


# ============================================
#  M4 校验：射箭 / 钩索（experiment: m4_gear）
# ============================================

def validate_applause_spend(player, use, game_state):
    from engine import experiments
    if not experiments.is_enabled("m6_scoring"):
        return False, "未知的行动类型：applause_spend"
    if not player.is_awake:
        return False, "你还没起床！"
    from actions.applause_spend import cost_of
    cost = cost_of(use)
    if cost <= 0:
        return False, f"未知喝彩用途「{use}」"
    if getattr(player, "applause", 0) < cost:
        return False, f"喝彩点不足（需 {cost}，有 {player.applause}）"
    return True, ""


def validate_shoot(player, target_str, game_state):
    from engine.economy import m4_enabled
    if not m4_enabled():
        return False, "未知的行动类型：shoot"
    if not player.is_awake:
        return False, "你还没起床！"
    ok, reason = _check_not_disabled(player, game_state)
    if not ok:
        return False, reason
    if not player.has_weapon("弓"):
        return False, "你没有弓"
    from engine.bow_modules import compute_shot
    if not compute_shot(player)["infinite"] and getattr(player, 'arrows', 0) < 1:
        return False, "箭袋空了（商店可补给，find 可拾取落箭）"
    if not target_str:
        return False, "请指定射击目标。"
    target_id = resolve_player_target(target_str, game_state)
    if not target_id:
        return False, f"找不到玩家「{target_str}」"
    actor_getter = getattr(game_state, "get_actor", game_state.get_player)
    target = actor_getter(target_id)
    if not target or not target.is_alive():
        return False, f"{target_str} 已死亡"
    if target_id == player.player_id:
        return False, "不能射自己"
    if not getattr(target, 'is_awake', False) or target.location is None:
        return False, f"{target.name} 不在地图上"
    if _check_love_wish_block(player.player_id, target_id, game_state):
        return False, f"💝「爱愿」生效中：你无法攻击 {target.name}"
    from engine.visibility import can_see
    if not can_see(player, target, game_state):
        return False, f"{target.name} 对你不可见"
    return True, ""


def validate_hook(player, parsed, game_state):
    from engine.economy import m4_enabled
    if not m4_enabled():
        return False, "未知的行动类型：hook"
    if not player.is_awake:
        return False, "你还没起床！"
    ok, reason = _check_not_disabled(player, game_state)
    if not ok:
        return False, reason
    if not any(getattr(i, 'name', '') == "钩索" for i in getattr(player, 'items', [])):
        return False, "你没有钩索"
    from engine.balance import get as _bget
    cooldown = _bget("hook", "cooldown_rounds", default=2)
    ready_round = getattr(player, '_last_hook_round', -99) + cooldown
    if game_state.current_round < ready_round:
        return False, f"钩索冷却中（第 {ready_round} 轮就绪）"
    if parsed.get("mode") == "self":
        dest = parsed.get("destination")
        from actions.move import get_all_valid_locations
        if dest not in get_all_valid_locations(game_state):
            return False, f"无效目的地「{dest}」"
        if dest == player.location:
            return False, "你已经在这里了"
        return True, ""
    target_str = parsed.get("target")
    target_id = resolve_player_target(target_str, game_state)
    if not target_id:
        return False, f"找不到玩家「{target_str}」"
    actor_getter = getattr(game_state, "get_actor", game_state.get_player)
    target = actor_getter(target_id)
    if not target or not target.is_alive():
        return False, f"{target_str} 已死亡"
    if target_id == player.player_id:
        return False, "拉自己请用 hook self <地点>"
    if not getattr(target, 'is_awake', False) or target.location is None:
        return False, f"{target.name} 不在地图上"
    if target.location == player.location:
        return False, f"{target.name} 已经和你同地点"
    if _check_love_wish_block(player.player_id, target_id, game_state):
        return False, f"💝「爱愿」生效中：你无法攻击 {target.name}"
    from engine.visibility import can_see
    if not can_see(player, target, game_state):
        return False, f"{target.name} 对你不可见"
    return True, ""


# ============================================
#  Phase 1/2 校验（保留不变）
# ============================================

def validate_wake(player):
    if player.is_awake:
        return False, "你已经起床了！"
    return True, ""

def validate_wake_police(player, police_id, game_state):
    """校验唤醒警察操作 - 对应README 10.7节
    条件：玩家与处于debuff的警察单位在同一地点，不需要找到步骤"""
    if not player.is_awake:
        return False, "你还没起床！"
    ok, reason = _check_not_disabled(player, game_state)
    if not ok:
        return False, reason
    if police_id is None:
        return False, "请指定要唤醒的警察单位ID。用法：wake <警察ID>"
    if not game_state.police_engine:
        return False, "警察系统未初始化"
    police = game_state.police
    if police.permanently_disabled:
        return False, "警察系统已永久关闭"
    unit = police.get_unit(police_id)
    if not unit:
        return False, f"找不到警察单位「{police_id}」"
    if not unit.is_alive():
        return False, f"{police_id} 已被击杀，无法唤醒"
    if not unit.is_disabled():
        return False, f"{police_id} 没有处于需要唤醒的状态"
    if unit.location != player.location:
        return False, f"你与 {police_id} 不在同一地点"
    # 沉沦+全息影像限制
    if unit.is_submerged:
        if game_state.police_engine._is_in_hologram_range(unit.location):
            return False, f"{police_id} 处于沉沦状态且在全息影像范围内，无法被唤醒"
    return True, ""

def validate_move(player, destination, game_state):
    if not player.is_awake:
        return False, "你还没起床！"
    if destination is None:
        return False, "请指定目的地。用法：move <地点名>"
    # 星野架盾：自身无法移动
    if (player.talent and hasattr(player.talent, 'shield_mode')
            and player.talent.shield_mode == "架盾"):
        return False, prompt_manager.get_prompt("talent", "g7hoshino.validate_shield_no_move")
    # 结界限制
    barrier_msg = _check_barrier_block(player, "move", game_state)
    if barrier_msg:
        return False, barrier_msg
    valid = get_all_valid_locations(game_state)
    if destination not in valid:
        return False, f"「{destination}」不是有效地点。可用：{', '.join(valid)}"
    # 超新星过载：允许选择当前地点作为目的地
    if destination == player.location:
        if has_active_supernova(player):
            return True, ""
        # 半进入状态：允许 move 同地点（突破守点）
        if (getattr(player, '_shield_half_entered', False)
                and getattr(player, '_shield_half_entered_location', None) == destination):
            return True, ""
        return False, "你已经在这个地点了。"
    # 军事基地：无通行证时提示可强买或花回合办理
    if destination == "军事基地" and not player.has_military_pass:
        if player.vouchers >= 1:
            pass  # 允许移动，到达后在 move.execute 中提供强买选项
        # 无凭证也允许移动，到达后需花回合办理通行证
    return True, ""

def validate_interact(player, item_name, game_state):
    if not player.is_awake:
        return False, "你还没起床！"
    if item_name is None:
        return False, "请指定交互项目。"
    blocked_getter = getattr(getattr(player, "talent", None),
                             "m9_blocked_action_types", None)
    if callable(blocked_getter) and "interact" in blocked_getter():
        return False, "当前天赋形态禁止 interact。"
    # 半进入状态：禁用 interact
    if getattr(player, '_shield_half_entered', False):
        return False, "🛡️ 你还没完全进入此地点，无法交互。再次 move 到此地点可完全进入。"
    # Terror 全局禁用 interact
    if game_state.is_terror_alive():
        return False, prompt_manager.get_prompt("game", "validate_terror_no_interact")
    # 星野持盾：无法执行 interact
    if (player.talent and hasattr(player.talent, 'shield_mode')
            and player.talent.shield_mode in ("持盾", "架盾")):
        return False, prompt_manager.get_prompt("talent", "g7hoshino.validate_shield_no_interact")
    # 结界限制
    barrier_msg = _check_barrier_block(player, "interact", game_state)
    if barrier_msg:
        return False, barrier_msg
    loc_module = get_location_module(player.location)
    if loc_module is None:
        return False, f"当前位置没有可交互的项目。"
    can, reason = loc_module.can_interact(player, item_name, game_state)
    if not can:
        return False, reason
    # M5 白昼限量营业：每地点每条目每轮 1 次（先攻争夺，先到先得）
    from engine import experiments
    if experiments.is_enabled("m5_clock"):
        from engine import world_clock
        if world_clock.active_value(game_state, "rationing", default=False):
            key = (player.location, item_name)
            if key in getattr(game_state, "_rationing_used", set()):
                return False, f"「{item_name}」本轮已被取用（限量营业，下轮再来）"
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
    from engine.m9.gate import m9_enabled
    if m9_enabled(game_state):
        from engine.m9.talents.g3 import lock_crosses_active_barrier
        if lock_crosses_active_barrier(game_state, player, target):
            return False, "无限剑制内部只能锁定同在结界内部的单位"
    # 锁定是远程攻击前置，必须持有远程武器
    from models.equipment import WeaponRange
    has_ranged = any(
        getattr(w, 'weapon_range', None) == WeaponRange.RANGED
        for w in (player.weapons or []) if w
    )
    if not has_ranged:
        return False, "锁定是远程攻击前置，你没有远程武器"
    # 烟雾弹：目标在烟雾区域内禁止 lock（星野自己除外）
    target_loc = target.location
    if _is_smoke_active(game_state, target_loc):
        if not _is_hoshino_player(player):
            return False, prompt_manager.get_prompt(
                "talent", "g7hoshino.smoke_no_lock",
                default="🌫️ 目标在烟雾区域中，无法锁定")

    # 全息影像/结界：破除隐身（必须在可见性检查之前）
    if _is_stealth_blocked(target_id, game_state):
        if game_state.markers.has(target_id, "INVISIBLE"):
            game_state.markers.remove(target_id, "INVISIBLE")

    from engine.visibility import can_see, mark_detected_if_seen
    if not can_see(player, target, game_state):
        return False, f"{target.name} 对你不可见"

    # 看穿隐身目标：添加 DETECTED_BY 关系（之后恒可见）
    mark_detected_if_seen(player, target, game_state)

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
    blocked_getter = getattr(getattr(player, "talent", None),
                             "m9_blocked_action_types", None)
    if callable(blocked_getter) and "find" in blocked_getter():
        return False, "当前天赋形态禁止 find。"
    ok, reason = _check_not_disabled(player, game_state)
    if not ok:
        return False, reason
    target_id = resolve_player_target(target_str, game_state)
    if not target_id:
        return False, f"找不到玩家「{target_str}」"
    actor_getter = getattr(game_state, "get_actor", game_state.get_player)
    target = actor_getter(target_id)
    if not target or not target.is_alive():
        return False, f"{target_str} 已死亡"
    if target_id == player.player_id:
        return False, "不能对自己使用找到"
    if target.location != player.location:
        return False, f"{target.name} 不在你的位置"
    # 烟雾弹：烟雾区域内禁止 find（星野自己除外）
    if _is_smoke_active(game_state, player.location):
        # 星野自己可以在烟雾中 find
        if not _is_hoshino_player(player):
            return False, prompt_manager.get_prompt(
                "talent", "g7hoshino.smoke_no_find",
                default="🌫️ 烟雾中无法执行 find")

    # 全息影像/结界：破除隐身（必须在可见性检查之前）
    if _is_stealth_blocked(target_id, game_state):
        if game_state.markers.has(target_id, "INVISIBLE"):
            game_state.markers.remove(target_id, "INVISIBLE")

    from engine.visibility import can_see, mark_detected_if_seen
    if not can_see(player, target, game_state):
        return False, f"{target.name} 对你不可见"

    # 看穿隐身目标：添加 DETECTED_BY 关系（之后恒可见）
    mark_detected_if_seen(player, target, game_state)

    already = game_state.markers.has_relation(
        player.player_id, "ENGAGED_WITH", target_id)
    if already:
        return False, f"你已经和 {target.name} 面对面了"

    # 全息影像：禁止非发动者锁定/找到
    hologram_block = _check_hologram_lock_find(player, game_state)
    if hologram_block:
        return False, hologram_block

    return True, ""

def _resolve_attack_weapon(player, weapon_spec, layer_str=None, attr_str=None):
    """按持有武器名对 attack 的武器段做最长匹配。

    parser 已把目标名之后的全部 token 拼进 weapon_spec（武器名可含空格，
    如 G0 的 BLACK FANG 465）。这里先按最长持有武器名精确/前缀匹配，
    剩余 token 依次回填 layer/attr，保持旧的 attack target weapon layer attr
    四参语义不变。
    """
    names = sorted(
        {w.name for w in getattr(player, "weapons", []) if w is not None},
        key=len, reverse=True)
    weapon_spec = (weapon_spec or "").strip()
    if not weapon_spec:
        return None, layer_str, attr_str
    for name in names:
        if weapon_spec == name:
            return player.get_weapon(name), layer_str, attr_str
        if weapon_spec.startswith(name + " "):
            suffix = weapon_spec[len(name) + 1:].split()
            if layer_str is None and suffix:
                layer_str = suffix[0]
            if attr_str is None and len(suffix) >= 2:
                attr_str = suffix[1]
            return player.get_weapon(name), layer_str, attr_str
    return None, layer_str, attr_str


def validate_attack(player, parsed, game_state):
    if not player.is_awake:
        return False, "你还没起床！"
    ok, reason = _check_not_disabled(player, game_state)
    if not ok:
        return False, reason
    # Terror 攻击：跳过所有常规校验（不需要目标、武器）
    if (player.talent and hasattr(player.talent, 'is_terror')
            and player.talent.is_terror):
        return True, ""
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

    weapon, layer_str, attr_str = _resolve_attack_weapon(
        player, weapon_name, parsed.get("layer"), parsed.get("attr"))
    if weapon is None:
        available = ", ".join(w.name for w in player.weapons)
        return False, f"你没有武器「{weapon_name}」。你持有：{available}"
    weapon_name = weapon.name
    parsed["weapon"] = weapon_name
    parsed["layer"] = layer_str
    parsed["attr"] = attr_str

    # 检查是否为警察目标
    if target_str.lower().startswith("police"):
        # 警察目标特殊验证
        weapon = player.get_weapon(weapon_name)
        if not weapon:
            available = ", ".join(w.name for w in player.weapons)
            return False, f"你没有武器「{weapon_name}」。你持有：{available}"
        # L346-347（警察目标分支）
        if weapon.requires_charge and getattr(weapon, 'charge_mandatory', True) and not weapon.is_charged:
            return False, f"「{weapon_name}」需要先蓄力！"
        # 六爻封印检查
        if getattr(weapon, '_hexagram_disabled', False):
            return False, f"「{weapon_name}」被六爻封印，暂时无法使用！"
        return True, ""

    # 原有玩家目标验证
    target_id = resolve_player_target(target_str, game_state)
    if not target_id:
        return False, f"找不到玩家「{target_str}」"
    actor_getter = getattr(game_state, "get_actor", game_state.get_player)
    target = actor_getter(target_id)
    if not target or not target.is_alive():
        return False, f"{target_str} 已死亡"
    if target_id == player.player_id:
        return False, "不能攻击自己"
    # 爱愿检查：持有爱愿的玩家无法攻击G5持有者
    if _check_love_wish_block(player.player_id, target_id, game_state):
        target_player = game_state.get_player(target_id)
        return False, f"💝「爱愿」生效中：你无法攻击 {target_player.name}"
    weapon = player.get_weapon(weapon_name)
    if not weapon:
        available = ", ".join(w.name for w in player.weapons)
        return False, f"你没有武器「{weapon_name}」。你持有：{available}"
    if weapon.requires_charge and getattr(weapon, 'charge_mandatory', True) and not weapon.is_charged:
        return False, f"「{weapon_name}」需要先蓄力！"
    # 六爻封印检查
    if getattr(weapon, '_hexagram_disabled', False):
        return False, f"「{weapon_name}」被六爻封印，暂时无法使用！"

    # 愿负世：救世主状态禁用远程
    if weapon.weapon_range == WeaponRange.RANGED:
        if player.talent and hasattr(player.talent, 'is_remote_disabled'):
            if player.talent.is_remote_disabled():
                return False, "救世主状态下禁用远程攻击。"

    # 警察保护不再阻止攻击，改为在 damage_resolver 中做阈值减免

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
        ish = getattr(game_state, 'ish_bosheth', None)
        # G2 在 ish-bosheth 中的演唱走交互式 choose 流程（含 duet 模式）
        if (ish and ish.phase in ("active", "duet")
                and player.player_id == ish.g2_owner_id):
            return True, ""
        # v2.0: G5 duet 伴唱走 _execute_action 分发（execute_harmonize）
        if (ish and ish.phase == "duet"
                and player.player_id == getattr(ish, 'duet_g5_pid', None)):
            return True, ""
        return False, "请指定操作名。"
    ok, reason = _check_not_disabled(player, game_state)
    if not ok:
        return False, reason
    available = get_available_specials(player, game_state)
    available_names = [s["name"] for s in available]
    if op_name not in available_names:
        # 支持带参数的操作（蓄力XX、更衣XX、修复XX）：用 startswith 匹配
        _PARAM_OPS = ("蓄力", "更衣", "修复")
        matched = any(
            op_name.startswith(prefix) and any(n.startswith(prefix) for n in available_names)
            for prefix in _PARAM_OPS
        )
        if not matched:
            if available_names:
                return False, f"当前不可执行「{op_name}」。可用：{', '.join(available_names)}"
            return False, f"当前没有可执行的特殊操作"
    return True, ""


# ============================================
#  警察相关校验（全部加结界拦截）- ver1.9适配
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
    """校验追踪指引 - ver1.9适配新警察模型"""
    if not player.is_awake:
        return False, "你还没起床！"
    ok, reason = _check_not_disabled(player, game_state)
    if not ok:
        return False, reason
    # 结界限制
    barrier_msg = _check_barrier_block(player, "track_guide", game_state)
    if barrier_msg:
        return False, barrier_msg
    # [Issue 11] 使用can_track_guide代替不存在的is_tracking属性
    if not game_state.police_engine:
        return False, "警察系统未初始化"
    police = game_state.police
    if police.reporter_id != player.player_id:
        return False, "只有举报者才能指引追踪"
    can_track, reason = game_state.police_engine.can_track_guide(player.player_id)
    if not can_track:
        return False, reason
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
    can, reason = game_state.police_engine.can_recruit(player.player_id)
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
    can, reason = game_state.police_engine.can_election(player.player_id)
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

def validate_police_command(player, parsed, game_state):
    """验证队长操控警察命令"""
    if not player.is_awake:
        return False, "你还没起床！"
    if not player.is_captain:
        return False, "只有队长可以操控警察"
    ok, reason = _check_not_disabled(player, game_state)
    if not ok:
        return False, reason
    # 结界限制
    barrier_msg = _check_barrier_block(player, "police_command", game_state)
    if barrier_msg:
        return False, barrier_msg
    if not game_state.police_engine:
        return False, "警察系统未初始化"
    subcommand = parsed.get("subcommand")
    police_id = parsed.get("police_id")
    if not subcommand or not police_id:
        return False, "命令格式错误"
    # 基本验证：警察单位是否存在
    police = game_state.police
    unit = police.get_unit(police_id)
    if not unit:
        return False, f"找不到警察单位 {police_id}"
    # 具体验证由警察引擎执行
    subcommand = parsed.get("subcommand")
    if subcommand == "attack":
        target_str = parsed.get("target")
        if not target_str:
            return False, "请指定攻击目标"
        from cli.parser import resolve_player_target
        target_id = resolve_player_target(target_str, game_state)
        if not target_id:
            return False, f"找不到玩家「{target_str}」"
        target = game_state.get_player(target_id)
        if not target or not target.is_alive():
            return False, f"{target_str} 已死亡"
        # 爱愿检查：队长持有爱愿时，警察无法攻击G5持有者
        if _check_love_wish_block(player.player_id, target_id, game_state):
            return False, f"💝「爱愿」生效中：你的警察无法攻击 {target.name}"
        # 也验证警察单位是否存活且可行动
        unit = game_state.police.get_unit(police_id)
        if unit and not unit.is_active():
            return False, f"{police_id} 处于行动阻碍状态，无法攻击"
        # 同地点检查（献予律法之诗等额外行动中，AI 选的单位可能与目标不同地点）
        if unit and target and unit.location != target.location:
            return False, f"{police_id} 与 {target.name} 不在同一地点（{police_id}在{unit.location}，目标在{target.location}）"

    # move 子命令也验证警察单位是否可移动
    if subcommand == "move":
        unit = game_state.police.get_unit(police_id)
        if unit and unit.is_disabled():
            return False, f"{police_id} 处于debuff状态，无法移动"

    return True, ""


# ============================================
#  内部辅助
# ============================================

def _validate_melee(player, target, target_id, game_state):
    if target.location != player.location:
        return False, f"近战需要同地点（{target.name}在{target.location}）"
    if (getattr(target, "_m9_police_actor", False)
            or getattr(target, "_m9_drone_actor", False)):
        return True, ""
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
    if ((getattr(target, "_m9_police_actor", False)
            or getattr(target, "_m9_drone_actor", False))
            and target.location == player.location):
        return True, ""
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
    actors = (game_state.iter_targetable_actors()
              if hasattr(game_state, "iter_targetable_actors")
              else game_state.players_at_location(player.location))
    others = [p for p in actors
              if p.player_id != player.player_id and p.is_alive()
              and p.location == player.location]
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
