# M9 合同测试缺口台账 v0.1（B-2）

> **Profile**：`m9-rfc`（断言对象为 `v2exp` 现行结构时显式标注）
> **日期**：2026-08-11
> **状态**：B0–B3 审计缺口清单的测试落地台账；每个缺口项 → 关联测试或「随 B-3 迁移同步」
> **配套**：[实现就绪审计 v0.2](m9-implementation-readiness-v0.2.md)、
> `tests/test_m9_contracts_v2exp.py`

## 一、缺口清单 → 测试落地映射

| # | 缺口项（B0–B3 清单） | 今天可测的 v2exp 结构 | 关联测试 | 随 B-3 迁移同步的合同断言 |
|---|---|---|---|---|
| 1 | control T1/T2 entry（压制/石化进入行动槽） | 石化 T0 二选一：保持→`petrify_skip`；解除→清标记+`petrify_release_damage`（hp20=2） | `tests/test_m9_contracts_v2exp.py::PetrifyControlEntryTest` | `ACTION_SUPPRESSED` 压制消费槽、统一收尾 `slot_resolved`/`resolution_kind`、控制优先级 |
| 2 | acted_this_round 语义（禁止单一布尔反推槽位结果） | K 模式弃权→True、坐牢→False 的现状布尔语义 | `tests/test_m9_contracts_v2exp.py::ActedThisRoundSemanticsTest` | 逐槽位结果记录（`slot_assigned/slot_resolved/root_action_performed/.../resolution_kind`） |
| 3 | full-extra 白名单/上限/递归 | v2exp 命名布尔插队机制（hexagram/crime/savior/G2/wakeup/通用 pending） | 现状机制由既有 `test_k_initiative.py` 等覆盖；本项合同断言无对应结构 | `ActionGrant` 信封、`source_id` 白名单、每轮每人至多一个 full-extra、递归深度闸 |
| 4 | T4 两→一 | `_scissors_paper` 置 `hexagram_extra_turn=2`（两次额外回合）；`get_t0_option` 按 charges 门控 | `tests/test_m9_contracts_v2exp.py::HexagramTwoPhaseTest` | 单次 `full_extra` ActionGrant（`allow_instant=true`、`allow_public=false`），删除充能制 |
| 5 | T5 FC 时延 | T2 判定→R4 结算→`pending_extra_turns=1`（下一轮 R3 才消费）；既有 `test_t5_combo_hp20.py` 已测判定 | `tests/test_m9_contracts_v2exp.py::T5FCDelayTest` | T5 退役转 G0（DOC-024）：M9 无谱面/FC 实现 |
| 6 | G6 模板池 | v2exp 笑点积累/充能触发（`test_g5_phase2.py` 已覆盖）+ 借用实时合法行动 | 既有 `test_g5_phase2.py::`（laugh/charge） | 按大类去重模板池、消费步骤、借用核心白名单、T4 或跃重掷不授额外行动 |
| 7 | G7 单收尾 | 战术宏=单 action 入口、内部步骤逐 dispatch（现状结构已近合同） | 既有 `test_g7_hoshino_hp20.py` / `test_ai_hoshino_terror.py` | 整宏一次 `root_action_performed`+根收尾+系统收尾、`resolution_kind=wake_followup`、起床改同槽受限追演 |
| 8 | T3 SP 合法性 | `get_t0_option` 按 `uses_remaining` 门控；同地点单位存在才可发 | `tests/test_m9_contracts_v2exp.py::StarUsesGateTest` | 删除次数/充能与即演入口；仅 2 SP 公演、公演执行时读取发动者当前地点原地释放（无地点选择 UI）、施法者排除 |
| 9 | T7 死亡后持久化 | 保险挂载后 T7 本人死亡仍触发（R4 死亡检查遍历全部天赋）；兑现后全局一次 | `tests/test_m9_contracts_v2exp.py::T7DeathPersistenceTest` | 兑现后永久落幕/不可重挂状态机、SP=0、`absolute_death` 不赔付、G5 彼岸 SP=2 覆盖 |
| 10 | G1 三熵 | 每 2 轮 debuff：炽愿先抵扣、外甲优先摧毁 | `tests/test_m9_contracts_v2exp.py::FireflyDebuffOrderTest` | 失熵量表（0→cap）、三形态阶梯、形态驱动累积率、调息只休不减、R4 冻结结算序、繁育/绝对死亡 |
| 11 | G4 十二烬/挑战/响应 | `divinity` 封顶 12、死亡自动进入、`spent=True` 永久退出 | `tests/test_m9_contracts_v2exp.py::SaviorDivinityCapTest` | 余烬池/`ember_floor`、焚诏拉条（秘密承诺快照）、响应按快照先攻降序+ID、天裁 `DIRECT_DAMAGE`+`absolute_death` |

