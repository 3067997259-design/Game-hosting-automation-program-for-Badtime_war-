# M9 槽位卡：G4–G7

> 父文档：[talents.md](talents.md)（共享世界模型 §0 / 交互矩阵 §2 / 评分器 §3 / Policy 协议 §4）。
> 本卡只承诺**决策接口事实**（决策点/经济/门/世界事实读写）与设计约定；机制语义以
> `docs/m9/current/` 对应 RFC 为准：G4 见 m9_g4_savior_cycle_rfc_v0.3、G5 见
> m9_g5_anchor_contract_rfc_v0.4 + m9_g5_poems_rfc_v0.1、G6 见 m9_g6_cutaway_joke_rfc_v0.2、
> G7 见 m9_g7_tactical_suppression_rfc_v0.3。治理同步（§6）以源码锚点（文末注释）为准。

### G4 愿负世 `Savior9`

- **决策点**：
  - T0 `talent_t0`（g4.py:381-410）：`负世·主动燃尽`（m9_kind=`g4_active_burn`）／人形态近战演出（m9_kind=`g4_human_performance`，即演 1 SP 单体 / 公演 2 SP 扫击全部 engaged 目标）／`灾厄·弑魂焚诏`（m9_kind=`g4_challenge`）；形态门决定同一轮至多一项
  - 人形态演出方式 choose（situation=`g4_human_performance_mode`）：`公演（2 SP）`/`即演（1 SP）`
  - 人形态演出武器/目标 choose（situation=`g4_strike_pick_weapon` / `g4_strike_pick_target`）
  - 控制器裸 choose（无 situation 标签）：焚诏拉条 `g4.py:429`「焚诏拉条：{name} 选择攻击或拒战？」选项 `攻击`/`拒战`（除 G4 外每名存活玩家各一次，秘密承诺；异常兜底"拒战"）
  - 关联 special op：无
- **经济与门**：
  - 火种 divinity 上限 12（人形态积累：外来敌对首次 +1 / 外来正面转移首次 +1 / 人形态即演或公演各 +2，g4.py:113-145）；`m9_burden_unlocked` 门（负世诗解锁，poems.py:306）
  - 人形态演出：SP≥1、至少一把可用近战武器、至少一个同地点存活 ENGAGED_WITH 目标；即演扣 1 SP 单体普通近战攻击；公演扣 2 SP + 本轮公演位，对全部同地点存活 engaged 目标结算武器伤害 + `human_public_bonus`（默认 1）
  - 主动燃尽：人形态 + 火种≥12 + m9_burden_unlocked → 完整形态，消耗 12 火种；SP **置 2**（非 +2，g4.py:193）；登记完整额外行动来源 `g4_savior_active_burn`（dispatch_full_extra，g4.py:251）
  - 焚诏：形态内 + SP≥2 + ruin_damage>0；占用本轮公演位（assign_public_slot + dispatch_public，g4.py:270-273）；反击/天裁池数值 `counter_total(D)`/`J` 待风洞
  - §2 矩阵 G4 行：结界/警务/终曲/影身/无人机/石化/保险/爱愿/被毁地点全 R；根行动入模板池 W*；焚诏天裁致死写入 `absolute_death` 白名单（combat.py:30 含 `g4_judgment`），跳过 T7 免死/保险
  - 形态寿命：完整 6 tick、建立轮 R4 不 tick（g4.py:205-210）；残缺按火种缩放 + `ember_floor` 最低地板（g4.py:182-188）；余烬生命池 ember_hp，形态内普通致死 = 消耗（g4.py:145-156），空池 1 HP 退场；`absolute_death` 直死不走本类
