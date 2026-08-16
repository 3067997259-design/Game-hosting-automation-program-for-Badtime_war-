# M9 未接入内容综合审计 v1 —— 接线缺口台账

> **Profile**：`m9-rfc`
> **日期**：2026-08-12
> **状态**：接线缺口台账（实现侧）；不定义 `v2exp` 现行玩法；不改变已冻结的 M9 设计
> **输入**：`docs/m9/current/` 19 份合同、`docs/m9/README.md`、`docs/contradictions.md`、
> `docs/audits/m9-implementation-readiness-v0.1/v0.2.md`、`docs/audits/m9-contract-test-gap-ledger-v0.1.md`、
> `docs/audits/m9-b5-paper-simulation-v0.4.md`、`docs/ai/commands*.md`、`docs/design/v2exp/m8_basicai_refactor.md`，
> 对照 `engine/m9/**`、`engine/round_manager.py`、`combat/**`、`actions/**`、`controllers/**`、`tui/**`、`rl/**`、
> `data/balance.json`（v0.4.6）、`tests/test_m9_*.py`、`tools/m9_*.py` 逐项核对的五个并行审计结论
> **方法**：五个并行只读子代理分域审计（PP/往世层/评分、G0-G7 天赋、系统层合同、AI/指令/TUI/数值、测试/文档治理），
> 主代理抽查关键行号核实；证据一律精确到 `file:line`。
> **结论**：M9 引擎主链路（行动槽/ActionGrant/SP/公演队列、A-H 两阶段伤害、DIRECT_DAMAGE/absolute_dead、
> 14 天赋 adapter、诗篇 14 首、警察状态机主体、烟测/剧本验收）已大面积落地；但仍存在
> **1 组设计已取代未拆除的旧系统、9 条 P0 断线（机制存在但游戏内不可达）、4 处活桩（扣费无效果）、
> 1 组 B4 主机制（投注/魂援/PP 经济/评分）整体未接引擎、多组机制级缺口与语义偏差**，
> 以及 AI/指令/TUI/RL 四层零接入（独立算法设计轨道）。本文是接线工作的执行台账，
> 各条目关闭后应在「处理顺序」表中回填验证证据。

---

## 一、设计已取代、实现未拆除（运行中的旧系统）

| # | 内容 | 证据 | 差距类型 |
|---|---|---|---|
| 1.1 | **星光行动（拨弄命运/预兆/加冕）在 m9-rfc 局中仍在运行**——B4 v0.4 已把往世层重定义为"投资者 + 魂援提供者"（`m9_pp_afterlife_betting_rfc_v0.4.md` §一/§2.2），但 `m6_scoring` 仍在 `PROFILES["m9-rfc"]`，R4 `_process_starlight` 仍执行，死者仍被置星并做星光行动 | `engine/experiments.py:42`；`engine/round_manager.py:1051-1053, 920-924, 773-788`；`actions/starlight.py:30-99`；`models/player.py:142-144` | 未拆除 |
| 1.2 | **终局胜者仍是旧 M6 评分**（剧情+喝彩+战果×存活系数+往世分），评分指针 v0.1 的四步求值（`base_final_score` → `game_winner_snapshot` → 锁市 → 派彩 → 黑马加成）完全未接引擎 | `engine/round_manager.py:41-53`（`_finalize_winner` → `engine/scoring.py compute_all`）；`engine/game_state.py:83` | 未接线 |
| 1.3 | **旧喝彩系统在 m9-rfc 下全量生效**，与 B4 的 PP 消耗语义（重掷先攻/加伤/偷看先攻/抵消犯罪）并存冲突；星光副作用字段（`_star_fate_bonus`/`_coronation_active`/`omens`）仍被读取 | `engine/applause.py:24-103`；`actions/applause_spend.py:16-66`；`cli/validator.py:191-203`；`engine/round_manager.py:316-319` | 未拆除 |
| 1.4 | **legacy 警察指令仍可达**：`report/assemble/track/recruit/election/designate/study/police 子命令/wake_police` 全部仍解析、仍校验、仍可执行；裸串"竞选队长"仍被 legacy `election` 分支捕获，与 M9 `special 竞选队长` 双路径并存 | `cli/parser.py:106-186`；`cli/validator.py:612-796`；`engine/round_manager.py:15-16` | 未拆除 |
| 1.5 | **`talents.*` 旧数值块仍被 M9 代码经 v2exp 基类读取**（G1/G4 双轨数值并存），W11 命名空间迁移未完成 | `data/balance.json:264-293, 319-358`；`talents/g4_savior.py:180-192`；`talents/g1_firefly.py:271-274` | 双轨并存 |

