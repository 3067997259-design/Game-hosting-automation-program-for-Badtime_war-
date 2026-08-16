# M9-rfc 风洞校准与数值台账（2026-09）

> **日期**：2026-09
> **状态**：当前生效的数值与机制校准记录；`--profile m9-rfc` 下以本文件 + `data/balance.json` 为
> 当前数值权威。机制语义仍以 `current/` 各 RFC 为规范，本文件只登记风洞期间发生的
> 校准裁决与终值，不与 RFC 正文冲突。
> **验收规模**：5000 局（`stats_runner.py --profile m9-rfc --players 6 --games 5000
> --seed 20260923`，结果文件 `windtunnel_archive/out_accept_5000.txt`）。

## 一、收敛目标与结果

目标：天赋胜率与人格胜率极差均收敛到 **最高 ≤ 最低 × 2**。

| 指标 | 最高 | 最低 | 极差比 | 结论 |
|---|---:|---:|---:|---|
| 天赋胜率 | G7 大叔短发 19.8% | G0 白子Terror 14.1% | **1.40×** | 达标 |
| 人格胜率 | defensive 18.7% | political 13.1% | **1.43×** | 达标 |

完整 5000 局天赋表（胜场/胜率）：

| 槽位 | 天赋 | Pick数 | 胜率 |
|---|---:|---:|---:|
| G7 | 神代天赋-大叔我啊，剪短发了 | 2400 | 19.8% |
| G4 | 神代天赋-愿负世，照拂黎明 | 2769 | 18.3% |
| G5 | 神代天赋-往世的涟漪 | 2227 | 17.9% |
| G2 | 神代天赋-请一直注视着我 | 1851 | 17.4% |
| T3 | 天星 | 1693 | 17.3% |
| G1 | 神代天赋-火萤IV型-完全燃烧 | 1856 | 17.0% |
| T7 | 死者苏生 | 2909 | 16.6% |
| G6 | 神代天赋-要有笑声！ | 2321 | 16.4% |
| G3 | 神代天赋-神话之外 | 1755 | 16.1% |
| T6 | 朝阳好市民 | 1590 | 16.1% |
| T1 | 一刀缭断 | 1865 | 16.1% |
| T4 | 六爻 | 2539 | 15.2% |
| T2 | 剪刀手一突 | 2667 | 14.2% |
| G0 | 砂狼白子*Terror | 1558 | 14.1% |

健康指标：平均轮次 40.7；平局率 3.9%（193/5000），其中「达到轮次上限(AI 僵持)」
1 次；引擎崩溃 0。

## 二、风洞期间的机制校准裁决（玩家可见，已生效）

以下按槽位列出相对 RFC 初稿的变更；没有列出的结构语义保持对应 `current/` RFC 不变。

### G0 砂狼白子*Terror
1. **十字炮火不再是 DIRECT_DAMAGE**：改为普通属性、必中（内部命中加值 1000），
   护甲与普通减伤照常生效；仍命中地点全员（含 G0），仍消耗无人机。
2. AR 基础伤害 3→1、弹匣 30→15；无人机 HP 5→3、持续 3→2、协同追加伤害 1→0。
3. 调整呼吸：持续 2→1 轮、呼吸期内每次 forfeit 回血 4→2、T+4 止损线 40%→30%。

### T3 天星
1. 剧情分减半裁决：T3 本人每**两次**「命中 ≥2 的星落」才记一次第二章（第一次照记）；
   星落击杀的第三章同样隔次记章。G6 借用星落不受此门。
2. `starfall_damage` 4→2、`petrify_duration` 保持 1、挣脱成功率 50%。

### G2 请一直注视着我（光影双身/终曲）
1. **影身击杀补第二章**：`death` 事件的 killer 为 `G2:shadow@<pid>` 时给光身第二章；
   若发生在登台前，登台后补授。
2. 终曲歌者不再执行转换前已发行的旧 grant（收到即收尾）；终曲建立当轮与后续听众
   tick 语义不变。
3. 终曲见证 1 tick；影身 HP 34；伤害共享比例 0.2；终曲压制 2 次；移动偏转 0.6；
   终曲 `arc_count` 增量 3；终曲易伤 0（数值见 §四）。

