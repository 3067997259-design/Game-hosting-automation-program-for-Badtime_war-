"""轮次调度器（Phase 4 完整版）：天赋钩子+响应窗口+额外行动回合"""

from utils.dice import roll_d4, roll_d6
from cli import display
from engine.action_turn import ActionTurnManager
from engine.police_system import PoliceEngine
from engine import experiments
from engine.balance import get as bget


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
                self.state.winner = winner_id
                if winner_id == "nobody":
                    display.show_info("所有玩家都已死亡……无人获胜。")
                else:
                    winner = self.state.get_player(winner_id)
                    display.show_victory(winner.name if winner else winner_id)
                return

            # 最大轮数安全网
            if self.state.is_max_rounds_reached():
                self.state.game_over = True
                self.state.winner = "nobody"
                display.show_info(
                    f"⚠️ 达到最大轮数限制（{self.state.max_rounds}轮），游戏判定平局。")
                return

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

    # ============================================
    # R1: D4 争夺行动权（v1）/ 先攻判定（k_initiative）
    # ============================================
    def _phase_r1(self):
        if experiments.is_enabled("k_initiative"):
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

        # 参与者收集：存活玩家 + 舞台模式的 Chorus
        entrants = []  # (pid, name, bonus_fn)
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
            total = roll + bonus
            tiebreak = roll_d6()  # 补掷（仅同点时有意义，统一掷保证消耗序稳定）
            rolls[pid] = (roll, bonus, total)
            entries.append((total, tiebreak, bonus, -order_map.get(pid, 99), pid, name))
        entries.sort(reverse=True)

        # 配额：K = max(1, 存活玩家数 - 坐牢数)；舞台模式全员行动
        sitout_count = bget("action_system", "k_sitout_count", default=1)
        if stage_active:
            quota = len(entries)
        else:
            quota = max(1, len(entries) - max(0, int(sitout_count)))

        actors = [e[4] for e in entries[:quota]]
        sitout = [e[4] for e in entries[quota:]]
        self.state.round_winners = actors
        self.state.initiative_results = rolls  # K 模式专属（动态属性，消费方用 getattr）

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

        # 构建行动队列（可在运行中插入额外行动回合）
        action_queue = list(self.state.round_winners)

        # v0.6 ish-bosheth: G2 保底最优先行动
        ish = self.state.ish_bosheth
        if ish and ish.phase in ("active", "duet"):
            g2_pid = ish.g2_owner_id
            if g2_pid in action_queue:
                action_queue.remove(g2_pid)
            action_queue.insert(0, g2_pid)
            display.show_info(f"🎵 G2 优先演唱回合")

        i = 0
        while i < len(action_queue):
            actor_id = action_queue[i]
            actor = self.state.get_player(actor_id)
            if not actor or not actor.is_alive():
                i += 1
                continue

            # 执行行动回合
            action_type = self.turn_manager.execute_action_turn(actor)

            # 犯罪检测（攻击和特殊行动都可能包含攻击）
            self._check_attack_crime(actor)

            # 更新行动记录
            actor.last_action_type = action_type
            non_action_types = ("status", "help",
                                "police_status", "allstatus", "shock_recover")
            if action_type not in non_action_types:
                actor.acted_this_round = True
                actor.no_action_streak = 0
                actor.total_action_turns += 1

            # === 响应窗口（你给路打油）===
            if hasattr(self.state, 'response_window'):
                triggered, responder = self.state.response_window.process_after_action(
                    actor, action_type)
                if triggered and responder:
                    # 插入额外行动回合：在当前位置之后
                    action_queue.insert(i + 1, responder.player_id)
                    display.show_info(
                        f"📌 {responder.name} 的额外行动回合已插入！")

            # === 六爻额外回合（剪刀vs布）===
            if getattr(actor, 'hexagram_extra_turn', 0) > 0:
                # V1.92: 支持连续多个额外行动回合
                turns_to_add = actor.hexagram_extra_turn
                actor.hexagram_extra_turn = 0
                for t_idx in range(turns_to_add):
                    action_queue.insert(i + 1 + t_idx, actor.player_id)
                display.show_info(
                    f"📌 {actor.name} 的六爻额外行动回合已插入（{turns_to_add}个）！")

            # === 犯罪触发的额外回合（不良少年等）===
            if getattr(actor, 'crime_extra_turn', False):
                actor.crime_extra_turn = False
                action_queue.insert(i + 1, actor.player_id)
                display.show_info(
                    f"📌 {actor.name} 的额外行动回合已插入！")
            # === 愿负世主动发动的额外回合 ===
            if getattr(actor, 'savior_extra_turn', False):
                actor.savior_extra_turn = False
                action_queue.insert(i + 1, actor.player_id)
                display.show_info(
                    f"📌 {actor.name} 的额外行动回合已插入！（主动发动）")

            # === G2 聚光灯额外行动回合 ===
            spotlight_target = getattr(actor, '_g2_spotlight_target_id', None)
            if spotlight_target:
                actor._g2_spotlight_target_id = None
                action_queue.insert(i + 1, spotlight_target)
                target = self.state.get_player(spotlight_target)
                tname = target.name if target else spotlight_target
                display.show_info(
                    f"📌 {tname} 的聚光灯额外行动回合已插入！")

            # === G2 聚光合影额外回合 ===
            photo_invitee = getattr(actor, '_photo_invitee_id', None)
            if photo_invitee:
                actor._photo_invitee_id = None
                action_queue.insert(i + 1, photo_invitee)
                invitee = self.state.get_player(photo_invitee)
                tname2 = invitee.name if invitee else photo_invitee
                display.show_info(
                    f"📸 {tname2} 的合影额外行动回合已插入！")

            # === 星野临战-Archer 起床额外回合 ===
            if getattr(actor, 'hoshino_wakeup_extra_turn', False):
                actor.hoshino_wakeup_extra_turn = False
                action_queue.insert(i + 1, actor.player_id)
                display.show_info(
                    f"📌 {actor.name} 的额外行动回合已插入！（临战-Archer起床加成）")

            # === 剪刀手一突·警觉额外回合（扫描所有玩家，支持被动方即时插入）===
            for pid in self.state.player_order:
                p = self.state.get_player(pid)
                if p and p.is_alive() and getattr(p, 'vigilance_extra_turn', False):
                    p.vigilance_extra_turn = False
                    action_queue.insert(i + 1, p.player_id)
                    display.show_info(
                        f"📌 {p.name} 的额外行动回合已插入！（警觉）")

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
        for pid in game_state.player_order:
            p = game_state.get_player(pid)
            if p and p.is_alive() and p.talent:
                if hasattr(p.talent, '_on_any_player_death'):
                    p.talent._on_any_player_death(victim_id, killer_id)

    # ============================================
    # R4: 轮次结束结算
    # ============================================
    def _phase_r4(self):
        self.state.current_phase = "r4_end"
        display.show_phase("📋 轮次结束结算")

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

        # R4-1: 警察执法
        police_msgs = self.police_engine.process_end_of_round()
        if police_msgs:
            display.show_police_enforcement(police_msgs)
        if self.state.check_victory():
            return

        # R4-1.5: 火萤灼烧结算（与警察同时机）
        for pid in self.state.player_order:
            p = self.state.get_player(pid)
            if p and p.is_alive() and p.talent:
                if hasattr(p.talent, 'process_burn_damage'):
                    p.talent.process_burn_damage(self.state.current_round)
        if self.state.check_victory():
            return

        # R4-1.6: M4 通用灼烧结算（火矢/燃烧瓶共用，G1 接入归 M7；v2.0 §11.3）
        if experiments.is_enabled("m4_gear"):
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

        # R4-3: 天赋轮次结束钩子
        for pid in self.state.player_order:
            p = self.state.get_player(pid)
            if p and p.is_alive() and p.talent:
                p.talent.on_round_end(self.state.current_round)
        # R4-4: 星野架盾 cost 扣除（README: "位于R4所有检查之后"）
        for pid in self.state.player_order:
            p = self.state.get_player(pid)
            if p and p.is_alive() and p.talent and hasattr(p.talent, '_r4_shield_cost_check'):
                p.talent._r4_shield_cost_check()
        if self.state.check_victory():
            return
