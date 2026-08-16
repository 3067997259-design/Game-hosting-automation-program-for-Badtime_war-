# M9 balance.json 数值占位符汇总

> **日期**：2026-08-08  
> **状态**：B5 第一步历史快照；当前合同已由 PP RFC v0.4 收口，且作者复核后 T5/G0 槽位
> 改为 26 项魂援，本文下方原 28 项表只保留当时取值证据；2026-08-10 的 v0.4.6 增量值
> 另见 §2.3  
> **版本**：历史基线 balance.json v0.4.0；当前增量 v0.4.6
>
> **2026-09 更新**：风洞/数值校准已收敛（5000 局验收通过）。**当前游玩/开发数值以
> [`current/m9_windtunnel_calibration_2026-09.md`](current/m9_windtunnel_calibration_2026-09.md)
> 及其引用的 `data/balance.json` 为准**；本文的历史初始值表不再作为 M9 数值依据。

本文档记录 B5 第一步添加到 balance.json 的所有 M9 新增数值占位符，
其最终位置按 v0.4.0 落在 `m9_talents_extended`（天赋扩展）与 `m9_system`（系统数值）两个命名空间。

---

## 一、`m9_talents_extended`（M9 天赋扩展数值）

### 1.1 `g0`（G0 砂狼白子*Terror）

| 键 | 初始值 | 说明 |
|---|---|---|
| `ar_magazine` | 30 | AR 初始弹匣容量 |
| `ar_base_damage` | 3 | AR 基础伤害 |
| `arrow_to_bullet_ratio` | 3 | 箭矢转子弹比例（1:3） |
| `drone_hp_cost` | 20 | 召唤无人机 HP 成本（%） |
| `drone_duration` | 3 | 无人机持续轮数 |
| `drone_hp` | 5 | 无人机生命值 |
| `drone_bonus_damage` | 1 | 协同攻击附加伤害 |
| `crossfire_hp_cost` | 20 | 十字炮火 HP 成本（%） |
| `crossfire_damage` | 3 | 十字炮火真伤 |
| `relic_support_hp_cost` | 20 | 遗物支援技 HP 成本（%） |
| `breath_max_uses` | 1 | 调整呼吸每局触发次数 |
| `breath_min_hp` | 1 | 调整呼吸最低 HP |
| `breath_duration` | 2 | 调整呼吸持续轮数 |
| `breath_forfeit_heal` | 4 | 呼吸期内每次 forfeit 回血 |
| `breath_recovery_threshold_pct` | 40 | T+4 止损线（最大 HP 百分比） |
| `relic_t1_mult` | 1.5 | T1 遗物支援技伤害倍率 |
| `relic_t2_duration` | 2 | T2 遗物支援技隐身持续 |
| `relic_t3_damage` | 2 | T3 遗物支援技 AOE 伤害 |
| `relic_g1_damage` | 2 | G1 遗物支援技伤害 |
| `relic_g1_burn` | 2 | G1 遗物支援技灼烧层数 |
| `relic_g3_duration` | 2 | G3 遗物支援技螺旋剑持续 |
| `relic_g3_bonus` | 1 | G3 遗物支援技武器加成 |
| `relic_g4_stacks` | 4 | G4 遗物支援技余烬护甲层数 |
| `relic_g5_memory` | 6 | G5 遗物支援技追忆点数 |
| `relic_g7_ratio` | 0.5 | G7 遗物支援技掩体比例（50%） |
| `relic_g7_duration` | 2 | G7 遗物支援技掩体持续 |
| `g7_synergy_bonus` | 20 | G0×G7 联动加成（%） |

### 1.2 `g1`（G1 火萤燃烧循环）

