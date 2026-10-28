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
| `survival_heal` | `checkpoints/codex_m9_course_survival_dual_s124/final.zip` | 治疗/购甲生存 | 自身 HP≤8 且存活对手≥1 |
| `armor_repair` | `checkpoints/codex_m9_course_survival_repair_bc_s131/final.zip` | 修甲 | 自身护甲耐久≥1 且 <50% |
| `sharpen` | `checkpoints/codex_m9_course_equipment_sharpen_bc_s135/final.zip` | 磨刀 | 有可磨武器（伤害 < 磨后阈值）且未持有磨后武器 |
| `detection` | `checkpoints/codex_m9_course_visibility_thermal_bc_s136/final.zip` | 隐身对策/探测 | 存在隐身存活对手且自身无探测 |
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

## 6. P3 统一蒸馏数据收集

- 序列格式扩展为 `(obs_seq, mask_seq, action, teacher_logits, expert_id, reward)`；
- 采样矩阵：自然 3/4/5/6 人局 × 全部原子课程场景，按技能均衡（稀有技能上采样，沿用 s138
  实测验证的 sample weight 机制）；
- 路由器每步 `(GameFacts, expert, reason)` 落日志——门控监督标签的数据源。

## 7. P4 统一学生训练

- 第一步：**单头 + 专家 ID 辅助头**（共享主干；辅助头逼表征区分技能，动作出口仍单头）；
- 第二步（仅当第一步预定义卡死：某原子课两轮蒸馏后仍低于教师 10pp 以上）：多头 + 门控
  （先监督模仿路由日志，后小学习率 PPO 微调；门控必须吃递归状态）；
- 训练：teacher-logit KL 蒸馏 → PPO 微调，蒸馏数据按比例混入当 anchor 防遗忘；
- **三层验收门禁**（复用 `rl/curriculum.py` gate 结构）：① 全部原子课达标；② 三人毕业指标
  ≥ 基线 −5pp 容差；③ 4/5/6 人自然局不低于专家栈零样本。任何技能倒退只回退学生。

## 8. P5 十四天赋课程

按机制复杂度四档推进：被动类 → SP 演出类（T1/T3 场景已有）→ 复杂机制类（G3/G5/G4/G1/G2）→
交互对抗类（T6 警察/G7 压制/G6 借用/G0 遗物）。每天赋三步协议：**oracle 可行性探针**
（复用 `tools/rung0_chain_probe.py` 模式，核心动作链在 RL 编码下走得通才轮到学习问题）→
定向课程（可归因靶场）→ 蒸馏数据并入统一学生。出口标准：14/14 天赋各有原子课门禁 +
防失明登记全绿。

## 9. P6 自博弈联赛与毕业重定义

统一学生进 `rl/self_play.py` 自博弈；专家栈降格为 league 成员与旧课对手。**毕业标准升级**：
单一 checkpoint（a）三人无天赋 ≥ 基线 −5pp；（b）随机天赋三人/四人局达标；（c）全部原子课
门禁绿。

## 10. 风险与对策

| 风险 | 对策 |
|---|---|
| obs 变维作废旧 checkpoint | append-only 尾部追加 + 零填充 adapter；专家栈冻结在旧 schema |
| 蒸馏后稀有技能掉点 | 三层门禁 + 预定义容差；卡死才升多头 |
| 天赋课重蹈梯度淹没 | 权重/截断/锚点三件套（s138 已验证） |
| 路由规则过拟合公开状态 | v1 阈值以课程布置事实为据；校准后回写注册表 |
| CPU 预算（20 核） | SubprocVecEnv 8 + torch 单线程；吞吐基线沿用 `tools/m9_rl_train.py` |
