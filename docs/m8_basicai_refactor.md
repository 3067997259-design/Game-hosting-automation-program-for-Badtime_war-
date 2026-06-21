# M8 · BasicAI 重写 + 信源统一审计

> 状态：审计完成（2026-06-22），待开 /plan。
> 背景：V2.0 数值模型（hp20）+ 全套新系统（弓/模块/钩锁/信用点/白昼/喝彩/往世层/锚定/谱面/舞台）
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
- **新系统"会玩"**：弓 shoot（唯一跨地点）/ 模块采购装卸 / 钩锁 / 信用点消费 / 白昼阶段规避 /
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
