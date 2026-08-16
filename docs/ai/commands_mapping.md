# 指令三方映射表（commands_mapping.md）

> **定位**：AI 生成层（`controllers/ai/command_builder/*` + 编排器）/ RL 索引层（`rl/action_space.py`）/ 引擎解析层（`cli/parser` + `engine/action_enumerator`）的**契约对齐表**；缺口（不对齐项）的发现来源。
>
> **Profile 注意**：当前 137 索引布局是**静态 v2exp-era 布局**（`rl/action_space.py` 头部注释明确「130 基础 + 7 G7 星野 special ops 扩展」，`SPECIAL_OPS` 为硬编码静态表）。M9 / M4 动态特殊操作（破界、热线举报、拆卸模块等）**不占索引**，本表在 §4 / §5 逐项标注。
>
> **阅读建议**：先看 §1 布局，再对照 §4 矩阵；矩阵中「✗」即缺口，§5 汇总根因与建议修法方向。

---

## 1. RL 137 索引布局（rl/action_space.py）

### 1.1 常量总览

| 常量 | 值 | 含义 |
|---|---|---|
| `ACTION_COUNT` | `137` | 动作空间大小（`action_space.py:50`，注释：130 基础 + 7 G7 星野 special） |
| `IDX_FORFEIT` | `0` | 放弃（`action_space.py:55`） |
| `IDX_WAKE` | `1` | 起床（`action_space.py:56`） |
| `IDX_MOVE_BASE` | `2` | move 起始，`2–7`（`action_space.py:57`） |
| `IDX_INTERACT_BASE` | `8` | interact 起始，`8–34`（`action_space.py:58`） |
| `IDX_LOCK_BASE` | `35` | lock 起始，`35–39`（`action_space.py:59`） |
| `IDX_FIND_BASE` | `40` | find 起始，`40–44`（`action_space.py:60`） |
| `IDX_ATTACK_BASE` | `45` | attack 起始，`45–94`（`action_space.py:61`） |
| `IDX_SPECIAL_BASE` | `95` | special 起始，`95–107`（`action_space.py:62`） |
| `IDX_POLICE_BASE` | `108` | 警察行动起始，`108–114`（`action_space.py:63`） |
| `IDX_TALENT_T0_TARGET_BASE` | `115` | T0 目标槽起始，`115–119`（`action_space.py:66`） |
| `IDX_TALENT_T0_SELF` | `120` | T0 自身目标（`action_space.py:67`） |
| `IDX_CHOOSE_BASE` | `121` | choose 通用选项起始，`121–136`（`action_space.py:68`） |

### 1.2 索引区间 → 含义（逐段明细）

| 区间 | 数量 | 含义 | 数据源 / 顺序 |
|---|---|---|---|
| `0` | 1 | `forfeit` | — |
| `1` | 1 | `wake` | — |
| `2–7` | 6 | `move <地点>` | `LOCATIONS = ["home","商店","魔法所","医院","军事基地","警察局"]`（`action_tables.py:18-20`） |
| `8–34` | 27 | `interact <物品>` | `INTERACT_ITEMS` 27 项（`action_tables.py:26-40`，断言 `len==27`）：home 3（凭证/小刀/盾牌）、商店 6（打工/磨刀石/隐身衣/热成像仪/陶瓷护甲/防毒面具）、魔法所 8（魔法护盾/魔法弹幕/远程魔法弹幕/封闭/地震/地动山摇/隐身术/探测魔法）、医院 3（晶化皮肤手术/额外心脏手术/不老泉手术）、军事基地 7（办理通行证/AT力场/电磁步枪/导弹控制权/高斯步枪/雷达/隐形涂层） |
| `35–39` | 5 | `lock <对手槽 0-4>` | 槽位按 `get_opponent_slots()`（`player_order` 顺序，排除自身，不足补 None，`action_space.py:181-195`） |
| `40–44` | 5 | `find <对手槽 0-4>` | 同上 |
| `45–94` | 50 | `attack <对手槽 0-4> <武器槽 0-9>` | 布局 `slot*10 + weapon`；`WEAPONS = ["拳击","小刀","警棍","魔法弹幕","远程魔法弹幕","地震","地动山摇","电磁步枪","高斯步枪","导弹"]`（`action_tables.py:80-92`，断言 `len==10`） |
| `95–100` | 6 | `special` 基础操作 | `SPECIAL_OPS[0..5]`：磨刀/吟唱魔法护盾/展开AT力场/蓄力电磁步枪/蓄力高斯步枪/释放病毒（`action_space.py:80-86`） |
| `101–107` | 7 | `special` G7 星野操作 | `SPECIAL_OPS[6..12]`：Hoshino/取消盾牌/修复/肾上腺素/更衣水着-shielder/更衣临战-Archer/更衣临战-shielder（`action_space.py:88-94`；`assert len(SPECIAL_OPS)==13`，`action_space.py:96`） |
| `108–114` | 7 | 警察行动 | `POLICE_CMDS = ["report","assemble","track","recruit","election","designate","study"]`（`action_space.py:99-107`，`assert len==7`） |
| `115–119` | 5 | T0 天赋目标槽（choose 模式专用） | `IDX_TALENT_T0_TARGET_BASE + slot` |
| `120` | 1 | T0 自身目标（choose 模式专用） | `IDX_TALENT_T0_SELF` |
| `121–136` | 16 | `choose_option <0-15>`（choose 同步专用） | `IDX_CHOOSE_BASE + i` |

### 1.3 `idx_to_command` 映射规则（action_space.py:301-364）

