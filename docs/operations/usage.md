# 使用说明

本项目需要 Python 3.8+。根据使用场景不同，提供以下入口脚本。

---

## 一、本地游戏（单机热座）

```bash
python main.py
```

启动交互式命令行游戏。程序会引导完成：
1. 选择调试模式（0-3 级，默认关闭）
2. 选择游戏模式（全人类 / 人机混合 / 全AI观战）
3. 设置玩家人数（2-6 人）与 AI 人格（balanced / aggressive / defensive / political / assassin / builder）
4. 是否启用天赋系统（14 个天赋，每个天赋仅能被 1 人选取）
5. 是否启用日志记录
6. 演示速度（含 AI 时可选逐步/慢速/中速/全速）

---

## 二、局域网联机

### 房主（服务器端）

```bash
python main_server.py [选项]
```

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--port` | 监听端口 | 9527 |
| `--players` | 总人数（2-6） | 2 |
| `--no-host-play` | 房主不参与游戏（观战模式） | 否 |
| `--cli` | 使用纯 CLI 模式（默认使用 Textual TUI） | 否 |
| `--debug` | 调试级别（0-3） | 0 |

启动后进入大厅管理，可用命令：
- `status` — 查看房间状态
- `ai <slot> [personality]` — 将某个槽位设为 BasicAI（可选人格）
- `rl <slot>` — 将某个槽位设为 RL AI（需已训练模型）
- `airi <slot>` — 将某个槽位设为 AIRI Bot（需 AIRI WebSocket 服务运行）
- `policy <slot> <wait|ai>` — 设置断线策略（等待重连 / AI接管）
- `chatmode <slot> <airi|llm|off>` — 设置 AI 聊天模式
- `name <新名字>` — 修改房主名称
- `debug <0-3>` — 动态切换调试级别
- `start` — 开始游戏
- `/chat <内容>` — 公屏聊天
- `/whisper <玩家名> <内容>` — 私聊

### 客户端（玩家端）

```bash
python main_client.py [选项]
```

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--host` | 服务器地址 | 127.0.0.1 |
| `--port` | 服务器端口 | 9527 |
| `--name` | 玩家名称 | 交互输入 |
| `--cli` | 使用纯 CLI 模式（默认使用 Textual TUI） | 否 |
| `--reconnect` | 断线重连模式 | 否 |
| `--debug` | 调试级别（0-3） | 0 |

> **TUI 说明**：服务器和客户端默认使用 Textual TUI 界面。若未安装 `textual`（`pip install textual`），会自动回退到纯 CLI 模式。

---

## 三、自动胜率统计

```bash
python stats_runner.py [选项]
```

运行全 AI 对局并输出天赋胜率、人格胜率、校正胜率等统计表格。

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--players` | 每局玩家人数（2-6） | 6 |
| `--games` | 总局数 | 5000 |
| `--model` | RL 模型路径（.zip），启用后一个 AI 席位替换为 RL | 无 |
| `--rl-talent` | RL 天赋选择：`model`=模型自选，`random`=均匀随机，数字=指定天赋编号，`0`=无天赋 | random |
| `--n-stack` | RL 帧堆叠数量（需与训练时一致） | 30 |

示例：
```bash
# 纯 BasicAI 统计
python stats_runner.py --players 6 --games 5000

# V2.0-exp 档案
python stats_runner.py --profile v2exp --players 6 --games 5000

# 加入 RL 模型对比
python stats_runner.py --players 6 --games 1000 --model checkpoints/best_model.zip --rl-talent random
```

不带 `--profile` 时，默认运行 v1 稳定口径（`legacy`）。

---

## 四、RL 训练系统

训练系统基于 MaskablePPO（sb3-contrib），使用 GRU 特征提取器处理帧堆叠观测。

### 4.1 行为克隆数据收集

```bash
python -m rl.bc_collector [选项]
```

运行全 AI 对局，记录 BasicAI 的决策数据 `(obs, action_idx, mask)` 用于行为克隆预训练。

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--games` | 游戏局数 | 5000 |
| `--players` | 每局玩家数 | 6 |
| `--output` | 输出目录 | bc_data/g7 |
| `--talent` | 收集天赋数据（`all`=所有天赋轮流，数字=指定天赋） | all |

### 4.2 行为克隆预训练

```bash
python -m rl.bc_pretrain [选项]
```