### G4 愿负世，照拂黎明
1. 火种收入：外来敌对来源 1→**0**、人形态演出 2→**1**、公演伤害加成 1→**0**；
   每轮火种上限仍为 2（敌对被 0 封顶后仅正面来源与演出供火）。
2. 完整形态 6→**5** 个未来 R4 tick；反击池成长 1.0→0.5/毁伤、天裁池 2.0→1.0/段、
   毁伤成长 2→1/次攻击。

### G6 要有笑声！
1. 模板窗口 3→**4** 轮（欢愉延展同为 4）。
2. 借用 G4 强化普攻倍率 3.5→**4.0**；借用 T1 核心斩击倍率 4.0→**4.5**。
3. BasicAI 策略（非玩家机制）：持公演位优先公演，无核心可借走召唤援助；无公演位时
   仅当窗口内能重演 attack 才即演；猜拳目标/出拳已接入 AI 策略；欢愉双借用第二核心
   优先非攻击核心。详见 `docs/m9/ai/slots_g4g5g6g7.md`。

### G7 大叔我啊，剪短发了
1. `m9_talents_extended.g7` 已真实接线：`cost_base_cap`、`eye_pellet_damage`、
   `shield_block_threshold`、`halo_initial_layers`、`archer_break_armor_loss`
   现在实际影响运行（此前部分键读旧 `talents.g7` 硬编码）。
2. 终值：Cost 基础上限 7、荷鲁斯之眼弹丸 3、架盾格挡阈值 8、光环初始 3 层、
   光环值 3、持盾防御 5、被动防御 3、Terror 攻击伤害 4、Terror 额外生命保底 10。
3. BasicAI 策略（非玩家机制）：自我怀疑只在“残局可斩杀或 1 HP 翻盘”时接受；
   战术未解锁时不报名公演；无弹药但有战术道具时走纯投掷宏。

### T6 朝阳好市民（警察线）
1. 固定警力 3 名；单位 HP 30；警棍伤害 10；掩体耐久 2。
2. 联防整备不再重复给同一警员换同名装备（去重）。
3. BasicAI 策略：未登台时保留 SP1 等 SP2 走公演登台；登台后不再报名公演。

### 评分系统
1. 终分权重：剧情分 1.8/章、击杀 1.5/杀、伤害 0.05/点、名次 2/档、唯一生还者 +4。
2. **槽位得分系数**（2026-09 用户批准的新平衡通道）：`ScoringEngine.score` 对合计后的
   base 乘算，只在终分判定中使用，不影响任何玩法动作；系数全表见 §四。

### 其他天赋数值
- T1 核心斩击倍率 1.5→**1.25**；T2 攻击回盾 2→**1**；T7 复活 HP 12→**8**。
- G1/G3/G5 只改数值（无结构变更），终值见 §四。

## 三、BasicAI 逻辑修复批次（不影响玩家规则，仅记录 AI 行为变化）

已同步至 `docs/m9/ai/`：

| 槽位 | 修复 |
|---|---|
| G2 | 影身攻击按武器伤害排序（不再恒拳击）；影身决策上下文绑定；终曲窗口临近才报名 |
| G3 | 螺旋剑连发为下一轮维持费保留预算；终段/独立幻想崩坏仅在可击杀主目标时结算；结界外仅持公演位且有捕捉对象才展开 |
| G4 | 人形态主动燃尽不再要求 engaged 目标；焚诏拉条时机门 |
| G6 | 即演/公演/借用/猜拳/欢愉双借用全链路接线（见上） |
| G7 | 数值接线 + 自我怀疑门 + 纯投掷宏 + 军基发育绕路修复 |
| T1 | 磨刀不再作为 T0 发动前提；游侠诗公演追猎门 |
| T2 | 演出方式按目标距离/锁定关系选择 |
| T3 | 天星只在持公演位时点 T0 |
| T4 | 天机分支优先正常出拳 |
| T6 | SP 蓄势、整备去重、登台后停止报名 |
| T7 | 保险自挂、R5 前蓄势等 SP2 |

## 四、当前数值终值（权威 = `data/balance.json`）

### 4.1 `m9_talents_extended`

