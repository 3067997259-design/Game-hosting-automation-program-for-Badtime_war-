"""
rl/rl_controller.py
───────────────────
RLController —— Gym 环境与游戏引擎的唯一接触点。

实现 PlayerController 接口：
  - get_command()  : 将 env 设置的 pending_action_idx 翻译为 CLI 命令
  - choose()       : 子决策用启发式（无天赋局精简版）
  - choose_multi() : 子决策用启发式
  - confirm()      : 子决策用启发式
  - on_event()     : 记录事件日志（供调试）

设计原则：
  RL 智能体只控制 get_command() 层面的"选什么行动"，
  行动内部的子决策（石化二选一、加入警察选奖励等）
  使用与 BasicAIController 相同的启发式规则，
  不暴露给 RL 智能体，以保持动作空间简洁。
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
      5. env 从 controller.last_action_type / last_action_success 读取结果
    """

    def __init__(self):
        # ── env 写入，get_command 读取 ──
        self.pending_action_idx: int = 0

        # ── get_command 写入，env 读取 ──
        self.last_action_type: str = ""
        self.last_action_success: bool = True

        # ── 内部状态 ──
        self._event_log: List[Dict] = []
        self._threat_scores: Dict[str, float] = {}

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
    #  接口实现：choose（子决策启发式，无天赋局精简版）
    # ══════════════════════════════════════════════════════════════════════════

    def choose(
        self,
        prompt: str,
        options: List[str],
        context: Optional[Dict] = None,
    ) -> str:
        """
        从 options 中选择一个。
        无天赋局只需处理以下 situation：
          - petrified          : 石化二选一
          - recruit_pick_1/2   : 加入警察选奖励
          - captain_election   : 竞选队长
        其余 situation 直接返回 options[0]。
        """
        if not options:
            return ""

        situation = (context or {}).get("situation", "")

        # ── 石化：优先解除 ──
        if situation == "petrified":
            for opt in options:
                if "解除" in opt:
                    return opt
            return options[0]

        # ── 加入警察：选奖励（盾牌 > 凭证 > 警棍）──
        if situation in ("recruit_pick_1", "recruit_pick_2"):
            priority = ["盾牌", "凭证", "警棍"]
            for preferred in priority:
                if preferred in options:
                    return preferred
            return options[0]

        # ── 竞选队长：默认不竞选 ──
        if situation == "captain_election":
            for opt in options:
                if "不竞选" in opt or "放弃" in opt:
                    return opt
            return options[0]

        # ── 猜拳（结界等，无天赋局理论上不触发，保险起见）──
        if situation in (
            "hexagram_my_choice", "hexagram_opp_choice", "mythland_rps"
        ):
            return random.choice(options)

        # ── 默认：选第一个 ──
        return options[0]

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
        无天赋局只需处理：
          - 强买通行证：同意（RL 智能体如果选了去军事基地，大概率需要通行证）
        其余默认拒绝。
        """
        if "强买通行证" in prompt:
            return True
        return False

    # ══════════════════════════════════════════════════════════════════════════
    #  接口实现：on_event
    # ══════════════════════════════════════════════════════════════════════════

    def on_event(self, event: Dict) -> None:
        self._event_log.append(event)
        event_type = event.get("type", "")
        attacker = event.get("attacker", "")

        if event_type == "attack" and event.get("target"):
            self._threat_scores[attacker] = (
                self._threat_scores.get(attacker, 0) + 20
            )

        if event_type == "find":
            finder = event.get("player", "")
            if finder:
                self._threat_scores[finder] = (
                    self._threat_scores.get(finder, 0) + 10
                )

        if event_type == "lock":
            locker = event.get("player", "")
            if locker:
                self._threat_scores[locker] = (
                    self._threat_scores.get(locker, 0) + 15
                )

        if event_type == "release_virus":
            releaser = event.get("player", "")
            if releaser:
                self._threat_scores[releaser] = (
                    self._threat_scores.get(releaser, 0) + 20
                )

        if event_type == "death":
            killer = event.get("killer", "")
            if killer:
                self._threat_scores[killer] = (
                    self._threat_scores.get(killer, 0) + 30
                )