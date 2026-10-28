# M9-RL 下一步训练计划（P0–P6）

> **状态**：候选（candidate）。制定日期：2026-08-22。
> **承接**：[m9_rl_roadmap.md](m9_rl_roadmap.md)（M9-A~F 阶段已落地）与 [bc_warm_start.md](bc_warm_start.md)。
> **运行口径**：`--profile m9-rfc`；机制权威一律以 `docs/m9/current/*.md` 为准，本文只治理 RL 训练路线。
> **纪律**：单次修改 ≤5 文件；评估单一信源 `rl/winrate_eval.py`；课程晋级门禁复用 `rl/curriculum.py`；每阶段收尾跑运行时冒烟。

## 1. 现状底账（2026-08-22）

- **规则侧**：`current/` 20 份合同冻结，风洞 5000 局验收通过（见风洞校准台账）。规则不是瓶颈。
- **RL 侧**：
  - 无天赋三人局达成"**分层系统毕业**"（外部汇报值：正式胜率约 35%、二杀率约 26%、首杀后续战约 75%；**待 P0 工具复核后以落盘 JSON 为准**）；
  - 分层栈 = 9 个课程专家 checkpoint + 基于公开状态的确定性路由；路由器此前**不在仓库内**；
  - 单一扁平动作头多次合并失败（s121→s122 胜率 82%→73%→54%、s149 二杀 22%→20% 等
    技能干扰证据）——归因于顺序打补丁式训练与稀有动作梯度淹没，而非模型容量；
  - 天赋部分仅有 2 个课程场景（`talent_t1_improvise` / `talent_t3_public`）与 3 个小 run（s94–s96）；
  - 观测无自身天赋 one-hot 通道（`rl/obs_builder_m9.py` 只有 7 个天赋状态 flag）。
- **四缺口**：① 路由/专家栈未落库；② 天赋观测通道缺失；③ 4–6 人局零评估；④ 蒸馏数据格式
  （teacher logits + expert ID）未建。

## 2. 总路线

**终局 = 单一 checkpoint**：共享编码器 + LSTM 主干 + 统一动作头 + 专家 ID 辅助预测头；
多头 + 门控仅作单头两轮蒸馏后仍卡死时的 fallback。**不做**：学习型 MoE、二次蒸馏成纯单头、
跨独立训练 LSTM 的权重平均。分层栈定位 = **教师 + 回归基准 + 因果归因工具**，冻结不改。

依赖关系：P0 → P1（并入 P0 工具参数）；P2 → P3 → P4 → P6；P2 → P5（与 P4 后半并行）→ P6。

## 3. P0 专家栈落库与毕业基线（本次实施）

**目标**：把"三人分层系统毕业"变成仓库内可执行、可复现、可回归的事实。

**交付**：

| 文件 | 内容 |
|---|---|
| `rl/expert_stack.py` | 专家注册表、确定性路由（公开状态纯函数）、每专家独立 LSTM 状态管理的 predict 侧路由器 |
| `tools/m9_eval_graduate.py` | 毕业评估：`--num-opponents 2` 出 3p 基线，`3/4/5` 出多人零样本；JSON 落盘 |
| `tests/test_rl_expert_stack.py` | 表驱动路由单测 + 栈状态管理单测 |

**专家注册表**（权威清单以 `rl/expert_stack.py::EXPERT_CHECKPOINTS` 为准；路径相对仓库根）：

| key | checkpoint | 职责 | 路由条件（v1，阈值待校准） |
|---|---|---|---|
| `opening` | `checkpoints/codex_m9_course_full_basicai_economy_ppo_s142/final.zip` | 首杀前通用开局 | `kill_count==0` 时的默认 |
| `postkill` | `checkpoints/codex_m9_course_real_postkill_dagger_s148/final.zip` | 首杀后经济续战 | `kill_count>=1` 时的默认 |
| `pp_finish` | `checkpoints/codex_m9_course_pp_damage_s121/final.zip` | PP 斩杀 | PP≥1 且存在存活对手 HP−最优武器伤害 ∈ (0,1]（对应 5HP 靶 vs 小刀 4 伤 +1PP 加伤的课程事实） |
| `survival_heal` | `checkpoints/codex_m9_course_survival_dual_s124/final.zip` | 治疗/购甲生存 | HP≤8 且位于 医院/商店（v1.1：仅可执行治疗/购甲的地点接管）且存活对手≥1 |
| `armor_repair` | `checkpoints/codex_m9_course_survival_repair_bc_s131/final.zip` | 修甲 | 自身护甲耐久≥1 且 <50% |
| `sharpen` | `checkpoints/codex_m9_course_equipment_sharpen_bc_s135/final.zip` | 磨刀 | 有可磨武器（伤害 < 磨后阈值）且未持有磨后武器 |
| `detection` | `checkpoints/codex_m9_course_visibility_thermal_bc_s136/final.zip` | 隐身对策/探测 | 存在隐身存活对手且自身无探测（v1.1：每局至多 4 拍，见 EXPERT_DECISION_BUDGETS） |
| `attribute` | `checkpoints/codex_m9_course_attribute_detection_bc_s144/final.zip` | 属性克制选择 | 存在护甲存活对手且自身探测属性不全 |
| `fire_antiarmor` | `checkpoints/codex_m9_course_fire_antiarmor_bc_s152/final.zip` | 火矢反甲 | 持弓且存在持陶瓷护甲的存活对手 |
| `police` | `checkpoints/codex_m9_course_police_election50k_s78/final.zip` | 警察竞选/指挥 | choose 模式且 situation 命中警察族关键词 |

**路由优先级**（先命中先赢）：
`police > pp_finish > fire_antiarmor > survival_heal > armor_repair > sharpen > detection > attribute > 默认（opening/postkill 按 kill_count 分流）`。
全部条件只读公开状态（`GameFacts` 快照，getattr 兜底），每步决策记录 `(expert, reason)`，
保证 100% 可归因。阈值常量是 v1 初始值（依据对应课程场景的布置事实），毕业评估后校准并回写本表。

