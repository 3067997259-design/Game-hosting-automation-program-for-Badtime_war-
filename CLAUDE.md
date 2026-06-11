# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Badtime War（起闯战争）是一个基于 Python 3.8+ 的回合制大逃杀桌游电子裁判系统。包含 14 个天赋（7 原初 + 7 神代）、BasicAI 启发式策略引擎、RL 训练管线（MaskablePPO + GRU）、联机对战（TCP + Textual TUI）、以及 LLM/AIRI 角色聊天集成。纯后端/命令行项目，无图形前端。

核心原则：所有设计和机制理解必须基于文档描述——玩法与天赋叙述见 `docs/完全游玩手册.md`，机制数值规格见 `docs/talents.md`（以代码实现为准），不能经由名称自行猜测。如果文档之间或文档与代码冲突、或者不确定，直接问用户。

---

## Entry Points

| 命令 | 用途 |
|------|------|
| `python main.py` | 本地热座（交互式） |
| `python main.py --mode all_ai --players 6 --debug-level 2` | 非交互式 AI 观战 |
| `python main_server.py --port 9527 --players N` | 联机房主 |
| `python main_client.py --host <IP> --port 9527` | 联机客户端 |
| `python stats_runner.py --players 6 --games 500` | 自动化胜率统计（核心集成测试） |
| `python debug_runner.py` | 调试/回放工具 |

额外依赖：
- 基础游戏仅需 Python 3.8+ 标准库
- 联机模式可选安装 `textual`（`pip install textual`）
- LLM 聊天需安装 `openai` 或 `requests`（Ollama）
- RL 训练需 `torch`、`stable-baselines3`、`sb3-contrib`、`gymnasium`、`numpy`

---

## 架构分层

```
engine/         — 游戏引擎核心（GameState, RoundManager, ActionTurnManager, PoliceEngine）
                  除非明确要求，禁止修改。关键文件：game_state.py, round_manager.py, action_turn.py
                  G2 舞台子系统：ish_bosheth.py（舞台结界状态机）、material_deck.py + cards/（物料牌，
                  注意 _CARD_DEFS 与 CARD_REGISTRY 双牌定义过渡期并存，改牌需同步两处）
combat/         — 伤害结算系统（22 步流水线），禁止随意修改核心流水线
actions/        — 行动类型实现，每个模块导出 execute() 函数
models/         — 数据模型（Player, Equipment, Markers, PoliceData, Virus）
talents/        — 天赋系统，所有天赋继承 BaseTalent（talents/base_talent.py）
                  原初（T1-T7）效果较简单，神代（G1-G7）机制复杂，G 级用 Mixin 拆分逻辑
                  复杂神代有子包：g2_songs/（歌曲）、g5/（锚定+献诗 Mixin）、g7/（星野 5 个 Mixin）
controllers/    — 玩家控制器（Human, BasicAI, RL, Network, Chorus）
                  AI 使用 Mixin 组合模式，注意 MRO 顺序
                  ai/stage/ — StageAI：舞台模式（G2 结界内）Chorus 与 BasicAI 共用的静态决策模块
cli/            — 命令行解析（parser.py）与验证（validator.py）
locations/      — 地点交互逻辑
network/        — 联机网络层（TCP 协议，不可随意修改通信格式）
rl/             — 强化学习训练管线
ai_chat/        — LLM 聊天集成（可选模块）
tui/            — Textual TUI 界面（联机下使用）
data/           — prompts.json（所有面向用户的文本模板）
config/         — 配置文件（game_config.json、llm_config.json 等）
```

### 核心游戏循环

游戏按轮次推进，每轮分 R0→R1→R2→R3→R4 五个阶段；每个玩家回合分 T0→T1→T2 三个阶段：

- **R0**：轮次开始结算（天赋 `on_round_start` 钩子）
- **R1**：D4 争夺行动权
- **R2**：D6 优势判定 + 警察状态机推进（idle → reported → assembled → dispatched）
- **R3**：玩家行动回合（按行动权顺序，T0 天赋→T1 指令输入→T2 回合结束），含额外行动回合插入逻辑
- **R4**：轮次结束结算（天赋 `on_round_end` 钩子），检查胜利条件

数据所有权：`GameState`（engine/game_state.py）是唯一数据源 — 所有玩家、标记、警察状态、病毒状态、事件日志均存储于此。Player 模型在 models/player.py。

### 天赋系统