## 二、P0 断线 —— 机制已实现但游戏内不可达 / 接线 bug

| # | 内容 | 证据 |
|---|---|---|
| 2.1 | **警察 R2 tick 永不可达**：`_phase_r2` 在 k_initiative 启用时无条件提前 return，而 m9-rfc 恒开 k_initiative → `m9_police.r2_tick`（lead 指派/重指派、队长候选上任）在游戏流内永不执行，R4 执法因无 lead 实际空转 | `engine/round_manager.py:368-411`；`engine/m9/police.py:252-306` |
| 2.2 | **警察局停机收尾未接游戏流**：G1 繁育摧毁警察局只调 legacy `police_engine.permanently_disable`，未调 `m9_police.shut_down()` → §3.4 停机收尾（关闭新举报、清通缉/队长/掩体、存活警察中立 NPC 化）永不发生 | `engine/m9/talents/g1.py:307-311`；`engine/m9/police.py:150-162` |
| 2.3 | **G5 追忆喂入断线**：`m9_on_combat_event`（`g5.py:206-222`）无任何引擎调用点 → `sealed_reminiscence` 恒 0 → 锚定与献诗"追忆不足"预检永远失败，**G5 核心玩法游戏内不可达** | `engine/m9/talents/g5.py:206-222` |
| 2.4 | **G5 献诗无 T0 入口**：`Ripple9.get_t0_option` 只返回 `g5_anchor`，14 首诗篇共享入口与简化标记兑换仅测试可达；微澜（W4：1 SP 信息型即演）完全未实现 | `engine/m9/talents/g5.py:228-241, 136-139` |
| 2.5 | **世界援助「昨日的同伴」零接线**：`WorldPoemAid` 的 `recompute`（黑马快照）/`should_followup_attack`（星野追演+震荡）/`can_heal_location`（绫音 R4 急救）全部无引擎调用方；黑马集合恒空 | `engine/m9/g0_world_poem.py:39-87`；`engine/m9/talents/g0.py:144-148` |
| 2.6 | **G7 即演入口死代码**：`m9_mark_improvise_exempt` 无调用者，"小准备"即演（下个 R0 豁免失却汇流成泉 + 免费一项补给）游戏内不可达 | `engine/m9/talents/g7.py:166-168`；`talents/g7/hoshino.py:271-273` |
| 2.7 | **绝对死亡者仍进往世层成星**（R4 M6 扫描不排除 `absolute_dead`）；且 G4 天裁/G7 Terror 致死不冻结 PP（`pp.freeze` 仅 G0 撤退/G5 闭合调用），结算合同 §6.8 边界泄漏 | `engine/round_manager.py:920-924`；`engine/m9/combat.py:505-565`；`engine/m9/pp.py:56-62`；`g4.py:352-363`；`g7.py:99-150` |
| 2.8 | **`m9_scoring` 未挂 gate**：`gate.py` 只挂 `m9_pp` 未挂 `m9_scoring` → G0/G5 撤退的 `mark_retreat`（0.5 生者公式）是死代码（访问恒 None 被 except 吞掉） | `engine/m9/gate.py:49-54`；`engine/m9/talents/g0.py:917-922`；`g5.py:177-183` |
| 2.9 | **G2/G5 AI 钩子显示名失配**：`controller.py` 钩子键（"请一直，注视着我"/"往世的涟漪"）与 M9 类 name（"神代天赋-请一直注视着我"/"神代天赋-往世的涟漪"）不匹配，两天赋钩子永不触发 | `controllers/ai/controller.py:158-168` vs `engine/m9/talents/g2.py:139` / `g5.py:113` |

## 三、活桩 —— 扣费/扣槽后无真实效果

