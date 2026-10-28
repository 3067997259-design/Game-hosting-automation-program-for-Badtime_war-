# AGENTS.md — 起闯战争（Badtime War）开发约定

本文件是所有编码代理共用的唯一项目约定（Claude Code、Codex、OpenCode、CherryStudio agent）。
`CLAUDE.md` 通过 `@AGENTS.md` 引入本文件，只补充 Claude 专属事项；规则改这里，不要两处各写一份。
最后核对：2026-10-28。事实以代码、测试和下列权威文档为准；本文件与它们冲突时，以它们为准并顺手修正本文件。

---

## 0. 现在在做什么

**主线：V2.0-GA 迁移。** 用一套 GA 规则替换 legacy / v2exp / m9-rfc 三套旧规则。
用户已授权破坏性更新：旧规则、旧测试、旧动作/观察空间可以重写或退役，不承担继续可玩的兼容义务。

| 阶段 | 内容 | 状态 |
|---|---|---|
| I0–I1 | GA 参数装配、公共合同、无天赋完整对局 | 完成 |
| I2–I3 | 警察、往世层、十四天赋、十四诗篇 | 完成（`tests/test_ga_*.py` 全过；固定矩阵 60/60） |
| I4 | CLI / BasicAI / RL 的 GA 动作、观察、奖励与模型语义版本 | **下一步** |
| I5 | 默认入口一次切到 GA，退役旧 profile 与旧分派 | 待做 |

- **现在的正式入口仍跑旧规则**：`main.py`、`stats_runner.py`、联机、RL 默认 `legacy`，M9 需 `--profile m9-rfc`。
  GA 目前只能用 `tools/ga_play.py` 运行。
- **玩法权威**：[GA 文档中心](docs/v2.0-ga/README.md) 的 DEC-001～050 与各 RFC；
  **阶段与验收**：[实施合同](docs/v2.0-ga/implementation_contract_v0.1.md)；
  **数值**：`data/balance.json` 的 `ga` 块（N0 初值，对应[玩家手册](docs/v2.0-ga/manual_draft_v0.1.md) §17）；
  **运行事实**：`engine/ga/` 与 `tests/test_ga_*.py`。
- GA 的代码、测试、文档和迁移/手册产物在 `feat/v2.0-exp` 分支上，2026-10-28 起进 git。
  这台电脑存储紧张，每完成一批工作就提醒用户提交；手册审阅快照 `artifacts/ga-manual/snapshots/` 跟手册一起提交。

---

## 1. 常用命令

```bash
# GA（主线）
python -B -m pytest -p no:cacheprovider tests/test_ga_*.py -q          # 约 20 秒
python -B tools/ga_play.py --players 6 --seed 2 --talents G2,G3,G5,G6,G7,G1 --output <out.json>
python -B tools/ga_play.py --players 2 --talents T4,T6 --human p0      # 人工调试台
python -B tools/ga_play.py --players 6 --seed 0 --script <choices.json> # 按脚本重放
python -B tools/ga_matrix.py                                            # 固定矩阵：2/4/6 人 × seed 0–19，约 35 秒，只打印汇总

# 旧运行层（I5 前维护用）
python main.py [--profile legacy|v2exp|m9-rfc]
python stats_runner.py --profile m9-rfc --players 6 --games 500         # M9 风洞
python tools/m9_rfc_smoke.py                                            # M9 烟雾，8/8 应全过
python -m pytest tests/ -q                                              # 全量测试

# 文档
python tools/check_doc_governance.py                                    # 登记文档的链接与跟踪状态
python tools/handbook.py check                                          # v2exp 模块化手册
python -B tools/manual_review.py snapshot <标签> --note <说明>           # GA 手册：交稿时存基线
python -B tools/manual_review.py diff <标签> --sections <章节>           # GA 手册：作者改稿后的审阅报告
```

Windows 下系统临时目录不可写时，pytest 加 `--basetemp <可写目录>`。
`tools/ga_play.py` 的输出含 `replay_inputs`，可直接存成 `--script` 文件做最小复现。

---

## 2. 代码地图

### GA（新规则，全部在 `engine/ga/`）