- **核心效果**：人形态通过即演/公演主动获取火种（每次演出 +2，按演出完成发放，不按命中）；火种至 12 后进入救世主形态（强化普攻 +毁伤，m9_modify_outgoing g4.py:378-382），SP=2 时发动焚诏公演——全桌秘密承诺攻/拒，攻击者吃统一反击、拒战者吃死星天裁（`DIRECT_DAMAGE` + `absolute_death`，白名单 combat.py:30-40 含 `g4_judgment`/`g4_counter`）。
- **AI 注记**：好策略要决定①何时主动燃尽（火种满 12 且已解锁，但完整形态 6 tick 内能否排到公演位——排不到就留人形态继续囤火种）；②人形态即演/公演是主动火种来源：无公演位/仅 1 个 engaged 目标走即演，持位且未登台或 ≥2 个 engaged 目标走公演；③人形态是"嘲讽靶"：打 G4 喂火种且可能被焚诏反击，攻击者的期望承伤随毁伤上升（§3.2 exposure 项）；④发动焚诏前预判对手攻/拒——攻者分摊反击、拒者摊天裁，全员拒战全池归拒战者、全员攻击只结算反击；⑤对手视角：集火攻击救世主可强制退场打断公演（§5.2），把"打断 G4 蓄力"作为高价值协同目标。

### G5 往世的涟漪 `Ripple9`

- **决策点**：
  - T0 `talent_t0`（g5.py:338-360）：SP≥2 → `公演：锚定 / 献诗`（m9_kind=`g5_anchor_or_poem`；仅德谬歌 DEMIURGE、SP≥2、无激活锚定）；SP≥1 且微澜重开 → `微澜：1 SP 信息型即演`（m9_kind=`g5_ripple`）
  - 控制器 hook：`choose_anchor_script(player, state)`（g5.py:264-266）填写 K 槽脚本；未实现/返回非法 → 确定性兜底 K 个 move 槽（`_DEFAULT_ANCHOR_LOCATIONS`，g5.py:254-275）
  - 诗篇入口：`recite_poem(poem_name, target_pid)`（g5.py:136-139 → poems.py:71-108）——选诗名+目标为调用方输入（裸 prompt，无 situation 标签）；守夜人诗由目标 `confirm`（poems.py:250）
  - 关联 special op：无
- **经济与门**：
  - 锚定：公演 2 SP + K 点追忆（K∈[3,8]，g5.py:291）+ 本轮公演位（g5.py:304-307）；投影预检（须产出 ≥1 候选）先于 SP/追忆/槽消费（g5.py:293-303）
  - 追忆只在小昔涟阶段 R0 结算（combat/loss/pp_event/idle 每类每轮一次，g5.py:206-222），cap 24；德谬歌诞生后为**有限总预算**（g5.py:161-163）
  - 互斥：激活锚定不可献诗（poems.py:78）；存在活跃爱愿不可锚定（g5.py:286-287）；献诗 poem_cost=12 追忆
  - 完整额外行动来源：地火诗 `g5_poem_earthfire`（poems.py:153）；彼岸诗复活置 SP2 + 带装备（poems.py:278-281）
  - 微澜（合同 §四，1 SP 信息型即演）在 adapter 未见接线：g5.py 只暴露锚定 T0 与 `recite_poem` 入口，策略暂按不可用处理
- **核心效果**：德谬歌用 2 SP + K 追忆写下 K 槽未来脚本，系统逐槽投影差分抽取 DEFEAT/DESTROY/RELOCATE/ACQUIRE 候选事件并逐 R4 兑现——自然实现或再投影强制（DEFEAT 强制 = `absolute_death`，白名单 combat.py:30 含 `g5_anchor`）；全部实现 → 未来闭合（快照窄回溯 + 水晶花，g5.py:390-421），第二次闭合得 PP 完结条；失败 → 因果被改写（不回溯仍得花）。
- **AI 注记**：锚定是长周期价值投资（§3.3 按脚本槽预期实现率折现）——脚本应写"世界大概率自然发生"的事件（第三方也能实现，不抢功劳），K 越小越易闭合但预算利用率低；追忆是德谬歌唯一有限预算，要在锚定与献诗之间分配（余额 <12 只能锚定）；爱愿是全局控制杠杆（持有者对 G5 伤害免疫，combat 先查，talents.md §0），决定给谁献诗影响全局仇恨与集火方向；按目标槽位选择诗篇：彼岸（T7 装备指定）、地火（T2 完整额外行动）、负世（G4 火种/解锁）。

### G6 要有笑声！ `CutawayJoke9`

