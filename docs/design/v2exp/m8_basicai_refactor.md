# M8 · BasicAI 重写 + 信源统一审计

> 状态：M8.1 步骤 1–5 已实施（2026-08-11，并入 PR #364）。
> 步骤 1（D4 归并）与步骤 2–5（m8_ai 门控 + 估算/经济/消费端接地）完成；
> 每步 golden 冻结 + stats_runner 烟雾通过，`m8_ai` off 路径逐字节不变。
> 修订（2026-08-10）：收缩为 **M8.1 = 原步骤 1–5（数值语义信源统一）**；步骤 6–7 剥离至 M9 引擎落地后。
> **现行指导见 §6**，§0–5 保留为原始审计记录。
> 背景：V2.0 数值模型（hp20）+ 全套新系统（弓/模块/钩索/信用点/白昼/喝彩/往世层/锚定/谱面/舞台）
> 已在 M1–M7 落地，但 **BasicAI 几乎全部决策逻辑仍停留在旧 2.0 模型**，且自身积累了大量信源债。
> 数值模型替换是稀有任务（不会每个大版本做一次），故本轮**尽可能把债清干净**。

---

## 0. 总览与结论

- **架构是好的、可扩展、不需要推倒**：`get_command → DecisionOrchestrator` 单管道，
  Mind（评估）/ CommandBuilder（出指令串）/ GoalStack（跨轮意图）/ TalentHook（天赋接管）
  分层清晰，加新行为基本是"加 Mind + Builder 方法 + Goal + Hook"的叠加，不动核心。
- **但工作量是全量**：战斗、风险评估、发育、天赋、舞台全部反向依赖一套**陈旧 + 重复**的战斗数学；
  外加往世层等**全新能力**需从零写。
- **路线 A（已拍板）**：新增 `m8_ai` 实验开关门控。`off` = 现 AI 字节不变（golden 冻结）、
  `on` = 信源统一的新 AI。理由：orchestrator 开发期被"莫名行为突变"坑过，要最大化安全。

---

## 1. 信源统一债（要清的，6 类）

| # | 债 | 应归并到（canonical） | 改行为? |
|---|---|---|---|
| **D1** | 战斗数学陈旧（裸伤不减防 / 护甲按件数 / 硬克制二元） | `combat/numeric_v2` + `engine/anchor_eval.simulate_path` | ✅ |
| **D2** | 内部重复（Mixin 退役半成品，多份分叉拷贝） | `GameQuery`（读）+ `controllers/ai/evaluation.py`（纯算） | 部分 |
| **D3** | 经济模型陈旧（不读 `.credits`，用 M4 前 voucher/pass） | `player.credits` + `balance.economy`/模块价 | ✅ |
| **D4** | `EQUIPMENT_LOCATION` 重复引擎 | 引擎单表（`police_system.py` 现有，宜挪 `action_tables`） | 否（中性） |
| **D5** | 天赋数值硬编码（火萤×2 / ardent×0.5） | `talent_num`/读活天赋已换算属性 | ✅ |
| **D6** | 打分魔数（hp*10 / dmg*15 / 件数*20 …） | `balance.ai.*`（新，[待风洞]） | ✅ |

### D1 战斗数学陈旧（核心，全局地基）

hp20 是**减法防御 + 耐久 + 穿甲 + 属性按防御表（硬克制已废）**，但 AI 全用旧模型。活标本：