| 文件 | 作用 |
|---|---|
| `config.py` | 严格读取 `balance.json` 的 `ga` 块：缺键、错类型、未知键直接报错，没有默认值 |
| `contracts.py` / `events.py` | 公共值对象（ActionRequest/Plan、EffectSpec、ResolutionRecord、TimedEffect、ChoiceWindow、TerminalSnapshot）与事件/奖励账本 |
| `actions.py` | 纯预检：不扣费、不抽随机数、不写状态 |
| `gameplay.py` | 所有控制者的共同入口 `build_options → validate_request → execute_action`，五地点服务 |
| `combat.py` | GA 原生伤害解析（单甲耐久计费、屏障、擦伤、保底），不走旧 22 步流水线 |
| `runtime.py` | R0–R4 / T0–T2 调度、公演夺台与递补、追击、额外回合 |
| `state.py` / `status.py` / `world.py` | 挂在 `GameState.new_ga()` 上的玩家与账目、普通状态与计时、世界时钟与全局法则 |
| `police.py` / `afterlife.py` / `poems.py` | 防暴编队、往世层三按钮、十四诗篇接受链 |
| `extensions.py` | 通用扩展点：濒死替代、选择窗口、章节、地点摧毁 |
| `talents/` | `base.py` 公共工具与 `Talent` 基类；T1–T4、T6、T7、G0–G7 各一个文件（T5 已退役） |
| `setup.py` | 唯一装配入口：安装警察、往世层和所选天赋 |
| `controllers.py` | 最小合法策略、人工台、脚本重放；不是 BasicAI |

### 旧运行层（I5 退役前仍在默认入口上）

`engine/`（GameState、RoundManager、ActionTurnManager、experiments 档案开关）、`engine/m9/`（M9 机制层）、
`combat/`（22 步流水线）、`actions/`、`cli/`（parser/validator）、`controllers/`（BasicAI 等）、`talents/`、
`locations/`、`models/`、`rl/`、`network/`、`ai_chat/`、`data/prompts.json`。详见 §7。

---

## 3. 开发流程

这一节取代早期的“每批最多五个文件”“每批保存原件与收据”等规定。

1. **开工**：先 `git status`，认清哪些是用户未提交的改动；按任务读对应权威文档，不通读整个 `docs/`。
2. **规划**：小改直接做。跨模块、改公共接口或会改变玩法结果的改动，先用几行说清“改什么、怎么验证”再动手。
3. **实施**：一次改动对应一个能验证的目的，文件数不设上限。不顺手重构无关代码，需要的话单独提出。
   机械批量改动可以用脚本，但必须看 diff 并跑对应测试。
4. **验证**（按影响面选，不要只跑 `py_compile`）：

   | 改了什么 | 至少跑 |
   |---|---|
   | 单个 GA 天赋或地点服务 | 对应 `tests/test_ga_*.py` + 一局含该天赋的 `ga_play` |
   | GA 调度、伤害、状态、事件、配置 | 全部 `tests/test_ga_*.py` + 固定矩阵 |
   | 旧运行层的战斗、天赋、AI | 相关旧测试 + `m9_rfc_smoke.py`；改动大时跑 500 局风洞 |
   | RL 动作 / 观察 | `tests/test_rl_action_coverage.py` 及相关 RL 测试 |
   | 文档 | 链接有效；改了登记文档就跑 `check_doc_governance.py` |

5. **汇报**：改了什么、跑了什么（命令与结果）、没覆盖什么、哪些需要用户拍板。失败照实写。
   只有阶段性结论才写进实施合同 §7，写几行摘要即可，不另存收据、原件或哈希清单。
6. **提交**：git 是唯一的回滚手段。用户要求时才提交或推送；完成一个阶段时提醒用户提交。
   禁止 `reset --hard`、`clean`、强推、覆盖式 `checkout`，也不要用 `--no-verify` 绕过 hook。

**不能动的东西**：用户未提交的改动；`checkpoints/`、`pretrained/`、`bc_data/`、`windtunnel_archive/`、
`logs/`、`artifacts/` 里的模型与实验产物（只读，不删，不自动训练）；用户亲手改写过的文档段落（见 §5）。