| 键 | 初始值 | 说明 |
|---|---|---|
| `unarmored_atk_penalty` | 2 | 卸甲常态攻击负修正 |
| `unarmored_def_penalty` | 2 | 卸甲常态防御负修正 |
| `unarmored_acc_penalty` | 15 | 卸甲常态命中负修正（%） |
| `entropy_gain_unarmored` | 1 | 卸甲失熵累积（每轮） |
| `entropy_recover` | 1 | 调息回落值 |
| `entropy_armor_loss` | 5 | 失熵结算内层护甲耐久损耗 |
| `sam_atk_bonus` | 3 | 次级燃烧攻击加成 |
| `sam_def_bonus` | 2 | 次级燃烧防御加成 |
| `full_burn_rounds` | 3 | 完全燃烧持续轮数 |
| `full_burn_atk_bonus` | 4 | 完全燃烧攻击加成 |
| `full_burn_heal` | 2 | 完全燃烧自愈值 |
| `full_burn_supernova_bonus` | 2 | 完全燃烧超新星加成 |
| `supernova_damage` | 8 | 超新星过载伤害 |
| `supernova_pierce` | 0.5 | 超新星穿甲系数 |
| `supernova_burn` | 2 | 超新星灼烧层数 |
| `ardent_temp_hp` | 3 | 炽愿临时生命值 |
| `propagation_rounds` | 3 | 繁育状态固定倒计时 |
| `propagation_hp` | 1 | 繁育状态初始 HP |
| `propagation_initiative` | 10 | 繁育状态先攻加成 |

> **G1 失熵量表未冻结键（仍待风洞，未建档）**：`entropy_gain_sam`、
> `entropy_gain_full_burn`、`entropy_cap`、`entropy_threshold`、
> `entropy_reset_amount`、`entropy_hp_loss`、`break_bonus_damage`、
> `ardent_initial`、`ardent_cap` —— 这些键只在 G1 RFC v0.2 中出现，
> 尚未落入 balance.json v0.4.0，本汇总不为它们预填数值。

### 1.2a `g2`（G2 光影双身与世末终曲 · v0.3）

| 键 | 初始值 | 说明 |
|---|---|---|
| `shadow_hp` | 8 | 普通影身创建时的当前/最大 HP |
| `return_item_count` | 1 | 影身消散时可跨地点归还光身的合法实物上限 |
| `terminal_vulnerability` | 1 | 终曲区域内合法攻击的固定攻击方加值 |
| `terminal_damage_share_ratio` | 1.0 | 从原目标 HP 伤害抽入共享池的比例 |
| `terminal_suppression_uses` | 1 | 每次终曲可消费的 `ACTION_SUPPRESSED` 次数 |
| `terminal_move_redirect_chance` | 0.5 | 根 `move` 离开终曲区域时被偏转的概率 |
| `terminal_witness_ticks` | 3 | `g2_last_song_heard` 所需有听众 R4 tick |
| `terminal_arc_count` | 1 | 首次终曲被听见时的 `arc_count` 增量 |

### 1.3 `g4`（G4 救世主轮回 · W2/W3 已冻结值）

| 键 | 初始值 | 说明 |
|---|---|---|
| `ember_cap` | 12 | 火种上限（完整救世主消耗 12） |
| `ember_gain_hostile` | 1 | 外来敌对效果火种增量（每轮第一次） |
| `ember_gain_positive` | 1 | 外来正面转移火种增量（每轮第一次） |
| `ember_gain_per_round_cap` | 2 | 每全局轮次火种合计上限 |
| `full_duration_r4` | 6 | 完整形态寿命（6 个未来 R4 tick，W3 冻结） |

### 1.4 `g5`（G5 轮回培养、锚定脚本与诗篇候选 · v0.4）

**轮回培养与锚定脚本**

| 键 | 初始值 | 说明 |
|---|---|---|
| `reminiscence_cap` | 24 | 追忆上限 |
| `cyrene_hp` | 8 | 小昔涟最大 HP |
| `cyrene_life_ticks` | 4 | 每一世未来 R4 tick 数 |
| `cyrene_max_incarnations` | 3 | 强制德谬歌诞生前最多世数 |
| `demiurge_birth_threshold` | 12 | 回家时可主动诞生德谬歌的封存追忆门槛 |
| `reminiscence_combat_gain` | 1 | 上轮发生有效战斗的追忆 |
| `reminiscence_combat_personal_bonus` | 1 | 同轮小昔涟作为有效攻击者或有效受伤目标时的额外追忆 |
| `reminiscence_loss_gain` | 1 | 上轮发生摧毁、死亡或回家的额外追忆 |
| `reminiscence_pp_event_gain` | 1 | 小昔涟所在地发生 B4 §3.2 PP 生成事件的额外追忆 |
| `reminiscence_idle_gain` | 0.5 | 上轮三类事件均无时的追忆 |
| `anchor_min_k` | 3 | 锚定脚本最少槽数与闭合退场地板 |
| `anchor_max_k` | 8 | 锚定脚本最多槽数 |
| `crystal_flower_arc_count` | 1 | 水晶花评分 `arc_count` 增量 |
| `double_anchor_arc_count` | 1 | 本局第二次未来闭合完成 G5 完结条时的额外 `arc_count` |
| `poem_cost` | 12 | 德谬歌献诗追忆消耗 |

