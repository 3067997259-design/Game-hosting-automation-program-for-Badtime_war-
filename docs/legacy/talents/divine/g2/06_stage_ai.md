---
doc_id: legacy.g2_stage_ai
status: mixed
profile: legacy
canonical_for: ["implementation.legacy.talents.g2.stage_ai"]
requires: ["legacy.g2_reset_overview"]
topics: ["talents", "g2", "stage_ai", "implementation"]
source_body_sha256: f520a64e9d1b454ff4171631ad3f80738123cef2516e0b26ee7db861265c2933
---
### 神代天赋2·重置 (v0.6) —— StageAI 决策系统

v0.6 引入了统一的 StageAI 决策模块（`controllers/ai/stage/`），供 Chorus 和 BasicAI 在舞台模式（正常 / duet）下共用。

**架构**：纯静态方法，不存状态。`StageAI.get_command()` 作为主入口，检测舞台状态后分发到对应模块（duet_mode / normal_mode）。

#### T0 预评估（`assess()`）

在回合开始前预先计算局面，供 T0 选牌 + T1 指令复用：
- **Duet 模式**：评估博弈姿态（cooperate/compete/mixed）、按钮座位、手牌、武器
- **正常模式**：评估合法目标、目标威胁排序、手牌

#### T0 物料牌选择（`decide_t0()`）

基于评估结果选择最优牌：
- **Duet**：前排票（不在按钮旁）> 后台通行证（Regard 告急）> 荧光棒/聚光合影（在按钮旁）> 花束（队友受伤）> 场刊整理/反光板 > 和弦谱
- **正常**：荧光棒（有同座目标）> 前排票（有异座目标）> 花束（队友受伤）

#### T1 行动决策

- **Duet** (`decide_duet_action`)：合作态按钮优先、竞争态按钮 > PvP > move、混合态按武器强弱灵活切换
- **正常** (`decide_normal_action`)：遍历威胁排序找射程可达目标 → 最佳武器 attack → 无合法目标则 move（队友聚集 / 猎物聚集）→ forfeit

#### 换牌（`decide_trade()`）

T0 同座位寻找交易对象：
- 识别其他声部专属牌 → 优先换出
- 在交易对象中找匹配声部 → 换入对方手中的本声部专属牌或通用好牌
- 接受标准（`decide_trade_accept()`）：本声部专属牌直接接受；通用好牌 + 手上有垃圾时接受

#### 交互式决策（`choose()`）

处理舞台内的选择：duet 入口投票、歌曲投票、位移目的地选择、Embrace 拥抱选择、安可物品选择等。

> **注意**：StageAI 的 vote_duet_entry、vote_song、choose_displacement_target、decide_embrace 目前为 MVP 占位实现（简单启发式或默认选项），草案中的完整博弈推理待后续迭代。


