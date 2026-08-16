# M9 通用剧情分（arc）：三章制完结条与登台优先 RFC v0.1

> **当前 M9 剧情分候选。** 总览与阅读路径见 [`../README.md`](../README.md)。要查询现在实际
> 游玩的规则，请查看 [`../../handbook/README.md`](../../handbook/README.md)，不要把本文当成已实装手册。
>
> **日期**：2026-08-14
> **状态**：用户批准方向下的设计候选；三章制、一章一事、顺序解锁与登台优先已冻结为公共规则
>
> **2026-09 风洞校准修正（实施口径）**：
> 1. `arc_weight` 当前终值为 **1.8**（§二.4 的 1.5 为起草值）；
> 2. T3 天星的高光/谢幕章改为**隔次记章**：本人每两次「命中 ≥2 的星落」记一次第二章，
>    星落击杀的第三章同样交替授予；G6 借用星落不受影响；
> 3. G2 影身击杀（killer=`G2:shadow@<pid>`）授予第二章；发生在登台前则登台后补授。
> 终值与溯源见 [`m9_windtunnel_calibration_2026-09.md`](m9_windtunnel_calibration_2026-09.md)。
> **Profile**：`m9-rfc`
> **上游**：[行动系统 RFC v0.8](m9_action_system_rfc_v0.8.md)、
> [结算合同 RFC v0.3](m9_resolution_contract_rfc_v0.3.md)、
> [天赋演出接口 RFC v0.5](m9_talent_spotlight_rfc_v0.5.md)、
> [PP/往世层投注与魂援 RFC v0.4](m9_pp_afterlife_betting_rfc_v0.4.md)、
> [评分系统指针与延伸 RFC v0.1](m9_pp_afterlife_scoring_rfc_v0.1.md)
> **取代/承接**：风洞 R48 裁决（`windtunnel_report.md`）把 `arc_weight` 暂记 0 的过渡状态，
> 以及评分指针 v0.1 §五“只有 G2/G5 有剧情分挂接”的旧口径。
> **实现**：`engine/m9/arc.py`（ChapterRegistry / ChapterLedger）、`engine/m9/action_system.py`
> （登台优先 + 章节扫描接线）、`engine/m9/pp.py`（arc 上限）、`engine/m9/gate.py`（挂载）。
> **不改写**：`v2exp` 当前 V2.0 玩家手册与旧 `finale.*` 完结条。

---

## 一、动机与结论

风洞数据（`windtunnel_report.md:310-314`）证明了两件事：

1. 旧剧情分通道只有 G2（终曲被听见）与 G5（水晶花/双锚）有入口，`arc_weight` 是非对称补贴；
2. “arc += 公演次数”不可行：公演次数是频率维度，由各槽位的 SP 身份结构性决定
   （T3 无即演入口、每局需多次公演；T7 公演与即演等价、每局一次即够）。用次数计价，
   等于让 T3 的考勤决定剧情分，T7 永远缺席。

本稿的规则命题是：

> **剧情分计量的是“角色完成的故事节拍”，不是登台次数。每个槽位每局有相同的三个
> 故事节拍（一章一事、按序解锁、上限 3）；第一章由“第一次真实公演”通用获得，
> 第二/三章由槽位专属高光与谢幕事件获得。**

公演次数从此不进入任何计分维度；重复公演的价值只来自其自身效果、战果分、关注与 PP。

---

## 二、三章制公共规则（冻结）

1. **章节上限**：每名玩家每局 `arc_cap = 3`，超过上限的章节事件只记录事实，不追加 arc。
2. **一章一事**：一次事件最多授予一个章节；同一事件不得同时点亮两章。
3. **顺序解锁**：第一章 → 第二章 → 第三章，必须按序；前一章未解锁时，后章事件不生效。
4. **章节不分大小**：所有章节的 arc 增量统一为 `arc_count +1`，乘统一
   `m9_system.scoring_m9.arc_weight`（恢复为 1.5，风洞可调）。
5. **完结条进展 PP**：每点亮一章，按 B4 §3.2 授予一次
   `m9_system.pp.arc_progress`（暂定 +1 PP）；绝对死亡冻结者不获得（沿用 `PPLedger` 纪律）。
6. **G2/G5 私有挂接退役**：`g2_last_song_heard` 与 G5 水晶花/双锚不再是独立评分入口，
   而是本稿第三章/第二章的事件来源之一，统一经 `ChapterLedger` 计分。

---

## 三、第一章·登台（通用公演动力）