**诗篇扩展**

| 键 | 初始值 | 说明 |
|---|---|---|
| `poem_destiny_stage_damage` | 5 | 「爱与记忆」诗单段伤害 |
| `poem_destiny_max_stages` | 6 | 「爱与记忆」诗段数上限 |
| `poem_stars_bounce_damage` | 2 | 「群星」诗弹射伤害 |
| `poem_bear_ember` | 2 | 「负世」诗火种增量 |
| `poem_nightwatch_horus` | 2 | 「守夜人」诗铁之荷鲁斯恢复护甲值 |
| `poem_nightwatch_hp_deduct` | 2 | 「守夜人」诗永久额外生命扣除 |
| `poem_joy_laugh_bonus` | 2 | 「欢愉」诗笑点加成 |
| `poem_firefly_entropy_reduction` | 1 | 「飞萤」诗失熵减缓 |
| `poem_firefly_rest_boost` | 1 | 「飞萤」诗调息加成 |
| `poem_firefly_duration` | 6 | 「飞萤」诗持续轮数 |
| `poem_joy_max_duration` | 6 | 「欢愉」诗超时轮数 |
| `poem_watchman_halo_restore` | 3 | 「守夜人」诗光环恢复层数 |
| `poem_watchman_cost_restore` | 3 | 「守夜人」诗 Cost 恢复值 |
| `poem_eternity_cost_reduction` | 1 | 「永恒」诗魔力折扣 |
| `poem_spotlight_damage` | 1 | 「追光」诗伤害加成 |
| `poem_spotlight_shadow_heal` | 1 | 「追光」有效攻击后治疗影身，或终曲承诺时治疗歌者 |
| `poem_burden_annihilation` | 5 | 「负世」诗毁伤转化值 |
| `poem_tomorrow_duration` | 6 | 「明天」诗持续轮数 |
| `poem_tomorrow_uses` | 3 | 「明天」诗使用次数 |

### 1.5 `g7`（G7 战术压制）

| 键 | 初始值 | 说明 |
|---|---|---|
| `iron_horus_repair` | 8 | 铁之荷鲁斯修复耐久 |
| `halo_value` | 3 | 光环每层护体值 |
| `cost_base_cap` | 5 | Cost 基础上限 |
| `halo_initial_layers` | 3 | 光环初始层数 |
| `iron_horus_durability` | 20 | 铁之荷鲁斯耐久 |
| `adrenaline_cost_cap` | 10 | 肾上腺素临时 Cost 上限 |
| `adrenaline_initiative_bonus` | 3 | 肾上腺素先攻加成 |
| `shield_block_threshold` | 6 | 架盾完全格挡阈值 |
| `shield_overflow_durability_cost` | 3 | 架盾溢出耐久消耗 |
| `hold_defense` | 5 | 持盾减法防御 |
| `passive_defense` | 3 | 被动防线减法防御 |
| `passive_break_reserve` | 2 | 被动防线将破保留线 |
| `eye_pellet_damage` | 2 | 荷鲁斯之眼弹丸伤害 |
| `armor_pierce_durability_cost` | 6 | 破甲额外耐久损耗 |
| `revive_hp` | 12 | 复活后 HP |
| `terror_armor_per_piece` | 3 | Terror 每件护甲折算额外生命 |
| `terror_horus_divisor` | 4 | Terror 铁之荷鲁斯耐久折算除数 |
| `terror_halo_per_layer` | 3 | Terror 每层光环折算额外生命 |
| `terror_hp_floor` | 12 | Terror 额外生命保底值 |
| `terror_attack_cost` | 6 | Terror 攻击额外生命代价 |
| `terror_attack_damage` | 4 | Terror 攻击全图伤害 |
| `terror_move_cost` | 2 | Terror 移动额外生命代价 |
| `g0_synergy_bonus` | 20 | G7×G0 联动加成（%） |

