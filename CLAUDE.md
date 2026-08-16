# CLAUDE.md

Badtime War（起闯战争）是一个基于 Python 的回合制大逃杀桌游电子裁判系统。
当前开发主线是 **M9-rfc**：独立 profile 下的单行动槽 + SP 即演/公演 + 14 天赋 +
PP/警察/剧情分体系；legacy/v2exp 规则保留且默认行为不被 M9 修改。

新 session 先读 §0–§2，再按需要跳到对应章节。规则细节以代码和测试为证据；
文档冲突查 `docs/contradictions.md`；未决设计问题直接问用户。

---

## 0. Profile：当前最重要的事实

| profile | 含义 | 默认 |
|---|---|---|
| `legacy` | v1 稳定口径 | **是**（`config/game_config.json` 设 `profile: legacy`） |
| `v2exp` | V2.0-exp 实验档案（M1–M7） | 否 |
| `m9-rfc` | **当前开发主线**：已实现、正在风洞/数值校准；未并入 legacy/v2exp | 否 |

- profile 由 `engine/experiments.py` 统一开关；`m9-rfc` 启用：
  `k_initiative, hp20, m3_accuracy, m4_gear, m5_clock, m6_scoring, m7_talents, m9_rfc, m8_ai`。
- M9 判定：`from engine.m9.gate import m9_enabled; m9_enabled(state)`。
- M9 代码全部在 `engine/m9/`；v2exp/legacy 不 import 它。改 M9 不得改变旧 profile 行为。

---

## 1. 5 分钟上手路径

1. **运行**：
   ```bash
   python main.py --profile m9-rfc
   python stats_runner.py --profile m9-rfc --players 6 --games 500
   python tools/m9_rfc_smoke.py          # 8 场景运行时烟雾，应全过
   python tools/m9_rfc_playtest.py       # 剧本验收；B.5/C/E/F 当前已知失败，见 §10
   ```
2. **规则权威**：`docs/m9/README.md` → `docs/m9/current/*.md`。
2.5. **手册装配**：作者源在 `docs/m9/manual/core/`，用
     `python tools/m9_handbook.py build` 生成作者版/玩家版，
     `python tools/m9_handbook.py check` 校验；数值写 `⟦bal:...⟧`。
3. **AI 决策接口事实**：`docs/m9/ai/talents.md` + `docs/m9/ai/slots_*.md`；
   命令侧：`docs/ai/commands.md` / `commands_choose.md` / `commands_mapping.md`。
4. **代码地图**：§3；**数值**：只改 `data/balance.json`，经 `engine.balance.get as bget` 读取。

---

## 2. Entry Points

| 命令 | 用途 |
|------|------|
| `python main.py [--profile legacy\|v2exp\|m9-rfc]` | 本地热座 |
| `python main.py --mode all_ai --players 6 --debug-level 2` | 非交互 AI 观战 |
| `python main_server.py --port 9527 --players N` | 联机房主 |
| `python main_client.py --host <IP> --port 9527` | 联机客户端 |
| `python stats_runner.py --profile m9-rfc --players 6 --games N` | M9 风洞（核心集成测试） |
| `python tools/m9_rfc_smoke.py` | M9 8 场景运行时烟雾 |
| `python tools/m9_rfc_playtest.py` | M9 六剧本验收 |
| `python -m pytest tests/ -q` | 全量测试 |

依赖：基础游戏仅需 Python 标准库；联机可选 `textual`；LLM 可选 `openai/requests`；
RL 需 `torch/stable-baselines3/sb3-contrib/gymnasium/numpy`。
`stats_runner.py` 启动时会尝试 import RL 依赖——常数启动成本，不影响每局速度。

---

## 3. 架构分层

