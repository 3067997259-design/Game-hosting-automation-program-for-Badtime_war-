# M9-RL：maskable-recurrent 的 M9 化路线图（进行中）

> **Profile 目标**：`m9-rfc` —— 让 `rl/maskable_recurrent` 在 M9 层上彻底可用并证明可学。
> **退出标准（用户 2026-08-18 指定）**：
> 1. 基于当前 maskable_recurrent 库彻底完成新的 RL **M9 化**；
> 2. 冒烟 + 大预算训练证明 RL 能学到东西（**loss 稳步下降**）；
> 3. 按本机逻辑核数提高 env 并行（当前 20 核；DummyVecEnv 受 GIL 限，建议大预算切 SubprocVecEnv）。
> **必须避免的历史教训**：legacy 早期 RL 看不到 `special` 指令 → G7 星野核心玩法无法学习。
> 本文件是差距清单 + 分阶段计划；机制权威一律以 `docs/m9/current/*.md` 为准，运行口径 `--profile m9-rfc`。

## 1. RL 必须表达的 M9 机制事实（当前实现 vs 差距）

来自 `docs/m9/README.md`（行动系统 v0.8 摘要）与 `engine/m9/action_system.py`。

| M9 机制 | 引擎事实 | 当前 RL 是否表达 | 差距 |
|---|---|---|---|
| 全员标准行动槽 | 每正常存活玩家每轮 1 槽；`ActionGrant`（kind/source/depth/restricted） | ✗（RL 动作是 legacy 的 move/interact/attack/lock/find 平铺） | 动作空间需按 grant 语义 + restricted 菜单过滤 |
| SP 分层 0/1/2 | 即演 −1 / 公演 −2；SP 是演出资源 | ✗ 观测无 SP；动作无"即演/公演"决策 | 观测加 SP；动作需 add 即演/公演/ActionGrant 分支 |
| 公演报名 | R0 报名、FIFO、队首失效不递补、T0 不得补报 | ✗ | R0/事件时点的公演报名 choose + 观测公演队列 |
| 完整额外行动白名单 | T4 或跃 > 地火诗 > G4 负世主动燃尽；每人每轮至多 1 | ✗ | 观测与 mask 需表达白名单源 |
| 天赋 14 槽 | T1–T4/T6–T7 + G0–G7（G0 取代退役 T5） | 部分（obs one-hot 是 legacy 类名布局，M9 类为 `*9` 后缀） | obs 天赋槽位按 M9 注册表重建 |
| 警察 | M9 案件驱动/固定警力/队长/掩体/停机（`m9_police`） | ✗ | obs 加警察联盟状态 |
| PP / 往世层 / 魂援 / 投注 | `m9_pp`/`m9_arc`/`m9_scoring` | ✗ | obs + 事件奖励加 PP/剧情分/援助 |
| 剧情分 | 全员 `arc_cap=3`、一章一事 | ✗ | obs + 奖励 |
| **special（历史 bug 重灾区）** | 见 §2 | ⚠️ legacy 13 槽覆盖 G7 旧 7 项；**M9 专属 special 未进入 RL 动作空间** | 动作空间必须登记/新增 M9 special 槽 |
| G0–G7 专属状态 | Hoshino9/Terror、G2 光影双身、G3 结界/连射、G5 锚定诗篇、G6 模板、G1 燃烧、G4 火种 | ✗（obs 天赋状态槽是 legacy 字段名） | obs 按 M9 类字段重建（getattr 兜底） |

## 2. special 可见性：现状核查与防失明设计

**现状**（`actions/special_op.py:get_available_specials`，引擎唯一枚举源）：
- 稳定名字：`磨刀 / 吟唱魔法护盾 / 展开AT力场 / 取消盾牌 / 修复 / 肾上腺素 / Hoshino / 释放病毒(非M9) / 破界 / 武器破界(M9)`。
- 动态模板：`蓄力<武器名>`（legacy：电磁/高斯步枪）、`更衣<水着-shielder|临战-Archer|临战-shielder>`、`拆卸<弓模块>`(M4)。
- **M9 专属**（`_append_m9_specials`，`m9-rfc` 下追加）：市民热线（T6 根行动）、队长竞选/指挥、以及后续 PP/援助/交易类延伸（见 special_op.py m9 分支）。
- legacy RL 的 `rl/action_space.SPECIAL_OPS` 13 槽已含 G7 旧 7 项（Hoshino/取消盾牌/修复/肾上腺素/更衣×3），**但未含 M9 专属特殊操作**。