### 1.6 `t3` / `t7` / `t6`

| 天赋 | 键 | 初始值 | 说明 |
|---|---|---|---|
| T3 | `petrify_duration` | 2 | 石化持续轮数 |
| T3 | `starfall_damage` | 4 | 天星基础伤害 |
| T7 | `revive_hp` | 12 | 复活后 HP |
| T6 | `remote_equip_options` | 3 | 远程配装选项数 |

---

## 二、`m9_system`（M9 系统数值）

### 2.1 `pp`（PP 系统）

| 键 | 初始值 | 说明 |
|---|---|---|
| **生成事件** | | |
| `first_kill` | 2 | 首杀 |
| `revenge_kill` | 3 | 复仇击杀 |
| `armor_break_kill` | 2 | 破甲击杀 |
| `endgame_kill` | 2 | 终焉击杀 |
| `clutch_kill` | 2 | 绝境击杀 |
| `arc_progress` | 1 | 完结条进展 |
| ~~`spectator_passive`~~ | — | 已退役（B4 RFC v0.2 A6：与衰减对冲，实为"死者免衰减"） |
| ~~`aid_accepted`~~ | — | 已退役（A6：主动援助报酬改从生者出价转移，不铸造） |
| ~~`aid_decisive`~~ | — | 已退役（同上） |
| `aid_passive_reward` | 1 | 新增（A4/A6：被动援助提供者固定铸造奖励） |
| **消耗（生前）** | | |
| `reroll_initiative` | 1 | 重掷先攻 |
| `bonus_damage` | 1 | 加伤 |
| `peek_initiative` | 1 | 偷看先攻 |
| `clear_crime` | 2 | 抵消犯罪 |
| **衰减** | | |
| `decay_rate` | 1 | 每轮衰减值 |
| `min_floor` | 0 | 最低保险值 |
| **投注系统** | | |
| `transfer_fee` | 2 | 换人转会费 |
| `blackhorse_atk` | 1 | 黑马进攻增益 |
| `blackhorse_def` | 2 | 黑马防御增益 |
| `blackhorse_bonus` | 10 | 黑马胜利终分加成 |
| `world_poem_g0_heal` | 1 | 「昨日的同伴/绫音的急救」每个 R4 的地点全员回复量 |
| **魂援（历史 28 个天赋效果）** | | |
| `aid_hp_threshold` | 20 | 被动防御援助触发阈值（%） |
| `t1_counter_ratio` | 50 | T1 防御反伤比例（%） |
| `t2_armor_steal` | 2 | T2 进攻削甲值 |
| `t2_evasion_boost` | 15 | T2 防御闪避提升 |
| `t3_aoe_damage` | 2 | T3 防御 AOE 伤害 |
| `t4_exile_duration` | 1 | T4 进攻放逐持续 |
| `t5_perfect_bonus` | 1 | T5 进攻 Perfect 伤害加成（M9 当前已退役） |
| `t5_rejudge_count` | 1 | T5 防御重判次数（M9 当前已退役） |
| `t7_regen_duration` | 2 | T7 进攻回复持续轮数 |
| `t7_regen_boost` | 1 | T7 进攻回复提升 |
| `t7_pp_absorb_ratio` | 1 | T7 防御 PP 吸收比例 |
| `g1_aoe_damage` | 2 | G1 进攻 AOE 伤害 |
| `g1_burn_stacks` | 2 | G1 进攻灼烧层数 |
| `g2_pp_to_atk_cap` | 5 | G2 进攻 PP 转攻击上限 |
| `g2_pp_to_atk_ratio` | 0.5 | G2 进攻 PP 转攻击比例 |
| `g2_pp_to_hp_cap` | 5 | G2 防御 PP 转 HP 上限 |
| `g2_pp_to_hp_ratio` | 1 | G2 防御 PP 转 HP 比例 |
| `g4_ramping_duration` | 3 | G4 递增持续轮数 |
| `g4_ramping_atk` | 0.5 | G4 进攻递增伤害 |
| `g4_ramping_def` | 0.5 | G4 防御递减伤害 |
| `g7_snipe_damage` | 3 | G7 进攻点射伤害 |
| `g7_vulnerable` | 20 | G7 进攻易伤提升（%） |
| `g7_splash_damage` | 2 | G7 进攻 AOE 伤害 |
| `g7_cover_ratio` | 50 | G7 防御掩体比例（%） |
| `g7_cover_min` | 5 | G7 防御掩体最低值 |
| `g7_cover_duration` | 2 | G7 防御掩体持续 |