```
engine/              — 核心循环（GameState / RoundManager / ActionTurnManager）
engine/m9/           — M9-rfc 独立机制层（action_system/combat/resolution/arc/police/talents）
engine/experiments.py — profile 与实验开关
engine/balance.py    — data/balance.json 唯一数值读取入口
combat/              — 22 步伤害流水线（M9 下由 engine/m9/combat 承接）
actions/             — 行动实现，每个模块导出 execute()
models/              — Player / Equipment / Markers / PoliceData / Virus
talents/             — BaseTalent + legacy 天赋；M9 天赋在 engine/m9/talents/
controllers/         — Human / BasicAI / RL / Network / Chorus
controllers/ai/      — BasicAI 新架构（§5）
cli/                 — parser.py / validator.py
locations/           — 地点交互
network/             — 联机 TCP + Textual TUI
rl/                  — MaskablePPO + GRU 训练管线
ai_chat/             — LLM 聊天集成
data/                — prompts.json（用户文本）
config/              — game_config/llm_config 等
docs/                — 文档；M9 入口 docs/m9/README.md
tools/               — 风洞/诊断/剧本脚本
tests/               — unittest 测试
```

---

## 4. 游戏循环（时序不可改）

每轮 `R0 → R1 → R2 → R3 → R4`；每玩家回合 `T0 → T1 → T2`：

- **R0**：天赋 `on_round_start`；M9 在此打开公演报名窗口并固化本轮唯一公演位；
- **R1**：行动权/先攻（M9 全员有标准槽，先攻只排序不淘汰）；
- **R2**：优势判定 + 警察状态推进（M9 警察在 R2 自动执法）；
- **R3**：按先攻顺序执行；T0 天赋 → T1 指令 → T2 结束；M9 直接消费 R1 创建的 `ActionGrant`；
- **R4**：天赋 `on_round_end`、胜利检查。

`GameState`（engine/game_state.py）是唯一数据源。

---

## 5. BasicAI 决策架构（当前形态）

**单一管道**：`get_command` 始终走 `DecisionOrchestrator`；旧 Mixin 只是方法库。
MRO（禁止更改）：
`HoshinoMixin → HelpersMixin → EvaluationMixin → ChooseMixin → CombatMixin → DevelopMixin → PoliceMixin → EventsMixin → PlayerController`

T1 流程：
1. `engine/action_turn` 用 `engine/action_enumerator.build_action_options` 预枚举合法动作，放进 context；
2. `BasicAIController.get_command` → `DecisionOrchestrator.generate`；
3. orchestrator 构建 `ProjectedSnapshot` → 运行 PoliceMind/ThreatMind/DevelopMind/CombatMind →
   目标/策略/目标栈 → talent hook 或 M9 slot adapter；
4. controller 用 `ActionCatalog` 复核候选——**唯一合法信源**：未命中候选被替换或剔除。

T0/choose 流程：
1. 引擎传 `talent_t0` context（含 `m9_kind`、player、game_state）；
2. `t0_policy.m9_decide_choose`（T0 发动/R0 公演报名/石化/演出方式）；
3. `c_policy.c_decide_choose`（目标/武器/借用等最小启发式）；
4. 回退 slot adapter / v2exp talent hook / ChooseMixin。

关键文件：`orchestrator.py`、`game_query.py`、`decision/`、`m9_adapters.py`、`minds/`。

性能缓存是**有意设计**，不要当冗余删除：
`experiments` 合并结果缓存、`VisibilityProxy` per-decision 可见性缓存、
orchestrator↔controller 快照复用、`_project_intents` 有界回扫、
ActionCatalog 复用引擎预枚举、`active_barrier` 带有效性校验的缓存。改前先看 §9。

---

## 6. 天赋系统

- 所有天赋继承 `BaseTalent`（talents/base_talent.py），覆盖钩子，禁止改签名：
  `on_register / on_round_start / on_round_end / on_turn_start / on_turn_end /
  get_t0_option / execute_t0 / modify_outgoing_damage / on_death_check / on_crime_check`。
- 14 个 M9 槽位：原初 T1–T4、T6–T7；神代 G0–G7（G0 取代退役 T5）。
  M9 注册表：`engine/m9/talent_registry.py`；legacy/v2exp 注册表：`engine/game_setup.py`。
- 新增 M9 天赋清单：
  1. 实现放 `engine/m9/talents/`；2. `talent_registry.py` 注册；
  3. 数值放 `m9_talents_extended.<slot>.*` 或 `m9_system.*`；
  4. `get_t0_option` 返回稳定 `m9_kind`，内部 choose 带 `situation`；
  5. 同步 `docs/m9/ai/talents.md`、`docs/m9/ai/slots_*.md`、`docs/ai/commands_choose.md`
     （有治理测试机械校验）；
  6. AI 命令逻辑加 adapter/policy，不塞进 orchestrator。