| # | 内容 | 证据 |
|---|---|---|
| 3.1 | **G6 借用核心 4/6 是活桩**：`core_slash`（t1 无此方法）、`core_attack`（`t2.py:512` 有实现但从不派发）、`simple_projection`（`g3.py:983-1019` 有实现但从不派发）、`enhanced_basic`（g4 无此方法）——**已扣 2 SP + 公演位但零结算**，返回占位文案 | `engine/m9/talents/g6.py:288-291` |
| 3.2 | **G6 召唤往世层援助：26 项援助执行器全桩**，只返回占位文案，无提供者指派、无 `aid_passive_reward` 发放 | `engine/m9/talents/g6.py:354-363` |
| 3.3 | **律法诗（T6）配装分支消息桩**（结案/威信分支已实现） | `engine/m9/talents/poems.py:195` |
| 3.4 | **G6 欢愉双借用未实现**：`poem_joy_borrow_cores`（balance.json:573）是死键；`joy_extend=True` 永不过期（6-tick 到期未实现） | `engine/m9/talents/g6.py:197, 215, 244` |
| 3.5 | **`aid_rest`（T3 防御援助：绝对免疫改写下一槽）tracker 已实现但无任何天赋/流程触发** | `engine/m9/resolution.py:88-102`；`engine/m9/action_system.py:77,101` |

## 四、B4 往世层 / PP / 评分主机制 —— `engine/m9/pp.py` 全是骨架，引擎零调用

| # | 内容 | 证据 |
|---|---|---|
| 4.1 | 投注全链路未实现：金额/托管、同目标追加分层 tranche、转仓（2 PP、按当前赔率）、赔率（存活人数:1）、死目标 tranche 销毁、终局锁市、派彩 | `engine/m9/pp.py:73-82`（`place_bet` 简化桩）；`engine/round_manager.py:85-146`（R0 无开市钩子） |
| 4.2 | R0 开市窗口 / 交易时点：R0 无任何投注/锁市/黑马快照钩子；`recompute_blackhorse` 仅被 `WorldPoemAid.recompute` 引用，后者引擎零调用 | `engine/m9/pp.py:91-95`；`engine/m9/g0_world_poem.py:39-47` |
| 4.3 | 魂援：4 次援助额度簿记有但无执行器；主动援助交易（生者出价→广播→死者接受/拒绝→成交序：押注最多→最早死→ID）未实现；被动援助（首次攻击/首次濒死且往世层非空）无触发点 | `engine/m9/pp.py:32,37,110-117` |
| 4.4 | PP 经济闭环全断：`earn`（首杀/复仇/破甲/终焉/绝境/完结条）引擎零调用；`spend`（重掷先攻/加伤/偷看/抵消犯罪）零调用；`decay`（生者 R4 衰减、死者免衰减）零调用；旧喝彩 4 用途仍全权占据同语义槽位 | `engine/m9/pp.py:42-70`；`engine/action_turn.py:1844-1845, 2153-2154` |
| 4.5 | 评分公式与 RFC 不符：生者分量缺 `scoring_m9.arc_weight/kill_weight/damage_weight` 与存活系数 1.5；死者分量 `arc×2.0` 任意系数（应为剩余 PP + 赌注收益 + 援助收益）；`settle` 无派彩、无 `game_winner_snapshot` 写回、引擎零调用 | `engine/m9/pp.py:153-188`；`data/balance.json:425-431` |
| 4.6 | 水晶花计分通道漂移（W4）：`_grant_flower` 走 `pp.earn(crystal_flower_arc_count)` 给 PP 余额而非 `ScoringEngine.add_arc`；G2 终曲同用 `pp.earn(1)` 代 arc 登记 | `engine/m9/talents/g5.py:414-421`；`g2.py:299-306`；`engine/m9/pp.py:142-144` |
| 4.7 | 交易系统（死者间/生者间 PP 交易）完全未实现 | `engine/m9/pp.py`（全文无交易逻辑） |
| 4.8 | 黑马增益（`_blackhorse_atk/_blackhorse_def`）读入但无应用点；G6 公演召唤援助真实结算缺失 | `engine/m9/pp.py:33-34, 100-107`；`g6.py:354-363` |

## 五、机制级未实现 / 语义偏差

