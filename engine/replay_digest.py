"""golden 回放摘要 —— 把 event_log 压缩为可逐行 diff 的稳定文本。

设计约束：
- 只保留结构字段（actor/target/数值），剔除自由文本（message 等）——
  文案改动不应污染回归判定。
- 字段按 key 排序输出，与 dict 插入序无关。
- 摘要是单向的（不能还原对局），用途是回归判定：同种子重跑后
  逐行比对，第一处分歧即第一处行为变化。
"""
from __future__ import annotations
from typing import Any, Dict, List

# 剔除字段：自由文本与展示层内容，改文案不算行为变化
_EXCLUDED_KEYS = {"round", "phase", "type", "message", "msg", "text", "description"}


def _fmt_value(v: Any) -> str:
    """值的稳定字符串化：浮点固定 2 位小数，容器递归排序。"""
    if isinstance(v, float):
        return f"{v:.2f}"
    if isinstance(v, (list, tuple)):
        return "[" + ";".join(sorted(_fmt_value(x) for x in v)) + "]"
    if isinstance(v, set):
        return "{" + ";".join(sorted(_fmt_value(x) for x in v)) + "}"
    if isinstance(v, dict):
        return "{" + ";".join(f"{k}:{_fmt_value(v[k])}" for k in sorted(v)) + "}"
    return str(v)


def digest_event(event: Dict[str, Any]) -> str:
    """单事件一行：R{round}|{phase}|{type}|k=v,k=v（k 按字典序）。"""
    parts = [
        f"R{event.get('round', '?')}",
        str(event.get("phase", "?")),
        str(event.get("type", "?")),
    ]
    kv = ",".join(
        f"{k}={_fmt_value(v)}"
        for k, v in sorted(event.items())
        if k not in _EXCLUDED_KEYS
    )
    parts.append(kv)
    return "|".join(parts)


def digest_event_log(event_log: List[Dict[str, Any]]) -> List[str]:
    """整局事件流 → 摘要行列表（保持事件顺序）。"""
    return [digest_event(e) for e in event_log]


def digest_game(result: Dict[str, Any],
                digest_lines: List[str]) -> Dict[str, Any]:
    """单局 golden 记录：种子 + 结局指纹 + 事件摘要。"""
    return {
        "seed": result.get("seed"),
        "winner_pid": result.get("winner_pid"),
        "rounds": result.get("rounds"),
        "draw": result.get("draw"),
        "draw_reason": result.get("draw_reason", ""),
        "digest": digest_lines,
    }


def diff_games(expected: Dict[str, Any], actual: Dict[str, Any]) -> List[str]:
    """比对两局 golden 记录，返回差异说明（空列表 = 全等）。

    只报告第一处事件分歧（带上下文），结局字段全部报告。
    """
    problems: List[str] = []
    for key in ("seed", "winner_pid", "rounds", "draw", "draw_reason"):
        if expected.get(key) != actual.get(key):
            problems.append(
                f"{key}: expected={expected.get(key)!r} actual={actual.get(key)!r}")

    exp_d: List[str] = expected.get("digest", [])
    act_d: List[str] = actual.get("digest", [])
    if exp_d != act_d:
        idx = next(
            (i for i, (a, b) in enumerate(zip(exp_d, act_d)) if a != b),
            min(len(exp_d), len(act_d)),
        )
        problems.append(f"事件摘要分歧 @ 行 {idx} "
                        f"(expected {len(exp_d)} 行 / actual {len(act_d)} 行)")
        lo = max(0, idx - 2)
        for j in range(lo, min(idx + 1, len(exp_d))):
            problems.append(f"  expected[{j}]: {exp_d[j]}")
        for j in range(lo, min(idx + 1, len(act_d))):
            problems.append(f"  actual  [{j}]: {act_d[j]}")
    return problems