**什么时候问用户**：会改变玩家选择、结算结果或已决 DEC 的问题，先问，一次问清并给出推荐。
命名、内部接口、测试夹具、文件组织、删除已退役代码，自己决定并在汇报里说明。

---

## 4. GA 实现原则

- `GameState` 是唯一状态源（GA 用 `GameState.new_ga(..., talents={actor: slot})` 创建）。
  生产代码不得绕过 `validate_request / execute_action` 直接改状态；测试夹具可以直接构造初始状态。
- **先预检后付费**：预检失败不收费、不抽随机数；合法发动后被闪避或拦截不退款；复合行动已结算的段不回滚。
- 天赋交互走钩子与效果描述（`EffectSpec`、`IncomingModifier`、`DeathGate` 等），不在核心调度或伤害代码里加具名角色分支。
  天赋模块之间不互相 import。
- 数值只经 `engine/ga/config.py` 读取。新增参数要同时改 `balance.json` 的 `ga` 块和 `config.py` 的参数规格
  （来源、单位、范围），`test_ga_config.py` 会校验。不得用旧 `bget(default=...)` 冒充 GA 数值。
- GA 缺能力时不得回退调用 M9 或旧天赋，不新增第四个长期 profile。
- 时序语义保持 `R0→R1→R2→R3→R4` 与 `T0→T1→T2`，实现可以重写。
- 事件与奖励幂等：重复回调不重复发奖、复活或终局；同 seed 必须可完全重放。
- 新的玩法决议登记为 DEC-xxx，写进 [GA 文档中心](docs/v2.0-ga/README.md)，并同步受影响的 RFC 与手册章节。

---

## 5. 文档约定

- **单一信源**：一条规则只在一个地方定义，其他地方链接过去；数字不手抄，以 `balance.json` 为准。
  v2exp 文档用 `⟦bal:键名⟧` 占位，经 `tools/render_docs.py` / `tools/handbook.py` 渲染。
- **GA 玩家手册**：[manual_draft_v0.1.md](docs/v2.0-ga/manual_draft_v0.1.md)。正在重写，
  方法、文风规范与进度见[重写计划](docs/v2.0-ga/manual_rewrite_plan.md)。
- **分工**：规则正文由助手写；元叙事（前言、台词、玩笑吐槽、天赋选择建议）与开发日记 `develop_diary` 由用户自己写、
  自己插入，助手不代写，也不留占位标记；能在仓库历史里核实的“早期版本”事实旁白和 changelog 由助手起草。
- 用户亲手改写过的段落带有作者文风，不要做风格上的润色。发现事实错误或笔误，直接在原文做最小改动更正
  （保留作者的语气和吐槽），并在汇报里逐条列出改了什么；会改变玩法的地方先问。
- 天赋章节的合作循环：助手写平实版并存快照 → 作者大改（改名、新机制）→ 助手用 `tools/manual_review.py diff`
  对比，同步改名、逐条确认数字和规则变化。详见[重写计划](docs/v2.0-ga/manual_rewrite_plan.md)第十一节。
  写手册前先核对文件散列或对应章节没被作者改过，不和作者同时改同一个文件。
- 冲突登记：GA 内部冲突与待决项写在 GA 文档中心的 DEC/OPEN 表；旧层冲突写在 `docs/contradictions.md`。
- 文档身份登记在 `docs/document_registry.json`；本文件与 `CLAUDE.md` 已登记，里面的本地链接必须有效。

---

## 6. 编码约定

- Python 3.13（部分旧代码声明 3.8+ 兼容，改动时不要无故破坏）。完整类型注解，中文 docstring，PEP 8，行宽 100，
  UTF-8 无 BOM，LF 换行。
- 不擅自新增第三方依赖。基础游戏只用标准库；联机（`textual`）、LLM（`openai/requests`）、
  RL（`torch/stable-baselines3/sb3-contrib/gymnasium/numpy`）是可选依赖。
- 旧运行层玩家可见的中文文本走 `data/prompts.json`；GA 的界面文本在 I4 一并定。调试日志可以写英文。
- 删除函数或方法前全仓搜索调用者，包括 `getattr/hasattr/setattr` 等字符串引用。
- 不要删除有意为之的性能缓存（见 §7）。