**指标定义**（评估口径单一信源 `rl/winrate_eval.py`）：

- 正式胜率 = `official_wins / official_settled_episodes`；
- 二杀率 = `rl_kill_count >= 2` 局数 / 总局数；
- 续战率 = `rl_kill_count >= 2` 局数 / `rl_kill_count >= 1` 局数。

**验收**：`python tools/m9_eval_graduate.py --seeds 5 --episodes 40` 一条命令产出上述三指标
（无天赋三人局、全 BasicAI 对手、5 种子），JSON 落盘为回归锚点；路由单测全绿。

### 3.1 v1.1 校准记录（2026-08-22）

v1 基线（5 种子 × 40 局，`graduation_baseline_3p.json` / `_4p.json`）实测与诊断：

- 3p：正式胜率 10.5%、二杀 0%、续战 0%、切换 2.4 次/局；**200 局全部
  `official_survival_end` 收场（平均约 26 轮）**——栈能存活但计分落败；
- 专家用量严重偏斜：detection 占 3p 27% / 4p 40% 决策，survival_heal 占 15%；
  postkill 仅 144 拍，pp/fire/sharpen/attribute/police 从未触发；
- 病理定性：**目标型条件的锁死**（"存在隐身对手且无探测"是专家自身未必能清除的
  目标条件 → 永久接管）；切换率低（2.4/局）说明主要病灶是误分配而非边界振荡。

v1.1 修正（`rl/expert_stack.py`，单测同步）：

1. `survival_heal` 加地点门：仅在 医院/商店（可执行治疗/购甲）接管，低血在途时
   由默认专家负责移动；
2. `detection` 加每局决策预算（`EXPERT_DECISION_BUDGETS = {"detection": 4}`），
   预算耗尽回落默认分流；`route()` 保持纯函数，新增 `route_with_budgets()`。

10 局小样本初验：detection 1611→4 拍、survival_heal 908→16 拍、opening 回到 93%。

### 3.2 v1.1 全量基线记录与停止校准决定（2026-08-22）

v1.1 全量重跑（同口径 5×40 / 3×20）：

- 3p：正式胜率 **3%**（v1 为 10.5%，z≈3.0，差异显著）、二杀 0%、切换 1.6/局；
  opening 占 94% 决策；200 局仍全部 `official_survival_end`，平均约 30 轮（v1 约 26），
  **mean_rank 2.4–2.6 / 3**；
- 4p：正式胜率 5%、mean_rank 3.05–3.35 / 4，其余同构。

结构性诊断（两版配置共同成立）：

1. **这个栈输在计分而非生存**：每局都活到结算，然后平均名次垫底——
   BasicAI 按官方计分体系（演出/剧情分/PP）拿分，栈的策略最大化的是
   RL 训练奖励，两者不对齐；
2. **击杀转化链从未闭合**：两版二杀均为 0，postkill 专家几乎不被触发，
   "杀穿取胜"这条路径实际不存在；
3. v1（防御专家拖缓）反而比 v1.1（opening 主导）得分更高——被动拖延在
   计分结算里比主动开局更值钱，两者都远低于外部汇报的 35%。

执行既定判定规则（§3.1 前承诺：重跑后仍明显低于 35% 即停止路由校准）：
**路由校准到此为止。** 仓库内可复现的最佳栈配置 = v1 路由 @ 10.5%
（阈值可经 `rl/expert_stack.py` 常量还原）；v1.1 = 3%（归因更干净）。
外部 35% 维持"未复现"记录。P4 统一学生的对照锚点取 **10.5%（v1 配置）**。
下一步进入 P2（天赋观测/动作通道）。

统计口径修正（同日）：发现并修复 `ExpertStackModel` 的局边界检测缺陷
（`winrate_eval` 每局首拍 `episode_start=[False]` 且局末 `[True]` 被外层
循环丢弃 → 此前每个 seed 的局被合并成一个汇总），改为 `state is None`
哨兵双保险；胜率/轮数/名次不受影响（出自 evaluate_policy 本体），仅
二杀/续战率的分母粒度此前偏粗（3p 的 0% 仍为真 0；4p 旧值 5% 粒度为
1/20，复测后再引用）。回归测试 `test_episode_boundary_via_state_sentinel`。

## 4. P1 多人零样本评估

工具能力已并入 P0（`--num-opponents 3/4/5`）。产出失败归因四分类：新技能缺失 / 路由冲突
（专家切换 LSTM 断裂）/ 资源压力 / 目标选择混乱。**量化路由切换率**（每局专家切换次数），
作为后续单模型蒸馏可白捡收益的预估基线。

## 5. P2 天赋观测与动作通道补全

- `rl/obs_builder_m9.py`：M9 block 尾部 **append-only** 追加自身天赋 one-hot（14）与对手可见
  天赋槽（对接 choose_visibility 语义）；旧 checkpoint 零填充/截断兼容，专家栈冻结不受影响；
- 动作空间防失明审计扩展到 14 天赋（`SPECIAL_OPS` 槽位或 `KNOWN_UNMAPPED_M9_SPECIALS` 登记，
  沿用 `tests/test_rl_m9_special_visibility.py` 纪律）；
- `rl/reward_m9.py` 预留天赋事件钩子（先不加 shaping，P5 按课加）。

### 5.1 P2 完成记录（2026-08-22）

- **观测**：天赋块 90 维 append-only——自身（1 有天赋标志 + 14 one-hot）+ 5 个稳定
  对手槽 ×（1 在席标志 + 14 one-hot）；`OBS_DIM_M9_TOTAL` 828→**918**；v5 核心宽度
  冻结为 `OBS_DIM_M9_V5_CORE=828`，choose 契约区仍固定在 [569..828)。对手天赋当前
  是公开信息（引擎无天赋隐藏机制）；若未来引入 concealment，one-hot 改按可见性门控，
  布局不变（已在 `_fill_talent_block` docstring 登记）。