```
controllers/ai/game_query.py:1078  best_effective_weapon_damage(player, target)
    dmg = get_weapon_damage(w)          # ① 裸伤，从不减目标 defense_map
    if not any(a in effective_set ...): continue   # ② 硬克制二元：不利武器整把丢弃
```
名字带 "effective" 又收 `target`，实则裸伤 + 旧硬克制。其余同病：
- `game_query.py:641 estimate_talent_adjusted_damage` — 返回裸伤 + 火萤×2 写死。
- `game_query.py:713 estimate_power` — hp*10 + 裸伤*15 + **护甲件数**×20/15。
- `combat_mixin.py:520(police_mixin)/926 _armor_counters_weapon / _pick_counter_attr` — `is_effective` 二元。
- `minds/combat_mind.py:173/295 _score_target / weapon score`、`combat_commands.py:432 weapon_score`、
  `police_mind.py:202 can_damage_through_protection`、`talents/hoshino_impl.py:480 _hoshino_can_effectively_shoot`
  —— 全部建立在裸伤 / 硬克制二元上。

> 注：属性**表**（`EFFECTIVE_AGAINST`/`COUNTER_ATTRIBUTE`）已正确从 `utils.attribute` 引（无表重复债）；
> 错的是**用法**——把"属性不利"当"武器无用"，hp20 下应改为"净伤更低但仍有效"。

### D2 内部重复（Mixin 退役半成品）

同一估算躺多处、且互相分叉（"小心求证"已逐一核对）：
- **effective_hp ×3**：`evaluation.py:40` / `game_query.py:628` / `evaluation_mixin.py:410`（委托 evaluation）。
- **estimate_power ×2**：`game_query.py:713` 与 `evaluation_mixin.py:413` **逐行复制**。
- **estimate_talent_adjusted_damage ×2**：`game_query.py:641` 与 `evaluation_mixin.py:364`。
- **best_weapon_damage ×2**：`game_query.py:663` 与 `evaluation_mixin.py:393`。
- **count_outer/inner_armor / get_*_armor_attr / get_weapon_damage**：`game_query` + `helpers_mixin` +
  `goals/flee_goal.py` 各一份。
- **has_armor_by_name ×4**：`game_query` + `helpers_mixin:144` + `flee_goal:150` + `goals/develop_goal.py:169`。
- **weapon_score ×3**：`combat_mind.py:295` + `combat_mixin.py:760` + `combat_commands.py:432`。

成因：CLAUDE.md 记载的"Mixin 退役进行中"——纯查询本应迁 `GameQuery`，迁了一半、旧拷贝未删。

### D3 经济模型陈旧

`controllers/ai/` 内 `.credits` 命中数 = **0**——AI 买东西不看钱。`constants.py:80 NEED_PROVIDERS`
成本标 `free/voucher/pass`（M4 信用点经济之前的凭证模型）。`develop_mixin.py:872 _score_destination`
按 voucher/has_pass 决策。canonical：`player.credits` + `balance.economy.sinks` / `bow_modules` 价格。

### D4 EQUIPMENT_LOCATION 重复引擎

`controllers/ai/constants.py:39` 的 `EQUIPMENT_LOCATION` 与 `engine/police_system.py:19` 的同名表两份。
canonical：引擎单表（建议迁到 `engine/action_tables.py`，AI 与 police_system 同读）。中性改动，可不门控。

### D5 天赋数值硬编码

`estimate_talent_adjusted_damage` 火萤 `×2.0`（game_query:652）、`effective_hp` ardent `×0.5`
（evaluation:52 / game_query:636）等写死，与 hp20 的 balance（`talents.g1.attack_bonus` 等）脱节。
canonical：`talent_num` 或读活天赋已换算属性。

### D6 打分魔数

`estimate_power` 的 hp*10 / dmg*15 / 件数*20/15 / 隐身*10 / 探测*5 / 荷鲁斯*15 等权重写死。
canonical：`balance.ai.*`（新分区，[待风洞]）。

---

## 2. 附加能力缺失（M8 要新写，**不算信源债**）

- **往世层星模式策略**：`actions/starlight.py` 信源干净（读 `balance.afterlife.*`）。死亡 AI 成星后经
  `star.controller.choose("afterlife"...)` 路由回 `BasicAIController.choose`，但 `controllers/ai` 内
  starlight/拨弄/加冕/预兆 命中 = **0** → **不崩但无脑**（落 choose 默认）。需新写星模式决策 +
  生者侧 omen 规避。
