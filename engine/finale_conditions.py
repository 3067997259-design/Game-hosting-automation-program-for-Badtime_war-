"""完结条框架（M6f，experiment: m6_scoring，v2.0 §4.3）。

剧情分大额块——试点 3 个完结条：
- G2 完整谢幕（+g2_curtain）
- G4 救世主毕业（救世主状态结束时存活，此后再存活 g4_survive_rounds 轮，+g4_graduation）
- G5 两次成功锚定（+g5_double_anchor）

**M6 只立框架 + 桩**：天赋达成时调 mark(player, key) 给剧情分。但天赋是 v1 量纲
（M7 才迁移），M6 的 --no-talents 风洞完结条恒不触发（剧情分=0），实际接入由 M7。
达成率验收目标 10~20%（M7 后才有数据）。

可见性（§4.4）：完结条进度半公开（"G5 已完成 1/2 次锚定"）。
"""
from __future__ import annotations
from typing import Any, List

from engine import experiments
from engine.balance import get as bget

# 完结条注册表：key → (天赋, balance 剧情分键, 描述)
FINALE_CONDITIONS = {
    "g2_curtain": ("g2_hologram", "g2_curtain", "G2 完整谢幕"),
    "g4_graduation": ("g4_savior", "g4_graduation", "G4 救世主毕业"),
    "g5_double_anchor": ("g5_ripple", "g5_double_anchor", "G5 两次成功锚定"),
}


def mark(player: Any, key: str, game_state: Any = None) -> bool:
    """标记玩家达成完结条，给剧情分（每个完结条每局每人限 1 次）。

    天赋在达成时调用——M7 天赋迁移时接入。返回是否实际给分。
    """
    if not experiments.is_enabled("m6_scoring"):
        return False
    if key not in FINALE_CONDITIONS:
        return False
    done = getattr(player, "_finale_done", None)
    if done is None:
        done = set()
        player._finale_done = done
    if key in done:
        return False
    done.add(key)
    _, score_key, desc = FINALE_CONDITIONS[key]
    pts = bget("finale", score_key, default=0)
    player.story_score = getattr(player, "story_score", 0) + pts
    if game_state is not None:
        game_state.log_event("finale", player=player.player_id,
                             condition=key, story_score=pts)
    return True


def progress(player: Any) -> List[str]:
    """完结条进度（半公开，§4.4）。供 display 展示。"""
    done = getattr(player, "_finale_done", set())
    return [FINALE_CONDITIONS[k][2] for k in done if k in FINALE_CONDITIONS]
