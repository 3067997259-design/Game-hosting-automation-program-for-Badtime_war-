"""EventsMixin —— 事件处理、轮次回调、响应窗口、调试输出"""
from __future__ import annotations
from typing import TYPE_CHECKING, List, Dict, Optional, Any
from controllers.ai.constants import debug_ai_basic

if TYPE_CHECKING:
    pass


class EventsMixin:
    """事件驱动的回调方法集合。

    以下类型注解仅用于消除 Pylance 对 mixin 属性的 unknown-member 报错，
    实际值由 BasicAIController.__init__ 初始化。
    """

    # ---- Pylance 类型提示（运行时不赋值）----
    event_log: List[Dict]
    player_name: Optional[str]
    _my_id: Optional[str]
    _threat_scores: Dict[str, float]
    _been_attacked_by: set
    _players_who_attacked: set
    _game_state: Any
    _round_number: int
    _action_used: bool
    _missile_cooldown: int
    _current_phase: str
    personality: str
    _police_cache: Optional[Dict]
    _in_combat: bool
    _combat_target: Any
    _last_commands: List[str]
    _virus_active: bool
    _virus_location: Optional[str]
    _low_threat_streak: Dict[str, int]

    # ════════════════════════════════════════════════════════
    #  on_event (原 lines 714-771)
    # ════════════════════════════════════════════════════════

    def on_event(self, event: Dict) -> None:
        self.event_log.append(event)
        event_type = event.get("type", "")
        target = event.get("target")
        attacker = event.get("attacker", "")

        # 被攻击
        if event_type == "attack" and self.player_name is not None:
            if target == self.player_name:
                self._been_attacked_by.add(attacker)
                self._threat_scores[attacker] = self._threat_scores.get(attacker, 0) + 20
            self._players_who_attacked.add(attacker)

        # 被找到
        if event_type == "find" and self._my_id is not None:
            finder = event.get("player", "")
            if target == self._my_id:
                finder_name = self._pid_to_name(finder)
                if finder_name:
                    self._threat_scores[finder_name] = self._threat_scores.get(finder_name, 0) + 10

        # 被锁定
        if event_type == "lock" and self._my_id is not None:
            locker = event.get("player", "")
            if target == self._my_id:
                locker_name = self._pid_to_name(locker)
                if locker_name:
                    self._threat_scores[locker_name] = self._threat_scores.get(locker_name, 0) + 15

        # 有人放毒
        if event_type == "release_virus":
            releaser_pid = event.get("player", "")
            releaser_name = self._pid_to_name(releaser_pid)
            if releaser_name and releaser_name != self.player_name:
                self._threat_scores[releaser_name] = self._threat_scores.get(releaser_name, 0) + 20

        # 有人死亡
        if event_type == "death":
            killer = event.get("killer", "")
            if killer:
                self._threat_scores[killer] = self._threat_scores.get(killer, 0) + 30

        # 有人竞选队长
        if event_type == "election":
            candidate_pid = event.get("player", "")
            candidate_name = self._pid_to_name(candidate_pid)
            if candidate_name and candidate_name != self.player_name:
                self._threat_scores[candidate_name] = self._threat_scores.get(candidate_name, 0) + 10

        # 有人当上队长
        if event_type == "captain_elected":
            captain_pid = event.get("captain", "")
            captain_name = self._pid_to_name(captain_pid)
            if captain_name and captain_name != self.player_name:
                self._threat_scores[captain_name] = self._threat_scores.get(captain_name, 0) + 30

    # ════════════════════════════════════════════════════════
    #  事件辅助 (原 lines 774-793)
    # ════════════════════════════════════════════════════════

    def _get_last_attacker(self, player, state) -> Optional[Any]:
        """获取最后一个攻击自己的存活玩家"""
        for event in reversed(self.event_log):
            if event.get("type") == "attack" and event.get("target") == player.name:
                attacker_name = event.get("attacker", "")
                for pid in state.player_order:
                    target = state.get_player(pid)
                    if target and target.is_alive() and target.name == attacker_name:
                        return target
        return None

    def _pid_to_name(self, player_id: str) -> Optional[str]:
        """将 player_id 转换为 player.name"""
        if not self._game_state:
            return None
        p = self._game_state.get_player(player_id)
        return p.name if p else None

    # ════════════════════════════════════════════════════════
    #  轮次回调 (原 lines 3893-3984)
    # ════════════════════════════════════════════════════════

    def on_round_start(self, player, state, round_number: int):
        """轮次开始时调用"""
        self._round_number = round_number
        self._action_used = False
        self._missile_cooldown = max(0, self._missile_cooldown - 1)
        self._update_threat_assessment(player, state)  # from EvaluationMixin
        self._update_caches(player, state)
        debug_ai_basic(player.name,
            f"轮次{round_number}开始，人格={self.personality}，"
            f"阶段={self._current_phase}")

    def on_round_end(self, player, state, round_number: int):
        """轮次结束时调用"""
        self._been_attacked_by.clear()

    def on_damaged(self, player, attacker_name: str, damage: float):
        """被攻击时调用"""
        self._been_attacked_by.add(attacker_name)
        self._threat_scores[attacker_name] = \
            self._threat_scores.get(attacker_name, 0) + damage * 10
        debug_ai_basic(player.name,
            f"被 {attacker_name} 攻击，伤害={damage}")

    def on_player_killed(self, player, killed_name: str, killer_name: str):
        """有玩家被杀时调用"""
        if killed_name in self._threat_scores:
            del self._threat_scores[killed_name]
        if killer_name and killer_name != player.name:
            self._threat_scores[killer_name] = \
                self._threat_scores.get(killer_name, 0) + 30
        debug_ai_basic(player.name,
            f"玩家 {killed_name} 被 {killer_name} 杀死")

    def _update_caches(self, player, state):
        """更新缓存信息"""
        self._read_police_state(state)  # from PoliceMixin
        virus = getattr(state, 'virus', None)
        if virus:
            self._virus_active = getattr(virus, 'is_active', False)
            self._virus_location = self._get_location_str(virus) if hasattr(virus, 'location') else None
        else:
            self._virus_active = False
            self._virus_location = None

    # ════════════════════════════════════════════════════════
    #  响应窗口 (原 lines 3989-4114)
    # ════════════════════════════════════════════════════════

    def respond_to_event(self, player, state, event_type: str,
                         event_data: dict) -> Optional[str]:
        debug_ai_basic(player.name,
            f"响应事件: type={event_type}, data={event_data}")
        if event_type == "被攻击":
            return self._respond_attacked(player, state, event_data)
        elif event_type == "举报":
            return self._respond_report(player, state, event_data)
        elif event_type == "天赋触发":
            return self._respond_talent(player, state, event_data)
        elif event_type == "投票":
            return self._respond_vote(player, state, event_data)
        elif event_type == "警察行动":
            return self._respond_police_action(player, state, event_data)
        elif event_type == "病毒":
            return self._respond_virus(player, state, event_data)
        else:
            debug_ai_basic(player.name, f"未知事件类型: {event_type}")
            return None

    def _respond_attacked(self, player, state, data) -> Optional[str]:
        attacker = data.get("attacker")
        if not attacker:
            return None
        self._been_attacked_by.add(attacker)
        options = data.get("options", [])
        if "dodge" in options and self.personality in ("assassin", "defensive"):
            return "dodge"
        if "block" in options:
            return "block"
        if "counter" in options and self.personality == "aggressive":
            return "counter"
        return options[0] if options else None

    def _respond_report(self, player, state, data) -> Optional[str]:
        reporter = data.get("reporter")
        target = data.get("target")
        options = data.get("options", [])
        if target == player.name:
            if "deny" in options:
                return "deny"
            return options[0] if options else None
        if "support" in options and "oppose" in options:
            target_player = None
            for pid in state.player_order:
                p = state.get_player(pid)
                if p and p.name == target:
                    target_player = p
                    break
            if target_player:
                threat = self._threat_scores.get(target, 0)
                if threat > 30:
                    return "support"
                elif self.personality == "political":
                    return "support"
                else:
                    return "oppose"
        return options[0] if options else None

    def _respond_talent(self, player, state, data) -> Optional[str]:
        options = data.get("options", [])
        if "accept" in options:
            return "accept"
        if "activate" in options:
            return "activate"
        return options[0] if options else None

    def _respond_vote(self, player, state, data) -> Optional[str]:
        options = data.get("options", [])
        candidate = data.get("candidate")
        if candidate:
            threat = self._threat_scores.get(candidate, 0)
            if threat < 20 and "support" in options:
                return "support"
            elif "oppose" in options:
                return "oppose"
        return options[0] if options else None

    def _respond_police_action(self, player, state, data) -> Optional[str]:
        options = data.get("options", [])
        action = data.get("action", "")
        if action == "arrest" and player.name == data.get("target"):
            if "resist" in options and self.personality == "aggressive":
                return "resist"
            if "surrender" in options:
                return "surrender"
        return options[0] if options else None

    def _respond_virus(self, player, state, data) -> Optional[str]:
        options = data.get("options", [])
        if "use_mask" in options:
            return "use_mask"
        if "flee" in options:
            return "flee"
        return options[0] if options else None

    # ════════════════════════════════════════════════════════
    #  调试输出 (原 lines 4423-4445)
    # ════════════════════════════════════════════════════════

    def get_debug_info(self, player) -> dict:
        dev_complete = False
        if player and self._game_state:
            dev_complete = self._is_development_complete(player, self._game_state)  # from DevelopMixin
        return {
            "personality": self.personality,
            "phase": self._current_phase,
            "round": self._round_number,
            "threat_scores": dict(self._threat_scores),
            "in_combat": self._in_combat,
            "combat_target": self._combat_target,
            "been_attacked_by": list(self._been_attacked_by),
            "virus_active": getattr(self, '_virus_active', False),
            "last_commands": self._last_commands[:],
            "police_cache": self._police_cache,
            "development_complete": dev_complete,
        }

    def __repr__(self) -> str:
        return (f"BasicAIController(personality={self.personality}, "
                f"phase={self._current_phase}, round={self._round_number})")