- **兼容**：`rl/obs_compat.LegacyObsDimWrapper`（尾部截断，限 n_stack=1）；
  `rl/schema.py` 登记 v6=obs918-act217、v5=obs828-act217 保留可识；毕业评估工具已
  接线 wrapper，10 个真实 828 专家 checkpoint 冒烟通过（schema 校验 + 路由正常）。
- **防失明扩展**：`tests/test_rl_m9_talent_visibility.py` 对 14 槽逐一跑真实 env 探针
  （obs 维度/有限性、天赋块 one-hot 端到端一致、非 forfeit 合法动作存在、逐槽无异常）；
  special/choose 静态审计维持全绿。
- **奖励钩子**：`rl/reward_m9.talent_event_reward`（P5 入口，空实现并已接入
  `m9_supplemental_event_reward` 调用路径；纪律：P5 启用时必须登记
  `M9_EVENT_REWARD` 并被 reward ledger 测试覆盖，禁止天赋具名分支）。
- **测试与冒烟**：新增 `test_rl_obs_talent_block`（9 例：布局/填充/截断 wrapper）、
  `test_rl_m9_talent_visibility`（14 槽探针）；更新 `test_rl_m9_choose_visibility`
  （choose 区基址断言改用显式常量、维度钉 918）。相关全量测试绿：action_coverage/
  special_visibility/choose_visibility/reward_ledger/reward_drift/expert_stack/
  contract_metrics/lifecycle_contract。运行时冒烟 `m9_rfc_smoke` 8/8。
- **已知边界（登记待裁决）**：env `_assign_rl_talent` 在指定编号已被对手先选时
  **静默跳过**（天赋池互斥），逐槽探针以多种子重试绕开；P3 蒸馏数据采集若需
  强制指定天赋，应先修该语义（失败应显式报错或提供强制位）。

## 6. P3 统一蒸馏数据收集

- 序列格式扩展为 `(obs_seq, mask_seq, action, teacher_logits, expert_id, reward)`；
- 采样矩阵：自然 3/4/5/6 人局 × 全部原子课程场景，按技能均衡（稀有技能上采样，沿用 s138
  实测验证的 sample weight 机制）；
- 路由器每步 `(GameFacts, expert, reason)` 落日志——门控监督标签的数据源。

### 6.1 P3 完成记录（2026-08-22）

- **裁决落实（用户拍板）**：env 新增 `rl_talent_reserve` 预留池语义——开启时指定
  `rl_talent=N` 从 AI 对手可选池排除，RL 必然拿到该编号；与 `rl_talent∈{None,0}`
  组合显式拒绝。此前"被抢即静默跳过"的坑（§5.1 登记）由此收口（默认关闭，
  存量训练/评估行为不变）。
- **采集工具 `tools/m9_distill_collect.py`**：
  - 执行者 = 冻结专家栈（`RecordingTruncateWrapper`：教师消费 828 截断观测，
    数据集记录 **918 原生观测** + 每局尾拍 next-state）；
  - 每步记录：`observations(918)/action_masks(217)/actions/expert_ids/
    reasons/teacher_probs(掩码后分布，双 forward 与部署零漂移)/rewards/
    episode_starts`；npz 每 cell 一分片 + `manifest.json`；
  - cell 矩阵：`natural_{3..6}p` / `talent_pilot_3p`（随机天赋预热）/ `course:<场景>`
    （合同与轮上限映射自 `tools/m9_recurrent_bc` 同源表）/ `course_t1|t3`
    （预留池强制天赋）；
  - `--verify` 复载校验不变量：动作合法于自身掩码、probs 有限且行和≈1、
    被选动作概率>0、expert_id 在册、边界标记与局数一致、obs=转换+局数。
- **专家栈捕获通道**：`ExpertStackModel(logits_fn=…, capture_enabled)`——
  `predict` 附带 `(expert, reason, probs, boundary)`；生产实现
  `expert_masked_probs`（tensor 流程与 rung0 承诺先验同源）。动作与路由语义
  与部署完全一致（捕获不改行为，有单测断言）。
- **小样验证集**：`bc_data/m9_distill/`（5 cell × 11 局 = 255 步，含天赋课
  course_t3 的预留池强制）——`--verify` 全过。
- **测试**：新增 `tests/test_rl_m9_distill_collect.py`（8 例：预留池语义/
  捕获通道/分片不变量/ID 序同步）；expert_stack 回归 23/23。
- **全量采集命令（用户自行安排，预计数小时 CPU）**：
  `python tools/m9_distill_collect.py --preset full --out bc_data/m9_distill`
  （自然 3–6 人各 150 局 + 随机天赋 50 局 + 27 场景 × 40 局；完成后
  `--verify` 必须全过再进 P4。）

### 6.2 全量数据集记录与 P4 输入约定（2026-08-22）

全量采集完成：32 cell、**40,234 步**、`--verify` 全过（目录中 sample 时代的
`course_t3.npz` 旧分片已清除；增量重采用 `--cells`，manifest 自动合并）。

**门控标签分布**（expert_id 全集）：opening 59.7% / postkill 19.6% /
survival_heal 10.9% / detection 3.4% / fire_antiarmor 2.9% / attribute 2.1% /
armor_repair 1.1% / pp_finish 0.3%——P4 训练必须按 expert_id 逆频率加权
（"按技能均衡"的落地方式），否则稀有技能被淹没（冷启动教训同源）。

**P4 v1 排除清单**（教师一步死 cell，logit 蒸馏会把死法教给学生）：
- `course:pp_damage_choice`（40 局 × 1 拍，均奖励 −114.6）
- `course:survival_heal_counterattack`（40 × 1 拍，−114.4）
- `course:survival_armor_counterattack`（40 × 1 拍，−114.4）

诊断：三者的被路由专家（pp_finish / survival_heal）首拍确定性动作错误且
置信度低（probs≈0.22–0.27，首拍仅 10 个合法动作），40 局同一死法。
后续可用 m9_recurrent_bc 的 DAgger/oracle 机制补采，或留给 P4 的 PPO
课程直接学——v1 先排除。