| 分组 | 键 | 终值 | 分组 | 键 | 终值 |
|---|---:|---|---:|---:|
| t1 | melee_multiplier | 1.25 | t1 | defense_coefficient | 0.5 |
| t2 | shield_recovery_durability | 1 | t3 | petrify_duration | 1 |
| t3 | starfall_damage | 2 | t3 | break_success_probability | 0.5 |
| t4 | qianlong_pierce_damage | 10 | t4 | weapon_disable_rounds | 3 |
| t7 | revive_hp | 8 | | | |
| g0 | ar_magazine | 15 | g0 | ar_base_damage | 1 |
| g0 | arrow_to_bullet_ratio | 3 | g0 | drone_hp_cost | 20 |
| g0 | drone_duration | 2 | g0 | drone_hp | 3 |
| g0 | drone_bonus_damage | 0 | g0 | crossfire_hp_cost | 20 |
| g0 | crossfire_damage | 1 | g0 | relic_support_hp_cost | 15 |
| g0 | breath_max_uses | 1 | g0 | breath_min_hp | 1 |
| g0 | breath_duration | 1 | g0 | breath_forfeit_heal | 2 |
| g0 | breath_recovery_threshold_pct | 30 | g0 | relic_t1_mult | 1.5 |
| g0 | relic_t2_duration | 2 | g0 | relic_t3_damage | 2 |
| g0 | relic_g1_damage | 2 | g0 | relic_g1_burn | 2 |
| g0 | relic_g3_duration | 2 | g0 | relic_g3_bonus | 1 |
| g0 | relic_g4_stacks | 4 | g0 | relic_g4_absorb | 1 |
| g0 | relic_g5_memory | 6 | g0 | relic_memory_cap | 12 |
| g0 | relic_memory_cost | 12 | g0 | relic_g7_ratio | 0.5 |
| g0 | relic_g7_duration | 2 | g0 | g7_synergy_bonus | 20 |
| g1 | unarmored_atk_penalty | 1 | g1 | unarmored_def_penalty | 1 |
| g1 | unarmored_acc_penalty | 15 | g1 | entropy_gain_unarmored | 1 |
| g1 | entropy_gain_sam | 2 | g1 | entropy_gain_full_burn | 3 |
| g1 | entropy_recover | 1 | g1 | entropy_cap | 12 |
| g1 | entropy_threshold | 6 | g1 | entropy_reset_amount | 4 |
| g1 | entropy_hp_loss | 6 | g1 | entropy_armor_loss | 5 |
| g1 | sam_atk_bonus | 3 | g1 | sam_def_bonus | 1 |
| g1 | full_burn_rounds | 3 | g1 | full_burn_atk_bonus | 2 |
| g1 | full_burn_heal | 2 | g1 | full_burn_supernova_bonus | 2 |
| g1 | supernova_damage | 6 | g1 | supernova_pierce | 0.5 |
| g1 | supernova_burn | 2 | g1 | break_bonus_damage | 2 |
| g1 | ardent_initial | 1 | g1 | ardent_cap | 4 |
| g1 | ardent_temp_hp | 3 | g1 | propagation_rounds | 3 |
| g1 | propagation_hp | 1 | g1 | propagation_initiative | 10 |
| g1 | supernova_grant_cap | 3 | g1 | dress_cooldown_rounds | 1 |
| g1 | burnout_dress_lockout_rounds | 2 | | | |
| g2 | shadow_hp | 34 | g2 | return_item_count | 1 |
| g2 | terminal_vulnerability | 0 | g2 | terminal_damage_share_ratio | 0.2 |
| g2 | terminal_suppression_uses | 2 | g2 | terminal_move_redirect_chance | 0.6 |
| g2 | terminal_witness_ticks | 1 | g2 | terminal_arc_count | 3 |
| g3 | magic_initial | 6 | g3 | magic_cap | 8 |
| g3 | magic_recover_r0 | 1 | g3 | public_temp_magic | 4 |
| g3 | spiral_cost | 2 | g3 | spiral_damage | 5 |
| g3 | spiral_hit_bonus | 15 | g3 | chain_max_repeats | 2 |
| g3 | chain_cost_step | 2 | g3 | chain_gale_threshold | 6 |
| g3 | dual_blade_cost | 1 | g3 | dual_blade_attack_bonus | 2 |
| g3 | dual_blade_reduction | 2 | g3 | rho_aias_cost | 2 |
| g3 | rho_aias_durability | 8 | g3 | copy_weapon_cost | 1 |
| g3 | outside_copy_ratio | 0.75 | g3 | sword_array_cost | 2 |
| g3 | sword_array_hit_bonus | 15 | g3 | sword_array_durability | 6 |
| g3 | sword_array_collapse_bonus | 2 | g3 | barrier_base_upkeep | 1 |
| g3 | barrier_per_unit_upkeep | 1 | g3 | barrier_wall_upkeep | 1 |
| g3 | barrier_array_upkeep | 1 | g3 | max_barrier_rounds | 5 |
| g3 | ideal_burn_styles | 3 | g3 | ideal_burn_cost_reduction | 1 |
| g3 | ideal_burn_upkeep | 1 | g3 | collapse_base_damage | 5 |
| g3 | collapse_per_style | 2 | g3 | collapse_style_cap | 5 |
| g3 | collapse_terminal_min_magic | 2 | g3 | barrier_anchor_durability | 10 |
| g3 | break_action_power | 2 | g3 | armament_overload_cost | 2 |
| g3 | armament_overload_bonus | 2 | g3 | dual_blade_base_damage | 3 |
| g4 | ember_cap | 12 | g4 | human_performance_ember | 1 |
| g4 | human_public_bonus | 0 | g4 | ember_floor | 1 |
| g4 | ember_gain_hostile | 0 | g4 | ember_gain_positive | 1 |
| g4 | ember_gain_per_round_cap | 2 | g4 | full_duration_r4 | 5 |
| g4 | challenge_punch | 2 | g4 | challenge_reduction | 3 |
| g4 | counter_pool_per_ruin | 0.5 | g4 | judgment_per_segment | 1.0 |
| g4 | ruin_gain_per_attack | 1 | g4 | ruin_start | 3 |
| g4 | ruin_cap | 9 | | | |
| g5 | reminiscence_cap | 24 | g5 | cyrene_hp | 10 |
| g5 | cyrene_life_ticks | 4 | g5 | cyrene_max_incarnations | 3 |
| g5 | demiurge_birth_threshold | 12 | g5 | reminiscence_combat_gain | 1 |
| g5 | reminiscence_combat_personal_bonus | 1 | g5 | reminiscence_loss_gain | 1 |
| g5 | reminiscence_pp_event_gain | 1 | g5 | reminiscence_idle_gain | 1 |
| g5 | anchor_min_k | 3 | g5 | anchor_max_k | 8 |
| g5 | crystal_flower_arc_count | 1 | g5 | double_anchor_arc_count | 1 |
| g5 | poem_cost | 10 | g5 | poem_lovewish_ticks | 6 |
| g5 | poem_destiny_stage_damage | 5 | g5 | poem_destiny_max_stages | 6 |
| g5 | poem_stars_bounce_damage | 2 | g5 | poem_bear_ember | 2 |
| g5 | poem_nightwatch_horus | 2 | g5 | poem_nightwatch_hp_deduct | 2 |
| g5 | poem_joy_laugh_bonus | 2 | g5 | poem_firefly_entropy_reduction | 1 |
| g5 | poem_firefly_rest_boost | 1 | g5 | poem_firefly_duration | 6 |
| g5 | poem_reduced_firefly_damage_reduction | 1 | g5 | poem_joy_max_duration | 6 |
| g5 | poem_joy_borrow_cores | 2 | g5 | poem_watchman_halo_restore | 3 |
| g5 | poem_watchman_cost_restore | 3 | g5 | poem_watchman_spend_cap | 2 |
| g5 | poem_watchman_armor_restore | 2 | g5 | poem_eternity_cost_reduction | 2 |
| g5 | poem_spotlight_damage | 1 | g5 | poem_spotlight_shadow_heal | 1 |
| g5 | poem_burden_annihilation | 5 | g5 | poem_burden_per_round_source | 2 |
| g5 | poem_burden_bonus_per_stack | 2 | g5 | poem_burden_max_stacks | 6 |
| g5 | poem_tomorrow_duration | 6 | g5 | poem_tomorrow_uses | 3 |
| g6 | template_window_rounds | 4 | g6 | joy_template_window_rounds | 4 |
| g6 | g4_borrow_basic_multiplier | 4.0 | g6 | core_slash_multiplier | 4.5 |
| g6 | core_slash_defense_coefficient | 0.5 | | | |
| g7 | iron_horus_repair | 8 | g7 | halo_value | 3 |
| g7 | cost_base_cap | 7 | g7 | halo_initial_layers | 3 |
| g7 | iron_horus_durability | 20 | g7 | adrenaline_cap | 10 |
| g7 | adrenaline_initiative_bonus | 3 | g7 | shield_block_threshold | 8 |
| g7 | shield_overflow_durability_cost | 3 | g7 | hold_defense | 5 |
| g7 | passive_defense | 3 | g7 | passive_break_reserve | 2 |
| g7 | eye_pellet_damage | 3 | g7 | archer_break_armor_loss | 3 |
| g7 | halo_revive_hp | 12 | g7 | terror_armor_per_piece | 3 |
| g7 | terror_horus_divisor | 4 | g7 | terror_halo_per_layer | 3 |
| g7 | terror_hp_floor | 10 | g7 | terror_attack_cost | 6 |
| g7 | terror_attack_damage | 4 | g7 | terror_move_cost | 2 |
| g7 | g0_synergy_bonus | 20 | | | |