### 2.2 `scoring_m9`（M9 评分系统）

| 键 | 初始值 | 说明 |
|---|---|---|
| `arc_weight` | 2 | 剧情分权重 |
| `kill_weight` | 3 | 击杀分权重 |
| `damage_weight` | 0.1 | 伤害分权重 |
| `g0_relic_bonus` | 5 | G0 遗物支援技使用加成 |
| `g0_breath_survive_bonus` | 10 | G0 调整呼吸后存活加成 |

### 2.3 v0.4.6 增量合同首轮值（2026-08-10）

| 命名空间 | 键 | 初始值 | 说明 |
|---|---|---:|---|
| `m9_system.pp` | `world_poem_g0_heal` | 1 | G0 世界援助的 R4 地点全员回复 |
| `m9_talents_extended.g3` | `chain_max_repeats` | 2 | 单根行动追加连发上限，即连同首发至多三发 |
| `m9_talents_extended.g3` | `chain_cost_step` | 2 | 连发段线性递增成本步长，等于当前螺旋剑成本 |
| `m9_talents_extended.g3` | `chain_gale_threshold` | 6 | 本根累计耗魔达到后触发「赤原猎风」 |
| `m9_talents_extended.g3` | `collapse_terminal_min_magic` | 2 | 终段幻想崩坏所需最低剩余魔力 |
| `m9_talents_extended.g5` | `reminiscence_combat_personal_bonus` | 1 | 小昔涟亲历有效战斗时，在全局战斗 +1 之外再 +1 |

---

## 三、数值设计原则

### 3.1 保守起步

所有初始值采用保守策略：
- 伤害类：2-4 点（HP20 量纲下的小幅增益）
- 持续类：2-3 轮（短期增益，不过度影响局势）
- 百分比：20-50%（显著但不压倒性）
- 成本：20% HP（G0 燃烧生命主题的统一代价）

### 3.2 数值分级

| 强度 | 数值范围 | 适用场景 |
|---|---|---|
| 低 | 1-2 点伤害 / 10-20% 加成 | 频繁触发、持续效果 |
| 中 | 3-4 点伤害 / 30-50% 加成 | 条件触发、中期效果 |
| 高 | 5+ 点伤害 / 60%+ 加成 | 罕见触发、终局效果 |

### 3.3 待风洞验证

以下数值需要在 B5 后续步骤（纸面推演、原型验证）中重点平衡：
- G0 无人机持续时间（3 轮）vs 召唤成本（20% HP）
- PP 衰减速率（1/轮）vs 生成事件频率
- 魂援效果强度 vs 遗物支援技强度
- G1 失熵累积速度 vs 调息回落速度
- 完整额外行动白名单（4 个）的平衡性

---

## 四、下一步（B5 后续）

1. **纸面推演**：模拟典型 6 人局，验证数值合理性
2. **原型验证**：实现关键机制（召唤物、遗物系统、神秘属性）
3. **平衡调整**：根据测试结果调整上述初始值
4. **代码迁移**：引擎、天赋、AI、RL 代码适配
5. **运行时烟雾**：stats_runner.py 验证崩溃数、平均轮数

**balance.json 版本轨迹**：v0.3.0 → v0.4.0（M9 扩展历史基线）→ v0.4.6（当前工作树）
