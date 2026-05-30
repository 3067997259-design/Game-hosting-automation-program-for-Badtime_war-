"""
engine/action_enumerator.py
───────────────────────────
为 bot_bridge 分层枚举系统生成参数化指令选项。

build_action_options(player, game_state, available_names) → Dict[str, List[str]]
返回每个需要参数的 action type 的完整合法参数组合列表。

设计原则：
- 复制 rl/action_space.py 中的枚举逻辑（保持行为一致），但输出字符串而非 mask 索引。
- 不引入对 rl/ 模块的依赖（避免循环导入）。
- 只枚举 available_names 中实际包含的 action type。
"""

from __future__ import annotations
from typing import TYPE_CHECKING, Dict, List, Optional, Set

if TYPE_CHECKING:
    from models.player import Player
    from engine.game_state import GameState

# 共享查找表已迁至 engine/action_tables.py（单一数据源）
from engine.action_tables import (
    LOCATIONS, INTERACT_ITEMS, ITEM_LOCATIONS, WEAPONS, SPELL_PREREQUISITES,
    normalize_location as _normalize_location,
    player_owned_names as _player_owned_names,
)


# ══════════════════════════════════════════════════════════════════
#  辅助函数（_normalize_location / _player_owned_names 已迁至 action_tables）
# ══════════════════════════════════════════════════════════════════

def _get_opponents(player: Player, game_state: GameState) -> List[Player]:
    """获取所有存活对手列表（按 player_order 顺序，排除自身）"""
    opponents: List[Player] = []
    for pid in getattr(game_state, 'player_order', []):
        if pid == player.player_id:
            continue
        p = game_state.get_player(pid)
        if p and p.is_alive():
            opponents.append(p)
    return opponents


# ══════════════════════════════════════════════════════════════════
#  主入口
# ══════════════════════════════════════════════════════════════════

def build_action_options(
    player: Player,
    game_state: GameState,
    available_names: List[str],
) -> Dict[str, List[str]]:
    """为每个需要参数的 action type 枚举合法参数组合。

    只枚举 available_names 中出现的 action type（即服务端已判定该大类可用）。
    返回的 dict key 为 action type（如 "move"），value 为完整指令字符串列表。
    对自身即合法指令的 action type（forfeit, wake, assemble, track_guide,
    recruit, election, study, police_command）不在此枚举，由 bot_bridge 直接使用。
    """
    options: Dict[str, List[str]] = {}
    name_set = set(available_names)

    # ── move ──────────────────────────────────────────────────────
    if "move" in name_set:
        opts = _enumerate_move(player)
        if opts:
            options["move"] = opts

    # ── interact ──────────────────────────────────────────────────
    if "interact" in name_set:
        opts = _enumerate_interact(player, game_state)
        if opts:
            options["interact"] = opts

    # ── 需要对手列表的 action type ────────────────────────────────
    opponents = _get_opponents(player, game_state)
    markers = game_state.markers

    # G2 ish-bosheth 情绪限制：过滤合法对手
    if getattr(game_state, 'ish_bosheth', None) and game_state.ish_bosheth.phase == "active":
        from engine.ish_bosheth import STRAPPANDO, ACCAREZZEVOLE, INDIFFERENZA
        attacker_emotion = getattr(player, 'emotion', None)
        g2_owner_id = game_state.ish_bosheth.g2_owner_id
        if attacker_emotion == STRAPPANDO:
            opponents = [o for o in opponents if o.player_id == g2_owner_id]
        elif attacker_emotion in (ACCAREZZEVOLE, INDIFFERENZA):
            opponents = [o for o in opponents if o.player_id != g2_owner_id]

    # ── lock ──────────────────────────────────────────────────────
    if "lock" in name_set:
        opts = _enumerate_lock(player, opponents, markers)
        if opts:
            options["lock"] = opts

    # ── find ──────────────────────────────────────────────────────
    if "find" in name_set:
        opts = _enumerate_find(player, opponents, markers)
        if opts:
            options["find"] = opts

    # ── attack ────────────────────────────────────────────────────
    if "attack" in name_set:
        opts = _enumerate_attack(player, opponents, markers)
        if opts:
            options["attack"] = opts

    # ── special ───────────────────────────────────────────────────
    if "special" in name_set:
        opts = _enumerate_special(player, game_state)
        if opts:
            options["special"] = opts

    # ── report / designate（需要目标参数）──────────────────────────
    if "report" in name_set:
        opts = _enumerate_report(game_state, opponents)
        if opts:
            options["report"] = opts

    if "designate" in name_set:
        opts = _enumerate_designate(opponents)
        if opts:
            options["designate"] = opts

    return options


# ══════════════════════════════════════════════════════════════════
#  各 action type 枚举子函数
# ══════════════════════════════════════════════════════════════════

