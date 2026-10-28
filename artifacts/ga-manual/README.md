# GA 玩家手册的历史版本（只读存档）

这里不是现行规则。现行手册是 `docs/v2.0-ga/manual_draft_v0.1.md`，重写计划见 `docs/v2.0-ga/manual_rewrite_plan.md`。

| 文件 | 内容 |
|---|---|
| `manual_draft_v0.1@2026-10-25-cherrystudio.md` | CherryStudio 内置 agent 拼出的初稿（1489 行），sha256 前缀 `d5b635222f7c0eeb` |
| `manual_draft_v0.1@2026-10-27-before-author.md` | 作者 10-27 动手前的版本（初稿 + Codex 10-26 的 22 个补丁），sha256 前缀 `36eca43bfa9f33d6`，与 `artifacts/ga-migration/i1-2026-10-27/baseline.json` 的记录一致 |
| `author-edits-2026-10-27.diff` | 作者 10-27 从卷首改到 §5.1 标题的完整改动 |
| `manual_draft_v0.1@2026-10-27-author-2015.md` | 作者 20:15 保存的版本，sha256 前缀 `cebecfdc73c9b30e`；之后助手并入了第五章重写并更正了作者部分的错误 |
| `scripts/cherry_extract.py` | 从 CherryStudio 会话记录还原初稿 |
| `scripts/replay_patches.py` | 把 Codex 会话里的手册补丁按时间重放到初稿上 |

重跑方法：

```bash
python -I artifacts/ga-manual/scripts/cherry_extract.py "<CherryStudio 会话 jsonl>" <输出目录>
python -I artifacts/ga-manual/scripts/replay_patches.py <输出目录>/cherry_final.md <还原结果.md> <Codex 会话 jsonl ...>
```
