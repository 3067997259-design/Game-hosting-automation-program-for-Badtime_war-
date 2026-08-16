# AIRI 接入文档

游戏支持接入 [AIRI](https://github.com/moeru-ai/airi) 作为 AI 角色，共两种接入模式：

| 模式 | 谁负责游戏决策 | 谁负责社交聊天 | 启动方式 |
| --- | --- | --- | --- |
| **模式 1（推荐）：聊天皮肤** | 内置 BasicAI（自动） | AIRI（仅社交） | 正常启动 `main_server.py`，加载 `config/airi_config.json` |
| **模式 2：独立玩家** | AIRI（行动、选择、确认全部） | AIRI | `python bot_bridge.py`，加载 `config/airi_bridge_config.json` |

不需要修改游戏或 AIRI 的任何源码。

---

## 模式 1（推荐）：AIRI 作为 BasicAI 的"聊天皮肤"

```
  ┌─────────────────┐    内部调用    ┌────────────────┐  WebSocket(JSON)  ┌───────┐
  │   BasicAI       │ ◄────────────► │ AiriBackend     │ ◄───────────────► │ AIRI  │
  │ (动作决策)       │                │ (聊天后端)      │                   │       │
  └─────────────────┘                └────────────────┘                   └───────┘
        │
        │  仅在收到聊天消息时调用 LLM
        ▼
  AIChatModule（解析 [THINK]/[REPLY]/[ADJUST]，[ADJUST] 反向调整 BasicAI 状态）
```

### 工作原理
- 游戏中真正的玩家仍然是一个 `BasicAIController`，所有行动决策（move / attack / interact / forfeit 等）由 BasicAI 自动执行。
- 当 AI 收到聊天消息时，`AIChatModule` 通过 `AiriBackend` 把对话转发给 AIRI，由 AIRI 生成回复。
- AIRI 的回复需遵循 `[THINK] / [REPLY] / [ADJUST]` 格式：
  - `[REPLY]` 段会发回到游戏聊天频道。
  - `[ADJUST]` 段（可选）的 JSON 反过来调整 BasicAI 的 `_threat_scores` / `_llm_alliance` / `_llm_aggression_mod`，从而影响后续的行动决策。
- `DisplayBroadcaster` 会把游戏事件（轮次开始、玩家死亡、行动结果等）通过 `notify()` 推送给 AIRI，让 AIRI 实时感知战局。

### 配置步骤

1. 安装依赖：
   ```bash
   pip install websockets
   ```
2. 启动 AIRI（默认 `ws://localhost:6121/ws`）。
3. 复制配置文件：
   ```bash
   cp config/airi_config.example.json config/airi_config.json
   ```
4. 编辑 `config/airi_config.json`：

| 字段 | 说明 |
| --- | --- |
| `airi_ws_url` | AIRI 的 WebSocket 地址（默认 `ws://localhost:6121/ws`） |
| `airi_auth_token` | 如果 AIRI 设置了认证 token，填入；否则留空 |
| `module_id` | 在 AIRI 端注册的模块 ID（默认 `badtime-war-bridge`） |
| `airi_slot_id` | 哪个 BasicAI slot 使用 AIRI 作为聊天后端，例如 `2` |
| `chat_timeout` | 等待 AIRI 回复聊天的超时秒数（建议 30） |

5. 启动游戏：
   ```bash
   python main_server.py --players 3
   ```
6. 在房主控制台中将目标 slot 设为 BasicAI（必须与 `airi_slot_id` 一致）：
   ```
   [房主] > ai 2 aggressive
   ```
7. 启动游戏后，控制台应输出 `[AIRI] 已连接，绑定到 slot 2 (...)`。

### 多 AI 共存

`config/airi_config.json` 只对 `airi_slot_id` 指定的那个 slot 生效。其他 BasicAI slot 仍然使用 `config/llm_config.json` 配置的普通 LLM 后端（OpenAI / Ollama）。如果只想让某些 AI 发声，删除 `llm_config.json` 即可——其他 slot 不会注册聊天模块。

### 回退行为

| 情况 | 行为 |
| --- | --- |
| 没有 `config/airi_config.json` | 不尝试连接 AIRI，所有 BasicAI 使用普通 LLM（如有 `llm_config.json`）。 |
| AIRI 未运行或连接失败 | 服务器输出 `[AIRI] 连接失败...`，对应 slot 回退到普通 LLM 后端（如果有 `llm_config.json`）。 |
| 没有任何 LLM 配置 | AI 不参与聊天，但仍能正常游戏（行动由 BasicAI 决定）。 |

### 配置 `[ADJUST]` 反向影响

AIRI 的回复格式：
```
[THINK] 玩家A提议结盟对付玩家B。B威胁分最高，A只有30。接受对我有利。
[REPLY] 有意思。B确实太嚣张了，我不介意先解决他。
[ADJUST] {"threat_mod": {"玩家B": 10, "玩家A": -10}, "alliance": ["玩家A"]}
```

| 字段 | 含义 | 限幅 |
| --- | --- | --- |
| `threat_mod` | `{玩家名: 增减值}`，调整 BasicAI 对该玩家的威胁分 | 单次 ±20 |
| `alliance` | `[盟友名, ...]`，记录 BasicAI 的盟友列表 | — |
| `aggression` | 整体攻击倾向调整（`_llm_aggression_mod`） | 单次 ±10，累计 ±20 |

---

## 模式 2：AIRI 作为独立玩家（bot_bridge）

`bot_bridge.py` 是一个独立的翻译层进程，作为普通的远程客户端连接到游戏服务器，
同时通过 WebSocket 连接到 AIRI，将游戏事件翻译成 AIRI 能理解的自然语言，并将 AIRI 的回复解析回游戏指令。

```
  ┌────────────────┐  TCP 长度前缀+JSON   ┌──────────────────┐  WebSocket(JSON)   ┌───────┐
  │ main_server.py │ ◄──────────────────► │  bot_bridge.py   │ ◄────────────────► │ AIRI  │
  └────────────────┘                      └──────────────────┘                    └───────┘
```

不需要修改游戏或 AIRI 的任何源码。

### 前置条件

1. AIRI 已安装并能正常运行（建议版本 0.10.x）
2. Python 环境已安装 `websockets` 库
3. 游戏服务器能正常启动

### 配置步骤

#### 步骤 1：安装依赖

```bash
pip install websockets
```

#### 步骤 2：复制配置文件

```bash
cp config/airi_bridge_config.example.json config/airi_bridge_config.json
```

#### 步骤 3：编辑配置文件

| 字段 | 说明 |
| --- | --- |
| `airi_ws_url` | AIRI 的 WebSocket 地址（默认 `ws://localhost:6121/ws`） |
| `airi_auth_token` | 如果 AIRI 设置了认证 token，填入；否则留空 |
| `module_id` | Bridge 在 AIRI 端注册的模块 ID（默认 `badtime-war-bridge`） |
| `game_server_host` / `game_server_port` | 游戏服务器地址和端口 |
| `bot_name` | Bot 在游戏中显示的名字 |
| `action_timeout` | 等待 AIRI 回复行动的超时秒数（建议 60） |
| `chat_reply_timeout` | 等待 AIRI 回复聊天的超时秒数（建议 30） |

#### 步骤 4：测试 AIRI 连接

```bash
python test_airi_connection.py
# 或指定地址：
python test_airi_connection.py ws://localhost:6121/ws
```

如果看到 `✓ AIRI 回复: ...` 说明连接正常。

#### 步骤 5：启动游戏

```bash
# 终端 1：启动游戏服务器（至少 2 人局）
python main_server.py --players 3

# 终端 2：启动 AIRI Bot Bridge
python bot_bridge.py
# 或带调试日志：
python bot_bridge.py --debug
# 或覆盖 Bot 名称：
python bot_bridge.py --name "AIRI"

# 终端 3（可选）：启动人类客户端
python main_client.py --name "玩家A"
```

### 启动顺序（重要）

1. 先启动 **AIRI**（确保 WebSocket 服务就绪）
2. 再启动 **游戏服务器** (`main_server.py`)
3. 最后启动 **Bot Bridge** (`bot_bridge.py`)
4. 可选：启动人类客户端 (`main_client.py`)
5. 在游戏服务器中，Bot Bridge 会自动占据一个远程玩家位置
6. 房主点击「开始游戏」即可

### 工作原理

#### 游戏事件 → AIRI

Bridge 监听以下游戏服务器事件，并翻译为人类可读的中文文本：

- `show_round_header` → `=== 第 N 轮 ===`
- `show_phase` → `--- <阶段名> ---`
- `show_action_turn_header` → `轮到 <玩家> 行动`
- `show_result` / `show_info` → 直接转发原文
- `show_death` → `<玩家> 死亡！原因：<cause>`
- `show_victory` → `<玩家> 获得了最终胜利！`
- 玩家聊天消息（公屏 / 私聊）

这些事件以 `spark:notify`（事件通知）或 `input:text`（聊天）形式推送给 AIRI。

#### 服务器请求 → AIRI 提示词

Bridge 把游戏服务器发来的 4 类请求翻译为带格式约束的提示词，发送给 AIRI：

| 服务器请求 | AIRI 提示格式 | 期望回复 |
| --- | --- | --- |
| `request_command` | `轮到你行动了！...请用 ACTION: <指令> 的格式回复` | `ACTION: move 商店` |
| `request_choose` | 列出选项 + `请用 CHOOSE: <编号>` | `CHOOSE: 2` |
| `request_choose_multi` | 列出选项 + `请用 CHOOSE: <编号1>,<编号2>` | `CHOOSE: 1,3` |
| `request_confirm` | `请用 CONFIRM: y 或 CONFIRM: n` | `CONFIRM: y` |

#### AIRI 回复 → 游戏指令

`ResponseParser` 从 AIRI 的自然语言回复中按以下顺序提取指令：

1. **标准格式**：`ACTION: ...` / `CHOOSE: 3` / `CONFIRM: y`
2. **中文格式**：`行动: ...` / `选择: 3` / `确认: 是`
3. **关键词匹配**：把「移动到商店」翻译为 `move 商店` 等
4. **直接匹配**：如果 AIRI 直接回复了合法指令文本，原样使用
5. **兜底**：解析失败时使用 `forfeit`（行动）/ 第一个选项（选择）/ `false`（确认）

---

## 调试指南（两种模式通用）

### 问题：报 `无法连接到 AIRI`

- 检查 AIRI 是否正在运行
- 检查 WebSocket 地址是否正确（默认 `ws://localhost:6121/ws`）
- 运行 `python test_airi_connection.py` 单独测试

### 问题：`bot_bridge.py` 报 `无法连接到游戏服务器`（仅模式 2）

- 检查 `main_server.py` 是否已启动
- 检查端口号是否匹配（默认 9527）

### 问题：AIRI 连接成功但不回复

- 检查 AIRI 的模型是否已加载
- 模式 2 下用 `--debug` 参数启动查看详细日志
- 检查 AIRI 的 WebSocket 是否只接受认证连接（需要填 `airi_auth_token`）

### 问题：AIRI 回复了但行动解析失败（仅模式 2，日志显示「无法解析行动，使用 forfeit」）

- 这是模式 2 的常见情况——AIRI 的回复格式不一定符合预期
- 查看日志中的「AIRI 原始回复」了解它实际说了什么
- 可以在 AIRI 的角色卡 / system prompt 中强调回复格式要求
- 可以在 `ResponseParser` 中添加更多匹配模式
- 模式 1 下不存在这个问题——行动由 BasicAI 决定，不依赖 AIRI 的回复

### 问题：模式 1 下 [ADJUST] 没有反向影响

- 检查服务器日志中的 `[LLM ADJUST]` 输出，确认 JSON 是否被解析
- 检查 `[LLM ADJUST ERROR]`，可能是 JSON 格式不对或字段名拼写错误
- 单次 `threat_mod` 限幅为 ±20，`aggression` 单次 ±10、累计 ±20

---

## 文件清单

| 文件 | 作用 |
| --- | --- |
| `ai_chat/airi_connection.py` | AIRI WebSocket 连接管理（两种模式共享） |
| `ai_chat/airi_backend.py` | 模式 1：AiriBackend，作为 LLMBackend 接入 AIChatModule |
| `bot_bridge.py` | 模式 2：独立玩家桥接进程 |
| `config/airi_config.example.json` | 模式 1 配置模板 |
| `config/airi_bridge_config.example.json` | 模式 2 配置模板 |
| `test_airi_connection.py` | 独立的 AIRI WebSocket 连接测试工具 |
| `docs/operations/airi_bridge.md` | 本文档 |
