# M9 评分系统指针与延伸 RFC v0.1

> **当前 M9 评分指针。** 评分主合同与投注/魂援机制见
> [`m9_pp_afterlife_betting_rfc_v0.4.md`](m9_pp_afterlife_betting_rfc_v0.4.md)（当前版本）；
> 本文件是评分侧的**当前指针与延伸**，不重复投注与援助规则，不随 B4 版本号联动（保持 v0.1）。
> **剧情分（arc）通道已升级**：`arc_count` 的章节化计分、全员上限与登台优先以
> [`m9_arc_universal_rfc_v0.1.md`](m9_arc_universal_rfc_v0.1.md) 为当前合同；
> 本文件 §五 的“G2/G5 私有挂接”自 2026-08-14 起按该合同并入章节表解释。
> 总览与阅读路径见 [`../README.md`](../README.md)。
>
> **日期**：2026-08-10
> **状态**：用户批准方向下的当前评分候选；数值待风洞
>
> **2026-09 风洞校准修正（实施口径，取代 §二/§三中的存活系数写法）**：
> `base_final_score = (剧情分 + 战果分 + 剩余PP + 名次加成) × 槽位得分系数`；
> 死者再额外 `+ 援助收益` 后乘系数（见 §3.1 四步求值与
> [`m9_windtunnel_calibration_2026-09.md`](m9_windtunnel_calibration_2026-09.md)）。
> 槽位得分系数表位于 `m9_system.scoring_m9.talent_score_multiplier`，
> 只在终分判定时乘算，不影响任何玩法动作。当前权重：剧情 1.8/章、击杀 1.5、
> 伤害 0.05、名次 2/档、唯一生还者 +4。
> **Profile**：`m9-rfc`
> **上游**：[行动系统 RFC v0.8](m9_action_system_rfc_v0.8.md)、
> [结算合同 RFC v0.3](m9_resolution_contract_rfc_v0.3.md)、
> [PP、往世层投注与魂援系统 RFC v0.4](m9_pp_afterlife_betting_rfc_v0.4.md)、
> [G0 砂狼白子 RFC v0.3](m9_g0_shiroko_terror_rfc_v0.3.md)、
> [G2 光影双身与世末终曲 RFC v0.3](m9_g2_holographic_presence_rfc_v0.3.md)、
> [G5 轮回培养与锚定脚本 RFC v0.4](m9_g5_anchor_contract_rfc_v0.4.md)
> **不改写**：`v2exp` 当前 V2.0 玩家手册

---

## 一、评分主合同（指向 B4 v0.4）

生者/死者终分公式以 [B4 RFC v0.4](m9_pp_afterlife_betting_rfc_v0.4.md) §六与 §3/§4 为准。
本文件只登记评分侧指针与延伸，不复制投注/援助规则。

## 二、生者终分

```
生者终分 = (剧情分 + 战果分) × 存活系数 + PP
```

| 项 | 计算 |
|---|---|
| 剧情分 | 完结条进展阶段数 × ⟦bal:m9_system.scoring_m9.arc_weight⟧（暂定权重） |
| 战果分 | 击杀数 × ⟦bal:m9_system.scoring_m9.kill_weight⟧ + 总伤害 × ⟦bal:m9_system.scoring_m9.damage_weight⟧（暂定权重） |
| 存活系数 | 存活：1.5 / 死亡：0.5 |
| PP | 剩余 PP 直接加入终分 |

## 三、死者终分

```
死者终分 = 剩余 PP + 赌注收益 + 援助收益
```

| 项 | 计算 |
|---|---|
| 剩余 PP | 本局结束时的 PP 值 |
| 赌注收益 | 被押者胜利（最终游戏胜者，可并列）：各 tranche 本金 × 赔率；被押者死亡：tranche 销毁（见 B4 v0.4 §四） |
| 援助收益 | 每次援助获得的 PP 之和（主动转移 + 被动固定奖励） |

### 3.1 终局胜者快照与显示终分（2026-08-10 裁决）

为避免“先靠赌注赢、又因为赢而派彩”的循环，终局严格按以下顺序结算：

