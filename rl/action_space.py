"""  
rl/action_space.py  
──────────────────  
动作空间定义（无天赋局，共 108 个 Discrete 动作）  
  
索引布局：  
  0        : forfeit  
  1        : wake  
  2 –  7   : move <地点>            (6 个地点)  
  8 – 34   : interact <物品/服务>   (27 种)  
  35 – 39  : lock  <对手槽 0-4>  
  40 – 44  : find  <对手槽 0-4>  
  45 – 94  : attack <对手槽 0-4> × <武器槽 0-9>  (5×10=50)  
  95 – 100 : special <操作>         (6 种)  
  101 – 107: 警察行动               (7 种)  
"""  
  
from __future__ import annotations  
from typing import TYPE_CHECKING, List, Optional  
import numpy as np  
  
if TYPE_CHECKING:  
    from models.player import Player  
    from engine.game_state import GameState  

  
# ─────────────────────────────────────────────────────────────────────────────  
#  动作空间大小  
# ─────────────────────────────────────────────────────────────────────────────  
ACTION_COUNT = 108  
  
# ─────────────────────────────────────────────────────────────────────────────  
#  索引偏移常量  
# ─────────────────────────────────────────────────────────────────────────────  
IDX_FORFEIT      = 0  
IDX_WAKE         = 1  
IDX_MOVE_BASE    = 2    # 2 – 7  
IDX_INTERACT_BASE = 8   # 8 – 34  
IDX_LOCK_BASE    = 35   # 35 – 39  
IDX_FIND_BASE    = 40   # 40 – 44  
IDX_ATTACK_BASE  = 45   # 45 – 94  
IDX_SPECIAL_BASE = 95   # 95 – 100  
IDX_POLICE_BASE  = 101  # 101 – 107  
  
# ─────────────────────────────────────────────────────────────────────────────  
#  枚举列表  
# ─────────────────────────────────────────────────────────────────────────────  
  
LOCATIONS: List[str] = [  
    "home", "商店", "魔法所", "医院", "军事基地", "警察局"  
]  
  
INTERACT_ITEMS: List[str] = [  
    # ── home (3) ──────────────────────────────────────────────────  
    "凭证", "小刀", "盾牌",  
    # ── 商店 (6，小刀已在 home 中，不重复) ────────────────────────  
    "打工", "磨刀石", "隐身衣", "热成像仪", "陶瓷护甲", "防毒面具",  
    # ── 魔法所 (8) ────────────────────────────────────────────────  
    "魔法护盾", "魔法弹幕", "远程魔法弹幕",  
    "封闭", "地震", "地动山摇", "隐身术", "探测魔法",  
    # ── 医院 (3，打工/防毒面具已在商店中，不重复) ─────────────────  
    "晶化皮肤手术", "额外心脏手术", "不老泉手术",  
    # ── 军事基地 (7) ──────────────────────────────────────────────  
    "办理通行证", "AT力场", "电磁步枪",  
    "导弹控制权", "高斯步枪", "雷达", "隐形涂层",  
]  
assert len(INTERACT_ITEMS) == 27, f"INTERACT_ITEMS 长度应为 27，实际 {len(INTERACT_ITEMS)}"  
  
WEAPONS: List[str] = [  
    "拳击",         # 0 — 始终可用，无需持有  
    "小刀",         # 1  
    "警棍",         # 2  
    "魔法弹幕",     # 3  
    "远程魔法弹幕", # 4  
    "地震",         # 5  
    "地动山摇",     # 6  
    "电磁步枪",     # 7  
    "高斯步枪",     # 8  
    "导弹",         # 9  
]  
assert len(WEAPONS) == 10  
  
SPECIAL_OPS: List[str] = [  
    "磨刀",         # 95  
    "吟唱魔法护盾", # 96  
    "展开AT力场",   # 97  
    "蓄力电磁步枪", # 98  
    "蓄力高斯步枪", # 99  
    "释放病毒",     # 100  
]  
assert len(SPECIAL_OPS) == 6  
  
