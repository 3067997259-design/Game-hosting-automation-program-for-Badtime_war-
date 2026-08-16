# 文稿归档区

本目录保存已经退出当前编辑链、但仍具有考古、迁移或独有内容价值的文稿。归档文件不定义
`legacy`、`v2exp` 或 `m9-rfc` 的现行规则；人类和 Agent 应从 [`../README.md`](../README.md)
选择正确入口。

## V2 迁移原件

| 文件 | 身份 | 处置理由 |
|---|---|---|
| `v2-migration/完全游玩手册V2.0-exp.src.docx` | 冻结迁移基线 | 其逻辑文本已无语义拆分为 `docs/handbook/`；不再作为作者源 |
| `v2-migration/完全游玩手册V2.0-exp.src.G3G7.docx` | 局部实验分叉 | 与主 DOCX 大量重合，但仍有约 100 个非精确重合段落，不能删除 |
| `v2-migration/G3G7.extracted.md` | 机器提取阅读稿 | 供 Agent 搜索局部实验分叉；不保留 Word 排版，不定义规则 |

冻结校验值：

| 对象 | SHA-256 |
|---|---|
| 主 DOCX 文件 | `AE5CC2AD709FAE8BC432354510B70700038EB1B57ADC721EAD11394E9713C10A` |
| 主 DOCX 逻辑文本 | `998354F3934261A90DC0DA13CC8037BBEB49472CDA1485A407A3FC28F27D483E` |
| G3/G7 DOCX 文件 | `0EEECD1CAE7A8364A8042B5082BE0305964B2FFC9122E3AB41C8B260F190D9F5` |
| G3/G7 逻辑文本 | `4523D61C17CD530F5E89D2532B8E8242A6845FEA70C00A978293C168099BEB94` |

## V2 旧 Markdown 生成链

`v2-old-chain/` 保存原 `docs/完全游玩手册V2.0-exp.src.md` 及其渲染版。旧作者 Markdown 与
已确认 DOCX 迁移基线的逻辑文本相似度约为 55.9%，因此既不能当成当前作者源，也不能直接
删除。当前 V2 作者源是 [`../handbook/README.md`](../handbook/README.md) 登记的模块；需要
完整阅读时使用 `complete.generated.md`。

## Legacy 迁移原件

`legacy-migration/完全游玩手册.md` 是原 legacy 单体手册的冻结快照，文件 SHA-256 为
`972DF15FE443B55B4FB7EFDF70D31B4A1DD2C689D97C2BE24BBB4680300F6809`。其正文已无语义拆分为
`docs/legacy/` 的 34 个模块。迁移完成时逐字符回组装 SHA-256 为
`02B5230D1550DA591713819A6DD101D9BAE56DFE048711973020643ECE542266`；迁移确认后仅修正了三个
因目录拆分而失效的相对链接，冻结原件未改动。

当前 legacy 作者源是模块，不再直接编辑归档原件。原路径 `docs/完全游玩手册.md` 只保留
一个兼容入口，防止旧链接静默落到错误 profile。

## 归档纪律

- 不在归档文件中继续演化规则；
- 只允许补充身份横幅、勘误和提取索引；
- 若独有内容被吸收，先在审计中给出逐项证据，再讨论删除原件；
- DOCX 发布版未来应从模块化 Markdown 生成，不再反向成为作者源；
- `~$*.docx` 是 Word 锁文件，不属于文稿，不进入归档。
