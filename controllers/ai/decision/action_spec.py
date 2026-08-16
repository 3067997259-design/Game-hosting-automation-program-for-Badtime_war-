"""BasicAI 决策内核：ActionSpec / ScoredActionSpec（I/O 地基替换）。

- `ActionSpec`：一条完整合法动作的不可变描述（parser 可直接执行的 `raw` 字符串 +
  决策上下文：profile / slot_id / grant_id / SP 消耗 / 快照版本 / 结构化参数）。
- `ScoredActionSpec`：带评分的候选（Orchestrator 排序后返回的形态）。

设计意图：AI 与 RL 共享同一动作信源（ActionCatalog），输出不再依赖
"裸拼字符串 + 引擎重试"；`raw` 保留只为兼容旧执行管线（adapter 转回命令字符串）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass(frozen=True)
class ActionSpec:
    """一条完整合法动作（不可变）。"""

    action_type: str                 # move / attack / interact / lock / find /
                                     # special / wake / forfeit / talent_t0 ...
    raw: str                         # parser 可直接执行的完整指令字符串
    profile: str = "v2exp"           # legacy / v2exp / m9-rfc
    slot_id: str = ""                # 天赋稳定槽位（T1..G7；非天赋动作空串）
    grant_id: str = ""               # 决策时点持有的 ActionGrant id（若有）
    sp_cost: int = 0                 # M9 演出入口：即演 1 / 公演 2；普通动作 0
    state_version: int = 0           # 快照版本（构建时 current_round 等）
    params: Dict[str, Any] = field(default_factory=dict)  # 结构化参数（可选）


@dataclass(frozen=True)
class ScoredActionSpec:
    """带评分的候选动作（Orchestrator 排序后输出形态）。"""

    spec: ActionSpec
    score: float = 0.0
    reason: str = ""