---

## 7. 旧运行层须知（I5 前仍在维护）

- **Profile**：`legacy`（默认，`config/game_config.json`）/ `v2exp` / `m9-rfc`，由 `engine/experiments.py` 统一开关；
  M9 判定用 `engine.m9.gate.m9_enabled(state)`。改 M9 不得改变 legacy/v2exp 行为。
  M9 规则入口：[docs/m9/README.md](docs/m9/README.md)。
- **时序与伤害**：`engine/round_manager.py` 时序、`combat/damage_resolver.py` 22 步流水线（M9 下分派到
  `engine/m9/combat.py`）、`engine/game_state.py` 是旧层核心。I5 前只修 bug，不在里面加新玩法。
- **BasicAI**：`get_command` 只走 `controllers/ai/orchestrator.py` 的 `DecisionOrchestrator`；`ActionCatalog` 是命令的唯一合法信源。
  Mixin 顺序不可改：`HoshinoMixin → HelpersMixin → EvaluationMixin → ChooseMixin → CombatMixin → DevelopMixin →
  PoliceMixin → EventsMixin → PlayerController`。
- **有意保留的性能缓存**：`experiments._merged_flags`、`visibility_proxy` 的每观察者缓存、orchestrator 与 controller
  之间的快照复用、`snapshot._project_intents` 有界回扫、`ActionCatalog.build(prebuilt_options=...)`、
  `engine/m9/talents/g3.active_barrier`。M9 风洞稳态约 3.3–3.7 局/秒，明显变慢先查这些缓存。
- **RL**：legacy/v2exp 观察 539 维、动作 137；M9 观察见 `rl/obs_builder_m9.py`，动作数由 `rl_action_count()` 决定。
  改动作或观察要同步 `rl/action_space.py`、`rl/obs_builder*.py`、`rl/reward*.py`，并跑 `tests/test_rl_action_coverage.py`
  （双向校验：引擎能执行的动作 RL 必须发得出，mask 点亮的动作 validator 必须接受）。
  课程与实验记录见 `docs/m9/rl/`。GA 的动作与观察在 I4 重新定义，旧 checkpoint 不能加载到 GA。
- **联机**：TCP 协议与心跳/重连机制见 `network/`；引擎同步线程与 asyncio 网络线程用 `threading.Event` 桥接，
  游戏循环里不要加阻塞操作。
- **已知失败**（不是回归）：
  - `tools/m9_rfc_playtest.py` 的 B.5 / C / E / F 是剧本脚本漂移，CI 不把它设为门禁；
  - Windows 临时目录不可写时，`test_experiments.py::test_config_file_loading`、
    `test_experiments.py::test_missing_experiments_section`、
    `test_ai_diagnostics.py::test_diag_report_saves_lightweight_game_outcomes` 会因 PermissionError 失败；
  - 本地存在 `config/llm_config.json` 时，`test_network_integration.py::TestLLMBackendFactory::test_create_backend_no_config_returns_none` 会失败。
- **技术债**：8 个 Mixin 只修不增；`engine/material_deck.py` 与 `engine/cards/` 两份牌定义并存，改牌要同步两处。

---

## 8. 文档入口速查

| 要找什么 | 去哪里 |
|---|---|
| GA 规则、决议、待决项 | [docs/v2.0-ga/README.md](docs/v2.0-ga/README.md) |
| GA 实施阶段、验收场景 | [实施合同](docs/v2.0-ga/implementation_contract_v0.1.md) |
| GA 玩家手册与重写计划 | [手册](docs/v2.0-ga/manual_draft_v0.1.md)、[重写计划](docs/v2.0-ga/manual_rewrite_plan.md) |
| 全部文档的身份与阅读路径 | [docs/README.md](docs/README.md) |
| 旧层文档冲突 | [docs/contradictions.md](docs/contradictions.md) |
| M9 / v2exp 规则 | [docs/m9/README.md](docs/m9/README.md)、[docs/handbook/README.md](docs/handbook/README.md) |