- **新系统"会玩"**：弓 shoot（唯一跨地点）/ 模块采购装卸 / 钩索 / 信用点消费 / 白昼阶段规避 /
  喝彩追分 / **T5 读谱按拍** / **G5 主动发锚 + 选命运路线 + 被锚开拓** / stage duet 投票·embrace 的
  MVP 桩补全（`stage_ai.py:154/185`）。
- 注：唯一深度接入的天赋是 **G7 星野**（`hoshino_impl/hook` 一整套），其余新天赋 AI 皆空。

---

## 3. 推荐架构（路线 A）

1. **GameQuery = 唯一信源 + 唯一门控点**。每个战斗/经济估算内部
   `if is_enabled("m8_ai"): <numeric_v2 新> else <逐字复刻旧>`。
2. **两个新原语收口 D1**：
   - `net_damage(attacker, weapon, target)` → `numeric_v2.compute_damage(raw, compute_defense(target, attr))`；
   - `rounds_to_kill(...)` → 复用 `anchor_eval.simulate_path`。
   消费端（best_effective / estimate_power / _score_target / weapon_score×3 / _armor_counters /
   can_damage_through_protection / hoshino 射击判定）逐个改调这两个 → **AI 与引擎、锚定神谕三方并轨同一套 numeric_v2**。
3. **去重的中性性问题（诚实）**：分叉拷贝合并必然改掉部分调用者行为，即使 `m8_ai` off 也会漂。
   故**不在原地强删**；做法：`on` 路径从第一天就信源统一（走 GameQuery），**旧散落代码 = off 分支冻结，
   待 `m8_ai` 转正、开关退役时再删**（同 C7 退役 `new_arch_enabled`）。"信源统一"在 on 路径当下即成立。
4. **D4 中性归并**可独立先做，不门控（两表本应相等，合并 + assert 防回退）。

---

## 4. 建议顺序

1. **D4 归并**（中性、练手）：迁 `EQUIPMENT_LOCATION` 到 `action_tables`，AI + police_system 同读 + assert。
2. **`m8_ai` 门控骨架 + GameQuery `net_damage`/`rounds_to_kill` 地基**（D1 根）。
3. **估算函数逐个接地**（effective_hp / best_weapon / can_damage / estimate_power / 属性克制→净伤），
   全在 GameQuery 内 `m8_ai` 分叉（D1/D5/D6）。
4. **经济 credits 接地**（D3）。
5. **消费端收口**：三处 weapon_score / 各 `_score_target` / 警察判定改调 net_damage。
6. **附加能力**：往世层星模式 → 新系统会玩 → 新天赋 AI（T5/G5/开拓）→ stage 桩补全。
7. **RL 解冻重训**（最后，等 BasicAI 能产出像样 BC 数据）。
8. **（开关转正后）** 删 evaluation_mixin/helpers_mixin/goals 的拷贝（D2 兑现）。

---

## 5. 纪律（PR#362 血泪）

- **每步 `stats_runner --players 6 --games 50` 运行时烟雾**（崩溃 0 + 平均轮数同噪声带）。
  **pytest 拦不住 AI 全链路回归。**
- `m8_ai` off 必须逐字节复刻 → 每步验 v1~m6 golden 冻结（注意：AI 改动若漏门控会让**所有 all_ai
  golden 漂移**，这是 M8 与 M1–M7 的根本不同——golden 是 AI 驱动的）。
- 字符串属性引用（`getattr(o,'_x')`/`hasattr`/`setattr`）grep 单独核；跨文件删函数前 grep 调用者清零。
- 单次 ≤5 文件；不重写正常代码来"优化"。

---

## 附录 · 散落估算函数清单（收口目标 = GameQuery）

