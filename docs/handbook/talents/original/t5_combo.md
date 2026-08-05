---
doc_id: talents.t5
status: candidate
profile: v2exp
canonical_for: ["rules.talents.t5"]
requires: ["talents.overview", "core.combat"]
topics: ["talents", "t5", "combo"]
source_body_sha256: 92c911018b05e8064f81797f2e31a0e823d55a4d2d08d9f505d9b6fde07863e9
---
#### 5.「combo」（原初）
「锁屏可打」
「已退出锁屏可打大队」
「好耶！感谢奥托普雷先生帮我又拿下一首 FC」
元老级天赋，只比天星要早。旧版 combo 是很朴素的设计：连续行动三轮，系统就给你奖励关。这说明大部分几千字的天赋，其强度有时还不如“我就是一直动”这种非常不讲道理的东西
V2.0 以后它终于回到了“音游”的本义：不是奖励你乱按，而是奖励你在公开谱面上按拍做对动作。神代天赋可以开演唱会，原初天赋当然也可以打谱面；区别只是她们唱的是世界，你打的是判定线。
- 无次数上限 / 自动 / 无成本 / 目标自己。
**音游谱面**：每隔 ⟦bal:talents.t5.chart_cadence_rounds⟧ 轮发一张有序谱面；谱面由 1-3 个音符组成，每个音符写作“动作大类 @ 拍点轮”，并提前公开。基础大类为移动 / 攻击 / 交互 / 特殊；若你具备可用警务行动，谱面池会加入警务。起床、锁定、寻找、弃权、状态查看等不算音符。
- **判定**：拍点当轮做对该大类 = Perfect；提前或滞后 1 轮 = Good；窗口过去仍未完成 = Miss。一次行动至多判定一个音符，且只看行动大类，不看这次攻击有没有命中、交互有没有摸到好东西。
- **结算**：全 Perfect = FC（全连）；0 Miss = Clear；有命中也有 Miss = 残谱；全 Miss = 无。残谱只留下剧情分/记录价值，全 Miss 没有额外惩罚。
- **手感火热**：FC / Clear 会回血并获得临时攻击加成。
FC：攻击 +⟦bal:talents.t5.fever_atk_fc⟧、回血 +⟦bal:talents.t5.heal_fc⟧、持续 ⟦bal:talents.t5.fever_duration_fc⟧ 轮，并额外获得 1 次追加行动；
Clear：攻击 +⟦bal:talents.t5.fever_atk_clear⟧、回血 +⟦bal:talents.t5.heal_clear⟧、持续 ⟦bal:talents.t5.fever_duration_clear⟧ 轮。
- **涟漪联动**：献予「旋律」之诗会先保证你有谱面，再让你从谱面大类中选一个立刻行动，并把整张谱面判为 FC。简单说，昔涟把谱面递到你手里，还顺手替你把判定线调到了脸上。
