# M9 槽位卡：T1–T4 / T6–T7

> 分片卡（核心槽）。父文档 [talents.md](talents.md)（共享世界模型 §0 / 交互矩阵 §2 / 评分器 §3 / Policy 协议 §4）。机制语义以 `docs/m9/current/` 对应 RFC 为准。

### T1 一刀缭断 `OneSlash9`
- **决策点**：T0 `talent_t0`（选项显示名："一刀缭断·演出"/"一刀缭断·即演"；m9_kind=`t1_performance`/`t1_improvise`）；控制器 choose 的 situation 标签：`oneslash_chase_target`（追猎目标，T0）、`t1_performance_mode`（即演/公演，T0）、`oneslash_pick_weapon`（选近战武器，T0）、`oneslash_pick_target`（选攻击目标，T0）；关联 special op：无
- **经济与门**：即演 1 SP / 公演 2 SP（SP≥2 出现"·演出"选项，但公演还须 R0 已固化的公演位，否则自动回退即演；T0 不得临时报名）；前置：有合法近战武器（melee 且未被六爻封印）+ 有同地点存活 `ENGAGED_WITH` 目标，或持有游侠诗标记（ranger_blade/ranger_chase）且存在 `LOCKED_BY` 目标；预检先于 SP 消费
- **核心效果**：单目标核心斩击——伤害×melee_multiplier（当前 1.25）、防御减半（defense_coefficient=0.5，武器属性保留）；公演可先追演到已锁定目标地点再斩（游侠诗，消耗标记；即演不消耗）；击杀由 M9 管线裁决
- **AI 注记**：本槽完全依赖关系事实——无 `ENGAGED_WITH`/`LOCKED_BY` 就没有 T0 选项，先把 find/lock 链路铺好再谈斩击；公演的价值 = 追演位移 + 舞台待遇，游侠诗在手时可远程收割锁定目标；伤害与击杀按 §3.2 探针评估，注意 case_risk（目击/案件）与 exposure（斩击后自身暴露），死亡结算交给 M9 管线无需自估
<!-- anchor: t1.py:116-134 get_t0_option; 136-190 execute_t0; 96-98 oneslash_chase_target; 162-164 t1_performance_mode; 221-223 oneslash_pick_weapon; 230-232 oneslash_pick_target; 54-62 _legal_targets; 73-86 _ranger_chase_targets; 202-210 _chase_to; 235-260 _slash; action_turn.py:419-428 talent_t0 -->

### T2 剪刀手一突 `ScissorRush9`
- **决策点**：T0 `talent_t0`（选项显示名："剪刀手一突·演出"/"剪刀手一突·即演"；m9_kind=`t2_public`/`t2_improvise`）；控制器 choose 的 situation 标签：`t2_core_target`（选攻击目标，T0）、`t2_performance_mode`（即演/公演，T0）、`t2_pick_weapon`（选武器，T0）、`t2_earthfire_hunt`（地火诗追猎目标，R3）；另：他人公演根行动完成自动触发追猎反应（`m9_on_public_root_completed`，无 choose）；G6 借用入口 `core_attack`（直接攻击，无追演/无 find 前置；**注意：G6 借用派发当前只实际接线了 T4 `hexagram_cast` 与 T3 `starfall`，`core_attack` 等其余核心仍返回"执行器随天赋阶段落地"骨架消息，策略侧不得按已接线预期**）；关联 special op：无
- **经济与门**：即演 1 SP / 公演 2 SP；前置：存在已找到（`ENGAGED_WITH`）或已被我方锁定（`LOCKED_BY`）的存活目标，否则 T0 选项不出现；公演须 R0 公演位，可先追演移动（actions.move）再攻击；被动无额外成本：伤人未杀免罪（"伤害玩家"且最近一次攻击未击杀 → immune）、攻击回盾（m9_on_attack 偶数次命中护甲 → 恢复同名外甲耐久，当前恢复量 1，铁之荷鲁斯除外）、零击杀隐身豁免、警觉 find/found 各每轮一次 +1 SP
- **核心效果**：对已找到/已锁定目标的核心攻击（普通武器结算，最强武器优先）；追猎反应全场一次：他人公演根行动完成后对可见目标做一次合法 find 或 lock（地火诗免费通道不耗额度）
- **AI 注记**：无锁定/已找到目标时 T0 不合法——本槽是"铺垫→收割"两段式，先决定何时用普通行动完成 find/lock；他人公演会触发你的追猎反应（全局一次），是白捡 find/lock 的时机不要浪费；零击杀隐身让攻击不暴露、但击杀后隐身解除；免罪只保未击杀的伤害，击杀仍记罪，case_risk 要按击杀与否分开折算
<!-- anchor: t2.py:373-396 get_t0_option; 398-436 execute_t0; 364-368 t2_core_target; 419-421 t2_performance_mode; 471-474 t2_pick_weapon; 248-250 t2_earthfire_hunt; 331-354 _core_targets; 211-232 m9_on_public_root_completed; 234-261 free_hunt_reaction; 62-76 on_crime_check; 82-94 m9_on_attack; 512-547 core_attack -->