---

## 7. M9 机制速查

- **行动**：每名正常存活玩家一个标准槽；G2 普通影身是唯一代理标准槽例外。
- **SP**：`0/1/2` 能力层级；即演 −1、公演 −2。SP 是演出资源，不是货币/行动次数。
- **公演**：R0 报名，FIFO；每轮唯一公演位；队首失效不递补；T0 不得补报名。
- **完整额外行动**：白名单三源 `T4 或跃 > G5 地火 > G4 负世主动燃尽`；每人每轮至多一个。
- **伤害**：M9 下 `combat.damage_resolver` 分派到 `engine/m9/combat.resolve_damage`；
  `DIRECT_DAMAGE` 跳过属性/护甲；`absolute_death` 白名单跳过 T7/免死/形态替代。
- **评分**：PP（`engine/m9/pp.py`）、剧情分三章（`engine/m9/arc.py`）、黑马/投注。
- **警察**：`engine/m9/police.py`（固定 roster、通缉、队长、掩体、停机）。
- **天赋合同**：`docs/m9/current/`；接口事实：`docs/m9/ai/slots_*.md`。

---

## 8. 编码与修改纪律

1. 用户可见中文文本走 `data/prompts.json`；调试日志可硬编码英文。
2. 完整类型注解；中文 docstring；PEP 8，行宽 100；UTF-8 无 BOM。
3. 数值必须经 `engine/balance.py` 读取；`data/balance.json` 唯一信源。
   旧硬编码保留为 `bget(..., default=旧值)` fallback。
4. `TYPE_CHECKING` 防循环导入；优先组合；避免可变默认参数；不擅自新增第三方库。
5. **单次修改不超过 5 个文件**；禁止脚本化批量变换（正则替换/按行删除/花括号计数）。
   机械变换后必须跑运行时烟雾，不能只跑 pytest。
6. **不可触碰**：`engine/round_manager.py` 时序、`combat/damage_resolver.py` 22 步流水线、
   `engine/game_state.py` 全局状态。可以修 bug；**新增天赋交互一律走钩子**，
   不得继续向引擎内添加天赋具名分支。
7. 禁止绕过 `cli/validator.py` 直接改 GameState；禁止游戏循环阻塞操作。
8. 禁止改变 `BasicAIController` Mixin 顺序；talent 之间禁止 import 另一个 talent 模块。
9. 删除方法前 grep 全仓库确认调用者清零（含 getattr/hasattr/setattr 字符串引用）。
10. 不确定的设计问题直接问用户，不要自行猜测。

---

## 9. 性能注意（2026-09 已清债，改代码前必读）

- M9 风洞稳态约 **3.3–3.7 局/秒**，v2exp 约 4.5–5.2 局/秒（本机前台，不含启动）。
- `stats_runner` 启动 import torch/sb3 是常数成本。
- 下列缓存有依赖关系，绕过/删除会导致行为或性能回归：
  - `engine/experiments.py` `_merged_flags` 缓存（enable/disable/set_profile/reset 失效）；
  - `engine/visibility_proxy.py` per-observer conceal 缓存（T1 决策期间状态只读）；
  - `orchestrator.generate → _run_all_minds → controller` 快照复用；
  - `controllers/ai/decision/snapshot._project_intents` 有界回扫；
  - `ActionCatalog.build(prebuilt_options=context["action_options"])` 复用引擎预枚举；
  - `engine/m9/talents/g3.active_barrier` 缓存（命中校验 `barrier_active` 仍真）。
- 性能回归验收：同 seed 前台对比
  `python stats_runner.py --profile m9-rfc --players 6 --games 100` 的完成耗时；
  大幅回退先查上述缓存是否被绕过。

---

## 10. 测试要求与已知失败

- 单元测试：`python -m pytest tests/ -q`。M9 相关在 `tests/test_m9_*.py` 与 `tests/test_ai_*.py`。
- **核心集成测试（风洞）**：改 combat/talent/AI 后必须跑
  `python stats_runner.py --profile m9-rfc --players 6 --games 500`，
  确认崩溃 0、平均轮数无异常、平局原因无“引擎异常/崩溃”。
