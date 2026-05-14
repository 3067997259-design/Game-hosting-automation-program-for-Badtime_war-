# 起闯战争 Badtime_war — 电子DM

![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/3067997259-design/Game-hosting-automation-program-for-Badtime_war-)

## 写在最前面

很多人上小学的时候都玩过这样的游戏：一堆人围着猜拳，赢了的人从"起床"开始声明一个行动，只要大家都觉得不太过分，就可以继续。一般，游戏场面会从"大家在商店买东西"很快演化为"太阳系公社在银河帝国内发动政变"再到"A死于因果链抹杀"，只要脑洞大，能博得满堂彩，无论赢了输了都会让人心服口服。

毕竟是个体育课上自由活动时打发时间的游戏，一局玩完之后大家都一笑了之，输赢似乎不重要，而那些名场面才会被挂在嘴边好几天。

十多年过去了，后来，有一个叫AfterRain的人，就是坐在屏幕前正在写下这段话的人，某一天对着自己喜欢的角色发癫的时候，突然想到了这些古早的回忆。

这个游戏的底层是简陋的，但也正因为此，足够开放，能够容纳得下他对于他所热爱的那些故事、所喜欢的所有角色的所有的想法。商业游戏必然因为受众的限制和自己的底层逻辑而让精彩的、或许能体现人物弧光的设计为复杂度让路。而他没有这些限制。他没有美术和音乐资源、不可能制作与现代二游类似的交互前端，甚至用RPG maker做个前端都很费劲；但他可以随心所欲地改造底层、引入机制，只为了让那些角色以另一种姿态，在这个小小的时间里以他所期望的方式活下去。

因此，零python基础的他做了一件事：用AI，无论是claude、deepseek处理文本和归纳思路，还是Devin/deepwiki等code agent，把自己的思路和记忆重整化为具有一个世界观高包容度（类似圣杯战争/型月世界观）、底层足够简单和严谨，向外几乎可以无限延伸的类似minecraft的游戏平台，以MUD的形式呈现大逃杀类的游戏过程和胜利条件，再附带自己的想象力和设计，就成了起床战争。

开发时间很短。从这行字被写下来，追溯到他第一天知道什么是cherry studio，怎么接入API，只过去了不到三个月。

我是AfterRain.
这是一个一人开发的项目。
很遗憾的是，我并没有什么团队。我的朋友们是我虚构出来，让我觉得开发过程中不那么孤独的。你把开发日志中所有"我们"替换为"我"，就能看到真实的故事。
它的目的从一开始就不是作为同人企划赚钱或者在B站上发个视频让更多人看见，而是为一些我爱的角色建造一方我力所能及的、无人注视的小小空间。这就是为什么你只能在Github上看到这段话。
如果你也深爱着这些角色，如果你也想让虚拟存在远离社区的戾气和无边际的争吵，
那这个项目是为你准备的。
如果你只是好奇"一个人凭借AI agent或者能做到什么程度"，
那我希望这个游戏不会让你失望。

---

## 概述

本项目是一个适用于自制演出向大逃杀类桌面游戏《起闯战争 Badtime_war》（也称起床战争，与minecraft同名玩法无关）的电子DM，在大部分时间内可实现全自动裁决和记录游戏状态。

也可在某些需要人工判定的场合为人类DM提供裁决帮助。总的来说，它接过了桌游中大部分人类DM的权能。

在 Ver1.94 前可以将它视作纯热座式的命令行游戏。Ver1.94 中加入了 CLI 和 TUI 支持的联机模式，支持远程玩家、BasicAI 和 RL 的接入，同时允许接入 LLM 模型使 AI 对手可以与你交流（实验性）。

废话少叙，祝玩得开心。

---

## 快速开始

```bash
# 本地热座
python main.py

# 联机模式（房主）
python main_server.py --players 3

# 联机模式（客户端）
python main_client.py --host <服务器IP> --name <你的名字>

# 全AI胜率统计
python stats_runner.py --players 6 --games 5000
```

---

## 文档导航

| 文档 | 内容 |
|------|------|
| [完全游玩手册](docs/完全游玩手册.md) | 完整游戏规则（人类阅读，含闭合补丁、FAQ、开发日志） |
| [天赋参考](docs/talents.md) | 14 个天赋的机械规格（以代码为准，AI 可读） |
| [使用说明](docs/usage.md) | CLI 参数详解、RL 训练管线、联机配置、LLM 聊天、AIRI 接入 |
| [指令表](docs/commands.md) | 电子裁决系统支持的所有指令及星野战术宏子系统 |
| [更新日志](docs/changelog.md) | 版本历史（含开发日志与胜率统计） |
| [AIRI 接入](docs/airi_bridge.md) | AIRI Bot 的三种接入模式详细配置 |

---

## 依赖

- **基础游戏**（`main.py`）：仅需 Python 3.8+ 标准库
- **联机模式**（`main_server.py` / `main_client.py`）：可选安装 `textual`（`pip install textual`）
- **LLM 聊天**：可选安装 `openai` 或 `requests`（Ollama）
- **RL 训练**：需要 `torch`、`stable-baselines3`、`sb3-contrib`、`gymnasium`、`numpy`

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
engine/         — 游戏引擎核心（GameState, RoundManager, ActionTurnManager）
models/         — 数据模型（Player, Equipment, Markers, PoliceData）
actions/        — 玩家行动（每个文件导出 execute() 函数）
talents/        — 天赋系统，所有天赋继承 BaseTalent
cli/            — 命令行解析（parser.py）与验证（validator.py）
controllers/    — 玩家控制器（Human, BasicAI, RL, Network）
combat/         — 战斗系统（damage_resolver.py）
ai_chat/        — LLM 聊天集成（可选模块）
rl/             — 强化学习训练管线（可选模块）
data/           — prompts.json（所有面向用户的文本模板）
config/         — 配置文件
docs/           — 文档
```
