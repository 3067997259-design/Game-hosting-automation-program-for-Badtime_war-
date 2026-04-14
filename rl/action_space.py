"""
rl/action_space.py
──────────────────
动作空间定义（天赋局，共 124 个 Discrete 动作）

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
  ── 以下为天赋扩展 ──
  108 – 112: talent_t0_activate <对手槽 0-4>  (5 个，选目标发动天赋)
  113      : talent_t0_self                   (1 个，对自己发动天赋)
  114 – 123: choose_option <0-9>              (10 个，用于 choose 同步)
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
ACTION_COUNT = 124

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

# ── 天赋扩展 ──
IDX_TALENT_T0_TARGET_BASE = 108  # 108 – 112
IDX_TALENT_T0_SELF        = 113
IDX_CHOOSE_BASE           = 114  # 114 – 123

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

# ─────────────────────────────────────────────────────────────────────────────
#  choose 同步：战略性 situation 列表
#  这些 situation 的 choose() 调用会交给 RL 决策，而非启发式
# ─────────────────────────────────────────────────────────────────────────────
STRATEGIC_CHOOSE_SITUATIONS: set[str] = {
    # ── 基础机制 ──
    "petrified",                # 石化解除 vs 保持
    "recruit_pick_1",           # 加入警察选奖励 1
    "recruit_pick_2",           # 加入警察选奖励 2
    "captain_election",         # 竞选队长
    # ── 天赋 T0 ──
    "talent_t0",                # 是否发动天赋（特殊处理：可用 T0_TARGET/SELF 索引）
    # ── 天赋子决策 ──
    "oneslash_pick_target",     # T1：一刀缭断选目标
    "hexagram_pick_target",     # T4：六爻选目标
    "hexagram_my_choice",       # T4：六爻发动者出拳
    "hexagram_opp_choice",      # T4：六爻对手出拳
    "resurrection_pick_target", # T7：死者苏生选目标
    "hologram_target",          # G2：全息影像选目标
    "mythland_pick_target",     # G3：神话之外选拉入目标
    "savior_activate",          # G4：愿负世主动发动（涟漪强化后）
    "ripple_anchor_type",       # G5：锚定事件类型
    "ripple_anchor_target",     # G5：锚定目标
    "ripple_poem_target",       # G5：献诗目标
    "cutaway_borrow_target",    # G6：插入式笑话借用目标
    "hoshino_form_choice",      # G7：形态选择
    "hoshino_self_doubt_choice", # G7：自我怀疑接受/拒绝
}

# 纯随机 / 无博弈空间的 situation → 保持启发式
HEURISTIC_CHOOSE_SITUATIONS: set[str] = {
    "mythland_rps",             # 结界猜拳：纯随机
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

    注意：天赋 T0 索引 (108-113) 和 choose 索引 (114-123) 不会经过此函数——
    它们在 _SyncRLController.choose() 中直接被解释为选项索引。
    如果意外到达这里，安全回退为 forfeit。
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

    if IDX_POLICE_BASE <= idx < IDX_TALENT_T0_TARGET_BASE:
        cmd = POLICE_CMDS[idx - IDX_POLICE_BASE]
        if cmd == "report":
            target_name = _auto_report_target(player, game_state)
            return f"report {target_name}" if target_name else "forfeit"
        if cmd == "designate":
            target_name = _auto_target(player, game_state)
            return f"designate {target_name}" if target_name else "forfeit"
        return cmd

    # ── 天赋 T0 / choose 索引：不应走 idx_to_command ──
    # 这些索引在 choose 同步路径中被直接解释，不经过此函数。
    # 如果意外到达这里（如 env 模式切换 bug），安全回退。
    if IDX_TALENT_T0_TARGET_BASE <= idx <= IDX_TALENT_T0_SELF:
        return "forfeit"

    if IDX_CHOOSE_BASE <= idx < IDX_CHOOSE_BASE + 10:
        return "forfeit"

    raise ValueError(f"动作索引越界: {idx}（合法范围 0–{ACTION_COUNT - 1}）")


# ─────────────────────────────────────────────────────────────────────────────
#  choose 模式辅助：将 RL 动作索引翻译为 choose 选项
# ─────────────────────────────────────────────────────────────────────────────

def idx_to_choose_option(
    idx: int,
    options: List[str],
    situation: str,
    player,
    game_state,
) -> str:
    """
    在 choose 同步模式下，将 RL 输出的动作索引翻译为 options 中的选项字符串。

    对于 talent_t0 situation：
      - IDX_TALENT_T0_TARGET_BASE + slot → "发动天赋"（目标信息存入 player._rl_t0_target_slot）
      - IDX_TALENT_T0_SELF              → "发动天赋"
      - IDX_CHOOSE_BASE + 1             → "不发动，正常行动"

    对于其他 situation：
      - IDX_CHOOSE_BASE + i             → options[i]

    返回
    ----
    options 中的一个字符串
    """
    if situation == "talent_t0":
        # 天赋 T0 特殊处理：T0_TARGET / T0_SELF → "发动天赋"
        if IDX_TALENT_T0_TARGET_BASE <= idx <= IDX_TALENT_T0_TARGET_BASE + 4:
            target_slot = idx - IDX_TALENT_T0_TARGET_BASE
            # 存储目标槽位，供后续 execute_t0 内的 choose 调用使用
            player._rl_t0_target_slot = target_slot
            # 返回"发动天赋"
            for opt in options:
                if "发动" in opt:
                    return opt
            return options[0]

        if idx == IDX_TALENT_T0_SELF:
            player._rl_t0_target_slot = -1  # -1 表示自身
            for opt in options:
                if "发动" in opt:
                    return opt
            return options[0]

        # 其他索引（如 IDX_CHOOSE_BASE + 1）→ "不发动"
        if IDX_CHOOSE_BASE <= idx < IDX_CHOOSE_BASE + 10:
            opt_idx = idx - IDX_CHOOSE_BASE
            if opt_idx < len(options):
                return options[opt_idx]
            return options[-1]

        # 安全回退：不发动
        for opt in options:
            if "不发动" in opt or "正常" in opt:
                return opt
        return options[-1]

    # ── 通用 choose：直接映射到 options 索引 ──
    if IDX_CHOOSE_BASE <= idx < IDX_CHOOSE_BASE + 10:
        opt_idx = idx - IDX_CHOOSE_BASE
        if opt_idx < len(options):
            return options[opt_idx]
        return options[-1]  # 越界保护

    # ── 安全回退 ──
    return options[0]


# ─────────────────────────────────────────────────────────────────────────────
#  Action Mask
# ─────────────────────────────────────────────────────────────────────────────

def build_action_mask(
    player,
    game_state,
    rl_player_id: str,
    *,
    choose_mode: bool = False,
    choose_situation: str = "",
    choose_options: Optional[List[str]] = None,
) -> np.ndarray:
    """
    返回 124 维 bool 数组，True 表示该动作当前合法可选。

    参数
    ----
    player       : RL 玩家对象
    game_state   : 当前游戏状态
    rl_player_id : RL 玩家 ID
    choose_mode  : 是否处于 choose 同步模式
    choose_situation : choose 的 situation 字符串
    choose_options   : choose 的选项列表

    设计原则：
    - choose_mode=False 时：构建正常 get_command 模式的 mask（索引 0-107）
    - choose_mode=True  时：构建 choose 模式的 mask（索引 108-123）
    - 两种模式互斥，不会同时启用
    """
    mask = np.zeros(ACTION_COUNT, dtype=bool)

    # ══════════════════════════════════════════════════════════════════════════
    #  choose 模式：只启用 108-123 范围的索引
    # ══════════════════════════════════════════════════════════════════════════
    if choose_mode:
        return _build_choose_mask(mask, player, game_state, choose_situation,
                                  choose_options or [])

    # ══════════════════════════════════════════════════════════════════════════
    #  正常 get_command 模式：索引 0-107（与原有逻辑完全一致）
    # ══════════════════════════════════════════════════════════════════════════

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

        learned_spells = getattr(player, 'learned_spells', set())
        owned_items = set(getattr(i, 'name', '') for i in (player.items or []))
        owned_weapons = set(getattr(w, 'name', '') for w in (player.weapons or []) if w)

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

            # 凭证/打工：已有凭证时不允许
            if item in ("凭证", "打工") and has_voucher:
                continue

            # === Ownership checks ===
            # 法术：已学会则跳过（AT力场除外，可重新展开）
            if item in learned_spells and item != "AT力场":
                continue
            # 小刀：已有则跳过
            if item == "小刀" and "小刀" in owned_weapons:
                continue
            # 盾牌：已有同名护甲则跳过
            if item == "盾牌":
                from models.equipment import make_armor as _test_ma
                _test = _test_ma("盾牌")
                if _test:
                    can_eq, _ = player.armor.check_can_equip(_test)
                    if not can_eq:
                        continue
            # 陶瓷护甲：已有同名护甲则跳过
            if item == "陶瓷护甲":
                from models.equipment import make_armor as _test_ma2
                _test2 = _test_ma2("陶瓷护甲")
                if _test2:
                    can_eq2, _ = player.armor.check_can_equip(_test2)
                    if not can_eq2:
                        continue
            # 手术：已有同名内层护甲则跳过
            if item in ("晶化皮肤手术", "额外心脏手术", "不老泉手术"):
                surgery_armor_map = {
                    "晶化皮肤手术": "晶化皮肤",
                    "额外心脏手术": "额外心脏",
                    "不老泉手术": "不老泉",
                }
                from models.equipment import ArmorPiece, ArmorLayer
                from utils.attribute import Attribute
                armor_name = surgery_armor_map[item]
                attr_map = {"晶化皮肤": Attribute.TECH, "额外心脏": Attribute.ORDINARY, "不老泉": Attribute.MAGIC}
                test_piece = ArmorPiece(armor_name, attr_map[armor_name], ArmorLayer.INNER, 1.0)
                can_eq3, _ = player.armor.check_can_equip(test_piece)
                if not can_eq3:
                    continue
            # 磨刀石：已有磨刀石 或 没有未磨小刀 则跳过
            if item == "磨刀石":
                if "磨刀石" in owned_items:
                    continue
                has_unsharpened = any(
                    getattr(w, 'name', '') == "小刀" and getattr(w, 'base_damage', 0) < 2
                    for w in (player.weapons or []) if w
                )
                if not has_unsharpened:
                    continue
            # 隐身衣/隐形涂层：已隐身则跳过
            if item in ("隐身衣", "隐形涂层"):
                if getattr(player, 'is_invisible', False):
                    continue
                if item in owned_items:
                    continue
            # 热成像仪/雷达：已有探测则跳过
            if item in ("热成像仪", "雷达"):
                if getattr(player, 'has_detection', False):
                    continue
            # 防毒面具：已有则跳过
            if item == "防毒面具":
                if "防毒面具" in owned_items:
                    continue
            # 电磁步枪/高斯步枪：已有则跳过
            if item in ("电磁步枪", "高斯步枪"):
                if item in owned_weapons:
                    continue
            if item == "AT力场":
                from models.equipment import ArmorPiece, ArmorLayer
                from utils.attribute import Attribute
                test_at = ArmorPiece("AT力场", Attribute.TECH, ArmorLayer.OUTER, 1.0, can_regen=True)
                can_eq_at, _ = player.armor.check_can_equip(test_at)
                if not can_eq_at:
                    continue
            # 办理通行证：已有则跳过
            if item == "办理通行证":
                if getattr(player, 'has_military_pass', False):
                    continue
            # 导弹控制权：已有控制权标记则跳过
            if item == "导弹控制权":
                if game_state and game_state.markers.has(player.player_id, "MISSILE_CTRL"):
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
            from models.equipment import WeaponRange
            has_ranged_weapon = any(
                getattr(w, 'weapon_range', None) == WeaponRange.RANGED
                and not getattr(w, '_hexagram_disabled', False)
                for w in (player.weapons or []) if w
            )
            if has_ranged_weapon:
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
        weapon_ranges["拳击"] = WeaponRange.MELEE

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
                    continue
                if w_obj and getattr(w_obj, '_hexagram_disabled', False):
                    continue
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
        learned = getattr(player, 'learned_spells', set())
        for si, op in enumerate(SPECIAL_OPS):
            req = SPECIAL_REQUIRES[op]
            if req is None:
                if op == "释放病毒":
                    if player.location == "医院" and not game_state.virus.is_active:
                        mask[IDX_SPECIAL_BASE + si] = True
                else:
                    mask[IDX_SPECIAL_BASE + si] = True
            elif op in ("吟唱魔法护盾", "展开AT力场"):
                if req in learned:
                    from models.equipment import ArmorLayer
                    from utils.attribute import Attribute
                    attr = Attribute.MAGIC if req == "魔法护盾" else Attribute.TECH
                    piece = player.armor.get_piece(ArmorLayer.OUTER, attr)
                    if piece is None:
                        mask[IDX_SPECIAL_BASE + si] = True
            elif req in owned:
                if op == "磨刀":
                    has_unsharpened = any(
                        getattr(w, 'name', '') == "小刀" and getattr(w, 'base_damage', 0) < 2
                        for w in (player.weapons or []) if w
                    )
                    if not has_unsharpened:
                        continue
                if op.startswith("蓄力"):
                    weapon_name = op[2:]
                    w_obj = next((w for w in (player.weapons or []) if w and w.name == weapon_name), None)
                    if w_obj and w_obj.is_charged:
                        continue
                    if w_obj and getattr(w_obj, '_hexagram_disabled', False):
                        continue
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
            if key == "report" and _auto_report_target(player, game_state) is None:
                continue
            mask[IDX_POLICE_BASE + offset] = True

    return mask


# ─────────────────────────────────────────────────────────────────────────────
#  choose 模式 mask 构建
# ─────────────────────────────────────────────────────────────────────────────

# 需要 RL 控制的战略性 choose situation 集合
STRATEGIC_SITUATIONS: set[str] = {
    # 基础博弈
    "petrified",                    # 石化解除 vs 保持
    "recruit_pick_1",               # 警察奖励选择（第1件）
    "recruit_pick_2",               # 警察奖励选择（第2件）
    "captain_election",             # 队长竞选投票
    # 天赋 T0 发动
    "talent_t0",                    # 是否发动天赋
    # T1 一刀缭断
    "oneslash_pick_target",         # 选目标
    # T4 六爻（矩阵博弈）
    "hexagram_my_choice",           # 发动者出拳
    "hexagram_opp_choice",          # 对手出拳
    "hexagram_pick_target",         # 选目标
    # T7 死者苏生
    "resurrection_pick_target",     # 选挂载目标
    # G2 全息影像
    "hologram_target",              # 选拉入目标
    # G3 神话之外
    "mythland_pick_target",         # 选拉入目标（猜拳是随机的，不在此列）
    # G4 愿负世
    "savior_activate",              # 涟漪强化后主动发动
    # G5 涟漪
    "ripple_activation_choice",     # 选锚定 vs 献诗
    "ripple_anchor_type",           # 锚定事件类型
    "ripple_anchor_target",         # 锚定目标
    "ripple_poem_target",           # 献诗目标
    # G6 要有笑声
    "cutaway_borrow_target",        # 借用行动目标
    # G7 星野
    "hoshino_form_choice",          # 形态选择
    "hoshino_self_doubt_choice",    # 自我怀疑接受/拒绝
    "hoshino_reorder_ammo",         # 排弹顺序（后续批次可能改为启发式）
}


def _build_choose_mask(
    mask: np.ndarray,
    player,
    game_state,
    situation: str,
    options: List[str],
) -> np.ndarray:
    """
    构建 choose 模式下的 action mask。

    choose 模式下，只有 [108-123] 范围的索引可能为 True。
    具体哪些索引可用取决于 situation 类型：

    - talent_t0 + 需要选目标的天赋：启用 108-112（对手槽）或 113（自身）
    - talent_t0 + 二选一（发动/不发动）：启用 114-115（choose 选项 0-1）
    - 通用 choose（石化/警察奖励/六爻出拳等）：启用 114 ~ 114+len(options)-1
    """

    # ══════════════════════════════════════════════════════════════
    #  talent_t0：是否发动天赋（二选一）
    # ══════════════════════════════════════════════════════════════
    if situation == "talent_t0":
        # 选项通常是 ["发动天赋", "不发动，正常行动"]
        # 映射到 choose 选项索引
        for i in range(min(len(options), 10)):
            mask[IDX_CHOOSE_BASE + i] = True
        return mask

    # ══════════════════════════════════════════════════════════════
    #  天赋目标选择：使用 talent_t0_target 索引 (108-112)
    # ══════════════════════════════════════════════════════════════
    _TARGET_SITUATIONS = {
        "oneslash_pick_target",
        "hexagram_pick_target",
        "resurrection_pick_target",
        "hologram_target",
        "mythland_pick_target",
        "ripple_anchor_target",
        "ripple_poem_target",
        "cutaway_borrow_target",
    }

    if situation in _TARGET_SITUATIONS:
        # 选项是玩家名字列表，需要映射到对手槽位
        opponents = get_opponent_slots(player, game_state)
        opp_name_to_slot = {}
        for slot, opp in enumerate(opponents):
            if opp is not None:
                opp_name_to_slot[opp.name] = slot

        has_any = False
        for opt in options:
            # 选项可能是玩家名字，也可能是 "自己" 之类
            slot = opp_name_to_slot.get(opt)
            if slot is not None:
                mask[IDX_TALENT_T0_TARGET_BASE + slot] = True
                has_any = True

        # 如果选项中包含自己（如死者苏生可以挂载给自己）
        player_name = getattr(player, 'name', '')
        if player_name in options or "自己" in options:
            mask[IDX_TALENT_T0_SELF] = True
            has_any = True

        # 兜底：如果没有匹配到任何槽位（选项格式不是玩家名），
        # 回退到通用 choose 索引
        if not has_any:
            for i in range(min(len(options), 10)):
                mask[IDX_CHOOSE_BASE + i] = True
        return mask

    # ══════════════════════════════════════════════════════════════
    #  自身目标天赋：使用 talent_t0_self 索引 (113)
    # ══════════════════════════════════════════════════════════════
    _SELF_SITUATIONS = {
        "savior_activate",
    }

    if situation in _SELF_SITUATIONS:
        # 通常是 ["发动", "不发动"] 之类的二选一
        # 用 choose 索引处理
        for i in range(min(len(options), 10)):
            mask[IDX_CHOOSE_BASE + i] = True
        return mask

    # ══════════════════════════════════════════════════════════════
    #  通用 choose：直接映射选项到 choose 索引 (114-123)
    # ══════════════════════════════════════════════════════════════
    #  适用于：petrified, recruit_pick, hexagram_my/opp_choice,
    #          captain_election, hoshino_form_choice, ripple_activation_choice,
    #          hoshino_self_doubt_choice, hoshino_reorder_ammo 等
    n_options = min(len(options), 10)
    for i in range(n_options):
        mask[IDX_CHOOSE_BASE + i] = True

    return mask