| # | 内容 | 证据 |
|---|---|---|
| 5.1 | G1 着装/卸甲宣言消费行动槽（RFC §2.0 明文"宣言本身不消费行动槽"） | `engine/m9/talents/g1.py:106, 125` |
| 5.2 | G1 完全燃烧受限追加未实现（`ActionGrant.kind` 无 `restricted_followup`；`_restricted_followup_round` 是死字段） | `engine/m9/action_system.py:46-61`；`g1.py:42` |
| 5.3 | G1 卸甲每轮一次免费 `find`、超击破（`break_bonus_damage` 未读）、超新星不挂灼烧（`supernova_burn` 未读）、完全燃烧窗口内非每轮自愈、繁育先攻加成（`propagation_initiative`）未实现 | `engine/m9/talents/g1.py:120-122, 138-147, 283-311`；`data/balance.json:456-458` |
| 5.4 | G1 地点摧毁兜底简化：硬编码回 `home`，无"最后安全地点兜底/就地回退"；警察局只 `permanently_disable` 未做停机联合收尾（同 2.2）；繁育"每局至多一次"无全局闸（复活后重复入窗口） | `engine/m9/talents/g1.py:313-322, 156-163` |
| 5.5 | G4 拉条期间救世主减伤、强化普攻获取毁伤（§2.2）未实现；快照先攻硬编码 0；`counter_total`/`judgment_per_segment=2.0` 硬编码占位（无 balance 键）；火种 +1 硬编码（`ember_gain_*` 键未读） | `engine/m9/talents/g4.py:126-139, 290, 304-305, 378-382` |
| 5.6 | G3 兵装通道 v1 仅登记（`self.armament="spiral"` 无战斗钩子读取，§4.4 兵装切换未实现） | `engine/m9/talents/g3.py:122, 539` |
| 5.7 | T3 穿防实现方式偏差：以 `__无视__` 属性哨兵实现零防御，与 T4 金身"无视属性可穿透"逻辑耦合（建议 `armor_pierce_factor=0.0`） | `engine/m9/talents/t3.py:110-115`；`combat/numeric_v2.py:38-61`；`t4.py:443` |
| 5.8 | 尘世之锁重施刷新用 `max(duration, remaining)` 而非"重置为基础持续时间" | `engine/m9/petrify.py:85-98` |
| 5.9 | 诗篇「永恒」/「追光」/「明天」只授不消费；七枚简化标记全部只授不消费；守夜人光环恢复层数、负世自动燃尽重复加成未实现 | `engine/m9/talents/poems.py:203-206, 283-313, 317-325`；`g3.py:305-315` |
| 5.10 | 同父事件完整额外行动固定优先级仲裁（T4>地火>负世，v0.8 §3.2.8）未接游戏流：`pick_full_extra_candidate` 仅 smoke 调用，游戏流内退化为"先派发先得" | `engine/m9/action_system.py:406-411`；`tools/m9_rfc_smoke.py:62` |
| 5.11 | `acted_this_round` 布尔残留（已不参与资格判定，结算 v0.3 §7 要求不得由单布尔反推事实） | `engine/round_manager.py:536-539, 644-658` |
| 5.12 | 压制无通用裁决器：仅 G2 终曲硬编码入口，无结算合同 §4 控制优先级通用通道 | `engine/round_manager.py:454-472`；`engine/m9/resolution.py:73-85` |

## 六、AI / 指令 / TUI / RL 四层零接入（独立算法设计轨道）

> 本组**不属于本次接线批次**：需要策略/世界模型/行为树算法设计而非接线体力活。
> 审计结论留档，供后续算法设计批次单独立项。

