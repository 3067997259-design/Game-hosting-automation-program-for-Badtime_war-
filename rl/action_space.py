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
    _enumerate_designate,
    _enumerate_report,
    _get_opponents,
)

# ─────────────────────────────────────────────────────────────────────────────
#  动作空间大小
# ─────────────────────────────────────────────────────────────────────────────
ACTION_COUNT = 137   # legacy/v2exp：130 基础 + 7 G7 星野 special ops 扩展（保持既有 checkpoint 兼容）
ACTION_COUNT_M9 = 154  # m9-rfc 核心：137 + 8 M9 字面 special + 4 模板族 special + 5 shoot 槽
M9_MAX_PLAYERS = 6

# M9 执行侧动态菜单相对 legacy 27 槽新增的全局稳定语义。禁止按“当前位置菜单
# 第几个”复用槽位：那会让同一 action id 在不同地点表示不同命令，策略无法形成
# 稳定 Q/advantage 语义。只允许在尾部追加；已有顺序属于 checkpoint schema。
M9_INTERACT_ITEMS_EXTRA: tuple[str, ...] = (
    # 商店
    "修理陶瓷护甲", "箭矢补给", "力量模块",
    # 魔法所
    "火矢模块", "魔导模块",
    # 医院
    "EPO", "海豚巧克力", "肾上腺素", "无限模块", "治疗",
    # 军事基地
    "破片手雷", "震撼弹", "闪光弹", "烟雾弹", "燃烧瓶",
    "穿甲模块", "冲击模块", "钩索",
    # 警察局（interact 执行路径与独立警察动词并存，均需可表达）
    "举报", "集结", "加入警察", "竞选队长", "追踪指引", "研究性学习", "唤醒警察",
)
assert len(M9_INTERACT_ITEMS_EXTRA) == 25

# 154–159：固定 6 人 home move 槽；160–184：M9 动态交互语义槽。
IDX_INTERACT_M9_BASE = ACTION_COUNT_M9 + M9_MAX_PLAYERS
IDX_POLICE_TARGET_M9_BASE = IDX_INTERACT_M9_BASE + len(M9_INTERACT_ITEMS_EXTRA)

# 参数化动作按“动作族 × 相对目标槽”展开，禁止代码代替策略挑首个/最高分目标。
M9_TARGET_SLOTS = M9_MAX_PLAYERS - 1
M9_POLICE_TARGET_VERBS: tuple[str, ...] = ("report", "designate")
IDX_SPECIAL_TARGET_M9_BASE = (
    IDX_POLICE_TARGET_M9_BASE + len(M9_POLICE_TARGET_VERBS) * M9_TARGET_SLOTS
)
M9_PLAYER_SPECIAL_FAMILIES: tuple[str, ...] = ("热线举报", "交易")
IDX_UNINSTALL_M9_BASE = (
    IDX_SPECIAL_TARGET_M9_BASE
    + len(M9_PLAYER_SPECIAL_FAMILIES) * M9_TARGET_SLOTS
)
M9_BOW_MODULE_NAMES: tuple[str, ...] = ("力量", "火矢", "魔导", "穿甲", "冲击", "无限")
IDX_POLICE_COMMAND_M9_BASE = IDX_UNINSTALL_M9_BASE + len(M9_BOW_MODULE_NAMES)
M9_POLICE_UNIT_IDS: tuple[str, ...] = ("unit1", "unit2", "unit3")
M9_POLICE_UNIT_COMMANDS: tuple[str, ...] = ("移动", "攻击")
ACTION_COUNT_M9_FIXED = (
    IDX_POLICE_COMMAND_M9_BASE
    + len(M9_POLICE_UNIT_IDS) * len(M9_POLICE_UNIT_COMMANDS)
)

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

# ── M9 专属 special（137 – 148；仅 m9-rfc 启用）──
IDX_SPECIAL_M9_BASE = IDX_CHOOSE_BASE + 16   # 137
assert IDX_SPECIAL_M9_BASE == 137

# ── M9 追加：shoot 槽（149 – 153，5 个对手槽）──
# 弓的专属跨地点动词。弓是 WEAPONS 第 11 件，5×10 attack 网格装不下
#（slot*10+10 会混叠到下一槽的拳击/越界进 special 段——曾实锤指令映射污染），
# 故弓不进 attack 网格（_cmd_to_attack_idx 过滤），射击走本专属段。
IDX_SHOOT_M9_BASE = 149
assert IDX_SHOOT_M9_BASE + 5 == ACTION_COUNT_M9

# ── M9 追加：他人住宅 move 槽（追入 home_pX 的可达性）──
# 动作区末尾按 player_order 每名玩家追加一槽（1v1→2、1v5→6…）。
# 引擎 _enumerate_move 允许 "move home_pX"，legacy 固定 6 格装不下 →
# 蹲家对手（turtle/archer 风筝 home）RL 永远到不了（曾实锤 5%）；此处补上。
#
# 为什么是 n_players 而不是 n_players-1：槽位下标必须只由 player_order 位次决定，
# 才能让"同一索引 = 同一目的地"在所有行动者视角下一致（RL 学到的语义才稳定）。
# 行动者自己那一槽恒不点亮（引擎不枚举自家 home_pX，裸 "move home" 走 IDX_MOVE_BASE），
# 与 attack 段自身槽同构。曾按 n_players-1 定长 → 末位玩家 home 索引越界 IndexError。
IDX_MOVE_HOME_M9_BASE = ACTION_COUNT_M9   # 154

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

# ── M9 动态模板族（引擎运行时拼接目标名：热线举报<名>/指挥<unit>移动|攻击/交易<名>/拆卸<模块>）
# 每族一个 RL 槽位；目标由 idx_to_command 按引擎 get_available_specials 首项自动填充
#（与 legacy report/designate 自动目标同模式），使 RL 对每个特殊操作都"可见且可发"。
SPECIAL_OPS_M9_TEMPLATE: tuple[str, ...] = ("热线举报", "指挥", "交易", "拆卸")