- 运行时烟雾：`python tools/m9_rfc_smoke.py`（8/8 应全过）。
- 剧本验收：`python tools/m9_rfc_playtest.py` **当前已知失败**：B.5（终曲建立轮 tick 口径）、
  C（G5 双人残局判定）、E（G2 影身 HP 新数值）、F（G6 模板窗口）——剧本脚本漂移，
  不是引擎回归；待修。CI（.github/workflows/m9-runtime.yml）在修复前不把剧本验收设为门禁。
- Windows 沙箱下临时目录不可写时，以下测试会因 PermissionError 失败（常规环境可跑）：
  `tests/test_experiments.py::test_config_file_loading`、
  `tests/test_experiments.py::test_missing_experiments_section`、
  `tests/test_ai_diagnostics.py::test_diag_report_saves_lightweight_game_outcomes`。
- 环境敏感已知项：`test_network_integration.py::TestLLMBackendFactory::test_create_backend_no_config_returns_none`
  在本地存在 `config/llm_config.json` 时会失败。

---

## 11. 关键文件索引

| 文件 | 作用 |
|------|------|
| `engine/experiments.py` | profile 开关（先查这里） |
| `engine/m9/gate.py` | M9 启用判定与机制挂载 |
| `engine/m9/action_system.py` | SP/即演/公演/ActionGrant/完整额外行动 |
| `engine/m9/combat.py` | M9 伤害结算、absolute_death、DIRECT_DAMAGE |
| `engine/m9/talent_registry.py` | M9 十四天赋注册与 slot 解析 |
| `engine/game_setup.py` | legacy/v2exp 天赋表与 AI 人格 |
| `engine/action_enumerator.py` | T1 合法动作枚举 |
| `cli/parser.py` / `cli/validator.py` | 命令解析/校验（武器名多词最长匹配） |
| `controllers/ai/orchestrator.py` | BasicAI 唯一决策管道 |
| `controllers/ai/game_query.py` | 只读查询层与评分 |
| `controllers/ai/decision/snapshot.py` | 不可变决策快照 + M9Facts |
| `controllers/ai/decision/t0_policy.py` | M9 T0 发动/R0 报名/演出方式 |
| `controllers/ai/decision/c_policy.py` | choose 目标/武器/借用等启发式 |
| `controllers/ai/decision/value.py` | 攻击效用探针（含 case_risk/exposure） |
| `controllers/ai/decision/action_catalog.py` | 命令唯一合法信源 |
| `controllers/ai/m9_adapters.py` | M9 slot adapter 分派 |
| `controllers/ai/minds/` | Police/Threat/Develop/Combat Mind |
| `controllers/ai/talents/` | 旧 talent hooks（M9 新逻辑优先 policy） |
| `docs/m9/README.md` | M9 文档唯一入口 |
| `docs/m9/ai/talents.md` | AI 策略接口事实总入口 |
| `docs/contradictions.md` | 文档/代码冲突与待决台账 |

---

## 12. 其他子系统（保留事实，不展开）

- **联机**：TCP 协议不变（`REQUEST_COMMAND/CHOOSE/CONFIRM/GAME_EVENT/CHAT/LOBBY/DISCONNECT`；
  客户端 `COMMAND_RESPONSE/CHOOSE_RESPONSE/CHAT_SEND/HEARTBEAT/RECONNECT`）；引擎同步线程 +
  asyncio 网络线程以 `threading.Event` 桥接；心跳超时 15s 后 ForfeitController 等待重连/AI_TAKEOVER。
- **AIRI/LLM**：聊天皮肤 / 独立玩家（WebSocket spark）/ bot_bridge 外部桥接；
  `[ADJUST]` 正则→JSON→`_apply_adjust()`。
- **RL**：OBS_DIM=539、ACTION_COUNT=137；修改动作/观察/奖励必须同步
  `rl/action_space.py`、`rl/obs_builder.py`、`rl/reward.py`（新增特征 `rl/feature_extractor.py`）。
- **技术债**：8 个 Mixin 仍作为方法库（只修不增）；`engine/material_deck.py` 与
  `engine/cards/` 双牌定义并存（改牌同步两处）；`docs/talents.md` 为 legacy/V2/实现混合参考。
