"""
rl/reward.py
────────────
奖励追踪器（四层奖励结构，天赋感知版）

第一层：终局奖励（Terminal Reward）
  获胜 +100 / 死亡 -100 / 平局(全灭) -75

第二层：势函数差分（Potential-Based Reward Shaping）
  r_shaping = gamma * Phi(s') - Phi(s)
  数学上保证不改变最优策略
  天赋扩展：根据天赋类型添加额外势能项

第三层：事件驱动奖励（Event-Based）
  从 game_state.event_log 增量提取
  天赋扩展：追踪天赋专属事件

第四层：行为惩罚（Anti-Degenerate）
  forfeit 递增惩罚 / 无效行动 / 过长对局
  天赋扩展：条件化惩罚（如攻击 G4 = 给他充能）

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


def _get_talent_cls(player) -> str:
    """返回玩家天赋的类名，无天赋返回空字符串"""
    talent = getattr(player, "talent", None)
    if talent is None:
        return ""
    return talent.__class__.__name__


# ─────────────────────────────────────────────────────────────────────────────
#  第二层：势函数 Phi(s)
# ─────────────────────────────────────────────────────────────────────────────

def potential(player, game_state) -> float:
    """
    势函数：衡量玩家当前状态的综合价值。

    设计参考 BasicAIController._estimate_power()，
    扩展了发育、战略、警察、天赋四个维度。
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
    phi += min(len(_unique_weapon_names), 3) * 10               # 有效武器种类（去重，上限3）
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

    # === 战斗准备维度 ===
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
        phi += 12 + game_state.police.authority * 3
    elif getattr(player, "has_police_protection", False):
        phi += 5
    if player.is_criminal:
        phi -= 8

    # ═══════════════════════════════════════════════════════════════════════
    #  天赋势能（根据天赋类型添加额外势能项）
    # ═══════════════════════════════════════════════════════════════════════

    talent = getattr(player, "talent", None)
    if talent is not None:
        cls = talent.__class__.__name__
        phi += _talent_potential(talent, cls, player, game_state)

    return phi