| 分支 | 规则 | 行号 |
|---|---|---|
| `0` | `"forfeit"` | 311-312 |
| `1` | `"wake"` | 314-315 |
| `2–7` | `f"move {LOCATIONS[idx-2]}"` | 317-318 |
| `8–34` | `f"interact {INTERACT_ITEMS[idx-8]}"` | 320-321 |
| `35–39` | `f"lock {slot_target.name}"`；槽空 → `"forfeit"` | 323-326 |
| `40–44` | `f"find {slot_target.name}"`；槽空 → `"forfeit"` | 328-331 |
| `45–94` | `offset=idx-45; target_slot=offset//10; weapon_slot=offset%10` → `f"attack {target.name} {WEAPONS[weapon_slot]}"`；槽空 → `"forfeit"` | 333-340 |
| `95–107` | `f"special {SPECIAL_OPS[idx-95]}"` | 342-343 |
| `108–114` | `POLICE_CMDS[idx-108]`；`report` 用 `_auto_report_target`（只选攻击过自己的犯罪者，`action_space.py:267-290`）、`designate` 用 `_auto_target`（kill_count 最高存活对手，`action_space.py:254-264`）自动填目标；无目标 → `"forfeit"` | 345-353 |
| `115–120` | **回退 `"forfeit"`**（不应到达；注释「天赋 T0 索引不应走此函数」） | 358-359 |
| `121–136` | **回退 `"forfeit"`**（同上） | 361-362 |
| 越界 | `raise ValueError` | 364 |

> **关键点**：115–136 全部只走 choose 同步路径（`_SyncRLController.choose()` 直接解释为选项索引，`env.py:697-717`），`idx_to_command` 对它们是安全回退 `forfeit`。

### 1.4 choose 索引经 `idx_to_choose_option` 映射（action_space.py:371-427）

| 区间 | 映射 | 行号 |
|---|---|---|
| `115–119` | 对手槽位 → 在 `options` 中匹配 `target_name`（或包含目标名的选项）；槽无效 → `options[0]` | 396-417 |
| `120` | 自身 → 匹配玩家名或含「自己」的选项；未命中 → `options[0]` | 397-403 |
| `121–136` | `options[idx - 121]`；越界 → `options[-1]` | 420-424 |
| 其他 | 安全回退 `options[0]` | 427 |

### 1.5 `build_action_mask` 逻辑要点（action_space.py:434-634）

- `choose_mode=True` → 只启用 choose 区间，委托 `_build_choose_mask`（465-467）。
- 未醒 → 仅 `wake`（481-484）；`forfeit` 恒可用（487）。
- 星野架盾/持盾二次过滤：`架盾` 禁 move+interact、`持盾` 禁 interact（489-497）。
- move/interact/lock/find/attack 全部**委托 `engine/action_enumerator` 枚举函数**（单一信源，501-541）；对手名→槽位映射 `opponent_slot_by_name` 由 `get_opponent_slots`（**仅 `player_order` 玩家**）构建（513-520）。
- special：按静态 `SPECIAL_OPS` + `SPECIAL_REQUIRES` 逐项判定（543-607），`Hoshino/取消盾牌/修复/肾上腺素/更衣*` 按星野 talent 状态判定（556-581），`释放病毒` 需在医院且病毒未激活（552-554）。
- 警察行动：`police_available_map`（report=0 / assemble=1 / track_guide=2 / recruit=3 / election=4 / designate=5 / study=6）依据 `available_set` + 存活目标 + `_auto_report_target` 判定（609-632）。
- `_build_choose_mask`（692-791）：`talent_t0` → 121 起二选一；目标选择 situation 集 `_TARGET_SITUATIONS`（723-736：oneslash/hexagram/resurrection/mythland/ripple_anchor_*/ripple_poem/cutaway_borrow/g2_sing_target/g2_melody_target/g2_melody_propagate）→ 对手命中槽 `115+slot`、含自己则 `120`、全部未命中回退 121 起；`savior_activate` → 121 起；其余通用 choose → 121 起。
- 战略 choose situation 双集合：`STRATEGIC_CHOOSE_SITUATIONS`（136-166）与 `STRATEGIC_SITUATIONS`（642-689）**重复定义且内容不完全一致**（后者多 `ripple_choose_method`、`hoshino_reorder_ammo` 等），属既有冗余。

### 1.6 已知过时注释（不改代码，仅记录）

| 位置 | 过时内容 | 实际 |
|---|---|---|
| `action_space.py:307-308`（idx_to_command docstring） | 「天赋 T0 索引 (108-113) 和 choose 索引 (114-129)」 | 应为 115-120 / 121-136 |
| `action_space.py:382-384`（idx_to_choose_option docstring） | 「108-112…113…114-129」 | 应为 115-119 / 120 / 121-136 |
| `action_space.py:702-707`（_build_choose_mask docstring） | 「108-129 范围」「114-115」 | 应为 115-136 / 121-122 |
| `env.py:700` 注释 | 「通用 choose 索引 (114-129)」 | 应为 121-136 |
| `action_space.py:444` docstring | 「返回 130 维 bool 数组」「索引 0-107 / 108-129」 | 实际 137 维；get_command 模式 0-107，choose 模式 115-136 |

---

## 2. AI 生成层命令集（command_builder/* + _validate_command 白名单）

> 注：AI 侧命令由 `controllers/ai/controller.py:get_command()` → `DecisionOrchestrator.generate()`（orchestrator.py）产出候选列表（controller.py:404-453），逐条 `attempt` 重试；**当前流程不调用 `_validate_command`**（全仓 grep 仅 `helpers_mixin.py:433` 一处定义，无调用点）。

### 2.1 `_validate_command` 白名单（helpers_mixin.py:433-447）

- 取首词 `action = parts[0]`；
- `action in available` → 合法（**available 白名单**，由引擎 `ActionTurnManager` 判定）；
- `action in ("police", "talent_activate", "special")` → **无条件放行**（helpers_mixin.py:445）——即 `police *`、`talent_activate *`、`special *` 前缀即使不在 available 也通过本白名单。

### 2.2 CombatCommandBuilder（combat_commands.py）可输出命令