- **决策点**：
  - T0 `talent_t0`（g6.py:199-228）：`即演：重演上一轮行动`（m9_kind=`g6_improvise`，SP≥1 且有合法类别）／`公演：插入式笑话`（m9_kind=`g6_public`，SP≥2）；合法即演优先返回，否则只出公演
  - 控制器裸 choose（无 situation 标签）：`即演重演或公演？`（g6.py:247）→ `选择重演类别`（g6.py:251）→ `公演路径：借用核心/召唤援助`（g6.py:263）→ `选择借用核心`（g6.py:277）→ 猜拳目标 `选择猜拳目标`（g6.py:317）→ `出拳`（g6.py:328/329）；choose 异常/非法取默认（_pick 兜底首个，g6.py:101-111）
  - 关联 special op：无
- **经济与门**：
  - 即演 1 SP（dispatch_improvise，g6.py:254）重演窗口内模板类别 move/interact/find/lock/attack（g6.py:22），窗口 1 轮（欢愉延展 2 轮，g6.py:50-52）；类别合法性预检先于 SP 消费（g6.py:245-253）
  - 公演 2 SP 双路径互斥（g6.py:263-268）：借用核心（白名单 t1/t2/t3/t4/g3/g4，g6.py:37-44）或召唤往世层援助（无 PP/无配额，提供者得被动奖励，g6.py:176-181）；借用预检先于 SP/公演位消费（g6.py:278-281）
  - T4 或跃在渊必须重掷到非或跃（g6.py:167-173、335-339）——**绝不创建完整额外行动**；G2 永不出现在借用白名单
  - 欢愉诗延展：adapter 只读 `joy_extend` 标记（g6.py:197/242）——窗口 2 轮 + 下次公演可顺序借两名不同合格玩家核心（至多一个攻击核心，RFC §八）
- **核心效果**：G6 重演他人上一轮的行动类别（用自己的装备/资源/目标，不继承原天赋被动/数值/责任），或在公演时立即结算一枚白名单天赋核心 / 召唤往世层援助，制造"你怎么有我的招式"的荒诞与战术混乱。
- **AI 注记**：冷启动轮模板池为空（§4.2 从零写），必须先做一次普通行动才能即演；即演是 1 SP 低成本机动——盯住上一轮 attack/lock/find 等高价值类别重演，责任（犯罪/击杀/关注）归 G6；公演借用核心 = **策略委托给被借槽**（§2 委托型交互复用被借槽策略），G6 只做预检后选择，注意执行差异（T4 或跃重掷、G3 仅单体投影、G4 强化普攻固定倍率）；召唤援助无 PP 成本，缺伤害/防御时作保底。BasicAI 持公演位且 SP≥2 时优先公演（有核心借核心、无核心走援助）；无公演位时只在窗口内能重演 attack 才花 1 SP 即演。

### G7 大叔我啊，剪短发了 `Hoshino9`