def _talent_potential(talent, cls: str, player, game_state) -> float:
    """天赋专属势能项。独立函数便于维护。"""
    phi = 0.0

    # ── T1 一刀缭断 ──
    if cls == "OneSlash":
        # 每次使用机会值 8 点势能
        phi += getattr(talent, 'uses_remaining', 0) * 8

    # ── T2 剪刀手一突 ──
    elif cls == "ScissorRush":
        phi += getattr(talent, 'response_uses_remaining', 0) * 5
        # 未触发的警觉值 3 点
        if not getattr(talent, 'find_triggered', True):
            phi += 3
        if not getattr(talent, 'found_triggered', True):
            phi += 3
        # 攻击回盾计数（偶数次攻击回盾，鼓励持续攻击）
        phi += getattr(talent, 'attack_count', 0) * 0.5

    # ── T3 天星 ──
    elif cls == "Star":
        phi += getattr(talent, 'uses_remaining', 0) * 10

    # ── T4 六爻 ──
    elif cls == "Hexagram":
        phi += getattr(talent, 'charges', 0) * 6
        if getattr(talent, 'immunity_active', False):
            phi += 15  # 金身状态极有价值

    # ── T5 Combo ──
    elif cls == "Combo":
        consecutive = getattr(talent, 'consecutive_actions', 0)
        phi += consecutive * 4  # 连续行动越多越接近触发
        if getattr(talent, '_d4_force', False):
            phi += 10  # 下一轮 D4=4, D6=6 非常有价值

    # ── T6 朝阳好市民 ──
    elif cls == "GoodCitizen":
        # 纯被动，不需要额外势能
        pass

    # ── T7 死者苏生 ──
    elif cls == "Resurrection":
        if not getattr(talent, 'learned', False):
            # 学习进度有价值
            phi += getattr(talent, 'learn_progress', 0) * 5
        elif getattr(talent, 'mounted_on', None) is None:
            # 已学会但未挂载，挂载机会值 10 点
            phi += 10
        elif not getattr(talent, 'used', False):
            # 已挂载未触发，保险值 15 点
            phi += 15

    # ── G1 火萤 ──
    elif cls == "G1MythFire":
        # debuff 前：行动次数越多越好（延迟 debuff）
        if not getattr(talent, 'debuff_started', False):
            phi += getattr(talent, 'action_turn_count', 0) * 1.5
        else:
            # debuff 后：炽愿充能是生存资源
            phi += getattr(talent, 'ardent_wish_charges', 0) * 8
        # 超新星是强力一次性资源
        if getattr(talent, 'has_supernova', False):
            phi += 12

    # ── G2 全息影像 ──
    elif cls == "Hologram":
        if not getattr(talent, 'used', False):
            phi += 12  # 未使用的天赋机会
        if getattr(talent, 'active', False):
            phi += getattr(talent, 'remaining_rounds', 0) * 3  # 影像持续中

    # ── G3 神话之外 ──
    elif cls == "Mythland":
        if not getattr(talent, 'used', False):
            phi += 15  # 未使用的结界机会
        if getattr(talent, 'active', False):
            phi += 10  # 结界中有战略优势

    # ── G4 愿负世 ──
    elif cls == "Savior":
        if getattr(talent, 'spent', False):
            pass  # 已永久失效
        elif getattr(talent, 'is_savior', False):
            # 救世主状态中：临时 HP + 攻击加成极有价值
            phi += getattr(talent, 'temp_hp', 0) * 10
            phi += getattr(talent, 'temp_attack_bonus', 0) * 8
            phi += getattr(talent, 'savior_duration', 0) * 3
        else:
            # 积累火种阶段：火种越多越接近触发
            divinity = getattr(talent, 'divinity', 0)
            phi += divinity * 2
            # 接近满火种时价值急剧上升
            if divinity >= 8:
                phi += (divinity - 8) * 5

    # ── G5 涟漪 ──
    elif cls == "Ripple":
        reminiscence = getattr(talent, 'reminiscence', 0)
        phi += reminiscence * 0.5
        # 锚定进行中
        if getattr(talent, 'anchor_active', False):
            phi += 8
            phi += getattr(talent, 'anchor_fate', 0) * 3  # 命定值越高越好
            phi -= getattr(talent, 'anchor_destructive_count', 0) * 5  # 破坏性行动是负面的

    # ── G6 要有笑声 ──
    elif cls == "CutawayJoke":
        phi += getattr(talent, 'laugh_points', 0) * 2
        phi += getattr(talent, 'cutaway_charges', 0) * 8
        if getattr(talent, '_d4_force', False):
            phi += 10

    # ── G7 星野 ──
    elif cls == "Hoshino":
        # 形态价值
        form = getattr(talent, 'form', None)
        if form:
            phi += 5  # 有形态 = 已融合
        # cost 资源
        phi += getattr(talent, 'cost', 0) * 2
        # 铁之荷鲁斯
        phi += getattr(talent, 'iron_horus_hp', 0) * 6
        # Terror 状态
        if getattr(talent, 'is_terror', False):
            phi += 20
            phi += getattr(talent, 'terror_extra_hp', 0) * 8

    return phi


# ─────────────────────────────────────────────────────────────────────────────
#  第三层：事件驱动奖励
# ─────────────────────────────────────────────────────────────────────────────

