"""
rl/action_space.py
──────────────────
动作空间定义（天赋局，共 137 个 Discrete 动作）

索引布局：
  0        : forfeit
  1        : wake
  2 –  7   : move <地点>            (6 个地点)
  8 – 34   : interact <物品/服务>   (27 种)
  35 – 39  : lock  <对手槽 0-4>
  40 – 44  : find  <对手槽 0-4>
  45 – 94  : attack <对手槽 0-4> × <武器槽 0-9>  (5×10=50)
  95 – 107 : special <操作>         (13 种: 6基础 + 7 G7星野)
  108 – 114: 警察行动               (7 种)
  ── 以下为天赋扩展 ──
  115 – 119: talent_t0_activate <对手槽 0-4>  (5 个，选目标发动天赋)
  120      : talent_t0_self                   (1 个，对自己发动天赋)
  121 – 136: choose_option <0-15>              (16 个，用于 choose 同步)
"""

from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional
import numpy as np

if TYPE_CHECKING:
    from models.player import Player
    from engine.game_state import GameState

from engine.action_tables import (
    LOCATIONS, INTERACT_ITEMS, ITEM_LOCATIONS, WEAPONS, SPELL_PREREQUISITES,
    normalize_location as _normalize_location,
    player_owned_names as _player_owned_names,
)

# 枚举逻辑已统一到 engine/action_enumerator.py（单一信源）
# build_action_mask 委托其进行细粒度枚举，仅负责命令串 → 索引转换
from engine.action_enumerator import (
    _enumerate_move,
    _enumerate_interact,
    _enumerate_lock,
    _enumerate_find,
    _enumerate_attack,
    _get_opponents,
)

# ─────────────────────────────────────────────────────────────────────────────
#  动作空间大小
# ─────────────────────────────────────────────────────────────────────────────
ACTION_COUNT = 137   # 130 基础 + 7 G7 星野 special ops 扩展

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
IDX_SPECIAL_BASE = 95   # 95 – 107  (13 种: 6基础 + 7 G7星野)
IDX_POLICE_BASE  = 108  # 108 – 114

# ── 天赋扩展 ──
IDX_TALENT_T0_TARGET_BASE = 115  # 115 – 119
IDX_TALENT_T0_SELF        = 120
IDX_CHOOSE_BASE           = 121  # 121 – 136

# ─────────────────────────────────────────────────────────────────────────────
#  枚举列表
# ─────────────────────────────────────────────────────────────────────────────

# LOCATIONS, INTERACT_ITEMS, ITEM_LOCATIONS, WEAPONS, SPELL_PREREQUISITES
# 已迁至 engine/action_tables.py（单一数据源），此处通过 import 引用。



SPECIAL_OPS: List[str] = [
    # ── 基础 6 个 ──
    "磨刀",         # 95
    "吟唱魔法护盾", # 96
    "展开AT力场",   # 97
    "蓄力电磁步枪", # 98
    "蓄力高斯步枪", # 99
    "释放病毒",     # 100
    # ── G7 星野 7 个（来自 actions/special_op.py get_available_specials）──
    "Hoshino",      # 101  战术指令宏
    "取消盾牌",     # 102  取消架盾/持盾
    "修复",         # 103  修复铁之荷鲁斯
    "肾上腺素",     # 104  注射肾上腺素
    "更衣水着-shielder",   # 105  形态切换
    "更衣临战-Archer",     # 106  形态切换
    "更衣临战-shielder",   # 107  形态切换
]
assert len(SPECIAL_OPS) == 13

# 警察行动 CLI 关键字（report/designate 需要目标，在 idx_to_command 中自动填充）
POLICE_CMDS: List[str] = [
    "report",       # 108 — 需要目标，自动选择
    "assemble",     # 109
    "track",        # 110
    "recruit",      # 111
    "election",     # 112
    "designate",    # 113 — 需要目标，自动选择
    "study",        # 114
]
assert len(POLICE_CMDS) == 7

# special op → 触发所需的武器/物品名（None 表示由游戏引擎/talent 动态验证）
SPECIAL_REQUIRES: dict[str, Optional[str]] = {
    # ── 基础 ──
    "磨刀":         "磨刀石",
    "吟唱魔法护盾": "魔法护盾",
    "展开AT力场":   "AT力场",
    "蓄力电磁步枪": "电磁步枪",
    "蓄力高斯步枪": "高斯步枪",
    "释放病毒":     None,
    # ── G7 星野 — 全部由 talent 动态判定，无需静态物品检查 ──
    "Hoshino":              None,
    "取消盾牌":             None,
    "修复":                 None,
    "肾上腺素":             None,
    "更衣水着-shielder":    None,
    "更衣临战-Archer":      None,
    "更衣临战-shielder":    None,
}

