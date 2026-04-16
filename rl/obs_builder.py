# rl/obs_builder.py
"""
rl/obs_builder.py
─────────────────
观测向量构建器（天赋局，共 515 维 float32）

维度布局：
  ┌─ 原有观测（不变）─────────────────────────────────────────┐
  │ [  0 –  21] 自身状态          (22)                        │
  │ [ 22 –  31] 武器持有          (10)                        │
  │ [ 32 –  38] 护甲持有          (7)                         │
  │ [ 39 –  43] 物品持有          (5)                         │
  │ [ 44 – 228] 对手状态 5×37     (185)                       │
  │ [229 – 243] 警察状态          (15)                        │
  │ [244 – 245] 病毒状态          (2)                         │
  │ [246 – 251] 轮次信息          (6)                         │
  │ [252 – 266] 自身标记          (15)                        │
  │ [267 – 286] 高层特征          (20)                        │
  └───────────────────────────────────────────────────────────┘
  ┌─ 新增：天赋系统观测 ─────────────────────────────────────┐
  │ [287 – 300] 自身天赋 ID       (14) one-hot                │
  │ [301 – 340] 自身天赋状态      (40) -1 哨兵=不适用         │
  │ [341 – 510] 对手天赋 5×(14+20)(170) 交错布局，见下        │
  │   每个对手槽位 34 维 = 天赋ID(14) + 天赋状态(20)          │
  │   slot0=[341-374] slot1=[375-408] slot2=[409-442]         │
  │   slot3=[443-476] slot4=[477-510]                         │
  │ [511]       存活对手数量      (1)  n_alive/5              │
  │ [512 – 514] choose 模式指示器 (3)                         │
  └───────────────────────────────────────────────────────────┘

自身天赋状态 40 维槽位分配（按批次逐步填充）：
  批次 2: [0]       T5 Combo consecutive_actions/3
  批次 3: [0]       T1 uses_remaining/2
          [0]       T3 uses_remaining/1
          [0-4]     T2 response_uses/2, triggered_crimes/5,
                    find_triggered, found_triggered, attack_count/10
  批次 4: [0-3]     T4 charges/2, round_counter/6, immunity, disabled_count/3
          [0-3]     T7 learned, learn_progress/2, mounted_slot(5-hot), used
          [0-3]     G6 laugh_points/6, cutaway_charges/3, d4_force, threshold/6
  批次 5: [0-4]     G1 debuff_started, action_count/20, supernova, ardent/3, burn_count/5
          [0-7]     G4 divinity/12, is_savior, duration/12, temp_hp/6,
                    atk_bonus/6, spent, can_active, aoe_bonus/2
  批次 6: [0-14]    G2 active, duration/6, location(6-hot), targets_count/5, ...
          [0-7]     G3 barrier_active, barrier_round/10, partner_slot(5-hot), ...
          [0-31]    G5 reminiscence/24, threshold/24, total_uses/5,
                    anchor_active, anchor_type(4-hot), anchor_rounds/5,
                    destructive/3, fate/5, variance/5, can_activate,
                    锚定评估 5×3(feasible,fate/5,variance/5),
                    poem_use/5, destiny_use/5, destiny_cost/24, love_wish
  批次 7: [0-24]    G7 form(3-hot), cost/5, iron_horus_hp/2, shield_mode,
                    ammo_count/6, is_terror, terror_hp/6, color(3), fused,
                    tactical_unlocked, front/5, back/5, guard_mode, ...

对手天赋状态 20 维槽位分配（公开信息子集）：
  [0-4]   通用：uses_remaining 或 charges 或 关键计数
  [5-9]   状态标记：immunity/savior/terror/barrier/hologram 等
  [10-14] 数值：divinity/temp_hp/terror_hp/laugh_points 等
  [15-19] 预留

存活对手数量：
  [511] n_alive_opponents / 5.0

choose 模式指示器：
  [512] is_choose_mode: 0=get_command, 1=choose
  [513] choose_situation_id / 29.0 (归一化)
  [514] choose_n_options / 16.0 (归一化)
"""

from __future__ import annotations
from typing import TYPE_CHECKING, Optional
import numpy as np

if TYPE_CHECKING:
    from models.player import Player
    from engine.game_state import GameState

from rl.action_space import LOCATIONS, WEAPONS, get_opponent_slots

# ─────────────────────────────────────────────────────────────────────────────
#  常量
# ─────────────────────────────────────────────────────────────────────────────

OBS_DIM = 515
_CHOOSE_OBS_DIM = 3

# 原有观测维度（用于内部断言，不对外暴露）
_BASE_OBS_DIM = 287

# 天赋系统维度
_TALENT_ID_DIM = 14          # 14 种天赋的 one-hot
_SELF_TALENT_STATE_DIM = 40  # 自身天赋状态
_OPP_TALENT_ID_DIM = 14      # 每个对手的天赋 ID
_OPP_TALENT_STATE_DIM = 20   # 每个对手的天赋状态
_CHOOSE_MODE_DIM = 3         # choose 模式指示器

# 每个对手槽位的维度（原有）
_OPP_DIM = 37

# 天赋类名 → one-hot 索引（与 game_setup.TALENT_TABLE 编号一致，0-indexed）
TALENT_CLASS_TO_IDX: dict[str, int] = {
    "OneSlash":    0,   # 天赋 1
    "ScissorRush": 1,   # 天赋 2
    "Star":        2,   # 天赋 3
    "Hexagram":    3,   # 天赋 4
    "Combo":       4,   # 天赋 5
    "GoodCitizen": 5,   # 天赋 6
    "Resurrection":6,   # 天赋 7
    "G1MythFire":  7,   # 天赋 8 (神代1)
    "Hologram":    8,   # 天赋 9 (神代2)
    "Mythland":    9,   # 天赋 10 (神代3)
    "Savior":      10,  # 天赋 11 (神代4)
    "Ripple":      11,  # 天赋 12 (神代5)
    "CutawayJoke": 12,  # 天赋 13 (神代6)
    "Hoshino":     13,  # 天赋 14 (神代7)
}

_TALENT_CLASS_INDEX = TALENT_CLASS_TO_IDX  # alias for backward compat

# 武器名称列表（与 action_space.WEAPONS 一致，共 10 种）
WEAPON_NAMES = WEAPONS

# 护甲名称列表（固定顺序，共 7 种）
ARMOR_NAMES = [
    "盾牌",       # 0
    "陶瓷护甲",   # 1
    "魔法护盾",   # 2
    "AT力场",     # 3
    "晶化皮肤",   # 4
    "额外心脏",   # 5
    "不老泉",     # 6
]

