"""  
rl/obs_builder.py  
─────────────────  
观测向量构建器（无天赋局，共 123 维 float32）  
  
维度布局：  
  [  0 – 18 ] 自身状态          (19)  
  [ 19 – 28 ] 武器持有          (10)  
  [ 29 – 35 ] 护甲持有          (7)  
  [ 36 – 40 ] 物品持有          (5)  
  [ 41 – 100] 对手状态 5×12     (60)  
  [101 – 104] 警察状态          (4)  
  [105 – 106] 病毒状态          (2)  
  [107 – 112] 轮次信息          (6)  
  [113 – 122] 自身标记          (10)  
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
  
OBS_DIM = 123  
  
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
_MAX_HP       = 5.0  
_MAX_VOUCHERS = 3.0  
_MAX_KILL     = 5.0  
_MAX_STREAK   = 6.0  
_MAX_ROUND    = 20.0  
_MAX_CRIMES   = 3.0  
_MAX_POLICE   = 3.0  
_MAX_VIRUS_CD = 3.0  
  
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
  
def _normalize_location(loc: str | None) -> str:  
    if loc and loc.startswith("home_"):  
        return "home"  
    return loc or "" 
# ─────────────────────────────────────────────────────────────────────────────  
#  核心 API  
# ─────────────────────────────────────────────────────────────────────────────  
  
def build_obs(player: "Player", game_state: "GameState") -> np.ndarray:  
    """  
    构建 123 维 float32 观测向量。  
  
    参数  
    ----  
    player     : RL 智能体控制的 Player 对象  
    game_state : 当前 GameState  
  
    返回  
    ----  
    np.ndarray, shape=(123,), dtype=float32  
    """  
    obs = np.zeros(OBS_DIM, dtype=np.float32)  
    idx = 0  
  
    # ══════════════════════════════════════════════════════════════════════════  
    #  自身状态 (19)  [0 – 18]  
    # ══════════════════════════════════════════════════════════════════════════  
    obs[idx] = player.hp / _MAX_HP;                          idx += 1  # 0  
    obs[idx] = player.max_hp / _MAX_HP;                      idx += 1  # 1  
    obs[idx] = player.vouchers / _MAX_VOUCHERS;              idx += 1  # 2  
  
    # location one-hot (6 维)  
    loc_i = _LOC_INDEX.get(player.location, -1) if player.location else -1
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
  
    assert idx == 19  
  
    # ══════════════════════════════════════════════════════════════════════════  
    #  武器持有 (10)  [19 – 28]  
    # ══════════════════════════════════════════════════════════════════════════  
    owned_weapons = {w.name for w in (player.weapons or []) if w}  
    for wi, wname in enumerate(WEAPON_NAMES):  
        obs[idx + wi] = float(wname in owned_weapons)  
    idx += len(WEAPON_NAMES)  
  
    assert idx == 29  
  
    # ══════════════════════════════════════════════════════════════════════════  
    #  护甲持有 (7)  [29 – 35]  
    # ══════════════════════════════════════════════════════════════════════════  
    active_armor_names: set[str] = set()  
    if hasattr(player, "armor") and player.armor:  
        for piece in player.armor.get_all_active():  
            active_armor_names.add(piece.name)  
    for ai, aname in enumerate(ARMOR_NAMES):  
        obs[idx + ai] = float(aname in active_armor_names)  
    idx += len(ARMOR_NAMES)  
  
    assert idx == 36  
  
    # ══════════════════════════════════════════════════════════════════════════  
    #  物品持有 (5)  [36 – 40]  
    # ══════════════════════════════════════════════════════════════════════════  
    owned_items = {item.name for item in (player.items or [])}  
    for ii, category in enumerate(ITEM_CATEGORIES):  
        obs[idx + ii] = float(bool(owned_items & category))  
    idx += len(ITEM_CATEGORIES)  
  
    assert idx == 41  
  
    # ══════════════════════════════════════════════════════════════════════════  
    #  对手状态 (5 × 12 = 60)  [41 – 100]  
    #  所有信息完全透明（隐身仅影响 lock/find 的 action mask，不影响观测）  
    # ══════════════════════════════════════════════════════════════════════════  
    opponents = get_opponent_slots(player, game_state)  
    for slot in range(5):  
        opp = opponents[slot]  
        base = idx + slot * 12  
  
        if opp is None or not opp.is_alive():  
            # 死亡 / 不存在 → 全零（is_alive=0 已足够标识）  
            pass  
        else:  
            obs[base + 0] = 1.0                              # is_alive  
            obs[base + 1] = float(opp.is_awake)              # is_awake  
            obs[base + 2] = opp.hp / _MAX_HP                 # hp  
            # location one-hot (6)  
            opp_loc_i = _LOC_INDEX.get(opp.location, -1)  
            if opp_loc_i >= 0:  
                obs[base + 3 + opp_loc_i] = 1.0  
            obs[base + 9]  = opp.kill_count / _MAX_KILL      # kill_count  
            obs[base + 10] = float(opp.is_captain)           # is_captain  
            obs[base + 11] = float(opp.is_criminal)          # is_criminal  
  
    idx += 60  
  
    assert idx == 101  
  
    # ══════════════════════════════════════════════════════════════════════════  
    #  警察状态 (4)  [101 – 104]  
    # ══════════════════════════════════════════════════════════════════════════  
    police = game_state.police  
    my_crimes = police.crime_records.get(player.player_id, set())  
    obs[idx] = len(my_crimes) / _MAX_CRIMES;                 idx += 1  # 101  
    obs[idx] = float(police.has_captain());                   idx += 1  # 102  
    obs[idx] = float(police.captain_id == player.player_id);  idx += 1  # 103  
    obs[idx] = len(police.alive_units()) / _MAX_POLICE;       idx += 1  # 104  
  
    assert idx == 105  
  
    # ══════════════════════════════════════════════════════════════════════════  
    #  病毒状态 (2)  [105 – 106]  
    # ══════════════════════════════════════════════════════════════════════════  
    virus = game_state.virus  
    obs[idx] = float(virus.is_active);                        idx += 1  # 105  
    obs[idx] = virus.countdown / _MAX_VIRUS_CD;               idx += 1  # 106  
  
    assert idx == 107  
  
    # ══════════════════════════════════════════════════════════════════════════  
    #  轮次信息 (6)  [107 – 112]  
    # ══════════════════════════════════════════════════════════════════════════  
    obs[idx] = game_state.current_round / _MAX_ROUND;         idx += 1  # 107  
    phase_i = _PHASE_INDEX.get(game_state.current_phase, -1)  
    if 0 <= phase_i < 5:  
        obs[idx + phase_i] = 1.0  
    idx += 5                                                            # 108-112  
  
    assert idx == 113  
  
    # ══════════════════════════════════════════════════════════════════════════  
    #  自身标记 (10)  [113 – 122]  
    #  LOCKED_BY (5 槽) + ENGAGED_WITH (5 槽)  
    # ══════════════════════════════════════════════════════════════════════════  
    locked_by    = game_state.markers.get_related(player.player_id, "LOCKED_BY")  
    engaged_with = game_state.markers.get_related(player.player_id, "ENGAGED_WITH")  
  
    for slot in range(5):  
        opp = opponents[slot]  
        if opp is not None:  
            obs[idx + slot]     = float(opp.player_id in locked_by)  
            obs[idx + 5 + slot] = float(opp.player_id in engaged_with)  
    idx += 10  
  
    assert idx == OBS_DIM  # 123  
  
    return obs