| 命令形式 | 行号 |
|---|---|
| `special 蓄力{weapon.name}`（任意 `requires_charge` 未蓄力武器） | 57, 138, 175, 554 |
| `attack {目标名} {武器名} {层} {属性}`（4 参，`pick_attack_layer` 产出 outer/inner + Attribute） | 209, 248, 258, 288, 329, 339 |
| `attack {目标名} {武器名}`（3 参） | 211, 251, 290, 329, 341 |
| `move {目标地点}` | 216, 240, 265, 335 |
| `find {目标名}` | 235, 241 |
| `lock {目标名}` | 283 |
| `interact 热成像仪` / `interact 探测魔法` / `interact 雷达`（探测获取） | 389 / 394 / 397 |
| `interact 魔法弹幕` / `interact 远程魔法弹幕` / `interact 地震` / `interact 地动山摇`（换武器） | 586 / 588 / 590 / 592 |
| `interact 高斯步枪` / `interact 电磁步枪` | 598 / 600 |
| `move {目的地}`（换武器/兜底移动） | 368, 401-408, 616, 627-631 |

### 2.3 DevelopCommandBuilder（develop_commands.py）可输出命令

| 命令形式 | 行号 |
|---|---|
| `recruit` / `election` / `move 警察局`（political 分支） | 76 / 78 / 80 |
| `interact <item>`（DevelopMind `current_location_actions`，如打工/小刀/盾牌等） | 87 |
| `special 蓄力{weapon_name}`（电磁步枪/高斯步枪或 builder 遍历） | 92-95, 101 |
| `move {best_move}` | 111 |
| `attack ...`（经 combat_builder.build_attack，发育受阻转进攻） | 121-124 |
| `move {fallback}` | 131 |
| `interact 盾牌` / `interact 陶瓷护甲` / `interact 打工` / `interact 魔法护盾` / `interact 晶化皮肤手术` / `interact AT力场` | 154 / 161 / 163 / 167 / 177 / 183 |
| `interact 防毒面具`（商店/医院） | 159, 207, 209 |
| `move {safe_loc}` | 187 |
| `interact 封闭`（病毒应急，魔法所） | 215 |
| `move 商店/医院/魔法所`（病毒应急） | 225, 227 |
| `special 磨刀` | 342 |

### 2.4 PoliceCommandBuilder（police_commands.py）可输出命令

| 命令形式 | 行号 |
|---|---|
| `study` | 60 |
| `police move {uid} {目的地}` | 485, 494, 530, 537, 597, 617 |
| `police equip {uid} {武器名}` / `police equip {uid} {护甲名}` | 500 / 509 |
| `police wake {uid}` | 550 |
| `police attack {uid} {目标player_id}` | 621 |
| `assemble` / `track` / `report {目标名}` / `recruit` / `election` / `designate {目标名}` / `move 警察局` | 138 / 149 / 181 / 186 / 195 / 218 / 224, 226 |
| `special 蓄力{aoe_name}` / `special 蓄力电磁步枪` | 290 / 306 |
| `attack {警察单位id} {aoe_name}`（**攻击 legacy 警察单位，按 id**） | 293 |
| `move {unit_loc}`（追警察单位） | 295 |
| `interact 通行证` / `interact 电磁步枪` / `move 军事基地` | 312 / 314 / 316 |
| `interact 地动山摇` / `interact 地震` / `move 魔法所` | 324 / 326 / 328 |
| `interact 电磁步枪` / `move 军事基地` | 331 / 333 |

### 2.5 Orchestrator 直产命令（orchestrator.py）

| 命令 | 行号 |
|---|---|
| `wake` | 323 |
| `special 释放病毒` | 347 |
| `move {dest}`（超新星分散等） | 662 |
| `forfeit`（收尾/兜底） | 359, 382, 430, 638 |
| GoalStack 补充指令（`goal.get_next_command`） | 1123-1141 |

### 2.6 构建器**不生成**、但引擎/RL 可能涉及的命令

- `special 吟唱魔法护盾` / `special 展开AT力场`（RL 有索引、引擎可执行，三个 builder + 编排器**均不生成**；旧架构 `develop_mixin.py` 等遗留路径才有）
- `special Hoshino` / `取消盾牌` / `修复` / `肾上腺素` / `更衣*`（RL 索引 101-107、引擎可执行；新架构 builder 不生成，属**天赋钩子域** `controllers/ai/talents/*_hook.py` / `hoshino_impl.py`）
- M9 全部特殊操作（§4.4）
- `talent_activate *`（仅白名单放行，无构建器产出）

---

## 3. 引擎解析/枚举层（cli/parser + action_enumerator）

### 3.1 `cli/parser.py` 可解析指令概要（parser.py:4-205）

| 首词 | 解析结果 | 行号 |
|---|---|---|
| `wake` / `wake_police` | `wake` / `wake_police{police_id}` | 12-26 |
| `move <dest>`（`home/家/回家` → `home_{pid}`） | `move{destination}` | 28-35 |
| `interact <item>`（`通行证`/`办通行证` → `办理通行证`） | `interact{item}` | 37-48 |
| `lock <target>` / `find <target>` | `lock` / `find` | 50-60 |
| `applause` / `shoot` / `hook`（M4/M6） | `applause_spend` / `shoot` / `hook` | 62-80 |
| `attack <target> <weapon> [layer] [attr]` | `attack{target,weapon,layer,attr}`（支持 4 参） | 82-89 |
| `special <op>`（空参进入交互式；别名：磨→磨刀、吟唱→吟唱魔法护盾、展开→展开AT力场、病毒/放毒→释放病毒、G2 歌名别名） | `special{operation}` | 91-103 |
| `report <target>` / `assemble` / `track` / `recruit` / `election` / `designate <target>` / `study` | 对应 action | 105-136 |
| `police move/equip/attack/wake <id> ...` | `police_command{subcommand,...}` | 138-186 |
| `police` / `police status` / `police_status` | `police_status` | 140-146, 190-191 |
| `forfeit` / `status` / `allstatus` / `help` | 对应 action | 193-205 |

