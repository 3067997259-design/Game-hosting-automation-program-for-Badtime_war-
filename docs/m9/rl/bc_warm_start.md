# M9 层行为克隆（BC Warm-up）设计

状态：设计稿（P1 待实施）。目标：缓解"复杂 M9 冷启动梯度信噪比极低"（1M 数据：
PG 信号 ~−0.006、熵未收窄、loss 持续降但胜率低）。

## 1. 为什么需要（依据）

- 1M 冷启动轨迹：`train/loss` 首段 29,535 → 末段 12,589（−57%，未收敛）；
  `policy_gradient_loss` ~−0.006（策略几乎不朝"赢"移动）；熵 2.34→1.86（远未收窄）。
- 冷启动在高维 570/149 动作空间里，随机探索起步几乎不可行（用户判断 + 数据一致）。
- BC 用 BasicAI 演示给一个**合法、近似专家**的初始策略 → 后续 RL fine-tune 从"会玩一点"开始，
  而不是从"什么都不会随机"开始。

## 2. 复用现有基建（不重写）

| 组件 | 现状（legacy） | M9 适配 |
|---|---|---|
| `rl/bc_collector.py` | BasicAI 演示收集，(obs539, action_idx139→137) | obs→`build_obs_profile`(570)；action→`ACTION_COUNT_M9`(149)；mask→`build_action_mask`(m9) 带 M9 specials/模板族；`set_profile('m9-rfc')` |
| BC 训练器（产出 `.pt`） | MLP：Linear(539→384)→(256)→(137) | Linear(570→384)→(256)→(149)；全天赋演示 |
| `rl/bc_migrate.py --algo maskable-recurrent` | 末两层 256→137 复制进 recurrent PPO head | OBS_DIM/ACTION_COUNT 参数化到 570/149；输出 m9 warm zip |

## 3. M9 特有要点

- **天赋口径**：BC 演示要在**随机天赋（与 P0 一致）**下收集——warm 出的策略应对"RL 有任一天赋"；
  观测不含 RL 自身天赋 one-hot（现 obs 无该通道），故混合天赋演示 ≈ 通用策略，可接受。
- **策略架构**：BC 只预热 policy 末两层/action_net；LSTM 仍随机 → fine-tune 真正学序列。
- **不 BS 陷阱**：BC 会过拟合脚本怪癖 → fine-tune 时保留熵（ent_coef>0）、别把 BC 当终点。
- **度量**：BC 后 150k fine-tune 的胜率曲线 vs 冷启动 150k（P0 已有同预算对照基线）。

## 4. 实施清单（P2 顺序）

1. `bc_collector` 加 m9 模式（profile/obs/action/mask 换口径；数据落 `bc_data/m9/`）。
2. 收集 2000–5000 局全天赋演示（每局几个 RL 决策点 → 预计数十万样本）。
3. BC 训练（570/149 MLP → `m9_bc_best.pt`）。
4. `bc_migrate --algo maskable-recurrent --profile m9` → `m9_warmstart.zip`。
5. `m9_rl_train.py` 加 `--pretrain <zip>`（载 pretrain.policy.state_dict 初值，LSTM 随机）。
6. **A/B**：同 seed，`--pretrain` 150k vs 冷启动 150k → 对比 loss 起点/胜率曲线/熵。
   - 预期：BC 后 initial loss 更低、熵更小、训练早期胜率即有值，fine-tune 逼近 BasicAI 更快。

## 5. 备选（若全量 BC 太重）

- **合法动作先验 warm-up**：只把初始 logits 压向 BasicAI 常用动作子集（不学完整映射），
  更省但收益小；不建议优先。
- **弱对手课程**：BC 之外/之后配 BasicAI 难度降档，作为渐进课程（与 BC 正交，可叠加）。
