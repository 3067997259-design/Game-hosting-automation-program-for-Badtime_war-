"""
rl/reward.py
────────────
奖励追踪器（四层奖励结构）

第一层：终局奖励（Terminal Reward）
  获胜 +100 / 死亡 -100 / 平局(全灭) -75

第二层：势函数差分（Potential-Based Reward Shaping）
  r_shaping = gamma * Phi(s') - Phi(s)
  数学上保证不改变最优策略

第三层：事件驱动奖励（Event-Based）
  从 game_state.event_log 增量提取

第四层：行为惩罚（Anti-Degenerate）
  forfeit 递增惩罚 / 无效行动 / 过长对局

完整公式：
  total = terminal
        + gamma * Phi(s') - Phi(s)
        + alpha * event_reward(new_events)
        + beta  * behavior_penalty(action)
"""

from __future__ import annotations
from typing import TYPE_CHECKING, List, Dict, Any

if TYPE_CHECKING:
    from models.player import Player
    from engine.game_state import GameState

# ─────────────────────────────────────────────────────────────────────────────
#  默认超参数
# ─────────────────────────────────────────────────────────────────────────────

GAMMA = 0.99        # 势函数折扣因子
ALPHA = 0.3         # 事件奖励权重
BETA  = 0.5         # 行为惩罚权重


# ─────────────────────────────────────────────────────────────────────────────
#  辅助函数
# ─────────────────────────────────────────────────────────────────────────────

def _count_outer_armor(player) -> int:
    """统计玩家活跃的外层护甲数量"""
    armor = getattr(player, "armor", None)
    if armor and hasattr(armor, "get_active"):
        from models.equipment import ArmorLayer
        return len(armor.get_active(ArmorLayer.OUTER))
    return 0


def _count_inner_armor(player) -> int:
    """统计玩家活跃的内层护甲数量"""
    armor = getattr(player, "armor", None)
    if armor and hasattr(armor, "get_active"):
        from models.equipment import ArmorLayer
        return len(armor.get_active(ArmorLayer.INNER))
    return 0


def _effective_weapons(player) -> list:
    """返回非拳击的有效武器列表"""
    return [w for w in (player.weapons or []) if w and w.name != "拳击"]


def _best_weapon_damage(player) -> float:
    """返回最强武器的有效伤害值"""
    best = 0.0
    for w in (player.weapons or []):
        if w and hasattr(w, "get_effective_damage"):
            dmg = w.get_effective_damage()
            if dmg > best:
                best = dmg
    return best


# ─────────────────────────────────────────────────────────────────────────────
#  第二层：势函数 Phi(s)
# ─────────────────────────────────────────────────────────────────────────────

def potential(player, game_state) -> float:
    """
    势函数：衡量玩家当前状态的综合价值。

    设计参考 BasicAIController._estimate_power()，
    扩展了发育、战略、警察三个维度。
    """
    if not player.is_alive():
        return -100.0

    phi = 0.0

    # === 生存维度 ===
    phi += player.hp * 20                                       # HP 价值
    phi += _count_outer_armor(player) * 15                      # 外层护甲
    phi += _count_inner_armor(player) * 12                      # 内层护甲

    # === 发育维度 ===
    phi += player.vouchers * 3                                  # 经济资源
    _unique_weapon_names = {w.name for w in _effective_weapons(player)}
    phi += min(len(_unique_weapon_names), 3) * 10                  # 有效武器种类（去重，上限3）
    phi += _best_weapon_damage(player) * 8                      # 最强武器伤害
    phi += len(getattr(player, "learned_spells", set())) * 5    # 已学法术
    phi += 8 if getattr(player, "has_military_pass", False) else 0
    phi += 6 if player.is_invisible else 0                      # 隐身
    phi += 4 if getattr(player, "has_detection", False) else 0  # 探测

    # === 战略维度 ===
    alive_count = len(game_state.alive_players())
    phi += player.kill_count * 15                               # 击杀数
    if alive_count > 0:
        phi += (1.0 / alive_count) * 30                        # 存活比例

    # === 战斗准备维度（新增）===
    markers = game_state.markers
    for p in game_state.alive_players():
        if p.player_id == player.player_id:
            continue
        # 已面对面 = 可以近战攻击
        if markers.has_relation(player.player_id, "ENGAGED_WITH", p.player_id):
            phi += 8
        # 已锁定 = 可以远程攻击（仅在持有远程武器时有价值）
        if markers.has_relation(p.player_id, "LOCKED_BY", player.player_id):
            from models.equipment import WeaponRange
            has_ranged = any(
                getattr(w, 'weapon_range', None) == WeaponRange.RANGED
                for w in (player.weapons or []) if w
            )
            if has_ranged:
                phi += 6

    # === 降低警察维度权重 ===
    if player.is_captain:
        phi += 12 + game_state.police.authority * 3   # 原来是 20 + 5*auth
    elif getattr(player, "has_police_protection", False):
        phi += 5                                       # 原来是 10
    if player.is_criminal:
        phi -= 8                                       # 原来是 -15

    return phi

# ─────────────────────────────────────────────────────────────────────────────
#  第三层：事件驱动奖励
# ─────────────────────────────────────────────────────────────────────────────