- **决策点**：
  - T0 `talent_t0`（engine/m9/talents/g7.py:185-215）：SP≥2 → `公演：战术补给`（m9_kind=`g7_public`，免费获得一项战术道具或药物 + 魂援窗口）；SP≥1 → `即演：小准备`（m9_kind=`g7_improvise`，下个 R0 豁免失却汇流成泉）；Terror 形态两者均不可用（§2.7）
  - 起床受限追演裸 prompt：`ctrl.choose("起床受限追演：", ["结束","move","interact","find","lock"])`（g7.py:64-67）
  - legacy situation 标签（引擎发出，talents/g7/*.py）：`hoshino_form`（注册形态 hoshino.py:83）、`hoshino_self_doubt_choice`（T0 色彩≥6 hoshino.py:263）、`hoshino_tactical_equip`（fusion_mixin.py:96）、`hoshino_repair_material`（fusion_mixin.py:133）、`hoshino_shield_shoot_target`（:349）、`hoshino_shoot_target`（:383）、`hoshino_reload`（:592）、`hoshino_throw_item`（:676）、`hoshino_throw_location`（:686）、`hoshino_medicine`（:803）、`hoshino_dash`（:842）、`hoshino_reorder_ammo`（:971）、`hoshino_tactical_input`（:139）；choose_mixin 处理器键：`hoshino_form_choice` / `hoshino_self_doubt` / `hoshino_dash_target` / `hoshino_find_target` / `hoshino_shoot_target` / `hoshino_medicine` / `hoshino_tactical_equip` / `poem_nightwatch_choice`（choose_mixin.py:57-167）
  - 演出入口裸 choose：`选择演出：`（公演/即演）、`选择补给类别：`（战术道具/药物）（g7.py:204-244）
  - 关联 special op（special_op.py）：`更衣<形态>`（:60-69/174-192，situation=`hoshino_change_form`）、`取消盾牌`（:55-58）、`Hoshino` 战术宏（:70-75）、`修复`（:77-81）、`肾上腺素`（:83-88，不耗回合）
- **经济与门**：
  - Cost 池 R0 回满（基础上限 `cost_base_cap`=5）；上一轮用过战术宏 → 下 R0 失却汇流成泉 −1；即演（1 SP）豁免下轮 −1（g7.py:156-168，豁免路径读 `cost_base_cap`）；肾上腺素全局一次 → 下 R0 cost 回满至 `adrenaline_cap`=10 并覆盖失却汇流成泉（RFC §2.5，经 v2exp `on_round_start` super() 路径）；战术宏不需 SP，只耗 Cost（special Hoshino）
  - Terror 攻击：批处理 `DIRECT_DAMAGE` + `absolute_death`（白名单 combat.py:30-40 含 `g7_terror`）；A=`terror_attack_damage`=4 全体玩家单位（g7.py:110-135），结算后扣 `terror_attack_cost`=6 额外 HP，全灭免扣（g7.py:137-150）
  - 起床：临战-Archer 无额外行动回合 → 同槽受限追演标记（g7.py:48-49）；水着获盾；临战-Shielder 恢复 1 层光环（g7.py:43-52）
  - §2 矩阵 G7 行：警务/掩体/通缉 R(逃离)、终曲/影身/无人机/石化/保险/爱愿/被毁地点 R、根行动入模板池 W*、SP/公演位 R(豁免)
- **核心效果**：以融合装备（铁之荷鲁斯 + 荷鲁斯之眼）与 Cost 驱动的战术宏实现持续战术压制，三形态提供不同全局加成；色彩 6 在 T0 选择是否自我怀疑、色彩 10 强制自我怀疑 → 反转为 Terror 纯粹破坏者。
- **AI 注记**：Terror 攻击是**批处理 DIRECT_DAMAGE**（不能发普通 attack，须走 `_terror_attack` 路径；`is_terror` 时警察逃离且拒绝队长对 Terror 的指令——§2 矩阵 G7 行"R(逃离)"，TerrorDefense 对批处理路径反应，T7/免死不赔付）；自我怀疑只在残局可斩杀或 1 HP 翻盘时接受；战术未解锁时不报名公演（补给=死库存），解锁后无弹药也走纯投掷宏；wake_followup 只能 move/interact/find/lock/结束（不能攻击/宏/即演，价值低但免费）；Archer 连续射击计数只在非射击攻击或换形态时重置（g7.py:91-93），应连续宏射击白嫖第三发免费射击。

---

<!-- 源码锚点（file:line）
G4  engine/m9/talents/g4.py:
     :227-241 get_t0_option（负世·主动燃尽 m9_kind=g4_active_burn / 灾厄·弑魂焚诏 m9_kind=g4_challenge）
     :297-301 焚诏拉条裸 choose（攻击/拒战，异常兜底拒战）；:283-292 快照；:303-366 反击/天裁结算
     :251-252 完整额外行动来源 g4_savior_active_burn；:193 SP 置 2（set_sp 非 +2）
     :119-139 火种来源（敌对首次+1/正面首次+1，每轮至多+2）；:113 火种上限 12
     :145-156 形态内普通致死=余烬消耗；:182-188 ember_floor/残缺缩放；:205-210 建立轮不 tick
     :270-273 公演位 assign_public_slot+dispatch_public；:378-382 强化普攻 +ruin_damage
G5  engine/m9/talents/g5.py:
     :338-360 get_t0_option（公演 m9_kind=g5_anchor_or_poem：锚定/献诗；微澜 m9_kind=g5_ripple，仅 DEMIURGE/SP≥2 或 SP≥1 微澜重开/无激活锚定）
     :366-395 execute_t0 演出选择（微澜/献诗/锚定）；:370-391 微澜 1 SP 信息即演；:393-447 献诗选诗名+目标
     :263-275 choose_anchor_script hook + 确定性兜底 K move 槽；:291 K∈[3,8]
     :293-319 execute_anchor（投影预检先于 SP/追忆/公演位消费）；:300-307 2 SP+公演位
     :206-222 追忆 R0 结算（combat/loss/pp_event/idle）；:321-327 爱愿互斥
     :329-342 R4 逐槽监控；:361-388 自然实现/再投影强制；:370-382 DEFEAT 强制=absolute_death（g5_anchor）
     :390-421 未来闭合/窄回溯/水晶花/第二次闭合 PP；:136-139 recite_poem
     engine/m9/talents/poems.py:71-108 献诗共享入口（DEMIURGE/2SP/poem_cost=12/公演位/天赋绑定/爱愿）
     poems.py:147-162 地火 full_extra g5_poem_earthfire；:246-276 守夜人 confirm；:278-281 彼岸；
     :288-307 负世（火种+2/毁伤/解锁 m9_burden_unlocked）
G6  engine/m9/talents/g6.py:
     :199-228 get_t0_option（即演 m9_kind=g6_improvise / 公演 m9_kind=g6_public）
     :247/:251/:263/:277/:317/:328/:329 裸 choose 锚点（即演重演或公演/类别/公演路径/借用核心/猜拳目标/出拳）
     :22 类别白名单 move/interact/find/lock/attack；:31-34 排除清单；:37-44 G6_BORROWABLE_CORE 白名单
     :50-52 窗口 1 轮/欢愉 2 轮；:101-111 _pick 兜底首个；:154-165 借用预检先于 SP
     :167-173 或跃重掷（HOJUMP_RESULT_KEY）；:176-181 援助 0 PP/提供者奖励；:254 dispatch_improvise
G7  engine/m9/talents/g7.py:
     :31-32 wake_followup_available；:38-52 on_wakeup（水着获盾/Archer 追演标记/Shielder 光环）
     :54-79 m9_wake_followup 受限追演（裸 prompt：结束/move/interact/find/lock）
     :85-93 射击计数重置（仅非射击攻击/换形态）；:99-150 _terror_attack（DIRECT_DAMAGE+absolute_death，
     A=terror_attack_damage，扣 terror_attack_cost，全灭免扣）
     :156-168 R0 即演豁免 + 豁免路径 cost_base_cap；:166-168 m9_mark_improvise_exempt
     talents/g7/hoshino.py:83 hoshino_form；:260-263 hoshino_self_doubt_choice；:271-273 get_t0_option=None
     （adrenaline_cap=10 语义见 m9_g7_tactical_suppression_rfc_v0.3 §2.5，经 v2exp on_round_start 生效）
     talents/g7/fusion_mixin.py:96 hoshino_tactical_equip；:133 hoshino_repair_material
     talents/g7/tactical_mixin.py:139 hoshino_tactical_input；:349 hoshino_shield_shoot_target；
     :383 hoshino_shoot_target；:592 hoshino_reload；:676 hoshino_throw_item；:686 hoshino_throw_location；
     :803 hoshino_medicine；:842 hoshino_dash；:971 hoshino_reorder_ammo
     actions/special_op.py:55-88 取消盾牌/更衣（hoshino_change_form）/Hoshino/修复/肾上腺素
     controllers/ai/choose_mixin.py:57-167 处理器键（hoshino_form_choice/hoshino_self_doubt/
     hoshino_dash_target/hoshino_find_target/hoshino_shoot_target/hoshino_medicine/
     hoshino_tactical_equip/poem_nightwatch_choice）
白名单  engine/m9/combat.py:30-31 ABSOLUTE_DEATH_SOURCES（g7_terror/g4_judgment/g5_anchor/g1_propagation）
     combat.py:34-40 DIRECT_DAMAGE_SOURCES（g0_crossfire/g4_counter/g4_judgment/g7_terror/world_clock_apocalypse）
-->