| 概念 | 散落处 |
|---|---|
| effective_hp | evaluation.py:40 / game_query.py:628 / evaluation_mixin.py:410 |
| estimate_power | game_query.py:713 / evaluation_mixin.py:413 |
| talent_adjusted_damage | game_query.py:641 / evaluation_mixin.py:364 |
| best_weapon_damage | game_query.py:663 / evaluation_mixin.py:393 |
| best_effective_weapon_damage | game_query.py:1078 |
| count_outer/inner_armor | game_query.py:224/232 / helpers_mixin.py:152/158 / flee_goal.py:134/142 |
| has_armor_by_name | game_query.py:215 / helpers_mixin.py:144 / flee_goal.py:150 / develop_goal.py:169 |
| weapon_score | combat_mind.py:295 / combat_mixin.py:760 / combat_commands.py:432 |
| can_damage(_through_protection) | minds/police_mind.py:202 / orchestrator.py:1300 |
| 属性克制 | combat_mixin.py:926 / police_mixin.py:520 / game_query.py:1078 |

---

## 6. 修订（2026-08-10）— M8.1 收缩与 M9 对齐（现行指导）

> 本节为现行指导，取代原 §2 与 §4 中第 6–7 步的范围约定；§0–5 保留为原始审计记录。
> 修订动因：M9 十九份合同（`docs/m9/current/`，2026-08 冻结，m9-rfc profile）对天赋机制层几乎全面重制，
> 若按原 §4 顺序把步骤 6–7 做完再等 M9，等于把 M8 投资投进即将被淘汰的设计。

### 6.1 背景：M9 对天赋机制层的重制面

| M9 合同 | 对 M7 天赋的处置 |
|---|---|
| `m9_talent_action_contract_rfc_v0.3` | **T5 退役**，原槽位转 G0 |
| `m9_g2_holographic_presence_rfc_v0.3` | G2 舞台冻结、duet suspended，改光身/影身双 actor |
| `m9_g3_reality_marble/chain_projection_rfc` | G3 重置为现实宝石 + 连续投影 |
| `m9_g4_savior_cycle_rfc_v0.3` | G4 十二火种轮回、残缺/完整救世主、焚诏/天裁 |
| `m9_g5_anchor_contract_rfc_v0.4` | G5 重入轮回 + 玩家自写 AnchorScript |
| `m9_g6_cutaway_joke_rfc_v0.2` | G6 即演重演、公演借用核心 |
| `m9_g7_tactical_suppression_rfc_v0.3` | G7 三形态、战术宏、连续射击、Terror |
| `m9_g1_firefly_burn_cycle_rfc_v0.3` | G1 三段形态、繁育、绝对死亡 |
| `m9_g0_shiroko_terror_rfc_v0.3` | G0 新增（BLACK FANG 465/无人机/遗物） |
| `m9_t3_t7_migration_rfc_v0.3` / `m9_police_t6_reset_rfc_v0.3` | T3/T7 迁移、T6 警察配装重置 |
| `m9_pp_afterlife_betting_rfc_v0.4` / `m9_pp_afterlife_scoring_rfc_v0.1` | 往世层改 PP 投注 + 魂援 + 绝对死排除 + arc_count 评分 |

原 §2 第 6 步的"新天赋 AI（T5 读谱/G5 发锚/stage duet 桩/星模式）"逐项命中上述重制面；
原第 7 步 RL 解冻采出的 BC 数据在 M9 落地后作废重采（先例：design draft §11.7"BC 数据全部作废，M7 后重采"）。

### 6.2 M8.1 范围：仅保留步骤 1–5

| 步骤 | 内容 | 与 M9 关系 |
|---|---|---|
| 1 | D4 归并（`EQUIPMENT_LOCATION` 单表，中性、不门控） | 无关，照做 |
| 2 | `m8_ai` 门控骨架 + GameQuery `net_damage`/`rounds_to_kill` 地基（D1 根） | **M9 前置** |
| 3 | 估算函数逐个接地（effective_hp / best_weapon / estimate_power / 克制→净伤） | **M9 前置** |
| 4 | 经济 credits 接地（D3） | M9 兼容（PP 为第二货币，不动 credits） |
| 5 | 消费端收口（weapon_score×3 / `_score_target` / 警察判定 → net_damage） | **M9 前置** |