# SPELL_PREREQUISITES 已迁至 engine/action_tables.py，此处通过 import 引用。


# ─────────────────────────────────────────────────────────────────────────────
#  choose 同步：战略性 situation 列表
#  这些 situation 的 choose() 调用会交给 RL 决策，而非启发式
# ─────────────────────────────────────────────────────────────────────────────
STRATEGIC_CHOOSE_SITUATIONS: set[str] = {
    # ── 预局 ──
    "talent_pick",              # 天赋选择（RL 自主选天赋）
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
    "g2_emotion_choice",        # G2：情绪选择
    "g2_sing_song",             # G2：选曲目
    "g2_sing_rhythm",           # G2：选节奏
    "g2_sing_target",           # G2：选听者
    "g2_melody_target",         # G2：旋律选目标
    "g2_melody_propagate",      # G2：旋律传播目标
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


# _normalize_location — 已通过 from engine.action_tables import normalize_location as _normalize_location 引用


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


# ─────────────────────────────────────────────────────────────────────────────
#  命令串 → 掩码索引 转换辅助函数
#  委托 engine/action_enumerator.py 的枚举结果，将合法命令字符串映射回
#  RL 动作空间的固定索引。
# ─────────────────────────────────────────────────────────────────────────────

def _cmd_to_move_idx(cmd: str) -> int:
    """ "move home" → IDX_MOVE_BASE + 0 """
    loc = cmd.split(" ", 1)[1]
    return IDX_MOVE_BASE + LOCATIONS.index(loc)


def _cmd_to_interact_idx(cmd: str) -> int:
    """ "interact 小刀" → IDX_INTERACT_BASE + 1 """
    item = cmd.split(" ", 1)[1]
    return IDX_INTERACT_BASE + INTERACT_ITEMS.index(item)


def _find_opponent_slot_by_name(
    target_name: str,
    opponent_slot_by_name: dict[str, int],
) -> int | None:
    """根据目标玩家名查找其在对手槽中的索引（0-4），找不到返回 None。"""
    return opponent_slot_by_name.get(target_name)


def _cmd_to_lock_idx(cmd: str, opponent_slot_by_name: dict[str, int]) -> int:
    """ "lock 张三" → IDX_LOCK_BASE + slot """
    target_name = cmd.split(" ", 1)[1]
    slot = _find_opponent_slot_by_name(target_name, opponent_slot_by_name)
    if slot is None:
        return -1  # 不应发生，调用方保证 cmd 合法
    return IDX_LOCK_BASE + slot


def _cmd_to_find_idx(cmd: str, opponent_slot_by_name: dict[str, int]) -> int:
    """ "find 张三" → IDX_FIND_BASE + slot """
    target_name = cmd.split(" ", 1)[1]
    slot = _find_opponent_slot_by_name(target_name, opponent_slot_by_name)
    if slot is None:
        return -1
    return IDX_FIND_BASE + slot


def _cmd_to_attack_idx(cmd: str, opponent_slot_by_name: dict[str, int]) -> int:
    """ "attack 张三 小刀" → IDX_ATTACK_BASE + slot*10 + weapon_slot """
    parts = cmd.split(" ", 2)
    target_name = parts[1]
    weapon_name = parts[2]
    slot = _find_opponent_slot_by_name(target_name, opponent_slot_by_name)
    if slot is None:
        return -1
    wi = WEAPONS.index(weapon_name)
    return IDX_ATTACK_BASE + slot * 10 + wi


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


# _player_owned_names — 已通过 from engine.action_tables import player_owned_names as _player_owned_names 引用



# ─────────────────────────────────────────────────────────────────────────────
#  核心 API
# ─────────────────────────────────────────────────────────────────────────────

def idx_to_command(idx: int, player, game_state) -> str:
    """
    将动作索引翻译为 CLI 命令字符串。
    调用方应保证 idx 在 action mask 允许范围内；
    若因竞态导致目标失效，则安全回退为 "forfeit"。

    注意：天赋 T0 索引 (108-113) 和 choose 索引 (114-129) 不会经过此函数——
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

    if IDX_CHOOSE_BASE <= idx < IDX_CHOOSE_BASE + 16:
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

    索引范围：
      108-112 (IDX_TALENT_T0_TARGET_BASE + slot) → 对手槽位目标选择
      113     (IDX_TALENT_T0_SELF)                → 自身目标选择
      114-129 (IDX_CHOOSE_BASE + i)               → 通用选项 options[i]

    返回
    ----
    options 中的一个字符串
    """
    if not options:
        return ""

    # ── 槽位目标选择 (108-113) ──
    # _build_choose_mask 对目标选择 situation 启用这些索引，
    # 将对手名字映射到槽位号。这里做反向翻译。
    if IDX_TALENT_T0_TARGET_BASE <= idx <= IDX_TALENT_T0_SELF:
        if idx == IDX_TALENT_T0_SELF:
            # 自身目标：匹配玩家名或"自己"
            player_name = getattr(player, 'name', '')
            for opt in options:
                if opt == player_name or "自己" in opt:
                    return opt
            return options[0]  # 安全回退

        # 对手槽位目标
        slot = idx - IDX_TALENT_T0_TARGET_BASE
        opponents = get_opponent_slots(player, game_state)
        if slot < len(opponents) and opponents[slot] is not None:
            target_name = opponents[slot].name
            # 在 options 中查找匹配的选项
            if target_name in options:
                return target_name
            for opt in options:
                if target_name in opt:
                    return opt
        # 槽位无效，安全回退
        return options[0]

    # ── 通用 choose 选项 (114-129) ──
    if IDX_CHOOSE_BASE <= idx < IDX_CHOOSE_BASE + 16:
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
    返回 130 维 bool 数组，True 表示该动作当前合法可选。

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
    - choose_mode=True  时：构建 choose 模式的 mask（索引 108-129）
    - 两种模式互斥，不会同时启用
    """
    mask = np.zeros(ACTION_COUNT, dtype=bool)

    # ══════════════════════════════════════════════════════════════════════════
    #  choose 模式：只启用 108-129 范围的索引
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

    # ── 星野架盾/持盾 防御性二次检查 ─────────────────────────────
    # action_turn._get_available_actions 已基于 shield_mode 过滤 names，
    # 这里再做一次防御性兜底（避免上游遗漏时 mask 透出非法行动给 RL 智能体）。
    # 见 README §12.2 / talents/g7/hoshino.py：
    #   - "架盾"：禁止 move 与 interact
    #   - "持盾"：禁止 interact（仍可 move）
    _hoshino_shield = getattr(getattr(player, "talent", None), "shield_mode", None)
    _hoshino_block_move = _hoshino_shield == "架盾"
    _hoshino_block_interact = _hoshino_shield in ("架盾", "持盾")

    # ── move ─────────────────────────────────────────────────────
    # 委托 engine/action_enumerator._enumerate_move（单一信源）
    if "move" in available_set and not _hoshino_block_move:
        for cmd in _enumerate_move(player):
            mask[_cmd_to_move_idx(cmd)] = True

    # ── interact ─────────────────────────────────────────────────
    # 委托 engine/action_enumerator._enumerate_interact（单一信源）
    if "interact" in available_set and not _hoshino_block_interact:
        for cmd in _enumerate_interact(player, game_state):
            mask[_cmd_to_interact_idx(cmd)] = True

    # ── lock / find / attack ──────────────────────────────────────
    # 委托 engine/action_enumerator 对应枚举函数（单一信源）
    combat_opponents = []
    opponent_slot_by_name = {}
    if any(action in available_set for action in ("lock", "find", "attack")):
        combat_opponents = _get_opponents(player, game_state)
        opponent_slots = get_opponent_slots(player, game_state)
        opponent_slot_by_name = {
            p.name: i for i, p in enumerate(opponent_slots) if p is not None
        }

    if "lock" in available_set:
        for cmd in _enumerate_lock(player, combat_opponents,
                                    game_state.markers):
            idx = _cmd_to_lock_idx(cmd, opponent_slot_by_name)
            if idx >= 0:
                mask[idx] = True

    if "find" in available_set:
        for cmd in _enumerate_find(player, combat_opponents,
                                    game_state.markers):
            idx = _cmd_to_find_idx(cmd, opponent_slot_by_name)
            if idx >= 0:
                mask[idx] = True

    if "attack" in available_set:
        for cmd in _enumerate_attack(player, combat_opponents,
                                      game_state.markers):
            idx = _cmd_to_attack_idx(cmd, opponent_slot_by_name)
            if idx >= 0:
                mask[idx] = True

    # ── special ───────────────────────────────────────────────────
    if "special" in available_set:
        owned = _player_owned_names(player)
        learned = getattr(player, 'learned_spells', set())
        talent = getattr(player, 'talent', None)
        for si, op in enumerate(SPECIAL_OPS):
            req = SPECIAL_REQUIRES[op]
            if req is None:
                # ── 无静态物品需求的 special ops：按 op 逐一判定 ──
                if op == "释放病毒":
                    if player.location == "医院" and not game_state.virus.is_active:
                        mask[IDX_SPECIAL_BASE + si] = True
                elif op == "Hoshino":
                    if (talent and getattr(talent, 'tactical_unlocked', False)
                            and not getattr(talent, 'is_terror', False)
                            and (getattr(talent, 'iron_horus_hp', 0) > 0
                                 or getattr(talent, 'eye_of_horus', None))):
                        mask[IDX_SPECIAL_BASE + si] = True
                elif op == "取消盾牌":
                    if (talent and hasattr(talent, 'shield_mode')
                            and getattr(talent, 'shield_mode', None) in ("架盾", "持盾")):
                        mask[IDX_SPECIAL_BASE + si] = True
                elif op == "修复":
                    if (talent and getattr(talent, 'fusion_shield_done', False)
                            and getattr(talent, 'iron_horus_hp', 0)
                            < getattr(talent, 'iron_horus_max_hp', 2)):
                        mask[IDX_SPECIAL_BASE + si] = True
                elif op == "肾上腺素":
                    if (talent and not getattr(talent, 'adrenaline_used', True)
                            and "肾上腺素" in getattr(talent, 'medicines', [])):
                        mask[IDX_SPECIAL_BASE + si] = True
                elif op.startswith("更衣"):
                    if talent and hasattr(talent, 'form'):
                        target_form = op[2:]  # "水着-shielder" / "临战-Archer" / "临战-shielder"
                        valid_forms = {"水着-shielder", "临战-Archer", "临战-shielder"}
                        if (target_form in valid_forms
                                and getattr(talent, 'form', None) != target_form
                                and player.location == f"home_{player.player_id}"):
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
        # 存活对手标记（供 report/designate 的目标有效性判断）
        _opponents = get_opponent_slots(player, game_state)
        _alive_flags = [(p is not None and p.is_alive()) for p in _opponents]

        police_available_map = {
            "report":      (0, "report"      in available_set),
            "assemble":    (1, "assemble"    in available_set),
            "track_guide": (2, "track_guide" in available_set),
            "recruit":     (3, "recruit"     in available_set),
            "election":    (4, "election"    in available_set),
            "designate":   (5, "designate"   in available_set),
            "study":       (6, "study"       in available_set),
        }
        has_alive_target = any(_alive_flags)
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
    # 预局
    "talent_pick",                  # 天赋选择（RL 自主选天赋）
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
    # G2 ish-bosheth
    "g2_emotion_choice",            # 情绪选择
    "g2_sing_song",                 # 选曲目
    "g2_sing_rhythm",               # 选节奏
    "g2_sing_target",               # 选听者
    "g2_melody_target",             # 旋律选目标
    "g2_melody_propagate",          # 旋律传播目标
    # G3 神话之外
    "mythland_pick_target",         # 选拉入目标（猜拳是随机的，不在此列）
    # G4 愿负世
    "savior_activate",              # 涟漪强化后主动发动
    # G5 涟漪
    "ripple_choose_method",         # 选锚定 vs 献诗
    "ripple_anchor_type",           # 锚定事件类型
    "ripple_anchor_kill_target",    # 击杀目标
    "ripple_anchor_armor_target",   # 护甲目标
    "ripple_anchor_armor_pick",     # 护甲层选择
    "ripple_anchor_acquire_item",   # 获取物品
    "ripple_anchor_arrive_loc",     # 到达地点
    "ripple_poem_target",           # 献诗目标
    "ripple_anchor_fail",           # 锚定失败后选择回溯/留下
    "ripple_destiny_damage",        # 爱与记忆之诗伤害选择
    "ripple_hexagram_free_choice",  # 六爻献诗自由选择
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

    choose 模式下，只有 [108-129] 范围的索引可能为 True。
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
        for i in range(min(len(options), 16)):
            mask[IDX_CHOOSE_BASE + i] = True
        return mask

    # ══════════════════════════════════════════════════════════════
    #  天赋目标选择：使用 talent_t0_target 索引 (108-112)
    # ══════════════════════════════════════════════════════════════
    _TARGET_SITUATIONS = {
        "oneslash_pick_target",
        "hexagram_pick_target",
        "resurrection_pick_target",
        "mythland_pick_target",
        "ripple_anchor_kill_target",
        "ripple_anchor_armor_target",
        "ripple_poem_target",
        "cutaway_borrow_target",
        # G2 ish-bosheth target situations
        "g2_sing_target",
        "g2_melody_target",
        "g2_melody_propagate",
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
            for i in range(min(len(options), 16)):
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
        for i in range(min(len(options), 16)):
            mask[IDX_CHOOSE_BASE + i] = True
        return mask

    # ══════════════════════════════════════════════════════════════
    #  通用 choose：直接映射选项到 choose 索引 (114-129)
    # ══════════════════════════════════════════════════════════════
    #  适用于：petrified, recruit_pick, hexagram_my/opp_choice,
    #          captain_election, hoshino_form_choice, ripple_activation_choice,
    #          hoshino_self_doubt_choice, hoshino_reorder_ammo 等
    n_options = min(len(options), 16)
    for i in range(n_options):
        mask[IDX_CHOOSE_BASE + i] = True

    return mask