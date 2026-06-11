"""AI 评估纯函数模块 —— 从旧 EvaluationMixin / HelpersMixin 抽取的无状态查询。

所有函数均为纯函数（无 self 状态、无副作用），接受 player/game_state 等显式参数。
旧 Mixin 方法保留为单行委托（→ C7 处决时移除），新架构模块直接导入本模块函数。
"""
from __future__ import annotations
from typing import Any, List, Optional


# ════════════════════════════════════════════════════════
#  天赋身份查询（原 helpers_mixin）
# ════════════════════════════════════════════════════════

def has_firefly_talent(player: Any) -> bool:
    """检查玩家是否持有火萤IV型天赋。"""
    talent = getattr(player, 'talent', None)
    if talent and hasattr(talent, 'name') and talent.name == "火萤IV型-完全燃烧":
        return True
    return False


def is_in_savior_state(player: Any) -> bool:
    """检查玩家是否处于救世主状态（愿负世附加模式）。"""
    talent = getattr(player, 'talent', None)
    if talent and hasattr(talent, 'is_savior'):
        return talent.is_savior
    return False


def get_divinity(player: Any) -> int:
    """获取愿负世持有者的当前火种数。无天赋返回 0。"""
    talent = getattr(player, 'talent', None)
    return getattr(talent, 'divinity', 0) if talent else 0


# ════════════════════════════════════════════════════════
#  战力 / HP 估算（原 evaluation_mixin）
# ════════════════════════════════════════════════════════

def get_effective_hp(player: Any) -> float:
    """计算玩家有效生命值（含天赋额外HP）。

    - 愿负世：救世主状态的临时HP（temp_hp）
    - 火萤IV型：炽愿层数 × 0.5
    """
    hp = float(getattr(player, 'hp', 0.0))
    talent = getattr(player, 'talent', None)
    if talent:
        temp_hp = getattr(talent, 'temp_hp', 0.0)
        if temp_hp > 0:
            hp += temp_hp
        charges = getattr(talent, 'ardent_wish_charges', 0)
        if charges > 0:
            hp += charges * 0.5
    return hp


# ════════════════════════════════════════════════════════
#  地图/武器推断（原 controller.py / helpers_mixin）
# ════════════════════════════════════════════════════════

def infer_aoe_weapon(destination: str) -> Optional[str]:
    """根据目的地推断 AI 要去拿什么 AOE 武器。"""
    if destination == "军事基地":
        return "电磁步枪"
    if destination == "魔法所":
        return "地动山摇"
    return None


# ════════════════════════════════════════════════════════
#  位置查询（原 helpers_mixin）
# ════════════════════════════════════════════════════════

def get_location_str(player: Any) -> str:
    """获取玩家位置的归一化字符串。"""
    loc = getattr(player, 'location', None)
    if loc is None:
        return "unknown"
    if isinstance(loc, str):
        return loc
    if hasattr(loc, 'name'):
        return loc.name
    return "unknown"


def same_location(player1: Any, player2: Any) -> bool:
    """两个玩家是否在同一地点。"""
    loc1 = get_location_str(player1)
    loc2 = get_location_str(player2)
    return loc1 == loc2 and loc1 != "unknown"


def get_same_location_targets(player: Any, state: Any) -> List[Any]:
    """获取与玩家同地点的所有存活敌人。"""
    result: List[Any] = []
    for pid in state.player_order:
        if pid == player.player_id:
            continue
        target = state.get_player(pid)
        if target and target.is_alive() and same_location(player, target):
            result.append(target)
    return result