| # | 内容 | 证据 |
|---|---|---|
| 6.1 | `M9AIPolicy`/`DefaultM9Policy` 协议只在 `docs/m9/ai/talents.md §4.1`，代码不存在；`controllers/ai/` 全目录 0 处 m9 | `docs/m9/ai/talents.md:121-131` |
| 6.2 | AI minds 仍读 legacy `police_engine` 与 `state.active_barrier`（M9 下恒 None，M9 结界过滤在 AI 层完全失效）；AI 仍按显示名分派 T0（与 M9 名称不匹配） | `controllers/ai/combat_mixin.py:69,605`；`game_query.py:1275-1335`；`choose_mixin.py:206-318` |
| 6.3 | AI 不生成任何 M9 special（破界/热线举报/竞选队长/指挥）；`m9_ai` 分叉不存在（文档与代码均无） | `cli/parser.py` 全文无 `m9_` 前缀分支 |
| 6.4 | TUI/CLI 帮助仍宣传退役警察指令；无 M9 special/SP/即演/公演/演出入口提示 | `tui/app.py:187, 954-963`；`cli/display.py:158-173, 304-329` |
| 6.5 | RL 137 索引无 M9 支持；`ai_chat/state_narrator.py` 无 M9 参考；`docs/handbook/manifest.json` 无 m9 模块 | `rl/action_space.py:50, 79-107` |
| 6.6 | 风洞缺失：无 AI 策略风洞（`talents.md §3` 评分器未落地）、无 6 人 BasicAI 批跑基准、W1 监测指标（首轮即演数/连续即演/演出总数/公演空置率）无采集 | `docs/m9/README.md:236-238`；`stats_runner.py:365-366` |
| 6.7 | G2/G5 AI 钩子显示名失配（同 2.9）；T2/T6/T7/G0/G6 从无钩子；既有钩子全部为 v2exp 时代逻辑 | `controllers/ai/controller.py:158-168`；`t1_oneslash_hook.py:61-80` |

## 七、测试与文档治理

| # | 内容 | 证据 |
|---|---|---|
| 7.1 | v0.1 就绪审计 33 场景：24 闭环、6 部分覆盖、**3 条测试缺失**（场景 10 T3 同槽挣脱、场景 15 G4 真正打断、场景 8 有效伤害→摇晃推进）；v0.2 的 17 场景 17/17 有测试（场景 3/4 弱断言） | `tests/test_m9_*.py`；`engine/m9/petrify.py:141-158` |
| 7.2 | W4 漂移：水晶花计分通道（同 4.6）；微澜（1 SP 信息型即演）无实现无测试 | `engine/m9/talents/g5.py:240`（仅 `g5_anchor` 一个 m9_kind） |
| 7.3 | balance 键漂移：`g4.max_embers`→`ember_cap`、`g1.entropy_rate_*`→`entropy_gain_*`、`g7.macro_costs` 缺失、`t6.equipment` 死配置（与代码白名单两套概念）、`poem_joy_borrow_cores` 死键 | `engine/m9/talent_registry.py:342-360`；`engine/m9/police.py:33-34` |
| 7.4 | 注册表漂移：`m9-implementation-readiness-v0.1` 被标 superseded（实为被 v0.2 补遗，33 场景仍权威）；`docs/m9/drafts/` 两个文件未登记；`last_reviewed` 陈旧 | `docs/document_registry.json:813-818` |
| 7.5 | DOC-013/DOC-014 代码内漂移未清：`game_setup.py:74-76` 仍写 T4 每 4 轮充能/T5 连续行动奖励；`orchestrator.py:22` 仍提已退役的 `new_arch_enabled` | `engine/game_setup.py:74-76`；`controllers/ai/orchestrator.py:22` |

---

## 处理顺序（接线批次执行台账）

> 顺序 = 依赖关系；每项关闭时在对应行回填：改动 commit/证据 file:line + 验证命令输出。
> 验证基准：`uv run pytest tests/test_m9_*.py`、`uv run python tools/m9_rfc_smoke.py`、
> `uv run python tools/m9_rfc_playtest.py`、`uv run pytest tests/test_talents_md_sync.py`（文档同步）。

