# 起闯战争 Badtime_war — 电子DM

![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/3067997259-design/Game-hosting-automation-program-for-Badtime_war-)

## 写在最前面

很多人上小学的时候都玩过这样的游戏：一堆人围着猜拳，赢了的人从"起床"开始声明一个行动，只要大家都觉得不太过分，就可以继续。一般，游戏场面会从"大家在商店买东西"经过二十分钟演化为"太阳系公社在银河帝国内发动政变"再到"A死于因果链抹杀"，只要脑洞大，能博得满堂彩，无论赢了输了都会让人心服口服。

毕竟是个体育课上自由活动时打发时间的游戏，一局玩完之后大家都一笑了之。没人考虑过什么应该有个毒圈强制游戏在多少分钟的时候结束，甚至没有谁在乎输赢，只有真正的名场面才会被挂在嘴边好几天。

十多年过去了，后来，有一个叫AfterRain的人，就是坐在屏幕前正在写下这段话的人，某一天对着自己喜欢的角色发癫的时候，突然想到了这些古早的回忆。

这个游戏的底层是简陋的，但也正因为此，足够开放，能够容纳得下他对于他所热爱的那些故事、所喜欢的所有角色的所有的想法。大厂会基于一个角色的性格亮色和剧情弧光为TA设计作为自机角色的技能组。在大多数情况下，它们在精彩程度、强度和复杂度的平衡上达到了相当好的权衡。然而，这也意味着，或许会存在一些“遗憾”，会有一些更为复杂和激进，但也更精彩的设计，一些能让这个自机角色/使用这个自机角色的玩家更像是角色本人上身的内容，不得不被舍去。而他没有这些限制。他没有美术和音乐资源、不可能制作与现代二游类似的交互前端，甚至用RPG maker做个前端都很费劲；但他可以随心所欲地改造底层、引入机制，世界的限制可以为了角色百分百让步，只为了让那些角色以另一种姿态，在这个小小的世界里以他所期望的方式活下去。

因此，零python基础的他学会了用AI，无论是claude、deepseek处理文本和归纳思路，还是Devin/claudecode等code agent，把自己的思路和记忆重整化为具有一个世界观高包容度（类似圣杯战争/型月世界观）、底层足够简单和严谨，向外几乎可以无限延伸的类似minecraft的游戏平台，以MUD的形式呈现大逃杀类的游戏过程和胜利条件，再附带自己的想象力和潦草设计，就成了起床战争。

开发时间很短。从这行字被写下来，追溯到他第一天知道什么是cherry studio，怎么接入API，只过去了不到三个月。

我是AfterRain.

这是一个一人开发的项目。

很遗憾的是，我并没有什么团队。我的朋友们是我虚构出来，让我觉得开发过程中不那么孤独的。你把开发日志中所有"我们"替换为"我"，就能看到真实的故事。

它的目的从一开始就不是作为同人企划赚钱或者在B站上发个视频让更多人看见，而是为一些我爱的角色建造一方我力所能及的、无人注视的小小空间。这就是为什么你只能在Github上看到这段话。

如果你也深爱着这些角色，如果你也想让虚拟存在远离社区的戾气和无边际的争吵，那这个项目是为你准备的。

如果你只是好奇"一个人凭借AI agent或者能做到什么程度"，那我希望这个游戏不会让你失望。

---

## 概述

本项目刚开始时被设计为一个适用于自制演出向大逃杀类游戏《起闯战争 Badtime_war》（也称起床战争，与minecraft同名玩法无关）的电子DM，在大部分时间内可实现全自动裁决和记录游戏状态。也可在某些需要人工判定的场合为人类DM提供裁决帮助。总的来说，在V1.92 Hotfix之前，它就已经接过了正常游玩流程中大部分人类DM的权能，其在大多数场景下的不可或缺性使得本游戏以G7的实装为分界线，从桌面游戏明显地向广义MUD游戏演化。

现已扩充为一个包含基于gymnasium和MaskablePPO的完整强化学习训练管线和RL接入实现、一般LLM模型/AIRI_project接入作为聊天后端、AIRI_bot基于websocket协议接入作为独立玩家、完整的强度测试脚本等板块的技术游乐场。

在 Ver1.94 前，本项目的游戏核心是一个纯热座式的命令行游戏。Ver1.94 中加入了 CLI 和 TUI 支持的联机模式，支持远程玩家、BasicAI 和 RL 的接入，同时允许接入 LLM 模型作为basicAI的chatskin，或者接入AIRI_bot作为chatskin/独立玩家（实验性）

需要注意的是，这个游戏内容的迭代速度较快，很多配套设施的建设速度可能赶不上游戏本体的改动速度。

废话少叙，祝玩得开心。

---

## Profile 分层

