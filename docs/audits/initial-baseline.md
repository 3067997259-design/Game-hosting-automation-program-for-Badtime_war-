# 文档维护初始基线

> 采集日期：2026-08-05  
> 用途：记录模块化维护开始前的工作树状态；不作为规则信源。

## 文稿状态

- `docs/` 下主要内容文稿：13 份 Markdown。
- 当时位于根目录、现归档至 `docs/archive/v2-migration/` 的 V2 DOCX SHA-256：`AE5CC2AD709FAE8BC432354510B70700038EB1B57ADC721EAD11394E9713C10A`。
- 该 DOCX提取逻辑文本：约 50,628 字符、1,090 行。
- 当时的 `docs/完全游玩手册V2.0-exp.src.md`（现归档至 `docs/archive/v2-old-chain/`）：约 24,627 字符、915 行。
- 两者文本相似度约 0.55，不能视为同一作者源的格式差异。
- 当前工作区已有大量既存修改；本轮维护不得覆盖或回滚这些修改。

## 初始检查

`python tools/lint_docs.py --selftest`：

```text
selftest: OK
```

`python tools/lint_docs.py` 摘要：

```text
CHECK 1 悬空占位符：0
CHECK 2 幽灵键：82 INFO
CHECK 3 疑似未迁移的硬编码数值：159 WARN
CHECK 4 退役内容残留：9 INFO
CHECK 5 flag 名一致性：5 WARN
ERROR: 0 | WARN: 164 | INFO: 91
已豁免: 38 条
```

`python tools/render_docs.py --check`：

```text
文档渲染版与 balance 一致
```

该结果只证明旧 `docs/*.src.md → docs/*.md` 链内部一致，不证明它与根目录 DOCX一致。

## DOCX 视觉检查限制

已尝试使用文档渲染器生成页面图像。源 DOCX未显式记录页面尺寸，渲染器回退到 LibreOffice 转换；本机没有 `soffice`，因此没有完成 PNG视觉检查。此项记录为“环境不可用”，不是检查通过。

## 初始风险

1. 唯一内容存在于未跟踪 DOCX和 M9 文件中。
2. 旧 Markdown 生成链会稳定地产生一份与 DOCX内容不同的手册。
3. 大量规则文件同时包含现行、历史和草案口径。
4. 在完成登记和无损迁移前移动/删除文件，可能造成不可恢复的信息丢失。
