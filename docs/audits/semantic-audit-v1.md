# 初步语义审计 V1

> 日期：2026-08-05  
> 范围：建立第一轮冲突面，不自动修改玩法或实现。

## 结论

模块化迁移解决了“找不到内容”和“必须加载整本手册”的结构问题，但没有让原文自动变成现行真理。当前最主要的语义债分为四类：profile 混写、设计稿越权、用户文案陈旧和实现注释漂移。

## 已确认的跨源冲突

### 1. T4 六爻充能周期

| 来源 | 口径 |
|---|---|
| `docs/talents.md` | 每 6 轮 |
| `data/balance.json` 的 `talents.t4.charge_interval_rounds` | 5 轮 |
| `talents/t4_hexagram.py` | legacy fallback 9 轮，V2 读取 balance 为 5 轮 |
| `engine/game_setup.py` 注册简介 | 每 4 轮 |

这不是单一的“文档过时”，而是 legacy、v2exp 和陈旧用户简介四个口径混在一起。模块化手册继承 DOCX内容，不在结构迁移时自行改写。

### 2. T5 combo 注册简介

`engine/game_setup.py` 仍使用“连续行动 3 轮→奖励关”的旧简介；V2 设计草案和当前天赋实现已经经历谱面重置。该字符串属于用户可见内容，还违反了新文案应进入 `data/prompts.json` 的纪律。

### 3. AIRI 模式数量

`docs/operations/airi_bridge.md` 把接入归纳为两种：聊天皮肤和 `bot_bridge` 独立玩家；根 README 与仓库指导文档描述三种：聊天皮肤、`airi_controller` 独立玩家、外部 `bot_bridge`。需要按实际入口区分“控制器模式”和“外部桥接模式”，不能只改数字。

### 4. BasicAI 架构说明

当前 `BasicAIController` 无条件创建并调用 `DecisionOrchestrator`，旧 Mixin 只作为方法库残留。`docs/design/v2exp/m8_basicai_refactor.md` 和仓库指导基本反映这一点，但 `controllers/ai/orchestrator.py` 文件头仍声称由 `new_arch_enabled` 选择管道。

### 5. M9 与 V2 货币纪律

V2 草案规定全局可计数货币最多两种；M9 设计为信用点、SP、PP 三种，并计划吸收多个天赋资源。M9 尚未提供逐资源迁移矩阵，也没有声明正式修订 V2 宪法，因此继续维持 `m9-rfc`，不得写入 `v2exp` 手册。

### 6. G2 草案与实现边界

仓库指导文件引用 `docs/g2_reset_draft_v0.7.md`，当前工作树没有该文件；同时说明反光板、耳返、聚光合影和 StageAI 投票仍有未接线内容。必须先恢复或定位该草案，再判断模块化手册中的 G2 长篇哪些是现行规则、哪些只是设计计划。

### 7. G5 方式二

迁移基线直接标注“尚未完成信源统一的编辑”，包含大量未经 `balance.json` 占位的数值和若干未闭合句式。该模块只能保持 `candidate`，不能在缺少专项审计时转为 `canonical`。

## 当前可直接收口的非设计问题

1. 项目入口改为优先链接 `docs/README.md` 和模块化手册。
2. Orchestrator 陈旧文件头可作为纯注释修复。
3. AIRI 文档应先验证三个入口，再重写分类，不应只把“两种”替换成“三种”。
4. `engine/game_setup.py` 的陈旧简介应另开代码维护任务，不与文档结构迁移捆绑。

## 仍需用户拍板

1. M9 是否正式修订“两货币纪律”。
2. 天赋局部状态哪些可以被 SP/PP 吸收，哪些必须保留。
3. M9 援助报价方、赔率快照与被动触发作用域。
4. G2 草案中未接线机制是保留计划、删除，还是降级到扩展提案。