def event_reward(events: List[Dict[str, Any]], player_id: str) -> float:
    """
    从 event_log 新增事件中提取即时奖励。

    参数
    ----
    events    : 本次 step 新增的事件列表（game_state.event_log 的切片）
    player_id : RL 智能体的 player_id
    """
    r = 0.0
    for event in events:
        etype = event.get("type", "")

        # ── 我方发起攻击 ──
        if etype == "attack" and event.get("attacker") == player_id:
            result = event.get("result", {})
            if result.get("success"):
                r += 2.0                                     # 命中
                r += result.get("hp_damage", 0) * 5          # 造成 HP 伤害
                if result.get("armor_broken"):
                    r += 3.0                                 # 击破护甲
                if result.get("stunned"):
                    r += 4.0                                 # 造成眩晕
                if result.get("killed"):
                    r += 30.0                                # 击杀
            else:
                r -= 1.0                                     # 攻击未命中/被克制 =

        # ── 成功找到目标（新增）──
        if etype == "find" and event.get("player") == player_id:
            r += 2.0                # 建立面对面关系

        # ── 成功锁定目标（新增）──
        if etype == "lock" and event.get("player") == player_id:
            r += 1.5                # 建立远程锁定

        # ── 我方被攻击 ──
        if etype == "attack" and event.get("target") == player_id:
            result = event.get("result", {})
            r -= result.get("hp_damage", 0) * 3
            if result.get("killed"):
                r -= 50.0

        # ── 犯罪记录 ──
        if etype == "crime" and event.get("player") == player_id:
            r -= 5.0

        # ── 成功举报（引擎需 log_event("report", reporter=...) 才会触发） ──
        if etype == "report" and event.get("reporter") == player_id:
            r += 3.0

        # ── 当选队长 ──
        if etype == "captain_elected" and event.get("captain") == player_id:
            r += 10.0

    return r


# ─────────────────────────────────────────────────────────────────────────────
#  第四层：行为惩罚
# ─────────────────────────────────────────────────────────────────────────────

def behavior_penalty(player, game_state, action_type, action_success, action_idx=None) -> float:
    r = 0.0

    if action_type == "forfeit":
        r -= 0.5 * player.no_action_streak

    if not action_success:
        # 递增惩罚：连续失败越多越痛
        fail_streak = getattr(player, '_rl_fail_streak', 0) + 1
        player._rl_fail_streak = fail_streak
        r -= 2.0 * fail_streak          # 第1次-2, 第2次-4, 第3次-6...
    else:
        player._rl_fail_streak = 0       # 成功则重置

    if game_state.current_round > 70:
        r -= 0.15 * (game_state.current_round - 70)

    # 在 behavior_penalty 函数中添加纯连续移动惩罚
    move_streak = getattr(player, '_rl_move_streak', 0)
    if action_type == "move":
        move_streak += 1
        player._rl_move_streak = move_streak
        if move_streak >= 3:
            r -= 2.0 * (move_streak - 2)  # 第3次-2, 第4次-4, 第5次-6...
    else:
        player._rl_move_streak = 0
    # 通用重复行动惩罚：完全相同的动作（同目标同武器/同目的地）连续重复 5 次及以上
    if action_idx is not None:
        history = getattr(player, '_rl_action_idx_history', [])
        history.append(action_idx)
        # 只保留最近 20 条，避免无限增长
        if len(history) > 20:
            history = history[-20:]
        player._rl_action_idx_history = history

        # 从末尾往前数连续相同的 action_idx
        streak = 1
        for i in range(len(history) - 2, -1, -1):
            if history[i] == action_idx:
                streak += 1
            else:
                break

        if streak >= 5:
            r -= 3.0 * (streak - 4)  # 第5次: -3, 第6次: -6, 第7次: -9...

    return r


# ─────────────────────────────────────────────────────────────────────────────
#  RewardTracker —— 有状态的奖励计算器
# ─────────────────────────────────────────────────────────────────────────────

class RewardTracker:
    """
    在 env.step() 中使用，跨步追踪势函数和事件日志偏移。

    用法::

        tracker = RewardTracker(rl_player_id, gamma=0.99, alpha=0.3, beta=0.5)
        tracker.reset(player, game_state)          # env.reset() 时调用
        reward = tracker.compute(                   # env.step() 后调用
            player, game_state, action_type, action_success
        )
    """

    def __init__(
        self,
        rl_player_id: str,
        gamma: float = GAMMA,
        alpha: float = ALPHA,
        beta: float = BETA,
    ):
        self.rl_player_id = rl_player_id
        self.gamma = gamma
        self.alpha = alpha
        self.beta = beta

        self._prev_potential: float = 0.0
        self._event_cursor: int = 0          # event_log 已处理到的位置

    # ─────────────────────────────────────────────────────────────────────
    def reset(self, player, game_state) -> None:
        """env.reset() 时调用，初始化势函数基线和事件游标。"""
        self._prev_potential = potential(player, game_state)
        self._event_cursor = len(game_state.event_log)

    # ─────────────────────────────────────────────────────────────────────
# ── 第 277-308 行，compute() 方法 ──
# 移除终局 early return，让所有四层都参与计算

    def compute(self, player, game_state, action_type, action_success, action_idx=None) -> float:
        total = 0.0

        if game_state.game_over:
            winner = game_state.winner
            if winner == self.rl_player_id:
                total += 100.0
            elif winner == "nobody":
                total += -75.0
            else:
                total += -100.0

        # ── 第二层：势函数差分 ──
        if game_state.game_over:
            # 终局：Phi(terminal) = 0（PBRS 理论要求）
            shaping = 0.0 * self.gamma - self._prev_potential
            self._prev_potential = 0.0
        else:
            curr_potential = potential(player, game_state)
            shaping = self.gamma * curr_potential - self._prev_potential
            self._prev_potential = curr_potential
        total += shaping

        # ── 第三层：事件驱动奖励 ──
        new_events = game_state.event_log[self._event_cursor:]
        self._event_cursor = len(game_state.event_log)
        total += self.alpha * event_reward(new_events, self.rl_player_id)

        # ── 第四层：行为惩罚 ──
        total += self.beta * behavior_penalty(
            player, game_state, action_type, action_success, action_idx
        )

        return total