# ── M9 专属特殊操作（引擎 actions/special_op._append_m9_specials 的字面名；仅 m9-rfc 启用；
#    模板族见 SPECIAL_OPS_M9_TEMPLATE；目标经 choose/自动填充，故意不展平到槽位）──
SPECIAL_OPS_M9: List[str] = [
    "破界",           # 137  M9 G3 破界根行动
    "武器破界",       # 138  M9 G3 武器破界
    "竞选队长",       # 139  M9 警察队长候选
    "PP重掷先攻",     # 140  M9 PP 生前消耗
    "PP加伤",         # 141
    "PP偷看先攻",     # 142  M9 PP（需 2）
    "PP抵消犯罪",     # 143
    "卸甲免费find",   # 144  M9 G1 卸甲免费 find（不占槽，玩家主动）
]
assert len(SPECIAL_OPS_M9) == 8
# special 段（字面 8 + 模板族 4）恰好填满 [IDX_SPECIAL_M9_BASE, IDX_SHOOT_M9_BASE)
assert ACTION_COUNT + len(SPECIAL_OPS_M9) + len(SPECIAL_OPS_M9_TEMPLATE) == IDX_SHOOT_M9_BASE


def rl_action_count(game_state=None, n_players: Optional[int] = None) -> int:
    """当前 profile 的 RL 动作空间规模：
    m9-rfc → 固定 ACTION_COUNT_M9_FIXED（为最多 6 名玩家预留 home move 槽，
    实际不存在的玩家槽由 mask 保持 False）；
    legacy/v2exp → 137。必须与 mask 构建/idx_to_command 同源，避免尺寸错位。
    """
    if game_state is not None and hasattr(game_state, "m9_enabled"):
        if game_state.m9_enabled:
            actual = (n_players if n_players is not None
                      else len(getattr(game_state, "player_order", []) or []))
            if actual > M9_MAX_PLAYERS:
                raise ValueError(
                    f"M9 RL 最多支持 {M9_MAX_PLAYERS} 人，实际收到 {actual} 人"
                )
            return ACTION_COUNT_M9_FIXED
        return ACTION_COUNT
    try:
        from engine.experiments import is_enabled
        if is_enabled("m9_rfc"):
            if n_players is not None and n_players > M9_MAX_PLAYERS:
                raise ValueError(
                    f"M9 RL 最多支持 {M9_MAX_PLAYERS} 人，实际收到 {n_players} 人"
                )
            return ACTION_COUNT_M9_FIXED
    except Exception:
        pass
    return ACTION_COUNT

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
#  RL 观测/动作的武器窗口（冻结，不随共享表漂移）
# ─────────────────────────────────────────────────────────────────────────────
# 『弓』是 V2.0-exp（M4 弓模块）才加入 engine/action_tables.WEAPONS 的第 11 件；
# 而 RL 的 OBS_DIM=539 / ACTION_COUNT=137 与 5×10 攻击槽（45–94）是冻结布局，
# 直接扩容会让后续所有观测块错位、并作废既有 checkpoint。
# 因此 RL 一律使用下面这 10 件窗口（恰为共享表前 10 项，顺序即历史顺序）：
#   - legacy：弓本不存在，10 件即完整武器集，行为与历史完全一致；
#   - v2exp/m9：对手 BasicAI 仍可使用弓，但 RL 观测不含弓的 one-hot、
#               RL 动作空间不含弓攻击（编码/解码时被过滤，见 _cmd_to_attack_idx）。
RL_WEAPON_NAMES: tuple[str, ...] = (
    "拳击", "小刀", "警棍", "魔法弹幕", "远程魔法弹幕",
    "地震", "地动山摇", "电磁步枪", "高斯步枪", "导弹",
)
assert len(RL_WEAPON_NAMES) == 10
assert list(RL_WEAPON_NAMES) == list(WEAPONS)[:10], (
    "engine/action_tables.WEAPONS 前 10 项与 RL 冻结武器窗口不一致——"
    "共享表被重排/扩容，需先对齐 RL 窗口语义"
)

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
    "shock_target_select",      # 震荡目标选择
    "talent_t0_retry",          # T0 重试
    # ── M9 往世层投注 ──
    "m9_betting_action",        # 押注/追加/转仓/不交易
    "m9_betting_target",        # 新押注或追加的生者目标
    "m9_betting_amount",        # 本次投入 PP
    "m9_betting_transfer_out",  # 转出已有仓位
    "m9_betting_transfer_in",   # 转入生者目标
    # ── 天赋 T0 ──
    "talent_t0",                # 是否发动天赋（特殊处理：可用 T0_TARGET/SELF 索引）
    # ── 天赋子决策 ──
    "oneslash_pick_target",     # T1：一刀缭断选目标
    "oneslash_pick_weapon",     # T1：择武器
    "oneslash_chase_target",    # T1：追猎目标
    "t1_performance_mode",      # T1：即演/普通 模式选择（M9）
    "hexagram_pick_target",     # T4：六爻选目标
    "hexagram_pick_opponent",   # T4：择对手
    "hexagram_my_choice",       # T4：六爻发动者出拳
    "hexagram_opp_choice",      # T4：六爻对手出拳
    "hexagram_thunder_target",  # T4：雷霆目标
    "hexagram_steal_target",    # T4：窃取目标
    "hexagram_steal_pick",      # T4：窃取选项
    "hexagram_disarm_target",   # T4：缴械目标
    "resurrection_pick_target", # T7：死者苏生选目标
    "t2_core_target",           # T2：核心目标（M9）
    "t2_earthfire_hunt",        # T2：地火追猎（M9）
    "t2_performance_mode",      # T2：即演/普通 模式（M9）
    "t2_pick_weapon",           # T2：择武器（M9）
    "t3_stars_bounce_target",   # T3：天星弹射目标（M9）
    "g2_emotion_choice",        # G2：情绪选择
    "g2_sing_song",             # G2：选曲目
    "g2_sing_rhythm",           # G2：选节奏
    "g2_sing_target",           # G2：选听者
    "g2_melody_target",         # G2：旋律选目标
    "g2_melody_propagate",      # G2：旋律传播目标
    "mythland_pick_target",     # G3：神话之外选拉入目标
    "savior_activate",          # G4：愿负世主动发动（涟漪强化后）
    "g4_human_performance_mode",# G4：即演/普通 模式（M9）
    "g4_strike_pick_weapon",    # G4：打击择武器（M9）
    "g4_strike_pick_target",    # G4：打击目标（M9）
    "ripple_anchor_type",       # G5：锚定事件类型
    "ripple_anchor_target",     # G5：锚定目标
    "ripple_poem_target",       # G5：献诗目标
    "cutaway_borrow_target",    # G6：插入式笑话借用目标
    "hoshino_form_choice",      # G7：形态选择
    "hoshino_self_doubt_choice", # G7：自我怀疑接受/拒绝
    "m9_g3",                    # G3：结界内通用选单（M9）
}