## 二、落地规则

1. 「关联测试」列：今日已落地并通过（结构未变，先行编写）；
2. 「随 B-3 迁移同步」列：需要 M9 引擎机制（`ActionGrant`、SP 0/1/2、槽位结算、
   统一收尾、`m9-rfc` profile）——在 B-3 实现对应机制时同步编写，本台账随后更新状态；
3. 新增缺口项时按本表格式追加，不删除历史行。

## 三、B-3 状态更新（2026-08-11）

B-3 机制层已在 `engine/m9/` 落地（profile: `m9-rfc`），上表「随 B-3 迁移同步」列
逐项获得机制级测试（`tests/test_m9_mechanisms.py`，29 用例）+ 冒烟
（`tools/m9_rfc_smoke.py`，8 场景 exit=0）：

| 原「随 B-3」项 | B-3 机制落点 | 机制级测试 |
|---|---|---|
| 压制/统一收尾（项 1/2） | `engine/m9/action_system.py`（SlotOutcome/resolution_kind） | `ActionSystemTest::test_slot_finalization_kinds` |
| full-extra 白名单/上限/递归（项 3/4） | `GrantLedger`（白名单/每人每轮上限/深度闸）+ `pick_full_extra_candidate` 三源仲裁 | `ActionSystemTest::test_full_extra_*` / `test_three_source_arbitration_priority` |
| T4 单 full_extra（项 4） | 三源含 `t4_hexagram_hojump`，候选整体丢弃 | 同上 |
| G6 模板池（项 6） | `engine/m9/talent_registry.py::g6_template_pool_categories`（大类白名单） | 结构登记（无独立机制测试，结算随接入层） |
| G7 单收尾（项 7） | 机制层预留（宏 Cost 表 `g7_tactical_macro_cost_table`） | 结构登记 |
| T3 2SP 公演（项 8） | `SPOTLIGHT_INDEX["T3"]=public/2` + `dispatch_public` 预检先消费 | `ActionSystemTest::test_improvise_and_public_dispatch` |
| T7 落幕/absolute_death（项 9） | `resolution.would_skip_revive` + `PPLedger.freeze` | `ResolutionTest::test_absolute_death_*` / `PPScoringTest::test_earn_freeze_decay` |
| G1 三熵（项 10） | `talents.g1_form_entropy`（量表/形态速率结构） | 结构登记 |
| G4 余烬/挑战/响应（项 11） | `talents.g4_ember_pool`（余烬/ember_floor 结构） | 结构登记 |
| G3 连续投影/赤原猎风 | `engine/m9/g3_chain.py` | `ProjectionChainTest`（5 用例） |
| G0 世界援助 | `engine/m9/g0_world_poem.py` | `WorldPoemAidTest`（4 用例） |
| PP/投注/魂援/评分 | `engine/m9/pp.py` | `PPScoringTest`（4 用例） |
| 警察/T6/掩体/停机 | `engine/m9/police.py` | `PoliceStationTest`（4 用例） |

> 注：机制层结构（纯数据/状态机）已落地可测；**接入 v2exp 现行流水线需迁移决策
> （B-4）拍板后另做**，机制层不依赖接入也能独立运行（m9_rfc_smoke 即证据）。