# 物品功能类别（合并等效物品，共 5 类）
# 隐身衣/隐形涂层 → 同一槽位；热成像仪/探测魔法 → 同一槽位
ITEM_CATEGORIES: list[set[str]] = [
    {"防毒面具"},                    # 0: 防毒
    {"磨刀石"},                      # 1: 磨刀石
    {"隐身衣", "隐形涂层"},          # 2: 隐身类物品
    {"热成像仪", "探测魔法"},        # 3: 探测类 A
    {"雷达"},                        # 4: 探测类 B
]

# 归一化上界
_MAX_HP        = 5.0
_MAX_VOUCHERS  = 3.0
_MAX_KILL      = 5.0
_MAX_STREAK    = 6.0
_MAX_ROUND     = 100.0
_MAX_CRIMES    = 3.0
_MAX_POLICE    = 3.0
_MAX_VIRUS_CD  = 3.0
_MAX_AUTHORITY = 5.0
_MAX_SPELLS    = 8.0
_MAX_OUTER     = 3.0
_MAX_INNER     = 3.0

# 阶段名 → one-hot 索引
_PHASE_INDEX = {
    "r0_start":    0,
    "r1_d4":       1,
    "r2_priority": 2,
    "r3_actions":  3,
    "r4_end":      4,
}

# 地点名 → one-hot 索引
_LOC_INDEX = {loc: i for i, loc in enumerate(LOCATIONS)}

# 警察举报阶段 → one-hot 索引
_REPORT_PHASE_INDEX = {
    "idle":       0,
    "reported":   1,
    "assembled":  2,
    "dispatched": 3,
}


def _normalize_location(loc: str | None) -> str:
    """将 home_xxx 归一化为 home，None 归一化为空字符串"""
    if loc is None:
        return ""
    if loc.startswith("home_"):
        return "home"
    return loc


# ─────────────────────────────────────────────────────────────────────────────
#  天赋观测辅助函数
# ─────────────────────────────────────────────────────────────────────────────

def _get_talent_class_name(player: "Player") -> str | None:
    """获取玩家天赋的类名，无天赋返回 None"""
    talent = getattr(player, 'talent', None)
    if talent is None:
        return None
    return talent.__class__.__name__


def _encode_talent_id(player: "Player", buf: np.ndarray, offset: int) -> None:
    """将玩家天赋编码为 one-hot 写入 buf[offset : offset+14]"""
    cls_name = _get_talent_class_name(player)
    if cls_name is not None and cls_name in TALENT_CLASS_TO_IDX:
        buf[offset + TALENT_CLASS_TO_IDX[cls_name]] = 1.0

def _build_talent_id(player) -> np.ndarray:
    """返回 14 维 one-hot 天赋 ID 向量。无天赋时全零。"""
    buf = np.zeros(_TALENT_ID_DIM, dtype=np.float32)
    talent = getattr(player, 'talent', None)
    if talent is not None:
        cls_name = talent.__class__.__name__
        idx = _TALENT_CLASS_INDEX.get(cls_name, -1)
        if 0 <= idx < _TALENT_ID_DIM:
            buf[idx] = 1.0
    return buf