**已知标签缺口**（P4 由 PPO 课程覆盖，不进蒸馏）：
1. `police` 专家 0 步：警察课全程无 choose 决策（选举/指挥是 get_command
   侧 special），公开状态路由无触发点。已加 `captain` 关键词加固并重采
   police 两 cell（798/819 步），仍 0——确认为命令模态覆盖缺口而非关键词
   漏配；
2. `sharpen` 专家 0 步：磨刀判据与动作掩码门槛同源（base_damage<2），
   课程局内小刀按该判据已"磨过"，磨刀动作由默认专家自行完成。

## 7. P4 统一学生训练

- 第一步：**单头 + 专家 ID 辅助头**（共享主干；辅助头逼表征区分技能，动作出口仍单头）；
- 第二步（仅当第一步预定义卡死：某原子课两轮蒸馏后仍低于教师 10pp 以上）：多头 + 门控
  （先监督模仿路由日志，后小学习率 PPO 微调；门控必须吃递归状态）；
- 训练：teacher-logit KL 蒸馏 → PPO 微调，蒸馏数据按比例混入当 anchor 防遗忘；
- **三层验收门禁**（复用 `rl/curriculum.py` gate 结构）：① 全部原子课达标；② 三人毕业指标
  ≥ 基线 −5pp 容差；③ 4/5/6 人自然局不低于专家栈零样本。任何技能倒退只回退学生。

### 7.1 P4 v1 记录（2026-08-22，进行中）

**架构与训练器**：
- `rl/distill_student.py`：`DistillStudentPolicy`（MaskableRecurrentActorCriticPolicy +
  policy_net 潜层上的 10 类 expert_head；`policy.optimizer` 不含 aux 头 → PPO 阶段自动冻结）；
  数据加载按 episode_starts 切回完整局（排除清单生效）；损失 = 加权[NLL + KL + 0.5·aux CE]，
  expert_id 逆频率（sqrt 温度）加权；早停判据 = val NLL + 0.5·val aux CE（与训练损失同构）。
- `tools/m9_distill_train.py`：distill / ppo 两阶段；PPO 阶段带 BC-anchor 回调
  （每 5 个 rollout 用蒸馏数据补 4 步监督梯度，独立 Adam lr=1e-5）+ 周期 winrate_eval。
- 评估：`winrate_eval` 报告新增二杀/续战指标（info 的 rl_kill_count，单一信源）；
  `tools/m9_eval_graduate.py --single` 评学生 checkpoint。

**蒸馏结果**（1610 局/37989 训练步，12 epoch × 97s）：
val NLL **0.583**、val KL 0.26、**aux 准确率 79.5%**（观测→专家身份可预测，表征信号成立）。

**纯蒸馏学生（无 PPO）三人局毕业指标**（5 种子 × 40 局 = 200 局）：

| 指标 | 纯蒸馏学生 | 专家栈最佳（v1 路由） | 外部汇报（未复现） |
|---|---|---|---|
| 正式胜率 | **24.5%**（逐种子 20–30%） | 10.5% | ~35% |
| 二杀率 | 4.5% | 0% | ~26% |
| 续战率 | 8.3% | 0% | ~75% |

**单一 checkpoint 在未做任何 PPO 前已超过教师栈本体**——门禁②（≥10.5%−5pp=5.5%）
提前通过；门禁③未过（4p 零样本 0% vs 栈 5%，学生如实继承了教师的多人天花板）。
门禁①（原子课达标）待 PPO 后统一评估。

**PPO 微调**：500k 步（自然三人局、4×subproc、蒸馏锚点）已完成（1513s）；
8×spawn 曾触发系统 fork/内存耗尽（机器上限），降为 4 worker 后稳定
（主 1.1GB + 4×586MB）。**运维教训**：subproc worker 工厂内部必须
`set_profile('m9-rfc')`（spawn 不继承 profile，obs 会回落 legacy 539）。

### 7.2 P4 v1 门禁评估（2026-08-23）

PPO 后学生（`checkpoints/p4_student_v1/finetuned.zip`）三层门禁结果：

**门禁②（三人毕业 ≥ 5.5%）——通过**：正式胜率 **37%**（5 种子 × 40 局，
逐种子 35–40%）、二杀 12%、续战 18%、结算率 100%。**超过外部汇报的 35%**
（该值此前在教师栈上从未复现）。训练曲线：25k 步 33% → 100k 后 50–67%
区间 → 终评 45%（40 局）。

**门禁③（多人零样本不低于专家栈）——通过**：

| 局型 | 学生零样本 | 专家栈零样本 | 均匀随机参照 |
|---|---|---|---|
| 4p | **28%** | 5% | 25% |
| 5p | **15%** | 3.3% | 20% |
| 6p | **13%** | 0% | 16.7% |

纯蒸馏学生 4p 曾为 0%——PPO 只在 3p 训练即把 4p 拉到 28%，蒸馏埋下的
多人表征 + RL 奖励激活的假说得到支持。

**门禁①（原子课达标）——部分通过**（2 种子 × 8 局抽检；PPO 前后逐项持平，
锚点零倒退）：

| 课程 | 蒸馏→微调 | 判定 |
|---|---|---|
| economy_pursuit_supply | 100% / 100% 二杀 | ✅ |
| visibility_thermal_chain | 69% | ✅ |
| survival_armor_repair | 44% | ⚠️ 部分 |
| pp_damage_finish | 0% | ❌（pp 标签仅 0.3%） |
| police_command | 6% | ❌（police 标签 0，命令模态缺口） |

**P4 v1 结论**：单头 + aux 头路线成立——单一 checkpoint 在自然局全面
超越教师栈（37%/28%/15%/13% vs 栈 10.5%/5%/3.3%/0%），无需升级多头+门控
（预定义卡死条件未触发）。遗留两项原子课失败，根因均为蒸馏标签稀缺：
1. PP：补采（DAgger 定向）或对 pp_damage 课做 PPO 定向微调；
2. police：命令模态无法经公开状态路由采标——按计划留给 PPO 课程。

**下一步**：P4.1 定向补强 pp/police 两课（可选，小时级）→ P5 十四天赋课程
（`talent_pilot` 切片已验证管线）。回退锚点：`distilled.zip`（3p 24.5%）。

