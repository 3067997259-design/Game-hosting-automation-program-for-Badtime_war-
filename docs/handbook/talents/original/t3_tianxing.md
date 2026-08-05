---
doc_id: talents.t3
status: candidate
profile: v2exp
canonical_for: ["rules.talents.t3"]
requires: ["talents.overview", "core.combat"]
topics: ["talents", "t3", "tianxing"]
source_body_sha256: 3df01003d71170e79407686c6d38d68857c6b60c5ab00861510116205d38285f
---
#### 3.「天星」（原初）
一句话概括：天理尝蛆。
元老级别的天赋中最年轻的一个。早期反制警察的对策卡，零帧起手的能力和石化伤控二选一在那个攻击成本高的年代算是猛攻哥最严厉的父亲之一。它的作用是让上头直接冲的人突然意识到：不是怎么老被砸啊
- 使用次数 2 / 行动回合开始发动 / 消耗回合 / 目标：你所在地点除你外的所有单位。
- **效果**：各造成 `⟦bal:talents.t3.aoe_base⟧ + 命中单位数`（上限 ⟦bal:talents.t3.aoe_cap⟧）伤害，
  穿甲（防御按 ⟦bal:talents.t3.armor_pierce⟧ 计），并施加**石化**。
- 边界：石化单位下回合二选一（解除/保持），被攻击则石化自动解除并额外受创。