def event_reward(events: List[Dict[str, Any]], player_id: str,
                 game_state=None) -> float:
    """
    从 event_log 新增事件中提取即时奖励。

    参数
    ----
    events     : 本次 step 新增的事件列表（game_state.event_log 的切片）
    player_id  : RL 智能体的 player_id
    game_state : 当前 GameState（用于条件化奖励，可选）
    """
    r = 0.0
    for event in events:
        etype = event.get("type", "")

        # ══════════════════════════════════════════════════════════════════
        #  基础战斗事件（原有）
        # ══════════════════════════════════════════════════════════════════

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
                r -= 1.0                                     # 攻击未命中/被克制

            # ── 条件化：攻击 G4 未进入救世主 = 给他充能 ──
            if game_state is not None:
                target_id = event.get("target")
                target = game_state.get_player(target_id) if target_id else None
                if target and _get_talent_cls(target) == "Savior":
                    target_talent = target.talent
                    if not getattr(target_talent, 'is_savior', False) \
                       and not getattr(target_talent, 'spent', False):
                        r -= 2.0  # 轻微惩罚：打未激活的 G4 = 给他充能

        # ── 成功找到目标 ──
        if etype == "find" and event.get("player") == player_id:
            r += 2.0

        # ── 成功锁定目标 ──
        if etype == "lock" and event.get("player") == player_id:
            r += 1.5

        # ── 我方被攻击 ──
        if etype == "attack" and event.get("target") == player_id:
            result = event.get("result", {})
            r -= result.get("hp_damage", 0) * 3
            if result.get("killed"):
                r -= 50.0

        # ── 犯罪记录 ──
        if etype == "crime" and event.get("player") == player_id:
            r -= 5.0

        # ── 成功举报 ──
        if etype == "report" and event.get("reporter") == player_id:
            r += 3.0

        # ── 当选队长 ──
        if etype == "captain_elected" and event.get("captain") == player_id:
            r += 10.0

        # ══════════════════════════════════════════════════════════════════
        #  天赋事件奖励
        # ══════════════════════════════════════════════════════════════════

        # ── T1 一刀缭断 ──
        if etype == "oneslash_attack" and event.get("player") == player_id:
            r += 10.0  # 成功发动天赋攻击
            if event.get("killed"):
                r += 15.0  # 天赋击杀额外奖励

        # ── T2 剪刀手一突：犯罪再动 ──
        if etype == "scissor_rush_crime_trigger" and event.get("player") == player_id:
            r += 5.0  # 首次犯罪类型触发额外行动

        # ── T2 剪刀手一突：紧急战斗策略 ──
        if etype == "scissor_rush_response" and event.get("player") == player_id:
            r += 6.0  # 响应窗口成功使用

        # ── T2 剪刀手一突：警觉 ──
        if etype == "scissor_rush_vigilance" and event.get("player") == player_id:
            r += 4.0  # find/found_by 触发额外行动

        # ── T2 剪刀手一突：攻击回盾 ──
        if etype == "scissor_rush_shield_recovery" and event.get("player") == player_id:
            r += 3.0  # 偶数次攻击回盾

        # ── T3 天星 ──
        if etype == "star" and event.get("player") == player_id:
            r += 8.0  # 天星发动

        # ── T4 六爻 ──
        if etype == "hexagram_cast" and event.get("player") == player_id:
            r += 6.0  # 成功发动六爻（结果好坏由通用攻击/伤害事件体现）

        # ── T5 Combo 连击触发 ──
        if etype == "combo_trigger" and event.get("player") == player_id:
            r += 5.0  # 连续行动达到阈值，下轮骰子必满

        # ── T7 死者苏生 ──
        if etype == "resurrection_learned" and event.get("player") == player_id:
            r += 3.0  # 学习完成（里程碑）
        if etype == "resurrection_mount" and event.get("player") == player_id:
            r += 5.0  # 成功挂载到目标
        if etype == "resurrection_trigger" and event.get("player") == player_id:
            r += 15.0  # 目标死亡触发苏生（极高价值）

        # ══════════════════════════════════════════════════════════════
        #  神代天赋事件奖励
        # ══════════════════════════════════════════════════════════════

        # ── G1 火萤IV型 ──
        if etype == "firefly_debuff_start" and event.get("player") == player_id:
            r -= 3.0  # debuff 开始（不可避免，但 RL 应感知到负面状态变化）
        if etype == "firefly_supernova_granted" and event.get("player") == player_id:
            r += 5.0  # 获得超新星过载（可用资源增加）
        if etype == "firefly_supernova" and event.get("player") == player_id:
            r += 8.0  # 超新星过载命中（移动时 AOE 伤害）
        if etype == "firefly_kill" and event.get("player") == player_id:
            r += 10.0  # 火萤击杀（会再次授予超新星，形成正循环）

        # ── G2 全息影像 ──
        if etype == "hologram_activate" and event.get("player") == player_id:
            r += 12.0  # 展开全息影像（一次性强力区域控制）
        if etype == "hologram_expire" and event.get("player") == player_id:
            r -= 2.0  # 影像自然消失（轻微负信号，鼓励在影像期间行动）

        # ── G3 神话之外 ──
        if etype == "mythland_activate" and event.get("player") == player_id:
            r += 10.0  # 展开结界（创造 1v1 或独立空间）
        if etype == "mythland_end" and event.get("player") == player_id:
            r += 0.0  # 结界结束（中性，结界内的战斗结果由通用事件体现）

        # ── G4 愿负世 ──
        if etype == "savior_activate" and event.get("player") == player_id:
            r += 20.0  # 进入救世主状态（巨大战力飙升）
        if etype == "savior_end" and event.get("player") == player_id:
            hp_gain = event.get("permanent_hp_gain", 0)
            r += 5.0 + hp_gain * 5.0  # 永久转化 HP（基础 +5，每点 HP +5）

        # ── G6 要有笑声 ──
        if etype == "cutaway_charge" and event.get("player") == player_id:
            r += 4.0  # 笑点积满，获得插入式笑话充能
        if etype == "cutaway_joke" and event.get("player") == player_id:
            r += 8.0  # 成功使用插入式笑话（借用他人行动）

    return r