读取 `.npz` 数据训练 MLP 策略网络。

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--data` | BC 数据 `.npz` 路径 | **必填** |
| `--output` | 输出 checkpoint 路径 | pretrained/g7_bc.zip |
| `--epochs` | 训练轮数 | 50 |
| `--batch-size` | 批大小 | 256 |
| `--lr` | 学习率 | 1e-3 |
| `--device` | 训练设备（auto/cpu/cuda） | auto |
| `--val-split` | 验证集比例 | 0.2 |

### 4.3 BC 权重迁移

```bash
python -m rl.bc_migrate [选项]
```

将 BC 预训练的 `.pt` 权重迁移到 MaskablePPO 的 `.zip` 模型中。

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--bc-weights` | BC 预训练权重路径（.pt） | **必填** |
| `--output` | 输出 MaskablePPO 模型路径（.zip） | pretrained/g7_warmstart.zip |
| `--n-stack` | 帧堆叠数量 | 30 |
| `--device` | 设备（cpu/cuda） | cpu |

### 4.4 PPO 训练（主训练脚本）

```bash
python -m rl.train [选项]
```

**核心训练参数：**

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--opponents` | 对手数量（1-5） | 3 |
| `--timesteps` | 总训练步数 | 1,000,000 |
| `--n-envs` | 并行环境数 | 1 |
| `--max-rounds` | 每局最大轮数（默认动态计算） | 动态 |
| `--seed` | 随机种子 | 42 |
| `--resume` | 从已有模型恢复训练（.zip 路径） | 无 |
| `--device` | 训练设备（auto/cpu/cuda） | auto |

**PPO 超参数：**

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--lr` | 学习率 | 3e-4 |
| `--n-steps` | 每次 rollout 步数 | 2048 |
| `--batch-size` | Mini-batch 大小 | 256 |
| `--n-epochs` | 每次更新 epoch 数 | 10 |
| `--gamma` | 折扣因子 | 0.99 |
| `--gae-lambda` | GAE lambda | 0.95 |
| `--clip-range` | PPO clip range | 0.2 |
| `--ent-coef` | 熵系数 | 0.01 |
| `--n-stack` | 帧堆叠数量 | 30 |

**训练回调：**

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--ckpt-freq` | Checkpoint 保存频率（步数） | 50,000 |
| `--eval-freq` | 评估频率（步数） | 50,000 |
| `--eval-episodes` | 每次评估局数 | 20 |
| `--n-eval-envs` | 评估并行环境数 | None（与 n-envs 一致） |

**课程学习：**

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--curriculum` | 启用课程学习 | 否 |
| `--curriculum-start` | 课程起始对手数 | 2 |
| `--curriculum-threshold` | 课程升级胜率阈值 | 自动计算 |
| `--curriculum-thresholds` | 每阶段升级阈值（空格分隔） | 自动计算 |
| `--ent-rebound` | 课程升级时 entropy 回弹系数 | 0.03 |
| `--ent-rebound-decay` | entropy 回弹衰减步数 | 200,000 |

**Self-play：**

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--self-play` | 启用 self-play 训练 | 否 |
| `--seed-model` | Self-play 种子模型路径（.zip） | 无 |
| `--pool-size` | 对手池最大模型数量 | 20 |
| `--self-play-save-freq` | 模型保存频率（步数） | 500,000 |
| `--initial-basic-ai-prob` | 初始 BasicAI 混入概率 | 0.5 |
| `--final-basic-ai-prob` | 最终 BasicAI 混入概率 | 0.3 |
| `--collapse-threshold` | 坍塌检测胜率阈值 | 0.12 |
| `--no-collapse-detection` | 禁用策略坍塌检测 | 否 |
| `--max-per-model` | 同一对手池模型最多出现次数 | 1 |

**天赋选择：**

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--rl-talent` | RL 天赋编号（None=RL 自选，0=无天赋，1-14=指定） | None |
| `--enable-talents` | 启用天赋系统 | True |
| `--no-talents` | 禁用天赋系统 | — |
| `--force-random-talent-until` | 在此步数之前强制随机分配天赋 | 0 |
| `--talent-grace-steps` | 天赋自选解锁后的学习期步数 | 2,000,000 |

**典型训练流程：**