def _build_self_talent_state(player: "Player") -> np.ndarray:
    """
    构建自身天赋状态向量（40 维）。
    不适用的维度填 -1（哨兵值），实际值归一化到 [0, 1]。

    批次 1：全部返回 -1（框架占位）。
    后续批次在此函数中逐步添加各天赋的状态编码。
    """
    buf = np.full(_SELF_TALENT_STATE_DIM, -1.0, dtype=np.float32)

    talent = getattr(player, 'talent', None)
    if talent is None:
        return buf

    cls = talent.__class__.__name__

    # ──────────────────────────────────────────────────────────
    #  批次 2: 纯被动天赋（T5 Combo, T6 GoodCitizen）
    # ──────────────────────────────────────────────────────────
    if cls == "Combo":
        buf[0] = getattr(talent, 'consecutive_actions', 0) / 3.0
        buf[1] = float(getattr(talent, '_bonus_round_active', False))
        buf[2] = float(getattr(talent, '_d4_force', False))
        return buf

    if cls == "GoodCitizen":
        # T6 纯被动，无内部状态需要编码（效果体现在 action mask 中）
        # 但标记为"有天赋"（非 -1）以区分无天赋
        buf[0] = 0.0  # 占位：表示"此天赋存在但无动态状态"
        return buf

    # ──────────────────────────────────────────────────────────
    #  批次 3: 简单主动天赋（T1, T3, T2 被动部分）
    # ──────────────────────────────────────────────────────────
    if cls == "OneSlash":
        buf[0] = getattr(talent, 'uses_remaining', 0) / 2.0
        return buf

    if cls == "Star":
        buf[0] = getattr(talent, 'uses_remaining', 0) / 1.0
        return buf

    if cls == "ScissorRush":
        buf[0] = getattr(talent, 'response_uses_remaining', 0) / 2.0
        buf[1] = len(getattr(talent, 'triggered_crime_types', set())) / 5.0
        buf[2] = float(getattr(talent, 'find_triggered', False))
        buf[3] = float(getattr(talent, 'found_triggered', False))
        buf[4] = getattr(talent, 'attack_count', 0) / 10.0
        return buf

    # ──────────────────────────────────────────────────────────
    #  批次 4: 中等天赋（T4, T7, G6）
    # ──────────────────────────────────────────────────────────
    if cls == "Hexagram":
        buf[0] = getattr(talent, 'charges', 0) / 2.0
        buf[1] = getattr(talent, 'round_counter', 0) / 6.0
        buf[2] = float(getattr(talent, 'immunity_active', False))
        buf[3] = len(getattr(talent, 'disabled_weapons', [])) / 3.0
        return buf

    if cls == "Resurrection":
        buf[0] = float(getattr(talent, 'learned', False))
        buf[1] = getattr(talent, 'learn_progress', 0) / 2.0
        buf[2] = float(getattr(talent, 'used', False))
        # mounted_on: 编码为对手槽位 one-hot [3-7]
        mounted_id = getattr(talent, 'mounted_on', None)
        if mounted_id is not None:
            # 需要 game_state 来解析槽位，这里用简单标记
            buf[3] = 1.0  # 已挂载
        else:
            buf[3] = 0.0  # 未挂载
        return buf

    if cls == "CutawayJoke":
        buf[0] = getattr(talent, 'laugh_points', 0) / 6.0
        buf[1] = getattr(talent, 'cutaway_charges', 0) / 3.0
        buf[2] = float(getattr(talent, '_d4_force', False))
        effective_threshold = getattr(talent, 'laugh_threshold', 6) - getattr(talent, 'forfeit_reduction', 0)
        buf[3] = max(0, effective_threshold) / 6.0
        return buf

    # ──────────────────────────────────────────────────────────
    #  批次 5: 神代被动（G1, G4）
    # ──────────────────────────────────────────────────────────
    if cls == "G1MythFire":
        buf[0] = float(getattr(talent, 'debuff_started', False))
        buf[1] = getattr(talent, 'action_turn_count', 0) / 20.0
        buf[2] = float(getattr(talent, 'has_supernova', False))
        buf[3] = getattr(talent, 'ardent_wish_charges', 0) / 3.0
        buf[4] = len(getattr(talent, 'burn_targets', {})) / 5.0
        return buf

    if cls == "Savior":
        buf[0] = getattr(talent, 'divinity', 0) / 12.0
        buf[1] = float(getattr(talent, 'is_savior', False))
        buf[2] = getattr(talent, 'savior_duration', 0) / 12.0
        buf[3] = getattr(talent, 'temp_hp', 0) / 6.0
        buf[4] = getattr(talent, 'temp_attack_bonus', 0) / 6.0
        buf[5] = float(getattr(talent, 'spent', False))
        buf[6] = float(getattr(talent, 'can_active_start', False))
        buf[7] = getattr(talent, 'aoe_bonus', 0) / 2.0
        return buf

    # ──────────────────────────────────────────────────────────
    #  批次 6: 神代主动（G2, G3, G5）
    # ──────────────────────────────────────────────────────────
    if cls == "Hologram":
        buf[0] = float(getattr(talent, 'active', False))
        buf[1] = getattr(talent, 'remaining_rounds', 0) / 6.0
        buf[2] = float(getattr(talent, 'used', False))
        return buf

    if cls == "Mythland":
        buf[0] = float(getattr(talent, 'barrier_active', False))
        buf[1] = float(getattr(talent, 'used', False))
        return buf

    if cls == "Ripple":
        buf[0] = getattr(talent, 'reminiscence', 0) / 24.0
        buf[1] = getattr(talent, 'activation_threshold', 24) / 24.0
        buf[2] = getattr(talent, 'total_uses', 0) / 5.0
        buf[3] = float(getattr(talent, 'anchor_active', False))
        # anchor_type one-hot [4-7]
        atype = getattr(talent, 'anchor_type', None)
        type_map = {"kill": 4, "break_armor": 5, "acquire": 6, "arrive": 7}
        if atype in type_map:
            buf[type_map[atype]] = 1.0
        buf[8] = getattr(talent, 'anchor_rounds_left', 0) / 5.0
        buf[9] = getattr(talent, 'anchor_destructive_count', 0) / 3.0
        buf[10] = getattr(talent, 'anchor_fate', 0) / 5.0
        buf[11] = getattr(talent, 'anchor_variance', 0) / 5.0

        # can_activate_now
        can_activate = (
            getattr(talent, 'reminiscence', 0) >= getattr(talent, 'activation_threshold', 24)
            and not getattr(talent, 'anchor_active', False)
        )
        buf[12] = float(can_activate)

        # 锚定评估结果（仅在可激活时计算，否则全 -1）
        if can_activate and hasattr(player, 'player_id'):
            _fill_anchor_eval(buf, 13, talent, player, getattr(talent, 'state', None))
        else:
            buf[13:28] = -1.0  # 哨兵值：不适用

        # 献诗相关
        buf[28] = sum(getattr(talent, 'poem_use_counts', {}).values()) / 5.0
        buf[29] = getattr(talent, 'destiny_use_count', 0) / 5.0
        buf[30] = talent.get_destiny_cost() / 24.0 if hasattr(talent, 'get_destiny_cost') else 0.5
        buf[31] = float(bool(getattr(talent, 'love_wish', {})))
        return buf

    # ──────────────────────────────────────────────────────────
    #  批次 7: G7 星野
    # ──────────────────────────────────────────────────────────
    if cls == "Hoshino":
        # 形态 one-hot [0-2]: 临战-Archer / 临战-shielder / 水着-shielder
        form = getattr(talent, 'form', None)
        form_map = {"临战-Archer": 0, "临战-shielder": 1, "水着-shielder": 2}
        if form in form_map:
            buf[form_map[form]] = 1.0
        else:
            buf[0:3] = 0.0  # 未融合，无形态

        buf[3] = getattr(talent, 'cost', 0) / 5.0
        buf[4] = getattr(talent, 'iron_horus_hp', 0) / 2.0
        buf[5] = float(getattr(talent, 'shield_mode', '') == '架盾')
        buf[6] = len(getattr(talent, 'ammo', [])) / 6.0
        buf[7] = float(getattr(talent, 'is_terror', False))
        buf[8] = getattr(talent, 'terror_extra_hp', 0) / 6.0
        buf[9] = float(getattr(talent, 'fused', False))
        buf[10] = float(getattr(talent, 'tactical_unlocked', False))
        buf[11] = len(getattr(talent, 'front_players', set())) / 5.0
        buf[12] = len(getattr(talent, 'back_players', set())) / 5.0
        buf[13] = float(getattr(talent, 'shield_guard_mode', '') == 'block_entering')

        # 色彩值 [14-16]
        color_values = getattr(talent, 'color_values', {})
        buf[14] = color_values.get('red', 0) / 10.0
        buf[15] = color_values.get('blue', 0) / 10.0
        buf[16] = color_values.get('yellow', 0) / 10.0

        # 弹药属性摘要 [17-19]: 普通/魔法/科技弹药数量
        ammo = getattr(talent, 'ammo', [])
        for bullet in ammo:
            attr = bullet.get('attribute', '普通') if isinstance(bullet, dict) else '普通'
            if attr == '普通':
                buf[17] = (buf[17] + 1.0) if buf[17] >= 0 else 1.0
            elif attr == '魔法':
                buf[18] = (buf[18] + 1.0) if buf[18] >= 0 else 1.0
            elif attr == '科技':
                buf[19] = (buf[19] + 1.0) if buf[19] >= 0 else 1.0
        # 如果有弹药但某属性为0，设为0而非-1；然后归一化
        if ammo:
            for i in range(17, 20):
                if buf[i] < 0:
                    buf[i] = 0.0
                else:
                    buf[i] = min(buf[i], 6.0) / 6.0

        return buf

    # 未知天赋类型：保持全 -1
    return buf