### T3 天星 `Star9`
- **决策点**：T0 `talent_t0`（选项显示名："天星（公演 2 SP）"；m9_kind=`t3_starfall`）；控制器 choose 的 situation 标签：`t3_stars_bounce_target`（群星诗弹射目标，T0）；**无即演入口**；G6 借用入口 `borrow_starfall`（无公演待遇）；关联 special op：无
- **经济与门**：仅 2 SP 公演（无 1 SP 即演）；须 R0 固化公演位（T0 不得临时报名）；预检：SP≥2 + 当前地点有效 + 当前地点存在除本人外合法目标；**报名公演不锁地点**，公演实际执行时读取发动者当前所在地点原地释放（2026-08-11 裁决，无地点选择 UI）
- **核心效果**：当前地点全体合法单位 AOE + 石化：starfall_damage 无属性（当前 2）、defense_coefficient=0（完全穿防）、仍受 flat 减伤与 25% 下限、非 `DIRECT_DAMAGE`；施法者本人不被命中；石化经 `m9_petrify` 注册表统一生命周期（摇晃/挣脱/尘世之锁）；死亡由 M9 管线裁决。2026-09 风洞校准：T3 本人高光/谢幕章隔次记章
- **AI 注记**：公演位 + 执行时地点（报名不锁地点）——报名后若移动，落点跟着变，人群密度决定收益：价值 ≈ Σ(地点内各单位 p_hit·E[伤害]) + 石化 control_utility − case_risk（命中友军/袭警）− exposure（§3.2）；命中友军也受伤，选位须避同盟；石化是控制价值而非击杀价值，对手吃 2 次有效伤害即解除，注意持续压制节奏
<!-- anchor: t3.py:188-205 get_t0_option; 207-228 execute_t0; 151-153 t3_stars_bounce_target; 46-86 _aoe_targets; 89-134 starfall_core; 230-234 _ensure_public_seat; 240-244 borrow_starfall -->