# 警察行动 CLI 关键字（report/designate 需要目标，在 idx_to_command 中自动填充）  
POLICE_CMDS: List[str] = [  
    "report",       # 101 — 需要目标，自动选择  
    "assemble",     # 102  
    "track",        # 103  
    "recruit",      # 104  
    "election",     # 105  
    "designate",    # 106 — 需要目标，自动选择  
    "study",        # 107  
]  
assert len(POLICE_CMDS) == 7  
  
# ─────────────────────────────────────────────────────────────────────────────  
#  物品 → 可交互地点映射  
# ─────────────────────────────────────────────────────────────────────────────  
ITEM_LOCATIONS: dict[str, set[str]] = {  
    "凭证":         {"home"},  
    "小刀":         {"home", "商店"},  
    "盾牌":         {"home"},  
    "打工":         {"商店", "医院"},  
    "磨刀石":       {"商店"},  
    "隐身衣":       {"商店"},  
    "热成像仪":     {"商店"},  
    "陶瓷护甲":     {"商店"},  
    "防毒面具":     {"商店", "医院"},  
    "魔法护盾":     {"魔法所"},  
    "魔法弹幕":     {"魔法所"},  
    "远程魔法弹幕": {"魔法所"},  
    "封闭":         {"魔法所"},  
    "地震":         {"魔法所"},  
    "地动山摇":     {"魔法所"},  
    "隐身术":       {"魔法所"},  
    "探测魔法":     {"魔法所"},  
    "晶化皮肤手术": {"医院"},  
    "额外心脏手术": {"医院"},  
    "不老泉手术":   {"医院"},  
    "办理通行证":   {"军事基地"},  
    "AT力场":       {"军事基地"},  
    "电磁步枪":     {"军事基地"},  
    "导弹控制权":   {"军事基地"},  
    "高斯步枪":     {"军事基地"},  
    "雷达":         {"军事基地"},  
    "隐形涂层":     {"军事基地"},  
}  
  
# special op → 触发所需的武器/物品名（None 表示由游戏引擎验证）  
SPECIAL_REQUIRES: dict[str, Optional[str]] = {  
    "磨刀":         "磨刀石",  
    "吟唱魔法护盾": "魔法护盾",  
    "展开AT力场":   "AT力场",  
    "蓄力电磁步枪": "电磁步枪",  
    "蓄力高斯步枪": "高斯步枪",  
    "释放病毒":     None,   # 需在医院且病毒已激活，由引擎验证  
}

# 法术前置依赖（与 magic_institute.PREREQUISITES 保持一致）  
SPELL_PREREQUISITES: dict[str, str] = {  
    "远程魔法弹幕": "魔法弹幕",  
    "地动山摇": "地震",  
}

def _normalize_location(loc: str | None) -> str:  
    """将 home_xxx 归一化为 home，None 归一化为空字符串"""  
    if loc is None:  
        return ""  
    if loc.startswith("home_"):  
        return "home"  
    return loc
  
# ─────────────────────────────────────────────────────────────────────────────  
#  辅助函数  
# ─────────────────────────────────────────────────────────────────────────────  
  
def get_opponent_slots(player, game_state) -> List:  
    """  
    按 player_order 顺序返回最多 5 个对手 Player 对象（排除自身）。  
    不足 5 个时用 None 填充，保证返回列表长度始终为 5。  
    """  
    slots: List = []  
    for pid in game_state.player_order:  
        if pid == player.player_id:  
            continue  
        slots.append(game_state.get_player(pid))  
        if len(slots) == 5:  
            break  
    while len(slots) < 5:  
        slots.append(None)  
    return slots  
  
  
def _auto_target(player, game_state) -> Optional[str]:  
    """  
    自动选择举报/指定目标：  
    report: 从攻击过自己的犯罪者中选 kill_count 最高的  
    designate: 保持原逻辑（kill_count 最高的存活对手）  
    """  
    slots = get_opponent_slots(player, game_state)  
    alive = [p for p in slots if p is not None and p.is_alive()]  
    if not alive:  
        return None  
    return max(alive, key=lambda p: getattr(p, "kill_count", 0)).name  
  
  
