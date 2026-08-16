# M9 AI 策略设计参考（talents.md）

> **定位**：给 BasicAI/RL 策略设计提供**单一事实来源**，避免加载全部 M9 合同文档。
> **边界**：本文档只承诺**决策接口事实**（决策点/经济/门/世界事实读写）与**设计约定**；
> 各机制语义以 `docs/m9/current/` 对应 RFC 为准。
> **同步**：`§0`/`§1` 中的接口清单由 `tests/test_talents_md_sync.py` 对照 adapter 源码
> 机械校验，漏列或过期即红；语义描述手写，评审时更新。

## 阅读顺序

1. 本页 `§0` 共享世界模型 → 建立共同物理；
2. 你负责的槽位卡（`§1` 分片）；
3. `§2` 交互矩阵 → 查该槽读写哪些世界事实；
4. `§3` 评分器草案 → 数值评估统一口径；
5. `§4` Policy 协议与复用边界 → 代码组织约定；
6. `§5` few-shot 示例 → 决策注记的写法示范。

## 0. 共享世界模型（所有策略的共同物理）

| 概念 | 写入者 | 读取者 | 关键 API / 状态 |
|---|---|---|---|
| SP / 演出经济 | 系统 | 全部 | `state.m9_system.get_sp/set_sp`（engine/m9/action_system.py）；即演 −1、公演 −2，SP∈{0,1,2} |
| 公演位 | 系统（R0 报名→固化） | 全部 | `m9.assign_public_slot(round)` / `dispatch_public` / `dispatch_improvise`；**T0 不得临时报名**，失败不污染队列/SP |
| 结界边界 | G3 | 全部 | `active_barrier(state)`、`attack_crosses_active_barrier(state,a,b)`（engine/m9/talents/g3.py）；被困者普通移动被拦，须走 `special 破界` |
| 警务（M9） | 系统/T6 | 全部 | `state.m9_police`（engine/m9/police.py）：cases/open_wanted/lead_id/roster/cover；`station.player_cover(pid)` 是掩体吸收源；`m9_police` 取代 legacy `state.police_engine`（仅剩犯罪记录壳） |
| 世界时钟 | 系统（M5） | 全部 | `world_clock.current_phase(state)`；黄昏 `police_protection:false` 撤掩体、终焉 `police_disabled:true` 警务停摆 |
| 终曲区域 | G2 | 全部 | `terminal_area_for(state, loc)`（engine/m9/talents/g2.py）：区域内全员易伤 + 伤害共享 + 移动偏转 + 一次压制 |
| 影身 | G2 | 全部 | `state.m9_shadows`（engine/m9/talents/g2.py）：独立 actor、代理标准槽；目标枚举经 `iter_actors` |
| 无人机 | G0 | 全部 | `g0_drone:<pid>`（engine/m9/talents/g0.py）：可被攻击、无行动槽；只经 `iter_actors` 可见 |
| 石化 | T3/系统 | 全部 | `state.m9_petrify`（engine/m9/petrify.py）：统一注册表；石化 T0 = forfeit 或同槽挣脱（1 SP/次） |
| 保险 | T7 | 全部 | `state.m9_insurance.is_mounted()/mounted_target()`（engine/m9/insurance.py）：全局一次 |
| 爱愿 | G5（诗篇） | 全部 | `talent.has_love_wish(pid)`（engine/m9/talents/poems.py）：攻击者对其伤害被免疫（combat.py 先查） |
| 被毁地点 | G1（超新星） | 全部 | `state.m9_destroyed_locations`：移动/发育路由必须排除 |
| 模板池 | 系统（行动循环记录） | G6 | `state.g6_template_pool`（engine/m9/gate.py）：类别去重、窗口 1 轮（欢愉延展 2 轮）；**T0 演出恒记 `talent_t0` 不入池**，入池只来自普通行动 |
| 临时 HP 吸收 | G4/G7/G1… | 结算器 | `receive_damage_to_temp_hp` 协议（engine/m9/talents/stub.py）；AI 折算需模拟此链 |
| 免死/保险裁决 | 结算器 | — | `DeathAdjudicator` / `adjudicate_and_finalize_death`（engine/m9/combat.py）；`absolute_death` source_kind 跳过 T7 |
| 目标枚举 | 引擎 | 全部 | `game_state.iter_actors()/iter_targetable_actors()`：含玩家/警察单位/无人机/影身；`player_order` 只含玩家 |
| 特殊行动（special op） | G3/T6/系统 | 被困者/T6/队长 | `actions/special_op.py`：`special 破界`/`武器破界`（G3 被困者的标准根行动）、`热线举报{玩家名}`/`竞选队长`/`指挥{警员}移动`（T6/队长）；AI 命令层必须能生成这些字符串 |