> 主文档：`docs/operations/commands.md`（含 V2.0 实验门控与星野战术宏子解析器）。

### 3.2 `engine/action_enumerator.py` —— `build_action_options` 输出结构（action_enumerator.py:61-149）

- 返回 `Dict[str, List[str]]`：key = action type，value = 完整指令字符串列表；**只枚举 `available_names` 中出现的大类**（66）。
- 无参类型（forfeit/wake/assemble/track_guide/recruit/election/study/police_command）**不在此枚举**，由 bot_bridge 直接使用（70-72）。
- 各 `_enumerate_*`：
  - `_enumerate_move`：排除当前地点（超新星除外）；`架盾` 时返回空（156-173）。
  - `_enumerate_interact`：地点/凭证/通行证/法术前置/所有权/护甲槽逐项过滤（176-308）。
  - `_get_opponents`（34-54）：**M9 感知**——`m9_enabled` 时走 `game_state.iter_targetable_actors()`（**含警察单位/无人机/影身**，见 `docs/m9/ai/talents.md` §0「目标枚举」）；legacy 走 `iter_actors` 或 `player_order`。
  - `_enumerate_lock` / `_enumerate_find`：**M9 NPC 显式排除**（`_m9_drone_actor` / `_m9_police_actor` 过滤，106-123），仅玩家目标；`_enumerate_lock` 需远程武器 + `can_see_m`（311-338），`_enumerate_find` 需同地点 + `can_see_m`（341-358）。
  - `_enumerate_attack`（361-418）：**M9 感知**——结界穿越过滤 `attack_crosses_active_barrier`（381-384）；`is_m9_npc` 目标**免 engaged/lock 前置**（386-387, 408-413）；武器按 MELEE/RANGED/AREA 三种射程判定（408-416）；M9 NPC 包含在 attack 目标中（**不在 lock/find 里**）。
  - `_enumerate_special`（421-428）：委托 `actions/special_op.get_available_specials`（**动态**：基础 + G7 星野 + M4 拆卸 + M9 破界/热线举报/竞选/指挥，见 special_op.py:7-95、98-136）。
  - `_enumerate_report`（431-446）：犯罪记录目标；`_enumerate_designate`（449-451）：全部存活对手。

### 3.3 `cli/validator.py` 校验要点（validator.py:133-184）

- `attack` 目标以 `police` 开头 → 警察目标分支（522-534，**AI 的 `attack policeX ...` 可过**）；玩家目标走 melee/ranged/area 前置（567-573）。
- `special`：`op_name in get_available_specials().names` 精确匹配，或 `startswith(蓄力/更衣/修复)` 且该前缀存在（575-605）——**M9 特殊操作以精确名通过**。
- `police_command` 需 `is_captain` + `police_engine` + 单位存在 + 同地点等（742-796）。
- 结界拦截 `_check_barrier_block`（67-77）应用到 move/interact/report/assemble/track_guide/recruit/election/designate/study/police_command。

---

## 4. 三方对齐矩阵（核心章节）

> 列说明：**AI 生成** = 三个 command_builder + 编排器是否产出；**RL 索引** = `idx_to_command` 能否回译（或 choose 索引）；**引擎解析** = parser/validator/enumerator 是否支持；**M9 可用性** = M9 profile 下引擎是否消费。
> 图例：✓ 支持 / ✗ 不支持 / ⚠ 部分或不完全 / — 不适用。

### 4.1 基础指令

| 命令 | AI 生成 | RL 索引 | 引擎解析 | M9 可用性 | 缺口说明 |
|---|---|---|---|---|---|
| `forfeit` | ✓（编排器收尾） | ✓ `0` | ✓ | ✓ | — |
| `wake` | ✓（orchestrator.py:323） | ✓ `1` | ✓ | ✓ | — |
| `move <地点>` | ✓ | ✓ `2–7`（6 地点） | ✓ | ⚠ 结界被困者普通 move 被拦（orchestrator.py:298-315 已过滤；需 `special 破界`） | M9 下 move 受 `active_barrier` 语义限制，RL 无「破界」表达 |
| `interact <物品>` | ✓（27 项均可达，另发 `interact 通行证`） | ✓ `8–34` | ✓（`通行证` 别名 → 办理通行证） | ⚠ 结界内禁 interact；M4 信用点经济下部分物品名变更 | **AI 发 `interact 通行证`（police_commands.py:312）不在 `INTERACT_ITEMS`**（实际是 `办理通行证`）→ AI 命令无法回译 RL 索引（`_cmd_to_interact_idx` 会对「通行证」抛 ValueError）；引擎经 parser 别名可执行 |
| `lock <目标>` | ✓ | ✓ `35–39` | ✓ | ⚠ enumerator 排除 M9 NPC 目标 | M9 下可锁目标集合 ≠ RL 槽位集合 |
| `find <目标>` | ✓ | ✓ `40–44` | ✓ | ⚠ 同 lock | 同上 |
| `attack <目标> <武器>` | ✓ | ✓ `45–94` | ✓ | ✓（M9 NPC 可被枚举为攻击目标） | M9 NPC 目标在 enumerator 有，但 RL `opponent_slot_by_name` 仅玩家 → **丢弃**（见 4.5） |
| `attack <目标> <武器> <层> <属性>` | ✓（4 参，combat_commands.py:209 等） | ✗ **无索引**（`idx_to_command` 只出 3 参；`_cmd_to_attack_idx` 按 `split(" ",2)` 三段解析，4 参会把「武器 层 属性」整个当武器名） | ✓（parser.py:82-89 支持 layer/attr） | ✓ | **AI 能打精确层/属性，RL 学不到、也回译不了**；AI 4 参攻击若反向喂给 mask 构建会误解析 |

### 4.2 special 指令