def _enumerate_move(player: Player) -> List[str]:
    """枚举可移动到的地点"""
    norm_loc = _normalize_location(player.location)
    has_supernova = bool(
        getattr(getattr(player, "talent", None), "has_supernova", False)
    )
    # 架盾时禁止 move
    shield_mode = getattr(getattr(player, "talent", None), "shield_mode", None)
    if shield_mode == "架盾":
        return []

    result: List[str] = []
    for loc in LOCATIONS:
        if loc != norm_loc:
            result.append(f"move {loc}")
        elif has_supernova:
            result.append(f"move {loc}")
    return result


def _enumerate_interact(player: Player, game_state: GameState) -> List[str]:
    """枚举当前位置可交互的所有项目"""
    norm_loc = _normalize_location(player.location)
    if not norm_loc:
        return []

    has_voucher = player.vouchers >= 1
    has_pass = getattr(player, 'has_military_pass', False)
    learned_spells: Set[str] = getattr(player, 'learned_spells', set()) or set()
    owned_items: Set[str] = set(
        getattr(i, 'name', '')
        for i in (getattr(player, 'items', None) or []) if i
    )
    owned_weapons: Set[str] = set(
        getattr(w, 'name', '')
        for w in (getattr(player, 'weapons', None) or []) if w
    )
    virus_active = getattr(getattr(game_state, 'virus', None), 'is_active', False)

    # 商店需要凭证的物品（小刀在家免费，在商店不免费；README §6.3）
    STORE_VOUCHER_ITEMS = {"小刀", "磨刀石", "隐身衣", "热成像仪", "陶瓷护甲"}
    MILITARY_NEEDS_PASS = {"AT力场", "电磁步枪", "导弹控制权", "高斯步枪", "雷达", "隐形涂层"}
    SURGERY_ITEMS = {"晶化皮肤手术", "额外心脏手术", "不老泉手术"}

    result: List[str] = []
    for item in INTERACT_ITEMS:
        # 1. 地点匹配
        valid_locations = ITEM_LOCATIONS.get(item, set())
        if norm_loc not in valid_locations:
            continue

        # 2. 凭证检查（按地点区分：商店购买/医院手术/防毒面具）
        if norm_loc == "商店" and item in STORE_VOUCHER_ITEMS and not has_voucher and not virus_active:
            continue
        if item == "防毒面具" and not has_voucher:
            if norm_loc == "医院":
                continue
            if norm_loc == "商店" and not virus_active:
                continue
        if item in MILITARY_NEEDS_PASS and not has_pass:
            continue
        if item in SURGERY_ITEMS and not has_voucher:
            continue

        # 3. 法术前置检查
        prereq = SPELL_PREREQUISITES.get(item)
        if prereq and prereq not in learned_spells:
            continue

        # 4. 凭证/打工：已有凭证时不允许
        if item in ("凭证", "打工") and has_voucher:
            continue

        # 5. 所有权 / 护甲槽位检查
        if item in learned_spells and item != "AT力场":
            continue
        if item == "小刀" and "小刀" in owned_weapons:
            continue
        if item == "盾牌":
            from models.equipment import make_armor as _ma
            _test = _ma("盾牌")
            if _test:
                can_eq, _ = player.armor.check_can_equip(_test)
                if not can_eq:
                    continue
        if item == "陶瓷护甲":
            from models.equipment import make_armor as _ma2
            _test2 = _ma2("陶瓷护甲")
            if _test2:
                can_eq2, _ = player.armor.check_can_equip(_test2)
                if not can_eq2:
                    continue
        if item in SURGERY_ITEMS:
            surgery_armor_map = {
                "晶化皮肤手术": "晶化皮肤",
                "额外心脏手术": "额外心脏",
                "不老泉手术": "不老泉",
            }
            from models.equipment import ArmorPiece, ArmorLayer
            from utils.attribute import Attribute
            armor_name = surgery_armor_map[item]
            attr_map = {
                "晶化皮肤": Attribute.TECH,
                "额外心脏": Attribute.ORDINARY,
                "不老泉": Attribute.MAGIC,
            }
            test_piece = ArmorPiece(armor_name, attr_map[armor_name], ArmorLayer.INNER, 1.0)
            can_eq3, _ = player.armor.check_can_equip(test_piece)
            if not can_eq3:
                continue
        if item == "磨刀石":
            if "磨刀石" in owned_items:
                continue
            has_unsharpened = any(
                getattr(w, 'name', '') == "小刀" and getattr(w, 'base_damage', 0) < 2
                for w in (getattr(player, 'weapons', None) or []) if w
            )
            if not has_unsharpened:
                continue
        if item in ("隐身衣", "隐形涂层"):
            if getattr(player, 'is_invisible', False):
                continue
            if item in owned_items:
                continue
        if item in ("热成像仪", "雷达"):
            if getattr(player, 'has_detection', False):
                continue
        if item == "防毒面具":
            if "防毒面具" in owned_items:
                continue
        if item in ("电磁步枪", "高斯步枪"):
            if item in owned_weapons:
                continue
        if item == "AT力场":
            from models.equipment import ArmorPiece, ArmorLayer
            from utils.attribute import Attribute
            test_at = ArmorPiece(
                "AT力场", Attribute.TECH, ArmorLayer.OUTER, 1.0, can_regen=True
            )
            can_eq_at, _ = player.armor.check_can_equip(test_at)
            if not can_eq_at:
                continue
        if item == "办理通行证":
            if getattr(player, 'has_military_pass', False):
                continue
        if item == "导弹控制权":
            if markers := getattr(game_state, 'markers', None):
                if markers.has(player.player_id, "MISSILE_CTRL"):
                    continue

        result.append(f"interact {item}")

    return result


