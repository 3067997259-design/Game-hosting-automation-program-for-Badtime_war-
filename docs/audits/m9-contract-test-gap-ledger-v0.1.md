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
| 8 | T3 SP 合法性 | `get_t0_option` 按 `uses_remaining` 门控；同地点单位存在才可发 | `tests/test_m9_contracts_v2exp.py::StarUsesGateTest` | 删除次数/充能与即演入口；仅 2 SP 公演、任意地图地点、施法者排除 |
| 9 | T7 死亡后持久化 | 保险挂载后 T7 本人死亡仍触发（R4 死亡检查遍历全部天赋）；兑现后全局一次 | `tests/test_m9_contracts_v2exp.py::T7DeathPersistenceTest` | 兑现后永久落幕/不可重挂状态机、SP=0、`absolute_death` 不赔付、G5 彼岸 SP=2 覆盖 |
| 10 | G1 三熵 | 每 2 轮 debuff：炽愿先抵扣、外甲优先摧毁 | `tests/test_m9_contracts_v2exp.py::FireflyDebuffOrderTest` | 失熵量表（0→cap）、三形态阶梯、形态驱动累积率、调息只休不减、R4 冻结结算序、繁育/绝对死亡 |
| 11 | G4 十二烬/挑战/响应 | `divinity` 封顶 12、死亡自动进入、`spent=True` 永久退出 | `tests/test_m9_contracts_v2exp.py::SaviorDivinityCapTest` | 余烬池/`ember_floor`、焚诏拉条（秘密承诺快照）、响应按快照先攻降序+ID、天裁 `DIRECT_DAMAGE`+`absolute_death` |

## 二、落地规则

1. 「关联测试」列：今日已落地并通过（结构未变，先行编写）；
2. 「随 B-3 迁移同步」列：需要 M9 引擎机制（`ActionGrant`、SP 0/1/2、槽位结算、
   统一收尾、`m9-rfc` profile）——在 B-3 实现对应机制时同步编写，本台账随后更新状态；
3. 新增缺口项时按本表格式追加，不删除历史行。

## 三、收尾状态

- B-2 今日落地：`tests/test_m9_contracts_v2exp.py` 12 个用例（T7 持久化 / T5 时延 /
  T4 两阶段 / T3 次数门控 / G1 结算序 / G4 封顶 / 石化入口 / acted_this_round 语义）；
- 其余合同断言全部标注「随 B-3 迁移同步」，无遗漏、无未标注项。