| 命令 | AI 生成 | RL 索引 | 引擎解析 | M9 可用性 | 缺口说明 |
|---|---|---|---|---|---|
| `special 磨刀` | ✓（develop_commands.py:342） | ✓ `95` | ✓ | ✓ | — |
| `special 吟唱魔法护盾` | ✗（builder 不生成） | ✓ `96` | ✓ | ✓ | RL/引擎对齐，AI 侧缺生成路径（遗留 mixin 才有） |
| `special 展开AT力场` | ✗（同上） | ✓ `97` | ✓ | ✓ | 同上 |
| `special 蓄力电磁步枪` | ✓（combat/develop/police） | ✓ `98` | ✓ | ✓ | — |
| `special 蓄力高斯步枪` | ✓ | ✓ `99` | ✓ | ✓ | — |
| `special 蓄力<其他武器>` | ✓（`build_charge` 遍历任意 `requires_charge` 武器，combat_commands.py:554） | ✗ 静态表仅含电磁/高斯两个 | ✓（validator startswith 蓄力） | ✓ | **动态蓄力名无法回译 RL**（`SPECIAL_OPS` 固定 13 项） |
| `special 释放病毒` | ✓（orchestrator.py:347） | ✓ `100` | ✓ | ⚠ M9 犯罪检查语义不同 | — |
| `special Hoshino` | ✗（天赋钩子域） | ✓ `101` | ✓ | ✓ | builder 不生成，属 hoshino 钩子 |
| `special 取消盾牌` | ✗（天赋钩子域） | ✓ `102` | ✓ | ✓ | 同上 |
| `special 修复` | ✗（天赋钩子域） | ✓ `103` | ✓ | ✓ | 同上 |
| `special 肾上腺素` | ✗（天赋钩子域） | ✓ `104` | ✓ | ✓ | 同上 |
| `special 更衣{水着-shielder/临战-Archer/临战-shielder}` | ✗（天赋钩子域） | ✓ `105–107` | ✓ | ✓ | 同上 |
| `special 拆卸<模块>`（M4 `m4_gear`） | ✗ | ✗ **无索引** | ✓（special_op.py:12-16 动态枚举） | — | 动态特殊名，RL 无法表达 |

### 4.3 警察 / legacy 指令

| 命令 | AI 生成 | RL 索引 | 引擎解析 | M9 可用性 | 缺口说明 |
|---|---|---|---|---|---|
| `report <目标>` | ✓（police_commands.py:181） | ✓ `108`（`_auto_report_target` 自动填目标） | ✓ | ✗ **M9 不消费**（`m9_police` 取代 legacy `police_engine`，仅剩犯罪记录壳，talents.md:25；举报走 `special 热线举报X`） | **RL 有索引但 M9 引擎不消费** |
| `assemble` | ✓（police_commands.py:138） | ✓ `109` | ✓ | ✗ M9 | 同上 |
| `track`（→ `track_guide`） | ✓（police_commands.py:149） | ✓ `110` | ✓（parser 别名） | ✗ M9 | 同上 |
| `recruit` | ✓（develop:76 / police:186） | ✓ `111` | ✓ | ✗ M9 | 同上 |
| `election` | ✓（develop:78 / police:195） | ✓ `112` | ✓ | ✗ M9（M9 用 `special 竞选队长`） | 同上 |
| `designate <目标>` | ✓（police_commands.py:218） | ✓ `113`（`_auto_target` 自动填目标） | ✓ | ✗ M9 | 同上 |
| `study` | ✓（police_commands.py:60） | ✓ `114` | ✓ | ✗ M9 | 同上 |
| `police move <id> <地点>` | ✓（police_commands.py:485 等） | ✗ **无索引** | ✓（parser → `police_command`；validator:742） | ✗ M9（legacy 语法冻结，talents.md:141） | **RL 无索引、M9 不消费**；仅 legacy 队长路径可用 |
| `police equip <id> <装备>` | ✓（500/509） | ✗ 无索引 | ✓ | ✗ M9 | 同上 |
| `police wake <id>` | ✓（550） | ✗ 无索引 | ✓ | ✗ M9 | 同上 |
| `police attack <id> <目标>` | ✓（621） | ✗ 无索引 | ✓（validator 目标需玩家） | ✗ M9 | 同上 |

### 4.4 M9 special 指令（引擎可解析，AI 不生成、RL 无索引）

| 命令 | AI 生成 | RL 索引 | 引擎解析 | M9 可用性 | 缺口说明 |
|---|---|---|---|---|---|
| `special 破界` | ✗ | ✗ | ✓（special_op.py:104-108 枚举；validator 精确名匹配） | ✓ G3 被困者标准根行动 | **引擎可解析但 AI 不生成、RL 无索引**（talents.md:24,38） |
| `special 武器破界` | ✗ | ✗ | ✓（special_op.py:109-111） | ✓ 同上 | 同上 |
| `special 热线举报<玩家名>` | ✗ | ✗ | ✓（special_op.py:116-125 动态按玩家枚举；execute 211-220） | ✓ T6/朝阳好市民，任意地点不读 SP | 同上 |
| `special 竞选队长` | ✗ | ✗ | ✓（special_op.py:127-130；execute 239-245） | ✓ T6 队长候选 | 同上（M9 版替换 legacy `election` 索引 112） |
| `special 指挥<警员id>移动` | ✗ | ✗ | ✓（special_op.py:132-136 按存活警员枚举；execute 246-253） | ✓ 队长指挥 | 同上（M9 版替换 legacy `police move`） |

### 4.5 M9 NPC 目标（警察单位 / 无人机 / 影身）