def _auto_report_target(player, game_state) -> Optional[str]:  
    """举报专用：只从攻击过自己的犯罪者中选"""  
    slots = get_opponent_slots(player, game_state)  
    alive = [p for p in slots if p is not None and p.is_alive()]  
    if not alive:  
        return None  
      
    # 筛选：攻击过 RL 的犯罪者  
    candidates = []  
    for p in alive:  
        if not getattr(game_state.police, 'is_criminal', lambda x: False)(p.player_id):  
            continue  
        was_attacked = any(  
            e.get("type") == "attack"  
            and e.get("attacker") == p.player_id  
            and e.get("target") == player.player_id  
            for e in game_state.event_log  
        )  
        if was_attacked:  
            candidates.append(p)  
      
    if not candidates:  
        return None  
    return max(candidates, key=lambda p: getattr(p, "kill_count", 0)).name 
  
  
def _player_owned_names(player) -> set[str]:  
    """  
    返回玩家当前持有的所有武器和物品的名称集合。  
    兼容 weapons/items 为对象列表或字符串列表两种情况。  
    """  
    names: set[str] = set()  
    for collection in (player.weapons or [], player.items or []):  
        for obj in collection:  
            names.add(obj.name if hasattr(obj, "name") else str(obj))  
    return names  
  
  
# ─────────────────────────────────────────────────────────────────────────────  
#  核心 API  
# ─────────────────────────────────────────────────────────────────────────────  
  
def idx_to_command(idx: int, player, game_state) -> str:  
    """  
    将动作索引翻译为 CLI 命令字符串。  
    调用方应保证 idx 在 action mask 允许范围内；  
    若因竞态导致目标失效，则安全回退为 "forfeit"。  
    """  
    if idx == IDX_FORFEIT:  
        return "forfeit"  
  
    if idx == IDX_WAKE:  
        return "wake"  
  
    if IDX_MOVE_BASE <= idx < IDX_INTERACT_BASE:  
        return f"move {LOCATIONS[idx - IDX_MOVE_BASE]}"  
  
    if IDX_INTERACT_BASE <= idx < IDX_LOCK_BASE:  
        return f"interact {INTERACT_ITEMS[idx - IDX_INTERACT_BASE]}"  
  
    if IDX_LOCK_BASE <= idx < IDX_FIND_BASE:  
        slot = idx - IDX_LOCK_BASE  
        target = get_opponent_slots(player, game_state)[slot]  
        return f"lock {target.name}" if target else "forfeit"  
  
    if IDX_FIND_BASE <= idx < IDX_ATTACK_BASE:  
        slot = idx - IDX_FIND_BASE  
        target = get_opponent_slots(player, game_state)[slot]  
        return f"find {target.name}" if target else "forfeit"  
  
    if IDX_ATTACK_BASE <= idx < IDX_SPECIAL_BASE:  
        offset      = idx - IDX_ATTACK_BASE  
        target_slot = offset // 10  
        weapon_slot = offset % 10  
        target = get_opponent_slots(player, game_state)[target_slot]  
        if target is None:  
            return "forfeit"  
        return f"attack {target.name} {WEAPONS[weapon_slot]}"  
  
    if IDX_SPECIAL_BASE <= idx < IDX_POLICE_BASE:  
        return f"special {SPECIAL_OPS[idx - IDX_SPECIAL_BASE]}"  
  
    if IDX_POLICE_BASE <= idx < ACTION_COUNT:  
            cmd = POLICE_CMDS[idx - IDX_POLICE_BASE]  
            if cmd == "report":  
                target_name = _auto_report_target(player, game_state)  
                return f"report {target_name}" if target_name else "forfeit"  
            if cmd == "designate":  
                target_name = _auto_target(player, game_state)  
                return f"designate {target_name}" if target_name else "forfeit"  
            return cmd  
  
    raise ValueError(f"动作索引越界: {idx}（合法范围 0–{ACTION_COUNT - 1}）")  
  
  