所有天赋继承 `BaseTalent`（talents/base_talent.py），通过重写以下钩子方法扩展行为：

| 钩子 | 调用时机 | 返回值 |
|------|----------|--------|
| `on_register()` | 天赋注册时（开局） | 无 |
| `on_round_start(round_num)` | R0：轮次开始 | 无 |
| `on_round_end(round_num)` | R4：轮次结束 | 无 |
| `on_turn_start(player)` | T0：回合开始 | None 正常继续；`{"consume_turn": True, "message": "..."}` 消耗本回合 |
| `on_turn_end(player, action_type)` | T2：回合结束 | 无 |
| `get_t0_option(player)` | 查询 T0 可选天赋 | `{"name": str, "description": str}` 或 None |
| `execute_t0(player)` | 执行 T0 天赋 | `(message: str, consume_turn: bool)` |
| `modify_outgoing_damage(attacker, target, weapon, base_damage)` | 伤害流水线步骤 3 | `{"damage": float, "ignore_counter": bool, "ignore_last_inner_absorb": bool}` 或 None |
| `on_death_check(player, damage_source)` | 死亡判定时 | `{"prevent_death": bool, "new_hp": float}` 或 None |
| `on_crime_check(player_id, crime_type)` | 犯罪判定时 | `{"immune": bool}` 或 `{"extra_turn": bool}` |

钩子覆盖规则：基类默认实现为空，子类可直接覆盖无需调用 `super()`。但 **禁止修改方法签名**。

天赋命名规范：`{category}{tier}_{name}`（如 `t1_one_slash`、`g7_hoshino`）。14 个天赋的完整注册表见 engine/game_setup.py 的 `TALENT_TABLE`。

### 伤害结算流水线（22 步，顺序不可变更）

1. 爱愿免疫检查 → 2. 电流免疫 → 3. 天赋修改输出伤害 → 4. 计算原始伤害 → 5. 星野架盾过滤 → 6. 警察保护阈值 → 7. 星野持盾减免 → 8. 星野被动保护 → 9. 萤火受伤减免 → 10. 选择目标护甲 → 11. 属性克制判定 → 12. 伤害量化 → 13. 扣减护甲 → 14. 全息影像额外伤害 → 15. 扣减 HP → 16. 星野色彩 10 检查 → 17. 石化解除 → 18. 眩晕/死亡判定 → 19. 电磁步枪震荡 → 20. 隐身失效 → 21. 愿负世积累火种 → 22. 破除爱愿

属性克制规则：科技 → 魔法 → 普通 → 科技。被克制则整次攻击无效。
电流免疫：陶瓷护甲（immune_electric tag）完全免疫电流武器和震荡。

天赋交互必须通过钩子方法（如 `modify_outgoing_damage`），不能直接在流水线中插入步骤。

### 护甲系统边界规则

- 外层护甲未全破时攻击内层：攻击自动导向外层，禁止直接攻击内层（`_select_armor_target`）
- 最后一件内层护甲被击破：溢出伤害转移到 HP（`_redirect_overflow_damage`）
- 护甲属性克制攻击方武器：整次攻击无效（`is_effective()` 检查）
- 溢出重定向有多件候选护甲：优先同层，随机选择不免疫武器属性的护甲

### 警察系统状态机

```
idle → reported（举报）→ assembled（集结）→ dispatched（出动）
```

威信机制：攻击无辜者 -1，队长犯罪 -1，归零则解除队长身份且原队长成为唯一违法者。

### 装备分布（EQUIPMENT_LOCATION）

警棍→警察局 | 高斯步枪→军事基地 | 魔法弹幕→魔法所 | 盾牌→商店/home | 陶瓷护甲→商店 | 魔法护盾→魔法所 | AT 力场→军事基地

### Action 模块接口

所有 actions/ 下模块的 `execute()` 函数返回格式：`{"success": bool, "message": str, ...}`。新增行动需在 `action_registry.py` 中注册，如需指令验证则在 `cli/validator.py` 中添加验证函数。

### PlayerController 接口

所有控制器实现以下方法：
- `get_command()` → 返回指令字符串
- `choose(prompt, options)` → 返回选项字符串
- `confirm(prompt)` → 返回 bool

### BasicAI 架构

**单一管道（2026-06 C7 重构后）**：旧 Mixin 瀑布流已处决，`new_arch_enabled` 标志与 `--new-arch`/`--disable-new-arch` CLI 参数均已移除。`get_command` 始终走 `DecisionOrchestrator`，事件始终走 `_on_*_new` 路径。

