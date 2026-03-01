"""
事件类型枚举（EventType）

用途
----
所有调用 game_state.log_event() 的地方，
事件类型由原来的裸字符串（如 "attack"、"death"）
改为 EventType.ATTACK、EventType.DEATH，
避免拼写错误，并让 IDE 能够跳转和补全。

扩展方式
--------
在 EventType 中新增一行即可，不影响任何已有逻辑。

示例
----
    from engine.events import EventType
    state.log_event(EventType.ATTACK, attacker="p1", target="p2", result={})
    state.log_event(EventType.DEATH,  player="p2", cause="attack")
"""

from enum import Enum


class EventType(str, Enum):
    """游戏事件类型。

    继承 str，使得 EventType.ATTACK == "attack" 为 True，
    与原有依赖字符串比较的代码（如 event.get("type") == "attack"）
    完全向后兼容，无需修改任何读取方逻辑。
    """

    # ── 战斗 ──────────────────────────────────────────────────────────
    ATTACK      = "attack"       # 攻击行动
    DEATH       = "death"        # 玩家死亡

    # ── 移动 & 交互 ───────────────────────────────────────────────────
    MOVE        = "move"         # 玩家移动
    INTERACT    = "interact"     # 玩家与地点交互

    # ── 状态变化 ──────────────────────────────────────────────────────
    STUN        = "stun"         # 进入眩晕
    STUN_RECOVER = "stun_recover"  # 眩晕苏醒
    SHOCK       = "shock"        # 进入震荡
    SHOCK_RECOVER = "shock_recover"  # 震荡苏醒
    PETRIFY     = "petrify"      # 进入石化
    PETRIFY_RECOVER = "petrify_recover"  # 石化解除
    GO_INVISIBLE = "go_invisible"   # 进入隐身
    LOSE_INVISIBLE = "lose_invisible"  # 失去隐身

    # ── 警察系统 ──────────────────────────────────────────────────────
    CRIME       = "crime"        # 犯罪行为记录
    REPORT      = "report"       # 举报
    POLICE_ENFORCE = "police_enforce"  # 警察执法
    JOIN_POLICE = "join_police"  # 加入警察
    ELECTION    = "election"     # 竞选队长进度

    # ── 病毒系统 ──────────────────────────────────────────────────────
    VIRUS_TICK  = "virus_tick"   # 病毒计时
    VIRUS_DEATH = "virus_death"  # 病毒致死

    # ── 天赋 ──────────────────────────────────────────────────────────
    TALENT_TRIGGER = "talent_trigger"  # 天赋触发

    # ── 回合管理 ──────────────────────────────────────────────────────
    ROUND_START = "round_start"  # 回合开始
    ROUND_END   = "round_end"    # 回合结束
    EXTRA_TURN  = "extra_turn"   # 额外行动回合插入

    # ── 通用 ──────────────────────────────────────────────────────────
    INFO        = "info"         # 通用信息（调试用）