| 场景 | AI 生成 | RL 索引 | 引擎解析 | M9 可用性 | 缺口说明 |
|---|---|---|---|---|---|
| `attack <NPC名> <武器>`（M9 actor 目标） | ⚠ 仅 legacy 按 id 生成 `attack policeX ...`（police_commands.py:293）；**M9 actor（无人机/影身/警员）无生成** | ✗ `opponent_slot_by_name` 由 `get_opponent_slots`（仅 `player_order`）构建（action_space.py:513-520）→ NPC 名 `_find_opponent_slot_by_name` 返回 None → `_cmd_to_attack_idx` 返回 -1 → mask False（242-251） | ✓ `_get_opponents` M9 走 `iter_targetable_actors()`（action_enumerator.py:36-42）；`_enumerate_attack` 含 NPC 且免 engaged/lock（386-387, 408-413） | ✓ 引擎枚举含 | **引擎枚举含但 RL `idx_to_*` 映射按 player_order 丢弃**（缺口 3，见 §5） |
| `lock/find <NPC名>` | ✗ | ✗ | ✗ enumerator 显式排除 `_m9_drone_actor/_m9_police_actor`（action_enumerator.py:106-123） | — | 引擎自己也不支持，无缺口 |

### 4.6 T0 发动 / choose 索引（非命令路径）

| 项 | AI 生成 | RL 索引 | 引擎解析 | M9 可用性 | 缺口说明 |
|---|---|---|---|---|---|
| T0 天赋目标槽 `115–119` / 自身 `120` | AI 走 `controller.choose()`（controller.py:465-493 → 天赋钩子/ChooseMixin），**非命令** | ✓ choose-sync：`env.step` 经 `idx_to_choose_option` 翻译（env.py:702-714）；**`idx_to_command` 对 115-136 回退 `forfeit`**（action_space.py:358-362） | choose() 控制器路径，不经 `parse` | ✓ M9 T0 门（talents.md §1） | 仅 choose-sync 路径使用；`idx_to_command` 对 T0/choose 是死代码分支 |
| 通用 choose `121–136` | 同上 | ✓ `env.step` 直接 `action - 121`（env.py:699-701） | choose() 路径 | ✓ | 同上 |
| `talent_activate *` 前缀 | ✗（无 builder 产出） | T0 索引 `115-120` 对应 | ✗ **parser 无 `talent_activate` 分支**（parser.py 全文无） | — | 白名单放行（helpers_mixin.py:445）但引擎无此 action → **白名单与解析器不对称**（若 AI 发出必被 validator 拒） |

### 4.7 主要不对齐项汇总（对照任务要求的四类）

1. **M9 special（破界/武器破界/热线举报X/竞选队长/指挥X移动）**：引擎可解析 ✓，AI 不生成 ✗，RL 无索引 ✗ → 见 4.4。
2. **legacy 警察语法（report/election/assemble/track/police move/equip/attack）**：RL 有索引（108-114）但 M9 引擎不消费 ✗ → 见 4.3。
3. **M9 NPC 目标（警察单位/无人机/影身）**：引擎枚举含 ✓，RL `idx_to_*` 映射按 player_order 丢弃 ✗ → 见 4.5。
4. **T0 发动/choose 索引（115-136）**：仅 choose-sync 路径使用 ✓，`idx_to_command` 回退 `forfeit` ⚠ → 见 4.6。
5. 额外：**AI 4 参攻击** RL 无索引 / **AI `interact 通行证`** 不匹配 `INTERACT_ITEMS` / **动态 special 名（蓄力其他/拆卸模块）** RL 静态表无法表达（见 4.1/4.2）。

---

## 5. 缺口清单（供后续工作引用）

> 每条：现象 → 根因（file:line）→ 影响（AI/RL）→ 建议修法方向（**不实现**）。

### 缺口 1：M9 special 无法被 AI 生成 / RL 表达
- **现象**：`special 破界` / `special 武器破界` / `special 热线举报{玩家}` / `special 竞选队长` / `special 指挥{警员}移动` 在 M9 局由引擎枚举并可执行，但 AI 三个 command_builder + 编排器均不产出，RL 137 索引无对应。
- **根因**：M9 特殊操作仅在 `actions/special_op._append_m9_specials` 动态追加（special_op.py:98-136）；`SPECIAL_OPS` 为 v2exp-era 静态表（action_space.py:79-96），无 M9 条目；AI 构建器无 M9 分支。
- **影响**：M9 局 AI（BasicAI）永远不破界、不热线举报、不竞选队长、不指挥；RL 无法学这些动作。
- **建议方向**：① `SPECIAL_OPS` 按 profile 扩展/参数化（M9 动态 special 映射新增索引段或复用 choose 索引）；② AI 构建器新增 M9 场景分支（破界优先于 move、热线举报替代 report、指挥替代 police move）；③ 索引布局需版本化（静态 v2exp vs M9 动态）。

### 缺口 2：legacy 警察语法在 M9 下成为死代码（RL 索引 108-114）
- **现象**：`report/assemble/track/recruit/election/designate/study` 索引 108-114 在 M9 profile 下 M9 引擎不消费（`m9_police` 取代 legacy `police_engine`，talents.md:25；legacy 警察语法链冻结，talents.md:141）。
- **根因**：索引布局为 v2exp-era 静态设计（action_space.py:99-107），未随 M9 重构；`build_action_mask` 警察段读 `police_engine`/`available_set`（609-632）。
- **影响**：M9 局 RL 会选到引擎不消费的警察动作（浪费/被 validator 拦截重试）；AI 生成的 `police move/equip/wake/attack`（police_commands.py:485-621）在 M9 下无引擎消费。
- **建议方向**：profile 感知索引布局；M9 下将 108-114 段重映射到 M9 警务动作或屏蔽；`police_command` 语法在 M9 下替换为 `special 指挥X移动`。

