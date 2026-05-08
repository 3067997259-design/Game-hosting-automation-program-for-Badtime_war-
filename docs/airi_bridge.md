# AIRI Bot Bridge 使用文档

`bot_bridge.py` 是一个独立的翻译层进程，作为普通的远程客户端连接到游戏服务器，
同时通过 WebSocket 连接到 [AIRI](https://github.com/moeru-ai/airi) ，将游戏事件
翻译成 AIRI 能理解的自然语言，并将 AIRI 的回复解析回游戏指令。

```
  ┌────────────────┐  TCP 长度前缀+JSON   ┌──────────────────┐  WebSocket(JSON)   ┌───────┐
  │ main_server.py │ ◄──────────────────► │  bot_bridge.py   │ ◄────────────────► │ AIRI  │
  └────────────────┘                      └──────────────────┘                    └───────┘
```

不需要修改游戏或 AIRI 的任何源码。

---

## 前置条件

1. AIRI 已安装并能正常运行（建议版本 0.10.x）
2. Python 环境已安装 `websockets` 库
3. 游戏服务器能正常启动

---

## 配置步骤

### 步骤 1：安装依赖

```bash
pip install websockets
```

### 步骤 2：复制配置文件

```bash
cp config/airi_bridge_config.example.json config/airi_bridge_config.json
```

### 步骤 3：编辑配置文件

| 字段 | 说明 |
| --- | --- |
| `airi_ws_url` | AIRI 的 WebSocket 地址（默认 `ws://localhost:6121/ws`） |
| `airi_auth_token` | 如果 AIRI 设置了认证 token，填入；否则留空 |
| `module_id` | Bridge 在 AIRI 端注册的模块 ID（默认 `badtime-war-bridge`） |
| `game_server_host` / `game_server_port` | 游戏服务器地址和端口 |
| `bot_name` | Bot 在游戏中显示的名字 |
| `action_timeout` | 等待 AIRI 回复行动的超时秒数（建议 60） |
| `chat_reply_timeout` | 等待 AIRI 回复聊天的超时秒数（建议 30） |

### 步骤 4：测试 AIRI 连接

```bash
python test_airi_connection.py
# 或指定地址：
python test_airi_connection.py ws://localhost:6121/ws
```

如果看到 `✓ AIRI 回复: ...` 说明连接正常。

### 步骤 5：启动游戏

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

---

## 启动顺序（重要）

1. 先启动 **AIRI**（确保 WebSocket 服务就绪）
2. 再启动 **游戏服务器** (`main_server.py`)
3. 最后启动 **Bot Bridge** (`bot_bridge.py`)
4. 可选：启动人类客户端 (`main_client.py`)
5. 在游戏服务器中，Bot Bridge 会自动占据一个远程玩家位置
6. 房主点击「开始游戏」即可

---

## 工作原理

### 游戏事件 → AIRI

Bridge 监听以下游戏服务器事件，并翻译为人类可读的中文文本：

- `show_round_header` → `=== 第 N 轮 ===`
- `show_phase` → `--- <阶段名> ---`
- `show_action_turn_header` → `轮到 <玩家> 行动`
- `show_result` / `show_info` → 直接转发原文
- `show_death` → `<玩家> 死亡！原因：<cause>`
- `show_victory` → `<玩家> 获得了最终胜利！`
- 玩家聊天消息（公屏 / 私聊）

这些事件以 `spark:notify`（事件通知）或 `input:text`（聊天）形式推送给 AIRI。

### 服务器请求 → AIRI 提示词

Bridge 把游戏服务器发来的 4 类请求翻译为带格式约束的提示词，发送给 AIRI：

| 服务器请求 | AIRI 提示格式 | 期望回复 |
| --- | --- | --- |
| `request_command` | `轮到你行动了！...请用 ACTION: <指令> 的格式回复` | `ACTION: move 商店` |
| `request_choose` | 列出选项 + `请用 CHOOSE: <编号>` | `CHOOSE: 2` |
| `request_choose_multi` | 列出选项 + `请用 CHOOSE: <编号1>,<编号2>` | `CHOOSE: 1,3` |
| `request_confirm` | `请用 CONFIRM: y 或 CONFIRM: n` | `CONFIRM: y` |

### AIRI 回复 → 游戏指令

`ResponseParser` 从 AIRI 的自然语言回复中按以下顺序提取指令：

1. **标准格式**：`ACTION: ...` / `CHOOSE: 3` / `CONFIRM: y`
2. **中文格式**：`行动: ...` / `选择: 3` / `确认: 是`
3. **关键词匹配**：把「移动到商店」翻译为 `move 商店` 等
4. **直接匹配**：如果 AIRI 直接回复了合法指令文本，原样使用
5. **兜底**：解析失败时使用 `forfeit`（行动）/ 第一个选项（选择）/ `false`（确认）

---

## 调试指南

### 问题：`bot_bridge.py` 报 `无法连接到 AIRI`

- 检查 AIRI 是否正在运行
- 检查 WebSocket 地址是否正确（默认 `ws://localhost:6121/ws`）
- 运行 `python test_airi_connection.py` 单独测试

### 问题：`bot_bridge.py` 报 `无法连接到游戏服务器`

- 检查 `main_server.py` 是否已启动
- 检查端口号是否匹配（默认 9527）

### 问题：AIRI 连接成功但不回复

- 检查 AIRI 的模型是否已加载
- 用 `--debug` 参数启动查看详细日志
- 检查 AIRI 的 WebSocket 是否只接受认证连接（需要填 `airi_auth_token`）

### 问题：AIRI 回复了但行动解析失败（日志显示「无法解析行动，使用 forfeit」）

- 这是正常的——AIRI 的回复格式不一定符合预期
- 查看日志中的「AIRI 原始回复」了解它实际说了什么
- 可以在 AIRI 的角色卡 / system prompt 中强调回复格式要求
- 可以在 `ResponseParser` 中添加更多匹配模式

### 问题：Bot 每回合都 `forfeit`

- 大概率是 AIRI 回复超时或格式不对
- 增大 `action_timeout`（配置文件中）
- 用 `--debug` 查看具体是超时还是解析失败

---

## 文件清单

| 文件 | 作用 |
| --- | --- |
| `bot_bridge.py` | 主脚本：游戏 ↔ AIRI 翻译层 |
| `config/airi_bridge_config.example.json` | 配置文件模板 |
| `test_airi_connection.py` | 独立的 AIRI WebSocket 连接测试工具 |
| `docs/airi_bridge.md` | 本文档 |
