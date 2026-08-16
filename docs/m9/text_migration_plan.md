# M9 文本迁移计划（hardcoded → data/prompts.json）

> 状态：已完成（P0–P8 全部落地；治理测试已转正；500 局风洞验收通过）。
> 产物：`docs/m9/text_inventory.json`、`docs/m9/text_allowlist.json`、
> `tests/test_m9_text_governance.py`。

## 1. 目标

M9-rfc **全部代码路径**的用户可见中文迁入 `data/prompts.json` 的 `m9`
命名空间：人玩通道 + AI 决策通道都要迁，避免两条通道文案语义漂移。
JSON 是唯一信源；调用点不保留中文 fallback；缺键显示
`[Missing: m9.<key>]` 并由治理测试兜底。

## 2. 边界

| 迁移 | 不迁移（冻结） |
|---|---|
| `engine/m9/**` 全部用户可见字符串 | network / TUI 联机 |
| 共享文件的 M9 分支（`engine/action_turn.py` 等） | RL（`rl/**`） |
| `cli/m9_ui.py`、`controllers/human.py`、`main.py` 的 M9 文本 | AIRI / ai_chat |
| AI 决策通道中 M9 相关字符串（t0/c_policy、m9_adapters、minds 等） | legacy/v2exp 路径文本 |
| 身份键只入白名单，不删不改语义 | 英文调试日志 |

共享游戏词汇（地点/物品/武器/命令词）是跨 profile 的 command 词表，
不是 M9 独有文本；本期只登记身份键白名单，不搬词表。

## 3. 精度

- 一个可见字符串 = 一个 JSON 键；包括 `get_t0_option` 的 name/description、
  `execute_t0` 内每一层 `choose(prompt, options)` 的 prompt 与 option 标签、
  返回给引擎的 `(msg, ok)`、`describe_status()`、CLI 横幅/菜单/帮助、
  AI 决策里输出/回显给玩家的字符串。
- 例：G2 光影双身 T0 → `m9.talents.g2.t0.name`、
  `m9.talents.g2.t0.option_create_improvise`、
  `m9.talents.g2.t0.prompt_choose_create` 等。
- 嵌套 f-string 在调用点算好数值再传参；JSON 模板只含 `{placeholder}`。

## 4. 键命名

`m9.<子系统>.<模块>.<键>`

- 子系统：`ui`、`action_system`、`combat`、`resolution`、`police`、`arc`、
  `pp`、`aids`、`executor`、`talents`、`ai`；
- 天赋模块：`talents.t1` … `talents.g7`、`talents.poems`。

## 5. 执行批次

| 批 | 内容 |
|---|---|
| P0 | `engine/m9/text.py`、`m9` 骨架、盘点工具、治理测试（进行中） |
| P1 | AST 盘点 → `docs/m9/text_inventory.json` + `text_allowlist.json` 人工分类 |
| P2 | `cli/m9_ui.py` 剩余硬编码 → `m9.ui.*` |
| P3 | `engine/m9` 核心层：combat / police / arc / pp / aids / executor / g3_chain |
| P4 | 原初天赋 t1/t2/t3/t4/t6/t7 |
| P5 | 神代天赋 g0–g7 + poems（含 G2 光影双身全部 T0/影身/终曲文案） |
| P6 | 共享文件 M9 分支：action_turn / round_manager / action_enumerator /
       game_setup / special_op |
| P7 | main / human / cli / AI 决策通道 M9 字符串 |
| P8 | 启用 strict 治理 + 全量验收 |

每批遵守「单次修改不超过 5 个文件」；prompts.json 按子系统分批追加。

## 6. 治理与验收

`tests/test_m9_text_governance.py`：

1. `m9` 命名空间存在且 JSON 合法；
2. 代码引用的每个 `m9_text(...)` / `get_prompt("m9", ...)` 键都存在；
3. Phase 8 启用 `M9_TEXT_STRICT=1`：M9 专有文件除 `text_allowlist.json`
   白名单身份键外不允许 CJK 字面量；
4. G2 光影双身 T0 文案专项回归 + 关键场景渲染快照。

运行时验收：全量 pytest → `tools/m9_rfc_smoke.py`（8/8）→
`stats_runner.py --profile m9-rfc --players 6 --games 100` 冒烟 →
5000 局确认 0 崩溃、机制无回归。
