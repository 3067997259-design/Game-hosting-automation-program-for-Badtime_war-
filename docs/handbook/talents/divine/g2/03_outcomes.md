---
doc_id: talents.g2.outcomes
status: candidate
profile: v2exp
canonical_for: ["rules.talents.g2.outcomes"]
requires: ["talents.g2.overview"]
topics: ["talents", "g2", "outcomes", "curtain"]
source_body_sha256: 6a23ed41b1f00d9d13bc64fcc4af2c887293edc1053fc396cfdace20a7ccf521
---
**七、阵营结局与奖励**
每个 R4 检查阵营胜利条件：
| 结局 | 触发条件 | 奖励 |
|------|---------|------|
| Acc 狂热终幕 | 无 Str 存活 + Acc 存活 | Acc 全员 D4/D6+1，下次伤害 +0.5 |
| Str 撕幕终幕 | 无 Acc 存活 + Str 存活；或破幕 | Str 全员 D4/D6+1，清除自身标记；破幕者额外 D4+1 |
| 完整谢幕 (Ind) | 持续 8 R4 + Acc/Str 均存活 | Ind 全员 D4/D6+1，恢复 0.5 HP；G2 隐身 + 按 Chorus 存活率追加奖励 |
| 静默终幕 | Acc 和 Str 同时不存在 + Ind 存在 | Ind 全员 D4+1；G2 隐身 |
| 空场退场 | 无真实非 G2 玩家存活 | 无奖励；G2 隐身 |
---
**八、破幕**
只有 **Strappando 真实玩家**可完成破幕。方式：
1. 移动到 G2 家 + attack 造成致命伤害（含属性克制结算）
2. 使用后台通行证生成 G2 投影 + attack 投影
破幕时 G2 不死亡（HP 恢复攻击前数值），结界以 `break` 理由结束，Str 阵营胜利，破幕者额外 D4+1。
Chorus 不能破幕——其攻击使 G2 HP 降至 `max(0.5, 攻击前)`，Regard -1。
---
**九、其他机制**
- **离场**：`move home_{pid}`。有安可层数时失败（消耗回合，安可 -1）。离场后清除舞台状态。
- **聚光灯**：Soave / Sognando 授予，持续至下个 R4。提供额外行动回合、临时 HP/ATK。
- **安可**：阻止离场，逐层消耗。
- **G2 投影**：后台通行证生成。攻击投影 = 攻击 G2（可破幕 / Regard -1）。
- **Embrace（相拥伤害）**：ish-bosheth 内所有单位造成伤害时自动标记为 Embrace，绕过天赋特殊防护但不绕过普通护甲。`[草案计划：安定値成熟后考虑移除]`
---