### 4.2 `m9_system`

| 分组 | 键 | 终值 |
|---|---|---:|
| action | full_extra_per_round | 1 |
| action | grant_depth | 2 |
| action | attention_per_round | 1 |
| police | fixed_roster | 3 |
| police | unit_hp | 30 |
| police | authority_initial | 3 |
| police | baton_damage | 10 |
| police | cover_durability | 2 |
| scoring_m9 | arc_weight | 1.8 |
| scoring_m9 | arc_cap | 3 |
| scoring_m9 | kill_weight | 1.5 |
| scoring_m9 | damage_weight | 0.05 |
| scoring_m9 | placement_step | 2 |
| scoring_m9 | last_survivor_bonus | 4 |
| scoring_m9 | g0_relic_bonus | 5 |
| scoring_m9 | g0_breath_survive_bonus | 10 |

槽位得分系数（`m9_system.scoring_m9.talent_score_multiplier`，终分合计后乘算）：

| 槽位 | 系数 | 槽位 | 系数 |
|---|---:|---:|---:|
| T1 | 1.00 | G0 | 0.82 |
| T2 | 0.85 | G1 | 1.16 |
| T3 | 1.00 | G2 | 1.17 |
| T4 | 0.85 | G3 | 1.00 |
| T6 | 1.05 | G4 | 0.85 |
| T7 | 0.85 | G5 | 0.85 |
| | | G6 | 1.15 |
| | | G7 | 1.10 |