## 1. 槽位卡（分片索引）

| 分片 | 槽位 | 文件 |
|---|---|---|
| 核心槽 | T1 / T2 / T3 / T4 / T6 / T7 | [`slots_t1t4t6t7.md`](slots_t1t4t6t7.md) |
| 神代 G 上半 | G0 / G1 / G2 / G3 | [`slots_g0g1g2g3.md`](slots_g0g1g2g3.md) |
| 神代 G 下半 | G4 / G5 / G6 / G7 | [`slots_g4g5g6g7.md`](slots_g4g5g6g7.md) |

卡格式（每槽固定四栏 + 可选示例）：

```markdown
### T1 一刀缭断 `OneSlash9`
- **决策点**：T0 `talent_t0`（选项名…）；situation 标签…；关联 special op…
- **经济与门**：成本/前置条件/冷却语义
- **核心效果**：一句话
- **AI 注记**：这个槽的好策略要决定什么
- **决策示例（few-shot，仅代表槽）**：场景 → 推理 → 输出
```

## 2. 交互矩阵（写↔读）

行 = 槽位，列 = 世界事实；`W` = 写（产出），`R` = 读（消费）。

| 槽位 | 结界 | 警务/掩体/通缉 | 终曲区域 | 影身 | 无人机 | 石化 | 保险 | 爱愿 | 被毁地点 | 模板池 | SP/公演位 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 系统 | — | W | — | — | — | W | W | — | W | W | W |
| T1 | R | R | R | R | R | R | R | R | R | W* | R |
| T2 | R | R | R | R | R | R | R | R | R | W* | R |
| T3 | R | R | R | R | R | **W** | R | R | R | W* | R |
| T4 | R | R | R | R | R | R(免疫) | R | R | R | W* | R |
| T6 | R | **W** | R | R | R | R | R | R | R | W* | R |
| T7 | R | R | R | R | R | R | **W** | R | R | W* | R |
| G0 | R | R | R | R | **W** | R | R | R | R | R(模板追演) | R |
| G1 | R | R | R | R | R | R | R | R | **W** | W* | R |
| G2 | R | R | **W** | **W** | R | R | R | R | R | W* | R |
| G3 | **W** | W(挂起) | R | R(不捕捉) | R(不捕捉) | R | R | R | R | W* | R |
| G4 | R | R | R | R | R | R | R | R | R | W* | R |
| G5 | R | R | R | R | R | R | R | **W** | R | R(彼岸指定装备) | R |
| G6 | R | R | R | R | R | R | R | R | R | **R**+W(借用) | R |
| G7 | R | R(逃离) | R | R | R | R | R | R | R | W* | R(豁免) |

> W*：该槽的根行动被行动循环正常记入模板池（attack/lock/find/move/interact 类别）。
> 设计约定：**所有两两交互退化为读/写共享事实**，不写点对点策略；委托型交互（G6 借用
> 核心、G0 遗物支援）由 Policy 协议复用被借槽策略；元博弈型（反队长、追猎时机）作为
> policy 扩展读取共享事实自行决策。

## 3. 通用评分器草案

> 2026-09 风洞收敛追加：M9 终分支持槽位得分系数
> `m9_system.scoring_m9.talent_score_multiplier`（balance.json，缺省 1.0），
> 在 `engine/m9/pp.py::ScoringEngine.score` 对合计后的 base 乘算；只用于
> 胜率极差收敛，不进入 AI 决策评分（AI 不应感知该系数）。

### 3.1 同源探针（信源 = 结算器）

- 基础：`engine/m9/combat.py::resolve_hit_probe(target, raw_int, attr, pierce_factor)`
  —— 只算账不落 HP 的 A/H 探针（AI 评估/预检用，与结算同一函数）。
- 需补齐的折算输入：掩体吸收（`station.player_cover` 并入 A 阶段）、临时 HP 链
  （`receive_damage_to_temp_hp` 模拟）、结界/爱愿可行性（**不可行 ≠ 0 伤，直接过滤**）、
  致死裁决探针（保险/免死预期）、终曲易伤与伤害共享分摊。

### 3.2 统一折算公式（骨架）

```
value(action, target) =
      p_hit · E[damage]                       # 同源探针
    + kill_utility(target) · p_lethal         # 致死概率 × 击杀效用（扣除保险覆盖）
    + control_utility(effect)                 # 石化/眩晕/缴械 ≈ 对手下轮预期伤害折现
    − case_risk                               # 警察目击/案件代价
    − resource_cost                           # SP/HP/追忆 机会成本折算
    − exposure                                # 预期承伤增量（对称探针）
```

人格权重（aggressive/defensive/…）只调制系数，不改公式。

### 3.3 显式不可折算项