# 纯随机 / 无博弈空间的 situation → 保持启发式
HEURISTIC_CHOOSE_SITUATIONS: set[str] = {
    "mythland_rps",             # 结界猜拳：纯随机
}

# 引擎中存在、但 RL 不参与（legacy 舞台/卡片/交易等；启发式/默认路径处理）的 choose situation。
# 防漂移：新增引擎 situation 若既不在 STRATEGIC/HEURISTIC 也不在本登记表 → 测试红。
KNOWN_CHOOSE_UNMAPPED: set[str] = {
    # legacy/v2exp 舞台与 ish-bosheth 老系统（M9 不涉及）
    "g2_voice_choice", "g2_melody_seat", "g2_melody_seat2",
    "g2_pickup_floor", "g2_discard", "g2_play_card", "g2_trade_init",
    "g2_trade_partner", "g2_trade_my_card", "g2_trade_their_card",
    "g2_trade_accept", "g2_photo_invite", "pick_location", "pick_item", "embrace",
    # legacy 卡片系统（g2_card_*）
    "g2_card_blank_stub", "g2_card_boo", "g2_card_bouquet",
    "g2_card_exchange_give", "g2_card_exchange_target", "g2_card_front_row",
    "g2_card_glow_stick_duet", "g2_card_mediation_acc", "g2_card_mediation_str",
    "g2_card_program_tidy", "g2_card_program_tidy_discard", "g2_card_program_tidy_pick",
    "g2_card_reflect_board", "g2_card_spotlight_photo", "g2_card_support_cheer",
}


# _normalize_location — 已通过 from engine.action_tables import normalize_location as _normalize_location 引用


# ─────────────────────────────────────────────────────────────────────────────
#  辅助函数
# ─────────────────────────────────────────────────────────────────────────────

def get_opponent_slots(player, game_state) -> List:
    """
    返回最多 5 个对手 Player 对象（排除自身）。

    m9-rfc 按稳定 player_id 排序，使观测 slot 与目标动作头不随每局随机行动
    顺序漂移；legacy/v2exp 保留历史 ``player_order`` 顺序，避免旧 checkpoint
    语义改变。死亡玩家保留原槽位，不把后续对手压缩进旧槽。
    不足 5 个时用 None 填充，保证返回列表长度始终为 5。
    """
    slots: List = []
    player_ids = list(game_state.player_order)
    from engine.m9.gate import m9_enabled as _m9_slots
    if _m9_slots(game_state):
        player_ids.sort(key=str)
    for pid in player_ids:
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

def _rl_move_destinations(game_state=None) -> list:
    """RL 移动目的地表（profile 感知）：
    legacy/v2exp → 6 个规范地点（历史兼容，checkpoint 索引不变）；
    m9-rfc       → 规范地点 + 各玩家住宅 `home_{pid}`（引擎 _enumerate_move 允许
                   追入他人住宅；RL 固定表此前漏列 → 追击命令被丢弃，蹲家对手
                   永远不可达 —— 曾实锤 vs turtle/archer 胜率 5%/5%）。
    顺序稳定：规范地点在前；M9 的 home 变体按 player_id 排序。
    若继续使用每局随机的 player_order，同一 move action id 会在
    ``home_ai_0`` / ``home_ai_1`` 之间漂移，与已稳定的对手目标槽产生
    相同的跨 episode 语义污染。legacy/v2exp 不走此扩展段。
    """
    from engine.m9.gate import m9_enabled
    base = list(LOCATIONS)
    if m9_enabled(game_state) and game_state is not None:
        player_ids = sorted(
            getattr(game_state, "player_order", []) or [], key=str,
        )
        for pid in player_ids:
            home_loc = f"home_{pid}"
            if home_loc not in base:
                base.append(home_loc)
    return base


def _cmd_to_move_idx(cmd: str, game_state=None) -> int:
    """ "move home" → IDX_MOVE_BASE + 0；"move home_p2"（M9 追入他人住宅）→
    IDX_MOVE_HOME_M9_BASE + (destination 序号 - 6)。

    位置不在 RL 移动目的地表内时返回 -1（防御共享表后续扩容）。
    """
    loc = cmd.split(" ", 1)[1]
    dests = _rl_move_destinations(game_state)
    try:
        i = dests.index(loc)
    except ValueError:
        return -1
    if i < len(LOCATIONS):
        return IDX_MOVE_BASE + i
    return IDX_MOVE_HOME_M9_BASE + (i - len(LOCATIONS))


def _cmd_to_interact_idx(cmd: str) -> int:
    """ "interact 小刀" → IDX_INTERACT_BASE + 1

    legacy 27 项保持历史索引；M9 新项目使用 160 起的全局稳定追加槽。
    真正未知的未来项目返回 -1，由覆盖门禁强制开发者显式分配语义。
    """
    item = cmd.split(" ", 1)[1]
    try:
        return IDX_INTERACT_BASE + INTERACT_ITEMS.index(item)
    except ValueError:
        try:
            return IDX_INTERACT_M9_BASE + M9_INTERACT_ITEMS_EXTRA.index(item)
        except ValueError:
            return -1


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


def _rl_weapon_names(game_state=None) -> tuple:
    """RL 攻击武器表（profile 感知）：
    legacy/v2exp → 冻结 10 窗口 RL_WEAPON_NAMES（历史兼容，checkpoint 索引不变）；
    m9-rfc       → 引擎全武器表（含『弓』等 M9 武器；冻结窗口会把弓锁死，
                   造成 RL 远程攻击恒非法——曾实锤 attack 掩码合法率 0%）。
    """
    from engine.m9.gate import m9_enabled
    if m9_enabled(game_state):
        return tuple(WEAPONS)
    return RL_WEAPON_NAMES


