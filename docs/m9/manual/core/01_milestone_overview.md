---
doc_id: manual.m9.milestone
status: candidate
profile: m9-rfc
canonical_for: ["manual.m9.overview", "manual.m9.milestone"]
requires: ["manual.m9.frontmatter"]
topics: ["overview", "milestone", "m9"]
source_body_sha256: 3432ec7071d86fb4f8dae8da2d1f88a1a4a13463e4fc5ec5c33612e3ec21e295
---

# 一、M9 里程碑概览（候选）

> 本节是首个里程碑快照。详细规则在写入手册前，先读
> [`docs/m9/README.md`](../../README.md) 的权威清单；语义冲突以 `current/` RFC 为准。

## 1.1 这个 profile 是什么

`m9-rfc` 是独立的实验档案：默认 profile 仍是 `legacy`，M9 只通过
`--profile m9-rfc` 运行。它把行动槽、天赋演出、警察、PP 与剧情分接到同一套
已实装机制上，并已经完成首轮 5000 局风洞校准；尚未并入默认 profile。

## 1.2 已经冻结的骨架

- 每名正常存活玩家一个标准行动槽；先攻只排序，不淘汰行动者。
- SP 是公开能力层级 `0 / 1 / 2`：即演固定 −1，公演固定 −2；公演每轮唯一，
  R0 报名、FIFO、队首失效不递补。
- 完整额外行动只有三个白名单来源：T4 或跃、G5 地火、G4 负世主动燃尽；
  每人每轮至多 `⟦bal:m9_system.action.full_extra_per_round⟧` 个。
- 14 个天赋槽位：T1–T4、T6–T7 与 G0–G7（G0 取代退役 T5）。
- 生命模型：HP 上限 `⟦bal:hp20.player_max_hp⟧`；剧情分每章上限
  `⟦bal:m9_system.scoring_m9.arc_cap⟧`。
- G2 光身/影身是共享玩家身份的完整双 actor；影身初始 HP
  `⟦bal:m9_talents_extended.g2.shadow_hp⟧`。

## 1.3 文本与数值口径

- 玩家可见文本统一在 `data/prompts.json` 的 `m9` 命名空间（949 个模板键）；
  代码不再携带中文 fallback，新增文案受 `tests/test_m9_text_governance.py` 约束。
- 所有机制数值以 `data/balance.json` 为唯一信源；本手册中的数字必须写成
  `⟦ bal:... ⟧`（示例带空格，实际不留空格），不允许在正文里复制数值。

## 1.4 后续模块规划（供扩展）

| 建议模块 | 主题 | 对应权威 |
|---|---|---|
| `core/02_rounds_actions` | 轮次、行动槽、SP/即演/公演 | 行动系统 RFC v0.8 |
| `core/03_combat_status` | 伤害、属性、状态、绝对死亡 | 结算合同 RFC v0.3 |
| `core/04_scoring` | PP、往世层、剧情分、黑马/投注 | PP/评分/剧情分 RFC |
| `core/05_police` | 固定警力、通缉、队长、停机 | 警察与 T6 RFC v0.3 |
| `talents/*` | 14 个天赋与 G5 诗篇 | 各天赋 current RFC |