### 7.3 P4.1 定向补强记录（2026-08-23）

**尝试 a（混合课程 PPO，失败）**：pp 双课 + police + 自然局四 worker 混合 80k 步。
结果：pp 仍 0%、police 6→12%，且 **visibility 69%→0% 实测倒退**（窄课梯度压过
锚点）。结论：标签稀缺型缺陷不能靠 PPO 定向修，回到数据侧。

**根因链（三层，逐层剥出）**：
1. **路由只触发一拍**：PP 花掉后条件消失，s121 每局只拿 1 拍标签（40/881）；
   → 路由 v1.2 粘性专家（`EXPERT_STICKY`）；
2. **粘性锁死到局末**：s121 只教过单靶语境，首杀后标签全是 forfeit——
   学生解码实测 `PP加伤 → attack AI_0（首杀）→ 连串 forfeit`；
   → 粘性在**击杀数变化时解除**（`sticky_marks`），首杀后交回 postkill 追二杀
   （重采后标签结构：pp_finish 80 步斩杀链 + postkill 995 步追击）；
3. **课程合同漏映射**：采集映射表把 pp/police/talent 三组课默认到 combat_core，
   而教师是在各自钻头合同（pp_course/police_course/talent_course）下受训的——
   错合同下教师行为退化（pp 课来回踱步不攻击）。修正后 `pp_damage_choice`
   从 40 步 1 拍死变成 751 步完整局，移出排除清单。
4. 附带：police 课强制 `--force-expert police`（命令模态路由不可达，绕过）。

**钻头课程的度量伪影（重要方法论结论）**：pp_course 钻头掩码下合法动作只有
`forfeit / special PP加伤`（攻击仅在加伤挂载后解锁，move/find 全隐藏）——带
第二名活对手的 40 轮局**结构性不可赢**，"官方胜率 0%"不代表技能缺失。行为
探针实测：**v13/v14 学生每局完美执行 `PP加伤 → 击杀`（首杀率 100%）**；
police 课学生正确执行 `指挥unit2攻击/指挥unit1移动` 序列。钻头合同课程
必须用行为指标（首杀率/专项动作执行率）而非官方胜率评估——**P5 天赋课
验收指标设计必须吸收此教训**。

**v14 终态**（`checkpoints/p4_student_v14/distilled.zip`，从 v1 重蒸 + 修正数据）：
- 保护组零倒退：economy 100%、visibility 69%、survival_repair 44%；
- 正式 3p 门禁（5×40）：**36% / 二杀 18% / 续战 27%**（击杀转化较 v1 提升）；
  但 4p 快检跌回 0%——监督重蒸磨掉了 PPO 驱动的多人迁移。

**v15 终态（P4.1 最终交付）**：v14 + 120k 短 PPO（锚点含修正标签）→
`checkpoints/p4_student_v15/finetuned.zip`：

| 指标 | v1 | v14 | **v15** | 外部汇报 |
|---|---|---|---|---|
| 3p 正式胜率 | 37% | 36% | **60%**（3 种子均 0.60） | ~35% |
| 3p 二杀 / 续战 | 12% / 18% | 18% / 27% | **27% / 37%** | ~26% / ~75% |
| 4p 零样本 | 28% | 0% | 17%（30 局快检） | — |
| pp 钻头首杀率 | 0% | 100% | **100%（16/16）** | — |
| economy / visibility | 100% / 69% | 100% / 69% | 100% / 69% | — |

pp 钻头技能扛过了 PPO pass（锚点数据含修正标签）；4p 未完全恢复（120k vs
v1 的 500k）——后续如需 4p+，延长 PPO 或加入多人 worker 即可，非结构性障碍。

P4.1 交付物：路由 v1.2（粘性 + 击杀解除 + forced_expert）、采集合同勘误
（pp/police/talent）、`distill --init` 续蒸口径、25+6 测试全绿。
**P4 阶段（含 4.1）就此收口；进入 P5 十四天赋课程。**

## 8. P5 十四天赋课程

按机制复杂度四档推进：被动类 → SP 演出类（T1/T3 场景已有）→ 复杂机制类（G3/G5/G4/G1/G2）→
交互对抗类（T6 警察/G7 压制/G6 借用/G0 遗物）。每天赋三步协议：**oracle 可行性探针**
（复用 `tools/rung0_chain_probe.py` 模式，核心动作链在 RL 编码下走得通才轮到学习问题）→
定向课程（可归因靶场）→ 蒸馏数据并入统一学生。出口标准：14/14 天赋各有原子课门禁 +
防失明登记全绿。

### 8.1 P5 开工：14 槽基线与激活分诊（2026-08-23）

**方法**：先度量后建课（P4.1 教训）。`m9_eval_graduate --rl-talent N`（预留池强制）
测自然局基线；行为探针按槽位收集引擎天赋事件（奖励账本 event_types）判定
"是否激活"，区分"没用天赋"与"用了但差"。

**自然局基线**（v15，2 种子 × 10 局/槽；无天赋参照 60%）：全部天赋拖累胜率——
T6/G5/G7 35% 为最高，G2 **0/20** 最低，T1/T4/G3 10%；二杀几乎归零。
（20 局样本置信区间宽，排序仅供分档。）

**激活分诊（核心结论）**：

| 组 | 槽位 | 证据（2 局探针内触发的事件） | 处置 |
|---|---|---|---|
| A：已激活（10） | T1/T2/T3/T4/G0/T7/G2/G3/G4/G5 | oneslash、star_attack、hexagram、g0 无人机全套、resurrection、**G2 影身+终曲完整链**、g3 expand、**G4 救世主全周期（含 judgment_completed）**、**G5 锚定脚本+水晶花+实现** | 用好问题——PPO 微调（开天赋自然局）+ 现有蒸馏锚即可，无需新课 |
| B：零激活（4） | **T6 / G1 / G6 / G7** | 探针内无任何本天赋事件（T6 无热线/队长；G1 无燃烧；G6 无借用；G7 无压制/补给——G7 正是历史 special 失明重灾区） | **首批建课对象** |

