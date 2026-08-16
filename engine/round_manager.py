"""轮次调度器（Phase 4 完整版）：天赋钩子+响应窗口+额外行动回合"""

from utils.dice import roll_d4, roll_d6
from cli import display
from engine.action_turn import ActionTurnManager
from engine.police_system import PoliceEngine
from engine import experiments
from engine.balance import get as bget
from engine.m9.text import m9_text, m9_text_list


class RoundManager:
    def __init__(self, game_state):
        self.state = game_state
        self.turn_manager = ActionTurnManager(game_state)
        self.police_engine = PoliceEngine(game_state)
        self.state.police_engine = self.police_engine

    def run_game_loop(self):
        while not self.state.game_over:
            self.run_one_round()
            winner_id = self.state.check_victory()
            if winner_id:
                self.state.game_over = True
                self.state.survival_winner = winner_id  # 存活轨（老指标）
                self.state.winner = self._finalize_winner(winner_id)
                self._announce_winner(winner_id)
                return

            # 最大轮数安全网
            if self.state.is_max_rounds_reached():
                self.state.game_over = True
                self.state.survival_winner = "nobody"
                self.state.winner = self._finalize_winner("nobody")
                if self.state.winner == "nobody":
                    display.show_info(
                        f"⚠️ 达到最大轮数限制（{self.state.max_rounds}轮），游戏判定平局。")
                else:
                    self._announce_winner(self.state.winner)
                return

    def _finalize_winner(self, survival_winner):
        """M6 终分制：胜者重定义为终分最高者（非最后存活）。
        非 m6 时返回 survival_winner（存活轨）。

        M9（评分指针 v0.1 §3.1 四步求值）：先算排除派彩/黑马加成的
        base_final_score 冻结 game_winner_snapshot（并列胜出），再锁市派彩与
        黑马加成进显示终分；`state.winner` 取快照首个展示。"""
        if not experiments.is_enabled("m6_scoring") \
                or experiments.is_enabled("m9_rfc"):
            scoring = getattr(self.state, "m9_scoring", None)
            if experiments.is_enabled("m9_rfc") and scoring is not None:
                alive_ids = [pid for pid in self.state.player_order
                             if self.state.get_player(pid).is_alive()]
                dead_ids = [pid for pid in self.state.player_order
                            if pid not in alive_ids]
                results = scoring.settle(alive_ids, dead_ids, self.state)
                if results:
                    winners = [pid for pid, r in results.items()
                               if r.is_winner]
                    self.state.game_winner_snapshot = frozenset(winners)
                    self.state.final_scores = {
                        pid: r.display_final_score
                        for pid, r in results.items()}
                    order = {pid: i for i, pid in
                             enumerate(self.state.player_order)}
                    return min(winners, key=lambda pid: order.get(pid, 99))
            return survival_winner
        from engine import scoring
        scores = scoring.compute_all(self.state)
        self.state.final_scores = scores
        if not scores:
            return survival_winner
        # 终分第一（并列时按 player_order 稳定取首个，确定性）
        order = {pid: i for i, pid in enumerate(self.state.player_order)}
        return max(scores, key=lambda pid: (scores[pid], -order.get(pid, 99)))

    def _announce_winner(self, winner_id):
        if winner_id == "nobody":
            display.show_info("所有玩家都已死亡……无人获胜。")
        else:
            w = self.state.get_player(winner_id)
            display.show_victory(w.name if w else winner_id)

    def run_one_round(self):
        self.state.current_round += 1
        display.show_round_header(self.state.current_round)
        for p in self.state.players.values():
            p.acted_this_round = False

        self._phase_r0()
        self._phase_r1()
        self._phase_r2()
        self._phase_r3()
        self._phase_r4()

        # 诊断：通知所有 controller 记录轮次快照
        for pid in self.state.player_order:
            p = self.state.get_player(pid)
            if p and hasattr(p.controller, '_diag') and p.controller._diag:
                p.controller._diag.record_round_snapshot(
                    self.state.current_round, self.state
                )

    # ============================================
    # R0: 轮次开始结算
    # ============================================
    def _phase_r0(self):
        self.state.current_phase = "r0_start"
        hp20 = experiments.is_enabled("hp20")

        # M5 世界时钟：阶段变化播报（experiment: m5_clock）
        if experiments.is_enabled("m5_clock"):
            from engine import world_clock
            phase = world_clock.current_phase(self.state)
            if phase != getattr(self.state, "_last_world_phase", None):
                self.state._last_world_phase = phase
                self.state.log_event("world_phase", phase=phase)
                display.show_info(f"🌅 世界进入「{world_clock.label(phase)}」")
            # 限量营业/首攻嫌疑 per-round 容器重置（M5e）
            self.state._rationing_used = set()
            self.state._first_attack_done = set()

        for pid in self.state.player_order:
            p = self.state.get_player(pid)
            if p:
                p._armor_gained_this_round = False
                p.moved_this_round = False  # M3 移动闪避按轮重置
                if hp20 and p.is_alive():
                    # 不老泉每轮再生（v2.0 §2.4，不超上限）
                    regen = getattr(p, 'regen_per_round', 0)
                    if regen > 0 and p.hp < p.max_hp:
                        p.hp = min(p.max_hp, p.hp + regen)
                    # 韧性脉冲轮数衰减（v2.0 §2.5.1）
                    if getattr(p, 'resist_pulse_rounds', 0) > 0:
                        p.resist_pulse_rounds -= 1

        # ish-bosheth 废墟谢幕（pending_curtain → 清理）
        if (self.state.ish_bosheth
                and self.state.ish_bosheth.phase == "pending_curtain"):
            self.state.ish_bosheth.on_r0_curtain(self.state)

        # v2.0: duet 模式按钮刷新
        ish = self.state.ish_bosheth
        if ish and ish.phase == "duet" and not ish.duet_curtain_triggered:
            ish._spawn_duet_buttons(self.state)

        # 天赋轮次开始钩子
        for pid in self.state.player_order:
            p = self.state.get_player(pid)
            if p and p.is_alive() and p.talent:
                p.talent.on_round_start(self.state.current_round)

        # M9：自动状态结束后开放报名窗口，并在 R0 固化本轮唯一公演位。
        m9 = getattr(self.state, "m9_system", None)
        if m9 is not None:
            m9.begin_round(self.state.current_round)
            self._m9_offer_performance_registration(self.state.player_order)
            m9.allocate_public_slot(
                self.state.current_round, self._m9_public_eligible)
            # M9：石化挣脱计数按轮清理
            petrify = getattr(self.state, "m9_petrify", None)
            if petrify is not None:
                petrify.reset_round(self.state.current_round)
            # M9：压制裁决器每轮清理（未来来源登记；G2 终曲状态自持）
            suppress_reg = getattr(self.state, "m9_suppress", None)
            if suppress_reg is not None:
                suppress_reg.clear_round()
            # M9 警察：R0 执法配额重置 + 掩体再验证
            m9_police = getattr(self.state, "m9_police", None)
            if m9_police is not None:
                m9_police.set_state_ref(self.state)
                m9_police.r0_tick(self.state, self.state.current_round)
            # M9 魂援：清理到期的援助/遗物掩体与脆弱标记（B4 §5.3 防御侧状态）
            from engine.m9.police import clear_expired_aid_covers
            clear_expired_aid_covers(self.state)
            # M9 开市窗口（B4 §4.1）：死者投注/追加/转仓；先于黑马快照与
            # 世界援助 recompute（公演队列清理之前，此处即 R0 中段）。
            self._m9_betting_window()
            # M9 G0 世界援助：R0 开市结束、tranche/转仓落定后重算黑马快照与
            # 激活门槛（合同 v0.1 §八场景 1-2）。投注接线（S4）后激活门槛随
            # 有效押注翻转；当前无投注则不激活（符合"无有效押注不生效"）。
            from engine.m9.g0_world_poem import world_poem_aid_of
            aid = world_poem_aid_of(self.state)
            if aid is not None:
                alive_ids = [pid for pid in self.state.player_order
                             if self.state.get_player(pid).is_alive()]
                dead_ids = [pid for pid in self.state.player_order
                            if pid not in alive_ids]
                try:
                    aid.recompute(self.state.current_round, alive_ids, dead_ids)
                except Exception:
                    pass

    def _m9_public_eligible(self, actor_id):
        player = self.state.players.get(actor_id)
        if player is None or not player.is_alive():
            return False
        form = getattr(getattr(player, "talent", None), "form", None)
        if form in ("home", "past"):
            return False
        talent = getattr(player, "talent", None)
        if talent is None or not hasattr(talent, "get_t0_option"):
            return False
        return talent.get_t0_option(player) is not None

    def _m9_offer_performance_registration(self, actor_ids):
        """在 R0 或升到 SP2 的事件收尾询问保留/报名。"""
        m9 = getattr(self.state, "m9_system", None)
        if m9 is None:
            return
        for actor_id in actor_ids:
            player_id = self.state.attention_owner_id(actor_id)
            player = self.state.players.get(player_id)
            if player is None or m9.get_sp(player_id) < 2:
                continue
            if m9.queue.is_in_queue(player_id):
                continue
            if not self._m9_public_eligible(player_id):
                continue
            try:
                choice = player.controller.choose(
                    m9_text("round_manager.registration_prompt"),
                    m9_text_list("round_manager.registration_options"),
                    context={"phase": "M9_PUBLIC_REGISTRATION",
                             "game_state": self.state,
                             "player": player},
                )
            except (AttributeError, TypeError, ValueError):
                choice = "保留"
            if choice == "报名公演":
                m9.register_performance(player_id, self.state.current_round)

    # ============================================
    # R1: D4 争夺行动权（v1）/ 先攻判定（k_initiative）
    # ============================================
    def _phase_r1(self):
        # m9_system 本身就是全员标准槽的硬门槛，不能依赖调用方另外记得
        # 打开 k_initiative；profile 与单独实验开关两种入口都必须闭合。
        if getattr(self.state, "m9_system", None) is not None \
                or experiments.is_enabled("k_initiative"):
            return self._phase_r1_initiative()
        self.state.current_phase = "r1_d4"
        display.show_phase("🎲 D4 争夺行动权")

        self.state.d4_results.clear()
        self.state.d4_bonuses.clear()
        self.state.round_winners.clear()

        max_val = 0
        results = {}
        raw = {}
        bonuses = {}

        for pid in self.state.player_order:
            player = self.state.get_player(pid)
            if not player or not player.is_alive():
                continue
            base_roll = roll_d4()
            bonus = player.get_d4_bonus()
            final = min(base_roll + bonus, 4)
            raw[pid] = base_roll
            bonuses[pid] = bonus
            results[pid] = final
            if final > max_val:
                max_val = final

        # Chorus 参与 D4
        if self.state.ish_bosheth and self.state.ish_bosheth.phase in ("active", "duet"):
            for c in self.state.ish_bosheth.chorus_list:
                if c.is_alive() and c.location:
                    base_roll = roll_d4()
                    final = min(base_roll, 4)  # Chorus 无 D4 加成
                    raw[c.player_id] = base_roll
                    bonuses[c.player_id] = 0
                    results[c.player_id] = final
                    if final > max_val:
                        max_val = final

        # v0.6 ish-bosheth/duet 模式：G2 固定 D4=0，所有人按 D4 排序，全部行动
        if self.state.ish_bosheth and self.state.ish_bosheth.phase in ("active", "duet"):
            g2_pid = self.state.ish_bosheth.g2_owner_id
            raw[g2_pid] = 0
            bonuses[g2_pid] = 0
            results[g2_pid] = 0

        self.state.d4_results = raw
        self.state.d4_bonuses = bonuses

        # ish-bosheth/duet 活跃 → 所有参与者按 D4 排序，全部获得行动权
        if self.state.ish_bosheth and self.state.ish_bosheth.phase in ("active", "duet"):
            sorted_pids = sorted(results.keys(),
                                 key=lambda pid: results[pid], reverse=True)
            self.state.round_winners = sorted_pids
        else:
            winners = [pid for pid, val in results.items() if val == max_val]
            self.state.round_winners = winners

        # 构建 pid → name 映射（含 Chorus）
        pid_to_name = {}
        for pid in raw:
            p = self.state.get_player(pid)
            if p:
                pid_to_name[pid] = p.name
            elif self.state.ish_bosheth:
                for c in self.state.ish_bosheth.chorus_list:
                    if c.player_id == pid:
                        pid_to_name[pid] = c.name
                        break
        display_names = {pid_to_name[pid]: raw[pid] for pid in raw}
        bonus_names = {pid_to_name[pid]: bonuses[pid] for pid in raw}
        winner_names = [pid_to_name[pid] for pid in self.state.round_winners]
        display.show_d4_results(display_names, bonus_names, winner_names)

    def _phase_r1_initiative(self):
        """K 常量行动制先攻判定（v2.0 §1.1，experiment: k_initiative）。

        全员掷 D6 + 先攻修正，降序排名前 K 名获得行动权（K = max(1, 存活数 - 坐牢数)）。
        同点 tiebreak：补掷 D6 → 先攻修正高者 → player_order 序（确定性兜底）。
        舞台模式（ish-bosheth active/duet）全员行动不坐牢，G2 仍由 R3 现有逻辑置顶。
        """
        self.state.current_phase = "r1_initiative"
        display.show_phase("🎲 先攻判定")

        # v1 字段清空（保持消费方语义：K 模式下 d4_results 恒为空）
        self.state.d4_results.clear()
        self.state.d4_bonuses.clear()
        self.state.round_winners.clear()

        stage_active = (self.state.ish_bosheth
                        and self.state.ish_bosheth.phase in ("active", "duet"))

        # 参与者收集：M9 是全部普通层 actor；旧 profile 保持玩家 + Chorus。
        entrants = []  # (pid, name, bonus_fn)
        m9 = getattr(self.state, "m9_system", None)
        if m9 is not None:
            for actor in self.state.iter_action_actors(self.state.current_round):
                bonus = actor.get_initiative_bonus()
                talent = getattr(actor, "talent", None)
                if talent is not None and hasattr(talent, "m9_initiative_bonus"):
                    try:
                        bonus += int(talent.m9_initiative_bonus())
                    except Exception:
                        pass
                entrants.append((actor.player_id, actor.name, bonus))
        else:
            for pid in self.state.player_order:
                p = self.state.get_player(pid)
                if p and p.is_alive():
                    entrants.append((pid, p.name, p.get_initiative_bonus()))
        if stage_active:
            for c in self.state.ish_bosheth.chorus_list:
                if c.is_alive() and c.location:
                    entrants.append((c.player_id, c.name, 0))  # Chorus 无修正

        # 掷骰 + 排序键：(总值, 补掷, 修正, -原始序) 全部降序
        order_map = {pid: i for i, pid in enumerate(self.state.player_order)}
        rolls = {}
        entries = []
        for pid, name, bonus in entrants:
            roll = roll_d6()
            # M6 喝彩消耗·重掷先攻（自消耗 flag）
            p_obj = self.state.get_player(pid)
            if p_obj is not None and getattr(p_obj, "_applause_reroll_initiative", False):
                p_obj._applause_reroll_initiative = False
                reroll = roll_d6()
                if reroll > roll:
                    roll = reroll
            # M6 往世层·拨弄命运（星光行动给的先攻 ±1，自消耗）
            # M9：星光行动已被 B4 v0.4 往世层新设计取代，不消费该副作用字段。
            if (p_obj is not None and getattr(p_obj, "_star_fate_bonus", 0)
                    and not experiments.is_enabled("m9_rfc")):
                bonus += p_obj._star_fate_bonus
                p_obj._star_fate_bonus = 0
            total = roll + bonus
            tiebreak = roll_d6()  # 补掷（仅同点时有意义，统一掷保证消耗序稳定）
            rolls[pid] = (roll, bonus, total)
            entries.append((total, tiebreak, bonus, -order_map.get(pid, 99), pid, name))
        entries.sort(reverse=True)

        # PP偷看先攻（2026-09 修复）：逐人秘密揭示本轮先攻结果并消耗 flag。
        for position, entry in enumerate(entries, 1):
            pid = entry[4]
            actor = self.state.get_actor(pid)
            if actor is None or not getattr(
                    actor, "_applause_peek_initiative", False):
                continue
            actor._applause_peek_initiative = False
            roll, bonus, total = rolls[pid]
            if m9 is not None:
                display.prompt_secret(m9_text(
                    "round_manager.initiative_peek_title", name=entry[5]))
                print(m9_text(
                    "round_manager.initiative_peek_line",
                    name=entry[5], total=total, roll=roll, bonus=bonus,
                    position=position, count=len(entries)))
            else:
                display.prompt_secret(f"{entry[5]} 的先攻偷看结果")
                print(f"  👁️ 你的先攻 = {total}（D6 {roll} + 修正 {bonus}），"
                      f"本轮行动顺位 {position}/{len(entries)}")

        # 配额：K = max(1, 存活玩家数 - 坐牢数)；舞台模式全员行动
        sitout_count = bget("action_system", "k_sitout_count", default=1)
        if m9 is not None or stage_active:
            quota = len(entries)
        else:
            quota = max(1, len(entries) - max(0, int(sitout_count)))

        actors = [e[4] for e in entries[:quota]]
        sitout = [e[4] for e in entries[quota:]]
        self.state.round_winners = actors
        self.state.initiative_results = rolls  # K 模式专属（动态属性，消费方用 getattr）
        if m9 is not None:
            self.state.m9_round_grants = [
                m9.issue_standard(
                    actor_id,
                    self.state.current_round,
                    allow_instant=not getattr(
                        self.state.get_actor(actor_id), "_m9_shadow_actor", False),
                    allow_public=not getattr(
                        self.state.get_actor(actor_id), "_m9_shadow_actor", False),
                    restricted=getattr(
                        self.state.get_actor(actor_id), "_m9_shadow_actor", False),
                )
                for actor_id in actors
            ]

        # 展示
        roll_names = {}
        bonus_names = {}
        pid_to_name = {e[4]: e[5] for e in entries}
        for pid, (roll, bonus, total) in rolls.items():
            roll_names[pid_to_name[pid]] = total
            bonus_names[pid_to_name[pid]] = bonus
        display.show_initiative_results(
            roll_names, bonus_names,
            [pid_to_name[pid] for pid in actors],
            [pid_to_name[pid] for pid in sitout],
        )

    # ============================================
    # R2: 先后手判定
    # ============================================
    def _phase_r2(self):
        self.state.current_phase = "r2_priority"

        # M9 警察：R2 自动执法（lead 分配/重分配 + 队长候选上任）。
        # 这是 v0.8 §7 R2"公共状态推进"的一部分，必须先于 k_initiative 的
        # 先攻序提前 return 执行；否则 m9-rfc（恒开 k_initiative）下永不可达。
        m9_police = getattr(self.state, "m9_police", None)
        if m9_police is not None and getattr(self.state, "m9_system", None) \
                is not None:
            m9_police.set_state_ref(self.state)
            msgs = m9_police.r2_tick(self.state, self.state.current_round)
            for msg in msgs:
                display.show_info(msg)

        if experiments.is_enabled("k_initiative"):
            return  # 先攻序已全局有序，同地点冲突 D6 判定过时（v2.0 §1.1）
        if len(self.state.round_winners) <= 1:
            return
        conflict = set()
        loc_groups = {}
        for pid in self.state.round_winners:
            p = self.state.get_player(pid)
            if p and p.location:
                loc_groups.setdefault(p.location, []).append(pid)
        for loc, pids in loc_groups.items():
            if len(pids) > 1:
                conflict.update(pids)

        if conflict:
            display.show_phase("⚔️ 先后手判定（D6）")
            rolls = {}
            for pid in conflict:
                p = self.state.get_player(pid)
                base_roll = roll_d6()
                bonus = p.get_d6_bonus()
                r = max(base_roll + bonus, 1)
                rolls[pid] = r
                if bonus != 0:
                    display.show_info(f"  {p.name}: D6 = {base_roll} {'+' if bonus >= 0 else ''}{bonus} → {r}")
                else:
                    display.show_info(f"  {p.name}: D6 = {r}")
            sorted_conflict = sorted(conflict, key=lambda x: rolls[x], reverse=True)
            non_conflict = [w for w in self.state.round_winners if w not in conflict]
            self.state.round_winners = sorted_conflict + non_conflict
        else:
            order_map = {pid: i for i, pid in enumerate(self.state.player_order)}
            self.state.round_winners.sort(key=lambda pid: order_map.get(pid, 99))

    # ============================================
    # R3: 行动阶段
    # ============================================
    def _phase_r3(self):
        self.state.current_phase = "r3_actions"
        display.show_phase("⚔️ 行动阶段")

        if not self.state.round_winners:
            display.show_info("本轮无人获得行动权。")
            return

        # M9 直接消费 R1 创建的 ActionGrant；旧 profile 保持 actor id 队列。
        m9 = getattr(self.state, "m9_system", None)
        if m9 is not None:
            action_queue = list(getattr(self.state, "m9_round_grants", []))
        else:
            action_queue = list(self.state.round_winners)

        # v0.6 ish-bosheth: G2 保底最优先行动
        ish = self.state.ish_bosheth
        if m9 is None and ish and ish.phase in ("active", "duet"):
            g2_pid = ish.g2_owner_id
            if g2_pid in action_queue:
                action_queue.remove(g2_pid)
            action_queue.insert(0, g2_pid)
            display.show_info(f"🎵 G2 优先演唱回合")

        i = 0
        while i < len(action_queue):
            queue_item = action_queue[i]
            grant = queue_item if m9 is not None else None
            actor_id = grant.actor_id if grant is not None else queue_item
            actor = self.state.get_actor(actor_id)
            if not actor or not actor.is_alive():
                i += 1
                continue
            if grant is not None:
                m9.begin_grant(grant)

            # M9 魂援·T3 防御援助（aid_rest）：下一次实际 ActionGrant 在控制
            # 裁决前改写为 aid_rest 并消费——绝对伤害免疫、槽以 aid_rest 收尾，
            # 压制/石化/震荡保留到后续槽（resolution.AidRestTracker 语义）。
            if m9 is not None and getattr(
                    actor, "_aid_t3_rest_pending", False):
                actor._aid_t3_rest_pending = False
                slot_id = m9.assign_slot(actor.player_id, grant)
                m9.resolve_slot(
                    slot_id, root_action=False, kind="aid_rest",
                    voluntary_forfeit=False, performance_performed=False)
                display.show_info(m9_text(
                    "round_manager.aid_rest_message", name=actor.name))
                m9.end_grant(grant)
                i += 1
                continue

            # M9：终曲一次压制（合同 G2 §8.3）——区域内非 G2 actor 的实际槽被
            # 消耗 suppression use → 跳过行动、槽以 ACTION_SUPPRESSED 收尾
            # 压制通用裁决器（结算合同 §4）：注册表登记的压制来源先裁决
            if m9 is not None:
                suppressed = False
                reg = getattr(self.state, "m9_suppress", None)
                if reg is not None and reg.is_suppressed(actor.player_id):
                    suppressed = True
                if not suppressed:
                    for pid in self.state.player_order:
                        p = self.state.get_player(pid)
                        if (p and p.talent
                                and hasattr(p.talent, "suppress_grant")
                                and p.talent.suppress_grant(
                                    actor.player_id, m9)):
                            suppressed = True
                            break
                if suppressed:
                    slot_id = m9.assign_slot(actor.player_id, grant)
                    m9.resolve_slot(slot_id, kind="suppressed",
                                    suppressed=True)
                    display.show_info(m9_text(
                        "round_manager.suppression_message", name=actor.name))
                    m9.end_grant(grant)
                    i += 1
                    continue

            # 执行行动回合；地火完整额外行动只观察本 grant 内新产生的事件。
            event_start = len(self.state.event_log)
            if grant is not None and grant.kind == "restricted_followup":
                # G1 完全燃烧受限追加：仅 move/attack，不重新开放 T0（§2.3）
                action_type = self._m9_restricted_followup(actor, grant)
            else:
                action_type = self.turn_manager.execute_action_turn(actor)

            # M9 槽收尾 + G6 模板池记录（profile: m9-rfc；v2exp 路径不执行）
            if m9 is not None:
                slot_id = m9.assign_slot(actor.player_id, grant)
                excluded = ("status", "help", "police_status", "allstatus",
                            "shock_recover", "wake")
                if getattr(actor, "_m9_last_slot_wake_followup", False):
                    kind = "wake_followup"
                    actor._m9_last_slot_wake_followup = False
                    root = True
                    voluntary_forfeit = False
                elif action_type == "forfeit":
                    kind = "forfeit"
                    root = False
                    voluntary_forfeit = True
                elif action_type == "petrify_hold":
                    kind = "petrified_hold"
                    root = False
                    voluntary_forfeit = False
                elif action_type not in excluded:
                    kind = "action_performed"
                    root = True
                    voluntary_forfeit = False
                else:
                    kind = "wake" if action_type == "wake" else "forfeit"
                    root = False
                    voluntary_forfeit = False
                m9.resolve_slot(
                    slot_id,
                    root_action=root,
                    kind=kind,
                    voluntary_forfeit=voluntary_forfeit,
                    performance_performed=(
                        m9.performance_actor_id
                        == self.state.attention_owner_id(actor.player_id)),
                )
                # M9：公演根行动完成后 → T2 追猎反应（全局一次；合法 find/lock）
                if m9.performance_kind == "public" and \
                        m9.performance_actor_id == \
                        self.state.attention_owner_id(actor.player_id):
                    self._m9_trigger_hunt_reaction(actor.player_id)
                    # G5 德谬歌完成一次完整公演 → 微澜重开（W4）
                    if actor.talent is not None and hasattr(
                            actor.talent, "open_ripple_after_public"):
                        try:
                            actor.talent.open_ripple_after_public()
                        except Exception:
                            pass
                # 微澜无视隐身窗口：G5 下一个实际结算的 ActionGrant 结束时清除
                for _mark_target in self.state.iter_actors():
                    if getattr(_mark_target, "_m9_ripple_ignore_stealth_from",
                               None) == actor.player_id:
                        _mark_target._m9_ripple_ignore_stealth_from = None
                pool = getattr(self.state, "g6_template_pool", None)
                if pool is not None:
                    pool.record(self.state.current_round, action_type,
                                getattr(actor, "location", ""),
                                actor.player_id)
                self._m9_offer_performance_registration(
                    m9.drain_ready_to_register())

            # 犯罪检测（攻击和特殊行动都可能包含攻击）
            self._check_attack_crime(actor)
            if (m9 is not None and grant is not None
                    and grant.source_id == "g5_poem_earthfire"):
                self._m9_trigger_earthfire_hunt(actor, event_start)

            # 更新行动记录
            actor.last_action_type = action_type
            non_action_types = ("status", "help",
                                "police_status", "allstatus", "shock_recover")
            if action_type not in non_action_types:
                # M9 诊断字段：资格判定一律走 ActionGrant，不得由单布尔反推
                # 事实（结算 v0.3 §7）；该字段仅供 v2exp 兼容展示/保底使用。
                actor.acted_this_round = True
                actor.no_action_streak = 0
                actor.total_action_turns += 1

            # === 响应窗口（你给路打油）===
            if m9 is None and hasattr(self.state, 'response_window'):
                triggered, responder = self.state.response_window.process_after_action(
                    actor, action_type)
                if triggered and responder:
                    # 插入额外行动回合：在当前位置之后
                    action_queue.insert(i + 1, responder.player_id)
                    display.show_info(
                        f"📌 {responder.name} 的额外行动回合已插入！")

            # === 六爻额外回合（剪刀vs布）===
            if m9 is None and getattr(actor, 'hexagram_extra_turn', 0) > 0:
                # V1.92: 支持连续多个额外行动回合
                turns_to_add = actor.hexagram_extra_turn
                actor.hexagram_extra_turn = 0
                for t_idx in range(turns_to_add):
                    action_queue.insert(i + 1 + t_idx, actor.player_id)
                display.show_info(
                    f"📌 {actor.name} 的六爻额外行动回合已插入（{turns_to_add}个）！")

            # === 犯罪触发的额外回合（不良少年等）===
            if m9 is None and getattr(actor, 'crime_extra_turn', False):
                actor.crime_extra_turn = False
                action_queue.insert(i + 1, actor.player_id)
                display.show_info(
                    f"📌 {actor.name} 的额外行动回合已插入！")
            # === 愿负世主动发动的额外回合 ===
            if m9 is None and getattr(actor, 'savior_extra_turn', False):
                actor.savior_extra_turn = False
                action_queue.insert(i + 1, actor.player_id)
                display.show_info(
                    f"📌 {actor.name} 的额外行动回合已插入！（主动发动）")

            # === G2 聚光灯额外行动回合 ===
            spotlight_target = getattr(actor, '_g2_spotlight_target_id', None)
            if m9 is None and spotlight_target:
                actor._g2_spotlight_target_id = None
                action_queue.insert(i + 1, spotlight_target)
                target = self.state.get_player(spotlight_target)
                tname = target.name if target else spotlight_target
                display.show_info(
                    f"📌 {tname} 的聚光灯额外行动回合已插入！")

            # === G2 聚光合影额外回合 ===
            photo_invitee = getattr(actor, '_photo_invitee_id', None)
            if m9 is None and photo_invitee:
                actor._photo_invitee_id = None
                action_queue.insert(i + 1, photo_invitee)
                invitee = self.state.get_player(photo_invitee)
                tname2 = invitee.name if invitee else photo_invitee
                display.show_info(
                    f"📸 {tname2} 的合影额外行动回合已插入！")

            # === 星野临战-Archer 起床额外回合 ===
            if m9 is None and getattr(actor, 'hoshino_wakeup_extra_turn', False):
                actor.hoshino_wakeup_extra_turn = False
                action_queue.insert(i + 1, actor.player_id)
                display.show_info(
                    f"📌 {actor.name} 的额外行动回合已插入！（临战-Archer起床加成）")

            # === 剪刀手一突·警觉额外回合（扫描所有玩家，支持被动方即时插入）===
            if m9 is None:
                for pid in self.state.player_order:
                    p = self.state.get_player(pid)
                    if p and p.is_alive() and getattr(
                            p, 'vigilance_extra_turn', False):
                        p.vigilance_extra_turn = False
                        action_queue.insert(i + 1, p.player_id)
                        display.show_info(
                            f"📌 {p.name} 的额外行动回合已插入！（警觉）")

            # === 通用追加回合通道（非天赋具名；天赋经 BaseTalent.grant_extra_turn 置位）===
            # 默认 pending_extra_turns=0 时本段惰性，不影响 v1
            pending = getattr(actor, 'pending_extra_turns', 0)
            if m9 is None and pending > 0:
                actor.pending_extra_turns = 0
                for k in range(pending):
                    action_queue.insert(i + 1 + k, actor.player_id)
                from engine.prompt_manager import prompt_manager
                display.show_info(prompt_manager.get_prompt(
                    "game", "extra_turn_inserted",
                    default="📌 {name} 的额外行动回合已插入！").format(name=actor.name))

            # M9 完整额外行动必须紧跟当前根行动；切片插入会排在任何旧兼容插队前。
            if m9 is not None:
                pending_grants = m9.drain_pending_full_extra()
                if pending_grants:
                    action_queue[i + 1:i + 1] = pending_grants
                m9.end_grant(grant)

            # 检查胜利
            if self.state.check_victory():
                return

            i += 1

        # v2.0: duet 按钮清理
        ish = self.state.ish_bosheth
        if ish and ish.phase == "duet":
            ish._despawn_duet_buttons(self.state)

        # 未行动保底（K 模式退役：坐牢是常态非惩罚，不积累 streak——
        # acted_this_round 语义保留，G6 笑点照常从「未行动」事件获得）
        if experiments.is_enabled("k_initiative"):
            return
        initial_count = len(self.state.player_order)
        alive_count = len([pid for pid in self.state.player_order
                   if self.state.get_player(pid) and self.state.get_player(pid).is_alive()])
        for pid in self.state.player_order:
            player = self.state.get_player(pid)
            if not player or not player.is_alive():
                continue
            if not player.acted_this_round:
                # 开局>3人且仅剩2人时，保底失效
                if initial_count > 3 and alive_count <= 2:
                    player.no_action_streak = 0
                else:
                    player.no_action_streak += 1

    def _m9_restricted_followup(self, actor, grant) -> str:
        """G1 完全燃烧受限追加（§2.3）：仅 move/attack 受限根行动。
        返回 action_type 供槽收尾记账；不重新开放玩家 T0/即演/公演。"""
        from engine.m9.executor import execute_category
        ctrl = getattr(actor, "controller", None)
        menu = ["move", "attack"]
        try:
            choice = ctrl.choose(
                m9_text("round_manager.restricted_followup_prompt"), menu)
        except Exception:
            choice = "move"
        if choice not in menu:
            choice = menu[0]
        msg, ok = execute_category(actor, self.state, choice)
        display.show_info(
            msg or m9_text("round_manager.restricted_followup_result",
                           choice=choice))
        return choice if ok else "forfeit"

    # 已知造成伤害的天赋攻击事件类型（非标准 "attack" 事件）
    _TALENT_DAMAGE_EVENT_TYPES = frozenset({
        "oneslash_attack",   # T1 一刀缭断
        "star_attack",       # T3 天星
        "firefly_kill",      # G1 火萤击杀
        "firefly_supernova", # G1 超新星过载
    })

    def _check_attack_crime(self, attacker):
        """攻击后犯罪检测（含天赋钩子及各类天赋攻击事件）"""
        for event in reversed(self.state.event_log):
            # 匹配标准攻击事件（attacker字段）或天赋攻击事件（player字段）
            is_my_event = (
                (event.get("attacker") == attacker.player_id)
                or (event.get("player") == attacker.player_id)
            )
            if not is_my_event:
                continue
            if event.get("round") != self.state.current_round:
                continue

            # 判定是否造成了伤害/击杀
            caused_damage = False
            etype = event.get("type")
            if etype == "attack":
                caused_damage = event.get("result", {}).get("success", False)
            elif etype in self._TALENT_DAMAGE_EVENT_TYPES:
                caused_damage = True  # 这些天赋必然造成伤害
            elif etype == "ripple_poem":
                # 只有「爱与记忆」之诗造成伤害，其他诗篇是 buff
                caused_damage = (event.get("poem_type") == "爱与记忆")
            else:
                # 其他事件：检查 killed/damage/final_damage 字段
                caused_damage = (
                    event.get("killed", False)
                    or event.get("damage", 0) > 0
                    or event.get("final_damage", 0) > 0
                )

            if caused_damage:
                # 天赋犯罪检查
                if attacker.talent:
                    crime_result = attacker.talent.on_crime_check(
                        attacker.player_id, "伤害玩家")
                    if crime_result:
                        if crime_result.get("immune"):
                            return  # 免罪
                        if crime_result.get("extra_turn"):
                            msg = crime_result.get("message", "")
                            if msg:
                                display.show_info(msg)
                            attacker.crime_extra_turn = True

                # M5 白昼：每人首次攻击记「嫌疑」不记罪（v2.0 §3）
                from engine import world_clock as _wc
                if (experiments.is_enabled("m5_clock")
                        and _wc.active_value(
                            self.state, "first_attack_suspicion", default=False)):
                    done = getattr(self.state, "_first_attack_done", set())
                    if attacker.player_id not in done:
                        done.add(attacker.player_id)
                        self.state._first_attack_done = done
                        attacker.is_suspect = True
                        self.state.log_event("suspicion", player=attacker.player_id)
                        display.show_info(f"🕵️ {attacker.name} 引起嫌疑（白昼首攻不记罪）")
                        break

                self.police_engine.check_and_record_crime(
                    attacker.player_id, "伤害玩家")
            break

    def _process_burn_stacks_m4(self):
        """M4 通用灼烧 R4 结算（v2.0 §11.3）：每层 1 伤直扣 HP，上限内；
        本轮获甲可扑灭 1 层（复用 _armor_gained_this_round）。DoT 不受抗性管辖。"""
        per_stack = bget("burn", "damage_per_stack", default=1)
        for pid in self.state.player_order:
            p = self.state.get_player(pid)
            if not p or not p.is_alive():
                continue
            stacks = getattr(p, 'burn_stacks', 0)
            if stacks <= 0:
                continue
            # 本轮获甲扑灭 1 层
            if getattr(p, '_armor_gained_this_round', False) and stacks > 0:
                stacks -= 1
                p.burn_stacks = stacks
                display.show_info(f"🛡️🔥 {p.name} 获甲扑灭 1 层灼烧（剩 {stacks} 层）")
                if stacks <= 0:
                    continue
            dmg = stacks * per_stack
            p.hp = max(0, p.hp - dmg)
            display.show_info(f"🔥 {p.name} 灼烧 {stacks} 层造成 {dmg} 伤害（{p.hp}/{p.max_hp}）")
            if p.hp <= 0:
                if getattr(self.state, "m9_system", None) is not None:
                    from engine.m9.combat import adjudicate_and_finalize_death
                    adjudicate_and_finalize_death(
                        self.state, p, source_kind="burn", cause="burn")
                    continue
                prevented = False
                if p.talent:
                    dr = p.talent.on_death_check(p, None)
                    if dr and dr.get("prevent_death"):
                        p.hp = dr.get("new_hp", 1)
                        prevented = True
                if not prevented:
                    self.state.markers.on_player_death(p.player_id)
                    if self.state.police_engine:
                        self.state.police_engine.on_player_death(p.player_id)
                    self.state.log_event("death", player=p.player_id, cause="burn")
                    display.show_death(p.name, "灼烧")
                    RoundManager.notify_all_talents_of_death(
                        self.state, p.player_id, killer_id=None)

    def _m9_world_poem_r4_heal(self):
        """G0 世界援助·绫音急救：每个 R4，每名存活黑马所在地点所有单位回复
        world_poem_g0_heal；不分敌我、同地点同 R4 至多一次、不超最大 HP；
        来源 WORLD_RULE/world_poem_g0_aid（不给 G4 火种、不占魂援额度）。"""
        from engine.m9.g0_world_poem import world_poem_aid_of
        aid = world_poem_aid_of(self.state)
        if aid is None or not aid.activated:
            return
        heal = float(aid.heal_amount())
        if heal <= 0:
            return
        rnd = self.state.current_round
        healed_locs = set()
        for pid in self.state.player_order:
            p = self.state.get_player(pid)
            if not p or not p.is_alive():
                continue
            if not aid.pp.is_blackhorse(pid):
                continue
            loc = getattr(p, "location", None)
            if not loc or loc in healed_locs:
                continue
            if not aid.can_heal_location(rnd, loc):
                continue
            healed_locs.add(loc)
            for unit in self.state.players_at_location(loc):
                if not unit.is_alive():
                    continue
                max_hp = getattr(unit, "max_hp", 0)
                if max_hp <= 0:
                    continue
                before = getattr(unit, "hp", 0)
                unit.hp = min(max_hp, before + heal)
                self.state.log_event(
                    "world_poem_heal", location=loc,
                    target=getattr(unit, "player_id", None),
                    amount=heal, source_kind="WORLD_RULE",
                    source_id="world_poem_g0_aid")

    def _m9_betting_window(self):
        """R0 开市窗口（B4 §4.1）：死者投注/追加（tranche 分层）/转仓，随后重算
        黑马快照。AI/脚本控制器不支持交互时静默跳过（死者保持旁观）。"""
        m9 = getattr(self.state, "m9_system", None)
        pp = getattr(self.state, "m9_pp", None)
        if m9 is None or pp is None:
            return
        alive = [pid for pid in self.state.player_order
                 if self.state.get_player(pid).is_alive()]
        dead = [pid for pid in self.state.player_order
                if not self.state.get_player(pid).is_alive()]
        if not dead or not alive:
            return
        alive_count = len(alive)
        for pid in dead:
            p = self.state.get_player(pid)
            if p is None or pp.is_frozen(pid):
                continue  # 绝对死亡：PP 冻结，不可投注
            ctrl = getattr(p, "controller", None)
            if ctrl is None:
                continue
            try:
                action = ctrl.choose(
                    m9_text("round_manager.betting_window_prompt", name=p.name),
                    m9_text_list("round_manager.betting_window_options"))
            except Exception:
                continue
            if action in ("押注", "追加"):
                try:
                    target = ctrl.choose(
                        m9_text("round_manager.betting_target_prompt"),
                        [self.state.get_player(t).name for t in alive])
                except Exception:
                    continue
                tpid = next((t for t in alive
                             if self.state.get_player(t).name == target), None)
                if tpid is None:
                    continue
                budget = pp.balance(pid)
                if budget < 1:
                    continue
                try:
                    amt = int(ctrl.choose(
                        m9_text("round_manager.betting_amount_prompt"),
                        [str(i) for i in range(1, min(6, budget) + 1)]))
                except Exception:
                    amt = 1
                amt = max(1, min(int(amt), budget))
                if pp.place_bet(pid, tpid, amount=amt, alive_count=alive_count):
                    self.state.log_event("m9_bet", bettor=pid, target=tpid,
                                         amount=amt)
            elif action == "转仓":
                current = pp.bet_targets(pid)
                if not current:
                    continue
                try:
                    old = ctrl.choose(
                        m9_text("round_manager.transfer_out_prompt"),
                        list(current))
                except Exception:
                    continue
                if old not in current:
                    continue
                try:
                    new = ctrl.choose(
                        m9_text("round_manager.transfer_in_prompt"),
                        [self.state.get_player(t).name for t in alive])
                except Exception:
                    continue
                npid = next((t for t in alive
                             if self.state.get_player(t).name == new), None)
                if npid is None or npid == old:
                    continue
                if pp.transfer_bet(pid, old, npid, alive_count):
                    self.state.log_event("m9_bet_transfer", bettor=pid,
                                         old=old, new=npid)
        pp.recompute_blackhorse(alive, dead)

    def _m9_pp_r4_decay(self):
        """R4：生者 PP 衰减（死者免衰减、绝对死亡冻结；B4 §3.4）。"""
        pp = getattr(self.state, "m9_pp", None)
        if pp is None:
            return
        for pid in self.state.player_order:
            p = self.state.get_player(pid)
            if p is None or not p.is_alive():
                continue
            pp.decay(pid)

    def _process_starlight(self):
        """M6 往世层星光阶段（v2.0 §5）：死者成星每轮 +1 星光（上限），
        星光够则做星光行动（用原 controller 选目标，每轮 1 次）。"""
        from engine import world_clock  # noqa（balance 读取无需，保持一致）
        gain = bget("afterlife", "starlight_per_round", default=1)
        cap = bget("afterlife", "starlight_cap", default=3)
        for pid in self.state.player_order:
            p = self.state.get_player(pid)
            if not p or p.is_alive() or not getattr(p, "is_star", False):
                continue
            p.starlight = min(cap, getattr(p, "starlight", 0) + gain)
            from actions import starlight
            if starlight.available_actions(p):
                msg = starlight.perform(p, self.state)
                if msg:
                    display.show_info(msg)

    def _process_apocalypse_damage(self):
        """M5 终焉真伤（v2.0 §3）：每轮末全体 −N 真伤，叙事级豁免无效——
        直扣 HP 不走任何减免（秩序崩塌的最后压力），死亡走免死天赋链。"""
        from engine import world_clock
        dmg = world_clock.active_value(
            self.state, "end_of_round_true_damage", default=0)
        if not dmg:
            return
        display.show_info(f"🌑 终焉降临：全体承受 {dmg} 点真实伤害")
        for pid in self.state.player_order:
            p = self.state.get_player(pid)
            if not p or not p.is_alive():
                continue
            p.hp = max(0, p.hp - dmg)
            if p.hp <= 0:
                if getattr(self.state, "m9_system", None) is not None:
                    from engine.m9.combat import adjudicate_and_finalize_death
                    adjudicate_and_finalize_death(
                        self.state, p,
                        source_kind="world_clock_apocalypse",
                        cause="apocalypse")
                    continue
                prevented = False
                if p.talent:
                    dr = p.talent.on_death_check(p, None)
                    if dr and dr.get("prevent_death"):
                        p.hp = dr.get("new_hp", 1)
                        prevented = True
                if not prevented:
                    self.state.markers.on_player_death(p.player_id)
                    if self.state.police_engine:
                        self.state.police_engine.on_player_death(p.player_id)
                    self.state.log_event("death", player=p.player_id, cause="apocalypse")
                    display.show_death(p.name, "终焉真伤")
                    RoundManager.notify_all_talents_of_death(
                        self.state, p.player_id, killer_id=None)

    @staticmethod
    def notify_all_talents_of_death(game_state, victim_id, killer_id=None):
        """
        通知所有存活玩家的天赋：有玩家死亡。
        用于星野色彩计数等需要全局死亡通知的机制。
        """
        m9 = getattr(game_state, "m9_system", None)
        if m9 is not None:
            m9.on_actor_exit(victim_id, clear_sp=True)
        m9_police = getattr(game_state, "m9_police", None)
        if m9_police is not None and hasattr(m9_police, "on_player_death"):
            try:
                m9_police.on_player_death(victim_id)
            except Exception:
                pass
        for pid in game_state.player_order:
            p = game_state.get_player(pid)
            if p and p.talent and (m9 is not None or p.is_alive()):
                if hasattr(p.talent, '_on_any_player_death'):
                    p.talent._on_any_player_death(victim_id, killer_id)

    def _m9_trigger_hunt_reaction(self, performer_id: str) -> None:
        """T2 追猎反应挂接：他人公演根行动完成后，若其天赋声明了
        m9_on_public_root_completed，按自身状态执行一次合法 find/lock
        （不创建 ActionGrant、不进 T0；全局一次由天赋内部保证）。"""
        m9 = getattr(self.state, "m9_system", None)
        if m9 is None:
            return
        for pid in self.state.player_order:
            if pid == performer_id:
                continue
            p = self.state.get_player(pid)
            if p is None or not p.is_alive():
                continue
            talent = getattr(p, "talent", None)
            if talent is None or not hasattr(talent, "m9_on_public_root_completed"):
                continue
            try:
                talent.m9_on_public_root_completed(performer_id)
            except Exception:
                pass

    def _m9_trigger_earthfire_hunt(self, actor, event_start: int) -> None:
        """地火 full-extra 内首次犯罪/发现敌人触发一次免费追猎。"""
        talent = getattr(actor, "talent", None)
        if talent is None or not hasattr(talent, "free_hunt_reaction"):
            return
        trigger_target = None
        triggered = False
        for event in self.state.event_log[event_start:]:
            event_type = event.get("type")
            if event_type == "find" and event.get("player") == actor.player_id:
                trigger_target = event.get("target")
                triggered = True
                break
            if event_type == "crime" and event.get("player") == actor.player_id:
                trigger_target = event.get("target")
                triggered = True
                break
        if triggered:
            talent.free_hunt_reaction(trigger_target)

    # ============================================
    # R4: 轮次结束结算
    # ============================================
    def _phase_r4(self):
        self.state.current_phase = "r4_end"
        display.show_phase("📋 轮次结束结算")

        # M9：幻想乡维持费是 R4 持续阶段的第一项；不足时先解除结界再承受环境伤害。
        if getattr(self.state, "m9_system", None) is not None:
            for pid in self.state.player_order:
                player = self.state.get_player(pid)
                talent = getattr(player, "talent", None) if player else None
                if (player is not None and player.is_alive()
                        and talent is not None
                        and hasattr(talent, "on_r4_upkeep")):
                    talent.on_r4_upkeep(self.state.current_round)

        # M4 弓模块回流 + M5 击杀掉落 + M6 死亡登记：轮末扫描死亡玩家（幂等）。
        m6 = experiments.is_enabled("m6_scoring")
        if experiments.is_enabled("m4_gear") or m6:
            m4 = experiments.is_enabled("m4_gear")
            m5 = experiments.is_enabled("m5_clock")
            for pid in self.state.player_order:
                p = self.state.get_player(pid)
                if p and not p.is_alive():
                    if m4 and getattr(p, 'bow_modules', None):
                        from engine.bow_modules import release_on_death
                        release_on_death(p, self.state)
                    if m4 and m5:
                        self.state.drop_loot_on_death(p)
                    # M6 死亡登记：记死于第几轮（存活系数用），幂等
                    if m6 and getattr(p, 'death_round', 0) == 0:
                        p.death_round = self.state.current_round
                        # 往世层：死者成星（被锚定击杀者不能成星，v2.0 §5）
                        # M9：往世层重定义为 PP 投注/魂援（B4 v0.4），星光成星被
                        # 取代；m9-rfc 下不给任何死者打旧星光星标（绝对死亡边界见 S2.7）。
                        if (not getattr(p, '_anchor_killed', False)
                                and not experiments.is_enabled("m9_rfc")):
                            p.is_star = True

        # R4-1: 警察执法（M9 走 m9_police 状态机；v2exp 走 legacy 引擎）
        m9_police = getattr(self.state, "m9_police", None)
        if m9_police is not None and getattr(self.state, "m9_system", None) \
                is not None:
            m9_police.set_state_ref(self.state)
            police_msgs = m9_police.r4_enforcement(
                self.state, self.state.current_round)
        else:
            police_msgs = self.police_engine.process_end_of_round()
        if police_msgs:
            display.show_police_enforcement(police_msgs)
        if self.state.check_victory():
            return

        # R4-1.2: G0 世界援助·绫音急救（防御支援；激活门槛见 R0 recompute）
        self._m9_world_poem_r4_heal()
        if self.state.check_victory():
            return

        # R4-1.3: M9 生者 PP 衰减（死者免衰减；绝对死亡冻结）
        self._m9_pp_r4_decay()
        if self.state.check_victory():
            return

        # R4-1.5: 火萤灼烧结算（与警察同时机）
        # m7 下 G1 灼烧走 M4 通用层（_process_burn_stacks_m4），不再单独结算（防双扣）
        if not experiments.is_enabled("m7_talents"):
            for pid in self.state.player_order:
                p = self.state.get_player(pid)
                if p and p.is_alive() and p.talent:
                    if hasattr(p.talent, 'process_burn_damage'):
                        p.talent.process_burn_damage(self.state.current_round)
            if self.state.check_victory():
                return

        # R4-1.6: 通用灼烧结算（M4 火矢/燃烧瓶 + M7 G1 共用，v2.0 §11.3）
        if experiments.is_enabled("m4_gear") or experiments.is_enabled("m7_talents"):
            self._process_burn_stacks_m4()
        if self.state.check_victory():
            return

        # R4-1.7: M5 终焉每轮末全体真伤（叙事级豁免无效，v2.0 §3）
        if experiments.is_enabled("m5_clock"):
            self._process_apocalypse_damage()
        if self.state.check_victory():
            return

        # R4-2: 病毒
        if self.state.virus.is_active and experiments.is_enabled("hp20"):
            # hp20 病毒重做（v2.0 §2.5）：潜伏期后每轮 -N HP（压力钟，非即死）
            in_damage_phase = self.state.virus.tick_hp20()
            display.show_virus_status(self.state)
            if in_damage_phase:
                dmg = self.state.virus.get_damage_per_round()
                for pid in self.state.player_order:
                    p = self.state.get_player(pid)
                    if not p or not p.is_alive():
                        continue
                    if self.state.virus._is_immune(p):
                        continue
                    p.hp = max(0, p.hp - dmg)
                    display.show_info(f"🦠 {p.name} 受病毒侵蚀 -{dmg} HP（{p.hp}/{p.max_hp}）")
                    if p.hp <= 0:
                        # 天赋免死链照常（先自己后他人）
                        prevented = False
                        if p.talent:
                            dr = p.talent.on_death_check(p, None)
                            if dr and dr.get("prevent_death"):
                                p.hp = dr.get("new_hp", 1)
                                prevented = True
                        if not prevented:
                            for pid2 in self.state.player_order:
                                p2 = self.state.get_player(pid2)
                                if p2 and p2.talent and p2.player_id != p.player_id:
                                    dr = p2.talent.on_death_check(p, None)
                                    if dr and dr.get("prevent_death"):
                                        p.hp = dr.get("new_hp", 1)
                                        prevented = True
                                        break
                        if not prevented:
                            self.state.markers.on_player_death(p.player_id)
                            if self.state.police_engine:
                                self.state.police_engine.on_player_death(p.player_id)
                            self.state.log_event("death", player=p.player_id, cause="virus")
                            display.show_death(p.name, "病毒侵蚀")
                            RoundManager.notify_all_talents_of_death(
                                self.state, p.player_id, killer_id=None)
        elif self.state.virus.is_active:
            is_lethal = self.state.virus.tick()
            display.show_virus_status(self.state)
            if is_lethal:
                dead = self.state.virus.get_dead_players(
                    list(self.state.players.values()))
                if dead:
                    display.show_virus_deaths(dead)
                    for p in dead:
                        # 六爻·元亨利贞：免疫病毒致死（最先检查，避免浪费死亡预防能力）
                        if p.talent and hasattr(p.talent, 'is_immune_to_damage') and p.talent.is_immune_to_damage("病毒"):
                            display.show_info(f"☯️ {p.name} 的「元亨利贞」免疫了病毒致死判定！")
                            continue
                        # 天赋死亡检查
                        prevented = False
                        if p.talent:
                            dr = p.talent.on_death_check(p, None)
                            if dr and dr.get("prevent_death"):
                                p.hp = dr.get("new_hp", 0.5)
                                prevented = True
                        if not prevented:
                            # 其他玩家天赋检查（死者苏生）
                            for pid2 in self.state.player_order:
                                p2 = self.state.get_player(pid2)
                                if p2 and p2.talent and p2.player_id != p.player_id:
                                    dr = p2.talent.on_death_check(p, None)
                                    if dr and dr.get("prevent_death"):
                                        p.hp = dr.get("new_hp", 0.5)
                                        prevented = True
                                        break
                        if not prevented:
                            p.hp = 0
                            self.state.markers.on_player_death(p.player_id)
                            if self.state.police_engine:
                                self.state.police_engine.on_player_death(p.player_id)
                            self.state.log_event("death", player=p.player_id, cause="virus")
                            RoundManager.notify_all_talents_of_death(
                                self.state, p.player_id, killer_id=None)
        if self.state.check_victory():
            return

        # R4-2.5: ish-bosheth R4 衰减
        if (self.state.ish_bosheth
                and self.state.ish_bosheth.phase in ("active", "duet")):
            self.state.ish_bosheth.on_r4(self.state)
        if self.state.check_victory():
            return

        # R4-2.7: M6 往世层星光阶段（死者挣星光 + 星光行动，v2.0 §5）
        # M9：往世层已重定义为"投资者 + 魂援提供者"（B4 v0.4），星光行动被
        # 取代（docs/ai/commands.md 附录已标注"设计已取代、实现未拆除"），
        # m9-rfc 下不执行旧星光阶段。
        if m6 and not experiments.is_enabled("m9_rfc"):
            self._process_starlight()

        # R4-3: 天赋轮次结束钩子
        for pid in self.state.player_order:
            p = self.state.get_player(pid)
            if p and p.is_alive() and p.talent:
                p.talent.on_round_end(self.state.current_round)
        # R4-3.5: M9 统一石化生命周期 tick（建立轮 R4 不 tick）
        petrify = getattr(self.state, "m9_petrify", None)
        if petrify is not None and getattr(self.state, "m9_system", None) is not None:
            petrify.on_r4_tick(self.state, self.state.current_round)
        # R4-4: 星野架盾 cost 扣除（README: "位于R4所有检查之后"）
        for pid in self.state.player_order:
            p = self.state.get_player(pid)
            if p and p.is_alive() and p.talent and hasattr(p.talent, '_r4_shield_cost_check'):
                p.talent._r4_shield_cost_check()
        if self.state.check_victory():
            return
