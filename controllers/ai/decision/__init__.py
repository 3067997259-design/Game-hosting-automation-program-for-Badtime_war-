"""BasicAI 决策内核包（I/O 地基替换）：

- `ActionSpec` / `ScoredActionSpec`：动作不可变描述与评分候选；
- `ActionCatalog`：唯一合法动作信源（引擎枚举器 + M9 special 动态列表）；
- `DecisionSnapshot`：决策点不可变快照（含 M9 世界事实投影）。

供 Orchestrator 骨架消费：minds/策略只读快照，候选经 Catalog 校验/补全后
输出 ScoredActionSpec，由 controller 的 adapter 转回命令字符串执行。
"""
