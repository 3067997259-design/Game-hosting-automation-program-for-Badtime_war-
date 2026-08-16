"""命运路线模板注册表（G5「往世的涟漪」，§7.5）。

**本站只预留接口，roster 待策展**（用户拍板：先打底子——发动者自传序列的判断，
模板后补）。一条模板 = 一个生产者 `fn(state, caster, target, goal, break_piece) -> 动作序列`
（动作 mini-language 见 engine.anchor_eval）。注册后 anchor_resolver 会对每条调
`simulate_path`、取 min-命数 可行者，作为"神谕发给玩家的牌"。

将来策展示例（未实现）：
  - 直取（已由 anchor_eval.build_direct_sequence 作底线提供，无需注册为模板）
  - 武装夺命：move 商店/军事基地 → 购入克制目标外甲属性的武器 → lock/find → 连击
  - 以学弑命：去对应地点 → study（固定轮数）→ 改变可达事件 → 达成
经济/地点信息一律读既有数据表（EQUIPMENT_LOCATION/价格），信源统一，不在模板里重写规则。
"""
from __future__ import annotations

from typing import Any, Callable, List, Optional

def _zhiqu(state: Any, caster: Any, target: Any,
           goal: str, break_piece: Optional[str]) -> List[List[tuple]]:
    """直取模板：限定范围枚举（每武器一条承诺 + 一条贪心）。见 anchor_eval.commit_sequences。"""
    from engine import anchor_eval
    from engine.balance import get as bget
    weapons = anchor_eval.resolve_weapons(caster)
    horizon = int(bget("anchor", "window", default=8))
    return anchor_eval.commit_sequences(
        state, caster, target, weapons,
        goal=goal, break_piece=break_piece, horizon=horizon)


# 模板生产者列表。每个 callable 接收 (state, caster, target, goal, break_piece)，
# 返回**一小撮候选序列**（list[list[tuple]]，限定范围枚举）。武装夺命/以学弑命待策展叠加。
TEMPLATES: List[Callable[..., Optional[List[List[tuple]]]]] = [_zhiqu]


def propose_paths(state: Any, caster: Any, target: Any,
                  goal: str, break_piece: Optional[str]) -> List[List[tuple]]:
    """收集所有模板产出的候选序列集（每模板吐一小撮，全汇入交评估器取 min-命数）。"""
    out: List[List[tuple]] = []
    for produce in TEMPLATES:
        try:
            seqs = produce(state, caster, target, goal, break_piece) or []
        except Exception:
            seqs = []
        out.extend(seqs)
    return out
