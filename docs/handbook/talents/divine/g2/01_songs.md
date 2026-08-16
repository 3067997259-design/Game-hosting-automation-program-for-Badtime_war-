---
doc_id: talents.g2.songs
status: candidate
profile: v2exp
canonical_for: ["rules.talents.g2.songs"]
requires: ["talents.g2.overview", "core.combat"]
topics: ["talents", "g2", "songs", "melody"]
source_body_sha256: b027081daf6b93468a0bf006be6e2e102e2b39bd352a0a6e725b8845e0b06e9d
---
**五、歌曲系统**
3 首歌 6 个节奏。Soave / Placido / Riposato 消耗 1 Regard，Sognando / Zeffiroso / Dolente 消耗 2 Regard。G2 在 T0 演唱（special）。
**5.1 追寻那道光（选择演员）**
| 节奏 | 费 | 效果 |
|------|-----|------|
| Soave (温柔) | 1 | 聚光灯 + 摸 1 牌 + 可额外打 1 牌。Acc: +0.5 临时 ATK。Ind: 免费用 1 次换牌。Str: 手牌保持公开。 |
| Sognando (追寻) | 2 | 同上但摸 2 弃至手牌上限(3)。Chorus: G2 可指定其下一次攻击目标。 |
**5.2 拼接遗憾（修补物料与观众）**
| 节奏 | 费 | 效果 |
|------|-----|------|
| Placido (平静) | 1 | 目标 +0.5 临时 HP（Chorus 额外 +0.5）。目标可选 1 张手牌放牌堆底，重新摸 1 张。 |
| Zeffiroso (遗憾) | 2 | 选两名观众交换各 1 张牌。若有 Chorus: Regard +0.5。可复活 1 名死亡 Chorus（HP=1.0，分配到人数最少的声部）。 |
**5.3 Before light（改变本轮安定值计算规则）**
| 节奏 | 费 | 效果 |
|------|-----|------|
| Riposato (休息) | 1 | 设置 pivot=⟦bal:talents.g2.before_light_riposato_pivot⟧（更多人判定为低防 → 被奶）`[草案计划]` |
| Dolente (悲伤) | 2 | 设置 pivot=⟦bal:talents.g2.before_light_dolente_pivot⟧（几乎全员判定为高防 → 挨打）`[草案计划]` |
**5.4 旋律（累计 ΔRegard 解锁）**
通过 **累计 |ΔRegard|** 解锁。G2 激进或保守（保 Regard=累计靠 R4 自然增长）都能达到解锁阈值。
| 名称 | 累计解锁 | 使用次数 | base_dmg 序列 |
|------|---------|---------|--------------|
| 序曲 | 开幕免费 | 1 次（不计入 melody_1_used） | ⟦bal:talents.g2.melody_seq_1⟧ |
| 旋律·第一音节 | ≥ 3.0 | 1 次 | ⟦bal:talents.g2.melody_seq_1⟧ |
| 旋律·第二间章 | ≥ 7.0 | 1 次 | ⟦bal:talents.g2.melody_seq_2⟧ |
| 旋律·第三间章 | ≥ 11.0 | 1 次 | ⟦bal:talents.g2.melody_seq_3⟧ |
**目标选择**：G2 选 1-2 个座位，每个座位上最多命中 4 个目标（按衰减序列递减）。
**安定值（每目标独立计算）**：
```
安定值 = (base + armor_mod) × armor_mult × decay_factor
base = clamp(cumulative_delta_regard / ⟦bal:talents.g2.stability_base_divisor⟧ - 0.5, -0.5, +1.5)
total_defense（当前 hp20 口径下为装甲度）
= Σ(护甲主防+副防) + 总耐久/⟦bal:talents.g2.armor_rating_durability_divisor⟧ + 临时安定値偏移
Pivot（造成伤害或提供治疗的分界点） = ⟦bal:talents.g2.stability_pivot⟧（被 Riposato/Dolente 覆盖为 ⟦bal:talents.g2.before_light_riposato_pivot⟧/⟦bal:talents.g2.before_light_dolente_pivot⟧）
armor_mod = (total_defense - pivot) × ⟦bal:talents.g2.stability_armor_coeff⟧
```
安定値 > 0 → 增伤（最少 ⟦bal:talents.g2.melody_damage_floor⟧）。安定値 < 0 → 治疗。旋律命中后所有临时安定値标记即时清除。
**设计意图**：天然惩罚叠甲的重装单位（旋律暴击），鼓励轻甲敏捷单位参与演出（旋律治疗），对闪T（隐身失效）和盾T（高防高伤）都造成压力。
**声部特效**（命中且存活后触发）：
- **Acc — 狂热**：下次攻击 Str 伤害 +0.5
- **Ind — 回声**：摸 1 张物料牌
- **Str — 裂音**：Regard -0.25 + 下次攻击 G2 伤害 +0.5
---