**决策层（DecisionOrchestrator）**：组合优于继承，通过 `GameQuery`（controllers/ai/game_query.py）作为只读查询层，`AIState` 维护 AI 内部状态，`DecisionOrchestrator` 编排 Goal/Strategy/TalentHook 的执行。所有 AI 模块通过 GameQuery 查询状态，不直接修改 GameState。

**方法库层（Mixin 遗留）**：`BasicAIController` 仍按固定 MRO 组合 8 个 Mixin：
`HoshinoMixin → HelpersMixin → EvaluationMixin → ChooseMixin → CombatMixin → DevelopMixin → PoliceMixin → EventsMixin → PlayerController`

它们**不再是决策管道**，而是新架构（talent hooks / orchestrator）反向依赖的共享方法库（`_pick_target`、`_cmd_attack`、`_count_outer_armor` 等）。**MRO 顺序禁止更改**；纯查询函数应逐步迁往 `controllers/ai/evaluation.py`（无状态模块函数）或 GameQuery。星野专属逻辑已整体迁入 `controllers/ai/talents/hoshino_impl.py`（HoshinoImpl 组合类，hoshino_mixin 仅剩单行委托）。

核心 AI 打分函数（参考）：
- 目标选择：`threat_score*2 + retaliation(+50) + location(+30) + hp_bonus((5-hp)*10) - armor_penalty(外-15, 内-10)`
- 武器选择：`damage*10 + effective(+20) - countered(-50) ± range_bonus - charge_penalty(未蓄力-500)`
- 战力估算：`hp*10 + weapons*15 + outer_armor*20 + inner*15 + stealth*10 + detection*5`

已知 AI 行为异常（修 bug 时优先关注；具体行号会随重构漂移，按描述搜索）：
1. 星野看见队长应激（controller.py / talents/hoshino_impl.py）
2. 火萤 debuff 后仍进危险模式（evaluation_mixin.py）
3. 政治人格 fallback 逻辑不完整

### 新增天赋检查清单（必须按顺序完成）

1. 创建天赋文件：`talents/tX_xxx.py` 或 `talents/gX_xxx.py`
2. 继承 `BaseTalent`，覆盖所需 hook 方法
3. 在 `engine/game_setup.py` 的 `TALENT_TABLE` 中注册
4. 如需 AI 优先选择：在 `AI_TALENT_PREFERENCE` 中添加偏好
5. 在 `data/prompts.json` 中添加 lore 文本和激活提示
6. 如需新指令：在 `actions/` 中添加并在 `action_registry.py` 中注册
7. 如需新验证：在 `cli/validator.py` 中添加验证函数
8. 如需 AI 会使用：在 `controllers/ai/talents/` 中添加 TalentHook
9. 如需 RL 支持：同步更新 `rl/action_space.py`、`rl/obs_builder.py`、`rl/reward.py`

### 联机架构

通信协议（Server ↔ Client）：
- Server→Client：`REQUEST_COMMAND` / `CHOOSE` / `CONFIRM`、`GAME_EVENT`、`CHAT_MESSAGE`、`LOBBY_UPDATE`、`DISCONNECT_NOTICE`
- Client→Server：`COMMAND_RESPONSE`、`CHOOSE_RESPONSE`、`CHAT_SEND`、`HEARTBEAT`、`RECONNECT`

同步机制：引擎在同步线程中运行，网络层使用 asyncio（独立线程），通过 `threading.Event` 桥接两个世界。

断线处理：心跳超时 15 秒（客户端每 5 秒发送），超时后切换到 ForfeitController 等待重连或 AI_TAKEOVER（优先 RL 模型，失败则 BasicAI）。

### AIRI/LLM 集成（三种模式）

1. 聊天皮肤（ai_chatter.py）：BasicAI 决策，AIRI 只负责聊天
2. 独立玩家（airi_controller.py）：AIRI 自己做决策（WebSocket spark 协议）
3. 外部桥接（bot_bridge.py）：TCP（游戏）↔ WebSocket（AIRI）独立进程

[ADJUST] 标签：正则提取→JSON 解析→_apply_adjust() 应用。威胁修正（±20）、联盟修整、攻击性修正（单次 ±10，累计 ±20）。目前无自动衰减机制。

### RL 系统

训练流程：BC 数据收集 → BC 预训练 → 权重迁移 → PPO 训练 → TorchScript 导出

