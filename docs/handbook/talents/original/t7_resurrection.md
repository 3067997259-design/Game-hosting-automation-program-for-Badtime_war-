---
doc_id: talents.t7
status: candidate
profile: v2exp
canonical_for: ["rules.talents.t7"]
requires: ["talents.overview", "core.scoring_afterlife"]
topics: ["talents", "t7", "resurrection"]
source_body_sha256: 9e5844e2399e80541f77746c3935fe4cf984b3fa12e3d9a32f117a4f8e95ebde
---
#### 7.「死者苏生」（原初）
一句话概括：以自己或对手墓地的一只怪兽为对象可以发动。什么？你说这是游戏王？这名字不就是从游戏王搬过来的？好吧，其实它就是个简单的复活而已
V2.0 已经有了往世层，所以复活不再承担“让死者别干坐”的唯一职责。它现在更像是一张提前埋下去的保险。我们也在想是否能允许它直接复活坠入往世层的玩家——诶，有个好东西叫献予彼岸之诗。
- 全局 1 次 / 需先在魔法所连续学习 2 回合 / 习得后消耗 1 回合"挂载" / 目标：任一玩家（含自己）。
- **效果**：被挂载者死亡时——保留全部物品、在自己家中重生、破损护盾（魔法护盾/AT力场）恢复展开、
  下个行动回合免起床；复活生命值 ⟦bal:talents.t7.revive_hp⟧。结算完成后本天赋永久失效。
- 边界：全场最多 1 名挂载目标；免死优先于复活结算（若被免死则不触发）。
