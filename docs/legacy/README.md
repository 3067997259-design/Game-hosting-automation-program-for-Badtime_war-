---
doc_id: legacy.index
status: mixed
profile: legacy
---

# Legacy 模块化手册

这里是 `legacy` profile 的模型友好入口。内容由
[`../archive/legacy-migration/完全游玩手册.md`](../archive/legacy-migration/完全游玩手册.md)
无语义拆分；原路径 `docs/完全游玩手册.md` 现仅是兼容入口。拆分只改善定位和上下文大小，
不代表其中的数值、退役天赋或实现说明已经完成语义审计。

> **不要与 V2 或 M9 混用。** V2 读取 [`../handbook/README.md`](../handbook/README.md)，M9
> 设计读取 [`../m9/README.md`](../m9/README.md)。

## 按任务读取

| 问题 | 建议模块 |
|---|---|
| 版本历史、序言 | `core/00_history_frontmatter.md` |
| 概述、准备、信息公开 | `core/01_overview_setup.md` |
| 轮次与行动 | `core/02_rounds.md`、`core/03_actions.md` |
| 地点、物品、购买凭证 | `core/04_locations_economy.md` |
| 护甲、伤害、状态 | `core/05_combat_status.md` |
| 警察与胜利 | `core/06_police_victory.md` |
| 原初天赋 | `talents/original/` |
| 神代天赋 | `talents/divine/` |
| G2 Reset / duet / StageAI | `talents/divine/g2/` |
| 新手、术语、指令、版本史 | `reference/` |

## Agent 读取

```powershell
python tools/handbook.py --manifest docs/legacy/manifest.json context combat --paths-only
python tools/handbook.py --manifest docs/legacy/manifest.json context g2
```

完整合订版 `complete.generated.md` 由模块生成，禁止手工编辑。

## 维护边界

- 当前模块统一标记为 `mixed` 或 `historical`；
- 一条规则只在一个模块中演化；
- 退役天赋与版本史保留为历史，不恢复为当前规则；
- 数值冲突先登记到 `../contradictions.md`，不静默选边；
- 修改后刷新模块元数据、重建合订版并运行文档检查。