Observation Space（539 维，`rl/obs_builder.py` 的 `OBS_DIM`）：基础 523 维（自身状态 22 + 武器拥有 10 + 护甲状态 7 + 对手状态 5×37=185 + 警察 15 + 自身天赋 ID one-hot 14 + 自身天赋状态 40 + 对手天赋 5×34=170 + choose 模式指示器 3 等）+ 16 维 G2 ish-bosheth 舞台全局特征。修改维度时以 `OBS_DIM` 常量为准，注意文件头注释可能滞后。

Action Space（137 维离散，`rl/action_space.py` 的 `ACTION_COUNT`）：基础 130 维（forfeit(1) + wake(1) + move(6) + interact(27) + lock(5) + find(5) + attack(50) + special(6) + police(7) + talent_t0_target(5) + talent_t0_self(1) + choose_option(16)）+ 7 维 G7 星野 special ops 扩展。

奖励函数包含 Terminal（±100/75）、Shaping（gamma*Phi(s')-Phi(s)）、Event（命中、伤害、破甲、击杀等）、Penalty（forfeit 递增、连续移动、重复行动惩罚）。

### 设计模式

- **Mixin 模式**：AI 控制器和 G 级天赋的组合方式。Mixin 类不应有 `__init__` 方法，应聚焦单一职责，避免相互依赖。
- **策略模式**：不同类型的 Controller（Human, AI, RL）
- **工厂模式**：TalentPool 管理天赋创建
- **观察者模式**：RL 环境遵循 OpenAI Gym 接口

---

## 编码规范

1. **用户可见文本必须走 `data/prompts.json`**，通过 `engine/prompt_manager.py` 获取。禁止在代码中硬编码中文面向用户的字符串。prompts.json 顶层分类：ui / game / combat / talent / system / help / debug / error。将文案设计留给用户，设计过程中不自行填充文本。
2. **调试日志可硬编码**，内部标识符使用英文。
3. **所有函数需完整类型注解**（使用 `typing` 模块）。
4. **文档字符串使用中文**。
5. **遵循 PEP 8**：4 空格缩进，最大行宽 100 字符。
6. **使用 UTF-8 编码**处理所有中文字符串。仓库内所有文本文件（含 `.gitignore`、配置、文档）必须保存为 UTF-8（无 BOM）——PowerShell 的 `Out-File`/`Set-Content`/重定向默认输出 UTF-16，曾因此损坏过 `.gitignore` 导致全部忽略规则失效；用 PowerShell 写文件必须加 `-Encoding utf8`。
7. **使用 `TYPE_CHECKING`** 避免循环导入。
8. **优先使用组合而非继承**（Mixin 场景除外）。
9. **避免可变默认参数**。
10. **配置常量统一在 `config/game_config.json` 管理**，避免硬编码。修改配置后需确保向后兼容。
11. **优先级**：优先采用已有方法，避免自创功能相同的新方法。
12. **新增第三方库前**先检查是否已在项目中使用，优先使用 Python 标准库。如需新库，先询问并说明理由。
13. **游戏数值必须经 `engine/balance.py` 读取**（`from engine.balance import get as bget`），禁止在业务代码中硬编码数值常量。`data/balance.json` 是唯一数值信源，修改数值只改此文件。渐进迁移期间，旧硬编码值保留为 `bget(..., default=旧值)` 的 fallback。

---

## 大规模重构纪律（2026-06 PR#362 教训，血泪换来的）

PR#362 的三个严重运行时回归（stats_runner 被截断成静默空跑、`_uses_new_arch_events` 误删致 70% 局崩溃、`_hoshino_shield_mode` 指向错误对象）全部来自**脚本化批量代码变换**（正则替换、按行删除、花括号计数），且 **pytest 全绿没有拦住任何一个**。因此：

1. **机械变换后必须跑运行时烟雾，不能只跑 pytest**。pytest 覆盖不到 AI 全链路；变换涉及 `controllers/`、`engine/`、`stats_runner.py` 的，提交前必须跑 `python stats_runner.py --players 6 --games 50` 并确认：崩溃数为 0、平均轮数与基线同噪声带、平局原因里没有"引擎异常"。
2. **批量替换对三类引用失明，必须单独 grep 核查**：
   - 字符串形式的属性引用：`getattr(obj, '_name')`、`hasattr(...)`、`setattr(...)` ——正则按 `self._name` 模式替换时碰不到它们；
   - 跨文件调用点：删除方法前先 `grep -rn` 全仓库确认调用者清零（注意 property、装饰器、测试 mock）；
   - 用脚本按行号/花括号删除 Python 代码段是禁手——Python 不是花括号语言，截断后往往语法依然合法、import 依然成功，错误只在运行时暴露。优先用 Edit 精确匹配文本删除，每次删除后看一遍文件尾部是否完整。

1. **不可触碰区域** — 这些文件只能通过钩子扩展，不能直接修改核心逻辑：
   - `engine/round_manager.py` — 游戏循环时序（R0-R4/T0-T2 执行顺序）
   - `combat/damage_resolver.py` — 22 步伤害结算流水线
   - `engine/game_state.py` — 全局状态管理

   现实说明：历史原因导致流水线与 `engine/action_turn.py` 中已存在天赋具名逻辑
   （G7 星野的步骤 5/7/8/16、G6 插入式笑话的 `_cutaway_*` 系列、G2 舞台分支等）。
   这些是既成事实，可以修 bug，但**新增天赋交互一律走钩子，不得继续向引擎内添加天赋具名分支**。
2. **天赋修改方式**：通过覆盖 `BaseTalent` 的钩子方法扩展行为，不能直接修改引擎核心逻辑。
3. **禁止绕过 cli.validator** 直接修改 GameState。
4. **禁止在游戏循环中使用阻塞操作**。
5. **单次修改不超过 5 个文件**。
6. **不要重写正常工作的代码**。
7. **修改代码前先阅读相关代码理解依赖关系**。
8. **保持模式一致**：
   - 新增天赋必须继承 `BaseTalent`
   - 复杂天赋优先使用 Mixin 组合，避免超长单文件
   - AI 功能扩展应添加新 Mixin 或 TalentHook 到 `controllers/ai/`，而非修改主控制器
9. **Mixin 使用注意**：Mixin 不应有 `__init__`（除非显式调用 `super().__init__()`），注意 MRO 顺序，聚焦单一职责避免相互依赖。
10. **RL 修改同步**：修改奖励函数需同步更新 `rl/reward.py`，修改动作空间需同步更新 `rl/action_space.py` 和 `rl/obs_builder.py`，新增特征需在 `rl/feature_extractor.py` 中实现。环境必须保持 OpenAI Gym 接口兼容。

---

## 当前过渡期与已知技术债（动手前先读）

1. **Mixin 退役进行中**：旧瀑布流管道已删除（C7），但 8 个 Mixin 仍作为共享方法库留在 MRO 中（新架构 hooks 反向依赖 `_pick_target`/`_cmd_attack` 等）。新功能只加到新架构（Goal/Strategy/TalentHook/evaluation.py），Mixin 只修不增；待 `_estimate_power` 等深依赖链函数迁出后再彻底移除。
2. **双牌定义并存**：`engine/material_deck.py` 的 `_CARD_DEFS`（供 build_deck/查询）与 `engine/cards/CARD_REGISTRY`（供 `_resolve_card_play()` 分派）。新增/修改物料牌必须同步两处，长期目标是统一到 CARD_REGISTRY。
3. **G2 Reset（feat/g2-reset-v0.6 分支）**：设计草案在 `docs/g2_reset_draft_v0.7.md`（草案≠实现，实现以 `docs/talents.md` 为准）。部分草案机制未完整接线（反光板/耳返安定値标记、聚光合影插入回合、StageAI 投票仍为 MVP 占位），见 `docs/changelog.md` 的"草案计划但代码未完整实装"表。
4. **文档数值漂移**：`docs/完全游玩手册.md` 与 `docs/talents.md` 与代码间存在数值不一致（例：六爻充能间隔手册写 5 轮、talents.md 写 6 轮、代码实际 9 轮）。**修改任何数值时以代码为准并同步 talents.md**；发现漂移时优先改文档而不是改代码。
5. **已知失败测试**：`tests/test_network_integration.py::TestLLMBackendFactory::test_create_backend_no_config_returns_none` 在本地存在 `config/llm_config.json` 时会失败（create_backend 回退读取了本地配置），属测试环境敏感问题，与你的改动无关。

---

## 禁止事项

1. **禁止在 talent 中直接 import 另一个 talent 模块**
2. **禁止绕过 `cli/validator.py` 直接修改 GameState**
3. **禁止修改 `BaseTalent` 的方法签名**
4. **禁止修改 `engine/` 的时序逻辑**（R0-R4/T0-T2 执行顺序）
5. **禁止改变 `BasicAIController` 的 Mixin 组合顺序**（影响 MRO）
6. **禁止在游戏循环中使用阻塞操作**
7. **禁止在非思考模式下做需要跨文件推理的设计决策**
8. **禁止硬编码用户面向的中文字符串**（必须走 `data/prompts.json`）
9. **不要删除或修改不相关的文件**
10. **不要重写正常工作的代码来"优化"它**
11. **不要改变现有的 Mixin 组合顺序**
12. **不确定的设计问题，直接问用户，不要自行猜测**

---

## 测试要求

1. 测试位于 `tests/` 目录（G2 舞台相关在 `tests/test_g2_reset/`），使用 `unittest` 框架：
   ```bash
   python -m pytest tests/ -v
   # 或单独运行
   python -m unittest tests.test_ai_combat_strategy -v
   ```
2. **核心集成测试**：每次修改 combat、talent 或 AI 逻辑后，必须运行：
   ```bash
   python stats_runner.py --players 6 --games 500
   ```
3. 调试验证：
   ```bash
   python main.py                          # 单机热座手动测试
   python main_server.py --port 9527 --players 4   # 联机测试
   ```
4. 遇到运行错误先用完整堆栈分析，再修复，不要盲目试错。
5. 添加新功能时考虑边界情况（空列表、None 值等）。
6. 调试日志级别：1=基本，2=详细，3=完整。日志输出到 `logs/` 目录，格式 `{timestamp}_{numH}H_{numAI}AI_{talent}.log`。

---

## 游戏卡住排查

1. `threading.Event` 未 set → 检查 `network/server.py` 的 `_sync_events`
2. `action_queue` 不空但所有玩家死了 → 检查 `round_manager.py`
3. 天赋额外回合无限链 → 检查 `round_manager.py` R3 阶段插入逻辑
4. 防额外回合无限循环措施：回合计数器、条件检查、状态标记。新增防循环逻辑应加在 `round_manager._phase_r3` 中。

---

## 常见 Bug 模式（开发时警惕）

1. 修改 BaseTalent 方法签名但未更新所有子类
2. 在 damage_resolver 中添加逻辑但未考虑 talent hook 优先级
3. 额外回合触发条件设置不当导致无限循环
4. 字符串编码未使用 UTF-8
5. 网络同步中 threading.Event 未正确 set 或消息丢失

---

## 关键文件索引

| 文件 | 作用 |
|------|------|
| `engine/game_setup.py` | TALENT_TABLE 注册、AI 人格表、天赋选择逻辑 |
| `engine/game_state.py` | 全局状态唯一定义 |
| `engine/round_manager.py` | 游戏循环（不可修改） |
| `engine/action_turn.py` | 玩家单回合调度 |
| `engine/prompt_manager.py` | 用户文本获取入口 |
| `combat/damage_resolver.py` | 22 步伤害流水线（不可修改） |
| `talents/base_talent.py` | 天赋钩子接口定义（不可修改签名） |
| `controllers/ai/controller.py` | BasicAI 主控制器（Orchestrator 分派 + Mixin 方法库） |
| `controllers/ai/orchestrator.py` | 决策编排器（唯一管道） |
| `controllers/ai/evaluation.py` | 无状态评估纯函数（Mixin 迁出目的地） |
| `controllers/ai/talents/hoshino_impl.py` | 星野战术 AI 实现类（原 hoshino_mixin） |
| `controllers/ai/game_query.py` | AI 只读查询层 |
| `controllers/ai/stage/stage_ai.py` | 舞台模式 AI 决策入口（Chorus/BasicAI 共用） |
| `controllers/chorus_controller.py` | Chorus 观众单位控制器 |
| `engine/ish_bosheth.py` | G2 舞台结界状态机（声部/Regard/旋律/duet） |
| `engine/material_deck.py` | 物料牌系统（注意双牌定义过渡期） |
| `data/prompts.json` | 所有面向用户的文本模板（含 g2reset/duet 命名空间） |
| `config/game_config.json` | 游戏配置（AI 禁用天赋列表等） |
| `config/llm_config.json` 等 | 本地配置，含 API 密钥，已被 .gitignore 忽略；模板在 `config/*.example.json` |
| `engine/debug_config.py` | 调试输出路由和级别控制 |
