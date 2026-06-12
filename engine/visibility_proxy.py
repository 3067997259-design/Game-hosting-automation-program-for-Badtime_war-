"""行动隐匿的观察者视图代理（M3，experiment: m3_accuracy，v2.0 §1.2）。

复用 engine/filtered_state.py（G7 闪光弹致盲系统）的**代理模式**而非实例：
- 致盲系统 = 快照冻结（FrozenPlayer，死数据，全字段定格）
- 本代理 = 活数据 + 行踪裁剪（RedactedPlayer 只藏 location/行动，
  HP/装备/天赋等明牌字段实时透传——v2.0 §8 防御轮廓本就明牌）

引擎与 validator 永远使用真实 GameState（规则必须见真相）；
代理只发给 controller.get_command（AI 反开图）。
"""
from __future__ import annotations
from typing import Any, List

from engine.visibility import can_see


class RedactedPlayer:
    """行踪被隐匿的玩家视图：位置不可知，其余明牌字段透传实时数据。"""

    _HIDDEN = {"location", "moved_this_round"}

    def __init__(self, real_player: Any):
        object.__setattr__(self, "_real", real_player)

    @property
    def location(self):
        return None  # 行踪隐匿

    @property
    def moved_this_round(self):
        return False

    def is_on_map(self):
        return False  # 对观察者而言不在地图上（找不到、追不了）

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_real"), name)

    def __repr__(self):
        real = object.__getattribute__(self, "_real")
        return f"<Redacted {getattr(real, 'name', '?')}>"


class VisibilityProxy:
    """per-observer 的 GameState 活代理（仿 FilteredGameState 骨架）。

    get_player() 对被隐匿目标返回 RedactedPlayer；players_at_location/
    alive_players 同步裁剪；其余属性 __getattr__ 透传真实 state。
    """

    def __init__(self, real_state: Any, observer_pid: str):
        object.__setattr__(self, "_real", real_state)
        object.__setattr__(self, "_observer_pid", observer_pid)

    def _observer(self):
        return self._real.get_player(self._observer_pid)

    def _is_concealed(self, target: Any) -> bool:
        if getattr(target, "player_id", None) == self._observer_pid:
            return False
        observer = self._observer()
        if observer is None:
            return False
        return not can_see(observer, target, self._real)

    def get_player(self, player_id):
        p = self._real.get_player(player_id)
        if p is not None and self._is_concealed(p):
            return RedactedPlayer(p)
        return p

    def players_at_location(self, location):
        result = []
        for pid in self._real.player_order:
            p = self.get_player(pid)
            if (p and p.is_alive()
                    and getattr(p, "location", None) == location):
                result.append(p)
        return result

    def alive_players(self):
        return [p for pid in self._real.player_order
                if (p := self.get_player(pid)) and p.is_alive()]

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_real"), name)