```bash
# 1. 收集 BC 数据
python -m rl.bc_collector --games 5000 --players 6 --talent 14

# 2. BC 预训练
python -m rl.bc_pretrain --data bc_data/g7/t14_bc_data.npz --epochs 50

# 3. 权重迁移
python -m rl.bc_migrate --bc-weights pretrained/g7_bc_best.pt

# 4. 带课程学习和 self-play 的 PPO 训练
python -m rl.train \
    --resume pretrained/g7_warmstart.zip \
    --opponents 5 \
    --timesteps 20000000 \
    --n-envs 16 \
    --curriculum --curriculum-start 2 \
    --self-play --seed-model pretrained/g7_warmstart.zip \
    --force-random-talent-until 5000000 \
    --device auto
```

### 4.5 模型导出（TorchScript）

```bash
python -m rl.export_torchscript --model <模型.zip> --output <输出.pts> [--n-stack 30] [--no-verify]
```

将训练好的 MaskablePPO 策略导出为 TorchScript，推理速度提升 2-5 倍。

### 4.6 观战脚本

```bash
python -m rl.watch_all [选项]
```

可视化 RL 智能体与 BasicAI 的对局过程。

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--model` | 模型路径（.zip） | **必填** |
| `--opponents` | 对手数量 | 1 |
| `--max-rounds` | 最大轮数 | 动态 |
| `--n-stack` | 帧堆叠数量 | 30 |
| `--enable-talents` | 启用天赋系统 | True |
| `--no-talents` | 关闭天赋系统 | — |
| `--rl-talent` | RL 天赋（None=自选，0=无，1-14=指定，random=随机） | None |
| `--games` | 连续跑多局并汇总统计 | 1 |

### 4.7 诊断工具

- **训练性能诊断**：`python -u -m rl.profile_train --resume <model> --opponents 5 --n-envs 16 --timesteps 200000`
- **Self-play 多进程诊断**：`python -m rl.diagnose_selfplay --seed-model <model> --opponents 5 --n-envs 16 --n-steps 200`
- **单局自对弈诊断**：`python -m rl.diagnose_single_game --model <model> --opponents 5 --rl-opponents 3 --n-stack 30`

---

## 五、LLM 聊天功能（联机模式）

在联机模式下，AI 玩家可通过 LLM 参与游戏内聊天。此功能完全可选。

### 配置

将 `config/llm_config.example.json` 复制为 `config/llm_config.json`，填入 LLM 配置：

**OpenAI / 兼容 API：**
```json
{
    "backend": "openai",
    "api_key": "你的API密钥",
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-3.5-turbo"
}
```

**Ollama 本地模型：**
```json
{
    "backend": "ollama",
    "host": "http://localhost:11434",
    "model": "llama3"
}
```

配置完成后，启动联机服务器时会自动为 BasicAI 玩家加载聊天模块。

---

## 六、AIRI 接入

详见 [docs/operations/airi_bridge.md](airi_bridge.md)。

共三种接入模式：

| 模式 | 谁负责游戏决策 | 谁负责社交聊天 |
|------|--------------|--------------|
| 模式 1（推荐）：聊天皮肤 | BasicAI（自动） | AIRI（仅社交，通过 `config/airi_config.json`） |
| 模式 2：独立玩家 | AIRI（全部决策） | AIRI（通过 `bot_bridge.py` + `config/airi_bridge_config.json`） |
| 模式 3：本地槽位 | AIRI（通过 `AiriController`） | AIRI（房主用 `airi <slot>` 命令设置） |

---

## 七、配置文件

| 文件 | 说明 |
|------|------|
| `config/game_config.json` | 游戏配置，可设置 AI 禁用天赋列表 |
| `config/llm_config.json` | LLM 聊天后端配置（需从 `.example.json` 复制） |
| `config/airi_config.json` | AIRI 聊天皮肤配置（模式 1，需从 `.example.json` 复制） |
| `config/airi_bridge_config.json` | AIRI 独立玩家桥接配置（模式 2，需从 `.example.json` 复制） |
| `config/prompt_config.json` | 提示文本配置 |

---

## 依赖说明

- **基础游戏**（`main.py`）：仅需 Python 标准库
- **联机模式**（`main_server.py` / `main_client.py`）：可选安装 `textual`（TUI 界面）
- **LLM 聊天**：可选安装 `openai` 或 `requests`（Ollama）
- **RL 训练**：需要 `torch`、`stable-baselines3`、`sb3-contrib`、`gymnasium`、`numpy`