| 步 | 组 | 范围 | 验证 |
|---|---|---|---|
| S1 | 一 | 拆除旧系统：m9-rfc 移除/门控 `m6_scoring`（星光/喝彩/旧评分/R4 置星/旧终局轨）；legacy 警察指令与 G2/G5 演唱在 m9-rfc 下门控或按 commands.md 附录纪律拆除；清理星光副作用字段与 `afterlife` 旧数值区 | 新终局走四步求值有集成测试；m9-rfc 局内无星光/喝彩发生 |
| S2 | 二 | 修 9 条 P0 断线（2.1 R2 警察 tick、2.2 停机收尾接线、2.3 G5 追忆喂入、2.4 献诗入口+微澜、2.5 世界援助接线、2.6 G7 即演入口、2.7 absolute_dead 排除星化+PP 冻结、2.8 gate 挂 m9_scoring、2.9 钩子名修配） | 每条对应新增/既有测试通过；`m9_rfc_playtest` 全剧本 exit 0 |
| S3 | 三 | 活桩改真：G6 四借用核心执行器（t2/g3 已实现仅需派发；补 t1/g4）、26 项援助执行器、律法配装分支、欢愉双借用+6-tick 到期、aid_rest 触发 | `test_m9_g6`、`test_m9_poems` 扩充用例通过 |
| S4 | 四 | B4 主机制落地：R0 开市（投注/tranche/转仓/赔率/锁市）、黑马快照、PP 经济闭环（earn/spend/decay）、评分公式对齐 RFC、水晶花改 `arc_count`、交易系统、四步求值终局接入 | 投注/派彩集成测试；终局快照并列胜者用例 |
| S5 | 五 | 机制级补齐与语义修正：G1（宣言不占槽/受限追加 kind/免费 find/超击破/灼烧/自愈/先攻加成/兜底/每局闸）、G4（拉条减伤/毁伤产出/真快照/键读）、G3 兵装、T3 穿防改法、尘世之锁刷新、诗篇消费点、仲裁接线、布尔清理、压制通用裁决器 | 对应 `test_m9_g1/g4/g3/t3/poems` 用例 |
| S6 | 七 | 测试与治理：补 v0.1 三条缺失场景测试、弱断言加固；balance 键漂移修正；注册表/drafts/last_reviewed 与 DOC-013/014 清理 | `test_m9_*` 全绿；`check_doc_governance` 无 ERROR |
| S7 | 六 | **（独立算法设计轨道，不在本批次）** AI 策略/世界模型/指令生成/TUI/RL/风洞 | 另行立项 |

## 关闭记录（2026-08-12）