def _fill_anchor_eval(buf, offset, talent, player, game_state):
    """
    为 G5 天赋填充锚定评估结果。
    对每个存活对手运行 AnchorVerifier.verify_kill()，
    将 (feasible, fate/5, variance/5) 写入 buf[offset + slot*3 : offset + slot*3 + 3]。
    """
    if game_state is None:
        buf[offset:offset + 15] = -1.0
        return

    try:
        from engine.anchor_resolver import AnchorVerifier
    except ImportError:
        buf[offset:offset + 15] = -1.0
        return

    verifier = AnchorVerifier(game_state)
    opp_slots = get_opponent_slots(player, game_state)

    for slot_idx in range(5):
        base = offset + slot_idx * 3
        if slot_idx < len(opp_slots):
            opp = opp_slots[slot_idx]
            if opp is not None and opp.is_alive():
                try:
                    result = verifier.verify_kill(player, opp)
                    buf[base] = float(result.feasible)
                    buf[base + 1] = result.fate / 5.0
                    buf[base + 2] = result.variance / 5.0
                    continue
                except Exception:
                    pass
        # 对手不存在或已死亡或评估失败
        buf[base] = -1.0
        buf[base + 1] = -1.0
        buf[base + 2] = -1.0


def _build_opp_talent_state(opp: "Player") -> np.ndarray:
    """
    构建对手天赋状态向量（20 维，公开信息子集）。
    不适用的维度填 -1（哨兵值）。

    批次 1：全部返回 -1（框架占位）。
    后续批次逐步添加各天赋的公开状态编码。
    """
    buf = np.full(_OPP_TALENT_STATE_DIM, -1.0, dtype=np.float32)

    talent = getattr(opp, 'talent', None)
    if talent is None:
        return buf

    cls = talent.__class__.__name__

    # ── 通用：uses / charges / 关键计数 [0-4] ──
    if cls == "OneSlash":
        buf[0] = getattr(talent, 'uses_remaining', 0) / 2.0
    elif cls == "Star":
        buf[0] = getattr(talent, 'uses_remaining', 0) / 1.0
    elif cls == "Hexagram":
        buf[0] = getattr(talent, 'charges', 0) / 2.0
        buf[1] = float(getattr(talent, 'immunity_active', False))
    elif cls == "Combo":
        buf[0] = getattr(talent, 'consecutive_actions', 0) / 3.0
    elif cls == "ScissorRush":
        buf[0] = getattr(talent, 'response_uses_remaining', 0) / 2.0
    elif cls == "Resurrection":
        buf[0] = float(getattr(talent, 'learned', False))
        buf[1] = float(getattr(talent, 'used', False))
        buf[2] = float(getattr(talent, 'mounted_on', None) is not None)
    elif cls == "GoodCitizen":
        buf[0] = 0.0  # 存在标记
    elif cls == "CutawayJoke":
        buf[0] = getattr(talent, 'laugh_points', 0) / 6.0
        buf[1] = getattr(talent, 'cutaway_charges', 0) / 3.0

    # ── 神代天赋公开状态 [5-19] ──
    elif cls == "G1MythFire":
        buf[0] = float(getattr(talent, 'debuff_started', False))
        buf[1] = float(getattr(talent, 'has_supernova', False))
        buf[2] = len(getattr(talent, 'burn_targets', {})) / 5.0

    elif cls == "Hologram":
        buf[0] = float(getattr(talent, 'used', False))
        buf[1] = float(getattr(talent, 'active', False))
        buf[2] = getattr(talent, 'remaining_rounds', 0) / 6.0
        buf[3] = float(getattr(talent, 'enhanced', False))
        # buf[4]: 影像所在地点是否与"我"相同（需要 rl_player 信息，
        #         在 _build_opponent_block 中由调用方填充，此处留 -1）

    elif cls == "Mythland":
        buf[0] = float(getattr(talent, 'used', False))
        buf[1] = float(getattr(talent, 'active', False))
        buf[2] = getattr(talent, 'barrier_round', 0) / 5.0
        # buf[3]: 我是否在结界内（需要 rl_player 信息，由调用方填充）

    elif cls == "Savior":
        buf[0] = getattr(talent, 'divinity', 0) / 12.0
        buf[1] = float(getattr(talent, 'is_savior', False))
        buf[2] = getattr(talent, 'temp_hp', 0) / 6.0
        buf[3] = float(getattr(talent, 'spent', False))
        buf[4] = float(getattr(talent, 'can_active_start', False))
        buf[5] = getattr(talent, 'savior_duration', 0) / 12.0

    elif cls == "Ripple":
        buf[0] = getattr(talent, 'reminiscence', 0) / 24.0
        buf[1] = float(getattr(talent, 'anchor_active', False))
        buf[2] = getattr(talent, 'anchor_rounds_left', 0) / 5.0
        buf[3] = getattr(talent, 'anchor_destructive_count', 0) / 3.0
        buf[4] = getattr(talent, 'anchor_fate', 0) / 5.0
        buf[5] = getattr(talent, 'anchor_variance', 0) / 5.0
        # buf[6]: RL 是否是被锚定的目标（需要 rl_player 信息，由调用方填充）
        buf[7] = float(getattr(talent, 'reminiscence', 0) >= getattr(talent, 'activation_threshold', 24))  # 对手即将可以锚定
        buf[8] = getattr(talent, 'total_uses', 0) / 5.0
        # buf[9]: 我是否持有此 G5 的爱愿（需要 rl_player 信息，由调用方填充）

    elif cls == "Hoshino":
        # 形态 one-hot [0-2]
        form = getattr(talent, 'form', None)
        if form == "水着-shielder":
            buf[0] = 1.0
        elif form == "临战-Archer":
            buf[1] = 1.0
        elif form == "临战-shielder":
            buf[2] = 1.0
        # 核心状态 [3-12]
        buf[3] = getattr(talent, 'cost', 0) / 10.0
        buf[4] = getattr(talent, 'iron_horus_hp', 0) / 2.0
        buf[5] = float(getattr(talent, 'tactical_unlocked', False))
        buf[6] = float(getattr(talent, 'is_terror', False))
        buf[7] = getattr(talent, 'terror_extra_hp', 0) / 6.0
        # 盾牌模式
        sm = getattr(talent, 'shield_mode', None)
        if sm == "持盾":
            buf[8] = 0.5
        elif sm == "架盾":
            buf[8] = 1.0
        else:
            buf[8] = 0.0
        # 光环
        halos = getattr(talent, 'halos', [])
        buf[9] = sum(1 for h in halos if h.get('active', False)) / 3.0
        # 色彩
        buf[10] = getattr(talent, 'color', 0) / 10.0
        buf[11] = float(getattr(talent, 'color_is_null', False))
        # 弹药数量
        buf[12] = len(getattr(talent, 'ammo', [])) / 8.0
        # 永久额外HP
        buf[13] = getattr(talent, 'permanent_extra_hp', 0) / 3.0
        # 战斗续行免死
        buf[14] = float(getattr(talent, '_combat_continuation_immunity', False))

    # 未知天赋类型：保持全 -1
    return buf