**防失明规则（写进回归测试 `tests/test_rl_m9_special_visibility.py`）**：
1. 引擎当前能产生的每个 special 名，必须 ∈ RL 动作空间 `SPECIAL_OPS` **或** 显式登记在 `KNOWN_UNMAPPED_M9_SPECIALS`（本规格维护，逐项注明实现批次）；二者皆不满足 → 测试红。
2. 动态模板（蓄力<武器>/更衣<form>）以“静态槽位 + SPECIAL_REQUIRES 判定”覆盖：RL 声明槽位名 = 模板可产生的固定集合；新增可蓄力/可更衣条目必须同时登记。
3. M9 改造后，`SPECIAL_OPS` 扩展为包含 M9 special 槽，并从 `get_available_specials`（m9 分支）驱动 mask（单一信源），杜绝 RL 侧脱离引擎手写名单。

## 3. 分阶段实现计划

- **阶段 M9-A（规格 + 防失明护栏）**：
  - [x] 本规格（差距清单）；[进行中] 防失明回归测试；env 并行基线（torch-threads 抑制已验证：基线 209 步/s）。
- **阶段 M9-B（动作空间 M9 化）**：`rl/action_space` 或新增 `rl/action_m9` ——
  1. `SPECIAL_OPS` 扩为 legacy 13 + M9 专属（市民热线/破界/武器破界/队长/PP 延伸），槽位随 `get_available_specials` 单一信源；写“M9 special 可见性”冒烟（真实 m9 env 中 RL mask 含 Hoshino/破界）。**这是历史 bug 的直接护栏**
  2. 动作掩码感知 `ActionGrant`：restricted → 只给受限菜单；standard → 完整菜单；allow_instant/allow_public → 在此基础上允许/要求即演/公演；无槽/无 grant → 只能等待/报名/forfeit。
  3. `IDX` 布局重排（137 → 更宽），并同步自对弈/导出/`bc_*`/`stats_runner`。
- **阶段 M9-C（观测 M9 化）**：`rl/obs_m9`（或 obs_builder 增加 m9 分支）——在 legacy 基础上叠加
  `SP(0..2)`、当前 grant 的 kind/allow_instant/allow_public/restricted、公演队列位置、PP/剧情分/往世层指示、m9_police 状态、G0–G7 状态槽（M9 类字段 getattr 兜底）。以“布局常量 + idx 断言”驱动，避免再出现 legacy 那种字段改名炸。
- **阶段 M9-D（奖励 M9 化）**：`rl/reward_m9`——事件层加 SP 消费、公演、PP/剧情分、魂援/投注、白名单 full-extra；势函数加 M9 资源项；复用四层结构。
- **阶段 M9-E（env 接入 + 冒烟）**：`BadtimeWarEnv` 支持 `m9-rfc`（profile 由 experiments 冻结于 state）；玩具/真 m9 局冒烟（无 NaN、mask 合法、special 可见）。
- **阶段 M9-F（大预算训练）**：`SubprocVecEnv n_envs≈8 / torch-threads≈2`（20 核）；
  `python tools/phase1_legacy_compare.py ...`（或 train.py `--algo maskable-recurrent`）在 `--profile m9-rfc` 下跑 ≥50 万步，
  记录 **loss 单调/稳定下行 + win_rate 曲线**；与 legacy 基线对照。
- 每阶段沿用本轮纪律：≤5 文件/改、运行时冒烟、EvalCallback→带状态线、`rl/winrate_eval` 单一信源。

## 4. env 并行建议（回应用户）

- 已修：`phase1_legacy_compare` 默认 `--torch-threads 2`（单样本小 forward 不需要多线程），实测 baseline 209 步/s。
- 大预算换 `SubprocVecEnv(start_method="spawn")` + `--n-envs 8`（20 逻辑核；每子进程经 `BadtimeWarEnv`/`_BaseOpponentRLController` 自动限 1 线程），避免 DummyVecEnv 的 GIL 串行瓶颈。
- 验收阈值：同 seed 下与 DummyVecEnv 等效步/s 增长，且曲线/loss 一致。