# ─────────────────────────────────────────────────────────────────────────────
#  第四层：行为惩罚（天赋感知版）
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

    # 纯连续移动惩罚
    move_streak = getattr(player, '_rl_move_streak', 0)
    if action_type == "move":
        move_streak += 1
        player._rl_move_streak = move_streak
        if move_streak >= 3:
            r -= 2.0 * (move_streak - 2)  # 第3次-2, 第4次-4, 第5次-6...
    else:
        player._rl_move_streak = 0

    # 通用重复行动惩罚：完全相同的动作连续重复 5 次及以上
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
            r -= 4.0 * (streak - 4)  # 第5次: -4, 第6次: -8, 第7次: -12...

    # ═══════════════════════════════════════════════════════════════════════
    #  天赋条件化惩罚
    # ═══════════════════════════════════════════════════════════════════════

    talent = getattr(player, 'talent', None)
    if talent is not None:
        cls = talent.__class__.__name__

        # G4 反直觉：攻击未进入救世主状态的 G4 玩家 = 给他充能
        if cls != "Savior":  # 自己不是 G4 时才检查
            for p in game_state.alive_players():
                if p.player_id == player.player_id:
                    continue
                opp_talent = getattr(p, 'talent', None)
                if (opp_talent and opp_talent.__class__.__name__ == "Savior"
                        and not getattr(opp_talent, 'is_savior', False)
                        and not getattr(opp_talent, 'spent', False)):
                    # 检查最近事件：是否刚攻击了这个 G4
                    for evt in game_state.event_log[-3:]:
                        if (evt.get("type") == "attack"
                                and evt.get("attacker") == player.player_id
                                and evt.get("target") == p.player_id):
                            divinity = getattr(opp_talent, 'divinity', 0)
                            if divinity >= 8:
                                r -= 3.0  # 对手火种已高，继续打 = 帮他触发
                            else:
                                r -= 1.0  # 轻微惩罚，提醒 RL 注意

        # Combo：forfeit 打断连击的额外惩罚
        if cls == "Combo":
            progress = getattr(talent, 'consecutive_actions', 0)
            if action_type == "forfeit" and progress >= 2:
                r -= 3.0  # 即将触发 combo 却 forfeit，浪费

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
    def compute(self, player, game_state, action_type, action_success, action_idx=None) -> float:
        """计算四层奖励总和。"""
        total = 0.0

        # ── 第一层：终局奖励 ──
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
        total += self.alpha * event_reward(
            new_events, self.rl_player_id, game_state
        )

        # ── 第四层：行为惩罚 ──
        total += self.beta * behavior_penalty(
            player, game_state, action_type, action_success, action_idx
        )

        return total