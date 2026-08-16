---
doc_id: core.scoring_afterlife
status: candidate
profile: v2exp
canonical_for: ["rules.scoring", "rules.applause", "rules.afterlife"]
requires: ["core.overview_setup"]
topics: ["scoring", "applause", "afterlife", "finale"]
source_body_sha256: c6bb3fed5e7def66e429a307d16f2786d43886cbd1f74e64071efc0e5e057adf
---
## 十二、评分 · 喝彩 · 往世层
### 12.1 终分总公式
```
终分 = （剧情分 + 喝彩净值 + 战果分） × 存活系数 + 往世分
```
- **存活系数**：终局存活 ×⟦bal:scoring.survival_alive⟧；中途死亡 ×max(⟦bal:scoring.survival_floor⟧, 存活轮数 / 总轮数)。
- **战果分**：每击杀 +⟦bal:scoring.kill_score⟧；每累计 ⟦bal:scoring.damage_per_point⟧ 点有效伤害 +1（上限 +⟦bal:scoring.damage_cap⟧）。
- 中盘只见组件、不见总分；精确终分**终局揭晓**。
### 12.2 喝彩（两用资源）
打出"名场面"可获喝彩点（**每类每局限 1 次**，反合谋）：
| 事件 | 喝彩 |
|---|---|
| 首杀 | +⟦bal:applause.first_kill⟧ |
| 重伤（HP≤⟦bal:hp20.severe_injury_threshold⟧）状态下反杀 | +⟦bal:applause.severe_revenge⟧ |
| 打破满配（3 外甲）目标的最后一件外甲 | +⟦bal:applause.break_full_armor⟧ |
| 用最后一支箭完成击杀 | +⟦bal:applause.last_arrow_kill⟧ |
| 在终焉阶段完成击杀 | +⟦bal:applause.apocalypse_kill⟧ |
喝彩是**两用资源**：余额计入终分，也可消耗（重掷先攻 / 单次裸伤 +2 / 偷看下轮先攻顺序 / 抵消一次犯罪记录 等）。
### 12.3 完结条（剧情分大额块）
达成叙事里程碑得大额剧情分（进度半公开）：
| 天赋 | 完结条 | 剧情分 |
|---|---|---|
| G2 注视 | 完成一次完整谢幕 | +⟦bal:finale.g2_curtain⟧ |
| G4 愿负世 | 救世主毕业（状态结束时存活，此后再存活 ⟦bal:finale.g4_survive_rounds⟧ 轮） | +⟦bal:finale.g4_graduation⟧ |
| G5 涟漪 | 完成 2 次成功锚定 | +⟦bal:finale.g5_double_anchor⟧ |
（试点 3 个，后续逐步为全部神代补完结条。）
### 12.4 往世层（死者成星）
被击杀者**坠入往世层、化作一颗星**（不出局）：
- 每轮获 ⟦bal:afterlife.starlight_per_round⟧ 点星光（上限 ⟦bal:afterlife.starlight_cap⟧）。
- **闪烁**（每轮 1 次，对同一玩家每 2 轮限 1 次）：
  - **诸神缄默不语**（⟦bal:afterlife.fate_cost⟧ 星光）：某玩家本轮先攻 ±1。
  - **预兆似有若无**（⟦bal:afterlife.omen_cost⟧ 星光）：在某地点投下预兆，下轮第一个交互者 ±2 HP。
  - **纷争加冕为王**（⟦bal:afterlife.coronation_cost⟧ 星光）：某玩家下一个喝彩事件分值 ×2。
- 闪烁产生**往世分**（×⟦bal:scoring.afterlife_mult⟧ 折算计入本人终分）。
- 星**不能直接造成伤害或击杀**；被锚定击杀者**不能成为星**。
- 正在考虑在后续Exp版本中让**往世的涟漪**和**死者苏生**与往世层产生交互