### T4 六爻 `Hexagram9`
- **决策点**：T0 `talent_t0`（选项显示名："六爻"）；控制器 choose 的 situation 标签（全部 T0）：`hexagram_pick_opponent`（选猜拳对手）、`hexagram_my_choice`（我方出拳）、`hexagram_opp_choice`（对手出拳）、`hexagram_thunder_target`（潜龙勿用选天雷目标）、`hexagram_steal_target` / `hexagram_steal_pick`（飞龙在天夺甲）、`hexagram_disarm_target`（亢龙有悔禁武目标）；裸提示：`"六爻演出："`（即演/公演）、`"阴阳的天机：指定卦象还是正常出拳？"`、`"指定卦象："`；G6 借用入口 `hexagram_cast`（或跃重掷至非或跃，绝不授完整额外行动）；关联 special op：无
- **经济与门**：即演 1 SP / 公演 2 SP；前置：至少 1 SP + 存在其他存活玩家（无需 find/lock）；公演须 R0 公演位；阴阳诗天机（`m9_poem_markers["yin_yang_tianji"]`）公演可指定非或跃结果，每次 −1，归零后标记移除；或跃在渊禁止指定
- **核心效果**：猜拳六结果之一——潜龙勿用（穿甲 0.5 天雷，qianlong_pierce_damage）、飞龙在天（复制目标 1 层外甲）、元亨利贞（金身：m9_modify_incoming 归零 + m9_on_lethal 免死，至下轮 R0）、亢龙有悔（禁武 weapon_disable_rounds 轮；仅拳击→震荡）、或跃在渊（完整额外行动，白名单源 t4_hexagram_hojump）、群龙无首（清锁定/探测 + 隐身 + 强制位移 D6）
- **AI 注记**：双人博弈（对手也出拳）——猜拳分布决定六结果期望，选对猜拳对手是关键一票；天机是稀缺指定资源，留给高价值结果（穿甲击杀/金身保命）；或跃在渊 ≈ 额外行动价值折现（§3.2 控制效用）；元亨利贞自保期以下轮 R0 失效；潜龙/飞龙/亢龙可重选目标，不必与猜拳对手同一人
<!-- anchor: t4.py:84-96 get_t0_option; 98-128 execute_t0; 134-136 hexagram_pick_opponent; 147-149 hexagram_my_choice; 150-152 hexagram_opp_choice; 233-235 hexagram_thunder_target; 268-270 hexagram_steal_target; 282-284 hexagram_steal_pick; 331-333 hexagram_disarm_target; 163-184 _maybe_specify_tianji; 364-378 _scissors_paper; 384-433 _rock_paper; 439-452 m9_modify_incoming/m9_on_lethal; 471-488 hexagram_cast -->

### T6 朝阳好市民 `GoodCitizen9`
- **决策点**：T0 `talent_t0`（选项显示名："联防整备"；m9_kind=`t6_equip`）；本槽内部 choose 全为裸提示（无 situation 标签）：`"选择联防整备方式"`（即演/公演）、`"选择整备警察："`、`"整备类型："`、`"选择{slot}："`；关联 special op（special_op.py）：`热线举报{玩家名}`（根行动，任意地点，不读 SP）、`竞选队长`（R2 就任）、`指挥{警员}移动`（队长专用；破界属 G3，非本槽）
- **经济与门**：即演 1 SP / 公演 2 SP；前置：`m9_police` 挂载且未停机 + 同地点存活警察 + 持有白名单真实装备（武器 baton/gauss_rifle/magic_barrage；护甲 shield/ceramic_armor/magic_shield/at_field）——执行时无存活警察则在消费 SP 前取消；热线不读 SP 但消耗标准根行动，举报前检：证据四类关闭清单（受害者/同地点目击者/系统探测器/T6 特别线索），已有通缉或结界内外 → 前置失败不耗证据/槽
- **核心效果**：把一件真实持有装备转交给同地点存活警察（武器或护甲，白名单冻结、不生成新装备）；常驻特别线索持久化（event_log 登记），仅存活/复活的 T6 本人可作证据使用
- **AI 注记**：警务投资价值（`m9_police` 世界事实）——配装把玩家装备转成警力执法力，与掩体吸收（station.player_cover）和队长指挥形成公共秩序杠杆；热线需目击/受害/探测/特别线索四类证据之一，和平局无犯罪时举报通道自然变窄（§5.4）；世界时钟黄昏撤掩体、终焉停摆会整体压低本槽价值；竞选队长获得指挥权是高控制高责任选择，注意威信归零即下台并转为通缉目标。2026-09 校准：未登台时保留 SP1 等 SP2 走公演登台；登台后不再报名公演；整备去重（不给同一警员重复换同名装备）
<!-- anchor: t6.py:161-181 get_t0_option; 183-214 execute_t0; 202/240/243/246 裸选择; 95-136 hotline_report; 138-155 _evidence_for; special_op.py:98-136 特殊操作登记; 211-220 热线执行; 239-245 竞选队长; 246-253 指挥 -->