根因备注：`talent_pilot` 蒸馏切片仅 ~50 局随机天赋（平均每槽 ~3.5 局），
激活覆盖靠的是 T1/T3 课程数据 + 采样运气；B 组是教师栈在天赋局同样不使用
这些机制的直接后果——蒸馏只能教教师会的东西。

**首批课程设计**（B 组，talent_drill 模板 = 1 HP 靶 + SP 档位 + 命令面仅
forfeit/wake，天赋发动走 choose 通道不受合同裁剪；非伤害型以行为事件为
成功指标——§7.3 度量伪影教训）：
1. `talent_t6_hotline`：T6 热线举报（有作案对手 + SP1），成功 = hotline 事件；
2. `talent_g1_burn`：G1 燃烧进入（SP1 即演换装），成功 = 燃烧事件；
3. `talent_g6_borrow`：G6 借用核心（在场可 copy 目标 + SP 档位），成功 = g6_borrow；
4. `talent_g7_suppress`：G7 战术压制形态/补给（SPECIAL_OPS hoshino 族已在
   动作空间，防失明测试在册），成功 = g7/hoshino 事件。

每课三步协议照旧：oracle 可行性 → 课（强制预留池）→ 并入蒸馏（`--force-expert`
仅当教师可胜任；B 组大概率需要 oracle/B 侧教师，参照 m9_recurrent_bc 模式）。
A 组随后用"开天赋 PPO 微调"收量（预计与 P4 的 3p 提升同机制）。

**P5 当前状态**：分诊完成；首批 4 课实现为下一步。

### 8.2 P5 首批课程实现与可行性验证（2026-08-23）

**四门 B 组课落地**（`rl/env.py` 场景 + `rl/action_space.py` 合同）：

| 课 | 场景 | 通道 | 合同 | 可行性证据（贪心 oracle 探针，tests/test_rl_m9_talent_drills.py） |
|---|---|---|---|---|
| T6 热线 | `talent_t6_hotline` | 根行动 special | `hotline_drill`（新；forfeit/wake + 热线模板槽） | hotline 事件触发 ✓ |
| G1 换装 | `talent_g1_burn` | T0 choose（SP1） | `talent_drill` | firefly_supernova_granted（换装专属副作用）✓ |
| G6 即演 | `talent_g6_borrow` | T0 choose（SP1） | `talent_drill` | SP 1→0（dispatch_improvise）✓ |
| G7 即演 | `talent_g7_suppress` | T0 choose（SP1） | `talent_drill` | SP 1→0 + hoshino_form 形态选择链 ✓ |

工程要点：
1. **行为指标按机制定制**（§7.3 教训落地）：G1 换装/G6/G7 即演在引擎层
   无专属事件（仅公演路径有）——G1 用换装副作用事件、G6/G7 用 SP 消耗
   作即演发生的证据；G1 终态 form 不可靠（贪心可能再选免费卸甲切回）；
2. T6 布置绕开天赋对象（`_apply_course_scenario` 运行时天赋尚未分配，
   延迟到游戏线程）：直接 `state.log_event("special_clue", …)`——
   `special_clues_for` 只按 type+suspect 过滤 event_log；
3. T6 线索资格 + 警察 roster（不自动执法）；G1/G6/G7 复用 T1/T3 靶场
   模板（1 HP 靶 + SP 档位 + talent_drill）。

回归：action_coverage 9/9、talent_visibility、special_visibility 8/8、
choose_visibility 15/15、drills 4/4。

**下一步**（P5 第二批）：四课数据采集并入蒸馏——教师候选先验
BasicAI（`t0_policy.m9_decide_choose` 有 G1/G6/G7 分派），不会则按
§8.1 预案上 choose-oracle 教师（探针贪心逻辑可复用）；随后 A 组
（已激活 10 槽）开天赋 PPO 微调收量。

### 8.3 P5 第二批准备（2026-08-23，待用户执行）

**教师裁决**：BasicAI `t0_policy` 仅覆盖 G6/T6 分派（G1/G7 无）——不能当
完整教师，按 §8.1 预案上 **choose-oracle**：采集器 `_oracle_choose_pick`
（talent_t0 choose 选发动项 + T6 命令通道点亮热线槽；举报成功后引擎关闭
新通缉，后续拍自动回落专家标签）覆盖动作与标签；B 组 cell 的
teacher_probs 用被选动作 **one-hot**（KL(one_hot‖student) 退化为 NLL，
蒸馏损失结构不变）。冒烟实证：G1 22/25、G6 18/31、G7 18/22 步为 oracle
标签；T6 修复命令通道后 12/12 全 one-hot、mean_reward −32→+2.8。

**新 preset**：`talent_batch` = B 组四课 × 60 局 + talent_pilot × 80 局。

**学生激活探针**：`tools/m9_talent_activation_probe.py`（行为指标，
阈值 75%）。**v15 基线**（4 局小样本）：G1 100% / G6 75% / G7 100% /
T6 50%——钻头合同下"发动"近乎唯一有意义选项，学生已从 talent_pilot
学到倾向；v16 的验收意义在 T6（真实激活）与把其余三项钉到 100%。

**用户执行序列**（结果回传后回写本节）：
采集 `talent_batch` → `distill --init v15` 产出 v16 → 激活探针（B 组）→
3p 快检 + 保护组抽检（economy/visibility）防倒退。

### 8.4 P5 第二批收口（2026-08-23）

**用户执行 + 本地修复后的最终链路**：采集 45,086 步（B 组 oracle 标签 +
talent_pilot×80）→ v16 续蒸（val NLL 0.503）→ 激活验收 → 120k 短 PPO
恢复 → `checkpoints/p5_student_v16f/finetuned.zip`（P5 当前交付）。

**过程中钉死的两个度量坑**（比结果本身更有长期价值）：
1. **探针确定性缺陷**：BasicAI 对手经全局 `random` 取随机（不经
   env.np_random）——不按局播种则四课共用进程时后课被前课的随机流污染
   （G7 独立跑 100%、顺序跑 50% 的"倒退"假象）。修复：每局
   `random.seed(seed)`；教训适用于一切多课共进程的评估。