def build_action_mask(player, game_state, rl_player_id: str) -> np.ndarray:  
    """  
    返回 108 维 bool 数组，True 表示该动作当前合法可选。  
  
    设计原则（保守放行）：  
    - 先用 ActionTurnManager._get_available_actions() 获取粗粒度合法动作类型  
    - 再结合玩家状态细化到具体动作索引  
    - 宁可多放行（游戏引擎做最终验证），不漏掉合法动作  
    """  
    mask = np.zeros(ACTION_COUNT, dtype=bool)  
  
    # 延迟导入，避免循环依赖  
    from engine.action_turn import ActionTurnManager  
  
    atm = ActionTurnManager(game_state)  
    available_names, _ = atm._get_available_actions(player)  
    available_set = set(available_names)  
  
    # ── 未醒来：只能 wake ─────────────────────────────────────────  
    if not player.is_awake:  
        if "wake" in available_set:  
            mask[IDX_WAKE] = True  
        return mask  
  
    # ── forfeit 始终可用 ──────────────────────────────────────────  
    mask[IDX_FORFEIT] = True  
  
    # ── move ─────────────────────────────────────────────────────  
    if "move" in available_set:  
        for i, loc in enumerate(LOCATIONS):  
            if loc != _normalize_location(player.location):
                mask[IDX_MOVE_BASE + i] = True  
  
    # ── interact ─────────────────────────────────────────────────  
    if "interact" in available_set:  
        norm_loc = _normalize_location(player.location)  
        has_voucher = player.vouchers >= 1  
        has_pass = getattr(player, 'has_military_pass', False)  
    
        # Items that are free (no voucher needed)  
        FREE_ITEMS = {"凭证", "小刀", "盾牌", "打工",  
                    "魔法护盾", "魔法弹幕", "远程魔法弹幕", "封闭",  
                    "地震", "地动山摇", "隐身术", "探测魔法",  
                    "办理通行证"}  
        # Items at 商店 that need vouchers (not free)  
        SHOP_NEEDS_VOUCHER = {"磨刀石", "隐身衣", "热成像仪", "陶瓷护甲"}  
        # Items at 军事基地 that need pass  
        MILITARY_NEEDS_PASS = {"AT力场", "电磁步枪", "导弹控制权", "高斯步枪", "雷达", "隐形涂层"}  
        # Items at 医院 that are surgery (need vouchers, consume all)  
        SURGERY_ITEMS = {"晶化皮肤手术", "额外心脏手术", "不老泉手术"}  
    
        for i, item in enumerate(INTERACT_ITEMS):  
            if norm_loc not in ITEM_LOCATIONS.get(item, set()):  
                continue  
            # Check voucher/pass requirements  
            if item in SHOP_NEEDS_VOUCHER and not has_voucher and not game_state.virus.is_active:  
                continue
            # 防毒面具：商店需凭证（病毒期间免费），医院始终需凭证  
            if item == "防毒面具" and not has_voucher:  
                if norm_loc == "医院":  
                    continue  
                if norm_loc == "商店" and not game_state.virus.is_active:  
                    continue
            if item in MILITARY_NEEDS_PASS and not has_pass:  
                continue  
            if item in SURGERY_ITEMS and not has_voucher:  
                continue  
            # 法术前置检查  
            prereq = SPELL_PREREQUISITES.get(item)  
            if prereq and prereq not in getattr(player, 'learned_spells', set()):  
                continue
            mask[IDX_INTERACT_BASE + i] = True
  
    # ── 对手槽位存活状态（lock / find / attack 共用）─────────────  
    opponents = get_opponent_slots(player, game_state)  
    alive_flags = [  
        (p is not None and p.is_alive())  
        for p in opponents  
    ]  
  
    # ── lock ─────────────────────────────────────────────────────  
    if "lock" in available_set:  
            for slot, (opp, alive) in enumerate(zip(opponents, alive_flags)):  
                if alive and opp is not None and opp.is_on_map():  
                    visible = game_state.markers.is_visible_to(  
                        opp.player_id, player.player_id, player.has_detection)  
                    if visible:  
                        mask[IDX_LOCK_BASE + slot] = True
  
    # ── find ─────────────────────────────────────────────────────  
    if "find" in available_set:  
            for slot, (opp, alive) in enumerate(zip(opponents, alive_flags)):  
                if alive and opp is not None and opp.location == player.location:  
                    visible = game_state.markers.is_visible_to(  
                        opp.player_id, player.player_id, player.has_detection)  
                    if visible:  
                        mask[IDX_FIND_BASE + slot] = True
  
    # ── attack ────────────────────────────────────────────────────  
    if "attack" in available_set:  
        from models.equipment import WeaponRange  
        owned = _player_owned_names(player)  
        owned.add("拳击")  
    
        # Pre-compute weapon range lookup  
        weapon_ranges = {}  
        for w in (player.weapons or []):  
            if w:  
                weapon_ranges[w.name] = getattr(w, 'weapon_range', WeaponRange.MELEE)  
        weapon_ranges["拳击"] = WeaponRange.MELEE  # 拳击 is always melee  
    
        markers = game_state.markers  
    
        for slot, (opp, alive) in enumerate(zip(opponents, alive_flags)):  
            if not alive or opp is None:  
                continue  
            same_loc = (opp.location == player.location)  
            is_engaged = markers.has_relation(player.player_id, "ENGAGED_WITH", opp.player_id)  
            is_locked = markers.has_relation(opp.player_id, "LOCKED_BY", player.player_id)  
    
            for wi, wname in enumerate(WEAPONS):  
                if wname not in owned:  
                    continue  
                wr = weapon_ranges.get(wname, WeaponRange.MELEE)
                w_obj = next((w for w in (player.weapons or []) if w and w.name == wname), None)  
                if w_obj and w_obj.requires_charge and getattr(w_obj, 'charge_mandatory', True) and not w_obj.is_charged:  
                    continue  # 需要蓄力但未蓄力，跳过    
                if wr == WeaponRange.MELEE:  
                    if same_loc and is_engaged:  
                        mask[IDX_ATTACK_BASE + slot * 10 + wi] = True  
                elif wr == WeaponRange.RANGED:  
                    if is_locked:  
                        mask[IDX_ATTACK_BASE + slot * 10 + wi] = True  
                elif wr == WeaponRange.AREA:  
                    if same_loc:  
                        mask[IDX_ATTACK_BASE + slot * 10 + wi] = True
  
 
    # ── special ───────────────────────────────────────────────────  
    if "special" in available_set:  
        owned = _player_owned_names(player)  
        for si, op in enumerate(SPECIAL_OPS):  
            req = SPECIAL_REQUIRES[op]  
            if req is None:  
                # 释放病毒：检查在医院且病毒未激活  
                if op == "释放病毒":  
                    if player.location == "医院" and not game_state.virus.is_active:  
                        mask[IDX_SPECIAL_BASE + si] = True  
                else:  
                    mask[IDX_SPECIAL_BASE + si] = True  
            elif req in owned:  
                mask[IDX_SPECIAL_BASE + si] = True
  
    # ── 警察行动 ──────────────────────────────────────────────────  
    if game_state.police_engine:  
        police_available_map = {  
            "report":      (0, "report"      in available_set),  
            "assemble":    (1, "assemble"    in available_set),  
            "track_guide": (2, "track_guide" in available_set),  
            "recruit":     (3, "recruit"     in available_set),  
            "election":    (4, "election"    in available_set),  
            "designate":   (5, "designate"   in available_set),  
            "study":       (6, "study"       in available_set),  
        }  
        has_alive_target = any(alive_flags)  
        for key, (offset, ok) in police_available_map.items():  
            if not ok:  
                continue  
            if key in ("report", "designate") and not has_alive_target:  
                continue  
            # 新增：report 需要有合法举报目标（被攻击过的犯罪者）  
            if key == "report" and _auto_report_target(player, game_state) is None:  
                continue  
            mask[IDX_POLICE_BASE + offset] = True 
  
    return mask