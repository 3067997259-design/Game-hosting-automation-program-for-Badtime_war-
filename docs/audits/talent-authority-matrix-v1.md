# 天赋权威矩阵 V1

> 本表用于定位，不宣称模块内容已经通过语义审定。玩家模块当前统一为 `candidate`。

| ID | 玩家模块 | 主要实现入口 | 初步状态 |
|---|---|---|---|
| T1 | `handbook/talents/original/t1_one_slash.md` | `talents/t1_one_slash.py` | 待逐项核对数值与无视范围 |
| T2 | `handbook/talents/original/t2_scissors.md` | `talents/t2_scissor_rush.py` | 名称和多个融合子效果需核对 |
| T3 | `handbook/talents/original/t3_tianxing.md` | `talents/t3_star.py` | AOE、穿甲、石化需按 V2 profile 核对 |
| T4 | `handbook/talents/original/t4_six_yao.md` | `talents/t4_hexagram.py` | 已确认存在 4/5/6/9 轮多口径 |
| T5 | `handbook/talents/original/t5_combo.md` | `talents/t5_combo.py` | 注册简介仍是旧连续行动版 |
| T6 | `handbook/talents/original/t6_citizen.md` | `talents/t6_good_citizen.py` | 与警察分级坠落共同审计 |
| T7 | `handbook/talents/original/t7_resurrection.md` | `talents/t7_resurrection.py` | 复活、保留装备与往世层交互待核对 |
| G1 | `handbook/talents/divine/g1_firefly.md` | `talents/g1_firefly.py` | hp20、灼烧、超新星链待核对 |
| G2 | `handbook/talents/divine/g2/` | `talents/g2_hologram.py`、`engine/ish_bosheth.py`、物料牌与歌曲子系统 | 文稿最大；存在草案缺失和未接线机制 |
| G3 | `handbook/talents/divine/g3_mythland.md` | `talents/g3_mythland.py` | 草案“即时决斗”与现行结界口径需区分 |
| G4 | `handbook/talents/divine/g4_ember.md` | `talents/g4_savior.py` | 火种、完结条和死亡结算待核对 |
| G5 | `handbook/talents/divine/g5/` | `talents/g5_ripple.py`、`talents/g5/` | 方式二明确未完成信源统一 |
| G6 | `handbook/talents/divine/g6_laughter.md` | `talents/g6_cutaway.py` | 借用行动的收益/责任归属待核对 |
| G7 | `handbook/talents/divine/g7/` | `talents/g7_hoshino.py`、`talents/g7/` | 战术、Cost、Terror 分模块审计 |

## 审计模板

每个天赋转为 `canonical` 前必须完成：

1. 玩家叙述与代码入口逐项对应；
2. 所有数值来自 `balance.json` 或明确记录例外；
3. legacy 与 v2exp 分支分别记录；
4. 注册简介、帮助文本和 prompts 与正式规则一致；
5. AI、RL 和网络可见状态的支持程度已登记；
6. 对应测试和最小运行时烟雾通过。

