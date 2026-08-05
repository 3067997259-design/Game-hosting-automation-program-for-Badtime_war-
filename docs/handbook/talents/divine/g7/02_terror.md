---
doc_id: talents.g7.terror
status: candidate
profile: v2exp
canonical_for: ["rules.talents.g7.terror"]
requires: ["talents.g7.core", "core.combat"]
topics: ["talents", "g7", "hoshino", "terror"]
source_body_sha256: cbca2f95fbceeff96959edd4572342dfc5443a749eb87a9c6866d218b33ff716
---
自我怀疑状态下，你的下一次行动回合会被跳过；该回合结束时，你反转为 Terror。
Terror 状态
Terror 形态下，cost 显示为 Null，玩家名被覆盖为「星野-Terror」。
你失去所有战术指令、战术道具、药物、铁之荷鲁斯与光环，只允许执行 attack 和 move。
进入 Terror 时，你失去反转发生时的所有护甲与光环，并将它们折算为额外生命值：每件护甲 +⟦bal:talents.g7.terror_armor_per_piece⟧；铁之荷鲁斯按剩余耐久 / ⟦bal:talents.g7.terror_horus_divisor⟧ 销毁并进行额外生命值（HP）的折算；每层光环提供 +⟦bal:talents.g7.terror_halo_per_layer⟧。特殊情况修正：若额外生命值总额低于该值： ⟦bal:talents.g7.terror_hp_floor⟧，补正到该保底值。
Terror 攻击：固定且只能使用荷鲁斯之眼发起，对地图上除自己外所有玩家单位造成 ⟦bal:talents.g7.terror_attack_damage⟧ 点真实伤害（不享受任何伤害加成）；伤害结算后扣除 ⟦bal:talents.g7.terror_attack_cost⟧ 点 Terror 额外生命值。若这一击已经全歼场上其他玩家，则直接判定游戏结束不再扣除代价。
Terror 移动：移动完成后扣除 ⟦bal:talents.g7.terror_move_cost⟧ 点 Terror 额外生命值；不足以支付也可以移动，但归零后判定死亡。
Terror 攻击正常违法，但无法被举报；警察不会主动靠近或攻击 Terror，也会拒绝队长攻击Terror/移动警察到Terror所在地点的指令。
Terror 形态下，挂载在你身上的复活不会生效。你的生命值归零时，立刻无视任何条件死亡。
