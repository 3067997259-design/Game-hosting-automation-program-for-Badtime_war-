# 游戏指令总表（commands.md）

> **定位**：全游戏命令系统的**单一契约文档**——覆盖 M9 更新完成后**现役**可解析的指令字符串、参数格式、前置条件、效果与归属。跨 profile 使用（legacy / v2exp / m9-rfc）：M9 只**新增**特殊操作并**改写**部分老指令语义（见 §5，逐行标注）。
> **范围纪律**：本文档**只梳理现役指令**（M9 改动完成后引擎仍接受且有意义的指令）。已退役/冻结的指令（legacy 警察引擎指令、G2/G5 演唱等）一律移入[附录：退役/冻结对照](#附录退役冻结对照)，不进入 AI 命令层契约；只有 `commands_mapping.md`（契约对齐侧）为解释 RL 静态索引而保留 legacy 标注。
> **与 talents.md 的关系**：`docs/m9/ai/talents.md` 管**天赋决策点**（T0 选项、SP 经济、世界事实读写、评分器草案）；本文档管**指令契约**（可解析字符串、参数格式、前置/效果、归属）。AI 命令层必须能生成本文档 §1/§2 中的每个合法字符串，术语与 talents.md §0 共享世界模型一致（公演位 / 掩体 / m9_police / 世界事实 / 结界边界）。
> **分片**：
> - 三方映射表 [commands_mapping.md](commands_mapping.md)（RL 索引 ↔ AI 生成 ↔ 引擎解析；保留 legacy 索引标注）
> - T0 选项层 [commands_choose.md](commands_choose.md)（引擎 T0 流程 + 天赋特异选项）

---

## 1. 引擎标准指令（通用层）

引擎 `cli/parser.py` 可解析的全部动作类指令（`engine/action_turn.py` T1 流程 = controller 取命令 → parse → validate → execute）。**M9 语义变更**列仅标注与 legacy/v2exp 相比发生变化或新增的语义；`—` 表示三 profile 语义一致。

### 1.1 动作类指令总表

| 指令（别名） | 参数格式 | 前置条件 | 效果摘要 | 备注（M9 语义变更） |
|---|---|---|---|---|
| `wake` / `起床` / `w` | `wake`；`wake <警察ID>`（唤醒警察的 legacy 分支见附录） | 未起床 | 起床，出现在自己家中（G2 舞台内 `liberamente_vivace` 不覆盖已分配座位）；触发天赋 `on_wakeup` 加成 | — |
| `move` / `移动` / `m` / `go` | `move <地点名>`；`home` / `家` / `回家` → `home_<玩家ID>` | 已起床；目的地合法（5 固定地点 + `home_<pid>`）；架盾者自身禁移；星野持盾/半进入/借机攻击等障碍检查；Terror 存活不禁止 | 移动到目标地点；清锁定/面对面标记；触发借机攻击（K 模式）、架盾阻碍/半进入突破、烟雾隐身、军事基地强买通行证、进入他人家/军事基地犯罪检查、超新星、全息影像进入 | **M9：G3 固有结界困住时普通移动不能离开结界地点**（强制位移豁免并同步释出结界身份 `release_from_barrier`）；普通 move 记入模板池（G6） |
| `interact` / `交互` / `i` / `get` / `拿` / `学` / `做` / `买` | `interact <项目名>`；简称映射：`通行证`/`办通行证` → `办理通行证` | 已起床；当前位置可交互（`can_interact`）；半进入/持盾/架盾/Terror 存活/结界限制；M5 白昼限量营业 | 按地点模块交互：home/商店/魔法所/医院/军事基地/警察局（27 个可交互条目，见 1.2） | 普通 interact 记入模板池（G6） |
| `lock` / `锁定` / `l` | `lock <目标>` | 已起床；持有远程武器；目标在地图/可见/未被锁定；烟雾（星野除外）与全息影像限制 | 放置 `LOCKED_BY` 标记（远程攻击前置）；看穿隐身目标加 `DETECTED_BY` | 前置经济不变；普通 lock 记入模板池（G6） |
| `find` / `找到` / `找` | `find <目标>` | 已起床；同地点；可见；未面对面；烟雾（星野除外）与全息影像限制 | 建立双向 `ENGAGED_WITH` 标记（近战前置）；触发剪刀手一突 `on_find_someone`/`on_found_by_someone` 钩子 | **find 顺带拾取本地点箭堆与击杀掉落**（M4 v2.0 §2.8 / M5 v2.0 §6.4，风险换弹药）；普通 find 记入模板池（G6） |
| `applause` / `喝彩` / `cheer` | `applause <用途>`：`伤害加成`(1) / `重掷先攻`(1) / `偷看先攻`(2) / `抵消犯罪`(2) | experiment `m6_scoring`；喝彩点足够 | 消耗喝彩：下次攻击伤害 +2；下轮 R1 先攻重掷；揭示下轮先攻顺序；抵消一条犯罪记录（无犯罪可抵消则退款）；多数不消耗回合 | — |
| `shoot` / `射箭` / `射` | `shoot <目标>` | experiment `m4_gear`；已起床；有「弓」；箭 ≥ 1 或无限模块；目标存活/可见/非自己；爱愿检查 | 弓专属攻击：跨地点合法（唯一常规跨地点武器）；锁定非前置只是命中加成（未锁定吃 `accuracy.unlocked_ranged_penalty` −15）；消耗 1 箭（落箭入目标地点箭堆，find 免费拾回）；按已装模块结算（burn/击退等） | — |
| `hook` / `钩索` / `钩` | 拉人：`hook <目标>`；拉己：`hook self <地点>` / `hook 自己 <地点>` | experiment `m4_gear`；已起床；持有「钩索」；冷却就绪（`hook.cooldown_rounds` 默认 2）；拉人：目标异地存活可见、非自己、爱愿检查 | 拉人：任意地点目标拽至己方地点 + 2 科伤（走命中骰；擦钩=不位移但目标本轮闪避全失效）；拉己：钩向任意地点位移，**不触发借机攻击**；双模式共享冷却 | — |
| `attack` / `攻击` / `atk` / `打` | `attack <目标> <武器> [层 属性]`；层=`外层`/`外`/`outer`、`内层`/`内`/`inner`；属性=`普通`/`ordinary`、`魔法`/`magic`、`科技`/`tech` | 已起床；多武器必须指定武器名（单武器自动补全）；近战需同地点 + find（ENGAGED_WITH）；远程需 lock + 可见（导弹须军事基地 + `MISSILE_CTRL` 标记）；范围需同地点他人；蓄力强制武器须已蓄力；六爻封印武器不可用；爱愿检查；Terror 攻击跳过常规校验 | 按武器射程结算伤害：近战→目标护甲（可选层/属性，指定且不存在时自动降级）；远程→命中/可见体系；范围→同地点所有人；v2.0 duet 走位移/热力路径 | **M9：走 M9 结算管线**——G3 结界边界拦截（`attack_crosses_active_barrier`）、无人机（G0）与警察 NPC（m9_police）actor 路由、玩家掩体吸收（A 阶段）、G3 远程防壁（七重圆环/拦截剑阵）、`engine.m9.combat.resolve_damage`（临时 HP 链/免死/保险/absolute_death 裁决）；普通 attack 记入模板池（G6） |
| `special` / `特殊` / `sp` / `操作` | `special <操作名>`；无参 → 交互式选择；简称映射：`磨`→`磨刀`、`吟唱`→`吟唱魔法护盾`、`展开`→`展开AT力场`、`病毒`/`放毒`→`释放病毒` | 已起床；操作名在当前可用列表（`蓄力`/`更衣`/`修复` 支持前缀匹配） | 执行特殊操作（全量见 §2） | **M9：新增 破界 / 武器破界 / 热线举报X / 竞选队长 / 指挥X移动**（`sing`/`演唱`/`唱` 别名与 G2/G5 演唱交互式入口已随舞台退役，见附录） |
| `police_status` / `警察状态` / `警状态` | `police_status` | 恒可用 | 查看警察状态 | — |
| `forfeit` / `放弃` / `f` / `pass` / `skip` | `forfeit` | 恒可用 | 放弃行动（视为已行动，未行动保底清零；不视为带效果的行动类型） | **M9：石化单位 T0 = `forfeit` 或同槽挣脱（1 SP/次）**；槽位收尾 `voluntary_forfeit` |
| `status` / `状态` / `s` | `status` | 恒可用 | 查看自身状态 | — |
| `allstatus` / `全场` / `all` / `a` | `allstatus` | 恒可用 | 查看全场状态 | — |
| `help` / `帮助` / `h` / `?` | `help` | 恒可用 | 帮助信息 | — |

> 注：`s` 在 `police s` 子命令中代表警察状态查看；顶层 `s` 代表 `status`（解析顺序：police 分支先于 status）。
> 注：legacy 警察引擎指令（举报 / 集结 / 追踪 / 入警 / 竞选 / 指定目标 / 研究性学习 / 警察四子命令 / 唤醒警察等 legacy 形式）在 M9 下不被 `m9_police` 消费（案件驱动取代），已移入[附录](#附录退役冻结对照)，不再属于现役 AI 命令契约。

### 1.2 参数字典（枚举层，`engine/action_tables.py` 单一数据源）

**地点（6 基础）**：`home`（归一化 `home_<pid>`）、`商店`、`魔法所`、`医院`、`军事基地`、`警察局`。`move` 的 `ALL_LOCATIONS` = 商店/魔法所/医院/军事基地/警察局 + 各玩家 `home_<pid>`。

**可交互条目（27）**：`凭证`、`小刀`、`盾牌`（home）；`打工`、`磨刀石`、`隐身衣`、`热成像仪`、`陶瓷护甲`、`防毒面具`（商店，防毒面具另在医院）；`魔法护盾`、`魔法弹幕`、`远程魔法弹幕`、`封闭`、`地震`、`地动山摇`、`隐身术`、`探测魔法`（魔法所）；`晶化皮肤手术`、`额外心脏手术`、`不老泉手术`（医院）；`办理通行证`、`AT力场`、`电磁步枪`、`导弹控制权`、`高斯步枪`、`雷达`、`隐形涂层`（军事基地）。

**武器（10，RL 固定顺序）**：`拳击`（0，恒可用）、`小刀`、`警棍`、`魔法弹幕`、`远程魔法弹幕`、`地震`、`地动山摇`、`电磁步枪`、`高斯步枪`、`导弹`。射程分类：近战（拳击/小刀/警棍）、远程（远程魔法弹幕/电磁步枪/高斯步枪/导弹，导弹须军事基地 + MISSILE_CTRL）、范围（魔法弹幕/地震/地动山摇）。

**喝彩用途（4）**：`伤害加成`(1) / `重掷先攻`(1) / `偷看先攻`(2) / `抵消犯罪`(2)。

> 注：往世层星光行动（`拨弄命运`/`预兆`/`加冕`）在**设计层已被 M9 往世层新设计取消**（B4 v0.4：PP 合并旧喝彩与星光；M8 重做计划按 B4 重写星光策略），但**代码层 `m9-rfc` 仍启用 `m6_scoring`**（experiments.py:42），R4 星光阶段仍在运行（round_manager.py:1052-1053）——属"设计已取代、实现未拆除"状态，已移入[附录](#附录退役冻结对照)。

---

## 2. 天赋特异指令（special op 层）

`actions/special_op.py` 登记的全部特殊操作。**归属**列 = 天赋/系统；**通用 or 天赋特异**列 = 是否仅特定天赋持有者可用。参数化操作名（`蓄力X`/`更衣X`/`修复X`/`热线举报X`/`指挥X移动`）按前缀匹配。

| special 名 | 参数 | 前置 | 效果 | 归属（天赋/系统） | 通用 or 天赋特异 |
|---|---|---|---|---|---|
| `磨刀` | 无 | 持有「磨刀石」+ 有小刀且 `base_damage < 2`（hp20：`balance.weapons.磨刀小刀.damage`=7） | 消耗磨刀石，小刀伤害提升至 2（hp20：7）；log_event `sharpen` | 系统通用 | 通用 |
| `吟唱魔法护盾` | 无 | 已学会「魔法护盾」且外层魔法护甲缺失（`armor.get_piece(OUTER, MAGIC)` 为 None） | 重新吟唱魔法护盾：已持有 → 耐久 +`repair_amount`（不超上限）；未持有 → 重新创建（hp20 增量制） | 系统通用 | 通用 |
| `展开AT力场` | 无 | 已学会「AT力场」且外层科技护甲缺失 | 重新展开 AT 力场（同上增量制） | 系统通用 | 通用 |
| `蓄力<武器名>` | `<武器名>` | 武器 `requires_charge` 且未蓄力且非六爻封印 | `is_charged = True`；log_event `charge` | 系统通用 | 通用 |
| `释放病毒` | 无 | 在医院 且 病毒未激活 | `virus.release`：全体感染，5 轮后未获防毒面具/封闭死亡，病毒期间商店物品免费；犯罪检查（`释放病毒` 在 crime_types 时：剪刀手一突 `on_crime_check`、警察成员/好市民扩展禁止） | 系统通用 | 通用 |
| `拆卸<模块名>` | `<模块名>`（弓模块） | experiment `m4_gear` 且持有弓模块（1 行动随处可做，拍板 §13-16） | 拆下弓模块「X」（回流市场）；`engine/bow_modules.uninstall` | 系统通用（M4） | 通用 |
| `取消盾牌` | 无 | G7 星野 `shield_mode ∈ {架盾, 持盾}` | 结束架盾/持盾状态；log_event `cancel_shield`；**不消耗回合** | 天赋 G7（星野） | 天赋特异 |
| `更衣<形态>` | 形态 ∈ {`水着-shielder`, `临战-Archer`, `临战-shielder`}；空参数时 choose 选择 | G7 星野 且 在自己家中（`home_<pid>`）且 形态 ≠ 当前 | 切换形态；log_event `change_form` | 天赋 G7（星野） | 天赋特异 |
| `Hoshino` | 无（宏内再取指令） | G7 战术已解锁（`tactical_unlocked`）、非 Terror、铁之荷鲁斯 HP>0 或荷鲁斯之眼存在 | 发动战术指令宏（TACTICAL_COST 指令序列：射击/投掷/find/lock/move/转向/排弹…，`terminal` 结束）；是否消耗回合由宏返回 | 天赋 G7（星野） | 天赋特异 |
| `修复[<护甲名>]` | `<护甲名>`（可选；盾牌/AT力场），空参数自动检测材料 | G7 融合盾完成（`fusion_shield_done`）且 铁之荷鲁斯受损（`iron_horus_hp < max`） | 消耗一件盾牌/AT力场修复铁之荷鲁斯 +`repair_amount`（`talent_num g7.iron_horus_repair`） | 天赋 G7（星野） | 天赋特异 |
| `肾上腺素` | 无 | G7 未使用过肾上腺素且持有「肾上腺素」药物 | 注射肾上腺素（下回合 cost+5 + 光环全恢复 + D4+3 行动顺序）；**不消耗回合**；log_event `adrenaline` | 天赋 G7（星野） | 天赋特异 |
| `破界` | 无 | **M9**（m9_enabled）；存在固有结界（`active_barrier`）且自己被困（`_is_trapped`）且非结界持有者 | 无武器无命中，稳定 `break_action_power`（默认 2）扣结界锚点结构耐久；归零 → 结界强制解除 | 系统（G3 固有结界机制；被困者标准根行动） | 通用（M9 特有） |
| `武器破界` | 无（choose 选武器，失败取首个） | 同 `破界` 且有未封印武器 | 以一件武器攻击结界锚点：攻击方结果 A 直接扣结构耐久（无目标侧 H）；归零 → 结界强制解除 | 系统（G3 固有结界机制） | 通用（M9 特有） |
| `热线举报<玩家名>` | `<玩家名>` | **M9**；T6 朝阳好市民；警察局未停机；目标存活非自己；证据资格成立（受害者/同地点目击者/系统归因探测器/T6 特别线索） | 任意地点举报（**不读 SP、不占 T0**，标准根行动）；举报前检失败不耗证据/槽；成功立即登记唯一通缉；结界内外不能举报 | 天赋 T6（朝阳好市民） | 天赋特异（M9） |
| `竞选队长` | 无 | **M9**；`m9_police.captain_id` 空缺；非 Terror；未在候选队列 | 登记警队队长候选（先到先得，可随时退出；R2 判定上任，失效候选自动让位） | 系统（m9_police；T6 警察语境，任意非 Terror 玩家可用） | 通用（M9 特有） |
| `指挥<警员ID>移动` | `<警员ID>`（如 `unit1`） | **M9**；当前队长本人（`captain_id == player`）；警员存活 | 指挥警员移动到警察局（`captain_command(move, 警察局)`）；Terror 队长不能命令移动 | 系统（m9_police 队长；T6 警察语境） | 天赋特异（M9 队长） |
| `PP重掷先攻` | 无 | **M9**；PP 余额 ≥1；非 Terror | 消耗 1 PP：下轮 R1 先攻骰重掷（B4 §3.3 生前消耗，接替旧喝彩用途） | 系统（PP 经济） | 通用（M9 特有） |
| `PP加伤` | 无 | **M9**；PP 余额 ≥1；非 Terror | 消耗 1 PP：下次攻击伤害 +2 | 系统（PP 经济） | 通用（M9 特有） |
| `PP偷看先攻` | 无 | **M9**；PP 余额 ≥2；非 Terror | 消耗 2 PP：下轮先攻顺序对你揭示 | 系统（PP 经济） | 通用（M9 特有） |
| `PP抵消犯罪` | 无 | **M9**；PP 余额 ≥2；非 Terror | 消耗 2 PP：清除一条犯罪记录（无记录退款） | 系统（PP 经济） | 通用（M9 特有） |
| `交易<玩家名>` | `<玩家名>`（金额执行时输入） | **M9**；PP 余额 ≥1；目标非自己 | 向目标玩家转移 PP（B4 §五 交易系统） | 系统（PP 经济） | 通用（M9 特有） |
| `卸甲免费find` | 无（choose 选目标） | **M9**；G1 卸甲常态；本轮未用过免费 find | 每轮一次免费 find（不占行动槽；G1 §2.1 发育加速） | 天赋 G1（火萤IV型） | 天赋特异（M9） |

### 2.1 补充：G2/G5 演唱类特殊指令（无参 `special` 交互式）

| 指令 | 触发 | 效果 | 归属 |
|---|---|---|---|
| `special`（无参） | G2 ish-bosheth 活跃/duet 且为 G2 持有者 | `execute_sing` 交互式选曲演唱 | 天赋 G2 |
| `special`（无参） | G5 duet 伴唱（`duet_g5_pid`） | `execute_harmonize` 伴唱 | 天赋 G5 |
| `special 追寻那道光`（别名 `追光`/`光`） | G2 演唱 | 追寻那道光·Soave(1费)/Sognando(2费) | 天赋 G2 |
| `special 拼接遗憾`（别名 `遗憾`/`拼接`） | G2 演唱 | 拼接遗憾·Placido(1费)/Zeffiroso(2费) | 天赋 G2 |
| `special Before light`（别名 `光色`/`bl`） | G2 演唱 | Before light·Riposato(1费)/Dolente(2费) | 天赋 G2 |

> 注：`rl/action_space.py` 固定枚举 13 条具体 special 字符串：磨刀 / 吟唱魔法护盾 / 展开AT力场 / 蓄力电磁步枪 / 蓄力高斯步枪 / 释放病毒 / Hoshino / 取消盾牌 / 修复 / 肾上腺素 / 更衣水着-shielder / 更衣临战-Archer / 更衣临战-shielder（RL 索引 95–107）。M9 特殊操作 5 项（破界 / 武器破界 / 热线举报 / 竞选队长 / 指挥）由 `special_op._append_m9_specials` 动态挂出，不在静态 RL 表内（详见 commands_mapping.md §5 缺口清单）。

---

## 附录：退役/冻结对照

> **本附录只做对照，不构成 AI 命令层契约**。下列指令在 legacy/v2exp profile 下可能仍可解析，但 **M9 更新后已退役、冻结或被取代**；后续开发不得以本表内容为现役依据。

| 指令/语法 | 原归属 | 退役/冻结原因 | M9 替代 |
|---|---|---|---|
| `report <目标>` | legacy 警察引擎 | `m9_police` 案件驱动取代 `report_phase` 流转 | `special 热线举报X`（T6，任意地点、不读 SP、证据资格四类） |
| `assemble` / `track` | legacy 警察引擎 | R2 自动出动与 lead 分配取代手动集结/追踪 | 无（R2 自动推进） |
| `recruit` / `加入警察` | legacy 警察引擎 | 固定警力编制取代玩家入警 | 无（`m9_police.ensure_roster` 固定编制） |
| `election` | legacy 警察引擎 | 竞选进度 3/3 制退役 | `special 竞选队长`（候选队列先到先得，R2 上任） |
| `designate <目标>` | legacy 队长 | 执法 lead 由 R2 自动分配 | `captain_command redesignate`（m9_police 队长） |
| `study` | legacy 队长 | legacy 威信体系 | `m9_police.authority`（队长上任威信） |
| `police move/equip/attack/wake <警察ID>` | legacy 队长 | legacy `police_engine` 语义不被 m9 警务推进消费 | `special 指挥X移动`；m9 队长命令 `captain_command` |
| `wake_police <警察ID>` | legacy 警察唤醒 | M9 警察单位唤醒走队长命令 | `captain_command wake` |
| `special`（无参交互式演唱）+ `追寻那道光`/`拼接遗憾`/`Before light` + `sing`/`演唱`/`唱` 别名 | G2 旧全桌舞台 / G5 duet | 旧舞台与 duet 冻结（B3 冻结）；M9 G2 为光影双身+终曲、M9 G5 无 duet | M9 G2 影身/终曲机制；G5 锚定脚本与十四诗篇 |
| 星光行动 `拨弄命运`/`预兆`/`加冕` | M6 往世层（round_manager 驱动，非解析指令；experiment `m6_scoring`） | **设计层已取消**：B4 v0.4 用 PP 合并旧喝彩与星光（`m9_pp_afterlife_betting_rfc_v0.4.md:22,40-42,54`），往世层重定义为"投资者 + 魂援提供者"博弈；M8 重做计划将星光策略列为"按 B4 重写"（`m8_basicai_refactor.md:215`）。**实现未拆除**：`m9-rfc` profile 仍含 `m6_scoring`（experiments.py:42），R4 `_process_starlight` 仍运行（round_manager.py:1052-1053），星光行动在 M9 局中仍实际发生 | PP / 往世层投注与魂援（B4 v0.4）；生死评分与水晶花挂接（评分指针 v0.1）；星光机制随 B4 落地拆除 |

---

## 3. 三方映射表 → 见 [commands_mapping.md](commands_mapping.md)

RL 动作索引 ↔ AI 生成指令字符串 ↔ 引擎解析（parser/validator/action_enumerator）的三方映射由该分片文档维护。

---

## 4. T0 选项层 → 见 [commands_choose.md](commands_choose.md)

引擎 T0 流程（`get_t0_option` / `execute_t0`、SP 即演/公演、公演位报名固化）与天赋特异 T0 选项（T6 联防整备、G0 召唤/无人机、G2 影身/终曲、G3 结界、G4 负世、G5 锚定/诗篇、G7 战术指令等）由该分片文档维护。

---

## 5. M9 语义变更注记

M9（profile: m9-rfc）对 legacy/v2exp 指令语义的**全部变更**汇总：

1. **find/lock 前置经济**：find 仍是近战前置（ENGAGED_WITH），但 **find 顺带拾取本地点箭堆与击杀掉落**（M4/M5，v2.0 §2.8/§6.4）——风险换弹药，AI 经济须折算（`actions/find_target.py:33-66`）；lock 仍为远程前置（LOCKED_BY），前置经济不变。
2. **report → 热线举报**：legacy `report` 走 `police_engine.report_phase`；M9 下 T6 朝阳好市民使用 `special 热线举报X`（任意地点、不读 SP、不占 T0、案件驱动、证据资格封闭清单、举报前检失败不耗证据/槽）。
3. **election → 竞选队长**：legacy `election` 为竞选进度 3/3 制；M9 下为 `special 竞选队长`（候选队列先到先得，R2 判定上任；失效候选自动让位；竞选减免退役）。
4. **attack 走 M9 结算管线**：G3 结界边界拦截（`attack_crosses_active_barrier`）；无人机（G0 `_m9_drone_actor`）与警察 NPC（m9_police `_m9_police_actor`）actor 路由；玩家掩体吸收（`station.player_cover`/`absorb_player_cover`，A 阶段）；G3 远程防壁（七重圆环/拦截剑阵 `defend_ranged`）；`engine.m9.combat.resolve_damage`（临时 HP 吸收链、免死/保险、`absolute_death` 死亡裁决）。
5. **警察指令 m9_police 不消费 legacy report/designate 等**：`m9_police` 取代 legacy `state.police_engine`（仅剩犯罪记录壳）；R2 自动分配执法 lead、R4 自动执法；队长命令走 `captain_command`（move/attack/wake/redesignate）；legacy assemble/track/recruit/designate/study 语义不再驱动 m9 警务推进。
6. **move 被 G3 结界困住**：被困单位普通移动不能离开结界地点（需 `special 破界` 或武器攻击锚点）；强制位移豁免（六爻放逐/涟漪强制位移/全息吸引）并同步释出结界身份（`release_from_barrier`）。
7. **新增 special 破界 / 武器破界**：被困者标准根行动（无武器无命中稳定扣锚点 / 武器 A 直接扣结构耐久）。
8. **普通行动入模板池（G6）**：move/interact/find/lock/attack 普通行动记入模板池（类别去重、窗口 1 轮）；T0 演出恒记 `talent_t0` 不入池。
9. **石化 T0**：石化单位 T0 = `forfeit` 或同槽挣脱（1 SP/次）。
10. **T0 演出与 SP 经济**：SP ∈ {0,1,2}；即演 −1、公演 −2；公演位 R0 报名 → 固化（FIFO，队首失效不递补）；T0 不得临时报名。
11. **攻击目标枚举扩展**：`iter_actors`/`iter_targetable_actors` 含玩家/警察单位/无人机/影身；`player_order` 只含玩家。

---

## 6. 治理

- `tests/test_commands_sync.py` 从 `cli/parser.py`、`actions/*.py`、`actions/special_op.py`、`rl/action_space.py`（及 `engine/action_tables.py`）提取命令字符串与 special 名，断言本文档 **§1/§2 现役清单**覆盖全部提取结果；漏列/过期即红。**附录（退役/冻结对照）不参与强制断言**——其内容为对照性记录，防止 legacy 指令被当作现役。
- 本文档与 `tests/test_talents_md_sync.py` 的 M9 特殊操作覆盖（破界/武器破界/热线举报/竞选队长/指挥）保持一致。
- 新增现役指令、新增 special、M9 语义变更必须同步更新 §1/§2/§5 与治理测试；指令退役时必须**移入附录**并注明替代，而不是留在现役表。

### 变更记录

| 日期 | 变更 |
|---|---|
| 2026-08-12 | 初稿：从 cli/parser.py、actions/*.py、actions/special_op.py、actions/action_registry.py、engine/action_enumerator.py、engine/action_tables.py、docs/m9/ai/talents.md 全量提取命令契约；M9 语义变更注记成文 |
| 2026-08-12 | 范围修订：限定为 M9 更新后的**现役指令**；legacy 警察引擎指令（report/assemble/track/recruit/election/designate/study/police 子命令/wake_police）与 G2/G5 演唱指令移入新增附录（退役/冻结对照），星光行动标注待核实 |
| 2026-08-12 | 星光行动（M6）状态确认：设计层已由 M9 往世层新设计取消（B4 v0.4 PP 合并星光；M8 重做计划按 B4 重写），代码层 `m6_scoring` 仍在 `m9-rfc` profile 启用、R4 星光阶段仍运行——附录标注"设计已取代、实现未拆除" |

<!-- ============================================================
信源锚点验证（file:line，全部经人工核对；供评审/治理测试交叉引用）
============================================================

## cli/parser.py（全部可解析指令）
- wake/起床/w + wake <警察ID> 转 wake_police:            parser.py:13-20
- wake_police/唤醒警察/唤醒:                              parser.py:23-26
- move/移动/m/go + home/家/回家 别名:                     parser.py:29-35
- interact/交互/i/get/拿/学/做/买 + 通行证 简称:          parser.py:38-48
- lock/锁定/l:                                            parser.py:51-54
- find/找到/找:                                           parser.py:57-60
- applause/喝彩/cheer (M6):                               parser.py:63-66
- shoot/射箭/射 (M4 弓):                                  parser.py:69-72
- hook/钩索/钩 + hook self/自己:                          parser.py:75-80
- attack/攻击/atk/打 + 目标/武器/层/属性 四参:            parser.py:83-89
- special/特殊/sp/操作/sing/演唱/唱 + 简称映射(磨/吟唱/展开/病毒/放毒/追光/光/遗憾/拼接/光色/bl): parser.py:92-103
- report/举报:                                            parser.py:106-109
- assemble/集结:                                          parser.py:112-113
- track/追踪/指引:                                        parser.py:116-117
- recruit/加入警察/入警:                                  parser.py:120-121
- election/竞选/竞选队长:                                 parser.py:124-125
- designate/指定目标/指定:                                parser.py:128-131
- study/研究/研究性学习:                                  parser.py:135-136
- police/警察命令 4 子命令(move/equip/attack/wake) + police status: parser.py:140-186
- police_status/警察状态/警状态:                          parser.py:190-191
- forfeit/放弃/f/pass/skip:                               parser.py:194-195
- status/状态/s:                                          parser.py:198-199
- allstatus/全场/all/a:                                   parser.py:200-201
- help/帮助/h/?:                                          parser.py:202-203
- resolve_player_target（名称/ID 解析）:                  parser.py:208-222

## cli/validator.py（全部前置条件）
- 警察成员犯罪限制:                                        validator.py:80-130
- validate 分派（24 个 action 类型）:                      validator.py:133-184
- validate_applause_spend:                                 validator.py:191-203
- validate_shoot:                                          validator.py:206-237
- validate_hook:                                           validator.py:240-282
- validate_wake / validate_wake_police:                    validator.py:289-322
- validate_move:                                           validator.py:324-354
- validate_interact:                                       validator.py:356-389
- validate_lock / validate_find:                           validator.py:391-497
- validate_attack（近战/远程/范围 _validate_melee/_ranged/_area）: validator.py:499-573, 803-833
- validate_special（_PARAM_OPS=蓄力/更衣/修复 前缀匹配）:   validator.py:575-605
- validate_report/assemble/track_guide/recruit/election/designate/study/police_command: validator.py:612-796
- 眩晕/震荡/石化 _check_not_disabled:                     validator.py:835-842

## actions/action_registry.py
- get_available_actions（起床/移动/交互/锁定/找到/攻击/特殊/警察/放弃）: action_registry.py:7-77
- _get_police_actions（举报/集结/追踪指引/加入警察/竞选队长/唤醒警察/指定目标/研究性学习）: action_registry.py:80-170
- lock/find 目标枚举、attack 可攻击信息（近战/远程/范围/NPC）: action_registry.py:173-261
- usage 字符串（"attack <目标> <武器> [层 属性]" 等）:      action_registry.py:12,19,28,37,46,54,63,73,99,107,117,127,139,152,159,166

## actions/*.py（效果与语法）
- attack：LAYER_MAP/ATTR_MAP:                             attack.py:9-18；M9 路由(结界/无人机/警察NPC): attack.py:47-102；G3 远程防壁+resolve_m9_damage: attack.py:142-164；legacy 警察目标: attack.py:104-116；目标护甲层/属性降级: attack.py:123-133
- find：ENGAGED_WITH 双向:                                find_target.py:14-29；M4 箭堆+M5 掉落顺带拾取: find_target.py:33-66, 69-137
- lock：LOCKED_BY:                                        lock_target.py:13-15
- move：ALL_LOCATIONS:                                    move.py:4-7；get_all_valid_locations: move.py:22-27；借机攻击: move.py:48-104；M9 G3 结界困住/强制退场释出: move.py:129-151, 253-254；架盾阻碍/半进入/超新星: move.py:152-242；犯罪检查: move.py:327-340
- shoot：跨地点/锁定非前置/消耗箭/落箭堆:                 shoot.py:1-63
- hook：拉人+科伤/擦钩闪避失效/拉己不触发借机:             hook.py:1-97
- interact：按地点模块分发:                                interact.py:11-69
- forfeit：保底清零:                                      forfeit.py:4-11
- wake_up：起床+G2 舞台内不覆盖座位:                       wake_up.py:4-24
- police_command：move/equip/attack/wake 四子命令:         police_command.py:3-75
- starlight：拨弄命运/预兆/加冕（往世层）:                 starlight.py:17-27, 30-99
- applause_spend：4 用途成本表 _USES:                      applause_spend.py:16-21, 34-66

## actions/special_op.py
- get_available_specials（全部可用 special）:              special_op.py:7-95
- _append_m9_specials（破界/武器破界/热线举报/竞选队长/指挥X移动）: special_op.py:98-136
- execute 分派（16 分支）：拆卸X/磨刀/吟唱魔法护盾/展开AT力场/蓄力X/释放病毒/取消盾牌/Hoshino/更衣X/修复X/肾上腺素/热线举报X/破界/武器破界/竞选队长/指挥X移动: special_op.py:139-255
- _do_sharpen:                                            special_op.py:258-282
- _repair_or_recreate（hp20 增量制）:                     special_op.py:285-318
- _do_charge / _do_release_virus:                         special_op.py:321-360

## engine/action_enumerator.py（可用动作枚举）
- build_action_options（move/interact/lock/find/attack/special/report/designate）: action_enumerator.py:61-149
- _enumerate_move/interact/lock/find/attack/special/report/designate: action_enumerator.py:156-451
- 免枚举类型（forfeit/wake/assemble/track_guide/recruit/election/study/police_command）: action_enumerator.py:70-72

## engine/action_tables.py（参数字典）
- LOCATIONS(6):                                          action_tables.py:18-20
- INTERACT_ITEMS(27):                                    action_tables.py:26-40
- ITEM_LOCATIONS:                                        action_tables.py:46-74
- WEAPONS(10):                                           action_tables.py:80-92

## docs/m9/ai/talents.md（术语/世界模型）
- 共享世界模型（SP/公演位/结界边界/警务/世界时钟/目标枚举/special op）: talents.md:22-38
- 特殊行动指令契约（破界/武器破界/热线举报X/竞选队长/指挥X移动）: talents.md:38
- 交互矩阵 W*（attack/lock/find/move/interact 记模板池）: talents.md:81

## engine/m9/（M9 语义）
- m9_enabled / ensure_state_mechanisms（挂 m9_police 等）: gate.py:14-59
- 警察局案件驱动/队长候选 R2 上任/lead 分配/R4 执法/掩体/队长命令(captain_command): engine/m9/police.py:100-116, 190-250, 252-306, 318-373, 375-434, 443-517, 519-595
- T6 热线举报（任意地点/不读 SP/证据清单/结界限制）:      engine/m9/talents/t6.py:60-62, 95-155
- G3 结界：active_barrier/attack_crosses_active_barrier: engine/m9/talents/g3.py:68-85；破界/武器破界(§6.1): g3.py:746-778；_is_trapped/_is_inside: g3.py:1048-1051
- M9 战斗结算（resolve_damage/掩体吸收/临时HP链/absolute_death）: engine/m9/combat.py:161-457, 584
- SP 经济（即演1/公演2/公演位固化/模板池）:               engine/m9/action_system.py:22-25, 169-204, 260-345
- 石化（forfeit 或同槽挣脱）:                             talents.md:30
- G7 星野：战术宏/修复铁之荷鲁斯/肾上腺素:               talents/g7/tactical_mixin.py:98-105；talents/g7/fusion_mixin.py:115-152；talents/g7/hoshino.py:36-50
- G2 演唱（execute_sing + 三曲目）:                       talents/g2_hologram.py:127, 215-218, 387, 511-634

## rl/action_space.py（RL 索引 ↔ 命令）
- 索引布局（137 动作）:                                   action_space.py:6-20
- SPECIAL_OPS(13 条) / POLICE_CMDS(7 条):                 action_space.py:79-108
============================================================ -->
