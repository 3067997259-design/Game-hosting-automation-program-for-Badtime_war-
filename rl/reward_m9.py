"""
rl/reward_m9.py
───────────────
M9-rfc 奖励层：在 legacy 四层结构上叠加 M9 决策信号。

- 势函数：legacy 基底 + SP / 当前授予 / 公演队列与公演位 / PP / 剧情分 / 队长
- 事件层：legacy 通用战斗事件 + M9 专属事件表（演出/天赋核心成功/负面）
- 终局与行为惩罚：复用 legacy（胜 +100 / 死 −100 / 全灭 −75；forfeit/失败递增等）

m9 事件类型以 engine/m9/**/*.py 的 log_event("type", ...) 为准；全部用默认值兜底，
事件/字段改动不会崩，只会少给/不给该信号。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

import rl.reward as _L  # legacy 基座（potential/event_reward/behavior_penalty/RewardTracker）

# ── M9 专属事件 → 奖励（按 player==pid 匹配；部分用 attacker/owner）──
M9_EVENT_REWARD: Dict[str, float] = {
    # 公共/演出/公演
    # 身份本身不给分：曾导致策略只竞选、随后反复移动 unit1。
    # 警察线只奖励可归因的主动指挥结果。
    "m9_captain": 0.0,
    "g4_human_public_performed": 8.0,  # G4 公演演出
    "g7_public_supply": 5.0,           # G7 公共补给
    "special_clue": 6.0,               # T6 特别线索
    "hotline": 4.0,                    # T6 市民热线
    "poem_stars": 5.0,                 # 诗篇
    # 天赋核心成功（高价值）
    "t1_chase": 4.0,
    "g4_savior_enter": 12.0,           # G4 救世主
    "g4_judgment_completed": 15.0,
    "m9_g3_expand": 8.0,               # G3 结界扩张（引擎正名：m9_g3_*）
    "m9_g3_ideal_burn": 10.0,          # G3 理想燃尽（引擎正名）
    "m9_g3_dismiss": -5.0,
    "m9_g3_release": -4.0,
    "TERMINAL_SONG_ENDED": -6.0,
    "MOVE_REDIRECTED": -3.0,
    "location_destroyed": 6.0,
    "m9_police_enforcement": 0.0,      # R4 自动执法，不归因给策略
    "m9_police_command_attack": 8.0,   # 队长主动下令且实际完成攻击
    "world_poem_shock": 6.0,
    "g6_borrow_core": 8.0,             # G6 借用核心
    "g0_drone_summon": 5.0,            # G0 无人机
    "g0_crossfire": 8.0,
    "SHADOW_CREATED": 6.0,             # G2 影身
    "TERMINAL_SONG_COMMITTED": 12.0,   # G2 终曲承诺
    "crystal_flower": 15.0,            # G5 水晶花
    "MATERIALIZED_BY_ANCHOR": 10.0,    # G5 锚定实现
    "anchor_script_committed": 6.0,
    # 负面
    "g4_savior_exit": -8.0,
    "SHADOW_DISSIPATED": -4.0,
    "m9_g3_collapse": -6.0,
}

# 这些 M9 仍会产生的事件已由 legacy event_reward 完整计分；不得在上表重复，
# 否则同一事件会在一条 transition 中获得双份奖励。
LEGACY_M9_EVENT_TYPES = frozenset({
    "oneslash_attack", "star_attack", "hexagram_cast",
    "resurrection_mount", "resurrection_trigger",
})

M9_EVENT_OWNER_FIELDS: Dict[str, tuple[str, ...]] = {
    # 自动执法保留 0 分审计键；主动指挥才把信用给队长。
    "m9_police_enforcement": ("captain",),
    "m9_police_command_attack": ("captain",),
}

# legacy 势能量级约 100–300；缩放 PBRS 后仍保留方向与理论不变性，同时让
# 单步 shaping 不再淹没 ±100 的正式终局目标。
M9_POTENTIAL_SCALE = 0.1

# ── 主动胜利条款（DOC-054 / 用户裁决 2026-08-23）──
# league 训练实证：避战拖分流可拿名次-only 8 分击败弱互杀对手（v22 的
# 80% 得分来自名次加成）。训练奖励要求"常规胜利 + 击杀数或剧情分最高"
# 才给满分；空胜利给打折分（保留生存价值但不主导）。只影响 RL 训练
# 奖励，不改引擎胜负判定与 official 口径。
ACTIVE_VICTORY_REWARD = 100.0
PASSIVE_VICTORY_REWARD = 30.0


def active_victory(game_state: Any, player_id: str) -> bool:
    """主动胜利判定：击杀数最高 或 剧情分最高（演出路线 proxy）。"""
    players = [
        game_state.get_player(pid)
        for pid in (getattr(game_state, "player_order", None) or [])
    ]
    players = [p for p in players if p is not None]
    if not players:
        return False
    me = game_state.get_player(player_id)
    if me is None:
        return False
    my_kills = int(getattr(me, "kill_count", 0) or 0)
    top_kills = max(
        (int(getattr(p, "kill_count", 0) or 0) for p in players), default=0,
    )
    if my_kills >= top_kills and my_kills > 0:
        return True
    scoring = getattr(game_state, "m9_scoring", None)
    if scoring is not None:
        my_story = scoring._story(player_id)
        top_story = max(
            (scoring._story(p.player_id) for p in players), default=0.0,
        )
        if my_story >= top_story and my_story > 0:
            return True
    return False


@dataclass(frozen=True)
class RewardLedger:
    terminal: float = 0.0
    potential_raw: float = 0.0
    potential: float = 0.0
    legacy_events_raw: float = 0.0
    m9_events_raw: float = 0.0
    events: float = 0.0
    behavior_raw: float = 0.0
    behavior: float = 0.0
    total: float = 0.0
    event_types: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _g(state: Any, path: str, default):
    cur: Any = state
    for part in path.split("."):
        cur = getattr(cur, part, None)
        if cur is None:
            return default
    return cur


def _call(obj: Any, name: str, *args, default=None):
    fn = getattr(obj, name, None)
    if not callable(fn):
        return default
    try:
        return fn(*args)
    except Exception:
        return default


def talent_event_reward(
    events: List[Dict[str, Any]], player_id: str,
) -> float:
    """P5 预留钩子：天赋课程的事件塑形入口（P2 空实现，不加 shaping）。

    纪律：P5 逐课启用时，事件类型与系数必须登记进 M9_EVENT_REWARD 并被
    tests/test_rl_m9_reward_ledger.py 覆盖；事件以 slot 无关的 type 表达，
    禁止在奖励层做天赋具名分支。
    """
    return 0.0


def m9_supplemental_event_reward(
    events: List[Dict[str, Any]], player_id: str,
) -> float:
    """只计算 M9 增量事件；legacy 已处理的事件不在此重复。"""
    r = 0.0
    for event in events:
        etype = event.get("type", "")
        if etype not in M9_EVENT_REWARD:
            continue
        fields = M9_EVENT_OWNER_FIELDS.get(
            etype, ("player", "attacker", "owner"),
        )
        if any(event.get(field) == player_id for field in fields):
            r += M9_EVENT_REWARD[etype]
    return r + talent_event_reward(events, player_id)


def m9_event_reward(events: List[Dict[str, Any]], player_id: str,
                    game_state=None) -> float:
    """兼容 API：legacy 通用事件 + 无重复的 M9 增量事件。"""
    return (
        _L.event_reward(events, player_id, game_state)
        + m9_supplemental_event_reward(events, player_id)
    )


def m9_behavior_penalty(
    player: Any,
    game_state: Any,
    action_type: str,
    action_success: bool,
    action_idx: Optional[int],
    events: List[Dict[str, Any]],
    *,
    forced_forfeit: bool = False,
) -> float:
    """M9 行为惩罚：普通主动放弃递增扣分，规则要求的放弃不误罚。

    legacy 的 ``no_action_streak`` 在 M9 已退役，旧 ``-0.5 * streak`` 因而
    让前几次 forfeit 实际为 0。这里仅对 M9 追加 -2/-3/-4/-5/-6 raw；
    RewardTracker 的 beta=0.5 后为 -1/-1.5/-2/-2.5/-3。
    """
    base = _L.behavior_penalty(
        player, game_state, action_type, action_success, action_idx,
    )
    if action_type != "forfeit":
        player._m9_rl_forfeit_streak = 0
        return base

    # G0 调整呼吸的主动 forfeit 是明文回血动作；事件比动作后的瞬时 talent
    # 状态更可靠（同一 step 的 R4 可能已经结束 breath_active）。
    g0_breath_forfeit = any(
        event.get("type") == "g0_breath_forfeit_heal"
        and event.get("player") == getattr(player, "player_id", None)
        for event in events
    )
    if forced_forfeit or g0_breath_forfeit:
        player._m9_rl_forfeit_streak = 0
        return base

    streak = int(getattr(player, "_m9_rl_forfeit_streak", 0) or 0) + 1
    player._m9_rl_forfeit_streak = streak
    return base - (2.0 + min(streak - 1, 4))


def m9_potential(player, game_state) -> float:
    """legacy 势函数 + M9 专属势能项。"""
    phi = _L.potential(player, game_state)
    m9 = getattr(game_state, "m9_system", None)
    pp = getattr(game_state, "m9_pp", None)
    arc = getattr(game_state, "m9_arc", None)
    police = getattr(game_state, "m9_police", None)
    pid = player.player_id

    # SP：越高越接近即演/公演
    phi += min(float(_call(m9, "get_sp", pid, default=0)), 2.0) * 8.0
    # 当前授予
    cur = _call(m9, "current_grant", default=None)
    if cur is not None and getattr(cur, "actor_id", None) == pid:
        phi += 6.0
    # 公演队列 / 公演位
    queue = _g(m9, "queue", None)
    if queue and _call(queue, "is_in_queue", pid, default=False):
        phi += 4.0
    holder = _g(m9, "_public_holder_by_round", None)
    if holder and holder.get(getattr(game_state, "current_round", 0)) == pid:
        phi += 8.0
    # PP / 剧情分
    balance = float(_call(pp, "balance", pid, default=0.0))
    phi += max(0.0, balance) * 2.0
    chapters = _call(arc, "chapters_of", pid, default=set())
    phi += len(chapters) * 6.0   # 对齐官方 story=min(arc,cap)*arc_weight(2*3)
    # 官方战果中“单体自证”组成项：damage_dealt（legacy 势能已有 kill*15，
    # 这里补伤害项；官方 damage_weight=0.1，取温和系数防尖锐）
    dmg = float(getattr(player, "damage_dealt", 0.0) or 0.0)
    phi += dmg * 0.2
    # 队长身份不进入势函数：它是权限而不是成果。否则仅当选就会持续获得
    # shaping，压过真正的调动/攻击决策。
    # 菜单式地图：接近 = 同地点。给"与存活敌人同地点"铺势能——近战链
    #（move→find→attack）的中间梯度，RL 追到对手同格即有正势能差。
    if game_state is not None:
        for opp_id in getattr(game_state, "player_order", []) or []:
            if opp_id == pid:
                continue
            opp = _call(game_state, "get_player", opp_id, default=None)
            if (opp is not None
                    and _call(opp, "is_alive", default=False)
                    and getattr(opp, "location", None) == getattr(player, "location", None)):
                phi += 6.0
                break
    return phi


class M9RewardTracker(_L.RewardTracker):
    """与 legacy RewardTracker 同结构，势函数/事件层换为 M9 版本。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_ledger = RewardLedger()
        self._episode_ledger: Dict[str, float] = {
            key: 0.0 for key in (
                "terminal", "potential", "events", "behavior", "total",
            )
        }

    def reset(self, player, game_state):
        self._prev_potential = m9_potential(player, game_state)
        self._event_cursor = len(game_state.event_log)
        self.last_ledger = RewardLedger()
        self._episode_ledger = {
            key: 0.0 for key in (
                "terminal", "potential", "events", "behavior", "total",
            )
        }

    @staticmethod
    def _terminal_reward(game_state, player_id: str) -> float:
        """按正式结算快照计分；多人败方保留名次梯度。"""
        if getattr(game_state, "rl_course_scenario", None) == (
            "combat_assassin_pressure"
        ):
            # This option-handoff lesson owns the first attributable kill;
            # the deployed continuation policy owns the remaining opponent.
            # A world-event win with zero RL kills is still a failed lesson.
            player = game_state.get_player(player_id)
            return (
                100.0
                if player is not None
                and int(getattr(player, "kill_count", 0) or 0) >= 1
                else -100.0
            )
        if getattr(game_state, "rl_course_scenario", None) in {
            "economy_pursuit_supply", "economy_armored_pursuit_supply",
            "economy_armored_pursuit_supply_slot0",
            "equipment_fire_antiarmor",
            "equipment_bow_loadout",
        }:
            # 该风洞从预置一杀开始；正式 M9 评分会把这份课前状态当成功劳，
            # 甚至在第二目标被世界事件消灭时给随机策略胜者奖励。专项课只认
            # 本 episode 内亲手完成的第二杀，避免错误终局标签反向训练策略。
            player = game_state.get_player(player_id)
            return (
                100.0
                if player is not None
                and int(getattr(player, "kill_count", 0) or 0) >= 2
                else -100.0
            )
        winners = getattr(game_state, "game_winner_snapshot", None)
        scores = getattr(game_state, "base_final_scores", None)
        if winners is not None:
            if player_id in winners:
                # 主动胜利条款（DOC-054）：空胜利（无击杀且无剧情领先）
                # 打折——避战拖分流对训练不再等价于主动玩法胜利。
                return (
                    ACTIVE_VICTORY_REWARD
                    if active_victory(game_state, player_id)
                    else PASSIVE_VICTORY_REWARD
                )
            if isinstance(scores, dict) and player_id in scores:
                n_players = max(len(scores), 1)
                rank = 1 + sum(score > scores[player_id] for score in scores.values())
                if n_players > 2:
                    return -20.0 - 80.0 * max(rank - 2, 0) / (n_players - 2)
            return -100.0

        # 防御性兼容：未走 M9 正式 settle 的异常/legacy 状态。
        winner = getattr(game_state, "winner", None)
        if winner == player_id:
            return 100.0
        if winner == "nobody":
            return -75.0
        return -100.0

    def episode_ledger(self) -> dict[str, float]:
        return dict(self._episode_ledger)

    def compute(self, player, game_state, action_type, action_success,
                action_idx=None, *, forced_forfeit: bool = False) -> float:
        terminal = (
            self._terminal_reward(game_state, self.rl_player_id)
            if game_state.game_over else 0.0
        )

        # 第二层：势函数差分（M9）
        if game_state.game_over:
            shaping = 0.0 * self.gamma - self._prev_potential
            self._prev_potential = 0.0
        else:
            curr = m9_potential(player, game_state)
            shaping = self.gamma * curr - self._prev_potential
            self._prev_potential = curr
        potential_raw = shaping
        shaping = M9_POTENTIAL_SCALE * potential_raw

        # 第三层：事件（M9 = legacy 通用 + M9 专属）
        new_events = game_state.event_log[self._event_cursor:]
        self._event_cursor = len(game_state.event_log)
        legacy_events_raw = _L.event_reward(
            new_events, self.rl_player_id, game_state,
        )
        m9_events_raw = m9_supplemental_event_reward(
            new_events, self.rl_player_id,
        )
        events = self.alpha * (legacy_events_raw + m9_events_raw)

        # 第四层：行为惩罚（legacy 通用）
        behavior_raw = m9_behavior_penalty(
            player, game_state, action_type, action_success, action_idx,
            new_events, forced_forfeit=forced_forfeit,
        )
        behavior = self.beta * behavior_raw
        total = terminal + shaping + events + behavior
        self.last_ledger = RewardLedger(
            terminal=terminal,
            potential_raw=potential_raw,
            potential=shaping,
            legacy_events_raw=legacy_events_raw,
            m9_events_raw=m9_events_raw,
            events=events,
            behavior_raw=behavior_raw,
            behavior=behavior,
            total=total,
            event_types=tuple(str(event.get("type", "")) for event in new_events),
        )
        for key in self._episode_ledger:
            self._episode_ledger[key] += float(getattr(self.last_ledger, key))
        return total


def make_reward_tracker(game_state=None) -> Any:
    """按 profile 返回奖励追踪器：m9-rfc → M9RewardTracker；否则 legacy。"""
    from engine.m9.gate import m9_enabled
    if m9_enabled(game_state):
        return M9RewardTracker
    return _L.RewardTracker