| 步 | 关闭证据 |
|---|---|
| S1 | `experiments.py:42` m9-rfc 仍含 m6_scoring 但运行面已门控：`round_manager.py` `_finalize_winner` 对 m9_rfc 返回存活轨（旧 M6 综合评分不再用）、R1 `_star_fate_bonus`/R4 死亡置星/R4 `_process_starlight` 均加 m9_rfc 门控；`applause.py` award/check_kill_applause、`applause_spend.py` available_uses/execute 对 m9_rfc 关闭；`cli/validator.py` validate() 对 9 类 legacy 警察/喝彩行动在 m9_rfc 下拒绝并提示 M9 入口。验证：`tests/ -k m9` 527 passed、smoke/playtest PASS。 |
| S2 | 2.1 `round_manager._phase_r2` 把 m9_police.r2_tick 移到 k_initiative 提前 return 之前；2.2 `g1._m9_supernova_burst` 调 `m9_police.shut_down`；2.3 `combat.resolve_damage` → `_feed_g5_combat` → `Ripple9.feed_combat_round`（两层按轮去重，DOC-046）；2.4 G5 T0 增加微澜（1 SP 信息即演 + 隐身揭示，`visibility.can_see_m` 消费 `_m9_ripple_ignore_stealth_from`）与献诗入口（`g5_anchor_or_poem`）；2.5 `world_poem_aid_of` + R0 recompute + R4 绫音急救 + `combat._world_poem_followup`（追演+震荡）；2.6 G7 T0 即演/公演入口（`g7_improvise`/`g7_public` + 补给）；2.7 `combat.finalize_death` 对 absolute_death 调 `m9_pp.freeze`；2.8 `gate.py` 挂 `m9_scoring`（ScoringEngine）；2.9 `controller.py` 为 G2/G5 钩子注册 M9 显示名别名。文档同步：`docs/ai/commands_choose.md`、`docs/m9/ai/slots_g4g5g6g7.md`。验证：`tests/ -k m9` 527 passed、smoke/playtest PASS。 |
| S3 | G6 四借用核心执行器：`_borrow_core_slash`/`_borrow_core_attack`/`_borrow_enhanced_basic`（g6.py）+ `Mythland9.borrow_simple_projection`，T4 或跃重掷保留；`engine/m9/aids.py` 26 项援助执行器（13 槽×攻/防）含 G5 简化标记白名单/G6 可复制模板/G3 前摇截断标记/T3 aid_rest，`police.py` 增 `temporary_performance_police`/`grant_cover_to_player`/`clear_expired_aid_covers`，combat `_apply_police_cover` 吸收援助掩体；G6 `_public_aid` 接线 G6 援助重演；律法诗配装分支走 m9_police；欢愉双借用（至多一攻击核心）+ 6-tick 到期（g6 on_round_end）；aid_rest 触发（round_manager R3 改写槽）。验证：`tests/ -k m9` 通过、smoke/playtest PASS。 |
| S4 | `pp.py` 重写：真实投注（tranche/同目标追加/转仓 fee/赔率=存活人数:1/派彩/死目标销毁）、aid_earned、黑马快照；`ScoringEngine` 对齐评分指针 v0.1（剧情=arc×arc_weight、战果=kill×kw+damage×dw、存活 1.5/死者 PP+援助收益/撤退 0.5）；四步求值终局接入 `round_manager._finalize_winner`（写 `game_winner_snapshot` 并列胜者 + `final_scores` 显示终分）；R0 开市窗口 `_m9_betting_window`；R4 生者衰减 `_m9_pp_r4_decay`；击杀 PP 生成 `combat._award_kill_pp`；生前消耗 special ops `PP重掷先攻/PP加伤/PP偷看先攻/PP抵消犯罪` + 交易 `交易<名>`；水晶花/G2 终曲改 `add_arc`；balance 增 `survival_alive`/`ruin_gain_per_attack`/`challenge_reduction`/`poem_spotlight_*`/`poem_tomorrow_*`。新增测试：`test_bet_payout_on_winner`、`test_parallel_winners_snapshot`。文档：commands.md 现役区登记新 special ops。验证：`tests/ -k m9` 529 passed、smoke/playtest PASS。 |
| S5 | G1：换装宣言不占槽（RFC §2.0）、超击破（burn/受控增伤）、超新星灼烧（supernova_burn）、繁育每局一次闸、繁育先攻加成（m9_initiative_bonus）、完全燃烧窗口每轮 R0 自愈、卸甲免费 find（`special 卸甲免费find`，不占槽、每轮一次）、受限追加 `ActionGrant(kind=restricted_followup)`（g1 on_turn_end 派发 + R3 `_m9_restricted_followup` 执行 move/attack）、最后安全地点兜底（B5-W8）+ home 就地回退；G4：真先攻快照（get_initiative_bonus）+ 拉条减伤（challenge_reduction，finally 清除）+ 强化普攻产毁伤（m9_on_attack，弃毁伤当攻击加值）+ **真正打断修复**（响应后检查退出形态，审计场景 15）；G3 永恒诗消费点（维持费折扣）；G2 追光诗消费点（combat `_apply_spotlight_focus` 影身攻击+治疗）；G0 明天诗消费点（遗物不毁装/不耗 HP，3 uses）；尘世之锁重施刷新改重置基础时长（petrify `_refresh`）；完整额外行动同父仲裁接线（`dispatch_full_extra` 用 `pick_full_extra_candidate`）；压制通用裁决器 `SuppressRegistry`（gate 挂载 + R3 先裁决 + R0 清理）；acted_this_round 标注为诊断字段。新增测试：场景 8/10（test_m9_t3）、场景 15（test_m9_g4）。**残留说明**：G3 兵装通道的数值效果 `[待风洞]`（RFC 无已冻结数值，仅通道登记/切换无行为差）；T3 穿防经 `__无视__` 哨兵实现零防御，与 RFC 的 defense_coefficient=0 结果一致，改传 armor_pierce_factor=0.0 属实现风格偏好，涉及 T4 金身耦合、改动风险高于收益，登记为已知偏差。验证：`tests/ -k m9` 532 passed、smoke/playtest PASS。 |
| S6 | v0.1 三条缺失场景测试补齐（T3 同槽挣脱/有效伤害摇晃/G4 真正打断）；balance 键漂移清理（移除 talent_registry 三个死代码键漂移 helper）；DOC-013（game_setup.py T4/T5 简介）+ DOC-014（orchestrator.py new_arch_enabled 文案）清理；document_registry.json 登记 drafts 缺失两文件（m9_g0_poem_design、m9_g5_poems_migration_b3d10_working）；`last_reviewed` 已刷新 2026-08-12。验证：`tests/ -k m9` 532 passed；`check_doc_governance` ERROR=0（WARN 8 均为 DOC-011「未纳入 Git」未跟踪文档，非错误）。 |

## 退出边界

S1–S6 全部关闭（对应验证通过）即本批次完成；第六大组（AI/指令/TUI/RL）为算法设计轨道，不作为本批次退出条件。
