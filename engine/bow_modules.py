"""弓升级模块系统（M4，experiment: m4_gear，v2.0 §2.8）。

- 双槽：每人最多 2 个模块；同名双装强化；无限模块独占双槽（全图 ×1 神器）
- 份数稀缺：每种全图 ×N（balance bow_modules.supply），抢完即止；
  死亡回流供应池（掉落地面系统归 M5 击杀掉落，先回流保稀缺品不灭失）
- 模块效果只接 shoot 路径（弓专属语法），不碰通用 attack
"""
from __future__ import annotations
import random
from typing import Any, Dict, List, Tuple

from engine.balance import get as bget


def module_specs() -> Dict[str, Dict]:
    """模块注册表（balance 驱动，剔除注释键）。"""
    raw = bget("bow_modules", default={}) or {}
    return {k: v for k, v in raw.items()
            if isinstance(v, dict) and not k.startswith("_")}


def init_supply(game_state: Any) -> None:
    """开局初始化全图供应池（GameState.__init__ 在 m4 下调用）。"""
    game_state.module_supply = {
        name: spec.get("supply", 2) for name, spec in module_specs().items()
    }


def supply_left(game_state: Any, name: str) -> int:
    return getattr(game_state, "module_supply", {}).get(name, 0)


def can_install(player: Any, name: str, game_state: Any) -> Tuple[bool, str]:
    """安装校验：供应/双槽/无限独占/同名最多 ×2。"""
    spec = module_specs().get(name)
    if spec is None:
        return False, f"没有「{name}」这种模块"
    if supply_left(game_state, name) <= 0:
        return False, f"「{name}」模块全图份数已被取完"
    slots = list(player.bow_modules)
    if "无限" in slots:
        return False, "无限模块独占双槽，无法再安装其他模块"
    if spec.get("exclusive") and slots:
        return False, "无限模块独占双槽——先拆下现有模块"
    if len(slots) >= 2:
        return False, "弓的模块槽已满（2/2），先拆卸"
    if slots.count(name) >= 2:
        return False, f"「{name}」最多双装"
    return True, ""


def install(player: Any, name: str, game_state: Any) -> str:
    """安装模块（调用方先过 can_install 与付费）。"""
    player.bow_modules.append(name)
    game_state.module_supply[name] -= 1
    spec = module_specs().get(name, {})
    if spec.get("exclusive"):
        return f"♾️ {player.name} 安装了「{name}」模块（独占双槽：不耗箭，恒基础箭）"
    doubled = "（双装强化！）" if player.bow_modules.count(name) == 2 else ""
    return f"🔧 {player.name} 安装了「{name}」模块{doubled}（{len(player.bow_modules)}/2 槽）"


def uninstall(player: Any, name: str, game_state: Any) -> Tuple[bool, str]:
    """拆卸模块（1 行动随处可做，拍板 §13-16）；回流供应池。"""
    if name not in player.bow_modules:
        return False, f"你没有安装「{name}」模块"
    player.bow_modules.remove(name)
    game_state.module_supply[name] = game_state.module_supply.get(name, 0) + 1
    return True, f"🔩 {player.name} 拆下了「{name}」模块（已回流市场）"


def release_on_death(player: Any, game_state: Any) -> None:
    """死亡：全部模块回流供应池（稀缺品不灭失）。"""
    supply = getattr(game_state, "module_supply", None)
    if supply is None:
        return
    for name in player.bow_modules:
        supply[name] = supply.get(name, 0) + 1
    player.bow_modules = []


def compute_shot(player: Any) -> Dict[str, Any]:
    """按已装模块换算本次射击的武器形态与附带效果。

    返回 {weapon, infinite, burn_stacks, knockback, knockback_initiative_penalty,
          hit_bonus}。weapon 为新建临时 Weapon（不污染玩家武器栏的基础弓）。
    """
    from models.equipment import Weapon, WeaponRange
    from utils.attribute import Attribute

    specs = module_specs()
    slots = list(player.bow_modules)
    counts = {name: slots.count(name) for name in set(slots)}

    damage = bget("bow", "damage", default=3)
    attr = Attribute.ORDINARY
    infinite = False
    burn = 0
    knockback = False
    knockback_pen = 0
    hit_bonus = 0

    for name, n in counts.items():
        spec = specs.get(name, {})
        if spec.get("infinite_ammo"):
            infinite = True
            continue  # 无限恒基础箭：不吃其他加成（独占双槽本就排他）
        if "damage_bonus" in spec:
            damage += spec["damage_bonus_x2"] if n >= 2 else spec["damage_bonus"]
        if "attribute" in spec:
            attr = {"魔法": Attribute.MAGIC, "科技": Attribute.TECH}.get(
                spec["attribute"], Attribute.ORDINARY)
        if "burn_stacks" in spec:
            burn += spec["burn_stacks_x2"] if n >= 2 else spec["burn_stacks"]
        if "hit_bonus" in spec:
            hit_bonus += spec["hit_bonus_x2"] if n >= 2 else spec["hit_bonus"]
        if spec.get("knockback"):
            knockback = True
            if n >= 2:
                knockback_pen = spec.get("knockback_x2_initiative", -2)

    weapon = Weapon("弓", attr, damage, WeaponRange.RANGED,
                    special_tags=["bow", "no_lock_required"])
    weapon._bow_hit_bonus = hit_bonus  # accuracy 消费（魔导制导）
    return {
        "weapon": weapon, "infinite": infinite, "burn_stacks": burn,
        "knockback": knockback, "knockback_initiative_penalty": knockback_pen,
        "hit_bonus": hit_bonus,
    }


def apply_burn(target: Any, stacks: int) -> str:
    """施加灼烧（通用层，上限 balance burn.max_stacks）。"""
    cap = bget("burn", "max_stacks", default=3)
    before = getattr(target, "burn_stacks", 0)
    target.burn_stacks = min(cap, before + stacks)
    return f"🔥 {target.name} 灼烧 +{target.burn_stacks - before}（{target.burn_stacks}/{cap} 层）"


def apply_knockback(target: Any, attacker: Any, game_state: Any,
                    initiative_penalty: int = 0) -> str:
    """冲击模块：同地点命中 → 击退至随机其他地点（复用 t4 强制位移模式）。

    随机目的地与六爻的指定放逐区分（拍板）；被动位移不触发借机攻击
    （不走 move.execute）。
    """
    from actions.move import get_all_valid_locations
    candidates = [loc for loc in get_all_valid_locations(game_state)
                  if loc != target.location]
    if not candidates:
        return "（无处可退）"
    destination = random.choice(candidates)
    target.location = destination
    game_state.markers.on_player_move(target.player_id)
    if initiative_penalty:
        target._resist_degrade_penalty = initiative_penalty  # 复用自消耗先攻 flag
    game_state.log_event("knockback", attacker=attacker.player_id,
                         target=target.player_id, to_loc=destination)
    pen_note = f"，下轮先攻 {initiative_penalty}" if initiative_penalty else ""
    return f"💨 {target.name} 被冲击箭击退至 {destination}{pen_note}！"