### 6.3 为什么步骤 1–5 不被 M9 淘汰（兼容性论据）

1. **M9 未推翻数值语义层**：B5 纸面推演 v0.1 锁定的 HP20 + 伤害下限 `ceil(A×25%)` 即现行语义；
   分辨率合同 v0.3 只是给伤害管道加 A/H 两阶段通道与 `DIRECT_DAMAGE` 身份，AI 侧"净伤能否打死"的评估模型不变。
2. **B2 已预演兼容**：T3 真伤终裁为"无属性 + `defense_coefficient=0`"
   （`m9_t3_t7_migration_rfc_v0.3.md` §1.3）——即 `numeric_v2.compute_damage(raw, compute_defense(...))` 的自然实例，
   M9 合同主动收敛到 M8 要建的原语上，而非分叉。
3. **动作制同构**：design draft §10 M8 行（L1007/L1091）"顺序全员行动制与其 assess() 框架同构"——
   Mind / GoalStack / GameQuery 评估框架在 M9 全员行动 + SP 分层制下继续有效；变的只是 Builder 的动作枚举层。

### 6.4 剥离项（移出 M8，挂到 M9 引擎落地后的 AI 适配）

| 原步骤 6 子项 | 剥离理由 | 后续承接 |
|---|---|---|
| 往世层星模式策略（starlight/拨弄/加冕/预兆） | M9 改 PP 投注 + 魂援 + 绝对死排除语义 | M9 引擎落地后按 B4 v0.4 合同重写 |
| T5 读谱按拍 | T5 已退役，槽位转 G0 | G0 无人机/遗物 AI |
| G5 主动发锚 + 命运路线 | G5 已重置为重入轮回 + AnchorScript | 按 `m9_g5_anchor_contract_rfc_v0.4` 重写 |
| stage duet 投票 / embrace 桩（`stage_ai.py:154/185`） | G2 舞台冻结、duet suspended | 按 G2 光身/影身合同重写（若 G2 保留） |
| 步骤 7 RL 解冻重训 | BC 数据基于天赋环境，M9 落地后作废 | 顺延至 M9 引擎落地后一次性重设计（同 §11.7） |

### 6.5 总路线（M8.1 之后的接续）

```
M8.1（步骤 1–5 信源统一）
  → M9 引擎实现（增量就绪审计：G0 世界援助 + G3 连续投影
    → 合同测试补缺（B0–B3 已列清单）→ [待风洞] 数值单值化
    → W1 原型指标 → 配置档迁移决策 → m9-rfc 接入 v2exp）
  → BasicAI 基于新地基重设计（原步骤 6 的 M9 版：SP/即演/公演/新天赋 AI）
  → stats_runner 风洞平衡（design draft §9 仪表盘 + M6 轮次敏感数值复核）
  → 终版方案定稿
  → 作者动笔：新完全手册 + 开发日记
```

### 6.6 完成标准与新增停止线

- **M8.1 完成标志**：引擎 / AI / 锚定神谕三方并轨同一 `numeric_v2`；验收仍按 §5 纪律
  （`m8_ai` off 逐字节不变 → v1~m6 golden 冻结；每步 `stats_runner --players 6 --games 50`
  烟雾：崩溃 0 + 平均轮长同噪声带）。
- **停止线（修订新增）**：
  1. M8.1 期间不写任何天赋 AI 能力（星模式 / 谱面 / 锚定 / 舞台均不碰）；
  2. M9 引擎落地前不做 RL 解冻；
  3. 不按 M7 天赋设计补原步骤 6 的任何子项。
- M8.1 开工前，本修订即 §2 / §4 过期清单的更新（后续再拆入 /plan 的工单级描述）。
