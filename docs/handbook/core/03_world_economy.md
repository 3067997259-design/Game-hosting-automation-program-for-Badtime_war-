---
doc_id: core.world_economy
status: candidate
profile: v2exp
canonical_for: ["rules.locations", "rules.items", "rules.economy"]
requires: ["core.rounds_actions"]
topics: ["locations", "items", "economy", "credits", "bow", "hook"]
source_body_sha256: 6f18a6f43476a242441ca8d73e14f9de76d9df6f41e007e4fc3840c21d65818d
---
## 六、地点与物品
### 6.1 各地点供应
| 地点 | 主要供应 |
|---|---|
| 家（home） | 起始包（弓+箭、小刀/盾牌拾取资格）；**不产经济资源** |
| 商店 | 打工、小刀、磨刀石、陶瓷护甲、隐身衣、热成像仪、防毒面具、**力量模块**、修甲、补给箭矢 |
| 魔法所 | 法术（魔法护盾/魔法弹幕/封闭/地震/隐身术/探测魔法等，部分需多回合学习）、**火矢/魔导模块** |
| 医院 | 打工、手术（晶化皮肤/额外心脏/不老泉）、治疗、防毒面具、**无限模块**、释放病毒 |
| 军事基地 | 通行证、AT 力场、电磁步枪、高斯步枪、雷达、隐形涂层、**穿甲/冲击模块**、**钩索** |
| 警察局 | 警察相关（见第十章） |
> 导弹（及其控制权三步流程）在 V2.0 中**已退役**，其攻城/反龟缩职责移交钩索。
### 6.2 弓与升级模块系统
远程三层架构：**弓（人手一份）→ 钩索（神器）→ G7 荷鲁斯之眼（天赋顶点）**。
**弓**：起始装备，裸伤 ⟦bal:bow.damage⟧，普通属性（基础箭），跨地点。箭初始 ⟦bal:bow.initial_arrows⟧、
上限 ⟦bal:bow.max_arrows⟧；补给 ⟦bal:economy.arrow_supply_amount⟧ 支 / ⟦bal:economy.sinks.箭矢补给⟧ 信用点。
**升级模块**：弓有**双槽**；每种全图 **×⟦bal:bow_modules.力量.supply⟧ 份**（份数稀缺，抢完即止，死亡回流）；
**同名双装强化**；装卸为 1 行动、随处可做。
| 模块 | 来源 | 单装 | 双装 |
|---|---|---|---|
| 力量 | 商店 | 裸伤 +⟦bal:bow_modules.力量.damage_bonus⟧ | +⟦bal:bow_modules.力量.damage_bonus_x2⟧ |
| 火矢 | 魔法所 | 属性→魔法，+⟦bal:bow_modules.火矢.burn_stacks⟧ 灼烧 | +⟦bal:bow_modules.火矢.burn_stacks_x2⟧ 灼烧 |
| 魔导 | 魔法所 | 命中 +⟦bal:bow_modules.魔导.hit_bonus⟧ | +⟦bal:bow_modules.魔导.hit_bonus_x2⟧ |
| 穿甲 | 军事基地 | 属性→科技，防御按 ⟦bal:bow_modules.穿甲.armor_pierce⟧ 计 | 防御按 ⟦bal:bow_modules.穿甲.armor_pierce_x2⟧ 计 |
| 冲击 | 军事基地 | 同地点命中→击退至随机地点 | 击退 + 目标下轮先攻 ⟦bal:bow_modules.冲击.knockback_x2_initiative⟧ |
| 无限 | 医院 | **神器×1，独占双槽**：不耗箭，恒为基础普通箭（白板 ⟦bal:bow.damage⟧ 伤） | 不可叠 |
> 例：你装了**双力量模块**，弓裸伤 ⟦bal:bow.damage⟧ → ⟦bal:bow.damage⟧+⟦bal:bow_modules.力量.damage_bonus_x2⟧；
> 但占满双槽，就没法再带穿甲/火矢——配置取舍是博弈的一部分。
### 6.3 钩索
军事基地的神器（需通行证），全图唯一。用法见 5.8。它继承了旧导弹的"攻城/反龟缩"职责——
把龟缩者拖出家门、拽回逃兵、又或者直接追着残血打。
### 6.4 手术 / 法术 / 学习（多回合）
部分项目需跨多个回合完成，进度保留：魔法所的法术学习、医院的手术（终身一次，见第七章财产税）等。
---
## 七、信用点经济
V2.0 废除旧"购买凭证"二元经济，改用**信用点**（统一通货）。设计目标：消灭龟缩——
让防御与苟活都背上**经常性开支**。
### 7.1 收入（faucets）
- **打工**：在商店/医院打工，+⟦bal:economy.faucets.打工⟧ 信用点 / 次。
- **击杀夺财**：击杀掉落含死者全部信用点（见 7.4）。
- **禁止被动收入**：没有"每轮自动 +X"，家也不产出——**收入必须出门挣**。
### 7.2 支出（sinks）
| 项目 | 信用点 |
|---|---|
| 小刀 | ⟦bal:economy.sinks.小刀⟧ |
| 磨刀石 | ⟦bal:economy.sinks.磨刀石⟧ |
| 陶瓷护甲 | ⟦bal:economy.sinks.陶瓷护甲⟧ |
| 隐身衣 / 热成像仪 | ⟦bal:economy.sinks.隐身衣⟧ |
| 防毒面具 | ⟦bal:economy.sinks.防毒面具⟧ |
| 修理陶瓷护甲（+⟦bal:armor.陶瓷护甲.repair_amount⟧ 耐久） | ⟦bal:economy.sinks.修理陶瓷护甲⟧ |
| 补给箭矢（⟦bal:economy.arrow_supply_amount⟧ 支） | ⟦bal:economy.sinks.箭矢补给⟧ |
| 力量模块 / 无限模块 | ⟦bal:economy.sinks.力量模块⟧ / ⟦bal:economy.sinks.无限模块⟧ |
| 钩索 | ⟦bal:economy.sinks.钩索⟧ |
| 手术（财产税） | **全部信用点**（下限 ⟦bal:economy.surgery_min_cost⟧） |
### 7.3 经济战与财产税
- **修甲收费 = 攻击变经济战**：对龟壳的每一刀都在磨它的护甲耐久、逼它花钱修——
  打不死你，但能打到你破产、发育归零。防御第一次有了经常性开支。
- **手术 = 财产税**：消耗你**全部**信用点（下限 ⟦bal:economy.surgery_min_cost⟧）换永久身体改造（内甲），倾家荡产。
> 例：你反复 `射箭` 磨对面陶瓷护甲耐久；他被迫每隔几轮回商店花
> ⟦bal:economy.sinks.修理陶瓷护甲⟧ 信用点修甲，打工收入全填进去，攒不出第二件装备。
### 7.4 击杀掉落
进入白昼阶段后启用（见第十一章）：死者的**装备、模块、剩余箭矢、钱包**全部掉落原地，1 行动拾取。
稀缺品（模块/神器）经击杀回流场地——杀死那个爱玩弓人就有可能拿到他的无限模块。