## 四、运行时注册边界（2026-08-11）

`engine/m9/talent_registry.py` 现为 `m9-rfc` 唯一槽位账本；所有选取、强制分配和
实例化入口必须经过该表。当前十四个活跃槽 T1/T2/T3/T4/T6/T7/G0-G7 均有独立
adapter，注册状态为 `IMPLEMENTED`；T5 标记为退役并指向 G0。各 adapter 继续只在
`m9-rfc` profile 装载，旧 `legacy`/`v2exp` 仍使用原有 14 项旧表，双管线互不改变。

M9 中编号 5 的迁移归属已交给 G0；字符串 `T5` 只解析为退役记录并明确拒绝。
统一分配入口会写入稳定的 `talent_slot_id`，后续 BasicAI/RL 不再需要按展示名或类名
猜测槽位。CI 除全量测试、smoke、剧本外，还用固定 seed 运行真实 6 人 M9 CLI。

该边界由 `tests/test_m9_talent_registry.py` 与各槽 E2E 覆盖：活跃槽完整性、十四个
可玩映射、T5 退役拒绝、旧 profile 保留，以及对局创建后 profile 冻结。

## 五、收尾状态

- B-2 今日落地：`tests/test_m9_contracts_v2exp.py` 12 个用例（T7 持久化 / T5 时延 /
  T4 两阶段 / T3 次数门控 / G1 结算序 / G4 封顶 / 石化入口 / acted_this_round 语义）；
- B-3 今日落地：`engine/m9/` 机制层 + `tests/test_m9_mechanisms.py` 29 用例 +
  `tools/m9_rfc_smoke.py`（exit=0）；v2exp profile 回归不漂（golden 冻结 + stats 92.6）；
- 第四层（2026-08-11 续）：六天赋机制完整落地 + 战斗语义完整接入——
  - 阶段 1：`engine/m9/combat.py` M9 结算路径（A/H、DIRECT_DAMAGE、absolute_dead
    分流、temp-HP 吸收、终曲区域易伤/伤害共享、m9_on_hit 协议）；
  - 阶段 2：G6 模板池（R4 记录/即演重演/公演借用或跃重掷/槽收尾接线）；
  - 阶段 3：G7 战术压制（wake_followup/Terror DIRECT_DAMAGE+absolute_dead/
    连续射击重置/R0 即演豁免）；
  - 阶段 4：G1 燃烧循环（三形态/失熵/R4 冻结序/繁育绝对死/超新星）；
  - 阶段 5：G4 救世主轮回（火种 W2/完整残缺/形态内致死消耗/负世 full_extra/
    焚诏拉条裁决）；
  - 阶段 6：G2 光影双身（影身代理槽/消散归还/终曲永久锁死/区域效果/听众 tick）；
  - 阶段 7：G5 轮回锚定（四形态/追忆封存/AnchorScript 投影器/逐槽监控/窄回溯）；
  - 阶段 8：审计 v0.2 全部 17 场景转正式测试（`tests/test_m9_audit_v02.py`）；
  - 阶段 9：剧本验收 `tools/m9_rfc_playtest.py`（6 剧本 exit=0）；
- 第五层（2026-08-11 续）：T1/T2/T3/T4/T6/T7/G3/G0 adapter 与公共运行时接入；
  十四活跃槽均通过真实 profile→setup→round→turn 的具体 adapter/标准槽收尾检查，
  空 stub 会被类型门禁拒绝；各天赋发动效果由对应 `tests/test_m9_*_e2e.py` 场景断言，
  不把两轮通用验收单独表述为“已证明十四天赋全部发动”；T5 仅保留退役记录；
- CI 门禁现包括全量 pytest、smoke、profile-aware 剧本、真实六人局、文档治理、
  handbook 一致性与 `git diff --check`；
- 全部缺口项均有「关联测试」「机制级测试」「剧本验收」或注册表拒绝理由，
  不再把 mechanism primitive 等同于可玩 adapter。
