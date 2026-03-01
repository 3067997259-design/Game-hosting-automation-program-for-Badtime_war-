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
      target_id: 目标玩家ID
      weapon_name: 使用的武器名
      game_state: 游戏状态
      layer_str: 攻击层字符串（"外层"/"内层"），可选
      attr_str: 护甲属性字符串（"普通"/"魔法"/"科技"），可选
      ignore_element: 无视克制
      damage_multiplier: 伤害倍率
      bonus_damage: 额外伤害

    返回 (结果消息str, 结算详情dict)
    """
    target = game_state.get_player(target_id)
    if not target:
        return f"❌ 找不到玩家 {target_id}", {}

    weapon = player.get_weapon(weapon_name)
    if not weapon:
        return f"❌ 你没有武器「{weapon_name}」", {}

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

    # 执行伤害结算
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
    )

    # 组装消息
    lines = [f"⚔️ {player.name} 用「{weapon.name}」攻击 {target.name}！"]
    for detail in result["details"]:
        lines.append(f"   {detail}")


    game_state.log_event("attack", attacker=player.player_id,
                         target=target_id, weapon=weapon_name,
                         result=result)

    return "\n".join(lines), result