- **条件**：玩家本局第一次完成**真实公演**——`SlotOutcome.performance_performed=true`
  且 `ActionSystem.performance_kind == "public"`（预检通过、SP/公演位已消费、真实结算）。
- **排除**：预检失败、被压制/石化/控制替代、公演位失效、只派发未结算的公演均不点亮。
- **效果**：`arc +1`、`PP +arc_progress`。
- **设计意图**：给全部 14 个槽位一个频率无关的登台理由。T7 的唯一一次公演与 T3 的
  第一次公演同价；T3 之后再演不再增加剧情分。

### 3.1 登台优先（公演队列规则，冻结）

`allocate_public_slot` 在完成常规资格清理后，按下述顺序选择本轮公演位：

```text
1. 队列中仍未点亮第一章·登台的合格候选（按原 FIFO 序）；
2. 若不存在，队列中最靠前的合格候选（原 FIFO 序）。
```

即“人生第一场公演”插队到高频天赋的后续公演之前；登台章完成后恢复纯 FIFO。
队首失效不递补、赤原猎风永久移除等 v0.8 §6.2 纪律不变。

---

## 四、第二/三章：十四槽位章节表（用户已接受草案，数值待风洞）

> 实现第一版的事件谓词与草案表述存在两处等价的工程近似，见 §六“开放项”。

| 槽位 | 第二章·高光 | 第三章·谢幕 |
|---|---|---|
| T1 一刀缭断 | 一次核心斩击完成击杀（death `source_kind=t1_core_slash`） | 在自己完成真实公演的同一轮内，核心斩击完成击杀 |
| T2 剪刀手 | 追猎反应当轮以核心攻击完成击杀（death `source_kind=t2_core_attack`，同轮存在 `t2_hunt_reaction`） | 在自己完成真实公演的同一轮内，核心攻击完成击杀 |
| T3 天星 | 一次天星公演命中 ≥2 名合法单位（`star_attack.hits>=2`） | 一次天星击杀至少一名玩家（death `source_kind=t3_starfall`） |
| T4 六爻 | 触发一次「或跃在渊」（`hexagram_cast` 掷出剪刀对布） | 在「或跃」授予的完整额外行动内完成击杀（death 位于同轮 full_extra 标记） |
| T6 好市民 | 当选队长（`m9_captain`）或建立通缉（`hotline`） | 队长任内警力执法完成击杀（`m9_police_enforcement` 同轮死亡） |
| T7 苏生 | 保险兑现（`resurrection_trigger`） | 复活之后完成一次击杀（death killer=自己，round > 复活轮） |
| G0 白子 | 遗物支援技成功结算（`g0_relic_effect`） | 十字炮火公演完成击杀（death `source_kind=g0_crossfire`） |
| G1 火萤 | 一次超新星命中 ≥2 名单位（`firefly_supernova.hits>=2`） | 进入繁育状态（`g1_propagation_death` 或由其 `location_destroyed`） |
| G2 注视 | 影身完成首次击杀（death killer=`G2:shadow@<pid>`） | 终曲被听见（`g2_last_song_heard`，承接原 `terminal_arc_count`） |
| G3 神话 | 固有结界存续期间击败被捕捉单位（death killer=自己，位于 expand→collapse 窗口内） | 完成幻想崩坏（`m9_g3_collapse` terminal） |
| G4 愿负世 | 进入救世主形态并存活至自然结束（`g4_savior_enter`→`g4_savior_exit`） | 完成焚诏拉条公演（`g4_judgment_completed`） |
| G5 涟漪 | 首次未来闭合获得水晶花（`crystal_flower`） | 第二次未来闭合（`g5_double_closure`） |
| G6 笑声 | 一次公演借用核心成功结算（`g6_borrow_core`） | 借用核心当轮完成击杀 |
| G7 星野 | 完成一次击杀（第一版近似：战术宏内部击杀需战斗来源标识，见 §六） | 色彩反转进入 Terror（扫描期 `talent.is_terror=true`） |

各章为**每局一次**的布尔事件；重复触发只记录事实、不重复计分（上限与一章一事双重保证）。

---

## 五、数值与实现接口

```jsonc
// data/balance.json
"m9_system": {
  "scoring_m9": {
    "arc_weight": 1.5,   // 由 0 恢复；风洞可调
    "arc_cap": 3         // 全员统一章节上限
  },
  "pp": {
    "arc_progress": 1    // 每点亮一章授予的 PP（沿用现有键）
  }
}
```

