---
doc_id: handbook.index
status: candidate
profile: v2exp
---

# V2.0-exp 模块化手册

这里是 V2.0-exp 唯一继续维护的模型友好手册。它最初从
`docs/archive/v2-migration/完全游玩手册V2.0-exp.src.docx` 无语义迁移；迁移已经完成，模块化
Markdown 现为作者源，DOCX 只保留为冻结历史证据。在逐主题语义审计完成前，各模块状态保持
`candidate`。

## 按任务读取

| 问题 | 建议模块 |
|---|---|
| 游戏概述、准备、公开信息 | `core/01_overview_setup.md` |
| 轮次、行动、交战 | `core/02_rounds_actions.md` |
| 地点、物品、信用点 | `core/03_world_economy.md` |
| 护甲、伤害、命中、状态 | `core/04_combat.md` |
| 警察、白昼世界时钟 | `core/05_police_daylight.md` |
| 评分、喝彩、往世层 | `core/06_scoring_afterlife.md` |
| 天赋 | `talents/00_overview.md` 后接对应天赋文件 |
| 新玩家 | `quickstart.md` |
| 术语与指令 | `reference/` |

## 模型上下文命令

只列出需要读取的文件：

```powershell
python tools/handbook.py context combat --paths-only
python tools/handbook.py context g2 --paths-only
```

直接把相关模块和递归依赖输出到标准输出：

```powershell
python tools/handbook.py context scoring
```

可用主题及模块元数据见 `manifest.json`。

## 维护纪律

- 不直接编辑 `complete.generated*.md`；
- 不从归档 DOCX 反向覆盖模块；
- 一条规则只在一个模块中定义；
- 摘要只提供导航，不复制完整规则；
- 模块 frontmatter 由 manifest 和工具校验；
- 修改后运行 `python tools/handbook.py check`；
- 设计草案不直接写入本手册，必须先完成决定记录。
