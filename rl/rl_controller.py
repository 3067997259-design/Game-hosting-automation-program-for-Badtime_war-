"""
rl/rl_controller.py
───────────────────
RLController —— Gym 环境与游戏引擎的唯一接触点。

实现 PlayerController 接口：
  - get_command()  : 将 env 设置的 pending_action_idx 翻译为 CLI 命令
  - choose()       : 战略决策走 RL 同步，非战略走启发式
  - choose_multi() : 按威胁分排序（启发式）
  - confirm()      : 响应窗口/强买通行证走 RL 同步，其余启发式
  - on_event()     : 记录事件日志 + 威胁分

设计原则（天赋局版）：
  绝大多数 choose/confirm 决策交给 RL 智能体控制。
  仅以下情况使用启发式：
    - mythland_rps          : 纯随机猜拳，无博弈空间
    - hoshino_reorder_ammo  : 排弹顺序，开支大收益小
    - G7 宏内参数选择       : 由自回归序列在 get_command 层面处理

  _SyncRLController（定义在 env.py）继承本类，
  覆写 _rl_choose() 和 _rl_confirm() 实现与 env 的线程同步。
"""

from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional, Dict, Any
import random

from controllers.base import PlayerController
from rl.action_space import idx_to_command

if TYPE_CHECKING:
    from models.player import Player
    from engine.game_state import GameState


