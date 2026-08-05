# 文档矛盾与待决事项

本台账只登记，不自动修改规则。状态使用：`open`、`decision-needed`、`planned`、`resolved`、`false-positive`。

| ID | 主题 | Profile | 证据与冲突 | 当前处理 | 状态 |
|---|---|---|---|---|---|
| DOC-001 | V2 作者源分叉 | v2exp | 迁移 DOCX 与旧单体 Markdown 只有约 55% 文本相似度，且 DOCX包含大量 Markdown 中不存在的内容 | 已从 DOCX逻辑文本迁移 34 个模块；模块成为唯一作者源，DOCX 与旧 Markdown 移至 `docs/archive/` | resolved |
| DOC-002 | 两货币与三货币 | v2exp / m9-rfc | V2 草案 §0.6 规定最多两种全局货币；M9 原稿提议信用点、SP、PP 三种 | 用户已接受行动 RFC v0.5 的口径：SP 是不可交易、不可计分、不可兑换的 0/1/2 就绪状态，不计入全局货币；全局货币仍为信用点与 PP | resolved |
| DOC-003 | 天赋权威混合 | legacy / v2exp | `talents.md`、V2 手册、设计草案和代码对部分天赋存在不同口径 | 先按天赋拆分玩家规则，再逐项建立文档/代码/决定对照 | open |
| DOC-004 | M8 状态漂移 | v2exp | M8 文档仍包含计划态描述，当前 BasicAI 已统一走 Orchestrator | 将计划历史与当前架构说明分离 | open |
| DOC-005 | AIRI 模式数量 | cross-profile | 部分说明按两种模式描述，仓库指导文件描述三种模式 | 对照当前入口和配置后更新集成文档 | open |
| DOC-006 | G2 设计稿引用 | v2exp | 指导文件引用 G2 reset 草案和未完整实装表，但当前工作树未见对应正式草案文件 | 核查被删除/分支文件与当前代码，避免丢失独有设计 | open |
| DOC-007 | 项目入口手册链接 | legacy / v2exp | 根 README 的手册入口没有清晰区分 legacy 与 v2exp | 根 README 与代理指导已改为先进入文档中心，并区分 legacy/V2 | resolved |
| DOC-008 | M9 内部协议矛盾 | m9-rfc | 赔率是否考虑强度/市场、援助报价方、被动触发作用域和“下一步”描述不一致 | 保留原文，进入 M9 专项设计决定 | decision-needed |
| DOC-009 | lint flag 误报 | cross-profile | CHECK 5 曾把决策地图中的文件名与 balance 键当成 flag | 已按精确文件+指纹加入白名单，保留真实 flag 检查 | resolved |
| DOC-010 | 单体手册过大 | v2exp | 约 1090 行、5 万字符；天赋章占 60% 以上，G2 单项约 1 万字符 | 已拆为 34 个模块并支持按主题装配；语义审计仍继续 | resolved |
| DOC-011 | 权威源未跟踪 | v2exp / m9-rfc | V2 DOCX、M9、决策地图和 lint 工具当前均未被 Git 跟踪 | 本轮保留并纳入维护交付范围；是否提交由后续 Git 流程处理 | open |
| DOC-012 | 生成链分裂 | v2exp | `lint_docs.py` 默认扫描根 DOCX，`render_docs.py` 只装配 `docs/*.src.md` | 新 `tools/handbook.py` 已能装配、注入 balance 并检查；旧链暂留作历史兼容 | resolved |
| DOC-013 | 天赋注册简介陈旧 | legacy / v2exp | `engine/game_setup.py` 仍称 T4 每 4 轮充能、T5 连续行动奖励；balance 与 V2 规则已不同 | 用户可见简介应迁入 prompts，并按 profile 生成 | open |
| DOC-014 | Orchestrator 文件头陈旧 | cross-profile | `controllers/ai/orchestrator.py` 文件头仍称通过 `new_arch_enabled` 切换，但该开关已退役 | 修正文档字符串，不改变运行逻辑 | open |
| DOC-015 | G5 方式二未收口 | v2exp | 迁移源自身标注“尚未完成信源统一的编辑”，且存在大量裸数值 | 在 G5 专项审计前保持 candidate | open |
| DOC-016 | K 行动配额与 SP 替代 | v2exp / m9-rfc | V2 当前 K=N−1，每轮稳定一人轮空；M9 原稿把 SP 叠在行动消耗上，可能形成双重行动门槛 | 设计决定已落入 v0.5：删除 K 配额，全员每轮一个标准行动槽；先攻只排序；SP 只决定天赋就绪，并允许即演或自愿公演。重建模型和原型通过后再计划迁入 v2exp | planned |
| DOC-017 | 完整额外行动的定位 | m9-rfc | v0.4 主要以追演替代额外行动，可能实质删除 T4/T5 的最高档奖励；用户要求像无视属性克制一样保留稀有规则豁免 | v0.5 建立明文白名单和防递归边界；首批来源为 T4「或跃在渊」与 T5 FC，同一玩家每轮至多一个且不得再次公演 | resolved |

## 处理顺序

1. DOC-007、DOC-009、DOC-011：收口入口与持续检查。
2. DOC-003、DOC-008：仍需要设计判断，结构完成后逐主题拍板。
3. DOC-004、DOC-005、DOC-006、DOC-014：独立进行架构与集成文档审计。
4. DOC-013、DOC-015：纳入天赋逐项审计。
5. DOC-016：按 v0.5 重建并发模型与可玩原型，验证后再讨论迁入 `v2exp`。
