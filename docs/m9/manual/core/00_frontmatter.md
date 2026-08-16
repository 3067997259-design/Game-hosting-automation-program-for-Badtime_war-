---
doc_id: manual.m9.frontmatter
status: candidate
profile: m9-rfc
canonical_for: ["manual.m9.frontmatter"]
requires: []
topics: ["entry", "profile", "m9"]
source_body_sha256: 71692b1dd278cf26035a093a751067e8954a519cfd59272708a692c79ea160b4
---

# 《起闯战争》M9 候选玩法手册（模块化作者源）

> **Profile**：`m9-rfc`
> **状态**：模块为作者源；装配产物 `complete.generated*.md` 禁止手编。
> **权威顺序**：`docs/m9/current/` 当前 RFC 与 `data/balance.json` 优先于本手册；
> 本手册只整合已冻结玩法，不发明新机制。

## 装配说明

- 模块清单：`docs/m9/manual/manifest.json`（`doc_id / path / canonical_for / requires / topics / batch`）。
- 修改模块后运行 `python tools/m9_handbook.py refresh` 刷新 frontmatter 哈希，
  再运行 `python tools/m9_handbook.py build` 生成：
  - `docs/m9/manual/complete.generated.src.md` —— 作者版；
  - `docs/m9/manual/complete.generated.md` —— 玩家版（`⟦ bal:... ⟧` 已渲染）。
- 校验一致性：`python tools/m9_handbook.py check`。
- 按主题取上下文：`python tools/m9_handbook.py context 行动 --paths-only`。

## 规则文本写入纪律

1. 一条规则只在一个模块中定义；摘要只做导航，不复制完整规则。
2. 机制数值一律写 `⟦ bal:dotted.key ⟧`（示例带空格，实际不留空格），渲染时从 `data/balance.json` 取值；
   缺键会让 build/check 直接失败，不允许留裸数值。
3. 与 `current/` RFC 冲突时以 RFC 为准；先改 RFC/审计，再回写本手册。
4. 新模块需在 manifest 登记 `canonical_for`（每个主题唯一权威）与 `requires` 依赖。