def _enumerate_lock(
    player: Player,
    opponents: List[Player],
    markers,
) -> List[str]:
    """枚举可锁定的目标"""
    from models.equipment import WeaponRange

    has_ranged = any(
        getattr(w, 'weapon_range', None) == WeaponRange.RANGED
        and not getattr(w, '_hexagram_disabled', False)
        for w in (getattr(player, 'weapons', None) or []) if w
    )
    if not has_ranged:
        return []

    result: List[str] = []
    for opp in opponents:
        if not opp.is_on_map():
            continue
        already_locked = markers.has_relation(
            opp.player_id, "LOCKED_BY", player.player_id)
        if already_locked:
            continue
        visible = markers.is_visible_to(
            opp.player_id, player.player_id,
            getattr(player, 'has_detection', False))
        if visible:
            result.append(f"lock {opp.name}")
    return result


def _enumerate_find(
    player: Player,
    opponents: List[Player],
    markers,
) -> List[str]:
    """枚举可找到的目标"""
    result: List[str] = []
    for opp in opponents:
        if opp.location != player.location:
            continue
        already_engaged = markers.has_relation(
            player.player_id, "ENGAGED_WITH", opp.player_id)
        if already_engaged:
            continue
        visible = markers.is_visible_to(
            opp.player_id, player.player_id,
            getattr(player, 'has_detection', False))
        if visible:
            result.append(f"find {opp.name}")
    return result


def _enumerate_attack(
    player: Player,
    opponents: List[Player],
    markers,
) -> List[str]:
    """枚举所有合法攻击组合（目标 × 武器）"""
    from models.equipment import WeaponRange

    owned = _player_owned_names(player)
    owned.add("拳击")

    weapon_ranges: Dict[str, WeaponRange] = {}
    for w in (getattr(player, 'weapons', None) or []):
        if w:
            weapon_ranges[w.name] = getattr(w, 'weapon_range', WeaponRange.MELEE)
    weapon_ranges["拳击"] = WeaponRange.MELEE

    result: List[str] = []
    for opp in opponents:
        same_loc = (opp.location == player.location)
        is_engaged = markers.has_relation(
            player.player_id, "ENGAGED_WITH", opp.player_id)
        is_locked = markers.has_relation(
            opp.player_id, "LOCKED_BY", player.player_id)

        for wname in WEAPONS:
            if wname not in owned:
                continue
            wr = weapon_ranges.get(wname, WeaponRange.MELEE)

            # 检查武器是否可用（蓄力、禁用等）
            w_obj = next((w for w in (getattr(player, 'weapons', None) or [])
                          if w and w.name == wname), None)
            if w_obj and getattr(w_obj, 'requires_charge', False) \
                    and getattr(w_obj, 'charge_mandatory', True) \
                    and not getattr(w_obj, 'is_charged', False):
                continue
            if w_obj and getattr(w_obj, '_hexagram_disabled', False):
                continue

            if wr == WeaponRange.MELEE:
                if same_loc and is_engaged:
                    result.append(f"attack {opp.name} {wname}")
            elif wr == WeaponRange.RANGED:
                if is_locked:
                    result.append(f"attack {opp.name} {wname}")
            elif wr == WeaponRange.AREA:
                if same_loc:
                    result.append(f"attack {opp.name} {wname}")

    return result


def _enumerate_special(player: Player, game_state: GameState) -> List[str]:
    """枚举可用的特殊操作（委托给 actions/special_op.get_available_specials）。"""
    from actions.special_op import get_available_specials
    try:
        specs = get_available_specials(player, game_state)
    except Exception:
        return []
    return [f"special {s['name']}" for s in specs]


def _enumerate_report(
    game_state: GameState,
    opponents: List[Player],
) -> List[str]:
    """枚举可举报的目标（有犯罪记录的对手）"""
    result: List[str] = []
    police = getattr(game_state, 'police', None)
    if police is None:
        return result
    for opp in opponents:
        try:
            if police.is_criminal(opp.player_id):
                result.append(f"report {opp.name}")
        except Exception:
            continue
    return result


def _enumerate_designate(opponents: List[Player]) -> List[str]:
    """枚举队长可指定的目标"""
    return [f"designate {opp.name}" for opp in opponents]