- 结界/爱愿/控制免疫 → **可行性过滤**（不进评分）；
- 保险否决 → 击杀效用 × (1 − 保险覆盖)；
- 公演位 → 稀缺资源，SP 机会成本单列；
- 锚定（G5）→ 长周期价值，按脚本槽预期实现率折现。

## 4. Policy 协议与复用边界

### 4.1 协议（每槽挂 slot_id，不按显示名匹配）

```
class M9AIPolicy:
    slot_id: str
    def should_activate_t0(player, state, option) -> bool        # T0 发动门（SP/HP/形态）
    def choose(player, state, prompt, options, context) -> str | None   # 内部 choose；None=回退
    def candidates(player, state, available) -> list[str] | None # 命令层覆盖；None=不参与
```

- 注册键 = `player.talent_slot_id`（稳定）；`DefaultM9Policy` 兜底所有未覆盖槽。
- 引擎 T0 上下文传 `t0_option["name"]`（显示名）——**策略层不得依赖显示名**，直接按
  slot_id 分派（收编断点：action_turn.py:425 的名字匹配失效问题）。
- **决策内核（2026-08-12 落地）**：`controllers/ai/decision/` 提供共享 I/O——
  - `ActionSpec`/`ScoredActionSpec`：动作不可变描述（action_type/raw/profile/slot_id/
    grant_id/sp_cost/state_version）；
  - `ActionCatalog`：唯一合法动作信源（`engine.action_enumerator` + `special_op`
    动态列表，M9 感知），`match/validate/specify/substitute`，attack 按
    `目标+武器` 三段主键归一化（覆盖 builders 的显式 layer/attr 四参写法）；
  - `DecisionSnapshot`：决策点不可变快照（actor/profile/slot_id/当前 grant/SP/
    装备/位置 + `M9Facts`：m9_police 摘要、`active_barrier(state)`、被毁地点、
    影身数）；Orchestrator 结界过滤与警务上下文已 M9 化。
  - 天赋 hook 分派：`controllers/ai/m9_adapters.py::resolve_talent_hook` 按
    `(profile, slot_id)` 优先、显示名回退；G2/G5/G6 已注册 M9 adapter 入口。

### 4.2 复用边界（来自 2026-08 三路审计的结论）

| 类别 | 内容 |
|---|---|
| 直接迁入 | T4 RPS 大脑 + `hexagram_*` 处理器；G7 choose 层 situation 处理器（形态/药物/目标）；T1 武器/目标选取；T7 挂自己（`resurrection_pick_target`）；T2 response_window confirm；`terror_defense.py`（不按名字匹配） |
| 重写后迁入 | G7 命令层（Terror 批处理路径、反队长新触发模型＝通缉 lead + 掩体耐久）；G1 发育/超新星（形态阶梯 + 移动触发 + 地点摧毁）；T1/T3 发动门（SP 经济版）；G3（结界新模型 `active_barrier(state)`） |
| 从零写 | T2/T6/T7（新机制门）/G0（**HP 成本门，唯一自残天赋**）/G2（影身 + 终曲）/G5（锚定 + 诗篇）/G6（模板池窗口冷启动） |
| 世界模型中心化 | minds 改读 `m9_police` / `active_barrier(state)` / `iter_actors` / `m9_destroyed_locations` / `world_clock`（对所有天赋成立，不逐槽写） |
| 冻结/删除 | `stage/*` 全层、`ripple_*`/`poem_*` 分支（v2exp 专属）、legacy 警察语法链；`combat_mixin.py`（[FROZEN]）；M9 下禁读 `state.police_engine` 的执法语义 |

## 5. few-shot 决策示例（写法示范）

示例置于槽位卡内（G0 与 G3 各一个），格式：**场景 → 推理（引用 §0 事实与 §3 公式）→ 输出**。

```markdown
**决策示例（G0 召唤）**：SP=1、HP=5/20、有无人机。
场景 → 推理：召唤需 20% 当前 HP=1，HP 5 时再扣 1 仍有残血但暴露；
无人机价值 ≈ 每轮追加 1 科技伤 × 剩余回合期望，扣掉 HP 成本与致死风险折现（§3.2 exposure 项）。
输出：HP≥10 时发动；HP<6 时放弃（保留撤退/调整呼吸窗口）。
```

## 6. 治理

- `tests/test_talents_md_sync.py` 从 `engine/m9/talents/*.py` 提取：`get_t0_option` 的
  `m9_kind`、`controller.choose` 的 `situation` 标签、`special_op` 关联命令名；
  断言本文档与分片卡覆盖全部提取结果。缺槽/缺标签即红。
- 本文档语义段落修改走评审；接口清单修改必须同步 `§1` 卡片与治理测试。

---

变更记录：2026-08-12 初稿（世界模型/交互矩阵/评分器/协议/治理；槽位卡分片化）。