### 缺口 3：M9 NPC 目标（警察单位/无人机/影身）无法进入 RL 动作空间
- **现象**：`engine/action_enumerator._get_opponents` 在 M9 下返回 `iter_targetable_actors()`（含 NPC，action_enumerator.py:36-42），`_enumerate_attack` 也包含 NPC（386-387, 408-413），但 `rl/action_space` 的对手名→槽位映射基于 `get_opponent_slots`（仅 `player_order`，action_space.py:513-520），NPC 名查不到槽 → `_cmd_to_attack_idx` 返回 -1（242-251）→ mask False。
- **根因**：RL 槽位固定 0-4 且只映射玩家；M9 actor 无稳定槽位编号协议。
- **影响**：M9 局 RL 无法攻击无人机/影身/警员（引擎枚举到、RL 学不到）；`idx_to_command` 也无法回译 NPC 目标命令。
- **建议方向**：为 M9 actor 定义统一槽位/actor_id 映射（如 `iter_targetable_actors` 顺序生成槽位，超出 5 槽扩展索引段），或让 `_cmd_to_*` 走 `resolve_player_target` 式 actor 解析（parser.py:208-222 已支持）。

### 缺口 4：T0/choose 索引（115-136）与命令路径割裂
- **现象**：115-120（T0 目标/自身）与 121-136（choose 选项）仅 choose-sync 路径（`env.step` 解释，env.py:697-717）使用；`idx_to_command` 对 115-136 恒回退 `forfeit`（action_space.py:358-362）。
- **根因**：`build_action_mask` 两模式互斥（456-458）设计使然；但多处 docstring 仍引用旧区间 108-129（见 §1.6），且 `STRATEGIC_CHOOSE_SITUATIONS`（136-166）与 `STRATEGIC_SITUATIONS`（642-689）双集合冗余。
- **影响**：RL 若在非 choose 模式输出 115-136 会静默变 forfeit（`idx_to_command` 无报错），训练信号被吞；注释误导后续维护。
- **建议方向**：统一 situation 集合为单一信源；`idx_to_command` 对越界进入 T0/choose 区间改为显式错误或日志；修正过时注释。

### 缺口 5：AI 4 参攻击（层/属性）无法回译 RL
- **现象**：`attack {目标} {武器} {层} {属性}`（combat_commands.py:209, 248, 258, 288, 329, 339）引擎可执行（parser.py:86-89 解析 layer/attr），但 RL `idx_to_command` 只产出 3 参（action_space.py:333-340），`_cmd_to_attack_idx` 按 3 段解析 4 参会把「武器 层 属性」整体当武器名（`WEAPONS.index` 抛 ValueError，action_space.py:244-251）。
- **根因**：RL attack 布局只编码 目标槽×武器槽（`slot*10+weapon`，action_space.py:61），无层/属性维度。
- **影响**：AI 可打护甲克制的精确层/属性，RL 无法学习；AI 4 参命令若进入 mask 构建路径会异常。
- **建议方向**：attack 索引扩展层/属性维度（如 `slot*10 + weapon*4 + layer_attr` 或参数化枚举）；或 AI 侧限制为 3 参，层/属性由引擎默认选择。

### 缺口 6：AI 命令与 `INTERACT_ITEMS` 命名不一致（`interact 通行证`）
- **现象**：AI 生成 `interact 通行证`（police_commands.py:312），而 `INTERACT_ITEMS` 中的正式名是 `办理通行证`（action_tables.py:37）；RL `_cmd_to_interact_idx` 直接 `INTERACT_ITEMS.index(item)`（action_space.py:210-213）遇「通行证」抛 ValueError。
- **根因**：parser 有 `通行证→办理通行证` 别名（parser.py:43-47），AI 侧复用了别名而 RL 表用正式名。
- **影响**：该 AI 命令无法回译 RL 索引；若 AI 命令被反哺用于训练数据会异常。
- **建议方向**：AI 统一使用正式名 `办理通行证`，或 `_cmd_to_interact_idx` 复用 parser 别名表。

### 缺口 7：动态 special 名（蓄力其他 / 拆卸模块）超出静态 `SPECIAL_OPS`
- **现象**：`special 蓄力<其他需蓄力武器>`（combat_commands.py:554 遍历全部 `requires_charge`）与 M4 `special 拆卸<模块>`（special_op.py:12-16）为动态名；`SPECIAL_OPS` 静态仅 13 项（action_space.py:79-96），RL 无索引；validator 靠 `startswith(蓄力/更衣/修复)` 放行（validator.py:596-600）。
- **根因**：RL 静态枚举与引擎动态枚举不一致（`_enumerate_special` 委托 `get_available_specials`，action_enumerator.py:421-428）。
- **影响**：新武器/模块加入后 RL 无法蓄力/拆卸；AI 生成的动态 special 名 RL 学不到。
- **建议方向**：special 索引段参数化（前缀槽 + 参数槽）或按 profile 重算静态表；至少保证 `SPECIAL_OPS` 覆盖所有 `requires_charge` 武器。

### 缺口 8：`talent_activate` 白名单与解析器不对称
- **现象**：`_validate_command` 无条件放行 `talent_activate` 前缀（helpers_mixin.py:445），但 `cli/parser.py` 无 `talent_activate` 分支（parser.py 全文检索无），validator 也无对应 action（validator.py:133-184）。
- **根因**：白名单为旧架构遗留（T0 天赋激活曾以命令形式出现），现 T0 走 choose() 路径。
- **影响**：若任何路径发出 `talent_activate *`，引擎 `parse` 返回 None → 行动被拒；白名单产生「看起来合法实则不可执行」的误导向。
- **建议方向**：从白名单移除 `talent_activate`（或补 parser 分支），保持白名单 = 引擎可解析集合的超集收敛。

---