def _cmd_to_attack_idx(cmd: str, opponent_slot_by_name: dict[str, int],
                       game_state=None) -> int:
    """ "attack 张三 小刀" → IDX_ATTACK_BASE + slot*10 + weapon_slot

    武器名不在 RL 武器表（legacy 冻结窗口 / m9 全表）时返回 -1 ——
    编码层即过滤，RL 动作空间不为其分配槽位。
    """
    parts = cmd.split(" ", 2)
    target_name = parts[1]
    weapon_name = parts[2]
    slot = _find_opponent_slot_by_name(target_name, opponent_slot_by_name)
    if slot is None:
        return -1
    try:
        wi = _rl_weapon_names(game_state).index(weapon_name)
    except ValueError:
        return -1  # 非 RL 武器表（连带防御未来新增武器）
    if wi >= 10:
        # 5×10 网格硬上限：第 11 件起（弓等）编码会混叠到下一槽/越界进 special 段
        # （曾实锤 mask 点亮错误索引 → 解码成攻击错误目标的拳击）。弓走 shoot 专属段。
        return -1
    return IDX_ATTACK_BASE + slot * 10 + wi


def _cmd_to_shoot_idx(cmd: str, opponent_slot_by_name: dict[str, int]) -> int:
    """ "shoot 张三" → IDX_SHOOT_M9_BASE + slot（仅 m9-rfc mask 会启用）"""
    target_name = cmd.split(" ", 1)[1]
    slot = _find_opponent_slot_by_name(target_name, opponent_slot_by_name)
    if slot is None:
        return -1
    return IDX_SHOOT_M9_BASE + slot


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


def _target_slot_tokens(player, game_state) -> dict[str, int]:
    """显示名与 player_id 均映射到固定对手槽，供 ActionSpec/BC 共用。"""
    result: dict[str, int] = {}
    for slot, target in enumerate(get_opponent_slots(player, game_state)):
        if target is None:
            continue
        for token in (
            getattr(target, "name", ""), getattr(target, "player_id", ""),
        ):
            if token:
                result[str(token)] = slot
    return result


def command_to_action_idx(command: str, player, game_state) -> int:
    """具体引擎命令 → 当前 schema 动作 id；无法无歧义表达时返回 -1。"""
    from rl.action_spec import command_action_spec

    slots = _target_slot_tokens(player, game_state)
    spec = command_action_spec(command, target_slots=slots)
    verb = spec.verb
    idx = -1
    if verb == "forfeit" and not spec.argument:
        idx = IDX_FORFEIT
    elif verb == "wake" and not spec.argument:
        idx = IDX_WAKE
    elif verb == "move" and spec.argument:
        idx = _cmd_to_move_idx(spec.raw, game_state)
    elif verb == "interact" and spec.argument:
        idx = _cmd_to_interact_idx(spec.raw)
    elif verb == "lock" and spec.argument:
        idx = _cmd_to_lock_idx(spec.raw, slots)
    elif verb == "find" and spec.argument:
        idx = _cmd_to_find_idx(spec.raw, slots)
    elif verb == "attack" and spec.target_token and spec.weapon_name:
        idx = _cmd_to_attack_idx(spec.raw, slots, game_state)
    elif verb == "shoot" and spec.argument:
        idx = _cmd_to_shoot_idx(spec.raw, slots)
    elif verb == "special" and spec.argument:
        parameterized = _m9_parameterized_special_idx(
            spec.argument, player, game_state,
        )
        if parameterized >= 0:
            idx = parameterized
        elif spec.argument in SPECIAL_OPS:
            idx = IDX_SPECIAL_BASE + SPECIAL_OPS.index(spec.argument)
        elif spec.argument in SPECIAL_OPS_M9:
            idx = IDX_SPECIAL_M9_BASE + SPECIAL_OPS_M9.index(spec.argument)
        else:
            family = next(
                (fam for fam in SPECIAL_OPS_M9_TEMPLATE
                 if spec.argument.startswith(fam)),
                None,
            )
            if family is not None:
                idx = (
                    IDX_SPECIAL_M9_BASE + len(SPECIAL_OPS_M9)
                    + SPECIAL_OPS_M9_TEMPLATE.index(family)
                )
    elif verb in M9_POLICE_TARGET_VERBS and spec.target_slot is not None:
        idx = (
            IDX_POLICE_TARGET_M9_BASE
            + M9_POLICE_TARGET_VERBS.index(verb) * M9_TARGET_SLOTS
            + spec.target_slot
        )
    elif verb in POLICE_CMDS:
        # M9 的 report/designate 必须带显式目标；legacy 维持自动目标槽。
        from engine.m9.gate import m9_enabled
        if verb not in M9_POLICE_TARGET_VERBS or not m9_enabled(game_state):
            idx = IDX_POLICE_BASE + POLICE_CMDS.index(verb)

    return idx if 0 <= idx < rl_action_count(game_state) else -1


def _m9_parameterized_special_idx(op_name: str, player, game_state) -> int:
    """M9 参数化 special → 展平后的稳定语义槽。"""
    slots = _target_slot_tokens(player, game_state)
    for family in M9_PLAYER_SPECIAL_FAMILIES:
        if op_name.startswith(family):
            target = op_name[len(family):]
            slot = slots.get(target)
            if slot is None:
                return -1
            return (
                IDX_SPECIAL_TARGET_M9_BASE
                + M9_PLAYER_SPECIAL_FAMILIES.index(family) * M9_TARGET_SLOTS
                + slot
            )
    if op_name.startswith("拆卸"):
        module = op_name[len("拆卸"):]
        if module in M9_BOW_MODULE_NAMES:
            return IDX_UNINSTALL_M9_BASE + M9_BOW_MODULE_NAMES.index(module)
    if op_name.startswith("指挥"):
        body = op_name[len("指挥"):]
        for command in M9_POLICE_UNIT_COMMANDS:
            if not body.endswith(command):
                continue
            unit_id = body[:-len(command)]
            if unit_id in M9_POLICE_UNIT_IDS:
                return (
                    IDX_POLICE_COMMAND_M9_BASE
                    + M9_POLICE_UNIT_IDS.index(unit_id)
                    * len(M9_POLICE_UNIT_COMMANDS)
                    + M9_POLICE_UNIT_COMMANDS.index(command)
                )
    return -1