def _fill_opp_talent_relational(
    opp_talent_buf: np.ndarray,
    opp: "Player",
    rl_player: "Player",
    game_state: "GameState",
) -> None:
    """
    填充对手天赋状态中依赖 RL 玩家信息的关系维度。
    直接修改 opp_talent_buf（原地写入）。

    这些维度在 _build_opp_talent_state 中被留为 -1，
    因为它们需要知道"我是谁"才能计算。
    """
    talent = getattr(opp, 'talent', None)
    if talent is None:
        return

    cls = talent.__class__.__name__
    rl_pid = rl_player.player_id

    if cls == "Hologram":
        # buf[4]: 影像地点是否与我相同
        if getattr(talent, 'active', False) and talent.location is not None:
            opp_talent_buf[4] = float(rl_player.location == talent.location)

    elif cls == "Mythland":
        # buf[3]: 我是否在结界内
        if getattr(talent, 'active', False):
            barrier_players = getattr(talent, 'barrier_players', [])
            opp_talent_buf[3] = float(rl_pid in barrier_players)

    elif cls == "Ripple":
        # buf[6]: RL 是否是被锚定的目标
        if getattr(talent, 'anchor_active', False):
            opp_talent_buf[6] = float(
                getattr(talent, 'anchor_target_id', None) == rl_pid
            )
        # buf[9]: 我是否持有此 G5 的爱愿
        love_wish = getattr(talent, 'love_wish', {})
        opp_talent_buf[9] = float(love_wish.get(rl_pid, 0) > 0)


# ════════════════════════════════════════════════════════════
#  choose 模式观测（3 维）
# ════════════════════════════════════════════════════════════

# 战略 situation → 编号映射
_CHOOSE_SITUATION_MAP: dict[str, int] = {
    "talent_t0":                1,
    "petrified":                2,
    "recruit_pick_1":           3,
    "recruit_pick_2":           4,
    "captain_election":         5,
    "hexagram_my_choice":       6,
    "hexagram_opp_choice":      7,
    "hexagram_pick_target":     8,
    "oneslash_pick_target":     9,
    "oneslash_pick_weapon":    10,
    "resurrection_pick_target": 11,
    "hologram_target":         12,
    "mythland_pick_target":    13,
    "ripple_choose_method":    14,
    "ripple_anchor_type":      15,
    "ripple_poem_target":      16,
    "savior_activate":         17,
    "hoshino_form_choice":     18,
    "hoshino_self_doubt_choice": 19,
    "cutaway_borrow_target":   20,
    "talent_pick":             21,
    # G5 锚定系统新增
    "ripple_anchor_kill_target":  22,
    "ripple_anchor_armor_target": 23,
    "ripple_anchor_armor_pick":   24,
    "ripple_anchor_acquire_item": 25,
    "ripple_anchor_arrive_loc":   26,
    "ripple_anchor_fail":         27,
    # G5 献诗系统新增
    "ripple_destiny_damage":      28,
    "ripple_hexagram_free_choice": 29,
}
_MAX_CHOOSE_SITUATIONS = 29


def build_choose_obs(situation: str, n_options: int) -> np.ndarray:
    """
    构建 choose 模式的 3 维附加观测。
    [0] current_mode: 1.0 = choose 模式, 0.0 = get_command 模式
    [1] choose_situation_id: 归一化的 situation 编号
    [2] choose_n_options: 选项数量 / 16
    """
    buf = np.zeros(_CHOOSE_OBS_DIM, dtype=np.float32)
    buf[0] = 1.0  # choose 模式
    buf[1] = _CHOOSE_SITUATION_MAP.get(situation, 0) / _MAX_CHOOSE_SITUATIONS
    buf[2] = min(n_options, 16) / 16.0
    return buf


def build_normal_mode_choose_obs() -> np.ndarray:
    """get_command 模式下的 choose 观测（全零）。"""
    return np.zeros(_CHOOSE_OBS_DIM, dtype=np.float32)


# ════════════════════════════════════════════════════════════
#  主入口：build_obs（重写）
# ════════════════════════════════════════════════════════════