1. 为所有玩家计算 `base_final_score`：生者、G0/G5 retreat 仍用各自生者公式；普通死者使用
   `剩余 PP + 援助收益`。所有身份在本阶段都**排除投注派彩和黑马胜利加成**；
2. 取最高 `base_final_score` 的全部玩家冻结为 `game_winner_snapshot`，同分者并列胜出；
3. 快照冻结后锁市。普通死者按其押注对象是否位于快照中结算各 tranche；位于快照中的黑马
   取得 `blackhorse_bonus`；
4. `display_final_score = base_final_score + 投注派彩 + 黑马胜利加成`。该值只用于终局展示与
   排名，不再次求胜者；即使显示排名发生变化，`game_winner_snapshot` 也不变。

## 四、G0 撤退（retreat）评分与状态

G0 调整呼吸失败退场是"撤退"（retreat），既不是存活也不是死亡。其状态与评分由
[G0 砂狼白子 RFC v0.3](m9_g0_shiroko_terror_rfc_v0.3.md) §7.3 / §九定义，本文件登记评分口径：

- 不能行动、不能被指定为目标、不触发 T7、不进往世层（不能投注、不能提供魂援/援助）
- **PP 冻结**：不衰减、不可支出，直接带入终分
- 装备掉落为遗留
- 终分按**生者公式**计算，但**存活系数取 0.5**：

```
G0 撤退终分 = (剧情分 + 战果分) × 0.5 + PP（冻结）
```

- 计入 G7 色彩"玩家出局"（+2，非击杀者无额外 +2）

### 4.1 G5 因果闭合

G5 的 `CAUSAL_CLOSURE` 同样使用 retreat 状态与上述 0.5 生者公式：不能行动或被指定，
不触发死亡/T7、不进往世层，装备掉落、PP 冻结，并计入 G7 色彩“玩家出局”。小昔涟的
`CYRENE_HOMECOMING` 只是形态转折，不是 retreat，也不在发生时结算终分。

## 五、水晶花评分挂接（G5 v0.4）

一朵水晶花只登记 **`arc_count +1`**，在剧情分中贡献
`1 × ⟦bal:m9_system.scoring_m9.arc_weight⟧`；
第一版不提供战斗强化或追忆折扣。水晶花的获得条件与回溯边界由
[G5 轮回培养与锚定脚本 RFC v0.4](m9_g5_anchor_contract_rfc_v0.4.md) 定义，本文件只负责评分侧挂接。

G5 本局第二次“未来闭合”另完成一次性完结条，登记
`arc_count +⟦bal:m9_talents_extended.g5.double_anchor_arc_count⟧`；它与两次锚定各自授予的
水晶花分开计算，并按 B4 §3.2 生成一次“完结条进展”PP。M9 不读取旧
`finale.g5_double_anchor` 裸分键。

### 5.1 G2 终曲被听见

G2 首次成立 `g2_last_song_heard` 时登记
`arc_count +⟦bal:m9_talents_extended.g2.terminal_arc_count⟧`，并按 B4 §3.2 生成一次
“完结条进展”PP。影身被击溃不算玩家死亡或击杀，终曲共享伤害沿原始来源计入战果；
精确听众 tick 与一次性边界服从 G2 v0.3 §十。

## 六、黑马胜利加成

无任何死者押注的生者（黑马）位于 `game_winner_snapshot` 时，显示终分额外
+⟦bal:m9_system.pp.blackhorse_bonus⟧（暂定 +10，见 B4 v0.4 §4.3）。该加成不参与
`base_final_score`，也不反向改变胜者快照。

---

## 七、与各 RFC 的评分调用点登记

| RFC | 调用点 | 评分延伸 |
|---|---|---|
| G0 砂狼白子 | 撤退终分（存活系数 0.5）、遗物追忆池 | 见本文件 §四 |
| G2 光影双身 | `g2_last_song_heard` 的 arc 与 PP；影身败退无玩家击杀 | 见本文件 §5.1 |
| G5 轮回与锚定 | 因果闭合 retreat；水晶花；第二次未来闭合完结条 | 见本文件 §4.1/§五 |
| G7 战术压制 | 色彩反转出局计数 | 击杀数/战果分按 B4 v0.4 |
| B4 投注/魂援 | 赌注收益、援助收益 | 见本文件 §二/§三 |