def action_spec_from_idx(
    idx: int,
    player,
    game_state,
    *,
    choose_mode: bool = False,
    choose_options: Optional[List[str]] = None,
    choose_situation: str = "",
):
    """动作 id → ``ActionSpec``，供 trace/诊断使用。"""
    from rl.action_spec import choose_action_spec, command_action_spec

    slots = _target_slot_tokens(player, game_state)
    if choose_mode:
        options = choose_options or []
        selected = idx_to_choose_option(
            idx, options, choose_situation, player, game_state,
        )
        return choose_action_spec(
            selected, options, choose_situation, target_slots=slots,
        )
    return command_action_spec(
        idx_to_command(idx, player, game_state), target_slots=slots,
    )


def action_family_from_idx(idx: int) -> str:
    """不依赖运行时目标的稳定动作族，用于评估分布与退化诊断。"""
    if idx == IDX_FORFEIT:
        return "forfeit"
    if idx == IDX_WAKE:
        return "wake"
    if IDX_MOVE_BASE <= idx < IDX_INTERACT_BASE:
        return "move"
    if IDX_INTERACT_BASE <= idx < IDX_LOCK_BASE:
        return "interact"
    if IDX_LOCK_BASE <= idx < IDX_FIND_BASE:
        return "lock"
    if IDX_FIND_BASE <= idx < IDX_ATTACK_BASE:
        return "find"
    if IDX_ATTACK_BASE <= idx < IDX_SPECIAL_BASE:
        return "attack"
    if IDX_SPECIAL_BASE <= idx < IDX_POLICE_BASE:
        return "special"
    if IDX_POLICE_BASE <= idx < IDX_TALENT_T0_TARGET_BASE:
        return POLICE_CMDS[idx - IDX_POLICE_BASE]
    if IDX_TALENT_T0_TARGET_BASE <= idx <= IDX_TALENT_T0_SELF:
        return "choose_target"
    if IDX_CHOOSE_BASE <= idx < IDX_CHOOSE_BASE + 16:
        return "choose"
    if IDX_SPECIAL_M9_BASE <= idx < IDX_SHOOT_M9_BASE:
        return "special"
    if IDX_SHOOT_M9_BASE <= idx < ACTION_COUNT_M9:
        return "shoot"
    if IDX_MOVE_HOME_M9_BASE <= idx < IDX_INTERACT_M9_BASE:
        return "move"
    if IDX_INTERACT_M9_BASE <= idx < IDX_POLICE_TARGET_M9_BASE:
        return "interact"
    if IDX_POLICE_TARGET_M9_BASE <= idx < IDX_SPECIAL_TARGET_M9_BASE:
        offset = idx - IDX_POLICE_TARGET_M9_BASE
        return M9_POLICE_TARGET_VERBS[offset // M9_TARGET_SLOTS]
    if IDX_SPECIAL_TARGET_M9_BASE <= idx < ACTION_COUNT_M9_FIXED:
        return "special"
    return "invalid"


ACTION_CONTRACT_FAMILIES: dict[str, frozenset[str]] = {
    # Rung 0：只学习备战/追击/接敌/伤害链；警察、交易、经济和天赋在后续 rung 解锁。
    "combat_core": frozenset({
        "forfeit", "wake", "move", "lock", "find", "attack", "shoot",
    }),
    # 警察课继承全部基础作战能力，只追加队长竞选与逐单位指挥槽。
    # PP、交易、天赋与旧警察动词仍隔离，避免再被无关侧系统劫持。
    "police_course": frozenset({
        "forfeit", "wake", "move", "lock", "find", "attack", "shoot",
    }),
    # 经济课的 none worker 仍回放 combat_core；专项 worker 只学习一条
    # 有用途闭环的“收入→消费→远程伤害”链，避免可累积打工劫持策略。
    "economy_course": frozenset({
        "forfeit", "wake", "move", "lock", "find", "attack", "shoot",
    }),
    # 年龄分层集成课：专项 worker 仍走窄资源追击合同；正常开局 worker
    # 使用 combat_economy，避免为了回放成熟第二目标而退回纯小刀动作域。
    "economy_integration_course": frozenset({
        "forfeit", "wake", "move", "lock", "find", "attack", "shoot",
    }),
    "economy_drill": frozenset({"forfeit", "wake", "lock", "shoot"}),
    # 经济应用课：第二目标会由完整 BasicAI 移动，必须保留追击/调查槽；
    # 额外经济动作仍只开放打工与箭矢补给，避免泛商店动作稀释闭环。
    "economy_pursuit_drill": frozenset({
        "forfeit", "wake", "move", "lock", "find", "shoot",
    }),
    # 真实首杀后的终结反事实：保留窄经济循环，但当完整 BasicAI 主动贴身时
    # 允许使用已经持有的近战武器，避免装甲残血目标只能补箭/射击。
    "economy_pursuit_finish": frozenset({
        "forfeit", "wake", "move", "lock", "find", "attack", "shoot",
    }),
    # 反陶瓷应用课：先在魔法所安装火矢，再沿用补箭/追射闭环。
    # 只暴露这一种新模块，避免把答案稀释成任意装备收集。
    "antiarmor_fire_drill": frozenset({
        "forfeit", "wake", "move", "lock", "find", "attack", "shoot",
    }),
    # 六种模块共享同一课程身份与动作头；场景状态负责表达需求，合同只保证
    # 所有模块都可被选择，避免训练成“课程 ID → 固定模块”的查表策略。
    "bow_loadout_drill": frozenset({
        "forfeit", "wake", "move", "lock", "find", "attack", "shoot",
    }),
    # PP 首课只教“消耗 1 PP 给下一次真实伤害 +1”。none worker 继续回放
    # combat_core；专项 worker 只开放这一项 self-use special 与基础攻击。
    # 无阵营 FFA 中的裸交易没有正收益目标，继续隔离到魂援/联盟课程。
    "pp_course": frozenset({
        "forfeit", "wake", "move", "lock", "find", "attack", "shoot",
    }),
    "pp_damage_drill": frozenset({"forfeit", "wake"}),
    # 生存课仍保留完整基础作战链，只额外开放医院治疗。专项局用真实
    # “治疗后承伤→反击→换目标”终局区分是否正确使用机制。
    "survival_course": frozenset({
        "forfeit", "wake", "move", "lock", "find", "attack", "shoot",
    }),
    "survival_heal_drill": frozenset({
        "forfeit", "wake", "move", "lock", "find", "attack", "shoot",
    }),
    "survival_armor_drill": frozenset({
        "forfeit", "wake", "move", "lock", "find", "attack", "shoot",
    }),
    "survival_repair_drill": frozenset({
        "forfeit", "wake", "move", "lock", "find", "attack", "shoot",
    }),
    "equipment_course": frozenset({
        "forfeit", "wake", "move", "lock", "find", "attack", "shoot",
    }),
    "sharpen_drill": frozenset({
        "forfeit", "wake", "move", "lock", "find", "attack", "shoot",
    }),
    "visibility_drill": frozenset({
        "forfeit", "wake", "move", "lock", "find", "attack", "shoot",
    }),
    # 属性护甲辨别课不教发育，只让策略在已经接敌、同时持有魔法/科技
    # 武器时依据目标外甲选择攻击动作。路由仅缩到“该目标的多种武器”，
    # 不替策略选择正确属性。
    "attribute_armor_drill": frozenset({"forfeit", "wake", "attack"}),
    # 基础经济集成：开放装备/补给交互，但继续隔离 PP、交易、警察与天赋 special。
    "combat_economy": frozenset({
        "forfeit", "wake", "move", "lock", "find", "attack", "shoot",
    }),
    # 指挥靶场不开放亲手攻击；否则已经学会基础课的策略会绕过警察机制，
    # 用个人小刀拿同一个终局奖励，专项梯度永远进不了新动作头。
    "police_drill": frozenset({"forfeit", "wake"}),
    # 天赋首课只考 T0 是否发动。天赋内部的目标/武器选择走 choose 模式，
    # 不受动作合同裁剪；根行动若开放普通攻击，会让旧策略绕过天赋拿终局奖励。
    "talent_drill": frozenset({"forfeit", "wake"}),
    # T6 热线是根行动 special（不读 SP、不走 T0 choose），talent_drill 会
    # 连热线槽一起屏蔽——T6 首课必须显式放行热线模板槽（P5 首批）。
    "hotline_drill": frozenset({"forfeit", "wake"}),
}

# 基础攻击必须允许取得一件通用近战武器，否则拳击无法在 25 轮内击穿 turtle
# 的防御路线。这里按稳定 action id 精确放行 home 的小刀，不开放打工、商店、
# 警察局交互等侧系统，避免重新出现“竞选队长劫持基础课”。
ACTION_CONTRACT_EXTRA_INDICES: dict[str, frozenset[int]] = {
    "combat_core": frozenset({
        IDX_INTERACT_BASE + INTERACT_ITEMS.index("小刀"),
    }),
    "police_course": frozenset({
        IDX_INTERACT_BASE + INTERACT_ITEMS.index("小刀"),
        IDX_SPECIAL_M9_BASE + SPECIAL_OPS_M9.index("竞选队长"),
        *range(IDX_POLICE_COMMAND_M9_BASE, ACTION_COUNT_M9_FIXED),
    }),
    # T6 首课：热线族第一个模板 family 占 IDX_SPECIAL_TARGET_M9_BASE 起的
    # M9_TARGET_SLOTS 个槽（M9_PLAYER_SPECIAL_FAMILIES = ("热线举报", "交易")）。
    "hotline_drill": frozenset(
        range(IDX_SPECIAL_TARGET_M9_BASE,
              IDX_SPECIAL_TARGET_M9_BASE + M9_TARGET_SLOTS),
    ),
    "police_drill": frozenset({
        IDX_SPECIAL_M9_BASE + SPECIAL_OPS_M9.index("竞选队长"),
        *range(IDX_POLICE_COMMAND_M9_BASE, ACTION_COUNT_M9_FIXED),
    }),
    "economy_drill": frozenset({
        IDX_INTERACT_BASE + INTERACT_ITEMS.index("打工"),
        IDX_INTERACT_M9_BASE + M9_INTERACT_ITEMS_EXTRA.index("箭矢补给"),
    }),
    "economy_pursuit_drill": frozenset({
        IDX_INTERACT_BASE + INTERACT_ITEMS.index("打工"),
        IDX_INTERACT_M9_BASE + M9_INTERACT_ITEMS_EXTRA.index("箭矢补给"),
    }),
    "economy_pursuit_finish": frozenset({
        IDX_INTERACT_BASE + INTERACT_ITEMS.index("打工"),
        IDX_INTERACT_M9_BASE + M9_INTERACT_ITEMS_EXTRA.index("箭矢补给"),
    }),
    "antiarmor_fire_drill": frozenset({
        IDX_INTERACT_BASE + INTERACT_ITEMS.index("打工"),
        IDX_INTERACT_M9_BASE + M9_INTERACT_ITEMS_EXTRA.index("箭矢补给"),
        IDX_INTERACT_M9_BASE + M9_INTERACT_ITEMS_EXTRA.index("火矢模块"),
    }),
    "bow_loadout_drill": frozenset({
        IDX_INTERACT_BASE + INTERACT_ITEMS.index("打工"),
        IDX_INTERACT_M9_BASE + M9_INTERACT_ITEMS_EXTRA.index("箭矢补给"),
        *(
            IDX_INTERACT_M9_BASE
            + M9_INTERACT_ITEMS_EXTRA.index(f"{module_name}模块")
            for module_name in M9_BOW_MODULE_NAMES
        ),
    }),
    "pp_damage_drill": frozenset({
        IDX_SPECIAL_M9_BASE + SPECIAL_OPS_M9.index("PP加伤"),
        # 单靶课程固定 ai_0=slot0；只保留已有小刀动作语义，不让拳击噪声
        # 稀释“+1 正好跨过 5HP 斩杀线”的因果关系。
        IDX_ATTACK_BASE + RL_WEAPON_NAMES.index("小刀"),
    }),
    "survival_heal_drill": frozenset({
        IDX_INTERACT_M9_BASE + M9_INTERACT_ITEMS_EXTRA.index("治疗"),
    }),
    "survival_armor_drill": frozenset({
        IDX_INTERACT_BASE + INTERACT_ITEMS.index("陶瓷护甲"),
    }),
    "survival_repair_drill": frozenset({
        IDX_INTERACT_M9_BASE + M9_INTERACT_ITEMS_EXTRA.index("修理陶瓷护甲"),
    }),
    "sharpen_drill": frozenset({
        IDX_INTERACT_BASE + INTERACT_ITEMS.index("磨刀石"),
        IDX_SPECIAL_BASE + SPECIAL_OPS.index("磨刀"),
    }),
    "visibility_drill": frozenset({
        IDX_INTERACT_BASE + INTERACT_ITEMS.index("热成像仪"),
        IDX_INTERACT_BASE + INTERACT_ITEMS.index("探测魔法"),
        IDX_INTERACT_BASE + INTERACT_ITEMS.index("雷达"),
    }),
    "combat_economy": frozenset({
        *range(IDX_INTERACT_BASE, IDX_LOCK_BASE),
        *range(
            IDX_INTERACT_M9_BASE,
            IDX_INTERACT_M9_BASE + M9_INTERACT_ITEMS_EXTRA.index("举报"),
        ),
    }),
    "economy_integration_course": frozenset({
        *range(IDX_INTERACT_BASE, IDX_LOCK_BASE),
        *range(
            IDX_INTERACT_M9_BASE,
            IDX_INTERACT_M9_BASE + M9_INTERACT_ITEMS_EXTRA.index("举报"),
        ),
    }),
}


def apply_action_contract(
    mask: np.ndarray,
    contract: Optional[str],
    *,
    choose_mode: bool = False,
) -> np.ndarray:
    """对引擎合法 mask 施加课程动作合同；choose 回调不截断以避免同步死锁。"""
    if contract is None or choose_mode:
        return mask
    if contract not in ACTION_CONTRACT_FAMILIES:
        raise ValueError(
            f"未知 RL action_contract={contract!r}；"
            f"可选 {sorted(ACTION_CONTRACT_FAMILIES)}"
        )
    allowed = ACTION_CONTRACT_FAMILIES[contract]
    extra_allowed = ACTION_CONTRACT_EXTRA_INDICES.get(contract, frozenset())
    result = mask.copy()
    for idx in np.flatnonzero(result):
        if (
            action_family_from_idx(int(idx)) not in allowed
            and int(idx) not in extra_allowed
        ):
            result[int(idx)] = False
    if not result.any():
        result[IDX_FORFEIT] = True
    return result


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
        wnames = _rl_weapon_names(game_state)
        if weapon_slot >= len(wnames):
            return "forfeit"
        return f"attack {target.name} {wnames[weapon_slot]}"

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

    # ── M9 专属 special（137 – 148；仅 m9-rfc 的 mask 会启用这些索引）──
    if IDX_SPECIAL_M9_BASE <= idx < IDX_SHOOT_M9_BASE:
        m9_off = idx - IDX_SPECIAL_M9_BASE
        if m9_off < len(SPECIAL_OPS_M9):
            return f"special {SPECIAL_OPS_M9[m9_off]}"
        # 模板族：从引擎当前可用特殊操作中挑该族首个具体名（与 report/designate 自动目标同模式）
        fam = SPECIAL_OPS_M9_TEMPLATE[m9_off - len(SPECIAL_OPS_M9)]
        from actions.special_op import get_available_specials as _specials_for_target  # noqa:C0415
        for spec in _specials_for_target(player, game_state):
            if spec["name"].startswith(fam):
                return f"special {spec['name']}"
        return "forfeit"

    # ── M9 shoot 槽（149 – 153）：弓的跨地点射击 ──
    if IDX_SHOOT_M9_BASE <= idx < ACTION_COUNT_M9:
        slot = idx - IDX_SHOOT_M9_BASE
        target = get_opponent_slots(player, game_state)[slot]
        return f"shoot {target.name}" if target else "forfeit"

    # ── 天赋 T0 / choose 索引：不应走 idx_to_command ──
    # 这些索引在 choose 同步路径中被直接解释，不经过此函数。
    # 如果意外到达这里（如 env 模式切换 bug），安全回退。
    if IDX_TALENT_T0_TARGET_BASE <= idx <= IDX_TALENT_T0_SELF:
        return "forfeit"

    if IDX_CHOOSE_BASE <= idx < IDX_CHOOSE_BASE + 16:
        return "forfeit"

    # ── M9 追加：他人住宅 move 槽（追入 home_pX）──
    # 区段 [IDX_MOVE_HOME_M9_BASE .. ACTION_COUNT_M9_FIXED) 固定预留 6 槽；
    # 当前对局不存在的玩家槽永不点亮，防御性解码为 forfeit。
    home_dests = _rl_move_destinations(game_state)
    n_home = len(home_dests) - len(LOCATIONS)
    if IDX_MOVE_HOME_M9_BASE <= idx < IDX_MOVE_HOME_M9_BASE + n_home:
        return f"move {home_dests[len(LOCATIONS) + (idx - IDX_MOVE_HOME_M9_BASE)]}"
    if IDX_MOVE_HOME_M9_BASE <= idx < IDX_INTERACT_M9_BASE:
        return "forfeit"

    # ── M9 追加：动态交互项稳定槽（160–184）──
    if IDX_INTERACT_M9_BASE <= idx < ACTION_COUNT_M9_FIXED:
        if idx < IDX_POLICE_TARGET_M9_BASE:
            return f"interact {M9_INTERACT_ITEMS_EXTRA[idx - IDX_INTERACT_M9_BASE]}"

    # ── M9 参数化警察动作：report/designate × 对手槽 ──
    if IDX_POLICE_TARGET_M9_BASE <= idx < IDX_SPECIAL_TARGET_M9_BASE:
        offset = idx - IDX_POLICE_TARGET_M9_BASE
        verb = M9_POLICE_TARGET_VERBS[offset // M9_TARGET_SLOTS]
        target = get_opponent_slots(player, game_state)[offset % M9_TARGET_SLOTS]
        return f"{verb} {target.name}" if target is not None else "forfeit"

    # ── M9 参数化玩家 special：热线举报/交易 × 对手槽 ──
    if IDX_SPECIAL_TARGET_M9_BASE <= idx < IDX_UNINSTALL_M9_BASE:
        offset = idx - IDX_SPECIAL_TARGET_M9_BASE
        family = M9_PLAYER_SPECIAL_FAMILIES[offset // M9_TARGET_SLOTS]
        target = get_opponent_slots(player, game_state)[offset % M9_TARGET_SLOTS]
        return f"special {family}{target.name}" if target is not None else "forfeit"

    if IDX_UNINSTALL_M9_BASE <= idx < IDX_POLICE_COMMAND_M9_BASE:
        return f"special 拆卸{M9_BOW_MODULE_NAMES[idx - IDX_UNINSTALL_M9_BASE]}"

    if IDX_POLICE_COMMAND_M9_BASE <= idx < ACTION_COUNT_M9_FIXED:
        offset = idx - IDX_POLICE_COMMAND_M9_BASE
        unit_id = M9_POLICE_UNIT_IDS[offset // len(M9_POLICE_UNIT_COMMANDS)]
        command = M9_POLICE_UNIT_COMMANDS[offset % len(M9_POLICE_UNIT_COMMANDS)]
        return f"special 指挥{unit_id}{command}"

    raise ValueError(f"动作索引越界: {idx}（合法范围 0–{rl_action_count(game_state) - 1}）")


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


def _fill_special_mask(mask: np.ndarray, player, game_state) -> None:
    """填充 special 动作掩码。

    m9-rfc：以引擎 actions/special_op.get_available_specials 为单一信源，把引擎
    判定可用的特殊操作映射进 base(SPECIAL_OPS) 或 M9(SPECIAL_OPS_M9) 槽位——
    防止“RL 手写名单脱离引擎导致特殊指令失明”（G7 宏历史 bug）。
    legacy/v2exp：维持原有手写判定，行为不变（不引入回归）。
    """
    from engine.m9.gate import m9_enabled as _m9_gate
    if _m9_gate(game_state):
        from actions.special_op import get_available_specials as _engine_specials
        names = [spec["name"] for spec in _engine_specials(player, game_state)]
        for name in names:
            parameterized = _m9_parameterized_special_idx(name, player, game_state)
            if parameterized >= 0:
                mask[parameterized] = True
                continue
            if name in SPECIAL_OPS:
                mask[IDX_SPECIAL_BASE + SPECIAL_OPS.index(name)] = True
            elif name in SPECIAL_OPS_M9:
                mask[IDX_SPECIAL_M9_BASE + SPECIAL_OPS_M9.index(name)] = True
        # 模板族：引擎出现任一该族实例 → 启用族槽位（目标由 idx_to_command 自动填首项）
        for i, fam in enumerate(SPECIAL_OPS_M9_TEMPLATE):
            if fam in M9_PLAYER_SPECIAL_FAMILIES or fam in ("指挥", "拆卸"):
                continue  # 已按参数展开；族槽保留但 M9 不再点亮
            if any(name.startswith(fam) for name in names):
                mask[IDX_SPECIAL_M9_BASE + len(SPECIAL_OPS_M9) + i] = True
        return

    # ── legacy/v2exp 原逻辑（行为不变）──
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
    mask = np.zeros(rl_action_count(game_state), dtype=bool)

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
    # 委托 engine/action_enumerator._enumerate_move（单一信源）。
    # 必须传 game_state：漏传时引擎不枚举 "move home_pX" → home 追加槽
    # （IDX_MOVE_HOME_M9_BASE+）永远不点亮，蹲家对手照旧不可达（曾实锤）。
    if "move" in available_set and not _hoshino_block_move:
        for cmd in _enumerate_move(player, game_state):
            idx = _cmd_to_move_idx(cmd, game_state)
            if idx >= 0:
                mask[idx] = True

    # ── interact ─────────────────────────────────────────────────
    # 委托 engine/action_enumerator._enumerate_interact（单一信源）
    if "interact" in available_set and not _hoshino_block_interact:
        for cmd in _enumerate_interact(player, game_state):
            idx = _cmd_to_interact_idx(cmd)
            if 0 <= idx < len(mask):
                mask[idx] = True

    # ── lock / find / attack ──────────────────────────────────────
    # 委托 engine/action_enumerator 对应枚举函数（单一信源）
    combat_opponents = []
    opponent_slot_by_name = {}
    if any(action in available_set for action in ("lock", "find", "attack", "shoot")):
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
            idx = _cmd_to_attack_idx(cmd, opponent_slot_by_name, game_state)
            if idx >= 0:
                mask[idx] = True

    # ── shoot（M9：弓的跨地点射击，专属槽 149–153）──────────────────
    if "shoot" in available_set:
        from engine.action_enumerator import _enumerate_shoot
        for cmd in _enumerate_shoot(player, combat_opponents, game_state):
            idx = _cmd_to_shoot_idx(cmd, opponent_slot_by_name)
            if idx >= 0:
                mask[idx] = True

    # ── special ───────────────────────────────────────────────────
    # m9-rfc：以引擎 get_available_specials 为唯一权威（很多 M9 special 如
    # 竞选队长/更衣/卸甲免费find 不依赖持武器，外层 available_set 粗门控会误伤，
    # 故 m9 下直接进入单信源填充）；legacy/v2exp 沿用粗门控 + 原手写逻辑。
    from engine.m9.gate import m9_enabled as _m9_gate_special
    if "special" in available_set or _m9_gate_special(game_state):
        _fill_special_mask(mask, player, game_state)

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
        from engine.m9.gate import m9_enabled as _m9_police_targets
        is_m9 = _m9_police_targets(game_state)
        for key, (offset, ok) in police_available_map.items():
            if not ok:
                continue
            if is_m9 and key in M9_POLICE_TARGET_VERBS:
                enum = (_enumerate_report(game_state, [p for p in _opponents if p])
                        if key == "report" else
                        _enumerate_designate([p for p in _opponents if p]))
                for command in enum:
                    idx = command_to_action_idx(command, player, game_state)
                    if idx >= 0:
                        mask[idx] = True
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

# 历史副本只保留作审计，不参与运行时；权威集合在文件顶部定义。
_DEPRECATED_STRATEGIC_SITUATIONS: set[str] = {
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

# 单一信源：mask 与可见性治理读取同一个集合，禁止两份清单继续漂移。
STRATEGIC_SITUATIONS = STRATEGIC_CHOOSE_SITUATIONS


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
        # M9 往世层投注：目标以稳定 opponent slot 表达，不能随 alive 列表
        # 收缩后退化为菜单序号。
        "m9_betting_target",
        "m9_betting_transfer_in",
    }

    if situation in _TARGET_SITUATIONS:
        # 选项是玩家名字列表，需要映射到对手槽位
        opponents = get_opponent_slots(player, game_state)
        opp_name_to_slot = {}
        for slot, opp in enumerate(opponents):
            if opp is not None:
                opp_name_to_slot[opp.name] = slot
                opp_name_to_slot[opp.player_id] = slot

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