| profile | 说明 |
|---|---|
| `legacy` | 默认稳定口径，不带 `--profile` 时使用 |
| `v2exp` | V2.0-exp 实验档案（M1–M7 系列） |
| `m9-rfc` | 当前开发主线：单行动槽、SP 即演/公演、14 天赋、PP/警察/剧情分；风洞与数值校准进行中 |

各 profile 由 `engine/experiments.py` 统一开关；默认配置见 `config/game_config.json`。

---

## 快速开始

```bash
# 本地热座（默认 legacy 口径）
python main.py

# V2.0-exp 档案
python main.py --profile v2exp

# M9 当前开发主线（独立 profile；风洞/数值校准口径）
python main.py --profile m9-rfc

# 联机模式（房主）
python main_server.py --players 3

# 联机模式（客户端）
python main_client.py --host <服务器IP> --name <你的名字>

# 全AI胜率统计（legacy）
python stats_runner.py --players 6 --games 5000

# V2.0-exp 档案风洞
python stats_runner.py --profile v2exp --players 6 --games 5000

# M9 风洞（当前开发主线常用命令）
python stats_runner.py --profile m9-rfc --players 6 --games 500

# M9 运行时烟雾 / 剧本验收
python tools/m9_rfc_smoke.py
python tools/m9_rfc_playtest.py
```

不带 `--profile` 时，默认运行 v1 稳定口径（`legacy`）。`m9-rfc` 是当前开发主线
（独立 profile，不修改 legacy/v2exp 行为）；其风洞与平衡数值仍在迭代。

---

## 文档导航

| 文档 | 内容 |
|------|------|
| [文档中心](docs/README.md) | 文档身份、适用版本、权威范围和冲突台账的统一入口 |
| [M9 文档中心](docs/m9/README.md) | M9-rfc 当前开发主线：合同、AI 策略接口、风洞与待冻结数值 |
| [V2.0-exp 模块化手册](docs/handbook/README.md) | 按主题和天赋拆分的候选玩家规则；支持模型按需读取 |
| [Legacy 模块化手册](docs/legacy/README.md) | 旧版规则的按主题入口；不要与 V2.0-exp 规则混用 |
| [旧天赋参考](docs/talents.md) | 混合 legacy、V2 与实现说明，语义审计完成前只用于定位 |
| [操作与接入](docs/operations/README.md) | 使用说明、指令表和 AIRI 接入的统一入口 |
| [历史文档](docs/history/README.md) | 版本更新日志与开发日记，只解释演化 |

---

## 依赖

- **基础游戏**（`main.py`）：仅需 Python 3.8+ 标准库
- **联机模式**（`main_server.py` / `main_client.py`）：可选安装 `textual`（`pip install textual`）
- **LLM 聊天**：可选安装 `openai` 或 `requests`（Ollama）
- **RL 训练**：需要 `torch`、`stable-baselines3`、`sb3-contrib`、`gymnasium`、`numpy`

> 风洞提示：`stats_runner.py` 启动时会尝试 import RL 依赖（torch/sb3）。未安装时
> BasicAI 风洞仍可运行，但建议在长期风洞环境预装，避免启动慢或回退噪声。

---

## 配置文件

| 文件 | 说明 |
|------|------|
| `config/game_config.json` | 游戏配置（可设置 AI 禁用天赋列表） |
| `config/llm_config.json` | LLM 聊天后端配置（需从 `.example.json` 复制） |
| `config/airi_config.json` | AIRI 聊天皮肤配置（模式 1） |
| `config/airi_bridge_config.json` | AIRI 独立玩家桥接配置（模式 2） |
| `config/prompt_config.json` | 提示文本配置 |

---

## 项目架构

```
engine/              — 游戏引擎核心（GameState, RoundManager, ActionTurnManager）
engine/m9/           — M9-rfc 独立机制层（行动系统/结算/评分/警察/十四天赋）
engine/experiments.py — profile 与实验开关（legacy/v2exp/m9-rfc）
engine/balance.py    — 数值读取入口（data/balance.json 唯一信源）
combat/              — 伤害结算系统（22 步流水线；M9 下由 engine/m9/combat 承接）
actions/             — 玩家行动（每个文件导出 execute() 函数）
models/              — 数据模型（Player, Equipment, Markers, PoliceData）
talents/             — 天赋系统，所有天赋继承 BaseTalent
cli/                 — 命令行解析（parser.py）与验证（validator.py）
controllers/         — 玩家控制器（Human, BasicAI, RL, Network, Chorus）
controllers/ai/      — BasicAI 新架构：orchestrator/minds/decision/game_query/adapters
locations/           — 地点交互逻辑
network/             — 联机网络层
rl/                  — 强化学习训练管线（可选模块）
ai_chat/             — LLM 聊天集成（可选模块）
tui/                 — Textual TUI 界面（联机）
data/                — prompts.json（所有面向用户的文本模板）
config/              — 配置文件
docs/                — 文档（docs/m9 为 M9 当前开发主线入口）
tools/               — 运行时验收/诊断脚本
tests/               — unittest 测试
```