## 五、已知偏差与未决项

1. **G2 影身“创建当轮即补标准槽”未实现**：R3 在 R1 已复制 `m9_round_grants`，
   当轮补发 grant 需要修改 `engine/round_manager.py` 时序（项目禁改区）。当前口径仍为
   “影身自下一轮 R1 进入先攻”。
2. `m9_talents_extended.g4` 的余烬生命池公式仍为代码内 `consumed × 2`（完整形态），
   尚未外提到 balance.json；后续如需再压 G4 头部应先外提该键。
3. 5000 局中“达到轮次上限(AI 僵持)”为 1/5000（0.02% 局数，占平局 0.5%），
   暂不展开治理。

## 六、溯源

- 500 局迭代：`windtunnel_archive/out_tune_r5_500.txt` … `windtunnel_archive/out_tune_r15_500.txt`
- 2000 局迭代：`windtunnel_archive/out_tune_r16_2000.txt` … `windtunnel_archive/out_tune_r26_2000.txt`
- 5000 局验收：`windtunnel_archive/out_accept_5000.txt`（其余 178 个风洞/诊断/性能输出与 `.pstats` 文件已全部归档在 `windtunnel_archive/`）
- AI 策略接口事实：`docs/m9/ai/talents.md` + `docs/m9/ai/slots_*.md`
- 数值唯一信源：`data/balance.json`