class RLController(PlayerController):
    """
    RL 智能体控制器。

    使用流程（在 BadtimeWarEnv.step 中）：
      1. env 设置 controller.pending_action_idx = action
      2. 游戏引擎调用 controller.get_command(...)
      3. get_command 将 pending_action_idx 翻译为 CLI 字符串返回
      4. 引擎执行该命令，期间可能调用 choose/choose_multi/confirm
      5. 战略 choose/confirm 通过 _rl_choose/_rl_confirm 走 RL 同步
      6. 非战略 choose/confirm 使用启发式直接返回
    """

    # ══════════════════════════════════════════════════════════════════════════
    #  启发式 situation 白名单（仅这些走启发式，其余全部交给 RL）
    # ══════════════════════════════════════════════════════════════════════════

    _HEURISTIC_CHOOSE: set[str] = {
        # 纯随机，无博弈空间
        "mythland_rps",

        # 排弹顺序：开支大收益小，让 damage_resolver 处理
        "hoshino_reorder_ammo",

        # G7 宏内参数选择：由自回归序列在 get_command 层面处理
        # 这些 situation 在战术宏 while 循环内部触发，
        # RL 的决策点是 get_command(situation="hoshino_tactical_input")，
        # 不是这些子参数 choose
        "hoshino_throw_item",
        "hoshino_throw_location",
        "hoshino_medicine",
        "hoshino_dash_target",
        "hoshino_shoot_target",
        "hoshino_find_target",
    }

    def __init__(self):
        # ── env 写入，get_command 读取 ──
        self.pending_action_idx: int = 0

        # ── get_command 写入，env 读取 ──
        self.last_action_type: str = ""
        self.last_action_success: bool = True

        # ── 内部状态 ──
        self._event_log: List[Dict] = []
        self._threat_scores: Dict[str, float] = {}
        self._round_number: int = 0
        self._been_attacked_by: set = set()
        self._player_ref = None
        self._state_ref = None

    # ══════════════════════════════════════════════════════════════════════════
    #  RL 同步钩子（由 _SyncRLController 覆写）
    # ══════════════════════════════════════════════════════════════════════════

    def _rl_choose(
        self,
        prompt: str,
        options: List[str],
        context: Optional[Dict] = None,
    ) -> str:
        """
        战略 choose 决策的 RL 同步入口。

        默认实现返回 options[0]（用于测试/离线场景）。
        _SyncRLController 覆写此方法，通过 threading.Event
        与 env.step() 同步，让 RL 智能体做出选择。
        """
        return options[0] if options else ""

    def _rl_confirm(
        self,
        prompt: str,
        context: Optional[Dict] = None,
    ) -> bool:
        """
        战略 confirm 决策的 RL 同步入口。

        默认实现返回 False。
        _SyncRLController 覆写此方法，内部映射为
        2 选项的 _rl_choose(["是", "否"])。
        """
        return False

    # ══════════════════════════════════════════════════════════════════════════
    #  接口实现：get_command
    # ══════════════════════════════════════════════════════════════════════════

    def get_command(
        self,
        player: Any,
        game_state: Any,
        available_actions: List[str],
        context: Optional[Dict] = None,
    ) -> str:
        """
        将 pending_action_idx 翻译为 CLI 命令字符串。

        env 在调用 game 引擎之前已设置好 self.pending_action_idx，
        此方法仅做索引 → 字符串的转换。
        """
        return idx_to_command(self.pending_action_idx, player, game_state)

    # ══════════════════════════════════════════════════════════════════════════
    #  接口实现：choose
    # ══════════════════════════════════════════════════════════════════════════

    def choose(
        self,
        prompt: str,
        options: List[str],
        context: Optional[Dict] = None,
    ) -> str:
        """
        从 options 中选择一个。

        路由逻辑：
          1. situation 在 _HEURISTIC_CHOOSE 中 → 启发式
          2. 其余所有 situation → RL 同步（_rl_choose）
        """
        if not options:
            return ""

        situation = (context or {}).get("situation", "")

        # ── 启发式路径 ──
        if situation in self._HEURISTIC_CHOOSE:
            return self._heuristic_choose(situation, prompt, options, context)

        # ── RL 路径（绝大多数 situation 走这里）──
        return self._rl_choose(prompt, options, context)

    # ══════════════════════════════════════════════════════════════════════════
    #  接口实现：choose_multi
    # ══════════════════════════════════════════════════════════════════════════

    def choose_multi(
        self,
        prompt: str,
        options: List[str],
        max_count: int,
        min_count: int = 0,
        context: Optional[Dict] = None,
    ) -> List[str]:
        """
        从 options 中选多个（如地动山摇选震荡目标）。
        按威胁分排序，取 min(max_count, len) 个。

        TODO: 后续可考虑让 RL 控制多选决策，
              当前用启发式（威胁分排序）。
        """
        if not options:
            return []
        sorted_opts = sorted(
            options,
            key=lambda name: self._threat_scores.get(name, 0),
            reverse=True,
        )
        count = max(min_count, min(max_count, len(sorted_opts)))
        return sorted_opts[:count]

    # ══════════════════════════════════════════════════════════════════════════
    #  接口实现：confirm
    # ══════════════════════════════════════════════════════════════════════════

    def confirm(
        self,
        prompt: str,
        context: Optional[Dict] = None,
    ) -> bool:
        """
        是/否判断。

        路由逻辑：
          1. response_window（天赋响应窗口）→ RL 同步
          2. 强买通行证 → RL 同步
          3. 其余 → 默认拒绝
        """
        phase = (context or {}).get("phase", "")

        # ── 响应窗口：RL 决定是否发动天赋 ──
        if phase == "response_window":
            return self._rl_confirm(prompt, context)

        # ── 强买通行证：RL 决定是否消耗凭证 ──
        if "强买通行证" in prompt or "强买" in prompt:
            return self._rl_confirm(prompt, context)

        # ── 默认：拒绝 ──
        return False

    # ══════════════════════════════════════════════════════════════════════════
    #  接口实现：on_event
    # ══════════════════════════════════════════════════════════════════════════

    def on_event(self, event: Dict) -> None:
        """记录事件日志，更新威胁分。"""
        self._event_log.append(event)
        event_type = event.get("type", "")
        attacker = event.get("attacker", "")
        target = event.get("target", "")

        # ── 攻击事件：攻击者威胁 +20 ──
        if event_type == "attack" and attacker:
            self._threat_scores[attacker] = (
                self._threat_scores.get(attacker, 0) + 20
            )

        # ── 找到事件：finder 威胁 +10 ──
        if event_type == "find":
            finder = event.get("player", "")
            if finder:
                self._threat_scores[finder] = (
                    self._threat_scores.get(finder, 0) + 10
                )

        # ── 锁定事件：locker 威胁 +15 ──
        if event_type == "lock":
            locker = event.get("player", "")
            if locker:
                self._threat_scores[locker] = (
                    self._threat_scores.get(locker, 0) + 15
                )

        # ── 释放病毒：releaser 威胁 +20 ──
        if event_type == "release_virus":
            releaser = event.get("player", "")
            if releaser:
                self._threat_scores[releaser] = (
                    self._threat_scores.get(releaser, 0) + 20
                )

        # ── 死亡事件：killer 威胁 +30，死者从威胁表移除 ──
        if event_type == "death":
            killer = event.get("killer", "")
            if killer:
                self._threat_scores[killer] = (
                    self._threat_scores.get(killer, 0) + 30
                )
            dead_name = event.get("dead", "") or target
            if dead_name and dead_name in self._threat_scores:
                del self._threat_scores[dead_name]

        # ── 竞选事件：candidate 威胁 +10 ──
        if event_type == "election":
            candidate = event.get("player", "")
            if candidate:
                self._threat_scores[candidate] = (
                    self._threat_scores.get(candidate, 0) + 10
                )

        # ── 当选队长：captain 威胁 +30 ──
        if event_type == "captain_elected":
            captain = event.get("captain", "")
            if captain:
                self._threat_scores[captain] = (
                    self._threat_scores.get(captain, 0) + 30
                )

        # ── 天赋相关事件：记录但不额外加分 ──
        # （天赋事件的威胁评估由 obs 中的天赋状态编码承担，
        #   不需要在 threat_scores 中重复体现）

    # ══════════════════════════════════════════════════════════════════════════
    #  生命周期回调
    # ══════════════════════════════════════════════════════════════════════════

    def on_round_start(self, player, state, round_number: int):
        """轮次开始时调用。更新内部缓存。"""
        self._round_number = round_number
        self._been_attacked_by.clear()
        # 威胁分衰减：每轮所有威胁分 ×0.95，防止远古事件永久影响决策
        for name in list(self._threat_scores):
            self._threat_scores[name] *= 0.95
            if self._threat_scores[name] < 1.0:
                del self._threat_scores[name]

    def on_round_end(self, player, state, round_number: int):
        """轮次结束时调用。"""
        pass  # 当前无需处理

    def on_damaged(self, player, attacker_name: str, damage: float):
        """被攻击时调用。"""
        self._been_attacked_by.add(attacker_name)
        self._threat_scores[attacker_name] = (
            self._threat_scores.get(attacker_name, 0) + damage * 10
        )

    def on_player_killed(self, player, killed_name: str, killer_name: str):
        """有玩家被杀时调用。"""
        if killed_name in self._threat_scores:
            del self._threat_scores[killed_name]
        if killer_name and killer_name != getattr(player, 'name', ''):
            self._threat_scores[killer_name] = (
                self._threat_scores.get(killer_name, 0) + 30
            )

    # ══════════════════════════════════════════════════════════════════════════
    #  内部方法：启发式 choose 调度
    # ══════════════════════════════════════════════════════════════════════════

    def _heuristic_choose(
        self,
        situation: str,
        prompt: str,
        options: List[str],
        context: Optional[Dict] = None,
    ) -> str:
        """启发式 choose 调度器，根据 situation 路由到具体子函数。"""
        if situation == "mythland_rps":
            return self._heuristic_mythland_rps(options)
        if situation == "hoshino_reorder_ammo":
            return self._heuristic_reorder_ammo(options)
        # G7 宏内参数选择
        return self._heuristic_tactical_macro(situation, options)

    def _heuristic_mythland_rps(self, options: List[str]) -> str:
        """结界猜拳：纯随机，无博弈空间。"""
        return random.choice(options)

    def _heuristic_reorder_ammo(self, options: List[str]) -> str:
        """
        排弹顺序：按属性克制排列。
        开销不小收益不大，大部分情况下子弹打出去让 damage_resolver 算。
        保持原序即可。
        """
        # 返回原序（不重排）
        return " ".join(str(i + 1) for i in range(len(options)))

    def _heuristic_tactical_macro(
        self,
        situation: str,
        options: List[str],
    ) -> str:
        """
        G7 战术宏内部的子 choose（目标选择、投掷物选择等）。
        这些在自回归序列中由 get_command 的战术动作索引直接包含目标信息，
        此处作为兜底：按威胁分选目标，按优先级选物品。
        """
        # ── 目标选择类 ──
        if situation in (
            "hoshino_dash_target",
            "hoshino_shoot_target",
            "hoshino_find_target",
        ):
            if not options:
                return ""
            return max(
                options,
                key=lambda name: self._threat_scores.get(name, 0),
            )

        # ── 投掷物选择 ──
        if situation == "hoshino_throw_item":
            priority = ["闪光弹", "烟雾弹", "破片手雷", "震撼弹", "燃烧瓶"]
            for item in priority:
                if item in options:
                    return item
            return options[0] if options else ""

        # ── 投掷地点选择 ──
        if situation == "hoshino_throw_location":
            # 默认选第一个（战术宏已经决定了目标，地点跟随目标）
            return options[0] if options else ""

        # ── 服药选择 ──
        if situation == "hoshino_medicine":
            for opt in options:
                if "EPO" in opt:
                    return opt
            for opt in options:
                if "巧克力" in opt:
                    return opt
            return options[0] if options else ""

        # ── 兜底 ──
        return options[0] if options else ""

    # ══════════════════════════════════════════════════════════════════════════
    #  内部方法：缓存更新
    # ══════════════════════════════════════════════════════════════════════════

    def _cache_player_ref(self, player, game_state):
        """
        缓存 player 和 game_state 引用，供 _rl_choose / _rl_confirm 使用。
        在 get_command() 被调用时自动更新。
        """
        self._player_ref = player
        self._state_ref = game_state

    # ══════════════════════════════════════════════════════════════════════════
    #  调试接口
    # ══════════════════════════════════════════════════════════════════════════

    def get_debug_info(self) -> Dict:
        """返回调试信息。"""
        return {
            "round": self._round_number,
            "threat_scores": dict(self._threat_scores),
            "been_attacked_by": list(self._been_attacked_by),
            "event_log_size": len(self._event_log),
        }

    def __repr__(self) -> str:
        return (
            f"RLController(round={self._round_number}, "
            f"threats={len(self._threat_scores)})"
        )