2. **SP 不是单调递减**：T2 登记关注（mark_attention）会推进 SP +1、
   grant 签发失败会退款——"终局 SP==0"不是即演证据，**局内 SP 最小值
   到过 0** 才是。修复后 G6/G7 立即从 62-71% 修正为 83-100%（学生
   每局都在正确发动，决策解码证实）。

**v16f 终态验收**（全部通过）：

| 指标 | v15 | v16（蒸馏后） | **v16f（+120k PPO）** |
|---|---|---|---|
| B 组激活 T6/G1/G7 | 50/100/100* | 100/100/100 | **100/100/100**（24 局） |
| B 组激活 G6 | 75* | 88 | **83** |
| 3p 正式胜率 | 60%（快检） | 33%（蒸馏回落） | **52%**（快检）/ 62.5%（终评 40 局） |
| 二杀 / 续战 | 27/37 | 18/27 | **25/35** |
| economy / visibility / pp 首杀 | 100/69/100% | 100/69/— | **100/69/16/16** |

*v15 基线为 4 局小样本且度量含 SP 伪影，仅供方向参考。

PPO 未磨掉 B 组激活（蒸馏锚点含 oracle 标签）。G6 的 83% 为"多数局发动"
（模板池轮次供给下的机会窗口），达标阈值。

**P5 剩余（第三批）**：A 组收量——开天赋自然局 PPO 微调（现在全天赋
自然局 10-35% vs 无天赋 52%，主差距在"会发动"之外的对局质量）；
A 组 10 槽的门禁化（统一激活探针口径）。B 组 G6/G7 的"用好"升级
（公演路径/战术宏深度）列入 P6 联赛阶段。

### 8.5 P5 第三批与阶段收口（2026-08-23）

**A 组门禁化**：探针扩展 `--group a|b|all`——A 组 10 槽用自然局专属事件
判定（预留池强制指定；阈值 50%，自然局单局激活机会 <1）。评估工具加
`--random-talents`（全桌开天赋 + RL 随机分配口径）。

**开天赋 PPO**（`ppo --force-random-talent`，150k，v18）：无天赋 3p 52→63%，
随机天赋 3p 20→35%；**但磨掉 A 组两槽**（G3 42%、G5 17%——talent_pilot
锚每槽平均 ~6 局锚不住深链天赋）。补采 talent_pilot×120 + 轻续蒸（v19）：
G3 恢复 75%、G5 仍 33%（G5 往世涟漪是多阶段深链：轮回→K 槽脚本→水晶花，
轻锚拉不回），且随机天赋 3p 掉回 17%（蒸馏磨掉开天赋增益）。

**三方对比与 P5 交付裁决**：

| 指标 | **v16f（P5 交付）** | v18 | v19 |
|---|---|---|---|
| 14 槽激活门禁 | **14/14 全绿** | 12/14 | 13/14 |
| 无天赋 3p | 52%/62.5% | **63%** | 57% |
| 随机天赋 3p | 20% | **35%** | 17% |

裁决依据：P5 出口标准 = 14/14 激活（RL 失明防治是本计划立身之本）+
防失明登记——**v16f 唯一全绿**，定为 P5 交付。v18/v19 登记为 P6 候选：
它们证明开天赋 PPO 有效（无天赋 +11pp、随机 +15pp）但**锚强度是约束**。

**P5 阶段收口**：出口标准达成——14/14 槽位激活门禁（B 组 4 课行为指标
+ A 组 10 槽自然局事件）全绿；防失明登记（special/choose/talent 三测试）
全绿。数据集 47,100 步；探针/评估工具链齐备（确定性播种 + sp_min 口径）。

**移交 P6 的已知事项**：
1. 锚强度：talent_pilot 需按槽均衡加厚（尤其 G5 深链），开天赋 PPO 的
   收量增益才能不付槽位退化代价；
2. G6 83%（模板池轮次供给的机会窗口）、G5 v18 系 33% 的"用好"升级；
3. B 组公演路径/战术宏深度。

## 9. P6 自博弈联赛与毕业重定义

统一学生进 `rl/self_play.py` 自博弈；专家栈降格为 league 成员与旧课对手。**毕业标准升级**：
单一 checkpoint（a）三人无天赋 ≥ 基线 −5pp；（b）随机天赋三人/四人局达标；（c）全部原子课
门禁绿。

### 9.1 P6 路线修订（用户裁决 2026-08-23）

对"直接上自对弈"的否决与修订（决策依据：naive self-play 的自吸引坍塌风险 +
当前学生天赋局 20% 的系统性盲区不宜当自我基线）：

1. **G5 不再锚定补强，交给大预算 RL 探索**：G5 锚数据源头是教师栈/BasicAI
   演示——BasicAI 自身的 G5 机制可能就有问题，加厚锚只会放大平庸；
   `M9_EVENT_REWARD` 中 G5 事件分值最高（水晶花 15/锚定实现 10），
   随机天赋局里 G5 有真实探索信号；
2. **P6a-1（当前阶段）：纯 BasicAI 对手训到能玩明白 6 人局**——混合
   worker（3p 开天赋保量 + 6p 无天赋学走位 + 6p 开天赋学机制），
   锚点继续防遗忘；退出标准 = 6p 门禁达标且 3p 双口径不倒退；
3. **P6a-2：混编自对弈**（league：BasicAI + 专家栈 + 历史快照 + self
   配比）——前提是 P6a-1 学生强到 self 局有质量；
4. **P6b：self 比例上调 + exploiters**，每轮过三层门禁 + 14 槽激活
   探针，退化即回退（快照机制天然支持）。

### 9.2 P6a-1 收口（2026-08-23）：v20 交付

**起点基线**（v16f 多人）：4p **8%（低于随机 25%）**、6p 12%——P4.1 以来
训练全在 3p，多人随蒸馏代数持续退化。混合 worker（1×3p 天赋 + 2×6p
无天赋 + 1×6p 天赋）500k 步训练 + 47.1k 锚 → `checkpoints/p6_student_v20/
finetuned.zip`（2448s）。