<!-- ============================================================
     源码锚点注释块（file:line 均已对照源码核实）

     §1 RL 137 布局（rl/action_space.py）
     - ACTION_COUNT=137: action_space.py:50
     - 偏移常量: IDX_FORFEIT=0 (55), IDX_WAKE=1 (56), IDX_MOVE_BASE=2 (57),
       IDX_INTERACT_BASE=8 (58), IDX_LOCK_BASE=35 (59), IDX_FIND_BASE=40 (60),
       IDX_ATTACK_BASE=45 (61), IDX_SPECIAL_BASE=95 (62), IDX_POLICE_BASE=108 (63),
       IDX_TALENT_T0_TARGET_BASE=115 (66), IDX_TALENT_T0_SELF=120 (67), IDX_CHOOSE_BASE=121 (68)
     - SPECIAL_OPS 13 项: action_space.py:79-96; POLICE_CMDS 7 项: 99-107
     - SPECIAL_REQUIRES: 111-127
     - get_opponent_slots（player_order 排序,不足补 None）: 181-195
     - _cmd_to_move_idx: 204-207; _cmd_to_interact_idx: 210-213; _cmd_to_lock_idx: 224-230;
       _cmd_to_find_idx: 233-239; _cmd_to_attack_idx（slot*10+weapon）: 242-251
     - _auto_target: 254-264; _auto_report_target: 267-290
     - idx_to_command: 301-364（0→forfeit 311; 1→wake 314; 2-7→move 317; 8-34→interact 320;
       35-39→lock 323; 40-44→find 328; 45-94→attack slot//10,weapon%10 333; 95-107→special 342;
       108-114→police（report/designate 自动目标）345; 115-120→forfeit 358; 121-136→forfeit 361; 越界→ValueError 364）
     - idx_to_choose_option: 371-427（115-119 对手槽 396; 120 自身 397; 121-136 options[i] 420）
     - build_action_mask: 434-634（choose 模式 465; 未醒 wake 481; forfeit 487; 星野盾 489-497;
       move 501; interact 507; lock/find/attack 委托 enumerator + opponent_slot_by_name 511-541;
       special 543-607; 警察 609-632）
     - STRATEGIC_CHOOSE_SITUATIONS: 136-166; STRATEGIC_SITUATIONS: 642-689
     - _build_choose_mask: 692-791（talent_t0 713; _TARGET_SITUATIONS 723-736; _SELF_SITUATIONS 770-772; 通用 781-789）

     §2 AI 生成层
     - _validate_command 白名单: helpers_mixin.py:433-447（available 442-443; police/talent_activate/special 445）
     - CombatCommandBuilder: combat_commands.py（蓄力 57/138/175/554; 4参attack 209/248/258/288/329/339;
       3参attack 211/251/290/329/341; move 216/240/265/335; find 235/241; lock 283;
       interact 热成像仪 389/探测魔法 394/雷达 397; interact 魔法弹幕 586/远程 588/地震 590/地动山摇 592/高斯 598/电磁 600）
     - DevelopCommandBuilder: develop_commands.py（recruit 76/election 78/move警察局 80; current_location_actions 87;
       蓄力 92-95/101; move best 111; attack 121-124; move fallback 131; interact 盾牌 154/陶瓷护甲 161/打工 163/魔法护盾 167/晶化 177/AT力场 183;
       防毒面具 159/207/209; move safe 187; 封闭 215; move 商店/医院/魔法所 225/227; special 磨刀 342）
     - PoliceCommandBuilder: police_commands.py（study 60; police move 485/494/530/537/597/617; police equip 500/509;
       police wake 550; police attack 621; assemble 138; track 149; report 181; recruit 186; election 195;
       designate 218; move警察局 224/226; 蓄力 290/306; attack police id 293; move unit 295; interact 通行证 312/电磁 314/地动山摇 324/地震 326; move 军事基地 316/333/魔法所 328）
     - Orchestrator: orchestrator.py（wake 323; special 释放病毒 347; move 662; forfeit 359/382/430/638; GoalStack 1123-1141）
     - 调用链: controller.py:get_command 308-453（orchestrator.generate 406; attempt 重试 422-453）

     §3 引擎解析/枚举层
     - cli/parser.py parse: 4-205（wake 12-26; move 28-35; interact 37-48; lock/find 50-60;
       applause/shoot/hook 62-80; attack 4参 82-89; special 91-103; report 105-109; assemble 111-113;
       track 115-117; recruit 119-121; election 123-125; designate 127-131; study 134-136;
       police 138-186; police_status 188-191; forfeit 193-195; status/allstatus/help 197-205）
     - resolve_player_target（含 actor）: parser.py:208-222
     - engine/action_enumerator.py build_action_options: 61-149（无参不枚举 70-72; move 77; interact 83;
       lock/find NPC排除 103-124; attack 含NPC 127-130; special 动态 133-136; report/designate 139-147）
     - _get_opponents（M9 iter_targetable_actors）: 34-54
     - _enumerate_attack（结界过滤 381-384; is_m9_npc 386-387; 射程判定 408-416）: 361-418
     - _enumerate_lock: 311-338; _enumerate_find: 341-358; _enumerate_special: 421-428;
       _enumerate_report: 431-446; _enumerate_designate: 449-451
     - cli/validator.py: validate 133-184; attack police分支 522-534; special startswith 575-605;
       police_command 742-796; _check_barrier_block 67-77

     §4/§5 相关
     - actions/special_op.py: get_available_specials 7-95（M4 拆卸 12-16; 磨刀 19-22; 吟唱 25-30; 展开 33-38;
       蓄力 41-49; 释放病毒 52-53; 取消盾牌 56-58; 更衣 61-69; Hoshino 71-75; 修复 78-81; 肾上腺素 84-88;
       M9 90-93）
     - _append_m9_specials: 98-136（破界 104-111; 热线举报 116-125; 竞选队长 127-130; 指挥移动 132-136）
     - execute（破界/热线/竞选/指挥）: 211-253
     - 主命令文档: docs/operations/commands.md（V2.0 门控; 星野宏 §三; M9 相关见 docs/m9/ai/talents.md §0:38 明确
       「AI 命令层必须能生成这些字符串」）
     - M9 术语（docs/m9/ai/talents.md）: 目标枚举含玩家/警察单位/无人机/影身（§0:37）; m9_police 取代 legacy
       police_engine（§0:25）; legacy 警察语法链冻结（§4.2:141）
     ============================================================ -->