实现接口（与现有 profile 纪律一致，`v2exp` 不 import 本模块）：

- **`engine/m9/arc.py`**：
  - `ChapterRegistry`：表驱动登记 14 槽位 × 3 章的事件谓词（纯函数，按
    `(event_list_slice, state, pid)` 判定）；
  - `ChapterLedger`：维护 `chapters[pid]`、`public_rounds[pid]`、
    `full_extra_rounds[pid]`、`revive_round[pid]` 与事件扫描游标；提供
    `on_public_performance / mark_full_extra_round / scan / has_debut / grant`。
  - 授予时写 `game_state.m9_scoring.add_arc(pid, 1)` 与 `m9_pp.earn(pid, arc_progress)`。
- **`engine/m9/action_system.py`**：
  - `allocate_public_slot`：按 §3.1 登台优先选择；
  - `resolve_slot`：`performance_performed and performance_kind=="public"` → 通知 ledger；
    grant 为 `full_extra` 且 `root_action` → 登记 full_extra 轮；
  - `begin_round`：调 `arc_ledger.scan(state)`（R0 补扫上一轮事件；终局漏扫由评分兜底）。
- **`engine/m9/pp.py`**：`ScoringEngine._story` 使用
  `min(arc_count, arc_cap) × arc_weight`；`settle` 前调用一次 `arc_ledger.scan(state)`。
- **`engine/m9/gate.py`**：`ensure_state_mechanisms` 创建 `game_state.m9_arc` 并双向接线
  （`m9_system.arc_ledger = m9_arc`、`m9_arc.attach_state(game_state)`）。
- **事件补丁（最小化）**：`star_attack` 增加 `hits` 字段；`m9_police_enforcement` 增加
  `captain` 字段；G4 焚诏结束增加 `g4_judgment_completed`；G6 借用成功增加
  `g6_borrow_core`；T2 追猎执行增加 `t2_hunt_reaction`。均为新增日志字段/事件，
  不改变既有结算语义。

---

## 六、BasicAI 说明与开放项

- **BasicAI**：`t0_policy.should_register_public` 增加登台优先意识——尚未点亮第一章的
  AI 在引擎资格门通过后倾向报名公演（结构无收益槽位如 G3 空结界、G5 无脚本仍保留
  原有拒绝门）；已点亮第一章者维持现有按槽位/人格的门。`M9AIPolicy` 不需要新增字段：
  AI 通过只读 `game_state.m9_arc.has_debut(pid)` 查询。
- **开放项（实现第一版的两处工程近似，待风洞修订）**：
  1. G7 第二章“战术宏内击杀”暂以“任意击杀”近似——战术宏内部步骤经
     `combat.damage_resolver` 结算但未携带可机检的宏来源标识；若风洞显示 G7 章节
     过度易得，再为宏步骤补来源标签。
  2. T6 第三章“队长任内指挥警力完成击杀”暂以“队长任内任意警力执法当轮死亡”近似，
     不区分“指挥攻击”与 R4 自动执法（两者都属队长权威行使）。
- 数值全部待风洞；`arc_weight` 初始 1.5，验证协议见 §七。

---

## 七、验证协议

`stats_runner --profile m9-rfc --players 6 --games 500` 必须同时报告：

1. 每槽位场均 arc 与满章率：无“零 arc 槽位”；满章率落在 10–60% 带内；
2. 每槽位公演报名率与首演轮次：14 槽位都有登台行为（T7 会为第一章演一次）；
3. 登台优先触发次数：争抢轮次首演者插队，空队列零副作用；
4. 天赋极差 / 人格极差：不劣于 R50（2.29× / 2.29×）基线；
5. 若个别槽位章节在 AI meta 下触发率 <5%，修订该槽位章节条件，不改全局 `arc_weight`。

---

## 八、决策记录

| 决策 | 内容 | 状态 |
|---|---|---|
| 2026-08-14 | 采用三章制完结条，放弃“arc += 公演次数”与公演价值当量（PVE）方案 | 已批准 |
| 2026-08-14 | 十四槽位第二/三章草案表暂时接受，风洞按触发率修订 | 已批准 |
| 2026-08-14 | 一章一事 + 章节顺序解锁冻结为公共规则 | 已批准 |
| 2026-08-14 | 登台优先（未登台者优先获得公演位，同档 FIFO）纳入设计并冻结 | 已批准 |
| 2026-08-14 | `arc_weight` 恢复 1.5、`arc_cap=3`、每章 +1 `arc_progress` PP | 随稿冻结，数值待风洞 |