### T7 死者苏生 `Resurrection9`
- **决策点**：T0 `talent_t0`（选项显示名："挂载死者苏生"；m9_kind=`t7_mount`）；控制器 choose 的 situation 标签：`resurrection_pick_target`（选挂载目标，T0）；裸提示：`"挂载方式："`（即演（1 SP）/公演（2 SP））、彼岸诗复活前 `"复活前选择携带一件装备："`；关联 special op：无
- **经济与门**：即演 1 SP / 公演 2 SP；前置：保险未挂载（`m9_insurance.is_mounted()` 为假）、未落幕 + 存在存活挂载目标（含自己）+ SP≥1；公演须 R0 公演位；挂载双向登记关注（目标先、T7 后，仍受每人每轮 +1 上限）；兑现后永久落幕（全局一次）
- **核心效果**：为一名存活玩家挂载全局唯一保险伏笔；普通死亡兑现——家中复活（home 被毁→结算时当前位置→最后安全地点兜底）、revive_hp=8（当前终值）、保留全部物品、恢复可再生破碎护盾、清击杀关系（击杀者无击杀、不成立犯罪、PP 视为未死亡）、复活后 SP=0；`absolute_death` 标签来源跳过保险；G5 彼岸诗强化：复活后 SP=2 + 复活前可选携带一件装备
- **AI 注记**：保险全局一次（`m9_insurance` 世界事实）——挂给谁是全场最重要的单一决策：己方关键角色防斩首，或队友牺牲保人；兑现后 T7 落幕，敌方击杀效用 × (1 − 保险覆盖)（§3.2）在挂载后必须下调；复活点固定 home，挂给守家型/发育型角色价值高；absolute_death 白名单（G5 锚定/G4 天裁/G7 Terror/G1 繁育）不受保，评估免死链时要区分普通死亡与绝对死亡
<!-- anchor: t7.py:52-72 get_t0_option; 74-130 execute_t0; 111-113 resurrection_pick_target; 95-97 挂载方式裸提示; 149-184 on_death_check; 186-200 _revive_location; 220-246 _consume_far_shore_watch -->

---

<!-- 源锚点清单（file:line）：
- T1 OneSlash9：t1.py:116-134 get_t0_option；136-190 execute_t0；96-98 oneslash_chase_target；162-164 t1_performance_mode；221-223 oneslash_pick_weapon；230-232 oneslash_pick_target；54-62 _legal_targets（ENGAGED_WITH）；73-86 _ranger_chase_targets（LOCKED_BY）；202-210 _chase_to；235-260 _slash
- T2 ScissorRush9：t2.py:373-396 get_t0_option；398-436 execute_t0；364-368 t2_core_target；419-421 t2_performance_mode；471-474 t2_pick_weapon；248-250 t2_earthfire_hunt；331-354 _core_targets（ENGAGED_WITH/LOCKED_BY）；211-232 m9_on_public_root_completed（追猎反应）；234-261 free_hunt_reaction；62-76 on_crime_check（免罪）；82-94 m9_on_attack（回盾）；512-547 core_attack（G6 借用）
- T3 Star9：t3.py:188-205 get_t0_option；207-228 execute_t0；151-153 t3_stars_bounce_target；46-86 _aoe_targets（人群/警察枚举）；89-134 starfall_core；230-234 _ensure_public_seat；240-244 borrow_starfall（G6 借用）
- T4 Hexagram9：t4.py:84-96 get_t0_option；98-128 execute_t0；134-136 hexagram_pick_opponent；147-149 hexagram_my_choice；150-152 hexagram_opp_choice；233-235 hexagram_thunder_target；268-270 hexagram_steal_target；282-284 hexagram_steal_pick；331-333 hexagram_disarm_target；163-184 _maybe_specify_tianji（天机）；364-378 _scissors_paper（或跃）；384-433 _rock_paper（群龙无首）；439-452 m9_modify_incoming/m9_on_lethal（金身）；471-488 hexagram_cast（G6 借用）
- T6 GoodCitizen9：t6.py:161-181 get_t0_option；183-214 execute_t0；202/240/243/246 裸选择；95-136 hotline_report；138-155 _evidence_for（证据四类）；special_op.py:98-136 特殊操作登记（热线/竞选/指挥）；211-220 热线执行；239-245 竞选队长；246-253 指挥
- T7 Resurrection9：t7.py:52-72 get_t0_option；74-130 execute_t0；111-113 resurrection_pick_target；95-97 挂载方式裸提示；149-184 on_death_check（兑现）；186-200 _revive_location；220-246 _consume_far_shore_watch（彼岸诗）
- T0 入口共用：engine/action_turn.py:419-428 talent_t0（phase=T0, situation=talent_t0）
-->
