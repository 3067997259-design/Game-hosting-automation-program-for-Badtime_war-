# T0 选项层参考（commands_choose.md）

> **定位**：引擎 T0 通用流程 + 14 槽天赋特异 T0/choose 表面；与 [talents.md](../m9/ai/talents.md) 槽位卡互引（卡管策略，本表管接口清单）。
> **范围纪律**：**现役口径**——只含 M9 更新后的 T0 体系（14 槽：T1/T2/T3/T4/T6/T7/G0–G7 + 引擎当前 T0 流程）。**不记录** legacy/退役 T0 行为（legacy v2exp 天赋 T0 流程、退役 G2 演唱、G5 duet 等）；如需对照只给一行指针（见 §1.6 与 [commands.md 附录](../ai/commands.md#附录退役冻结对照)）。
> **互引**：机制语义以 `docs/m9/current/` 对应 RFC 为准；`tests/test_commands_sync.py` 治理本文档；槽位卡（`docs/m9/ai/slots_*.md`）管理策略语义。

---

## 1. 通用 T0 流程（所有天赋共享）

### 1.1 主入口与流程

`ActionTurnManager.execute_action_turn`（`engine/action_turn.py:73-105`）顺序：

```
未起床 → wake（T0 物料阶段/天赋选项在起床后才运行）→ 同槽受限追演（m9_wake_followup，仅 G7 实现）
已起床 → _phase_t0(player)：
    skip 非空 → 返回 skip（本回合结束，action_type=skip）
    skip 为空（None）→ _phase_t1 普通行动 → _phase_t2 回合结束钩子
```

`_phase_t0`（`action_turn.py:125-438`）内部顺序：

1. **眩晕苏醒**（`action_turn.py:128-143`）：`is_stunned` 且无 SHOCKED 标记 → 苏醒（M5 黄昏可延长 1 轮 → `stun_extended`）；hp20 下苏醒不回血。
2. **天赋被动 T0 钩子**（`action_turn.py:146-153`）：`talent.on_turn_start(player)`；返回 `{"consume_turn": True}` 时跳过本回合（返回消息字符串；例：G7 色彩 10 自我怀疑，hoshino.py:255-256）。
3. **震荡处理**（`action_turn.py:156-175`）：SHOCKED 标记 → 结界免疫自动解除 / 黄昏延长（`shock_recover`）/ 苏醒（`shock_recover`）。
4. **M9 石化 T0**（`action_turn.py:178-182` → `_phase_t0_m9_petrified` 440-488）：`m9_petrify.is_petrified` 且 m9 启用 → 走 §1.3 子流程。**先于** legacy PETRIFIED 标记检查。
5. **legacy 石化处理**（`action_turn.py:185-239`）：PETRIFIED 标记路径（v2exp 表面对照用；M9 下被步骤 4 提前拦截，见 §1.6）。
6. **G2 发动者舞台免疫硬控**（`action_turn.py:242-259`）：ish-bosheth active/duet 的 G2 清眩晕/震荡/石化（无 choose）。
7. **G2 舞台 T0 物料阶段**（`action_turn.py:262-384`）：legacy stage（`liberamente_vivace`）冻结内容，见 §1.6 一行指针。
8. **天赋 T0 选项**（`action_turn.py:386-436`）：`get_t0_option` 非空 → `talent_t0` 提示（§1.2）。

### 1.2 talent_t0 提示（条件：get_t0_option 非空）

- 引擎 choose（`action_turn.py:419-428`）：
  - prompt：`"是否在本回合开始时发动天赋？"`
  - options：`["发动天赋", "不发动，正常行动"]`
  - context：`{"phase": "T0", "situation": "talent_t0", "talent_name": t0_option["name"], "talent_desc": t0_option["description"]}`
- **关键事实**：
  - `talent_name` 传的是**选项显示名**（`t0_option["name"]`），不是类名；**策略层不得依赖显示名，按 `slot_id` 分派**（talents.md §4.1；收编断点见 action_turn.py:425）。
  - 发动 → `talent.execute_t0(player)` 返回 `(msg, consumes_turn)`；`consumes_turn=True` 时 `action_type="talent_t0"`（`action_turn.py:431-435`）。
  - **T0 演出恒记 `action_type="talent_t0"`，不入 G6 模板池**：`G6TemplatePool.record` 只认 `ACTION_TYPE_TO_CATEGORY`（move/interact/find/lock/attack + shoot/hook，g6.py:25-28），`talent_t0` 无映射 → 不入池（g6.py:61-72）；slot 收尾仍记 `resolution_kind=action_performed`（`round_manager.py:496-499`）。
  - `get_t0_option` 可返回 `str`/`dict`；字符串→自动包装成 `{"name": talent.name, "description": str}`（`action_turn.py:394-405`）。
  - 禁用门：`_eternity_blocked`（永恒之诗被拉入者禁用主动天赋，action_turn.py:390-391）；六爻额外回合中禁用六爻（action_turn.py:408-411）。
  - 不发动/`get_t0_option` 为空 → `_phase_t0` 返回 `None` → T1 普通行动。

### 1.3 M9 石化 T0 子流程（forfeit / 同槽挣脱 1 SP 50%）

`_phase_t0_m9_petrified`（`action_turn.py:440-488`）：

- 入口 choose（`action_turn.py:452-455`）：prompt `"选择处理方式："`，options `["保持石化（跳过本槽，不获SP）", "尝试挣脱（1 SP/次，50%）"]`，context `{"phase": "T0", "situation": "petrified"}`；异常兜底保持。
- **保持石化** → 返回 `petrify_hold`：槽以 `resolution_kind=petrified_hold` 收尾、**不获得 SP**（round_manager.py:492-495）。
- **挣脱子循环**：同轮至多 2 次（`while attempts < 2 and registry.break_attempts_left(round, pid) > 0`，action_turn.py:464-465；petrify.py:141-143）；每次 `m9.spend_sp(pid, 1)` 预检（action_turn.py:466）→ `registry.attempt_break` 50% 判定（petrify.py:149-157；`break_success_probability()=0.5`，petrify.py:29-31）。
  - 成功 → 返回 `None`（本槽继续正常行动）；失败 → 再选 `"是否再尝试一次？"` `["继续尝试", "放弃（本槽收尾）"]`（action_turn.py:480-481，裸 prompt）→ 放弃/耗尽 → 返回 `petrify_hold`。
- 石化生命周期（petrify.py）：被动解除 = 统一有效伤害摇晃 2 次（`on_effective_hit`，110-118）；尘世之锁（群星诗）无被动解除、只能 T0 挣脱或 forfeit 到期；R4 tick（建立轮不 tick，125-138）；R0 清理挣脱计数（159-162）。

### 1.4 R0 公演报名窗口 choose（phase=M9_PUBLIC_REGISTRATION，无 situation）

`RoundManager._m9_offer_performance_registration`（`round_manager.py:160-185`）：

- 触发：R0（`_phase_r0`，round_manager.py:131-146）或升到 SP2 事件收尾（round_manager.py:523-524 槽收尾后）。
- 条件（round_manager.py:165-173）：SP≥2（`m9.get_sp(player_id) < 2 → continue`）；不在公演队列（`queue.is_in_queue`）；`_m9_public_eligible`（round_manager.py:148-158）= 存活 + 形态不在 `home/past` + `talent.get_t0_option(player) is not None`。
- choose：prompt `"SP 已满：保留即演权限或报名公演？"`，options `["保留", "报名公演"]`，context `{"phase": "M9_PUBLIC_REGISTRATION", "game_state": ..., "player": ...}` —— **无 situation 键**；异常兜底 `"保留"`。
- 报名 = `m9.register_performance(pid, round)`（round_manager.py:185 → action_system.py:261-266）：SP≥2 才可报名、FIFO 入队。
- **公演位分配**（`action_system.py:273-294` `allocate_public_slot` / 296-298 `assign_public_slot`）：R0 固化唯一公演位；队首失效（SP<2 或 eligibility 失败）→ **永久移除、不递补**（可重报，action_system.py:150-158）；`dispatch_public` 预检公演位先于 SP 消费（action_system.py:324-345）。

### 1.5 即演/公演派发前置（SP 经济，action_system.py）

| 派发 | SP 成本 | 前置/门 | 效果 | 锚点 |
|---|---|---|---|---|
| `dispatch_improvise` | 即演 −1（`SP_IMPROVISE_COST=1`，action_system.py:23） | `_current_grant.allow_instant`；`spend_sp` 预检先于消费（不足返回 None 不改状态，197-204）；移出公演队列 | 发 `standard` grant，`allow_instant=True`、`restricted=True`（受限菜单 move/interact/attack/find/lock，RESTRICTED_MENU 32） | action_system.py:301-322 |
| `dispatch_public` | 公演 −2（`SP_PUBLIC_COST=2`，action_system.py:24） | `_current_grant.allow_public`；**`assign_public_slot(round) == actor_id` 预检先于消费**（位失则 SP 不动）；移出公演队列（公演位消费） | 发 `standard` grant，`allow_public=True` | action_system.py:324-345 |
| `dispatch_full_extra` | 无 SP 直扣 | 三源白名单 `FULL_EXTRA_SOURCES = ("t4_hexagram_hojump", "g5_poem_earthfire", "g4_savior_active_burn")`；每人每轮至多一个、递归深度闸；`_current_grant.kind != "full_extra"` | 发 `full_extra` grant；同轮多候选只取优先级最高（`pick_full_extra_candidate`，406-411） | action_system.py:28-29, 347-362 |

- SP ∈ {0,1,2}（`SP_MAX=2`）；M9 开局 SP=1（`register_player`，190-192）。
- 槽位收尾 `RESOLUTION_KINDS`（action_system.py:77-80）：`action_performed / suppressed / aid_rest / wake / petrified_hold / forfeit / no_target / wake_followup / shadow_dissipated / terminal_song_conversion`。

### 1.6 退役/冻结内容（仅一行指针，不展开）

- **G2 旧全桌舞台演唱 / G5 duet 伴唱**（含 `sing`/`演唱` 别名、ish-bosheth active/duet 的 T1 special 路径与舞台内 T0 物料阶段 `g2_pickup_floor`/`g2_trade_accept`/`g2_play_card`/`g2_discard`，action_turn.py:262-384）→ 见 [commands.md 附录（退役/冻结对照）](../ai/commands.md#附录退役冻结对照)；M9 下 G2 = 光影双身+终曲、G5 = 锚定+诗篇（本文件 §2/§3）。
- **legacy v2exp 石化标记 T0**（action_turn.py:185-239，choose situation 同为 `petrified`，options 为 `["解除石化（受0.5伤害）","保持石化（本回合跳过）"]`）→ M9 下被 §1.3 提前拦截；仅作 situation=`petrified` 双表面对照，policy 须按 options 区分。
- **legacy 警察指令与星光行动**（report/assemble/…）→ 见 commands.md 附录；M9 警务走 `m9_police`（T6 关联 special 见 §2/§3）。

---

## 2. 天赋特异 T0 选项表（14 槽）

> 复用并核实 `docs/m9/ai/slots_*.md` 的提取结果（差异见 §5）；`m9_kind` 与 choose 锚点为治理测试机械校验对象。显示名来自 `get_t0_option()["name"]`（`talent_t0` context 的 `talent_name` 即此值）。

| 槽 | 类名 | T0 显示名（条件） | m9_kind | SP | 前置/门 | choose situation 标签（T0） | choose 裸提示 |
|---|---|---|---|---|---|---|---|
| T1 | `OneSlash9` | `一刀缭断·演出`（SP≥2）；`一刀缭断·即演`（SP≥1） | `t1_performance` / `t1_improvise` | 即演 1 / 公演 2 | 合法近战武器（melee 且非 `_hexagram_disabled`）+（同地点存活 `ENGAGED_WITH` 目标 **或** 游侠诗标记 ranger_blade/ranger_chase 且存在 `LOCKED_BY` 目标）；预检先于 SP | `oneslash_chase_target`（游侠诗追猎目标）、`t1_performance_mode`（演出方式）、`oneslash_pick_weapon`（选近战武器）、`oneslash_pick_target`（选攻击目标） | — |
| T2 | `ScissorRush9` | `剪刀手一突·演出`（SP≥2）；`剪刀手一突·即演`（SP≥1） | `t2_public` / `t2_improvise` | 即演 1 / 公演 2 | 存在已找到（`ENGAGED_WITH`）或已被我方锁定（`LOCKED_BY`）的存活目标；预检先于 SP | `t2_core_target`（选攻击目标）、`t2_performance_mode`（演出方式）、`t2_pick_weapon`（选武器）；`t2_earthfire_hunt` 为 **R3** 地火诗免费追猎（非 T0） | — |
| T3 | `Star9` | `天星（公演 2 SP）`（SP≥2 且地点有合法目标） | `t3_starfall` | 仅公演 2（**无即演入口**） | SP≥2 + 当前地点有效 + 当前地点存在除本人外合法目标（`_aoe_targets`）；公演位只消费 R0 已固化；执行时读取发动者当前地点（不锁报名地点） | `t3_stars_bounce_target`（群星诗弹射目标） | — |
| T4 | `Hexagram9` | `六爻`（SP≥1 且存在其他存活玩家） | `t4_hexagram` | 即演 1 / 公演 2 | 至少 1 SP + 存在其他存活玩家（无需 find/lock）；公演须 R0 公演位 | `hexagram_pick_opponent`、`hexagram_my_choice`、`hexagram_opp_choice`、`hexagram_thunder_target`（潜龙勿用天雷目标）、`hexagram_steal_target` / `hexagram_steal_pick`（飞龙在天夺甲）、`hexagram_disarm_target`（亢龙有悔禁武目标） | `六爻演出：`（即演/公演）；`阴阳的天机：指定卦象还是正常出拳？`；`指定卦象：`（天机；或跃在渊禁止指定） |
| T6 | `GoodCitizen9` | `联防整备`（SP≥1 且 m9_police 挂载未停机且同地点存活警察×白名单装备） | `t6_equip` | 即演 1 / 公演 2 | `m9_police` 挂载且未 `is_disabled()`；`_equipment_candidates` 非空（同地点存活警察 × 白名单真实装备：武器 baton/gauss_rifle/magic_barrage、护甲 shield/ceramic_armor/magic_shield/at_field）；执行时无存活警察则在消费 SP 前取消 | —（全部裸提示） | `选择联防整备方式`（即演/公演）；`选择整备警察：`；`整备类型：`；`选择{slot}：` |
| T7 | `Resurrection9` | `挂载死者苏生`（保险未挂载未落幕且存活挂载目标且 SP≥1） | `t7_mount` | 即演 1 / 公演 2 | 保险未挂载（`m9_insurance.is_mounted()` 假）+ 未落幕 + 存在存活挂载目标（含自己）+ SP≥1；挂载双向登记关注（目标先、T7 后，每人每轮 +1 上限） | `resurrection_pick_target`（选挂载目标） | `挂载方式：`（即演（1 SP）/公演（2 SP））；彼岸诗复活前 `复活前选择携带一件装备：` |
| G0 | `ShirokoTerror9` | 无无人机且 SP≥1 → `即演·召唤无人机`；有无人机且 SP≥2 → `公演·十字炮火/遗物支援技`；调整呼吸中/已撤退/SP 不足 → 无 T0 | `g0_drone_summon` / `g0_performance` | 即演 1 + **20% 当前 HP**（half-up 至少 1）；公演 2 + 20% 当前 HP | **唯一自残天赋**：每次即演/公演扣当前 HP 百分比（HP 成本致死 source `g0_hp_cost` 不算攻击、不触发调整呼吸）；公演须无人机在场 + R0 固化公演位；遗物支援技另需摧毁 1 件遗物装备 | —（全部裸提示） | `G0 公演：`（十字炮火/遗物支援技）；`遗物支援公演：`（追忆满 12 时：兑换简化诗篇（12 追忆）/ 使用遗物支援技）；`选择要摧毁的遗物装备：`；`选择简化标记诗篇：`（游侠/群星/阴阳/永恒/飞萤/追光/明天）；`回声追演：选择攻击目标`（G2 遗物）；`要有笑声模板追演：选择类别`（G6 遗物） |
| G1 | `G1MythFire9` | 卸甲形态 SP≥1 → `着装宣言：次级燃烧`；次级燃烧 → `火萤宣言`（description 列可用项）；完全燃烧/繁育 → 无 T0 | `g1_dress`（两种显示名共用） | 着装即演 1；完全燃烧公演 2；卸甲免费 | 形态状态机（卸甲/次级/完全/繁育）；完全燃烧 = 2 SP + R0 公演位（`_ensure_public_seat`），窗口 `full_burn_rounds` + 即时回复 `full_burn_heal`；SP=0 → 无 T0 | —（裸提示） | `火萤宣言：`（完全燃烧（公演 2 SP）/ 卸甲宣言（免费）） |
| G2 | `Hologram9` | `光影双身`（description 按条件列出：创建影身（即演 1 SP）/ 创建影身（公演 2 SP）/ 世末终曲承诺（公演 2 SP，永久锁死再造资格）） | `g2_dualbodies` | 即演创建 1；公演创建/终曲承诺 2 | 创建：`shadow_creation_eligible` 且无影身且 SP≥1/2；终曲承诺：有影身且非终曲歌者且 SP≥2；终曲永久消费 `shadow_creation_eligible`（不可逆）；公演位只消费 R0 固化 | —（裸提示；**终曲承诺无 choose**——T0 执行即提交） | `创建影身：`（创建影身（即演 1 SP）/ 创建影身（公演 2 SP）） |
| G3 | `Mythland9` | 结界内 → `固有结界·行动`；结界外 SP≥2 → `展开固有结界（公演）`；结界外 SP<2 → `投影魔术` | `g3_barrier_action` / `g3_barrier_expand` / `g3_projection` | 结界展开公演 2（2 SP 转 `public_temp_magic`）；投影只耗魔力不耗 SP | 魔力账本（普通先付、临时后付、不足预检失败不扣）；结界外每 R0 恢复 1（cap 8）；R4 维持费（基础 1 + 被困人数×1 + 圆环/剑阵/理想燃烧各 1；建立轮不 tick；不足强制解除）；硬上限 `max_barrier_rounds` | 全部经 `_choose` 助手 **`m9_g3`**（phase=T0，g3.py:1025-1039）：结界外/结界内行动、投影、复制武器、免费初始配置、剑阵功能、连发/终段、投影创建、目标、主目标 | —（G6 借用 `simple_projection` 裸提示 `选择螺旋剑目标：` g3.py:1003） |
| G4 | `Savior9` | 人形态+火种≥12+负世诗解锁 → `负世·主动燃尽`；人形态 SP≥1 + 近战武器 + 同地点 engaged 目标 → `即演/公演·人形态近战演出`（m9_kind=`g4_human_performance`）；形态内 SP≥2+ruin_damage>0 → `灾厄·弑魂焚诏`（形态门决定同一轮至多一项） | `g4_active_burn` / `g4_human_performance` / `g4_challenge` | 主动燃尽不直扣 SP（**SP 置 2**，非 +2，g4.py:193）；人形态即演 1 / 公演 2（公演 = 全部同地点存活 engaged 目标武器伤害 + `human_public_bonus`，各 +2 火种按演出完成发放）；焚诏公演 2 | 主动燃尽：人形态 + `divinity>=12` + `m9_burden_unlocked` → 完整形态（完整额外行动源 `g4_savior_active_burn`，dispatch_full_extra）；人形态演出：近战武器（melee 非 `_hexagram_disabled`）+ 同地点存活 `ENGAGED_WITH` 目标，预检先于 SP/公演位；焚诏：形态内 + SP≥2 + `ruin_damage>0` + 本轮公演位 | `g4_human_performance_mode`（演出方式）、`g4_strike_pick_weapon`（选近战武器）、`g4_strike_pick_target`（选单体目标）；焚诏为裸提示 | `选择人形态演出方式：`（公演（2 SP）/ 即演（1 SP））；`选择人形态演出武器：`；`选择人形态攻击目标：`；`焚诏拉条：{name} 选择攻击或拒战？`（`攻击`/`拒战`，除 G4 外每名存活玩家各一次，秘密承诺，异常兜底拒战） |
| G5 | `Ripple9` | SP≥2 → `公演：锚定 / 献诗`；SP≥1 且微澜重开 → `微澜：1 SP 信息型即演`（仅德谬歌 DEMIURGE；锚定监控期两者均不可用） | `g5_anchor_or_poem` / `g5_ripple` | 锚定/献诗公演 2；微澜即演 1 | 锚定：投影预检（须产出 ≥1 候选）先于 SP/追忆/槽消费，2 SP + K 点追忆（K∈[3,8]）+ 本轮公演位，存在爱愿不可锚定，激活锚定不可献诗，追忆 cap 24（德谬歌后有限总预算）；献诗：共享入口（2 SP + poem_cost 追忆 + 公演位 + 目标持对应天赋 + 爱愿 6 ticks），14 首选诗；微澜：1 SP 信息型即演，选合法可感知单位公开位置与装备，对其无视隐身/闪避至 G5 下一行动结束，不取得物品/不增追忆，每个完整公演后重开一次 | —（controller hook `choose_anchor_script(player, state)` 填 K 槽脚本；未实现/非法 → 确定性兜底 K 个 move 槽 `_DEFAULT_ANCHOR_LOCATIONS`）；微澜选单位、献诗选诗名+目标经 `_choose`（g5.py `_do_ripple`/`_do_poem`） | 守夜人诗由目标 `confirm`（poems.py:250）；微澜揭示信息型即演 |
| G6 | `CutawayJoke9` | SP≥1 且有合法类别 → `即演：重演上一轮行动`；SP≥2 → `公演：插入式笑话`（合法即演优先返回，否则只出公演） | `g6_improvise` / `g6_public` | 即演 1 / 公演 2 | 即演：窗口内模板类别（move/interact/find/lock/attack，窗口 1 轮/欢愉延展 2 轮）按 G6 自身状态过滤合法（预检先于 SP）；公演双路径互斥（借用核心 `G6_BORROWABLE_CORE` 白名单 t1/t2/t3/t4/g3/g4，G2 永不在；或召唤往世层援助无 PP/无额度），借用预检先于 SP/公演位；T4 或跃在渊必须重掷到非或跃（绝不创建完整额外行动） | —（全部裸提示） | `即演重演或公演？`；`选择重演类别`；`公演路径：`（借用核心/召唤援助）；`选择借用核心`；`选择猜拳目标：`；`出拳：`；`{target.name} 出拳：` |
| G7 | `Hoshino9` | SP≥2 → `公演：战术补给`；SP≥1 → `即演：小准备`（Terror 形态两者均不可用） | `g7_public` / `g7_improvise` | 即演 1 / 公演 2 | 即演：下个 R0 豁免失却汇流成泉（cost 回满后不减 1，`m9_mark_improvise_exempt`）；公演：R0 固化公演位 + 免费获得一项补给（战术道具 `TACTICAL_ITEMS` 或药物 `MEDICINES`，直接加入 `tactical_items`/`medicines`，无需回家）+ 魂援窗口（S3 接线）；Terror 不参与即演/公演；战术宏走 Cost 不读 SP | 起床受限追演：`起床受限追演：`（`["结束","move","interact","find","lock"]`，g7.py:64-67）；演出入口：`选择演出：`（公演/即演）、`选择补给类别：`（战术道具/药物） | 起床受限追演仅限 move/interact/find/lock/结束（不能攻击/宏/即演）；Terror 攻击走 `_terror_attack` 批处理（DIRECT_DAMAGE + absolute_death，A=terror_attack_damage，扣 terror_attack_cost，全灭免扣） |

---

## 3. 天赋特异 choose 表面汇总

### 3.1 全部 situation 标签 → 槽位归属

> `situation` 键由各调用方 context 发出。T0 层的核心标签为 `talent_t0`（引擎通用）与 `petrified`（引擎通用，M9 石化 T0）；其余为槽内 choose。G7 各标签为 v2exp 继承（`Hoshino9` 继承 v2exp `Hoshino`，M9 下现役），多为非 `get_t0_option` 表面（战术宏/更衣/自我怀疑等），标注来源阶段。

| situation | 槽位 | 阶段 | 提示文本（首次出现） | 锚点 |
|---|---|---|---|---|
| `talent_t0` | 引擎通用（14 槽共用） | T0 | `是否在本回合开始时发动天赋？` | action_turn.py:419-428 |
| `petrified` | 引擎通用（M9 石化 T0 + legacy PETRIFIED 标记 T0 双表面） | T0 | `选择处理方式：`（M9 options 与 legacy options 不同，policy 按 options 区分） | action_turn.py:452-455（M9）、195-199（legacy 对照） |
| `oneslash_chase_target` | T1 | T0 | `选择追猎目标：`（游侠诗，多候选时） | t1.py:96-98 |
| `t1_performance_mode` | T1 | T0 | `选择一刀缭断演出方式：` | t1.py:162-164 |
| `oneslash_pick_weapon` | T1 | T0 | `选择使用的近战武器：` | t1.py:221-223 |
| `oneslash_pick_target` | T1 | T0 | `选择攻击目标：` | t1.py:230-232 |
| `t2_core_target` | T2 | T0 | `选择攻击目标：` | t2.py:364-368 |
| `t2_performance_mode` | T2 | T0 | `选择剪刀手一突演出方式：` | t2.py:419-421 |
| `t2_pick_weapon` | T2 | T0 | `选择使用的武器：` | t2.py:471-474 |
| `t2_earthfire_hunt` | T2 | R3（地火诗 free_hunt_reaction） | `地火追猎目标：` | t2.py:248-250 |
| `t3_stars_bounce_target` | T3 | T0（群星诗标记） | `群星弹射目标：` | t3.py:151-153 |
| `hexagram_pick_opponent` | T4 | T0 | `选择猜拳对手：` | t4.py:134-136 |
| `hexagram_my_choice` | T4 | T0 | `{player.name}，请出拳：` | t4.py:147-149 |
| `hexagram_opp_choice` | T4 | T0 | `{target.name}，请出拳：` | t4.py:150-152 |
| `hexagram_thunder_target` | T4 | T0 | `⚡ 潜龙勿用——天雷！选择承受伤害的玩家：` | t4.py:233-235 |
| `hexagram_steal_target` | T4 | T0 | `☯️ 飞龙在天——选择目标：` | t4.py:268-270 |
| `hexagram_steal_pick` | T4 | T0 | `选择要复制的护甲：` | t4.py:282-284 |
| `hexagram_disarm_target` | T4 | T0 | `☯️ 亢龙有悔——选择禁武目标：` | t4.py:331-333 |
| `resurrection_pick_target` | T7 | T0 | `选择挂载「死者苏生」的目标：` | t7.py:111-113 |
| `m9_g3` | G3 | T0（`_choose` 助手统一标签，全部结界内/外选择） | 见 §3.3 全部 prompt | g3.py:1025-1039 |
| `hoshino_form` | G7（v2exp 继承） | register | 注册形态 | hoshino.py:83 |
| `hoshino_self_doubt_choice` | G7（v2exp 继承，T0 被动钩子内） | T0 | 色彩≥6 时是否自我怀疑 | hoshino.py:263 |
| `hoshino_tactical_equip` | G7（v2exp 继承） | T0 | 战术宏选装备 | fusion_mixin.py:96 |
| `hoshino_repair_material` | G7（v2exp 继承） | T1 | 修复材料 | fusion_mixin.py:133 |
| `hoshino_shield_shoot_target` | G7（v2exp 继承） | T0 | 持盾射击目标 | tactical_mixin.py:349 |
| `hoshino_shoot_target` | G7（v2exp 继承） | T0 | 射击目标 | tactical_mixin.py:383 |
| `hoshino_reload` | G7（v2exp 继承） | T0 | 换弹 | tactical_mixin.py:592 |
| `hoshino_throw_item` | G7（v2exp 继承） | T0 | 投掷物品 | tactical_mixin.py:676 |
| `hoshino_throw_location` | G7（v2exp 继承） | T0 | 投掷地点 | tactical_mixin.py:686 |
| `hoshino_medicine` | G7（v2exp 继承） | T0 | 用药 | tactical_mixin.py:803 |
| `hoshino_dash` | G7（v2exp 继承） | T0 | 突进 | tactical_mixin.py:842 |
| `hoshino_reorder_ammo` | G7（v2exp 继承） | T0 | 重排弹药 | tactical_mixin.py:971 |
| `hoshino_tactical_input` | G7（v2exp 继承） | T0（战术宏输入） | 战术输入 | tactical_mixin.py:139 |
| `hoshino_change_form` | G7 | T1（`special 更衣<形态>`，无参时 choose） | `选择要更换到的形态：` | special_op.py:174-186 |
| `recruit_pick_1` / `recruit_pick_2` | 引擎通用（非天赋特异；T1 加入警察三选二） | T1 | 非 T0 表面，仅记录归属 | action_turn.py:1948-1960 |

> 引擎 T0 的 legacy stage 标签（`g2_pickup_floor`/`g2_trade_accept`/`g2_play_card`/`g2_discard`，action_turn.py:290/311/351/377）为冻结舞台内容，见 §1.6 指针，不列入现役契约。

### 3.2 全部裸提示 prompt 文本 → 槽位归属

> 裸提示 = `controller.choose(prompt, options)` **未传 situation 键**（或传 `context` 中无 `situation`）。

| 裸提示（prompt） | 槽位 | 说明/options | 锚点 |
|---|---|---|---|
| `六爻演出：` | T4 | `["即演","公演"]` | t4.py:114 |
| `阴阳的天机：指定卦象还是正常出拳？` | T4 | `["指定卦象","正常出拳"]`；或跃在渊禁止指定 | t4.py:169-183 |
| `指定卦象：` | T4 | 天机指定 `TIANJI_SPECIFIABLE` + `或跃在渊`（禁选） | t4.py:177 |
| `出拳：` | T4 / G6 | `hexagram_cast` 借用（t4.py:477）与 G6 借六爻（g6.py:328） | t4.py:477; g6.py:328 |
| `{target.name} 出拳：` | T4 / G6 | 对手出拳 | t4.py:478; g6.py:329 |
| `选择联防整备方式` | T6 | `["即演","公演"]`（SP≥2 时） | t6.py:202 |
| `选择整备警察：` | T6 | 警察单位名 | t6.py:240 |
| `整备类型：` | T6 | 武器/护甲 | t6.py:243 |
| `选择{slot}：` | T6 | 具体装备名 | t6.py:246 |
| `挂载方式：` | T7 | `["即演（1 SP）","公演（2 SP）"]` | t7.py:95-97 |
| `复活前选择携带一件装备：` | T7 | 彼岸诗复活时 | t7.py:234 |
| `G0 公演：` | G0 | `["十字炮火","遗物支援技"]` | g0.py:310 |
| `遗物支援公演：` | G0 | 追忆≥12：`["兑换简化诗篇（12 追忆）","使用遗物支援技"]` | g0.py:363 |
| `选择要摧毁的遗物装备：` | G0 | 遗物列表（name+slot） | g0.py:432 |
| `选择简化标记诗篇：` | G0 | `REDUCED_POEM_WHITELIST`：游侠/群星/阴阳/永恒/飞萤/追光/明天 | g0.py:474 |
| `回声追演：选择攻击目标` | G0 | G2 遗物支援技（回声追演） | g0.py:649 |
| `要有笑声模板追演：选择类别` | G0 | G6 遗物支援技（模板重演类别） | g0.py:680 |
| `火萤宣言：` | G1 | `["完全燃烧（公演 2 SP）","卸甲宣言（免费）"]` | g1.py:109 |
| `创建影身：` | G2 | `["创建影身（即演 1 SP）","创建影身（公演 2 SP）"]` | g2.py:184 |
| `选择螺旋剑目标：` | G3 | G6 借用 `simple_projection` 单体螺旋剑 | g3.py:1003 |
| `选择人形态演出方式：` | G4 | `["公演（2 SP）","即演（1 SP）"]`（SP≥2 且持公演位）或 `["即演（1 SP）"]` | g4.py:433-435 |
| `选择人形态演出武器：` | G4 | 可用近战武器（melee 非 `_hexagram_disabled`） | g4.py:288-293 |
| `选择人形态攻击目标：` | G4 | 同地点存活 `ENGAGED_WITH` 单位 | g4.py:305-311 |
| `焚诏拉条：{name} 选择攻击或拒战？` | G4 | `["攻击","拒战"]`；除 G4 外每名存活玩家各一次；异常兜底拒战 | g4.py:461-464 |
| `即演重演或公演？` | G6 | `["即演","公演"]` | g6.py:247 |
| `选择重演类别` | G6 | 合法模板类别 | g6.py:251 |
| `公演路径：` | G6 | `["借用核心","召唤援助"]` | g6.py:263 |
| `选择借用核心` | G6 | `G6_BORROWABLE_CORE` 键（t1/t2/t3/t4/g3/g4） | g6.py:277 |
| `选择猜拳目标：` | G6 | 借六爻猜拳目标 | g6.py:317 |
| `起床受限追演：` | G7 | `["结束","move","interact","find","lock"]`（m9_wake_followup） | g7.py:64-67 |
| `选择破界武器：` | G3 反制通道（被困非 G3 单位） | `special 武器破界` 内 choose | special_op.py:234 |
| `是否再尝试一次？` | 引擎通用（M9 石化 T0 挣脱循环） | `["继续尝试","放弃（本槽收尾）"]` | action_turn.py:480-481 |

### 3.3 G3 `m9_g3` 槽内全部 choose prompt（统一 situation 标签）

| prompt | options | 锚点 |
|---|---|---|
| `选择结界外行动` | `["展开固有结界","投影魔术"]`（SP≥2 时） | g3.py:206 |
| `选择结界内行动` | `["螺旋剑连发","兵装攻击"(池非空),"剑阵","投影创建","破界"]`（+`幻想崩坏` 若合法；v0.3 §7.2 兵装池） | g3.py:222 |
| `选择投影` | `["螺旋剑（伪）","双刀·攻势","双刀·守势","七重圆环","复制武器"]` | g3.py:322 |
| `选择复制武器` | 见证式样优先，无见证回退关闭白名单 | g3.py:398 |
| `免费初始配置` | `["兵装（螺旋剑）","防壁（七重圆环）","剑阵"]`（展开后免费三选一） | g3.py:528 |
| `选择剑阵功能` | `SWORD_ARRAY_FUNCTIONS`（弹道校正/拦截/崩坏准备） | g3.py:534（初始配置内）、675（结界内剑阵） |
| `是否继续连发？` | `["继续连发","停止"]` | g3.py:612 |
| `是否结算终段幻想崩坏？` | `["是","否"]` | g3.py:621 |
| `选择投影创建` | `["七重圆环","双刀·守势","复制武器"]` | g3.py:686 |
| `选择目标` | 合法目标名 | g3.py:891 |
| `指定主目标` | 存活被捕捉单位 | g3.py:900 |

### 3.4 关联 special op（非 choose 但属于槽接口表面）

| special op | 归属 | 说明 | 锚点 |
|---|---|---|---|
| `special 破界` | G3 反制通道（被困非 G3 单位） | 固定 `break_action_power` 结构伤害、无武器；普通 move 离开被拦 | special_op.py:98-111（登记）、221-238（执行）；move.py:149-151（拦截提示） |
| `special 武器破界` | G3 反制通道 | 武器攻击 A 直接扣锚点耐久；执行时 choose `选择破界武器：` | special_op.py:109-111、228-238 |
| `special 热线举报{玩家名}` | T6（根行动，任意地点、不读 SP） | 证据资格四类；`hotline_report`（t6.py:95-136） | special_op.py:116-125、211-220 |
| `special 竞选队长` | T6/通用 | R2 就任；`apply_captain` | special_op.py:127-130、239-245 |
| `special 指挥{警员}移动` | T6 队长 | 队长专用；`captain_command` | special_op.py:131-136、246-253 |
| `special 更衣<形态>` / `取消盾牌` / `Hoshino`（战术宏）/ `修复` / `肾上腺素` | G7（v2exp 继承） | 更衣含 situation `hoshino_change_form`；肾上腺素不耗回合 | special_op.py:55-88、174-192 |

---

## 4. 通用 vs 天赋特异划分

### 4.1 引擎层通用表面（所有槽共享，政策只须实现 should_activate_t0 + 槽内 choose）

| 表面 | 引擎流程 | 说明 | 锚点 |
|---|---|---|---|
| `talent_t0` 发动门 | `_phase_t0` 天赋 T0 段 | `get_t0_option` 非空即提示；发动与否由 `controller.choose` 决定；发动后 `execute_t0` → `action_type="talent_t0"`（不入 G6 模板池） | action_turn.py:386-436 |
| 醒来（wake） | `execute_action_turn` | 未起床先 wake（T0 物料/天赋选项在起床后才运行）；G7 同槽受限追演 | action_turn.py:87-97 |
| 控制解除（眩晕/震荡/石化） | `_phase_t0` 步骤 1/3/4/5 | 苏醒/震荡恢复/石化 T0；无 choose 或 `petrified` choose | action_turn.py:128-175、185-239、440-488 |
| M9 石化 T0 | `_phase_t0_m9_petrified` | forfeit（`petrify_hold`，不获 SP）或同槽挣脱（1 SP/次、50%、至多 2 次） | action_turn.py:440-488；petrify.py:141-157 |
| R0 公演报名窗口 | `_m9_offer_performance_registration` | `phase="M9_PUBLIC_REGISTRATION"`、**无 situation**；SP≥2 才可报名；公演位 R0 固化（FIFO，队首失效不递补） | round_manager.py:160-185；action_system.py:261-266、273-298 |
| 即演/公演派发 | `dispatch_improvise` / `dispatch_public` | SP 预检先于消费；公演位预检先于 SP | action_system.py:301-345 |
| 槽位收尾 | `resolve_slot` | `RESOLUTION_KINDS`；`petrified_hold`/`wake_followup`/`forfeit` 等 | action_system.py:425-439、77-80；round_manager.py:478-524 |

> 未来协议 policy（talents.md §4.1 `M9AIPolicy`）：注册键 = `player.talent_slot_id`（稳定）；引擎层通用表面由引擎代管，policy 只须实现 `should_activate_t0(player, state, option)` + 槽内 `choose(player, state, prompt, options, context)` + 命令层 `candidates`；`DefaultM9Policy` 兜底所有未覆盖槽；**不得按显示名分派**。

### 4.2 天赋特异接管点（policy.choose 的接盘点）

| 槽 | T0 入口 | 内部 choose 接管点（situation/裸提示） | 命令层补充 |
|---|---|---|---|
| T1 | `t1_performance` / `t1_improvise` | 追猎目标/演出方式/选武器/选目标（situation 4 个） | 普通行动铺 find/lock 链路 |
| T2 | `t2_public` / `t2_improvise` | 攻击目标/演出方式/选武器（situation 3 个）；R3 地火追猎（`t2_earthfire_hunt`） | 追猎反应（`m9_on_public_root_completed` 无 choose）；`core_attack` 为 G6 借用入口 |
| T3 | `t3_starfall` | 群星弹射目标（situation 1 个） | `borrow_starfall` 为 G6 借用入口（无公演待遇） |
| T4 | `t4_hexagram` | 猜拳 6 项（situation 7 个）+ 天机 3 裸提示 | `hexagram_cast` 为 G6 借用入口（或跃重掷）；金身/武器禁用由引擎钩子处理 |
| T6 | `t6_equip` | 4 裸提示（方式/警察/类型/装备） | 热线/竞选/指挥 special；特别线索证据资格 |
| T7 | `t7_mount` | 挂载方式裸提示 + `resurrection_pick_target` | 彼岸诗复活带装备裸提示 |
| G0 | `g0_drone_summon` / `g0_performance` | 6 裸提示（公演/遗物/诗篇/追演/模板） | HP 成本门自残预算 |
| G1 | `g1_dress` | `火萤宣言：` 裸提示 | 形态状态机与失熵节奏 |
| G2 | `g2_dualbodies` | `创建影身：` 裸提示；终曲承诺无 choose | 影身 actor 代理槽（`_phase_t1_shadow`）与终曲区域 |
| G3 | `g3_barrier_action` / `g3_barrier_expand` / `g3_projection` | 全部经 `m9_g3`（11 个 prompt） | 魔力账本、维持费、幻想崩坏；G6 借用 `simple_projection` 裸提示 |
| G4 | `g4_active_burn` / `g4_challenge` | 焚诏拉条裸提示（**他人 controller 被 choose**，异常兜底拒战） | 完整额外行动源 `g4_savior_active_burn` |
| G5 | `g5_anchor` | `choose_anchor_script` hook（非 controller.choose）；诗篇入口调用方输入 | 献诗需选诗名+目标；守夜人诗目标 confirm |
| G6 | `g6_improvise` / `g6_public` | 7 裸提示（即演/公演/类别/路径/核心/猜拳/出拳） | 借用核心白名单 `G6_BORROWABLE_CORE`；援助无 PP |
| G7 | 无（get_t0_option=None） | 起床受限追演裸提示；v2exp 继承标签（§3.1） | `special 更衣/取消盾牌/Hoshino/修复/肾上腺素`；`_terror_attack` 批处理 |

---

## 5. 治理与变更记录

- `tests/test_commands_sync.py`（commands.md §6）从 `cli/parser.py`、`actions/*.py`、`actions/special_op.py`、`rl/action_space.py`、`engine/action_tables.py` 提取命令与 special 名，断言 commands.md 现役清单覆盖；**本文档（T0 选项层）与 commands.md（命令契约层）同属命令层接口清单**，新增 T0 选项/situation/裸提示须同步更新本表与 `tests/test_talents_md_sync.py` 覆盖的槽位卡（talents.md §6）。
- **冲突裁决**：本文档清单与 `docs/m9/ai/slots_*.md` 冲突时**以源码为准**，并在本文档 §5 报告。
- 现役口径检查：T0 选项/choose 表面增减或退役（如 G7 恢复 get_t0_option、G5 接线微澜）必须同步 §2/§3/§4 与槽位卡。

### 变更记录

| 日期 | 变更 |
|---|---|
| 2026-08-12 | 初稿：引擎 T0 流程（wake → 被动钩子 → 控制处理 → talent_t0）+ 14 槽 T0 选项表 + choose 表面汇总 + 通用/天赋特异划分；复用并核实 slots_*.md 提取结果 |

---

<!-- ============================================================
T0 选项层参考——信源锚点验证（file:line，全部经人工核对；供评审/治理测试交叉引用）
============================================================

## 引擎 T0 流程（engine/action_turn.py）
- execute_action_turn 主入口:                     action_turn.py:73-105
- wake 流程 + m9_wake_followup 挂接:              action_turn.py:87-97
- _phase_t0 眩晕苏醒:                             action_turn.py:128-143
- 天赋被动 T0 on_turn_start（consume_turn）:      action_turn.py:146-153
- 震荡处理（shock_recover）:                      action_turn.py:156-175
- M9 石化入口:                                    action_turn.py:178-182
- _phase_t0_m9_petrified（forfeit/挣脱）:          action_turn.py:440-488
- M9 石化 choose（situation=petrified）:           action_turn.py:452-455
- M9 石化再尝试裸提示:                            action_turn.py:480-481
- legacy PETRIFIED 标记 T0（对照）:               action_turn.py:185-239；choose 195-199（situation=petrified）
- G2 发动者舞台免疫:                              action_turn.py:242-259
- G2 舞台 T0 物料阶段（冻结，一行指针）:          action_turn.py:262-384（situation: 290/311/351/377）
- talent_t0 提示 choose:                          action_turn.py:386-436（choose 419-428；execute_t0 431-435）
- 禁用门 _eternity_blocked/六爻额外回合:           action_turn.py:390-411

## 石化生命周期（engine/m9/petrify.py）
- break_success_probability=0.5:                   petrify.py:29-31
- 被动解除（摇晃 2 次）/尘世之锁:                 petrify.py:110-122, 90-98
- R4 tick（建立轮不 tick）:                        petrify.py:125-138
- break_attempts_left（同轮上限 2）:               petrify.py:141-143
- attempt_break 50%:                               petrify.py:149-157
- R0 清理:                                         petrify.py:159-162

## R0 公演报名窗口（engine/round_manager.py）
- _m9_offer_performance_registration:              round_manager.py:160-185
- 报名 choose（phase=M9_PUBLIC_REGISTRATION 无 situation）: round_manager.py:175-185
- _m9_public_eligible（form/get_t0_option 门）:    round_manager.py:148-158
- M9 R0 流程（begin_round/报名/固化/石化清理/警务）: round_manager.py:131-146
- 槽收尾（petrified_hold/wake_followup 等）:       round_manager.py:478-524

## SP/派发（engine/m9/action_system.py）
- SP 常量（即演 1/公演 2/上限 2）:                action_system.py:23-25
- FULL_EXTRA_SOURCES（t4/g5/g4 三源）:             action_system.py:28-29
- RESTRICTED_MENU:                                 action_system.py:32
- RESOLUTION_KINDS:                                action_system.py:77-80
- register_performance（SP≥2 报名）:               action_system.py:261-266
- allocate_public_slot / assign_public_slot:       action_system.py:273-294, 296-298
- dispatch_improvise（−1 SP，预检先于消费）:       action_system.py:301-322
- dispatch_public（公演位预检先于 SP）:            action_system.py:324-345
- dispatch_full_extra / pick_full_extra_candidate: action_system.py:347-362, 406-411

## 14 槽 get_t0_option / execute_t0 / choose（engine/m9/talents/*.py）
- T1: get_t0_option 116-134；execute_t0 136-190；_precheck 104-110；_legal_targets 54-62；_ranger_chase_targets 73-86
  choose：oneslash_chase_target 96-98；t1_performance_mode 162-164；oneslash_pick_weapon 221-223；oneslash_pick_target 230-232
- T2: get_t0_option 373-396；execute_t0 398-436；_core_targets 331-354；on_crime_check 62-76；m9_on_attack 82-94；m9_on_public_root_completed 211-232；free_hunt_reaction 234-261；core_attack（G6 借用）512-547
  choose：t2_core_target 364-368；t2_performance_mode 419-421；t2_pick_weapon 471-474；t2_earthfire_hunt 248-250（R3）
- T3: get_t0_option 188-205；execute_t0 207-228；_ensure_public_seat 230-234；borrow_starfall（G6 借用）240-244
  choose：t3_stars_bounce_target 151-153
- T4: get_t0_option 84-96；execute_t0 98-128；_maybe_specify_tianji 163-184；_scissors_paper（或跃）364-378；_rock_paper（群龙无首）384-433；m9_modify_incoming/m9_on_lethal 439-452；hexagram_cast（G6 借用）471-488
  choose：hexagram_pick_opponent 134-136；hexagram_my_choice 147-149；hexagram_opp_choice 150-152；hexagram_thunder_target 233-235；hexagram_steal_target 268-270；hexagram_steal_pick 282-284；hexagram_disarm_target 331-333
  裸提示：六爻演出：114；阴阳的天机：169；指定卦象：177；出拳：477/478（hexagram_cast）
- T6: get_t0_option 161-181；execute_t0 183-214；_choose 助手 267-275；hotline_report 95-136；_evidence_for 138-155
  裸提示：选择联防整备方式 202；选择整备警察：240；整备类型：243；选择{slot}：246
- T7: get_t0_option 52-72；execute_t0 74-130；_mount_targets 132-136；on_death_check 149-184；_revive_location 186-200；_consume_far_shore_watch 220-246
  choose：resurrection_pick_target 111-113；裸提示：挂载方式：95-97；复活前选择携带一件装备：234
- G0: get_t0_option 1052-1076；execute_t0 1078-1096；_do_summon 270-295；_do_performance 301-317；_hp_cost_pct/_pay_hp_cost 386-407；_ensure_public_seat 379-384
  裸提示：G0 公演：310；遗物支援公演：363；选择要摧毁的遗物装备：432；选择简化标记诗篇：474；回声追演：选择攻击目标 649；要有笑声模板追演：选择类别 680
  REDUCED_POEM_WHITELIST: g0.py:37-39；AR_WEAPON_NAME: g0.py:34
- G1: get_t0_option 69-88；execute_t0 90-126；m9_on_lethal（繁育替代）156-172；m9_on_root_move（超新星）273-281
  裸提示：火萤宣言：109
- G2: get_t0_option 151-170；execute_t0 172-204；_create_shadow 214-229；dissipate 237-250；m9_on_lethal 252-261；_commit_terminal 265-272；TerminalArea 113-133
  裸提示：创建影身：184（终曲承诺无 choose 196-203）
- G3: get_t0_option 166-186；execute_t0 188-230；_choose 助手（situation=m9_g3）1025-1039；_pay_magic/_consume_magic 243-258；on_r4_upkeep 284-303；_upkeep_cost 305-315
  choose（m9_g3）：选择结界外行动 206；选择结界内行动 222；选择兵装 248；是否超限灌注（消耗临时魔力）？260；选择投影 322；选择复制武器 398；免费初始配置 600；选择剑阵功能 534/675；是否继续连发？612；是否结算终段幻想崩坏？621；选择投影创建 686；选择目标 891；指定主目标 900
  裸提示：选择螺旋剑目标：1003（G6 借用 simple_projection 984-1010）
- G4: get_t0_option 227-241；execute_t0 243-263；SP 置 2（g4.py:193）；dispatch_full_extra g4_savior_active_burn 251-252；_ensure_public_seat 270-273；_run_challenge 275-364
  裸提示：焚诏拉条：{name} 选择攻击或拒战？297-301
- G5: get_t0_option 228-240；execute_t0 242-251；_collect_anchor_script（choose_anchor_script hook）257-275；_DEFAULT_ANCHOR_LOCATIONS 254-255；execute_anchor 277-319（投影预检 293-299、SP 300-301、追忆 302-303、公演位 304-307）；recite_poem 136-139
  poems.py：共享入口 recite 71-108（2 SP+poem_cost 12+公演位+天赋绑定+爱愿）；地火 full_extra 147-162；守夜人 confirm 246-276；彼岸 278-281；负世（m9_burden_unlocked）288-307
- G6: get_t0_option 199-228；execute_t0 230-270；G6_BORROWABLE_CORE 37-44；TEMPLATE_CATEGORIES 22；EXCLUDED_ACTION_TYPES 31-34；template_window_rounds 50-52；precheck_borrow 154-165；hexagram_reroll_until_legal 167-173；aid_summon_cost 176-181；_ensure_public_seat 294-299；_borrow_hexagram 301-339
  裸提示：即演重演或公演？247；选择重演类别 251；公演路径：263；选择借用核心 277；选择猜拳目标：317；出拳：328；{target.name} 出拳：329
- G7（engine/m9/talents/g7.py）: m9_wake_followup 54-79（裸提示 起床受限追演：67）；on_wakeup 38-52；_terror_attack 99-150；on_round_start（即演豁免）156-164；m9_mark_improvise_exempt 166-168
  v2exp 继承（talents/g7/）：get_t0_option=None hoshino.py:271-273；hoshino_form hoshino.py:83；hoshino_self_doubt_choice hoshino.py:263；hoshino_tactical_equip fusion_mixin.py:96；hoshino_repair_material fusion_mixin.py:133；hoshino_shield_shoot_target tactical_mixin.py:349；hoshino_shoot_target :383；hoshino_reload :592；hoshino_throw_item :676；hoshino_throw_location :686；hoshino_medicine :803；hoshino_dash :842；hoshino_reorder_ammo :971；hoshino_tactical_input :139
  choose_mixin 处理器键（controllers/ai/choose_mixin.py:57-167）：hoshino_form_choice 57 / hoshino_self_doubt 70 / hoshino_tactical_equip 89 / hoshino_medicine 126 / hoshino_dash_target 137 / hoshino_shoot_target 144 / hoshino_find_target 150 / poem_nightwatch_choice 156

## 关联 special op（actions/special_op.py）
- 破界/武器破界登记: special_op.py:98-111；执行 221-238（武器破界 choose「选择破界武器：」234）
- 热线举报X 登记: 116-125；执行 211-220
- 竞选队长: 127-130 登记；239-245 执行
- 指挥X移动: 131-136 登记；246-253 执行
- G7：取消盾牌 55-58；更衣（hoshino_change_form）60-69/174-192；Hoshino 70-75/169-173；修复 77-81/193-197；肾上腺素 83-88/198-210

## G6 模板池（engine/m9/talents/g6.py）
- ACTION_TYPE_TO_CATEGORY（talent_t0 无映射→不入池）: g6.py:25-28
- G6TemplatePool.record（类别去重）: g6.py:61-72
- talent_t0 入池排除：round_manager.py:518-522 record 调用 + g6.py:25-28 映射
-->
