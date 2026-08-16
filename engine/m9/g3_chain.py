"""M9 G3 连续投影机制（profile: m9-rfc，连续投影合同 v0.1）。

- projection_chain：仅固有结界内螺旋剑（伪），关闭列举根内子循环（非普通追演、
  非 ActionGrant）；逐段独立预检+选目标，递增魔力成本；整条连发一次
  root_action_performed、一次系统收尾。
- 累计耗魔计数器：每根行动从 0 累计实际支付的投影魔力，根收尾清零。
- 赤原猎风：累计耗魔 ≥ 阈值后，后续每次命中玩家单位的释放附加 SP−1 + 移出
  公演队列（复用失效纪律永久移除）；即时效果非控制；频率闸按 player_id×结界一次。
- 终段幻想崩坏：连射停止后，理想燃烧已激活且剩余魔力 ≥ 下限可选同根终段；
  清空剩余魔力 + 牺牲三通道 + 统一攻击（defense_coefficient=0.5）→ 解除结界。

数值全读 `m9_talents_extended.g3.*`（[待风洞]，DOC-048 已登记首轮值）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from engine.balance import get as bget


def _g3(key: str, default):
    return bget("m9_talents_extended", "g3", key, default=default)


@dataclass
class ChainConfig:
    max_repeats: int = 2          # 单根行动至多 3 发（含首发）
    cost_step: int = 2            # 递增成本步长
    gale_threshold: int = 6       # 累计耗魔阈值（X）
    terminal_min_magic: int = 2   # 终段最低剩余魔力
    spiral_cost: int = 2          # 螺旋剑基础投影费用


def default_chain_config() -> ChainConfig:
    return ChainConfig(
        max_repeats=int(_g3("chain_max_repeats", 2)),
        cost_step=int(_g3("chain_cost_step", 2)),
        gale_threshold=int(_g3("chain_gale_threshold", 6)),
        terminal_min_magic=int(_g3("collapse_terminal_min_magic", 2)),
        spiral_cost=int(_g3("spiral_cost", 2)),
    )


@dataclass
class ChainSegment:
    """一段连发的完整结算账目。"""
    index: int = 0                 # 1-based（首发=1，追加段=2..）
    magic_paid: int = 0
    target_id: str = ""
    hit: Optional[Any] = None      # resolution.HitResolution
    gale_applied: bool = False     # 本段是否附加赤原猎风


class ProjectionChain:
    """单根行动内的连续投影子循环（关闭列举白名单）。"""

    def __init__(self, config: Optional[ChainConfig] = None,
                 inside_barrier: bool = True, weapon_name: str = "螺旋剑（伪）",
                 is_copy_weapon: bool = False) -> None:
        self.config = config or default_chain_config()
        self.inside_barrier = inside_barrier
        self.weapon_name = weapon_name
        self.is_copy_weapon = is_copy_weapon
        self.cumulative_magic = 0       # 累计耗魔计数器（根收尾清零）
        self._gale_triggered = False
        self._gale_applied_to: set = set()  # 频率闸：player_id × 结界一次
        self.segments: List[ChainSegment] = []
        self.magic_budget: int = 0      # 魔力预算（由接入层注入）

    def can_chain(self) -> bool:
        """白名单：仅结界内、非复制武器、螺旋剑（伪）式样。"""
        if not self.inside_barrier:
            return False
        if self.is_copy_weapon:
            return False
        return self.weapon_name == "螺旋剑（伪）"

    def _segment_cost(self, index: int) -> int:
        """第 n 段追加连发的魔力成本：spiral_cost + (n−1)×cost_step。"""
        return self.config.spiral_cost + (index - 1) * self.config.cost_step

    def next_segment_cost(self) -> Optional[int]:
        """下一追加段的成本；超上限/魔力不足/白名单外返回 None（连射停止）。"""
        if not self.can_chain():
            return None
        next_index = len(self.segments) + 1
        if next_index > self.config.max_repeats + 1:
            return None
        if next_index == 1:
            return self.config.spiral_cost
        cost = self._segment_cost(next_index)
        if cost > self.magic_budget - self.cumulative_magic:
            return None
        return cost

    def precheck(self, target_id: str) -> bool:
        """逐段合法性预检：预检失败不支付该段成本、连射立即结束。"""
        if not self.can_chain():
            return False
        cost = self.next_segment_cost()
        if cost is None:
            return False
        if not target_id:
            return False
        return True

    def pay(self, target_id: str) -> Optional[ChainSegment]:
        """支付本段成本并记段（实际命中结算由接入层调 resolve_attack 后填 hit）。"""
        if not self.precheck(target_id):
            return None
        cost = self.next_segment_cost()
        index = len(self.segments) + 1
        self.cumulative_magic += cost
        segment = ChainSegment(index=index, magic_paid=cost, target_id=target_id)
        self.segments.append(segment)
        if self.cumulative_magic >= self.config.gale_threshold:
            self._gale_triggered = True
        return segment

    def should_apply_gale(self, player_id: str) -> bool:
        """赤原猎风：自触发起本根后续每次命中玩家单位的释放附加；
        频率闸——同一 player_id 每次结界至多一次。"""
        if not self._gale_triggered:
            return False
        if player_id in self._gale_applied_to:
            return False
        self._gale_applied_to.add(player_id)
        return True

    def gale_sp_cost(self) -> int:
        """赤原猎风 SP −1（下限 0 由 SP 层保证）。"""
        return 1

    def can_terminal_collapse(self, ideal_burn_active: bool) -> bool:
        """终段幻想崩坏：理想燃烧已激活 + 剩余魔力 ≥ 下限。"""
        if not ideal_burn_active:
            return False
        return self.cumulative_magic < self.magic_budget and (
            self.magic_budget - self.cumulative_magic
            >= self.config.terminal_min_magic)

    def terminal_collapse(self) -> int:
        """结算终段：清空全部剩余魔力，返回牺牲的魔力值（含三通道牺牲）。"""
        remaining = max(0, self.magic_budget - self.cumulative_magic)
        self.magic_budget = 0
        return remaining

    def finish_root(self) -> None:
        """根行动收尾：计数器清零（不跨根行动保留）。"""
        self.cumulative_magic = 0
        self._gale_triggered = False
        self._gale_applied_to.clear()