**v20 验收**：

| 指标 | v16f 基线 | **v20** | 随机参照 |
|---|---|---|---|
| 6p 正式胜率 | 12% | **71%**（45 局） | 16.7% |
| 4p / 5p | 8% / 未测 | **67% / 33%** | 25% / 20% |
| 3p 无天赋 | 52% | **55%**（不倒退） | 33% |
| 3p 随机天赋 | 20% | **60%** | — |
| 14 槽激活 | 14/14 | **13/14**（T2 83→33% 退化） | — |
| G5 深链探索 | 67%（v18 系 33%） | **58%** ✓ | — |

**两项裁决均被数据验证**：
1. G5 交给大预算 RL 探索——成功（58%，锚补强路线 v18/v19 均未做到）；
2. 先 6p 后混编——多人从废到强（6p 12→71%），且随机天赋三倍（20→60%）
   反超无天赋：3p 天赋 worker 的机制学习 + 6p 容量压力的组合效应。

**轻蒸修复第二次被证伪**（v21：T2/G5 恢复但 6p 71→0、随机 60→8）——
PPO 深训练后的增益对监督 pass 极敏感，与 v19 教训一致。**T2 退化
（33%）登记移交 P6a-2**：league 混编时锚 + 对手多样性拉回，或下轮
训练调 T2 事件采样权重。

**P6a-1 退出标准达成**（6p 门禁 71% ≥ 随机 ×3 且 3p 双口径不倒退）。
下一步 P6a-2：league 混编自对弈（对手池 = BasicAI 人格 + 专家栈 10
checkpoint + 历史快照 v1/v14/v16f/v18/v20 + self 配比 ~25%）。

### 9.3 P6a-2：避战坍缩的发现、诊断与修复（2026-08-23/24）

**league v1 配方**（v22 = v20 + 300k，league 2w + BasicAI 2w，快照池
v14/v16f/v18/v20）：6p 98%、3p 100%、league 口径 14/15——但**二杀 0%、
激活 10/14**（T1 25/T2 33/G4 33，进攻性天赋荒废）。行为分解（6 局）：
interact 169 次、1 杀；得分分解：**名次加成 8.0 占总分 80%**。

**根因（DOC-054，已入台账 decision-needed）**：引擎终分
`剧情 + 击杀×3 + 伤害×0.1 + PP + 2×(名次−1) + 唯一生还4`——唯一生还
名次分（8）> 两杀战果（6），击杀有风险而苟活无风险；对手为互杀倾向的
BasicAI/快照时**避战拖分是 dominant strategy**。RL 利用的是计分设计
漏洞而非针对攻击——对抗性暴露设计缺陷，RL 的价值所在。

**修复实验矩阵**（四组对照）：

| Run | 起点 | Reward | 结果 |
|---|---|---|---|
| v22 | v20 | 旧（胜=+100） | 避战坍缩（100%/0 杀） |
| v23 | v22 | 旧 + combat 课 | 课程压力不够（T1/T2 仍 33、0 杀） |
| v24 | v22 | 主动胜利 | 过渡态（0 杀、胜率掉至 53-60%——从坍缩专家学主动太难） |
| **v25** | **v20** | **主动胜利** | **修复**（见下） |

主动胜利条款（`rl/reward_m9`，用户裁决 2026-08-23）：引擎胜利 且
（击杀数最高 或 剧情分最高）才得 +100，空胜利 30（低于主动但高于败方
梯度起点 −20）；只影响 RL 训练奖励，引擎判定与 official 口径不变。
**起点选择是第二个关键**：修复坍缩比预防坍缩贵——v24 从已坍缩的 v22
出发失败，v25 从有击杀底子的 v20 出发成功。

**v25 终态（P6a-2 交付，`checkpoints/p6_student_v25/finetuned.zip`）**：

| 指标 | v20（P6a-1） | v22（坍缩） | **v25（修复）** |
|---|---|---|---|
| 14 槽激活 | 13/14 | 10/14 | **14/14 PASS** |
| T1/T2/G4 | 58/33/67 | 25/33/33 | **83/67/83** |
| 二杀率 | 12% | 0% | **13%** |
| 3p 无天赋 | 55% | 100% | **97%** |
| 6p | 71% | 98% | **90%** |
| 3p 随机天赋 | 60% | 78% | 48% |
| league 口径（快照 15 局） | 67% | 93% | **40%** |

**主动化的过渡代价（如实登记）**：随机天赋 60→48%、league 93→40%——
主动流在"对手仍是旧 reward 训出的互杀/避战流"的池子里，引擎口径天然
吃亏（主动出击=暴露）。v25 在弱对手（BasicAI）下已能主动获胜（97%），
对快照强对手的击杀能力仍在爬坡。这是**引擎计分未修时的结构性错位**：
主动胜利条款只在 RL 内部对齐了训练目标，引擎口径仍奖励避战——
**给游戏侧修订（DOC-054）的最有力论据：引擎空胜利条款落地后，主动流
与引擎胜利将重新对齐，league 强度可随训练恢复**。

**移交 P6b**：v25 为起点；league 池刷新（v22/v25 入池）；exploiters
（专打主力的旧快照）与 self 配比上调；每轮三层门禁 + 14 槽激活探针。

## 10. 风险与对策

| 风险 | 对策 |
|---|---|
| obs 变维作废旧 checkpoint | append-only 尾部追加 + 零填充 adapter；专家栈冻结在旧 schema |
| 蒸馏后稀有技能掉点 | 三层门禁 + 预定义容差；卡死才升多头 |
| 天赋课重蹈梯度淹没 | 权重/截断/锚点三件套（s138 已验证） |
| 路由规则过拟合公开状态 | v1 阈值以课程布置事实为据；校准后回写注册表 |
| CPU 预算（20 核） | SubprocVecEnv 8 + torch 单线程；吞吐基线沿用 `tools/m9_rl_train.py` |