def build_obs(player: "Player", game_state: "GameState",
              rl_player_id: str,
              choose_mode: bool = False,
              choose_situation: str = "",
              choose_n_options: int = 0) -> np.ndarray:
    """
    构建完整观测向量（OBS_DIM 维）。

    参数:
        player:           当前 RL 玩家对象
        game_state:       游戏状态（可能是 FilteredGameState）
        rl_player_id:     RL 玩家 ID
        choose_mode:      是否处于 choose 决策模式
        choose_situation:  choose 的 situation 字符串
        choose_n_options:  choose 的选项数量

    维度布局:
        [  0 – 21 ]  自身基础状态          (22)
        [ 22 – 31 ]  武器持有              (10)
        [ 32 – 38 ]  护甲持有              (7)
        [ 39 – 43 ]  物品持有              (5)
        [ 44 –228 ]  对手基础状态 5×37     (185)  ← 不变
        [229 –243 ]  警察状态              (15)
        [244 –245 ]  病毒状态              (2)
        [246 –251 ]  轮次信息              (6)
        [252 –266 ]  自身标记              (15)
        [267 –286 ]  高层特征              (20)
        ─── 以下为新增 ───
        [287 –300 ]  自身天赋 ID           (14)
        [301 –340 ]  自身天赋状态          (40)  哨兵 -1
        [341 –510 ]  对手天赋 5×(ID14+状态20) (170) 交错布局
                     slot0=[341-374] slot1=[375-408] ...
        [511]        存活对手数量          (1)  n_alive/5
        [512 –514 ]  choose 模式指示       (3)
        ─── 总计 515 ───
    """
    from models.equipment import ArmorLayer
    from utils.attribute import Attribute

    obs = np.zeros(OBS_DIM, dtype=np.float32)
    idx = 0

    obs[idx] = player.hp / _MAX_HP;                          idx += 1  # 0
    obs[idx] = player.max_hp / _MAX_HP;                      idx += 1  # 1
    obs[idx] = player.vouchers / _MAX_VOUCHERS;              idx += 1  # 2

    # location one-hot (6 维)
    loc_i = _LOC_INDEX.get(_normalize_location(player.location), -1) if player.location else -1
    if loc_i >= 0:
        obs[idx + loc_i] = 1.0
    idx += len(LOCATIONS)                                              # 3-8

    obs[idx] = float(player.is_awake);                       idx += 1  # 9
    obs[idx] = float(player.is_stunned);                     idx += 1  # 10
    obs[idx] = float(player.is_shocked);                     idx += 1  # 11
    obs[idx] = float(player.is_petrified);                   idx += 1  # 12
    obs[idx] = float(player.is_invisible);                   idx += 1  # 13
    obs[idx] = float(player.is_captain);                     idx += 1  # 14
    obs[idx] = float(player.is_criminal);                    idx += 1  # 15
    obs[idx] = player.kill_count / _MAX_KILL;                idx += 1  # 16
    obs[idx] = player.no_action_streak / _MAX_STREAK;        idx += 1  # 17
    obs[idx] = float(player.has_military_pass);              idx += 1  # 18
    # ── 新增 ──
    obs[idx] = float(getattr(player, 'has_detection', False));       idx += 1  # 19
    obs[idx] = float(getattr(player, 'has_police_protection', False)); idx += 1  # 20
    obs[idx] = float(getattr(player, 'is_police', False));           idx += 1  # 21

    assert idx == 22

    # ══════════════════════════════════════════════════════════════════════════
    #  武器持有 (10)  [22 – 31]
    # ══════════════════════════════════════════════════════════════════════════
    owned_weapons = {w.name for w in (player.weapons or []) if w}
    for wi, wname in enumerate(WEAPON_NAMES):
        obs[idx + wi] = float(wname in owned_weapons)
    idx += len(WEAPON_NAMES)

    assert idx == 32

    # ══════════════════════════════════════════════════════════════════════════
    #  护甲持有 (7)  [32 – 38]
    # ══════════════════════════════════════════════════════════════════════════
    active_armor_names: set[str] = set()
    if hasattr(player, "armor") and player.armor:
        for piece in player.armor.get_all_active():
            active_armor_names.add(piece.name)
    for ai, aname in enumerate(ARMOR_NAMES):
        obs[idx + ai] = float(aname in active_armor_names)
    idx += len(ARMOR_NAMES)

    assert idx == 39

    # ══════════════════════════════════════════════════════════════════════════
    #  物品持有 (5)  [39 – 43]
    # ══════════════════════════════════════════════════════════════════════════
    owned_items = {item.name for item in (player.items or [])}
    for ii, category in enumerate(ITEM_CATEGORIES):
        obs[idx + ii] = float(bool(owned_items & category))
    idx += len(ITEM_CATEGORIES)

    assert idx == 44

    # ══════════════════════════════════════════════════════════════════════════
    #  对手状态 (5 × 37 = 185)  [44 – 228]
    #  所有信息完全透明（隐身仅影响 lock/find 的 action mask，不影响观测）
    # ══════════════════════════════════════════════════════════════════════════
    opponents = get_opponent_slots(player, game_state)
    for slot in range(5):
        opp = opponents[slot]
        base = idx + slot * _OPP_DIM

        if opp is None or not opp.is_alive():
            # 死亡 / 不存在 → 全零（is_alive=0 已足够标识）
            pass
        else:
            # ── 原有 12 维 ──
            obs[base + 0] = 1.0                              # is_alive
            obs[base + 1] = float(opp.is_awake)              # is_awake
            obs[base + 2] = opp.hp / _MAX_HP                 # hp
            # location one-hot (6)
            opp_loc_i = _LOC_INDEX.get(_normalize_location(opp.location), -1)
            if opp_loc_i >= 0:
                obs[base + 3 + opp_loc_i] = 1.0
            obs[base + 9]  = opp.kill_count / _MAX_KILL      # kill_count
            obs[base + 10] = float(opp.is_captain)           # is_captain
            obs[base + 11] = float(opp.is_criminal)          # is_criminal

            # ── 新增：武器持有 (10 binary) [offset 12-21] ──
            opp_weapons = {w.name for w in (opp.weapons or []) if w}
            for wi, wname in enumerate(WEAPON_NAMES):
                obs[base + 12 + wi] = float(wname in opp_weapons)

            # ── 新增：外层护甲属性 (3 binary: 普通/魔法/科技) [offset 22-24] ──
            opp_outer = []
            if hasattr(opp, "armor") and opp.armor:
                opp_outer = opp.armor.get_active(ArmorLayer.OUTER)
            outer_attrs = {piece.attribute for piece in opp_outer}
            obs[base + 22] = float(Attribute.ORDINARY in outer_attrs)
            obs[base + 23] = float(Attribute.MAGIC in outer_attrs)
            obs[base + 24] = float(Attribute.TECH in outer_attrs)

            # ── 新增：护甲数量 [offset 25-26] ──
            opp_inner = []
            if hasattr(opp, "armor") and opp.armor:
                opp_inner = opp.armor.get_active(ArmorLayer.INNER)
            obs[base + 25] = len(opp_outer) / _MAX_OUTER
            obs[base + 26] = len(opp_inner) / _MAX_INNER

            # ── 新增：防毒面具 [offset 27] ──
            opp_items = {item.name for item in (opp.items or [])}
            obs[base + 27] = float("防毒面具" in opp_items)

            # ── 新增：debuff 状态 [offset 28-30] ──
            obs[base + 28] = float(opp.is_stunned)
            obs[base + 29] = float(opp.is_shocked)
            obs[base + 30] = float(opp.is_petrified)

            # ── 新增：隐身 [offset 31] ──
            obs[base + 31] = float(opp.is_invisible)

            # ── 新增：经济 [offset 32] ──
            obs[base + 32] = opp.vouchers / _MAX_VOUCHERS

            # ── 新增：通行证 [offset 33] ──
            obs[base + 33] = float(getattr(opp, 'has_military_pass', False))

            # ── 新增：探测能力 [offset 34] ──
            obs[base + 34] = float(getattr(opp, 'has_detection', False))

            # ── 新增：警察保护 [offset 35] ──
            obs[base + 35] = float(getattr(opp, 'has_police_protection', False))

            # ── 新增：已学法术数 [offset 36] ──
            obs[base + 36] = len(getattr(opp, 'learned_spells', set())) / _MAX_SPELLS

    idx += 5 * _OPP_DIM  # 5 * 37 = 185

    assert idx == 229

    # ══════════════════════════════════════════════════════════════════════════
    #  警察状态 (15)  [229 – 243]
    # ══════════════════════════════════════════════════════════════════════════
    police = game_state.police
    my_crimes = police.crime_records.get(player.player_id, set())
    obs[idx] = len(my_crimes) / _MAX_CRIMES;                 idx += 1  # 229
    obs[idx] = float(police.has_captain());                   idx += 1  # 230
    obs[idx] = float(police.captain_id == player.player_id);  idx += 1  # 231
    obs[idx] = len(police.alive_units()) / _MAX_POLICE;       idx += 1  # 232

    # ── 新增：report_phase one-hot (4 维) [233-236] ──
    rp_i = _REPORT_PHASE_INDEX.get(police.report_phase, -1)
    if 0 <= rp_i < 4:
        obs[idx + rp_i] = 1.0
    idx += 4

    # ── 新增：reported_target 对手槽位 one-hot (5 维) [237-241] ──
    reported_target_id = police.reported_target_id
    if reported_target_id:
        for slot in range(5):
            opp = opponents[slot]
            if opp is not None and opp.player_id == reported_target_id:
                obs[idx + slot] = 1.0
                break
    idx += 5

    # ── 新增：我是否是举报者 (1 维) [242] ──
    obs[idx] = float(police.reporter_id == player.player_id); idx += 1

    # ── 新增：威信值 (1 维) [243] ──
    obs[idx] = getattr(police, 'authority', 0) / _MAX_AUTHORITY; idx += 1

    assert idx == 244

    # ══════════════════════════════════════════════════════════════════════════
    #  病毒状态 (2)  [244 – 245]
    # ══════════════════════════════════════════════════════════════════════════
    virus = game_state.virus
    obs[idx] = float(virus.is_active);                        idx += 1  # 244
    obs[idx] = virus.countdown / _MAX_VIRUS_CD;               idx += 1  # 245

    assert idx == 246

    # ══════════════════════════════════════════════════════════════════════════
    #  轮次信息 (6)  [246 – 251]
    # ══════════════════════════════════════════════════════════════════════════
    obs[idx] = game_state.current_round / _MAX_ROUND;         idx += 1  # 246
    phase_i = _PHASE_INDEX.get(game_state.current_phase, -1)
    if 0 <= phase_i < 5:
        obs[idx + phase_i] = 1.0
    idx += 5                                                            # 247-251

    assert idx == 252

    # ══════════════════════════════════════════════════════════════════════════
    #  自身标记 (15)  [252 – 266]
    #  LOCKED_BY (5 槽) + ENGAGED_WITH (5 槽) + I_LOCKED (5 槽)
    # ══════════════════════════════════════════════════════════════════════════
    locked_by    = game_state.markers.get_related(player.player_id, "LOCKED_BY")
    engaged_with = game_state.markers.get_related(player.player_id, "ENGAGED_WITH")

    for slot in range(5):
        opp = opponents[slot]
        if opp is not None:
            obs[idx + slot]     = float(opp.player_id in locked_by)      # 谁锁了我
            obs[idx + 5 + slot] = float(opp.player_id in engaged_with)   # 谁和我面对面
    idx += 10

    # ── 新增：我锁了谁 (5 维) [262-266] ──
    for slot in range(5):
        opp = opponents[slot]
        if opp is not None:
            obs[idx + slot] = float(
                game_state.markers.has_relation(opp.player_id, "LOCKED_BY", player.player_id)
            )
    idx += 5
    # ══════════════════════════════════════════════════════════════════════════
    #  高层特征 (20)  [267 – 286]
    # ══════════════════════════════════════════════════════════════════════════
    from models.equipment import WeaponRange

    # [267] has_ranged_weapon: 是否持有远程武器
    has_ranged = any(
        getattr(w, 'weapon_range', None) == WeaponRange.RANGED
        for w in (player.weapons or []) if w
    )
    obs[idx] = float(has_ranged); idx += 1

    # [268] development_phase: 发育完成度 (0-1)
    #   有真实武器(非拳击) +0.25, 有外甲 +0.25, 有内甲 +0.25, 有探测 +0.25
    real_weapons = [w for w in (player.weapons or []) if w and w.name != "拳击"]
    dev_score = 0.0
    if len(real_weapons) > 0:
        dev_score += 0.25
    outer_count = len(player.armor.get_active(ArmorLayer.OUTER)) if hasattr(player, 'armor') and player.armor else 0
    inner_count = len(player.armor.get_active(ArmorLayer.INNER)) if hasattr(player, 'armor') and player.armor else 0
    if outer_count > 0:
        dev_score += 0.25
    if inner_count > 0:
        dev_score += 0.25
    if getattr(player, 'has_detection', False):
        dev_score += 0.25
    obs[idx] = dev_score; idx += 1

    # [269] kill_chain_progress: 击杀链进度 (0/0.33/0.67/1.0)
    #   0=nothing, 0.33=found someone (engaged), 0.67=locked someone, 1.0=locked+has ranged
    chain = 0.0
    i_locked_anyone = any(
        game_state.markers.has_relation(opp.player_id, "LOCKED_BY", player.player_id)
        for opp in opponents if opp is not None and opp.is_alive()
    )
    i_engaged_anyone = any(
        game_state.markers.has_relation(player.player_id, "ENGAGED_WITH", opp.player_id)
        for opp in opponents if opp is not None and opp.is_alive()
    )
    if i_locked_anyone and has_ranged:
        chain = 1.0
    elif i_locked_anyone:
        chain = 0.67
    elif i_engaged_anyone:
        chain = 0.33
    obs[idx] = chain; idx += 1

    # [270] total_armor_count: 自身总护甲数 / 6
    obs[idx] = (outer_count + inner_count) / 6.0; idx += 1

    # [271] weapon_count: 真实武器数 / 5
    obs[idx] = len(real_weapons) / 5.0; idx += 1

    # [272-274] has_effective_weapon_vs_attribute: 对三种属性是否有有效武器
    #   普通→普通/魔法有效, 魔法→魔法/科技有效, 科技→科技/普通有效
    from utils.attribute import Attribute, is_effective
    weapon_attrs = set()
    for w in real_weapons:
        if hasattr(w, 'attribute'):
            weapon_attrs.add(w.attribute)
    obs[idx] = float(any(is_effective(wa, Attribute.ORDINARY) for wa in weapon_attrs)); idx += 1
    obs[idx] = float(any(is_effective(wa, Attribute.MAGIC) for wa in weapon_attrs)); idx += 1
    obs[idx] = float(any(is_effective(wa, Attribute.TECH) for wa in weapon_attrs)); idx += 1

    # [275-279] per-opponent development score (5 slots)
    for slot in range(5):
        opp = opponents[slot]
        if opp is None or not opp.is_alive():
            obs[idx] = 0.0
        else:
            opp_dev = 0.0
            opp_real_w = [w for w in (opp.weapons or []) if w and w.name != "拳击"]
            if len(opp_real_w) > 0:
                opp_dev += 0.25
            opp_outer = len(opp.armor.get_active(ArmorLayer.OUTER)) if hasattr(opp, 'armor') and opp.armor else 0
            opp_inner = len(opp.armor.get_active(ArmorLayer.INNER)) if hasattr(opp, 'armor') and opp.armor else 0
            if opp_outer > 0:
                opp_dev += 0.25
            if opp_inner > 0:
                opp_dev += 0.25
            if getattr(opp, 'has_detection', False):
                opp_dev += 0.25
            obs[idx] = opp_dev
        idx += 1

    # [280] threat_disparity: 最强对手战力 / 自身战力 (capped at 3.0)
    my_power = (player.hp * 10 + len(real_weapons) * 15 + outer_count * 20 + inner_count * 15)
    max_opp_power = 0.0
    for slot in range(5):
        opp = opponents[slot]
        if opp is not None and opp.is_alive():
            opp_rw = [w for w in (opp.weapons or []) if w and w.name != "拳击"]
            opp_o = len(opp.armor.get_active(ArmorLayer.OUTER)) if hasattr(opp, 'armor') and opp.armor else 0
            opp_i = len(opp.armor.get_active(ArmorLayer.INNER)) if hasattr(opp, 'armor') and opp.armor else 0
            opp_p = opp.hp * 10 + len(opp_rw) * 15 + opp_o * 20 + opp_i * 15
            max_opp_power = max(max_opp_power, opp_p)
    if my_power > 0:
        obs[idx] = min(max_opp_power / my_power, 3.0) / 3.0
    else:
        obs[idx] = 1.0
    idx += 1

    # [281] armor_advantage: (my_armor - avg_opp_armor) / 6, clamped to [-1, 1]
    my_armor = outer_count + inner_count
    alive_opps = [opp for opp in opponents if opp is not None and opp.is_alive()]
    if alive_opps:
        avg_opp_armor = sum(
            (len(opp.armor.get_active(ArmorLayer.OUTER)) + len(opp.armor.get_active(ArmorLayer.INNER)))
            if hasattr(opp, 'armor') and opp.armor else 0
            for opp in alive_opps
        ) / len(alive_opps)
    else:
        avg_opp_armor = 0
    obs[idx] = max(-1.0, min(1.0, (my_armor - avg_opp_armor) / 6.0)); idx += 1

    # [282] num_alive_opponents: 存活对手数 / 5
    obs[idx] = len(alive_opps) / 5.0; idx += 1

    # [283] num_enemies_at_location: 同地点敌人数 / 5
    #   使用原始 location 比较，避免把不同玩家的家 (home_p1 vs home_p2) 误判为同一地点
    my_loc = player.location
    enemies_here = sum(
        1 for opp in alive_opps
        if opp.location == my_loc and my_loc is not None
    )
    obs[idx] = enemies_here / 5.0; idx += 1

    # [284] is_being_targeted: 是否有人锁定了我或和我面对面
    is_targeted = len(locked_by) > 0 or len(engaged_with) > 0
    obs[idx] = float(is_targeted); idx += 1

    # [285] rounds_progress: 当前轮次 / 最大轮次 (already exists at idx 246, but this is a convenience duplicate in the high-level section)
    # Actually, let's use something more useful:
    # virus_threat: 是否有病毒且没有面具
    has_mask = "防毒面具" in {item.name for item in (player.items or [])}
    virus_active = game_state.virus.is_active
    obs[idx] = float(virus_active and not has_mask); idx += 1

    # [286] can_buy_at_current_location: 当前地点是否有可购买的东西 (有凭证+在商店/医院/魔法所)
    norm_loc = _normalize_location(player.location)
    can_shop = False
    if norm_loc in ("商店", "医院", "魔法所"):
        if player.vouchers > 0 or norm_loc == "商店":  # 商店打工不需要凭证
            can_shop = True
    obs[idx] = float(can_shop); idx += 1

    # ── [287 – 300] 自身天赋 ID (14-hot) ──
    talent_id_start = 287
    obs[talent_id_start:talent_id_start + _TALENT_ID_DIM] = _build_talent_id(player)

    # ── [301 – 340] 自身天赋状态 (40 维) ──
    self_talent_start = talent_id_start + _TALENT_ID_DIM  # 301
    obs[self_talent_start:self_talent_start + _SELF_TALENT_STATE_DIM] = \
        _build_self_talent_state(player)

    # ── [341 – 510] 对手天赋 ID + 状态 ──
    opp_talent_start = self_talent_start + _SELF_TALENT_STATE_DIM  # 341
    opp_block_size = _TALENT_ID_DIM + _OPP_TALENT_STATE_DIM  # 14 + 20 = 34
    for slot in range(5):
        opp = opponents[slot]
        block_base = opp_talent_start + slot * opp_block_size

        if opp is None:
            # 空槽位：天赋 ID 全 0，天赋状态全 -1
            obs[block_base:block_base + _TALENT_ID_DIM] = 0.0
            obs[block_base + _TALENT_ID_DIM:
                block_base + opp_block_size] = -1.0
        else:
            # 天赋 ID
            obs[block_base:block_base + _TALENT_ID_DIM] = \
                _build_talent_id(opp)
            # 天赋状态
            opp_state = _build_opp_talent_state(opp)
            obs[block_base + _TALENT_ID_DIM:
                block_base + opp_block_size] = opp_state
            # 填充关系维度（依赖 RL 玩家信息的维度）
            _fill_opp_talent_relational(
                opp_state, opp, player, game_state
            )
            # 回写（因为 _fill_opp_talent_relational 修改的是 opp_state）
            obs[block_base + _TALENT_ID_DIM:
                block_base + opp_block_size] = opp_state

    # ── [511] 存活对手数量 (1 维) ──
    alive_opp_start = opp_talent_start + 5 * opp_block_size  # 511
    alive_opponents = 0
    for pid in game_state.player_order:
        if pid != rl_player_id:
            p = game_state.get_player(pid)
            if p and p.is_alive():
                alive_opponents += 1
    obs[alive_opp_start] = alive_opponents / 5.0

    # ── [512 – 514] choose 模式指示 (3 维) ──
    choose_start = alive_opp_start + 1  # 512
    if choose_mode:
        obs[choose_start:choose_start + _CHOOSE_OBS_DIM] = \
            build_choose_obs(choose_situation, choose_n_options)
    else:
        obs[choose_start:choose_start + _CHOOSE_OBS_DIM] = \
            build_normal_mode_choose_obs()

    return obs