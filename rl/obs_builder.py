# rl/obs_builder.py
"""
rl/obs_builder.py
─────────────────
观测向量构建器（无天赋局，共 267 维 float32）

维度布局：
  [  0 –  21] 自身状态          (22)
  [ 22 –  31] 武器持有          (10)
  [ 32 –  38] 护甲持有          (7)
  [ 39 –  43] 物品持有          (5)
  [ 44 – 228] 对手状态 5×37     (185)
  [229 – 243] 警察状态          (15)
  [244 – 245] 病毒状态          (2)
  [246 – 251] 轮次信息          (6)
  [252 – 266] 自身标记          (15)
  [267 – 286] 高层特征          (20)
"""

from __future__ import annotations
from typing import TYPE_CHECKING
import numpy as np

if TYPE_CHECKING:
    from models.player import Player
    from engine.game_state import GameState

from rl.action_space import LOCATIONS, WEAPONS, get_opponent_slots

# ─────────────────────────────────────────────────────────────────────────────
#  常量
# ─────────────────────────────────────────────────────────────────────────────

OBS_DIM = 287

# 每个对手槽位的维度
_OPP_DIM = 37

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
#  核心 API
# ─────────────────────────────────────────────────────────────────────────────

def build_obs(player: "Player", game_state: "GameState") -> np.ndarray:
    """
    构建 267 维 float32 观测向量。

    参数
    ----
    player     : RL 智能体控制的 Player 对象
    game_state : 当前 GameState

    返回
    ----
    np.ndarray, shape=(267,), dtype=float32
    """
    from models.equipment import ArmorLayer
    from utils.attribute import Attribute

    obs = np.zeros(OBS_DIM, dtype=np.float32)
    idx = 0

    # ══════════════════════════════════════════════════════════════════════════
    #  自身状态 (22)  [0 – 21]
    # ══════════════════════════════════════════════════════════════════════════
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

    assert idx == OBS_DIM  # 287
    return obs
