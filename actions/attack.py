"""行动类型：攻击"""

from combat.damage_resolver import resolve_damage
from models.equipment import ArmorLayer
from utils.attribute import Attribute


# 属性名到枚举的映射
ATTR_MAP = {
    "普通": Attribute.ORDINARY, "ordinary": Attribute.ORDINARY,
    "魔法": Attribute.MAGIC, "magic": Attribute.MAGIC,
    "科技": Attribute.TECH, "tech": Attribute.TECH,
}

LAYER_MAP = {
    "外层": ArmorLayer.OUTER, "outer": ArmorLayer.OUTER, "外": ArmorLayer.OUTER,
    "内层": ArmorLayer.INNER, "inner": ArmorLayer.INNER, "内": ArmorLayer.INNER,
}


def execute(player, target_id, weapon_name, game_state,
            layer_str=None, attr_str=None,
            ignore_element=False, damage_multiplier=1.0, bonus_damage=0.0):
    """
    执行攻击。

    参数：
      player: 攻击者
      target_id: 目标玩家ID或警察目标
      weapon_name: 使用的武器名
      game_state: 游戏状态
      layer_str: 攻击层字符串（"外层"/"内层"），可选
      attr_str: 护甲属性字符串（"普通"/"魔法"/"科技"），可选
      ignore_element: 无视克制
      damage_multiplier: 伤害倍率
      bonus_damage: 额外伤害

    返回 (结果消息str, 结算详情dict)
    """
    if not target_id:
        return "❌ 攻击目标无效", {}

    weapon = player.get_weapon(weapon_name)
    if not weapon:
        return f"❌ 你没有武器「{weapon_name}」", {}

    from engine.m9.gate import m9_enabled
    if m9_enabled(game_state):
        actor_getter = getattr(game_state, "get_actor", game_state.get_player)
        target_actor = actor_getter(target_id)
        station = getattr(game_state, "m9_police", None)
        if target_actor is None and station is not None:
            target_actor = station.get_unit(target_id)

        from engine.m9.talents.g3 import attack_crosses_active_barrier
        if target_actor is not None and attack_crosses_active_barrier(
                game_state, player, target_actor):
            return "❌ 固有结界边界阻止了这次攻击", {}

        if getattr(target_actor, "_m9_drone_actor", False):
            raw = max(0, int(round(
                float(weapon.get_effective_damage()) * damage_multiplier
                + bonus_damage)))
            drone_result = target_actor.owner_talent.attack_drone(player, raw)
            result = {
                "success": bool(drone_result.get("success")),
                "raw_damage": raw,
                "final_damage": raw,
                "hp_damage": raw if drone_result.get("success") else 0,
                "target_hp": getattr(target_actor, "hp", 0),
                "killed": bool(drone_result.get("destroyed")),
                "details": [f"无人机承受 {raw} 点结构伤害"],
                **drone_result,
            }
            if not result["success"]:
                return "❌ 无人机目标已失效", {}
            game_state.log_event(
                "attack", attacker=player.player_id,
                target=target_actor.player_id, weapon=weapon_name,
                location=getattr(target_actor, "location", None),
                witnesses=_attack_witnesses(game_state, player, target_actor),
                result=result)
            return (f"⚔️ {player.name} 用「{weapon.name}」攻击"
                    f" {target_actor.name}，造成 {raw} 点结构伤害！"), result

        if getattr(target_actor, "_m9_police_actor", False):
            station.set_state_ref(game_state)
            result = station.attack_unit(
                player, target_actor.unit_id, weapon,
                damage_multiplier=damage_multiplier,
                bonus_damage=bonus_damage)
            if not result.get("success"):
                return "❌ 警察目标无效", {}
            lines = [f"⚔️ {player.name} 用「{weapon.name}」攻击 {target_actor.name}！"]
            lines.extend(f"   {detail}" for detail in result.get("details", []))
            game_state.log_event(
                "attack", attacker=player.player_id,
                target=target_actor.player_id, weapon=weapon_name,
                location=getattr(target_actor, "location", None),
                witnesses=_attack_witnesses(game_state, player, target_actor),
                result=result)
            return "\n".join(lines), result

    # 检查是否是 legacy 警察目标
    if target_id.lower().startswith("police"):
        # 调用警察攻击接口
        if not hasattr(game_state, 'police_engine') or not game_state.police_engine:
            return "❌ 警察系统未初始化", {}

        result = game_state.police_engine.attack_police(
            attacker_id=player.player_id,
            police_target=target_id,
            attack_method=weapon_name
        )
        # 警察攻击返回字符串消息，没有详情dict
        return result, {}

    # 原有玩家攻击逻辑
    target = game_state.get_player(target_id)
    if not target:
        return f"❌ 找不到玩家 {target_id}", {}

    # 解析层和属性
    target_layer = LAYER_MAP.get(layer_str) if layer_str else None
    target_attr = ATTR_MAP.get(attr_str) if attr_str else None

    # 检查目标是否有护甲可选
    if target_layer and target_attr:
        piece = target.armor.get_piece(target_layer, target_attr)
        if not piece:
            # 如果指定了但不存在，自动降级
            target_layer = None
            target_attr = None

    # v2.0 duet: 所有攻击走位移/热力路径（按钮→热力，玩家→位移）
    ish = getattr(game_state, 'ish_bosheth', None)
    duet_displace = (ish is not None and ish.phase == "duet")

    # G3 的远程防壁/拦截先于目标护甲；由普通攻击入口真实承接。
    from models.equipment import WeaponRange
    target_talent = getattr(target, "talent", None)
    if (m9_enabled(game_state) and weapon.weapon_range is WeaponRange.RANGED
            and target_talent is not None
            and hasattr(target_talent, "defend_ranged")):
        raw = max(0, int(round(
            float(weapon.get_effective_damage()) * damage_multiplier
            + bonus_damage)))
        remaining = target_talent.defend_ranged(player, raw)
        if remaining <= 0:
            result = {
                "success": True, "raw_damage": raw, "final_damage": 0,
                "armor_hit": None, "armor_broken": False, "hp_damage": 0,
                "target_hp": target.hp, "target_hp_before": target.hp,
                "stunned": False, "shocked": False, "killed": False,
                "details": ["远程攻击被七重圆环或拦截剑阵承接"],
                "m9_kind": "g3_defended", "absolute_dead": False,
            }
        else:
            from engine.m9.combat import resolve_damage as resolve_m9_damage
            result = resolve_m9_damage(
                attacker=player, target=target, weapon=None,
                game_state=game_state, raw_damage_override=remaining,
                damage_attribute_override=getattr(weapon.attribute, "value", "普通"),
                source_kind="ordinary_ranged_after_g3_defense")
    else:
        result = resolve_damage(
            attacker=player,
            target=target,
            weapon=weapon,
            game_state=game_state,
            target_layer=target_layer,
            target_armor_attr=target_attr,
            ignore_element=ignore_element,
            damage_multiplier=damage_multiplier,
            bonus_damage=bonus_damage,
            displacement_only=duet_displace,
        )

    # 组装消息
    lines = [f"⚔️ {player.name} 用「{weapon.name}」攻击 {target.name}！"]
    for detail in result["details"]:
        lines.append(f"   {detail}")

    game_state.log_event("attack", attacker=player.player_id,
                         target=target_id, weapon=weapon_name,
                         location=getattr(target, "location", None),
                         witnesses=_attack_witnesses(game_state, player, target),
                         result=result)

    return "\n".join(lines), result


def _attack_witnesses(game_state, attacker, target):
    """Bind same-location witnesses to the event at resolution time."""
    location = getattr(target, "location", None)
    if location is None:
        return []
    actors = (game_state.iter_actors() if hasattr(game_state, "iter_actors")
              else game_state.alive_players())
    excluded = {
        getattr(attacker, "player_id", None),
        getattr(target, "player_id", None),
    }
    return [
        actor.player_id for actor in actors
        if actor is not None and actor.is_alive()
        and actor.player_id not in excluded
        and getattr(actor, "location", None) == location
    ]
